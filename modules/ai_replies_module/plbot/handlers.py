import asyncio
import json
import re
import threading
import time
from datetime import datetime
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from settings import Settings as global_sett
from tgbot.telegrambot import get_telegram_bot, get_telegram_bot_loop

from ..data import Data as data
from ..meta import NAME, PREFIX
from ..settings import DATA, Settings as sett

if TYPE_CHECKING:
    from plbot.playerokbot import PlayerokBot


logger = getLogger(f"{NAME}.playerok")
ROOT_MODULES = Path(__file__).resolve().parents[2]

# Терминальные статусы сделки: ИИ в таких чатах не отвечает.
FINISHED_DEAL_STATUSES = {"SENT", "CONFIRMED", "CONFIRMED_AUTOMATICALLY", "ROLLED_BACK"}
# Статусы чатов holdboost, во время которых ИИ молчит (идёт автовыдача).
HOLDBOOST_BUSY_STATUSES = {"WAIT_INVITE", "PROCESSING", "HOLDBOOST_CREATED"}

FENCE = chr(96) * 3  # символы разметки кода, вырезаются из ответа ИИ

me_last_text: dict[str, float] = {}           # чат -> время последнего РУЧНОГО сообщения продавца
auto_sent: dict[str, tuple[float, str]] = {}  # чат -> (время, текст) последнего ответа ИИ
_seen_message_ids: set[str] = set()
_limit_notified_date: str = ""
_write_lock = threading.Lock()


def ai_msg(message_name: str, plbot: "PlayerokBot", **kwargs) -> str | None:
    return plbot.msg(message_name, "messages", DATA, **kwargs)


def _config():
    return sett.get("config")


def _reply_stats():
    stats = data.get("stats")
    today = datetime.now().strftime("%Y-%m-%d")
    if not isinstance(stats, dict) or stats.get("date") != today:
        stats = {"date": today, "count": 0}
    return stats


def _count_reply():
    stats = _reply_stats()
    stats["count"] = int(stats.get("count", 0) or 0) + 1
    data.set("stats", stats)


def _daily_replies():
    return int(_reply_stats().get("count", 0) or 0)


def holdboost_busy_chat_ids() -> set[str]:
    """Чаты, в которых прямо сейчас HoldBoost выдаёт бусты: ИИ там помолчит."""
    path = ROOT_MODULES / "holdboost_module" / "module_data" / "deals.json"
    try:
        deals = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    result = set()
    for entry in (deals or {}).values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status", "")) in HOLDBOOST_BUSY_STATUSES and entry.get("chat_id"):
            result.add(str(entry.get("chat_id")))
    return result


def _in_quiet_hours(rules: dict) -> bool:
    start = int(rules.get("quiet_start", 0) or 0)
    end = int(rules.get("quiet_end", 0) or 0)
    if start == end:
        return False
    hour = datetime.now().hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def notify_telegram(text: str) -> None:
    telegram_bot = get_telegram_bot()
    telegram_loop = get_telegram_bot_loop()
    if not telegram_bot or not telegram_loop:
        logger.error(text)
        return
    signed_users = global_sett.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    if not signed_users:
        logger.error(text)
        return
    for user_id in signed_users:
        future = asyncio.run_coroutine_threadsafe(
            telegram_bot.bot.send_message(chat_id=user_id, text=text),
            telegram_loop,
        )
        future.add_done_callback(lambda result: None)


def ai_request(messages: list[dict], ai: dict) -> str:
    """Запрос к OpenRouter-совместимому API (POST {base_url}/chat/completions)."""
    api_key = str(ai.get("api_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("В config.json не заполнен ai.api_key")

    base_url = str(ai.get("base_url", "https://openrouter.ai/api/v1")).rstrip("/")
    payload = {
        "model": str(ai.get("model", "openai/gpt-4o-mini")),
        "messages": messages,
        "max_tokens": int(ai.get("max_tokens", 400) or 400),
        "temperature": float(ai.get("temperature", 0.7) or 0.7),
        "stream": False,
    }
    logger.info(
        "%s Запрос к ИИ: model=%s messages=%s max_tokens=%s",
        PREFIX, payload["model"], len(messages), payload["max_tokens"],
    )
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "Playerok Universal Bot",
        },
        json=payload,
        timeout=int(ai.get("timeout", 60) or 60),
    )
    try:
        result = response.json()
    except ValueError:
        result = {"raw": response.text}

    if response.status_code >= 400:
        error = result.get("error", {}).get("message") if isinstance(result, dict) else None
        raise RuntimeError(
            f"ИИ HTTP {response.status_code}: {error or json.dumps(result, ensure_ascii=False)[:300]}"
        )

    content = result["choices"][0]["message"]["content"]
    logger.info("%s Ответ ИИ получен: chars=%s", PREFIX, len(content))
    return content


def build_history_lines(plbot: "PlayerokBot", chat_id: str, my_id: str, limit: int = 15) -> list[str]:
    try:
        page = plbot.account.get_chat_messages(chat_id, count=24)
        messages = list(page.messages or [])
    except Exception:
        logger.exception("%s Не удалось получить историю чата %s", PREFIX, chat_id)
        return []
    rows = []
    for message in messages:
        text = getattr(message, "text", None)
        if not text:
            continue
        user = getattr(message, "user", None)
        who = "Продавец" if user and str(getattr(user, "id", "")) == str(my_id) else "Покупатель"
        created = getattr(message, "created_at", "") or ""
        rows.append((str(created), f"{who}: {str(text).strip()}"))
    rows.sort(key=lambda row: row[0])
    return [row[1] for row in rows[-limit:]]


def _normalized_name(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def _group_items(group: dict) -> list[str | dict]:
    """Список товаров группы; поддерживает и старый формат одной привязки."""
    items = group.get("items")
    if isinstance(items, list) and items:
        return [item for item in items if isinstance(item, dict)]
    if group.get("item_id"):
        return [{
            "item_id": group.get("item_id"),
            "item_name": group.get("item_name"),
            "item_url": group.get("item_url"),
        }]
    return []


def match_group(config: dict, item_id, item_name):
    """Группа товара: у совпадение с любым её товаром по ID, затем по названию."""
    groups = config.get("groups", []) or []
    if not groups:
        return None
    by_id = [group for group in groups if any(
        str(item.get("item_id", "")) == str(item_id or "")
        for item in _group_items(group)
    )]
    if len(by_id) == 1:
        return by_id[0]
    if len(by_id) > 1:
        logger.warning("%s Товар входит в несколько групп: item_id=%s, беру первую", PREFIX, item_id)
        return by_id[0]

    normalized = _normalized_name(item_name)
    if not normalized:
        return None
    by_name = [group for group in groups if any(
        _normalized_name(item.get("item_name", "")) == normalized
        for item in _group_items(group)
    )]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        logger.warning("%s Неоднозначные группы по названию: item_name=%r, беру первую", PREFIX, item_name)
        return by_name[0]
    return None


def build_item_context(chat) -> str:
    """Карточка последней сделки чата: товар, цена, поля и статус."""
    deals = getattr(chat, "deals", None) or []
    if not deals:
        return "Не удалось определить товар."
    try:
        deal = max(deals, key=lambda d: getattr(d, "created_at", "") or "")
    except (TypeError, ValueError):
        deal = deals[0]
    item = getattr(deal, "item", None)
    lines = [f"Статус сделки: {getattr(deal, 'status', '—')}"]
    if item is not None:
        lines.append(f"Товар: {getattr(item, 'name', '—')}")
        price = getattr(item, "price", None)
        if price is not None:
            lines.append(f"Цена: {price} руб.")
        description = getattr(item, "description", None)
        if description:
            lines.append(f"Описание: {str(description)[:1000]}")
    for field in (getattr(deal, "obtaining_fields", None) or []):
        value = getattr(field, "value", None)
        if value:
            lines.append(f"{getattr(field, 'label', None) or 'Поле'} (заполнил покупатель): {str(value)[:200]}")
    return "\n".join(lines)


def latest_chat_item(chat):
    """Последний товар чата: ID и название нужны для сопоставления с группой."""
    deals = getattr(chat, "deals", None) or []
    if not deals:
        return None
    try:
        deal = max(deals, key=lambda d: getattr(d, "created_at", "") or "")
    except (TypeError, ValueError):
        deal = deals[0]
    return getattr(deal, "item", None) or None


def empty_knowledge(entries) -> bool:
    return not [e for e in (entries or []) if isinstance(e, dict) and (e.get("q") or e.get("a"))]


def build_knowledge_block(entries: list) -> str:
    rows = []
    for entry in entries[:60]:
        if not isinstance(entry, dict):
            continue
        q = str(entry.get("q", "")).strip()
        a = str(entry.get("a", "")).strip()
        if q or a:
            rows.append(f"- Вопрос: {q}\n  Ответ: {a}")
    if not rows:
        return "Справка пока пуста."
    return "\n".join(rows)[:6000]


def postprocess_reply(raw: str, max_chars: int) -> str:
    text = str(raw or "").strip()
    # OpenRouter возвращает только текст, но на всякий случай срезаем
    # возможную разметку кода и ведущие кавычки.
    text = text.replace(FENCE, " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip('"«»')
    if len(text) > max_chars:
        cut = text[:max_chars]
        dot = cut.rfind(". ")
        text = (cut[:dot + 1] if dot > 60 else cut.rstrip() + "…")
    return text.strip()


def latest_deal_finished(chat) -> bool:
    """True, если последняя сделка чата уже выполнена (подтверждена/возвращена)."""
    deals = [deal for deal in (getattr(chat, "deals", None) or []) if getattr(deal, "status", None)]
    if not deals:
        return False
    try:
        deal = max(deals, key=lambda d: getattr(d, "created_at", "") or "")
    except (TypeError, ValueError):
        deal = deals[0]
    return getattr(deal, "status", None).name in FINISHED_DEAL_STATUSES


def process_reply(plbot: "PlayerokBot", chat, buyer_name: str, buyer_text: str, my_id: str) -> None:
    chat_id = str(chat.id)
    try:
        config = _config()
        rules = config.get("rules", {})
        history = build_history_lines(plbot, chat_id, my_id)

        item, item_context = latest_chat_item(chat), build_item_context(chat)
        group = match_group(config, getattr(item, "id", None), getattr(item, "name", None))

        # Группа перекрывает общие правила, когда у товара она есть.
        if group:
            prompt_lines = [str(line) for line in (group.get("system_prompt") or []) if line]
            if empty_knowledge(group.get("knowledge", [])):
                knowledge = build_knowledge_block(config.get("knowledge", []) or [])
            else:
                knowledge = build_knowledge_block(group.get("knowledge", []))
            logger.info("%s Товар сопоставлен с группой: num=%s item=%s", PREFIX, group.get("num") or "—", getattr(item, "id", "—"))
        else:
            prompt_lines = [str(line) for line in (rules.get("system_prompt") or []) if line]
            knowledge = build_knowledge_block(config.get("knowledge", []) or [])

        system = "\n".join(prompt_lines)
        system += (
            "\nЯзык ответа: русский (всегда, независимо от языка сообщения покупателя)."
            f"\nМаксимальная длина ответа: {int(rules.get('max_reply_chars', 700) or 700)} символов."
            "\nСправка продавца (FAQ) ниже; опирайся на неё."
        )
        user_content = (
            f"СПРАВКА (FAQ):\n{knowledge}\n\n"
            f"КАРТОЧКА ПОСЛЕДНЕЙ СДЕЛКИ ЧАТА:\n{item_context}\n\n"
            f"ИСТОРИЯ ПЕРЕПИСКИ:\n" + ("\n".join(history) or "(нет сообщений)") + "\n\n"
            f"ПОСЛЕДНЕЕ СООБЩЕНИЕ ПОКУПАТЕЛЯ, НА КОТОРОЕ НАДО ОТВЕТИТЬ:\n{buyer_text}"
        )

        reply = postprocess_reply(
            ai_request(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                config.get("ai", {}),
            ),
            int(rules.get("max_reply_chars", 700) or 700),
        )
        if not reply:
            logger.warning("%s ИИ вернул пустой ответ, отправки не будет: chat=%s", PREFIX, chat_id)
            return

        delay = max(0, int(rules.get("delay_seconds", 4) or 0))
        if delay:
            time.sleep(delay)

        with _write_lock:
            auto_sent[chat_id] = (time.time(), reply)
        sent = plbot.send_message(chat_id, reply)
        _count_reply()
        logger.info(
            "%s Автоответ отправлен: chat=%s buyer=%s chars=%s text=%.120r",
            PREFIX, chat_id, buyer_name, len(reply), reply,
        )
        if sent is None:
            logger.error("%s Не удалось подтвердить отправку сообщения Playerok: chat=%s", PREFIX, chat_id)
    except Exception as exc:
        logger.exception("%s Ошибка автоответа в чате %s", PREFIX, chat_id)
        try:
            notify_telegram(ai_msg(
                "ai_error",
                plbot,
                chat_id=chat_id,
                buyer=buyer_name,
                message=buyer_text[:300],
                error=str(exc)[:400],
            ) or f"🤖 ИИ не смог ответить в чате {chat_id}: {str(exc)[:300]}")
        except Exception:
            logger.error("%s Не удалось отправить уведомление в Telegram: %s", PREFIX, exc)


async def on_new_message(plbot: "PlayerokBot", event):
    global _limit_notified_date
    message = getattr(event, "message", None)
    chat = getattr(event, "chat", None)
    if message is None or chat is None:
        return

    buyer = getattr(message, "user", None)
    my_id = str(getattr(getattr(plbot, "playerok_account", None), "id", "") or "")
    chat_id = str(getattr(chat, "id", "") or "")

    if buyer and my_id and str(getattr(buyer, "id", "")) == my_id:
        # Своё сообщение: ручное учитывается для паузы, автоответ ИИ — нет.
        text = getattr(message, "text", None)
        if text:
            with _write_lock:
                last = auto_sent.get(chat_id)
                if last and last[1] == text:
                    auto_sent[chat_id] = (time.time(), last[1])
                else:
                    me_last_text[chat_id] = time.time()
        return

    text = str(getattr(message, "text", "") or "").strip()
    if not text:
        return

    config = _config()
    rules = config.get("rules", {})
    message_id = str(getattr(message, "id", "") or text)

    with _write_lock:
        seen = message_id in _seen_message_ids
        if not seen:
            _seen_message_ids.add(message_id)
            if len(_seen_message_ids) > 500:
                _seen_message_ids.clear()
    if seen:
        return

    buyer_name = str(getattr(buyer, "username", None) or getattr(buyer, "id", "?") or "?")

    def skip(reason: str):
        logger.info("%s Автоответ пропущен: chat=%s buyer=%s причина=%s", PREFIX, chat_id, buyer_name, reason)

    # Покупатель пометил сообщение «!» — хочет, чтобы отвечал сам продавец.
    if text.startswith("!"):
        return skip("покупатель пометил сообщение «!»")

    if not rules.get("enabled", True):
        return skip("автоответы выключены")
    if chat_id in (config.get("ignored_chats", []) or []):
        return skip("чат в чёрном списке")
    if rules.get("skip_completed_deals", True) and latest_deal_finished(chat):
        return skip("сделка уже завершена (подтверждена)")
    # «Только сделки»: отвечаем лишь в чатах, привязанных к сделкам.
    if not getattr(chat, "deals", None):
        return skip("чат не привязан к сделке")
    busy = holdboost_busy_chat_ids()
    if chat_id in busy:
        return skip("в чате работает автовыдача HoldBoost")
    if _in_quiet_hours(rules):
        return skip("тихие часы")

    today = datetime.now().strftime("%Y-%m-%d")
    if _daily_replies() >= int(rules.get("daily_limit", 150) or 150):
        if _limit_notified_date != today:
            _limit_notified_date = today
            notify_telegram(f"🤖 ИИ-автоответы: достигнут дневной лимит ({rules.get('daily_limit', 150)}). Ответы приостановлены до завтра.")
        return skip("дневной лимит ответов")

    pause_minutes = float(rules.get("pause_after_seller_minutes", 5) or 0)
    if pause_minutes > 0:
        with _write_lock:
            last_manual = me_last_text.get(chat_id, 0)
            last_auto = auto_sent.get(chat_id, (0, ""))[0]
        if time.time() - last_manual < pause_minutes * 60:
            return skip("продавец писал в чат недавно")
        # После автоответа ждём только короткую паузу, не полную.
        if time.time() - last_auto < 20:
            return skip("автоответ отправлен только что")

    logger.info("%s Готовлю автоответ: chat=%s buyer=%s text=%.80r", PREFIX, chat_id, buyer_name, text)
    await asyncio.to_thread(process_reply, plbot, chat, buyer_name, text, my_id)
