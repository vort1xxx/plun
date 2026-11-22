"""Offline checks: extract handlers without importing the live bot or its settings."""
import ast
import asyncio
from collections import defaultdict
import copy
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
from string import Formatter
from threading import Lock
import time
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def extract(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(enabled=True, api_key="fake", delivery_template="{login} {password} {hours}", mappings=[])
        self.state = {"orders": {}, "rentals": {}}
        self.api = Mock()
        self.api.rent.return_value = {"data": {"rental_uid": "r1"}}
        self.api.credentials.return_value = {"login": "buyer", "password": "secret"}
        self.ns = dict(asyncio=asyncio, defaultdict=defaultdict, logger=logging.getLogger("test"),
                       re=re, time=time, _delivery_lock=Lock(), _cfg=lambda: self.config,
                       Data=SimpleNamespace(get=lambda _: copy.deepcopy(self.state), set=self.save),
                       KOSellAPI=lambda _: self.api, _report_error=Mock(), _notify_admins=Mock(),
                       ItemDealStatuses=SimpleNamespace(SENT="SENT"))
        extract("kosell_api.py", {"KOSellAPIError"}, self.ns)
        extract("validation.py", {"valid_template"}, self.ns | {})
        validation = {"Formatter": Formatter}
        extract("validation.py", {"valid_template"}, validation)
        self.ns["valid_template"] = validation["valid_template"]
        extract("plbot/__init__.py", {"_unwrap", "_deliver_sync", "on_new_deal", "on_new_message"}, self.ns)
        self.mapping = dict(item_id="listing", item_name="Game 1 hour", product_id="1", hours=1)
        self.bot = SimpleNamespace(account=Mock(id="seller"))

    def save(self, name, state):
        self.state = copy.deepcopy(state)

    def deliver(self):
        self.ns["_deliver_sync"](self.bot, None, self.mapping, "d1", "chat")

    def test_delivery_duplicate_does_not_charge_twice(self):
        self.deliver()
        self.deliver()
        self.api.rent.assert_called_once()
        self.bot.account.send_message.assert_called_once()
        self.assertEqual(self.state["orders"]["d1"]["playerok_status"], "sent")

    def test_retry_credentials_reuses_rental(self):
        self.api.credentials.side_effect = self.ns["KOSellAPIError"]("unavailable")
        self.deliver()
        self.api.credentials.side_effect = None
        self.deliver()
        self.api.rent.assert_called_once()
        self.assertEqual(self.state["orders"]["d1"]["status"], "delivered")

    def test_uncertain_order_never_repeated(self):
        self.state["orders"]["d1"] = {"status": "creating"}
        self.deliver()
        self.api.rent.assert_not_called()

    def test_disabled_or_bad_template_prevents_purchase(self):
        self.config["enabled"] = False
        self.deliver()
        self.config.update(enabled=True, delivery_template="{login} {password} {hours:invalid}")
        self.deliver()
        self.api.rent.assert_not_called()

    def test_storage_failure_prevents_purchase(self):
        self.ns["Data"].set = Mock(side_effect=OSError("disk full"))
        with self.assertRaises(OSError):
            self.deliver()
        self.api.rent.assert_not_called()

    def test_full_name_fallback_and_ambiguity(self):
        self.config["mappings"] = [self.mapping]
        event = SimpleNamespace(deal=SimpleNamespace(id="d1", item=SimpleNamespace(id="copy", name="Game 1 hour")), chat=SimpleNamespace(id="chat"))
        asyncio.run(self.ns["on_new_deal"](self.bot, event))
        self.api.rent.assert_called_once()
        self.api.reset_mock()
        self.config["mappings"].append(dict(self.mapping, item_id="another"))
        asyncio.run(self.ns["on_new_deal"](self.bot, event))
        self.api.rent.assert_not_called()

    def test_expired_rental_does_not_request_code(self):
        self.state["rentals"]["chat"] = [dict(login="buyer", rental_uid="r1", created_at=time.time()-7200, hours=1)]
        event = SimpleNamespace(message=SimpleNamespace(user=SimpleNamespace(id="buyer"), text="!code"), chat=SimpleNamespace(id="chat"))
        asyncio.run(self.ns["on_new_message"](self.bot, event))
        self.api.code.assert_not_called()

    def test_template_validation(self):
        valid = self.ns["valid_template"]
        self.assertTrue(valid("{login} {password} {hours} {{literal}}"))
        for template in ("{{login}} {{password}}", "{login} {password} {hours:no}", "{login} {password} {unknown}", "{login} {password} {", "{login} {password} {game.__class__}"):
            self.assertFalse(valid(template), template)

    def test_delete_token_survives_reordering(self):
        ns = dict(sha256=sha256, json=json)
        extract("tgbot/handlers.py", {"_mapping_token"}, ns)
        token = ns["_mapping_token"]
        self.assertEqual(token(self.mapping), token(dict(reversed(list(self.mapping.items())))))
        self.assertNotEqual(token(self.mapping), token(dict(self.mapping, hours=2)))
        self.assertLess(len("kosellrent:del:" + token(self.mapping)), 64)


    def test_api_error_in_http_200_is_rejected(self):
        ns = dict(logger=logging.getLogger("test"), API_BASE="https://example.invalid", TIMEOUT=1,
                  requests=SimpleNamespace(RequestException=RuntimeError))
        extract("kosell_api.py", {"KOSellAPIError", "KOSellAPI"}, ns)
        api = ns["KOSellAPI"].__new__(ns["KOSellAPI"])
        api.session = Mock()
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"success": False, "error": {"code": "no_stock"}}
        api.session.request.return_value = response
        with self.assertRaisesRegex(ns["KOSellAPIError"], "no_stock"):
            api._request("POST", "/rental/rent")
        response.json.return_value = {"success": True, "data": {"uid": "r1"}}
        self.assertEqual(api._request("POST", "/rental/rent")["data"]["uid"], "r1")

    def test_storage_does_not_reset_corrupt_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rental_orders.json"
            path.write_text('{broken', encoding="utf-8")
            writer = Mock()
            ns = dict(Path=Path, json=json, deepcopy=copy.deepcopy, set_json=writer,
                      DATA=[SimpleNamespace(name="rental_orders", path=str(path), default={"orders": {}, "rentals": {}})])
            extract("data.py", {"Data"}, ns)
            with self.assertRaises(json.JSONDecodeError):
                ns["Data"].get("rental_orders")
            writer.assert_not_called()
            self.assertEqual(path.read_text(), '{broken')


if __name__ == "__main__":
    unittest.main()
