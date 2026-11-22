import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from .logic import DEFAULT_CONFIG, validate_config

ROOT = Path(__file__).resolve().parents[2]
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


def read(path, default, legacy=None, validate=None):
    with LOCK:
        source = path if path.exists() else legacy if legacy and legacy.is_file() else None
        if source:
            with source.open(encoding="utf-8-sig") as stream:
                value = json.load(stream)
        else:
            value = deepcopy(default)
        if validate:
            value = validate(value)
        if source != path:
            write(path, value)
        return value


def get_config():
    return read(ROOT / 'bot_settings/autobump_groups.json', DEFAULT_CONFIG,
                ROOT / 'modules/autobump_groups/module_settings/config.json', validate_config)


def save_config(value):
    write(ROOT / 'bot_settings/autobump_groups.json', validate_config(value))


def get_state():
    def validate(state):
        if not isinstance(state, dict) or not isinstance(state.get('items'), dict):
            raise ValueError('Повреждена история поднятий.')
        return state
    return read(ROOT / 'bot_data/autobump_groups/state.json', {'items': {}},
                ROOT / 'modules/autobump_groups/module_data/state.json', validate)


def save_state(state):
    write(ROOT / 'bot_data/autobump_groups/state.json', state)
