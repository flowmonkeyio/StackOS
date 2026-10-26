"""Independent test databases copied from immutable schema/catalog snapshots.

The production plugin repository owns catalog initialization. Consumer tests
reuse the result of that real initialization; bootstrap/sync tests explicitly
request an empty catalog. Only immutable bytes are shared, never connections,
transactions, project records, credentials, or execution history.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

import stackos.db.models  # noqa: F401 -- register the complete application metadata
from stackos.db.connection import make_memory_engine
from stackos.plugins.manifest import BuiltinPluginManifestSnapshot
from stackos.repositories import plugins as plugin_repository
from stackos.repositories.plugins import PluginRepository

_CATALOG_TABLES = frozenset(
    {"plugins", "providers", "capabilities", "resources", "actions", "action_versions"}
)


@dataclass(frozen=True)
class SQLiteSnapshot:
    data: bytes
    catalog_generation: str | None = None


def capture_database_snapshot(engine: Engine) -> SQLiteSnapshot:
    """Capture committed SQLite contents, including any real catalog sync memo.

    A standalone DELETE-journal image also works when the source uses WAL;
    copying only its main database file could omit committed WAL pages.
    """
    with TemporaryDirectory(prefix="stackos-test-snapshot-") as directory:
        path = Path(directory) / "snapshot.sqlite"
        with closing(sqlite3.connect(path)) as target, engine.connect() as source:
            source.connection.driver_connection.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        return SQLiteSnapshot(
            data=path.read_bytes(),
            catalog_generation=PluginRepository._builtin_sync_generations.get(engine),
        )


def mark_snapshot_catalog(engine: Engine, snapshot: SQLiteSnapshot) -> None:
    """Reuse only current, copied sync evidence without replacing a real sync."""
    if snapshot.catalog_generation is None:
        return
    current = plugin_repository.get_builtin_plugin_manifest_snapshot().generation
    if snapshot.catalog_generation == current:
        PluginRepository._builtin_sync_generations.setdefault(engine, current)


def restore_database_snapshot(engine: Engine, snapshot: SQLiteSnapshot) -> None:
    """Restore independent rows while keeping the destination connection options."""
    # The destination's previous memo cannot describe the replacement database.
    PluginRepository._builtin_sync_generations.pop(engine, None)
    # Direct deserialize resets SQLite's synchronous PRAGMA. Backup preserves
    # the normal factory settings on both in-memory and on-disk destinations.
    with closing(sqlite3.connect(":memory:")) as source, engine.connect() as connection:
        source.deserialize(snapshot.data)
        source.backup(connection.connection.driver_connection)
    mark_snapshot_catalog(engine, snapshot)


@lru_cache(maxsize=1)
def _empty_schema() -> bytes:
    engine = make_memory_engine()
    try:
        SQLModel.metadata.create_all(engine)
        with engine.connect() as connection:
            return connection.connection.driver_connection.serialize()
    finally:
        engine.dispose()


def empty_schema_snapshot() -> SQLiteSnapshot:
    """Return immutable metadata schema bytes without catalog or business rows."""
    return SQLiteSnapshot(_empty_schema())


def _assert_no_business_rows(engine: Engine) -> None:
    with engine.connect() as connection:
        tables = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).scalars()
        for table in tables:
            if table in _CATALOG_TABLES or table == "alembic_version":
                continue
            quoted = engine.dialect.identifier_preparer.quote(table)
            if connection.exec_driver_sql(f"SELECT EXISTS(SELECT 1 FROM {quoted})").scalar_one():
                raise ValueError(f"catalog snapshot cannot contain business rows: {table}")


@lru_cache(maxsize=4)
def _catalog_snapshot(
    schema: bytes,
    generation: str,
    source_loader: Callable[[], BuiltinPluginManifestSnapshot],
) -> SQLiteSnapshot:
    """Initialize each schema/source generation through the real synchronizer."""
    engine = make_memory_engine()
    try:
        restore_database_snapshot(engine, SQLiteSnapshot(schema))
        _assert_no_business_rows(engine)
        if (
            plugin_repository.get_builtin_plugin_manifest_snapshot is not source_loader
            or source_loader().generation != generation
        ):
            raise RuntimeError("plugin sources changed before catalog snapshot initialization")
        with Session(engine) as session:
            PluginRepository(session).sync_builtin_plugins()
        snapshot = capture_database_snapshot(engine)
        if (
            snapshot.catalog_generation != generation
            or source_loader().generation != generation
            or plugin_repository.get_builtin_plugin_manifest_snapshot is not source_loader
        ):
            raise RuntimeError("plugin sources changed during catalog snapshot initialization")
        _assert_no_business_rows(engine)
        return snapshot
    finally:
        engine.dispose()


def builtin_catalog_snapshot(schema_snapshot: SQLiteSnapshot | None = None) -> SQLiteSnapshot:
    """Return a catalog built on the supplied pristine schema or SQLModel metadata.

    A caller may supply a schema produced by real Alembic migrations. Its bytes
    are part of the cache key; metadata and migrated schemas never share images.
    The loader identity also separates tests that replace the manifest source.
    """
    schema = schema_snapshot.data if schema_snapshot is not None else _empty_schema()
    source_loader = plugin_repository.get_builtin_plugin_manifest_snapshot
    generation = source_loader().generation
    return _catalog_snapshot(schema, generation, source_loader)


def make_test_memory_engine(*, catalog: bool = False) -> Engine:
    """Return a fresh empty or initialized-catalog database with normal options."""
    snapshot = builtin_catalog_snapshot() if catalog else empty_schema_snapshot()
    engine = make_memory_engine()
    try:
        restore_database_snapshot(engine, snapshot)
    except BaseException:
        engine.dispose()
        raise
    return engine
