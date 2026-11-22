import asyncio
import json
from html import escape
from io import BytesIO
from logging import getLogger
from threading import Thread
from urllib.parse import urlparse

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from settings import Settings as MainSettings
from tgbot.helpful import do_auth
from plbot.playerokbot import get_playerok_bot

from ..meta import NAME, PREFIX
from ..module_logging import get_log_bytes
from ..settings import Settings
from ..plbot.handlers import (
    create_holdboost_order,
    extract_discord_invite,
    notify_telegram,
    wait_holdboost_result,
)
from .states import HoldBoostSetup

router = Router()
logger = getLogger(NAME)


# Порядок и названия сообщений, доступных для правки через бота.
MESSAGE_KEYS = {
    "process": "Ссылка получена, начинаю выдачу",
    "ask_invite": "Запрос ссылки (запасной путь)",
    "bad_invite": "Не похоже на Discord-ссылку",
    "done": "Бусты выданы",
    "customer_error": "Покупателю про ошибку",
    "telegram_error": "Уведомление продавцу об ошибке",
}


def _config():
    return Settings.get("config")


def _save(config):
    Settings.set("config", config)
    logger.info(
        "Конфигурация HoldBoost сохранена: mappings=%s api_key_present=%s preset_id=%r",
        len(config.get("boost_mappings", [])),
        bool(config.get("holdboost", {}).get("api_key")),
        str(config.get("holdboost", {}).get("preset_id", "") or "") or None,
    )


def _authorized(user_id):
    users = MainSettings.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(value) for value in users)


def _keyboard():
    auto_done = bool(_config().get("playerok", {}).get("auto_complete_deal"))
    preset_id = str(_config().get("holdboost", {}).get("preset_id", "") or "").strip()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Привязать товар", callback_data="holdboost:add")],
        [InlineKeyboardButton(text="📋 Мои привязки", callback_data="holdboost:list")],
        [InlineKeyboardButton(
            text=f"🎯 Preset ID: {'задан' if preset_id else 'не задан'}",
            callback_data="holdboost:preset_id",
        )],
        [InlineKeyboardButton(text="💬 Сообщения", callback_data="holdboost:msgs")],
        [InlineKeyboardButton(
            text=f"✅ Авто-выполнение сделки: {'вкл' if auto_done else 'выкл'}",
            callback_data="holdboost:autodone",
        )],
        [InlineKeyboardButton(text="⚡ Ручная выдача бустов (/boost)", callback_data="holdboost:manual")],
        [InlineKeyboardButton(text="🔑 API-ключ", callback_data="holdboost:api_key")],
        [InlineKeyboardButton(text="🗄 Архив (сохранение/выгрузка/журнал)", callback_data="holdboost:service")],
    ])


def _service_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить конфиг", callback_data="holdboost:save")],
        [InlineKeyboardButton(text="📤 Выгрузить конфиг", callback_data="holdboost:export")],
        [InlineKeyboardButton(text="📥 Загрузить конфиг", callback_data="holdboost:import")],
        [InlineKeyboardButton(text="🧾 Скачать журнал", callback_data="holdboost:logs")],
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="holdboost:menu")],
    ])


def _set_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="holdboost:menu")],
    ])


def _ensure_numbers(mappings):
    """Присваивает каждому товару постоянный номер один раз; при удалении номера остальных не сдвигаются."""
    next_num = max((int(row.get("num", 0) or 0) for row in mappings), default=0)
    changed = False
    for row in mappings:
        try:
            current = int(row.get("num", 0) or 0)
        except (TypeError, ValueError):
            current = 0
        if current < 1:
            next_num += 1
            row["num"] = next_num
            changed = True
    return changed


async def _show(message, note=""):
    mappings = _config().get("boost_mappings", [])
    holdboost = _config().get("holdboost", {})
    key_is_set = bool(holdboost.get("api_key"))
    preset_id = str(holdboost.get("preset_id", "") or "").strip()
    auto_done = bool(_config().get("playerok", {}).get("auto_complete_deal"))
    text = (
        "⚙️ <b>Настройки HoldBoost</b>\n\n"
        f"Настроено товаров: <b>{len(mappings)}</b>.\n"
        f"API-ключ: <b>{'задан' if key_is_set else 'не задан'}</b>.\n"
        f"Preset ID: <b>{escape(preset_id) if preset_id else 'не задан'}</b>.\n"
        f"Авто-выполнение сделки: <b>{'вкл' if auto_done else 'выкл'}</b>.\n"
        "Выдача начинается сразу после оплаты: ссылка берётся из полей оформления."
    )
    if not mappings:
        text += "\nПока привязок нет."
    if note:
        text += "\n\n" + note
    await message.answer(text, reply_markup=_keyboard(), parse_mode="HTML")


async def _find_item(value: str):
    raw = value.strip()
    slug = urlparse(raw).path.rstrip("/").split("/")[-1] if "://" in raw else ""
    account = get_playerok_bot().account
    try:
        item = await asyncio.to_thread(account.get_item, id=raw)
    except Exception:
        item = None
    if item is None and slug:
        try:
            item = await asyncio.to_thread(account.get_item, slug=slug)
        except Exception:
            item = None
    logger.info("Поиск товара Playerok для привязки: found=%s", bool(item))
    return item


@router.message(Command(NAME))
async def menu(message: types.Message, state: FSMContext):
    await state.clear()
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await _show(message)


@router.message(Command("holdboost_import"))
async def import_command(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await state.set_state(HoldBoostSetup.import_config)
    await message.answer("Пришлите JSON-конфиг HoldBoost, ранее выгруженный из бота.")


@router.callback_query(F.data == "holdboost:menu")
async def menu_back(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await state.clear()
    try:
        await callback.message.edit_text(
            "⚙️ <b>Настройки HoldBoost</b>",
            reply_markup=_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "holdboost:service")
async def service_tab(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await callback.message.answer(
        "🗄 <b>Архив и обслуживание</b>\n\n"
        "💾 Сохранить — конфиг запишется в настройки модуля.\n"
        "📤 Выгрузить — пришлю файлом (внутри API-ключ и preset_id).\n"
        "📥 Загрузить — приму JSON, заменю настройки.\n"
        "🧾 Скачать журнал — файл holdboost.log.",
        reply_markup=_service_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "holdboost:save")
async def save_config(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    _save(_config())
    await callback.answer("Конфигурация сохранена")
    await callback.message.answer("✅ Конфигурация сохранена в настройках модуля.")


@router.callback_query(F.data == "holdboost:autodone")
async def toggle_autodone(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    config = _config()
    playerok = config.setdefault("playerok", {})
    playerok["auto_complete_deal"] = not bool(playerok.get("auto_complete_deal"))
    _save(config)
    new_value = playerok["auto_complete_deal"]
    logger.info("Авто-выполнение сделки HoldBoost переключено: %s", new_value)
    await callback.answer(f"Авто-выполнение сделки: {'вкл' if new_value else 'выкл'}", show_alert=True)
    await _show(callback.message)


@router.callback_query(F.data == "holdboost:preset_id")
async def preset_id_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    current = str(_config().get("holdboost", {}).get("preset_id", "") or "").strip()
    await state.set_state(HoldBoostSetup.preset_id)
    await callback.message.answer(
        "🎯 <b>Preset ID HoldBoost</b>\n\n"
        "ID пресета для POST /v1/external/orders (персональная реклама).\n"
        f"Текущее значение: <b>{escape(current) if current else 'не задано'}</b>.\n\n"
        "Пришлите новый ID пресета или <code>-</code>, чтобы сбросить.",
        parse_mode="HTML",
        reply_markup=_set_keyboard(),
    )


@router.message(HoldBoostSetup.preset_id, F.text)
async def preset_id_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    raw = message.text.strip()
    value = "" if raw in {"-", "—"} else raw
    config = _config()
    config.setdefault("holdboost", {})["preset_id"] = value
    _save(config)
    await state.clear()
    logger.info("Preset ID HoldBoost обновлён через Telegram: %s", value or "(сброшен)")
    await _show(
        message,
        "🎯 Preset ID сброшен." if not value else f"🎯 Preset ID сохранён: <code>{escape(value)}</code>.",
    )


@router.callback_query(F.data == "holdboost:msgs")
async def messages_editor(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    messages = Settings.get("messages")
    text = ["💬 <b>Сообщения бота</b>\n\nВыберите сообщение для просмотра и правки:"]
    buttons = []
    for key, title in MESSAGE_KEYS.items():
        entry = messages.get(key, {}) if isinstance(messages, dict) else {}
        state = "вкл" if entry.get("enabled") else "выкл"
        buttons.append([InlineKeyboardButton(
            text=f"✉️ {title} ({state})",
            callback_data=f"holdboost:msg:{key}",
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ В настройки", callback_data="holdboost:menu")])
    await callback.message.answer("\n".join(text), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("holdboost:msg:"))
async def view_message(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    key = callback.data.split(":", 2)[-1]
    if key not in MESSAGE_KEYS:
        return await callback.answer("Неизвестное сообщение.", show_alert=True)
    await callback.answer()
    entry = Settings.get("messages").get(key, {})
    enabled = bool(entry.get("enabled"))
    body = "\n".join(str(line) for line in (entry.get("text") or []))
    text = (
        f"✉️ <b>{MESSAGE_KEYS[key]}</b> <code>{key}</code>\n"
        f"Статус: <b>{'вкл' if enabled else 'выкл'}</b>\n\n"
        f"Текущий текст:\n<pre>{escape(body) or '(пусто)'}</pre>\n\n"
        "Переменные: <code>{done}</code>, <code>{amount}</code>, <code>{months}</code>, "
        "<code>{server}</code>, <code>{deal_id}</code>, <code>{item_name}</code>, <code>{error}</code> "
        "— для этого сообщения актуальны не все."
    )
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🔇 Выключить' if enabled else '🔊 Включить'}",
            callback_data=f"holdboost:msgtoggle:{key}",
        )],
        [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"holdboost:msgt:{key}")],
        [InlineKeyboardButton(text="⬅️ К списку сообщений", callback_data="holdboost:msgs")],
    ]), parse_mode="HTML")


@router.callback_query(F.data.startswith("holdboost:msgtoggle:"))
async def toggle_message(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    key = callback.data.split(":", 2)[-1]
    if key not in MESSAGE_KEYS:
        return await callback.answer("Неизвестное сообщение.", show_alert=True)
    messages = Settings.get("messages")
    entry = messages.setdefault(key, {})
    entry["enabled"] = not bool(entry.get("enabled"))
    Settings.set("messages", messages)
    logger.info("Сообщение HoldBoost %s переключено: %s", key, entry["enabled"])
    await callback.answer(f"Сообщение {'включено' if entry['enabled'] else 'выключено'}")
    await view_message(callback)


@router.callback_query(F.data.startswith("holdboost:msgt:"))
async def edit_message_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    key = callback.data.split(":", 2)[-1]
    if key not in MESSAGE_KEYS:
        return await callback.answer("Неизвестное сообщение.", show_alert=True)
    await callback.answer()
    await state.set_state(HoldBoostSetup.msg_text)
    await state.update_data(msg_key=key)
    await callback.message.answer(
        f"✏️ Пришлите новый текст сообщения <b>{MESSAGE_KEYS[key]}</b> (<code>{key}</code>).\n"
        "Каждая строка отдельным сообщением — оправляйте текст целиком, переносы сохраню.\n"
        "Переменные: <code>{done}</code>, <code>{amount}</code>, <code>{months}</code>, "
        "<code>{server}</code>, <code>{deal_id}</code>, <code>{item_name}</code>, <code>{error}</code>.",
        parse_mode="HTML",
        reply_markup=_set_keyboard(),
    )


@router.message(HoldBoostSetup.msg_text, F.text)
async def edit_message_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    data = await state.get_data()
    key = data.get("msg_key")
    if key not in MESSAGE_KEYS:
        await state.clear()
        return await message.answer("Неизвестное сообщение. Начните заново через /holdboost → 💬 Сообщения.")
    text = message.text.strip()
    if not text:
        return await message.answer("Текст пустой. Пришлите текст ещё раз или /holdboost для выхода.")
    messages = Settings.get("messages")
    entry = messages.setdefault(key, {})
    entry["enabled"] = bool(entry.get("enabled", True))
    entry["text"] = text.splitlines()
    Settings.set("messages", messages)
    await state.clear()
    logger.info("Сообщение HoldBoost %s обновлено через Telegram", key)
    await message.answer(f"✅ Текст сообщения <b>{MESSAGE_KEYS[key]}</b> сохранён.", parse_mode="HTML")


@router.callback_query(F.data == "holdboost:api_key")
async def api_key_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(HoldBoostSetup.api_key)
    await callback.message.answer("Отправьте новый API-ключ HoldBoost. Он будет сохранён, но не появится в ответном сообщении.")


@router.message(HoldBoostSetup.api_key, F.text)
async def api_key_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    api_key = message.text.strip()
    if not api_key:
        return await message.answer("API-ключ пустой. Отправьте ключ ещё раз.")
    config = _config()
    config.setdefault("holdboost", {})["api_key"] = api_key
    _save(config)
    logger.info("API-ключ HoldBoost обновлён через Telegram")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.bot.send_message(
        chat_id=message.chat.id,
        text="✅ API-ключ обновлён и сохранён. Значение скрыто.",
        reply_markup=_keyboard(),
    )


@router.callback_query(F.data == "holdboost:manual")
async def manual_boost_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(HoldBoostSetup.manual_invite)
    await callback.message.answer(
        "⚡ <b>Ручная выдача бустов</b>\n\n"
        "Пришлите ссылку на Discord-сервер (discord.gg/...), на который поставить бусты.",
        parse_mode="HTML",
        reply_markup=_set_keyboard(),
    )


@router.message(HoldBoostSetup.manual_invite, F.text)
async def manual_invite_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    invite = extract_discord_invite(message.text.strip())
    if not invite:
        return await message.answer(
            "Не вижу Discord-ссылку. Пришлите ссылку вида discord.gg/... или /holdboost для выхода."
        )
    await state.update_data(manual_invite=invite)
    await state.set_state(HoldBoostSetup.manual_amount)
    await message.answer(
        f"Сервер: <code>{escape(invite)}</code>\nСколько бустов поставить? Введите положительное целое число:",
        parse_mode="HTML",
    )


@router.message(HoldBoostSetup.manual_amount, F.text)
async def manual_amount_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    try:
        amount = int(message.text.strip())
        if not 1 <= amount <= 1000:
            raise ValueError
    except ValueError:
        return await message.answer("Введите целое число от 1 до 1000.")
    data = await state.get_data()
    invite = data.get("manual_invite", "")
    await state.clear()
    if not invite:
        return await message.answer("Ссылка потерялась, начните заново: /boost.")
    logger.info("Ручная выдача HoldBoost запущена: invite=%s amount=%s (пользователь Telegram %s)", invite, amount, message.from_user.id)
    await message.bot.send_message(
        chat_id=message.chat.id,
        text=f"⚙️ Создаю заказ HoldBoost: {amount} бустов → {invite}\nО результате напишу здесь.",
        reply_markup=_keyboard(),
    )
    Thread(target=_manual_boost_worker, args=(invite, amount), daemon=True).start()


def _manual_boost_worker(invite: str, amount: int) -> None:
    """Ручная выдача вне сделки: создание заказа HoldBoost и уведомление админов в Telegram."""
    try:
        response = create_holdboost_order(invite, int(amount))
        if not response.get("success"):
            raise RuntimeError(json.dumps(response, ensure_ascii=False)[:400])
        order = response["data"]
        order_id = order["id"]
        logger.info("Ручной заказ HoldBoost создан: order=%s amount=%s", order_id, amount)
        notify_telegram(f"⚡ Ручная выдача: заказ {order_id} создан ({amount} бустов). Ожидаю результат…")
        result = wait_holdboost_result(order_id)
        done = int(result.get("boosts_done") or 0)
        if result.get("status") == "COMPLETED" and done >= amount:
            notify_telegram(f"✅ Ручная выдача завершена: {done}/{amount} бустов → {invite}")
            logger.info("Ручная выдача завершена: order=%s done=%s/%s", order_id, done, amount)
        else:
            raise RuntimeError(f"Заказ не выполнен полностью: {json.dumps(result, ensure_ascii=False)[:400]}")
    except Exception as exc:
        logger.exception("Ручная выдача HoldBoost не удалась: invite=%s amount=%s", invite, amount)
        notify_telegram(f"⚠️ Ручная выдача не удалась ({amount} бустов → {invite}):\n{str(exc)[:400]}")


@router.message(Command("boost"))
async def boost_command(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await state.set_state(HoldBoostSetup.manual_invite)
    await message.answer(
        "⚡ <b>Ручная выдача бустов</b>\n\n"
        "Пришлите ссылку на Discord-сервер (discord.gg/...), на который поставить бусты.",
        parse_mode="HTML",
        reply_markup=_set_keyboard(),
    )


@router.callback_query(F.data == "holdboost:export")
async def export_config(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    payload = {
        "format": "holdboost-playerok-config",
        "version": 1,
        "config": _config(),
    }
    file = BufferedInputFile(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        filename="holdboost_config.json",
    )
    await callback.message.answer_document(
        file,
        caption="Конфигурация HoldBoost. Внутри содержится API-ключ — не пересылайте файл посторонним.",
    )
    logger.info("Конфигурация HoldBoost выгружена через Telegram")


@router.callback_query(F.data == "holdboost:logs")
async def download_logs(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer("Готовлю журнал")
    log_bytes = get_log_bytes()
    if not log_bytes:
        return await callback.message.answer("Журнал пока пуст или недоступен. Перезапустите модуль и попробуйте ещё раз.")
    logger.info("Пользователь Telegram %s скачал журнал HoldBoost", callback.from_user.id)
    await callback.message.answer_document(
        BufferedInputFile(log_bytes, filename="holdboost.log"),
        caption="Журнал HoldBoost. API-ключи, пароли и Steam Guard коды скрыты.",
    )


@router.callback_query(F.data == "holdboost:import")
async def import_config_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(HoldBoostSetup.import_config)
    await callback.message.answer("Отправьте JSON-файл, ранее выгруженный из HoldBoost. Текущие настройки будут заменены.")


@router.message(HoldBoostSetup.import_config, F.document)
async def import_config_file(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    document = message.document
    if document.file_size and document.file_size > 1_000_000:
        return await message.answer("Файл слишком большой, максимум 1 МБ.")
    try:
        telegram_file = await message.bot.get_file(document.file_id)
        buffer = BytesIO()
        await message.bot.download_file(telegram_file.file_path, destination=buffer)
        payload = json.loads(buffer.getvalue().decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Корень JSON должен быть объектом")
        if payload.get("format") not in (None, "holdboost-playerok-config"):
            raise ValueError("Это конфиг другого формата")
        imported = payload.get("config", payload)
        if not isinstance(imported, dict):
            raise ValueError("В конфиге нет объекта config")

        holdboost = imported.get("holdboost")
        playerok = imported.get("playerok")
        mappings_raw = imported.get("boost_mappings", [])
        if not isinstance(holdboost, dict) or not isinstance(playerok, dict):
            raise ValueError("В конфиге должны быть разделы holdboost и playerok")
        if not isinstance(mappings_raw, list):
            raise ValueError("boost_mappings должен быть списком")

        mappings = []
        seen = set()
        for entry in mappings_raw:
            if not isinstance(entry, dict):
                raise ValueError("Каждая привязка должна быть объектом")
            item_id = str(entry.get("item_id", "")).strip()
            amount = int(entry.get("amount", 0))
            if not item_id or amount < 1:
                raise ValueError("Для каждой привязки нужны item_id и положительное количество бустов")
            if item_id in seen:
                raise ValueError("В конфиге повторяется товар")
            seen.add(item_id)
            mappings.append({
                "item_id": item_id,
                "item_name": str(entry.get("item_name", item_id)),
                "item_url": str(entry.get("item_url", "")),
                "amount": amount,
            })

        current = _config()
        merged = dict(current)
        merged["holdboost"] = {**current.get("holdboost", {}), **holdboost}
        merged["playerok"] = {**current.get("playerok", {}), **playerok}
        merged["boost_mappings"] = mappings
        merged["holdboost"]["api_key"] = str(merged["holdboost"].get("api_key", "")).strip()
        merged["holdboost"]["base_url"] = str(merged["holdboost"].get("base_url", "https://api.holdboost.store")).strip()
        if not merged["holdboost"]["base_url"].startswith(("https://", "http://")):
            raise ValueError("Некорректный holdboost.base_url")
        merged["holdboost"]["preset_id"] = str(merged["holdboost"].get("preset_id", "") or "").strip()
        _ensure_numbers(mappings)
        _save(merged)
        logger.info("Конфигурация HoldBoost импортирована: mappings=%s", len(mappings))
        await state.clear()
        return await _show(message, f"✅ Конфиг загружен. Привязок товаров: {len(mappings)}. API-ключ {'задан' if merged['holdboost']['api_key'] else 'не задан'}.")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return await message.answer(f"Не удалось загрузить конфиг: {escape(str(exc)[:300])}")


@router.callback_query(F.data == "holdboost:add")
async def add_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(HoldBoostSetup.item_url)
    await callback.message.answer("Пришлите ссылку на опубликованный товар Playerok.")


@router.message(HoldBoostSetup.item_url, F.text)
async def item_url_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    item = await _find_item(message.text)
    if item is None:
        return await message.answer("Не удалось найти товар по ссылке. Проверьте, что объявление опубликовано, и пришлите ссылку ещё раз.")
    await state.update_data(item_id=str(item.id), item_name=str(getattr(item, "name", None) or item.id), item_url=message.text.strip())
    await state.set_state(HoldBoostSetup.boost_amount)
    await message.answer(
        f"Товар найден: <b>{escape(str(getattr(item, 'name', None) or item.id))}</b>\n"
        "Сколько бустов заказывать при его покупке? Введите любое положительное целое число.",
        parse_mode="HTML",
    )


@router.message(HoldBoostSetup.boost_amount, F.text)
async def boost_amount_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    try:
        amount = int(message.text.strip())
        if amount < 1:
            raise ValueError
    except ValueError:
        return await message.answer("Введите положительное целое число бустов, например 1, 5 или 100.")
    data = await state.get_data()
    config = _config()
    mappings = [row for row in config.get("boost_mappings", []) if str(row.get("item_id")) != data["item_id"]]
    next_num = max((int(row.get("num", 0) or 0) for row in mappings), default=0) + 1
    mappings.append({
        "num": next_num,
        "item_id": data["item_id"],
        "item_name": data["item_name"],
        "item_url": data["item_url"],
        "amount": amount,
    })
    config["boost_mappings"] = mappings
    _save(config)
    await state.clear()
    logger.info("Создана или обновлена привязка HoldBoost: item=%s amount=%s", data["item_id"], amount)
    await _show(message, f"✅ Для товара <b>{escape(data['item_name'])}</b> сохранено: {amount} бустов.")


@router.callback_query(F.data == "holdboost:list")
async def list_mappings(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    config = _config()
    mappings = config.get("boost_mappings", [])
    if not mappings:
        return await callback.message.answer("Список пуст. Нажмите «Привязать товар» в меню /holdboost.")
    if _ensure_numbers(mappings):
        _save(config)
    lines = ["📋 <b>Привязки товаров</b>"]
    buttons = []
    for mapping in mappings:
        number = int(mapping.get("num", 0))
        lines.append(f"\n{number}. <b>{escape(str(mapping.get('item_name', mapping.get('item_id'))))}</b> — {int(mapping.get('amount', 0))} бустов")
        buttons.append([InlineKeyboardButton(text=f"🗑 Удалить {number}", callback_data=f"holdboost:delete:{number}")])
    buttons.append([InlineKeyboardButton(text="⬅️ В настройки", callback_data="holdboost:menu")])
    await callback.message.answer("".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data.startswith("holdboost:delete:"))
async def delete_mapping(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    try:
        number = int(callback.data.rsplit(":", 1)[-1])
        config = _config()
        mappings = config.get("boost_mappings", [])
        index = next(
            (i for i, row in enumerate(mappings) if int(row.get("num", 0) or 0) == number),
            None,
        )
        if index is None:
            return await callback.answer("Эта привязка уже удалена.", show_alert=True)
        removed = mappings.pop(index)
        config["boost_mappings"] = mappings
        _save(config)
        logger.info("Удалена привязка HoldBoost: num=%s item=%s", number, removed.get("item_id"))
        await callback.answer(f"Удалена привязка №{number}")
    except (ValueError, IndexError):
        await callback.answer("Эта привязка уже удалена.", show_alert=True)
