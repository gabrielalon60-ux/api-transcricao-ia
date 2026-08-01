import logging
import json
from datetime import datetime
import contextvars

correlation_id_var = contextvars.ContextVar("correlation_id", default=None)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "name": record.name,
        }
        corr_id = correlation_id_var.get()
        if corr_id:
            log_record["correlation_id"] = corr_id
        return json.dumps(log_record)

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    # Clear existing handlers to prevent duplicate logs in FastAPI
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
