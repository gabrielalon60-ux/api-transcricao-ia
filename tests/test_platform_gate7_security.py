from __future__ import annotations

import inspect
import os
import uuid

import pytest

from db_writer.config import DBWriterSettings
from orchestrator.config import Settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError


def test_writer_production_requires_verify_full() -> None:
    settings = DBWriterSettings(
        environment="production",
        database_url="postgresql://writer:password@db.example/df",
        db_writer_internal_token="non-default-production-token",
    )
    with pytest.raises(ValueError, match="verify-full"):
        settings.validate_environment()


def test_writer_timeout_contract() -> None:
    settings = DBWriterSettings()
    assert settings.connect_timeout_seconds == 2
    assert settings.lock_timeout_ms == 1000
    assert settings.statement_timeout_ms == 5000
    assert settings.handling_deadline_seconds == 8


@pytest.mark.parametrize(
    "sslmode", ["disable", "allow", "prefer", "require", "verify-ca"]
)
def test_writer_rejects_non_verify_full_tls_modes(sslmode) -> None:
    settings = DBWriterSettings(
        environment="production",
        database_url=f"postgresql://writer:secret@db.example/df?sslmode={sslmode}",
        db_writer_internal_token="non-default-production-token",
    )
    with pytest.raises(ValueError, match="verify-full"):
        settings.validate_environment()


def test_writer_tls_validation_does_not_accept_substring_trick() -> None:
    settings = DBWriterSettings(
        environment="production",
        database_url=(
            "postgresql://writer:secret@db.example/df?"
            "sslmode=require&application_name=sslmode%3Dverify-full"
        ),
        db_writer_internal_token="non-default-production-token",
    )
    with pytest.raises(ValueError, match="verify-full"):
        settings.validate_environment()


def test_writer_requires_database_url_and_explicit_disposable_insecure_mode() -> None:
    with pytest.raises(ValueError, match="required"):
        DBWriterSettings(database_url="").validate_environment()
    with pytest.raises(ValueError, match="explicit disposable"):
        DBWriterSettings(
            database_url="postgresql://postgres:postgres@localhost/test"
        ).validate_environment()
    DBWriterSettings(
        database_url="postgresql://postgres:postgres@localhost/test",
        allow_insecure_disposable_db=True,
    ).validate_environment()


def test_orchestrator_settings_has_no_df_database_url() -> None:
    assert "df_database_url" not in Settings.model_fields


def test_writer_generic_handler_is_sanitized() -> None:
    from db_writer import main

    source = inspect.getsource(main.generic_exception_handler)
    assert "str(exc)" not in source
    assert "Internal server error occurred" in source


def test_disposable_writer_role_is_least_privilege() -> None:
    url = os.getenv(
        "GATE7_WRITER_DISPOSABLE_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:55432/db_writer_gate4_test",
    )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL 15 unavailable: {exc}")
    role = f"gate7_writer_{uuid.uuid4().hex[:12]}"
    password = uuid.uuid4().hex
    enterprise_id = str(uuid.uuid4())
    supplier_id = str(uuid.uuid4())
    record_id = str(uuid.uuid4())
    ledger_id = str(uuid.uuid4())
    database_name = make_url(url).database
    assert database_name is not None
    with engine.begin() as connection:
        connection.execute(
            text(
                f'CREATE ROLE "{role}" LOGIN PASSWORD :password '
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ),
            {"password": password},
        )
        connection.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
        connection.execute(
            text(f'GRANT CONNECT ON DATABASE "{database_name}" TO "{role}"')
        )
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        connection.execute(text(f'GRANT SELECT ON enterprises, suppliers TO "{role}"'))
        connection.execute(
            text(
                f'GRANT SELECT, INSERT ON financial_records TO "{role}"; '
                f'GRANT SELECT, INSERT, UPDATE ON write_ledger TO "{role}"'
            )
        )
        connection.execute(text("CREATE TABLE gate7_unrelated_secret(secret text)"))
        connection.execute(
            text("INSERT INTO enterprises(id,name) VALUES (:id,'Enterprise')"),
            {"id": enterprise_id},
        )
        connection.execute(
            text(
                "INSERT INTO suppliers(id,cnpj,name) VALUES (:id,'12345678000190','Supplier')"
            ),
            {"id": supplier_id},
        )

    restricted_url = make_url(url).set(username=role, password=password)
    restricted = create_engine(restricted_url)
    with restricted.begin() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM enterprises")).scalar_one()
            >= 1
        )
        assert (
            connection.execute(text("SELECT count(*) FROM suppliers")).scalar_one() >= 1
        )
        returned = connection.execute(
            text(
                "INSERT INTO financial_records"
                "(id,transaction_date,enterprise_id,amount,supplier_id,origin,processing_item_id) "
                "VALUES (:id,now(),:enterprise,1.00,:supplier,'WHATSAPP',:item) RETURNING id"
            ),
            {
                "id": record_id,
                "enterprise": enterprise_id,
                "supplier": supplier_id,
                "item": f"least-{record_id}",
            },
        ).scalar_one()
        assert str(returned) == record_id
        connection.execute(
            text(
                "INSERT INTO write_ledger"
                "(id,idempotency_key,canonical_payload_hash,processing_item_id,organization_id,instance_id,user_id,status) "
                "VALUES (:id,:key,:hash,:item,'o','i','u','COMMITTED')"
            ),
            {
                "id": ledger_id,
                "key": f"least-{ledger_id}",
                "hash": "a" * 64,
                "item": f"least-{record_id}",
            },
        )
        connection.execute(
            text("UPDATE write_ledger SET attempt_count=2 WHERE id=:id"),
            {"id": ledger_id},
        )

    forbidden = [
        "CREATE TABLE forbidden_create(id int)",
        "ALTER TABLE financial_records ADD COLUMN forbidden_column int",
        "DROP TABLE financial_records",
        "TRUNCATE financial_records",
        "DELETE FROM financial_records",
        "UPDATE financial_records SET amount=2",
        "INSERT INTO suppliers(id,cnpj,name) VALUES (gen_random_uuid(),'99999999000199','X')",
        "UPDATE suppliers SET name='X'",
        "DELETE FROM suppliers",
        "INSERT INTO enterprises(id,name) VALUES (gen_random_uuid(),'X')",
        "UPDATE enterprises SET name='X'",
        "DELETE FROM enterprises",
        "SELECT * FROM gate7_unrelated_secret",
        "CREATE ROLE forbidden_role",
        "SET ROLE postgres",
    ]
    for statement in forbidden:
        with pytest.raises(DBAPIError), restricted.begin() as connection:
            connection.execute(text(statement))
    with pytest.raises(DBAPIError):
        with restricted.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.execute(text("CREATE DATABASE forbidden_database"))

    restricted.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP OWNED BY "{role}"'))
        connection.execute(text(f'DROP ROLE "{role}"'))
        connection.execute(text("DROP TABLE gate7_unrelated_secret"))
        connection.execute(text("GRANT CREATE ON SCHEMA public TO PUBLIC"))
    engine.dispose()
