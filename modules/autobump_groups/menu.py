import asyncio
from datetime import datetime
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import time
from uuid import uuid4
from urllib.parse import urlparse

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from settings import Settings as MainSettings
from tgbot.helpful import do_auth
from plbot.playerokbot import get_playerok_bot

from .logic import LOCAL_TZ, next_check, parse_window, parse_period, period_label
from .storage import LOCK, get_config, get_state, save_config, save_state
from .worker import builtin_enabled, runtime_status, raise_group

router = Router()
_manual_running = set()


class Setup(StatesGroup):
    name = State()
    edit = State()
    links = State()
    import_config = State()


def signed(user_id):
    users = (MainSettings.get("config") or {}).get("telegram", {}).get("bot", {}).get("signed_users", [])
    return any(str(user_id) == str(user) for user in users)


async def auth_message(message, state):
    if message.from_user and signed(message.from_user.id):
        return True
    await state.clear()
    await do_auth(message, state)
    return False


async def auth_callback(callback):
    if signed(callback.from_user.id):
        await callback.answer()
        return True
    await callback.answer("Сначала авторизуйтесь в боте.", show_alert=True)
    return False


def b(text, action):
    return [InlineKeyboardButton(text=text, callback_data="bg:" + action)]


def kb(rows):
    buttons = [button for row in rows for button in row]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 2] for i in range(0, len(buttons), 2)])


def item_token(item):
    return sha256(item["id"].encode()).hexdigest()[:12]


def find_group(config, gid):
    return next((g for g in config["groups"] if g["id"] == gid), None)


async def show_main(message, note=""):
    config = get_config()
    text = ("⬆️ <b>Автоподнятие по группам</b>\n\n"
            f"Работа: <b>{'включена' if config['enabled'] else 'выключена'}</b>\n"
            f"Групп: {len(config['groups'])}; товаров: {sum(len(g['items']) for g in config['groups'])}\n"
            "У каждой группы своё окно проверки, частота проверок и порог позиции. Время — Калининград (UTC+2).")
    text += "\n\nСостояние: " + escape(runtime_status(get_playerok_bot()))
    if builtin_enabled():
        text += "\n\n⚠️ Встроенное автоподнятие бота включено. Этот модуль приостановлен: отключите встроенное поднятие в настройках бота."
    if note:
        text += "\n\n" + escape(note)
    await message.answer(text, parse_mode="HTML", reply_markup=kb([
        b("⏸ Выключить" if config["enabled"] else "▶️ Включить", "toggle"),
        b("➕ Создать группу", "create"), b("📂 Мои группы", "groups:0"),
        b("💾 Сохранить", "save"), b("📤 Выгрузить конфиг", "export"),
        b("📥 Загрузить конфиг", "import"), b("🧾 Скачать журнал", "logs"),
    ]))


async def show_group(message, gid, note=""):
    group = find_group(get_config(), gid)
    if group is None:
        return await show_main(message, "Группа уже удалена.")
    text = (f"📂 <b>{escape(group['name'])}</b>\n\n"
            f"Работа группы: {'включена' if group['enabled'] else 'на паузе'}\n"
            f"Время проверки: <b>{group['window_start']} — {group['window_end']}</b> ежедневно (Калининград, UTC+2)\n"
            f"Проверять каждые: <b>{period_label(group['check_interval_seconds'])}</b>\n"
            f"Поднимать, если позиция <b>больше {group['position']}</b>\n"
            f"Товаров: {len(group['items'])}\n\n"
            "Поднятие выбирает первый PREMIUM или платный статус, как встроенный бот, и оплачивает его с баланса Playerok. "
            "Автоматические проверки выполняются внутри окна. Если позиция равна порогу или меньше, товар не поднимается. "
            "Кнопка «Поднять товары» запускает платное поднятие всех опубликованных товаров группы сразу, без проверки времени и позиции.")
    if note:
        text += "\n\n" + escape(note)
    await message.answer(text, parse_mode="HTML", reply_markup=kb([
        b("⏸ Пауза группы" if group["enabled"] else "▶️ Включить группу", "gt:" + gid),
        b("➕ Привязать товары по ссылкам", "add:" + gid),
        b("📦 Товары и статус", f"items:{gid}:0"),
        b("✏️ Название группы", f"edit:{gid}:name"),
        b("🕒 Время проверки", f"edit:{gid}:window"),
        b("🔄 Частота проверок", f"edit:{gid}:check_interval_seconds"),
        b("📍 Порог позиции", f"edit:{gid}:position"),
        b("⬆️ Поднять товары", "raise:" + gid),
        b("🗑 Удалить группу", "delete:" + gid), b("↩️ Группы", "groups:0"),
    ]))


@router.callback_query(F.data.startswith("bg:raise:"))
async def manual_raise(callback: types.CallbackQuery, state: FSMContext):
    if not await auth_callback(callback):
        return
    gid = callback.data.rsplit(':', 1)[1]
    if gid in _manual_running:
        return await callback.message.answer("Поднятие этой группы уже выполняется.")
    bot = get_playerok_bot()
    if bot is None or getattr(bot, 'account', None) is None:
        return await callback.message.answer("Playerok-бот ещё не запущен.")
    _manual_running.add(gid)
    try:
        await state.clear()
        progress = await callback.message.answer("⬆️ Поднимаю товары группы…")
        try:
            totals = await asyncio.to_thread(raise_group, bot.account, gid)
        except Exception as exc:
            return await progress.edit_text("Не удалось запустить поднятие: " + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
        await progress.edit_text(
            f"✅ Поднято: {totals['raised']}\n"
            f"Пропущено: {totals['skipped']}\n"
            f"Ошибок: {totals['errors']}\n"
            f"Требуют проверки списания: {totals['uncertain']}"
        )
        await show_group(callback.message, gid)
    finally:
        _manual_running.discard(gid)


@router.message(Command("bumpgroups"))
async def command_menu(message: types.Message, state: FSMContext):
    if not await auth_message(message, state):
        return
    await state.clear()
    await show_main(message)


@router.callback_query(F.data == "bg:menu")
async def menu(callback: types.CallbackQuery, state: FSMContext):
    if not await auth_callback(callback):
        return
    await state.clear()
    await show_main(callback.message)


@router.callback_query(F.data == "bg:toggle")
async def toggle(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    with LOCK:
        config = get_config()
        config["enabled"] = not config["enabled"]
        save_config(config)
    await show_main(callback.message)


@router.callback_query(F.data.startswith("bg:groups:"))
async def groups(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    try:
        page = max(0, int(callback.data.rsplit(":", 1)[1]))
    except ValueError:
        return
    all_groups = get_config()["groups"]
    rows = [b(f"{'▶️' if group['enabled'] else '⏸'} {group['name'][:24]} · {group['window_start']}–{group['window_end']}", "group:" + group["id"]) for group in all_groups[page * 10:page * 10 + 10]]
    if page:
        rows.append(b("⬅️ Предыдущие", f"groups:{page-1}"))
    if len(all_groups) > (page + 1) * 10:
        rows.append(b("Следующие ➡️", f"groups:{page+1}"))
    rows += [b("➕ Создать группу", "create"), b("↩️ В меню", "menu")]
    await callback.message.answer("📂 Выберите группу" if all_groups else "Групп пока нет.", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("bg:group:"))
async def group_menu(callback: types.CallbackQuery, state: FSMContext):
    if not await auth_callback(callback):
        return
    await state.clear()
    await show_group(callback.message, callback.data.rsplit(":", 1)[1])


@router.callback_query(F.data == "bg:create")
async def create(callback: types.CallbackQuery, state: FSMContext):
    if not await auth_callback(callback):
        return
    await state.clear()
    await state.set_state(Setup.name)
    await callback.message.answer("Введите название новой группы (до 80 символов).")


@router.message(Setup.name, F.text)
async def create_name(message: types.Message, state: FSMContext):
    if not await auth_message(message, state):
        return
    name = message.text.strip()
    if not 1 <= len(name) <= 80:
        return await message.answer("Название должно содержать 1–80 символов.")
    gid = uuid4().hex[:12]
    with LOCK:
        config = get_config()
        if len(config["groups"]) >= 100:
            return await message.answer("Достигнут лимит 100 групп.")
        config["groups"].append(dict(id=gid, name=name, enabled=False, window_start="18:00", window_end="20:00", check_interval_seconds=30, position=5, items=[]))
        save_config(config)
    await state.clear()
    await show_group(message, gid, "Группа создана на паузе. Настройте время, частоту проверок, порог позиции и товары, затем включите её.")


@router.callback_query(F.data.startswith("bg:gt:"))
async def group_toggle(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    gid = callback.data.rsplit(":", 1)[1]
    with LOCK:
        config = get_config()
        group = find_group(config, gid)
        if group:
            group["enabled"] = not group["enabled"]
            save_config(config)
    await show_group(callback.message, gid)


@router.callback_query(F.data.startswith("bg:edit:"))
async def edit_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not await auth_callback(callback):
        return
    _, _, gid, field = callback.data.split(":")
    prompts = {"window": "Введите ежедневное время проверки: 18:00 - 20:00 по Калининграду. Через полночь: 22:00 - 02:00. Весь день: 00:00 - 24:00.",
               "check_interval_seconds": "Введите интервал между проверками внутри окна: 30 сек, 5 мин, 1 ч. Число без единицы — минуты.",
               "position": "Введите порог позиции от 1 до 100000. Например 5: при позиции 6 товар поднимается, при позиции 5 или меньше — нет.",
               "name": "Введите новое название группы (до 80 символов)."}
    if field not in prompts or find_group(get_config(), gid) is None:
        return
    await state.clear()
    await state.update_data(gid=gid, field=field)
    await state.set_state(Setup.edit)
    await callback.message.answer(prompts[field])


@router.message(Setup.edit, F.text)
async def edit_value(message: types.Message, state: FSMContext):
    if not await auth_message(message, state):
        return
    data = await state.get_data()
    try:
        field, value = data["field"], message.text.strip()
        if field == "check_interval_seconds":
            value = parse_period(value)
        elif field == "window":
            value = parse_window(value)
        elif field == "position":
            value = int(value)
            if not 1 <= value <= 100000:
                raise ValueError("Порог позиции: от 1 до 100000.")
        elif field == "name" and not 1 <= len(value) <= 80:
            raise ValueError("Название: 1–80 символов.")
        with LOCK:
            config = get_config()
            group = find_group(config, data["gid"])
            if group is None:
                await state.clear()
                return await show_main(message, "Группа удалена.")
            if field == "window":
                group['window_start'], group['window_end'] = value
            elif field in ("name", "position", "check_interval_seconds"):
                group[field] = value
            else:
                raise ValueError("Эта настройка больше не используется. Откройте меню группы заново.")
            save_config(config)
    except (ValueError, KeyError) as exc:
        return await message.answer(str(exc) if str(exc) and "invalid literal" not in str(exc) else "Введите целое число.")
    await state.clear()
    await show_group(message, data["gid"], "Настройка сохранена.")


async def resolve_item(link):
    uri = urlparse(link)
    if uri.scheme not in ("http", "https") or uri.hostname not in ("playerok.com", "www.playerok.com") or not uri.path.startswith("/products/"):
        return None
    slug = uri.path.rstrip("/").split("/")[-1]
    bot = get_playerok_bot()
    if bot is None or getattr(bot, "account", None) is None:
        return None
    try:
        item = await asyncio.to_thread(bot.account.get_item, slug=slug)
        if item is None or str(getattr(getattr(item, "user", None), "id", "")) != str(bot.account.id):
            return None
        return item
    except Exception:
        return None


@router.callback_query(F.data.startswith("bg:add:"))
async def links_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not await auth_callback(callback):
        return
    gid = callback.data.rsplit(":", 1)[1]
    if find_group(get_config(), gid) is None:
        return
    await state.clear()
    await state.update_data(gid=gid)
    await state.set_state(Setup.links)
    await callback.message.answer("Пришлите ссылки на ваши товары Playerok: одну ссылку на строку, до 20 за сообщение. Товар из другой группы будет перенесён сюда.")


@router.message(Setup.links, F.text)
async def links_entered(message: types.Message, state: FSMContext):
    if not await auth_message(message, state):
        return
    links = list(dict.fromkeys(message.text.split()))
    if not 1 <= len(links) <= 20:
        return await message.answer("Пришлите от 1 до 20 ссылок.")
    gid = (await state.get_data()).get("gid")
    found = []
    failures = 0
    for link in links:
        item = await resolve_item(link)
        if item is None:
            failures += 1
        elif not any(row["id"] == str(item.id) for row in found):
            found.append({"id": str(item.id), "name": str(item.name or item.id)})
    if not found:
        return await message.answer("Товары не найдены. Проверьте ссылки, владельца и запуск Playerok-бота.")
    with LOCK:
        config = get_config()
        group = find_group(config, gid)
        if group is None:
            await state.clear()
            return await show_main(message, "Группа удалена.")
        ids = {row["id"] for row in found}
        for other in config["groups"]:
            other["items"] = [row for row in other["items"] if row["id"] not in ids]
        group["items"].extend(found)
        try:
            save_config(config)
        except ValueError as exc:
            return await message.answer(str(exc))
    await state.clear()
    await show_group(message, gid, f"Привязано товаров: {len(found)}. Не найдено ссылок: {failures}.")


RESULTS = {"default_priority": "обычный приоритет — пропущен, как в боте", "scheduled": "ожидает проверки", "checking": "проверяется", "raised": "поднят", "position_ok": "позиция подходит",
           "unknown_position": "позиция неизвестна", "no_priority": "нет доступного статуса поднятия", "outside_window": "время проверки закончилось", "inactive": "не опубликован",
           "not_owned": "не найден / другой владелец", "uncertain": "требуется проверить списание", "attempting": "запрос отправлен",
           "settings_changed": "настройки изменились", "check_error": "ошибка проверки"}


@router.callback_query(F.data.startswith("bg:items:"))
async def items(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    try:
        _, _, gid, raw_page = callback.data.split(":")
        page = max(0, int(raw_page))
    except ValueError:
        return
    group = find_group(get_config(), gid)
    if group is None:
        return await show_main(callback.message, "Группа удалена.")
    state = get_state()["items"]
    display_items = group["items"]
    lines, rows = ["📦 <b>Товары группы</b>"], []
    for index, row in enumerate(display_items[page * 5:page * 5 + 5], page * 5 + 1):
        info = state.get(row["id"], {})
        # Older versions saved priority_position or an index in the seller list.
        position = info.get("position") if info.get("position_source") == "sequence" else None
        result = RESULTS.get(info.get("result"), "ещё не проверялся")
        if info.get('result') == 'check_error':
            result += f" ({info.get('error', 'неизвестная ошибка')})"
        when = datetime.fromtimestamp(next_check(max(time.time(), info.get("next_at", 0)), group), LOCAL_TZ).strftime("%d.%m %H:%M:%S") if group['enabled'] and get_config()['enabled'] else "группа / модуль на паузе"
        lines.append(f"\n{index}. <b>{escape(row['name'][:100])}</b>\n{escape(result)}; позиция: {position or '—'}\nСледующая проверка: {when} (Калининград)")
        rows.append(b(f"🗑 Убрать товар {index}", f"remove:{gid}:{item_token(row)}"))
        if info.get("uncertain"):
            rows.append(b(f"⚠️ Проверил списание: товар {index}", f"reset:{gid}:{item_token(row)}"))
    if page:
        rows.append(b("⬅️ Предыдущие", f"items:{gid}:{page-1}"))
    if len(display_items) > (page + 1) * 5:
        rows.append(b("Следующие ➡️", f"items:{gid}:{page+1}"))
    rows += [b("➕ Добавить товары", "add:" + gid), b("↩️ В группу", "group:" + gid)]
    await callback.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("bg:remove:"))
async def remove_item(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    _, _, gid, token = callback.data.split(":")
    with LOCK:
        config = get_config()
        group = find_group(config, gid)
        if group:
            group["items"] = [row for row in group["items"] if item_token(row) != token]
            save_config(config)
    await show_group(callback.message, gid, "Товар убран из группы.")


@router.callback_query(F.data.startswith("bg:reset:"))
async def reset_prompt(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    _, _, gid, token = callback.data.split(":")
    await callback.message.answer("Проверьте историю списаний Playerok. Если прошлый запрос завершился неопределённо, повтор может ещё раз списать баланс. Возобновить плановые поднятия этого товара?", reply_markup=kb([b("Возобновить после проверки", f"resetok:{gid}:{token}"), b("↩️ Отмена", "group:" + gid)]))


@router.callback_query(F.data.startswith("bg:resetok:"))
async def reset(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    _, _, gid, token = callback.data.split(":")
    with LOCK:
        group = find_group(get_config(), gid)
        state = get_state()
        known = group["items"] if group else []
        row = next((row for row in known if item_token(row) == token), None) if group else None
        if row:
            state = get_state()
            info = state["items"].setdefault(row["id"], {})
            info.update(uncertain=False, schedule="", result="scheduled")
            save_state(state)
    await show_group(callback.message, gid, "Блокировка снята. Товар снова участвует в плановых проверках группы.")


@router.callback_query(F.data.startswith("bg:delete:"))
async def delete_prompt(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    gid = callback.data.rsplit(":", 1)[1]
    await callback.message.answer("Удалить группу со всеми привязками? Объявления на Playerok сохранятся.", reply_markup=kb([b("🗑 Удалить группу", "deleteok:" + gid), b("↩️ Отмена", "group:" + gid)]))


@router.callback_query(F.data.startswith("bg:deleteok:"))
async def delete_group(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    gid = callback.data.rsplit(":", 1)[1]
    with LOCK:
        config = get_config()
        config["groups"] = [group for group in config["groups"] if group["id"] != gid]
        save_config(config)
    await show_main(callback.message, "Группа удалена.")


@router.callback_query(F.data == "bg:save")
async def save(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    with LOCK:
        save_config(get_config())
    await show_main(callback.message, "Конфигурация сохранена. Изменения также сохраняются автоматически.")


@router.callback_query(F.data == "bg:export")
async def export(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    value = {"format": "bumpgroups", "version": 1, "config": get_config()}
    await callback.message.answer_document(BufferedInputFile(json.dumps(value, ensure_ascii=False, indent=2).encode(), filename="bumpgroups.json"))


@router.callback_query(F.data == "bg:import")
async def import_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not await auth_callback(callback):
        return
    await state.clear()
    await state.set_state(Setup.import_config)
    await callback.message.answer("Пришлите bumpgroups.json (до 1 МБ). Группы будут заменены; работа после импорта будет на паузе.")


@router.message(Setup.import_config, F.document)
async def import_file(message: types.Message, state: FSMContext):
    if not await auth_message(message, state):
        return
    if message.document.file_size and message.document.file_size > 1_000_000:
        return await message.answer("Максимальный размер — 1 МБ.")
    try:
        file = await message.bot.get_file(message.document.file_id)
        buffer = BytesIO()
        await message.bot.download_file(file.file_path, destination=buffer)
        if len(buffer.getvalue()) > 1_000_000:
            raise ValueError("Файл слишком большой.")
        payload = json.loads(buffer.getvalue().decode("utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("format") != "bumpgroups" or payload.get("version") != 1 or not isinstance(payload.get("config"), dict):
            raise ValueError("Нужен конфиг bumpgroups версии 1.")
        payload["config"]["enabled"] = False
        save_config(payload["config"])
    except (ValueError, TypeError, OSError) as exc:
        return await message.answer("Не удалось импортировать конфиг: " + str(exc)[:200])
    await state.clear()
    await show_main(message, "Группы импортированы. Проверьте настройки и включите работу модуля.")


@router.callback_query(F.data == "bg:logs")
async def logs(callback: types.CallbackQuery):
    if not await auth_callback(callback):
        return
    from .module_logging import get_log_bytes
    await callback.message.answer_document(BufferedInputFile(get_log_bytes(), filename="bumpgroups.log"))
