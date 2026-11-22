import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from .logic import DEFAULT_CONFIG, validate_config

ROOT = Path(__file__).resolve().parent
LOCK = RLock()


def write(path, value):
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def read(path, default):
    with LOCK:
        if not path.exists():
            write(path, default)
            return deepcopy(default)
        with path.open(encoding="utf-8-sig") as stream:
            return json.load(stream)


def get_config():
    return validate_config(read(ROOT / "module_settings" / "config.json", DEFAULT_CONFIG))


def save_config(value):
    write(ROOT / "module_settings" / "config.json", validate_config(value))


def get_state():
    state = read(ROOT / "module_data" / "state.json", {"items": {}})
    if not isinstance(state, dict) or not isinstance(state.get("items"), dict):
        raise ValueError("Повреждена история поднятий.")
    return state


def save_state(state):
    write(ROOT / "module_data" / "state.json", state)
