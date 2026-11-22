import asyncio
from datetime import date, datetime
from html import escape
from logging import getLogger

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from settings import Settings as MainSettings
from tgbot.helpful import do_auth
from plbot.playerokbot import get_playerok_bot

from ..meta import NAME
from ..profit import fetch_profit_report, format_profit_report
from ..settings import Settings
from .states import ReportSetup

router = Router()
logger = getLogger(NAME)
CONFIG_KEY = "playerok_profit_report"


def _config():
    return Settings.get("config").get(CONFIG_KEY, {"daily_enabled": True, "daily_time": "21:00", "daily_last_sent": ""})


def _save(config):
    Settings.set("config", {CONFIG_KEY: config})


def _authorized(user_id):
    users = MainSettings.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(value) for value in users)


def _keyboard():
    cfg = _config()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Отчёт за период", callback_data="profitreport:period")],
        [InlineKeyboardButton(text=f"🕒 Ежедневный отчёт: {'вкл' if cfg.get('daily_enabled', True) else 'выкл'} · {cfg.get('daily_time', '21:00')}", callback_data="profitreport:time")],
        [InlineKeyboardButton(text="⏯ Включить/выключить ежедневный отчёт", callback_data="profitreport:toggle")],
        [InlineKeyboardButton(text="↩️ Все отчёты", callback_data="reports:menu")],
    ])


async def _show(message, note=""):
    cfg = _config()
    text = (
        "⚙️ <b>Отчёты Playerok</b>\n\n"
        "Показывает подтверждённые зачисления по продажам и продажи в ожидании или обработке.\n\n"
        f"Ежедневный отчёт: <b>{'включён' if cfg.get('daily_enabled', True) else 'выключен'}, {escape(str(cfg.get('daily_time', '21:00')))} (Europe/Kaliningrad)</b>."
    )
    if note:
        text += "\n\n" + note
    await message.answer(text, reply_markup=_keyboard(), parse_mode="HTML")


def _parse_period(raw: str):
    parts = raw.strip().split()
    if len(parts) != 2:
        raise ValueError("Введите две даты: ГГГГ-ММ-ДД ГГГГ-ММ-ДД")
    start, end = (date.fromisoformat(value) for value in parts)
    if end < start:
        raise ValueError("Конечная дата раньше начальной")
    if (end - start).days > 365:
        raise ValueError("Максимальный период — 366 дней")
    return start, end


def _split_report(text: str, limit: int = 3800) -> list[str]:
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if current and len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


async def _report_text(start: date, end: date):
    playerok = get_playerok_bot()
    if playerok is None:
        raise RuntimeError("Бот Playerok ещё не запущен")
    report = await asyncio.to_thread(fetch_profit_report, playerok.account, start, end)
    return format_profit_report(report)


async def _send_report(message, raw: str):
    try:
        start, end = _parse_period(raw)
        await message.answer("Собираю операции Playerok…")
        for chunk in _split_report(await _report_text(start, end)):
            await message.answer(chunk)
    except (ValueError, RuntimeError) as exc:
        await message.answer(escape(str(exc)))
    except Exception as exc:
        logger.exception("Не удалось сформировать отчёт Playerok")
        await message.answer(f"Не удалось получить отчёт из Playerok ({type(exc).__name__}).")


async def send_daily_report(telegram_bot, day: date):
    chunks = _split_report(await _report_text(day, day))
    users = MainSettings.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    if not users:
        raise RuntimeError("Нет авторизованных пользователей Telegram")
    for user_id in users:
        for chunk in chunks:
            await telegram_bot.bot.send_message(chat_id=user_id, text=chunk)


@router.message(Command("profit"))
async def profit_command(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1:
        return await _send_report(message, args[1])
    await state.set_state(ReportSetup.period)
    await message.answer("Введите период двумя датами, например: 2026-10-01 2026-10-03")


@router.message(ReportSetup.period, F.text)
async def period_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    await state.clear()
    await _send_report(message, message.text)


@router.message(Command(NAME))
async def settings_command(message: types.Message, state: FSMContext):
    await state.clear()
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await _show(message)


@router.callback_query(F.data == "profitreport:period")
async def period_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(ReportSetup.period)
    await callback.message.answer("Введите период двумя датами: ГГГГ-ММ-ДД ГГГГ-ММ-ДД")


@router.callback_query(F.data == "profitreport:toggle")
async def toggle_daily(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    cfg = _config()
    cfg["daily_enabled"] = not bool(cfg.get("daily_enabled", True))
    _save(cfg)
    await _show(callback.message, f"Ежедневный отчёт {'включён' if cfg['daily_enabled'] else 'выключен'}.")


@router.callback_query(F.data == "profitreport:time")
async def time_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(ReportSetup.daily_time)
    await callback.message.answer("Введите время ежедневного отчёта в формате ЧЧ:ММ (Europe/Kaliningrad), например 21:00.")


@router.message(ReportSetup.daily_time, F.text)
async def time_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    try:
        report_time = datetime.strptime(message.text.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        return await message.answer("Формат времени: ЧЧ:ММ, например 21:00.")
    cfg = _config()
    cfg["daily_time"] = report_time
    cfg["daily_enabled"] = True
    _save(cfg)
    await state.clear()
    await _show(message, f"Время ежедневного отчёта сохранено: {report_time}.")
