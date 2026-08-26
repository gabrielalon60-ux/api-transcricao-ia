from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.models import Event, ProcessingItem, UserAnswer, UserInteraction
from orchestrator.services.business_rules_evaluator import BusinessRulesEvaluatorService
from orchestrator.services.fifo_worker_service import (
    Gate6DecisionConflict,
    claim_next_ready_item,
    claim_next_resumable_validating_item,
    evaluate_and_persist_validating_item,
)
from orchestrator.services.cancel_command_handler import handle_cancel_command
from orchestrator.services.stale_recovery_service import recover_stale_validating_items
from orchestrator.services.user_interaction_service import (
    apply_user_answer,
    create_or_get_open_interaction,
    dispatch_user_prompt,
)
from orchestrator.services.waiting_input_sweeper import expire_waiting_user_input_items

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "packages" / "db" / "alembic.ini"
DISPOSABLE_DB_URL = os.getenv(
    "GATE4_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/platform_gate4_test",
)
DF_ID = "00000000000000"


@pytest.fixture(scope="module")
def disposable_postgres():
    engine = create_engine(
        DISPOSABLE_DB_URL,
        pool_size=20,
        max_overflow=10,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT version()"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL 15 is not accessible: {exc}")

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(disposable_postgres):
    yield
    with disposable_postgres.begin() as connection:
        connection.execute(text(
            "TRUNCATE user_answers, user_interactions, service_usage, executions, "
            "processing_items, conversation_queue_counters, events, registration_rate_limits, "
            "registration_attempts, instances, users, bots, organizations CASCADE"
        ))


def _context(engine, suffix: str = "") -> tuple[str, str, str]:
    org_id, bot_id, instance_id, user_id = (str(uuid.uuid4()) for _ in range(4))
    unique = f"{uuid.uuid4().int}"[-8:]
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO organizations (id,name,slug,status) VALUES (:id,'Org',:slug,'ACTIVE')"), {"id": org_id, "slug": f"g6-{org_id}"})
        connection.execute(text("INSERT INTO bots (id,organization_id,name,service_key,status) VALUES (:id,:org,'Bot',:key,'ACTIVE')"), {"id": bot_id, "org": org_id, "key": f"key-{bot_id}"})
        connection.execute(text("INSERT INTO instances (id,organization_id,bot_id,provider,external_instance_id,phone_number,status) VALUES (:id,:org,:bot,'WUZAPI',:ext,:phone,'ACTIVE')"), {"id": instance_id, "org": org_id, "bot": bot_id, "ext": f"ext-{instance_id}", "phone": f"55117{unique}"})
        connection.execute(text("INSERT INTO users (id,organization_id,phone_number,status) VALUES (:id,:org,:phone,'ACTIVE')"), {"id": user_id, "org": org_id, "phone": f"55118{unique}{suffix}"})
    return org_id, instance_id, user_id


def _item(
    engine,
    context: tuple[str, str, str],
    *,
    sequence: int = 1,
    status: str = "VALIDATING",
    amount: str | None = None,
    claimed_by: str | None = "worker-g6",
    lease_expires_at: datetime | None = None,
) -> str:
    org_id, instance_id, user_id = context
    event_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
    lease = lease_expires_at
    if claimed_by and lease is None:
        lease = datetime.now(timezone.utc) + timedelta(seconds=60)
    normalized = {
        "total_amount": amount,
        "invoice_date": "2026-08-08",
        "customer_cpf_cnpj": DF_ID,
        "supplier_cpf_cnpj": DF_ID,
    }
    with Session(engine) as db:
        db.add(Event(
            id=event_id,
            correlation_id=f"c-{event_id}",
            provider="WUZAPI",
            external_instance_id=f"ext-{instance_id}",
            external_message_id=f"msg-{event_id}",
            organization_id=org_id,
            instance_id=instance_id,
            user_id=user_id,
            message_type="image",
            status="RECEIVED",
        ))
        db.add(ProcessingItem(
            id=item_id,
            event_id=event_id,
            correlation_id=f"c-{event_id}",
            organization_id=org_id,
            instance_id=instance_id,
            user_id=user_id,
            sequence=sequence,
            status=status,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=100,
            file_sha256=uuid.uuid4().hex * 2,
            document_type="invoice",
            normalized_data=normalized,
            attempt_count=1,
            claimed_by=claimed_by,
            heartbeat_at=datetime.now(timezone.utc) if claimed_by else None,
            lease_expires_at=lease,
        ))
        db.commit()
    return item_id


def _answer_event(engine, context: tuple[str, str, str]) -> str:
    org_id, instance_id, user_id = context
    event_id = str(uuid.uuid4())
    with Session(engine) as db:
        db.add(Event(
            id=event_id,
            correlation_id=f"c-{event_id}",
            provider="WUZAPI",
            external_instance_id=f"ext-{instance_id}",
            external_message_id=f"answer-{event_id}",
            organization_id=org_id,
            instance_id=instance_id,
            user_id=user_id,
            message_type="text",
            status="RECEIVED",
        ))
        db.commit()
    return event_id


def _evaluator() -> BusinessRulesEvaluatorService:
    return BusinessRulesEvaluatorService([DF_ID])


def _evaluate(engine, item_id: str, worker: str = "g6"):
    with Session(engine) as db:
        return evaluate_and_persist_validating_item(db, item_id, worker, _evaluator())[1]


def test_g6_x01_third_of_five_waits_and_fourth_fifth_cannot_overtake(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    _item(disposable_postgres, context, sequence=1, status="COMPLETED", claimed_by=None)
    _item(disposable_postgres, context, sequence=2, status="COMPLETED", claimed_by=None)
    third_id = _item(disposable_postgres, context, sequence=3)
    decision = _evaluate(disposable_postgres, third_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            third_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    fourth_id = _item(disposable_postgres, context, sequence=4, status="READY", claimed_by=None)
    fifth_id = _item(disposable_postgres, context, sequence=5, status="READY", claimed_by=None)
    with Session(disposable_postgres) as db:
        assert claim_next_ready_item(db, "later") is None
        assert db.get(ProcessingItem, third_id).status == "WAITING_USER_INPUT"
        assert db.get(ProcessingItem, fourth_id).status == "READY"
        assert db.get(ProcessingItem, fifth_id).status == "READY"
        assert db.query(UserInteraction).filter_by(processing_item_id=third_id).count() == 1


@pytest.mark.parametrize(("raw_answer", "expected"), [("1", "income"), ("2", "expense")])
def test_g6_x02_x03_numeric_direction_resolves_active_item(
    disposable_postgres,
    raw_answer: str,
    expected: str,
) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context, amount="10.00")
    decision = _evaluate(disposable_postgres, item_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    with Session(disposable_postgres) as db:
        answer = apply_user_answer(db, _answer_event(disposable_postgres, context), raw_answer)
        item = db.get(ProcessingItem, item_id)
        assert answer.status == "APPLIED"
        assert item and item.status == "VALIDATING" and item.direction == expected


def test_g6_x01_x02_x03_x04_direction_then_amount_end_to_end(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)

    first = _evaluate(disposable_postgres, item_id)
    assert first.question_type == "transaction_direction"
    assert first.is_eligible_for_auto_write is False

    with Session(disposable_postgres) as db:
        interaction1 = dispatch_user_prompt(
            db,
            item_id,
            first.question_type,
            lambda *_: True,
            worker_id="worker-g6",
        )
        assert interaction1.generation == 1
    with Session(disposable_postgres) as db:
        answer1 = apply_user_answer(db, _answer_event(disposable_postgres, context), "2")
        assert answer1.status == "APPLIED"

    with Session(disposable_postgres) as db:
        resumed = claim_next_resumable_validating_item(db, "g6")
        assert resumed and resumed.id == item_id
    second = _evaluate(disposable_postgres, item_id)
    assert second.direction == "expense"
    assert second.question_type == "transaction_amount"

    with Session(disposable_postgres) as db:
        interaction2 = dispatch_user_prompt(
            db,
            item_id,
            second.question_type,
            lambda *_: True,
            worker_id="worker-g6",
        )
        assert interaction2.generation == 2
    with Session(disposable_postgres) as db:
        answer2 = apply_user_answer(db, _answer_event(disposable_postgres, context), "R$ 1.200,50")
        assert answer2.status == "APPLIED"

    with Session(disposable_postgres) as db:
        resumed = claim_next_resumable_validating_item(db, "g6")
        assert resumed and resumed.id == item_id
    final = _evaluate(disposable_postgres, item_id)
    assert final.direction == "expense"
    assert final.amount == Decimal("1200.50")
    assert final.question_type is None
    assert final.clarification_reason is None
    assert final.is_eligible_for_auto_write is True
    with Session(disposable_postgres) as db:
        interactions = db.query(UserInteraction).filter_by(processing_item_id=item_id).order_by(UserInteraction.generation).all()
        assert [(row.generation, row.question_type, row.status) for row in interactions] == [
            (1, "transaction_direction", "ANSWERED"),
            (2, "transaction_amount", "ANSWERED"),
        ]


def test_g6_x05_invalid_answer_keeps_generation_and_ttl(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, item_id)
    with Session(disposable_postgres) as db:
        interaction = dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
        original_expiry = interaction.expires_at
        interaction_id = interaction.id
    with Session(disposable_postgres) as db:
        answer = apply_user_answer(db, _answer_event(disposable_postgres, context), "3")
        assert answer.status == "REJECTED"
        item = db.get(ProcessingItem, item_id)
        interaction = db.get(UserInteraction, interaction_id)
        assert item and item.status == "WAITING_USER_INPUT"
        assert interaction and interaction.status == "WAITING" and interaction.expires_at == original_expiry
        assert db.query(UserInteraction).filter_by(processing_item_id=item_id).count() == 1


def test_g6_x06_x08_waiting_blocks_same_conversation_but_not_other(disposable_postgres) -> None:
    context1 = _context(disposable_postgres)
    first_id = _item(disposable_postgres, context1)
    decision = _evaluate(disposable_postgres, first_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            first_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    later_id = _item(disposable_postgres, context1, sequence=2, status="READY", claimed_by=None)
    context2 = _context(disposable_postgres, "9")
    other_id = _item(disposable_postgres, context2, status="READY", claimed_by=None)
    with Session(disposable_postgres) as db:
        claimed = claim_next_ready_item(db, "other")
        assert claimed and claimed.id == other_id
        assert db.get(ProcessingItem, later_id).status == "READY"


def test_g6_x07_x08_x09_expiry_releases_next_and_requires_resend(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    first_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, first_id)
    with Session(disposable_postgres) as db:
        interaction = dispatch_user_prompt(
            db,
            first_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
        interaction.waiting_since = datetime.now(timezone.utc) - timedelta(seconds=3601)
        interaction.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        item = db.get(ProcessingItem, first_id)
        assert item is not None
        item.waiting_since = interaction.waiting_since
        item.expires_at = interaction.expires_at
        db.commit()
    later_id = _item(disposable_postgres, context, sequence=2, status="READY", claimed_by=None)
    with Session(disposable_postgres) as db:
        assert expire_waiting_user_input_items(db) == 1
        expired = db.get(ProcessingItem, first_id)
        assert expired and expired.status == "EXPIRED"
        claimed = claim_next_ready_item(db, "next")
        assert claimed and claimed.id == later_id
    with Session(disposable_postgres) as db:
        late = apply_user_answer(db, _answer_event(disposable_postgres, context), "2")
        assert late.status == "LATE"


def test_g6_x10_resume_claim_race_has_one_winner(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, item_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    with Session(disposable_postgres) as db:
        apply_user_answer(db, _answer_event(disposable_postgres, context), "2")

    def claim(worker: str) -> str | None:
        engine = create_engine(DISPOSABLE_DB_URL)
        try:
            with Session(engine) as db:
                item = claim_next_resumable_validating_item(db, worker)
                return item.claimed_by if item else None
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["a", "b"]))
    winners = [result for result in results if result]
    assert len(winners) == 1
    with Session(disposable_postgres) as db:
        item = db.get(ProcessingItem, item_id)
        assert item and item.claimed_by == winners[0] and item.attempt_count == 1


def test_stale_resume_ignores_historical_ack_and_is_reclaimable(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, item_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    with Session(disposable_postgres) as db:
        apply_user_answer(db, _answer_event(disposable_postgres, context), "2")
    with Session(disposable_postgres) as db:
        claimed = claim_next_resumable_validating_item(db, "crashed")
        assert claimed
        claimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    with Session(disposable_postgres) as db:
        assert recover_stale_validating_items(db) == 1
        item = db.get(ProcessingItem, item_id)
        assert item and item.status == "VALIDATING" and item.claimed_by is None
    with Session(disposable_postgres) as db:
        reclaimed = claim_next_resumable_validating_item(db, "replacement")
        assert reclaimed and reclaimed.id == item_id


def test_outbound_unknown_is_answerable_without_resend(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, item_id)
    sends: list[str] = []
    with Session(disposable_postgres) as db:
        interaction = dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: sends.append("send") or False,
            worker_id="worker-g6",
        )
        assert interaction.status == "OUTBOUND_OUTCOME_UNKNOWN"
    with Session(disposable_postgres) as db:
        answer = apply_user_answer(db, _answer_event(disposable_postgres, context), "2")
        assert answer.status == "APPLIED"
        interaction = db.query(UserInteraction).filter_by(processing_item_id=item_id).one()
        assert interaction.status == "ANSWERED"
    assert sends == ["send"]


def test_answer_replay_is_idempotent(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, item_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    event_id = _answer_event(disposable_postgres, context)
    with Session(disposable_postgres) as db:
        first = apply_user_answer(db, event_id, "2")
    with Session(disposable_postgres) as db:
        second = apply_user_answer(db, event_id, "1")
        assert second.id == first.id and second.status == "APPLIED"
        assert db.query(UserAnswer).filter_by(inbound_event_id=event_id).count() == 1
        assert db.get(ProcessingItem, item_id).direction == "expense"


def test_recoverable_reserved_generation_is_claimed_without_new_generation(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    with Session(disposable_postgres) as db:
        interaction = create_or_get_open_interaction(db, item_id, "transaction_direction")
        db.commit()
        generation = interaction.generation
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        item.claimed_by = None
        item.heartbeat_at = None
        item.lease_expires_at = None
        db.commit()
    with Session(disposable_postgres) as db:
        resumed = claim_next_resumable_validating_item(db, "reserved")
        assert resumed and resumed.id == item_id
        interaction = db.query(UserInteraction).filter_by(processing_item_id=item_id).one()
        assert interaction.status == "RESERVED" and interaction.generation == generation == 1


def test_materialized_answer_divergence_fails_closed(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, item_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    with Session(disposable_postgres) as db:
        apply_user_answer(db, _answer_event(disposable_postgres, context), "2")
        item = db.get(ProcessingItem, item_id)
        assert item is not None
        item.direction = "income"
        db.commit()
    with Session(disposable_postgres) as db:
        assert claim_next_resumable_validating_item(db, "g6") is not None
    with Session(disposable_postgres) as db:
        with pytest.raises(Gate6DecisionConflict):
            evaluate_and_persist_validating_item(db, item_id, "g6", _evaluator())
        item = db.get(ProcessingItem, item_id)
        assert item and item.status == "VALIDATING"
        assert db.query(UserInteraction).filter_by(processing_item_id=item_id).count() == 1


def test_cancellation_closes_waiting_item_without_resume(disposable_postgres) -> None:
    context = _context(disposable_postgres)
    item_id = _item(disposable_postgres, context)
    decision = _evaluate(disposable_postgres, item_id)
    with Session(disposable_postgres) as db:
        dispatch_user_prompt(
            db,
            item_id,
            decision.question_type or "",
            lambda *_: True,
            worker_id="worker-g6",
        )
    cancel_event = _answer_event(disposable_postgres, context)
    with Session(disposable_postgres) as db:
        result = handle_cancel_command(
            db,
            context[0],
            context[1],
            context[2],
            cancel_event,
            f"c-{cancel_event}",
        )
        assert result is not None
        item = db.get(ProcessingItem, item_id)
        interaction = db.query(UserInteraction).filter_by(processing_item_id=item_id).one()
        assert item and item.status == "CANCELLED"
        assert interaction.status == "CANCELLED"
        assert claim_next_resumable_validating_item(db, "g6") is None
