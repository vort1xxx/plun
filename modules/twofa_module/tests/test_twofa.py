"""Run offline without loading the bot, its settings, or installed aiogram."""
import base64
from copy import deepcopy
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "twofa_offline"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def load(name, relative_path):
    spec = importlib.util.spec_from_file_location(PACKAGE + "." + name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


logic = load("logic", "logic.py")
storage = load("storage", "storage.py")


class Status(Enum):
    PAID = 0
    PENDING = 1
    SENT = 2
    CONFIRMED = 3
    CONFIRMED_AUTOMATICALLY = 4
    ROLLED_BACK = 5


class Direction(Enum):
    IN = 0
    OUT = 1


enums = ModuleType("playerokapi.enums")
enums.ItemDealStatuses = Status
enums.ItemDealDirections = Direction
with patch.dict(sys.modules, {"playerokapi": ModuleType("playerokapi"), "playerokapi.enums": enums}):
    plbot_package = ModuleType(PACKAGE + ".plbot")
    plbot_package.__path__ = [str(ROOT / "plbot")]
    sys.modules[plbot_package.__name__] = plbot_package
    handlers = load("plbot.handlers", "plbot/handlers.py")

SECRET = base64.b32encode(b"12345678901234567890").decode()


class CodeTests(unittest.TestCase):
    def test_telegram_menu_import_and_buttons(self):
        class Filter:
            def __getattr__(self, name):
                return self
            def __eq__(self, value):
                return self
            def startswith(self, value):
                return self

        class Router:
            def __init__(self):
                self.handlers = []

            def message(self, *filters):
                def register(function):
                    self.handlers.append(function)
                    return function
                return register

            callback_query = message

        def module(name, **values):
            result = ModuleType(name)
            result.__dict__.update(values)
            return result

        fake_types = SimpleNamespace(Message=object, CallbackQuery=object)
        telegram_package = module(PACKAGE + ".tgbot")
        telegram_package.__path__ = [str(ROOT / "tgbot")]
        overrides = {
            "aiogram": module("aiogram", F=Filter(), Router=Router, types=fake_types),
            "aiogram.filters": module("aiogram.filters", Command=lambda name: name),
            "aiogram.fsm.context": module("aiogram.fsm.context", FSMContext=object),
            "aiogram.fsm.state": module("aiogram.fsm.state", State=object, StatesGroup=object),
            "aiogram.types": module("aiogram.types", BufferedInputFile=Mock(), InlineKeyboardButton=SimpleNamespace, InlineKeyboardMarkup=SimpleNamespace),
            "settings": module("settings", Settings=Mock()),
            "tgbot.helpful": module("tgbot.helpful", do_auth=Mock()),
            "plbot.playerokbot": module("plbot.playerokbot", get_playerok_bot=Mock()),
            PACKAGE + ".tgbot": telegram_package,
        }
        with patch.dict(sys.modules, overrides):
            menu = load("tgbot.handlers", "tgbot/handlers.py")
            registered = {function.__name__ for function in menu.router.handlers}
            self.assertTrue({"menu", "add", "secret_entered", "hours_entered", "toggle", "delete_mapping", "import_file", "export_config", "logs"} <= registered)
            keyboard = menu.keyboard(logic.DEFAULT_CONFIG)
            self.assertEqual(len(keyboard.inline_keyboard), 11)
            for row in keyboard.inline_keyboard:
                self.assertLessEqual(len(row[0].callback_data.encode()), 64)

    def test_rfc6238_sha1_vectors(self):
        mapping = dict(secret=SECRET, digits=8)
        vectors = [(59, "94287082"), (1111111109, "07081804"),
                   (1111111111, "14050471"), (1234567890, "89005924"),
                   (2000000000, "69279037"), (20000000000, "65353130")]
        for timestamp, expected in vectors:
            self.assertEqual(logic.generate_code(mapping, timestamp)[0], expected)

    def test_uri_and_plain_secret(self):
        uri = "otpauth://totp/Test%3Auser?secret=" + SECRET + "&algorithm=SHA256&digits=8&period=60"
        parsed = logic.parse_secret(uri)
        self.assertEqual(parsed["label"], "Test:user")
        self.assertEqual((parsed["algorithm"], parsed["digits"], parsed["period"]), ("SHA256", 8, 60))
        self.assertEqual(logic.parse_secret(SECRET.lower())["secret"], SECRET)
        self.assertEqual(logic.generate_code(dict(secret=SECRET), 60)[1], 30)

    def test_invalid_secrets_and_templates(self):
        for raw in (None, "", "123456", "ABC!", "otpauth://hotp/test?secret=" + SECRET, "otpauth://totp/test?secret=" + SECRET + "&digits=5"):
            with self.assertRaises(ValueError):
                logic.parse_secret(raw)
        self.assertTrue(logic.valid_template("{code} {account} {seconds}"))
        for template in ("{{code}}", "{code:bad}", "{code!r}", "{code} {account.__class__}", "{code} {"):
            self.assertFalse(logic.valid_template(template))

    def test_config_duplicates_after_trimming(self):
        row = dict(item_id="item", secret=SECRET)
        with self.assertRaises(ValueError):
            logic.validate_config(dict(mappings=[row, dict(row, item_id=" item ")]))
        with self.assertRaises(ValueError):
            logic.validate_config(dict(enabled="false"))

    def test_name_matching_is_unambiguous(self):
        row = dict(item_id="item", item_name="Game account")
        self.assertIs(logic.find_mapping([row], "copy", "GAME  account"), row)
        self.assertIsNone(logic.find_mapping([row, dict(row, item_id="other")], "copy", "Game account"))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root_patch = patch.object(storage, "ROOT", Path(self.temp.name))
        root_patch.start()
        self.addCleanup(root_patch.stop)
        code_patch = patch.object(handlers, "generate_code", return_value=("123456", 30))
        code_patch.start()
        self.addCleanup(code_patch.stop)
        self.config = logic.validate_config(dict(mappings=[dict(item_id="item", item_name="Game account", label="user", secret=SECRET, access_hours=1)]))
        storage.save_config(self.config)
        self.bot = SimpleNamespace(account=Mock(id="seller"))
        self.deal = SimpleNamespace(id="deal", direction=Direction.OUT, status=Status.PAID,
                                   item=SimpleNamespace(id="item", name="Game account"), user=SimpleNamespace(id="buyer"))
        self.bot.account.get_deal.return_value = self.deal
        self.event = SimpleNamespace(deal=self.deal, chat=SimpleNamespace(id="chat"))

    def command(self, sender="buyer", chat="chat", text="!2fa"):
        event = SimpleNamespace(message=SimpleNamespace(text=text, user=SimpleNamespace(id=sender)), chat=SimpleNamespace(id=chat))
        handlers.handle_command(self.bot, event)

    def register(self):
        handlers.register_deal(self.bot, self.event)
        self.bot.account.send_message.reset_mock()

    def test_paid_buyer_can_receive_code(self):
        self.register()
        self.command()
        self.assertIn("123456", self.bot.account.send_message.call_args.kwargs["text"])
        self.assertNotIn(SECRET, (Path(self.temp.name) / "module_data" / "deals.json").read_text())
        self.bot.account.update_deal.assert_not_called()

    def test_wrong_buyer_and_chat_never_receive_code(self):
        self.register()
        self.command(sender="stranger")
        self.command(chat="elsewhere")
        self.command(sender="seller")
        self.bot.account.get_deal.assert_not_called()
        for call in self.bot.account.send_message.call_args_list:
            self.assertNotIn("123456", call.kwargs["text"])

    def test_refund_and_expiration_revoke_access(self):
        self.register()
        handlers.revoke("deal")
        self.command()
        self.bot.account.get_deal.assert_not_called()
        state = storage.get_state()
        state["deals"]["deal"].update(revoked=False, expires_at=1)
        storage.save_state(state)
        self.command()
        self.bot.account.get_deal.assert_not_called()

    def test_live_refund_blocks_code(self):
        self.register()
        self.deal.status = Status.ROLLED_BACK
        self.command()
        self.assertNotIn("123456", self.bot.account.send_message.call_args.kwargs["text"])

    def test_replacing_secret_blocks_previous_buyers(self):
        self.register()
        self.config["mappings"][0]["secret"] = base64.b32encode(b"new account secret!!").decode()
        storage.save_config(self.config)
        self.command()
        self.bot.account.get_deal.assert_not_called()

    def test_duplicate_payment_and_cooldown(self):
        self.register()
        original = deepcopy(storage.get_state())
        handlers.register_deal(self.bot, self.event)
        self.assertEqual(storage.get_state(), original)
        self.command()
        self.command()
        self.bot.account.send_message.assert_called_once()

    def test_auto_issue_and_confirmation_only_after_send(self):
        self.config.update(auto_issue=True, auto_complete=True)
        storage.save_config(self.config)
        handlers.register_deal(self.bot, self.event)
        self.bot.account.update_deal.assert_called_once_with("deal", Status.SENT)

    def test_failed_send_never_confirms(self):
        self.config.update(auto_issue=True, auto_complete=True)
        storage.save_config(self.config)
        self.bot.account.send_message.side_effect = OSError("offline")
        with self.assertRaises(OSError):
            handlers.register_deal(self.bot, self.event)
        self.assertFalse(storage.get_state()["deals"]["deal"]["issued"])
        self.bot.account.update_deal.assert_not_called()

    def test_disabled_module_creates_no_grant(self):
        self.config["enabled"] = False
        storage.save_config(self.config)
        handlers.register_deal(self.bot, self.event)
        self.assertEqual(storage.get_state()["deals"], {})

    def test_corrupt_history_not_overwritten(self):
        path = Path(self.temp.name) / "module_data" / "deals.json"
        path.parent.mkdir()
        path.write_text("broken")
        with self.assertRaises(ValueError):
            handlers.register_deal(self.bot, self.event)
        self.assertEqual(path.read_text(), "broken")


if __name__ == "__main__":
    unittest.main()
