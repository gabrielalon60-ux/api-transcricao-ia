"""Durable WhatsApp summaries for classification-only outcomes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from db.models import Execution, ProcessingItem, User
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from orchestrator.services.business_rules_evaluator import BUSINESS_TIMEZONE


NOTIFICATION_TYPE: Final = "CLASSIFICATION_VALIDATED"
RESERVED_OPERATION: Final = "CLASSIFICATION_NOTIFICATION_RESERVED"
DISPATCHED_OPERATION: Final = "CLASSIFICATION_NOTIFICATION_DISPATCHED"
ACKNOWLEDGED_OPERATION: Final = "CLASSIFICATION_NOTIFICATION_ACKNOWLEDGED"
UNKNOWN_OPERATION: Final = "CLASSIFICATION_NOTIFICATION_OUTCOME_UNKNOWN"
POLL_INTERVAL_SECONDS: Final = 1.0


@dataclass(frozen=True)
class ClassificationNotificationIntent:
    processing_item_id: str
    phone_number: str
    message: str
    outbound_message_id: str
    dispatch_execution_id: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _reservation_key(item_id: str) -> str:
    return f"{item_id}:{RESERVED_OPERATION}:{NOTIFICATION_TYPE}"


def _dispatch_key(item_id: str) -> str:
    return f"{item_id}:{DISPATCHED_OPERATION}:{NOTIFICATION_TYPE}"


def _final_key(item_id: str) -> str:
    return f"{item_id}:CLASSIFICATION_NOTIFICATION_FINAL:{NOTIFICATION_TYPE}"


def _outbound_message_id(item_id: str) -> str:
    return f"classification_{item_id}"


def format_classification_summary(item: ProcessingItem) -> str:
    """Format a non-persistence summary from durable validated facts."""
    if item.status != "VALIDATED":
        raise ValueError("Classification summary requires VALIDATED status")
    if item.direction not in {"income", "expense"}:
        raise ValueError("Classification summary requires a resolved direction")
    if item.amount is None or Decimal(item.amount) <= 0:
        raise ValueError("Classification summary requires a positive amount")
    if item.transaction_date is None:
        raise ValueError("Classification summary requires a transaction date")
    if not (item.enterprise_display_name or "").strip():
        raise ValueError("Classification summary requires an enterprise display name")

    movement = "Pagamento" if item.direction == "expense" else "Recebimento"
    amount = Decimal(item.amount)
    amount_text = (
        f"R$ {amount:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )
    if item.date_source == "DOCUMENT" and item.document_date:
        display_date = date.fromisoformat(item.document_date)
    else:
        display_date = item.transaction_date.astimezone(BUSINESS_TIMEZONE).date()
    date_text = display_date.strftime("%d/%m/%Y")
    return (
        "✅ Documento processado com sucesso.\n\n"
        f"Empreendimento: {item.enterprise_display_name.strip()}\n"
        f"Movimentação: {movement}\n"
        f"Valor: {amount_text}\n"
        f"Data: {date_text}\n\n"
        "Os dados foram identificados, mas o lançamento ainda não foi gravado."
    )


def reserve_classification_notification(
    db: Session, *, now: datetime | None = None
) -> bool:
    prior = aliased(Execution)
    item = db.scalar(
        select(ProcessingItem)
        .where(
            ProcessingItem.status == "VALIDATED",
            ~exists().where(
                prior.processing_item_id == ProcessingItem.id,
                prior.operation.in_(
                    (RESERVED_OPERATION, ACKNOWLEDGED_OPERATION, UNKNOWN_OPERATION)
                ),
            ),
        )
        .order_by(ProcessingItem.updated_at.asc(), ProcessingItem.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if item is None:
        db.rollback()
        return False

    current_time = now or _utc_now()
    try:
        with db.begin_nested():
            db.add(
                Execution(
                    processing_item_id=item.id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation=RESERVED_OPERATION,
                    operation_idempotency_key=_reservation_key(item.id),
                    status="SUCCESS",
                    attempt=1,
                    started_at=current_time,
                    completed_at=current_time,
                )
            )
            db.flush()
    except IntegrityError:
        db.rollback()
        return False
    db.commit()
    return True


def claim_classification_notification(
    db: Session, *, now: datetime | None = None
) -> ClassificationNotificationIntent | None:
    prior_dispatch = aliased(Execution)
    prior_final = aliased(Execution)
    reservation = db.scalar(
        select(Execution)
        .where(
            Execution.operation == RESERVED_OPERATION,
            ~exists().where(
                prior_dispatch.processing_item_id == Execution.processing_item_id,
                prior_dispatch.operation == DISPATCHED_OPERATION,
            ),
            ~exists().where(
                prior_final.processing_item_id == Execution.processing_item_id,
                prior_final.operation.in_(
                    (ACKNOWLEDGED_OPERATION, UNKNOWN_OPERATION)
                ),
            ),
        )
        .order_by(Execution.created_at.asc(), Execution.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if reservation is None or reservation.processing_item_id is None:
        db.rollback()
        return None

    item = db.get(ProcessingItem, reservation.processing_item_id)
    if item is None or item.status != "VALIDATED":
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
        component="BOT_DF",
        operation=DISPATCHED_OPERATION,
        operation_idempotency_key=_dispatch_key(item.id),
        outbound_message_id=_outbound_message_id(item.id),
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
    intent = ClassificationNotificationIntent(
        processing_item_id=item.id,
        phone_number=user.phone_number,
        message=format_classification_summary(item),
        outbound_message_id=dispatch.outbound_message_id or "",
        dispatch_execution_id=dispatch.id,
    )
    db.commit()
    return intent


def finalize_classification_notification(
    db: Session,
    intent: ClassificationNotificationIntent,
    *,
    acknowledged: bool,
    now: datetime | None = None,
) -> bool:
    if db.scalar(
        select(exists().where(Execution.operation_idempotency_key == _final_key(
            intent.processing_item_id
        )))
    ):
        db.rollback()
        return False
    item = db.get(ProcessingItem, intent.processing_item_id)
    if item is None:
        db.rollback()
        return False
    current_time = now or _utc_now()
    try:
        with db.begin_nested():
            db.add(
                Execution(
                    processing_item_id=intent.processing_item_id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation=(
                        ACKNOWLEDGED_OPERATION if acknowledged else UNKNOWN_OPERATION
                    ),
                    operation_idempotency_key=_final_key(
                        intent.processing_item_id
                    ),
                    external_reference=intent.outbound_message_id,
                    status="SUCCESS" if acknowledged else "FAILED",
                    effect_status=(
                        "ACKNOWLEDGED"
                        if acknowledged
                        else "OUTBOUND_OUTCOME_UNKNOWN"
                    ),
                    attempt=1,
                    started_at=current_time,
                    completed_at=current_time,
                )
            )
            db.flush()
    except IntegrityError:
        db.rollback()
        return False
    db.commit()
    return True


def run_classification_notification_iteration(
    session_factory: Callable[[], Session],
    sender: Callable[[str, str, str], bool],
) -> bool:
    with session_factory() as db:
        reserve_classification_notification(db)
    with session_factory() as db:
        intent = claim_classification_notification(db)
    if intent is None:
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
        finalize_classification_notification(
            db,
            intent,
            acknowledged=acknowledged,
        )
    return True
