from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.real_pg15


DATABASE_URL = os.environ.get("GATE10_DISPOSABLE_DATABASE_URL", "postgresql://postgres:postgres@localhost:55432/platform_gate10_test")


@pytest.fixture(scope="module")
def database():
    engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as connection:
            version = int(connection.execute(text("SELECT current_setting('server_version_num')")).scalar_one())
            if not 150000 <= version < 160000:
                pytest.skip("Gate 10 database evidence requires PostgreSQL major 15")
    except Exception as exc:
        pytest.skip(f"disposable PostgreSQL 15 is unavailable: {exc}")
    yield engine
    engine.dispose()


def test_writer_role_has_dml_without_ownership_or_ddl(database) -> None:
    suffix = uuid.uuid4().hex[:12]
    role, schema = f"g10_writer_{suffix}", f"g10_schema_{suffix}"
    try:
        with database.begin() as connection:
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT'))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'CREATE TABLE "{schema}".records (id integer primary key, value text not null)'))
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            connection.execute(text(f'GRANT SELECT, INSERT, UPDATE ON "{schema}".records TO "{role}"'))
        with database.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            connection.execute(text(f'INSERT INTO "{schema}".records VALUES (1, \'ok\')'))
            assert connection.execute(text(f'SELECT value FROM "{schema}".records WHERE id=1')).scalar_one() == "ok"
        with pytest.raises(DBAPIError):
            with database.begin() as connection:
                connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                connection.execute(text(f'ALTER TABLE "{schema}".records ADD COLUMN forbidden integer'))
        with database.connect() as connection:
            owner = connection.execute(text("SELECT pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname='records'"), {"schema": schema}).scalar_one()
            assert owner != role
    finally:
        with database.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
