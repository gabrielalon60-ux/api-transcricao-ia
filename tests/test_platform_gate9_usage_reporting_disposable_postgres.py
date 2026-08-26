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
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.real_pg15


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
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as connection:
            assert not connection.execute(
                text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                {"name": name},
            ).scalar_one()
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            connection.exec_driver_sql(
                f"COMMENT ON DATABASE \"{name}\" IS 'gate9-test:{marker}'"
            )
    finally:
        admin.dispose()
    return make_url(admin_url).set(database=name).render_as_string(hide_password=False)


def _drop_owned_database(admin_url: str, name: str, marker: str) -> None:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as connection:
            evidence = connection.execute(
                text(
                    """
                    SELECT shobj_description(oid, 'pg_database')
                    FROM pg_database WHERE datname=:name
                    """
                ),
                {"name": name},
            ).scalar_one_or_none()
            if evidence != f"gate9-test:{marker}":
                pytest.fail("Refusing cleanup of an unowned Gate 9 test database")
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        admin.dispose()


@pytest.fixture(scope="module")
def databases() -> Iterator[dict[str, str]]:
    admin_url = _admin_url()
    marker = uuid.uuid4().hex
    platform_name = f"gate9_platform_{marker[:16]}"
    transcription_name = f"gate9_transcription_{marker[:16]}"
    created: list[str] = []
    try:
        platform_url = _create_database(admin_url, platform_name, marker)
        created.append(platform_name)
        transcription_url = _create_database(admin_url, transcription_name, marker)
        created.append(transcription_name)

        platform_config = Config(str(ROOT / "packages" / "db" / "alembic.ini"))
        platform_config.set_main_option("sqlalchemy.url", platform_url)
        command.upgrade(platform_config, "head")
        transcription_config = Config(
            str(ROOT / "apps" / "transcription" / "alembic.ini")
        )
        transcription_config.set_main_option("sqlalchemy.url", transcription_url)
        previous_transcription_url = os.environ.get("TRANSCRIPTION_DATABASE_URL")
        os.environ["TRANSCRIPTION_DATABASE_URL"] = transcription_url
        try:
            command.upgrade(transcription_config, "head")
        finally:
            if previous_transcription_url is None:
                os.environ.pop("TRANSCRIPTION_DATABASE_URL", None)
            else:
                os.environ["TRANSCRIPTION_DATABASE_URL"] = previous_transcription_url
        yield {
            "admin": admin_url,
            "marker": marker,
            "platform": platform_url,
            "transcription": transcription_url,
        }
    finally:
        for name in reversed(created):
            _drop_owned_database(admin_url, name, marker)


def _platform_context(connection, organization_id: str, item_id: str, correlation: str) -> None:
    bot_id, instance_id, user_id, event_id = (str(uuid.uuid4()) for _ in range(4))
    connection.execute(
        text(
            "INSERT INTO organizations(id,name,slug,status) VALUES (:id,'Org',:id,'ACTIVE') "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"id": organization_id},
    )
    connection.execute(
        text(
            "INSERT INTO bots(id,organization_id,name,service_key,status) "
            "VALUES (:id,:org,'Bot',:id,'ACTIVE')"
        ),
        {"id": bot_id, "org": organization_id},
    )
    connection.execute(
        text(
            "INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,phone_number,status) "
            "VALUES (:id,:org,:bot,'WUZAPI',:id,:phone,'ACTIVE')"
        ),
        {
            "id": instance_id,
            "org": organization_id,
            "bot": bot_id,
            "phone": f"55{uuid.uuid4().int}"[:15],
        },
    )
    connection.execute(
        text(
            "INSERT INTO users(id,organization_id,phone_number,status) "
            "VALUES (:id,:org,:phone,'ACTIVE')"
        ),
        {
            "id": user_id,
            "org": organization_id,
            "phone": f"54{uuid.uuid4().int}"[:15],
        },
    )
    connection.execute(
        text(
            "INSERT INTO events(id,correlation_id,provider,external_instance_id,external_message_id,"
            "organization_id,instance_id,user_id,message_type,status,duplicate_count,received_at) "
            "VALUES (:id,:corr,'WUZAPI',:instance,:id,:org,:instance,:user,'image','ROUTED',0,:received)"
        ),
        {
            "id": event_id,
            "corr": correlation,
            "instance": instance_id,
            "org": organization_id,
            "user": user_id,
            "received": datetime.now(UTC) - timedelta(minutes=10),
        },
    )
    connection.execute(
        text(
            "INSERT INTO processing_items(id,event_id,correlation_id,organization_id,instance_id,user_id,"
            "sequence,status,message_received_at,file_mime_type,file_size,file_sha256,completed_at) "
            "VALUES (:id,:event,:corr,:org,:instance,:user,1,'COMPLETED',:received,'image/jpeg',10,:sha,:completed)"
        ),
        {
            "id": item_id,
            "event": event_id,
            "corr": correlation,
            "org": organization_id,
            "instance": instance_id,
            "user": user_id,
            "received": datetime.now(UTC) - timedelta(minutes=10),
            "sha": uuid.uuid4().hex * 2,
            "completed": datetime.now(UTC),
        },
    )


def _request(connection, item_id: str, correlation: str) -> None:
    connection.execute(
        text(
            "INSERT INTO requests(id,status,correlation_id,received_at,source,created_at) "
            "VALUES (CAST(:id AS uuid),'SUCCEEDED',:corr,:created,'WHATSAPP',:created)"
        ),
        {"id": item_id, "corr": correlation, "created": datetime.now(UTC)},
    )


def _usage(
    connection,
    item_id: str,
    attempt: int,
    created_at: datetime,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    cost: str | None,
    status: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO usage_logs(id,request_id,attempt_number,provider,model_name,status,"
            "input_tokens,output_tokens,total_tokens,usage_status,estimated_cost,currency,"
            "sanitized_error_code,created_at) "
            "VALUES (CAST(:id AS uuid),CAST(:request AS uuid),:attempt,'GEMINI','gemini-test',:status,"
            ":input,:output,:total,:usage_status,:cost,'USD',:error,:created)"
        ),
        {
            "id": str(uuid.uuid4()),
            "request": item_id,
            "attempt": attempt,
            "status": status,
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
            "usage_status": "known" if total_tokens is not None else "unknown",
            "cost": cost,
            "error": None if status == "SUCCESS" else "PROVIDER_TIMEOUT",
            "created": created_at,
        },
    )


def _run_report(databases: dict[str, str], *arguments: str) -> dict:
    environment = os.environ.copy()
    environment["G9_PLATFORM_DATABASE_URL"] = databases["platform"]
    environment["G9_TRANSCRIPTION_DATABASE_URL"] = databases["transcription"]
    environment.pop("DATABASE_URL", None)
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


def test_g9_x01_tokens_per_document_actual_cli(databases: dict[str, str]) -> None:
    organization_id, item_id, correlation = (str(uuid.uuid4()) for _ in range(3))
    platform = create_engine(databases["platform"], poolclass=NullPool)
    transcription = create_engine(databases["transcription"], poolclass=NullPool)
    now = datetime.now(UTC)
    try:
        with platform.begin() as connection:
            _platform_context(connection, organization_id, item_id, correlation)
        with transcription.begin() as connection:
            _request(connection, item_id, correlation)
            _usage(connection, item_id, 1, now - timedelta(minutes=3), 10, 5, 15, "0.01000000", "SUCCESS")
            _usage(connection, item_id, 2, now - timedelta(minutes=2), 7, 3, 10, "0.02000000", "FAILED")
            _usage(connection, item_id, 3, now - timedelta(minutes=1), None, None, None, None, "FAILED")

        payload = _run_report(
            databases,
            "--limit",
            "1",
            "tokens-document",
            "--processing-item-id",
            item_id,
        )
        assert payload["identity"] == {
            "processing_item_id": item_id,
            "correlation_id": correlation,
            "organization_id": organization_id,
            "event_id": payload["identity"]["event_id"],
            "transcription_request_id": item_id,
        }
        assert payload["totals"] == {
            "attempt_count": 3,
            "known_usage_attempt_count": 2,
            "unknown_usage_attempt_count": 1,
            "input_tokens_known_sum": 17,
            "output_tokens_known_sum": 8,
            "provider_total_tokens_known_sum": 25,
            "known_cost_sum": "0.03000000",
            "partial_usage": True,
        }
        assert [row["attempt_number"] for row in payload["rows"]] == [3]
        assert payload["truncated"] is True
        assert payload["next_cursor"] is not None
        with platform.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM service_usage")) == 0
    finally:
        platform.dispose()
        transcription.dispose()


def test_g9_x02_tokens_per_organization_actual_cli(databases: dict[str, str]) -> None:
    platform = create_engine(databases["platform"], poolclass=NullPool)
    transcription = create_engine(databases["transcription"], poolclass=NullPool)
    selected_org, other_org = str(uuid.uuid4()), str(uuid.uuid4())
    selected_items = [str(uuid.uuid4()), str(uuid.uuid4())]
    other_item = str(uuid.uuid4())
    correlations = [str(uuid.uuid4()) for _ in range(3)]
    now = datetime.now(UTC)
    try:
        with platform.begin() as connection:
            _platform_context(connection, selected_org, selected_items[0], correlations[0])
            _platform_context(connection, selected_org, selected_items[1], correlations[1])
            _platform_context(connection, other_org, other_item, correlations[2])
        with transcription.begin() as connection:
            for item, correlation in zip(
                [*selected_items, other_item], correlations, strict=True
            ):
                _request(connection, item, correlation)
            _usage(connection, selected_items[0], 1, now - timedelta(hours=2), 20, 10, 30, "0.10000000", "SUCCESS")
            _usage(connection, selected_items[0], 2, now - timedelta(hours=1), None, None, None, None, "FAILED")
            _usage(connection, selected_items[1], 1, now - timedelta(minutes=30), 4, 2, 6, "0.02000000", "SUCCESS")
            _usage(connection, selected_items[1], 2, now - timedelta(days=3), 100, 100, 200, "9.00000000", "SUCCESS")
            _usage(connection, other_item, 1, now - timedelta(minutes=20), 999, 999, 1998, "8.00000000", "SUCCESS")

        payload = _run_report(
            databases,
            "--since",
            (now - timedelta(days=1)).isoformat(),
            "--until",
            now.isoformat(),
            "--limit",
            "1",
            "tokens-organization",
            "--organization-id",
            selected_org,
        )
        assert payload["document_count"] == 2
        assert payload["totals"] == {
            "attempt_count": 3,
            "known_usage_attempt_count": 2,
            "unknown_usage_attempt_count": 1,
            "input_tokens_known_sum": 24,
            "output_tokens_known_sum": 12,
            "provider_total_tokens_known_sum": 36,
            "known_cost_sum": "0.12000000",
            "partial_usage": True,
        }
        assert len(payload["rows"]) == 1
        assert payload["rows"][0]["request_id"] in selected_items
        assert payload["truncated"] is True
        assert payload["next_cursor"] is not None
        with platform.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM service_usage")) == 0
    finally:
        platform.dispose()
        transcription.dispose()
