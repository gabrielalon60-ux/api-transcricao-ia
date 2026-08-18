import logging
import json
from datetime import datetime
import contextvars
import re

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


class JSONFormatter(logging.Formatter):
    def format(self, record):
        message = sanitize_log_message(record.getMessage())
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": message,
            "name": record.name,
        }
        corr_id = correlation_id_var.get()
        if corr_id:
            log_record["correlation_id"] = corr_id
        return json.dumps(log_record)


def sanitize_log_message(value: str) -> str:
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


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    # Clear existing handlers to prevent duplicate logs in FastAPI
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
