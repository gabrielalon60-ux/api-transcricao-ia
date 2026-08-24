from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.fifo_worker import _process_validating_item, _send_gate6_prompt
from orchestrator.services.fifo_worker_service import (
    EffectiveFinancialDecision,
    Gate6DecisionConflict,
    _to_decimal,
    _validated_human_overrides,
    adapt_gate3_normalized_data,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("50.00"), Decimal("50.00")),
        (50, Decimal("50")),
        (50.0, Decimal("50.0")),
        ("50", Decimal("50")),
        ("50.00", Decimal("50.00")),
        ("50,00", Decimal("50.00")),
        ("1234,56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),
        ("R$ 50,00", Decimal("50.00")),
        ("R$ 1.234,56", Decimal("1234.56")),
        # PT-BR Grouped Integers
        ("1.234", Decimal("1234")),
        ("12.345", Decimal("12345")),
        ("123.456", Decimal("123456")),
        ("1.234.567", Decimal("1234567")),
        ("R$ 1.234", Decimal("1234")),
        ("R$ 12.345", Decimal("12345")),
        # Canonical 1 or 2 digit decimals
        ("1.2", Decimal("1.2")),
        ("1.23", Decimal("1.23")),
        ("12.34", Decimal("12.34")),
        ("1234.56", Decimal("1234.56")),
        # Comma 1 or 2 digit decimals
        ("1,2", Decimal("1.2")),
        ("1,23", Decimal("1.23")),
        ("1,234", None),  # 3-digit comma rejected (BRL scale <= 2 decimals)
        # Malformed grouping & ambiguous US fail-closed
        ("1,234.56", None),
        ("1.23.456", None),
        ("12.34.567", None),
        ("1..234", None),
        (".123", None),
        ("123.", None),
        ("R$ .", None),
        ("R$ 1.23.456,78", None),
        # Fail closed on invalid / empty / non-string
        (None, None),
        ("", None),
        ("   ", None),
        ("abc", None),
        ("R$ abc", None),
        (True, None),
        (False, None),
        ([50], None),
        ({"a": 50}, None),
        # Zeros & Negatives
        ("0", Decimal("0")),
        ("0,00", Decimal("0.00")),
        ("0.00", Decimal("0.00")),
        ("-50,00", Decimal("-50.00")),
        ("-1.234,56", Decimal("-1234.56")),
        ("-1.234", Decimal("-1234")),
        ("-50.00", Decimal("-50.00")),
    ],
)
def test_to_decimal_comprehensive_matrix(value: object, expected: Optional[Decimal]) -> None:
    assert _to_decimal(value) == expected


@pytest.mark.parametrize(
    ("document_type", "data", "expected"),
    [
        ("pix_receipt", {"amount": "10.50", "transaction_date": "2026-01-01", "sender_cpf_cnpj": "p", "receiver_cpf_cnpj": "r"}, (Decimal("10.50"), "2026-01-01", "p", "r")),
        ("bank_receipt", {"amount": "20", "payment_date": "2026-01-02", "payer_cpf_cnpj": "p", "recipient_cpf_cnpj": "r"}, (Decimal("20"), "2026-01-02", "p", "r")),
        ("invoice", {"total_amount": "30.25", "invoice_date": "2026-01-03", "customer_cpf_cnpj": "p", "supplier_cpf_cnpj": "r"}, (Decimal("30.25"), "2026-01-03", "p", "r")),
        ("commercial_document", {"total_amount": "40", "document_date": "2026-01-04", "customer_cpf_cnpj": "p", "supplier_cpf_cnpj": "r"}, (Decimal("40"), "2026-01-04", "p", "r")),
        # PT-BR Comma Decimal Formats Across All 4 Approved Types
        ("bank_receipt", {"amount": "50,00", "payment_date": "2026-08-22", "payer_cpf_cnpj": "p", "recipient_cpf_cnpj": "r"}, (Decimal("50.00"), "2026-08-22", "p", "r")),
        ("pix_receipt", {"amount": "150,90", "transaction_date": "2026-08-22", "sender_cpf_cnpj": "p", "receiver_cpf_cnpj": "r"}, (Decimal("150.90"), "2026-08-22", "p", "r")),
        ("invoice", {"total_amount": "1.234,56", "invoice_date": "2026-08-22", "customer_cpf_cnpj": "p", "supplier_cpf_cnpj": "r"}, (Decimal("1234.56"), "2026-08-22", "p", "r")),
        ("commercial_document", {"total_amount": "2.500,00", "document_date": "2026-08-22", "customer_cpf_cnpj": "p", "supplier_cpf_cnpj": "r"}, (Decimal("2500.00"), "2026-08-22", "p", "r")),
    ],
)
def test_gate3_adapter_exact_mapping(document_type: str, data: dict[str, str], expected: tuple[object, ...]) -> None:
    item = SimpleNamespace(document_type=document_type, normalized_data=data)
    adapted = adapt_gate3_normalized_data(item)  # type: ignore[arg-type]
    assert (adapted["amount"], adapted["document_date"], adapted["payer_identifier"], adapted["receiver_identifier"]) == expected


def test_gate3_adapter_physical_ready_bank_receipt_reproduction() -> None:
    """Reproduction of physical READY item normalized_data structure."""
    physical_data = {
        "amount": "50,00",
        "amount_assurance_percentage": None,
        "bank_code": None,
        "barcode": None,
        "document_type": "bank_receipt",
        "due_date": None,
        "payer_cpf_cnpj": None,
        "payer_name": None,
        "payment_date": "22/08/2026",
        "recipient_cpf_cnpj": None,
        "recipient_name": "Mercado Teste LTDA",
    }
    item = SimpleNamespace(document_type="bank_receipt", normalized_data=physical_data)
    adapted = adapt_gate3_normalized_data(item)  # type: ignore[arg-type]
    assert adapted["amount"] == Decimal("50.00")
    assert adapted["document_date"] == "22/08/2026"
    assert adapted["payer_identifier"] is None
    assert adapted["receiver_identifier"] is None


def test_gate3_adapter_does_not_invent_aliases() -> None:
    item = SimpleNamespace(document_type="invoice", normalized_data={"amount": "99", "date": "2026-01-01"})
    adapted = adapt_gate3_normalized_data(item)  # type: ignore[arg-type]
    assert adapted == {"amount": None, "document_date": None, "payer_identifier": None, "receiver_identifier": None}


def test_effective_financial_decision_is_immutable_and_consistent() -> None:
    decision = EffectiveFinancialDecision(
        direction="expense",
        amount=Decimal("100.00"),
        transaction_date=datetime.now(timezone.utc),
        document_date_str="2026-01-01",
        date_source="DOCUMENT",
        question_type=None,
        clarification_reason=None,
        is_eligible_for_auto_write=True,
    )
    assert decision.is_eligible_for_auto_write is True
    with pytest.raises(Exception):
        decision.direction = "income"  # type: ignore[misc]


def _answer(value: str) -> SimpleNamespace:
    return SimpleNamespace(parsing_result={"value": value})


def test_answer_provenance_matches_materialized_values() -> None:
    item = SimpleNamespace(direction="expense", amount=Decimal("125.50"))
    direction, amount = _validated_human_overrides(  # type: ignore[arg-type]
        {"transaction_direction": _answer("expense"), "transaction_amount": _answer("125.50")},
        item,
    )
    assert direction == "expense"
    assert amount == Decimal("125.50")


@pytest.mark.parametrize(
    ("answers", "item"),
    [
        ({"transaction_direction": _answer("expense")}, SimpleNamespace(direction="income", amount=None)),
        ({"transaction_amount": _answer("125.50")}, SimpleNamespace(direction=None, amount=Decimal("99.00"))),
    ],
)
def test_answer_provenance_divergence_fails_closed(answers: dict[str, object], item: object) -> None:
    with pytest.raises(Gate6DecisionConflict):
        _validated_human_overrides(answers, item)  # type: ignore[arg-type]


def test_runtime_sender_never_treats_missing_configuration_as_ack() -> None:
    client = MagicMock(base_url="", token="")
    with patch("orchestrator.fifo_worker.WuzapiClient", return_value=client):
        assert _send_gate6_prompt("5511999999999", "transaction_direction") is False
        client.send_text_message.assert_not_called()


def test_gate6_worker_never_dispatches_final_success_message() -> None:
    source = inspect.getsource(_process_validating_item)
    assert "format_success_message" not in source
    assert "send_text_message" not in source
