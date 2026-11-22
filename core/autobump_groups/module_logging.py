import logging
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .meta import NAME

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / 'bot_data/autobump_groups/bumpgroups.log'


class KaliningradFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        timestamp = datetime.fromtimestamp(record.created, timezone(timedelta(hours=2)))
        return timestamp.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def configure_logging():
    logger = logging.getLogger(NAME)
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, "bumpgroups", False) for handler in logger.handlers):
        PATH.parent.mkdir(parents=True, exist_ok=True)
        legacy = ROOT / 'modules/autobump_groups/module_data/bumpgroups.log'
        if not PATH.exists() and legacy.is_file():
            PATH.write_bytes(legacy.read_bytes())
        handler = RotatingFileHandler(PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.bumpgroups = True
        handler.setFormatter(KaliningradFormatter("%(asctime)s (Калининград) | %(levelname)s | %(message)s"))
        logger.addHandler(handler)


def get_log_bytes():
    configure_logging()
    for handler in logging.getLogger(NAME).handlers:
        handler.flush()
    return PATH.read_bytes()
