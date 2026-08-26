from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from transcription.database.migrations.profile_b_reconciliation import reconcile_profile_b
from transcription.database.migrations.schema_verifier import (
    GATE3_STATUS,
    PROFILE_A_STATUS,
    verify_gate3,
    verify_profile_a,
    verify_profile_b,
)

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "apps" / "transcription" / "alembic.ini"
TEST_URL_ENV = "GATE3_DISPOSABLE_DATABASE_URL"
PRESERVED_HOST = "localhost"
PRESERVED_PORT = 5432
PRESERVED_DB = "transcription"


def _require_disposable_url() -> str:
    url = os.environ.get(TEST_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_URL_ENV} is required for disposable PostgreSQL validation")
    parsed = urlparse(url)
    assert parsed.hostname in {"localhost", "127.0.0.1"}
    assert parsed.port != PRESERVED_PORT
    assert parsed.path.lstrip("/") != PRESERVED_DB
    assert "gate3" in parsed.path
    return url


@pytest.fixture(scope="session")
def base_url() -> str:
    return _require_disposable_url()


def _db_url(base_url: str, name: str) -> str:
    assert name != PRESERVED_DB
    return base_url.rsplit("/", 1)[0] + "/" + name


def _admin_engine(base_url: str) -> Engine:
    return sa.create_engine(base_url, isolation_level="AUTOCOMMIT")


def _create_database(base_url: str, name: str) -> str:
    assert name.startswith("gate3_disposable_")
    admin = _admin_engine(base_url)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    return _db_url(base_url, name)


def _drop_database(base_url: str, name: str) -> None:
    admin = _admin_engine(base_url)
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


@pytest.fixture()
def disposable_db(base_url: str):
    created: list[str] = []

    def factory(suffix: str) -> str:
        name = f"gate3_disposable_{suffix}_{uuid.uuid4().hex[:8]}"
        created.append(name)
        return _create_database(base_url, name)

    yield factory

    for name in created:
        _drop_database(base_url, name)


def _engine(url: str) -> Engine:
    return sa.create_engine(url)


def _alembic(url: str) -> Config:
    parsed = urlparse(url)
    assert parsed.port != PRESERVED_PORT
    assert parsed.path.lstrip("/") != PRESERVED_DB
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ["TRANSCRIPTION_DATABASE_URL"] = url
    return cfg


def _upgrade(url: str, revision: str) -> None:
    command.upgrade(_alembic(url), revision)


def _stamp(url: str, revision: str) -> None:
    command.stamp(_alembic(url), revision)


def _enum_labels(conn: sa.Connection) -> list[str]:
    return list(
        conn.execute(
            text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = 'requeststatus'
                ORDER BY e.enumsortorder
                """
            )
        ).scalars()
    )


def _columns(conn: sa.Connection, table: str) -> dict[str, dict]:
    return {str(col["name"]): dict(col) for col in sa.inspect(conn).get_columns(table)}


def _uniques(conn: sa.Connection, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in sa.inspect(conn).get_unique_constraints(table)
    }


def _create_profile_a(conn: sa.Connection) -> None:
    conn.execute(text("CREATE TYPE requeststatus AS ENUM ('PENDING','PROCESSING','COMPLETED','FAILED')"))
    conn.execute(
        text(
            """
            CREATE TABLE applications (
              id UUID PRIMARY KEY,
              name VARCHAR(255) NOT NULL,
              api_key VARCHAR(255) NOT NULL UNIQUE,
              active BOOLEAN NOT NULL,
              created_at TIMESTAMPTZ NOT NULL
            );
            CREATE UNIQUE INDEX ix_applications_api_key ON applications(api_key);
            CREATE TABLE requests (
              id UUID PRIMARY KEY,
              application_id UUID NOT NULL REFERENCES applications(id),
              created_at TIMESTAMPTZ NOT NULL,
              completed_at TIMESTAMPTZ NULL,
              status requeststatus NOT NULL,
              processing_time_ms INTEGER NULL
            );
            CREATE INDEX ix_requests_application_id ON requests(application_id);
            CREATE TABLE extractions (
              id UUID PRIMARY KEY,
              request_id UUID NOT NULL UNIQUE REFERENCES requests(id),
              prompt TEXT NOT NULL,
              response_json JSONB NOT NULL,
              image_reference VARCHAR(512) NULL,
              created_at TIMESTAMPTZ NOT NULL
            );
            CREATE TABLE usage_logs (
              id UUID PRIMARY KEY,
              request_id UUID NOT NULL UNIQUE REFERENCES requests(id),
              model_name VARCHAR(100) NOT NULL,
              input_tokens INTEGER NOT NULL,
              output_tokens INTEGER NOT NULL,
              estimated_cost DOUBLE PRECISION NOT NULL,
              created_at TIMESTAMPTZ NOT NULL
            );
            """
        )
    )


def _insert_legacy_rows(conn: sa.Connection) -> tuple[str, str]:
    app_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO applications(id, name, api_key, active, created_at) "
            "VALUES (:id, 'legacy', 'legacy-key', true, now())"
        ),
        {"id": app_id},
    )
    first_request = ""
    statuses = ["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
    for index, status in enumerate(statuses, start=1):
        request_id = str(uuid.uuid4())
        if not first_request:
            first_request = request_id
        conn.execute(
            text(
                "INSERT INTO requests(id, application_id, created_at, completed_at, status, processing_time_ms) "
                "VALUES (:id, :app, now(), NULL, :status, :ms)"
            ),
            {"id": request_id, "app": app_id, "status": status, "ms": index * 10},
        )
        conn.execute(
            text(
                "INSERT INTO extractions(id, request_id, prompt, response_json, image_reference, created_at) "
                "VALUES (:id, :request_id, :prompt, '{\"legacy\": true}', NULL, now())"
            ),
            {"id": str(uuid.uuid4()), "request_id": request_id, "prompt": f"prompt-{status}"},
        )
        conn.execute(
            text(
                "INSERT INTO usage_logs(id, request_id, model_name, input_tokens, output_tokens, estimated_cost, created_at) "
                "VALUES (:id, :request_id, 'gemini-legacy', :input, :output, :cost, now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "request_id": request_id,
                "input": index,
                "output": index + 10,
                "cost": Decimal(f"{index}.125"),
            },
        )
    return app_id, first_request


def _create_profile_b(conn: sa.Connection, *, duplicates: bool = False) -> tuple[str, str]:
    _create_profile_a(conn)
    conn.execute(text("ALTER TYPE requeststatus ADD VALUE 'SUCCEEDED' BEFORE 'FAILED'"))
    conn.execute(text("ALTER TYPE requeststatus ADD VALUE 'PERSISTENCE_FAILED'"))
    app_id, request_id = _insert_legacy_rows(conn)
    conn.execute(
        text(
            """
            ALTER TABLE requests
              ADD COLUMN correlation_id VARCHAR(128),
              ADD COLUMN event_id UUID,
              ADD COLUMN organization_id UUID,
              ADD COLUMN instance_id UUID,
              ADD COLUMN user_id UUID,
              ADD COLUMN received_at TIMESTAMPTZ,
              ADD COLUMN source VARCHAR(64),
              ADD COLUMN processing_started_at TIMESTAMPTZ,
              ADD COLUMN last_persistence_error_at TIMESTAMPTZ,
              ADD COLUMN error_code VARCHAR(64),
              ADD COLUMN detected_mime VARCHAR(64),
              ADD COLUMN declared_mime VARCHAR(64),
              ADD COLUMN file_size_bytes INTEGER,
              ADD COLUMN file_sha256 VARCHAR(64);
            ALTER TABLE usage_logs
              ADD COLUMN attempt_number INTEGER,
              ADD COLUMN provider VARCHAR(100),
              ADD COLUMN status VARCHAR(64),
              ADD COLUMN started_at TIMESTAMPTZ,
              ADD COLUMN completed_at TIMESTAMPTZ,
              ADD COLUMN total_tokens INTEGER,
              ADD COLUMN cached_tokens INTEGER,
              ADD COLUMN usage_status VARCHAR(32),
              ADD COLUMN currency VARCHAR(8),
              ADD COLUMN pricing_version VARCHAR(32),
              ADD COLUMN sanitized_error_code VARCHAR(64);
            UPDATE usage_logs SET attempt_number = 1;
            ALTER TABLE usage_logs ALTER COLUMN attempt_number SET NOT NULL;
            ALTER TABLE usage_logs ALTER COLUMN input_tokens DROP NOT NULL;
            ALTER TABLE usage_logs ALTER COLUMN output_tokens DROP NOT NULL;
            """
        )
    )
    if duplicates:
        conn.execute(text("ALTER TABLE usage_logs DROP CONSTRAINT usage_logs_request_id_key"))
        conn.execute(
            text(
                "INSERT INTO usage_logs(id, request_id, model_name, input_tokens, output_tokens, estimated_cost, created_at, attempt_number) "
                "VALUES (:id, :request_id, 'gemini-legacy', 2, 3, 0.5, now(), 1)"
            ),
            {"id": str(uuid.uuid4()), "request_id": request_id},
        )
    return app_id, request_id


def test_fresh_database_migration_to_gate3(disposable_db) -> None:
    url = disposable_db("fresh")
    _upgrade(url, "head")
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            tables = set(sa.inspect(conn).get_table_names())
            assert {"applications", "requests", "extractions", "usage_logs", "alembic_version_transcription"} <= tables
            assert "alembic_version" not in tables
            assert "organizations" not in tables
            assert conn.execute(text("SELECT version_num FROM alembic_version_transcription")).scalar_one() == "gate3_schema"
            assert _enum_labels(conn) == GATE3_STATUS
            assert verify_gate3(conn, require_version_table=True).ok
    finally:
        engine.dispose()


def test_baseline_matches_historical_v1(disposable_db) -> None:
    url = disposable_db("baseline")
    _upgrade(url, "transcription_1_0_baseline")
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            assert verify_profile_a(conn).ok
            assert _enum_labels(conn) == PROFILE_A_STATUS
            assert "attempt_number" not in _columns(conn, "usage_logs")
            assert not _columns(conn, "requests")["application_id"]["nullable"]
            assert not _columns(conn, "extractions")["prompt"]["nullable"]
            assert tuple(["request_id"]) in _uniques(conn, "usage_logs").values()
            assert "DOUBLE" in str(_columns(conn, "usage_logs")["estimated_cost"]["type"]).upper()
    finally:
        engine.dispose()


def test_canonical_gate3_upgrade_preserves_legacy_data_and_constraints(disposable_db) -> None:
    url = disposable_db("canonical")
    _upgrade(url, "transcription_1_0_baseline")
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            _, request_id = _insert_legacy_rows(conn)
        _upgrade(url, "head")
        with engine.connect() as conn:
            assert verify_gate3(conn, require_version_table=True).ok
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM requests")).scalar_one() == 4
            assert conn.execute(text("SELECT count(*) FROM extractions WHERE prompt LIKE 'prompt-%'")).scalar_one() == 4
            assert conn.execute(text("SELECT count(*) FROM usage_logs WHERE attempt_number = 1")).scalar_one() == 4
            assert not _columns(conn, "usage_logs")["attempt_number"]["nullable"]
            assert _columns(conn, "usage_logs")["attempt_number"]["default"] is None
            assert _columns(conn, "requests")["status"]["default"] is None
            assert tuple(["request_id"]) not in _uniques(conn, "usage_logs").values()
            assert tuple(["request_id", "attempt_number"]) in _uniques(conn, "usage_logs").values()
            assert _enum_labels(conn) == GATE3_STATUS
            assert conn.execute(text("SELECT estimated_cost FROM usage_logs ORDER BY estimated_cost LIMIT 1")).scalar_one() == Decimal("1.12500000")
            conn.execute(
                text(
                    "INSERT INTO usage_logs(id, request_id, attempt_number, model_name, created_at) "
                    "VALUES (:id, :request_id, 2, 'gemini-legacy', now())"
                ),
                {"id": str(uuid.uuid4()), "request_id": request_id},
            )
            with pytest.raises(sa.exc.IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO usage_logs(id, request_id, attempt_number, model_name, created_at) "
                        "VALUES (:id, :request_id, 2, 'gemini-legacy', now())"
                    ),
                    {"id": str(uuid.uuid4()), "request_id": request_id},
                )
    finally:
        engine.dispose()


def test_profile_a_adoption_stamp_and_upgrade(disposable_db) -> None:
    url = disposable_db("profile_a")
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            _create_profile_a(conn)
        with engine.connect() as conn:
            assert verify_profile_a(conn).ok
            assert not verify_profile_b(conn).ok
            assert "alembic_version_transcription" not in sa.inspect(conn).get_table_names()
        _stamp(url, "transcription_1_0_baseline")
        _upgrade(url, "head")
        with engine.connect() as conn:
            assert verify_gate3(conn, require_version_table=True).ok
            assert conn.execute(text("SELECT version_num FROM alembic_version_transcription")).scalar_one() == "gate3_schema"
    finally:
        engine.dispose()


def test_profile_b_reconciliation_and_stamp(disposable_db) -> None:
    url = disposable_db("profile_b")
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            _create_profile_b(conn)
        with engine.connect() as conn:
            assert verify_profile_b(conn).ok
            result = reconcile_profile_b(conn)
            assert result.ok, result.mismatches
            assert verify_gate3(conn).ok
            assert conn.execute(text("SELECT count(*) FROM requests")).scalar_one() == 4
        _stamp(url, "gate3_schema")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version_transcription")).scalar_one() == "gate3_schema"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "case_sql, expected_fragment",
    [
        ("ALTER TYPE requeststatus ADD VALUE 'BOGUS'", "enum"),
        ("ALTER TABLE usage_logs ALTER COLUMN attempt_number DROP NOT NULL", "attempt_number"),
        ("ALTER TABLE usage_logs DROP CONSTRAINT usage_logs_request_id_key", "request-only"),
        ("ALTER TABLE requests ADD COLUMN request_id UUID", "requests columns mismatch"),
        ("ALTER TABLE usage_logs ALTER COLUMN estimated_cost TYPE NUMERIC(10,2) USING estimated_cost::numeric(10,2)", "floating physical type"),
        ("ALTER TABLE requests ADD COLUMN unexpected_column TEXT", "unexpected"),
    ],
)
def test_profile_b_rejects_unsupported_drift_without_reconciliation(disposable_db, case_sql: str, expected_fragment: str) -> None:
    url = disposable_db("drift")
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            _create_profile_b(conn)
            conn.execute(text(case_sql))
        with engine.connect() as conn:
            preflight = verify_profile_b(conn)
            assert not preflight.ok
            assert any(expected_fragment in item for item in preflight.mismatches)
            before = set(sa.inspect(conn).get_table_names())
            result = reconcile_profile_b(conn)
            assert not result.ok
            after = set(sa.inspect(conn).get_table_names())
            assert before == after
            assert "alembic_version_transcription" not in after
    finally:
        engine.dispose()


def test_profile_b_rejects_duplicate_attempt_pairs(disposable_db) -> None:
    url = disposable_db("duplicates")
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            _create_profile_b(conn, duplicates=True)
        with engine.connect() as conn:
            preflight = verify_profile_b(conn)
            assert not preflight.ok
            assert any("duplicate request/attempt" in item for item in preflight.mismatches)
            result = reconcile_profile_b(conn)
            assert not result.ok
            assert "alembic_version_transcription" not in sa.inspect(conn).get_table_names()
    finally:
        engine.dispose()
