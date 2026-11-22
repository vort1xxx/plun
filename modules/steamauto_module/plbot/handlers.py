import asyncio
import json
import re
import time
import traceback
from logging import getLogger
from threading import Event as ThreadingEvent, Thread
from typing import TYPE_CHECKING, Any

import requests
from colorama import Fore

from playerokapi.enums import ItemDealStatuses
from playerokapi.listener.events import NewDealEvent, NewMessageEvent
from settings import Settings as global_sett
from tgbot.telegrambot import get_telegram_bot, get_telegram_bot_loop

from ..data import Data as data
from ..meta import NAME, PREFIX
from ..settings import DATA, Settings as sett

if TYPE_CHECKING:
    from plbot.playerokbot import PlayerokBot


logger = getLogger(f"{NAME}.playerok")

config = sett.get("config")
deals = data.get("deals")
stop_cycles_event = ThreadingEvent()
active_threads: set[str] = set()
unmapped_items_notified: set[str] = set()

STEAM_PROFILE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?steamcommunity\.com/(?:profiles/\d{17}|id/[A-Za-z0-9_.\-]{2,64})/?",
    flags=re.IGNORECASE,
)


def msg(message_name: str, plbot: "PlayerokBot", **kwargs) -> str | None:
    return plbot.msg(message_name, "messages", DATA, **kwargs)


def save_deals() -> None:
    data.set("deals", deals)


def _log_telegram_send_failure(future, user_id: int | str) -> None:
    try:
        error = future.exception()
    except Exception as exc:
        error = exc
    if error:
        logger.error(
            "%s Не удалось отправить сообщение об ошибке пользователю Telegram %s: %s",
            PREFIX,
            user_id,
            error,
        )


def set_deal_state(deal_id: str, **values: Any) -> None:
    current = deals.setdefault(deal_id, {})
    old_status = current.get("status")
    current.update(values)
    current["updated_at"] = int(time.time())
    save_deals()
    if "status" in values and values["status"] != old_status:
        logger.info(
            "%s Состояние сделки изменено: deal=%s %s -> %s",
            PREFIX,
            deal_id,
            old_status or "—",
            values["status"],
        )


def extract_amount_from_item_name(item_name: str) -> int | None:
    keyword = re.escape(config["playerok"]["item_keyword"])
    # Без \b на конце: "наград", "награда", "награды" — одно словоосновы.
    match = re.search(rf"(\d+)\s*{keyword}", item_name, flags=re.IGNORECASE)
    if not match:
        return None

    amount = int(match.group(1))
    if amount < 1:
        return None
    return amount


def get_reward_amount(item_id: str, item_name: str) -> int | None:
    """Ищет связку по ID, затем по полному названию; без связок — старый режим."""
    mappings = config.get("item_mappings", [])
    for mapping in mappings:
        if str(mapping.get("item_id")) == str(item_id):
            return int(mapping["amount"])
    if mappings:
        # Товар в сделке может иметь другой ID, чем опубликованное объявление:
        # сопоставляем только по полному сохранённому названию.
        normalized_name = " ".join(item_name.split()).casefold()
        matching_amounts = {
            int(mapping["amount"])
            for mapping in mappings
            if normalized_name
            and " ".join(str(mapping.get("item_name") or "").split()).casefold() == normalized_name
        }
        if len(matching_amounts) == 1:
            amount = matching_amounts.pop()
            logger.info(
                "%s Товар сопоставлен по полному названию: item_id=%s item_name=%r amount=%s",
                PREFIX, item_id, item_name, amount,
            )
            return amount
        if len(matching_amounts) > 1:
            logger.warning(
                "%s Неоднозначные привязки по названию: item_id=%s item_name=%r amounts=%s",
                PREFIX, item_id, item_name, sorted(matching_amounts),
            )
        return None
    return extract_amount_from_item_name(item_name)


def extract_steam_profile(text: str) -> str | None:
    match = STEAM_PROFILE_RE.search(text or "")
    if not match:
        return None

    profile = match.group(0).rstrip("/")
    if not profile.startswith(("http://", "https://")):
        profile = "https://" + profile
    # SteamSMM ожидает корректную ссылку: http приводим к https, www убираем.
    if profile.startswith("http://"):
        profile = "https://" + profile[len("http://"):]
    if profile.startswith("https://www."):
        profile = "https://" + profile[len("https://www."):]
    return profile


def extract_profile_from_deal(item_deal) -> str | None:
    """Ищет ссылку на профиль Steam в полях, заполненных покупателем при оформлении."""
    sources = []
    for source_fields in (
        getattr(item_deal, "obtaining_fields", None),
        getattr(item_deal, "automation_obtaining_fields", None),
    ):
        for field in (source_fields or []):
            value = getattr(field, "value", None)
            if value:
                sources.append(str(value))
    comment = getattr(item_deal, "comment_from_buyer", None)
    if comment:
        sources.append(str(comment))
    for source in sources:
        profile = extract_steam_profile(source)
        if profile:
            return profile
    return None


def steamsmm_request(method: str, path: str, **kwargs) -> dict:
    api_key = config["steamsmm"]["api_key"]
    if not api_key:
        raise RuntimeError("В config.json не заполнен steamsmm.api_key")

    logger.info("%s SteamSMM запрос: %s %s", PREFIX, method, path)
    try:
        response = requests.request(
            method,
            config["steamsmm"]["base_url"].rstrip("/") + path,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30,
            **kwargs
        )
    except requests.RequestException as exc:
        logger.exception("%s SteamSMM API недоступен: %s %s (%s)", PREFIX, method, path, type(exc).__name__)
        raise RuntimeError(f"SteamSMM connection error: {type(exc).__name__}") from exc

    try:
        result = response.json()
    except ValueError:
        result = {"raw": response.text}

    status = result.get("status") if isinstance(result, dict) else None
    code = result.get("code") if isinstance(result, dict) else None
    logger.info(
        "%s SteamSMM ответ: %s %s http=%s status=%s code=%s",
        PREFIX, method, path, response.status_code, status, code,
    )
    if response.status_code >= 400:
        message = result.get("message") if isinstance(result, dict) else None
        raise RuntimeError(
            f"SteamSMM HTTP {response.status_code} {code or ''}: {message or json.dumps(result, ensure_ascii=False)[:300]}"
        )
    return result


def create_reward_order(profile_url: str, amount: int) -> dict:
    """POST /api/order/create: награды на профиль. Заказ считается созданным
    только при status == success и наличии data.order_id (по документации)."""
    payload = {
        "action_type": str(config["steamsmm"].get("action_type", "reward_pack")),
        "quantity": int(amount),
        "target_link": profile_url,
    }
    response = steamsmm_request("POST", "/order/create", json=payload)
    if not isinstance(response, dict) or response.get("status") != "success":
        raise RuntimeError(json.dumps(response, ensure_ascii=False)[:400])
    order = response.get("data") or {}
    if not order.get("order_id"):
        raise RuntimeError(f"SteamSMM не вернул order_id: {json.dumps(response, ensure_ascii=False)[:400]}")
    return order


def get_reward_order_status(order_id: str) -> dict:
    response = steamsmm_request("GET", f"/order/status/{order_id}")
    if not isinstance(response, dict) or response.get("status") != "success":
        raise RuntimeError(json.dumps(response, ensure_ascii=False)[:400])
    return response.get("data") or {}


def wait_reward_result(order_id: str) -> dict:
    poll_attempts = int(config["steamsmm"]["poll_attempts"])
    poll_seconds = int(config["steamsmm"]["poll_seconds"])

    for attempt in range(1, poll_attempts + 1):
        order = get_reward_order_status(order_id)
        status = str(order.get("status", ""))
        logger.info(
            "%s Проверка заказа SteamSMM: order=%s attempt=%s/%s status=%s progress=%s",
            PREFIX,
            order_id,
            attempt,
            poll_attempts,
            status,
            order.get("progress"),
        )

        if status in {"completed", "failed", "cancelled"}:
            return order

        time.sleep(poll_seconds)

    raise TimeoutError(f"SteamSMM заказ {order_id} не завершился за отведенное время")


def notify_telegram(text: str) -> None:
    telegram_bot = get_telegram_bot()
    telegram_loop = get_telegram_bot_loop()
    if not telegram_bot or not telegram_loop:
        logger.error(text)
        return

    signed_users = telegram_bot.token and global_sett.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    if not signed_users:
        logger.warning("%s Ошибку нельзя отправить в Telegram: нет авторизованных пользователей", PREFIX)
        logger.error(text)
        return
    for user_id in signed_users:
        future = asyncio.run_coroutine_threadsafe(
            telegram_bot.bot.send_message(chat_id=user_id, text=text),
            telegram_loop
        )
        future.add_done_callback(lambda result, recipient=user_id: _log_telegram_send_failure(result, recipient))


def fetch_full_deal(plbot: "PlayerokBot", deal_id: str, fallback=None):
    """Свежая сделка: поля оформления покупателя есть в данных API, а не в событии."""
    for _ in range(3):
        try:
            return plbot.account.get_deal(deal_id)
        except Exception:
            time.sleep(2)
    return fallback


def process_deal(plbot: "PlayerokBot", deal_id: str) -> None:
    if deal_id in active_threads:
        logger.warning("%s Повторная обработка сделки проигнорирована: deal=%s", PREFIX, deal_id)
        return

    active_threads.add(deal_id)
    try:
        deal_data = deals[deal_id]
        set_deal_state(deal_id, status="PROCESSING")
        profile_url = deal_data["profile"]
        amount = int(deal_data["amount"])
        logger.info(
            "%s Создаю заказ SteamSMM: deal=%s amount=%s profile=%s",
            PREFIX, deal_id, amount, profile_url,
        )

        order = create_reward_order(profile_url, amount)
        order_id = order["order_id"]
        logger.info("%s Заказ SteamSMM создан: deal=%s order=%s cost=%s", PREFIX, deal_id, order_id, order.get("cost"))

        set_deal_state(deal_id, status="STEAM_ORDER_CREATED", steam_order_id=order_id)
        result = wait_reward_result(order_id)

        status = str(result.get("status", ""))
        if status != "completed":
            raise RuntimeError(f"SteamSMM заказ не выполнен: {json.dumps(result, ensure_ascii=False)[:400]}")

        plbot.send_message(deal_data["chat_id"], msg("done", plbot, amount=amount))
        logger.info("%s Покупателю отправлено подтверждение выдачи: deal=%s", PREFIX, deal_id)
        if config["playerok"]["auto_complete_deal"]:
            plbot.account.update_deal(deal_id, ItemDealStatuses.SENT)
            logger.info("%s Сделка Playerok автоматически подтверждена: deal=%s", PREFIX, deal_id)

        set_deal_state(deal_id, status="DONE")
        if config["playerok"]["log_states"]:
            logger.info(f"{PREFIX} {Fore.LIGHTWHITE_EX}{deal_id}{Fore.WHITE}: награды отправлены ({amount})")

    except Exception as exc:
        error = str(exc)
        logger.exception("%s Ошибка обработки сделки %s в SteamSMM", PREFIX, deal_id)
        set_deal_state(deal_id, status="ERROR", error=error)
        try:
            plbot.send_message(deals[deal_id]["chat_id"], msg("customer_error", plbot))
        except Exception:
            logger.error(f"{PREFIX} Не удалось отправить сообщение покупателю: {traceback.format_exc()}")

        notify_telegram(msg(
            "telegram_error",
            plbot,
            deal_id=deal_id,
            chat_id=deals.get(deal_id, {}).get("chat_id"),
            item_name=deals.get(deal_id, {}).get("item_name"),
            amount=deals.get(deal_id, {}).get("amount"),
            profile=deals.get(deal_id, {}).get("profile"),
            error=error
        ) or error)
    finally:
        active_threads.discard(deal_id)


async def on_new_deal(plbot: "PlayerokBot", event: NewDealEvent):
    deal_id = event.deal.id
    existing = deals.get(deal_id)
    if existing and existing.get("status") in {"WAIT_PROFILE", "PROCESSING", "STEAM_ORDER_CREATED", "DONE", "ERROR"}:
        return

    item = getattr(event.deal, "item", None)
    item_name = str(getattr(item, "name", "") or "")
    item_id = str(getattr(item, "id", "") or "")
    logger.info(
        "%s Получено событие оплаченной сделки: deal=%s item_id=%s item_name=%r",
        PREFIX,
        deal_id,
        item_id or "—",
        item_name or "—",
    )
    amount = get_reward_amount(item_id, item_name)
    if amount is None:
        configured = config.get("item_mappings", [])
        keyword = str(config.get("playerok", {}).get("item_keyword", "НАГРАД")).casefold()
        looks_like_reward = keyword and keyword in item_name.casefold()
        logger.warning(
            "%s Оплаченная сделка %s пропущена: товар не сопоставлен (item_id=%s, item_name=%r, mappings=%s)",
            PREFIX, deal_id, item_id or "—", item_name or "—", len(configured),
        )
        if looks_like_reward and item_id not in unmapped_items_notified:
            unmapped_items_notified.add(item_id)
            notify_telegram(
                "⚠️ SteamSMM пропустил оплаченную сделку: не найдена привязка товара.\n"
                f"Товар: {item_name or 'название не получено'}\n"
                f"ID товара: {item_id or 'не получен'}\n"
                f"Сделка: {deal_id}\n"
                "Откройте /steamauto и проверьте привязку по ссылке на товар."
            )
        return

    logger.info("%s Найдена оплата наград Steam: deal=%s item_id=%s amount=%s", PREFIX, deal_id, item_id, amount)

    # Ссылка на профиль берётся из полей оформления: сначала получаем свежую
    # сделку, т.к. в событии obtaining_fields может отсутствовать.
    full_deal = await asyncio.to_thread(fetch_full_deal, plbot, deal_id, event.deal)
    profile = extract_profile_from_deal(full_deal)

    deals[deal_id] = {
        "chat_id": event.chat.id,
        "buyer_id": event.deal.user.id if event.deal.user else None,
        "buyer_username": event.deal.user.username if event.deal.user else None,
        "item_name": item_name,
        "item_id": str(item_id),
        "amount": amount,
        "profile": profile or "",
        "status": "PROCESSING" if profile else "WAIT_PROFILE",
        "created_at": int(time.time()),
        "updated_at": int(time.time())
    }
    save_deals()

    if profile:
        logger.info("%s Ссылка на профиль Steam взята из поля оформления покупки: deal=%s profile=%s", PREFIX, deal_id, profile)
        plbot.send_message(
            event.chat.id,
            msg("process", plbot, profile=profile, amount=amount)
        )
        Thread(target=process_deal, args=(plbot, deal_id), daemon=True).start()
        return

    await asyncio.sleep(1)
    plbot.send_message(event.chat.id, msg("ask_profile", plbot))

    if config["playerok"]["log_states"]:
        logger.info(f"{PREFIX} {Fore.LIGHTWHITE_EX}{deal_id}{Fore.WHITE}: поля оформления без ссылки, ожидаю профиль Steam в чате")


async def on_new_message(plbot: "PlayerokBot", event: NewMessageEvent):
    global deals
    if event.message.text is None:
        return
    if event.message.user.id == plbot.playerok_account.id:
        return

    active_deal_id = None
    for deal_id, deal in deals.items():
        if deal.get("chat_id") == event.chat.id and deal.get("status") == "WAIT_PROFILE":
            active_deal_id = deal_id
            break

    if active_deal_id is None:
        return

    text = event.message.text.strip()
    profile = extract_steam_profile(text)
    if not profile:
        deal = deals[active_deal_id]
        # Ссылка могла появиться в поле оформления позже — перепроверяем сделку.
        full_deal = fetch_full_deal(plbot, active_deal_id)
        profile = extract_profile_from_deal(full_deal) if full_deal else None
        if not profile:
            logger.warning("%s Некорректная ссылка на профиль Steam: deal=%s", PREFIX, active_deal_id)
            plbot.send_message(event.chat.id, msg("bad_profile", plbot))
            return

    set_deal_state(active_deal_id, status="PROCESSING", profile=profile)
    logger.info("%s Ссылка на профиль Steam получена: deal=%s profile=%s", PREFIX, active_deal_id, profile)
    plbot.send_message(
        deals[active_deal_id]["chat_id"],
        msg("process", plbot, profile=profile, amount=deals[active_deal_id]["amount"])
    )
    Thread(target=process_deal, args=(plbot, active_deal_id), daemon=True).start()


async def run_cycles(_):
    def run():
        while True:
            global config, deals
            config = sett.get("config")
            deals = data.get("deals")
            if stop_cycles_event.wait(5):
                return

    stop_cycles_event.clear()
    Thread(target=run, daemon=True).start()
    logger.info("%s Запущен цикл обновления конфигурации и сделок", PREFIX)


async def stop_cycles(_):
    stop_cycles_event.set()
    logger.info("%s Остановлен цикл обновления конфигурации и сделок", PREFIX)
