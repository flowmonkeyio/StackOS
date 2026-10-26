"""Cloned test schemas retain real transactions, constraints, and isolation."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from stackos.db.connection import make_memory_engine
from stackos.db.models import IdempotencyKey, Project
from tests.helpers.sqlite_schema import make_test_memory_engine


@pytest.fixture
def engines():
    opened = []

    def create():
        engine = make_test_memory_engine()
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
