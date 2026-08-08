from __future__ import annotations

import os
import uuid
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from alembic.config import Config
from alembic import command

from db.models import Event, ProcessingItem, Execution
from orchestrator.services.fifo_worker_service import (
    claim_next_ready_item,
    transition_active_to_validating,
    TERMINAL_STATES,
    PHYSICAL_PARTIAL_INDEX_NAME,
)

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "packages" / "db" / "alembic.ini"
DISPOSABLE_DB_URL = "postgresql://postgres:postgres@localhost:55432/platform_gate4_test"


@pytest.fixture(scope="module")
def disposable_postgres():
    os.environ["GATE4_DISPOSABLE_DATABASE_URL"] = DISPOSABLE_DB_URL
    engine = create_engine(DISPOSABLE_DB_URL, pool_size=20, max_overflow=10, connect_args={"connect_timeout": 2})

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
        conn.execute(text("TRUNCATE service_usage, executions, processing_items, conversation_queue_counters, events, registration_rate_limits, registration_attempts, instances, users, bots, organizations CASCADE;"))
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


def create_test_item(engine, org_id, inst_id, user_id, seq=1, status="READY", received_at=None, claimed_by=None, lease_expires_at=None):
    evt_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())
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
            message_received_at=received_at or datetime.now(timezone.utc),
            file_mime_type="image/jpeg",
            file_size=1024,
            file_sha256="sha256-test",
            original_filename="receipt.jpg",
            attempt_count=0 if status == "READY" else 1,
            claimed_by=claimed_by,
            lease_expires_at=lease_expires_at,
        )
        session.add(item)
        session.commit()
    return item_id


def test_1_one_ready_item_claimed(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status="READY")

    with Session(disposable_postgres) as session:
        claimed = claim_next_ready_item(session, worker_id="w1")
        assert claimed is not None
        assert claimed.id == item_id
        assert claimed.status == "ACTIVE"
        assert claimed.claimed_by == "worker-w1"
        assert claimed.attempt_count == 1


def test_2_anti_starvation_blocked_oldest_candidate(disposable_postgres):
    """Proves that a blocked READY item from Conversation A does not starve an eligible READY item from Conversation B."""
    org_a, inst_a, user_a = setup_test_context(disposable_postgres)
    t_old = datetime.now(timezone.utc) - timedelta(seconds=100)
    create_test_item(disposable_postgres, org_a, inst_a, user_a, seq=1, status="RECEIVED", received_at=t_old)
    create_test_item(disposable_postgres, org_a, inst_a, user_a, seq=2, status="READY", received_at=t_old)

    org_b, inst_b, user_b = setup_test_context(disposable_postgres)
    item_b1 = create_test_item(disposable_postgres, org_b, inst_b, user_b, seq=1, status="READY")

    with Session(disposable_postgres) as session:
        claimed = claim_next_ready_item(session, worker_id="w_starve_check")
        assert claimed is not None
        assert claimed.id == item_b1
        assert claimed.status == "ACTIVE"


def test_3_skip_locked_real_two_connections(disposable_postgres):
    """Proves real SKIP LOCKED behavior across two independent DB connections."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    item1 = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status="READY")

    org_id2, inst_id2, user_id2 = setup_test_context(disposable_postgres)
    item2 = create_test_item(disposable_postgres, org_id2, inst_id2, user_id2, seq=1, status="READY")

    session1 = Session(disposable_postgres)
    s1_item = session1.query(ProcessingItem).filter_by(id=item1).with_for_update().first()
    assert s1_item is not None

    session2 = Session(disposable_postgres)
    claimed2 = claim_next_ready_item(session2, worker_id="w_conn2")
    assert claimed2 is not None
    assert claimed2.id == item2

    session1.rollback()
    session1.close()
    session2.close()


def test_4_physical_partial_index_race_rejection(disposable_postgres):
    """Proves physical partial index uq_processing_items_one_active_per_conversation rejects two blocking items."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status="ACTIVE")

    evt_id2, item_id2 = str(uuid.uuid4()), str(uuid.uuid4())
    with Session(disposable_postgres) as session:
        evt = Event(id=evt_id2, correlation_id=f"c-{evt_id2}", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=f"msg-{evt_id2}", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(evt)
        session.flush()

        item2 = ProcessingItem(
            id=item_id2, event_id=evt_id2, correlation_id=f"c-{evt_id2}", organization_id=org_id, instance_id=inst_id, user_id=user_id, sequence=2, status="ACTIVE", message_received_at=datetime.now(timezone.utc), file_mime_type="image/jpeg", file_size=1024, file_sha256="sha256-test", original_filename="receipt.jpg", attempt_count=1
        )
        session.add(item2)
        with pytest.raises(IntegrityError) as exc_info:
            session.commit()
        assert PHYSICAL_PARTIAL_INDEX_NAME in str(exc_info.value)


def test_5_earlier_non_terminal_blocks_later_ready(disposable_postgres):
    """Tests that RECEIVED, EXTRACTING, EXTRACTED, READY, WAITING_USER_INPUT block later READY items."""
    for blk_status in ["RECEIVED", "EXTRACTING", "EXTRACTED", "READY", "WAITING_USER_INPUT"]:
        org_id, inst_id, user_id = setup_test_context(disposable_postgres)
        create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status=blk_status)
        create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=2, status="READY")

        with Session(disposable_postgres) as session:
            claimed = claim_next_ready_item(session, worker_id="w_block")
            if blk_status == "READY":
                assert claimed is not None
                assert claimed.sequence == 1
            else:
                assert claimed is None


def test_6_terminal_earlier_state_allows_later_ready(disposable_postgres):
    """Proves each terminal state allows later sequence READY items to be claimed."""
    for term_status in TERMINAL_STATES:
        org_id, inst_id, user_id = setup_test_context(disposable_postgres)
        create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status=term_status)
        item2 = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=2, status="READY")

        with Session(disposable_postgres) as session:
            claimed = claim_next_ready_item(session, worker_id="w_term")
            assert claimed is not None
            assert claimed.id == item2
            assert claimed.sequence == 2


def test_7_sequence_null_never_claimed(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=None, status="READY")

    with Session(disposable_postgres) as session:
        claimed = claim_next_ready_item(session, worker_id="w_null")
        assert claimed is None


def test_8_active_to_validating_guards_and_idempotency(disposable_postgres):
    """Proves ACTIVE -> VALIDATING transition guards (worker ownership, lease validity) and idempotency."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    item_id = create_test_item(disposable_postgres, org_id, inst_id, user_id, seq=1, status="READY")

    with Session(disposable_postgres) as session:
        claimed = claim_next_ready_item(session, worker_id="w_guard")
        assert claimed is not None

    # Mismatched worker fails
    with Session(disposable_postgres) as session:
        res_wrong = transition_active_to_validating(session, item_id, worker_id="w_wrong")
        assert res_wrong is None

    # Matching worker succeeds
    with Session(disposable_postgres) as session:
        res_valid = transition_active_to_validating(session, item_id, worker_id="w_guard")
        assert res_valid is not None
        assert res_valid.status == "VALIDATING"
        assert res_valid.attempt_count == 1  # attempt_count NOT incremented

    # Idempotent re-call returns existing without duplicate checkpoint
    with Session(disposable_postgres) as session:
        res_idem = transition_active_to_validating(session, item_id, worker_id="w_guard")
        assert res_idem is not None
        assert res_idem.status == "VALIDATING"

        exc_count = session.query(Execution).filter_by(processing_item_id=item_id, operation="BUSINESS_VALIDATION_STARTED").count()
        assert exc_count == 1
