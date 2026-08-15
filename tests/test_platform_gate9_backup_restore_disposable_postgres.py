from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "scripts" / "operations" / "platform_backup.py"
RESTORE_SCRIPT = ROOT / "scripts" / "operations" / "platform_restore.py"
ADMIN_ENV = "GATE9_TEST_ADMIN_DATABASE_URL"


def _admin_url() -> str:
    value = os.environ.get(ADMIN_ENV)
    if not value:
        pytest.skip(f"{ADMIN_ENV} is required for disposable PostgreSQL 15 evidence")
    parsed = make_url(value)
    if parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Gate 9 backup/restore evidence requires loopback PostgreSQL")
    if parsed.database != "postgres":
        pytest.fail("Gate 9 admin DSN must name postgres")
    return value


def _create_database(admin_url: str, name: str, marker: str) -> str:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as connection:
            assert not connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                {"name": name},
            )
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            connection.exec_driver_sql(
                f"COMMENT ON DATABASE \"{name}\" IS 'gate9-test:{marker}'"
            )
    finally:
        admin.dispose()
    return make_url(admin_url).set(database=name).render_as_string(hide_password=False)


def _drop_database(admin_url: str, name: str, marker: str) -> None:
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with admin.connect() as connection:
            actual = connection.scalar(
                text(
                    "SELECT shobj_description(oid, 'pg_database') "
                    "FROM pg_database WHERE datname=:name"
                ),
                {"name": name},
            )
            if actual != f"gate9-test:{marker}":
                pytest.fail("Refusing cleanup of an unowned Gate 9 source database")
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
    finally:
        admin.dispose()


@pytest.fixture(scope="module")
def source_database() -> Iterator[dict[str, str]]:
    admin_url = _admin_url()
    marker = uuid.uuid4().hex
    name = f"gate9_platform_{marker[:16]}"
    source_url = _create_database(admin_url, name, marker)
    try:
        config = Config(str(ROOT / "packages" / "db" / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", source_url)
        command.upgrade(config, "head")
        engine = create_engine(source_url, poolclass=NullPool)
        now = datetime.now(UTC)
        values = {
            key: str(uuid.uuid4())
            for key in ("org", "bot", "instance", "user", "event", "item", "correlation")
        }
        try:
            with engine.begin() as connection:
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
                        "INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,"
                        "phone_number,status) VALUES (:instance,:org,:bot,'WUZAPI',:instance,"
                        "'5511999999999','ACTIVE')"
                    ),
                    values,
                )
                connection.execute(
                    text(
                        "INSERT INTO users(id,organization_id,phone_number,status) "
                        "VALUES (:user,:org,'5511888888888','ACTIVE')"
                    ),
                    values,
                )
                connection.execute(
                    text(
                        "INSERT INTO events(id,correlation_id,provider,external_instance_id,external_message_id,"
                        "organization_id,instance_id,user_id,message_type,status,duplicate_count,received_at) "
                        "VALUES (:event,:correlation,'WUZAPI',:instance,:event,:org,:instance,:user,"
                        "'image','ROUTED',0,:now)"
                    ),
                    {**values, "now": now},
                )
                connection.execute(
                    text(
                        "INSERT INTO processing_items(id,event_id,correlation_id,organization_id,instance_id,"
                        "user_id,sequence,status,message_received_at,file_mime_type,file_size,file_sha256,"
                        "completed_at,document_type,direction,amount,transaction_date,date_source) "
                        "VALUES (:item,:event,:correlation,:org,:instance,:user,1,'COMPLETED',:now,"
                        "'image/jpeg',128,:sha,:now,'pix_receipt','expense',10.00,:now,'DOCUMENT')"
                    ),
                    {**values, "now": now, "sha": uuid.uuid4().hex * 2},
                )
                connection.execute(
                    text(
                        "INSERT INTO executions(id,event_id,processing_item_id,correlation_id,component,"
                        "operation,operation_idempotency_key,status,effect_status,attempt,started_at,completed_at) "
                        "VALUES (:id,:event,:item,:correlation,'ORCHESTRATOR','PERSISTENCE_COMMITTED',"
                        ":key,'SUCCESS','ACKNOWLEDGED',1,:now,:now)"
                    ),
                    {
                        **values,
                        "id": str(uuid.uuid4()),
                        "key": f"{values['item']}:gate9-backup",
                        "now": now,
                    },
                )
        finally:
            engine.dispose()
        yield {"admin": admin_url, "source": source_url, "name": name, **values}
    finally:
        _drop_database(admin_url, name, marker)


def _run(script: Path, environment: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_g9_x05_and_x06_actual_backup_restore_and_exact_cleanup(
    source_database: dict[str, str], tmp_path: Path
) -> None:
    output_root = tmp_path / "backup-root"
    output = output_root / "artifacts"
    ownership_root = tmp_path / "restore-root"
    ownership = ownership_root / "ownership"
    output_root.mkdir()
    ownership_root.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "G9_BACKUP_SOURCE_DATABASE_URL": source_database["source"],
            "G9_BACKUP_EXPECTED_DATABASE_NAME": source_database["name"],
            "G9_BACKUP_DISPOSABLE_CONFIRMATION": source_database["name"],
            "G9_BACKUP_OUTPUT_ROOT": str(output_root),
            "G9_BACKUP_OUTPUT_DIRECTORY": str(output),
            "G9_BACKUP_RETENTION_COUNT": "5",
        }
    )
    backup = _run(BACKUP_SCRIPT, environment)
    assert backup.returncode == 0, backup.stderr
    result = json.loads(backup.stdout)
    artifact = output / result["artifact"]
    manifest_path = output / result["manifest"]
    checksum_path = output / result["checksum"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert artifact.stat().st_size > 0
    assert digest == result["sha256"] == manifest["sha256"]
    assert checksum_path.read_text(encoding="ascii") == f"{digest}  {artifact.name}\n"
    assert manifest["table_counts"]["events"] == 1
    assert manifest["table_counts"]["processing_items"] == 1
    assert manifest["table_counts"]["executions"] == 1
    assert manifest["tool_major_version"] == 1
    assert manifest["table_identity_bounds"]["events"] == {
        "minimum_id": source_database["event"],
        "maximum_id": source_database["event"],
    }
    assert manifest["table_identity_bounds"]["processing_items"] == {
        "minimum_id": source_database["item"],
        "maximum_id": source_database["item"],
    }
    assert manifest["alembic_version"]
    catalog = subprocess.run(
        ["pg_restore", "--list", str(artifact)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert catalog.returncode == 0
    assert all(name in catalog.stdout for name in ("events", "processing_items", "executions"))

    rendered_artifacts = backup.stdout + backup.stderr
    for path in (manifest_path, checksum_path, output / ".gate9-platform-backup-owner.json"):
        rendered_artifacts += path.read_text(encoding="utf-8")
    assert source_database["source"] not in rendered_artifacts

    restore_environment = os.environ.copy()
    restore_environment.update(
        {
            "G9_RESTORE_ADMIN_DATABASE_URL": source_database["admin"],
            "G9_RESTORE_ADMIN_DATABASE_NAME": "postgres",
            "G9_RESTORE_TARGET_OWNER": "postgres",
            "G9_RESTORE_DISPOSABLE_CONFIRMATION": "GATE9_DISPOSABLE_RESTORE",
            "G9_RESTORE_OWNERSHIP_ROOT": str(ownership_root),
            "G9_RESTORE_OWNERSHIP_DIRECTORY": str(ownership),
        }
    )
    restore = _run(RESTORE_SCRIPT, restore_environment, "--artifact", str(artifact))
    assert restore.returncode == 0, restore.stderr
    restored = json.loads(restore.stdout)
    assert restored["cleanup_completed"] is True
    assert restored["validation"]["table_counts"] == manifest["table_counts"]
    assert restored["validation"]["table_identity_bounds"] == manifest["table_identity_bounds"]
    assert restored["validation"]["alembic_version"] == manifest["alembic_version"]
    admin = create_engine(source_database["admin"], poolclass=NullPool)
    try:
        with admin.connect() as connection:
            assert not connection.scalar(
                text("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=:name)"),
                {"name": restored["target_database"]},
            )
    finally:
        admin.dispose()
    sidecars = list(ownership.glob("restore-*.json"))
    assert sidecars == []
    assert source_database["admin"] not in restore.stdout + restore.stderr


def test_g9_x06_missing_authorization_fails_before_target_creation(
    source_database: dict[str, str], tmp_path: Path
) -> None:
    ownership_root = tmp_path / "root"
    ownership_root.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "G9_RESTORE_ADMIN_DATABASE_URL": source_database["admin"],
            "G9_RESTORE_ADMIN_DATABASE_NAME": "postgres",
            "G9_RESTORE_TARGET_OWNER": "postgres",
            "G9_RESTORE_OWNERSHIP_ROOT": str(ownership_root),
            "G9_RESTORE_OWNERSHIP_DIRECTORY": str(ownership_root / "ownership"),
        }
    )
    result = _run(RESTORE_SCRIPT, environment, "--artifact", str(tmp_path / "absent.dump"))
    assert result.returncode == 2
    assert not (ownership_root / "ownership").exists()
    admin = create_engine(source_database["admin"], poolclass=NullPool)
    try:
        with admin.connect() as connection:
            assert connection.scalar(
                text("SELECT count(*) FROM pg_database WHERE datname LIKE 'gate9_restore_%'")
            ) == 0
    finally:
        admin.dispose()
