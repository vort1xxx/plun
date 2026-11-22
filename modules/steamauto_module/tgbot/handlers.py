import asyncio
import json
from html import escape
from io import BytesIO
from logging import getLogger
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

from .states import SteamAutoSetup

router = Router()
logger = getLogger(NAME)


def _config():
    return Settings.get("config")


def _save(config):
    Settings.set("config", config)
    logger.info(
        "Конфигурация SteamSMM сохранена: mappings=%s api_key_present=%s",
        len(config.get("item_mappings", [])),
        bool(config.get("steamsmm", {}).get("api_key")),
    )


def _authorized(user_id):
    users = MainSettings.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(value) for value in users)


def _ensure_numbers(mappings):
    """Присваивает каждому товару постоянный номер; при удалении номера остальных не сдвигаются."""
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


def _keyboard():
    auto_done = bool(_config().get("playerok", {}).get("auto_complete_deal"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Привязать товар", callback_data="steamauto:add")],
        [InlineKeyboardButton(text="📋 Мои привязки", callback_data="steamauto:list")],
        [InlineKeyboardButton(
            text=f"✅ Авто-выполнение сделки: {'вкл' if auto_done else 'выкл'}",
            callback_data="steamauto:autodone",
        )],
        [InlineKeyboardButton(text="🔑 API-ключ", callback_data="steamauto:api_key")],
        [InlineKeyboardButton(text="🗄 Архив (сохранение/выгрузка/журнал)", callback_data="steamauto:service")],
    ])


def _service_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить конфиг", callback_data="steamauto:save")],
        [InlineKeyboardButton(text="📤 Выгрузить конфиг", callback_data="steamauto:export")],
        [InlineKeyboardButton(text="📥 Загрузить конфиг", callback_data="steamauto:import")],
        [InlineKeyboardButton(text="🧾 Скачать журнал", callback_data="steamauto:logs")],
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="steamauto:menu")],
    ])


def _back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="steamauto:menu")],
    ])


async def _show(message, note=""):
    mappings = _config().get("item_mappings", [])
    key_is_set = bool(_config().get("steamsmm", {}).get("api_key"))
    auto_done = bool(_config().get("playerok", {}).get("auto_complete_deal"))
    text = (
        "⚙️ <b>Настройки SteamSMM</b>\n\n"
        f"Настроено товаров: <b>{len(mappings)}</b>.\n"
        f"API-ключ: <b>{'задан' if key_is_set else 'не задан'}</b>.\n"
        f"Авто-выполнение сделки: <b>{'вкл' if auto_done else 'выкл'}</b>.\n"
        "Ссылка на профиль Steam берётся из полей оформления или комментария покупателя."
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
    logger.info("Поиск товара Playerok для привязки SteamSMM: found=%s", bool(item))
    return item


@router.message(Command(NAME))
async def menu(message: types.Message, state: FSMContext):
    await state.clear()
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await _show(message)


@router.callback_query(F.data == "steamauto:menu")
async def menu_back(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await state.clear()
    try:
        await callback.message.edit_text(
            "⚙️ <b>Настройки SteamSMM</b>",
            reply_markup=_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "steamauto:service")
async def service_tab(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await callback.message.answer(
        "🗄 <b>Архив и обслуживание</b>\n\n"
        "💾 Сохранить — конфиг запишется в настройки модуля.\n"
        "📤 Выгрузить — пришлю файлом (внутри API-ключ).\n"
        "📥 Загрузить — приму JSON, заменю настройки.\n"
        "🧾 Скачать журнал — файл steamauto.log.",
        reply_markup=_service_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "steamauto:save")
async def save_config(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    _save(_config())
    await callback.answer("Конфигурация сохранена", show_alert=True)


@router.callback_query(F.data == "steamauto:autodone")
async def toggle_autodone(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    config = _config()
    playerok = config.setdefault("playerok", {})
    playerok["auto_complete_deal"] = not bool(playerok.get("auto_complete_deal"))
    _save(config)
    new_value = playerok["auto_complete_deal"]
    logger.info("Авто-выполнение сделки SteamSMM переключено: %s", new_value)
    await callback.answer(f"Авто-выполнение сделки: {'вкл' if new_value else 'выкл'}", show_alert=True)
    await _show(callback.message)


@router.callback_query(F.data == "steamauto:api_key")
async def api_key_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(SteamAutoSetup.api_key)
    await callback.message.answer("Отправьте API-ключ SteamSMM (Bearer-токен из личного кабинета). Он будет сохранён, но не появится в ответном сообщении.")


@router.message(SteamAutoSetup.api_key, F.text)
async def api_key_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    api_key = message.text.strip()
    if not api_key:
        return await message.answer("API-ключ пустой. Отправьте ключ ещё раз.")
    config = _config()
    config.setdefault("steamsmm", {})["api_key"] = api_key
    _save(config)
    logger.info("API-ключ SteamSMM обновлён через Telegram")
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


@router.callback_query(F.data == "steamauto:export")
async def export_config(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    payload = {
        "format": "steamauto-playerok-config",
        "version": 1,
        "config": _config(),
    }
    file = BufferedInputFile(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        filename="steamauto_config.json",
    )
    await callback.message.answer_document(
        file,
        caption="Конфигурация SteamSMM. Внутри содержится API-ключ — не пересылайте файл посторонним.",
    )
    logger.info("Конфигурация SteamSMM выгружена через Telegram")


@router.callback_query(F.data == "steamauto:logs")
async def download_logs(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer("Готовлю журнал")
    log_bytes = get_log_bytes()
    if not log_bytes:
        return await callback.message.answer("Журнал пока пуст или недоступен. Перезапустите модуль и попробуйте ещё раз.")
    logger.info("Пользователь Telegram %s скачал журнал SteamSMM", callback.from_user.id)
    await callback.message.answer_document(
        BufferedInputFile(log_bytes, filename="steamauto.log"),
        caption="Журнал SteamSMM. API-ключи, пароли и Steam Guard коды скрыты.",
    )


@router.callback_query(F.data == "steamauto:import")
async def import_config_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(SteamAutoSetup.import_config)
    await callback.message.answer("Отправьте JSON-файл, ранее выгруженный из SteamSMM. Текущие настройки будут заменены.")


@router.message(SteamAutoSetup.import_config, F.document)
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
        if payload.get("format") not in (None, "steamauto-playerok-config"):
            raise ValueError("Это конфиг другого формата")
        imported = payload.get("config", payload)
        if not isinstance(imported, dict):
            raise ValueError("В конфиге нет объекта config")

        steamsmm = imported.get("steamsmm")
        playerok = imported.get("playerok")
        mappings_raw = imported.get("item_mappings", [])
        if not isinstance(steamsmm, dict) or not isinstance(playerok, dict):
            raise ValueError("В конфиге должны быть разделы steamsmm и playerok")
        if not isinstance(mappings_raw, list):
            raise ValueError("item_mappings должен быть списком")

        mappings = []
        seen = set()
        for entry in mappings_raw:
            if not isinstance(entry, dict):
                raise ValueError("Каждая привязка должна быть объектом")
            item_id = str(entry.get("item_id", "")).strip()
            amount = int(entry.get("amount", 0))
            if not item_id or amount < 1:
                raise ValueError("Для каждой привязки нужны item_id и положительное количество наград")
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
        merged["steamsmm"] = {**current.get("steamsmm", {}), **steamsmm}
        merged["playerok"] = {**current.get("playerok", {}), **playerok}
        merged["item_mappings"] = mappings
        merged["steamsmm"]["api_key"] = str(merged["steamsmm"].get("api_key", "")).strip()
        merged["steamsmm"]["base_url"] = str(merged["steamsmm"].get("base_url", "https://steamsmm.ru/api")).strip()
        if not merged["steamsmm"]["base_url"].startswith(("https://", "http://")):
            raise ValueError("Некорректный steamsmm.base_url")
        _ensure_numbers(mappings)
        _save(merged)
        logger.info("Конфигурация SteamSMM импортирована: mappings=%s", len(mappings))
        await state.clear()
        return await _show(message, f"✅ Конфиг загружен. Привязок товаров: {len(mappings)}. API-ключ {'задан' if merged['steamsmm']['api_key'] else 'не задан'}.")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return await message.answer(f"Не удалось загрузить конфиг: {escape(str(exc)[:300])}")


@router.callback_query(F.data == "steamauto:add")
async def add_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(SteamAutoSetup.item_url)
    await callback.message.answer("Пришлите ссылку на опубликованный товар Playerok.")


@router.message(SteamAutoSetup.item_url, F.text)
async def item_url_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    item = await _find_item(message.text)
    if item is None:
        return await message.answer("Не удалось найти товар по ссылке. Проверьте, что объявление опубликовано, и пришлите ссылку ещё раз.")
    await state.update_data(item_id=str(item.id), item_name=str(getattr(item, "name", None) or item.id), item_url=message.text.strip())
    await state.set_state(SteamAutoSetup.reward_amount)
    await message.answer(
        f"Товар найден: <b>{escape(str(getattr(item, 'name', None) or item.id))}</b>\n"
        "Сколько наград отправлять при его покупке? Введите любое целое число от 1 до 2000.",
        parse_mode="HTML",
    )


@router.message(SteamAutoSetup.reward_amount, F.text)
async def reward_amount_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    try:
        amount = int(message.text.strip())
        if not 1 <= amount <= 2000:
            raise ValueError
    except ValueError:
        return await message.answer("Введите целое число наград от 1 до 2000, например 10, 50 или 100.")
    data = await state.get_data()
    config = _config()
    mappings = [row for row in config.get("item_mappings", []) if str(row.get("item_id")) != data["item_id"]]
    next_num = max((int(row.get("num", 0) or 0) for row in mappings), default=0) + 1
    mappings.append({
        "num": next_num,
        "item_id": data["item_id"],
        "item_name": data["item_name"],
        "item_url": data["item_url"],
        "amount": amount,
    })
    config["item_mappings"] = mappings
    _save(config)
    await state.clear()
    logger.info("Создана или обновлена привязка SteamSMM: item=%s amount=%s", data["item_id"], amount)
    await _show(message, f"✅ Для товара <b>{escape(data['item_name'])}</b> сохранено: {amount} наград.")


@router.callback_query(F.data == "steamauto:list")
async def list_mappings(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    config = _config()
    mappings = config.get("item_mappings", [])
    if not mappings:
        return await callback.message.answer("Список пуст. Нажмите «Привязать товар» в меню /steamauto.")
    if _ensure_numbers(mappings):
        _save(config)
    lines = ["📋 <b>Привязки товаров</b>"]
    buttons = []
    for mapping in mappings:
        number = int(mapping.get("num", 0))
        lines.append(f"\n{number}. <b>{escape(str(mapping.get('item_name', mapping.get('item_id'))))}</b> — {int(mapping.get('amount', 0))} наград")
        buttons.append([InlineKeyboardButton(text=f"🗑 Удалить {number}", callback_data=f"steamauto:delete:{number}")])
    buttons.append([InlineKeyboardButton(text="⬅️ В настройки", callback_data="steamauto:menu")])
    await callback.message.answer("".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data.startswith("steamauto:delete:"))
async def delete_mapping(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    try:
        number = int(callback.data.rsplit(":", 1)[-1])
        config = _config()
        mappings = config.get("item_mappings", [])
        index = next(
            (i for i, row in enumerate(mappings) if int(row.get("num", 0) or 0) == number),
            None,
        )
        if index is None:
            return await callback.answer("Эта привязка уже удалена.", show_alert=True)
        removed = mappings.pop(index)
        config["item_mappings"] = mappings
        _save(config)
        logger.info("Удалена привязка SteamSMM: num=%s item=%s", number, removed.get("item_id"))
        await callback.answer(f"Удалена привязка №{number}")
    except (ValueError, IndexError):
        await callback.answer("Эта привязка уже удалена.", show_alert=True)
