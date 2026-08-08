from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.fifo_worker import _process_validating_item, _send_gate6_prompt
from orchestrator.services.fifo_worker_service import (
    EffectiveFinancialDecision,
    Gate6DecisionConflict,
    _validated_human_overrides,
    adapt_gate3_normalized_data,
)


@pytest.mark.parametrize(
    ("document_type", "data", "expected"),
    [
        ("pix_receipt", {"amount": "10.50", "transaction_date": "2026-01-01", "sender_cpf_cnpj": "p", "receiver_cpf_cnpj": "r"}, (Decimal("10.50"), "2026-01-01", "p", "r")),
        ("bank_receipt", {"amount": "20", "payment_date": "2026-01-02", "payer_cpf_cnpj": "p", "recipient_cpf_cnpj": "r"}, (Decimal("20"), "2026-01-02", "p", "r")),
        ("invoice", {"total_amount": "30.25", "invoice_date": "2026-01-03", "customer_cpf_cnpj": "p", "supplier_cpf_cnpj": "r"}, (Decimal("30.25"), "2026-01-03", "p", "r")),
        ("commercial_document", {"total_amount": "40", "document_date": "2026-01-04", "customer_cpf_cnpj": "p", "supplier_cpf_cnpj": "r"}, (Decimal("40"), "2026-01-04", "p", "r")),
    ],
)
def test_gate3_adapter_exact_mapping(document_type: str, data: dict[str, str], expected: tuple[object, ...]) -> None:
    item = SimpleNamespace(document_type=document_type, normalized_data=data)
    adapted = adapt_gate3_normalized_data(item)  # type: ignore[arg-type]
    assert (adapted["amount"], adapted["document_date"], adapted["payer_identifier"], adapted["receiver_identifier"]) == expected


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
