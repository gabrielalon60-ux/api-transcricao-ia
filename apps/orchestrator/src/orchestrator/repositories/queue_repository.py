from __future__ import annotations

import uuid
from typing import Any, Dict
from sqlalchemy.orm import Session
import sqlalchemy as sa

from db.models import ConversationQueueCounter, ProcessingItem, Event


TERMINAL_STATUSES = (
    "COMPLETED",
    "EXTRACTION_FAILED",
    "PERSISTENCE_FAILED",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
    "IGNORED",
)


def get_non_terminal_capacity_count(
    db: Session, organization_id: str, instance_id: str, user_id: str
) -> int:
    """Counts active non-terminal processing items for a specific conversation."""
    return (
        db.query(sa.func.count(ProcessingItem.id))
        .filter(
            ProcessingItem.organization_id == organization_id,
            ProcessingItem.instance_id == instance_id,
            ProcessingItem.user_id == user_id,
            ProcessingItem.status.notin_(TERMINAL_STATUSES),
        )
        .scalar()
        or 0
    )


def lock_or_create_conversation_counter(
    db: Session, organization_id: str, instance_id: str, user_id: str
) -> ConversationQueueCounter:
    """Ensures conversation_queue_counters row exists atomically without missing-row race, then locks it FOR UPDATE."""
    # 1. Atomic INSERT ON CONFLICT DO NOTHING to solve missing-row race
    db.execute(
        sa.text("""
            INSERT INTO conversation_queue_counters (organization_id, instance_id, user_id, last_sequence, created_at, updated_at)
            VALUES (:org_id, :inst_id, :user_id, 0, NOW(), NOW())
            ON CONFLICT (organization_id, instance_id, user_id) DO NOTHING;
        """),
        {"org_id": organization_id, "inst_id": instance_id, "user_id": user_id},
    )
    db.flush()

    # 2. Acquire FOR UPDATE row lock (guaranteed to exist now)
    counter = (
        db.query(ConversationQueueCounter)
        .filter_by(
            organization_id=organization_id,
            instance_id=instance_id,
            user_id=user_id,
        )
        .with_for_update()
        .first()
    )
    if not counter:
        raise RuntimeError("Failed to acquire conversation counter lock")
    return counter


def allocate_next_sequence(
    db: Session, organization_id: str, instance_id: str, user_id: str
) -> int:
    """Increments the conversation counter and returns the newly allocated monotonic sequence number."""
    counter = lock_or_create_conversation_counter(
        db, organization_id, instance_id, user_id
    )
    counter.last_sequence += 1
    db.flush()
    return counter.last_sequence


def create_processable_processing_item(
    db: Session,
    event: Event,
    organization_id: str,
    instance_id: str,
    user_id: str,
    sequence: int,
    file_info: Dict[str, Any],
) -> ProcessingItem:
    """Creates a processable ProcessingItem with status='RECEIVED' and allocated sequence."""
    item = ProcessingItem(
        id=str(uuid.uuid4()),
        event_id=event.id,
        correlation_id=event.correlation_id,
        organization_id=organization_id,
        instance_id=instance_id,
        user_id=user_id,
        sequence=sequence,
        status="RECEIVED",
        message_received_at=event.received_at,
        file_mime_type=file_info.get("file_mime_type", "application/octet-stream"),
        file_size=file_info.get("file_size", 0),
        file_sha256=file_info.get(
            "file_sha256",
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
        original_filename=file_info.get("original_filename"),
        media_ref=file_info.get("media_ref"),
    )
    db.add(item)
    db.flush()
    return item


def create_capacity_rejected_processing_item(
    db: Session,
    event: Event,
    organization_id: str,
    instance_id: str,
    user_id: str,
    file_info: Dict[str, Any],
) -> ProcessingItem:
    """Creates a capacity-rejected ProcessingItem with sequence=NULL, status='FAILED', error_code='QUEUE_CAPACITY_EXCEEDED'."""
    item = ProcessingItem(
        id=str(uuid.uuid4()),
        event_id=event.id,
        correlation_id=event.correlation_id,
        organization_id=organization_id,
        instance_id=instance_id,
        user_id=user_id,
        sequence=None,
        status="FAILED",
        error_code="QUEUE_CAPACITY_EXCEEDED",
        error_message_sanitized="Queue capacity limit reached for this conversation",
        message_received_at=event.received_at,
        file_mime_type=file_info.get("file_mime_type", "application/octet-stream"),
        file_size=file_info.get("file_size", 0),
        file_sha256=file_info.get(
            "file_sha256",
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
        original_filename=file_info.get("original_filename"),
        media_ref=file_info.get("media_ref"),
    )
    db.add(item)
    db.flush()
    return item
