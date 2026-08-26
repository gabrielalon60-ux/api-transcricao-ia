from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.models import Event, ProcessingItem, UserInteraction
from orchestrator import fifo_worker
from orchestrator.fifo_worker import WorkerClaimTracker
from orchestrator.services.business_rules_evaluator import BusinessRulesEvaluatorService
from orchestrator.services.fifo_worker_service import (
    claim_next_ready_item,
    claim_next_resumable_validating_item,
    evaluate_and_persist_validating_item,
    ignore_income_out_of_scope,
)
from orchestrator.services.stale_recovery_service import recover_stale_validating_items
from orchestrator.services.persistence_service import (
    transition_validating_to_persisting,
)

pytestmark = pytest.mark.real_pg15



ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv(
    "GATE7_PLATFORM_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/platform_gate4_test",
)


@pytest.fixture(scope="module")
def engine():
    value = create_engine(URL, pool_size=10, max_overflow=5)
    try:
        with value.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL 15 unavailable: {exc}")
    config = Config(str(ROOT / "packages/db/alembic.ini"))
    config.set_main_option("sqlalchemy.url", URL)
    command.upgrade(config, "head")
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE organizations CASCADE"))


def _context(engine) -> tuple[str, str, str, str]:
    org, bot, inst, user = (str(uuid.uuid4()) for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations(id,name,slug,status) VALUES (:id,'O',:slug,'ACTIVE')"
            ),
            {"id": org, "slug": org},
        )
        connection.execute(
            text(
                "INSERT INTO bots(id,organization_id,name,service_key,status) VALUES (:id,:org,'B',:key,'ACTIVE')"
            ),
            {"id": bot, "org": org, "key": bot},
        )
        connection.execute(
            text(
                "INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,phone_number,status) VALUES (:id,:org,:bot,'WUZAPI',:ext,:phone,'ACTIVE')"
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
                "INSERT INTO users(id,organization_id,phone_number,status) VALUES (:id,:org,:phone,'ACTIVE')"
            ),
            {"id": user, "org": org, "phone": f"54{uuid.uuid4().int}"[:15]},
        )
    return org, inst, user, bot


def _item(
    db: Session, context: tuple[str, str, str, str], sequence: int, status: str
) -> ProcessingItem:
    org, inst, user, _ = context
    now = datetime.now(timezone.utc)
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
        claimed_by="worker-1" if status == "VALIDATING" else None,
        lease_expires_at=now + timedelta(minutes=5) if status == "VALIDATING" else None,
        heartbeat_at=now if status == "VALIDATING" else None,
        attempt_count=1 if status == "VALIDATING" else 0,
        message_received_at=now + timedelta(seconds=sequence),
        file_mime_type="image/jpeg",
        file_size=1,
        file_sha256=f"{sequence:064d}",
        document_type="pix_receipt",
        normalized_data={},
        direction="income",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_income_ignored_releases_fifo_and_is_not_recovered(engine) -> None:
    context = _context(engine)
    with Session(engine) as db:
        income = _item(db, context, 1, "VALIDATING")
        ready = _item(db, context, 2, "READY")
        _, decision = evaluate_and_persist_validating_item(
            db, income.id, "worker-1", BusinessRulesEvaluatorService(["00000000000000"])
        )
        assert decision.direction == "income"
        assert decision.question_type is None
        ignored = ignore_income_out_of_scope(db, income.id, "worker-1")
        assert ignored is not None
        assert (ignored.status, ignored.outcome_reason) == (
            "IGNORED",
            "INCOME_OUT_OF_SCOPE",
        )
        assert ignored.error_code is None
        assert ignore_income_out_of_scope(db, income.id, "worker-1").id == income.id
        assert recover_stale_validating_items(db) == 0
        assert claim_next_resumable_validating_item(db, "worker-2") is None
        claimed = claim_next_ready_item(db, "worker-2")
        assert claimed is not None and claimed.id == ready.id


def test_income_worker_path_has_zero_writer_supplier_prompt_or_notification(
    engine, monkeypatch
) -> None:
    context = _context(engine)
    calls = {"writer": 0, "prompt": 0, "persistence": 0, "notification": 0}

    class ForbiddenWriter:
        def __init__(self):
            calls["writer"] += 1
            raise AssertionError("income must not construct the expense Writer client")

    def forbidden_prompt(*_args, **_kwargs):
        calls["prompt"] += 1
        raise AssertionError("income must not open an interaction")

    def forbidden_persistence(*_args, **_kwargs):
        calls["persistence"] += 1
        raise AssertionError("income must not enter persistence")

    def forbidden_notification(*_args, **_kwargs):
        calls["notification"] += 1
        raise AssertionError("Gate 7 must not send a final notification")

    monkeypatch.setattr(fifo_worker, "DBWriterClient", ForbiddenWriter)
    monkeypatch.setattr(fifo_worker, "dispatch_user_prompt", forbidden_prompt)
    monkeypatch.setattr(
        fifo_worker, "transition_validating_to_persisting", forbidden_persistence
    )
    monkeypatch.setattr(fifo_worker, "_send_gate6_prompt", forbidden_notification)

    with Session(engine) as db:
        item = _item(db, context, 1, "VALIDATING")
        tracker = WorkerClaimTracker("worker-1")
        tracker.add_claim(item.id)
        fifo_worker._process_validating_item(
            db,
            item,
            "worker-1",
            BusinessRulesEvaluatorService(["00000000000000"]),
            tracker,
        )
        db.refresh(item)
        assert (item.status, item.outcome_reason) == (
            "IGNORED",
            "INCOME_OUT_OF_SCOPE",
        )
        assert db.query(UserInteraction).count() == 0
        assert item.id not in tracker.owned_claims
    assert calls == {"writer": 0, "prompt": 0, "persistence": 0, "notification": 0}


def test_ignored_constraint_rejects_wrong_reason(engine) -> None:
    context = _context(engine)
    with Session(engine) as db:
        item = _item(db, context, 1, "READY")
        with pytest.raises(Exception):
            db.execute(
                text(
                    "UPDATE processing_items SET status='IGNORED', outcome_reason=NULL WHERE id=:id"
                ),
                {"id": item.id},
            )
            db.commit()
        db.rollback()


@pytest.mark.parametrize(
    "invalid_field",
    ["direction", "amount", "transaction_date", "enterprise_id"],
)
def test_persistence_transition_fails_closed_on_missing_prerequisite(
    engine, invalid_field
) -> None:
    context = _context(engine)
    with Session(engine) as db:
        item = _item(db, context, 1, "VALIDATING")
        item.direction = "expense"
        item.amount = Decimal("10.00")
        item.transaction_date = datetime.now(timezone.utc)
        item.enterprise_id = str(uuid.uuid4())
        setattr(item, invalid_field, None)
        db.commit()
        assert (
            transition_validating_to_persisting(
                db, item.id, require_gate7_expense_destination=True
            )
            is None
        )
        db.refresh(item)
        assert item.status == "VALIDATING"
