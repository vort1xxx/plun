from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from settings import Settings
from tgbot import callback_datas as calls

router = Router()


def authorized(user):
    config = Settings.get('config') or {}
    return user and str(user.id) in {str(uid) for uid in config.get('telegram', {}).get('bot', {}).get('signed_users', [])}


async def show(message):
    await message.answer('📊 <b>Отчёты</b>\n\nВыберите отчёт или откройте сохранение и выгрузку истории сделок.', parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='💳 Продажи за период', callback_data='reports:profit'),
             InlineKeyboardButton(text='📦 История сделок', callback_data='reports:deals')],
            [InlineKeyboardButton(text='📊 Отчёт за вчера', callback_data='reports:yesterday'),
             InlineKeyboardButton(text='💾 Данные истории', callback_data='reports:data')],
            [InlineKeyboardButton(text='🕒 Настройки ежедневного отчёта', callback_data='reports:settings')],
            [InlineKeyboardButton(text='🏠 Главное меню', callback_data=calls.MenuNavigation(to='default').pack())]
        ]))


@router.message(Command('reports'))
async def reports_command(message, state):
    if not authorized(message.from_user):
        from tgbot.helpful import do_auth
        return await do_auth(message, state)
    await state.clear()
    await show(message)


@router.callback_query(F.data.startswith('reports:'))
async def reports_callback(callback, state):
    if not authorized(callback.from_user):
        return await callback.answer('Сначала авторизуйтесь.', show_alert=True)
    await callback.answer()
    await state.clear()
    from .profit.tgbot.handlers import profit_command, settings_command
    from .deals.tgbot.handlers import data_menu, deals_command, daily_command
    action = callback.data.split(':', 1)[1]
    if action == 'menu':
        return await show(callback.message)
    handlers = {'profit': profit_command, 'settings': settings_command,
                'data': data_menu, 'deals': deals_command, 'yesterday': daily_command}
    handler = handlers.get(action)
    if handler:
        message = callback.message.model_copy(update={'from_user': callback.from_user, 'text': '', 'entities': None})
        await handler(message, state)
