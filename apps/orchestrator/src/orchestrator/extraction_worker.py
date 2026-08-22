from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import ProcessingItem, User
from orchestrator.config import get_settings
from orchestrator.services.extraction_dispatcher import (
    ExtractionDispatcher,
    apply_extraction_failure,
    claim_expired_extracting_item_for_recovery,
    claim_next_received_item_for_extraction,
)
from orchestrator.transcription_client import TranscriptionClient
from orchestrator.wuzapi import WuzapiClient

logger = logging.getLogger(__name__)

# Module-local poll interval constant (consistent with fifo_worker)
EXTRACTION_POLL_INTERVAL_SECONDS = 1.0


class ExtractionWorker:
    """Worker daemon executing the extraction phase: RECEIVED -> EXTRACTING -> READY."""

    def __init__(
        self,
        worker_id: str,
        session_factory: sessionmaker[Session],
        dispatcher: ExtractionDispatcher,
        wuzapi_client: Optional[WuzapiClient] = None,
        poll_interval: float = EXTRACTION_POLL_INTERVAL_SECONDS,
    ) -> None:
        self.worker_id = worker_id if worker_id.startswith("worker-") else f"worker-{worker_id}"
        self.session_factory = session_factory
        self.dispatcher = dispatcher
        self.wuzapi_client = wuzapi_client or WuzapiClient()
        self.poll_interval = poll_interval
        self.running = True

    def stop(self) -> None:
        """Signals the worker loop to stop gracefully after current item completes."""
        logger.info(f"Extraction worker {self.worker_id} stop requested.")
        self.running = False

    def _resolve_user_phone(self, user_id: str) -> str:
        """Resolves phone number for user in a short-lived scoped session that closes immediately."""
        with self.session_factory() as db:
            user = db.query(User).filter_by(id=user_id).first()
            return user.phone_number if user and user.phone_number else ""

    async def _download_media_bytes(self, phone: str, item: ProcessingItem) -> Optional[bytes]:
        """Downloads media from WUZAPI outside of any open database transaction."""
        if not item.media_ref or not isinstance(item.media_ref, dict):
            return None

        direct_path = item.media_ref.get("direct_path")
        if not direct_path:
            return None

        external_message_id = item.media_ref.get("external_message_id") or ""

        try:
            return await self.wuzapi_client.download_media(
                phone=phone,
                external_message_id=external_message_id,
                direct_path=direct_path,
            )
        except Exception as exc:
            logger.warning(f"WUZAPI media download failed for item {item.id}: {exc}")
            return None

    async def process_claimed_item(self, item: ProcessingItem) -> Optional[ProcessingItem]:
        """Processes one claimed item through ExtractionDispatcher with strict transaction boundaries."""
        start_ts = time.monotonic()
        has_direct_path = bool(
            item.media_ref and isinstance(item.media_ref, dict) and item.media_ref.get("direct_path")
        )

        file_bytes: Optional[bytes] = None
        if has_direct_path:
            # 1. Resolve phone in a short-lived session that closes before network I/O
            phone = self._resolve_user_phone(item.user_id)
            # 2. Download media outside of any database transaction
            file_bytes = await self._download_media_bytes(phone, item)

        # 3. Open scoped session to apply extraction result or fail-closed failure
        with self.session_factory() as db:
            if has_direct_path and not file_bytes:
                logger.error(
                    f"Failing extraction for item {item.id} due to missing media bytes from WUZAPI"
                )
                result = apply_extraction_failure(
                    db,
                    processing_item_id=item.id,
                    dispatched_claim_token=item.extraction_claim_token,
                    error_code="EXTRACTION_ERROR",
                    retryable=True,
                )
            else:
                result = await self.dispatcher.process_item(
                    db,
                    item,
                    mock_file_bytes=file_bytes,
                )
            elapsed_ms = int((time.monotonic() - start_ts) * 1000)
            final_status = result.status if result else "UNKNOWN"
            logger.info(
                f"Extraction worker {self.worker_id} processed item {item.id} -> {final_status} ({elapsed_ms}ms)"
            )
            return result

    async def run_iteration(self) -> bool:
        """Executes one tick of the extraction worker loop.

        Returns True if an item was claimed and processed, False if queue was idle.
        """
        # 1. Attempt normal claim of fresh RECEIVED item
        with self.session_factory() as db:
            item = claim_next_received_item_for_extraction(db, dispatcher_id=self.worker_id)

        if item is not None:
            logger.info(
                f"Extraction worker {self.worker_id} claimed RECEIVED item {item.id} (sequence={item.sequence})"
            )
            await self.process_claimed_item(item)
            return True

        # 2. If no fresh item, evaluate stale lease recovery
        with self.session_factory() as db:
            recovered_item = claim_expired_extracting_item_for_recovery(db, dispatcher_id=self.worker_id)

        if recovered_item is not None:
            logger.info(
                f"Extraction worker {self.worker_id} claimed expired EXTRACTING item {recovered_item.id} for recovery (attempt={recovered_item.attempt_count})"
            )
            await self.process_claimed_item(recovered_item)
            return True

        return False

    async def run_loop(self) -> None:
        """Main daemon loop running serially (concurrency=1) until stopped."""
        logger.info(f"Extraction worker {self.worker_id} loop started (poll_interval={self.poll_interval}s).")
        while self.running:
            try:
                processed = await self.run_iteration()
                if not processed and self.running:
                    await asyncio.sleep(self.poll_interval)
            except Exception as exc:
                logger.error(f"Error in extraction worker {self.worker_id} loop: {exc}", exc_info=True)
                if self.running:
                    await asyncio.sleep(self.poll_interval)
        logger.info(f"Extraction worker {self.worker_id} loop shut down cleanly.")


def run_extraction_worker_loop(
    worker_id: str = "1",
    poll_interval: float = EXTRACTION_POLL_INTERVAL_SECONDS,
) -> None:
    """Entrypoint initializing runtime settings, database pool, and running the async worker loop."""
    settings = get_settings()
    settings.validate_environment()

    engine = create_engine(settings.database_url)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    transcription_client = TranscriptionClient()
    dispatcher = ExtractionDispatcher(transcription_client=transcription_client)
    wuzapi_client = WuzapiClient()

    worker = ExtractionWorker(
        worker_id=worker_id,
        session_factory=session_factory,
        dispatcher=dispatcher,
        wuzapi_client=wuzapi_client,
        poll_interval=poll_interval,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal(sig: int, frame: object) -> None:
        logger.info(f"Signal {sig} received. Stopping extraction worker...")
        worker.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        loop.run_until_complete(worker.run_loop())
    finally:
        loop.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    worker_id_arg = sys.argv[1] if len(sys.argv) > 1 else "1"
    run_extraction_worker_loop(worker_id=worker_id_arg)
