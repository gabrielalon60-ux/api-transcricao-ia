from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session
import sqlalchemy as sa

from db.models import ProcessingItem, Execution, UserInteraction

logger = logging.getLogger(__name__)


def expire_waiting_user_input_items(db: Session) -> int:
    """Scans and expires WAITING_USER_INPUT items whose expires_at < NOW().

    Idempotency & Concurrency:
      - Uses FOR UPDATE SKIP LOCKED to allow concurrent sweepers to run safely.
      - Transition to EXPIRED unblocks FIFO eligibility because EXPIRED is a terminal state.

    Returns count of expired items.
    """
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "WAITING_USER_INPUT",
            ProcessingItem.expires_at.isnot(None),
            ProcessingItem.expires_at < sa.func.now(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    expired_count = 0
    for item in candidates:
        if not item.expires_at or item.expires_at >= datetime.now(timezone.utc):
            continue

        # Lock open interaction
        interaction = (
            db.query(UserInteraction)
            .filter(
                UserInteraction.processing_item_id == item.id,
                UserInteraction.status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
            )
            .with_for_update()
            .first()
        )

        item.status = "EXPIRED"
        item.waiting_since = None
        item.expires_at = None
        item.claimed_by = None
        item.heartbeat_at = None
        item.lease_expires_at = None

        if interaction:
            interaction.status = "EXPIRED"
            interaction.resolved_at = now

        expired_idem_key = f"{item.id}:USER_INPUT_EXPIRED:{interaction.outbound_message_id if interaction else 'no_interaction'}"
        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                component="BOT_DF",
                operation="USER_INPUT_EXPIRED",
                external_reference=interaction.outbound_message_id if interaction else None,
                operation_idempotency_key=expired_idem_key,
                status="SUCCESS",
                attempt=item.attempt_count,
                started_at=now,
                completed_at=now,
            )
        )

        db.commit()
        expired_count += 1
        logger.info(f"Durably expired WAITING_USER_INPUT item {item.id} (TTL exceeded).")

    return expired_count
