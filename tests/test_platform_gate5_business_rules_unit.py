"""Gate 5 — Business Rules Evaluator Unit & Component Tests.

Covers G5-X01 through G5-X10 acceptance matrix and additional safety tests
per the approved IMPLEMENTATION_PLAN_GATE_5.md.

Pure component tests only — no database, no HTTP, no WUZAPI, no FIFO runtime.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from orchestrator.services.business_rules_evaluator import (
    BUSINESS_TIMEZONE,
    BusinessRulesEvaluatorService,
    FinancialEvaluationResult,
    classify_direction,
    format_success_message,
    get_display_date,
    normalize_digits,
    resolve_transaction_date,
    validate_amount,
)

# --- Fixture: default DF Holding identifiers (PRD RN-012 placeholders) ---

DF_IDS = [
    "00000000000000",  # CNPJ_1
    "11111111111111",  # CNPJ_2
    "00000000000",     # CPF_1
    "11111111111",     # CPF_2
]

CNPJ_1_FORMATTED = "00.000.000/0000-00"
CNPJ_2_FORMATTED = "11.111.111/1111-11"
CPF_1_FORMATTED = "000.000.000-00"
CPF_2_FORMATTED = "111.111.111-11"

EXTERNAL_CNPJ = "12.345.678/0001-90"
EXTERNAL_CPF = "123.456.789-09"

MSG_TIMESTAMP = datetime(2026, 8, 8, 15, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def evaluator() -> BusinessRulesEvaluatorService:
    return BusinessRulesEvaluatorService(df_holding_identifiers=DF_IDS)


# ============================================================================
# G5-X01: DF Payer Only -> expense -> eligible
# ============================================================================

class TestG5X01DFPayerOnly:
    """G5-X01: DF payer only -> expense, eligible."""

    def test_direction_expense(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("150.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.direction == "expense"
        assert result.is_eligible_for_auto_write is True
        assert result.question_type is None
        assert result.clarification_reason is None

    def test_date_source_document(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("150.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "DOCUMENT"
        assert result.document_date_str == "2026-08-01"

    def test_amount_quantized(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("150.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.amount == Decimal("150.00")


# ============================================================================
# G5-X02: DF Receiver Only -> income -> eligible
# ============================================================================

class TestG5X02DFReceiverOnly:
    """G5-X02: DF receiver only -> income, eligible."""

    def test_direction_income(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("500.00"),
            document_date="2026-08-02",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=EXTERNAL_CNPJ,
            receiver_identifier=CNPJ_2_FORMATTED,
        )
        assert result.direction == "income"
        assert result.is_eligible_for_auto_write is True
        assert result.question_type is None
        assert result.clarification_reason is None


# ============================================================================
# G5-X03: DF Both Sides -> ambiguous -> not eligible
# ============================================================================

class TestG5X03DFBothSides:
    """G5-X03: Both DF -> ambiguous, transaction_direction, not eligible."""

    def test_direction_ambiguous(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("200.00"),
            document_date="2026-08-03",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=CNPJ_2_FORMATTED,
        )
        assert result.direction == "ambiguous"
        assert result.is_eligible_for_auto_write is False
        assert result.question_type == "transaction_direction"
        assert result.clarification_reason == "AMBIGUOUS_DIRECTION"


# ============================================================================
# G5-X04: DF Neither Side -> unknown -> not eligible
# ============================================================================

class TestG5X04DFNeitherSide:
    """G5-X04: Neither DF -> unknown, transaction_direction, not eligible."""

    def test_direction_unknown(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("300.00"),
            document_date="2026-08-04",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=EXTERNAL_CNPJ,
            receiver_identifier=EXTERNAL_CPF,
        )
        assert result.direction == "unknown"
        assert result.is_eligible_for_auto_write is False
        assert result.question_type == "transaction_direction"
        assert result.clarification_reason == "UNKNOWN_DIRECTION"


# ============================================================================
# G5-X05: Amount Zero -> not eligible
# ============================================================================

class TestG5X05AmountZero:
    """G5-X05: Amount = 0 -> transaction_amount, not eligible."""

    def test_amount_zero(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("0.00"),
            document_date="2026-08-05",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=EXTERNAL_CNPJ,
            receiver_identifier=CNPJ_1_FORMATTED,
        )
        assert result.is_eligible_for_auto_write is False
        assert result.question_type == "transaction_amount"
        assert result.clarification_reason == "INVALID_AMOUNT"


# ============================================================================
# G5-X06: Amount Missing -> not eligible
# ============================================================================

class TestG5X06AmountMissing:
    """G5-X06: Amount = None -> transaction_amount, not eligible."""

    def test_amount_missing(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=None,
            document_date="2026-08-06",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=EXTERNAL_CNPJ,
            receiver_identifier=CNPJ_1_FORMATTED,
        )
        assert result.is_eligible_for_auto_write is False
        assert result.question_type == "transaction_amount"
        assert result.clarification_reason == "MISSING_AMOUNT"
        assert result.amount is None


# ============================================================================
# G5-X07: Document Date Used -> DOCUMENT, 00:00 America/Sao_Paulo
# ============================================================================

class TestG5X07DocumentDateUsed:
    """G5-X07: Valid document date -> DOCUMENT, 00:00 America/Sao_Paulo."""

    def test_document_date_source(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-07-29",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "DOCUMENT"
        assert result.document_date_str == "2026-07-29"

    def test_transaction_date_midnight_sao_paulo(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-07-29",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        expected = datetime(2026, 7, 29, 0, 0, 0, tzinfo=BUSINESS_TIMEZONE)
        assert result.transaction_date == expected
        assert result.transaction_date.tzinfo is not None

    def test_calendar_day_preserved_in_business_tz(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Calendar day must remain 2026-07-29 when converted to America/Sao_Paulo."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-07-29",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        sp_date = result.transaction_date.astimezone(BUSINESS_TIMEZONE).date()
        assert sp_date == date(2026, 7, 29)

    def test_eligible(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-07-29",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.is_eligible_for_auto_write is True


# ============================================================================
# G5-X08: Date Missing -> MESSAGE_TIMESTAMP, real instant normalized to UTC
# ============================================================================

class TestG5X08DateMissing:
    """G5-X08: Missing document date -> MESSAGE_TIMESTAMP, UTC instant."""

    def test_message_timestamp_fallback(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date=None,
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "MESSAGE_TIMESTAMP"
        assert result.document_date_str is None
        assert result.transaction_date == MSG_TIMESTAMP

    def test_display_date_in_sao_paulo(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date=None,
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        display = get_display_date(result)
        expected_sp = MSG_TIMESTAMP.astimezone(BUSINESS_TIMEZONE).date()
        assert display == expected_sp

    def test_eligible(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date=None,
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.is_eligible_for_auto_write is True


# ============================================================================
# G5-X09: Budget Date Fallback -> MESSAGE_TIMESTAMP
# ============================================================================

class TestG5X09BudgetDateFallback:
    """G5-X09: Orçamento without date -> MESSAGE_TIMESTAMP fallback."""

    def test_budget_no_date(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("75.00"),
            document_date=None,
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "MESSAGE_TIMESTAMP"
        assert result.is_eligible_for_auto_write is True

    def test_empty_string_date(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("75.00"),
            document_date="",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "MESSAGE_TIMESTAMP"

    def test_whitespace_date(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("75.00"),
            document_date="   ",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "MESSAGE_TIMESTAMP"


# ============================================================================
# G5-X10: Complete Item -> eligible, question_type None
#          Success formatter tested independently
# ============================================================================

class TestG5X10CompleteItem:
    """G5-X10: Complete deterministic item -> eligible, question_type None."""

    def test_eligible_and_no_question(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("1000.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.is_eligible_for_auto_write is True
        assert result.question_type is None
        assert result.clarification_reason is None
        assert result.direction == "expense"
        assert result.amount == Decimal("1000.00")
        assert result.date_source == "DOCUMENT"


# ============================================================================
# Success Formatter (tested independently under simulated post-COMMITTED)
# ============================================================================

class TestSuccessFormatter:
    """Success formatter tested independently under simulated post-COMMITTED."""

    def test_expense_message(self) -> None:
        msg = format_success_message("expense", Decimal("150.00"), date(2026, 8, 1))
        assert msg == "✅ Gravado com sucesso.\n\nDespesa de R$ 150,00 realizada em 01/08/2026."

    def test_income_message(self) -> None:
        msg = format_success_message("income", Decimal("500.00"), date(2026, 8, 2))
        assert msg == "✅ Gravado com sucesso.\n\nEntrada de R$ 500,00 realizada em 02/08/2026."

    def test_large_amount_formatting(self) -> None:
        msg = format_success_message("expense", Decimal("1234567.89"), date(2026, 1, 15))
        assert "R$ 1.234.567,89" in msg

    def test_small_amount_formatting(self) -> None:
        msg = format_success_message("income", Decimal("0.01"), date(2026, 12, 31))
        assert "R$ 0,01" in msg

    def test_display_date_document(self) -> None:
        """get_display_date for DOCUMENT returns the document calendar date."""
        result = FinancialEvaluationResult(
            is_eligible_for_auto_write=True,
            direction="expense",
            amount=Decimal("100.00"),
            transaction_date=datetime(2026, 8, 1, 0, 0, 0, tzinfo=BUSINESS_TIMEZONE),
            document_date_str="2026-08-01",
            date_source="DOCUMENT",
            question_type=None,
            clarification_reason=None,
        )
        assert get_display_date(result) == date(2026, 8, 1)

    def test_display_date_message_timestamp(self) -> None:
        """get_display_date for MESSAGE_TIMESTAMP converts to America/Sao_Paulo."""
        ts = datetime(2026, 8, 9, 2, 30, 0, tzinfo=timezone.utc)
        result = FinancialEvaluationResult(
            is_eligible_for_auto_write=True,
            direction="income",
            amount=Decimal("200.00"),
            transaction_date=ts,
            document_date_str=None,
            date_source="MESSAGE_TIMESTAMP",
            question_type=None,
            clarification_reason=None,
        )
        # 2026-08-09 02:30 UTC = 2026-08-08 23:30 in Sao Paulo
        assert get_display_date(result) == date(2026, 8, 8)


# ============================================================================
# CPF/CNPJ Normalization Safety Tests
# ============================================================================

class TestCPFCNPJNormalization:
    """CPF/CNPJ normalization and matching safety tests."""

    def test_formatted_cnpj_equals_digits(self) -> None:
        assert normalize_digits("00.000.000/0000-00") == "00000000000000"

    def test_formatted_cpf_equals_digits(self) -> None:
        assert normalize_digits("000.000.000-00") == "00000000000"

    def test_none_returns_none(self) -> None:
        assert normalize_digits(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert normalize_digits("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert normalize_digits("   ") is None

    def test_letters_only_returns_none(self) -> None:
        assert normalize_digits("abc") is None

    def test_mixed_with_digits(self) -> None:
        assert normalize_digits("abc123def456") == "123456"

    def test_digits_only_passthrough(self) -> None:
        assert normalize_digits("00000000000000") == "00000000000000"

    def test_none_payer_direction(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """None payer identifier -> never classified as expense."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=None,
            receiver_identifier=CNPJ_1_FORMATTED,
        )
        assert result.direction == "income"

    def test_none_receiver_direction(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """None receiver identifier -> never classified as income."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=None,
        )
        assert result.direction == "expense"

    def test_both_none_identifiers(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Both None identifiers -> unknown direction."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=None,
            receiver_identifier=None,
        )
        assert result.direction == "unknown"
        assert result.question_type == "transaction_direction"

    def test_empty_string_identifiers(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Empty string identifiers -> unknown direction."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier="",
            receiver_identifier="",
        )
        assert result.direction == "unknown"

    def test_malformed_identifier(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Malformed identifier with no valid digits -> unknown direction."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier="NOT-A-NUMBER",
            receiver_identifier="ALSO-NOT",
        )
        assert result.direction == "unknown"


# ============================================================================
# Amount Validation Safety Tests
# ============================================================================

class TestAmountValidation:
    """Amount validation safety tests."""

    def test_negative_amount(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Explicit negative amount -> not eligible, INVALID_AMOUNT."""
        result = evaluator.evaluate(
            amount=Decimal("-50.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.is_eligible_for_auto_write is False
        assert result.question_type == "transaction_amount"
        assert result.clarification_reason == "INVALID_AMOUNT"
        assert result.amount == Decimal("-50.00")

    def test_very_small_positive_amount(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Smallest valid positive amount."""
        result = evaluator.evaluate(
            amount=Decimal("0.01"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.is_eligible_for_auto_write is True
        assert result.amount == Decimal("0.01")

    def test_amount_quantization(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Amount is quantized to 2 decimal places."""
        result = evaluator.evaluate(
            amount=Decimal("100.999"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.amount == Decimal("101.00")

    def test_validate_amount_standalone(self) -> None:
        """Direct validate_amount function tests."""
        amt, valid, qt, reason = validate_amount(Decimal("100.00"))
        assert valid is True
        assert qt is None
        assert reason is None
        assert amt == Decimal("100.00")

        amt, valid, qt, reason = validate_amount(None)
        assert valid is False
        assert qt == "transaction_amount"
        assert reason == "MISSING_AMOUNT"

        amt, valid, qt, reason = validate_amount(Decimal("0.00"))
        assert valid is False
        assert qt == "transaction_amount"
        assert reason == "INVALID_AMOUNT"

        amt, valid, qt, reason = validate_amount(Decimal("-1.00"))
        assert valid is False
        assert reason == "INVALID_AMOUNT"


# ============================================================================
# QUESTION_PRIORITY: direction > amount
# ============================================================================

class TestQuestionPriority:
    """Both unresolved direction and invalid amount must respect QUESTION_PRIORITY."""

    def test_ambiguous_direction_and_missing_amount(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Direction takes priority over amount when both are unresolved."""
        result = evaluator.evaluate(
            amount=None,
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=CNPJ_2_FORMATTED,
        )
        # direction is ambiguous AND amount is None
        # QUESTION_PRIORITY: transaction_direction > transaction_amount
        assert result.question_type == "transaction_direction"
        assert result.clarification_reason == "AMBIGUOUS_DIRECTION"

    def test_unknown_direction_and_zero_amount(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Direction takes priority over amount when both are unresolved."""
        result = evaluator.evaluate(
            amount=Decimal("0.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=EXTERNAL_CNPJ,
            receiver_identifier=EXTERNAL_CPF,
        )
        assert result.question_type == "transaction_direction"
        assert result.clarification_reason == "UNKNOWN_DIRECTION"

    def test_valid_direction_invalid_amount(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """When direction is resolved but amount is invalid, question_type is transaction_amount."""
        result = evaluator.evaluate(
            amount=None,
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.direction == "expense"
        assert result.question_type == "transaction_amount"
        assert result.clarification_reason == "MISSING_AMOUNT"


# ============================================================================
# Date Resolution Safety Tests
# ============================================================================

class TestDateResolution:
    """Date resolution edge cases and timezone safety."""

    def test_valid_leap_day(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Valid leap day: 2024-02-29."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2024-02-29",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "DOCUMENT"
        assert result.document_date_str == "2024-02-29"
        sp_date = result.transaction_date.astimezone(BUSINESS_TIMEZONE).date()
        assert sp_date == date(2024, 2, 29)

    def test_invalid_calendar_date_fallback(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Invalid calendar date (Feb 30) -> MESSAGE_TIMESTAMP fallback."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-02-30",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "MESSAGE_TIMESTAMP"
        assert result.document_date_str is None

    def test_pt_br_document_date(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """Extraction-native PT-BR date is normalized as a document date."""
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="01/08/2026",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "DOCUMENT"
        assert result.document_date_str == "2026-08-01"

    def test_invalid_format_fallback(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="data-invalida",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        assert result.date_source == "MESSAGE_TIMESTAMP"
        assert result.document_date_str is None

    def test_document_date_never_shifts_calendar_day(self, evaluator: BusinessRulesEvaluatorService) -> None:
        """DOCUMENT date must never shift to previous/next calendar day in America/Sao_Paulo."""
        for day in range(1, 29):
            doc_date = f"2026-08-{day:02d}"
            result = evaluator.evaluate(
                amount=Decimal("100.00"),
                document_date=doc_date,
                message_received_at=MSG_TIMESTAMP,
                payer_identifier=CNPJ_1_FORMATTED,
                receiver_identifier=EXTERNAL_CNPJ,
            )
            sp_date = result.transaction_date.astimezone(BUSINESS_TIMEZONE).date()
            assert sp_date == date(2026, 8, day), (
                f"Calendar day shift detected for {doc_date}: got {sp_date}"
            )

    def test_timezone_boundary_near_midnight_message_timestamp(self) -> None:
        """MESSAGE_TIMESTAMP near midnight UTC -> correct Sao Paulo date."""
        # 2026-08-09 01:00 UTC = 2026-08-08 22:00 Sao Paulo
        ts_near_midnight = datetime(2026, 8, 9, 1, 0, 0, tzinfo=timezone.utc)
        tx_date, source, doc_str = resolve_transaction_date(None, ts_near_midnight)
        assert source == "MESSAGE_TIMESTAMP"
        sp_date = tx_date.astimezone(BUSINESS_TIMEZONE).date()
        assert sp_date == date(2026, 8, 8)

    def test_timezone_boundary_just_after_midnight_sp(self) -> None:
        """MESSAGE_TIMESTAMP at 03:01 UTC = 00:01 Sao Paulo -> same calendar day."""
        # 2026-08-09 03:01 UTC = 2026-08-09 00:01 Sao Paulo
        ts = datetime(2026, 8, 9, 3, 1, 0, tzinfo=timezone.utc)
        tx_date, source, doc_str = resolve_transaction_date(None, ts)
        sp_date = tx_date.astimezone(BUSINESS_TIMEZONE).date()
        assert sp_date == date(2026, 8, 9)

    def test_resolve_transaction_date_document(self) -> None:
        """Direct resolve_transaction_date for DOCUMENT."""
        tx_date, source, doc_str = resolve_transaction_date("2026-08-01", MSG_TIMESTAMP)
        assert source == "DOCUMENT"
        assert doc_str == "2026-08-01"
        assert tx_date.tzinfo is not None
        assert tx_date.astimezone(BUSINESS_TIMEZONE).date() == date(2026, 8, 1)

    def test_resolve_transaction_date_message(self) -> None:
        """Direct resolve_transaction_date for MESSAGE_TIMESTAMP."""
        tx_date, source, doc_str = resolve_transaction_date(None, MSG_TIMESTAMP)
        assert source == "MESSAGE_TIMESTAMP"
        assert doc_str is None
        assert tx_date == MSG_TIMESTAMP


# ============================================================================
# FinancialEvaluationResult Immutability
# ============================================================================

class TestResultImmutability:
    """FinancialEvaluationResult is frozen dataclass."""

    def test_frozen(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        with pytest.raises(AttributeError):
            result.is_eligible_for_auto_write = False  # type: ignore[misc]

    def test_frozen_direction(self, evaluator: BusinessRulesEvaluatorService) -> None:
        result = evaluator.evaluate(
            amount=Decimal("100.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        with pytest.raises(AttributeError):
            result.direction = "income"  # type: ignore[misc]


# ============================================================================
# Deterministic Repeated Evaluation
# ============================================================================

class TestDeterministicEvaluation:
    """Equivalent inputs produce identical results."""

    def test_repeated_evaluation_identical(self, evaluator: BusinessRulesEvaluatorService) -> None:
        kwargs = dict(
            amount=Decimal("150.00"),
            document_date="2026-08-01",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_1_FORMATTED,
            receiver_identifier=EXTERNAL_CNPJ,
        )
        result1 = evaluator.evaluate(**kwargs)
        result2 = evaluator.evaluate(**kwargs)
        assert result1 == result2

    def test_different_evaluator_instances_same_result(self) -> None:
        """Two separate evaluator instances with same config produce same result."""
        eval1 = BusinessRulesEvaluatorService(df_holding_identifiers=DF_IDS)
        eval2 = BusinessRulesEvaluatorService(df_holding_identifiers=list(DF_IDS))
        kwargs = dict(
            amount=Decimal("250.00"),
            document_date="2026-08-15",
            message_received_at=MSG_TIMESTAMP,
            payer_identifier=CNPJ_2_FORMATTED,
            receiver_identifier=EXTERNAL_CPF,
        )
        assert eval1.evaluate(**kwargs) == eval2.evaluate(**kwargs)


# ============================================================================
# Direction Classification Standalone
# ============================================================================

class TestClassifyDirectionStandalone:
    """Direct classify_direction function tests."""

    def test_expense(self) -> None:
        d, qt, cr = classify_direction(CNPJ_1_FORMATTED, EXTERNAL_CNPJ, DF_IDS)
        assert d == "expense"
        assert qt is None

    def test_income(self) -> None:
        d, qt, cr = classify_direction(EXTERNAL_CNPJ, CNPJ_2_FORMATTED, DF_IDS)
        assert d == "income"
        assert qt is None

    def test_ambiguous(self) -> None:
        d, qt, cr = classify_direction(CNPJ_1_FORMATTED, CNPJ_2_FORMATTED, DF_IDS)
        assert d == "ambiguous"
        assert qt == "transaction_direction"

    def test_unknown(self) -> None:
        d, qt, cr = classify_direction(EXTERNAL_CNPJ, EXTERNAL_CPF, DF_IDS)
        assert d == "unknown"
        assert qt == "transaction_direction"

    def test_cpf_payer_expense(self) -> None:
        """CPF_1 as payer -> expense."""
        d, qt, cr = classify_direction(CPF_1_FORMATTED, EXTERNAL_CNPJ, DF_IDS)
        assert d == "expense"

    def test_cpf_receiver_income(self) -> None:
        """CPF_2 as receiver -> income."""
        d, qt, cr = classify_direction(EXTERNAL_CNPJ, CPF_2_FORMATTED, DF_IDS)
        assert d == "income"
