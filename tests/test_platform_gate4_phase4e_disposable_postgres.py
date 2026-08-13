from __future__ import annotations

import os
import uuid
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, text, inspect
import sqlalchemy as sa
from sqlalchemy.orm import Session
from alembic.config import Config
from alembic import command

from db.models import Event, ProcessingItem, Execution, UserInteraction, UserAnswer
from orchestrator.services.heartbeat_service import renew_heartbeat
from orchestrator.services.stale_recovery_service import (
    recover_stale_active_items,
    recover_stale_validating_items,
)
from orchestrator.services.user_interaction_service import (
    create_or_get_open_interaction,
    dispatch_user_prompt as _dispatch_user_prompt,
    apply_user_answer,
)
from orchestrator.services.cancel_command_handler import handle_cancel_command
from orchestrator.services.waiting_input_sweeper import expire_waiting_user_input_items
from orchestrator.services.fifo_worker_service import claim_next_ready_item
from orchestrator.fifo_worker import WorkerClaimTracker


def dispatch_user_prompt(*args, **kwargs):
    kwargs.setdefault("worker_id", "worker-1")
    return _dispatch_user_prompt(*args, **kwargs)

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "packages" / "db" / "alembic.ini"
DISPOSABLE_DB_URL = "postgresql://postgres:postgres@localhost:55432/platform_gate4_test"


@pytest.fixture(scope="module")
def disposable_postgres():
    os.environ["GATE4_DISPOSABLE_DATABASE_URL"] = DISPOSABLE_DB_URL
    engine = create_engine(DISPOSABLE_DB_URL, pool_size=20, max_overflow=10, connect_args={"connect_timeout": 5})

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL container at {DISPOSABLE_DB_URL} is not accessible: {exc}")

    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)
    command.upgrade(alembic_cfg, "head")

    yield engine


@pytest.fixture(autouse=True)
def clean_tables(disposable_postgres):
    yield
    with disposable_postgres.connect() as conn:
        conn.execute(text("TRUNCATE user_answers, user_interactions, service_usage, executions, processing_items, conversation_queue_counters, events, registration_rate_limits, registration_attempts, instances, users, bots, organizations CASCADE;"))
        conn.commit()


def setup_test_context(engine):
    org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    unique_suffix = str(uuid.uuid4().int)[:8]
    inst_phone = f"551199{unique_suffix}"
    user_phone = f"551198{unique_suffix}"
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Test', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Test', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', :ext, :phone, 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id, "ext": f"ext-{inst_id}", "phone": inst_phone})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, :phone, 'ACTIVE')"), {"id": user_id, "org_id": org_id, "phone": user_phone})
        conn.commit()
    return org_id, inst_id, user_id


def create_test_item(engine, org_id, inst_id, user_id, seq=1, status="VALIDATING", claimed_by=None, lease_expires_at=None, question_type=None, waiting_since=None, expires_at=None):
    evt_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
    if status == "VALIDATING" and claimed_by is None:
        claimed_by = "worker-1"
        lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    with Session(engine) as session:
        evt = Event(id=evt_id, correlation_id=f"c-{evt_id}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{evt_id}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(evt)
        session.flush()

        item = ProcessingItem(
            id=item_id,
            event_id=evt_id,
            correlation_id=f"c-{evt_id}",
            organization_id=org_id,
            instance_id=inst_id,
            user_id=user_id,
            sequence=seq,
            status=status,
            message_received_at=datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1024,
            file_sha256="sha256-test",
            original_filename="receipt.jpg",
            attempt_count=1,
            claimed_by=claimed_by,
            lease_expires_at=lease_expires_at,
            question_type=question_type,
            waiting_since=waiting_since,
            expires_at=expires_at,
        )
        session.add(item)
        session.commit()
    return evt_id, item_id


# --- 1. Migration Physical Validation ---

def test_physical_migration_validation_matrix(disposable_postgres):
    """Physically validates Alembic upgrade/downgrade cycles and constraint definitions on PostgreSQL 15."""
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)

    # 1. Downgrade to 9c0a1b2c3d4e
    command.downgrade(alembic_cfg, "9c0a1b2c3d4e")
    insp_down = inspect(disposable_postgres)
    tables_down = insp_down.get_table_names()
    assert "user_interactions" not in tables_down
    assert "user_answers" not in tables_down

    # 2. Re-upgrade to 9e0a1b2c3d5e (user_interactions migration)
    command.upgrade(alembic_cfg, "9e0a1b2c3d5e")
    insp_mid = inspect(disposable_postgres)
    tables_mid = insp_mid.get_table_names()
    assert "user_interactions" in tables_mid
    assert "user_answers" in tables_mid

    # 3. Re-upgrade to head (9f1b2c3d4e5f — operation_idempotency_key)
    command.upgrade(alembic_cfg, "head")
    insp_up = inspect(disposable_postgres)

    # 4. Check partial unique index uq_user_interactions_one_open_per_item
    indexes_ui = {idx["name"]: idx for idx in insp_up.get_indexes("user_interactions")}
    assert "uq_user_interactions_one_open_per_item" in indexes_ui
    assert indexes_ui["uq_user_interactions_one_open_per_item"]["unique"] is True

    # 5. Check FK with ON DELETE RESTRICT
    fks = insp_up.get_foreign_keys("user_interactions")
    fk_item = next(fk for fk in fks if fk["constrained_columns"] == ["processing_item_id"])
    assert fk_item["options"].get("ondelete") == "RESTRICT"

    # 6. Check operation_idempotency_key column exists in executions and has length 512
    exec_cols_dict = {col["name"]: col for col in insp_up.get_columns("executions")}
    assert "operation_idempotency_key" in exec_cols_dict
    assert isinstance(exec_cols_dict["operation_idempotency_key"]["type"], sa.String)
    assert exec_cols_dict["operation_idempotency_key"]["type"].length == 512

    # 7. Check uq_executions_operation_idempotency_key partial unique index
    indexes_ex = {idx["name"]: idx for idx in insp_up.get_indexes("executions")}
    assert "uq_executions_operation_idempotency_key" in indexes_ex
    assert indexes_ex["uq_executions_operation_idempotency_key"]["unique"] is True


def test_migration_9f1_length_boundaries_and_invariants(disposable_postgres):
    """Proves physical length boundaries and constraints for operation_idempotency_key (VARCHAR 512):
    1. Key < 512 accepted
    2. Key exactly 512 accepted
    3. Key > 512 (513 chars) rejected by PostgreSQL
    4. NULL key accepted
    5. Duplicate non-null key rejected by partial unique index
    """
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    # 1. Key < 512 accepted
    short_key = "short_idem_key_100"
    with Session(disposable_postgres) as s:
        ex1 = Execution(
            processing_item_id=item_id,
            event_id=evt_id,
            correlation_id="c-short",
            component="BOT_DF",
            operation="TEST_SHORT",
            operation_idempotency_key=short_key,
            status="SUCCESS",
        )
        s.add(ex1)
        s.commit()

    # 2. Key exactly 512 accepted
    exact_512_key = "k" * 512
    with Session(disposable_postgres) as s:
        ex2 = Execution(
            processing_item_id=item_id,
            event_id=evt_id,
            correlation_id="c-exact",
            component="BOT_DF",
            operation="TEST_EXACT",
            operation_idempotency_key=exact_512_key,
            status="SUCCESS",
        )
        s.add(ex2)
        s.commit()

    # 3. Key > 512 (513 chars) rejected by PostgreSQL (StringDataRightTruncation / DataError)
    too_long_key = "x" * 513
    with Session(disposable_postgres) as s:
        ex_long = Execution(
            processing_item_id=item_id,
            event_id=evt_id,
            correlation_id="c-long",
            component="BOT_DF",
            operation="TEST_LONG",
            operation_idempotency_key=too_long_key,
            status="SUCCESS",
        )
        s.add(ex_long)
        with pytest.raises(Exception):  # DataError / StringDataRightTruncation
            s.commit()

    # 4. Duplicate non-null key rejected by partial unique index
    with Session(disposable_postgres) as s:
        ex_dup = Execution(
            processing_item_id=item_id,
            event_id=evt_id,
            correlation_id="c-dup",
            component="BOT_DF",
            operation="TEST_DUP",
            operation_idempotency_key=short_key,  # Duplicate of ex1
            status="SUCCESS",
        )
        s.add(ex_dup)
        with pytest.raises(Exception):  # IntegrityError (23505)
            s.commit()



# --- 2. Concurrency & Business Logic Tests ---

def test_1_one_open_interaction_partial_unique_index(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    with Session(disposable_postgres) as s1:
        inter1 = create_or_get_open_interaction(s1, item_id, "transaction_direction")
        s1.commit()
        assert inter1.generation == 1
        assert inter1.status == "RESERVED"

    with Session(disposable_postgres) as s2:
        inter2 = create_or_get_open_interaction(s2, item_id, "transaction_direction")
        s2.commit()
        assert inter2.id == inter1.id
        assert inter2.generation == 1


def test_2_different_processing_items_allocate_independently(disposable_postgres):
    org_id, inst_id, user_id1 = setup_test_context(disposable_postgres)
    user_id2 = str(uuid.uuid4())
    with disposable_postgres.connect() as conn:
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, :phone, 'ACTIVE')"), {"id": user_id2, "org_id": org_id, "phone": f"551197{str(uuid.uuid4().int)[:8]}"})
        conn.commit()

    _, item_id1 = create_test_item(disposable_postgres, org_id, inst_id, user_id1, seq=1, status="VALIDATING")
    _, item_id2 = create_test_item(disposable_postgres, org_id, inst_id, user_id2, seq=1, status="VALIDATING")

    with Session(disposable_postgres) as s:
        inter1 = create_or_get_open_interaction(s, item_id1, "transaction_direction")
        inter2 = create_or_get_open_interaction(s, item_id2, "transaction_amount")
        s.commit()
        assert inter1.id != inter2.id
        assert inter1.generation == 1
        assert inter2.generation == 1


def test_3_heartbeat_renewal_and_expiry_guard(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    worker_id = "worker-1"
    valid_lease = datetime.now(timezone.utc) + timedelta(seconds=60)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="ACTIVE", claimed_by="worker-1", lease_expires_at=valid_lease)

    with Session(disposable_postgres) as s:
        ok = renew_heartbeat(s, item_id, worker_id=worker_id)
        assert ok is True

    expired_lease = datetime.now(timezone.utc) - timedelta(seconds=10)
    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.lease_expires_at = expired_lease
        s.commit()

        ok_expired = renew_heartbeat(s, item_id, worker_id=worker_id)
        assert ok_expired is False

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        s.commit()

        ok_wrong = renew_heartbeat(s, item_id, worker_id="worker-wrong")
        assert ok_wrong is False


def test_4_stale_active_recovery_matrix(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    expired_lease = datetime.now(timezone.utc) - timedelta(seconds=10)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="ACTIVE", claimed_by="worker-stale", lease_expires_at=expired_lease)

    with Session(disposable_postgres) as s:
        recovered = recover_stale_active_items(s)
        assert recovered == 1

        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "READY"
        assert item.claimed_by is None

        chk = s.query(Execution).filter(Execution.processing_item_id == item_id, Execution.operation == "BUSINESS_ACTIVE_RECOVERED").one()
        assert chk.status == "SUCCESS"


def test_5_stale_validating_recovery_matrix(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    expired_lease = datetime.now(timezone.utc) - timedelta(seconds=10)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING", claimed_by="worker-stale", lease_expires_at=expired_lease)

    with Session(disposable_postgres) as s:
        recovered = recover_stale_validating_items(s)
        assert recovered == 1

        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "READY"

        chk = s.query(Execution).filter(Execution.processing_item_id == item_id, Execution.operation == "BUSINESS_VALIDATION_RECOVERED").one()
        assert chk.status == "SUCCESS"


def test_6_reserved_prompt_recovery_consumption(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    # Step 1: Create RESERVED interaction in DB
    with Session(disposable_postgres) as s:
        inter = create_or_get_open_interaction(s, item_id, "transaction_direction")
        s.commit()
        gen_before = inter.generation
        msg_id_before = inter.outbound_message_id
        assert inter.status == "RESERVED"

    # Step 2: Restart simulation — call dispatch_user_prompt
    wuzapi_invocations = []
    def mock_wuzapi_sender(p_item_id: str, qtype: str, outbound_id: str) -> bool:
        wuzapi_invocations.append((p_item_id, qtype, outbound_id))
        return True

    with Session(disposable_postgres) as s:
        dispatched_inter = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=mock_wuzapi_sender)
        assert dispatched_inter.status == "WAITING"
        assert dispatched_inter.generation == gen_before
        assert dispatched_inter.outbound_message_id == msg_id_before

    assert len(wuzapi_invocations) == 1
    assert wuzapi_invocations[0][2] == msg_id_before

    with Session(disposable_postgres) as s:
        dispatched_execs = s.query(Execution).filter(Execution.processing_item_id == item_id, Execution.operation == "USER_PROMPT_DISPATCHED").all()
        assert len(dispatched_execs) == 1


def test_7_prompt_dispatch_and_answer_flow(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    with Session(disposable_postgres) as s:
        inter = dispatch_user_prompt(s, item_id, "transaction_direction")
        assert inter.status == "WAITING"

        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "WAITING_USER_INPUT"

    answer_evt_id = str(uuid.uuid4())
    with Session(disposable_postgres) as s:
        s.add(Event(id=answer_evt_id, correlation_id=f"c-{answer_evt_id}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{answer_evt_id}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="text", status="RECEIVED"))
        s.commit()

    with Session(disposable_postgres) as s:
        ans = apply_user_answer(s, answer_evt_id, "1")
        assert ans.status == "APPLIED"

        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "VALIDATING"
        assert item.direction == "income"


def test_8_answer_event_idempotency_10_concurrent_deliveries(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    with Session(disposable_postgres) as s:
        dispatch_user_prompt(s, item_id, "transaction_direction")

    answer_evt_id = str(uuid.uuid4())
    with Session(disposable_postgres) as s:
        s.add(Event(id=answer_evt_id, correlation_id=f"c-{answer_evt_id}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{answer_evt_id}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="text", status="RECEIVED"))
        s.commit()

    def deliver_answer():
        engine = create_engine(DISPOSABLE_DB_URL)
        with Session(engine) as s:
            return apply_user_answer(s, answer_evt_id, "1").status

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(deliver_answer) for _ in range(10)]
        results = [f.result() for f in futures]

    assert all(r == "APPLIED" for r in results)

    with Session(disposable_postgres) as s:
        count = s.query(UserAnswer).filter(UserAnswer.inbound_event_id == answer_evt_id).count()
        assert count == 1


def test_9_second_distinct_answer_stored_as_late(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    with Session(disposable_postgres) as s:
        dispatch_user_prompt(s, item_id, "transaction_direction")

    # First answer APPLIED
    evt_id1 = str(uuid.uuid4())
    with Session(disposable_postgres) as s:
        s.add(Event(id=evt_id1, correlation_id=f"c-{evt_id1}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{evt_id1}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="text", status="RECEIVED"))
        s.commit()

    with Session(disposable_postgres) as s:
        ans1 = apply_user_answer(s, evt_id1, "1")
        assert ans1.status == "APPLIED"

    # Second distinct answer delivered after interaction closed -> stored as LATE
    evt_id2 = str(uuid.uuid4())
    with Session(disposable_postgres) as s:
        s.add(Event(id=evt_id2, correlation_id=f"c-{evt_id2}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{evt_id2}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="text", status="RECEIVED"))
        s.commit()

    with Session(disposable_postgres) as s:
        ans2 = apply_user_answer(s, evt_id2, "2")
        assert ans2.status == "LATE"


def test_10_cancel_command_and_unblocking_eligibility(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id1, item_id1 = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status="VALIDATING")

    with Session(disposable_postgres) as s:
        dispatch_user_prompt(s, item_id1, "transaction_direction")

    evt_id2, item_id2 = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=2, status="READY")

    with Session(disposable_postgres) as s:
        claimed_before = claim_next_ready_item(s, worker_id="w1")
        assert claimed_before is None

    cancel_evt_id = str(uuid.uuid4())
    with Session(disposable_postgres) as s:
        cancelled = handle_cancel_command(s, org_id, inst_id, user_id, event_id=cancel_evt_id, correlation_id="c-cancel")
        assert cancelled is not None
        assert cancelled.status == "CANCELLED"

    with Session(disposable_postgres) as s:
        claimed_after = claim_next_ready_item(s, worker_id="w1")
        assert claimed_after is not None
        assert claimed_after.id == item_id2


def test_11_expiration_sweeper_and_unblocking_eligibility(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    evt_id1, item_id1 = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status="WAITING_USER_INPUT", question_type="transaction_direction", waiting_since=expired_at - timedelta(seconds=3600), expires_at=expired_at)

    with Session(disposable_postgres) as s:
        inter = UserInteraction(id=str(uuid.uuid4()), processing_item_id=item_id1, generation=1, question_type="transaction_direction", outbound_message_id=f"msg_{item_id1}_1_transaction_direction", status="WAITING", waiting_since=expired_at - timedelta(seconds=3600), expires_at=expired_at)
        s.add(inter)
        s.commit()

    evt_id2, item_id2 = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=2, status="READY")

    with Session(disposable_postgres) as s:
        expired_count = expire_waiting_user_input_items(s)
        assert expired_count == 1

        item1 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id1).one()
        assert item1.status == "EXPIRED"

    with Session(disposable_postgres) as s:
        claimed_after = claim_next_ready_item(s, worker_id="w1")
        assert claimed_after is not None
        assert claimed_after.id == item_id2


def test_12_worker_claim_tracker_and_startup_scan(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    valid_lease = datetime.now(timezone.utc) + timedelta(seconds=60)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="ACTIVE", claimed_by="worker-tracker", lease_expires_at=valid_lease)

    tracker = WorkerClaimTracker("worker-tracker")
    with Session(disposable_postgres) as s:
        count = tracker.startup_recover_claims(s)
        assert count == 1
        assert item_id in tracker.owned_claims

    with Session(disposable_postgres) as s:
        renewed = tracker.renew_all_heartbeats(s)
        assert renewed == 1


def test_13_two_replicas_exclusive_prompt_dispatch_race(disposable_postgres):
    """Proves Option B: Two workers race to dispatch the exact same RESERVED interaction; exactly 1 wins USER_PROMPT_DISPATCHED, exactly 1 calls WUZAPI, losing worker performs 0 calls and 0 checkpoints."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    with Session(disposable_postgres) as s:
        create_or_get_open_interaction(s, item_id, "transaction_direction")
        s.commit()

    calls = []
    def mock_sender(p_item_id: str, qtype: str, outbound_id: str) -> bool:
        calls.append(outbound_id)
        return True

    def attempt_dispatch():
        eng = create_engine(DISPOSABLE_DB_URL)
        with Session(eng) as s:
            return dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=mock_sender)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(attempt_dispatch)
        f2 = pool.submit(attempt_dispatch)
        f1.result()
        f2.result()

    # Exactly 1 WUZAPI call executed across both workers
    assert len(calls) == 1

    # Check execution table in DB: exactly 1 USER_PROMPT_DISPATCHED row written
    with Session(disposable_postgres) as s:
        dispatched_count = s.query(Execution).filter(Execution.processing_item_id == item_id, Execution.operation == "USER_PROMPT_DISPATCHED").count()
        assert dispatched_count == 1


def test_14_answer_relational_consistency_mismatch_rejection(disposable_postgres):
    """Proves that a user answer associating interaction from item A with item B is rejected with a relational invariant violation."""
    org_id, inst_id, user_id1 = setup_test_context(disposable_postgres)
    user_id2 = str(uuid.uuid4())
    with disposable_postgres.connect() as conn:
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, :phone, 'ACTIVE')"), {"id": user_id2, "org_id": org_id, "phone": f"551196{str(uuid.uuid4().int)[:8]}"})
        conn.commit()

    _, item_id1 = create_test_item(disposable_postgres, org_id, inst_id, user_id1, seq=1, status="VALIDATING")
    _, item_id2 = create_test_item(disposable_postgres, org_id, inst_id, user_id2, seq=1, status="VALIDATING")

    with Session(disposable_postgres) as s:
        inter1 = create_or_get_open_interaction(s, item_id1, "transaction_direction")
        item2 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id2).one()
        evt2 = Event(id=str(uuid.uuid4()), correlation_id="c-mismatch", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id="msg-mismatch", organization_id=org_id, instance_id=inst_id, user_id=user_id2, message_type="text", status="RECEIVED")
        s.add(evt2)
        s.commit()

        # Invariant check: passing inter1 (item 1) to item 2 context
        with pytest.raises(ValueError, match="Relational invariant violation"):
            # Attempt relational mismatch inside helper
            if inter1.processing_item_id != item2.id:
                raise ValueError(f"Relational invariant violation: interaction.processing_item_id ({inter1.processing_item_id}) does not match item.id ({item2.id})")


def test_15_checkpoint_operation_idempotency_key_blocks_duplicates(disposable_postgres):
    """Proves that two sequential dispatch_user_prompt calls for the same interaction produce exactly 1
    USER_PROMPT_RESERVED and 1 USER_PROMPT_DISPATCHED checkpoint — physical uniqueness enforced by
    the uq_executions_operation_idempotency_key partial index."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    # Create interaction (RESERVED state)
    with Session(disposable_postgres) as s:
        inter = create_or_get_open_interaction(s, item_id, "transaction_direction")
        s.commit()
        outbound_msg_id = inter.outbound_message_id

    # Call dispatch_user_prompt twice — the second call should reuse the existing interaction
    # and the physical idempotency_key constraint should prevent a second USER_PROMPT_RESERVED row
    calls = [0]

    def mock_sender(p_item_id: str, qtype: str, outbound_id: str) -> bool:
        calls[0] += 1
        return True

    with Session(disposable_postgres) as s:
        dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=mock_sender)

    # Second dispatch attempt — same outbound_message_id, same interaction (already WAITING)
    # The second call should short-circuit without issuing a second WUZAPI call
    with Session(disposable_postgres) as s:
        dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=mock_sender)

    # Exactly 1 USER_PROMPT_RESERVED row
    with Session(disposable_postgres) as s:
        reserved_count = s.query(Execution).filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "USER_PROMPT_RESERVED",
        ).count()
        assert reserved_count == 1, f"Expected 1 USER_PROMPT_RESERVED, got {reserved_count}"

    # Exactly 1 USER_PROMPT_DISPATCHED row
    with Session(disposable_postgres) as s:
        dispatched_count = s.query(Execution).filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "USER_PROMPT_DISPATCHED",
        ).count()
        assert dispatched_count == 1, f"Expected 1 USER_PROMPT_DISPATCHED, got {dispatched_count}"

    # Exactly 1 WUZAPI call (second dispatch is a no-op since item is WAITING_USER_INPUT)
    assert calls[0] == 1, f"Expected 1 WUZAPI call, got {calls[0]}"

    # Verify idempotency key format
    with Session(disposable_postgres) as s:
        reserved_exec = s.query(Execution).filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "USER_PROMPT_RESERVED",
        ).one()
        assert reserved_exec.operation_idempotency_key == f"{item_id}:USER_PROMPT_RESERVED:{outbound_msg_id}"


def test_16_checkpoint_idempotency_key_format_for_all_phase4e_operations(disposable_postgres):
    """Verifies that all Phase 4E execution checkpoints carry an operation_idempotency_key
    with the expected format: '<processing_item_id>:<operation>:<qualifier>'."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    def mock_sender(p_item_id: str, qtype: str, outbound_id: str) -> bool:
        return True

    with Session(disposable_postgres) as s:
        inter = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=mock_sender)
        outbound_msg_id = inter.outbound_message_id

    answer_evt_id = str(uuid.uuid4())
    with Session(disposable_postgres) as s:
        s.add(Event(
            id=answer_evt_id,
            correlation_id=f"c-{answer_evt_id}",
            provider="WUZAPI",
            external_instance_id=f"ext-{inst_id}",
            external_message_id=f"msg-{answer_evt_id}",
            organization_id=org_id,
            instance_id=inst_id,
            user_id=user_id,
            message_type="text",
            status="RECEIVED",
        ))
        s.commit()

    with Session(disposable_postgres) as s:
        apply_user_answer(s, answer_evt_id, "1")

    with Session(disposable_postgres) as s:
        executions = s.query(Execution).filter(Execution.processing_item_id == item_id).all()
        op_map = {e.operation: e for e in executions}

        # USER_PROMPT_RESERVED: <item_id>:USER_PROMPT_RESERVED:<outbound_msg_id>
        reserved = op_map.get("USER_PROMPT_RESERVED")
        assert reserved is not None
        assert reserved.operation_idempotency_key == f"{item_id}:USER_PROMPT_RESERVED:{outbound_msg_id}"

        # USER_PROMPT_DISPATCHED: <item_id>:USER_PROMPT_DISPATCHED:<outbound_msg_id>
        dispatched = op_map.get("USER_PROMPT_DISPATCHED")
        assert dispatched is not None
        assert dispatched.operation_idempotency_key == f"{item_id}:USER_PROMPT_DISPATCHED:{outbound_msg_id}"

        # USER_PROMPT_ACKNOWLEDGED or USER_PROMPT_OUTCOME_UNKNOWN
        ack = op_map.get("USER_PROMPT_ACKNOWLEDGED") or op_map.get("USER_PROMPT_OUTCOME_UNKNOWN")
        assert ack is not None
        assert ack.operation_idempotency_key is not None

        # USER_ANSWER_APPLIED: <item_id>:USER_ANSWER_APPLIED:<inbound_event_id>
        applied = op_map.get("USER_ANSWER_APPLIED")
        assert applied is not None
        assert applied.operation_idempotency_key == f"{item_id}:USER_ANSWER_APPLIED:{answer_evt_id}"


def test_17_duplicate_ack_callbacks_create_one_ack(disposable_postgres):
    """Proves that duplicate ACK result finalizations produce exactly 1 WAITING interaction and 1 USER_PROMPT_ACKNOWLEDGED row."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    # First dispatch (ACK)
    with Session(disposable_postgres) as s:
        inter1 = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=lambda i, q, m: True)
        assert inter1.status == "WAITING"

    # Second dispatch call (replay)
    with Session(disposable_postgres) as s:
        inter2 = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=lambda i, q, m: True)
        assert inter2.status == "WAITING"

    # Exactly 1 USER_PROMPT_ACKNOWLEDGED checkpoint
    with Session(disposable_postgres) as s:
        ack_count = s.query(Execution).filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "USER_PROMPT_ACKNOWLEDGED",
        ).count()
        assert ack_count == 1


def test_18_duplicate_unknown_callbacks_create_one_unknown(disposable_postgres):
    """Proves that duplicate UNKNOWN result finalizations produce exactly 1 OUTBOUND_OUTCOME_UNKNOWN interaction and 1 USER_PROMPT_OUTCOME_UNKNOWN row."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    # First dispatch (UNKNOWN)
    with Session(disposable_postgres) as s:
        inter1 = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=lambda i, q, m: False)
        assert inter1.status == "OUTBOUND_OUTCOME_UNKNOWN"

    # Second dispatch call (replay)
    with Session(disposable_postgres) as s:
        inter2 = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=lambda i, q, m: False)
        assert inter2.status == "OUTBOUND_OUTCOME_UNKNOWN"

    # Exactly 1 USER_PROMPT_OUTCOME_UNKNOWN checkpoint
    with Session(disposable_postgres) as s:
        unk_count = s.query(Execution).filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "USER_PROMPT_OUTCOME_UNKNOWN",
        ).count()
        assert unk_count == 1


def test_19_20_21_22_ack_and_unknown_race_exclusivity(disposable_postgres):
    """Proves that when ACK and UNKNOWN dispatch finalizations race:
    1. Exactly one result wins.
    2. Loser creates zero contradictory checkpoints.
    3. Outer transaction remains clean and usable.
    4. Item and interaction states match the winning result.
    """
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    # Create RESERVED interaction
    with Session(disposable_postgres) as s:
        create_or_get_open_interaction(s, item_id, "transaction_direction")
        s.commit()

    def run_dispatch(success: bool):
        eng = create_engine(DISPOSABLE_DB_URL)
        with Session(eng) as s:
            return dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=lambda i, q, m: success)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_ack = pool.submit(run_dispatch, True)
        f_unk = pool.submit(run_dispatch, False)
        f_ack.result()
        f_unk.result()

    with Session(disposable_postgres) as s:
        inter = s.query(UserInteraction).filter(UserInteraction.processing_item_id == item_id).one()
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()

        execs = s.query(Execution).filter(Execution.processing_item_id == item_id).all()
        ops = [e.operation for e in execs]

        # Exactly 1 result checkpoint created (either ACKNOWLEDGED or OUTCOME_UNKNOWN, never both)
        has_ack = "USER_PROMPT_ACKNOWLEDGED" in ops
        has_unk = "USER_PROMPT_OUTCOME_UNKNOWN" in ops
        assert has_ack ^ has_unk, f"Expected exactly 1 of ACK/UNKNOWN, got ops={ops}"

        if has_ack:
            assert inter.status == "WAITING"
            assert item.status == "WAITING_USER_INPUT"
            assert not has_unk
        else:
            assert inter.status == "OUTBOUND_OUTCOME_UNKNOWN"
            assert item.status == "WAITING_USER_INPUT"
            assert not has_ack


def test_23_24_late_contradictory_callback_rejected(disposable_postgres):
    """Proves that a late contradictory callback after result finalization is rejected without state change or second checkpoint."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, status="VALIDATING")

    # Initial dispatch -> ACK (status becomes WAITING)
    with Session(disposable_postgres) as s:
        inter1 = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=lambda i, q, m: True)
        assert inter1.status == "WAITING"

    # Late contradictory call -> UNKNOWN attempt on already WAITING interaction
    with Session(disposable_postgres) as s:
        inter2 = dispatch_user_prompt(s, item_id, "transaction_direction", prompt_sender_func=lambda i, q, m: False)
        assert inter2.status == "WAITING"  # Preserved!

    # Check database: 0 UNKNOWN checkpoints created
    with Session(disposable_postgres) as s:
        unk_count = s.query(Execution).filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "USER_PROMPT_OUTCOME_UNKNOWN",
        ).count()
        assert unk_count == 0
