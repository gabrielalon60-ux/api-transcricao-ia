from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import sqlalchemy as sa

from db.models import ProcessingItem, Execution

logger = logging.getLogger(__name__)

# Configurable initial defaults (approved by Gate 4 architecture)
WORKER_LEASE_DURATION_SECONDS = 60
POLL_INTERVAL_SECONDS = 1.0

TERMINAL_STATES = (
    "COMPLETED",
    "EXTRACTION_FAILED",
    "PERSISTENCE_FAILED",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
)

BLOCKING_STATES = (
    "ACTIVE",
    "VALIDATING",
    "WAITING_USER_INPUT",
    "PERSISTING",
    "PERSIST_RETRYABLE",
    "PERSIST_OUTCOME_UNKNOWN",
)

# Physical partial index name: uq_processing_items_one_active_per_conversation
PHYSICAL_PARTIAL_INDEX_NAME = "uq_processing_items_one_active_per_conversation"


def _normalize_worker_id(worker_id: str) -> str:
    """Ensures worker_id has a consistent 'worker-' prefix."""
    return worker_id if worker_id.startswith("worker-") else f"worker-{worker_id}"


def is_conversation_blocked(
    db: Session,
    organization_id: str,
    instance_id: str,
    user_id: str,
    target_sequence: int,
) -> bool:
    """Defensive inline revalidation: returns True if conversation has any active blocking item or earlier sequence item in a non-terminal state."""
    has_blocking = (
        db.query(ProcessingItem.id)
        .filter(
            ProcessingItem.organization_id == organization_id,
            ProcessingItem.instance_id == instance_id,
            ProcessingItem.user_id == user_id,
            ProcessingItem.status.in_(BLOCKING_STATES),
        )
        .first()
    )
    if has_blocking:
        return True

    has_earlier_non_terminal = (
        db.query(ProcessingItem.id)
        .filter(
            ProcessingItem.organization_id == organization_id,
            ProcessingItem.instance_id == instance_id,
            ProcessingItem.user_id == user_id,
            ProcessingItem.sequence < target_sequence,
            ProcessingItem.status.not_in(TERMINAL_STATES),
        )
        .first()
    )
    if has_earlier_non_terminal:
        return True

    return False


def claim_next_ready_item(db: Session, worker_id: str = "worker-1") -> Optional[ProcessingItem]:
    """Atomically claims the globally oldest eligible READY item for business execution.

    SQL-Level Eligibility & Anti-Starvation:
      - Item status == 'READY'
      - Sequence IS NOT NULL
      - NOT EXISTS physical blocking item in same conversation
      - NOT EXISTS earlier sequence item (< target_sequence) in same conversation in a non-terminal state

    Global Fairness:
      - Ordered by message_received_at ASC, organization_id ASC, instance_id ASC, user_id ASC, sequence ASC.
    """
    full_worker_id = _normalize_worker_id(worker_id)

    # Subquery 1: Check for any blocking item in same conversation
    BlockingItem = sa.orm.aliased(ProcessingItem)
    blocking_subquery = sa.select(BlockingItem.id).where(
        BlockingItem.organization_id == ProcessingItem.organization_id,
        BlockingItem.instance_id == ProcessingItem.instance_id,
        BlockingItem.user_id == ProcessingItem.user_id,
        BlockingItem.status.in_(BLOCKING_STATES),
    ).exists()

    # Subquery 2: Check for earlier non-terminal sequence items in same conversation
    EarlierItem = sa.orm.aliased(ProcessingItem)
    earlier_subquery = sa.select(EarlierItem.id).where(
        EarlierItem.organization_id == ProcessingItem.organization_id,
        EarlierItem.instance_id == ProcessingItem.instance_id,
        EarlierItem.user_id == ProcessingItem.user_id,
        EarlierItem.sequence < ProcessingItem.sequence,
        EarlierItem.status.not_in(TERMINAL_STATES),
    ).exists()

    # Combined candidate query pushing all eligibility rules into SQL
    candidate = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "READY",
            ProcessingItem.sequence.isnot(None),
            ~blocking_subquery,
            ~earlier_subquery,
        )
        .order_by(
            ProcessingItem.message_received_at.asc(),
            ProcessingItem.organization_id.asc(),
            ProcessingItem.instance_id.asc(),
            ProcessingItem.user_id.asc(),
            ProcessingItem.sequence.asc(),
        )
        .with_for_update(skip_locked=True)
        .first()
    )

    if not candidate:
        return None

    # Inline defensive revalidation
    if is_conversation_blocked(
        db,
        organization_id=candidate.organization_id,
        instance_id=candidate.instance_id,
        user_id=candidate.user_id,
        target_sequence=candidate.sequence,
    ):
        return None

    now = datetime.now(timezone.utc)
    candidate.status = "ACTIVE"
    candidate.claimed_by = full_worker_id
    candidate.lease_expires_at = sa.func.now() + sa.text("INTERVAL '60 seconds'")
    candidate.heartbeat_at = now
    candidate.activated_at = now
    candidate.attempt_count += 1  # Increment once at READY -> ACTIVE claim

    # Create executions checkpoint for business claim
    execution = Execution(
        processing_item_id=candidate.id,
        event_id=candidate.event_id,
        correlation_id=candidate.correlation_id,
        component="BOT_DF",
        operation="BUSINESS_CLAIM",
        status="SUCCESS",
        attempt=candidate.attempt_count,
        started_at=now,
        completed_at=now,
        external_reference=candidate.claimed_by,
    )
    db.add(execution)

    try:
        db.commit()
        db.refresh(candidate)
        return candidate
    except IntegrityError as exc:
        db.rollback()
        orig = getattr(exc, "orig", None)
        pgcode = getattr(orig, "pgcode", None)
        diag = getattr(orig, "diag", None)
        constraint_name = getattr(diag, "constraint_name", None) or getattr(orig, "constraint_name", None)
        msg = str(exc)

        is_sqlstate_23505 = pgcode == "23505" or "23505" in msg
        is_target_index = (
            constraint_name == PHYSICAL_PARTIAL_INDEX_NAME
            or PHYSICAL_PARTIAL_INDEX_NAME in msg
        )

        if is_sqlstate_23505 and is_target_index:
            logger.info(
                f"Lost claim race on conversation item {candidate.id} due to partial unique index guard ({PHYSICAL_PARTIAL_INDEX_NAME}): {exc}"
            )
            return None
        logger.error(f"Unrelated integrity violation during claim: {exc}")
        raise exc


def transition_active_to_validating(db: Session, item_id: str, worker_id: str) -> Optional[ProcessingItem]:
    """Atomically transitions an ACTIVE item to VALIDATING guarded by worker claim ownership.

    Idempotency:
      - If item is already VALIDATING with matching worker_id, returns existing item without creating duplicate execution checkpoints.
    """
    full_worker_id = _normalize_worker_id(worker_id)
    now = datetime.now(timezone.utc)

    # Idempotency check: item already VALIDATING with matching worker
    existing = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "VALIDATING",
            ProcessingItem.claimed_by == full_worker_id,
        )
        .first()
    )
    if existing:
        return existing

    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "ACTIVE",
            ProcessingItem.claimed_by == full_worker_id,
            ProcessingItem.lease_expires_at > sa.func.now(),
        )
        .with_for_update()
        .first()
    )
    if not item:
        logger.warning(f"Failed ACTIVE -> VALIDATING transition for item {item_id} by worker {full_worker_id} (mismatched claim, invalid status, or expired lease)")
        return None

    item.status = "VALIDATING"
    # attempt_count NOT incremented on ACTIVE -> VALIDATING

    execution = Execution(
        processing_item_id=item.id,
        event_id=item.event_id,
        correlation_id=item.correlation_id,
        component="BOT_DF",
        operation="BUSINESS_VALIDATION_STARTED",
        status="SUCCESS",
        attempt=item.attempt_count,
        started_at=now,
        completed_at=now,
        external_reference=item.claimed_by,
    )
    db.add(execution)

    db.commit()
    db.refresh(item)
    return item
