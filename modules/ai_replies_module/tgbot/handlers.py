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
from .states import AiSetup

router = Router()
logger = getLogger(NAME)


def _config():
    return Settings.get("config")


def _save(config):
    Settings.set("config", config)
    ai = config.get("ai", {})
    logger.info(
        "Конфигурация ИИ-автоответов сохранена: enabled=%s model=%s groups=%s faq=%s key_present=%s",
        bool(config.get("rules", {}).get("enabled")),
        ai.get("model"),
        len(config.get("groups", []) or []),
        len(config.get("knowledge", []) or []),
        bool(ai.get("api_key")),
    )


def _authorized(user_id):
    users = MainSettings.get("config").get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(value) for value in users)


# ---------- клавиатуры ----------

def _keyboard():
    config = _config()
    rules = config.get("rules", {})
    ai = config.get("ai", {})
    enabled = bool(rules.get("enabled"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'🟢 Автоответы ВКЛ' if enabled else '🔴 Автоответы ВЫКЛ'}",
            callback_data="aire:toggle",
        )],
        [InlineKeyboardButton(text="🔑 API-ключ", callback_data="aire:api_key"),
         InlineKeyboardButton(text="🌐 Провайдер (URL)", callback_data="aire:base_url")],
        [InlineKeyboardButton(text=f"🧠 Модель: {ai.get('model', '—')}", callback_data="aire:model")],
        [InlineKeyboardButton(
            text=f"📦 Группы товаров ({len(config.get('groups', []) or [])})",
            callback_data="aire:grp:list",
        )],
        [InlineKeyboardButton(text="🧩 Общий промпт", callback_data="aire:prompt"),
         InlineKeyboardButton(text=f"📚 Общая база знаний ({len(config.get('knowledge', []) or [])})", callback_data="aire:faq:G")],
        [InlineKeyboardButton(text="⏱ Пауза/лимит/тишина", callback_data="aire:limits")],
        [InlineKeyboardButton(text="🗄 Архив (сохранение/выгрузка/журнал)", callback_data="aire:service")],
    ])


def _service_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить конфиг", callback_data="aire:save")],
        [InlineKeyboardButton(text="📤 Выгрузить конфиг", callback_data="aire:export")],
        [InlineKeyboardButton(text="📥 Загрузить конфиг", callback_data="aire:import")],
        [InlineKeyboardButton(text="🧾 Скачать журнал", callback_data="aire:logs")],
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="aire:menu")],
    ])


def _limits_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📏 Макс. длина ответа", callback_data="aire:max_chars"),
         InlineKeyboardButton(text="⏱ Пауза после вашего сообщения", callback_data="aire:pause")],
        [InlineKeyboardButton(text="🧮 Лимит ответов в сутки", callback_data="aire:daily"),
         InlineKeyboardButton(text="🌙 Тихие часы", callback_data="aire:quiet")],
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="aire:menu")],
    ])


def _back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="aire:menu")],
    ])


def _main_text(note=""):
    config = _config()
    rules = config.get("rules", {})
    ai = config.get("ai", {})
    enabled = bool(rules.get("enabled"))
    text = (
        "🤖 <b>Настройки ИИ-автоответов</b>\n\n"
        f"Автоответы: <b>{'вкл' if enabled else 'выкл'}</b>\n"
        f"Провайдер: <b>{escape(str(ai.get('base_url', '—') or '—'))}</b>\n"
        f"Модель: <b>{escape(str(ai.get('model', '—') or '—'))}</b>\n"
        f"API-ключ: <b>{'задан' if ai.get('api_key') else 'не задан'}</b>\n"
        f"Групп товаров: <b>{len(config.get('groups', []) or [])}</b>, общая база: <b>{len(config.get('knowledge', []) or [])}</b>\n\n"
        "Язык ответов: <b>русский</b>. ИИ отвечает покупателям в чатах сделок; в завершённых сделках и во время автовыдачи HoldBoost — молчит.\n"
        "Товар без группы → общий промпт и общая база знаний."
    )
    if note:
        text += "\n\n" + note
    return text


async def _show(message, note=""):
    await message.answer(_main_text(note), reply_markup=_keyboard(), parse_mode="HTML")


# ---------- поиск товара Playerok ----------

async def _find_item(value: str):
    raw = value.strip()
    slug = urlparse(raw).path.rstrip("/").split("/")[-1] if "://" in raw else ""
    bot = get_playerok_bot()
    account = getattr(bot, "account", None) if bot else None
    if account is None:
        return None
    try:
        item = await asyncio.to_thread(account.get_item, id=raw)
    except Exception:
        item = None
    if item is None and slug:
        try:
            item = await asyncio.to_thread(account.get_item, slug=slug)
        except Exception:
            item = None
    logger.info("Поиск товара Playerok для группы ИИ: found=%s", bool(item))
    return item


# ---------- меню ----------

@router.message(Command(NAME))
async def menu(message: types.Message, state: FSMContext):
    await state.clear()
    if not _authorized(message.from_user.id):
        return await do_auth(message, state)
    await _show(message)


@router.callback_query(F.data == "aire:menu")
async def menu_back(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await state.clear()
    try:
        await callback.message.edit_text(_main_text(), reply_markup=_keyboard(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "aire:toggle")
async def toggle(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    config = _config()
    rules = config.setdefault("rules", {})
    rules["enabled"] = not bool(rules.get("enabled"))
    _save(config)
    await callback.answer(f"Автоответы {'включены' if rules['enabled'] else 'выключены'}", show_alert=True)
    await _show(callback.message)


# ---------- ключ / провайдер / модель ----------

@router.callback_query(F.data == "aire:api_key")
async def api_key_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(AiSetup.api_key)
    await callback.message.answer(
        "Отправьте API-ключ OpenRouter (или вашего провайдера). Ключ сохранится, но не появится в ответе."
    )


@router.message(AiSetup.api_key, F.text)
async def api_key_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    api_key = message.text.strip()
    if not api_key:
        return await message.answer("Ключ пустой. Пришлите ключ ещё раз.")
    config = _config()
    config.setdefault("ai", {})["api_key"] = api_key
    _save(config)
    logger.info("API-ключ ИИ обновлён через Telegram")
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


@router.callback_query(F.data == "aire:base_url")
async def base_url_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    current = _config().get("ai", {}).get("base_url", "")
    await state.set_state(AiSetup.base_url)
    await callback.message.answer(
        f"🌐 Адрес OpenAI-совместимого API. Текущий: <code>{escape(str(current or '—'))}</code>\n\n"
        "OpenRouter: <code>https://openrouter.ai/api/v1</code>\n"
        "DeepSeek: <code>https://api.deepseek.com/v1</code>\n"
        "OpenAI: <code>https://api.openai.com/v1</code>"
    )


@router.message(AiSetup.base_url, F.text)
async def base_url_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    raw = message.text.strip()
    if not raw.startswith(("http://", "https://")):
        return await message.answer("Адрес должен начинаться с http:// или https://")
    config = _config()
    config.setdefault("ai", {})["base_url"] = raw.rstrip("/")
    _save(config)
    await state.clear()
    await _show(message, f"🌐 Адрес API сохранён: <code>{escape(raw)}</code>")


@router.callback_query(F.data == "aire:model")
async def model_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    current = _config().get("ai", {}).get("model", "")
    await state.set_state(AiSetup.model)
    await callback.message.answer(
        f"🧠 Пришлите название модели. Текущая: <b>{escape(str(current or '—'))}</b>.\n\n"
        "Примеры OpenRouter:\n<code>deepseek/deepseek-v4.1-flash</code>\n<code>openai/gpt-4o-mini</code>\n"
        "<code>google/gemini-2.0-flash-001</code>\n<code>deepseek/deepseek-chat-v3:free</code>"
    )


@router.message(AiSetup.model, F.text)
async def model_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    model = message.text.strip()
    if not model:
        return await message.answer("Название модели пустое. Пришлите ещё раз.")
    config = _config()
    config.setdefault("ai", {})["model"] = model
    _save(config)
    await state.clear()
    await _show(message, f"🧠 Модель сохранена: <code>{escape(model)}</code>")


# ---------- общий промпт ----------

@router.callback_query(F.data == "aire:prompt")
async def prompt_show(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    rules = _config().get("rules", {})
    body = "\n".join(str(line) for line in (rules.get("system_prompt") or []))
    await callback.message.answer(
        "🧩 <b>Общий системный промпт</b>\n\n"
        f"Текущий:\n<pre>{escape(body) or '(пусто)'}</pre>\n\n"
        "Пришлите новый текст целиком одним сообщением, чтобы заменить. "
        "Промпт группы товаров перекрывает общий, когда у товара есть группа.",
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
    )


@router.message(AiSetup.prompt_text, F.text)
async def prompt_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    text = message.text.strip()
    if not text:
        return await message.answer("Промпт пустой. Пришлите текст ещё раз.")
    config = _config()
    config.setdefault("rules", {})["system_prompt"] = text.splitlines()
    _save(config)
    await state.clear()
    await _show(message, "✅ Общий промпт сохранён.")


# ---------- База знаний (общая и групп) ----------

def _faq_entries(config, scope):
    """scope: 'G' — общая база, число — номер группы."""
    if scope == "G":
        return config.setdefault("knowledge", [])
    for group in config.get("groups", []):
        if int(group.get("num", 0) or 0) == scope:
            return group.setdefault("knowledge", [])
    return None


def _faq_title(scope):
    return "📚 Общая база знаний" if scope == "G" else f"📚 База знаний группы №{scope}"


def _faq_keyboard(scope):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пункт", callback_data=f"aire:faqadd:{scope}")],
        [InlineKeyboardButton(text="🗑 Очистить базу", callback_data=f"aire:faqclear:{scope}")],
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="aire:menu")],
    ])


async def _show_faq(message, scope, note=""):
    config = _config()
    entries = _faq_entries(config, scope)
    if entries is None:
        return await message.answer("Группа не найдена.")
    lines = [f"{_faq_title(scope)}"]
    if not entries:
        lines.append("\nПока пусто. ИИ будет отвечать по общим правилам и карточке товара.")
    for index, entry in enumerate(entries):
        lines.append(f"\n{index + 1}. ❓ {entry.get('q', '')}\n💬 {entry.get('a', '')}")
    if note:
        lines.append("\n" + note)
    markup_rows = []
    for index in range(len(entries)):
        markup_rows.append([InlineKeyboardButton(text=f"🗑 Удалить {index + 1}", callback_data=f"aire:faqdel:{scope}:{index}")])
    for row in _faq_keyboard(scope).inline_keyboard:
        markup_rows.append(row)
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=markup_rows), parse_mode=None)


@router.callback_query(F.data.startswith("aire:faq:"))
async def faq_view(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    raw = callback.data.rsplit(":", 1)[-1]
    scope = "G" if raw == "G" else int(raw)
    await state.clear()
    await _show_faq(callback.message, scope)


@router.callback_query(F.data.startswith("aire:faqadd:"))
async def faq_add_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    raw = callback.data.rsplit(":", 1)[-1]
    scope = "G" if raw == "G" else int(raw)
    await state.set_state(AiSetup.faq_question)
    await state.update_data(faq_scope=scope)
    await callback.message.answer(
        f"{_faq_title(scope)} ➕ Добавить пункт.\n\nПришлите вопрос покупателя (как можно чаще он звучит):"
    )


@router.message(AiSetup.faq_question, F.text)
async def faq_question_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    question = message.text.strip()
    if not question:
        return await message.answer("Вопрос пустой. Пришлите текст ещё раз.")
    await state.update_data(faq_q=question)
    await state.set_state(AiSetup.faq_answer)
    await message.answer("❓ Вопрос принят:\n" + question[:500] + "\n\nТеперь пришлите ответ продавца:")


@router.message(AiSetup.faq_answer, F.text)
async def faq_answer_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    data = await state.get_data()
    scope = data.get("faq_scope", "G")
    question = data.get("faq_q", "")
    answer = message.text.strip()
    if not answer:
        return await message.answer("Ответ пустой. Пришлите текст ещё раз.")
    config = _config()
    entries = _faq_entries(config, scope)
    if entries is None:
        await state.clear()
        return await message.answer("Группа не найдена.")
    entries.append({"q": question, "a": answer})
    _save(config)
    await state.clear()
    logger.info("Добавлен пункт базы знаний ИИ: scope=%s q=%.60r", scope, question)
    await _show_faq(message, scope, "✅ Пункт добавлен.")


@router.callback_query(F.data.startswith("aire:faqdel:"))
async def faq_delete(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    parts = callback.data.rsplit(":", 2)
    try:
        raw, index = parts[1], int(parts[2])
        scope = "G" if raw == "G" else int(raw)
        config = _config()
        entries = _faq_entries(config, scope)
        removed = entries.pop(index)
        _save(config)
        logger.info("Удалён пункт базы знаний ИИ: scope=%s q=%.60r", scope, removed.get("q", ""))
        await callback.answer("Пункт удалён")
        await _show_faq(callback.message, scope)
    except (ValueError, IndexError, AttributeError):
        await callback.answer("Этот пункт уже удалён.", show_alert=True)


@router.callback_query(F.data.startswith("aire:faqclear:"))
async def faq_clear(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    raw = callback.data.rsplit(":", 1)[-1]
    scope = "G" if raw == "G" else int(raw)
    config = _config()
    entries = _faq_entries(config, scope)
    if entries is None:
        return await callback.answer("Группа не найдена.", show_alert=True)
    count = len(entries)
    entries.clear()
    _save(config)
    logger.info("Очищена база знаний ИИ: scope=%s count=%s", scope, count)
    await callback.answer(f"Удалено пунктов: {count}", show_alert=True)
    await _show_faq(callback.message, scope)


# ---------- группы товаров ----------

def _group_items(group: dict) -> list:
    """Товары группы (текстовый вид для UI); поддерживает старый формат одной привязки."""
    items = group.get("items")
    if isinstance(items, list) and items:
        return [item for item in items if isinstance(item, dict)]
    if group.get("item_id"):
        return [{
            "item_id": group.get("item_id"),
            "item_name": group.get("item_name"),
            "item_url": group.get("item_url"),
        }]
    return []


def _group_by_num(config, number):
    for group in config.get("groups", []):
        if int(group.get("num", 0) or 0) == number:
            return group
    return None


def _grp_from(num):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Прикрепить товар", callback_data=f"aire:grp:attach:{num}"),
         InlineKeyboardButton(text="✏️ Промпт группы", callback_data=f"aire:grp:prompt:{num}")],
        [InlineKeyboardButton(text="📚 База знаний группы", callback_data=f"aire:faq:{num}")],
        [InlineKeyboardButton(text="🗑 Удалить группу", callback_data=f"aire:grp:del:{num}")],
        [InlineKeyboardButton(text="⬅️ В настройки", callback_data="aire:menu")],
    ])


@router.callback_query(F.data == "aire:grp:list")
async def grp_list(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.clear()
    config = _config()
    groups = config.get("groups", [])
    if not groups:
        return await callback.message.answer(
            "📦 <b>Группы пока пусты.</b>\n\nГруппа = товар (по ссылке) + свой промпт и своя база знаний.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Прикрепить товар", callback_data="aire:grp:add")],
                [InlineKeyboardButton(text="⬅️ В настройки", callback_data="aire:menu")],
            ]),
            parse_mode="HTML",
        )
    buttons = []
    for group in groups:
        number = int(group.get("num", 0) or 0)
        items = _group_items(group)
        name = str((items[0].get("item_name") if items else None) or "товар")
        buttons.append([InlineKeyboardButton(
            text=f"{number}. {name[:26]} ({len(items)} шт.)",
            callback_data=f"aire:grp:view:{number}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Прикрепить товар", callback_data="aire:grp:add")])
    buttons.append([InlineKeyboardButton(text="⬅️ В настройки", callback_data="aire:menu")])
    await callback.message.answer("📦 <b>Группы товаров</b>\n\nВыберите группу:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")


@router.callback_query(F.data == "aire:grp:add")
async def grp_add_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(AiSetup.grp_url)
    await callback.message.answer("Пришлите ссылку на опубликованный товар Playerok — он станет новой группой.")


@router.message(AiSetup.grp_url, F.text)
async def grp_url_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    item = await _find_item(message.text)
    if item is None:
        return await message.answer("Не удалось найти товар по ссылке. Проверьте, что объявление опубликовано, и пришлите ссылку ещё раз.")
    data = await state.get_data()
    grp_num = data.get("grp_num")

    config = _config()
    if grp_num:
        # Прикрепление дополнительного товара к существующей группе.
        group = _group_by_num(config, int(grp_num or 0))
        if group is None:
            await state.clear()
            return await message.answer("Группа не найдена. Начните заново через /aireplies.")
        items = group.setdefault("items", [])
        if str(item.id) in {str(it.get("item_id")) for it in items}:
            await state.clear()
            return await message.answer("Этот товар уже есть в группе.")
        items.append({
            "item_id": str(item.id),
            "item_name": str(getattr(item, "name", None) or item.id),
            "item_url": message.text.strip(),
        })
        _save(config)
        await state.clear()
        logger.info("Товар прикреплён к группе ИИ: num=%s item=%s (всего %s)", grp_num, item.id, len(items))
        await message.answer(
            f"✅ Товар <b>{escape(str(getattr(item, 'name', None) or item.id))}</b> прикреплён к группе №{grp_num}.\n"
            f"Теперь в группе <b>{len(items)}</b> товар(ов) — все получают промпт и базу знаний группы.",
            reply_markup=_grp_from(int(grp_num)),
            parse_mode="HTML",
        )
        return

    # Новая группа: первый товар.
    groups = config.get("groups", [])
    next_num = max((int(g.get("num", 0) or 0) for g in groups), default=0) + 1
    group = {
        "num": next_num,
        "items": [{
            "item_id": str(item.id),
            "item_name": str(getattr(item, "name", None) or item.id),
            "item_url": message.text.strip(),
        }],
        "system_prompt": [],
        "knowledge": [],
    }
    groups.append(group)
    config["groups"] = groups
    _save(config)
    await state.clear()
    logger.info("Создана группа ИИ-автоответов: num=%s item=%s", next_num, group["items"][0]["item_id"])
    await message.answer(
        f"📦 Группа <b>№{next_num}</b> создана, товар <b>{escape(group['items'][0]['item_name'])}</b> прикреплён.\n"
        "Можно прикрепить ещё товары (они получат те же настройки) либо задать промпт и базу знаний.",
        reply_markup=_grp_from(next_num),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("aire:grp:view:"))
async def grp_view(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    try:
        number = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        return await callback.answer("Группа не найдена.", show_alert=True)
    group = _group_by_num(_config(), number)
    if group is None:
        return await callback.answer("Группа не найдена.", show_alert=True)
    items = _group_items(group)
    prompt = "\n".join(str(line) for line in (group.get("system_prompt") or []))
    lines = [
        f"📦 <b>Группа №{number}</b>\n",
        f"Товаров: <b>{len(items)}</b>",
    ]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {escape(str(item.get('item_name', '—'))[:60])}")
    lines.append(
        f"\nСвой промпт: {'<b>задан</b> (перекрывает общий)' if prompt else '<i>не задан, используется общий</i>'}\n"
        f"Своя база знаний: <b>{len(group.get('knowledge', []) or [])}</b> пунктов\n\n"
        "Покупателя по любому товару группы ИИ обслуживает правилами этой группы; "
        "пустой промпт/база → общие."
    )
    rows = []
    for index in range(len(items)):
        rows.append([InlineKeyboardButton(
            text=f"🗑 Открепить {index + 1}",
            callback_data=f"aire:grp:itmdel:{number}:{index}",
        )])
    for row in _grp_from(number).inline_keyboard:
        rows.append(row)
    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("aire:grp:attach:"))
async def grp_attach_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    try:
        number = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        return await callback.answer("Группа не найдена.", show_alert=True)
    await state.set_state(AiSetup.grp_url)
    await state.update_data(grp_num=number)
    await callback.message.answer(
        f"Пришлите ссылку на товар Playerok — он будет прикреплён к группе №{number} "
        "и получит её промпт и базу знаний."
    )


@router.callback_query(F.data.startswith("aire:grp:itmdel:"))
async def grp_item_delete(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    parts = callback.data.rsplit(":", 2)
    try:
        number, index = int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        return await callback.answer("Группа не найдена.", show_alert=True)
    config = _config()
    group = _group_by_num(config, number)
    if group is None:
        return await callback.answer("Группа не найдена.", show_alert=True)
    # СТАРЫЙ формат (одна привязка) конвертируем в список при первом удалении/правке.
    items = group.setdefault("items", [])
    if not items and group.get("item_id"):
        items.append({
            "item_id": group.get("item_id"),
            "item_name": group.get("item_name"),
            "item_url": group.get("item_url"),
        })
    try:
        removed = items.pop(index)
    except (IndexError, TypeError):
        return await callback.answer("Этот товар уже откреплён.", show_alert=True)
    if not items:
        config.get("groups", []).remove(group)
        _save(config)
        logger.info("Группа ИИ удалена вместе с последним товаром: num=%s", number)
        await callback.answer("Товар откреплён; группа была без товаров и удалена", show_alert=True)
        return await callback.message.answer(
            f"🗑 Товар «{escape(str(removed.get('item_name', 'товар')))[:60]}» откреплён. "
            f"Группа №{number} опустела и была удалена."
        )
    _save(config)
    logger.info("Товар откреплён от группы ИИ: num=%s item=%s", number, removed.get("item_id"))
    await callback.answer("Товар откреплён")
    await callback.message.answer(f"🗑 Товар «{escape(str(removed.get('item_name', 'товар')))[:60]}» откреплён от группы №{number}. В группе осталось {len(items)} товар(ов).")


@router.callback_query(F.data.startswith("aire:grp:prompt:"))
async def grp_prompt_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    try:
        number = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        return await callback.answer("Группа не найдена.", show_alert=True)
    await state.set_state(AiSetup.grp_prompt)
    await state.update_data(grp_num=number)
    group = _group_by_num(_config(), number)
    body = "\n".join(str(line) for line in ((group or {}).get("system_prompt") or []))
    await callback.message.answer(
        f"✏️ Промпт группы №{number}.\n\n"
        f"Текущий:\n<pre>{escape(body) or '(пусто — используется общий)'}</pre>\n\n"
        "Пришлите новый текст одним сообщением (каждая строка = строка промпта).",
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
    )


@router.message(AiSetup.grp_prompt, F.text)
async def grp_prompt_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    data = await state.get_data()
    number = data.get("grp_num")
    config = _config()
    group = _group_by_num(config, int(number or 0))
    if group is None:
        await state.clear()
        return await message.answer("Группа не найдена.")
    text = message.text.strip()
    if not text:
        return await message.answer("Промпт пустой. Пришлите текст ещё раз.")
    group["system_prompt"] = text.splitlines()
    _save(config)
    await state.clear()
    logger.info("Обновлён промпт группы ИИ: num=%s", number)
    await _show(message, f"✅ Промпт группы №{escape(str(number))} сохранён.")


@router.callback_query(F.data.startswith("aire:grp:del:"))
async def grp_delete(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    try:
        number = int(callback.data.rsplit(":", 1)[-1])
        config = _config()
        groups = config.get("groups", [])
        index = [i for i, g in enumerate(groups) if int(g.get("num", 0) or 0) == number][0]
        removed = groups.pop(index)
        config["groups"] = groups
        _save(config)
        logger.info("Удалена группа ИИ: num=%s item=%s", number, removed.get("item_name"))
        await callback.answer(f"Группа №{number} удалена", show_alert=True)
        await callback.message.answer(f"🗑 Группа №{number} («{escape(str(removed.get('item_name', 'товар')))}») удалена. Работа ИИ продолжится без неё.")
    except (ValueError, IndexError):
        await callback.answer("Группа уже удалена.", show_alert=True)


# ---------- лимиты ----------

@router.callback_query(F.data == "aire:limits")
async def limits_menu(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    rules = _config().get("rules", {})
    await callback.message.answer(
        f"⏱ <b>Лимиты и паузы</b>\n\n"
        f"Максимальная длина ответа: <b>{rules.get('max_reply_chars', 700)}</b> символов\n"
        f"Пауза после вашего ручного сообщения: <b>{rules.get('pause_after_seller_minutes', 5)}</b> мин\n"
        f"Дневной лимит ответов: <b>{rules.get('daily_limit', 150)}</b>\n"
        f"Тихие часы: <b>{rules.get('quiet_start', 0)}–{rules.get('quiet_end', 0)}</b> (0–0 = выключено)\n\n"
        "В тихие часы ИИ не отвечает; сообщения покупателей по-прежнему видны в Playerok.",
        reply_markup=_limits_keyboard(),
        parse_mode="HTML",
    )


def _limits_back():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К лимитам", callback_data="aire:limits")]])


async def _ask_number(callback: types.CallbackQuery, state: FSMContext, fsm_state, header: str):
    await callback.answer()
    await state.set_state(fsm_state)
    await callback.message.answer(header, reply_markup=_limits_back())


@router.callback_query(F.data == "aire:max_chars")
async def max_chars_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await _ask_number(callback, state, AiSetup.max_chars, "📏 Пришлите максимальную длину ответа в символах (например 700).")


@router.message(AiSetup.max_chars, F.text)
async def max_chars_entered(message: types.Message, state: FSMContext):
    await _set_int(message, state, "max_reply_chars", 100)


@router.callback_query(F.data == "aire:pause")
async def pause_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await _ask_number(callback, state, AiSetup.pause_minutes, "⏱ Пришлите паузу после вашего ручного сообщения в минутах (например 5).")


@router.message(AiSetup.pause_minutes, F.text)
async def pause_entered(message: types.Message, state: FSMContext):
    await _set_int(message, state, "pause_after_seller_minutes", 0, is_float=True)


@router.callback_query(F.data == "aire:daily")
async def daily_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await _ask_number(callback, state, AiSetup.daily_limit, "🧮 Пришлите дневной лимит автоответов (например 150).")


@router.message(AiSetup.daily_limit, F.text)
async def daily_entered(message: types.Message, state: FSMContext):
    await _set_int(message, state, "daily_limit", 1)


@router.callback_query(F.data == "aire:quiet")
async def quiet_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(AiSetup.quiet_hours)
    await callback.message.answer(
        "🌙 Пришлите тихие часы в формате <code>HH-HH</code> (например <code>23-8</code> — не отвечать с 23:00 до 08:00) или <code>-</code>, чтобы выключить.",
        parse_mode="HTML",
        reply_markup=_limits_back(),
    )


@router.message(AiSetup.quiet_hours, F.text)
async def quiet_entered(message: types.Message, state: FSMContext):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    raw = message.text.strip()
    config = _config()
    rules = config.setdefault("rules", {})
    if raw in {"-", "—"}:
        rules["quiet_start"] = 0
        rules["quiet_end"] = 0
        _save(config)
        await state.clear()
        return await _show(message, "🌙 Тихие часы выключены.")
    import re as _re
    match = _re.match(r"^(\d{1,2})\s*[-–]\s*(\d{1,2})$", raw)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 23:
        return await message.answer("Формат: HH-HH, например 23-8. Или «-», чтобы выключить.")
    rules["quiet_start"] = int(match.group(1))
    rules["quiet_end"] = int(match.group(2))
    _save(config)
    await state.clear()
    await _show(message, f"🌙 Тихие часы: {rules['quiet_start']}–{rules['quiet_end']}.")


async def _set_int(message: types.Message, state: FSMContext, key: str, minimum: int, is_float: bool = False):
    if not _authorized(message.from_user.id):
        await state.clear()
        return await message.answer("Сначала авторизуйтесь в боте.")
    raw = message.text.strip().replace(",", ".")
    try:
        value = float(raw) if is_float else int(raw)
    except ValueError:
        return await message.answer("Введите число, например 5.")
    config = _config()
    config.setdefault("rules", {})[key] = value
    _save(config)
    await state.clear()
    await _show(message, f"✅ Сохранено: {key} = {value}.")


# ---------- Архив ----------

@router.callback_query(F.data == "aire:service")
async def service_tab(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await callback.message.answer(
        "🗄 <b>Архив и обслуживание</b>",
        reply_markup=_service_keyboard(),
    )


@router.callback_query(F.data == "aire:save")
async def save_config(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    _save(_config())
    await callback.answer("Конфигурация сохранена", show_alert=True)


@router.callback_query(F.data == "aire:export")
async def export_config(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    payload = {"format": "aireplies-config", "version": 1, "config": _config()}
    await callback.message.answer_document(
        BufferedInputFile(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            filename="aireplies_config.json",
        ),
        caption="Конфигурация ИИ-автоответов. Внутри API-ключ — не пересылайте посторонним.",
    )
    logger.info("Конфигурация ИИ-автоответов выгружена через Telegram")


@router.callback_query(F.data == "aire:import")
async def import_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer()
    await state.set_state(AiSetup.import_config)
    await callback.message.answer("Отправьте JSON-файл, ранее выгруженный из этого модуля. Текущие настройки будут заменены.")


@router.message(AiSetup.import_config, F.document)
async def import_file(message: types.Message, state: FSMContext):
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
        if payload.get("format") not in (None, "aireplies-config"):
            raise ValueError("Это конфиг другого формата")
        imported = payload.get("config", payload)
        if not isinstance(imported, dict):
            raise ValueError("В конфиге нет объекта config")
        ai = imported.get("ai")
        rules = imported.get("rules")
        groups = imported.get("groups", [])
        knowledge = imported.get("knowledge", [])
        ignored = imported.get("ignored_chats", [])
        if not isinstance(ai, dict) or not isinstance(rules, dict):
            raise ValueError("Нужны разделы ai и rules")
        for name, value in (("groups", groups), ("knowledge", knowledge), ("ignored_chats", ignored)):
            if not isinstance(value, list):
                raise ValueError(f"{name} должен быть списком")
        current = _config()
        merged = dict(current)
        merged["ai"] = {**current.get("ai", {}), **ai}
        merged["ai"]["api_key"] = str(merged["ai"].get("api_key", "")).strip()
        base_url = str(merged["ai"].get("base_url", "https://openrouter.ai/api/v1")).strip()
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Некорректный ai.base_url")
        merged["ai"]["base_url"] = base_url
        merged["rules"] = {**current.get("rules", {}), **rules}
        merged["rules"]["system_prompt"] = [str(line) for line in (rules.get("system_prompt") or [])]
        merged["groups"] = groups
        merged["knowledge"] = knowledge
        merged["ignored_chats"] = ignored
        _save(merged)
        await state.clear()
        return await _show(message, "✅ Конфиг загружен.")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return await message.answer(f"Не удалось загрузить конфиг: {escape(str(exc)[:300])}")


@router.callback_query(F.data == "aire:logs")
async def download_logs(callback: types.CallbackQuery):
    if not _authorized(callback.from_user.id):
        return await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    await callback.answer("Готовлю журнал")
    log_bytes = get_log_bytes()
    if not log_bytes:
        return await callback.message.answer("Журнал пока пуст или недоступен.")
    logger.info("Пользователь Telegram %s скачал журнал ИИ-автоответов", callback.from_user.id)
    await callback.message.answer_document(
        BufferedInputFile(log_bytes, filename="aireplies.log"),
        caption="Журнал ИИ-автоответов. API-ключи скрыты.",
    )
