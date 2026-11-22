import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .meta import NAME

LOG_PATH = Path(__file__).resolve().parent / "module_data" / "twofa.log"


def configure_logging():
    logger = logging.getLogger(NAME)
    logger.setLevel(logging.INFO)
    if not any(getattr(handler, "twofa_handler", False) for handler in logger.handlers):
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.twofa_handler = True
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)


def get_log_bytes():
    configure_logging()
    for handler in logging.getLogger(NAME).handlers:
        handler.flush()
    return LOG_PATH.read_bytes()
