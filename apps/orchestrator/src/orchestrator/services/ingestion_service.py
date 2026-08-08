from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
import sqlalchemy as sa

from db.models import Event, ProcessingItem, Execution
from orchestrator.payload import compute_payload_hash, is_payload_mutated
from orchestrator.repositories.queue_repository import (
    get_non_terminal_capacity_count,
    lock_or_create_conversation_counter,
    allocate_next_sequence,
    create_processable_processing_item,
    create_capacity_rejected_processing_item,
)


class IngestionOutcome(str, Enum):
    CREATED = "CREATED"
    DUPLICATE = "DUPLICATE"
    DRIFT_REPAIRED = "DRIFT_REPAIRED"
    CAPACITY_REJECTED = "CAPACITY_REJECTED"
    PAYLOAD_CONFLICT = "PAYLOAD_CONFLICT"


@dataclass
class IngestionResult:
    outcome: IngestionOutcome
    item: Optional[ProcessingItem]
    sequence: Optional[int]


def ingest_event_transaction(
    db: Session,
    event: Event,
    organization_id: str,
    instance_id: str,
    user_id: str,
    file_info: Dict[str, Any],
    max_queue_limit: int = 10,
) -> IngestionResult:
    """Orchestrates single-transaction event ingestion into persistent FIFO queue."""
    current_payload_hash = compute_payload_hash(file_info)

    # 1. Check if Event already exists by provider + external_instance_id + external_message_id
    existing_evt = (
        db.query(Event)
        .filter(
            Event.provider == event.provider,
            Event.external_instance_id == event.external_instance_id,
            Event.external_message_id == event.external_message_id,
        )
        .first()
    )

    if existing_evt:
        target_evt = existing_evt
    else:
        target_evt = event
        db.add(target_evt)

    # Persist payload hash on Event if not yet set
    if not target_evt.payload_hash:
        target_evt.payload_hash = current_payload_hash

    # 2. Check if ProcessingItem already exists for this event
    existing_item = db.query(ProcessingItem).filter_by(event_id=target_evt.id).first()
    if existing_item:
        # Compare incoming canonical payload hash against persisted event.payload_hash
        persisted_hash = target_evt.payload_hash or existing_item.file_sha256
        if is_payload_mutated(persisted_hash, current_payload_hash):
            audit_execution = Execution(
                id=str(uuid.uuid4()),
                event_id=target_evt.id,
                processing_item_id=existing_item.id,
                correlation_id=target_evt.correlation_id,
                component="ORCHESTRATOR",
                operation="INGEST_PAYLOAD_CONFLICT",
                status="FAILED",
                error_code="USER_EVENT_PAYLOAD_MUTATED",
                error_message_sanitized="Webhook payload mutated under existing external message ID",
            )
            db.add(audit_execution)
            target_evt.duplicate_count += 1
            target_evt.last_duplicate_at = sa.func.now()
            db.commit()
            return IngestionResult(
                outcome=IngestionOutcome.PAYLOAD_CONFLICT,
                item=existing_item,
                sequence=existing_item.sequence,
            )

        # Duplicate delivery replay
        target_evt.duplicate_count += 1
        target_evt.last_duplicate_at = sa.func.now()
        db.commit()
        return IngestionResult(
            outcome=IngestionOutcome.DUPLICATE,
            item=existing_item,
            sequence=existing_item.sequence,
        )

    # 3. Race-safe Capacity Check & Counter Locking
    lock_or_create_conversation_counter(db, organization_id, instance_id, user_id)

    current_active_count = get_non_terminal_capacity_count(
        db, organization_id, instance_id, user_id
    )

    if current_active_count < max_queue_limit:
        allocated_sequence = allocate_next_sequence(
            db, organization_id, instance_id, user_id
        )

        item = create_processable_processing_item(
            db=db,
            event=target_evt,
            organization_id=organization_id,
            instance_id=instance_id,
            user_id=user_id,
            sequence=allocated_sequence,
            file_info=file_info,
        )
        db.commit()
        return IngestionResult(
            outcome=IngestionOutcome.CREATED,
            item=item,
            sequence=allocated_sequence,
        )

    # Capacity limit reached -> Capacity Rejected
    item = create_capacity_rejected_processing_item(
        db=db,
        event=target_evt,
        organization_id=organization_id,
        instance_id=instance_id,
        user_id=user_id,
        file_info=file_info,
    )
    db.commit()
    return IngestionResult(
        outcome=IngestionOutcome.CAPACITY_REJECTED,
        item=item,
        sequence=None,
    )
