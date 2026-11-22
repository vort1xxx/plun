import asyncio
import hashlib
import json
import logging
from html import escape
from io import BytesIO
from urllib.parse import urlparse

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from settings import Settings as MainSettings
from tgbot.helpful import do_auth
from plbot.playerokbot import get_playerok_bot

from ..logic import parse_secret, valid_template, validate_config
from ..meta import NAME
from ..module_logging import get_log_bytes
from ..storage import LOCK, get_config, save_config
from .states import Setup

router = Router()
logger = logging.getLogger(NAME)


def authorized(user_id):
    users = (MainSettings.get("config") or {}).get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(user) for user in users)


async def check_message(message, state):
    if message.from_user and authorized(message.from_user.id):
        return True
    await state.clear()
    await do_auth(message, state)
    return False


async def check_callback(callback):
    if authorized(callback.from_user.id):
        await callback.answer()
        return True
    await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    return False


def token(mapping):
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def button(text, action):
    return [InlineKeyboardButton(text=text, callback_data="twofa:" + action)]


def keyboard(config):
    return InlineKeyboardMarkup(inline_keyboard=[
        button("➕ Привязать товар / заменить секрет", "add"),
        button("📋 Мои привязки", "list"),
        button("✉️ Шаблон сообщения", "template"),
        button("⌨️ Команда покупателя", "command"),
        button(f"⚡ Автовыдача после оплаты: {'ВКЛ' if config['auto_issue'] else 'ВЫКЛ'}", "toggle:auto_issue"),
        button(f"✅ Отмечать товар переданным: {'ВКЛ' if config['auto_complete'] else 'ВЫКЛ'}", "toggle:auto_complete"),
        button(f"🔌 Выдача кодов: {'ВКЛ' if config['enabled'] else 'ВЫКЛ'}", "toggle:enabled"),
        button("💾 Сохранить конфиг", "save"),
        button("📤 Выгрузить конфиг", "export"),
        button("📥 Загрузить конфиг", "import"),
        button("🧾 Скачать журнал", "logs"),
    ])


async def show(message, note=""):
    config = get_config()
    text = ("⚙️ <b>Выдача 2FA → Playerok</b>\n\n"
            f"Привязано товаров: <b>{len(config['mappings'])}</b>\n"
            f"Команда покупателя: <code>{escape(config['command'])}</code>\n"
            "Код генерируется из TOTP-секрета привязанного аккаунта.\n"
            "Настройки сохраняются автоматически.")
    if note:
        text += "\n\n" + note
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard(config))


async def resolve_item(raw):
    raw = raw.strip()
    bot = get_playerok_bot()
    if bot is None or getattr(bot, "account", None) is None:
        return None
    if "://" in raw:
        uri = urlparse(raw)
        if uri.hostname not in ("playerok.com", "www.playerok.com"):
            return None
        kwargs = {"slug": uri.path.rstrip("/").split("/")[-1]}
    else:
        kwargs = {"id": raw}
    try:
        item = await asyncio.to_thread(bot.account.get_item, **kwargs)
        owner = getattr(getattr(item, "user", None), "id", None)
        if owner is not None and str(owner) != str(bot.account.id):
            return None
        return item
    except Exception:
        return None


@router.message(Command(NAME))
async def menu(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    await state.clear()
    await show(message)


@router.callback_query(F.data == "twofa:menu")
async def callback_menu(callback: types.CallbackQuery, state: FSMContext):
    if not await check_callback(callback):
        return
    await state.clear()
    await show(callback.message)


@router.callback_query(F.data.startswith("twofa:toggle:"))
async def toggle(callback: types.CallbackQuery):
    if not await check_callback(callback):
        return
    key = callback.data.rsplit(":", 1)[-1]
    if key not in ("enabled", "auto_issue", "auto_complete"):
        return
    with LOCK:
        config = get_config()
        config[key] = not config[key]
        save_config(config)
    await show(callback.message)


@router.callback_query(F.data == "twofa:save")
async def save(callback: types.CallbackQuery):
    if not await check_callback(callback):
        return
    with LOCK:
        save_config(get_config())
    await show(callback.message, "✅ Настройки сохранены.")


@router.callback_query(F.data == "twofa:add")
async def add(callback: types.CallbackQuery, state: FSMContext):
    if not await check_callback(callback):
        return
    await state.clear()
    await state.set_state(Setup.item)
    await callback.message.answer("Пришлите ссылку или ID вашего опубликованного товара Playerok.")


@router.message(Setup.item, F.text)
async def item_entered(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    item = await resolve_item(message.text)
    if item is None:
        return await message.answer("Товар не найден. Проверьте ссылку и запуск Playerok-бота.")
    await state.update_data(item_id=str(item.id), item_name=str(item.name or item.id))
    await state.set_state(Setup.label)
    await message.answer("Товар найден: " + str(item.name or item.id) + "\nВведите название аккаунта или логин (до 150 символов).")


@router.message(Setup.label, F.text)
async def label_entered(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    label = message.text.strip()
    if not 1 <= len(label) <= 150:
        return await message.answer("Название аккаунта должно содержать от 1 до 150 символов.")
    await state.update_data(label=label)
    await state.set_state(Setup.secret)
    await message.answer("Пришлите TOTP-секрет в Base32 или ссылку otpauth://totp/... из настройки аутентификатора. Нужен секрет, а не текущий шестизначный код.")


@router.message(Setup.secret, F.text)
async def secret_entered(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    try:
        parsed = parse_secret(message.text)
    except ValueError as exc:
        return await message.answer(str(exc))
    await state.update_data(secret=parsed["secret"], digits=parsed["digits"], period=parsed["period"], algorithm=parsed["algorithm"])
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(Setup.hours)
    await message.answer("Секрет принят. Сколько часов после оплаты покупатель сможет запрашивать код? Введите целое число; 0 — без ограничения срока.")


@router.message(Setup.hours, F.text)
async def hours_entered(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    try:
        hours = int(message.text.strip())
        if not 0 <= hours <= 87600:
            raise ValueError
    except ValueError:
        return await message.answer("Введите число часов от 0 до 87600.")
    values = await state.get_data()
    values["access_hours"] = hours
    try:
        with LOCK:
            config = get_config()
            config["mappings"] = [row for row in config["mappings"] if row["item_id"] != values["item_id"]] + [values]
            save_config(config)
    except (ValueError, OSError) as exc:
        logger.error("Ошибка сохранения привязки: error=%s", type(exc).__name__)
        return await message.answer("Не удалось сохранить привязку. Проверьте настройки и доступ к файлам модуля.")
    await state.clear()
    logger.info("Привязка сохранена: item=%s", values["item_id"])
    await show(message, "✅ Привязка сохранена. Секрет не отображается в списке.")


@router.callback_query(F.data == "twofa:list")
async def list_mappings(callback: types.CallbackQuery):
    if not await check_callback(callback):
        return
    mappings = get_config()["mappings"]
    if not mappings:
        return await show(callback.message, "Привязок пока нет.")
    for start in range(0, len(mappings), 5):
        lines, buttons = ["📋 <b>Привязки товаров</b>"], []
        for index, mapping in enumerate(mappings[start:start + 5], start=start):
            duration = f"{mapping['access_hours']} ч." if mapping["access_hours"] else "без ограничения"
            lines.append(f"\n{index + 1}. <b>{escape(mapping['item_name'][:100])}</b>\nАккаунт: {escape(mapping['label'][:80])}\nДоступ: {duration}; код: {mapping['digits']} цифр / {mapping['period']} сек.\nID: <code>{escape(mapping['item_id'])}</code>")
            buttons.append(button(f"🗑 Удалить {index + 1}", "delete:" + token(mapping)))
        buttons.append(button("↩️ В меню", "menu"))
        await callback.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("twofa:delete:"))
async def delete_mapping(callback: types.CallbackQuery):
    if not await check_callback(callback):
        return
    expected = callback.data.rsplit(":", 1)[-1]
    with LOCK:
        config = get_config()
        row = next((row for row in config["mappings"] if token(row) == expected), None)
        if row is not None:
            config["mappings"].remove(row)
            save_config(config)
    await show(callback.message, "Привязка удалена, выдача для неё прекращена." if row else "Список изменился. Откройте привязки заново.")


@router.callback_query(F.data == "twofa:template")
async def template_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not await check_callback(callback):
        return
    await state.clear()
    await state.set_state(Setup.template)
    await callback.message.answer("Введите шаблон. Обязательно {code}, также доступны {account}, {item}, {seconds}.\nТекущий:\n" + get_config()["template"])


@router.message(Setup.template, F.text)
async def template_entered(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    template = message.text.strip()
    if not valid_template(template):
        return await message.answer("Нужен {code}. Разрешены {account}, {item}, {seconds}, длина до 2000 символов, без спецификаторов формата.")
    with LOCK:
        config = get_config()
        config["template"] = template
        save_config(config)
    await state.clear()
    await show(message, "✅ Шаблон сохранён.")


@router.callback_query(F.data == "twofa:command")
async def command_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not await check_callback(callback):
        return
    await state.clear()
    await state.set_state(Setup.command)
    await callback.message.answer("Введите команду, например !2fa. Команда !код также используется KOSell: для совместной работы выберите отдельную команду.")


@router.message(Setup.command, F.text)
async def command_entered(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    try:
        with LOCK:
            config = get_config()
            config["command"] = message.text.strip()
            save_config(config)
    except ValueError as exc:
        return await message.answer(str(exc))
    await state.clear()
    await show(message, "✅ Команда сохранена.")


@router.callback_query(F.data == "twofa:export")
async def export_config(callback: types.CallbackQuery):
    if not await check_callback(callback):
        return
    payload = {"format": "twofa-playerok-config", "version": 1, "config": get_config()}
    document = BufferedInputFile(json.dumps(payload, ensure_ascii=False, indent=2).encode(), filename="twofa_config.json")
    await callback.message.answer_document(document, caption="Конфигурация 2FA содержит секреты аккаунтов. Храните файл как пароль.")


@router.callback_query(F.data == "twofa:import")
async def import_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not await check_callback(callback):
        return
    await state.clear()
    await state.set_state(Setup.import_config)
    await callback.message.answer("Пришлите twofa_config.json. Текущие настройки и привязки будут заменены.")


@router.message(Setup.import_config, F.document)
async def import_file(message: types.Message, state: FSMContext):
    if not await check_message(message, state):
        return
    if message.document.file_size and message.document.file_size > 1_000_000:
        return await message.answer("Максимальный размер файла — 1 МБ.")
    try:
        file = await message.bot.get_file(message.document.file_id)
        buffer = BytesIO()
        await message.bot.download_file(file.file_path, destination=buffer)
        if len(buffer.getvalue()) > 1_000_000:
            raise ValueError("Файл слишком большой.")
        payload = json.loads(buffer.getvalue().decode("utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("format") != "twofa-playerok-config" or payload.get("version") != 1:
            raise ValueError("Нужен конфиг формата twofa-playerok-config версии 1.")
        config = validate_config(payload.get("config"))
        save_config(config)
    except (ValueError, TypeError, OSError):
        return await message.answer("Не удалось загрузить конфиг. Проверьте формат, секреты, настройки и доступ к файлам.")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    logger.info("Импортировано привязок: %s", len(config["mappings"]))
    await show(message, "✅ Конфигурация загружена.")


@router.callback_query(F.data == "twofa:logs")
async def logs(callback: types.CallbackQuery):
    if not await check_callback(callback):
        return
    await callback.message.answer_document(BufferedInputFile(get_log_bytes(), filename="twofa.log"), caption="Журнал 2FA. Секреты и коды модуль в журнал не записывает.")
