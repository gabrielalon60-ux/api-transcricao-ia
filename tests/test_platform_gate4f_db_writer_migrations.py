from __future__ import annotations

import os
import uuid
import pytest
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, DataError
from alembic import command
from alembic.config import Config

from db_writer.models import WriteLedger

pytestmark = pytest.mark.real_pg15


ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "apps" / "db_writer" / "alembic.ini"
DISPOSABLE_DB_URL = os.getenv(
    "DB_WRITER_DISPOSABLE_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:55432/db_writer_gate4_test",
)


@pytest.fixture(scope="module")
def db_writer_engine():
    engine = create_engine(DISPOSABLE_DB_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(
            f"Disposable PostgreSQL container at {DISPOSABLE_DB_URL} is not accessible: {exc}"
        )

    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)

    # Clean previous schema
    with engine.connect() as conn:
        conn.execute(
            text(
                "DROP TABLE IF EXISTS financial_records, suppliers, enterprises, "
                "write_ledger, df_business_records, db_writer_alembic_version CASCADE;"
            )
        )
        conn.commit()

    # Upgrade to head
    command.upgrade(alembic_cfg, "head")
    yield engine

    # Clean up
    with engine.connect() as conn:
        conn.execute(
            text(
                "DROP TABLE IF EXISTS financial_records, suppliers, enterprises, "
                "write_ledger, df_business_records, db_writer_alembic_version CASCADE;"
            )
        )
        conn.commit()


def test_1_db_writer_migration_upgrade_downgrade_cycle(db_writer_engine):
    """Proves fresh upgrade, downgrade, and re-upgrade cycle for Database Writer migration chain."""
    alembic_cfg = Config(str(ALEMBIC_INI))
    alembic_cfg.set_main_option("sqlalchemy.url", DISPOSABLE_DB_URL)

    # Downgrade to base
    command.downgrade(alembic_cfg, "base")

    inspector = inspect(db_writer_engine)
    tables = inspector.get_table_names()
    assert "write_ledger" not in tables
    assert "df_business_records" not in tables

    # Re-upgrade to head
    command.upgrade(alembic_cfg, "head")

    inspector_after = inspect(db_writer_engine)
    tables_after = inspector_after.get_table_names()
    assert "write_ledger" in tables_after
    assert "df_business_records" in tables_after


def test_2_single_alembic_head_and_version_table(db_writer_engine):
    """Proves Database Writer has exactly one canonical Alembic head and uses db_writer_alembic_version."""
    with db_writer_engine.connect() as conn:
        res = conn.execute(
            text("SELECT version_num FROM db_writer_alembic_version")
        ).fetchall()
        assert len(res) == 1
        assert res[0][0] == "b7c8d9e0f1a3"


def test_3_orm_migration_parity(db_writer_engine):
    """Proves physical table columns match ORM model definitions."""
    inspector = inspect(db_writer_engine)
    ledger_cols = {c["name"]: c for c in inspector.get_columns("write_ledger")}

    assert "idempotency_key" in ledger_cols
    assert ledger_cols["idempotency_key"]["type"].length == 512
    assert not ledger_cols["idempotency_key"]["nullable"]

    assert "canonical_payload_hash" in ledger_cols
    assert ledger_cols["canonical_payload_hash"]["type"].length == 64

    assert "schema_version" in ledger_cols
    assert ledger_cols["schema_version"]["type"].length == 32


def test_4_idempotency_uniqueness_and_length_constraints(db_writer_engine):
    """Proves UNIQUE idempotency_key constraint (23505) and length truncation rejection (22001)."""
    item_id = str(uuid.uuid4())
    idem_key = f"write_{item_id}"

    with Session(db_writer_engine) as s:
        s.add(
            WriteLedger(
                idempotency_key=idem_key,
                canonical_payload_hash="a" * 64,
                processing_item_id=item_id,
                organization_id="org-1",
                instance_id="inst-1",
                user_id="user-1",
                status="COMMITTED",
            )
        )
        s.commit()

    # Duplicate key raises IntegrityError 23505
    with pytest.raises(IntegrityError) as exc_info:
        with Session(db_writer_engine) as s:
            s.add(
                WriteLedger(
                    idempotency_key=idem_key,
                    canonical_payload_hash="b" * 64,
                    processing_item_id=str(uuid.uuid4()),
                    organization_id="org-1",
                    instance_id="inst-1",
                    user_id="user-1",
                    status="COMMITTED",
                )
            )
            s.commit()
    assert (
        "23505" in str(exc_info.value)
        or "unique constraint" in str(exc_info.value).lower()
    )

    # Oversized idempotency_key (>512 chars) raises DataError 22001
    oversized_key = "k" * 513
    with pytest.raises(DataError) as exc_info_len:
        with Session(db_writer_engine) as s:
            s.add(
                WriteLedger(
                    idempotency_key=oversized_key,
                    canonical_payload_hash="c" * 64,
                    processing_item_id=str(uuid.uuid4()),
                    organization_id="org-1",
                    instance_id="inst-1",
                    user_id="user-1",
                    status="COMMITTED",
                )
            )
            s.commit()
    assert (
        "22001" in str(exc_info_len.value)
        or "value too long" in str(exc_info_len.value).lower()
    )
