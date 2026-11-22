"""Bootstrap flow without real apt, GitHub, root access or systemd mutations."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from core.tests.test_installer import BASH


ROOT = Path(__file__).resolve().parents[2]


def shell_path(path):
    value = Path(path).resolve().as_posix()
    if os.name == "nt":
        return "/" + value[0].lower() + value[2:]
    return value


class SetupInputTests(unittest.TestCase):
    def test_setup_python_keeps_stdin_available_for_credentials(self):
        # Execute the Python command actually embedded in the setup controller.
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        command = script.split('"$PYTHON" -c \'\n', 1)[1].split("\n'\n", 1)[0]
        with TemporaryDirectory(dir=Path(__file__).parent) as temporary:
            folder = Path(temporary).resolve()
            assert folder.is_relative_to(Path(__file__).resolve().parent)
            (folder / "colorama").mkdir()
            (folder / "colorama/__init__.py").write_text("def init(): pass\n", encoding="utf-8")
            (folder / "colorama/ansitowin32.py").write_text("class AnsiToWin32: pass\n", encoding="utf-8")
            (folder / "utils.py").write_text(
                "def configure_config():\n    print('saved=' + input('credential: '))\n", encoding="utf-8")
            result = subprocess.run([sys.executable, "-c", command], input="test-token\n",
                                    text=True, capture_output=True, cwd=folder, check=True, timeout=10)
        self.assertIn("saved=test-token", result.stdout)


@unittest.skipUnless(BASH, "Bash не установлен")
class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(dir=Path(__file__).parent)
        self.folder = Path(self.temporary.name).resolve()
        assert self.folder.is_relative_to(Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.source = self.folder / "fixture"
        self.source.mkdir()
        for name in ["bot.py", "deploy.py", "requirements.txt", "__init__.py", "utils.py", "settings.py", "data.py"]:
            (self.source / name).write_text("# fixture\n", encoding="utf-8")
        (self.source / "install.sh").write_text(
            '#!/usr/bin/env bash\nprintf "install %s\\n" "$*" >> "$BOOTSTRAP_TEST_LOG"\n', encoding="utf-8")
        for name in ["core", "playerokapi", "plbot", "tgbot"]:
            (self.source / name).mkdir()
        self.controller = self.folder / "pluniversal"
        self.controller.write_text(
            '#!/usr/bin/env bash\nprintf "controller %s\\n" "$*" >> "$BOOTSTRAP_TEST_LOG"\n', encoding="utf-8")
        self.controller.chmod(0o755)
        self.log = self.folder / "commands.log"
        self.target = self.folder / "installed bot"

    def execute(self, existing="", fail_clone=False):
        code = r'''
source "$1"
BOOTSTRAP_CONTROLLER="$2"
export TMPDIR="$3" BOOTSTRAP_TEST_LOG="$4" BOOTSTRAP_TEST_SOURCE="$5"
export BOOTSTRAP_TEST_EXISTING="$6" BOOTSTRAP_TEST_FAIL_CLONE="$7"
bootstrap_require_root() { :; }
bootstrap_dependencies() { :; }
bootstrap_lock() { :; }
git() {
  if [[ "$1" == check-ref-format ]]; then return 0; fi
  if [[ "$BOOTSTRAP_TEST_FAIL_CLONE" == true ]]; then return 128; fi
  cp -R -- "$BOOTSTRAP_TEST_SOURCE" "${@: -1}"
}
systemctl() {
  if [[ "$1" == show ]]; then printf '%s' "$BOOTSTRAP_TEST_EXISTING";
  else printf 'service %s\n' "$*" >> "$BOOTSTRAP_TEST_LOG"; fi
}
bootstrap_main --dir "$8" --no-setup
'''
        return subprocess.run([BASH, "-c", code, "bootstrap-test", shell_path(ROOT / "bootstrap.sh"),
                               shell_path(self.controller), shell_path(self.folder), shell_path(self.log),
                               shell_path(self.source), existing, "true" if fail_clone else "false", shell_path(self.target)],
                              text=True, encoding="utf-8", capture_output=True, timeout=30)

    def test_download_install_and_update_setup_order(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / "bot.py").exists())
        lines = self.log.read_text().splitlines()
        self.assertEqual(lines[0], "install --no-start")
        self.assertIn("controller update-setup", lines[1])
        self.assertIn("--no-enable", lines[1])
        self.assertEqual(lines[2], "controller update")
        self.assertEqual(lines[3], "service enable --now playerokuniversal-update.timer")
        self.assertIn("sudo pluniversal setup", result.stdout)
        self.assertEqual(list(self.folder.glob("plun-bootstrap.*")), [])

    def test_existing_directory_is_preserved(self):
        self.target.mkdir()
        original = self.target / "credentials.json"
        original.write_text("preserve")
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(original.read_text(), "preserve")
        self.assertFalse(self.log.exists())

    def test_existing_service_is_preserved(self):
        result = self.execute(existing="/opt/old-bot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/opt/old-bot", result.stderr)
        self.assertFalse(self.target.exists())

    def test_missing_source_and_download_failure_leave_target_untouched(self):
        (self.source / "bot.py").unlink()
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bot.py", result.stderr)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.folder.glob("plun-bootstrap.*")), [])
        result = self.execute(fail_clone=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())

    def test_script_runs_when_piped_on_stdin(self):
        result = subprocess.run([BASH, "-s", "--", "--help"], input=(ROOT / "bootstrap.sh").read_text(encoding="utf-8"),
                                text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Установка из GitHub", result.stdout)


if __name__ == "__main__":
    unittest.main()
