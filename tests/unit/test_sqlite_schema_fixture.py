"""Cloned test schemas retain real transactions, constraints, and isolation."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from stackos.db.connection import make_engine, make_memory_engine
from stackos.db.models import (
    Action,
    ActionVersion,
    Capability,
    IdempotencyKey,
    Plugin,
    Project,
    Provider,
    Resource,
)
from stackos.plugins.manifest import BuiltinPluginManifestSnapshot, PluginManifest
from stackos.repositories import plugins as plugin_repository
from stackos.repositories.plugins import PluginRepository
from tests.helpers import sqlite_schema
from tests.helpers.sqlite_schema import (
    builtin_catalog_snapshot,
    capture_database_snapshot,
    make_test_memory_engine,
    mark_snapshot_catalog,
    restore_database_snapshot,
)


@pytest.fixture
def engines():
    opened = []

    def create(*, catalog=False):
        engine = make_test_memory_engine(catalog=catalog)
        opened.append(engine)
        return engine

    yield create
    for engine in opened:
        engine.dispose()


def _project(slug: str) -> Project:
    return Project(slug=slug, name=slug, domain="example.test", locale="en")


def test_clone_has_the_complete_metadata_schema_and_connection_pragmas(engines):
    fresh = make_memory_engine()
    try:
        SQLModel.metadata.create_all(fresh)
        clone = engines()
        assert type(clone.pool) is type(fresh.pool)
        with fresh.connect() as original, clone.connect() as copied:
            schema_query = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            assert (
                copied.exec_driver_sql(schema_query).all()
                == original.exec_driver_sql(schema_query).all()
            )
            for pragma in (
                "journal_mode",
                "synchronous",
                "busy_timeout",
                "foreign_keys",
                "temp_store",
            ):
                assert copied.exec_driver_sql(f"PRAGMA {pragma}").scalar_one() == (
                    original.exec_driver_sql(f"PRAGMA {pragma}").scalar_one()
                )
            assert copied.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        fresh.dispose()


def test_commits_persist_and_rollbacks_work_without_leaking_to_the_next_database(engines):
    first = engines()
    with Session(first) as session:
        project = _project("committed")
        session.add(project)
        session.commit()
        committed_id = project.id
        session.add(_project("rolled-back"))
        session.flush()
        session.rollback()
    with Session(first) as session:
        assert [row.slug for row in session.exec(select(Project)).all()] == ["committed"]
        assert session.get(Project, committed_id) is not None

    second = engines()
    with second.connect() as connection:
        tables = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).scalars()
        for table in tables:
            assert connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one() == 0


def test_ddl_and_index_changes_do_not_mutate_the_shared_schema(engines):
    first = engines()
    with first.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE fixture_only (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("ALTER TABLE projects ADD COLUMN fixture_column TEXT")
        connection.exec_driver_sql("DROP INDEX ix_project_plugins_project")

    second = engines()
    with second.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE name = 'fixture_only'"
            ).first()
            is None
        )
        assert "fixture_column" not in {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(projects)")
        }
        assert (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE name = 'ix_project_plugins_project'"
            ).scalar_one()
            == "ix_project_plugins_project"
        )


def test_foreign_keys_unique_idempotency_and_cascade_deletes_are_enforced(engines):
    engine = engines()
    with Session(engine) as session:
        session.add(IdempotencyKey(project_id=999, tool_name="test", idempotency_key="key"))
        with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
            session.commit()
        session.rollback()

        project = _project("constraint-owner")
        session.add(project)
        session.commit()
        session.add(IdempotencyKey(project_id=project.id, tool_name="test", idempotency_key="key"))
        session.commit()
        session.add(IdempotencyKey(project_id=project.id, tool_name="test", idempotency_key="key"))
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            session.commit()
        session.rollback()

        session.delete(project)
        session.commit()
        assert session.exec(select(IdempotencyKey)).all() == []

    with engine.connect() as connection:
        matching_indexes = []
        for index in connection.exec_driver_sql("PRAGMA index_list(idempotency_keys)"):
            columns = [
                row[2] for row in connection.exec_driver_sql(f'PRAGMA index_info("{index[1]}")')
            ]
            if columns == ["project_id", "tool_name", "idempotency_key"]:
                matching_indexes.append(index)
        assert len(matching_indexes) == 1
        assert matching_indexes[0][2] == 1  # The model already owns this unique index.


@pytest.fixture
def catalog_source(monkeypatch):
    """Small source variations still run the real catalog persistence code."""
    current = [
        BuiltinPluginManifestSnapshot(
            generation="fixture-catalog-1",
            manifests=(PluginManifest(slug="fixture-catalog", name="First source"),),
        )
    ]
    sqlite_schema._catalog_snapshot.cache_clear()
    monkeypatch.setattr(
        plugin_repository, "get_builtin_plugin_manifest_snapshot", lambda: current[0]
    )
    yield current
    sqlite_schema._catalog_snapshot.cache_clear()


def test_catalog_clone_matches_real_sync_without_preseeding_business_data(engines, monkeypatch):
    original, copied = engines(), engines(catalog=True)
    with Session(original) as session:
        PluginRepository(session).sync_builtin_plugins()
    catalog_models = (Plugin, Provider, Capability, Resource, Action, ActionVersion)
    with original.connect() as actual, copied.connect() as clone:
        for model in catalog_models:
            table = model.__table__
            columns = [
                column
                for column in table.columns
                if column.name not in {"created_at", "updated_at"}
            ]
            query = select(*columns).order_by(table.c.id)
            assert clone.execute(query).all() == actual.execute(query).all()
        for table in SQLModel.metadata.sorted_tables:
            if table.name in {model.__tablename__ for model in catalog_models}:
                continue
            assert clone.execute(select(table).limit(1)).first() is None

    def unexpected_resync(_repository, _manifest):
        pytest.fail("An unchanged catalog clone must retain its real initialization evidence")

    monkeypatch.setattr(PluginRepository, "_sync_manifest", unexpected_resync)
    with Session(copied) as session:
        PluginRepository(session).sync_builtin_plugins()


def test_catalog_clones_keep_mutations_commits_and_ddl_local(engines, catalog_source):
    first = engines(catalog=True)
    with Session(first) as session:
        plugin = session.exec(select(Plugin)).one()
        plugin.name = "Only the first clone"
        session.add(plugin)
        session.add(_project("only-first"))
        session.commit()
    with first.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE only_first (id INTEGER PRIMARY KEY)")

    second = engines(catalog=True)
    with Session(second) as session:
        assert session.exec(select(Plugin)).one().name == "First source"
        assert session.exec(select(Project)).all() == []
    with second.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE name = 'only_first'"
            ).first()
            is None
        )


def test_catalog_source_changes_rebuild_snapshots_and_stale_memos_never_hide_changes(
    engines, catalog_source, monkeypatch
):
    synced = []
    sync = PluginRepository._sync_manifest

    def observe(repository, manifest):
        synced.append(manifest.name)
        return sync(repository, manifest)

    monkeypatch.setattr(PluginRepository, "_sync_manifest", observe)
    old_snapshot = builtin_catalog_snapshot()
    engines(catalog=True)
    engines(catalog=True)
    assert synced == ["First source"]

    catalog_source[0] = BuiltinPluginManifestSnapshot(
        generation="fixture-catalog-2",
        manifests=(PluginManifest(slug="fixture-catalog", name="Changed source"),),
    )
    current = engines(catalog=True)
    with Session(current) as session:
        assert session.exec(select(Plugin)).one().name == "Changed source"
    assert synced == ["First source", "Changed source"]

    stale = engines()
    restore_database_snapshot(stale, old_snapshot)
    assert stale not in PluginRepository._builtin_sync_generations
    with Session(stale) as session:
        PluginRepository(session).sync_builtin_plugins()
        assert session.exec(select(Plugin)).one().name == "Changed source"
    mark_snapshot_catalog(stale, old_snapshot)
    assert PluginRepository._builtin_sync_generations[stale] == "fixture-catalog-2"
    assert synced == ["First source", "Changed source", "Changed source"]


def test_failed_catalog_sync_is_not_cached(engines, catalog_source, monkeypatch):
    attempts = []
    sync = PluginRepository._sync_manifest

    def fail_once(repository, manifest):
        attempts.append(manifest.slug)
        sync(repository, manifest)
        if len(attempts) == 1:
            raise RuntimeError("synthetic interrupted catalog initialization")

    monkeypatch.setattr(PluginRepository, "_sync_manifest", fail_once)
    with pytest.raises(RuntimeError, match="interrupted catalog initialization"):
        builtin_catalog_snapshot()
    with Session(engines(catalog=True)) as session:
        assert session.exec(select(Plugin)).one().name == "First source"
    assert attempts == ["fixture-catalog", "fixture-catalog"]


def test_catalog_builder_refuses_a_schema_snapshot_with_project_rows(engines, catalog_source):
    engine = engines()
    with Session(engine) as session:
        session.add(_project("must-not-be-shared"))
        session.commit()
    snapshot = capture_database_snapshot(engine)
    with pytest.raises(ValueError, match="cannot contain business rows: projects"):
        builtin_catalog_snapshot(snapshot)


def test_catalog_snapshot_retains_supplied_schema_and_round_trips_wal_databases(
    engines, catalog_source, tmp_path
):
    source = make_engine(tmp_path / "source.db")
    target = make_engine(tmp_path / "target.db")
    try:
        SQLModel.metadata.create_all(source)
        with source.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"
            )
            connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('schema-fixture')")
            connection.exec_driver_sql("CREATE INDEX fixture_schema_index ON projects(name)")
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        migrated_schema = capture_database_snapshot(source)
        snapshot = builtin_catalog_snapshot(migrated_schema)
        restore_database_snapshot(target, snapshot)
        with target.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "schema-fixture"
            )
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name = 'fixture_schema_index'"
                ).scalar_one()
                == "fixture_schema_index"
            )
        with Session(target) as session:
            assert session.exec(select(Plugin)).one().name == "First source"

        # A metadata-based catalog must not inherit the distinct migrated schema.
        with engines(catalog=True).connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name = 'alembic_version'"
                ).first()
                is None
            )
    finally:
        source.dispose()
        target.dispose()
