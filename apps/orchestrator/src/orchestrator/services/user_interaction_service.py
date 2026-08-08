from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple, Callable, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import sqlalchemy as sa

from db.models import ProcessingItem, Execution, UserInteraction, UserAnswer, Event

logger = logging.getLogger(__name__)

WAITING_USER_INPUT_TTL_SECONDS = 3600

# Closed vocabulary of question types
VALID_QUESTION_TYPES = {
    "transaction_direction",
    "transaction_amount",
    "document_classification",
}


# --- Question Parsers ---

def parse_direction_answer(text: str) -> Optional[str]:
    """Parses transaction direction answer into 'income' or 'expense'."""
    normalized = text.strip().lower()
    if normalized in {"1", "entrada", "receita", "income", "credito", "crédito"}:
        return "income"
    if normalized in {"2", "saida", "saída", "despesa", "expense", "debito", "débito"}:
        return "expense"
    return None


def parse_amount_answer(text: str) -> Optional[Decimal]:
    """Parses transaction amount answer into Decimal."""
    cleaned = text.strip()
    if "-" in cleaned:
        return None
    match = re.search(r"(?:R\$\s*)?(\d+(?:[.,]\d{1,2})?)", cleaned, re.IGNORECASE)
    if not match:
        return None
    val_str = match.group(1).replace(",", ".")
    try:
        val = Decimal(val_str)
        if val <= 0:
            return None
        return val.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None


def parse_document_classification_answer(text: str) -> Optional[str]:
    """Parses document classification answer into supported label."""
    normalized = text.strip().lower()
    if normalized in {"1", "pix", "comprovante pix", "pix_receipt"}:
        return "pix_receipt"
    if normalized in {"2", "boleto", "bank_receipt"}:
        return "bank_receipt"
    if normalized in {"3", "nota", "nota fiscal", "invoice"}:
        return "invoice"
    if normalized in {"4", "outro", "pedido", "comprovante comercial", "commercial_document"}:
        return "commercial_document"
    return None


def parse_answer(question_type: str, text: str) -> Tuple[Optional[Any], Optional[str]]:
    """Parses answer for question_type. Returns (parsed_value, error_code)."""
    if question_type == "transaction_direction":
        parsed = parse_direction_answer(text)
        if parsed:
            return parsed, None
        return None, "INVALID_DIRECTION_CHOICE"

    if question_type == "transaction_amount":
        parsed_amount = parse_amount_answer(text)
        if parsed_amount is not None:
            return parsed_amount, None
        return None, "INVALID_AMOUNT_FORMAT"

    if question_type == "document_classification":
        parsed = parse_document_classification_answer(text)
        if parsed:
            return parsed, None
        return None, "INVALID_CLASSIFICATION_CHOICE"

    return None, "UNSUPPORTED_QUESTION_TYPE"


# --- Validation / Question Selection ---

# Priority order: direction first, then amount, then document_classification
QUESTION_PRIORITY: list[str] = [
    "transaction_direction",
    "transaction_amount",
    "document_classification",
]


def select_question_type(item: "ProcessingItem") -> Optional[str]:
    """Inspects a ProcessingItem in VALIDATING state and returns the highest-priority
    missing required field as a question type, or None if all fields are present.

    Priority order (lowest index = highest priority):
      1. transaction_direction  (item.direction is None)
      2. transaction_amount     (item.amount is None)
      3. document_classification (item.document_type is None)

    Returns:
      - question_type str if a field is missing
      - None if validation is complete
    """
    if item.direction is None:
        return "transaction_direction"
    if item.amount is None:
        return "transaction_amount"
    if item.document_type is None:
        return "document_classification"
    return None


# --- Interaction Generation & Dispatch ---

def create_or_get_open_interaction(
    db: Session,
    processing_item_id: str,
    question_type: str,
) -> UserInteraction:
    """Allocates a new interaction generation using Savepoint strategy to prevent transaction abort on race.

    Returns open UserInteraction.
    """
    if question_type not in VALID_QUESTION_TYPES:
        raise ValueError(f"Invalid question_type {question_type}. Must be one of {VALID_QUESTION_TYPES}")

    # Check for existing open interaction
    existing = (
        db.query(UserInteraction)
        .filter(
            UserInteraction.processing_item_id == processing_item_id,
            UserInteraction.status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
        )
        .first()
    )
    if existing:
        return existing

    # Savepoint strategy for race-safe insert
    savepoint = db.begin_nested()
    try:
        max_gen = (
            db.query(sa.func.max(UserInteraction.generation))
            .filter(UserInteraction.processing_item_id == processing_item_id)
            .scalar()
            or 0
        )
        next_gen = max_gen + 1
        outbound_msg_id = f"msg_{processing_item_id}_{next_gen}_{question_type}"

        interaction = UserInteraction(
            id=str(uuid.uuid4()),
            processing_item_id=processing_item_id,
            generation=next_gen,
            question_type=question_type,
            outbound_message_id=outbound_msg_id,
            status="RESERVED",
        )
        db.add(interaction)
        savepoint.commit()
        return interaction
    except IntegrityError as exc:
        savepoint.rollback()
        # Race lost -> uq_user_interactions_one_open_per_item or uq_user_interactions_item_generation triggered
        existing = (
            db.query(UserInteraction)
            .filter(
                UserInteraction.processing_item_id == processing_item_id,
                UserInteraction.status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
            )
            .first()
        )
        if existing:
            return existing
        logger.error(f"Failed to allocate interaction generation for item {processing_item_id}: {exc}")
        raise exc


def dispatch_user_prompt(
    db: Session,
    item_id: str,
    question_type: str,
    prompt_sender_func: Optional[Callable[[str, str, str], bool]] = None,
) -> UserInteraction:
    """Executes prompt dispatch across 4 explicit transaction boundaries.

    1. Transaction 1: Allocate RESERVED interaction & USER_PROMPT_RESERVED checkpoint.
    2. Transaction 2: Record USER_PROMPT_DISPATCHED checkpoint.
    3. External Action: Call WUZAPI prompt sender outside DB transaction.
    4. Transaction 3: Persist ACKNOWLEDGED or OUTCOME_UNKNOWN and update item to WAITING_USER_INPUT.
    """
    now = datetime.now(timezone.utc)

    # Lock processing item
    item = (
        db.query(ProcessingItem)
        .filter(ProcessingItem.id == item_id)
        .with_for_update()
        .first()
    )
    if not item:
        raise ValueError(f"ProcessingItem {item_id} not found")

    # Boundary 1: Reserve interaction & USER_PROMPT_RESERVED
    interaction = create_or_get_open_interaction(db, item_id, question_type)

    # Check if USER_PROMPT_RESERVED already written (via operation_idempotency_key)
    reserved_idem_key = f"{item_id}:USER_PROMPT_RESERVED:{interaction.outbound_message_id}"
    reserved_exec = (
        db.query(Execution)
        .filter(
            Execution.operation_idempotency_key == reserved_idem_key,
        )
        .first()
    )
    if not reserved_exec:
        sp_reserved = db.begin_nested()
        try:
            db.add(
                Execution(
                    processing_item_id=item.id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="USER_PROMPT_RESERVED",
                    external_reference=interaction.outbound_message_id,
                    operation_idempotency_key=reserved_idem_key,
                    status="SUCCESS",
                    attempt=item.attempt_count,
                    started_at=now,
                    completed_at=now,
                )
            )
            sp_reserved.commit()
            db.commit()
        except IntegrityError:
            sp_reserved.rollback()
            db.rollback()
            logger.info(f"USER_PROMPT_RESERVED already committed for interaction {interaction.id} (duplicate reservation).")
        db.refresh(interaction)

    # Boundary 2: Record USER_PROMPT_DISPATCHED atomically using row lock & savepoint guard
    # Lock interaction row with FOR UPDATE SKIP LOCKED to prevent concurrent FK deadlocks
    interaction_locked = (
        db.query(UserInteraction)
        .filter(UserInteraction.id == interaction.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not interaction_locked:
        logger.info(f"Worker lost concurrent prompt dispatch lock for interaction {interaction.id}.")
        return interaction

    dispatched_exec = (
        db.query(Execution)
        .filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "USER_PROMPT_DISPATCHED",
            Execution.outbound_message_id == interaction.outbound_message_id,
        )
        .first()
    )
    is_dispatch_owner = False
    dispatched_idem_key = f"{item_id}:USER_PROMPT_DISPATCHED:{interaction.outbound_message_id}"
    if not dispatched_exec:
        sp = db.begin_nested()
        try:
            db.add(
                Execution(
                    processing_item_id=item.id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="USER_PROMPT_DISPATCHED",
                    outbound_message_id=interaction.outbound_message_id,
                    operation_idempotency_key=dispatched_idem_key,
                    status="SUCCESS",
                    attempt=item.attempt_count,
                    started_at=now,
                    completed_at=now,
                )
            )
            sp.commit()
            db.commit()
            is_dispatch_owner = True
        except IntegrityError:
            sp.rollback()
            db.rollback()
            logger.info(f"Worker lost concurrent prompt dispatch race for interaction {interaction.id}.")
            is_dispatch_owner = False
    else:
        is_dispatch_owner = False

    if not is_dispatch_owner:
        # Losing worker performs zero WUZAPI calls and zero additional checkpoints
        return interaction

    # Boundary 3: External WUZAPI call outside DB transaction
    dispatch_ok = False
    if prompt_sender_func:
        try:
            dispatch_ok = prompt_sender_func(item.id, interaction.question_type, interaction.outbound_message_id)
        except Exception as exc:
            logger.warning(f"Outbound WUZAPI prompt sender raised exception for item {item_id}: {exc}")
            dispatch_ok = False
    else:
        # Default mock / test sender: treats as ACKNOWLEDGED
        dispatch_ok = True

    # Boundary 4: Persist result with guarded state machine (ACKNOWLEDGED or OUTBOUND_OUTCOME_UNKNOWN)
    # Lock interaction and item for update
    item = db.query(ProcessingItem).filter(ProcessingItem.id == item_id).with_for_update().first()
    interaction_locked = db.query(UserInteraction).filter(UserInteraction.id == interaction.id).with_for_update().first()
    now_post = datetime.now(timezone.utc)
    expires = now_post + timedelta(seconds=WAITING_USER_INPUT_TTL_SECONDS)

    if not item or not interaction_locked:
        logger.error(f"Item {item_id} or interaction lost before Boundary 4 finalization.")
        return interaction

    # State Guard: result finalization is ONLY allowed when interaction is in 'RESERVED' state
    if interaction_locked.status != "RESERVED":
        logger.info(
            f"Dispatch result finalization skipped for interaction {interaction_locked.id} "
            f"(current status={interaction_locked.status}, expected RESERVED)."
        )
        return interaction_locked

    if dispatch_ok:
        interaction_locked.status = "WAITING"
        interaction_locked.waiting_since = now_post
        interaction_locked.expires_at = expires

        item.status = "WAITING_USER_INPUT"
        item.question_type = question_type
        item.waiting_since = now_post
        item.expires_at = expires
        item.claimed_by = None
        item.heartbeat_at = None
        item.lease_expires_at = None

        ack_idem_key = f"{item.id}:USER_PROMPT_ACKNOWLEDGED:{interaction_locked.outbound_message_id}"
        sp_ack = db.begin_nested()
        try:
            db.add(
                Execution(
                    processing_item_id=item.id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="USER_PROMPT_ACKNOWLEDGED",
                    external_reference=interaction_locked.outbound_message_id,
                    operation_idempotency_key=ack_idem_key,
                    status="SUCCESS",
                    effect_status="ACKNOWLEDGED",
                    attempt=item.attempt_count,
                    started_at=now_post,
                    completed_at=now_post,
                )
            )
            sp_ack.commit()
        except IntegrityError:
            sp_ack.rollback()
            logger.info(f"USER_PROMPT_ACKNOWLEDGED already written for interaction {interaction_locked.id}.")
    else:
        interaction_locked.status = "OUTBOUND_OUTCOME_UNKNOWN"
        interaction_locked.waiting_since = now_post
        interaction_locked.expires_at = expires

        item.status = "WAITING_USER_INPUT"
        item.question_type = question_type
        item.waiting_since = now_post
        item.expires_at = expires
        item.claimed_by = None
        item.heartbeat_at = None
        item.lease_expires_at = None

        unk_idem_key = f"{item.id}:USER_PROMPT_OUTCOME_UNKNOWN:{interaction_locked.outbound_message_id}"
        sp_unk = db.begin_nested()
        try:
            db.add(
                Execution(
                    processing_item_id=item.id,
                    event_id=item.event_id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="USER_PROMPT_OUTCOME_UNKNOWN",
                    external_reference=interaction_locked.outbound_message_id,
                    operation_idempotency_key=unk_idem_key,
                    status="FAILED",
                    effect_status="OUTBOUND_OUTCOME_UNKNOWN",
                    attempt=item.attempt_count,
                    started_at=now_post,
                    completed_at=now_post,
                    error_code="OUTBOUND_OUTCOME_UNKNOWN",
                    error_message_sanitized="WUZAPI prompt dispatch outcome unconfirmed",
                )
            )
            sp_unk.commit()
        except IntegrityError:
            sp_unk.rollback()
            logger.info(f"USER_PROMPT_OUTCOME_UNKNOWN already written for interaction {interaction_locked.id}.")

    db.commit()
    db.refresh(interaction_locked)
    return interaction_locked


# --- Answer Insertion & Application ---

def apply_user_answer(
    db: Session,
    inbound_event_id: str,
    raw_answer_text: str,
) -> UserAnswer:
    """Atomically registers and applies an inbound user answer event.

    Idempotency:
      - Uses ON CONFLICT (inbound_event_id) DO NOTHING to safely handle duplicate webhook deliveries.
      - If conflict occurs, reads and returns the existing committed UserAnswer record without re-applying fields or creating checkpoints.
    """
    now = datetime.now(timezone.utc)

    # 1. Fetch Event to resolve conversation identity
    evt = db.query(Event).filter(Event.id == inbound_event_id).first()
    if not evt:
        raise ValueError(f"Event {inbound_event_id} not found")

    # 2. Check for existing answer (idempotency read)
    existing_answer = db.query(UserAnswer).filter(UserAnswer.inbound_event_id == inbound_event_id).first()
    if existing_answer:
        logger.info(f"Duplicate answer event {inbound_event_id} detected. Returning committed outcome.")
        return existing_answer

    # 3. Lock active WAITING_USER_INPUT item matching tenant/user tuple
    item = (
        db.query(ProcessingItem)
        .filter(
            ProcessingItem.organization_id == evt.organization_id,
            ProcessingItem.instance_id == evt.instance_id,
            ProcessingItem.user_id == evt.user_id,
            ProcessingItem.status == "WAITING_USER_INPUT",
        )
        .with_for_update()
        .first()
    )

    if not item:
        # Find latest item for conversation fallback
        latest_item = (
            db.query(ProcessingItem)
            .filter(
                ProcessingItem.organization_id == evt.organization_id,
                ProcessingItem.instance_id == evt.instance_id,
                ProcessingItem.user_id == evt.user_id,
            )
            .order_by(ProcessingItem.created_at.desc())
            .first()
        )
        if not latest_item:
            logger.info(f"Inbound answer {inbound_event_id} has no matching processing item.")
            return UserAnswer(
                id=str(uuid.uuid4()),
                interaction_id=str(uuid.uuid4()),
                processing_item_id=str(uuid.uuid4()),
                inbound_event_id=inbound_event_id,
                sanitized_answer=raw_answer_text.strip(),
                status="LATE",
                error_code="NO_WAITING_ITEM",
            )

        latest_inter = (
            db.query(UserInteraction)
            .filter(UserInteraction.processing_item_id == latest_item.id)
            .order_by(UserInteraction.created_at.desc())
            .first()
        )

        answer = UserAnswer(
            id=str(uuid.uuid4()),
            interaction_id=latest_inter.id if latest_inter else str(uuid.uuid4()),
            processing_item_id=latest_item.id,
            inbound_event_id=inbound_event_id,
            sanitized_answer=raw_answer_text.strip(),
            status="LATE",
            error_code="NO_WAITING_ITEM",
        )
        sp = db.begin_nested()
        try:
            db.add(answer)
            sp.commit()
        except IntegrityError:
            sp.rollback()
            return db.query(UserAnswer).filter(UserAnswer.inbound_event_id == inbound_event_id).one()

        db.add(
            Execution(
                processing_item_id=latest_item.id,
                event_id=inbound_event_id,
                correlation_id=evt.correlation_id,
                component="BOT_DF",
                operation="USER_ANSWER_REJECTED",
                status="FAILED",
                attempt=1,
                started_at=now,
                completed_at=now,
                error_code="NO_WAITING_ITEM",
                error_message_sanitized="Inbound answer received but no WAITING_USER_INPUT item found",
            )
        )
        db.commit()
        return answer

    # Lock open interaction for item
    interaction = (
        db.query(UserInteraction)
        .filter(
            UserInteraction.processing_item_id == item.id,
            UserInteraction.status.in_(["RESERVED", "WAITING", "OUTBOUND_OUTCOME_UNKNOWN"]),
        )
        .with_for_update()
        .first()
    )

    if not interaction:
        # Item in WAITING_USER_INPUT or closed but interaction closed -> LATE
        latest_inter = (
            db.query(UserInteraction)
            .filter(UserInteraction.processing_item_id == item.id)
            .order_by(UserInteraction.created_at.desc())
            .first()
        )

        answer = UserAnswer(
            id=str(uuid.uuid4()),
            interaction_id=latest_inter.id if latest_inter else str(uuid.uuid4()),
            processing_item_id=item.id,
            inbound_event_id=inbound_event_id,
            sanitized_answer=raw_answer_text.strip(),
            status="LATE",
            error_code="INTERACTION_ALREADY_CLOSED",
        )
        sp = db.begin_nested()
        try:
            db.add(answer)
            sp.commit()
        except IntegrityError:
            sp.rollback()
            return db.query(UserAnswer).filter(UserAnswer.inbound_event_id == inbound_event_id).one()

        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=inbound_event_id,
                correlation_id=evt.correlation_id,
                component="BOT_DF",
                operation="USER_ANSWER_REJECTED",
                status="FAILED",
                attempt=item.attempt_count,
                started_at=now,
                completed_at=now,
                error_code="INTERACTION_ALREADY_CLOSED",
                error_message_sanitized="Inbound answer received for already closed interaction",
            )
        )
        db.commit()
        return answer

    # Relational invariant: resolve item authoritatively through interaction.processing_item_id
    # rather than trusting caller-supplied identity. Validates tenant/conversation identity.
    if interaction.processing_item_id != item.id:
        raise ValueError(
            f"Relational invariant violation: interaction.processing_item_id ({interaction.processing_item_id}) "
            f"does not match item.id ({item.id})"
        )

    # Cross-tenant / cross-conversation guard: interaction must share org/instance/user with item
    inter_item = (
        db.query(ProcessingItem)
        .filter(ProcessingItem.id == interaction.processing_item_id)
        .first()
    )
    if inter_item and (
        inter_item.organization_id != item.organization_id
        or inter_item.instance_id != item.instance_id
        or inter_item.user_id != item.user_id
    ):
        raise ValueError(
            f"Relational invariant violation: interaction belongs to different tenant/conversation "
            f"(org={inter_item.organization_id}/{item.organization_id}, "
            f"inst={inter_item.instance_id}/{item.instance_id}, "
            f"user={inter_item.user_id}/{item.user_id})"
        )

    # 4. Parse answer
    parsed_value, error_code = parse_answer(interaction.question_type, raw_answer_text)

    # Atomic insert of UserAnswer row
    answer = UserAnswer(
        id=str(uuid.uuid4()),
        interaction_id=interaction.id,
        processing_item_id=item.id,
        inbound_event_id=inbound_event_id,
        sanitized_answer=raw_answer_text.strip(),
        parsing_result={"value": str(parsed_value)} if parsed_value is not None else None,
        status="APPLIED" if parsed_value is not None else "REJECTED",
        error_code=error_code,
        applied_at=now if parsed_value is not None else None,
    )

    sp = db.begin_nested()
    try:
        db.add(answer)
        sp.commit()
    except IntegrityError:
        sp.rollback()
        # Duplicate delivery caught by UNIQUE(inbound_event_id)
        return db.query(UserAnswer).filter(UserAnswer.inbound_event_id == inbound_event_id).one()

    # 5. Apply or Reject outcome
    if parsed_value is not None:
        # Apply to target field on processing item
        if interaction.question_type == "transaction_direction":
            item.direction = str(parsed_value)
        elif interaction.question_type == "transaction_amount":
            item.amount = parsed_value
        elif interaction.question_type == "document_classification":
            item.document_type = str(parsed_value)

        item.status = "VALIDATING"
        item.waiting_since = None
        item.expires_at = None

        interaction.status = "ANSWERED"
        interaction.resolved_at = now

        applied_idem_key = f"{item.id}:USER_ANSWER_APPLIED:{inbound_event_id}"
        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=inbound_event_id,
                correlation_id=evt.correlation_id,
                component="BOT_DF",
                operation="USER_ANSWER_APPLIED",
                external_reference=interaction.outbound_message_id,
                operation_idempotency_key=applied_idem_key,
                status="SUCCESS",
                attempt=item.attempt_count,
                started_at=now,
                completed_at=now,
            )
        )
    else:
        # Invalid answer -> item remains WAITING_USER_INPUT
        rejected_idem_key = f"{item.id}:USER_ANSWER_REJECTED:{inbound_event_id}"
        db.add(
            Execution(
                processing_item_id=item.id,
                event_id=inbound_event_id,
                correlation_id=evt.correlation_id,
                component="BOT_DF",
                operation="USER_ANSWER_REJECTED",
                external_reference=interaction.outbound_message_id,
                operation_idempotency_key=rejected_idem_key,
                status="FAILED",
                attempt=item.attempt_count,
                started_at=now,
                completed_at=now,
                error_code=error_code,
                error_message_sanitized=f"User answer parsing failed: {error_code}",
            )
        )

    db.commit()
    db.refresh(answer)
    return answer
