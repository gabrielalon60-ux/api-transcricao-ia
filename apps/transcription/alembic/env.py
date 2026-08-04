from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "apps" / "transcription" / "src"
sys.path.insert(0, str(SRC))

from transcription.core.config import get_settings  # noqa: E402
from transcription.database.models import Application, Extraction, Request, UsageLog  # noqa: E402,F401
from transcription.database.session import Base  # noqa: E402

VERSION_TABLE = "alembic_version_transcription"
OWNED_TABLES = frozenset({"applications", "requests", "extractions", "usage_logs"})

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

unexpected_tables = set(target_metadata.tables) - OWNED_TABLES
if unexpected_tables:
    raise RuntimeError(
        "Transcription Alembic metadata contains non-owned tables: "
        + ", ".join(sorted(unexpected_tables))
    )


def _database_url() -> str:
    configured = os.environ.get("TRANSCRIPTION_DATABASE_URL")
    if configured:
        return configured
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        include_schemas=False,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            include_schemas=False,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
