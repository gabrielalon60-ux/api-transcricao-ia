from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from zoneinfo import ZoneInfo

from db_writer.df_adapter import LocalDFAdapter
from db_writer import main as writer_main
from db_writer.main import app, get_db, settings
from orchestrator.services.business_rules_evaluator import resolve_transaction_date


ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv(
    "GATE7_WRITER_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/db_writer_gate4_test",
)


@pytest.fixture(scope="module")
def engine():
    value = create_engine(URL)
    try:
        with value.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL 15 unavailable: {exc}")
    cfg = Config(str(ROOT / "apps/db_writer/alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", URL)
    with value.begin() as connection:
        connection.execute(
            text(
                "DROP TABLE IF EXISTS financial_records, suppliers, enterprises, "
                "write_ledger, df_business_records, db_writer_alembic_version CASCADE"
            )
        )
    command.upgrade(cfg, "head")
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE financial_records, suppliers, enterprises, write_ledger, df_business_records CASCADE"
            )
        )
    yield


@pytest.fixture
def client(engine):
    def override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _headers():
    return {"Authorization": f"Bearer {settings.db_writer_internal_token}"}


def _body(enterprise_id: str, item: str = "item-1", supplier: str | None = None):
    return {
        "idempotency_key": f"write_{item}",
        "processing_item_id": item,
        "organization_id": "org",
        "instance_id": "inst",
        "user_id": "user",
        "correlation_id": "corr",
        "document_type": "invoice",
        "schema_version": "2.0",
        "payload": {
            "amount": "25.50",
            "direction": "expense",
            "instance_id": "inst",
            "organization_id": "org",
            "processing_item_id": item,
            "user_id": "user",
            "schema_version": "2.0",
            "transaction_date": datetime.now(timezone.utc).isoformat(),
            "date_source": "DOCUMENT",
            "enterprise_id": enterprise_id,
            "supplier_cnpj_snapshot": supplier,
            "origin": "WHATSAPP",
        },
    }


def _seed(engine, supplier: bool = True) -> tuple[str, str | None]:
    enterprise_id = str(uuid.uuid4())
    supplier_id = str(uuid.uuid4()) if supplier else None
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO enterprises(id,name) VALUES (:id,'Empresa')"),
            {"id": enterprise_id},
        )
        if supplier_id:
            connection.execute(
                text(
                    "INSERT INTO suppliers(id,cnpj,name) VALUES (:id,'12345678000190','Fornecedor')"
                ),
                {"id": supplier_id},
            )
    return enterprise_id, supplier_id


def test_v2_happy_path_supplier_match_and_replay(client, engine) -> None:
    enterprise_id, supplier_id = _seed(engine)
    body = _body(enterprise_id, supplier="12.345.678/0001-90")
    first = client.post("/internal/write", json=body, headers=_headers())
    replay = client.post("/internal/write", json=body, headers=_headers())
    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == replay.json()["status"] == "COMMITTED"
    assert first.json()["committed_record_id"] == replay.json()["committed_record_id"]
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT supplier_id,expense_type_id,comments,is_deleted,deleted_at,origin FROM financial_records"
            )
        ).one()
        assert str(row.supplier_id) == supplier_id
        assert row.expense_type_id is None and row.comments is None
        assert (
            row.is_deleted is False
            and row.deleted_at is None
            and row.origin == "WHATSAPP"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM financial_records")
            ).scalar_one()
            == 1
        )


def test_unknown_supplier_is_nullable_and_snapshot_preserved(client, engine) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)
    response = client.post(
        "/internal/write",
        json=_body(enterprise_id, "item-2", "99999999000199"),
        headers=_headers(),
    )
    assert response.json()["status"] == "COMMITTED"
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT supplier_id,supplier_cnpj_snapshot FROM financial_records")
        ).one()
        assert (
            row.supplier_id is None and row.supplier_cnpj_snapshot == "99999999000199"
        )


def test_missing_enterprise_rejects_and_rolls_back(client, engine) -> None:
    response = client.post(
        "/internal/write", json=_body(str(uuid.uuid4()), "item-3"), headers=_headers()
    )
    assert response.json()["status"] == "REJECTED"
    assert response.json()["error_code"] == "ENTERPRISE_NOT_FOUND"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM financial_records")
            ).scalar_one()
            == 0
        )


def test_same_key_different_payload_conflicts(client, engine) -> None:
    enterprise_id, _ = _seed(engine)
    body = _body(enterprise_id, "item-4")
    assert (
        client.post("/internal/write", json=body, headers=_headers()).json()["status"]
        == "COMMITTED"
    )
    body["payload"]["amount"] = "30.00"
    assert (
        client.post("/internal/write", json=body, headers=_headers()).status_code == 409
    )


def test_enterprise_api_is_minimal_and_read_only(client, engine) -> None:
    enterprise_id, _ = _seed(engine)
    response = client.get("/internal/enterprises", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {
        "enterprises": [{"id": enterprise_id, "display_name": "Empresa"}]
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", 1.25),
        ("amount", "NaN"),
        ("amount", "Infinity"),
        ("amount", "-Infinity"),
        ("amount", "1.234"),
        ("amount", "0"),
        ("amount", "-1"),
        ("transaction_date", "2026-08-11T00:00:00"),
        ("document_type", "bank_receipt"),
    ],
)
def test_v2_strict_invalid_shapes_are_rejected(client, engine, field, value) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)
    body = _body(enterprise_id, f"strict-{field}-{uuid.uuid4()}")
    body["payload"][field] = value
    response = client.post("/internal/write", json=body, headers=_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM financial_records")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(text("SELECT count(*) FROM write_ledger")).scalar_one()
            == 0
        )


def test_supplier_format_variants_share_one_canonical_replay(client, engine) -> None:
    enterprise_id, _ = _seed(engine)
    formatted = _body(
        enterprise_id, "supplier-canonical", "12.345.678/0001-90"
    )
    digits = _body(enterprise_id, "supplier-canonical", "12345678000190")
    digits["payload"]["transaction_date"] = formatted["payload"]["transaction_date"]
    first = client.post("/internal/write", json=formatted, headers=_headers())
    replay = client.post("/internal/write", json=digits, headers=_headers())
    assert first.status_code == replay.status_code == 200
    assert first.json()["committed_record_id"] == replay.json()["committed_record_id"]
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT supplier_cnpj_snapshot FROM financial_records")
        ).scalar_one() == "12345678000190"
        assert connection.execute(
            text("SELECT count(*) FROM write_ledger")
        ).scalar_one() == 1


def test_malformed_supplier_rejected_before_hash_and_ledger(client, engine) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)
    body = _body(enterprise_id, "supplier-malformed", "abc12.345.678/0001-90")
    response = client.post("/internal/write", json=body, headers=_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert response.json()["error_code"] == "INVALID_SUPPLIER_CNPJ"
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM financial_records")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM write_ledger")
        ).scalar_one() == 0


def test_gate5_date_semantics_round_trip_through_timestamptz(client, engine) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)
    sao_paulo = ZoneInfo("America/Sao_Paulo")
    document_dt, source, _ = resolve_transaction_date(
        "2026-08-11", datetime(2026, 8, 12, 2, 30, tzinfo=timezone.utc)
    )
    document = _body(enterprise_id, "date-document")
    document["payload"]["transaction_date"] = document_dt.isoformat()
    document["payload"]["date_source"] = source
    assert (
        client.post("/internal/write", json=document, headers=_headers()).json()[
            "status"
        ]
        == "COMMITTED"
    )

    message_dt, message_source, _ = resolve_transaction_date(
        None, datetime(2026, 8, 12, 2, 30, tzinfo=timezone.utc)
    )
    message = _body(enterprise_id, "date-message")
    message["payload"]["transaction_date"] = message_dt.isoformat()
    message["payload"]["date_source"] = message_source
    assert (
        client.post("/internal/write", json=message, headers=_headers()).json()[
            "status"
        ]
        == "COMMITTED"
    )

    with engine.connect() as connection:
        rows = dict(
            connection.execute(
                text(
                    "SELECT processing_item_id, transaction_date FROM financial_records "
                    "WHERE processing_item_id IN ('date-document','date-message')"
                )
            ).all()
        )
    assert (
        rows["date-document"].astimezone(sao_paulo).date().isoformat() == "2026-08-11"
    )
    assert rows["date-message"].astimezone(timezone.utc) == message_dt


def test_concurrent_same_key_serializes_before_financial_insert(client, engine) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)
    body = _body(enterprise_id, "concurrent-same")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/internal/write", json=body, headers=_headers()),
                range(2),
            )
        )
    assert [response.status_code for response in responses] == [200, 200]
    ids = {response.json()["committed_record_id"] for response in responses}
    assert len(ids) == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM financial_records")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT count(*) FROM write_ledger")).scalar_one()
            == 1
        )


def test_concurrent_different_payload_cannot_overwrite_winner(client, engine) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)
    first = _body(enterprise_id, "concurrent-different")
    second = _body(enterprise_id, "concurrent-different")
    second["payload"]["amount"] = "31.00"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda body: client.post(
                    "/internal/write", json=body, headers=_headers()
                ),
                [first, second],
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM financial_records")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT count(*) FROM write_ledger")).scalar_one()
            == 1
        )


def test_flush_integrity_error_is_sanitized_rejection(
    client, engine, monkeypatch
) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)

    def fail(*_args, **_kwargs):
        raise IntegrityError("statement", {}, Exception("secret constraint detail"))

    monkeypatch.setattr(LocalDFAdapter, "insert_expense", fail)
    response = client.post(
        "/internal/write",
        json=_body(enterprise_id, "flush-integrity"),
        headers=_headers(),
    )
    assert response.json()["status"] == "REJECTED"
    assert response.json()["error_code"] == "DESTINATION_CONSTRAINT_VIOLATION"
    assert "secret" not in response.text


def test_precommit_operational_error_is_retryable(client, engine, monkeypatch) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)

    def fail(*_args, **_kwargs):
        raise OperationalError("statement", {}, Exception("secret transport detail"))

    monkeypatch.setattr(LocalDFAdapter, "insert_expense", fail)
    response = client.post(
        "/internal/write",
        json=_body(enterprise_id, "precommit-operational"),
        headers=_headers(),
    )
    assert response.json()["status"] == "RETRYABLE_FAILURE"
    assert "secret" not in response.text


def test_precommit_schema_dbapi_error_is_sanitized_rejection(
    client, engine, monkeypatch
) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)

    def fail(*_args, **_kwargs):
        raise DBAPIError(
            "statement", {}, Exception("secret schema detail"), False
        )

    monkeypatch.setattr(LocalDFAdapter, "insert_expense", fail)
    response = client.post(
        "/internal/write",
        json=_body(enterprise_id, "precommit-schema"),
        headers=_headers(),
    )
    assert response.json()["status"] == "REJECTED"
    assert response.json()["error_code"] == "DESTINATION_SCHEMA_CONTRACT_ERROR"
    assert "secret" not in response.text


@pytest.mark.parametrize(
    "commit_error",
    [
        OperationalError("statement", {}, Exception("secret operational commit")),
        DBAPIError("statement", {}, Exception("secret generic commit"), False),
    ],
    ids=["operational", "generic-dbapi"],
)
def test_commit_driver_error_is_outcome_unknown(
    engine, commit_error
) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)

    class CommitFailingSession:
        def __init__(self, session: Session):
            self.session = session

        def __getattr__(self, name):
            return getattr(self.session, name)

        def commit(self):
            raise commit_error

    def override():
        with Session(engine) as session:
            yield CommitFailingSession(session)

    app.dependency_overrides[get_db] = override
    try:
        response = TestClient(app).post(
            "/internal/write",
            json=_body(enterprise_id, f"commit-{uuid.uuid4()}"),
            headers=_headers(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["status"] == "OUTCOME_UNKNOWN"
    assert response.json()["error_code"] == "AMBIGUOUS_COMMIT"
    assert "secret" not in response.text


def test_deadline_exhaustion_starts_no_business_dml(
    client, engine, monkeypatch
) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)
    values = iter([0.0, 9.0])
    monkeypatch.setattr(writer_main, "monotonic", lambda: next(values))
    response = client.post(
        "/internal/write",
        json=_body(enterprise_id, "deadline"),
        headers=_headers(),
    )
    assert response.json()["status"] == "RETRYABLE_FAILURE"
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM financial_records")
            ).scalar_one()
            == 0
        )


def test_post_race_zero_budget_starts_no_lookup_and_retries_known_rollback(
    engine, monkeypatch
) -> None:
    enterprise_id, _ = _seed(engine, supplier=False)

    class DriverRace(Exception):
        pgcode = "23505"
        diag = SimpleNamespace(
            sqlstate="23505",
            constraint_name="uq_write_ledger_idempotency_key",
        )

    race = IntegrityError("statement", {}, DriverRace("ledger race"))
    values = iter([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 9.0])
    monkeypatch.setattr(writer_main, "monotonic", lambda: next(values))

    class RaceSession:
        def __init__(self, session: Session):
            self.session = session
            self.commit_failed = False
            self.post_failure_operations = 0

        def __getattr__(self, name):
            return getattr(self.session, name)

        def commit(self):
            self.commit_failed = True
            raise race

        def get_bind(self):
            if self.commit_failed:
                self.post_failure_operations += 1
            return self.session.get_bind()

        def execute(self, *args, **kwargs):
            if self.commit_failed:
                self.post_failure_operations += 1
            return self.session.execute(*args, **kwargs)

        def query(self, *args, **kwargs):
            if self.commit_failed:
                self.post_failure_operations += 1
            return self.session.query(*args, **kwargs)

    wrapper: RaceSession | None = None

    def override():
        nonlocal wrapper
        with Session(engine) as session:
            wrapper = RaceSession(session)
            yield wrapper

    app.dependency_overrides[get_db] = override
    try:
        response = TestClient(app).post(
            "/internal/write",
            json=_body(enterprise_id, "race-deadline"),
            headers=_headers(),
        )
    finally:
        app.dependency_overrides.clear()
    assert response.json()["status"] == "RETRYABLE_FAILURE"
    assert response.json()["error_code"] == "WRITER_DEADLINE_EXHAUSTED"
    assert wrapper is not None and wrapper.post_failure_operations == 0
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM financial_records")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT count(*) FROM write_ledger")
        ).scalar_one() == 0
