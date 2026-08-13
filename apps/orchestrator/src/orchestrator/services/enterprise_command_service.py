from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    EnterpriseCommandAnswer,
    EnterpriseCommandSession,
    Event,
    Execution,
    ProcessingItem,
    UserInteraction,
    WhatsappChatEnterpriseBinding,
)
from orchestrator.db_writer_client import DBWriterClient
from orchestrator.repositories.queue_repository import (
    lock_or_create_conversation_counter,
)
from orchestrator.services.enterprise_resolution_service import (
    build_enterprise_option_mapping,
)


OPEN_COMMAND_STATES = ("RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN")
OPEN_INTERACTION_STATES = ("RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN")
COMMAND_TTL_SECONDS = 3600
LATE_ANSWER_WINDOW_SECONDS = 3600
BUSY_MESSAGE = (
    "⚠️ Existe um lançamento aguardando sua resposta.\n\n"
    "Conclua a pergunta atual antes de alterar o empreendimento deste chat."
)


@dataclass(frozen=True)
class CommandOpenResult:
    status: str
    session: Optional[EnterpriseCommandSession] = None


def format_enterprise_command_prompt(session: EnterpriseCommandSession) -> str:
    lines = ["Selecione o empreendimento deste chat:", ""]
    for position in sorted(session.option_mapping, key=lambda value: int(value)):
        choice = session.option_mapping[position]
        lines.append(f"{position} - {choice['display_name']}")
    lines.append(f"{session.clear_option_position} - Limpar seleção")
    return "\n".join(lines)


def _open_document_interaction_exists(
    db: Session, organization_id: str, instance_id: str, user_id: str
) -> bool:
    return (
        db.query(UserInteraction.id)
        .join(ProcessingItem, ProcessingItem.id == UserInteraction.processing_item_id)
        .filter(
            ProcessingItem.organization_id == organization_id,
            ProcessingItem.instance_id == instance_id,
            ProcessingItem.user_id == user_id,
            UserInteraction.status.in_(OPEN_INTERACTION_STATES),
        )
        .first()
        is not None
    )


def open_enterprise_command_session(
    db: Session,
    organization_id: str,
    instance_id: str,
    user_id: str,
    event_id: str,
    correlation_id: str,
    client: Optional[DBWriterClient] = None,
) -> CommandOpenResult:
    client = client or DBWriterClient()
    lock_or_create_conversation_counter(db, organization_id, instance_id, user_id)
    if _open_document_interaction_exists(db, organization_id, instance_id, user_id):
        return CommandOpenResult(status="DOCUMENT_INTERACTION_BUSY")

    existing = (
        db.query(EnterpriseCommandSession)
        .filter(
            EnterpriseCommandSession.organization_id == organization_id,
            EnterpriseCommandSession.instance_id == instance_id,
            EnterpriseCommandSession.user_id == user_id,
            EnterpriseCommandSession.status.in_(OPEN_COMMAND_STATES),
        )
        .first()
    )
    if existing is not None:
        if existing.expires_at is not None and existing.expires_at <= datetime.now(
            timezone.utc
        ):
            existing.status = "EXPIRED"
            existing.resolved_at = datetime.now(timezone.utc)
            db.commit()
        else:
            return CommandOpenResult(status="REPLAY", session=existing)

    option_mapping = build_enterprise_option_mapping(client, correlation_id)

    if existing is not None:
        # The expired generation remains durable; this inbound command starts a
        # new generation only after the prior barrier is terminal.
        lock_or_create_conversation_counter(db, organization_id, instance_id, user_id)

    generation = (
        db.query(sa.func.max(EnterpriseCommandSession.generation))
        .filter(
            EnterpriseCommandSession.organization_id == organization_id,
            EnterpriseCommandSession.instance_id == instance_id,
            EnterpriseCommandSession.user_id == user_id,
        )
        .scalar()
        or 0
    ) + 1
    session = EnterpriseCommandSession(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        instance_id=instance_id,
        user_id=user_id,
        generation=generation,
        status="RESERVED",
        option_mapping=option_mapping,
        clear_option_position=len(option_mapping) + 1,
        outbound_message_id=f"enterprise_{organization_id}_{instance_id}_{user_id}_{generation}",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=COMMAND_TTL_SECONDS),
    )
    db.add(session)
    db.add(
        Execution(
            event_id=event_id,
            processing_item_id=None,
            correlation_id=correlation_id,
            component="ORCHESTRATOR",
            operation="ENTERPRISE_COMMAND_RESERVED",
            external_reference=session.id,
            operation_idempotency_key=f"{session.id}:ENTERPRISE_COMMAND_RESERVED",
            status="SUCCESS",
            attempt=1,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.refresh(session)
    return CommandOpenResult(status="CREATED", session=session)


def dispatch_enterprise_command_session(
    db: Session,
    session: EnterpriseCommandSession,
    event_id: str,
    correlation_id: str,
    sender: Callable[[str, str], bool],
) -> EnterpriseCommandSession:
    if not reserve_enterprise_command_dispatch(db, session, event_id, correlation_id):
        return session
    acknowledged = False
    try:
        acknowledged = sender(
            session.outbound_message_id, format_enterprise_command_prompt(session)
        )
    except Exception:
        acknowledged = False
    return finalize_enterprise_command_dispatch(db, session.id, acknowledged)


def reserve_enterprise_command_dispatch(
    db: Session,
    session: EnterpriseCommandSession,
    event_id: str,
    correlation_id: str,
) -> bool:
    lock_or_create_conversation_counter(
        db, session.organization_id, session.instance_id, session.user_id
    )
    session = (
        db.query(EnterpriseCommandSession)
        .filter(EnterpriseCommandSession.id == session.id)
        .with_for_update()
        .one()
    )
    now = datetime.now(timezone.utc)
    if session.status != "RESERVED":
        return False
    if session.expires_at is not None and session.expires_at <= now:
        session.status = "EXPIRED"
        session.resolved_at = now
        db.commit()
        return False

    dispatched_key = f"{session.id}:ENTERPRISE_COMMAND_DISPATCHED"
    if (
        db.query(Execution.id)
        .filter(Execution.operation_idempotency_key == dispatched_key)
        .first()
    ):
        return False

    db.add(
        Execution(
            event_id=event_id,
            processing_item_id=None,
            correlation_id=correlation_id,
            component="ORCHESTRATOR",
            operation="ENTERPRISE_COMMAND_DISPATCHED",
            external_reference=session.id,
            outbound_message_id=session.outbound_message_id,
            operation_idempotency_key=dispatched_key,
            status="SUCCESS",
            effect_status="DISPATCHED",
            attempt=1,
            started_at=now,
            completed_at=now,
        )
    )
    db.commit()
    return True


def finalize_enterprise_command_dispatch(
    db: Session,
    session_id: str,
    acknowledged: bool,
) -> EnterpriseCommandSession:
    session = (
        db.query(EnterpriseCommandSession)
        .filter(EnterpriseCommandSession.id == session_id)
        .one()
    )

    lock_or_create_conversation_counter(
        db, session.organization_id, session.instance_id, session.user_id
    )
    locked = (
        db.query(EnterpriseCommandSession)
        .filter(EnterpriseCommandSession.id == session.id)
        .with_for_update()
        .one()
    )
    if locked.status != "RESERVED":
        return locked
    finalized = datetime.now(timezone.utc)
    locked.status = "WAITING" if acknowledged else "OUTBOUND_OUTCOME_UNKNOWN"
    locked.waiting_since = finalized
    locked.expires_at = finalized + timedelta(seconds=COMMAND_TTL_SECONDS)
    db.commit()
    db.refresh(locked)
    return locked


def reserve_busy_response(db: Session, event: Event, correlation_id: str) -> bool:
    key = f"{event.id}:ENTERPRISE_COMMAND_BUSY_RESPONSE"
    if (
        db.query(Execution.id)
        .filter(Execution.operation_idempotency_key == key)
        .first()
    ):
        return False
    now = datetime.now(timezone.utc)
    db.add(
        Execution(
            event_id=event.id,
            processing_item_id=None,
            correlation_id=correlation_id,
            component="ORCHESTRATOR",
            operation="ENTERPRISE_COMMAND_BUSY_RESPONSE_DISPATCHED",
            outbound_message_id=f"enterprise_busy_{event.id}",
            operation_idempotency_key=key,
            status="SUCCESS",
            effect_status="DISPATCHED",
            attempt=1,
            started_at=now,
            completed_at=now,
        )
    )
    db.commit()
    return True


def apply_enterprise_command_answer(
    db: Session,
    organization_id: str,
    instance_id: str,
    user_id: str,
    inbound_event_id: str,
    raw_answer_text: str,
) -> EnterpriseCommandAnswer:
    duplicate = (
        db.query(EnterpriseCommandAnswer)
        .filter(EnterpriseCommandAnswer.inbound_event_id == inbound_event_id)
        .first()
    )
    if duplicate is not None:
        return duplicate

    lock_or_create_conversation_counter(db, organization_id, instance_id, user_id)
    session = (
        db.query(EnterpriseCommandSession)
        .filter(
            EnterpriseCommandSession.organization_id == organization_id,
            EnterpriseCommandSession.instance_id == instance_id,
            EnterpriseCommandSession.user_id == user_id,
        )
        .order_by(EnterpriseCommandSession.generation.desc())
        .with_for_update()
        .first()
    )
    if session is None:
        raise ValueError("No enterprise command session exists")

    now = datetime.now(timezone.utc)
    raw = raw_answer_text.strip()
    is_open = session.status in OPEN_COMMAND_STATES
    if is_open and session.expires_at is not None and session.expires_at <= now:
        session.status = "EXPIRED"
        session.resolved_at = now
        is_open = False

    selected = session.option_mapping.get(raw) if raw.isdigit() else None
    is_clear = raw.isdigit() and int(raw) == session.clear_option_position
    if not is_open:
        answer_status, error_code = "LATE", "COMMAND_SESSION_CLOSED"
    elif not is_clear and not isinstance(selected, dict):
        answer_status, error_code = "REJECTED", "INVALID_ENTERPRISE_CHOICE"
    else:
        answer_status, error_code = "APPLIED", None

    answer = EnterpriseCommandAnswer(
        id=str(uuid.uuid4()),
        session_id=session.id,
        inbound_event_id=inbound_event_id,
        sanitized_answer=raw,
        parsing_result=(
            {"clear": True}
            if is_clear
            else {"enterprise_id": selected["enterprise_id"]}
            if isinstance(selected, dict)
            else None
        ),
        status=answer_status,
        error_code=error_code,
        applied_at=now if answer_status == "APPLIED" else None,
    )
    db.add(answer)

    if answer_status == "APPLIED":
        binding = (
            db.query(WhatsappChatEnterpriseBinding)
            .filter(
                WhatsappChatEnterpriseBinding.organization_id == organization_id,
                WhatsappChatEnterpriseBinding.instance_id == instance_id,
                WhatsappChatEnterpriseBinding.user_id == user_id,
            )
            .first()
        )
        if is_clear:
            if binding is not None:
                db.delete(binding)
        else:
            assert isinstance(selected, dict)
            enterprise_id = str(selected["enterprise_id"])
            if binding is None:
                binding = WhatsappChatEnterpriseBinding(
                    organization_id=organization_id,
                    instance_id=instance_id,
                    user_id=user_id,
                    enterprise_id=enterprise_id,
                    source_command_session_id=session.id,
                )
                db.add(binding)
            else:
                binding.enterprise_id = enterprise_id
                binding.source_command_session_id = session.id
        session.status = "ANSWERED"
        session.resolved_at = now
        session.resolved_by_event_id = inbound_event_id

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        duplicate = (
            db.query(EnterpriseCommandAnswer)
            .filter(EnterpriseCommandAnswer.inbound_event_id == inbound_event_id)
            .first()
        )
        if duplicate is not None:
            return duplicate
        raise
    db.refresh(answer)
    return answer


def expire_enterprise_command_sessions(db: Session) -> int:
    now = datetime.now(timezone.utc)
    sessions = (
        db.query(EnterpriseCommandSession)
        .filter(
            EnterpriseCommandSession.status.in_(OPEN_COMMAND_STATES),
            EnterpriseCommandSession.expires_at.isnot(None),
            EnterpriseCommandSession.expires_at < sa.func.now(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for session in sessions:
        session.status = "EXPIRED"
        session.resolved_at = now
    db.commit()
    return len(sessions)


def recover_reserved_enterprise_command_sessions(
    db: Session,
    sender: Callable[[EnterpriseCommandSession], bool],
) -> int:
    """Resumes orphaned RESERVED generations without regenerating their mapping."""
    now = datetime.now(timezone.utc)
    session_ids = (
        db.query(EnterpriseCommandSession.id)
        .filter(EnterpriseCommandSession.status == "RESERVED")
        .order_by(EnterpriseCommandSession.created_at.asc())
        .all()
    )
    recovered = 0
    for (session_id,) in session_ids:
        session = (
            db.query(EnterpriseCommandSession)
            .filter(EnterpriseCommandSession.id == session_id)
            .one()
        )
        lock_or_create_conversation_counter(
            db, session.organization_id, session.instance_id, session.user_id
        )
        session = (
            db.query(EnterpriseCommandSession)
            .filter(EnterpriseCommandSession.id == session_id)
            .with_for_update()
            .one()
        )
        if session.status != "RESERVED":
            continue
        if session.expires_at is not None and session.expires_at <= now:
            session.status = "EXPIRED"
            session.resolved_at = now
            recovered += 1
            continue
        checkpoint = (
            db.query(Execution.id)
            .filter(
                Execution.operation_idempotency_key
                == f"{session.id}:ENTERPRISE_COMMAND_DISPATCHED"
            )
            .first()
        )
        if checkpoint is not None:
            session.status = "OUTBOUND_OUTCOME_UNKNOWN"
            session.waiting_since = now
            recovered += 1
            continue
        # Commit the durable checkpoint before any outbound call.
        reservation = (
            db.query(Execution)
            .filter(
                Execution.operation_idempotency_key
                == f"{session.id}:ENTERPRISE_COMMAND_RESERVED"
            )
            .one()
        )
        if not reserve_enterprise_command_dispatch(
            db,
            session,
            event_id=reservation.event_id,
            correlation_id=reservation.correlation_id,
        ):
            continue
        acknowledged = False
        try:
            acknowledged = sender(session)
        except Exception:
            acknowledged = False
        finalize_enterprise_command_dispatch(db, session.id, acknowledged)
        recovered += 1
    db.commit()
    return recovered


def find_recent_closed_command_for_late_answer(
    db: Session,
    organization_id: str,
    instance_id: str,
    user_id: str,
) -> Optional[EnterpriseCommandSession]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LATE_ANSWER_WINDOW_SECONDS)
    return (
        db.query(EnterpriseCommandSession)
        .filter(
            EnterpriseCommandSession.organization_id == organization_id,
            EnterpriseCommandSession.instance_id == instance_id,
            EnterpriseCommandSession.user_id == user_id,
            EnterpriseCommandSession.status.in_(["EXPIRED", "CANCELLED"]),
            EnterpriseCommandSession.resolved_at.isnot(None),
            EnterpriseCommandSession.resolved_at >= cutoff,
        )
        .order_by(EnterpriseCommandSession.generation.desc())
        .first()
    )
