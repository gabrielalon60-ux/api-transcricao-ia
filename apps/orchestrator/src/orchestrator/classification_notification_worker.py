"""Standalone classification-summary WhatsApp notifier."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.config import get_settings
from orchestrator.services.classification_notification_service import (
    POLL_INTERVAL_SECONDS,
    run_classification_notification_iteration,
)
from orchestrator.wuzapi import WuzapiClient


logger = logging.getLogger(__name__)
running = True


def _send(phone_number: str, message: str, outbound_message_id: str) -> bool:
    client = WuzapiClient()
    if not client.base_url or not client.token:
        return False
    try:
        asyncio.run(client.send_text_message(phone_number, message))
        return True
    except Exception:
        logger.warning(
            "Classification notification outcome is unknown for %s.",
            outbound_message_id,
        )
        return False


def run_classification_notification_loop(
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> None:
    global running
    running = True
    settings = get_settings()
    settings.validate_environment()
    engine = create_engine(settings.database_url)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def handle_signal(signum: int, frame: object) -> None:
        global running
        logger.info("Signal %s received; stopping classification notifier.", signum)
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    logger.info("Classification notification worker started.")
    while running:
        try:
            processed = run_classification_notification_iteration(
                session_factory,
                _send,
            )
            if not processed:
                time.sleep(poll_interval)
        except Exception:
            logger.exception("Classification notification iteration failed.")
            time.sleep(poll_interval)
    engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else POLL_INTERVAL_SECONDS
    run_classification_notification_loop(interval)
