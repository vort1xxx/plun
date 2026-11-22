import textwrap
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from settings import Settings as sett, ITEM_MESSAGE_NAMES
from utils import escape_html

from .. import callback_datas as calls


def mess_page_text(message_id: str):
    messages = sett.get("messages")
    
    enabled = "✅" if messages[message_id]["enabled"] else "❌"
    message_text = "\n".join(messages[message_id]["text"]) or "❌ Не задано"
    
    txt = textwrap.dedent(f"""
        <b>📄💬 Страница сообщения</b>
        \n<b>🆔 ID:</b> {message_id}\n<b>💡 Включено:</b> {enabled}
        \n<b>💬 Текст:</b> <blockquote>{escape_html(message_text)}</blockquote>
    """)
    if message_id in ITEM_MESSAGE_NAMES:
        txt += f"\n<b>➖ Исключения товаров:</b> {len(messages[message_id].get('excluded', []))}\n"
    return txt


def mess_page_kb(message_id: str, page: int = 0):
    messages = sett.get("messages")
    
    enabled = "✅" if messages[message_id]["enabled"] else "❌"
    message_text = "\n".join(messages[message_id]["text"]) or "❌ Не задано"
    
    rows = [
        [InlineKeyboardButton(text=f"💡 Включено: {enabled}", callback_data="switch_message_enabled")],
        [InlineKeyboardButton(text=f"💬 Текст: {message_text}", callback_data="enter_message_text")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=calls.MessagesPagination(page=page).pack())]
    ]
    if message_id in ITEM_MESSAGE_NAMES:
        rows.insert(-1, [InlineKeyboardButton(
            text=f"➖ Исключения товаров: {len(messages[message_id].get('excluded', []))}",
            callback_data=calls.MessageExcludedPagination(message_id=message_id).pack()
        )])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return kb


def mess_page_float_text(placeholder: str):
    txt = textwrap.dedent(f"""
        <b>📄💬 Страница сообщения</b>
        \n{placeholder}
    """)
    return txt
