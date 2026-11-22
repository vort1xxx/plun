"""Linux/systemd deployment from a public or private GitHub repository (stdlib only)."""
import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time


SERVICE = "playerokuniversal"
TIMER = SERVICE + "-update.timer"
ROOT = Path(__file__).resolve().parent
PROTECTED = {
    ".git", ".deploy", ".ssh", ".codex", ".agents", ".aws", ".venv", "venv",
    "bot_settings", "bot_data", "module_settings", "module_data", "logs",
    "__pycache__", "artifacts", "modules_off", "modules_clients",
}


def protected(name):
    parts = PurePosixPath(name).parts
    return any(part in PROTECTED or part == ".env" or part.startswith(".env.")
               or part.endswith((".pem", ".key", ".pyc", ".pyo", ".log")) for part in parts)


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, text=True,
                          encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=kwargs.pop("timeout", 300), **kwargs).stdout.strip()


@contextmanager
def deployment_lock(state):
    import fcntl
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "update.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Обновление уже выполняется") from None
        yield


def ssh_environment(key):
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_SSH_COMMAND"] = shlex.join([
        "ssh", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=20",
    ])
    return environment


def git_environment(config):
    if config.get("key"):
        return ssh_environment(config["key"])
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def validate_source(repo, branch, key):
    https = re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repo)
    ssh = re.fullmatch(r"git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repo)
    if not (https or ssh):
        raise ValueError("Нужна ссылка https://github.com/OWNER/REPO.git или git@github.com:OWNER/REPO.git")
    run("git", "check-ref-format", "--branch", branch)
    config = dict(repo=repo, branch=branch, key=None)
    if ssh:
        key = Path(key or "/root/.ssh/playerok_deploy").expanduser().resolve()
        if not key.is_file():
            raise ValueError(f"SSH-ключ не найден: {key}")
        config["key"] = str(key)
    run("git", "ls-remote", "--exit-code", repo, f"refs/heads/{branch}", env=git_environment(config))
    return config


def write_units(folder):
    (folder / f"{SERVICE}-update.service").write_text(
        "[Unit]\nDescription=Playerok update from GitHub\n"
        "Wants=network-online.target\nAfter=network-online.target\n\n"
        "[Service]\nType=oneshot\n"
        "ExecStart=/usr/local/bin/pluniversal update --automatic\nTimeoutStartSec=30min\n",
        encoding="utf-8")
    (folder / TIMER).write_text(
        "[Unit]\nDescription=Check Playerok updates every five minutes\n\n"
        "[Timer]\nOnBootSec=2min\nOnUnitInactiveSec=5min\nAccuracySec=30s\n\n"
        "[Install]\nWantedBy=timers.target\n", encoding="utf-8")


def active():
    return run("systemctl", "show", SERVICE, "--property=ActiveState", "--value") == "active"


def healthy(seconds=30):
    """Check that the service retains the same live process, without restart loops."""
    process = None
    for _ in range(seconds):
        state = dict(line.split("=", 1) for line in run(
            "systemctl", "show", SERVICE, "--property=ActiveState,MainPID,NRestarts"
        ).splitlines() if "=" in line)
        current_pid = state.get("MainPID", "0")
        if state.get("ActiveState") != "active" or current_pid == "0":
            raise ValueError("После обновления сервис остановился")
        current_process = (current_pid, state.get("NRestarts", "0"))
        if process is not None and process != current_process:
            raise ValueError("После обновления сервис перезапускается")
        process = current_process
        time.sleep(1)


def target_path(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts:
        raise ValueError(f"Недопустимый путь в обновлении: {name}")
    target = root
    for index, part in enumerate(path.parts):
        target = target / part
        if target.is_symlink():
            raise ValueError(f"Обновление не заменяет символические ссылки: {name}")
        if index < len(path.parts) - 1 and target.exists() and not target.is_dir():
            raise ValueError(f"Родительский путь не является каталогом: {name}")
    if target.exists() and not target.is_file():
        raise ValueError(f"Вместо файла обнаружен каталог: {name}")
    return target


def unpack(archive, stage):
    files = []
    with tarfile.open(archive) as bundle:
        for entry in bundle:
            name = entry.name
            # Validate paths even for entries that would otherwise be ignored.
            target = target_path(stage, name.rstrip("/")) if not entry.isdir() else None
            parts = PurePosixPath(name).parts
            if PurePosixPath(name).is_absolute() or ".." in parts or "\\" in name:
                raise ValueError(f"Недопустимый путь: {name}")
            if protected(name) or entry.isdir():
                continue
            if not entry.isfile():
                raise ValueError(f"Поддерживаются только обычные файлы: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(entry) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            target.chmod(0o755 if entry.mode & 0o111 else 0o644)
            files.append(name)
    required = {"bot.py", "requirements.txt", "deploy.py", "install.sh"}
    if not required.issubset(files):
        raise ValueError("В корне репозитория должны быть bot.py, requirements.txt, deploy.py и install.sh")
    for name in files:
        if name.endswith(".py"):
            ast.parse((stage / name).read_bytes(), filename=name)
    return sorted(files)


def fetch_source(config, state, stage):
    cache = state / "repository.git"
    environment = git_environment(config)
    if not cache.exists():
        run("git", "init", "--bare", cache)
    run("git", "-C", cache, "fetch", "--depth=1", "--no-tags", config["repo"],
        f"refs/heads/{config['branch']}", env=environment)
    commit = run("git", "-C", cache, "rev-parse", "FETCH_HEAD")
    archive = stage.parent / "source.tar"
    run("git", "-C", cache, "archive", "--format=tar", f"--output={archive}", commit)
    return commit, unpack(archive, stage)


def replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".update-", dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_environment(root, stage, candidate):
    previous = root / "requirements.txt"
    if previous.exists() and previous.read_bytes() == (stage / "requirements.txt").read_bytes():
        return False
    environment = root / "venv"
    if environment.is_symlink():
        raise ValueError("Для обновления зависимостей venv должен быть обычным каталогом")
    # Keep installed module dependencies; the running environment is untouched.
    shutil.copytree(environment, candidate, symlinks=True)
    run(candidate / "bin/python", "-m", "pip", "install", "-r", stage / "requirements.txt", cwd=stage, timeout=1200)
    # Newly installed console scripts must survive moving the staged environment.
    for script in (candidate / "bin").iterdir():
        if script.is_symlink() or not script.is_file():
            continue
        content = script.read_bytes()
        if b"\0" not in content:
            script.write_bytes(content.replace(os.fsencode(candidate), os.fsencode(environment)))
    return True


def backup_code(root, state, files, previous, commit, was_active):
    backup = state / "backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + commit[:12])
    backup.mkdir(parents=True)
    missing = []
    for name in files:
        target = target_path(root, name)
        if target.exists():
            destination = backup / "code" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
        else:
            missing.append(name)
    transaction = dict(backup=str(backup), files=files, missing=missing,
                       previous=previous, commit=commit, was_active=was_active)
    write_json(state / "transaction.json", transaction)
    return transaction


def rollback(root, state, transaction):
    run("systemctl", "stop", SERVICE)
    backup = Path(transaction["backup"])
    for name in transaction["files"]:
        target = target_path(root, name)
        if name in transaction["missing"]:
            target.unlink(missing_ok=True)
        else:
            replace_file(backup / "code" / name, target)
    if (backup / "venv").exists():
        environment = root / "venv"
        if environment.exists():
            os.replace(environment, backup / "failed-venv")
        os.replace(backup / "venv", environment)
    write_json(state / "current.json", transaction["previous"])
    write_json(state / "failed.json", dict(commit=transaction["commit"], backup=str(backup)))
    # Leave the journal if restart fails, so recovery is retried on the next run.
    if transaction["was_active"]:
        run("systemctl", "start", SERVICE)
    (state / "transaction.json").unlink()
    print(f"Предыдущая версия кода восстановлена. Резервная копия: {backup}", flush=True)


def update(root, automatic=False):
    state = root / ".deploy"
    with deployment_lock(state):
        interrupted = read_json(state / "transaction.json")
        if interrupted:
            print("Восстанавливаю прерванное обновление…", flush=True)
            rollback(root, state, interrupted)
        config = read_json(state / "config.json")
        if not config:
            raise ValueError("Сначала настройте репозиторий: pluniversal update-setup --repo https://github.com/OWNER/REPO.git")
        was_active = active()
        if automatic and not was_active:
            print("Бот остановлен; автоматическое обновление пропущено.")
            return
        previous = read_json(state / "current.json", {})
        with tempfile.TemporaryDirectory(prefix="stage-", dir=state) as temporary:
            stage = Path(temporary) / "code"
            stage.mkdir()
            commit, files = fetch_source(config, state, stage)
            if previous.get("commit") == commit:
                print(f"Уже установлена версия {commit[:12]}.")
                return
            if automatic and read_json(state / "failed.json", {}).get("commit") == commit:
                print(f"Версия {commit[:12]} ранее завершилась ошибкой; ожидаю следующий commit.")
                return
            managed = sorted(set(files) | {name for name in previous.get("files", []) if not protected(name)})
            for name in managed:
                target_path(root, name)
            candidate = Path(temporary) / "venv"
            has_environment = prepare_environment(root, stage, candidate)
            # Recheck after download: respect a manual stop while preparing the update.
            was_active = active()
            if automatic and not was_active:
                print("Бот остановлен во время подготовки; обновление пропущено.")
                return
            transaction = backup_code(root, state, managed, previous, commit, was_active)
            try:
                run("systemctl", "stop", SERVICE)
                for name in files:
                    replace_file(stage / name, target_path(root, name))
                for name in set(managed) - set(files):
                    target_path(root, name).unlink(missing_ok=True)
                if has_environment:
                    os.replace(root / "venv", Path(transaction["backup"]) / "venv")
                    os.replace(candidate, root / "venv")
                if was_active:
                    run("systemctl", "start", SERVICE)
                    healthy()
                write_json(state / "current.json", dict(commit=commit, files=files, repo=config["repo"], branch=config["branch"]))
                (state / "transaction.json").unlink()
            except BaseException:
                rollback(root, state, transaction)
                raise
            print(f"Обновление установлено: {commit[:12]}. Резервная копия: {transaction['backup']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup")
    setup.add_argument("--repo", required=True)
    setup.add_argument("--branch", default="main")
    setup.add_argument("--key", default=None)
    updater = commands.add_parser("update")
    updater.add_argument("--automatic", action="store_true")
    commands.add_parser("status")
    args = parser.parse_args()
    try:
        if args.command == "status":
            state = ROOT / ".deploy"
            config = read_json(state / "config.json", {})
            current = read_json(state / "current.json", {})
            print(f"Репозиторий: {config.get('repo', 'не настроен')}\nВетка: {config.get('branch', '-')}\nВерсия: {current.get('commit', 'ещё не установлена')}")
            return 0
        if os.name != "posix" or os.geteuid() != 0:
            raise ValueError("Обновление предназначено для Linux/systemd. Запустите команду через sudo.")
        if args.command == "setup":
            config = validate_source(args.repo, args.branch, args.key)
            with deployment_lock(ROOT / ".deploy"):
                write_json(ROOT / ".deploy/config.json", config)
                write_units(Path("/etc/systemd/system"))
            run("systemctl", "daemon-reload")
            run("systemctl", "enable", "--now", TIMER)
            print("Автообновление включено: проверка каждые 5 минут. Первая проверка: sudo pluniversal update")
        else:
            update(ROOT, args.automatic)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", None) or str(error)
        print(f"Ошибка обновления: {detail}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
