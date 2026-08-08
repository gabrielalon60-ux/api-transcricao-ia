from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
import sqlalchemy as sa

from db.models import ProcessingItem, Execution, UserInteraction

logger = logging.getLogger(__name__)

STALE_RECOVERY_SWEEPER_INTERVAL_SECONDS = 30
WAITING_USER_INPUT_TTL_SECONDS = 3600


def recover_stale_active_items(db: Session) -> int:
    """Scans and recovers expired ACTIVE processing items.

    If no later external effects exist:
      - ACTIVE -> READY
      - clear claimed_by, heartbeat_at, lease_expires_at
      - preserve attempt_count
      - insert BUSINESS_ACTIVE_RECOVERED execution checkpoint

    Returns count of recovered items.
    """
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "ACTIVE",
            ProcessingItem.lease_expires_at.isnot(None),
            ProcessingItem.lease_expires_at < sa.func.now(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    recovered_count = 0
    for item in candidates:
        # Revalidate inside locked transaction
        if not item.lease_expires_at or item.lease_expires_at >= datetime.now(timezone.utc):
            continue

        # Inspect executions for later external / irreversible effects
        later_effects = (
            db.query(Execution)
            .filter(
                Execution.processing_item_id == item.id,
                Execution.operation.in_(["EXTERNAL_DISPATCH", "WUZAPI_DISPATCH", "DB_WRITE"]),
            )
            .first()
        )

        if later_effects:
            # Irreversible effect exists -> emit anomaly evidence, do not reset to READY
            logger.warning(
                f"Stale ACTIVE item {item.id} has later external effect ({later_effects.operation}). Recording recovery anomaly."
            )
            anomaly = Execution(
                processing_item_id=item.id,
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                component="BOT_DF",
                operation="ACTIVE_RECOVERY_ANOMALY",
                status="FAILED",
                attempt=item.attempt_count,
                started_at=now,
                completed_at=now,
                error_code="LATER_EFFECT_EXISTS",
                error_message_sanitized="Stale ACTIVE item has later external effect; manual intervention required",
            )
            db.add(anomaly)
            db.commit()
            continue

        # Safe to reset ACTIVE -> READY
        old_worker = item.claimed_by
        item.status = "READY"
        item.claimed_by = None
        item.heartbeat_at = None
        item.lease_expires_at = None
        # attempt_count PRESERVED

        execution = Execution(
            processing_item_id=item.id,
            event_id=item.event_id,
            correlation_id=item.correlation_id,
            component="BOT_DF",
            operation="BUSINESS_ACTIVE_RECOVERED",
            status="SUCCESS",
            attempt=item.attempt_count,
            started_at=now,
            completed_at=now,
            external_reference=old_worker,
        )
        db.add(execution)
        db.commit()
        recovered_count += 1
        logger.info(f"Recovered stale ACTIVE item {item.id} (previously claimed by {old_worker}) -> READY")

    return recovered_count


def recover_stale_validating_items(db: Session) -> int:
    """Scans and recovers expired VALIDATING processing items using deterministic matrix.

    Returns count of recovered items.
    """
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "VALIDATING",
            ProcessingItem.lease_expires_at.isnot(None),
            ProcessingItem.lease_expires_at < sa.func.now(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    recovered_count = 0
    for item in candidates:
        if not item.lease_expires_at or item.lease_expires_at >= datetime.now(timezone.utc):
            continue

        # Fetch checkpoints and open user_interaction
        execs = (
            db.query(Execution)
            .filter(Execution.processing_item_id == item.id)
            .all()
        )
        op_set = {e.operation for e in execs}
        effect_set = {e.effect_status for e in execs if e.effect_status is not None}

        open_interaction = (
            db.query(UserInteraction)
            .filter(
                UserInteraction.processing_item_id == item.id,
                UserInteraction.status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
            )
            .first()
        )

        # Contradictory check
        if "USER_PROMPT_ACKNOWLEDGED" in op_set and "OUTBOUND_OUTCOME_UNKNOWN" in effect_set:
            logger.warning(f"Contradictory ledger detected on item {item.id}. Recording recovery anomaly.")
            db.add(
                Execution(
                    processing_item_id=item.id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="VALIDATION_RECOVERY_ANOMALY",
                    status="FAILED",
                    attempt=item.attempt_count,
                    started_at=now,
                    completed_at=now,
                    error_code="CONTRADICTORY_LEDGER",
                    error_message_sanitized="Contradictory prompt checkpoints detected",
                )
            )
            db.commit()
            continue

        # Matrix Evaluation:
        # Case A: USER_PROMPT_ACKNOWLEDGED checkpoint exists
        if "USER_PROMPT_ACKNOWLEDGED" in op_set:
            item.status = "WAITING_USER_INPUT"
            item.waiting_since = item.waiting_since or now
            item.expires_at = item.expires_at or (now + timedelta(seconds=WAITING_USER_INPUT_TTL_SECONDS))
            item.claimed_by = None
            item.heartbeat_at = None
            item.lease_expires_at = None
            if open_interaction and open_interaction.status != "WAITING":
                open_interaction.status = "WAITING"
                open_interaction.waiting_since = open_interaction.waiting_since or now
                open_interaction.expires_at = open_interaction.expires_at or (now + timedelta(seconds=WAITING_USER_INPUT_TTL_SECONDS))
            db.commit()
            recovered_count += 1
            continue

        # Case B: USER_PROMPT_DISPATCHED without ACKNOWLEDGED or OUTCOME_UNKNOWN
        if "USER_PROMPT_DISPATCHED" in op_set and "USER_PROMPT_ACKNOWLEDGED" not in op_set:
            item.status = "WAITING_USER_INPUT"
            item.waiting_since = now
            item.expires_at = now + timedelta(seconds=WAITING_USER_INPUT_TTL_SECONDS)
            item.claimed_by = None
            item.heartbeat_at = None
            item.lease_expires_at = None
            if open_interaction:
                open_interaction.status = "OUTBOUND_OUTCOME_UNKNOWN"
                open_interaction.waiting_since = now
                open_interaction.expires_at = item.expires_at

            db.add(
                Execution(
                    processing_item_id=item.id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="USER_PROMPT_OUTCOME_UNKNOWN",
                    status="FAILED",
                    effect_status="OUTBOUND_OUTCOME_UNKNOWN",
                    attempt=item.attempt_count,
                    started_at=now,
                    completed_at=now,
                    error_code="OUTBOUND_OUTCOME_UNKNOWN",
                    error_message_sanitized="Dispatched prompt status unconfirmed after worker lease expiration",
                )
            )
            db.commit()
            recovered_count += 1
            continue

        # Case C: RESERVED interaction exists without USER_PROMPT_DISPATCHED
        if open_interaction and open_interaction.status == "RESERVED":
            # Preserve interaction and item in VALIDATING so dispatcher resumes
            item.claimed_by = None
            item.heartbeat_at = None
            item.lease_expires_at = None
            db.commit()
            recovered_count += 1
            continue

        # Case D: No prompt interaction or prompt checkpoint -> VALIDATING -> READY
        old_worker = item.claimed_by
        item.status = "READY"
        item.claimed_by = None
        item.heartbeat_at = None
        item.lease_expires_at = None

        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                component="BOT_DF",
                operation="BUSINESS_VALIDATION_RECOVERED",
                status="SUCCESS",
                attempt=item.attempt_count,
                started_at=now,
                completed_at=now,
                external_reference=old_worker,
            )
        )
        db.commit()
        recovered_count += 1
        logger.info(f"Recovered stale VALIDATING item {item.id} -> READY")

    return recovered_count
