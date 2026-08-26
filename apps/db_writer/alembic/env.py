from logging.config import fileConfig
import os
from alembic import context
from sqlalchemy import engine_from_config, pool

from db_writer.models import DBWriterBase

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = DBWriterBase.metadata


def run_migrations_offline() -> None:
    url = os.getenv("DB_WRITER_DISPOSABLE_DATABASE_URL", config.get_main_option("sqlalchemy.url"))
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="db_writer_alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = os.getenv("DB_WRITER_DISPOSABLE_DATABASE_URL", config.get_main_option("sqlalchemy.url"))
    configuration = config.get_section(config.config_ini_section, {})
    if configuration is not None and url is not None:
        configuration["sqlalchemy.url"] = url

    connectable = engine_from_config(
        configuration if configuration is not None else {"sqlalchemy.url": url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="db_writer_alembic_version",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
