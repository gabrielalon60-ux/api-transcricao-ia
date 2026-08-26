from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from db.models import Event, Execution, ProcessingItem
from orchestrator import fifo_worker
from orchestrator.services.final_notification_service import (
    EXPENSE_COMMITTED,
    FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS,
    claim_reserved_notification,
    final_key,
    finalize_notification,
    finalize_stale_dispatched_notifications,
    reserve_final_notifications,
    run_final_notification_iteration,
)

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv(
    "GATE8_PLATFORM_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/platform_gate4_test",
)


@pytest.fixture(scope="module")
def engine():
    value = create_engine(URL, pool_size=10, max_overflow=5)
    with value.connect() as connection:
        assert connection.scalar(text("SHOW server_version"))
    config = Config(str(ROOT / "packages/db/alembic.ini"))
    config.set_main_option("sqlalchemy.url", URL)
    command.upgrade(config, "head")
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE organizations CASCADE"))
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE organizations CASCADE"))


def _context(engine) -> tuple[str, str, str]:
    org, bot, inst, user = (str(uuid.uuid4()) for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations(id,name,slug,status) "
                "VALUES (:id,'O',:slug,'ACTIVE')"
            ),
            {"id": org, "slug": org},
        )
        connection.execute(
            text(
                "INSERT INTO bots(id,organization_id,name,service_key,status) "
                "VALUES (:id,:org,'B',:key,'ACTIVE')"
            ),
            {"id": bot, "org": org, "key": bot},
        )
        connection.execute(
            text(
                "INSERT INTO instances(id,organization_id,bot_id,provider,"
                "external_instance_id,phone_number,status) "
                "VALUES (:id,:org,:bot,'WUZAPI',:ext,:phone,'ACTIVE')"
            ),
            {
                "id": inst,
                "org": org,
                "bot": bot,
                "ext": inst,
                "phone": f"55{uuid.uuid4().int}"[:15],
            },
        )
        connection.execute(
            text(
                "INSERT INTO users(id,organization_id,phone_number,status) "
                "VALUES (:id,:org,:phone,'ACTIVE')"
            ),
            {"id": user, "org": org, "phone": f"54{uuid.uuid4().int}"[:15]},
        )
    return org, inst, user


def create_terminal_item(
    engine,
    *,
    status: str = "COMPLETED",
    sequence: int = 1,
    direction: str = "expense",
    external_operation_status: str | None = "COMMITTED",
    persistence_proof: bool = True,
    context: tuple[str, str, str] | None = None,
) -> str:
    org, inst, user = context or _context(engine)
    now = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)
    with Session(engine) as db:
        event = Event(
            correlation_id=str(uuid.uuid4()),
            provider="WUZAPI",
            external_instance_id=inst,
            external_message_id=str(uuid.uuid4()),
            organization_id=org,
            instance_id=inst,
            user_id=user,
            message_type="image",
            status="ROUTED",
        )
        db.add(event)
        db.flush()
        item = ProcessingItem(
            event_id=event.id,
            correlation_id=event.correlation_id,
            organization_id=org,
            instance_id=inst,
            user_id=user,
            sequence=sequence,
            status=status,
            outcome_reason=("INCOME_OUT_OF_SCOPE" if status == "IGNORED" else None),
            message_received_at=now + timedelta(seconds=sequence),
            file_mime_type="image/jpeg",
            file_size=1,
            file_sha256=f"{sequence:064d}",
            direction=direction,
            amount=Decimal("1200.00"),
            document_date="2026-07-29",
            transaction_date=now,
            date_source="DOCUMENT",
            external_operation_status=(
                external_operation_status if status == "COMPLETED" else None
            ),
            completed_at=now,
        )
        db.add(item)
        db.flush()
        if status == "COMPLETED" and persistence_proof:
            db.add(
                Execution(
                    event_id=event.id,
                    processing_item_id=item.id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="PERSISTENCE_COMMITTED",
                    status="SUCCESS",
                    effect_status="ACKNOWLEDGED",
                    external_reference=f"record-{item.id}",
                    completed_at=now,
                )
            )
        db.commit()
        return item.id


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_concurrent_reservation_creates_one_logical_intent(engine) -> None:
    item_id = create_terminal_item(engine)
    barrier = threading.Barrier(2)

    def reserve() -> int:
        barrier.wait()
        with Session(engine) as db:
            return reserve_final_notifications(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    with Session(engine) as db:
        rows = list(
            db.scalars(
                select(Execution).where(
                    Execution.processing_item_id == item_id,
                    Execution.operation == "FINAL_NOTIFICATION_RESERVED",
                )
            )
        )
    assert sum(results) == 1
    assert len(rows) == 1


def test_concurrent_dispatch_creates_one_owner_and_one_outbound_attempt(engine) -> None:
    create_terminal_item(engine)
    with Session(engine) as db:
        assert reserve_final_notifications(db) == 1
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        with Session(engine) as db:
            return claim_reserved_notification(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    owners = [claim for claim in claims if claim is not None]
    outbound_attempts = [owner.outbound_message_id for owner in owners]
    assert len(owners) == 1
    assert len(outbound_attempts) == 1


def test_dispatch_checkpoint_commits_before_sender_invocation(engine) -> None:
    item_id = create_terminal_item(engine)

    def sender(_phone: str, _message: str, outbound_id: str) -> bool:
        with Session(engine) as inspection:
            dispatch = inspection.scalar(
                select(Execution).where(Execution.outbound_message_id == outbound_id)
            )
            assert dispatch is not None
            assert dispatch.processing_item_id == item_id
        return True

    assert run_final_notification_iteration(_factory(engine), sender)


def test_reserved_without_dispatch_is_restart_recoverable(engine) -> None:
    create_terminal_item(engine)
    with Session(engine) as db:
        reserve_final_notifications(db)
    with Session(engine) as db:
        first = claim_reserved_notification(db)
    assert first is not None
    assert first.outbound_message_id == f"final_{first.processing_item_id}_expense_committed"


def test_dispatched_without_finalization_becomes_unknown_without_resend(engine) -> None:
    create_terminal_item(engine)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    with Session(engine) as db:
        reserve_final_notifications(db, now=base)
    with Session(engine) as db:
        intent = claim_reserved_notification(db, now=base)
    assert intent is not None
    with Session(engine) as db:
        assert (
            finalize_stale_dispatched_notifications(
                db,
                now=base
                + timedelta(seconds=FINAL_NOTIFICATION_DISPATCH_GRACE_SECONDS + 1),
            )
            == 1
        )
    calls = 0

    def forbidden(*_args: str) -> bool:
        nonlocal calls
        calls += 1
        return True

    assert not run_final_notification_iteration(
        _factory(engine), forbidden, now=base + timedelta(minutes=2)
    )
    assert calls == 0


def test_acknowledged_and_unknown_share_one_finalization_identity(engine) -> None:
    create_terminal_item(engine)
    with Session(engine) as db:
        reserve_final_notifications(db)
    with Session(engine) as db:
        intent = claim_reserved_notification(db)
    assert intent is not None
    with Session(engine) as db:
        assert finalize_notification(db, intent, acknowledged=True)
    with Session(engine) as db:
        assert not finalize_notification(db, intent, acknowledged=False)
        count = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.operation_idempotency_key
                == final_key(intent.processing_item_id, EXPENSE_COMMITTED)
            )
        )
    assert count == 1


def test_terminal_items_created_before_notifier_startup_are_discovered(engine) -> None:
    item_id = create_terminal_item(engine)
    sent: list[str] = []
    assert run_final_notification_iteration(
        _factory(engine), lambda _p, _m, outbound: not sent.append(outbound)
    )
    assert sent == [f"final_{item_id}_expense_committed"]


def test_notification_outcome_does_not_change_business_state_or_fifo(engine) -> None:
    item_id = create_terminal_item(engine, sequence=7)
    assert run_final_notification_iteration(
        _factory(engine), lambda _p, _m, _o: False
    )
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        assert (item.status, item.sequence, item.external_operation_status) == (
            "COMPLETED",
            7,
            "COMMITTED",
        )


def test_retryable_and_persistence_outcome_unknown_create_no_intent(engine) -> None:
    create_terminal_item(
        engine,
        status="PERSIST_RETRYABLE",
        external_operation_status="RETRYABLE",
        persistence_proof=False,
    )
    create_terminal_item(
        engine,
        status="PERSIST_OUTCOME_UNKNOWN",
        sequence=2,
        external_operation_status="OUTCOME_UNKNOWN",
        persistence_proof=False,
    )
    with Session(engine) as db:
        assert reserve_final_notifications(db) == 0


@pytest.mark.parametrize(
    ("operation", "status", "effect_status"),
    [
        ("FINAL_NOTIFICATION_ACKNOWLEDGED", "SUCCESS", "ACKNOWLEDGED"),
        (
            "FINAL_NOTIFICATION_OUTCOME_UNKNOWN",
            "FAILED",
            "OUTBOUND_OUTCOME_UNKNOWN",
        ),
    ],
)
@pytest.mark.parametrize("requested_batch_size", [101, 1000])
def test_existing_finalization_prevents_reservation_and_outbound(
    engine,
    operation: str,
    status: str,
    effect_status: str,
    requested_batch_size: int,
) -> None:
    item_id = create_terminal_item(engine)
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        db.add(
            Execution(
                event_id=item.event_id,
                processing_item_id=item.id,
                correlation_id=item.correlation_id,
                component="BOT_DF",
                operation=operation,
                operation_idempotency_key=final_key(item.id, EXPENSE_COMMITTED),
                status=status,
                effect_status=effect_status,
                completed_at=datetime.now(UTC),
            )
        )
        db.commit()
    with Session(engine) as db:
        assert reserve_final_notifications(db, batch_size=requested_batch_size) == 0
    sends = 0

    def forbidden(*_args: str) -> bool:
        nonlocal sends
        sends += 1
        return True

    assert not run_final_notification_iteration(_factory(engine), forbidden)
    assert sends == 0


def test_requested_batch_size_is_hard_clamped_to_one_hundred(engine) -> None:
    context = _context(engine)
    org, inst, user = context
    now = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)
    with Session(engine) as db:
        for sequence in range(1, 202):
            event = Event(
                correlation_id=str(uuid.uuid4()),
                provider="WUZAPI",
                external_instance_id=inst,
                external_message_id=str(uuid.uuid4()),
                organization_id=org,
                instance_id=inst,
                user_id=user,
                message_type="image",
                status="ROUTED",
            )
            db.add(event)
            db.flush()
            item = ProcessingItem(
                event_id=event.id,
                correlation_id=event.correlation_id,
                organization_id=org,
                instance_id=inst,
                user_id=user,
                sequence=sequence,
                status="COMPLETED",
                message_received_at=now + timedelta(seconds=sequence),
                file_mime_type="image/jpeg",
                file_size=1,
                file_sha256=f"{sequence:064d}",
                direction="expense",
                amount=Decimal("1.00"),
                document_date="2026-07-29",
                transaction_date=now,
                date_source="DOCUMENT",
                external_operation_status="COMMITTED",
                completed_at=now,
            )
            db.add(item)
            db.flush()
            db.add(
                Execution(
                    event_id=event.id,
                    processing_item_id=item.id,
                    correlation_id=item.correlation_id,
                    component="BOT_DF",
                    operation="PERSISTENCE_COMMITTED",
                    status="SUCCESS",
                    external_reference=f"record-{item.id}",
                    completed_at=now,
                )
            )
        db.commit()
    with Session(engine) as db:
        assert reserve_final_notifications(db, batch_size=101) == 100
    with Session(engine) as db:
        assert reserve_final_notifications(db, batch_size=1000) == 100
    with Session(engine) as db:
        assert reserve_final_notifications(db, batch_size=1000) == 1


def test_dispatch_grace_boundary_never_resends(engine) -> None:
    create_terminal_item(engine)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    with Session(engine) as db:
        reserve_final_notifications(db, now=base)
    with Session(engine) as db:
        intent = claim_reserved_notification(db, now=base)
    assert intent is not None
    with Session(engine) as db:
        assert finalize_stale_dispatched_notifications(
            db, now=base + timedelta(seconds=59)
        ) == 0
        assert db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.operation == "FINAL_NOTIFICATION_OUTCOME_UNKNOWN"
            )
        ) == 0
    sends = 0

    def forbidden(*_args: str) -> bool:
        nonlocal sends
        sends += 1
        return True

    assert not run_final_notification_iteration(
        _factory(engine), forbidden, now=base + timedelta(seconds=59)
    )
    with Session(engine) as db:
        assert finalize_stale_dispatched_notifications(
            db, now=base + timedelta(seconds=60)
        ) == 1
    assert not run_final_notification_iteration(
        _factory(engine), forbidden, now=base + timedelta(seconds=61)
    )
    assert sends == 0


def test_shutdown_after_dispatched_recovers_unknown_without_resend(
    engine, monkeypatch
) -> None:
    item_id = create_terminal_item(engine)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    clock = {"now": base}
    entered = threading.Event()
    release = threading.Event()
    outbound_attempts: list[str] = []
    active_shutdown: dict[str, threading.Event | None] = {"event": None}
    iteration_completed = threading.Event()

    def clocked_runtime_iteration(
        session_factory,
        sender,
        *,
        stop_requested=None,
    ) -> bool:
        try:
            return run_final_notification_iteration(
                session_factory,
                sender,
                now=clock["now"],
                stop_requested=stop_requested,
            )
        finally:
            iteration_completed.set()
            shutdown = active_shutdown["event"]
            if shutdown is not None:
                shutdown.set()

    def process_loss_sender(_phone: str, _message: str, outbound: str) -> bool:
        outbound_attempts.append(outbound)
        entered.set()
        assert release.wait(2)
        raise SystemExit

    monkeypatch.setattr(
        fifo_worker,
        "run_final_notification_iteration",
        clocked_runtime_iteration,
    )
    monkeypatch.setattr(fifo_worker, "_send_final_notification", process_loss_sender)

    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        business_state = (
            item.status,
            item.outcome_reason,
            item.external_operation_status,
            item.sequence,
            item.direction,
            item.amount,
        )

    shutdown = threading.Event()
    active_shutdown["event"] = shutdown

    def run_runtime() -> None:
        try:
            fifo_worker.run_final_notification_loop(_factory(engine), shutdown)
        except SystemExit:
            pass

    thread = threading.Thread(target=run_runtime)
    thread.start()
    assert entered.wait(2)
    shutdown.set()
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert outbound_attempts == [f"final_{item_id}_expense_committed"]

    with Session(engine) as db:
        dispatch = db.scalar(
            select(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_DISPATCHED",
            )
        )
        assert dispatch is not None and dispatch.completed_at is not None
        assert dispatch.completed_at == base

    clock["now"] = base + timedelta(seconds=59)
    iteration_completed.clear()
    restart_59_shutdown = threading.Event()
    active_shutdown["event"] = restart_59_shutdown
    restart_59 = fifo_worker._start_final_notification_thread(
        _factory(engine), restart_59_shutdown
    )
    assert iteration_completed.wait(2)
    restart_59.join(2)
    assert not restart_59.is_alive()
    with Session(engine) as db:
        reserved_59 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_RESERVED",
            )
        )
        dispatched_59 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_DISPATCHED",
            )
        )
        acknowledged_59 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_ACKNOWLEDGED",
            )
        )
        unknown_59 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_OUTCOME_UNKNOWN",
            )
        )
        final_59 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.operation_idempotency_key
                == final_key(item_id, EXPENSE_COMMITTED)
            )
        )
    assert (reserved_59, dispatched_59, acknowledged_59, unknown_59, final_59) == (
        1,
        1,
        0,
        0,
        0,
    )
    assert len(outbound_attempts) == 1

    clock["now"] = base + timedelta(seconds=60)
    iteration_completed.clear()
    restart_60_shutdown = threading.Event()
    active_shutdown["event"] = restart_60_shutdown
    restart_60 = fifo_worker._start_final_notification_thread(
        _factory(engine), restart_60_shutdown
    )
    assert iteration_completed.wait(2)
    restart_60.join(2)
    assert not restart_60.is_alive()
    with Session(engine) as db:
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        final_rows = list(
            db.scalars(
                select(Execution).where(
                    Execution.operation_idempotency_key
                    == final_key(item_id, EXPENSE_COMMITTED)
                )
            )
        )
        reserved_60 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_RESERVED",
            )
        )
        dispatched_60 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_DISPATCHED",
            )
        )
        acknowledged_60 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_ACKNOWLEDGED",
            )
        )
        unknown_60 = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_OUTCOME_UNKNOWN",
            )
        )
        final_business_state = (
            item.status,
            item.outcome_reason,
            item.external_operation_status,
            item.sequence,
            item.direction,
            item.amount,
        )
    assert (reserved_60, dispatched_60, acknowledged_60, unknown_60) == (1, 1, 0, 1)
    assert len(final_rows) == 1
    assert final_rows[0].operation == "FINAL_NOTIFICATION_OUTCOME_UNKNOWN"
    assert len(outbound_attempts) == 1
    assert final_business_state == business_state


def test_shutdown_at_reserved_recovers_same_intent(engine, monkeypatch) -> None:
    item_id = create_terminal_item(engine)
    with Session(engine) as db:
        assert reserve_final_notifications(db) == 1
        reservation = db.scalar(
            select(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_RESERVED",
            )
        )
        assert reservation is not None
        reservation_identity = reservation.operation_idempotency_key

    shutdown_before_dispatch = threading.Event()
    shutdown_before_dispatch.set()
    fifo_worker.run_final_notification_loop(_factory(engine), shutdown_before_dispatch)
    with Session(engine) as db:
        assert db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_DISPATCHED",
            )
        ) == 0

    sent: list[str] = []
    restarted_shutdown = threading.Event()

    def sender(_phone: str, _message: str, outbound: str) -> bool:
        sent.append(outbound)
        restarted_shutdown.set()
        return True

    monkeypatch.setattr(fifo_worker, "_send_final_notification", sender)
    thread = fifo_worker._start_final_notification_thread(
        _factory(engine), restarted_shutdown
    )
    thread.join(2)
    assert sent == [f"final_{item_id}_expense_committed"]
    with Session(engine) as db:
        reservation = db.scalar(
            select(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_RESERVED",
            )
        )
        dispatch_count = db.scalar(
            select(text("count(*)")).select_from(Execution).where(
                Execution.processing_item_id == item_id,
                Execution.operation == "FINAL_NOTIFICATION_DISPATCHED",
            )
        )
    assert reservation is not None
    assert reservation.operation_idempotency_key == reservation_identity
    assert dispatch_count == 1
