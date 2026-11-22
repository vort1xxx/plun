import asyncio
from html import escape
from io import BytesIO
import json
from logging import getLogger
from hashlib import sha256
from urllib.parse import urlparse

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from settings import Settings as MainSettings
from tgbot.helpful import do_auth
from plbot.playerokbot import get_playerok_bot

from ..kosell_api import KOSellAPI, KOSellAPIError
from ..meta import NAME
from ..module_logging import get_log_bytes
from ..settings import Settings
from ..validation import valid_template as _valid_template
from .states import Setup

router = Router()
logger = getLogger(NAME)


def _cfg():
    return Settings.get("config")["playerok"]


def _save(config):
    Settings.set("config", {"playerok": config})
    logger.info(
        "Конфигурация KOSell сохранена: mappings=%s api_key_present=%s",
        len(config.get("mappings", [])),
        bool(config.get("api_key")),
    )


def _is_signed(user_id):
    main = MainSettings.get("config")
    signed = main.get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(value) for value in signed)


async def _require_signed_message(message: types.Message, state: FSMContext) -> bool:
    if _is_signed(message.from_user.id):
        return True
    await state.clear()
    await do_auth(message, state)
    return False


async def _require_signed_callback(callback: types.CallbackQuery) -> bool:
    if _is_signed(callback.from_user.id):
        return True
    await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    return False


def _menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Настроить ключ KOSell", callback_data="kosellrent:key")],
        [InlineKeyboardButton(text="➕ Добавить ещё товар", callback_data="kosellrent:add")],
        [InlineKeyboardButton(text="📋 Мои связки", callback_data="kosellrent:list")],
        [InlineKeyboardButton(text="✉️ Шаблон сообщения", callback_data="kosellrent:template")],
        [InlineKeyboardButton(text="💾 Сохранить конфигурацию", callback_data="kosellrent:save")],
        [InlineKeyboardButton(text="📤 Выгрузить конфиг", callback_data="kosellrent:export")],
        [InlineKeyboardButton(text="📥 Загрузить конфиг", callback_data="kosellrent:import")],
        [InlineKeyboardButton(text="🧾 Скачать журнал", callback_data="kosellrent:logs")],
    ])


def _product_name(product):
    return str(product.get("name") or product.get("title") or product.get("product_name") or f"Товар {product.get('id')}")


def _mapping_token(mapping):
    return sha256(json.dumps(mapping, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


async def _show(message, note=""):
    config = _cfg()
    mappings = config.get("mappings", [])
    api_status = "настроен" if config.get("api_key") else "не задан"
    body = (
        "⚙️ <b>Автовыдача аренды KOSell → Playerok</b>\n\n"
        f"🔑 Ключ KOSell: <b>{api_status}</b>\n"
        f"🛍️ Объявлений настроено: <b>{len(mappings)}</b>\n"
        f"💱 Валюта: <b>{escape(str(config.get('currency', 'RUB')))}</b>\n\n"
        "Добавьте отдельную связку для каждого объявления: у каждого могут быть свои игра и срок. "
        "После оплаты модуль закажет аренду для соответствующего объявления и отправит данные в чат сделки."
    )
    if note:
        body += "\n\n" + note
    await message.answer(body, reply_markup=_menu_keyboard(), parse_mode="HTML")


async def _resolve_item(raw):
    raw = raw.strip()
    slug = urlparse(raw).path.rstrip("/").split("/")[-1] if "://" in raw else ""
    bot = get_playerok_bot()
    if bot is None or getattr(bot, "account", None) is None:
        return None
    account = bot.account
    try:
        item = await asyncio.to_thread(account.get_item, id=raw)
    except Exception:
        item = None
    if item is None and slug:
        try:
            item = await asyncio.to_thread(account.get_item, slug=slug)
        except Exception:
            item = None
    logger.info("Поиск товара Playerok для привязки KOSell: found=%s", bool(item))
    return item


@router.message(Command(NAME))
async def command_menu(message: types.Message, state: FSMContext):
    await state.clear()
    if not _is_signed(message.from_user.id):
        return await do_auth(message, state)
    await _show(message)


@router.callback_query(F.data == "kosellrent:menu")
async def menu(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_signed_callback(callback):
        return
    await state.clear()
    await callback.answer()
    await _show(callback.message)


@router.callback_query(F.data == "kosellrent:save")
async def save_config(callback: types.CallbackQuery):
    if not _is_signed(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer("Сохраняю настройки")
    _save(_cfg())
    await _show(callback.message, "✅ Конфигурация сохранена. Изменения также сохраняются автоматически при настройке.")


@router.callback_query(F.data == "kosellrent:export")
async def export_config(callback: types.CallbackQuery):
    if not _is_signed(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    config = _cfg()
    payload = {
        "format": "kosellrent-playerok-config",
        "version": 1,
        "playerok": {
            "enabled": bool(config.get("enabled", True)),
            "api_key": str(config.get("api_key", "")),
            "currency": str(config.get("currency", "RUB")),
            "delivery_template": str(config.get("delivery_template", "")),
            "mappings": config.get("mappings", []),
        },
    }
    document = BufferedInputFile(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        filename="kosellrent_config.json",
    )
    await callback.message.answer_document(
        document,
        caption="Конфигурация модуля KOSellRent. Файл содержит API-ключ KOSell — не пересылайте его посторонним.",
    )
    logger.info("Конфигурация KOSell выгружена через Telegram")


@router.callback_query(F.data == "kosellrent:logs")
async def download_logs(callback: types.CallbackQuery):
    if not _is_signed(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer("Готовлю журнал")
    log_bytes = get_log_bytes()
    if not log_bytes:
        return await callback.message.answer("Журнал пока пуст или недоступен. Перезапустите модуль и попробуйте ещё раз.")
    logger.info("Пользователь Telegram %s скачал журнал KOSell", callback.from_user.id)
    await callback.message.answer_document(
        BufferedInputFile(log_bytes, filename="kosellrent.log"),
        caption="Журнал KOSellRent. API-ключи, пароли и Steam Guard коды скрыты.",
    )


@router.callback_query(F.data == "kosellrent:import")
async def import_config_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _is_signed(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(Setup.import_config)
    await callback.message.answer("Загрузите JSON-файл, ранее выгруженный кнопкой «Выгрузить конфиг».")


@router.message(Setup.import_config, F.document)
async def import_config_file(message: types.Message, state: FSMContext):
    if not _is_signed(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    document = message.document
    if document.file_size and document.file_size > 1_000_000:
        return await message.answer("Файл конфигурации слишком большой (максимум 1 МБ).")
    try:
        tg_file = await message.bot.get_file(document.file_id)
        buffer = BytesIO()
        await message.bot.download_file(tg_file.file_path, destination=buffer)
        payload = json.loads(buffer.getvalue().decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Корень JSON должен быть объектом")
        if payload.get("format") not in (None, "kosellrent-playerok-config"):
            raise ValueError("Это конфигурация другого формата")
        raw = payload.get("playerok")
        if raw is None:
            raw = payload.get("config", {}).get("playerok") if isinstance(payload.get("config"), dict) else None
        if not isinstance(raw, dict):
            raise ValueError("В файле нет настроек playerok")

        mappings_raw = raw.get("mappings", [])
        if not isinstance(mappings_raw, list):
            raise ValueError("Поле mappings должно быть списком")
        mappings = []
        seen_items = set()
        for value in mappings_raw:
            if not isinstance(value, dict):
                raise ValueError("Каждая связка товара должна быть объектом")
            item_id = str(value.get("item_id") or "").strip()
            product_id = str(value.get("product_id") or "").strip()
            hours = int(value.get("hours", 0))
            if not item_id or not product_id.isascii() or not product_id.isdigit() or int(product_id) < 1:
                raise ValueError("У связки должны быть item_id и числовой product_id")
            if not 1 <= hours <= 720:
                raise ValueError("Срок каждой аренды должен быть от 1 до 720 часов")
            if item_id in seen_items:
                raise ValueError("В конфиге повторяется ID товара Playerok")
            seen_items.add(item_id)
            mapping_currency = str(value.get("currency", raw.get("currency", "RUB"))).upper()
            if mapping_currency not in ("RUB", "USD"):
                raise ValueError("Валюта связки должна быть RUB или USD")
            mappings.append({
                "item_id": item_id,
                "item_name": str(value.get("item_name", item_id)),
                "product_id": product_id,
                "product_name": str(value.get("product_name", product_id)),
                "hours": hours,
                "currency": mapping_currency,
            })

        template = str(raw.get("delivery_template", _cfg().get("delivery_template", ""))).strip()
        if not _valid_template(template):
            raise ValueError("Шаблон должен содержать {login} и {password}; разрешённые поля: {game}, {login}, {password}, {hours}, {duration}")
        currency = str(raw.get("currency", "RUB")).upper()
        if currency not in ("RUB", "USD"):
            raise ValueError("Валюта должна быть RUB или USD")
        if not isinstance(raw.get("enabled", True), bool):
            raise ValueError("Поле enabled должно быть true или false")
        imported = {
            "enabled": bool(raw.get("enabled", True)),
            "api_key": str(raw.get("api_key") or "").strip(),
            "currency": currency,
            "delivery_template": template,
            "mappings": mappings,
        }
        _save(imported)
        logger.info("Конфигурация KOSell импортирована: mappings=%s", len(mappings))
        await state.clear()
        return await _show(message, f"✅ Конфигурация загружена: товаров — {len(mappings)}. Ключ KOSell {'задан' if imported['api_key'] else 'не задан'}.")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return await message.answer(f"Не удалось загрузить конфиг: {escape(str(exc)[:300])}\nПроверьте JSON и попробуйте снова.")


@router.callback_query(F.data == "kosellrent:key")
async def key_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _is_signed(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(Setup.api_key)
    await callback.message.answer("Отправьте API-ключ KOSell. Он сохранится в настройках модуля и не будет показан в ответе.")


@router.message(Setup.api_key, F.text)
async def key_entered(message: types.Message, state: FSMContext):
    if not await _require_signed_message(message, state):
        return
    key = message.text.strip()
    if not key:
        return await message.answer("API-ключ не может быть пустым.")
    try:
        products = await asyncio.to_thread(KOSellAPI(key).products, "", _cfg().get("currency", "RUB"))
    except KOSellAPIError as exc:
        logger.warning("KOSell API key validation failed: %s", exc.code)
        return await message.answer("Не удалось проверить ключ KOSell. Проверьте ключ и доступ API к аренде.")
    config = _cfg()
    config["api_key"] = key
    _save(config)
    logger.info("API-ключ KOSell обновлён и успешно проверен")
    await state.clear()
    await _show(message, f"Ключ принят. В каталоге KOSell сейчас найдено позиций: {len(products)}.")


@router.callback_query(F.data == "kosellrent:add")
async def add_mapping(callback: types.CallbackQuery, state: FSMContext):
    if not _is_signed(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    if not _cfg().get("api_key"):
        await callback.message.answer("Сначала нажмите «Настроить ключ KOSell» и добавьте API-ключ.")
        return
    await state.set_state(Setup.playerok_item)
    await callback.message.answer("Отправьте ссылку на уже опубликованный товар Playerok или его ID.")


@router.message(Setup.playerok_item, F.text)
async def playerok_item_entered(message: types.Message, state: FSMContext):
    if not await _require_signed_message(message, state):
        return
    item = await _resolve_item(message.text)
    if item is None:
        return await message.answer("Товар не найден. Проверьте ссылку или ID опубликованного объявления.")
    await state.update_data(item_id=str(item.id), item_name=str(item.name or item.id))
    await state.set_state(Setup.kosell_search)
    await message.answer("Товар найден: <b>" + escape(str(item.name or item.id)) + "</b>\n\nВведите название игры для поиска в каталоге KOSell:", parse_mode="HTML")


@router.message(Setup.kosell_search, F.text)
async def kosell_search(message: types.Message, state: FSMContext):
    if not await _require_signed_message(message, state):
        return
    query = message.text.strip()
    if not query:
        return await message.answer("Введите название игры.")
    config = _cfg()
    try:
        products = await asyncio.to_thread(KOSellAPI(config["api_key"]).products, query, config.get("currency", "RUB"))
    except KOSellAPIError as exc:
        logger.warning("KOSell product search failed: %s", exc.code)
        return await message.answer("Не удалось получить список игр KOSell. Проверьте ключ или попробуйте позже.")
    products = [p for p in products if isinstance(p, dict) and p.get("id") is not None]
    products = products[:10]
    if not products:
        return await message.answer("Игра не найдена в каталоге KOSell. Попробуйте другое название.")
    await state.update_data(kosell_products={str(p["id"]): p for p in products})
    buttons = [[InlineKeyboardButton(text=_product_name(p)[:60], callback_data=f"kosellrent:game:{p['id']}")] for p in products]
    await message.answer("Выберите игру из каталога KOSell:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(Setup.kosell_search, F.data.startswith("kosellrent:game:"))
async def kosell_product_selected(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_signed_callback(callback):
        return
    await callback.answer()
    product_id = callback.data.rsplit(":", 1)[-1]
    data = await state.get_data()
    products = data.get("kosell_products", {})
    product = products.get(product_id)
    if not product:
        await callback.message.answer("Выбор устарел. Начните привязку заново через /kosellrent.")
        return
    await state.update_data(product_id=str(product_id), product_name=_product_name(product))
    await state.set_state(Setup.rental_hours)
    await callback.message.answer(
        f"Игра: <b>{escape(_product_name(product))}</b>\n\n"
        "Введите срок аренды в часах (от 1 до 720). Этот срок будет заказан в KOSell при каждой покупке.",
        parse_mode="HTML",
    )


@router.message(Setup.rental_hours, F.text)
async def rental_hours_entered(message: types.Message, state: FSMContext):
    if not await _require_signed_message(message, state):
        return
    try:
        hours = int(message.text.strip())
        if not 1 <= hours <= 720:
            raise ValueError
    except ValueError:
        return await message.answer("Введите целое число часов от 1 до 720.")
    data = await state.get_data()
    mapping = {
        "item_id": data["item_id"],
        "item_name": data["item_name"],
        "product_id": data["product_id"],
        "product_name": data["product_name"],
        "hours": hours,
        "currency": _cfg().get("currency", "RUB"),
    }
    config = _cfg()
    mappings = [m for m in config.get("mappings", []) if str(m.get("item_id")) != mapping["item_id"]]
    mappings.append(mapping)
    config["mappings"] = mappings
    _save(config)
    await state.clear()
    logger.info("Создана или обновлена привязка KOSell: item=%s product=%s hours=%s", mapping["item_id"], mapping["product_id"], hours)
    await _show(message, f"Связка сохранена: <b>{escape(mapping['item_name'])}</b> → <b>{escape(mapping['product_name'])}</b>, аренда {hours} ч.")


@router.callback_query(F.data == "kosellrent:list")
async def list_mappings(callback: types.CallbackQuery):
    if not await _require_signed_callback(callback):
        return
    await callback.answer()
    mappings = _cfg().get("mappings", [])
    if not mappings:
        return await callback.message.answer("Объявлений пока нет. Нажмите «Добавить ещё товар» и настройте первую связку.")
    for start in range(0, len(mappings), 10):
        lines = ["📋 <b>Настроенные связки</b>"]
        buttons = []
        for i, mapping in enumerate(mappings[start:start + 10], start=start):
            lines.append(
                f"\n{i + 1}. <b>{escape(str(mapping.get('item_name', 'товар'))[:80])}</b> "
                f"→ {escape(str(mapping.get('product_name', 'игра'))[:80])}, "
                f"{int(mapping.get('hours', 1))} ч."
            )
            buttons.append([InlineKeyboardButton(text=f"🗑 Удалить {i + 1}", callback_data=f"kosellrent:del:{_mapping_token(mapping)}")])
        buttons.append([InlineKeyboardButton(text="↩️ В меню", callback_data="kosellrent:menu")])
        await callback.message.answer("".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data.startswith("kosellrent:del:"))
async def delete_mapping(callback: types.CallbackQuery):
    if not await _require_signed_callback(callback):
        return
    await callback.answer()
    try:
        token = callback.data.rsplit(":", 1)[-1]
        config = _cfg()
        mappings = config.get("mappings", [])
        index = next((i for i, row in enumerate(mappings) if _mapping_token(row) == token), None)
        if index is None:
            return await callback.message.answer("Эта связка уже удалена или список изменился. Откройте список заново.")
        mappings.pop(index)
        config["mappings"] = mappings
        _save(config)
        logger.info("Удалена привязка KOSell: index=%s", index)
        await callback.message.answer("Связка удалена.")
    except (ValueError, IndexError):
        await callback.message.answer("Эта связка уже удалена или список изменился.")


@router.callback_query(F.data == "kosellrent:template")
async def template_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not await _require_signed_callback(callback):
        return
    await callback.answer()
    await state.set_state(Setup.delivery_template)
    current = escape(_cfg().get("delivery_template", ""))
    await callback.message.answer(
        "Отправьте шаблон сообщения покупателю. Переменные: <code>{game}</code>, "
        "<code>{login}</code>, <code>{password}</code>, <code>{hours}</code>.\n"
        "Текущий шаблон:\n<pre>" + current + "</pre>", parse_mode="HTML",
    )


@router.message(Setup.delivery_template, F.text)
async def template_entered(message: types.Message, state: FSMContext):
    if not await _require_signed_message(message, state):
        return
    if not _valid_template(message.text.strip()):
        return await message.answer("Шаблон должен содержать {login} и {password}. Разрешённые поля: {game}, {login}, {password}, {hours}, {duration}.")
    config = _cfg()
    config["delivery_template"] = message.text.strip()
    _save(config)
    logger.info("Шаблон сообщения KOSell обновлён")
    await state.clear()
    await _show(message, "Шаблон сохранён.")
