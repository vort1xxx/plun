import asyncio
import json
import re
import time
import traceback
from logging import getLogger
from threading import Event as ThreadingEvent, Thread
from types import SimpleNamespace
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


def msg(message_name: str, plbot: "PlayerokBot", **kwargs) -> str | None:
    return plbot.msg(message_name, "messages", DATA, **kwargs)


def _item_binding(deal_data: dict):
    # Объект с id/name для проверки исключений товаров в msg()
    item_id = str(deal_data.get("item_id") or "")
    item_name = str(deal_data.get("item_name") or "")
    if not item_id and not item_name:
        return None
    return SimpleNamespace(id=item_id or None, slug=None, name=item_name or None)


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
    match = re.search(rf"(\d+)\s*{keyword}\b", item_name, flags=re.IGNORECASE)
    if not match:
        return None

    amount = int(match.group(1))
    if amount < 1:
        return None
    return amount


def get_boost_amount(item_id: str, item_name: str) -> int | None:
    """Ищет связку по ID, затем по полному названию; без связок — старый режим."""
    mappings = config.get("boost_mappings", [])
    for mapping in mappings:
        if str(mapping.get("item_id")) == str(item_id):
            return int(mapping["amount"])
    if mappings:
        # The item attached to a purchase can have a different ID from the
        # published listing. Only match its full saved name, never just BOOST
        # or the number in the title, so unrelated listings stay excluded.
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


def extract_discord_invite(text: str) -> str | None:
    match = re.search(
        r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord\.com/invite)/[A-Za-z0-9-]+",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    invite = match.group(0)
    if not invite.startswith(("http://", "https://")):
        invite = "https://" + invite
    return invite


def extract_invite_from_deal(item_deal) -> str | None:
    """Ищет Discord-ссылку в полях, заполненных покупателем при оформлении."""
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
        invite = extract_discord_invite(source)
        if invite:
            return invite
    return None


def holdboost_request(method: str, path: str, **kwargs) -> dict:
    api_key = config["holdboost"]["api_key"]
    if not api_key:
        raise RuntimeError("В config.json не заполнен holdboost.api_key")

    request_data = kwargs.get("json") or {}
    safe_data = {
        key: request_data[key]
        for key in ("amount", "boost_months", "preset_id")
        if key in request_data
    }
    logger.info("%s HoldBoost API запрос: %s %s payload=%s", PREFIX, method, path, safe_data)
    try:
        response = requests.request(
            method,
            config["holdboost"]["base_url"].rstrip("/") + path,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json"
            },
            timeout=30,
            **kwargs
        )
    except requests.RequestException as exc:
        logger.exception("%s HoldBoost API недоступен: %s %s (%s)", PREFIX, method, path, type(exc).__name__)
        raise RuntimeError(f"HoldBoost connection error: {type(exc).__name__}") from exc

    try:
        result = response.json()
    except ValueError:
        result = {"raw": response.text}

    logger.info(
        "%s HoldBoost API ответ: %s %s status=%s success=%s keys=%s",
        PREFIX,
        method,
        path,
        response.status_code,
        result.get("success") if isinstance(result, dict) else None,
        sorted(result.keys()) if isinstance(result, dict) else [],
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HoldBoost HTTP {response.status_code}: {json.dumps(result, ensure_ascii=False)}")
    return result


def create_holdboost_order(invite: str, amount: int) -> dict:
    payload = {
        "guild_invite": invite,
        "amount": amount,
        "boost_months": config["holdboost"]["boost_months"]
    }
    preset_id = str(config.get("holdboost", {}).get("preset_id", "") or "").strip()
    # preset_id из POST /v1/external/orders: пресет персональной рекламы,
    # применяется без наценки и игнорируется при partner_ad.
    if preset_id:
        payload["preset_id"] = preset_id
    return holdboost_request("POST", "/v1/external/orders", json=payload)


def get_holdboost_order(id: str) -> dict:
    return holdboost_request("GET", f"/v1/external/orders/{id}")


def wait_holdboost_result(id: str) -> dict:
    poll_attempts = int(config["holdboost"]["poll_attempts"])
    poll_seconds = int(config["holdboost"]["poll_seconds"])

    for attempt in range(1, poll_attempts + 1):
        response = get_holdboost_order(id)

        if not response.get("success"):
            raise RuntimeError(json.dumps(response, ensure_ascii=False))

        order = response["data"]
        logger.info(
            "%s Проверка заказа HoldBoost: order=%s attempt=%s/%s status=%s boosts_done=%s",
            PREFIX,
            id,
            attempt,
            poll_attempts,
            order.get("status"),
            order.get("boosts_done"),
        )

        if order["status"] in {"COMPLETED", "FAILED"}:
            return order

        time.sleep(poll_seconds)

    raise TimeoutError(f"HoldBoost заказ {id} не завершился за отведенное время")


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
    """Свежая сделка: поля оформления buyer'а есть только в данных API, а не в событии."""
    for _ in range(3):
        try:
            return plbot.account.get_deal(deal_id)
        except Exception:
            time.sleep(2)
    return fallback


def process_confirmed_deal(plbot: "PlayerokBot", deal_id: str) -> None:
    if deal_id in active_threads:
        logger.warning("%s Повторное подтверждение сделки проигнорировано: deal=%s", PREFIX, deal_id)
        return

    active_threads.add(deal_id)
    try:
        deal_data = deals[deal_id]
        set_deal_state(deal_id, status="PROCESSING")
        logger.info(
            "%s Создаю заказ HoldBoost: deal=%s amount=%s months=%s preset_id=%s",
            PREFIX,
            deal_id,
            deal_data["amount"],
            config["holdboost"]["boost_months"],
            str(config.get("holdboost", {}).get("preset_id", "") or "") or "—",
        )
        try:
            plbot.send_message(
                deal_data["chat_id"],
                msg(
                    "process",
                    plbot,
                    server=deal_data.get("invite"),
                    amount=deal_data["amount"],
                    months=config["holdboost"]["boost_months"],
                    item=_item_binding(deal_data),
                )
            )
        except Exception:
            logger.error("%s Не удалось отправить сообщение о начале выдачи: deal=%s", PREFIX, deal_id)

        response = create_holdboost_order(
            deal_data["invite"],
            int(deal_data["amount"])
        )

        if not response.get("success"):
            raise RuntimeError(json.dumps(response, ensure_ascii=False))
        order = response["data"]
        order_id = order["id"]
        logger.info("%s Заказ HoldBoost создан: deal=%s order=%s", PREFIX, deal_id, order_id)

        set_deal_state(deal_id,status="HOLDBOOST_CREATED",holdboost_order_id=order_id)
        result = wait_holdboost_result(order_id)

        done = int(result.get("boosts_done") or 0)
        amount = int(deal_data["amount"])

        if result.get("status") != "COMPLETED" or done < amount:
            raise RuntimeError(f"HoldBoost заказ не выполнен полностью: {json.dumps(result, ensure_ascii=False)}")

        plbot.send_message(deal_data["chat_id"], msg("done", plbot, done=done, amount=amount, item=_item_binding(deal_data)))
        logger.info("%s Покупателю отправлено подтверждение выдачи: deal=%s", PREFIX, deal_id)
        if config["playerok"]["auto_complete_deal"]:
            plbot.account.update_deal(deal_id, ItemDealStatuses.SENT)
            logger.info("%s Сделка Playerok автоматически отмечена выполненной: deal=%s", PREFIX, deal_id)

        set_deal_state(deal_id, status="DONE", boosts_done=done)
        if config["playerok"]["log_states"]:
            logger.info(f"{PREFIX} {Fore.LIGHTWHITE_EX}{deal_id}{Fore.WHITE}: бусты выданы {done}/{amount}")

    except Exception as exc:
        error = str(exc)
        logger.exception("%s Ошибка обработки сделки %s в HoldBoost", PREFIX, deal_id)
        set_deal_state(deal_id, status="ERROR", error=error)
        try:
            plbot.send_message(deals[deal_id]["chat_id"], msg("customer_error", plbot, item=_item_binding(deals.get(deal_id, {}))))
        except Exception:
            logger.error(f"{PREFIX} Не удалось отправить сообщение покупателю: {traceback.format_exc()}")

        notify_telegram(msg(
            "telegram_error",
            plbot,
            deal_id=deal_id,
            chat_id=deals.get(deal_id, {}).get("chat_id"),
            item_name=deals.get(deal_id, {}).get("item_name"),
            amount=deals.get(deal_id, {}).get("amount"),
            invite=deals.get(deal_id, {}).get("invite"),
            error=error
        ) or error)
    finally:
        active_threads.discard(deal_id)


async def on_new_deal(plbot: "PlayerokBot", event: NewDealEvent):
    deal_id = event.deal.id
    existing = deals.get(deal_id)
    if existing and existing.get("status") in {"WAIT_INVITE", "PROCESSING", "HOLDBOOST_CREATED", "DONE", "ERROR"}:
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
    amount = get_boost_amount(item_id, item_name)
    if amount is None:
        configured = config.get("boost_mappings", [])
        keyword = str(config.get("playerok", {}).get("item_keyword", "BOOST")).casefold()
        looks_like_boost = keyword and keyword in item_name.casefold()
        logger.warning(
            "%s Оплаченная сделка %s пропущена: товар не сопоставлен (item_id=%s, item_name=%r, mappings=%s)",
            PREFIX, deal_id, item_id or "—", item_name or "—", len(configured),
        )
        # Don't message for ordinary unrelated listings. Boost-looking listings
        # get one actionable notice instead of being silently ignored.
        if looks_like_boost and item_id not in unmapped_items_notified:
            unmapped_items_notified.add(item_id)
            notify_telegram(
                "⚠️ HoldBoost пропустил оплаченную сделку: не найдена привязка товара.\n"
                f"Товар: {item_name or 'название не получено'}\n"
                f"ID товара: {item_id or 'не получен'}\n"
                f"Сделка: {deal_id}\n"
                "Откройте /holdboost и проверьте привязку по ссылке на товар."
            )
        return

    logger.info("%s Найдена оплата HoldBoost: deal=%s item_id=%s amount=%s", PREFIX, deal_id, item_id, amount)

    # Ссылка берётся из полей, заполненных покупателем при оформлении: сначала
    # получаем свежую сделку, т.к. в событии obtaining_fields может отсутствовать.
    full_deal = await asyncio.to_thread(fetch_full_deal, plbot, deal_id, event.deal)
    invite = extract_invite_from_deal(full_deal)

    deals[deal_id] = {
        "chat_id": event.chat.id,
        "buyer_id": event.deal.user.id if event.deal.user else None,
        "buyer_username": event.deal.user.username if event.deal.user else None,
        "item_name": item_name,
        "item_id": str(item_id),
        "amount": amount,
        "invite": invite or "",
        "status": "PROCESSING" if invite else "WAIT_INVITE",
        "created_at": int(time.time()),
        "updated_at": int(time.time())
    }
    save_deals()

    if invite:
        logger.info("%s Discord-ссылка взята из поля оформления покупки: deal=%s invite=%s", PREFIX, deal_id, invite)
        Thread(target=process_confirmed_deal, args=(plbot, deal_id), daemon=True).start()
        return

    await asyncio.sleep(1)
    plbot.send_message(event.chat.id, msg("ask_invite", plbot, item=_item_binding(deals.get(deal_id, {}))))

    if config["playerok"]["log_states"]:
        logger.info(f"{PREFIX} {Fore.LIGHTWHITE_EX}{deal_id}{Fore.WHITE}: поля оформления без ссылки, ожидаю Discord-ссылку в чате")


async def on_new_message(plbot: "PlayerokBot", event: NewMessageEvent):
    global deals
    if event.message.text is None:
        return
    if event.message.user.id == plbot.playerok_account.id:
        return

    active_deal_id = None
    for deal_id, deal in deals.items():
        if deal.get("chat_id") == event.chat.id and deal.get("status") == "WAIT_INVITE":
            active_deal_id = deal_id
            break

    if active_deal_id is None:
        return

    text = event.message.text.strip()
    invite = extract_discord_invite(text)
    if not invite:
        deal = deals[active_deal_id]
        # Ссылка может появиться в поле оформления позже — перепроверяем сделку.
        full_deal = fetch_full_deal(plbot, active_deal_id)
        invite = extract_invite_from_deal(full_deal) if full_deal else None
        if not invite:
            logger.warning("%s Некорректная Discord-ссылка: deal=%s", PREFIX, active_deal_id)
            plbot.send_message(event.chat.id, msg("bad_invite", plbot, item=_item_binding(deals.get(active_deal_id, {}))))
            return

    set_deal_state(active_deal_id, status="PROCESSING", invite=invite)
    logger.info("%s Discord-ссылка получена для сделки %s", PREFIX, active_deal_id)
    Thread(target=process_confirmed_deal, args=(plbot, active_deal_id), daemon=True).start()


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
