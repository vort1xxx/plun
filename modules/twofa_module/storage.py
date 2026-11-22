import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from copy import deepcopy

from .logic import DEFAULT_CONFIG, validate_config

ROOT = Path(__file__).resolve().parent
LOCK = RLock()


def read_json(path, default):
    with LOCK:
        if not path.exists():
            write_json(path, default)
            return deepcopy(default)
        with path.open(encoding="utf-8") as source:
            return json.load(source)


def write_json(path, value):
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
                temporary = output.name
                json.dump(value, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def get_config():
    return validate_config(read_json(ROOT / "module_settings" / "config.json", DEFAULT_CONFIG))


def save_config(config):
    write_json(ROOT / "module_settings" / "config.json", validate_config(config))


def get_state():
    state = read_json(ROOT / "module_data" / "deals.json", {"deals": {}})
    if not isinstance(state, dict) or not isinstance(state.get("deals"), dict):
        raise ValueError("Некорректная история 2FA.")
    return state


def save_state(state):
    write_json(ROOT / "module_data" / "deals.json", state)
