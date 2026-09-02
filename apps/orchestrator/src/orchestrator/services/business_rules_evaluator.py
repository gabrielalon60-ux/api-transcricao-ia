"""Gate 5 — BusinessRulesEvaluatorService (Pure Component).

Deterministic financial business rules evaluator for DF Holding.
Consumes normalized extraction metadata and applies PRD RN-010 through RN-017
rules to produce a FinancialEvaluationResult.

This module has ZERO side effects:
- No database writes
- No HTTP calls
- No WUZAPI calls
- No UserInteraction allocation
- No ProcessingItem status transitions
- No queue claims or lease mutations
- No persistence dispatch
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional
from zoneinfo import ZoneInfo


# --- Business Timezone ---

BUSINESS_TIMEZONE = ZoneInfo("America/Sao_Paulo")


# --- Phase 4E Question Type Vocabulary (frozen) ---

QUESTION_PRIORITY: list[str] = [
    "transaction_direction",
    "transaction_amount",
    "document_classification",
]


# --- FinancialEvaluationResult ---

@dataclass(frozen=True)
class FinancialEvaluationResult:
    """Immutable result of deterministic financial business rules evaluation.

    Attributes:
        is_eligible_for_auto_write: Whether the item may be dispatched to
            Gate 4 PersistenceService for automatic database write.
        direction: Classified direction ("expense", "income", "ambiguous",
            "unknown").
        amount: Validated Decimal amount quantized to 2 decimal places,
            or None if missing/invalid.
        transaction_date: Timezone-aware datetime.
            For DOCUMENT: 00:00 local time in America/Sao_Paulo.
            For MESSAGE_TIMESTAMP: real inbound instant normalized to UTC.
        document_date_str: Original ISO "YYYY-MM-DD" string if date_source
            is DOCUMENT, otherwise None.
        date_source: "DOCUMENT" or "MESSAGE_TIMESTAMP".
        question_type: Frozen Phase 4E question type
            ("transaction_direction", "transaction_amount", etc.) if not
            eligible, otherwise None.
        clarification_reason: In-memory diagnostic string
            ("MISSING_AMOUNT", "INVALID_AMOUNT", "AMBIGUOUS_DIRECTION",
            "UNKNOWN_DIRECTION"). NOT persisted. NOT a database column.
            NOT a migration. The durable interaction vocabulary remains
            question_type. Gate 6 may consume this in memory.
    """

    is_eligible_for_auto_write: bool
    direction: str
    amount: Optional[Decimal]
    transaction_date: datetime
    document_date_str: Optional[str]
    date_source: str
    question_type: Optional[str]
    clarification_reason: Optional[str]


# --- CPF/CNPJ Normalization ---

def normalize_digits(value: Optional[str]) -> Optional[str]:
    """Strips all non-digit characters from a CPF/CNPJ string.

    Returns None if input is None, empty, or contains no digits.
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits if digits else None


# --- Date Resolution ---

def _parse_iso_date(date_str: Optional[str]) -> Optional[date]:
    """Attempts to parse an ISO or extraction-native PT-BR date string.

    Returns None if the string is None, empty, or not a valid calendar date.
    """
    if not date_str or not date_str.strip():
        return None
    try:
        return date.fromisoformat(date_str.strip())
    except (ValueError, TypeError):
        try:
            return datetime.strptime(date_str.strip(), "%d/%m/%Y").date()
        except (ValueError, TypeError):
            return None


def resolve_transaction_date(
    document_date_str: Optional[str],
    message_received_at: datetime,
) -> tuple[datetime, str, Optional[str]]:
    """Resolves transaction_date using the approved date semantics.

    Returns:
        (transaction_date, date_source, document_date_str)

    DOCUMENT:
        Semantic source = document calendar date.
        Representation = 00:00 local time in America/Sao_Paulo.

    MESSAGE_TIMESTAMP:
        Semantic source = real inbound message instant.
        Representation = timezone-aware real instant normalized to UTC.
    """
    parsed = _parse_iso_date(document_date_str)
    if parsed is not None:
        # DOCUMENT: 00:00 local time in America/Sao_Paulo
        transaction_date = datetime.combine(
            parsed,
            time.min,
            tzinfo=BUSINESS_TIMEZONE,
        )
        return transaction_date, "DOCUMENT", parsed.isoformat()

    # MESSAGE_TIMESTAMP: real instant normalized to UTC
    if message_received_at.tzinfo is None:
        # Safety: ensure UTC if naive (should not happen in production)
        normalized = message_received_at.replace(tzinfo=timezone.utc)
    else:
        normalized = message_received_at.astimezone(timezone.utc)
    return normalized, "MESSAGE_TIMESTAMP", None


# --- Direction Classification ---

def classify_direction(
    payer_identifier: Optional[str],
    receiver_identifier: Optional[str],
    df_holding_identifiers: List[str],
) -> tuple[str, Optional[str], Optional[str]]:
    """Classifies transaction direction based on DF Holding CPF/CNPJ matching.

    Returns:
        (direction, question_type, clarification_reason)
    """
    payer_normalized = normalize_digits(payer_identifier)
    receiver_normalized = normalize_digits(receiver_identifier)

    payer_is_df = payer_normalized in df_holding_identifiers if payer_normalized else False
    receiver_is_df = receiver_normalized in df_holding_identifiers if receiver_normalized else False

    if payer_is_df and not receiver_is_df:
        return "expense", None, None
    elif receiver_is_df and not payer_is_df:
        return "income", None, None
    elif payer_is_df and receiver_is_df:
        return "ambiguous", "transaction_direction", "AMBIGUOUS_DIRECTION"
    else:
        return "unknown", "transaction_direction", "UNKNOWN_DIRECTION"


# --- Amount Validation ---

def validate_amount(
    raw_amount: Optional[Decimal],
) -> tuple[Optional[Decimal], bool, Optional[str], Optional[str]]:
    """Validates and quantizes the transaction amount.

    Returns:
        (quantized_amount, is_valid, question_type, clarification_reason)
    """
    if raw_amount is None:
        return None, False, "transaction_amount", "MISSING_AMOUNT"

    try:
        quantized = Decimal(str(raw_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None, False, "transaction_amount", "INVALID_AMOUNT"

    if quantized <= Decimal("0.00"):
        return quantized, False, "transaction_amount", "INVALID_AMOUNT"

    return quantized, True, None, None


# --- Success Message Formatter ---

def format_success_message(
    direction: str,
    amount: Decimal,
    display_date: date,
) -> str:
    """Formats the approved PT-BR success message for a committed transaction.

    This formatter is a pure function. It does NOT determine whether
    persistence succeeded. Eligibility alone does NOT authorize invocation.
    Only Database Writer COMMITTED authorizes invocation.

    REJECTED, RETRYABLE_FAILURE, and PERSIST_OUTCOME_UNKNOWN must NEVER
    produce the success outcome.
    """
    lbl = "Despesa" if direction == "expense" else "Entrada"
    amt_str = f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    dt_str = display_date.strftime("%d/%m/%Y")
    return f"✅ Gravado com sucesso.\n\n{lbl} de {amt_str} realizada em {dt_str}."


def get_display_date(result: FinancialEvaluationResult) -> date:
    """Extracts the user-visible calendar date from evaluation result.

    For DOCUMENT: uses the authoritative document_date_str directly.
    For MESSAGE_TIMESTAMP: converts transaction_date to America/Sao_Paulo.
    """
    if result.date_source == "DOCUMENT" and result.document_date_str:
        return date.fromisoformat(result.document_date_str)
    # MESSAGE_TIMESTAMP: convert to business timezone for display
    return result.transaction_date.astimezone(BUSINESS_TIMEZONE).date()


# --- BusinessRulesEvaluatorService ---

class BusinessRulesEvaluatorService:
    """Pure deterministic financial business rules evaluator.

    Evaluates extraction metadata against DF Holding PRD rules and produces
    an immutable FinancialEvaluationResult.

    This service is stateless and has zero side effects:
    no DB writes, no HTTP calls, no WUZAPI, no queue mutations.
    """

    def __init__(self, df_holding_identifiers: List[str]) -> None:
        """Initializes with the list of normalized DF Holding CPF/CNPJ digits.

        Args:
            df_holding_identifiers: List of digit-only strings representing
                DF Holding CPF and CNPJ identifiers.
        """
        self._df_ids = list(df_holding_identifiers)

    def evaluate(
        self,
        *,
        amount: Optional[Decimal],
        document_date: Optional[str],
        message_received_at: datetime,
        payer_identifier: Optional[str],
        receiver_identifier: Optional[str],
    ) -> FinancialEvaluationResult:
        """Evaluates business rules and returns a deterministic result.

        Args:
            amount: Extracted Decimal amount (or None if missing).
            document_date: Extracted ISO "YYYY-MM-DD" string (or None).
            message_received_at: Timezone-aware inbound message timestamp.
            payer_identifier: Extracted payer CPF/CNPJ (may contain
                formatting chars).
            receiver_identifier: Extracted receiver CPF/CNPJ (may contain
                formatting chars).

        Returns:
            Immutable FinancialEvaluationResult with all evaluated fields.
        """
        # 1. Date resolution (G5-T03, G5-T04, G5-T05)
        transaction_date, date_source, doc_date_str = resolve_transaction_date(
            document_date, message_received_at,
        )

        # 2. Direction classification (G5-T07, G5-T08, G5-T09, G5-T10)
        direction, dir_question_type, dir_reason = classify_direction(
            payer_identifier, receiver_identifier, self._df_ids,
        )

        # 3. Amount validation (G5-T02)
        quantized_amount, amount_valid, amt_question_type, amt_reason = validate_amount(amount)

        # 4. Select question_type using Phase 4E QUESTION_PRIORITY
        #    Priority: transaction_direction > transaction_amount
        question_type: Optional[str] = None
        clarification_reason: Optional[str] = None

        if dir_question_type is not None:
            question_type = dir_question_type
            clarification_reason = dir_reason
        elif amt_question_type is not None:
            question_type = amt_question_type
            clarification_reason = amt_reason

        # 5. Derive eligibility (G5-T01)
        is_eligible_for_auto_write = bool(
            amount_valid
            and direction in ("expense", "income")
        )

        return FinancialEvaluationResult(
            is_eligible_for_auto_write=is_eligible_for_auto_write,
            direction=direction,
            amount=quantized_amount,
            transaction_date=transaction_date,
            document_date_str=doc_date_str,
            date_source=date_source,
            question_type=question_type,
            clarification_reason=clarification_reason,
        )
