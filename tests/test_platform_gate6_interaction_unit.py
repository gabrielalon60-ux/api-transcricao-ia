from __future__ import annotations

from decimal import Decimal

import pytest

from orchestrator.services.user_interaction_service import (
    AMOUNT_PROMPT,
    DIRECTION_PROMPT,
    format_question_prompt,
    parse_amount_answer,
    parse_direction_answer,
)


def test_gate6_exact_prompts() -> None:
    assert DIRECTION_PROMPT == (
        "Este lançamento é uma entrada ou uma despesa?\n\n"
        "1 - Entrada\n2 - Despesa\n\nResponda com 1 ou 2."
    )
    assert AMOUNT_PROMPT == "Qual é o valor deste lançamento?"
    assert format_question_prompt("transaction_direction") == DIRECTION_PROMPT
    assert format_question_prompt("transaction_amount") == AMOUNT_PROMPT
    with pytest.raises(ValueError):
        format_question_prompt("document_classification")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "income"),
        ("entrada", "income"),
        ("receita", "income"),
        ("income", "income"),
        ("credito", "income"),
        ("crédito", "income"),
        ("2", "expense"),
        ("saida", "expense"),
        ("saída", "expense"),
        ("despesa", "expense"),
        ("expense", "expense"),
        ("debito", "expense"),
        ("débito", "expense"),
        ("  DESPESA  ", "expense"),
    ],
)
def test_gate6_direction_aliases(raw: str, expected: str) -> None:
    assert parse_direction_answer(raw) == expected


@pytest.mark.parametrize("raw", ["", "3", "entrada agora", "créditos", "abc"])
def test_gate6_direction_rejections(raw: str) -> None:
    assert parse_direction_answer(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", Decimal("1.00")),
        ("1,2", Decimal("1.20")),
        ("1,20", Decimal("1.20")),
        ("1.20", Decimal("1.20")),
        ("150", Decimal("150.00")),
        ("150,50", Decimal("150.50")),
        ("R$125,50", Decimal("125.50")),
        ("R$ 1.234,56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),
        ("1.234.567,89", Decimal("1234567.89")),
    ],
)
def test_gate6_amount_accepted_full_match(raw: str, expected: Decimal) -> None:
    assert parse_amount_answer(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["1.234", "1,234", "0", "-10", "abc", "12abc", "1,2345", "1.23.4,56", "1.234,567"],
)
def test_gate6_amount_rejected_without_partial_parsing(raw: str) -> None:
    assert parse_amount_answer(raw) is None
