"""Offline checks of the real message filter and exclusion handlers."""
import ast
import __future__
from copy import deepcopy
from html import escape
import math
from pathlib import Path
import textwrap
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

from settings import ITEM_MESSAGE_NAMES, MESSAGES, restore_config


ROOT = Path(__file__).resolve().parents[2]


def extract(path, names, namespace, class_name=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name).body if class_name else tree.body
    nodes = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec",
                 flags=__future__.annotations.compiler_flag), namespace)


class Callback:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def pack(self):
        return str(self.__dict__)


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self.messages = {
            "new_deal": dict(enabled=True, text=["Hello"], excluded=[{"items": [dict(id="a", slug="product-a", name="Same name")]}]),
            "deal_confirmed": dict(enabled=True, text=["Thanks"])
        }
        self.settings = NS(get=lambda name, *args: deepcopy(self.messages),
                           set=lambda name, value: setattr(self, "messages", deepcopy(value)))
        self.ns = dict(sett=self.settings, DATA=[], msg_account=lambda _: None, escape_html=escape,
                       ITEM_MESSAGE_NAMES=ITEM_MESSAGE_NAMES)
        extract("utils.py", {"normalize_binding", "binding_items", "binding_phrases", "item_matches_binding",
                            "item_matches_any_binding", "collect_binding_ids", "split_new_items", "_rebind_in_obj"}, self.ns)
        extract("plbot/playerokbot.py", {"msg"}, self.ns, class_name="PlayerokBot")
        self.bot = NS(account=NS(profile=None), _format_msg_text=lambda text, **kwargs: text)

    def test_excluded_id_and_slug_are_suppressed(self):
        for item in [NS(id="a"), NS(id="other", slug="product-a")]:
            self.assertIsNone(self.ns["msg"](self.bot, "new_deal", item=item))
        self.assertIsNone(self.ns["msg"](self.bot, "new_deal", deal=NS(item=NS(id="a"))))

    def test_same_name_different_product_and_other_messages_are_sent(self):
        self.assertEqual(self.ns["msg"](self.bot, "new_deal", item=NS(id="b", name="Same name")), "Hello")
        self.assertEqual(self.ns["msg"](self.bot, "deal_confirmed", item=NS(id="a")), "Thanks")
        self.assertEqual(self.ns["msg"](self.bot, "new_deal"), "Hello")
        self.messages["new_deal"]["enabled"] = False
        self.assertIsNone(self.ns["msg"](self.bot, "new_deal", item=NS(id="b")))

    def test_old_settings_gain_empty_exclusions_without_losing_text(self):
        restored = restore_config({"new_deal": dict(enabled=True, text=["Custom"])}, MESSAGES.default)
        self.assertEqual(restored["new_deal"], dict(enabled=True, text=["Custom"], excluded=[]))

    def test_recreated_product_keeps_its_exclusion(self):
        self.assertEqual(self.ns["_rebind_in_obj"](self.messages, "a", dict(id="new-id", slug="new-slug")), 1)
        self.assertIsNone(self.ns["msg"](self.bot, "new_deal", item=NS(id="new-id")))

    def test_menu_links_pagination_and_return_to_message(self):
        self.ns.update(math=math, textwrap=textwrap, quote=quote,
                       InlineKeyboardButton=NS, InlineKeyboardMarkup=NS,
                       calls=NS(MessageExcludedPagination=Callback, AddMessageExcluded=Callback,
                                DeleteMessageExcluded=Callback, MessagePage=Callback, MessagesPagination=Callback))
        extract("utils.py", {"binding_title", "binding_single_url", "item_url"}, self.ns)
        extract("tgbot/templates/mess_page.py", {"mess_page_kb"}, self.ns)
        extract("tgbot/templates/mess_excluded.py", {"mess_excluded_kb"}, self.ns)
        main = self.ns["mess_page_kb"]("new_deal").inline_keyboard
        self.assertIn("Исключения товаров: 1", main[-2][0].text)
        self.messages["new_deal"]["excluded"] *= 11
        page = self.ns["mess_excluded_kb"]("new_deal", 1).inline_keyboard
        self.assertEqual(page[0][0].url, "https://playerok.com/products/product-a")
        self.assertEqual(page[1][1].text, "📃 2/2")
        self.assertIn("new_deal", page[-1][0].callback_data)
        self.messages["new_deal"]["excluded"] = []
        empty = self.ns["mess_excluded_kb"]("new_deal", 1).inline_keyboard
        self.assertEqual(len(empty), 2)
        self.assertIn("Добавить", empty[0][0].text)

    def test_link_addition_duplicates_and_deletion(self):
        # All awaits use immediate mocks; no sockets or event loop are needed.
        coroutine = self.check_link_addition_duplicates_and_deletion()
        try:
            coroutine.send(None)
        except StopIteration:
            return
        finally:
            coroutine.close()
        self.fail("Offline handler unexpectedly awaited external I/O")

    async def check_link_addition_duplicates_and_deletion(self):
        state_data = dict(message_id="new_deal", message_excluded_page=0)
        state = NS(get_data=AsyncMock(side_effect=lambda: state_data), set_state=AsyncMock(),
                   update_data=AsyncMock(side_effect=lambda **kw: state_data.update(kw)))
        resolved = dict(id="b", slug="product-b", name="Product B")
        resolver = AsyncMock(return_value=([resolved], []))
        output = AsyncMock()
        reporter = Mock(return_value="Added")
        self.ns.update(
            resolve_item_lines=resolver, extract_lines=AsyncMock(return_value=["https://playerok.com/products/product-b"]),
            item_refs_report=reporter, throw_float_message=output, notify=AsyncMock(),
            templ=NS(mess_page_float_text=str, back_kb=lambda value: value,
                     mess_excluded_text=lambda mid: mid, mess_excluded_kb=lambda *args: None),
            calls=NS(MessageExcludedPagination=Callback),
        )
        extract("tgbot/handlers/states_messages.py", {
            "require_item_message", "handler_waiting_for_excluded_message_items",
            "render_message_excluded", "callback_delete_message_excluded"
        }, self.ns)
        message = NS(text="https://playerok.com/products/product-b")
        await self.ns["handler_waiting_for_excluded_message_items"](message, state)
        self.assertEqual(len(self.messages["new_deal"]["excluded"]), 2)
        self.assertNotIn("excluded", self.messages["deal_confirmed"])
        self.assertIsNone(self.ns["msg"](self.bot, "new_deal", item=NS(id="b")))
        reporter.side_effect = ValueError("Already added")
        await self.ns["handler_waiting_for_excluded_message_items"](message, state)
        self.assertEqual(len(self.messages["new_deal"]["excluded"]), 2)
        self.assertEqual(reporter.call_args.args[0], [])
        await self.ns["callback_delete_message_excluded"](
            NS(message=message), NS(message_id="new_deal", index=1, page=0), state)
        self.assertEqual(self.ns["msg"](self.bot, "new_deal", item=NS(id="b")), "Hello")


if __name__ == "__main__":
    unittest.main()
