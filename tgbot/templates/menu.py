import textwrap
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from __init__ import VERSION

from .. import callback_datas as calls


def menu_text():
    txt = textwrap.dedent(f"""
        <b>🏠 Playerok Universal</b> v{VERSION}
        Ваш бот-помощник для Playerok
    """)
    return txt


def menu_kb():
    rows = [
        [InlineKeyboardButton(text="━━━  НАСТРОЙКИ  ━━━", callback_data="null_answer")],
        #[InlineKeyboardButton(text="🎯 Ивенты УБРАТЬ ПОТОМ", callback_data=calls.MenuNavigation(to="events").pack())],
        [
        InlineKeyboardButton(text="💬 Сообщения", callback_data=calls.MessagesPagination(page=0).pack()),
        InlineKeyboardButton(text="❗ Команды", callback_data=calls.CustomCommandsPagination(page=0).pack()),
        ],
        [
        InlineKeyboardButton(text="🚀 Авто-выдача", callback_data=calls.AutoDeliveriesPagination(page=0).pack()),
        InlineKeyboardButton(text="⬆️ Авто-поднятие", callback_data=calls.MenuNavigation(to="bump").pack()),
        ],
        [
        InlineKeyboardButton(text="♻️ Восстановление", callback_data=calls.MenuNavigation(to="restore").pack()),
        InlineKeyboardButton(text="☑️ Подтверждение", callback_data=calls.MenuNavigation(to="complete").pack()),
        ],
        [
        InlineKeyboardButton(text="⚡ Быстрые ответы", callback_data=calls.FastRepliesPagination(page=0).pack()),
        ],
        [
        InlineKeyboardButton(text="🔔 Уведомления", callback_data=calls.MenuNavigation(to="notifications").pack()),
        ],
        [InlineKeyboardButton(text="━━━  УПРАВЛЕНИЕ  ━━━", callback_data="null_answer")],
        [InlineKeyboardButton(text="⬆️ Группы поднятия", callback_data="bg:menu")],
        [
        InlineKeyboardButton(text="👤 Профиль", callback_data=calls.MenuNavigation(to="profile").pack()),
        InlineKeyboardButton(text="📊 Статистика", callback_data=calls.StatsNavigation(to="day").pack()),
        ],
        [
        InlineKeyboardButton(text="💬 Чаты", callback_data=calls.ChatsPagination(page=0).pack()),
        InlineKeyboardButton(text="📋 Сделки", callback_data=calls.DealsPagination(page=0).pack()),
        ],
        [
        InlineKeyboardButton(text="🛍️ Товары", callback_data=calls.ItemsPagination(page=0).pack()),
        ],
        [InlineKeyboardButton(text="🌟 Отзывы", callback_data=calls.ReviewsPagination(page=0).pack())],
        [InlineKeyboardButton(text="━━━  СИСТЕМА  ━━━", callback_data="null_answer")],
        [
        InlineKeyboardButton(text="📊 Отчёты", callback_data="reports:menu"),
        InlineKeyboardButton(text="🗒️ Логи", callback_data=calls.MenuNavigation(to="logs").pack()),
        ],
        [
        InlineKeyboardButton(text="🧩 Модули", callback_data="mm:list:0"),
        InlineKeyboardButton(text="🔑 Авторизации", callback_data=calls.SignedUsersPagination(page=0).pack()),
        ],
        [InlineKeyboardButton(text="🗂 Конфиги", callback_data=calls.MenuNavigation(to="configs").pack())],
    ]
    
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return kb