"""Deployment integration checks using real temporary files and a fake service/Git."""
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.state = self.root / ".deploy"
        self.state.mkdir()
        self.config = dict(repo="git@github.com:owner/repo.git", branch="main", key="/root/.ssh/test")
        deploy.write_json(self.state / "config.json", self.config)
        self.write("bot.py", "old = True\n")
        self.write("requirements.txt", "old==1\n")
        self.write("obsolete.py", "old = True\n")
        self.write("venv/marker", "old environment")
        self.write("bot_settings/config.json", "private credentials")
        self.write("modules/shop/module_data/orders.json", "orders")
        self.write("local_module.py", "keep = True\n")
        deploy.write_json(self.state / "current.json", dict(commit="old", files=["bot.py", "obsolete.py"]))
        self.commit = "a" * 40
        self.source = {"bot.py": "new = True\n", "deploy.py": "# updater\n", "install.sh": "#!/bin/bash\n",
                       "requirements.txt": "new==2\n", "new.py": "value = 42\n"}
        self.commands = Mock(return_value="")
        self.fetch = Mock(side_effect=self.fetch_source)
        self.service_active = Mock(return_value=True)
        self.check_health = Mock()
        self.prepare = Mock(return_value=False)
        for name, replacement in [
            ("deployment_lock", lambda _: nullcontext()), ("run", self.commands),
            ("fetch_source", self.fetch), ("active", self.service_active),
            ("healthy", self.check_health), ("prepare_environment", self.prepare),
        ]:
            patcher = patch.object(deploy, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def fetch_source(self, config, state, stage):
        for name, text in self.source.items():
            (stage / name).write_text(text, encoding="utf-8")
        return self.commit, sorted(self.source)

    def assert_data_preserved(self):
        self.assertEqual((self.root / "bot_settings/config.json").read_text(), "private credentials")
        self.assertEqual((self.root / "modules/shop/module_data/orders.json").read_text(), "orders")
        self.assertTrue((self.root / "local_module.py").exists())

    def test_update_and_same_commit_does_not_restart(self):
        deploy.update(self.root, automatic=True)
        self.assertEqual((self.root / "bot.py").read_text(), "new = True\n")
        self.assertFalse((self.root / "obsolete.py").exists())
        self.assert_data_preserved()
        self.assertEqual(deploy.read_json(self.state / "current.json")["commit"], self.commit)
        self.assertFalse((self.state / "transaction.json").exists())
        self.commands.assert_any_call("systemctl", "stop", deploy.SERVICE)
        self.commands.assert_any_call("systemctl", "start", deploy.SERVICE)
        self.commands.reset_mock()
        deploy.update(self.root, automatic=True)
        self.commands.assert_not_called()

    def test_failed_start_restores_code_and_skips_failed_commit(self):
        self.check_health.side_effect = ValueError("failed start")
        with self.assertRaisesRegex(ValueError, "failed start"):
            deploy.update(self.root, automatic=True)
        self.assertEqual((self.root / "bot.py").read_text(), "old = True\n")
        self.assertFalse((self.root / "new.py").exists())
        self.assertTrue((self.root / "obsolete.py").exists())
        self.assertEqual(deploy.read_json(self.state / "current.json")["commit"], "old")
        self.assertEqual(deploy.read_json(self.state / "failed.json")["commit"], self.commit)
        self.assert_data_preserved()
        self.commands.reset_mock()
        deploy.update(self.root, automatic=True)
        self.commands.assert_not_called()
        self.check_health.side_effect = None
        deploy.update(self.root)  # An explicit update can retry the failed commit.
        self.assertEqual((self.root / "bot.py").read_text(), "new = True\n")

    def test_stopped_bot_is_not_updated_or_started(self):
        self.service_active.return_value = False
        deploy.update(self.root, automatic=True)
        self.fetch.assert_not_called()
        self.commands.assert_not_called()
        deploy.update(self.root)  # Manual update preserves the stopped state.
        self.assertFalse(any(call.args[:2] == ("systemctl", "start") for call in self.commands.call_args_list))

    def test_manual_stop_during_download_is_respected(self):
        self.service_active.side_effect = [True, False]
        deploy.update(self.root, automatic=True)
        self.commands.assert_not_called()
        self.assertEqual((self.root / "bot.py").read_text(), "old = True\n")

    def test_dependency_preparation_failure_does_not_stop_bot(self):
        self.prepare.side_effect = ValueError("pip failure")
        with self.assertRaisesRegex(ValueError, "pip failure"):
            deploy.update(self.root)
        self.commands.assert_not_called()
        self.assertFalse((self.state / "transaction.json").exists())

    def test_environment_swap_and_rollback(self):
        def prepare(root, stage, candidate):
            candidate.mkdir()
            (candidate / "marker").write_text("new environment")
            return True
        self.prepare.side_effect = prepare
        self.check_health.side_effect = ValueError("failed start")
        with self.assertRaises(ValueError):
            deploy.update(self.root)
        self.assertEqual((self.root / "venv/marker").read_text(), "old environment")
        self.assertTrue(list((self.state / "backups").glob("*/failed-venv/marker")))

    def test_interrupted_update_is_recovered(self):
        previous = deploy.read_json(self.state / "current.json")
        transaction = deploy.backup_code(self.root, self.state, ["bot.py", "new.py"], previous, self.commit, True)
        self.write("bot.py", "broken = True\n")
        self.write("new.py", "broken = True\n")
        deploy.update(self.root, automatic=True)
        self.assertEqual((self.root / "bot.py").read_text(), "old = True\n")
        self.assertFalse((self.root / "new.py").exists())
        self.assertFalse((self.state / "transaction.json").exists())
        self.assertTrue(Path(transaction["backup"]).exists())

    def test_invalid_target_is_rejected_before_service_stop(self):
        self.source["local_module.py/nested.py"] = "pass\n"
        # Supply just a filename here to test destination validation, not staging.
        self.fetch.side_effect = lambda *args: (self.commit, ["local_module.py/nested.py"])
        with self.assertRaises((ValueError, OSError)):
            deploy.update(self.root)
        self.commands.assert_not_called()

    def test_interrupted_environment_swap_is_recovered(self):
        previous = deploy.read_json(self.state / "current.json")
        transaction = deploy.backup_code(self.root, self.state, ["bot.py"], previous, self.commit, True)
        os.replace(self.root / "venv", Path(transaction["backup"]) / "venv")
        self.write("bot.py", "broken = True\n")
        deploy.update(self.root, automatic=True)
        self.assertEqual((self.root / "venv/marker").read_text(), "old environment")
        self.assertEqual((self.root / "bot.py").read_text(), "old = True\n")


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_relative_to(Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)

    def archive(self, files, link=None):
        path = self.root / "source.tar"
        with tarfile.open(path, "w") as output:
            for name, content in files.items():
                data = content.encode()
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                output.addfile(entry, io.BytesIO(data))
            if link:
                entry = tarfile.TarInfo(link)
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/etc/passwd"
                output.addfile(entry)
        stage = self.root / "code"
        stage.mkdir(exist_ok=True)
        return path, stage

    def test_runtime_data_and_secrets_are_not_extracted(self):
        source = {name: "# source\n" for name in ["bot.py", "deploy.py", "install.sh", "requirements.txt"]}
        source.update({name: "private" for name in ["bot_settings/config.json", "bot_data/orders.json",
                      "modules/a/module_settings/config.json", "modules/a/module_data/orders.json",
                      ".deploy/config.json", "venv/package", ".env", "logs/latest.log", "artifacts/update.zip",
                      "temp/configs/42/export/full.zip"]})
        archive, stage = self.archive(source)
        self.assertEqual(set(deploy.unpack(archive, stage)), {"bot.py", "deploy.py", "install.sh", "requirements.txt"})
        self.assertFalse((stage / "bot_settings").exists())

    def test_traversal_symlinks_and_invalid_python_are_rejected(self):
        for source, link in [({"../escape.py": "pass"}, None), ({}, "linked.py"), ({"bot.py": "if :"}, None)]:
            with self.subTest(source=source, link=link):
                files = {name: "# source\n" for name in ["bot.py", "deploy.py", "install.sh", "requirements.txt"]}
                files.update(source)
                archive, stage = self.archive(files, link)
                with self.assertRaises((ValueError, SyntaxError)):
                    deploy.unpack(archive, stage)

    def test_git_fetch_uses_explicit_branch_and_strict_ssh(self):
        config = dict(repo="git@github.com:owner/repo.git", branch="release", key="/root/key with spaces")
        state = self.root / ".deploy"
        state.mkdir()
        (state / "repository.git").mkdir()
        with patch.object(deploy, "run", side_effect=["", "a" * 40, ""]) as command, patch.object(deploy, "unpack", return_value=["bot.py"]):
            result = deploy.fetch_source(config, state, self.root / "code")
        self.assertEqual(result[0], "a" * 40)
        self.assertEqual(command.call_args_list[0].args[-1], "refs/heads/release")
        environment = command.call_args_list[0].kwargs["env"]
        self.assertIn("StrictHostKeyChecking=yes", environment["GIT_SSH_COMMAND"])
        self.assertIn("'/root/key with spaces'", environment["GIT_SSH_COMMAND"])

    def test_process_restart_is_detected(self):
        with patch.object(deploy, "run", side_effect=["ActiveState=active\nMainPID=10\nNRestarts=0", "ActiveState=active\nMainPID=11\nNRestarts=1"]), patch.object(deploy.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "перезапускается"):
                deploy.healthy(seconds=2)

    def test_generated_timer_and_service(self):
        deploy.write_units(self.root)
        self.assertIn("OnUnitInactiveSec=5min", (self.root / deploy.TIMER).read_text())
        self.assertIn("pluniversal update --automatic", (self.root / (deploy.SERVICE + "-update.service")).read_text())

    def test_public_repository_needs_no_key(self):
        with patch.object(deploy, "run", return_value="ref") as command:
            config = deploy.validate_source("https://github.com/owner/repo.git", "main", None)
        self.assertIsNone(config["key"])
        environment = command.call_args.kwargs["env"]
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("-i /root/.ssh/playerok_deploy", environment.get("GIT_SSH_COMMAND", ""))

    def test_setup_can_prepare_timer_without_starting_it(self):
        config = dict(repo="https://github.com/owner/repo.git", branch="main", key=None)
        with patch.object(deploy, "validate_source", return_value=config), \
             patch.object(deploy, "deployment_lock", return_value=nullcontext()), \
             patch.object(deploy, "write_units"), patch.object(deploy, "run") as command:
            deploy.setup_repository(self.root, config["repo"], config["branch"], enable=False)
        command.assert_called_once_with("systemctl", "daemon-reload")
        self.assertEqual(deploy.read_json(self.root / ".deploy/config.json"), config)

    def test_non_github_addresses_are_rejected(self):
        for repo in ["https://user:token@github.com/owner/repo.git", "https://example.org/repo", "--upload-pack=evil"]:
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                deploy.validate_source(repo, "main", None)

    def test_dependencies_are_staged_and_console_scripts_relocated(self):
        stage = self.root / "stage"
        stage.mkdir()
        (stage / "requirements.txt").write_text("new==2\n")
        (self.root / "requirements.txt").write_text("old==1\n")
        scripts = self.root / "venv/bin"
        scripts.mkdir(parents=True)
        (scripts / "python").write_bytes(b"binary\0interpreter")
        candidate = self.root / "candidate"
        def pip(*args, **kwargs):
            (candidate / "bin/tool").write_bytes(b"#!" + os.fsencode(candidate / "bin/python") + b"\nprint('tool')\n")
        with patch.object(deploy, "run", side_effect=pip) as command:
            self.assertTrue(deploy.prepare_environment(self.root, stage, candidate))
        self.assertIn(os.fsencode(self.root / "venv/bin/python"), (candidate / "bin/tool").read_bytes())
        self.assertFalse((scripts / "tool").exists())
        self.assertEqual((scripts / "python").read_bytes(), b"binary\0interpreter")
        self.assertEqual(command.call_args.kwargs["cwd"], stage)


if __name__ == "__main__":
    unittest.main()
