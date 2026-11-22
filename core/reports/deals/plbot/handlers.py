from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from logging import getLogger
from threading import Lock
from zoneinfo import ZoneInfo

from playerokapi.enums import ItemDealDirections
from settings import Settings as MainSettings
from tgbot.telegrambot import get_telegram_bot, get_telegram_bot_loop

from ..data import Data
from ..meta import NAME

logger = getLogger(NAME)
REPORT_TZ = ZoneInfo("Europe/Kaliningrad")
_save_lock = Lock()
_history_sync_lock = Lock()
MAX_HISTORY_PAGES = 500


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            if not re.search(r"[T ]\d{2}:\d{2}", str(value)):
                return None
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_record(record) -> bool:
    return isinstance(record, dict) and bool(record.get("deal_id")) and record.get("time_source") not in {"unknown", "event_received"} and _as_datetime(record.get("created_at")) is not None


def save_history() -> list[dict]:
    with _save_lock:
        state = Data.get("deals") or {"deals": []}
        state["deals"] = [record for record in state.get("deals", []) if _valid_record(record)]
        state["deals"].sort(key=lambda record: str(record["created_at"]))
        Data.set("deals", state)
        return state["deals"]


def _title(deal) -> str:
    item = getattr(deal, "item", None)
    return str(
        getattr(item, "name", None)
        or getattr(item, "title", None)
        or getattr(deal, "title", None)
        or "Товар без названия"
    )


def _save_deal(record: dict) -> bool:
    if not _valid_record(record):
        return False
    with _save_lock:
        state = Data.get("deals") or {"deals": []}
        records = [saved for saved in state.get("deals", []) if _valid_record(saved)]
        state["deals"] = records
        deal_id = record.get("deal_id")
        existing = next((index for index, saved in enumerate(records)
                         if deal_id and str(saved.get("deal_id")) == str(deal_id)), None)
        is_new = existing is None
        if existing is None:
            records.append(record)
        else:
            records[existing] = {**records[existing], **record}
        records.sort(key=lambda saved: str(saved.get("created_at", "")))
        Data.set("deals", state)
    return is_new


def _merge_history(history: list[dict]) -> None:
    with _save_lock:
        state = Data.get("deals") or {"deals": []}
        merged = {
            str(record.get("deal_id")): record
            for record in state.setdefault("deals", [])
            if _valid_record(record)
        }
        for record in history:
            deal_id = str(record.get("deal_id", ""))
            if not _valid_record(record):
                continue
            previous = merged.get(deal_id, {})
            merged[deal_id] = {**previous, **record}
        state["deals"] = sorted(
            merged.values(), key=lambda saved: str(saved.get("created_at", ""))
        )
        Data.set("deals", state)


def _fetch_all_seller_deals(account) -> tuple[list[dict], bool]:
    """Gets seller deals page-by-page and always has a finite stop condition."""
    records = []
    cursor = None
    seen_cursors = set()
    truncated = False
    for page_number in range(1, MAX_HISTORY_PAGES + 1):
        page = account.get_deals(
            direction=ItemDealDirections.OUT,
            count=24,
            after_cursor=cursor,
        )
        page_deals = getattr(page, "deals", []) or []
        logger.info("История сделок Playerok: страница=%s сделок=%s", page_number, len(page_deals))
        for deal in page_deals:
            created_at = _as_datetime(getattr(deal, "created_at", None))
            if created_at is None:
                logger.info("Сделка без времени создания пропущена: deal=%s", getattr(deal, "id", ""))
                continue
            records.append({
                "deal_id": str(getattr(deal, "id", "") or ""),
                "title": _title(deal),
                "created_at": created_at.isoformat(),
                "time_source": "deal",
            })

        page_info = getattr(page, "page_info", None)
        if not page_info:
            truncated = bool(getattr(page, "deals", []))
            break
        if not getattr(page_info, "has_next_page", False):
            break
        next_cursor = getattr(page_info, "end_cursor", None)
        if not next_cursor or next_cursor == cursor or next_cursor in seen_cursors:
            truncated = True
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        truncated = True
        logger.warning("История сделок Playerok остановлена на лимите страниц: %s", MAX_HISTORY_PAGES)

    # Keep even unusually old/closed deals; only collapse duplicate IDs.
    unique = {}
    for record in records:
        deal_id = record.get("deal_id")
        key = deal_id or f"{record['created_at']}:{record['title']}"
        unique[key] = record
    return sorted(unique.values(), key=lambda value: value.get("created_at", ""), reverse=True), truncated


def sync_seller_history(account) -> tuple[int, bool] | None:
    """Synchronizes history once. A parallel /deals request does not start a second crawl."""
    if not _history_sync_lock.acquire(blocking=False):
        return None
    try:
        history, truncated = _fetch_all_seller_deals(account)
        _merge_history(history)
        logger.info("История сделок Playerok синхронизирована: deals=%s truncated=%s", len(history), truncated)
        return len(history), truncated
    finally:
        _history_sync_lock.release()


async def _send_to_sellers(text: str) -> None:
    telegram = get_telegram_bot()
    if telegram is None or getattr(telegram, "bot", None) is None:
        raise RuntimeError("Telegram-бот Playerok не запущен")
    users = MainSettings.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    if not users:
        raise RuntimeError("В Telegram-боте нет авторизованных пользователей для уведомления")
    for user_id in users:
        try:
            await telegram.bot.send_message(chat_id=user_id, text=text)
        except Exception:
            logger.exception("Не удалось отправить уведомление о сделке пользователю %s", user_id)


def _notify_sellers(text: str) -> None:
    loop = get_telegram_bot_loop()
    if loop is None or not loop.is_running():
        logger.error("Не удалось отправить уведомление о сделке: Telegram event loop не запущен")
        return
    try:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            loop.create_task(_send_to_sellers(text))
        else:
            asyncio.run_coroutine_threadsafe(_send_to_sellers(text), loop).result(timeout=15)
    except Exception:
        logger.exception("Ошибка уведомления продавца о новой сделке")


async def on_new_deal(plbot, event) -> None:
    deal = getattr(event, "deal", None)
    if deal is None:
        logger.warning("Playerok NEW_DEAL событие пришло без объекта сделки")
        return

    deal_id = str(getattr(deal, "id", "") or "")
    title = _title(deal)
    created_at = _as_datetime(getattr(deal, "created_at", None))
    if created_at is None:
        logger.info("Новая сделка без времени создания пропущена: deal=%s", deal_id)
        return
    record = {
        "deal_id": deal_id,
        "title": title,
        "created_at": created_at.isoformat(),
        "time_source": "deal",
    }
    if not _save_deal(record):
        logger.info("Повторное событие Playerok NEW_DEAL проигнорировано: deal=%s", deal_id)
        return

    logger.info("Сохранена новая сделка Playerok: id=%s title=%r created_at=%s", deal_id, title, record["created_at"])
