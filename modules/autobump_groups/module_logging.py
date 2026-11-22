import logging
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .meta import NAME

PATH = Path(__file__).resolve().parent / "module_data" / "bumpgroups.log"


class KaliningradFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        timestamp = datetime.fromtimestamp(record.created, timezone(timedelta(hours=2)))
        return timestamp.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def configure_logging():
    logger = logging.getLogger(NAME)
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, "bumpgroups", False) for handler in logger.handlers):
        PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.bumpgroups = True
        handler.setFormatter(KaliningradFormatter("%(asctime)s (Калининград) | %(levelname)s | %(message)s"))
        logger.addHandler(handler)


def get_log_bytes():
    configure_logging()
    for handler in logging.getLogger(NAME).handlers:
        handler.flush()
    return PATH.read_bytes()
