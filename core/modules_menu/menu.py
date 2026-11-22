import logging
from html import escape
from uuid import UUID

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from core.modules import get_modules, get_module_by_uuid
from settings import Settings
from tgbot import callback_datas as calls

from .catalog import module_page, settings_command
from .meta import NAME

router = Router()
logger = logging.getLogger(NAME)


def button(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def authorized(user):
    config = Settings.get('config') or {}
    return user is not None and str(user.id) in {
        str(value) for value in config.get('telegram', {}).get('bot', {}).get('signed_users', [])
    }


async def check_callback(callback):
    if not authorized(callback.from_user):
        await callback.answer('Сначала авторизуйтесь в боте.', show_alert=True)
        return False
    await callback.answer()
    return callback.message is not None


async def show_catalog(message, page=0):
    modules = list(get_modules())
    entries, page, pages = module_page(modules, page)
    buttons = [button(f"{'🟢' if module.enabled else '⏸'} {module.meta.name[:40]}", f'mm:open:{module.uuid}') for module in entries]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    nav = []
    if page:
        nav.append(button('⬅️ Назад', f'mm:list:{page - 1}'))
    if page + 1 < pages:
        nav.append(button('Далее ➡️', f'mm:list:{page + 1}'))
    if nav:
        rows.append(nav)
    rows.append([button('🔌 Управление модулями', calls.ModulesPagination(page=0).pack()),
                 button('🏠 Главное меню', calls.MenuNavigation(to='default').pack())])
    await message.answer(f'🧩 <b>Модули</b>\n\nУстановлено: {len(modules)}. Страница {page + 1}/{pages}.\nВыберите модуль, чтобы открыть его настройки.',
                         parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command(NAME))
async def command_menu(message: types.Message, state):
    if not authorized(message.from_user):
        from tgbot.helpful import do_auth
        await state.clear()
        return await do_auth(message, state)
    await state.clear()
    await show_catalog(message)


@router.callback_query(F.data.startswith('mm:list:'))
async def list_modules(callback: types.CallbackQuery, state):
    if not await check_callback(callback):
        return
    try:
        page = int(callback.data.rsplit(':', 1)[1])
    except ValueError:
        return
    await state.clear()
    await show_catalog(callback.message, page)


async def show_fallback(message, module, reason):
    await message.answer(f"📦 <b>{escape(module.meta.name)}</b>\n\n{escape(reason)}",
                         parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [button('🔧 Управление модулем', calls.ModulePage(uuid=module.uuid).pack()),
                              button('↩️ Все модули', 'mm:list:0')]]))


@router.callback_query(F.data.startswith('mm:open:'))
async def open_module(callback: types.CallbackQuery, state):
    if not await check_callback(callback):
        return
    try:
        module = get_module_by_uuid(UUID(callback.data.rsplit(':', 1)[1]))
    except ValueError:
        module = None
    await state.clear()
    if module is None:
        return await show_catalog(callback.message)
    if not module.enabled:
        return await show_fallback(callback.message, module, 'Модуль выключен. Включите его на странице управления.')
    command = settings_command(module)
    if command is None:
        return await show_fallback(callback.message, module, 'У этого модуля нет отдельного меню настроек. Откройте страницу управления.')
    # A callback's message was sent by the bot. Supply the real clicking user
    # so the target module's own authorization check remains effective.
    event = callback.message.model_copy(update={
        'from_user': callback.from_user, 'text': '/' + command,
        'entities': [MessageEntity(type='bot_command', offset=0, length=len(command) + 1)],
        'caption': None, 'caption_entities': None,
    })
    try:
        for target_router in module.telegram_bot_routers:
            result = await target_router.propagate_event(update_type='message', event=event, state=state, bot=callback.bot)
            if result is not UNHANDLED:
                await callback.message.answer('↩️ Вернуться к списку модулей', reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[button('🧩 Все модули', 'mm:list:0')]]))
                return
        await show_fallback(callback.message, module, 'Команда настроек не обработана модулем.')
    except Exception:
        logger.exception('Ошибка открытия настроек модуля %s', module._dir_name)
        await show_fallback(callback.message, module, 'Не удалось открыть настройки. Подробности — в журнале бота.')
