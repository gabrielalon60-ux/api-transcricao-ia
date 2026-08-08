from __future__ import annotations

import pytest
from decimal import Decimal

from orchestrator.services.heartbeat_service import validate_heartbeat_config
from orchestrator.services.user_interaction_service import (
    parse_direction_answer,
    parse_amount_answer,
    parse_document_classification_answer,
    parse_answer,
    select_question_type,
    VALID_QUESTION_TYPES,
    QUESTION_PRIORITY,
)


def test_configuration_validation() -> None:
    """Verifies that malformed or non-positive heartbeat/sweeper configurations raise ValueError."""
    # Valid
    validate_heartbeat_config(15, 60, 30)

    # Invalid: non-positive
    with pytest.raises(ValueError, match="positive"):
        validate_heartbeat_config(0, 60, 30)
    with pytest.raises(ValueError, match="positive"):
        validate_heartbeat_config(15, -5, 30)
    with pytest.raises(ValueError, match="positive"):
        validate_heartbeat_config(15, 60, 0)

    # Invalid: heartbeat >= lease
    with pytest.raises(ValueError, match="strictly less than"):
        validate_heartbeat_config(60, 60, 30)
    with pytest.raises(ValueError, match="strictly less than"):
        validate_heartbeat_config(90, 60, 30)

    # Invalid: sweeper > lease
    with pytest.raises(ValueError, match="less than or equal"):
        validate_heartbeat_config(15, 60, 90)


def test_question_parsers_direction() -> None:
    """Verifies direction answer parsing grammar."""
    assert parse_direction_answer("1") == "income"
    assert parse_direction_answer("Entrada") == "income"
    assert parse_direction_answer("receita") == "income"
    assert parse_direction_answer("income") == "income"

    assert parse_direction_answer("2") == "expense"
    assert parse_direction_answer("Saída") == "expense"
    assert parse_direction_answer("saida") == "expense"
    assert parse_direction_answer("despesa") == "expense"

    assert parse_direction_answer("invalid") is None
    assert parse_direction_answer("3") is None


def test_question_parsers_amount() -> None:
    """Verifies amount answer parsing grammar."""
    assert parse_amount_answer("150") == Decimal("150.00")
    assert parse_amount_answer("150.50") == Decimal("150.50")
    assert parse_amount_answer("R$ 150,50") == Decimal("150.50")
    assert parse_amount_answer("R$1234.56") == Decimal("1234.56")

    assert parse_amount_answer("invalid") is None
    assert parse_amount_answer("0") is None
    assert parse_amount_answer("-50") is None


def test_question_parsers_document_classification() -> None:
    """Verifies document classification answer parsing grammar."""
    assert parse_document_classification_answer("1") == "pix_receipt"
    assert parse_document_classification_answer("PIX") == "pix_receipt"
    assert parse_document_classification_answer("comprovante pix") == "pix_receipt"

    assert parse_document_classification_answer("2") == "bank_receipt"
    assert parse_document_classification_answer("boleto") == "bank_receipt"

    assert parse_document_classification_answer("3") == "invoice"
    assert parse_document_classification_answer("nota fiscal") == "invoice"

    assert parse_document_classification_answer("4") == "commercial_document"
    assert parse_document_classification_answer("outro") == "commercial_document"

    assert parse_document_classification_answer("99") is None


def test_parse_answer_dispatcher() -> None:
    """Verifies error code assignment for unsupported and invalid answers."""
    val, err = parse_answer("transaction_direction", "1")
    assert val == "income" and err is None

    val, err = parse_answer("transaction_direction", "bogus")
    assert val is None and err == "INVALID_DIRECTION_CHOICE"

    val, err = parse_answer("transaction_amount", "R$ 100")
    assert val == Decimal("100.00") and err is None

    val, err = parse_answer("transaction_amount", "invalid")
    assert val is None and err == "INVALID_AMOUNT_FORMAT"

    val, err = parse_answer("unsupported_question", "1")
    assert val is None and err == "UNSUPPORTED_QUESTION_TYPE"


def test_prompt_identity_format() -> None:
    """Verifies deterministic outbound_message_id construction format."""
    item_id = "item-uuid-123"
    generation = 1
    qtype = "transaction_direction"
    msg_id = f"msg_{item_id}_{generation}_{qtype}"
    assert msg_id == "msg_item-uuid-123_1_transaction_direction"
    assert qtype in VALID_QUESTION_TYPES


def test_question_priority_ordering() -> None:
    """Verifies QUESTION_PRIORITY list matches the mandated priority order."""
    assert QUESTION_PRIORITY[0] == "transaction_direction"
    assert QUESTION_PRIORITY[1] == "transaction_amount"
    assert QUESTION_PRIORITY[2] == "document_classification"
    assert len(QUESTION_PRIORITY) == 3
    # All entries are valid question types
    for qt in QUESTION_PRIORITY:
        assert qt in VALID_QUESTION_TYPES


def test_select_question_type_all_missing() -> None:
    """select_question_type returns transaction_direction when all fields are None."""
    from unittest.mock import MagicMock
    item = MagicMock()
    item.direction = None
    item.amount = None
    item.document_type = None
    assert select_question_type(item) == "transaction_direction"


def test_select_question_type_direction_only_present() -> None:
    """select_question_type returns transaction_amount when direction is set."""
    from unittest.mock import MagicMock
    item = MagicMock()
    item.direction = "income"
    item.amount = None
    item.document_type = None
    assert select_question_type(item) == "transaction_amount"


def test_select_question_type_direction_and_amount_present() -> None:
    """select_question_type returns document_classification when direction and amount are set."""
    from unittest.mock import MagicMock
    item = MagicMock()
    item.direction = "expense"
    item.amount = 500
    item.document_type = None
    assert select_question_type(item) == "document_classification"


def test_select_question_type_returns_none_when_complete() -> None:
    """select_question_type returns None when all required fields are populated."""
    from unittest.mock import MagicMock
    item = MagicMock()
    item.direction = "income"
    item.amount = 100
    item.document_type = "invoice"
    assert select_question_type(item) is None
