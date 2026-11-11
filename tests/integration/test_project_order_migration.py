"""Upgrade a populated old catalog in an explicitly disposable database."""

import sqlite3

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlmodel import Session

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.migrate import alembic_config
from stackos.repositories.projects import ProjectRepository


def test_project_order_migration_preserves_visible_order_and_nonorder_data(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    config = alembic_config(settings)
    command.upgrade(config, "0037_schedule_id_nonreuse")
    with sqlite3.connect(settings.db_path) as connection:
        for project_id in (9, 3, 17):
            connection.execute(
                "INSERT INTO projects (id,slug,name,domain,locale,is_active,created_at,updated_at) "
                "VALUES (?,?,'Same','fixture.test','en-US',1,'2026-01-01','2026-01-01')",
                (project_id, f"fixture-{project_id}"),
            )
        before = connection.execute("SELECT * FROM projects ORDER BY id").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT * FROM projects ORDER BY order_index,id").fetchall() == [
            (*row, row[0]) for row in before
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        repo = ProjectRepository(session)
        assert [p.id for p in repo.list().items] == [3, 9, 17]
        appended = repo.create(slug="appended", name="Append", domain="test").data
        assert appended.order_index == 18
        assert [p.id for p in repo.list().items] == [3, 9, 17, appended.id]
        repo.move(appended.id, placement="top")
        expected = [p.model_dump(mode="json") for p in repo.list().items]
    engine.dispose()
    # A committed upgrade with a lost version stamp must not reset saved moves.
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("UPDATE alembic_version SET version_num='0037_schedule_id_nonreuse'")
    command.upgrade(config, "head")
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        assert [
            p.model_dump(mode="json") for p in ProjectRepository(session).list().items
        ] == expected
    engine.dispose()


def test_project_order_migration_failure_rolls_back_before_retry(tmp_path):
    import importlib

    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    command.upgrade(alembic_config(settings), "0037_schedule_id_nonreuse")
    migration = importlib.import_module("stackos.db.migrations.versions.0038_project_order")
    engine = make_engine(settings.db_path)
    with engine.connect() as connection:

        def fail_after_seed(_connection, _cursor, statement, _parameters, _context, _many):
            if statement == "UPDATE projects SET order_index = id":
                raise RuntimeError("injected order seed failure")

        sa.event.listen(connection, "after_cursor_execute", fail_after_seed)
        with (
            Operations.context(MigrationContext.configure(connection)),
            pytest.raises(RuntimeError),
        ):
            migration.upgrade()
        sa.event.remove(connection, "after_cursor_execute", fail_after_seed)
        assert "order_index" not in {
            col["name"] for col in sa.inspect(connection).get_columns("projects")
        }
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert "order_index" in {
            col["name"] for col in sa.inspect(connection).get_columns("projects")
        }
    engine.dispose()
