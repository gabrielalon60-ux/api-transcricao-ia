from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import sqlalchemy as sa

from db.models import (
    EnterpriseCommandSession,
    ProcessingItem,
    Execution,
    UserAnswer,
    UserInteraction,
)
from orchestrator.repositories.queue_repository import (
    lock_or_create_conversation_counter,
)
from orchestrator.services.business_rules_evaluator import (
    BusinessRulesEvaluatorService,
    QUESTION_PRIORITY,
)

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

FIFO_TERMINAL_STATES = TERMINAL_STATES + ("IGNORED",)

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
            ProcessingItem.status.not_in(FIFO_TERMINAL_STATES),
        )
        .first()
    )
    if has_earlier_non_terminal:
        return True

    return False


def claim_next_ready_item(
    db: Session, worker_id: str = "worker-1"
) -> Optional[ProcessingItem]:
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
    blocking_subquery = (
        sa.select(BlockingItem.id)
        .where(
            BlockingItem.organization_id == ProcessingItem.organization_id,
            BlockingItem.instance_id == ProcessingItem.instance_id,
            BlockingItem.user_id == ProcessingItem.user_id,
            BlockingItem.status.in_(BLOCKING_STATES),
        )
        .exists()
    )

    OpenCommand = sa.orm.aliased(EnterpriseCommandSession)
    command_barrier = (
        sa.select(OpenCommand.id)
        .where(
            OpenCommand.organization_id == ProcessingItem.organization_id,
            OpenCommand.instance_id == ProcessingItem.instance_id,
            OpenCommand.user_id == ProcessingItem.user_id,
            OpenCommand.status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
        )
        .exists()
    )

    # Subquery 2: Check for earlier non-terminal sequence items in same conversation
    EarlierItem = sa.orm.aliased(ProcessingItem)
    earlier_subquery = (
        sa.select(EarlierItem.id)
        .where(
            EarlierItem.organization_id == ProcessingItem.organization_id,
            EarlierItem.instance_id == ProcessingItem.instance_id,
            EarlierItem.user_id == ProcessingItem.user_id,
            EarlierItem.sequence < ProcessingItem.sequence,
            EarlierItem.status.not_in(FIFO_TERMINAL_STATES),
        )
        .exists()
    )

    # Combined candidate query pushing all eligibility rules into SQL
    candidate = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "READY",
            ProcessingItem.sequence.isnot(None),
            ~blocking_subquery,
            ~command_barrier,
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
    assert candidate.sequence is not None

    lock_or_create_conversation_counter(
        db, candidate.organization_id, candidate.instance_id, candidate.user_id
    )
    if (
        db.query(EnterpriseCommandSession.id)
        .filter(
            EnterpriseCommandSession.organization_id == candidate.organization_id,
            EnterpriseCommandSession.instance_id == candidate.instance_id,
            EnterpriseCommandSession.user_id == candidate.user_id,
            EnterpriseCommandSession.status.in_(
                ["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]
            ),
        )
        .first()
    ):
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
        constraint_name = getattr(diag, "constraint_name", None) or getattr(
            orig, "constraint_name", None
        )
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


def transition_active_to_validating(
    db: Session, item_id: str, worker_id: str
) -> Optional[ProcessingItem]:
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
        logger.warning(
            f"Failed ACTIVE -> VALIDATING transition for item {item_id} by worker {full_worker_id} (mismatched claim, invalid status, or expired lease)"
        )
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


class Gate6DecisionConflict(RuntimeError):
    """Raised when durable answer provenance disagrees with the materialized fact."""


@dataclass(frozen=True)
class EffectiveFinancialDecision:
    direction: str
    amount: Optional[Decimal]
    transaction_date: datetime
    document_date_str: Optional[str]
    date_source: str
    question_type: Optional[str]
    clarification_reason: Optional[str]
    is_eligible_for_auto_write: bool


_NORMALIZED_FIELD_MAP: dict[str, tuple[str, str, str, str]] = {
    "pix_receipt": (
        "amount",
        "transaction_date",
        "sender_cpf_cnpj",
        "receiver_cpf_cnpj",
    ),
    "bank_receipt": ("amount", "payment_date", "payer_cpf_cnpj", "recipient_cpf_cnpj"),
    "invoice": (
        "total_amount",
        "invoice_date",
        "customer_cpf_cnpj",
        "supplier_cpf_cnpj",
    ),
    "commercial_document": (
        "total_amount",
        "document_date",
        "customer_cpf_cnpj",
        "supplier_cpf_cnpj",
    ),
}


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def adapt_gate3_normalized_data(item: ProcessingItem) -> dict[str, Any]:
    """Maps the four approved Gate 3 schemas to the frozen Gate 5 inputs."""
    normalized = item.normalized_data or {}
    fields = _NORMALIZED_FIELD_MAP.get(item.document_type or "")
    if fields is None:
        return {
            "amount": None,
            "document_date": None,
            "payer_identifier": None,
            "receiver_identifier": None,
        }
    amount_key, date_key, payer_key, receiver_key = fields
    return {
        "amount": _to_decimal(normalized.get(amount_key)),
        "document_date": normalized.get(date_key),
        "payer_identifier": normalized.get(payer_key),
        "receiver_identifier": normalized.get(receiver_key),
    }


def _latest_applied_answers(db: Session, item_id: str) -> dict[str, UserAnswer]:
    rows = (
        db.query(UserAnswer, UserInteraction)
        .join(UserInteraction, UserInteraction.id == UserAnswer.interaction_id)
        .filter(
            UserAnswer.processing_item_id == item_id,
            UserAnswer.status == "APPLIED",
            UserInteraction.status == "ANSWERED",
        )
        .order_by(
            UserInteraction.generation.desc(),
            UserAnswer.applied_at.desc(),
            UserAnswer.created_at.desc(),
        )
        .all()
    )
    latest: dict[str, UserAnswer] = {}
    for answer, interaction in rows:
        latest.setdefault(interaction.question_type, answer)
    return latest


def _answer_value(answer: UserAnswer) -> Optional[str]:
    result = answer.parsing_result or {}
    value = result.get("value")
    return str(value) if value is not None else None


def _validated_human_overrides(
    answers: dict[str, UserAnswer],
    item: ProcessingItem,
) -> tuple[Optional[str], Optional[Decimal]]:
    direction: Optional[str] = None
    amount: Optional[Decimal] = None

    direction_answer = answers.get("transaction_direction")
    if direction_answer is not None:
        parsed_direction = _answer_value(direction_answer)
        if (
            parsed_direction not in {"income", "expense"}
            or item.direction != parsed_direction
        ):
            raise Gate6DecisionConflict(
                "APPLIED direction answer diverges from ProcessingItem.direction"
            )
        direction = parsed_direction

    amount_answer = answers.get("transaction_amount")
    if amount_answer is not None:
        parsed_amount = _to_decimal(_answer_value(amount_answer))
        item_amount = _to_decimal(item.amount)
        if parsed_amount is None or item_amount is None or parsed_amount != item_amount:
            raise Gate6DecisionConflict(
                "APPLIED amount answer diverges from ProcessingItem.amount"
            )
        amount = item_amount

    return direction, amount


def evaluate_and_persist_validating_item(
    db: Session,
    item_id: str,
    worker_id: str,
    evaluator: BusinessRulesEvaluatorService,
) -> tuple[ProcessingItem, EffectiveFinancialDecision]:
    """Evaluates a worker-owned VALIDATING item and persists only effective financial facts."""
    full_worker_id = _normalize_worker_id(worker_id)
    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "VALIDATING",
            ProcessingItem.claimed_by == full_worker_id,
            ProcessingItem.lease_expires_at > sa.func.now(),
        )
        .with_for_update()
        .first()
    )
    if item is None:
        raise Gate6DecisionConflict(f"Item {item_id} is not owned VALIDATING work")

    adapted = adapt_gate3_normalized_data(item)
    answers = _latest_applied_answers(db, item.id)
    confirmed_direction, confirmed_amount = _validated_human_overrides(answers, item)

    existing_amount = _to_decimal(item.amount)
    amount_input = confirmed_amount
    if amount_input is None and existing_amount is not None and existing_amount > 0:
        amount_input = existing_amount
    if amount_input is None:
        amount_input = adapted["amount"]

    raw_result = evaluator.evaluate(
        amount=amount_input,
        document_date=adapted["document_date"],
        message_received_at=item.message_received_at,
        payer_identifier=adapted["payer_identifier"],
        receiver_identifier=adapted["receiver_identifier"],
    )

    existing_direction = (
        item.direction if item.direction in {"income", "expense"} else None
    )
    effective_direction = (
        confirmed_direction or existing_direction or raw_result.direction
    )
    effective_amount = raw_result.amount

    unresolved: dict[str, str] = {}
    if effective_direction not in {"income", "expense"}:
        unresolved["transaction_direction"] = (
            "AMBIGUOUS_DIRECTION"
            if effective_direction == "ambiguous"
            else "UNKNOWN_DIRECTION"
        )
    # Gate 7 expense-only guard: once income is known, no destination-only
    # facts (amount/enterprise) may be requested.
    if effective_direction != "income" and (
        effective_amount is None or effective_amount <= 0
    ):
        unresolved["transaction_amount"] = (
            "MISSING_AMOUNT" if effective_amount is None else "INVALID_AMOUNT"
        )

    question_type = next(
        (name for name in QUESTION_PRIORITY if name in unresolved), None
    )
    clarification_reason = unresolved.get(question_type) if question_type else None
    eligible = (
        question_type is None
        and effective_direction == "expense"
        and bool(effective_amount is not None and effective_amount > 0)
    )

    decision = EffectiveFinancialDecision(
        direction=effective_direction,
        amount=effective_amount,
        transaction_date=raw_result.transaction_date,
        document_date_str=raw_result.document_date_str,
        date_source=raw_result.date_source,
        question_type=question_type,
        clarification_reason=clarification_reason,
        is_eligible_for_auto_write=eligible,
    )

    item.amount = decision.amount  # type: ignore[assignment]
    item.document_date = decision.document_date_str
    item.transaction_date = decision.transaction_date
    item.date_source = decision.date_source
    item.direction = decision.direction
    db.commit()
    db.refresh(item)
    return item, decision


def ignore_income_out_of_scope(
    db: Session,
    item_id: str,
    worker_id: str,
) -> Optional[ProcessingItem]:
    """Atomically closes an owned VALIDATING income item as a non-error outcome."""
    full_worker_id = _normalize_worker_id(worker_id)
    existing = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "IGNORED",
            ProcessingItem.outcome_reason == "INCOME_OUT_OF_SCOPE",
        )
        .first()
    )
    if existing is not None:
        return existing

    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "VALIDATING",
            ProcessingItem.direction == "income",
            ProcessingItem.claimed_by == full_worker_id,
            ProcessingItem.lease_expires_at > sa.func.now(),
        )
        .with_for_update()
        .first()
    )
    if item is None:
        return None

    now = datetime.now(timezone.utc)
    item.status = "IGNORED"
    item.outcome_reason = "INCOME_OUT_OF_SCOPE"
    item.completed_at = now
    item.claimed_by = None
    item.heartbeat_at = None
    item.lease_expires_at = None

    checkpoint_key = f"{item.id}:INCOME_OUT_OF_SCOPE"
    if (
        not db.query(Execution.id)
        .filter(Execution.operation_idempotency_key == checkpoint_key)
        .first()
    ):
        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                component="BOT_DF",
                operation="INCOME_OUT_OF_SCOPE",
                operation_idempotency_key=checkpoint_key,
                status="SUCCESS",
                attempt=max(1, item.attempt_count),
                started_at=now,
                completed_at=now,
            )
        )
    db.commit()
    db.refresh(item)
    return item


def defer_validating_for_enterprise_command(
    db: Session, item_id: str, worker_id: str
) -> Optional[ProcessingItem]:
    """Releases VALIDATING ownership when a command wins the prompt-open race."""
    full_worker_id = _normalize_worker_id(worker_id)
    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.id == item_id,
            ProcessingItem.status == "VALIDATING",
            ProcessingItem.claimed_by == full_worker_id,
        )
        .with_for_update()
        .first()
    )
    if item is None:
        return None
    item.claimed_by = None
    item.heartbeat_at = None
    item.lease_expires_at = None
    key = f"{item.id}:ENTERPRISE_COMMAND_BARRIER_DEFERRED"
    if (
        not db.query(Execution.id)
        .filter(Execution.operation_idempotency_key == key)
        .first()
    ):
        now = datetime.now(timezone.utc)
        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                component="BOT_DF",
                operation="ENTERPRISE_COMMAND_BARRIER_DEFERRED",
                operation_idempotency_key=key,
                status="SUCCESS",
                attempt=max(1, item.attempt_count),
                started_at=now,
                completed_at=now,
            )
        )
    db.commit()
    db.refresh(item)
    return item


def claim_next_resumable_validating_item(
    db: Session,
    worker_id: str = "worker-1",
) -> Optional[ProcessingItem]:
    """Atomically claims unowned VALIDATING work backed by Gate 6 resume provenance."""
    if not isinstance(db, Session):
        return None
    full_worker_id = _normalize_worker_id(worker_id)
    OpenInteraction = sa.orm.aliased(UserInteraction)
    AnsweredInteraction = sa.orm.aliased(UserInteraction)
    AppliedAnswer = sa.orm.aliased(UserAnswer)
    ReservedInteraction = sa.orm.aliased(UserInteraction)
    DispatchExecution = sa.orm.aliased(Execution)
    BarrierExecution = sa.orm.aliased(Execution)
    EarlierItem = sa.orm.aliased(ProcessingItem)

    open_waiting = (
        sa.select(OpenInteraction.id)
        .where(
            OpenInteraction.processing_item_id == ProcessingItem.id,
            OpenInteraction.status.in_(["WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
        )
        .exists()
    )
    answered_applied = (
        sa.select(AppliedAnswer.id)
        .join(
            AnsweredInteraction,
            AnsweredInteraction.id == AppliedAnswer.interaction_id,
        )
        .where(
            AnsweredInteraction.processing_item_id == ProcessingItem.id,
            AnsweredInteraction.status == "ANSWERED",
            AppliedAnswer.status == "APPLIED",
        )
        .exists()
    )
    matching_dispatch = (
        sa.select(DispatchExecution.id)
        .where(
            DispatchExecution.processing_item_id == ProcessingItem.id,
            DispatchExecution.operation == "USER_PROMPT_DISPATCHED",
            DispatchExecution.outbound_message_id
            == ReservedInteraction.outbound_message_id,
        )
        .exists()
    )
    recoverable_reserved = (
        sa.select(ReservedInteraction.id)
        .where(
            ReservedInteraction.processing_item_id == ProcessingItem.id,
            ReservedInteraction.status == "RESERVED",
            ~matching_dispatch,
        )
        .exists()
    )
    command_barrier_deferred = (
        sa.select(BarrierExecution.id)
        .where(
            BarrierExecution.processing_item_id == ProcessingItem.id,
            BarrierExecution.operation == "ENTERPRISE_COMMAND_BARRIER_DEFERRED",
        )
        .exists()
    )
    OpenCommand = sa.orm.aliased(EnterpriseCommandSession)
    open_command = (
        sa.select(OpenCommand.id)
        .where(
            OpenCommand.organization_id == ProcessingItem.organization_id,
            OpenCommand.instance_id == ProcessingItem.instance_id,
            OpenCommand.user_id == ProcessingItem.user_id,
            OpenCommand.status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
        )
        .exists()
    )
    earlier_non_terminal = (
        sa.select(EarlierItem.id)
        .where(
            EarlierItem.organization_id == ProcessingItem.organization_id,
            EarlierItem.instance_id == ProcessingItem.instance_id,
            EarlierItem.user_id == ProcessingItem.user_id,
            EarlierItem.sequence < ProcessingItem.sequence,
            EarlierItem.status.not_in(FIFO_TERMINAL_STATES),
        )
        .exists()
    )

    candidate = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.status == "VALIDATING",
            ProcessingItem.claimed_by.is_(None),
            ProcessingItem.lease_expires_at.is_(None),
            ProcessingItem.heartbeat_at.is_(None),
            ~open_waiting,
            sa.or_(answered_applied, recoverable_reserved, command_barrier_deferred),
            ~open_command,
            ~earlier_non_terminal,
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
    if candidate is None:
        return None

    now = datetime.now(timezone.utc)
    candidate.claimed_by = full_worker_id
    candidate.heartbeat_at = now
    candidate.lease_expires_at = sa.func.now() + sa.text("INTERVAL '60 seconds'")
    db.commit()
    db.refresh(candidate)
    return candidate
