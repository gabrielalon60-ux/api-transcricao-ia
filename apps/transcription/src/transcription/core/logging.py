import logging
import re
import sys
from transcription.core.config import get_settings


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
    sanitized = value.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    sanitized = re.sub(
        r"(?i)(authorization|token|secret|api[_-]?key|password|dsn)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*",
        "Bearer [REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"\b(?:postgres(?:ql)?|mysql|mariadb)://[^\s]+",
        "[REDACTED_DSN]",
        sanitized,
        flags=re.IGNORECASE,
    )
    return re.sub(r"(?<!\d)\d{10,15}(?!\d)", "[REDACTED_PHONE]", sanitized)
