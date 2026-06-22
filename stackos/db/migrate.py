"""Alembic migration helpers shared by CLI and daemon startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from stackos.config import Settings
from stackos.db.connection import make_engine


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of an upgrade-to-head attempt."""

    stamped_existing_schema: bool = False


def _alembic_ini_path() -> Path:
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_config(settings: Settings) -> Config:
    """Build an Alembic config pinned to the configured SQLite database."""
    repo_root = Path(__file__).resolve().parents[2]
    cfg_path = _alembic_ini_path()
    migrations_path = Path(__file__).resolve().parent / "migrations"
    cfg = Config(str(cfg_path)) if cfg_path.exists() else Config()
    cfg.set_main_option("script_location", str(migrations_path))
    cfg.set_main_option("prepend_sys_path", str(repo_root))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{settings.db_path}")
    return cfg


def _stamp_create_all_schema_if_needed(settings: Settings, cfg: Config) -> bool:
    """Stamp a daemon-created schema that predates Alembic version tracking.

    Early/dev installs can have a fully materialised schema from
    ``SQLModel.metadata.create_all`` but no ``alembic_version`` table. Running
    ``upgrade head`` from base would then fail on the first ``CREATE TABLE``.
    When the current head-shaped tables are already present, stamp the DB at
    head.
    """
    if not settings.db_path.exists():
        return False

    required_tables = {
        "projects",
        "runs",
        "plugins",
        "resources",
        "auth_providers",
        "workflow_templates",
        "run_plans",
        "action_calls",
        "workspace_bindings",
    }
    engine = make_engine(settings.db_path)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            if inspector.has_table("alembic_version"):
                row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
                current = row[0] if row else None
                if current not in {None, "0001_initial_empty"}:
                    return False
            if not required_tables <= set(inspector.get_table_names()):
                return False
    finally:
        engine.dispose()

    command.stamp(cfg, "head")
    return True


def upgrade_to_head(settings: Settings) -> MigrationResult:
    """Upgrade the configured database to Alembic head.

    Handles pre-Alembic create_all-shaped local databases before invoking
    Alembic's normal upgrade machinery, so fresh and existing local installs
    land on a version-tracked schema.
    """
    settings.ensure_dirs()
    cfg = alembic_config(settings)
    stamped = _stamp_create_all_schema_if_needed(settings, cfg)
    command.upgrade(cfg, "head")
    return MigrationResult(stamped_existing_schema=stamped)


def current_alembic_version(settings: Settings) -> str | None:
    """Return the current version row, or None when version tracking is absent."""
    if not settings.db_path.exists():
        return None
    engine = make_engine(settings.db_path)
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("alembic_version"):
                return None
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            return row[0] if row else None
    finally:
        engine.dispose()


__all__ = [
    "MigrationResult",
    "alembic_config",
    "current_alembic_version",
    "upgrade_to_head",
]
