"""Prepare independent app databases from real migrations and builtin sync.

Transport integration tests exercise an already-installed daemon. They reuse
only immutable schema/catalog bytes; every test still runs create_app and the
complete lifespan against its own database, seed, token, and runtime. Fresh
installation and migration tests deliberately do not use this helper.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from stackos.config import Settings
from stackos.crypto import aes_gcm
from stackos.db import migrate
from stackos.db.connection import make_engine
from tests.helpers.sqlite_schema import (
    SQLiteSnapshot,
    builtin_catalog_snapshot,
    capture_database_snapshot,
    restore_database_snapshot,
)


def _migration_source_files() -> tuple[Path, ...]:
    root = Path(migrate.__file__).resolve().parents[2]
    # Content, rather than mtime/size, keeps same-size edits from reusing an old
    # revision. Include the engine/model owners used by the migration runtime.
    return (root / "alembic.ini", *sorted((root / "stackos" / "db").rglob("*.py")))


def _migration_source_identity() -> str:
    digest = hashlib.sha256()
    for path in _migration_source_files():
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=2)
def _migrated_schema_snapshot(
    source_identity: str,
    upgrade: Callable[[Settings], migrate.MigrationResult],
) -> SQLiteSnapshot:
    # Retaining the actual callable in the key also honors tests that replace
    # the migration entry point. No app, connection, or mutable rows are cached.
    with (
        pytest.MonkeyPatch.context() as crypto_context,
        TemporaryDirectory(prefix="stackos-test-migrations-") as directory,
    ):
        # Real migrations select their installation's seed path. The temporary
        # schema builder must restore the calling test's crypto configuration,
        # including an unconfigured None, on both success and failure.
        crypto_context.setattr(aes_gcm, "_default_seed_path", aes_gcm._default_seed_path)
        root = Path(directory)
        settings = Settings(data_dir=root / "data", state_dir=root / "state")
        try:
            upgrade(settings)
            engine = make_engine(settings.db_path)
            try:
                snapshot = capture_database_snapshot(engine)
            finally:
                engine.dispose()
        finally:
            with aes_gcm._lock:
                aes_gcm._cache.clear()
    if source_identity != _migration_source_identity():
        raise RuntimeError("migration sources changed while preparing the test database")
    return snapshot


def prepare_app_database(settings: Settings) -> SQLiteSnapshot:
    """Copy a current migrated catalog into an absent, per-test database.

    The receipt lets the fixture mark its eventual app engine only for a real
    builtin sync of the current source generation. Startup still executes the
    normal Alembic head check, recovery sweeps, scheduler, and runtime setup.
    """
    if settings.db_path.exists():
        raise FileExistsError(f"refusing to replace an existing test database: {settings.db_path}")
    source_identity = _migration_source_identity()
    schema = _migrated_schema_snapshot(source_identity, migrate.upgrade_to_head)
    snapshot = builtin_catalog_snapshot(schema)
    if source_identity != _migration_source_identity():
        raise RuntimeError("migration sources changed while preparing the test catalog")
    engine = make_engine(settings.db_path)
    try:
        restore_database_snapshot(engine, snapshot)
    finally:
        engine.dispose()
    return snapshot
