"""Prepared transport databases retain real installation and startup contracts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import stackos.server as server_module
from stackos.config import Settings
from stackos.db import migrate
from stackos.db.connection import make_engine
from stackos.db.model_core import ActionCall, Plugin, Project
from stackos.db.model_enums import ActionCallStatus
from stackos.repositories.plugins import PluginRepository
from stackos.server import create_app
from tests.helpers import prepared_app
from tests.helpers.sqlite_schema import mark_snapshot_catalog


def _settings(root: Path, name: str) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=5180,
        data_dir=root / name / "data",
        state_dir=root / name / "state",
    )


def test_prepared_database_matches_real_migrations_and_sync_without_private_state(tmp_path):
    prepared = _settings(tmp_path, "prepared")
    fresh = _settings(tmp_path, "fresh")
    prepared_app.prepare_app_database(prepared)
    migrate.upgrade_to_head(fresh)
    original_engine, copied_engine = make_engine(fresh.db_path), make_engine(prepared.db_path)
    try:
        with Session(original_engine) as session:
            PluginRepository(session).sync_builtin_plugins()
        with original_engine.connect() as original, copied_engine.connect() as copied:
            schema_query = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            assert (
                copied.exec_driver_sql(schema_query).all()
                == original.exec_driver_sql(schema_query).all()
            ), "prepared app schema must come from real Alembic migrations"
            tables = (
                copied.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
                .scalars()
                .all()
            )
            populated = set()
            for table in tables:
                # Catalog timestamps describe the actual sync that built each
                # baseline. Every other persisted field must be equivalent.
                columns = [
                    row[1]
                    for row in original.exec_driver_sql(f'PRAGMA table_info("{table}")')
                    if row[1] not in {"created_at", "updated_at"}
                ]
                selected = ", ".join(f'"{name}"' for name in columns)
                query = f'SELECT {selected} FROM "{table}"'
                expected = sorted(original.exec_driver_sql(query).all(), key=repr)
                actual = sorted(copied.exec_driver_sql(query).all(), key=repr)
                assert actual == expected, f"prepared table differs from genuine sync: {table}"
                if actual:
                    populated.add(table)
            assert populated == {
                "alembic_version",
                "plugins",
                "providers",
                "capabilities",
                "resources",
                "actions",
                "action_versions",
            }, "only migration tracking and builtin catalog rows belong in the baseline"
            assert copied.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert migrate.current_alembic_version(prepared) == migrate.current_alembic_version(fresh)
        assert not prepared.seed_path.exists(), "seed must be created by this test's app"
        assert not prepared.token_path.exists(), "token must be created by this test's app"
    finally:
        original_engine.dispose()
        copied_engine.dispose()


def test_prepared_apps_are_isolated_and_run_real_migration_recovery_and_shutdown(
    tmp_path, monkeypatch
):
    first_settings = _settings(tmp_path, "first")
    first_snapshot = prepared_app.prepare_app_database(first_settings)
    seeded_engine = make_engine(first_settings.db_path)
    try:
        with Session(seeded_engine) as session:
            project = Project(
                slug="isolated", name="Isolated", domain="example.test", locale="en-US"
            )
            session.add(project)
            session.flush()
            call = ActionCall(
                project_id=project.id,
                action_key="builtin.utils.ftp.upload",
                plugin_slug="builtin-utils-ftp",
                operation="file.upload",
                status=ActionCallStatus.RUNNING,
            )
            session.add(call)
            plugin = session.exec(select(Plugin).order_by(Plugin.id)).first()
            assert plugin is not None
            plugin_slug, original_description = plugin.slug, plugin.description
            plugin.description = "this test's local catalog change"
            session.add(plugin)
            session.commit()
            call_id = call.id
        with seeded_engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE test_local_state (id INTEGER PRIMARY KEY)")
    finally:
        seeded_engine.dispose()

    migration_calls = []
    real_upgrade = server_module.upgrade_to_head

    def observed_upgrade(settings):
        migration_calls.append(settings.db_path)
        return real_upgrade(settings)

    monkeypatch.setattr(server_module, "upgrade_to_head", observed_upgrade)
    first_app = create_app(first_settings)
    with TestClient(first_app, base_url="http://127.0.0.1:5180") as client:
        mark_snapshot_catalog(first_app.state.engine, first_snapshot)
        assert client.get("/api/v1/health").status_code == 200
        assert first_app.state.scheduler_running is True
        with Session(first_app.state.engine) as session:
            recovered = session.get(ActionCall, call_id)
            assert recovered is not None
            assert recovered.status == ActionCallStatus.FAILED
            assert recovered.completed_at is not None
            assert recovered.error == "daemon-restart-orphan"
            assert recovered.response_json is not None
            assert recovered.response_json["outcome_unknown"] is True
            assert recovered.response_json["retry_safe"] is False
    assert first_app.state.scheduler_running is False
    assert first_app.state.scheduler.running is False

    second_settings = _settings(tmp_path, "second")
    second_snapshot = prepared_app.prepare_app_database(second_settings)
    second_app = create_app(second_settings)
    with TestClient(second_app, base_url="http://127.0.0.1:5180"):
        mark_snapshot_catalog(second_app.state.engine, second_snapshot)
        assert second_app.state.engine is not first_app.state.engine
        assert second_app.state.scheduler is not first_app.state.scheduler
        assert second_settings.seed_path.read_bytes() != first_settings.seed_path.read_bytes()
        assert second_settings.token_path.read_bytes() != first_settings.token_path.read_bytes()
        with Session(second_app.state.engine) as session:
            assert session.exec(select(Project)).all() == [], "project leaked from another test"
            assert session.exec(select(ActionCall)).all() == [], (
                "action audit leaked from another test"
            )
            plugin = session.exec(select(Plugin).where(Plugin.slug == plugin_slug)).one()
            assert plugin.description == original_description, (
                "mutable catalog leaked between tests"
            )
        with second_app.state.engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name = 'test_local_state'"
                ).first()
                is None
            ), "DDL leaked from another test"
    assert second_app.state.scheduler_running is False
    assert migration_calls == [first_settings.db_path, second_settings.db_path], (
        "each cloned database must still pass through the real startup migration entry point"
    )


def test_migration_source_content_changes_rebuild_cached_schema(tmp_path, monkeypatch):
    source = tmp_path / "revision.py"
    source.write_text("first", encoding="utf-8")
    original_stat = source.stat()
    monkeypatch.setattr(prepared_app, "_migration_source_files", lambda: (source,))
    real_upgrade = migrate.upgrade_to_head
    built_versions = []

    def versioned_upgrade(settings):
        result = real_upgrade(settings)
        version = source.read_text(encoding="utf-8")
        built_versions.append(version)
        engine = make_engine(settings.db_path)
        try:
            with engine.begin() as connection:
                # An empty revision-specific table changes the schema without
                # adding business data to the pristine catalog baseline.
                connection.exec_driver_sql(f"CREATE TABLE test_revision_{version} (value TEXT)")
        finally:
            engine.dispose()
        return result

    monkeypatch.setattr(migrate, "upgrade_to_head", versioned_upgrade)
    for name, expected in (("first", "first"), ("cached", "first"), ("changed", "later")):
        if name == "changed":
            # Same path, length and timestamps: content must still invalidate.
            source.write_text(expected, encoding="utf-8")
            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        settings = _settings(tmp_path, name)
        prepared_app.prepare_app_database(settings)
        engine = make_engine(settings.db_path)
        try:
            with engine.connect() as connection:
                observed = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE name LIKE 'test_revision_%'"
                ).scalar_one()
                assert observed == f"test_revision_{expected}", (
                    "stale migration snapshot reused after a source edit"
                )
        finally:
            engine.dispose()
    assert built_versions == ["first", "later"]


def test_changing_migration_during_preparation_cannot_publish_a_stale_database(
    tmp_path, monkeypatch
):
    source = tmp_path / "revision.py"
    source.write_text("before", encoding="utf-8")
    monkeypatch.setattr(prepared_app, "_migration_source_files", lambda: (source,))
    real_upgrade = migrate.upgrade_to_head

    def changing_upgrade(settings):
        result = real_upgrade(settings)
        source.write_text("after", encoding="utf-8")
        return result

    monkeypatch.setattr(migrate, "upgrade_to_head", changing_upgrade)
    settings = _settings(tmp_path, "rejected")
    with pytest.raises(RuntimeError, match="migration sources changed"):
        prepared_app.prepare_app_database(settings)
    assert not settings.db_path.exists(), "a mixed-source snapshot must never reach the test app"


def test_preparation_refuses_to_overwrite_an_existing_database(tmp_path):
    settings = _settings(tmp_path, "existing")
    settings.data_dir.mkdir(parents=True)
    settings.db_path.write_bytes(b"existing test state")
    with pytest.raises(FileExistsError, match="refusing to replace"):
        prepared_app.prepare_app_database(settings)
    assert settings.db_path.read_bytes() == b"existing test state"
