from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from db.models import (
    EnterpriseCommandSession,
    ProcessingItem,
    Execution,
    UserInteraction,
)
from orchestrator.repositories.queue_repository import (
    lock_or_create_conversation_counter,
)

logger = logging.getLogger(__name__)


def handle_cancel_command(
    db: Session,
    organization_id: str,
    instance_id: str,
    user_id: str,
    event_id: str,
    correlation_id: str,
) -> Optional[ProcessingItem | EnterpriseCommandSession]:
    """Handles explicit /cancelar command for a conversation with a WAITING_USER_INPUT item.

    Idempotency:
      - Repeated /cancelar calls on an already CANCELLED item return the item without creating duplicate execution checkpoints.
      - Queue unblocking occurs because CANCELLED is a terminal state (status NOT IN BLOCKING_STATES).
    """
    now = datetime.now(timezone.utc)

    # 1. Lock candidate WAITING_USER_INPUT item for exact conversation
    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.organization_id == organization_id,
            ProcessingItem.instance_id == instance_id,
            ProcessingItem.user_id == user_id,
            ProcessingItem.status == "WAITING_USER_INPUT",
        )
        .with_for_update()
        .first()
    )

    if not item:
        lock_or_create_conversation_counter(db, organization_id, instance_id, user_id)
        command_session = (
            db.query(EnterpriseCommandSession)
            .filter(
                EnterpriseCommandSession.organization_id == organization_id,
                EnterpriseCommandSession.instance_id == instance_id,
                EnterpriseCommandSession.user_id == user_id,
                EnterpriseCommandSession.status.in_(
                    ["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]
                ),
            )
            .with_for_update()
            .first()
        )
        if command_session is not None:
            command_session.status = "CANCELLED"
            command_session.resolved_at = now
            db.commit()
            db.refresh(command_session)
            return command_session
        # Check if latest item in conversation is already CANCELLED (idempotent return)
        already_cancelled = (
            db.query(ProcessingItem)
            .filter(
                ProcessingItem.organization_id == organization_id,
                ProcessingItem.instance_id == instance_id,
                ProcessingItem.user_id == user_id,
                ProcessingItem.status == "CANCELLED",
            )
            .order_by(ProcessingItem.updated_at.desc())
            .first()
        )
        if already_cancelled:
            logger.info(
                f"Repeated /cancelar command for conversation item {already_cancelled.id} (already CANCELLED). Returning existing item."
            )
            return already_cancelled
        logger.info(
            f"No WAITING_USER_INPUT item found to cancel for conversation ({organization_id}, {instance_id}, {user_id})."
        )
        return None

    # 2. Lock open interaction
    interaction = (
        db.query(UserInteraction)
        .filter(
            UserInteraction.processing_item_id == item.id,
            UserInteraction.status.in_(
                ["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]
            ),
        )
        .with_for_update()
        .first()
    )

    # 3. Transition states
    item.status = "CANCELLED"
    item.waiting_since = None
    item.expires_at = None
    item.claimed_by = None
    item.heartbeat_at = None
    item.lease_expires_at = None

    if interaction:
        interaction.status = "CANCELLED"
        interaction.resolved_at = now

    # 4. Insert USER_CANCELLED execution checkpoint
    cancelled_idem_key = f"{item.id}:USER_CANCELLED:{interaction.outbound_message_id if interaction else 'no_interaction'}"
    db.add(
        Execution(
            processing_item_id=item.id,
            event_id=item.event_id,
            correlation_id=correlation_id,
            component="BOT_DF",
            operation="USER_CANCELLED",
            external_reference=interaction.outbound_message_id if interaction else None,
            operation_idempotency_key=cancelled_idem_key,
            status="SUCCESS",
            attempt=item.attempt_count,
            started_at=now,
            completed_at=now,
        )
    )

    db.commit()
    db.refresh(item)
    logger.info(f"Durably cancelled processing item {item.id} via /cancelar command.")
    return item
