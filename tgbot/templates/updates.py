from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .. import callback_datas as calls


def updates_text():
    return '<b>Обновления</b>\n\nАвтообновление отключено. Обновляйте файлы бота вручную на сервере.'


def updates_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='🏠 Главное меню', callback_data=calls.MenuNavigation(to='default').pack())
    ]])


def updates_float_text(placeholder):
    from html import escape
    return updates_text() + '\n\n' + escape(str(placeholder))
