from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from db_writer import main as writer_main
from orchestrator.services.final_notification_service import reserve_final_notifications
from orchestrator.services.persistence_service import (
    claim_persistence_dispatch,
    dispatch_persistence_write,
    transition_validating_to_persisting,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = ROOT / "scripts" / "operations" / "gate9_report.py"
ADMIN_ENV = "GATE9_TEST_ADMIN_DATABASE_URL"


def _admin_url() -> str:
    value = os.environ.get(ADMIN_ENV)
    if not value:
        pytest.skip(f"{ADMIN_ENV} is required for disposable PostgreSQL 15 evidence")
    parsed = make_url(value)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Gate 9 test admin database must be loopback")
    if parsed.database != "postgres":
        pytest.fail("Gate 9 test admin DSN must name the postgres admin database")
    return value


def _create_database(admin_url: str, name: str, marker: str) -> str:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            assert not connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                {"name": name},
            )
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            connection.exec_driver_sql(
                f"COMMENT ON DATABASE \"{name}\" IS 'gate9-test:{marker}'"
            )
    finally:
        engine.dispose()
    return make_url(admin_url).set(database=name).render_as_string(hide_password=False)


def _drop_database(admin_url: str, name: str, marker: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            actual = connection.scalar(
                text(
                    "SELECT shobj_description(oid, 'pg_database') "
                    "FROM pg_database WHERE datname=:name"
                ),
                {"name": name},
            )
            if actual != f"gate9-test:{marker}":
                pytest.fail("Refusing cleanup of an unowned Gate 9 database")
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def databases() -> Iterator[dict[str, str]]:
    admin_url = _admin_url()
    marker = uuid.uuid4().hex
    names = {
        "platform": f"gate9_platform_{marker[:16]}",
        "transcription": f"gate9_transcription_{marker[:16]}",
        "writer": f"gate9_writer_{marker[:16]}",
    }
    created: list[str] = []
    urls: dict[str, str] = {"admin": admin_url}
    try:
        for key, name in names.items():
            urls[key] = _create_database(admin_url, name, marker)
            created.append(name)
        migrations = {
            "platform": ROOT / "packages" / "db" / "alembic.ini",
            "transcription": ROOT / "apps" / "transcription" / "alembic.ini",
            "writer": ROOT / "apps" / "db_writer" / "alembic.ini",
        }
        for key, config_path in migrations.items():
            config = Config(str(config_path))
            config.set_main_option("sqlalchemy.url", urls[key])
            override_name = {
                "transcription": "TRANSCRIPTION_DATABASE_URL",
                "writer": "DB_WRITER_DISPOSABLE_DATABASE_URL",
            }.get(key)
            previous = os.environ.get(override_name) if override_name else None
            if override_name:
                os.environ[override_name] = urls[key]
            try:
                command.upgrade(config, "head")
            finally:
                if override_name and previous is None:
                    os.environ.pop(override_name, None)
                elif override_name and previous is not None:
                    os.environ[override_name] = previous
        yield urls
    finally:
        for name in reversed(created):
            _drop_database(admin_url, name, marker)


def _context(connection, *, status: str, received: datetime) -> dict[str, str]:
    values = {
        key: str(uuid.uuid4())
        for key in ("org", "bot", "instance", "user", "event", "item", "correlation")
    }
    phone = f"55{uuid.uuid4().int}"[:15]
    connection.execute(
        text("INSERT INTO organizations(id,name,slug,status) VALUES (:org,'Org',:org,'ACTIVE')"),
        values,
    )
    connection.execute(
        text(
            "INSERT INTO bots(id,organization_id,name,service_key,status) "
            "VALUES (:bot,:org,'Bot',:bot,'ACTIVE')"
        ),
        values,
    )
    connection.execute(
        text(
            "INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,phone_number,status) "
            "VALUES (:instance,:org,:bot,'WUZAPI',:instance,:phone,'ACTIVE')"
        ),
        {**values, "phone": phone},
    )
    connection.execute(
        text(
            "INSERT INTO users(id,organization_id,phone_number,status) "
            "VALUES (:user,:org,:phone,'ACTIVE')"
        ),
        {**values, "phone": phone},
    )
    connection.execute(
        text(
            "INSERT INTO events(id,correlation_id,provider,external_instance_id,external_message_id,"
            "organization_id,instance_id,user_id,message_type,status,duplicate_count,received_at) "
            "VALUES (:event,:correlation,'WUZAPI',:instance,:event,:org,:instance,:user,"
            "'image','ROUTED',0,:received)"
        ),
        {**values, "received": received},
    )
    connection.execute(
        text(
            "INSERT INTO processing_items(id,event_id,correlation_id,organization_id,instance_id,user_id,"
            "sequence,status,message_received_at,file_mime_type,file_size,file_sha256,created_at,updated_at) "
            "VALUES (:item,:event,:correlation,:org,:instance,:user,1,:status,:received,"
            "'image/jpeg',10,:sha,:received,:received)"
        ),
        {**values, "status": status, "received": received, "sha": uuid.uuid4().hex * 2},
    )
    return values


def _run_report(databases: dict[str, str], *arguments: str) -> dict:
    environment = os.environ.copy()
    environment.update(
        {
            "G9_PLATFORM_DATABASE_URL": databases["platform"],
            "G9_TRANSCRIPTION_DATABASE_URL": databases["transcription"],
            "G9_WRITER_DATABASE_URL": databases["writer"],
        }
    )
    result = subprocess.run(
        [sys.executable, str(REPORT_SCRIPT), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "postgresql://" not in result.stdout + result.stderr
    return json.loads(result.stdout)


def test_g9_x03_durations_use_only_authoritative_timestamps(
    databases: dict[str, str],
) -> None:
    platform = create_engine(databases["platform"], poolclass=NullPool)
    t0 = datetime.now(UTC) - timedelta(minutes=20)
    try:
        with platform.begin() as connection:
            completed = _context(connection, status="COMPLETED", received=t0)
            unavailable = _context(
                connection, status="EXTRACTION_FAILED", received=t0 + timedelta(seconds=1)
            )
            connection.execute(
                text(
                    "UPDATE processing_items SET completed_at=:terminal,updated_at=:late "
                    "WHERE id=:item"
                ),
                {
                    "terminal": t0 + timedelta(seconds=120),
                    "late": t0 + timedelta(seconds=999),
                    "item": completed["item"],
                },
            )
            connection.execute(
                text(
                    "UPDATE processing_items SET updated_at=:late,error_code='EXTRACTION_FAILED' "
                    "WHERE id=:item"
                ),
                {"late": t0 + timedelta(seconds=999), "item": unavailable["item"]},
            )
            connection.execute(
                text(
                    "INSERT INTO user_interactions(id,processing_item_id,generation,question_type,"
                    "outbound_message_id,status,waiting_since,resolved_at) "
                    "VALUES (:id,:item,1,'transaction_amount',:id,'ANSWERED',:waiting,:resolved)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "item": completed["item"],
                    "waiting": t0 + timedelta(seconds=30),
                    "resolved": t0 + timedelta(seconds=50),
                },
            )
            notification = str(uuid.uuid4())
            for operation, second, effect, key in (
                ("FINAL_NOTIFICATION_DISPATCHED", 130, "DISPATCHED", f"a:b:{notification}"),
                ("FINAL_NOTIFICATION_ACKNOWLEDGED", 135, "ACKNOWLEDGED", f"c:d:{notification}"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO executions(id,event_id,processing_item_id,correlation_id,component,"
                        "operation,operation_idempotency_key,status,effect_status,attempt,started_at,completed_at) "
                        "VALUES (:id,:event,:item,:correlation,'ORCHESTRATOR',:operation,:key,'SUCCESS',"
                        ":effect,1,:at,:at)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "event": completed["event"],
                        "item": completed["item"],
                        "correlation": completed["correlation"],
                        "operation": operation,
                        "key": key,
                        "effect": effect,
                        "at": t0 + timedelta(seconds=second),
                    },
                )

        payload = _run_report(
            databases,
            "--since",
            (t0 - timedelta(seconds=1)).isoformat(),
            "--until",
            (t0 + timedelta(hours=1)).isoformat(),
            "durations",
        )
        rows = {row["id"]: row for row in payload["rows"]}
        assert rows[completed["item"]]["business_e2e_seconds"] == "120.000000"
        assert rows[completed["item"]]["human_wait_seconds"] == "20.000000"
        assert rows[completed["item"]]["final_notification_seconds"] == "5.000000"
        assert rows[unavailable["item"]]["duration_available"] is False
        assert rows[unavailable["item"]]["business_e2e_seconds"] is None
        assert rows[unavailable["item"]]["unavailable_reason"] == (
            "NO_AUTHORITATIVE_TERMINAL_TIMESTAMP"
        )
        summary = _run_report(
            databases,
            "--since",
            (t0 - timedelta(seconds=1)).isoformat(),
            "--until",
            (t0 + timedelta(hours=1)).isoformat(),
            "summary",
        )
        assert summary["event_count"] == 2
        assert summary["completed_items"] == 1
        assert summary["failed_items"] == 1
        assert summary["final_notification_unknown_count"] == 0
        assert summary["average_business_e2e_seconds"] == "120.0000000000000000"
        assert summary["p50_business_e2e_seconds"] == 120.0
        assert summary["p95_business_e2e_seconds"] == 120.0
    finally:
        platform.dispose()


class _WriterBridge:
    def __init__(self, client: TestClient):
        self.client = client

    def write(self, **kwargs: object) -> dict[str, object]:
        response = self.client.post(
            "/internal/write",
            json=kwargs,
            headers={
                "Authorization": f"Bearer {writer_main.settings.db_writer_internal_token}",
                "X-Correlation-ID": str(kwargs["correlation_id"]),
            },
        )
        assert response.status_code == 200, response.text
        return response.json()


def test_g9_x04_real_writer_rejection_is_correlated_without_sensitive_payload(
    databases: dict[str, str],
) -> None:
    platform = create_engine(databases["platform"], poolclass=NullPool)
    writer = create_engine(databases["writer"], poolclass=NullPool)
    transcription = create_engine(databases["transcription"], poolclass=NullPool)
    secret = "never-print-gate9-payload"
    now = datetime.now(UTC)
    try:
        with platform.begin() as connection:
            context = _context(connection, status="VALIDATING", received=now)
            connection.execute(
                text(
                    "UPDATE processing_items SET direction='expense',amount=42.50,"
                    "transaction_date=:date,date_source='DOCUMENT',document_type='pix_receipt',"
                    "enterprise_id=:enterprise,normalized_data=CAST(:normalized AS json) WHERE id=:item"
                ),
                {
                    "date": now,
                    "enterprise": str(uuid.uuid4()),
                    "normalized": json.dumps({"receiver_cpf_cnpj": "12345678000190", "raw": secret}),
                    "item": context["item"],
                },
            )
        with transcription.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO requests(id,status,correlation_id,received_at,source,created_at) "
                    "VALUES (CAST(:id AS uuid),'SUCCEEDED',:correlation,:now,'WHATSAPP',:now)"
                ),
                {"id": context["item"], "correlation": context["correlation"], "now": now},
            )

        def override():
            with Session(writer) as session:
                yield session

        writer_main.app.dependency_overrides[writer_main.get_db] = override
        bridge = _WriterBridge(TestClient(writer_main.app))
        with Session(platform) as session:
            item = transition_validating_to_persisting(
                session,
                context["item"],
                require_gate7_expense_destination=True,
            )
            assert item is not None
            claim = claim_persistence_dispatch(session, context["item"])
            assert claim is not None
            rejected = dispatch_persistence_write(
                session, context["item"], claim[1], bridge
            )
            assert rejected is not None and rejected.status == "PERSISTENCE_FAILED"
            assert reserve_final_notifications(session, batch_size=100) >= 1
        with writer.connect() as connection:
            ledger = connection.execute(
                text(
                    "SELECT status,error_code FROM write_ledger "
                    "WHERE processing_item_id=:item"
                ),
                {"item": context["item"]},
            ).one()
            assert ledger.status == "REJECTED"

        secondary_event = str(uuid.uuid4())
        secondary_item = str(uuid.uuid4())
        secondary_interaction = str(uuid.uuid4())
        secondary_usage = str(uuid.uuid4())
        secondary_ledger = str(uuid.uuid4())
        with platform.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO events(id,correlation_id,provider,external_instance_id,"
                    "external_message_id,organization_id,instance_id,user_id,message_type,"
                    "status,duplicate_count,received_at) "
                    "VALUES (:event,:correlation,'WUZAPI',:instance,:event,:org,:instance,"
                    ":user,'image','ROUTED',0,:old)"
                ),
                {
                    **context,
                    "event": secondary_event,
                    "old": now - timedelta(hours=1),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO processing_items(id,event_id,correlation_id,organization_id,"
                    "instance_id,user_id,sequence,status,message_received_at,file_mime_type,"
                    "file_size,file_sha256,created_at,updated_at) "
                    "VALUES (:item,:event,:correlation,:org,:instance,:user,2,'RECEIVED',:old,"
                    "'image/jpeg',10,:sha,:old,:old)"
                ),
                {
                    **context,
                    "event": secondary_event,
                    "item": secondary_item,
                    "old": now - timedelta(hours=1),
                    "sha": uuid.uuid4().hex * 2,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO user_interactions(id,processing_item_id,generation,question_type,"
                    "outbound_message_id,status,created_at,updated_at) "
                    "VALUES (:id,:item,1,'transaction_amount',:outbound,'RESERVED',:created,:created)"
                ),
                {
                    "id": secondary_interaction,
                    "item": secondary_item,
                    "outbound": str(uuid.uuid4()),
                    "created": now + timedelta(seconds=1),
                },
            )
        with transcription.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO requests(id,status,correlation_id,received_at,source,created_at) "
                    "VALUES (CAST(:id AS uuid),'SUCCEEDED',:correlation,:created,'WHATSAPP',:created)"
                ),
                {
                    "id": secondary_item,
                    "correlation": context["correlation"],
                    "created": now + timedelta(seconds=2),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO usage_logs(id,request_id,attempt_number,provider,model_name,status,"
                    "input_tokens,output_tokens,total_tokens,usage_status,estimated_cost,currency,created_at) "
                    "VALUES (CAST(:id AS uuid),CAST(:request AS uuid),1,'GEMINI','gemini-test','SUCCESS',"
                    "1,1,2,'known',0.01,'USD',:created)"
                ),
                {
                    "id": secondary_usage,
                    "request": secondary_item,
                    "created": now + timedelta(seconds=3),
                },
            )
        with writer.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO write_ledger(id,idempotency_key,canonical_payload_hash,"
                    "processing_item_id,organization_id,instance_id,user_id,schema_version,status,"
                    "error_code,attempt_count,created_at,updated_at) "
                    "VALUES (:id,:key,:hash,:item,:org,:instance,:user,'2.0','REJECTED',"
                    "'SECONDARY_REJECTION',1,:created,:created)"
                ),
                {
                    **context,
                    "id": secondary_ledger,
                    "key": f"{secondary_item}:secondary",
                    "hash": "a" * 64,
                    "item": secondary_item,
                    "created": now + timedelta(seconds=4),
                },
            )

        payload = _run_report(databases, "correlation", "--correlation-id", context["correlation"])
        assert payload["processing_items"][0]["status"] == "PERSISTENCE_FAILED"
        assert any(
            row["operation"] == "PERSISTENCE_FAILED_FINAL"
            for row in payload["executions"]
        )
        assert any(
            row["operation"] == "FINAL_NOTIFICATION_RESERVED"
            for row in payload["executions"]
        )
        assert payload["writer_ledger"][0]["status"] == "REJECTED"
        rendered = json.dumps(payload)
        assert secret not in rendered
        assert "normalized_data" not in rendered

        first_page = _run_report(
            databases,
            "--limit",
            "1",
            "correlation",
            "--correlation-id",
            context["correlation"],
        )
        assert first_page["processing_items"][0]["id"] == context["item"]
        assert first_page["interactions"][0]["processing_item_id"] == secondary_item
        assert first_page["transcription_requests"][0]["id"] == secondary_item
        assert first_page["usage_attempts"][0]["id"] == secondary_usage
        assert first_page["writer_ledger"][0]["id"] == secondary_ledger
        execution_cursor = first_page["next_cursors"]["executions"]
        assert first_page["truncated"]["executions"] is True
        assert execution_cursor is not None
        second_page = _run_report(
            databases,
            "--limit",
            "1",
            "--cursor",
            execution_cursor,
            "correlation",
            "--collection",
            "executions",
            "--correlation-id",
            context["correlation"],
        )
        assert second_page["executions"][0]["id"] != first_page["executions"][0]["id"]
    finally:
        writer_main.app.dependency_overrides.clear()
        platform.dispose()
        writer.dispose()
        transcription.dispose()
