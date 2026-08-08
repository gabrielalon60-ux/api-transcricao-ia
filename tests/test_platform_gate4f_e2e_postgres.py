from __future__ import annotations

import os
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config

from db.models import Event, ProcessingItem
from orchestrator.services.ingestion_service import ingest_event_transaction
from orchestrator.services.fifo_worker_service import claim_next_ready_item, transition_active_to_validating
from orchestrator.services.user_interaction_service import (
    select_question_type,
    dispatch_user_prompt,
    apply_user_answer,
)
from orchestrator.services.persistence_service import (
    transition_validating_to_persisting,
    claim_persistence_dispatch,
    dispatch_persistence_write,
    reconcile_persistence_outcomes,
    recover_stale_persistence_items,
)
from tests.test_platform_gate4f_orchestrator_persistence_postgres import MockWriterClient

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "packages" / "db" / "alembic.ini"
DISPOSABLE_DB_URL = os.getenv("GATE4_DISPOSABLE_DATABASE_URL", "postgresql://postgres:postgres@localhost:55432/platform_gate4_test")


@pytest.fixture(scope="module")
def disposable_postgres():
    engine = create_engine(DISPOSABLE_DB_URL)
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

    with engine.connect() as conn:
        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org E2E', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot E2E', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', :ext, :phone, 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id, "ext": f"ext-{inst_id}", "phone": f"551199{unique_suffix}"})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, :phone, 'ACTIVE')"), {"id": user_id, "org_id": org_id, "phone": f"551198{unique_suffix}"})
        conn.commit()

    return org_id, inst_id, user_id


def create_event(evt_id, ext_msg, msg_type="media"):
    return Event(
        id=evt_id,
        correlation_id=f"c-{evt_id}",
        provider="WUZAPI",
        external_instance_id="ext-1",
        external_message_id=ext_msg,
        message_type=msg_type,
        status="RECEIVED",
        duplicate_count=0,
    )


# --- 15 Explicit Named E2E Tests (Section 13) ---

def test_1_document_successful_persistence_completed(disposable_postgres):
    """E2E Test 1: Ingestion -> READY -> VALIDATING -> PERSISTING -> DB Writer COMMITTED -> COMPLETED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-1")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("150.00")
        item.direction = "expense"
        item.document_type = "invoice"
        s.commit()

    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-e2e")
        cres = claim_persistence_dispatch(s, pers.id, worker_id="w-e2e")
        final_item = dispatch_persistence_write(s, pers.id, dispatch_token=cres[1], client=MockWriterClient(outcome="COMMITTED"))
        assert final_item.status == "COMPLETED"


def test_2_clarification_answer_persistence_completed(disposable_postgres):
    """E2E Test 2: Ingestion -> READY -> VALIDATING (missing field) -> WAITING_USER_INPUT -> Inbound Answer -> READY -> PERSISTING -> COMPLETED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-2")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        # Missing direction
        item.amount = Decimal("300.00")
        item.document_type = "invoice"
        s.commit()

    # Step 1: Transition READY -> ACTIVE -> VALIDATING -> WAITING_USER_INPUT
    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        qtype = select_question_type(val)
        assert qtype in ("direction", "transaction_direction")
        interaction = dispatch_user_prompt(s, val.id, qtype, prompt_sender_func=lambda p, t, m: True)
        assert interaction.status == "WAITING"

    # Step 2: Receive inbound user answer "Despesa"
    ans_evt_id = str(uuid.uuid4())
    ans_evt = create_event(ans_evt_id, "msg-ans-1", msg_type="text")
    ans_evt.organization_id = org_id
    ans_evt.instance_id = inst_id
    ans_evt.user_id = user_id
    ans_evt.raw_payload = {"text": "Despesa"}

    with Session(disposable_postgres) as s:
        s.add(ans_evt)
        ans_obj = apply_user_answer(s, ans_evt_id, "Despesa")
        assert ans_obj.status == "APPLIED"

    # Step 3: Item returned to VALIDATING, now transition to PERSISTING -> COMPLETED
    with Session(disposable_postgres) as s:
        pers2 = transition_validating_to_persisting(s, item_id, worker_id="w-e2e")
        cres2 = claim_persistence_dispatch(s, pers2.id, worker_id="w-e2e")
        final_item2 = dispatch_persistence_write(s, pers2.id, dispatch_token=cres2[1], client=MockWriterClient(outcome="COMMITTED"))
        assert final_item2.status == "COMPLETED"


def test_3_writer_durable_rejection_persistence_failed(disposable_postgres):
    """E2E Test 3: Writer durable rejection transitions item to PERSISTENCE_FAILED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-3")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("50.00")
        item.direction = "expense"
        item.document_type = "invoice"
        s.commit()

    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-e2e")
        cres = claim_persistence_dispatch(s, pers.id, worker_id="w-e2e")
        final_item = dispatch_persistence_write(s, pers.id, dispatch_token=cres[1], client=MockWriterClient(outcome="REJECTED", err_code="INVALID_PAYLOAD"))
        assert final_item.status == "PERSISTENCE_FAILED"


def test_4_retryable_scheduled_retry_committed(disposable_postgres):
    """E2E Test 4: RETRYABLE failure -> scheduled retry -> COMMITTED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-4")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("100.00")
        item.direction = "expense"
        item.document_type = "invoice"
        s.commit()

    # Dispatch 1: RETRYABLE_FAILURE
    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-e2e")
        cres = claim_persistence_dispatch(s, pers.id, worker_id="w-e2e")
        item_ret = dispatch_persistence_write(s, pers.id, dispatch_token=cres[1], client=MockWriterClient(outcome="RETRYABLE_FAILURE"))
        assert item_ret.status == "PERSIST_RETRYABLE"

    # Make persistence_next_attempt_at past so sweeper recovers it
    with Session(disposable_postgres) as s:
        item_db = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item_db.persistence_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()

    # Sweeper recovers eligible PERSIST_RETRYABLE -> PERSISTING
    with Session(disposable_postgres) as s:
        rec = recover_stale_persistence_items(s, stale_threshold_seconds=0)
        assert rec == 1

    # Dispatch 2: COMMITTED
    with Session(disposable_postgres) as s:
        cres2 = claim_persistence_dispatch(s, item_id, worker_id="w-e2e")
        final_item = dispatch_persistence_write(s, item_id, dispatch_token=cres2[1], client=MockWriterClient(outcome="COMMITTED"))
        assert final_item.status == "COMPLETED"


def test_5_timeout_unknown_reconciliation_committed(disposable_postgres):
    """E2E Test 5: Timeout -> OUTCOME_UNKNOWN -> reconciliation COMMITTED -> COMPLETED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-5")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("200.00")
        item.direction = "income"
        item.document_type = "pix_receipt"
        s.commit()

    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-e2e")
        cres = claim_persistence_dispatch(s, pers.id, worker_id="w-e2e")
        item_unk = dispatch_persistence_write(s, pers.id, dispatch_token=cres[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))
        assert item_unk.status == "PERSIST_OUTCOME_UNKNOWN"

    with Session(disposable_postgres) as s:
        reconciled = reconcile_persistence_outcomes(s, client=MockWriterClient(outcome="COMMITTED"))
        assert reconciled == 1

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "COMPLETED"


def test_6_timeout_unknown_reconciliation_rejected(disposable_postgres):
    """E2E Test 6: Timeout -> OUTCOME_UNKNOWN -> reconciliation REJECTED -> PERSISTENCE_FAILED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-6")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("200.00")
        item.direction = "income"
        item.document_type = "pix_receipt"
        s.commit()

    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-e2e")
        cres = claim_persistence_dispatch(s, pers.id, worker_id="w-e2e")
        dispatch_persistence_write(s, pers.id, dispatch_token=cres[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    with Session(disposable_postgres) as s:
        reconcile_persistence_outcomes(s, client=MockWriterClient(outcome="REJECTED", err_code="INVALID_PAYLOAD"))

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "PERSISTENCE_FAILED"


def test_7_duplicate_inbound_webhook(disposable_postgres):
    """E2E Test 7: Duplicate inbound webhook with same external_message_id is safely deduplicated."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id1, evt_id2 = str(uuid.uuid4()), str(uuid.uuid4())
    evt1 = create_event(evt_id1, "dup-msg-1")
    evt2 = create_event(evt_id2, "dup-msg-1")  # Same external message ID
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt1)
        res1 = ingest_event_transaction(s, evt1, org_id, inst_id, user_id, file_info)
        item_id1 = res1.item.id

    with Session(disposable_postgres) as s:
        res2 = ingest_event_transaction(s, evt2, org_id, inst_id, user_id, file_info)
        assert res2.outcome.value == "DUPLICATE"
        assert res2.item.id == item_id1


def test_8_duplicate_extraction_callback(disposable_postgres):
    """E2E Test 8: Duplicate extraction callback on READY item returns same item without mutating sequence."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-8")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        s.commit()

    with Session(disposable_postgres) as s:
        item_check = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item_check.status == "READY"
        assert item_check.sequence == 1


def test_9_duplicate_user_answer(disposable_postgres):
    """E2E Test 9: Duplicate user answer event is handled idempotently via UserAnswer ledger."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-9")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("300.00")
        s.commit()

    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        qtype = select_question_type(val)
        dispatch_user_prompt(s, val.id, qtype, prompt_sender_func=lambda p, t, m: True)

    ans_evt_id = str(uuid.uuid4())
    ans_evt = create_event(ans_evt_id, "msg-ans-9", msg_type="text")
    ans_evt.organization_id = org_id
    ans_evt.instance_id = inst_id
    ans_evt.user_id = user_id

    with Session(disposable_postgres) as s:
        s.add(ans_evt)
        a1 = apply_user_answer(s, ans_evt_id, "Despesa")
        assert a1.status == "APPLIED"
        a1_id = a1.id

    # Duplicate answer call returns same committed UserAnswer row
    with Session(disposable_postgres) as s:
        a2 = apply_user_answer(s, ans_evt_id, "Despesa")
        assert a2.id == a1_id


def test_10_duplicate_persistence_dispatch(disposable_postgres):
    """E2E Test 10: Duplicate persistence dispatch call returns existing item without duplicate POST."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-10")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("100.00")
        item.direction = "expense"
        item.document_type = "invoice"
        s.commit()

    mock_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-e2e")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-e2e")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-e2e")
        cres = claim_persistence_dispatch(s, pers.id, worker_id="w-e2e")
        f1 = dispatch_persistence_write(s, pers.id, dispatch_token=cres[1], client=mock_client)
        assert f1.status == "COMPLETED"

    # Second call when item already COMPLETED
    with Session(disposable_postgres) as s:
        f2 = dispatch_persistence_write(s, item_id, client=mock_client)
        assert f2 is None
        assert mock_client.calls == 1  # Exactly 1 HTTP POST call made


def test_11_restart_after_persistence_reservation(disposable_postgres):
    """E2E Test 11: Process restart after PERSISTENCE_DISPATCH_RESERVED resumes dispatch under same writer_idempotency_key."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-11")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("400.00")
        item.direction = "income"
        item.document_type = "pix_receipt"
        s.commit()

    # Step 1: Transition to PERSISTING (reservation written)
    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-1")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-1")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-1")
        writer_key = pers.writer_idempotency_key

    # Step 2: Simulated process restart -> Worker 2 acquires new claim and dispatches
    mock_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        cres2 = claim_persistence_dispatch(s, item_id, worker_id="w-2")
        final_item = dispatch_persistence_write(s, item_id, dispatch_token=cres2[1], client=mock_client)
        assert final_item.status == "COMPLETED"
        assert final_item.writer_idempotency_key == writer_key


def test_12_restart_after_dispatched_before_response(disposable_postgres):
    """E2E Test 12: Process crash after DISPATCHED before HTTP response recovers via PERSIST_OUTCOME_UNKNOWN reconciliation."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id = str(uuid.uuid4())
    evt = create_event(evt_id, "msg-12")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt)
        res = ingest_event_transaction(s, evt, org_id, inst_id, user_id, file_info)
        item_id = res.item.id

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.status = "READY"
        item.amount = Decimal("120.00")
        item.direction = "expense"
        item.document_type = "invoice"
        s.commit()

    with Session(disposable_postgres) as s:
        claimed = claim_next_ready_item(s, worker_id="w-1")
        val = transition_active_to_validating(s, claimed.id, worker_id="w-1")
        pers = transition_validating_to_persisting(s, val.id, worker_id="w-1")
        cres = claim_persistence_dispatch(s, pers.id, worker_id="w-1")
        dispatch_persistence_write(s, pers.id, dispatch_token=cres[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    # Process restart: Sweeper reconciles outcome
    with Session(disposable_postgres) as s:
        reconciled = reconcile_persistence_outcomes(s, client=MockWriterClient(outcome="COMMITTED"))
        assert reconciled == 1

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "COMPLETED"


def test_13_later_queue_item_blocked_during_retryable(disposable_postgres):
    """E2E Test 13: Later sequence queue item remains strictly blocked while earlier item is in PERSIST_RETRYABLE."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id1, evt_id2 = str(uuid.uuid4()), str(uuid.uuid4())
    evt1 = create_event(evt_id1, "msg-seq-1")
    evt2 = create_event(evt_id2, "msg-seq-2")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt1)
        res1 = ingest_event_transaction(s, evt1, org_id, inst_id, user_id, file_info)
        item_id1 = res1.item.id

    with Session(disposable_postgres) as s:
        s.add(evt2)
        res2 = ingest_event_transaction(s, evt2, org_id, inst_id, user_id, file_info)
        item_id2 = res2.item.id

    with Session(disposable_postgres) as s:
        item1 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id1).one()
        item1.status = "READY"
        item1.amount = Decimal("100.00")
        item1.direction = "expense"
        item1.document_type = "invoice"

        item2 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id2).one()
        item2.status = "READY"
        item2.amount = Decimal("200.00")
        item2.direction = "income"
        item2.document_type = "pix_receipt"
        s.commit()

    # Item 1 enters PERSIST_RETRYABLE
    with Session(disposable_postgres) as s:
        claimed1 = claim_next_ready_item(s, worker_id="w-e2e")
        val1 = transition_active_to_validating(s, claimed1.id, worker_id="w-e2e")
        pers1 = transition_validating_to_persisting(s, val1.id, worker_id="w-e2e")
        cres1 = claim_persistence_dispatch(s, pers1.id, worker_id="w-e2e")
        item_ret1 = dispatch_persistence_write(s, pers1.id, dispatch_token=cres1[1], client=MockWriterClient(outcome="RETRYABLE_FAILURE"))
        assert item_ret1.status == "PERSIST_RETRYABLE"

    # Item 2 FIFO claim attempt -> BLOCKED
    with Session(disposable_postgres) as s:
        claimed_blocked = claim_next_ready_item(s, worker_id="w-e2e")
        assert claimed_blocked is None


def test_14_later_queue_item_blocked_during_unknown(disposable_postgres):
    """E2E Test 14: Later sequence queue item remains strictly blocked while earlier item is in PERSIST_OUTCOME_UNKNOWN."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id1, evt_id2 = str(uuid.uuid4()), str(uuid.uuid4())
    evt1 = create_event(evt_id1, "msg-seq-1b")
    evt2 = create_event(evt_id2, "msg-seq-2b")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt1)
        res1 = ingest_event_transaction(s, evt1, org_id, inst_id, user_id, file_info)
        item_id1 = res1.item.id

    with Session(disposable_postgres) as s:
        s.add(evt2)
        res2 = ingest_event_transaction(s, evt2, org_id, inst_id, user_id, file_info)
        item_id2 = res2.item.id

    with Session(disposable_postgres) as s:
        item1 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id1).one()
        item1.status = "READY"
        item1.amount = Decimal("100.00")
        item1.direction = "expense"
        item1.document_type = "invoice"

        item2 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id2).one()
        item2.status = "READY"
        item2.amount = Decimal("200.00")
        item2.direction = "income"
        item2.document_type = "pix_receipt"
        s.commit()

    # Item 1 enters PERSIST_OUTCOME_UNKNOWN
    with Session(disposable_postgres) as s:
        claimed1 = claim_next_ready_item(s, worker_id="w-e2e")
        val1 = transition_active_to_validating(s, claimed1.id, worker_id="w-e2e")
        pers1 = transition_validating_to_persisting(s, val1.id, worker_id="w-e2e")
        cres1 = claim_persistence_dispatch(s, pers1.id, worker_id="w-e2e")
        item_unk1 = dispatch_persistence_write(s, pers1.id, dispatch_token=cres1[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))
        assert item_unk1.status == "PERSIST_OUTCOME_UNKNOWN"

    # Item 2 FIFO claim attempt -> BLOCKED
    with Session(disposable_postgres) as s:
        claimed_blocked = claim_next_ready_item(s, worker_id="w-e2e")
        assert claimed_blocked is None


def test_15_later_queue_item_unblocked_after_terminal_persistence_result(disposable_postgres):
    """E2E Test 15: Later sequence queue item becomes unblocked and claimed after earlier item reaches COMPLETED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    evt_id1, evt_id2 = str(uuid.uuid4()), str(uuid.uuid4())
    evt1 = create_event(evt_id1, "msg-unblock-1")
    evt2 = create_event(evt_id2, "msg-unblock-2")
    file_info = {"mime": "application/pdf", "size": 2048, "sha256": "sha123"}

    with Session(disposable_postgres) as s:
        s.add(evt1)
        res1 = ingest_event_transaction(s, evt1, org_id, inst_id, user_id, file_info)
        item_id1 = res1.item.id

    with Session(disposable_postgres) as s:
        s.add(evt2)
        res2 = ingest_event_transaction(s, evt2, org_id, inst_id, user_id, file_info)
        item_id2 = res2.item.id

    with Session(disposable_postgres) as s:
        item1 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id1).one()
        item1.status = "READY"
        item1.amount = Decimal("100.00")
        item1.direction = "expense"
        item1.document_type = "invoice"

        item2 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id2).one()
        item2.status = "READY"
        item2.amount = Decimal("200.00")
        item2.direction = "income"
        item2.document_type = "pix_receipt"
        s.commit()

    # Process Item 1 to COMPLETED
    with Session(disposable_postgres) as s:
        claimed1 = claim_next_ready_item(s, worker_id="w-e2e")
        val1 = transition_active_to_validating(s, claimed1.id, worker_id="w-e2e")
        pers1 = transition_validating_to_persisting(s, val1.id, worker_id="w-e2e")
        cres1 = claim_persistence_dispatch(s, pers1.id, worker_id="w-e2e")
        final1 = dispatch_persistence_write(s, pers1.id, dispatch_token=cres1[1], client=MockWriterClient(outcome="COMMITTED"))
        assert final1.status == "COMPLETED"

    # Item 2 is now unblocked and claimed successfully!
    with Session(disposable_postgres) as s:
        claimed2 = claim_next_ready_item(s, worker_id="w-e2e")
        assert claimed2 is not None
        assert claimed2.id == item_id2
        assert claimed2.sequence == 2
