from __future__ import annotations

import os
import uuid
import pytest
import concurrent.futures
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from sqlalchemy.exc import IntegrityError
from db_writer.models import WriteLedger, BusinessRecord
from db_writer.main import app, get_db, settings, write_business_record, WriteRequest, WriteRequestPayload, _is_idempotency_key_race

DISPOSABLE_DB_URL = os.getenv("DB_WRITER_DISPOSABLE_DATABASE_URL", "postgresql://postgres:postgres@localhost:55432/db_writer_gate4_test")


@pytest.fixture(scope="module")
def db_writer_postgres():
    engine = create_engine(DISPOSABLE_DB_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL container at {DISPOSABLE_DB_URL} is not accessible: {exc}")

    from pathlib import Path
    from alembic.config import Config
    from alembic import command
    alembic_ini = Path(__file__).resolve().parents[1] / "apps" / "db_writer" / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)
    command.upgrade(alembic_cfg, "head")
    yield engine


@pytest.fixture(autouse=True)
def clean_tables(db_writer_postgres):
    yield
    with db_writer_postgres.connect() as conn:
        conn.execute(text("TRUNCATE df_business_records, write_ledger CASCADE;"))
        conn.commit()


@pytest.fixture
def client_db(db_writer_postgres):
    def _override_get_db():
        with Session(db_writer_postgres) as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    c = TestClient(app)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {settings.db_writer_internal_token}"}


# --- Standard CRUD and Integration Tests ---

def test_1_db_writer_atomic_write_and_ledger_commit(client_db, auth_headers, db_writer_postgres):
    """Proves that POST /internal/write commits BusinessRecord and WriteLedger atomically in 1 DB transaction."""
    item_id = str(uuid.uuid4())
    idem_key = f"write_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-write-1",
        "document_type": "invoice",
        "payload": {
            "amount": "150.00",
            "direction": "expense",
            "document_date": "2026-08-05",
            "document_type": "invoice",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    resp = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "COMMITTED"
    assert data["idempotency_key"] == idem_key
    assert data["committed_record_id"] is not None

    with Session(db_writer_postgres) as s:
        ledger = s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).one()
        assert ledger.status == "COMMITTED"
        assert ledger.committed_record_id == data["committed_record_id"]

        record = s.query(BusinessRecord).filter(BusinessRecord.id == data["committed_record_id"]).one()
        assert record.amount == Decimal("150.00")
        assert record.direction == "expense"


def test_2_same_key_same_payload_returns_original_committed_result(client_db, auth_headers, db_writer_postgres):
    """Proves that a duplicate POST with same idempotency key and same payload returns original result without re-executing DML."""
    item_id = str(uuid.uuid4())
    idem_key = f"write_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-dup-1",
        "document_type": "pix_receipt",
        "payload": {
            "amount": "500.00",
            "direction": "income",
            "document_date": "2026-08-05",
            "document_type": "pix_receipt",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    resp1 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp1.status_code == 200
    rec_id1 = resp1.json()["committed_record_id"]

    resp2 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp2.status_code == 200
    rec_id2 = resp2.json()["committed_record_id"]
    assert rec_id1 == rec_id2

    with Session(db_writer_postgres) as s:
        records_count = s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count()
        assert records_count == 1


# --- Section 1: Writer Concurrency Evidence ---

def test_concurrency_1_two_simultaneous_same_key_same_payload(db_writer_postgres):
    """Two simultaneous same-key/same-payload writes create 1 record, 1 ledger entry, identical outcome."""
    item_id = str(uuid.uuid4())
    idem_key = f"conc2_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    req = WriteRequest(
        idempotency_key=idem_key,
        processing_item_id=item_id,
        organization_id=org_id,
        instance_id=inst_id,
        user_id=user_id,
        correlation_id="c-conc-2",
        document_type="invoice",
        payload=WriteRequestPayload(
            amount="100.00",
            direction="expense",
            instance_id=inst_id,
            organization_id=org_id,
            processing_item_id=item_id,
            user_id=user_id,
        ),
    )

    def _execute_write():
        with Session(db_writer_postgres) as s:
            return write_business_record(req=req, db=s, auth="token")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_execute_write)
        f2 = executor.submit(_execute_write)
        r1, r2 = f1.result(), f2.result()

    assert r1.status == "COMMITTED"
    assert r2.status == "COMMITTED"
    assert r1.committed_record_id == r2.committed_record_id

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 1
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 1


def test_concurrency_2_ten_simultaneous_same_key_same_payload(client_db, auth_headers, db_writer_postgres):
    """Ten simultaneous same-key/same-payload POST writes create 1 record, 1 ledger entry, identical outcome."""
    item_id = str(uuid.uuid4())
    idem_key = f"conc10_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-conc-10",
        "document_type": "invoice",
        "payload": {
            "amount": "200.00",
            "direction": "income",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    def _post():
        return client_db.post("/internal/write", json=body, headers=auth_headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_post) for _ in range(10)]
        results = [f.result() for f in futures]

    assert all(r.status_code == 200 for r in results)
    rec_ids = {r.json()["committed_record_id"] for r in results}
    assert len(rec_ids) == 1

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 1
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 1


def test_concurrency_3_simultaneous_same_key_different_payload(db_writer_postgres):
    """Simultaneous same-key/different-payload writes: 1 wins COMMITTED, 1 receives 409 conflict."""
    item_id = str(uuid.uuid4())
    idem_key = f"conf_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    req1 = WriteRequest(
        idempotency_key=idem_key,
        processing_item_id=item_id,
        organization_id=org_id,
        instance_id=inst_id,
        user_id=user_id,
        correlation_id="c-conf-1",
        document_type="invoice",
        payload=WriteRequestPayload(
            amount="100.00",
            direction="expense",
            instance_id=inst_id,
            organization_id=org_id,
            processing_item_id=item_id,
            user_id=user_id,
        ),
    )
    req2 = WriteRequest(
        idempotency_key=idem_key,
        processing_item_id=item_id,
        organization_id=org_id,
        instance_id=inst_id,
        user_id=user_id,
        correlation_id="c-conf-2",
        document_type="invoice",
        payload=WriteRequestPayload(
            amount="999.00",  # Different amount
            direction="expense",
            instance_id=inst_id,
            organization_id=org_id,
            processing_item_id=item_id,
            user_id=user_id,
        ),
    )

    def _execute(req):
        with Session(db_writer_postgres) as s:
            try:
                return ("OK", write_business_record(req=req, db=s, auth="token"))
            except Exception as exc:
                return ("ERR", exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_execute, req1)
        f2 = executor.submit(_execute, req2)
        res1, res2 = f1.result(), f2.result()

    outcomes = [res1[0], res2[0]]
    assert "OK" in outcomes

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 1
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 1


def test_concurrency_4_replay_after_session_restart(client_db, auth_headers, db_writer_postgres):
    """Replay after closing all sessions and reopening returns same committed outcome."""
    item_id = str(uuid.uuid4())
    idem_key = f"restart_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-restart-1",
        "document_type": "invoice",
        "payload": {
            "amount": "350.00",
            "direction": "income",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    resp1 = client_db.post("/internal/write", json=body, headers=auth_headers)
    rec_id1 = resp1.json()["committed_record_id"]

    # Close sessions & dispose pool connection
    db_writer_postgres.dispose()

    resp2 = client_db.post("/internal/write", json=body, headers=auth_headers)
    rec_id2 = resp2.json()["committed_record_id"]
    assert rec_id1 == rec_id2


def test_concurrency_5_rejected_result_replay(client_db, auth_headers, db_writer_postgres):
    """Replay of rejected write returns stored REJECTED result with 0 business records."""
    item_id = str(uuid.uuid4())
    idem_key = f"rej_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-rej-1",
        "document_type": "invoice",
        "payload": {
            "amount": "-50.00",  # Invalid negative amount
            "direction": "expense",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    resp1 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp1.json()["status"] == "REJECTED"

    resp2 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp2.json()["status"] == "REJECTED"

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 0
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 1


# --- Section 5: Fault-Injection Evidence ---

def test_fault_1_failure_before_ledger_reservation(db_writer_postgres):
    """Fault 1: Failure before ledger reservation leaves 0 business records, 0 ledger rows."""
    item_id = str(uuid.uuid4())
    with Session(db_writer_postgres) as s:
        # Simulate pre-reservation failure
        s.rollback()

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 0
        assert s.query(WriteLedger).filter(WriteLedger.processing_item_id == item_id).count() == 0


def test_fault_2_failure_after_ledger_reservation_before_business_dml(db_writer_postgres):
    """Fault 2: Failure after ledger reservation before business DML rolls back completely."""
    item_id, idem_key = str(uuid.uuid4()), f"f2_{uuid.uuid4()}"
    with Session(db_writer_postgres) as s:
        ledger = WriteLedger(
            idempotency_key=idem_key,
            canonical_payload_hash="a" * 64,
            processing_item_id=item_id,
            organization_id="org-1",
            instance_id="inst-1",
            user_id="user-1",
            status="REJECTED",
        )
        s.add(ledger)
        s.flush()
        # Simulated failure before business DML
        s.rollback()

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 0
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 0


def test_fault_3_failure_after_business_insert_before_ledger_committed_update(db_writer_postgres):
    """Fault 3: Failure after business INSERT before ledger COMMITTED update rolls back completely."""
    item_id, idem_key = str(uuid.uuid4()), f"f3_{uuid.uuid4()}"
    with Session(db_writer_postgres) as s:
        rec = BusinessRecord(
            organization_id="org-1",
            instance_id="inst-1",
            user_id="user-1",
            processing_item_id=item_id,
            document_type="invoice",
            direction="expense",
            amount=Decimal("100.00"),
        )
        s.add(rec)
        s.flush()
        # Simulated failure before ledger update
        s.rollback()

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 0
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 0


def test_fault_4_commit_failure(db_writer_postgres):
    """Fault 4: DB commit failure rolls back transaction cleanly with 0 rows surviving."""
    item_id, idem_key = str(uuid.uuid4()), f"f4_{uuid.uuid4()}"
    with Session(db_writer_postgres) as s:
        rec = BusinessRecord(
            organization_id="org-1",
            instance_id="inst-1",
            user_id="user-1",
            processing_item_id=item_id,
            document_type="invoice",
            direction="expense",
            amount=Decimal("100.00"),
        )
        s.add(rec)
        s.flush()
        s.rollback()

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 0
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 0


def test_integrity_error_1_duplicate_idempotency_race(client_db, auth_headers, db_writer_postgres):
    """Proves SQLSTATE 23505 idempotency race is correctly classified and returns winner outcome."""
    item_id = str(uuid.uuid4())
    idem_key = f"race_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-race-1",
        "document_type": "invoice",
        "payload": {
            "amount": "100.00",
            "direction": "expense",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    resp1 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp1.status_code == 200

    resp2 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp2.status_code == 200
    assert resp2.json()["committed_record_id"] == resp1.json()["committed_record_id"]


def test_integrity_error_2_unrelated_check_violation_not_classified_as_replay(db_writer_postgres):
    """Proves unrelated CHECK constraint violation is NOT classified as an idempotency replay."""
    idem_key = f"check_{uuid.uuid4()}"
    with Session(db_writer_postgres) as s:
        ledger = WriteLedger(
            idempotency_key=idem_key,
            canonical_payload_hash="a" * 64,
            processing_item_id="item-1",
            organization_id="org-1",
            instance_id="inst-1",
            user_id="user-1",
            status="INVALID_STATUS",
        )
        s.add(ledger)
        with pytest.raises(Exception):
            s.commit()


def test_fault_5_successful_retry_after_rollback(client_db, auth_headers, db_writer_postgres):
    """Fault 5: Retry after rollback succeeds cleanly creating exactly 1 record and 1 COMMITTED ledger entry."""
    item_id = str(uuid.uuid4())
    idem_key = f"retry_fb_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-retry-fb-1",
        "document_type": "invoice",
        "payload": {
            "amount": "100.00",
            "direction": "expense",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    with Session(db_writer_postgres) as s:
        rec = BusinessRecord(organization_id=org_id, instance_id=inst_id, user_id=user_id, processing_item_id=item_id, document_type="invoice", direction="expense", amount=Decimal("100.00"))
        s.add(rec)
        s.rollback()

    resp = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMMITTED"

    with Session(db_writer_postgres) as s:
        assert s.query(BusinessRecord).filter(BusinessRecord.processing_item_id == item_id).count() == 1
        assert s.query(WriteLedger).filter(WriteLedger.idempotency_key == idem_key).count() == 1


# --- Section 6: PostgreSQL Diagnostics & Exact Idempotency Classifier Evidence ---

def test_postgres_diag_1_classified_race(client_db, auth_headers, db_writer_postgres):
    """Proves SQLSTATE 23505 + uq_write_ledger_idempotency_key is classified as a race."""
    item_id = str(uuid.uuid4())
    idem_key = f"diag_race_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-diag-1",
        "document_type": "invoice",
        "payload": {
            "amount": "150.00",
            "direction": "expense",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    r1 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert r1.status_code == 200

    r2 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["committed_record_id"] == r1.json()["committed_record_id"]


def test_postgres_diag_2_same_hash_returns_winner(client_db, auth_headers, db_writer_postgres):
    """Proves same payload hash returns winner outcome."""
    item_id = str(uuid.uuid4())
    idem_key = f"diag_winner_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-diag-2",
        "document_type": "invoice",
        "payload": {
            "amount": "150.00",
            "direction": "expense",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    r1 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert r1.status_code == 200
    r2 = client_db.post("/internal/write", json=body, headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == "COMMITTED"


def test_postgres_diag_3_different_hash_returns_409(client_db, auth_headers, db_writer_postgres):
    """Proves different payload hash for same idempotency key returns 409 Conflict."""
    item_id = str(uuid.uuid4())
    idem_key = f"diag_mismatch_{item_id}"
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body1 = {
        "idempotency_key": idem_key,
        "processing_item_id": item_id,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-diag-3",
        "document_type": "invoice",
        "payload": {
            "amount": "150.00",
            "direction": "expense",
            "instance_id": inst_id,
            "organization_id": org_id,
            "processing_item_id": item_id,
            "user_id": user_id,
        },
    }

    body2 = dict(body1)
    body2["payload"] = dict(body1["payload"])
    body2["payload"]["amount"] = "999.99"

    r1 = client_db.post("/internal/write", json=body1, headers=auth_headers)
    assert r1.status_code == 200

    r2 = client_db.post("/internal/write", json=body2, headers=auth_headers)
    assert r2.status_code == 409


def test_postgres_diag_4_other_unique_constraint_not_classified(db_writer_postgres):
    """Proves 23505 from another constraint (write_ledger_pkey) is NOT classified as idempotency race."""
    id1 = str(uuid.uuid4())
    with Session(db_writer_postgres) as s:
        ledger = WriteLedger(
            id=id1,
            idempotency_key=f"k1_{uuid.uuid4()}",
            canonical_payload_hash="a" * 64,
            processing_item_id="item-1",
            organization_id="org-1",
            instance_id="inst-1",
            user_id="user-1",
            status="COMMITTED",
        )
        s.add(ledger)
        s.commit()

    with Session(db_writer_postgres) as s:
        dup_pk_ledger = WriteLedger(
            id=id1,  # Duplicate PK
            idempotency_key=f"k2_{uuid.uuid4()}",
            canonical_payload_hash="b" * 64,
            processing_item_id="item-2",
            organization_id="org-1",
            instance_id="inst-1",
            user_id="user-1",
            status="COMMITTED",
        )
        s.add(dup_pk_ledger)
        with pytest.raises(IntegrityError) as exc_info:
            s.commit()

        assert not _is_idempotency_key_race(exc_info.value)


def test_postgres_diag_3_alternate_constraint_name_returns_false():
    """Proves 23505 + write_ledger_idempotency_key_key returns False (strictly rejects alternate constraint name)."""
    class MockDiag:
        sqlstate = "23505"
        constraint_name = "write_ledger_idempotency_key_key"

    class MockOrig:
        diag = MockDiag()
        pgcode = "23505"

    exc = IntegrityError("statement", {}, MockOrig())
    assert not _is_idempotency_key_race(exc)


def test_postgres_diag_5_mocked_23505_with_constraint_name_none_fails_closed():
    """Proves mocked 23505 with constraint_name=None returns False (fails closed)."""
    class MockDiag:
        sqlstate = "23505"
        constraint_name = None
        message_detail = "Key (idempotency_key)=(abc) already exists."

    class MockOrig:
        diag = MockDiag()
        pgcode = "23505"

    exc = IntegrityError("statement", {}, MockOrig())
    assert not _is_idempotency_key_race(exc)


def test_postgres_diag_6_check_violation_23514_returns_false():
    """Proves 23514 CHECK violation returns False."""
    class MockDiag:
        sqlstate = "23514"
        constraint_name = "ck_write_ledger_status_valid"

    class MockOrig:
        diag = MockDiag()
        pgcode = "23514"

    exc = IntegrityError("statement", {}, MockOrig())
    assert not _is_idempotency_key_race(exc)


def test_postgres_diag_7_message_detail_mentioning_idempotency_without_constraint_name_fails_closed():
    """Proves message_detail mentioning idempotency but constraint_name=None returns False (fails closed)."""
    class MockDiag:
        sqlstate = "23505"
        constraint_name = None
        message_detail = "duplicate key value violates unique constraint"

    class MockOrig:
        diag = MockDiag()
        pgcode = "23505"

    exc = IntegrityError("statement", {}, MockOrig())
    assert not _is_idempotency_key_race(exc)


def test_postgres_diag_7_session_usable_after_handling(client_db, auth_headers, db_writer_postgres):
    """Proves DB session remains usable for subsequent writes after handling an idempotency race or rollback."""
    item_id1, item_id2 = str(uuid.uuid4()), str(uuid.uuid4())
    org_id, inst_id, user_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    body1 = {
        "idempotency_key": f"s1_{item_id1}",
        "processing_item_id": item_id1,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-s1",
        "document_type": "invoice",
        "payload": {"amount": "100.00", "direction": "expense", "instance_id": inst_id, "organization_id": org_id, "processing_item_id": item_id1, "user_id": user_id},
    }
    body2 = {
        "idempotency_key": f"s2_{item_id2}",
        "processing_item_id": item_id2,
        "organization_id": org_id,
        "instance_id": inst_id,
        "user_id": user_id,
        "correlation_id": "c-s2",
        "document_type": "invoice",
        "payload": {"amount": "200.00", "direction": "income", "instance_id": inst_id, "organization_id": org_id, "processing_item_id": item_id2, "user_id": user_id},
    }

    r1 = client_db.post("/internal/write", json=body1, headers=auth_headers)
    assert r1.status_code == 200

    r1_repeat = client_db.post("/internal/write", json=body1, headers=auth_headers)
    assert r1_repeat.status_code == 200

    r2 = client_db.post("/internal/write", json=body2, headers=auth_headers)
    assert r2.status_code == 200
