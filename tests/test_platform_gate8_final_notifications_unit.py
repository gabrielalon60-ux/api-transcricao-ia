from __future__ import annotations

import threading
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from db.models import Execution, ProcessingItem
from orchestrator import fifo_worker
from orchestrator.services.final_notification_service import (
    EXPENSE_COMMITTED,
    EXTRACTION_FAILED,
    EXTRACTION_FAILED_MESSAGE,
    FINAL_NOTIFICATION_BATCH_SIZE,
    FINAL_NOTIFICATION_DISPATCH_CONCURRENCY,
    FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS,
    FINAL_NOTIFICATION_POLL_INTERVAL_SECONDS,
    FINAL_NOTIFICATION_SHUTDOWN_JOIN_SECONDS,
    INCOME_OUT_OF_SCOPE,
    INCOME_OUT_OF_SCOPE_MESSAGE,
    PERSISTENCE_FAILED,
    PERSISTENCE_FAILED_MESSAGE,
    dispatch_key,
    final_key,
    message_for_item,
    notification_type_for_item,
    outbound_message_id,
    reservation_key,
)
import test_platform_gate8_e2e_disposable_postgres as e2e

engine = e2e.engine
writer_engine = e2e.writer_engine


@pytest.fixture
def clean(engine, writer_engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE organizations CASCADE"))
    with writer_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE financial_records, suppliers, enterprises, "
                "write_ledger, df_business_records CASCADE"
            )
        )
    yield


@pytest.fixture
def writer_client(writer_engine, clean, monkeypatch):
    from fastapi.testclient import TestClient
    from db_writer import main as writer_main

    def override():
        with Session(writer_engine) as db:
            yield db

    writer_main.app.dependency_overrides[writer_main.get_db] = override
    bridge = e2e.LocalWriterClient(TestClient(writer_main.app))
    monkeypatch.setattr(fifo_worker, "DBWriterClient", lambda: bridge)
    monkeypatch.setattr(
        "orchestrator.services.persistence_service.DBWriterClient", lambda: bridge
    )
    monkeypatch.setattr(fifo_worker, "_send_gate6_prompt", lambda *_args: True)
    yield bridge
    writer_main.app.dependency_overrides.clear()


def _item(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "item-1",
        "status": "COMPLETED",
        "outcome_reason": None,
        "external_operation_status": "COMMITTED",
        "direction": "expense",
        "amount": Decimal("1200.00"),
        "transaction_date": datetime(2026, 7, 29, 15, 0, tzinfo=UTC),
        "date_source": "DOCUMENT",
        "document_date": "2026-07-29",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_terminal_notification_type_matrix() -> None:
    db = Mock()
    db.scalar.return_value = True
    assert notification_type_for_item(db, _item()) == EXPENSE_COMMITTED
    assert (
        notification_type_for_item(
            db, _item(status="IGNORED", outcome_reason="INCOME_OUT_OF_SCOPE")
        )
        == INCOME_OUT_OF_SCOPE
    )
    assert (
        notification_type_for_item(db, _item(status="EXTRACTION_FAILED"))
        == EXTRACTION_FAILED
    )
    assert (
        notification_type_for_item(db, _item(status="PERSISTENCE_FAILED"))
        == PERSISTENCE_FAILED
    )
    assert (
        notification_type_for_item(
            db, _item(status="IGNORED", outcome_reason="another_reason")
        )
        is None
    )


def test_completed_requires_committed_record_evidence() -> None:
    db = Mock()
    db.scalar.return_value = False
    assert notification_type_for_item(db, _item()) is None
    db.scalar.return_value = True
    for field, value in (
        ("external_operation_status", "PENDING"),
        ("direction", "income"),
        ("amount", Decimal("0")),
        ("transaction_date", None),
    ):
        assert notification_type_for_item(db, _item(**{field: value})) is None


def test_nonterminal_and_ambiguous_states_are_ineligible() -> None:
    db = Mock()
    for status in (
        "RECEIVED",
        "EXTRACTING",
        "EXTRACTED",
        "READY",
        "ACTIVE",
        "VALIDATING",
        "WAITING_USER_INPUT",
        "PERSISTING",
        "PERSIST_RETRYABLE",
        "PERSIST_OUTCOME_UNKNOWN",
        "FAILED",
        "EXPIRED",
        "CANCELLED",
    ):
        assert notification_type_for_item(db, _item(status=status)) is None


def test_success_formatter_adapter_reuses_frozen_gate5_formatter() -> None:
    message = message_for_item(_item(), EXPENSE_COMMITTED)
    assert message == (
        "✅ Gravado com sucesso.\n\n"
        "Despesa de R$ 1.200,00 realizada em 29/07/2026."
    )
    fallback = message_for_item(
        _item(
            date_source="MESSAGE_TIMESTAMP",
            document_date=None,
            transaction_date=datetime(2026, 8, 2, 2, 30, tzinfo=UTC),
        ),
        EXPENSE_COMMITTED,
    )
    assert fallback.endswith("realizada em 01/08/2026.")


def test_final_user_messages_are_exact_and_sanitized() -> None:
    assert message_for_item(_item(), INCOME_OUT_OF_SCOPE) == INCOME_OUT_OF_SCOPE_MESSAGE
    assert message_for_item(_item(), EXTRACTION_FAILED) == EXTRACTION_FAILED_MESSAGE
    assert message_for_item(_item(), PERSISTENCE_FAILED) == PERSISTENCE_FAILED_MESSAGE
    combined = " ".join(
        (
            INCOME_OUT_OF_SCOPE_MESSAGE,
            EXTRACTION_FAILED_MESSAGE,
            PERSISTENCE_FAILED_MESSAGE,
        )
    ).lower()
    for internal in ("gemini", "sql", "writer", "record id", "error_code"):
        assert internal not in combined


def test_stable_outbound_identity_is_deterministic_and_bounded() -> None:
    assert reservation_key("item-1", EXPENSE_COMMITTED) == (
        "item-1:FINAL_NOTIFICATION_RESERVED:EXPENSE_COMMITTED"
    )
    assert dispatch_key("item-1", EXPENSE_COMMITTED) == (
        "item-1:FINAL_NOTIFICATION_DISPATCHED:EXPENSE_COMMITTED"
    )
    assert final_key("item-1", EXPENSE_COMMITTED) == (
        "item-1:FINAL_NOTIFICATION_FINAL:EXPENSE_COMMITTED"
    )
    assert outbound_message_id("item-1", EXPENSE_COMMITTED) == (
        "final_item-1_expense_committed"
    )
    with pytest.raises(ValueError):
        reservation_key("x" * 512, EXPENSE_COMMITTED)


def test_notifier_bounds_are_frozen() -> None:
    assert FINAL_NOTIFICATION_BATCH_SIZE == 100
    assert FINAL_NOTIFICATION_DISPATCH_CONCURRENCY == 1
    assert FINAL_NOTIFICATION_POLL_INTERVAL_SECONDS == 1.0
    assert FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS == 60
    assert FINAL_NOTIFICATION_SHUTDOWN_JOIN_SECONDS == 1.0


def _prepare_notifier_and_ready_business(engine, writer_engine):
    terminal_context = e2e.create_context(engine, writer_engine)
    terminal_id, _ = e2e.ingest_and_extract(
        engine, terminal_context, e2e.expense_normalized()
    )
    assert e2e.process_next_business(engine, "terminal") == terminal_id
    ready_context = e2e.create_context(engine, writer_engine)
    ready_id, _ = e2e.ingest_and_extract(
        engine, ready_context, e2e.expense_normalized()
    )
    return terminal_id, ready_id


def _start_runtime_notifier(
    engine,
    monkeypatch: pytest.MonkeyPatch,
    sender,
) -> tuple[threading.Event, threading.Thread]:
    shutdown = threading.Event()
    monkeypatch.setattr(fifo_worker, "_send_final_notification", sender)
    thread = fifo_worker._start_final_notification_thread(
        sessionmaker(bind=engine, expire_on_commit=False), shutdown
    )
    return shutdown, thread


@pytest.mark.real_pg15
def test_slow_final_sender_does_not_delay_next_business_claim(
    engine, writer_engine, writer_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ready_id = _prepare_notifier_and_ready_business(engine, writer_engine)
    entered, release = threading.Event(), threading.Event()

    def slow_sender(*_args: str) -> bool:
        entered.set()
        assert release.wait(2)
        return True

    shutdown, thread = _start_runtime_notifier(engine, monkeypatch, slow_sender)
    assert entered.wait(2)
    assert e2e.process_next_business(engine, "while-slow") == ready_id
    with Session(engine) as db:
        assert db.get(ProcessingItem, ready_id).status == "COMPLETED"
    release.set()
    shutdown.set()
    thread.join(2)


@pytest.mark.real_pg15
def test_final_sender_timeout_does_not_stop_business_loop(
    engine, writer_engine, writer_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ready_id = _prepare_notifier_and_ready_business(engine, writer_engine)
    attempted = threading.Event()

    def timeout(*_args: object, **_kwargs: object) -> bool:
        attempted.set()
        raise TimeoutError("deterministic local timeout")

    shutdown, thread = _start_runtime_notifier(engine, monkeypatch, timeout)
    assert attempted.wait(2)
    assert e2e.process_next_business(engine, "after-timeout") == ready_id
    with Session(engine) as db:
        assert db.get(ProcessingItem, ready_id).status == "COMPLETED"
    assert thread.is_alive()
    shutdown.set()
    thread.join(2)


@pytest.mark.real_pg15
def test_notifier_exception_does_not_stop_business_loop(
    engine, writer_engine, writer_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, ready_id = _prepare_notifier_and_ready_business(engine, writer_engine)
    attempted = threading.Event()

    def failing_sender(*_args: str) -> bool:
        attempted.set()
        raise RuntimeError("sanitized")

    shutdown, thread = _start_runtime_notifier(engine, monkeypatch, failing_sender)
    assert attempted.wait(2)
    assert e2e.process_next_business(engine, "after-exception") == ready_id
    with Session(engine) as db:
        assert db.get(ProcessingItem, ready_id).status == "COMPLETED"
    assert thread.is_alive()
    shutdown.set()
    thread.join(2)


@pytest.mark.real_pg15
def test_notification_backlog_does_not_starve_business_claims(
    engine, writer_engine, writer_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_context = e2e.create_context(engine, writer_engine)
    for n in range(5):
        terminal_id, _ = e2e.ingest_and_extract(
            engine, terminal_context, e2e.expense_normalized()
        )
        assert e2e.process_next_business(engine, f"terminal-{n}") == terminal_id
    ready_context = e2e.create_context(engine, writer_engine)
    ready_id, _ = e2e.ingest_and_extract(engine, ready_context, e2e.expense_normalized())
    entered, release = threading.Event(), threading.Event()

    def blocked(*_args: str) -> bool:
        entered.set()
        assert release.wait(2)
        return True

    shutdown, thread = _start_runtime_notifier(engine, monkeypatch, blocked)
    assert entered.wait(2)
    assert e2e.process_next_business(engine, "backlog-business") == ready_id
    with Session(engine) as db:
        assert db.get(ProcessingItem, ready_id).status == "COMPLETED"
        assert db.scalar(
            select(Execution).where(Execution.operation == "FINAL_NOTIFICATION_DISPATCHED")
        ) is not None
    release.set()
    shutdown.set()
    thread.join(2)


@pytest.mark.real_pg15
def test_business_and_notifier_loops_preserve_fifo_sequence(
    engine, writer_engine, writer_client, monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_context = e2e.create_context(engine, writer_engine)
    terminal_id, _ = e2e.ingest_and_extract(
        engine, terminal_context, e2e.expense_normalized()
    )
    assert e2e.process_next_business(engine, "notifier-seed") == terminal_id
    business_context = e2e.create_context(engine, writer_engine)
    item_ids = [
        e2e.ingest_and_extract(engine, business_context, e2e.expense_normalized())[0]
        for _ in range(5)
    ]
    entered, release = threading.Event(), threading.Event()

    def blocked(*_args: str) -> bool:
        entered.set()
        assert release.wait(3)
        return True

    shutdown, thread = _start_runtime_notifier(engine, monkeypatch, blocked)
    assert entered.wait(2)
    processed = [e2e.process_next_business(engine, f"fifo-{n}") for n in range(5)]
    assert processed == item_ids
    with Session(engine) as db:
        sequences = list(
            db.scalars(
                select(ProcessingItem.sequence)
                .where(ProcessingItem.id.in_(item_ids))
                .order_by(ProcessingItem.sequence)
            )
        )
        assert sequences == [1, 2, 3, 4, 5]
        assert all(db.get(ProcessingItem, item_id).status == "COMPLETED" for item_id in item_ids)
    release.set()
    shutdown.set()
    thread.join(3)
