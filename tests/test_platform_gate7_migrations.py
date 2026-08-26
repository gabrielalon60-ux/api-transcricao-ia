from __future__ import annotations

from pathlib import Path
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.real_pg15



ROOT = Path(__file__).resolve().parents[1]
PLATFORM = (
    ROOT
    / "packages/db/alembic/versions/b7c8d9e0f1a2_gate7_income_enterprise_platform.py"
)
WRITER = ROOT / "apps/db_writer/alembic/versions/b7c8d9e0f1a3_gate7_local_df_mvp.py"


def test_platform_migration_contains_gate7_contracts() -> None:
    source = PLATFORM.read_text(encoding="utf-8")
    for token in (
        "IGNORED",
        "INCOME_OUT_OF_SCOPE",
        "enterprise_id",
        "enterprise_selection",
        "whatsapp_chat_enterprise_bindings",
        "enterprise_command_sessions",
        "enterprise_command_answers",
        "uq_enterprise_command_one_open_per_conversation",
    ):
        assert token in source


def test_writer_migration_preserves_v1_adapter_and_adds_local_destination() -> None:
    source = WRITER.read_text(encoding="utf-8")
    assert "financial_records" in source
    assert "suppliers" in source
    assert "enterprises" in source
    assert "df_business_records" not in source
    assert "write_ledger" not in source


def test_migration_revisions_are_linear() -> None:
    assert (
        'down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"'
        in PLATFORM.read_text(encoding="utf-8")
    )
    assert (
        'down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"'
        in WRITER.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("url_env", "ini", "expected"),
    [
        (
            "GATE7_PLATFORM_DISPOSABLE_DATABASE_URL",
            ROOT / "packages/db/alembic.ini",
            "enterprise_command_sessions",
        ),
        (
            "GATE7_WRITER_DISPOSABLE_DATABASE_URL",
            ROOT / "apps/db_writer/alembic.ini",
            "financial_records",
        ),
    ],
)
def test_gate7_migration_upgrade_and_previous_revision_round_trip(
    url_env, ini, expected
) -> None:
    default_db = (
        "platform_gate4_test" if "PLATFORM" in url_env else "db_writer_gate4_test"
    )
    url = os.getenv(
        url_env, f"postgresql://postgres:postgres@localhost:55432/{default_db}"
    )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL 15 unavailable: {exc}")
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    assert expected in inspect(engine).get_table_names()
    command.downgrade(cfg, "-1")
    assert expected not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    assert expected in inspect(engine).get_table_names()
    engine.dispose()


def test_platform_downgrade_refuses_data_bearing_gate7_state() -> None:
    url = os.getenv(
        "GATE7_PLATFORM_DISPOSABLE_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:55432/platform_gate4_test",
    )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Disposable PostgreSQL 15 unavailable: {exc}")
    cfg = Config(str(ROOT / "packages/db/alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    org, bot, instance, user, binding = (str(uuid.uuid4()) for _ in range(5))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations(id,name,slug,status) VALUES (:org,'O',:org,'ACTIVE'); "
                "INSERT INTO bots(id,organization_id,name,service_key,status) VALUES (:bot,:org,'B',:bot,'ACTIVE'); "
                "INSERT INTO instances(id,organization_id,bot_id,provider,external_instance_id,phone_number,status) "
                "VALUES (:instance,:org,:bot,'WUZAPI',:instance,:phone1,'ACTIVE'); "
                "INSERT INTO users(id,organization_id,phone_number,status) VALUES (:user,:org,:phone2,'ACTIVE'); "
                "INSERT INTO whatsapp_chat_enterprise_bindings"
                "(id,organization_id,instance_id,user_id,enterprise_id) "
                "VALUES (:binding,:org,:instance,:user,'enterprise')"
            ),
            {
                "org": org,
                "bot": bot,
                "instance": instance,
                "user": user,
                "binding": binding,
                "phone1": f"55{uuid.uuid4().int}"[:15],
                "phone2": f"54{uuid.uuid4().int}"[:15],
            },
        )
    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(cfg, "-1")
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM whatsapp_chat_enterprise_bindings WHERE id=:binding; "
                "DELETE FROM instances WHERE id=:instance; "
                "DELETE FROM users WHERE id=:user; "
                "DELETE FROM bots WHERE id=:bot; "
                "DELETE FROM organizations WHERE id=:org"
            ),
            {
                "binding": binding,
                "instance": instance,
                "user": user,
                "bot": bot,
                "org": org,
            },
        )
    engine.dispose()
