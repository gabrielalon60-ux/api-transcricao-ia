from __future__ import annotations

import os
import uuid
import concurrent.futures
import pytest
from pathlib import Path
from sqlalchemy import create_engine, text
from alembic.config import Config
from alembic import command

from db.models import Event, Execution, ProcessingItem
from orchestrator.payload import compute_payload_hash
from orchestrator.services.ingestion_service import (
    ingest_event_transaction,
    IngestionOutcome,
)

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "packages" / "db" / "alembic.ini"
DISPOSABLE_DB_URL = "postgresql://postgres:postgres@localhost:55432/platform_gate4_test"


@pytest.fixture(scope="module")
def disposable_postgres():
    os.environ["GATE4_DISPOSABLE_DATABASE_URL"] = DISPOSABLE_DB_URL
    engine = create_engine(DISPOSABLE_DB_URL, pool_size=20, max_overflow=10)

    # Run Alembic Upgrade Head
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)
    command.upgrade(alembic_cfg, "head")

    yield engine

    # Clean up test rows
    with engine.connect() as conn:
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


def test_1_first_event_creates_event_and_item_with_sequence_1(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    ext_msg_id = f"msg-1-{evt_id}"

    from sqlalchemy.orm import Session
    with Session(disposable_postgres) as session:
        evt = Event(
            id=evt_id,
            correlation_id=str(uuid.uuid4()),
            provider="WUZAPI",
            external_instance_id=f"ext-{inst_id}",
            external_message_id=ext_msg_id,
            organization_id=org_id,
            instance_id=inst_id,
            user_id=user_id,
            message_type="image",
            status="RECEIVED",
        )
        session.add(evt)
        session.flush()

        file_info = {"file_sha256": "sha-1", "file_size": 100, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": ext_msg_id, "message_type": "image"}
        res = ingest_event_transaction(session, evt, org_id, inst_id, user_id, file_info, max_queue_limit=10)

        assert res.outcome == IngestionOutcome.CREATED
        assert res.sequence == 1
        assert res.item.sequence == 1
        assert res.item.status == "RECEIVED"
        assert evt.payload_hash is not None


def test_2_second_distinct_event_receives_sequence_2(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    from sqlalchemy.orm import Session
    with Session(disposable_postgres) as session:
        e1 = Event(id=str(uuid.uuid4()), correlation_id="c1", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id="m1", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(e1)
        session.flush()
        f1 = {"file_sha256": "sha-e1", "file_size": 100, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": "m1", "message_type": "image"}
        r1 = ingest_event_transaction(session, e1, org_id, inst_id, user_id, f1, max_queue_limit=10)
        assert r1.sequence == 1

    with Session(disposable_postgres) as session:
        e2 = Event(id=str(uuid.uuid4()), correlation_id="c2", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id="m2", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(e2)
        session.flush()
        f2 = {"file_sha256": "sha-e2", "file_size": 100, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": "m2", "message_type": "image"}
        r2 = ingest_event_transaction(session, e2, org_id, inst_id, user_id, f2, max_queue_limit=10)
        assert r2.sequence == 2


def test_3_sequential_duplicate_does_not_consume_sequence(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    from sqlalchemy.orm import Session
    with Session(disposable_postgres) as session:
        e1 = Event(id=str(uuid.uuid4()), correlation_id="c1", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id="m1-dup", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(e1)
        session.flush()
        f1 = {"file_sha256": "sha-dup", "file_size": 100, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": "m1-dup", "message_type": "image"}
        r1 = ingest_event_transaction(session, e1, org_id, inst_id, user_id, f1, max_queue_limit=10)
        assert r1.outcome == IngestionOutcome.CREATED
        assert r1.sequence == 1

        r1_dup = ingest_event_transaction(session, e1, org_id, inst_id, user_id, f1, max_queue_limit=10)
        assert r1_dup.outcome == IngestionOutcome.DUPLICATE
        assert r1_dup.sequence == 1


def test_10_high_contention_simultaneous_duplicate_deliveries(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    ext_msg_id = f"m-high-contention-{uuid.uuid4()}"
    file_info = {"file_sha256": "sha-hc", "file_size": 500, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": ext_msg_id, "message_type": "image"}

    from sqlalchemy.orm import Session

    def worker_ingest(worker_id):
        engine = disposable_postgres
        with Session(engine) as session:
            # Subtransaction savepoint protection
            sp = session.begin_nested()
            try:
                e = Event(
                    id=str(uuid.uuid4()),
                    correlation_id=f"c-{worker_id}",
                    provider="WUZAPI",
                    external_instance_id=f"ext-{inst_id}",
                    external_message_id=ext_msg_id,
                    organization_id=org_id,
                    instance_id=inst_id,
                    user_id=user_id,
                    message_type="image",
                    status="RECEIVED",
                    payload_hash=compute_payload_hash(file_info),
                )
                session.add(e)
                sp.commit()
            except Exception:
                sp.rollback()
                e = session.query(Event).filter_by(provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=ext_msg_id).first()

            return ingest_event_transaction(session, e, org_id, inst_id, user_id, file_info, max_queue_limit=10)

    # Launch 10 concurrent duplicate workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_ingest, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Assert exactly 1 CREATED and 9 DUPLICATE
    outcomes = [r.outcome for r in results]
    assert outcomes.count(IngestionOutcome.CREATED) == 1
    assert outcomes.count(IngestionOutcome.DUPLICATE) == 9

    # Assert exactly 1 event and 1 processing_item exist in DB
    with Session(disposable_postgres) as session:
        evts = session.query(Event).filter_by(provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=ext_msg_id).all()
        assert len(evts) == 1
        items = session.query(ProcessingItem).filter_by(event_id=evts[0].id).all()
        assert len(items) == 1
        assert items[0].sequence == 1


def test_11_concurrent_first_distinct_events_in_new_conversation(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    from sqlalchemy.orm import Session

    def worker_first_distinct(i):
        engine = disposable_postgres
        with Session(engine) as session:
            e = Event(
                id=str(uuid.uuid4()),
                correlation_id=f"c-new-{i}",
                provider="WUZAPI",
                external_instance_id=f"ext-{inst_id}",
                external_message_id=f"m-new-conv-{i}",
                organization_id=org_id,
                instance_id=inst_id,
                user_id=user_id,
                message_type="image",
                status="RECEIVED",
            )
            session.add(e)
            session.flush()
            f_info = {"file_sha256": f"sha-new-{i}", "file_size": 100, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": f"m-new-conv-{i}", "message_type": "image"}
            return ingest_event_transaction(session, e, org_id, inst_id, user_id, f_info, max_queue_limit=10)

    # Launch 5 concurrent distinct events for a brand new conversation
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker_first_distinct, i) for i in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    sequences = sorted([r.sequence for r in results])
    assert sequences == [1, 2, 3, 4, 5]


def test_12_payload_mutation_under_same_external_id_preserves_original_event(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    from sqlalchemy.orm import Session

    with Session(disposable_postgres) as session:
        e1 = Event(id=str(uuid.uuid4()), correlation_id="c1", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id="m-mut", organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(e1)
        session.flush()
        f1 = {"file_sha256": "sha-original", "file_size": 100, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": "m-mut", "message_type": "image"}
        r1 = ingest_event_transaction(session, e1, org_id, inst_id, user_id, f1, max_queue_limit=10)
        assert r1.outcome == IngestionOutcome.CREATED

        # Re-send same event but mutated payload hash
        f1_mutated = {"file_sha256": "sha-MUTATED-DIFFERENT", "file_size": 100, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": "m-mut", "message_type": "image"}
        r1_mutated = ingest_event_transaction(session, e1, org_id, inst_id, user_id, f1_mutated, max_queue_limit=10)

        assert r1_mutated.outcome == IngestionOutcome.PAYLOAD_CONFLICT

        # Section 5: Original event & processing item status remain semantically UNCHANGED!
        assert e1.status == "RECEIVED"

        # Check execution audit entry was created
        exec_audit = session.query(Execution).filter_by(event_id=e1.id, operation="INGEST_PAYLOAD_CONFLICT").first()
        assert exec_audit is not None
        assert exec_audit.error_code == "USER_EVENT_PAYLOAD_MUTATED"


def test_13_persisted_payload_hash_survives_process_restart(disposable_postgres):
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    ext_msg = f"m-restart-{evt_id}"
    f_info = {"file_sha256": "sha-restart", "file_size": 500, "file_mime_type": "image/jpeg", "provider": "WUZAPI", "external_instance_id": f"ext-{inst_id}", "external_message_id": ext_msg, "message_type": "image"}

    from sqlalchemy.orm import Session
    # Process 1
    with Session(disposable_postgres) as session:
        e1 = Event(id=evt_id, correlation_id="c1", provider="WUZAPI", external_instance_id=f"ext-{inst_id}", external_message_id=ext_msg, organization_id=org_id, instance_id=inst_id, user_id=user_id, message_type="image", status="RECEIVED")
        session.add(e1)
        session.flush()
        ingest_event_transaction(session, e1, org_id, inst_id, user_id, f_info, max_queue_limit=10)

    # Process 2 (Simulated restart)
    with Session(disposable_postgres) as session:
        reloaded_event = session.get(Event, evt_id)
        assert reloaded_event.payload_hash is not None

        # Ingestion call after restart uses persisted payload_hash
        res = ingest_event_transaction(session, reloaded_event, org_id, inst_id, user_id, f_info, max_queue_limit=10)
        assert res.outcome == IngestionOutcome.DUPLICATE
