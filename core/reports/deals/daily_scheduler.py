import asyncio
from datetime import datetime, timedelta
from logging import getLogger
from threading import Event, Lock, Thread
import time

from settings import Settings as MainSettings
from plbot.playerokbot import get_playerok_bot
from tgbot.telegrambot import get_telegram_bot, get_telegram_bot_loop

from .daily_report import REPORT_TZ, fetch_report, format_report
from .data import Data
from .meta import NAME

logger = getLogger(NAME)
ACTIVE = Event()
_thread = None
_start_lock = Lock()
_report_lock = Lock()
_inflight = {}


def due_day(now):
    now = now.astimezone(REPORT_TZ)
    return now.date() - timedelta(days=1) if now.hour >= 20 else None


def deliver_daily(now=None):
    if not ACTIVE.is_set() or not _report_lock.acquire(blocking=False):
        return
    try:
        day = due_day(now or datetime.now(REPORT_TZ))
        if day is None:
            return
        config = MainSettings.get("config") or {}
        users = list(dict.fromkeys(str(user) for user in config.get("telegram", {}).get("bot", {}).get("signed_users", [])))
        bot, telegram, loop = get_playerok_bot(), get_telegram_bot(), get_telegram_bot_loop()
        if not users or bot is None or telegram is None or loop is None or not loop.is_running():
            return
        state = Data.get("daily_reports")
        key = day.isoformat()
        recipients = state["sent"].setdefault(key, [])
        pending = [user for user in users if user not in recipients]
        if not pending:
            return
        report = state["reports"].get(key)
        if report is None:
            report = fetch_report(bot.account, day)
            state["reports"][key] = report
            Data.set("daily_reports", state)
        text = format_report(report)
        for user in pending:
            if not ACTIVE.is_set():
                return
            token = (key, user)
            previous = _inflight.get(token)
            if previous is not None:
                if not previous.done():
                    continue
                del _inflight[token]
                try:
                    previous.result()
                except Exception as exc:
                    logger.error("Ошибка отложенной отправки: user=%s error=%s", user, type(exc).__name__)
                    continue
                recipients.append(user)
                Data.set("daily_reports", state)
                continue
            future = asyncio.run_coroutine_threadsafe(telegram.bot.send_message(chat_id=int(user), text=text), loop)
            try:
                future.result(timeout=30)
            except TimeoutError:
                _inflight[token] = future
                continue
            except Exception as exc:
                logger.error("Ошибка отправки ежедневного отчёта: user=%s error=%s", user, type(exc).__name__)
                continue
            recipients.append(user)
            Data.set("daily_reports", state)
            logger.info("Ежедневный отчёт отправлен: day=%s user=%s", key, user)
    finally:
        _report_lock.release()


def start():
    global _thread
    ACTIVE.set()
    with _start_lock:
        if _thread and _thread.is_alive():
            return
        def run():
            while True:
                if ACTIVE.wait(30):
                    try:
                        deliver_daily()
                    except Exception as exc:
                        logger.error("Не удалось сформировать ежедневный отчёт: %s", type(exc).__name__)
                    time.sleep(30)
        _thread = Thread(target=run, daemon=True, name="deals-daily-report")
        _thread.start()


async def on_disabled(module):
    ACTIVE.clear()
