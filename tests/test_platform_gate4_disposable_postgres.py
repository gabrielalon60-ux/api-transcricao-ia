from __future__ import annotations

import os
import uuid
import pytest
from pathlib import Path
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import IntegrityError
from alembic.config import Config
from alembic import command

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "packages" / "db" / "alembic.ini"
DISPOSABLE_DB_URL = "postgresql://postgres:postgres@localhost:55432/platform_gate4_test"


@pytest.fixture(scope="module")
def disposable_postgres():
    os.environ["GATE4_DISPOSABLE_DATABASE_URL"] = DISPOSABLE_DB_URL
    engine = create_engine(DISPOSABLE_DB_URL, connect_args={"connect_timeout": 2})

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL container at {DISPOSABLE_DB_URL} is not accessible: {exc}")

    # Run Alembic Upgrade Head
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)
    command.upgrade(alembic_cfg, "head")

    yield engine

    # Clean up test rows
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE service_usage, executions, processing_items, conversation_queue_counters, events, registration_rate_limits, registration_attempts, instances, users, bots, organizations CASCADE;"))
        conn.commit()


def test_1_fresh_database_migration_to_gate4_head(disposable_postgres):
    inspector = inspect(disposable_postgres)
    tables = inspector.get_table_names()
    assert "conversation_queue_counters" in tables
    assert "processing_items" in tables
    assert "executions" in tables
    assert "service_usage" in tables


def test_2_upgrade_from_pre_gate4_head_31b9b65431a4(disposable_postgres):
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)

    # 1. Downgrade database to pre-Gate-4 head (31b9b65431a4)
    command.downgrade(alembic_cfg, "31b9b65431a4")

    # 2. Insert sample Gate 2 data into pre-Gate-4 tables
    org_id = str(uuid.uuid4())
    evt_id = str(uuid.uuid4())
    with disposable_postgres.connect() as conn:
        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Gate 2 Pre-existing Org', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'corr-pre', 'WUZAPI', 'ext-pre', 'msg-pre', 'image', 'RECEIVED', 0)"), {"id": evt_id})
        conn.commit()

    # 3. Upgrade to Gate 4 head (7a8f9c1b2d3e)
    command.upgrade(alembic_cfg, "7a8f9c1b2d3e")

    # 4. Verify pre-existing Gate 2 data survived upgrade seamlessly
    with disposable_postgres.connect() as conn:
        res_org = conn.execute(text("SELECT name FROM organizations WHERE id = :id"), {"id": org_id}).scalar()
        assert res_org == "Gate 2 Pre-existing Org"
        res_evt = conn.execute(text("SELECT correlation_id FROM events WHERE id = :id"), {"id": evt_id}).scalar()
        assert res_evt == "corr-pre"



def test_3_downgrade_and_re_upgrade_cycle(disposable_postgres):
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)

    # Downgrade to 31b9b65431a4 (Gate 2 head)
    command.downgrade(alembic_cfg, "31b9b65431a4")
    inspector = inspect(disposable_postgres)
    tables = inspector.get_table_names()
    assert "processing_items" not in tables

    # Re-upgrade to head (7a8f9c1b2d3e)
    command.upgrade(alembic_cfg, "head")
    inspector_after = inspect(disposable_postgres)
    assert "processing_items" in inspector_after.get_table_names()


def test_4_atomic_counter_upsert_under_concurrent_transactions(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Concurrent', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Concurrent', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-c', '5511999990001', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880001', 'ACTIVE')"), {"id": user_id, "org_id": org_id})

        seq1 = conn.execute(text("""
            INSERT INTO conversation_queue_counters (organization_id, instance_id, user_id, last_sequence)
            VALUES (:org_id, :inst_id, :user_id, 1)
            ON CONFLICT (organization_id, instance_id, user_id)
            DO UPDATE SET last_sequence = conversation_queue_counters.last_sequence + 1
            RETURNING last_sequence;
        """), {"org_id": org_id, "inst_id": inst_id, "user_id": user_id}).scalar()
        assert seq1 == 1

        seq2 = conn.execute(text("""
            INSERT INTO conversation_queue_counters (organization_id, instance_id, user_id, last_sequence)
            VALUES (:org_id, :inst_id, :user_id, 1)
            ON CONFLICT (organization_id, instance_id, user_id)
            DO UPDATE SET last_sequence = conversation_queue_counters.last_sequence + 1
            RETURNING last_sequence;
        """), {"org_id": org_id, "inst_id": inst_id, "user_id": user_id}).scalar()
        assert seq2 == 2
        conn.commit()


def test_5_uniqueness_of_non_null_conversation_sequences(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt1, evt2 = str(uuid.uuid4()), str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Seq', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Seq', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-seq', '5511999990002', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880002', 'ACTIVE')"), {"id": user_id, "org_id": org_id})

        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-seq', 'm1', 'image', 'RECEIVED', 0)"), {"id": evt1})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c2', 'WUZAPI', 'ext-seq', 'm2', 'image', 'RECEIVED', 0)"), {"id": evt2})

        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e1, 'c1', :org, :inst, :usr, 1, 'RECEIVED', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e1": evt1, "org": org_id, "inst": inst_id, "usr": user_id})
        conn.commit()

        # Duplicate sequence 1 for same conversation -> FAIL
        with pytest.raises(IntegrityError):
            conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e2, 'c2', :org, :inst, :usr, 1, 'RECEIVED', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e2": evt2, "org": org_id, "inst": inst_id, "usr": user_id})
            conn.commit()
        conn.rollback()


def test_6_multiple_null_sequence_rows_allowed(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt1, evt2 = str(uuid.uuid4()), str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Null', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Null', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-null', '5511999990003', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880003', 'ACTIVE')"), {"id": user_id, "org_id": org_id})

        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-null', 'mn1', 'image', 'RECEIVED', 0)"), {"id": evt1})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c2', 'WUZAPI', 'ext-null', 'mn2', 'image', 'RECEIVED', 0)"), {"id": evt2})

        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e1, 'c1', :org, :inst, :usr, NULL, 'FAILED', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e1": evt1, "org": org_id, "inst": inst_id, "usr": user_id})
        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e2, 'c2', :org, :inst, :usr, NULL, 'FAILED', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e2": evt2, "org": org_id, "inst": inst_id, "usr": user_id})
        conn.commit()


def test_7_invalid_processing_status_rejection(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt = str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Status', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Status', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-st', '5511999990004', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880004', 'ACTIVE')"), {"id": user_id, "org_id": org_id})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-st', 'mst1', 'image', 'RECEIVED', 0)"), {"id": evt})

        with pytest.raises(IntegrityError) as exc_info:
            conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e, 'c1', :org, :inst, :usr, 1, 'INVALID_STATUS', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e": evt, "org": org_id, "inst": inst_id, "usr": user_id})
            conn.commit()
        assert "ck_processing_items_status_valid" in str(exc_info.value)
        conn.rollback()


def test_8_rejection_of_two_blocking_items_in_one_conversation(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt1, evt2 = str(uuid.uuid4()), str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Block', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Block', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-bl', '5511999990005', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880005', 'ACTIVE')"), {"id": user_id, "org_id": org_id})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-bl', 'mbl1', 'image', 'RECEIVED', 0)"), {"id": evt1})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c2', 'WUZAPI', 'ext-bl', 'mbl2', 'image', 'RECEIVED', 0)"), {"id": evt2})

        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e1, 'c1', :org, :inst, :usr, 1, 'ACTIVE', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e1": evt1, "org": org_id, "inst": inst_id, "usr": user_id})
        conn.commit()

        with pytest.raises(IntegrityError) as exc_info:
            conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e2, 'c2', :org, :inst, :usr, 2, 'VALIDATING', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e2": evt2, "org": org_id, "inst": inst_id, "usr": user_id})
            conn.commit()
        assert "uq_processing_items_one_active_per_conversation" in str(exc_info.value)
        conn.rollback()


def test_9_acceptance_of_blocking_items_in_different_conversations(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
        e1, e2 = str(uuid.uuid4()), str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Diff', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Diff', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-diff', '5511999990006', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880006', 'ACTIVE')"), {"id": u1, "org_id": org_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880007', 'ACTIVE')"), {"id": u2, "org_id": org_id})

        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-diff', 'md1', 'image', 'RECEIVED', 0)"), {"id": e1})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c2', 'WUZAPI', 'ext-diff', 'md2', 'image', 'RECEIVED', 0)"), {"id": e2})

        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e1, 'c1', :org, :inst, :u1, 1, 'ACTIVE', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e1": e1, "org": org_id, "inst": inst_id, "u1": u1})
        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e2, 'c2', :org, :inst, :u2, 1, 'ACTIVE', NOW(), 'image/jpeg', 10, 'sha')"), {"id": str(uuid.uuid4()), "e2": e2, "org": org_id, "inst": inst_id, "u2": u2})
        conn.commit()


def test_10_outbound_message_id_uniqueness(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt = str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Out', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Out', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-out', '5511999990008', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880008', 'ACTIVE')"), {"id": user_id, "org_id": org_id})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-out', 'mout1', 'image', 'RECEIVED', 0)"), {"id": evt})

        conn.execute(text("INSERT INTO executions (id, event_id, correlation_id, component, operation, outbound_message_id, status) VALUES (:id, :e, 'c1', 'BOT_DF', 'USER_PROMPT', 'out_msg_100', 'SUCCESS')"), {"id": str(uuid.uuid4()), "e": evt})
        conn.commit()

        with pytest.raises(IntegrityError) as exc_info:
            conn.execute(text("INSERT INTO executions (id, event_id, correlation_id, component, operation, outbound_message_id, status) VALUES (:id, :e, 'c1', 'BOT_DF', 'USER_PROMPT', 'out_msg_100', 'SUCCESS')"), {"id": str(uuid.uuid4()), "e": evt})
            conn.commit()
        assert "uq_executions_outbound_msg" in str(exc_info.value)
        conn.rollback()


def test_11_service_usage_duplicate_source_rejection(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt, item = str(uuid.uuid4()), str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Usg', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Usg', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-usg', '5511999990009', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880009', 'ACTIVE')"), {"id": user_id, "org_id": org_id})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-usg', 'musg1', 'image', 'RECEIVED', 0)"), {"id": evt})
        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e, 'c1', :org, :inst, :usr, 1, 'RECEIVED', NOW(), 'image/jpeg', 10, 'sha')"), {"id": item, "e": evt, "org": org_id, "inst": inst_id, "usr": user_id})

        # Insert 1st usage attempt (req-1, attempt 1) -> OK
        conn.execute(text("INSERT INTO service_usage (id, event_id, processing_item_id, source_service, source_request_id, source_attempt_number, provider, model, input_tokens, output_tokens, total_tokens) VALUES (:id, :e, :pi, 'TRANSCRIPTION', 'req-1', 1, 'google', 'gemini-1.5', 10, 20, 30)"), {"id": str(uuid.uuid4()), "e": evt, "pi": item})
        conn.commit()

        # Duplicate same source attempt (req-1, attempt 1) -> REJECT
        with pytest.raises(IntegrityError) as exc_info:
            conn.execute(text("INSERT INTO service_usage (id, event_id, processing_item_id, source_service, source_request_id, source_attempt_number, provider, model, input_tokens, output_tokens, total_tokens) VALUES (:id, :e, :pi, 'TRANSCRIPTION', 'req-1', 1, 'google', 'gemini-1.5', 10, 20, 30)"), {"id": str(uuid.uuid4()), "e": evt, "pi": item})
            conn.commit()
        assert "uq_service_usage_source_attempt" in str(exc_info.value)
        conn.rollback()


def test_12_distinct_service_usage_attempts_accepted(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt, item = str(uuid.uuid4()), str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Usg 2', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Usg 2', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-usg2', '5511999990010', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880010', 'ACTIVE')"), {"id": user_id, "org_id": org_id})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-usg2', 'musg2', 'image', 'RECEIVED', 0)"), {"id": evt})
        conn.execute(text("INSERT INTO processing_items (id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256) VALUES (:id, :e, 'c1', :org, :inst, :usr, 1, 'RECEIVED', NOW(), 'image/jpeg', 10, 'sha')"), {"id": item, "e": evt, "org": org_id, "inst": inst_id, "usr": user_id})

        # Attempt 1
        conn.execute(text("INSERT INTO service_usage (id, event_id, processing_item_id, source_service, source_request_id, source_attempt_number, provider, model, input_tokens, output_tokens, total_tokens) VALUES (:id, :e, :pi, 'TRANSCRIPTION', 'req-multi', 1, 'google', 'gemini-1.5', 10, 20, 30)"), {"id": str(uuid.uuid4()), "e": evt, "pi": item})
        # Attempt 2 -> MUST SUCCEED
        conn.execute(text("INSERT INTO service_usage (id, event_id, processing_item_id, source_service, source_request_id, source_attempt_number, provider, model, input_tokens, output_tokens, total_tokens) VALUES (:id, :e, :pi, 'TRANSCRIPTION', 'req-multi', 2, 'google', 'gemini-1.5', 15, 25, 40)"), {"id": str(uuid.uuid4()), "e": evt, "pi": item})
        conn.commit()


def test_13_14_15_partial_indexes_exist_with_exact_predicates(disposable_postgres):
    with disposable_postgres.connect() as conn:
        res = conn.execute(text("""
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename = 'processing_items'
              AND indexname IN ('ix_processing_items_lease_recovery', 'ix_processing_items_capacity_check', 'ix_processing_items_expiration')
        """)).fetchall()
        idx_dict = {row[0]: row[1] for row in res}

        assert "ix_processing_items_lease_recovery" in idx_dict
        assert "ix_processing_items_capacity_check" in idx_dict
        assert "ix_processing_items_expiration" in idx_dict


def test_16_server_generated_ids_work_when_omitted(disposable_postgres):
    with disposable_postgres.connect() as conn:
        org_id, bot_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        evt = str(uuid.uuid4())

        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org GenID', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot GenID', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', 'ext-gen', '5511999990011', 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, '5511988880011', 'ACTIVE')"), {"id": user_id, "org_id": org_id})
        conn.execute(text("INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, message_type, status, duplicate_count) VALUES (:id, 'c1', 'WUZAPI', 'ext-gen', 'mgen1', 'image', 'RECEIVED', 0)"), {"id": evt})

        # Insert processing_items OMITTING id column -> DB server default gen_random_uuid()::text MUST produce non-empty ID
        inserted_id = conn.execute(text("""
            INSERT INTO processing_items (event_id, correlation_id, organization_id, instance_id, user_id, sequence, status, message_received_at, file_mime_type, file_size, file_sha256)
            VALUES (:e, 'c1', :org, :inst, :usr, 1, 'RECEIVED', NOW(), 'image/jpeg', 10, 'sha')
            RETURNING id;
        """), {"e": evt, "org": org_id, "inst": inst_id, "usr": user_id}).scalar()

        assert inserted_id is not None
        assert len(inserted_id) == 36
        conn.commit()


def test_17_existing_gate2_rows_survive_upgrade(disposable_postgres):
    with disposable_postgres.connect() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM events")).scalar()
        assert res >= 0


def test_18_orm_migration_parity(disposable_postgres):
    inspector = inspect(disposable_postgres)
    cols = {c["name"] for c in inspector.get_columns("processing_items")}
    assert "writer_idempotency_key" in cols
    assert "claimed_by" in cols
    assert "lease_expires_at" in cols
    assert "heartbeat_at" in cols
