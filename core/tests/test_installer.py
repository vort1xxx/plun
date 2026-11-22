"""Проверки установщика без apt, systemd и запуска настоящего бота."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

PROJECT = Path(__file__).resolve().parents[2]
BASH = os.environ.get('TEST_BASH') or shutil.which('bash')
if os.name == 'nt' and not os.environ.get('TEST_BASH'):
    # The Windows bash.exe launcher is not a usable shell without a WSL distro,
    # and these Windows-path tests require Git Bash rather than a WSL shell.
    candidate = Path('C:/Program Files/Git/bin/bash.exe')
    BASH = str(candidate) if candidate.exists() else None


@unittest.skipUnless(BASH, 'Bash не установлен')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "bot space's $name %n"
        self.folder.mkdir()
        self.service = Path(self.temp.name) / 'playerokuniversal.service'
        self.controller = Path(self.temp.name) / 'pluniversal'
        self.run_bash(
            'source "$1"; BOT_DIR="$2"; VENV_PYTHON="$3"; SERVICE_FILE="$4"; COMMAND_FILE="$5"; '
            'write_launcher; write_service; write_controller',
            PROJECT / 'install.sh', self.folder, Path(sys.executable), self.service, self.controller)

    def run_bash(self, code, *args, check=True):
        return subprocess.run([BASH, '-c', code, 'installer-test',
                               *(str(value).replace('\\', '/') for value in args)],
                              cwd=PROJECT, check=check, text=True, encoding='utf-8',
                              capture_output=True, timeout=30)

    def test_generated_service_and_shell_files(self):
        content = self.service.read_text(encoding='utf-8')
        folder = self.folder.as_posix().replace('%', '%%')
        self.assertIn('WorkingDirectory=' + folder + '/\n', content)
        self.assertIn('ExecStart=/bin/bash "' + folder.replace('$', '$$') + '/run.sh"', content)
        self.assertIn('WantedBy=multi-user.target', content)
        self.assertNotIn('/root/playerokuniversal', content)
        for path in [PROJECT / 'install.sh', self.folder / 'run.sh', self.controller]:
            subprocess.run([BASH, '-n', str(path)], check=True, capture_output=True, timeout=30)

    def test_launcher_changes_directory_and_preserves_arguments(self):
        interpreter = self.folder / 'venv/bin/python'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$PWD" "$@"\n', encoding='utf-8')
        interpreter.chmod(0o755)
        result = self.run_bash('bash "$1" --example "two words"', self.folder / 'run.sh')
        lines = result.stdout.splitlines()
        self.assertTrue(lines[0].endswith(self.folder.name))
        self.assertEqual(lines[1], '-u')
        self.assertTrue(lines[2].endswith(self.folder.name + '/bot.py'))
        self.assertEqual(lines[3:], ['--example', 'two words'])

    def test_controller_checks_local_config_and_uses_local_folder(self):
        interpreter = self.folder / 'venv/bin/python'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n', encoding='utf-8')
        interpreter.chmod(0o755)
        (self.folder / 'deploy.py').write_text('import sys\nprint("deploy stub:", " ".join(sys.argv[1:]))\n', encoding='utf-8')
        self.assertIn('deploy stub: update --automatic', self.run_bash('bash "$1" update --automatic', self.controller).stdout)
        self.assertIn('deploy stub: setup --repo git@github.com:owner/repo.git',
                      self.run_bash('bash "$1" update-setup --repo git@github.com:owner/repo.git', self.controller).stdout)
        result = self.run_bash('bash "$1" start', self.controller, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('pluniversal setup', result.stderr)
        config = self.folder / 'bot_settings/config.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'playerok': {'api': {'cookies': 'test'}},
                                     'telegram': {'api': {'token': 'test'}, 'bot': {'password': 'test'}}}), encoding='utf-8')
        result = self.run_bash('systemctl() { printf "stub:%s\\n" "$*"; }; export -f systemctl; bash "$1" start', self.controller)
        self.assertIn('stub:start playerokuniversal', result.stdout)


if __name__ == '__main__':
    unittest.main()
