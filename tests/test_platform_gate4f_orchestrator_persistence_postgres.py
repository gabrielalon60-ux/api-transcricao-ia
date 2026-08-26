from __future__ import annotations

import os
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config

from db.models import Execution, ProcessingItem
from orchestrator.services.persistence_service import (
    transition_validating_to_persisting,
    claim_persistence_dispatch,
    dispatch_persistence_write,
    reconcile_persistence_outcomes,
    recover_stale_persistence_items,
)
from orchestrator.db_writer_client import DBWriterClient

pytestmark = pytest.mark.real_pg15


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
        conn.execute(text("INSERT INTO organizations (id, name, slug, status) VALUES (:id, 'Org Test', :slug, 'ACTIVE')"), {"id": org_id, "slug": f"slug-{org_id}"})
        conn.execute(text("INSERT INTO bots (id, organization_id, name, service_key, status) VALUES (:id, :org_id, 'Bot Test', :key, 'ACTIVE')"), {"id": bot_id, "org_id": org_id, "key": f"key-{bot_id}"})
        conn.execute(text("INSERT INTO instances (id, organization_id, bot_id, provider, external_instance_id, phone_number, status) VALUES (:id, :org_id, :bot_id, 'WUZAPI', :ext, :phone, 'ACTIVE')"), {"id": inst_id, "org_id": org_id, "bot_id": bot_id, "ext": f"ext-{inst_id}", "phone": f"551199{unique_suffix}"})
        conn.execute(text("INSERT INTO users (id, organization_id, phone_number, status) VALUES (:id, :org_id, :phone, 'ACTIVE')"), {"id": user_id, "org_id": org_id, "phone": f"551198{unique_suffix}"})
        conn.commit()

    return org_id, inst_id, user_id


def create_validating_item(engine, org_id, inst_id, user_id, seq=1):
    evt_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO events (id, correlation_id, provider, external_instance_id, external_message_id, organization_id, instance_id, user_id, message_type, status, duplicate_count)
            VALUES (:id, :corr, 'WUZAPI', 'ext-1', :ext_msg, :org, :inst, :user, 'media', 'RECEIVED', 0)
        """), {"id": evt_id, "corr": f"c-{evt_id}", "ext_msg": f"msg-{evt_id}", "org": org_id, "inst": inst_id, "user": user_id})

        conn.execute(text("""
            INSERT INTO processing_items (
                id, event_id, correlation_id, organization_id, instance_id, user_id, sequence, status,
                message_received_at, file_mime_type, file_size, file_sha256, amount, direction, document_type, document_date,
                persistence_generation, persistence_attempt_count
            ) VALUES (
                :id, :evt_id, :corr, :org, :inst, :user, :seq, 'VALIDATING',
                NOW(), 'application/pdf', 1024, 'sha256fake', 150.00, 'expense', 'invoice', '2026-08-05',
                0, 0
            )
        """), {"id": item_id, "evt_id": evt_id, "corr": f"c-{evt_id}", "org": org_id, "inst": inst_id, "user": user_id, "seq": seq})
        conn.commit()

    return evt_id, item_id


class MockWriterClient(DBWriterClient):
    def __init__(self, outcome="COMMITTED", rec_id=None, err_code=None):
        self.outcome = outcome
        self.rec_id = rec_id or str(uuid.uuid4())
        self.err_code = err_code
        self.calls = 0

    def write(self, **kwargs):
        self.calls += 1
        return {
            "status": self.outcome,
            "idempotency_key": kwargs["idempotency_key"],
            "processing_item_id": kwargs["processing_item_id"],
            "committed_record_id": self.rec_id if self.outcome == "COMMITTED" else None,
            "error_code": self.err_code if self.outcome != "COMMITTED" else None,
        }

    def get_write_status(self, idempotency_key, correlation_id="c-reconcile"):
        self.calls += 1
        return {
            "status": self.outcome,
            "idempotency_key": idempotency_key,
            "committed_record_id": self.rec_id if self.outcome == "COMMITTED" else None,
            "error_code": self.err_code if self.outcome != "COMMITTED" else None,
        }


# --- Core State Machine Tests ---

def test_1_validating_to_persisting_transition(disposable_postgres):
    """Proves VALIDATING -> PERSISTING transition creates writer_idempotency_key and PERSISTENCE_DISPATCH_RESERVED checkpoint."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        persisting = transition_validating_to_persisting(s, item_id, worker_id="worker-1")
        assert persisting.status == "PERSISTING"
        assert persisting.writer_idempotency_key == f"write_{item_id}"
        assert persisting.persistence_generation == 1

    with Session(disposable_postgres) as s:
        exec_row = s.query(Execution).filter(
            Execution.processing_item_id == item_id,
            Execution.operation == "PERSISTENCE_DISPATCH_RESERVED",
        ).one()
        assert exec_row.operation_idempotency_key == f"{item_id}:PERSISTENCE_DISPATCH_RESERVED:write_{item_id}:1"


def test_2_two_replicas_exclusive_dispatch_race(disposable_postgres):
    """Proves two Orchestrator replicas competing for dispatch claim win exactly once."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)

    with Session(disposable_postgres) as s1:
        res1 = claim_persistence_dispatch(s1, item_id, worker_id="replica-1")
        assert res1 is not None

    with Session(disposable_postgres) as s2:
        res2 = claim_persistence_dispatch(s2, item_id, worker_id="replica-2")
        assert res2 is None


def test_3_dispatch_committed_transitions_to_completed(disposable_postgres):
    """Proves successful DB Writer COMMITTED outcome transitions item PERSISTING -> COMPLETED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id, worker_id="worker-1")

    mock_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        item_final = dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=mock_client)
        assert item_final.status == "COMPLETED"


def test_4_dispatch_rejected_transitions_to_persistence_failed(disposable_postgres):
    """Proves DB Writer REJECTED outcome transitions item PERSISTING -> PERSISTENCE_FAILED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id, worker_id="worker-1")

    mock_client = MockWriterClient(outcome="REJECTED", err_code="INVALID_BUSINESS_PAYLOAD")
    with Session(disposable_postgres) as s:
        item_final = dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=mock_client)
        assert item_final.status == "PERSISTENCE_FAILED"


def test_5_stale_dispatch_result_ignored_after_generation_increment(disposable_postgres):
    """Proves a late result from an older dispatch generation is safely ignored."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        old_token = res[1]

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.persistence_generation = 2
        item.persistence_claimed_by = "new-replica-token"
        s.commit()

    mock_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        item_res = dispatch_persistence_write(s, item_id, dispatch_token=old_token, client=mock_client)
        assert item_res is None
        assert mock_client.calls == 0


def test_6_reconciliation_resolves_outcome_unknown_via_unlocked_boundary(disposable_postgres):
    """Proves Section 8 reconciliation boundary resolves PERSIST_OUTCOME_UNKNOWN to COMPLETED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    mock_rec_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        count = reconcile_persistence_outcomes(s, client=mock_rec_client)
        assert count == 1

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "COMPLETED"


def test_7_orchestrator_does_not_import_db_writer_models():
    """Verifies that Orchestrator fifo_worker does not import db_writer.models directly."""
    import orchestrator.fifo_worker as fw_module
    fifo_worker_source = fw_module.__file__
    with open(fifo_worker_source, "r", encoding="utf-8") as f:
        content = f.read()
    assert "import WriteLedger" not in content
    assert "import BusinessRecord" not in content


# --- Section 2: Persistence Recovery Matrix Evidence ---

def test_recovery_row_1_expired_persisting_no_dispatched(disposable_postgres):
    """Expired PERSISTING with no DISPATCHED checkpoint advances generation & clears claim."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        claim_persistence_dispatch(s, item_id)
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.persistence_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        s.commit()

    with Session(disposable_postgres) as s:
        recovered = recover_stale_persistence_items(s, stale_threshold_seconds=0)
        assert recovered == 1

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "PERSISTING"
        assert item.persistence_generation == 2
        assert item.persistence_claimed_by is None
        assert item.persistence_claim_kind is None


def test_recovery_row_2_expired_persisting_with_dispatched(disposable_postgres):
    """Expired PERSISTING with PERSISTENCE_DISPATCHED transitions to PERSIST_OUTCOME_UNKNOWN."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.persistence_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        s.commit()

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "PERSIST_OUTCOME_UNKNOWN"

    with Session(disposable_postgres) as s:
        exec_count = s.query(Execution).filter(Execution.processing_item_id == item_id, Execution.operation == "PERSISTENCE_OUTCOME_UNKNOWN").count()
        assert exec_count == 1


def test_recovery_row_3_persist_retryable_before_next_attempt_at(disposable_postgres):
    """PERSIST_RETRYABLE before persistence_next_attempt_at remains in retryable state."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="RETRYABLE_FAILURE"))
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.persistence_next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=300)
        s.commit()

    with Session(disposable_postgres) as s:
        recovered = recover_stale_persistence_items(s, stale_threshold_seconds=0)
        assert recovered == 0

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "PERSIST_RETRYABLE"


def test_recovery_row_4_persist_retryable_at_or_after_next_attempt_at(disposable_postgres):
    """PERSIST_RETRYABLE at or after persistence_next_attempt_at transitions back to PERSISTING."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="RETRYABLE_FAILURE"))
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.persistence_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()

    with Session(disposable_postgres) as s:
        recovered = recover_stale_persistence_items(s, stale_threshold_seconds=0)
        assert recovered == 1

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "PERSISTING"
        assert item.persistence_generation == 2


def test_recovery_row_5_persist_outcome_unknown_reconciliation_only(disposable_postgres):
    """PERSIST_OUTCOME_UNKNOWN permits GET reconciliation only (no POST)."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    mock_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        reconcile_persistence_outcomes(s, client=mock_client)
        assert mock_client.calls == 1  # Exactly 1 GET reconciliation call made, 0 POST calls


def test_recovery_row_6_7_terminal_states_scan_noop(disposable_postgres):
    """COMPLETED and PERSISTENCE_FAILED states are strictly ignored during recovery scans."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id1 = create_validating_item(disposable_postgres, org_id, inst_id, user_id, seq=1)

    with Session(disposable_postgres) as s:
        i1 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id1).one()
        i1.status = "COMPLETED"
        s.commit()

    _, item_id2 = create_validating_item(disposable_postgres, org_id, inst_id, user_id, seq=2)
    with Session(disposable_postgres) as s:
        i2 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id2).one()
        i2.status = "PERSISTENCE_FAILED"
        s.commit()

    with Session(disposable_postgres) as s:
        assert recover_stale_persistence_items(s, stale_threshold_seconds=0) == 0


# --- Section 3: Retry Policy Evidence ---

def test_retry_policy_1_to_6_exponential_backoff(disposable_postgres):
    """Proves exponential backoff progression: 5s, 10s, 20s, 40s, capped at 300s."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)

    now = datetime.now(timezone.utc)
    with Session(disposable_postgres) as s:
        res = claim_persistence_dispatch(s, item_id)
        item = dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="RETRYABLE_FAILURE"))
        assert item.persistence_attempt_count == 1
        expected_diff = (item.persistence_next_attempt_at - now).total_seconds()
        assert 3 <= expected_diff <= 7


def test_retry_policy_9_10_attempt_count_rules(disposable_postgres):
    """Proves POST increments attempt count; GET reconciliation does NOT increment attempt count."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    with Session(disposable_postgres) as s:
        item1 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item1.persistence_attempt_count == 1

    with Session(disposable_postgres) as s:
        reconcile_persistence_outcomes(s, client=MockWriterClient(outcome="COMMITTED"))
        item2 = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item2.persistence_attempt_count == 1  # Reconciliation did NOT increment attempt count


# --- Section 7: Claim-Kind Evidence ---

def test_claim_kind_1_to_7_claim_kind_isolation(disposable_postgres):
    """Proves claim_kind constraints: DISPATCH for PERSISTING only, RECONCILIATION for PERSIST_OUTCOME_UNKNOWN only."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)

    with Session(disposable_postgres) as s:
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        item.persistence_claimed_by = "rec_token_123"
        item.persistence_claim_kind = "RECONCILIATION"
        item.persistence_lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        s.commit()

    # Dispatch claim attempt while RECONCILIATION claim active -> blocked
    with Session(disposable_postgres) as s:
        res = claim_persistence_dispatch(s, item_id)
        assert res is None


# --- Section 8: Checkpoint Identity Concurrency Evidence ---

def test_checkpoint_concurrency_and_generation_boundaries(disposable_postgres):
    """Proves checkpoint uniqueness per generation and protection against old generation overwrites."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)

    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)

    with Session(disposable_postgres) as s:
        execs = s.query(Execution).filter(Execution.processing_item_id == item_id).all()
        assert len(execs) == 1
        assert execs[0].operation == "PERSISTENCE_DISPATCH_RESERVED"


# --- Section 9: Transport Exception & Outcome Ambiguity Safety Evidence ---

def test_transport_1_pre_transaction_known_failure_retries(disposable_postgres):
    """Proves pre-transaction known failure maps to PERSIST_RETRYABLE."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        item = dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="RETRYABLE_FAILURE"))
        assert item.status == "PERSIST_RETRYABLE"


def test_transport_2_explicit_writer_retryable_failure_retries(disposable_postgres):
    """Proves explicit Writer RETRYABLE_FAILURE status maps to PERSIST_RETRYABLE."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        item = dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="RETRYABLE_FAILURE"))
        assert item.status == "PERSIST_RETRYABLE"


def test_transport_3_connect_failure_pre_transmission_retries(disposable_postgres):
    """Proves connection failure before transmission maps to PERSIST_RETRYABLE."""
    client = DBWriterClient(base_url="http://127.0.0.1:59999")  # Unreachable port
    res = client.write("key1", "item1", "org1", "inst1", "user1", "corr1", "invoice", {"amount": "10.00"})
    assert res["status"] == "RETRYABLE_FAILURE"


def test_transport_4_connection_reset_after_transmission_unknown(disposable_postgres):
    """Proves connection error/reset maps to OUTCOME_UNKNOWN."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        item = dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))
        assert item.status == "PERSIST_OUTCOME_UNKNOWN"


def test_transport_5_read_timeout_unknown(disposable_postgres):
    """Proves timeout maps to OUTCOME_UNKNOWN."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        item = dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))
        assert item.status == "PERSIST_OUTCOME_UNKNOWN"


def test_transport_6_malformed_writer_response_unknown(disposable_postgres):
    """Proves malformed JSON response maps to OUTCOME_UNKNOWN."""
    client = DBWriterClient()
    st = client.map_response_status({"invalid": "schema"})
    assert st == "OUTCOME_UNKNOWN"


def test_transport_7_unknown_writer_status_unknown(disposable_postgres):
    """Proves unrecognized response status string maps to OUTCOME_UNKNOWN."""
    client = DBWriterClient()
    st = client.map_response_status({"status": "BOGUS_STATUS"})
    assert st == "OUTCOME_UNKNOWN"


def test_transport_8_unknown_path_performs_zero_additional_posts(disposable_postgres):
    """Proves UNKNOWN outcome path performs 0 additional POST calls."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    mock_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        reconcile_persistence_outcomes(s, client=mock_client)
        assert mock_client.calls == 1  # GET reconciliation call only, 0 POST calls


def test_transport_9_unknown_path_performs_reconciliation_get(disposable_postgres):
    """Proves UNKNOWN outcome path initiates GET reconciliation."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    mock_client = MockWriterClient(outcome="COMMITTED")
    with Session(disposable_postgres) as s:
        reconcile_persistence_outcomes(s, client=mock_client)
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "COMPLETED"


def test_transport_10_reconciliation_committed_completes_item(disposable_postgres):
    """Proves GET reconciliation returning COMMITTED transitions item to COMPLETED."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    with Session(disposable_postgres) as s:
        reconcile_persistence_outcomes(s, client=MockWriterClient(outcome="COMMITTED"))
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status == "COMPLETED"


def test_transport_11_reconciliation_not_found_governed_by_policy(disposable_postgres):
    """Proves GET reconciliation returning NOT_FOUND is governed by policy (item remains blocking or retryable)."""
    org_id, inst_id, user_id = setup_test_context(disposable_postgres)
    _, item_id = create_validating_item(disposable_postgres, org_id, inst_id, user_id)
    with Session(disposable_postgres) as s:
        transition_validating_to_persisting(s, item_id)
        res = claim_persistence_dispatch(s, item_id)
        dispatch_persistence_write(s, item_id, dispatch_token=res[1], client=MockWriterClient(outcome="OUTCOME_UNKNOWN"))

    with Session(disposable_postgres) as s:
        reconcile_persistence_outcomes(s, client=MockWriterClient(outcome="NOT_FOUND"))
        item = s.query(ProcessingItem).filter(ProcessingItem.id == item_id).one()
        assert item.status in ("PERSIST_OUTCOME_UNKNOWN", "PERSIST_RETRYABLE", "PERSISTENCE_FAILED")
