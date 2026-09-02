from __future__ import annotations

import os
import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from db.models import Event, ProcessingItem
from orchestrator.transcription_client import TranscriptionClient, TranscriptionClientError

logger = logging.getLogger(__name__)

# Configurable initial defaults (approved in principle)
MAX_EXTRACTION_ATTEMPTS = 3
EXTRACTION_LEASE_DURATION_SECONDS = 60
EXTRACTION_REQUEST_TIMEOUT_SECONDS = 60
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 30
EXTRACTION_BACKOFF_INITIAL_SECONDS = 2
EXTRACTION_BACKOFF_MAX_SECONDS = 30
MAX_CONCURRENT_EXTRACTIONS_PER_SERVICE = 5
SUPPORTED_DOCUMENT_TYPES = (
    "invoice",
    "pix_receipt",
    "bank_receipt",
    "commercial_document",
)


def claim_next_received_item_for_extraction(db: Session, dispatcher_id: str = "dispatcher-1") -> Optional[ProcessingItem]:
    """Atomically claims one item in RECEIVED status for extraction, generating a fresh claim token."""
    item = (
        db.query(ProcessingItem)
        .filter(ProcessingItem.status == "RECEIVED")
        .order_by(ProcessingItem.sequence.asc().nulls_last(), ProcessingItem.message_received_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not item:
        return None

    now = datetime.now(timezone.utc)
    claim_token = f"claim-{uuid.uuid4()}"
    item.status = "EXTRACTING"
    item.claimed_by = f"extraction-{dispatcher_id}"
    item.extraction_claim_token = claim_token
    item.lease_expires_at = now + timedelta(seconds=EXTRACTION_LEASE_DURATION_SECONDS)
    item.heartbeat_at = now
    item.attempt_count += 1
    db.commit()
    db.refresh(item)
    return item


def claim_expired_extracting_item_for_recovery(db: Session, dispatcher_id: str = "dispatcher-1") -> Optional[ProcessingItem]:
    """Atomically recovers an abandoned EXTRACTING item whose extraction lease has expired, generating a fresh claim token."""
    now = datetime.now(timezone.utc)
    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "EXTRACTING",
            ProcessingItem.lease_expires_at < now,
            ProcessingItem.attempt_count < MAX_EXTRACTION_ATTEMPTS,
        )
        .order_by(ProcessingItem.sequence.asc().nulls_last())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not item:
        return None

    claim_token = f"claim-rec-{uuid.uuid4()}"
    item.claimed_by = f"extraction-recovery-{dispatcher_id}"
    item.extraction_claim_token = claim_token
    item.lease_expires_at = now + timedelta(seconds=EXTRACTION_LEASE_DURATION_SECONDS)
    item.heartbeat_at = now
    item.attempt_count += 1
    db.commit()
    db.refresh(item)
    return item


def apply_extraction_success(
    db: Session,
    processing_item_id: str,
    dispatched_claim_token: Optional[str],
    extraction_payload: Dict[str, Any],
    local_buffer_path: Optional[str] = None,
) -> Optional[ProcessingItem]:
    """Atomically applies a successful Gate 3 extraction result guarded by extraction_claim_token.

    Commit-Safe Media Cleanup Ordering:
      1. Receive Gate 3 result
      2. Persist extraction fields in Platform DB transaction
      3. Commit EXTRACTED -> READY
      4. ONLY after successful commit, delete the local buffer file.
    """
    query = db.query(ProcessingItem).filter(
        ProcessingItem.id == processing_item_id,
        ProcessingItem.status == "EXTRACTING",
    )
    if dispatched_claim_token:
        query = query.filter(ProcessingItem.extraction_claim_token == dispatched_claim_token)

    item = query.with_for_update().first()
    if not item:
        # Guard: Late duplicate or invalid claim generation response ignored cleanly
        logger.warning(
            f"Ignored stale/invalid extraction result for item {processing_item_id} with token {dispatched_claim_token}"
        )
        return None

    doc_type = (extraction_payload.get("document_type") or "unknown").lower()
    item.document_type = doc_type

    raw = extraction_payload.get("extraction")
    normalization = extraction_payload.get("normalization")
    item.raw_extraction = raw if isinstance(raw, dict) else {}
    item.normalized_data = (
        normalization
        if isinstance(normalization, dict) and normalization
        else item.raw_extraction
    )
    item.quality_flags = {"flags": extraction_payload.get("quality_flags") or []}
    item.confidence_data = {"score": extraction_payload.get("confidence")}

    # Clear extraction lease fields & claim token prior to READY transition
    item.claimed_by = None
    item.extraction_claim_token = None
    item.lease_expires_at = None
    item.heartbeat_at = None
    item.status = "EXTRACTED"
    db.flush()

    # Structural Validation: EXTRACTED -> READY
    if validate_structural_readiness(item):
        item.status = "READY"
        item.attempt_count = 0  # Unambiguous attempt counter reset at READY
    else:
        item.status = "EXTRACTION_FAILED"
        if item.document_type not in SUPPORTED_DOCUMENT_TYPES:
            item.error_code = "UNSUPPORTED_DOCUMENT"
            item.error_message_sanitized = "UNSUPPORTED_DOCUMENT"
        else:
            item.error_code = "INVALID_EXTRACTION_RESULT"
            item.error_message_sanitized = "INVALID_EXTRACTION_RESULT"

    db.commit()
    db.refresh(item)

    # Commit-Safe Media Cleanup (Delete buffer ONLY after successful commit)
    if local_buffer_path and os.path.exists(local_buffer_path):
        try:
            os.remove(local_buffer_path)
        except OSError as exc:
            logger.warning(f"Failed to remove local buffer file {local_buffer_path}: {exc}")

    return item


def apply_extraction_failure(
    db: Session,
    processing_item_id: str,
    dispatched_claim_token: Optional[str],
    error_code: str,
    retryable: bool,
    error_message_sanitized: Optional[str] = None,
    local_buffer_path: Optional[str] = None,
) -> Optional[ProcessingItem]:
    """Atomically applies extraction failure guarded by extraction_claim_token."""
    query = db.query(ProcessingItem).filter(
        ProcessingItem.id == processing_item_id,
        ProcessingItem.status == "EXTRACTING",
    )
    if dispatched_claim_token:
        query = query.filter(ProcessingItem.extraction_claim_token == dispatched_claim_token)

    item = query.with_for_update().first()
    if not item:
        logger.warning(
            f"Ignored stale extraction failure for item {processing_item_id} with token {dispatched_claim_token}"
        )
        return None

    item.claimed_by = None
    item.extraction_claim_token = None
    item.lease_expires_at = None
    item.heartbeat_at = None

    if not retryable or item.attempt_count >= MAX_EXTRACTION_ATTEMPTS:
        item.status = "EXTRACTION_FAILED"
        item.error_code = error_code
        item.error_message_sanitized = error_message_sanitized or error_code
    else:
        item.status = "RECEIVED"

    db.commit()
    db.refresh(item)

    if local_buffer_path and os.path.exists(local_buffer_path):
        try:
            os.remove(local_buffer_path)
        except OSError:
            pass

    return item


def validate_structural_readiness(item: ProcessingItem) -> bool:
    """Deterministic structural validation for EXTRACTED -> READY transition using exact Gate 3 types."""
    if (
        not item.document_type
        or item.document_type.lower() not in SUPPORTED_DOCUMENT_TYPES
    ):
        return False
    if not item.normalized_data or not isinstance(item.normalized_data, dict):
        return False
    return True


class ExtractionDispatcher:
    def __init__(self, transcription_client: TranscriptionClient, max_concurrency: int = MAX_CONCURRENT_EXTRACTIONS_PER_SERVICE):
        self.transcription_client = transcription_client
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def process_item(self, db: Session, item: ProcessingItem, mock_file_bytes: bytes | None = None, local_buffer_path: str | None = None) -> Optional[ProcessingItem]:
        """Processes a claimed ProcessingItem through Gate 3 extraction with token guard and commit-safe cleanup."""
        async with self.semaphore:
            if db.new or db.dirty or db.deleted:
                raise RuntimeError("ExtractionDispatcher requires a clean database session with no pending uncommitted changes")

            evt = db.query(Event).filter_by(id=item.event_id).first()
            correlation_id = evt.correlation_id if evt else item.correlation_id
            bot_instance_id = item.instance_id
            dispatched_token = item.extraction_claim_token
            item_id = item.id
            received_at = item.message_received_at or datetime.now(timezone.utc)

            file_bytes = mock_file_bytes or b"MOCK_DOCUMENT_BINARY_CONTENT"
            filename = item.original_filename or "document.jpg"
            mime_type = item.file_mime_type or "image/jpeg"

            # End the read-only transaction before awaiting external Transcription/Gemini I/O
            if db.in_transaction():
                db.rollback()

            if db.in_transaction():
                raise RuntimeError("Database transaction remained open before external Transcription I/O")

            try:
                res_payload = await self.transcription_client.extract(
                    processing_item_id=item_id,
                    bot_instance_id=bot_instance_id,
                    correlation_id=correlation_id,
                    received_at=received_at,
                    file_bytes=file_bytes,
                    filename=filename,
                    mime_type=mime_type,
                )
                updated_item = apply_extraction_success(db, item_id, dispatched_token, res_payload, local_buffer_path=local_buffer_path)
                return updated_item or item
            except TranscriptionClientError as exc:
                updated_item = apply_extraction_failure(
                    db,
                    item_id,
                    dispatched_token,
                    exc.error_code or "EXTRACTION_ERROR",
                    exc.retryable,
                    error_message_sanitized=exc.error_code or "EXTRACTION_ERROR",
                    local_buffer_path=local_buffer_path,
                )
                return updated_item or item
            except Exception as exc:
                logger.error(f"Unexpected extraction dispatcher error: {exc}")
                updated_item = apply_extraction_failure(
                    db,
                    item_id,
                    dispatched_token,
                    "UNEXPECTED_ERROR",
                    retryable=True,
                    error_message_sanitized="UNEXPECTED_ERROR",
                    local_buffer_path=local_buffer_path,
                )
                return updated_item or item
