"""Durable, fail-closed final notifications for Gate 8 BOT DF outcomes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

from db.models import Execution, ProcessingItem, User
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from orchestrator.services.business_rules_evaluator import (
    BUSINESS_TIMEZONE,
    format_success_message,
)

EXPENSE_COMMITTED: Final = "EXPENSE_COMMITTED"
INCOME_OUT_OF_SCOPE: Final = "INCOME_OUT_OF_SCOPE"
EXTRACTION_FAILED: Final = "EXTRACTION_FAILED"
PERSISTENCE_FAILED: Final = "PERSISTENCE_FAILED"

FINAL_NOTIFICATION_TYPES: Final = (
    EXPENSE_COMMITTED,
    INCOME_OUT_OF_SCOPE,
    EXTRACTION_FAILED,
    PERSISTENCE_FAILED,
)

FINAL_NOTIFICATION_BATCH_SIZE: Final = 100
FINAL_NOTIFICATION_DISPATCH_CONCURRENCY: Final = 1
FINAL_NOTIFICATION_POLL_INTERVAL_SECONDS: Final = 1.0
FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS: Final = 60
FINAL_NOTIFICATION_SHUTDOWN_JOIN_SECONDS: Final = 1.0

_COMPONENT: Final = "BOT_DF"
_RESERVED_OPERATION: Final = "FINAL_NOTIFICATION_RESERVED"
_DISPATCHED_OPERATION: Final = "FINAL_NOTIFICATION_DISPATCHED"
_ACKNOWLEDGED_OPERATION: Final = "FINAL_NOTIFICATION_ACKNOWLEDGED"
_UNKNOWN_OPERATION: Final = "FINAL_NOTIFICATION_OUTCOME_UNKNOWN"
_IDENTITY_MAX_LENGTH: Final = 512

INCOME_OUT_OF_SCOPE_MESSAGE: Final = (
    "ℹ️ Entrada identificada.\n\n"
    "No momento, os lançamentos via WhatsApp registram apenas despesas.\n"
    "Este documento não foi gravado."
)
EXTRACTION_FAILED_MESSAGE: Final = (
    "⚠️ Não foi possível processar este documento.\n\n"
    "Tente enviá-lo novamente em alguns instantes."
)
PERSISTENCE_FAILED_MESSAGE: Final = (
    "⚠️ Não foi possível gravar este lançamento.\n\n"
    "Nenhuma confirmação de gravação foi enviada. Tente novamente mais tarde."
)


@dataclass(frozen=True)
class FinalNotificationIntent:
    processing_item_id: str
    notification_type: str
    phone_number: str
    message: str
    outbound_message_id: str
    dispatch_execution_id: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_notification_type(notification_type: str) -> None:
    if notification_type not in FINAL_NOTIFICATION_TYPES:
        raise ValueError("Unsupported final notification type")


def _bounded_identity(value: str) -> str:
    if not value or len(value) > _IDENTITY_MAX_LENGTH:
        raise ValueError("Final notification identity exceeds the supported bounds")
    return value


def reservation_key(processing_item_id: str, notification_type: str) -> str:
    _validate_notification_type(notification_type)
    return _bounded_identity(
        f"{processing_item_id}:FINAL_NOTIFICATION_RESERVED:{notification_type}"
    )


def dispatch_key(processing_item_id: str, notification_type: str) -> str:
    _validate_notification_type(notification_type)
    return _bounded_identity(
        f"{processing_item_id}:FINAL_NOTIFICATION_DISPATCHED:{notification_type}"
    )


def final_key(processing_item_id: str, notification_type: str) -> str:
    _validate_notification_type(notification_type)
    return _bounded_identity(
        f"{processing_item_id}:FINAL_NOTIFICATION_FINAL:{notification_type}"
    )


def outbound_message_id(processing_item_id: str, notification_type: str) -> str:
    _validate_notification_type(notification_type)
    return _bounded_identity(
        f"final_{processing_item_id}_{notification_type.lower()}"
    )


def _notification_type_from_key(key: str | None) -> str | None:
    if key is None:
        return None
    for notification_type in FINAL_NOTIFICATION_TYPES:
        if key.endswith(f":{notification_type}"):
            return notification_type
    return None


def _committed_persistence_exists(db: Session, item_id: str) -> bool:
    return (
        db.scalar(
            select(
                exists().where(
                    Execution.processing_item_id == item_id,
                    Execution.operation.in_(
                        ("PERSISTENCE_COMMITTED", "PERSISTENCE_RECONCILED_COMMITTED")
                    ),
                    Execution.status == "SUCCESS",
                    Execution.external_reference.is_not(None),
                    Execution.external_reference != "",
                )
            )
        )
        is True
    )


def notification_type_for_item(db: Session, item: ProcessingItem) -> str | None:
    """Return the sole eligible final outcome, or no intent fail-closed."""
    if item.status == "IGNORED":
        return (
            INCOME_OUT_OF_SCOPE
            if item.outcome_reason == "INCOME_OUT_OF_SCOPE"
            else None
        )
    if item.status == "EXTRACTION_FAILED":
        return EXTRACTION_FAILED
    if item.status == "PERSISTENCE_FAILED":
        return PERSISTENCE_FAILED
    if item.status != "COMPLETED":
        return None
    if item.external_operation_status != "COMMITTED":
        return None
    if item.direction != "expense":
        return None
    if item.amount is None or Decimal(item.amount) <= 0:
        return None
    if item.transaction_date is None:
        return None
    if not _committed_persistence_exists(db, item.id):
        return None
    return EXPENSE_COMMITTED


def _display_date(item: ProcessingItem) -> date:
    if item.date_source == "DOCUMENT" and item.document_date:
        return date.fromisoformat(item.document_date)
    if item.transaction_date is None:
        raise ValueError("Eligible committed expense has no transaction date")
    return item.transaction_date.astimezone(BUSINESS_TIMEZONE).date()


def message_for_item(item: ProcessingItem, notification_type: str) -> str:
    _validate_notification_type(notification_type)
    if notification_type == EXPENSE_COMMITTED:
        if item.amount is None:
            raise ValueError("Eligible committed expense has no amount")
        return format_success_message("expense", Decimal(item.amount), _display_date(item))
    if notification_type == INCOME_OUT_OF_SCOPE:
        return INCOME_OUT_OF_SCOPE_MESSAGE
    if notification_type == EXTRACTION_FAILED:
        return EXTRACTION_FAILED_MESSAGE
    return PERSISTENCE_FAILED_MESSAGE


def _execution_exists(db: Session, key: str) -> bool:
    return (
        db.scalar(
            select(
                exists().where(Execution.operation_idempotency_key == key)
            )
        )
        is True
    )


def reserve_final_notifications(
    db: Session,
    *,
    batch_size: int = FINAL_NOTIFICATION_BATCH_SIZE,
    now: datetime | None = None,
) -> int:
    """Reserve eligible terminal outcomes atomically without performing I/O."""
    current_time = now or _utc_now()
    effective_batch_size = max(0, min(batch_size, FINAL_NOTIFICATION_BATCH_SIZE))
    if effective_batch_size == 0:
        db.rollback()
        return 0
    prior_reservation = aliased(Execution)
    prior_finalization = aliased(Execution)
    committed_execution = aliased(Execution)
    candidates = list(
        db.scalars(
            select(ProcessingItem)
            .where(
                or_(
                    and_(
                        ProcessingItem.status == "IGNORED",
                        ProcessingItem.outcome_reason == INCOME_OUT_OF_SCOPE,
                    ),
                    ProcessingItem.status.in_(
                        ("EXTRACTION_FAILED", "PERSISTENCE_FAILED")
                    ),
                    and_(
                        ProcessingItem.status == "COMPLETED",
                        ProcessingItem.external_operation_status == "COMMITTED",
                        ProcessingItem.direction == "expense",
                        ProcessingItem.amount.is_not(None),
                        ProcessingItem.amount > 0,
                        ProcessingItem.transaction_date.is_not(None),
                        exists().where(
                            committed_execution.processing_item_id
                            == ProcessingItem.id,
                            committed_execution.operation.in_(
                                (
                                    "PERSISTENCE_COMMITTED",
                                    "PERSISTENCE_RECONCILED_COMMITTED",
                                )
                            ),
                            committed_execution.status == "SUCCESS",
                            committed_execution.external_reference.is_not(None),
                            committed_execution.external_reference != "",
                        ),
                    ),
                ),
                ~exists().where(
                    prior_reservation.processing_item_id == ProcessingItem.id,
                    prior_reservation.operation == _RESERVED_OPERATION,
                ),
                ~exists().where(
                    prior_finalization.processing_item_id == ProcessingItem.id,
                    prior_finalization.operation.in_(
                        (_ACKNOWLEDGED_OPERATION, _UNKNOWN_OPERATION)
                    ),
                    prior_finalization.operation_idempotency_key.is_not(None),
                ),
            )
            .order_by(
                ProcessingItem.completed_at.asc().nulls_last(),
                ProcessingItem.created_at.asc(),
                ProcessingItem.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(effective_batch_size)
        )
    )
    reserved = 0
    for item in candidates:
        notification_type = notification_type_for_item(db, item)
        if notification_type is None:
            continue
        reserve_identity = reservation_key(item.id, notification_type)
        if _execution_exists(db, reserve_identity):
            continue
        try:
            with db.begin_nested():
                db.add(
                    Execution(
                        processing_item_id=item.id,
                        event_id=item.event_id,
                        correlation_id=item.correlation_id,
                        component=_COMPONENT,
                        operation=_RESERVED_OPERATION,
                        operation_idempotency_key=reserve_identity,
                        status="SUCCESS",
                        attempt=1,
                        started_at=current_time,
                        completed_at=current_time,
                    )
                )
                db.flush()
            reserved += 1
        except IntegrityError:
            # Another notifier won the unique identity race.
            continue
    db.commit()
    return reserved


def claim_reserved_notification(
    db: Session,
    *,
    now: datetime | None = None,
) -> FinalNotificationIntent | None:
    """Durably record dispatch ownership before returning an outbound intent."""
    prior_dispatch = aliased(Execution)
    prior_final = aliased(Execution)
    reservation = db.scalar(
        select(Execution)
        .where(
            Execution.operation == _RESERVED_OPERATION,
            ~exists().where(
                prior_dispatch.processing_item_id == Execution.processing_item_id,
                prior_dispatch.operation == _DISPATCHED_OPERATION,
            ),
            ~exists().where(
                prior_final.processing_item_id == Execution.processing_item_id,
                prior_final.operation.in_(
                    (_ACKNOWLEDGED_OPERATION, _UNKNOWN_OPERATION)
                ),
            ),
        )
        .order_by(Execution.created_at.asc(), Execution.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if reservation is None:
        db.rollback()
        return None

    notification_type = _notification_type_from_key(
        reservation.operation_idempotency_key
    )
    if notification_type is None:
        db.rollback()
        return None
    item = db.get(ProcessingItem, reservation.processing_item_id)
    if item is None or notification_type_for_item(db, item) != notification_type:
        db.rollback()
        return None
    dispatch_identity = dispatch_key(item.id, notification_type)
    if _execution_exists(db, final_key(item.id, notification_type)) or _execution_exists(
        db, dispatch_identity
    ):
        db.rollback()
        return None
    user = db.get(User, item.user_id)
    if user is None or not user.phone_number:
        db.rollback()
        return None

    current_time = now or _utc_now()
    dispatch = Execution(
        processing_item_id=item.id,
        event_id=item.event_id,
        correlation_id=item.correlation_id,
        component=_COMPONENT,
        operation=_DISPATCHED_OPERATION,
        operation_idempotency_key=dispatch_identity,
        outbound_message_id=outbound_message_id(item.id, notification_type),
        status="SUCCESS",
        effect_status="DISPATCHED",
        attempt=1,
        started_at=current_time,
        completed_at=current_time,
    )
    try:
        with db.begin_nested():
            db.add(dispatch)
            db.flush()
    except IntegrityError:
        db.rollback()
        return None
    intent = FinalNotificationIntent(
        processing_item_id=item.id,
        notification_type=notification_type,
        phone_number=user.phone_number,
        message=message_for_item(item, notification_type),
        outbound_message_id=dispatch.outbound_message_id or "",
        dispatch_execution_id=dispatch.id,
    )
    db.commit()
    return intent


def finalize_notification(
    db: Session,
    intent: FinalNotificationIntent,
    *,
    acknowledged: bool,
    now: datetime | None = None,
) -> bool:
    """Finalize ACK or UNKNOWN on the single shared terminal identity."""
    dispatch = db.scalar(
        select(Execution)
        .where(Execution.id == intent.dispatch_execution_id)
        .with_for_update()
    )
    if dispatch is None:
        db.rollback()
        return False
    terminal_identity = final_key(intent.processing_item_id, intent.notification_type)
    if _execution_exists(db, terminal_identity):
        db.rollback()
        return False
    current_time = now or _utc_now()
    final_execution = Execution(
        processing_item_id=dispatch.processing_item_id,
        event_id=dispatch.event_id,
        correlation_id=dispatch.correlation_id,
        component=_COMPONENT,
        operation=(
            _ACKNOWLEDGED_OPERATION if acknowledged else _UNKNOWN_OPERATION
        ),
        operation_idempotency_key=terminal_identity,
        status="SUCCESS" if acknowledged else "FAILED",
        effect_status=(
            "ACKNOWLEDGED" if acknowledged else "OUTBOUND_OUTCOME_UNKNOWN"
        ),
        external_reference=intent.outbound_message_id,
        attempt=1,
        started_at=current_time,
        completed_at=current_time,
    )
    try:
        with db.begin_nested():
            db.add(final_execution)
            db.flush()
    except IntegrityError:
        db.rollback()
        return False
    db.commit()
    return True


def finalize_stale_dispatched_notifications(
    db: Session,
    *,
    now: datetime | None = None,
    grace_seconds: int = FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS,
    batch_size: int = FINAL_NOTIFICATION_BATCH_SIZE,
) -> int:
    """Close abandoned dispatched rows as UNKNOWN; never make them resendable."""
    current_time = now or _utc_now()
    cutoff = current_time - timedelta(seconds=grace_seconds)
    dispatches = list(
        db.scalars(
            select(Execution)
            .where(
                Execution.operation == _DISPATCHED_OPERATION,
                Execution.completed_at <= cutoff,
            )
            .order_by(Execution.completed_at.asc(), Execution.id.asc())
            .with_for_update(skip_locked=True)
            .limit(batch_size)
        )
    )
    finalized = 0
    for dispatch in dispatches:
        notification_type = _notification_type_from_key(
            dispatch.operation_idempotency_key
        )
        if notification_type is None:
            continue
        terminal_identity = final_key(dispatch.processing_item_id, notification_type)
        if _execution_exists(db, terminal_identity):
            continue
        try:
            with db.begin_nested():
                db.add(
                    Execution(
                        processing_item_id=dispatch.processing_item_id,
                        event_id=dispatch.event_id,
                        correlation_id=dispatch.correlation_id,
                        component=_COMPONENT,
                        operation=_UNKNOWN_OPERATION,
                        operation_idempotency_key=terminal_identity,
                        status="FAILED",
                        effect_status="OUTBOUND_OUTCOME_UNKNOWN",
                        external_reference=dispatch.outbound_message_id,
                        attempt=1,
                        started_at=current_time,
                        completed_at=current_time,
                    )
                )
                db.flush()
            finalized += 1
        except IntegrityError:
            continue
    db.commit()
    return finalized


def run_final_notification_iteration(
    session_factory: Callable[[], Session],
    sender: Callable[[str, str, str], bool],
    *,
    now: datetime | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    """Run one bounded notifier cycle with no DB resources held during I/O."""
    current_time = now or _utc_now()
    should_stop = stop_requested or (lambda: False)
    with session_factory() as db:
        finalize_stale_dispatched_notifications(db, now=current_time)
    if should_stop():
        return False
    with session_factory() as db:
        reserve_final_notifications(db, now=current_time)
    if should_stop():
        return False
    with session_factory() as db:
        intent = claim_reserved_notification(db, now=current_time)
    if intent is None:
        return False
    if should_stop():
        return False

    try:
        acknowledged = sender(
            intent.phone_number,
            intent.message,
            intent.outbound_message_id,
        )
    except Exception:
        acknowledged = False

    with session_factory() as db:
        finalize_notification(
            db,
            intent,
            acknowledged=acknowledged,
            now=current_time,
        )
    return True
