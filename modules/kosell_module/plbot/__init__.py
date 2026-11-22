from __future__ import annotations

import asyncio
from collections import defaultdict
from logging import getLogger
import re
from threading import Lock
import time

from playerokapi.enums import ItemDealStatuses

from ..data import Data
from ..kosell_api import KOSellAPI, KOSellAPIError
from ..meta import NAME
from ..settings import Settings
from ..validation import valid_template
from settings import Settings as MainSettings
from tgbot.telegrambot import get_telegram_bot, get_telegram_bot_loop

logger = getLogger(NAME)
_delivery_lock = Lock()


def _cfg():
    return Settings.get("config")["playerok"]


def _unwrap(value):
    if isinstance(value, dict):
        for key in ("data", "rental", "result"):
            nested = value.get(key)
            if isinstance(nested, dict):
                return nested
    return value if isinstance(value, dict) else {}


BUYER_ERROR_MESSAGE = "⚠️ Возникла ошибка, я позвал продавца, чтобы он решил проблему."


def _send_failure_message(plbot, chat_id):
    try:
        plbot.account.send_message(chat_id=str(chat_id), text=BUYER_ERROR_MESSAGE)
        logger.info("Покупателю отправлено сообщение об ошибке: chat=%s", chat_id)
    except Exception:
        logger.exception("Не удалось сообщить покупателю о задержке автовыдачи")


async def _notify_admins_async(text):
    telegram = get_telegram_bot()
    if not telegram or not getattr(telegram, "bot", None):
        raise RuntimeError("Telegram-бот Playerok Universal не запущен")
    config = MainSettings.get("config")
    users = config.get("telegram", {}).get("bot", {}).get("signed_users", [])
    if not users:
        raise RuntimeError("Нет авторизованных пользователей Telegram для уведомления")
    for user_id in users:
        try:
            await telegram.bot.send_message(chat_id=user_id, text=text)
        except Exception:
            logger.exception("Не удалось отправить уведомление об ошибке пользователю Telegram %s", user_id)


def _notify_admins(deal_id: str, mapping: dict, error: str):
    loop = get_telegram_bot_loop()
    if loop is None or not loop.is_running():
        logger.error("Не удалось уведомить продавца в Telegram: event loop не запущен; deal=%s; error=%s", deal_id, error)
        return
    text = (
        "⚠️ Ошибка автовыдачи KOSell\n"
        f"Сделка Playerok: {deal_id}\n"
        f"Товар: {mapping.get('item_name', mapping.get('item_id', '—'))}\n"
        f"Игра: {mapping.get('product_name', mapping.get('product_id', '—'))}\n"
        f"Причина: {error}"
    )
    logger.warning("Продавцу отправляется уведомление об ошибке: deal=%s reason=%s", deal_id, error)
    try:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            loop.create_task(_notify_admins_async(text))
        else:
            asyncio.run_coroutine_threadsafe(_notify_admins_async(text), loop).result(timeout=15)
    except Exception:
        logger.exception("Не удалось отправить Telegram-уведомление об ошибке сделки %s", deal_id)


def _report_error(plbot, chat_id: str, deal_id: str, mapping: dict, error: str):
    logger.error("Ошибка автовыдачи: deal=%s chat=%s error=%s", deal_id, chat_id, error)
    _notify_admins(deal_id, mapping, error)
    _send_failure_message(plbot, chat_id)


def _deliver_sync(plbot, event, mapping, deal_id: str, chat_id: str):
    with _delivery_lock:
        logger.info(
            "Начата автовыдача KOSell: deal=%s chat=%s item=%s product=%s hours=%s",
            deal_id,
            chat_id,
            mapping.get("item_id"),
            mapping.get("product_id"),
            mapping.get("hours"),
        )
        state = Data.get("rental_orders") or {"orders": {}, "rentals": {}}
        orders = state.setdefault("orders", {})
        record = orders.get(deal_id, {})
        if record.get("status") == "delivered":
            logger.info("Повторное событие сделки %s пропущено: аренда уже выдана", deal_id)
            return

        config = _cfg()
        if not config.get("enabled", True):
            return
        template = config.get("delivery_template")
        if not valid_template(template):
            _report_error(plbot, chat_id, deal_id, mapping, "некорректный шаблон выдачи; исправьте его в /kosellrent")
            return
        if not config.get("api_key"):
            _report_error(plbot, chat_id, deal_id, mapping, "API-ключ KOSell не задан")
            return
        api = KOSellAPI(config.get("api_key", ""))
        rental_uid = record.get("rental_uid")
        if not rental_uid:
            if record.get("status"):
                logger.error("Deal %s has uncertain KOSell rent state; refusing duplicate rent", deal_id)
                _report_error(plbot, chat_id, deal_id, mapping, "у сделки уже есть аренда с неопределённым статусом; повторный заказ остановлен")
                return
            # Запись до запроса предотвращает повторное списание, если событие придёт повторно.
            orders[deal_id] = {
                "status": "creating",
                "item_id": str(mapping["item_id"]),
                "product_id": str(mapping["product_id"]),
                "hours": int(mapping["hours"]),
            }
            Data.set("rental_orders", state)
            logger.info("Создана запись заказа KOSell до API-запроса: deal=%s", deal_id)
            try:
                rental = _unwrap(api.rent(int(mapping["product_id"]), int(mapping["hours"]), mapping.get("currency", "RUB")))
            except KOSellAPIError as exc:
                orders[deal_id] = {**orders[deal_id], "status": "failed", "error_code": exc.code}
                Data.set("rental_orders", state)
                logger.error("KOSell rent failed for Playerok deal %s: %s", deal_id, exc.code)
                _report_error(plbot, chat_id, deal_id, mapping, f"ошибка KOSell API: {exc.code}")
                return
            rental_uid = rental.get("rental_uid") or rental.get("uid")
            if not rental_uid:
                orders[deal_id] = {**orders[deal_id], "status": "unknown", "error_code": "missing_rental_uid"}
                Data.set("rental_orders", state)
                logger.error("KOSell rent response had no rental id for Playerok deal %s", deal_id)
                _report_error(plbot, chat_id, deal_id, mapping, "KOSell не вернул идентификатор аренды")
                return
            orders[deal_id] = {
                **orders[deal_id],
                "status": "rented",
                "rental_uid": str(rental_uid),
                "login": str(rental.get("steam_login") or rental.get("login") or ""),
            }
            Data.set("rental_orders", state)
            logger.info("KOSell подтвердил создание аренды: deal=%s rental=%s", deal_id, rental_uid)

        try:
            credentials = _unwrap(api.credentials(str(rental_uid)))
        except KOSellAPIError as exc:
            logger.error("KOSell credentials unavailable for deal %s: %s", deal_id, exc.code)
            _report_error(plbot, chat_id, deal_id, mapping, f"не удалось получить данные аренды из KOSell: {exc.code}")
            return
        login = str(credentials.get("steam_login") or credentials.get("login") or orders.get(deal_id, {}).get("login") or "")
        password = str(credentials.get("steam_password") or credentials.get("password") or "")
        logger.info(
            "Получены данные аренды KOSell: deal=%s login_present=%s password_present=%s",
            deal_id,
            bool(login),
            bool(password),
        )
        if not login or not password:
            logger.error("KOSell returned incomplete credentials for Playerok deal %s", deal_id)
            _report_error(plbot, chat_id, deal_id, mapping, "KOSell вернул неполные данные аккаунта")
            return

        message = template.format_map(defaultdict(str, {
            "game": mapping.get("product_name", "игра"),
            "login": login,
            "password": password,
            "hours": int(mapping["hours"]),
            "duration": f"{int(mapping['hours'])} ч.",
        }))
        if "!код" not in message.casefold() and "!code" not in message.casefold():
            message += "\n🔐 Steam Guard код: напишите !код"
        if "продлить аренду" not in message.casefold():
            message += "\n🔁 Чтобы продлить аренду, напишите продавцу минимум за час до окончания срока."
        try:
            plbot.account.send_message(chat_id=str(chat_id), text=message)
            logger.info("Данные аренды отправлены покупателю: deal=%s chat=%s", deal_id, chat_id)
        except Exception as exc:
            logger.exception("Could not send rental credentials to Playerok deal %s", deal_id)
            _report_error(plbot, chat_id, deal_id, mapping, f"не удалось отправить сообщение Playerok ({type(exc).__name__})")
            return

        orders[deal_id] = {
            **orders.get(deal_id, {}),
            "status": "delivered",
            "playerok_status": "confirmation_pending",
            "item_id": str(mapping["item_id"]),
            "product_id": str(mapping["product_id"]),
            "hours": int(mapping["hours"]),
            "rental_uid": str(rental_uid),
        }
        rentals = state.setdefault("rentals", {})
        chat_rentals = rentals.setdefault(str(chat_id), [])
        if not any(str(r.get("deal_id")) == deal_id for r in chat_rentals):
            chat_rentals.append({
                "deal_id": deal_id,
                "rental_uid": str(rental_uid),
                "login": login,
                "item_id": str(mapping["item_id"]),
                "item_name": str(mapping.get("item_name", "")),
                "product_id": str(mapping["product_id"]),
                "product_name": str(mapping.get("product_name", "игра")),
                "hours": int(mapping["hours"]),
                "created_at": time.time(),
            })
        Data.set("rental_orders", state)
        logger.info("Аренда сохранена в истории чата: deal=%s chat=%s", deal_id, chat_id)
        try:
            plbot.account.update_deal(deal_id, ItemDealStatuses.SENT)
        except Exception as exc:
            orders[deal_id]["playerok_status"] = "confirmation_failed"
            orders[deal_id]["playerok_status_error"] = type(exc).__name__
            Data.set("rental_orders", state)
            logger.exception("Rental delivered but could not mark Playerok deal %s as SENT", deal_id)
            _notify_admins(
                deal_id,
                mapping,
                f"аренда выдана и данные отправлены, но Playerok не удалось отметить сделку выполненной ({type(exc).__name__})",
            )
        else:
            orders[deal_id]["playerok_status"] = "sent"
            Data.set("rental_orders", state)
            logger.info("Rental delivered and Playerok deal %s marked as SENT", deal_id)


async def on_new_deal(plbot, event):
    """Заказывает аренду для привязанного товара и отправляет данные в чат сделки."""
    mapping = None
    deal_id = "неизвестна"
    chat_id = ""
    try:
        deal = event.deal
        item = getattr(deal, "item", None)
        item_id = str(getattr(item, "id", "") or "")
        item_name = str(getattr(item, "name", "") or "")
        config = _cfg()
        if not config.get("enabled", True):
            return
        mappings = config.get("mappings", [])
        mapping = next((m for m in mappings if str(m.get("item_id")) == item_id), None)
        if mapping is None and item_name.strip():
            normalized_name = " ".join(item_name.split()).casefold()
            candidates = [m for m in mappings if " ".join(str(m.get("item_name") or "").split()).casefold() == normalized_name]
            if len(candidates) == 1:
                mapping = candidates[0]
                logger.info("Связка KOSell найдена по полному названию: item_id=%s mapped_item_id=%s", item_id, mapping.get("item_id"))
        if not mapping:
            logger.warning(
                "KOSell автовыдача пропущена: для товара нет связки; deal=%s item_id=%s item_name=%r mappings=%d",
                getattr(deal, "id", ""), item_id, item_name, len(mappings),
            )
            return
        deal_id = str(getattr(deal, "id", "") or "")
        chat_id = str(getattr(getattr(event, "chat", None), "id", "") or "")
        logger.info(
            "Получена оплаченная сделка для связки KOSell: deal=%s item_id=%s item_name=%r chat=%s",
            deal_id, item_id, item_name, chat_id,
        )
        if not deal_id or not chat_id:
            logger.error("Playerok NEW_DEAL event lacks a deal or chat id")
            if chat_id and mapping:
                _report_error(plbot, chat_id, deal_id or "неизвестна", mapping, "событие Playerok не содержит ID сделки или чата")
            return
        await asyncio.to_thread(_deliver_sync, plbot, event, mapping, deal_id, chat_id)
    except Exception as exc:
        logger.exception("Автовыдача для Playerok завершилась ошибкой")
        if mapping and chat_id:
            await asyncio.to_thread(_report_error, plbot, chat_id, deal_id, mapping, f"внутренняя ошибка модуля ({type(exc).__name__})")


async def on_new_message(plbot, event):
    """Отправляет покупателю Steam Guard код по команде !код [логин]."""
    message = getattr(event, "message", None)
    sender = getattr(message, "user", None)
    text = str(getattr(message, "text", "") or "").strip()
    if not sender or str(getattr(sender, "id", "")) == str(plbot.account.id):
        return
    match = re.fullmatch(r"!(?:код|code)(?:\s+(.+))?", text, flags=re.IGNORECASE)
    if not match:
        return

    chat_id = str(getattr(getattr(event, "chat", None), "id", "") or "")
    if not chat_id:
        return
    logger.info("Получена команда Steam Guard: chat=%s login_specified=%s", chat_id, bool(match.group(1)))
    state = Data.get("rental_orders") or {"orders": {}, "rentals": {}}
    rentals = state.get("rentals", {}).get(chat_id, [])
    # Old rentals remain in history but must not receive Guard codes after
    # their local rental period ends. Prefer the newest rental for a login.
    now = time.time()
    rentals = sorted(
        (r for r in rentals if float(r.get("created_at", 0)) + int(r.get("hours", 0)) * 3600 > now),
        key=lambda r: float(r.get("created_at", 0)), reverse=True,
    )
    login_arg = (match.group(1) or "").strip()
    if login_arg:
        target = next((r for r in rentals if str(r.get("login", "")).casefold() == login_arg.casefold()), None)
    elif len(rentals) == 1:
        target = rentals[0]
    elif len(rentals) > 1:
        logins = "\n".join(f"• {r.get('login', 'аккаунт')}" for r in rentals)
        reply = "В этом чате несколько арендованных аккаунтов. Укажите логин: !код логин\n" + logins
        await asyncio.to_thread(plbot.account.send_message, chat_id=chat_id, text=reply)
        return
    else:
        target = None

    if not target:
        await asyncio.to_thread(
            _report_error, plbot, chat_id, "неизвестна", {},
            "команда !код получена, но аренда в этом чате не найдена",
        )
        return

    config = _cfg()
    mapping = {
        "item_id": target.get("item_id", ""),
        "item_name": target.get("item_name", ""),
        "product_id": target.get("product_id", ""),
        "product_name": target.get("product_name", ""),
    }
    api = KOSellAPI(config.get("api_key", ""))
    try:
        code_data = _unwrap(await asyncio.to_thread(api.code, str(target["rental_uid"])))
        code = code_data.get("code")
        ttl = int(code_data.get("expires_in", 30) or 30)
        if not code:
            raise KOSellAPIError("steam_guard_code_unavailable")
        if ttl < 5:
            await asyncio.sleep(max(1, ttl) + 1)
            code_data = _unwrap(await asyncio.to_thread(api.code, str(target["rental_uid"])))
            code = code_data.get("code")
            ttl = int(code_data.get("expires_in", 30) or 30)
            if not code:
                raise KOSellAPIError("steam_guard_code_unavailable")
    except (KOSellAPIError, ValueError, KeyError, TypeError) as exc:
        reason = exc.code if isinstance(exc, KOSellAPIError) else type(exc).__name__
        await asyncio.to_thread(_report_error, plbot, chat_id, str(target.get("deal_id", "?")), mapping, f"не удалось получить Steam Guard код: {reason}")
        return

    reply = f"🔐 Steam Guard код для {target.get('login', '')}: {code}\nДействует примерно {ttl} сек."
    try:
        await asyncio.to_thread(plbot.account.send_message, chat_id=chat_id, text=reply)
        logger.info("Steam Guard код отправлен покупателю: deal=%s chat=%s ttl=%s", target.get("deal_id", "?"), chat_id, ttl)
    except Exception as exc:
        await asyncio.to_thread(_report_error, plbot, chat_id, str(target.get("deal_id", "?")), mapping, f"не удалось отправить Steam Guard код ({type(exc).__name__})")
