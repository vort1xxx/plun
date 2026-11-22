from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .meta import NAME


LOG_PATH = Path(__file__).resolve().parent / "module_data" / "steamauto.log"
_HANDLER_MARKER = "steamauto_module_file_handler"


class _SecretFilter(logging.Filter):
    """Не допускает попадания ключей и паролей в выгружаемый журнал."""

    _patterns = (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,}\]]+"),
        re.compile(r"(?i)(api[_ -]?key\s*[\"']?\s*[:=]\s*[\"']?)[^\s,}\]\"']+"),
        re.compile(r"(?i)(password\s*[\"']?\s*[:=]\s*[\"']?)[^\s,}\]\"']+"),
        re.compile(r"(?i)(steam_guard(?:_code)?\s*[\"']?\s*[:=]\s*[\"']?)[^\s,}\]\"']+"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self._patterns:
            message = pattern.sub(r"\1[СКРЫТО]", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging() -> Path:
    """Подключает файл журнала один раз, в том числе после перезагрузки модуля."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(NAME)
    logger.setLevel(logging.DEBUG)

    for handler in logger.handlers:
        if getattr(handler, "_module_log_marker", None) == _HANDLER_MARKER:
            return LOG_PATH

    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler._module_log_marker = _HANDLER_MARKER
    handler.setLevel(logging.DEBUG)
    handler.addFilter(_SecretFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.info("Файловое журналирование включено: %s", LOG_PATH.name)
    return LOG_PATH


def get_log_bytes() -> bytes | None:
    try:
        return LOG_PATH.read_bytes()
    except OSError:
        return None
