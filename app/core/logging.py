import logging
import sys
from app.core.config import get_settings


def setup_logging() -> None:
    settings = get_settings()

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Suppress noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def sanitize_log_value(value: str | None) -> str:
    """
    Strips newlines and carriage returns from user-controlled strings before logging.
    Prevents log injection attacks where attackers forge log lines via filenames
    or other user-supplied values.
    """
    if not value:
        return ""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

