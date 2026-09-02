from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Bot, Event, Execution, Instance, Organization, ProcessingItem, User
from orchestrator.services.classification_notification_service import (
    ACKNOWLEDGED_OPERATION,
    DISPATCHED_OPERATION,
    RESERVED_OPERATION,
    UNKNOWN_OPERATION,
    format_classification_summary,
    run_classification_notification_iteration,
)
from orchestrator.services.fifo_worker_service import (
    transition_validating_to_validated,
)
from orchestrator.services.user_interaction_service import (
    DIRECTION_PROMPT,
    parse_direction_answer,
)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _seed_item(
    session_factory: sessionmaker,
    *,
    status: str = "VALIDATED",
    direction: str = "expense",
) -> str:
    now = datetime.now(UTC)
    with session_factory() as db:
        organization = Organization(id="org-1", name="Org", slug="org-1")
        bot = Bot(
            id="bot-1",
            organization_id=organization.id,
            name="Bot",
            service_key="classification-test-bot",
        )
        instance = Instance(
            id="instance-1",
            organization_id=organization.id,
            bot_id=bot.id,
            external_instance_id="external-1",
            phone_number="5511999999999",
        )
        user = User(
            id="user-1",
            organization_id=organization.id,
            phone_number="5511888888888",
        )
        event = Event(
            id="event-1",
            correlation_id="correlation-1",
            provider="WUZAPI",
            external_instance_id=instance.external_instance_id,
            external_message_id="message-1",
            organization_id=organization.id,
            instance_id=instance.id,
            user_id=user.id,
            message_type="image",
        )
        item = ProcessingItem(
            id="item-1",
            event_id=event.id,
            correlation_id=event.correlation_id,
            organization_id=organization.id,
            instance_id=instance.id,
            user_id=user.id,
            sequence=1,
            status=status,
            claimed_by="worker-classification-1" if status == "VALIDATING" else None,
            lease_expires_at=(
                now + timedelta(minutes=5) if status == "VALIDATING" else None
            ),
            attempt_count=1,
            message_received_at=now,
            file_mime_type="image/jpeg",
            file_size=10,
            file_sha256="a" * 64,
            amount=Decimal("1234.56"),
            document_date="2026-08-22",
            transaction_date=datetime(2026, 8, 22, tzinfo=UTC),
            date_source="DOCUMENT",
            direction=direction,
            enterprise_id="enterprise-1",
            enterprise_display_name="Empreendimento Central",
        )
        db.add_all([organization, bot, instance, user, event, item])
        db.commit()
        return item.id


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("1", "income"),
        ("recebimento", "income"),
        ("entrada", "income"),
        ("2", "expense"),
        ("pagamento", "expense"),
        ("saída", "expense"),
    ],
)
def test_direction_prompt_and_answers(answer: str, expected: str) -> None:
    assert "Recebimento" in DIRECTION_PROMPT
    assert "Pagamento" in DIRECTION_PROMPT
    assert parse_direction_answer(answer) == expected


@pytest.mark.parametrize(
    ("direction", "label"),
    [("expense", "Pagamento"), ("income", "Recebimento")],
)
def test_classification_summary_is_complete_and_not_a_persistence_claim(
    session_factory: sessionmaker,
    direction: str,
    label: str,
) -> None:
    item_id = _seed_item(session_factory, direction=direction)
    with session_factory() as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        message = format_classification_summary(item)
    assert "Empreendimento: Empreendimento Central" in message
    assert f"Movimentação: {label}" in message
    assert "Valor: R$ 1.234,56" in message
    assert "Data: 22/08/2026" in message
    assert "ainda não foi gravado" in message
    assert "Gravado com sucesso" not in message


def test_validating_transitions_to_validated_without_persistence(
    session_factory: sessionmaker,
) -> None:
    item_id = _seed_item(session_factory, status="VALIDATING")
    with session_factory() as db:
        item = transition_validating_to_validated(
            db,
            item_id,
            "classification-1",
        )
        assert item is not None
        assert item.status == "VALIDATED"
        assert item.claimed_by is None
        operations = set(
            row[0]
            for row in db.query(Execution.operation)
            .filter(Execution.processing_item_id == item_id)
            .all()
        )
    assert operations == {"BUSINESS_CLASSIFICATION_COMPLETED"}
    assert not any(operation.startswith("PERSISTENCE_") for operation in operations)


def test_notification_is_idempotent_and_acknowledged(
    session_factory: sessionmaker,
) -> None:
    item_id = _seed_item(session_factory)
    sent: list[tuple[str, str, str]] = []

    def sender(phone: str, message: str, outbound_id: str) -> bool:
        sent.append((phone, message, outbound_id))
        return True

    assert run_classification_notification_iteration(session_factory, sender) is True
    assert run_classification_notification_iteration(session_factory, sender) is False
    assert len(sent) == 1
    with session_factory() as db:
        operations = [
            row[0]
            for row in db.query(Execution.operation)
            .filter(Execution.processing_item_id == item_id)
            .all()
        ]
    assert operations.count(RESERVED_OPERATION) == 1
    assert operations.count(DISPATCHED_OPERATION) == 1
    assert operations.count(ACKNOWLEDGED_OPERATION) == 1
    assert UNKNOWN_OPERATION not in operations


def test_notification_failure_is_terminal_unknown(
    session_factory: sessionmaker,
) -> None:
    item_id = _seed_item(session_factory)
    assert (
        run_classification_notification_iteration(
            session_factory,
            lambda *_args: False,
        )
        is True
    )
    assert (
        run_classification_notification_iteration(
            session_factory,
            lambda *_args: True,
        )
        is False
    )
    with session_factory() as db:
        operations = {
            row[0]
            for row in db.query(Execution.operation)
            .filter(Execution.processing_item_id == item_id)
            .all()
        }
    assert UNKNOWN_OPERATION in operations
    assert ACKNOWLEDGED_OPERATION not in operations
