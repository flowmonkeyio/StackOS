"""Alembic environment.

Reads the selected database from Settings unless explicitly pinned by the
programmatic caller. Importing `stackos.db.models` registers tables on
`SQLModel.metadata`, which Alembic's autogenerate uses as the target.
"""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import Connection
from sqlmodel import SQLModel

# Importing the models package as a side effect populates
# `SQLModel.metadata` with the 30 table definitions. Without this,
# `alembic check` and autogenerate run against an empty metadata.
import stackos.db.models  # noqa: F401  (import for side effect)
from stackos.config import get_settings
from stackos.db.connection import database_url, make_engine

# this is the Alembic Config object, which provides access to values within
# the .ini file in use.
config = context.config

# Configure stdlib logging from alembic.ini if present.
if config.config_file_name is not None and Path(config.config_file_name).exists():
    fileConfig(config.config_file_name)

# `target_metadata` drives autogenerate. SQLModel.metadata is empty until M1
# imports models; that's intentional — M0 ships an empty initial migration.
target_metadata = SQLModel.metadata


def _resolve_db_url() -> str:
    """Resolve the DB URL from settings unless alembic.ini overrides it."""
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    settings = get_settings()
    settings.ensure_dirs()
    return database_url(settings)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout, no DB connection."""
    url = _resolve_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _prepare_postgres_schema(connection: Connection) -> None:
    """Keep application tables private and support existing long revision IDs."""
    connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS stackos")
    connection.exec_driver_sql("REVOKE ALL ON SCHEMA stackos FROM PUBLIC")
    # Supabase client roles must never use the server's application schema,
    # including its service-role Data API. Generic Postgres need not have them.
    for role in ("anon", "authenticated", "service_role"):
        if connection.exec_driver_sql("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).first():
            connection.exec_driver_sql(f'REVOKE ALL ON SCHEMA stackos FROM "{role}"')
    # Alembic's default VARCHAR(32) cannot hold our established revision names.
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS stackos.alembic_version "
        "(version_num VARCHAR(128) NOT NULL PRIMARY KEY)"
    )


def run_migrations_online() -> None:
    """Run migrations on the same target and search path as application sessions."""
    engine = make_engine(_resolve_db_url())
    try:
        with engine.connect() as connection:
            if engine.dialect.name == "postgresql":
                with connection.begin():
                    _prepare_postgres_schema(connection)
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
