import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from settings import Settings as sett
from utils import binding_single_url, binding_title, escape_html

from .. import callback_datas as calls


def mess_excluded_text(message_id: str):
    excluded = sett.get("messages")[message_id].get("excluded", [])
    return (
        "<b>💬➖ Исключения товаров</b>\n\n"
        f"Сообщение: <code>{escape_html(message_id)}</code>\n"
        f"Всего исключённых товаров: <b>{len(excluded)}</b>\n\n"
        "Это сообщение не будет отправляться для указанных товаров."
    )


def mess_excluded_kb(message_id: str, page: int = 0):
    excluded = sett.get("messages")[message_id].get("excluded", [])
    total_pages = max(1, math.ceil(len(excluded) / 10))
    page = max(0, min(page, total_pages - 1))
    rows = []
    for index, entry in enumerate(excluded[page * 10:(page + 1) * 10], start=page * 10):
        url = binding_single_url(entry)
        title = binding_title(entry)
        title = title[:32] + ("..." if len(title) > 32 else "")
        rows.append([
            InlineKeyboardButton(text=title, url=url) if url else
            InlineKeyboardButton(text=title, callback_data="null_answer"),
            InlineKeyboardButton(text="🗑️", callback_data=calls.DeleteMessageExcluded(
                message_id=message_id, index=index, page=page
            ).pack())
        ])
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="←", callback_data=calls.MessageExcludedPagination(
                message_id=message_id, page=page - 1
            ).pack()) if page > 0 else InlineKeyboardButton(text="🛑", callback_data="null_answer"),
            InlineKeyboardButton(text=f"📃 {page + 1}/{total_pages}", callback_data="null_answer"),
            InlineKeyboardButton(text="→", callback_data=calls.MessageExcludedPagination(
                message_id=message_id, page=page + 1
            ).pack()) if page < total_pages - 1 else InlineKeyboardButton(text="🛑", callback_data="null_answer")
        ])
    rows.append([InlineKeyboardButton(text="➕ Добавить по ссылке", callback_data=calls.AddMessageExcluded(
        message_id=message_id, page=page
    ).pack())])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=calls.MessagePage(message_id=message_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
