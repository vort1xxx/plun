import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

ROOT = Path(__file__).resolve().parents[2]
LOCK = RLock()


def write(path, value):
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def read(relative, default, legacy=None, validate=None):
    with LOCK:
        path = ROOT / relative
        source = path if path.exists() else ROOT / legacy if legacy and (ROOT / legacy).is_file() else None
        if source:
            with source.open(encoding='utf-8-sig') as stream:
                value = json.load(stream)
            if not isinstance(value, dict):
                raise ValueError('Некорректный формат данных отчётов: ' + str(source))
        else:
            value = json.loads(json.dumps(default))
        if validate:
            validate(value)
        if source != path:
            write(path, value)
        return value
