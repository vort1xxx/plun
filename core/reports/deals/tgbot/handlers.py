import asyncio
import json
from datetime import datetime, timedelta, timezone
from html import escape
from logging import getLogger
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from settings import Settings as MainSettings
from tgbot.helpful import do_auth
from plbot.playerokbot import get_playerok_bot

from ..meta import NAME
from ..plbot.handlers import save_history, sync_seller_history
from ..daily_report import REPORT_TZ as DAILY_TZ, fetch_report, format_report

router = Router()
REPORT_TZ = ZoneInfo("Europe/Kaliningrad")
logger = getLogger(NAME)
HISTORY_SYNC_TIMEOUT = 45


def history_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Отчёт за вчера", callback_data="dealsreport:daily")],
        [InlineKeyboardButton(text="💾 Сохранить историю", callback_data="dealsreport:save")],
        [InlineKeyboardButton(text="📤 Скачать историю JSON", callback_data="dealsreport:export")],
        [InlineKeyboardButton(text="↩️ Все отчёты", callback_data="reports:menu")],
    ])


@router.message(Command("deals_data"))
async def data_menu(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await message.answer("Ежедневный отчёт за вчера отправляется в 20:00 по Калининграду. История сохраняется в bot_data/reports/deals.json, отчёты — в bot_data/reports/daily_reports.json.", reply_markup=history_keyboard())


async def show_daily_report(message):
    playerok = get_playerok_bot()
    if playerok is None:
        return await message.answer("Playerok-бот ещё не запущен.")
    progress = await message.answer("⏳ Собираю отчёт за вчера…")
    try:
        day = datetime.now(DAILY_TZ).date() - timedelta(days=1)
        report = await asyncio.to_thread(fetch_report, playerok.account, day)
    except Exception:
        logger.exception("Не удалось сформировать отчёт за вчера")
        return await progress.edit_text("Не удалось получить полный отчёт. Проверьте доступ к Playerok и журнал бота.")
    await progress.edit_text(format_report(report), reply_markup=history_keyboard())


@router.message(Command("deals_report"))
async def daily_command(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await show_daily_report(message)


@router.callback_query(F.data == "dealsreport:daily")
async def daily_callback(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await show_daily_report(callback.message)


@router.callback_query(F.data == "dealsreport:save")
async def save_data(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    try:
        records = await asyncio.to_thread(save_history)
    except (OSError, ValueError):
        return await callback.message.answer("Не удалось сохранить историю. Проверьте файл данных и права на папку плагина.")
    await callback.message.answer(f"💾 Сохранено в bot_data/reports/deals.json в основном боте. Сделок с временем: {len(records)}.", reply_markup=history_keyboard())


@router.callback_query(F.data == "dealsreport:export")
async def export_data(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    try:
        records = await asyncio.to_thread(save_history)
    except (OSError, ValueError):
        return await callback.message.answer("Не удалось прочитать или сохранить историю. Проверьте файл данных и права на папку плагина.")
    document = BufferedInputFile(json.dumps({"deals": records}, ensure_ascii=False, indent=2).encode("utf-8"), filename="deals.json")
    await callback.message.answer_document(document, caption=f"История сделок с временем: {len(records)}. Копия также сохранена в плагине.")


def _authorized(user_id: int) -> bool:
    users = MainSettings.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(value) for value in users)


@router.message(Command("deals"))
async def deals_command(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)

    playerok = get_playerok_bot()
    if playerok is None:
        return await message.answer("Бот Playerok ещё не запущен. Попробуйте позже.")
    progress = await message.answer("⏳ Загружаю полную историю продаж Playerok…")
    truncated = False
    background_sync = False
    try:
        sync_result = await asyncio.wait_for(
            asyncio.to_thread(sync_seller_history, playerok.account),
            timeout=HISTORY_SYNC_TIMEOUT,
        )
        if sync_result is None:
            background_sync = True
            logger.info("/deals: история уже загружается в другом запросе")
        else:
            _, truncated = sync_result
    except asyncio.TimeoutError:
        # The worker continues and saves the result when Playerok responds,
        # while Telegram immediately gets a clear status instead of hanging.
        background_sync = True
        logger.warning("/deals: Playerok не ответил за %s секунд; синхронизация продолжена в фоне", HISTORY_SYNC_TIMEOUT)
    except Exception:
        logger.exception("Не удалось получить полную историю сделок Playerok")
        return await progress.edit_text("Не удалось загрузить историю сделок из Playerok. Проверьте журнал бота и попробуйте позже.")

    deals = sorted(
        await asyncio.to_thread(save_history),
        key=lambda deal: str(deal.get("created_at", "")),
        reverse=True,
    )
    if not deals:
        if background_sync:
            return await progress.edit_text(
                "⏳ Playerok отвечает дольше обычного. История продолжает загружаться в фоне; "
                "повторите /deals через минуту."
            )
        return await progress.edit_text("Не найдено сделок с временем создания.", reply_markup=history_keyboard())

    header = f"🛒 <b>Все сделки Playerok</b>: {len(deals)}\n🕒 Время указано по Калининграду."
    if background_sync:
        header += "\n⏳ Полная история ещё загружается в фоне. Показаны уже сохранённые сделки."
    chunks = [header]
    current_length = len(header)
    for index, deal in enumerate(deals, 1):
        title = escape(str(deal.get("title", "Товар без названия"))[:200])
        created_at = str(deal.get("created_at", ""))
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            time_text = parsed.astimezone(REPORT_TZ).strftime("%d.%m.%Y %H:%M:%S")
        except (TypeError, ValueError):
            time_text = "Время неизвестно"
        entry = f"\n\n{index}. <b>{title}</b>\n🕒 {time_text}"
        if deal.get("deal_id"):
            entry += f"\n🆔 {escape(str(deal['deal_id']))}"
        if current_length + len(entry) > 3800:
            chunks.append("")
            current_length = 0
        chunks[-1] += entry
        current_length += len(entry)

    await progress.delete()
    for chunk_index, chunk in enumerate(chunks):
        if chunk:
            await message.answer(chunk, parse_mode="HTML", reply_markup=history_keyboard() if chunk_index == len(chunks) - 1 else None)
    if truncated:
        await message.answer("⚠️ Playerok не вернул курсор следующей страницы. Показана часть истории.")
