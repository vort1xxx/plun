from aiogram import types, Router, F
from aiogram.fsm.context import FSMContext

from settings import Settings as sett, ITEM_MESSAGE_NAMES
from utils import escape_html, split_new_items

from .. import templates as templ
from .. import states
from .. import callback_datas as calls
from ..helpful import throw_float_message, extract_lines, resolve_item_lines, item_refs_report, notify


router = Router()


def require_item_message(message_id):
    messages = sett.get("messages") or {}
    if message_id not in ITEM_MESSAGE_NAMES or message_id not in messages:
        raise ValueError("❌ Меню устарело — откройте настройки сообщения заново")
    return messages


async def render_message_excluded(callback, state, message_id, page=0):
    await state.set_state(None)
    await state.update_data(message_id=message_id, message_excluded_page=page)
    await throw_float_message(
        state=state, message=callback.message, callback=callback,
        text=templ.mess_excluded_text(message_id),
        reply_markup=templ.mess_excluded_kb(message_id, page)
    )


@router.callback_query(calls.MessageExcludedPagination.filter())
async def callback_message_excluded(callback: types.CallbackQuery, callback_data: calls.MessageExcludedPagination, state: FSMContext):
    try:
        require_item_message(callback_data.message_id)
        await render_message_excluded(callback, state, callback_data.message_id, callback_data.page)
    except Exception as e:
        await notify(callback, str(e), alert=True)


@router.callback_query(calls.AddMessageExcluded.filter())
async def callback_add_message_excluded(callback: types.CallbackQuery, callback_data: calls.AddMessageExcluded, state: FSMContext):
    try:
        require_item_message(callback_data.message_id)
        await state.update_data(message_id=callback_data.message_id, message_excluded_page=callback_data.page)
        await state.set_state(states.MessagesStates.waiting_for_excluded_items)
        await throw_float_message(
            state=state, message=callback.message, callback=callback,
            text=templ.mess_page_float_text(
                "🔗 Отправьте <b>ссылку на товар</b>, для которого не нужно отправлять это сообщение.\n\n"
                "Можно добавить несколько ссылок — каждую с новой строки, или отправить .txt файл."
            ),
            reply_markup=templ.back_kb(calls.MessageExcludedPagination(
                message_id=callback_data.message_id, page=callback_data.page
            ).pack())
        )
    except Exception as e:
        await notify(callback, str(e), alert=True)


@router.callback_query(calls.DeleteMessageExcluded.filter())
async def callback_delete_message_excluded(callback: types.CallbackQuery, callback_data: calls.DeleteMessageExcluded, state: FSMContext):
    try:
        messages = require_item_message(callback_data.message_id)
        excluded = messages[callback_data.message_id].get("excluded", [])
        if 0 <= callback_data.index < len(excluded):
            excluded.pop(callback_data.index)
            sett.set("messages", messages)
            await notify(callback, "🗑️ Товар убран из исключений")
        await render_message_excluded(callback, state, callback_data.message_id, callback_data.page)
    except Exception as e:
        await notify(callback, str(e), alert=True)


@router.message(states.MessagesStates.waiting_for_excluded_items, F.text | F.document)
async def handler_waiting_for_excluded_message_items(message: types.Message, state: FSMContext):
    data = await state.get_data()
    message_id = data.get("message_id")
    page = data.get("message_excluded_page", 0)
    if not message_id:
        await state.set_state(None)
        return await throw_float_message(
            state=state, message=message,
            text=templ.mess_float_text("❌ Меню устарело — откройте настройки сообщения заново"),
            reply_markup=templ.back_kb(calls.MessagesPagination(page=0).pack())
        )
    try:
        require_item_message(message_id)
        items, errors = await resolve_item_lines(await extract_lines(message))
        # Получаем актуальные настройки после запросов к Playerok.
        messages = require_item_message(message_id)
        excluded = messages[message_id].setdefault("excluded", [])
        items, dupes = split_new_items(items, excluded)
        text = item_refs_report(
            items, errors + dupes,
            "✅ Товар <b>{name}</b> добавлен в исключения для этого сообщения",
            "✅ Добавлено <b>{count}</b> товаров в исключения для этого сообщения"
        )
        excluded.extend({"items": [item]} for item in items)
        sett.set("messages", messages)
        await state.set_state(None)
    except Exception as e:
        text = str(e)
    await throw_float_message(
        state=state, message=message, text=templ.mess_page_float_text(text),
        reply_markup=templ.back_kb(calls.MessageExcludedPagination(message_id=message_id, page=page).pack())
    )


@router.message(states.MessagesStates.waiting_for_message_text, F.text)
async def handler_waiting_for_message_text(message: types.Message, state: FSMContext):
    try:
        await state.set_state(None)
        
        data = await state.get_data()
        message_id = data.get("message_id")
        
        if len(message.text) <= 0:
            raise Exception("❌ Слишком короткий текст")

        messages = sett.get("messages")
        message_split_lines = message.text.split('\n')
        messages[message_id]["text"] = message_split_lines
        sett.set("messages", messages)
        
        await throw_float_message(
            state=state,
            message=message,
            text=templ.mess_page_float_text(
                f"✅ <b>Текст сообщения</b> <code>{message_id}</code> "
                f"был успешно изменён на <blockquote>{escape_html(message.text)}</blockquote>"
            ),
            reply_markup=templ.back_kb(calls.MessagePage(message_id=message_id).pack())
        )
    except Exception as e:
        await throw_float_message(
            state=state,
            message=message,
            text=templ.mess_page_float_text(e), 
            reply_markup=templ.back_kb(calls.MessagePage(message_id=message_id).pack())
        )
