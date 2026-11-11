"""Both accepted database histories converge without losing operator state."""

import json
import sqlite3
from datetime import datetime

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlmodel import Session

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.migrate import alembic_config, upgrade_to_head
from stackos.db.models import (
    TaskTracker,
    TrackerItemStatus,
    TrackerRevision,
    TrackerSourceKind,
    TrackerTask,
)
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker.ordering import NORMALIZATION_KIND


def test_capacity_migration_replay_preserves_configured_preference(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("INSERT INTO task_delivery_capacity VALUES (1, 9)")
        connection.execute("UPDATE alembic_version SET version_num='0041_ui804_consolidation'")
    upgrade_to_head(settings)
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT * FROM task_delivery_capacity").fetchall() == [(1, 9)]


@pytest.mark.parametrize(
    "prior_head", ["0040_artifact_managed_files", "0039_tracker_board_arrivals"]
)
def test_consolidation_preserves_both_prior_heads_and_reruns_once(tmp_path, prior_head):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    config = alembic_config(settings)
    assert ScriptDirectory.from_config(config).get_heads() == ["0042_task_delivery_capacity"]
    command.upgrade(config, "0037_schedule_id_nonreuse")
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "keep").write_text("source bytes")
    with sqlite3.connect(settings.db_path) as connection:
        for project_id in (1, 2):
            connection.execute(
                "INSERT INTO projects (id,slug,name,domain,locale,is_active,created_at,updated_at) "
                "VALUES (?,?,'Fixture','test','en-US',1,'2026-01-01','2026-01-01')",
                (project_id, f"fixture-{project_id}"),
            )
        connection.execute(
            "INSERT INTO workspace_bindings "
            "(id,project_id,repo_fingerprint,last_known_root,created_at,updated_at) "
            "VALUES (1,1,'path:source',?,'2026-01-01','2026-01-01')",
            (str(folder),),
        )
        connection.execute(
            "INSERT INTO artifacts (id,project_id,kind,uri,status,created_at,updated_at) "
            "VALUES (1,1,'file','/generated-assets/fixture.png','draft','2026-01-01','2026-01-01')"
        )
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        session.add(TaskTracker(id=1, project_id=1, key="default", name="Work", rev=2))
        session.commit()
        for task_id, key in ((1, "old"), (2, "new")):
            session.add(
                TrackerTask(
                    id=task_id,
                    project_id=1,
                    tracker_id=1,
                    key=key,
                    title=key,
                    status=TrackerItemStatus.NOT_STARTED,
                    source_kind=TrackerSourceKind.MANUAL,
                    order_index=task_id,
                    created_at=datetime(2026, 1, task_id),
                )
            )
            session.add(
                TrackerRevision(
                    project_id=1,
                    tracker_id=1,
                    rev=task_id,
                    change_kind="create",
                    entity_kind="task",
                    entity_id=task_id,
                    entity_key=key,
                    after_json={"order_index": task_id},
                )
            )
        session.commit()
    engine.dispose()
    command.upgrade(config, prior_head)
    with sqlite3.connect(settings.db_path) as connection:
        if prior_head == "0040_artifact_managed_files":
            connection.execute("UPDATE projects SET order_index = 3-id")
            connection.execute(
                "UPDATE projects SET cleanup_json=? WHERE id=2",
                (json.dumps({"operation_id": "retained"}),),
            )
            connection.execute(
                "UPDATE artifacts SET managed_file_json=?", (json.dumps({"custody": "retained"}),)
            )
        else:
            connection.execute("UPDATE projects SET primary_workspace_binding_id=1 WHERE id=1")
        columns = {
            table: ",".join(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
            for table in ("projects", "workspace_bindings", "artifacts")
        }
        before = {
            table: connection.execute(f"SELECT {names} FROM {table} ORDER BY id").fetchall()
            for table, names in columns.items()
        }
    upgrade_to_head(settings)
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0042_task_delivery_capacity",)
        ]
        assert {
            table: connection.execute(f"SELECT {names} FROM {table} ORDER BY id").fetchall()
            for table, names in columns.items()
        } == before
        assert connection.execute(
            "SELECT key FROM tracker_tasks ORDER BY order_index"
        ).fetchall() == [("new",), ("old",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM tracker_revisions WHERE change_kind=?", (NORMALIZATION_KIND,)
        ).fetchone() == (1,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert any(
            row[2:5] == ("workspace_bindings", "primary_workspace_binding_id", "id")
            and row[6] == "SET NULL"
            for row in connection.execute("PRAGMA foreign_key_list(projects)")
        )
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        projects = ProjectRepository(session)
        expected_order = [2, 1] if prior_head == "0040_artifact_managed_files" else [1, 2]
        assert [project.id for project in projects.list().items] == expected_order
        if prior_head == "0040_artifact_managed_files":
            assert projects.get(1).primary_workspace_binding_id is None
            projects.update(1, primary_workspace_binding_id=1)
        assert projects.get(1).project_dir == str(folder)
    engine.dispose()
    assert (folder / "keep").read_text() == "source bytes"
