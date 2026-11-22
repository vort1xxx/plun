"""Full export checks without Telegram or access to live bot credentials."""
import ast
import asyncio
import __future__
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace as NS
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from settings import SettingsFile


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("configs_export_offline", ROOT / "core/configs.py")
configs = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"rarfile": ModuleType("rarfile")}):
    spec.loader.exec_module(configs)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        for name, value in [("PROJECT_DIR", str(self.root)), ("TEMP_DIR", str(self.root / "temp")),
                            ("CONFIGS_TEMP_DIR", str(self.root / "temp/configs"))]:
            replacement = patch.object(configs, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def test_complete_export_preserves_module_code_data_and_settings(self):
        content = {
            "modules/shop/__init__.py": "# shop module\n",
            "modules/shop/module_settings/config.json": '{"shop": true}',
            "modules/shop/module_data/database.db": b"\x00\x01binary database",
            "modules_off/disabled/logic.py": "# disabled module\n",
            "modules_clients/client/logic.py": "# client module\n",
            "bot_settings/config.json": '{"main": true}',
            "bot_settings/custom-settings.txt": "custom settings",
            "bot_settings/backups/previous.zip": b"previous backup",
            "bot_data/reports/daily_reports.json": '{"report": true}',
            "bot_data/custom/nested.bin": b"\xff\x00custom",
        }
        for name, data in content.items():
            self.write(name, data)
        self.write("modules/shop/__pycache__/logic.pyc", b"bytecode")
        self.write("bot_data/.instance.lock", b"running lock")
        self.write("logs/latest.log", "root log")
        self.write(".deploy/config.json", "deployment settings")
        (self.root / "modules/shop/empty").mkdir()
        with zipfile.ZipFile(configs.export_complete(42)) as archive:
            for name, data in content.items():
                self.assertEqual(archive.read(name), data.encode() if isinstance(data, str) else data)
            self.assertIn("modules/shop/empty/", archive.namelist())
            self.assertFalse(any("__pycache__" in name or ".instance.lock" in name for name in archive.namelist()))
            self.assertNotIn("logs/latest.log", archive.namelist())
            self.assertNotIn(".deploy/config.json", archive.namelist())
            self.assertIsNone(archive.testzip())

    def test_missing_optional_directories_and_empty_data_directory(self):
        (self.root / "bot_data").mkdir()
        with zipfile.ZipFile(configs.export_complete(42)) as archive:
            self.assertEqual(archive.namelist(), ["bot_data/"])

    def test_no_source_directories_is_reported(self):
        with self.assertRaisesRegex(Exception, "Не найдены папки"):
            configs.export_complete(42)

    def test_links_are_not_followed(self):
        linked_file = self.write("modules/shop/linked.txt", "external file")
        self.write("modules/shop/regular.py", "pass\n")
        linked_directory = self.root / "modules/shop/external"
        self.write("modules/shop/external/secret.txt", "external secret")
        original = os.path.islink
        with patch.object(os.path, "islink", side_effect=lambda path: Path(path) in {linked_file, linked_directory} or original(path)):
            with zipfile.ZipFile(configs.export_complete(42)) as archive:
                self.assertIn("modules/shop/regular.py", archive.namelist())
                self.assertNotIn("modules/shop/linked.txt", archive.namelist())
                self.assertFalse(any("external/" in name for name in archive.namelist()))

    def test_full_archive_import_does_not_replace_main_config_with_module_config(self):
        self.write("modules/shop/module_settings/config.json", '{"module": true}')
        self.write("modules/shop/module_settings/messages.json", '{"module_message": true}')
        self.write("bot_settings/config.json", '{"main": true}')
        self.write("bot_settings/messages.json", '{"main_message": true}')
        archive = configs.export_complete(42)
        registered = [SettingsFile("config", "bot_settings/config.json", True, {}),
                      SettingsFile("messages", "bot_settings/messages.json", True, {})]
        with patch.object(configs, "_all_files", return_value=registered), \
             patch.object(configs, "_prepare_json", side_effect=lambda file, raw, keep: json.loads(raw)), \
             patch.object(configs, "make_backup", return_value="backup.zip"), \
             patch.object(configs, "_write_file") as write:
            self.assertEqual(set(configs.peek_import(archive)), {"config", "messages"})
            applied, skipped, _ = configs.import_file(archive)
        self.assertEqual(applied, ["config.json", "messages.json"])
        self.assertEqual([call.args[1] for call in write.call_args_list], [{"main": True}, {"main_message": True}])
        self.assertEqual(len(skipped), 2)

    def test_legacy_config_export_stays_separate(self):
        file = SettingsFile("config", "bot_settings/config.json", True, {})
        with patch.object(configs, "_all_files", return_value=[file]), \
             patch.object(configs, "_read_file", return_value={"legacy": True}):
            with zipfile.ZipFile(configs.export_all(42)) as archive:
                self.assertEqual(archive.namelist(), ["bot_settings/config.json"])
                self.assertEqual(json.loads(archive.read(file.path)), {"legacy": True})


class ExportUiTests(unittest.TestCase):
    def extract(self, path, names, namespace):
        nodes = [node for node in ast.parse((ROOT / path).read_text(encoding="utf-8")).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec",
                     flags=__future__.annotations.compiler_flag), namespace)

    def immediate(self, coroutine):
        try:
            coroutine.send(None)
        except StopIteration:
            return
        finally:
            coroutine.close()
        self.fail("Offline handler unexpectedly awaited external I/O")

    def test_button_and_handler(self):
        namespace = dict(InlineKeyboardButton=NS, InlineKeyboardMarkup=NS,
                         calls=NS(MenuNavigation=lambda **kw: NS(pack=lambda: "menu")))
        self.extract("tgbot/templates/configs.py", {"configs_kb"}, namespace)
        buttons = [button for row in namespace["configs_kb"]().inline_keyboard for button in row]
        self.assertTrue(any(button.callback_data == "export_complete_configs" for button in buttons))
        run = AsyncMock()
        namespace.update(run_export=run, export_complete=configs.export_complete)
        self.extract("tgbot/callback_handlers/actions_configs.py", {"callback_export_complete_configs"}, namespace)
        self.immediate(namespace["callback_export_complete_configs"]("callback", "state"))
        run.assert_awaited_once_with("callback", "state", configs.export_complete)

    def test_export_worker_sends_then_cleans_up(self):
        callback = NS(from_user=NS(id=42))
        state = NS(set_state=AsyncMock())
        worker = AsyncMock(return_value="export.zip")
        send = AsyncMock()
        cleanup = Mock()
        namespace = dict(asyncio=NS(Lock=asyncio.Lock, to_thread=worker), _export_locks={},
                         send_export=send, clear_export_dir=cleanup, notify=AsyncMock())
        self.extract("tgbot/callback_handlers/actions_configs.py", {"run_export"}, namespace)
        self.immediate(namespace["run_export"](callback, state, configs.export_complete))
        worker.assert_awaited_once_with(configs.export_complete, 42)
        send.assert_awaited_once_with(callback, state, "export.zip")
        cleanup.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
