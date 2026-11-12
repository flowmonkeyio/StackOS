"""Both delivered histories upgrade to one head without losing saved work."""

import json
import sqlite3

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlmodel import Session

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.migrate import alembic_config, upgrade_to_head
from stackos.repositories.agent_requests import AgentRequestRepository


@pytest.mark.parametrize(
    "prior_head",
    [
        "0041_ui804_consolidation",
        "0042_schedule_main_thread",
        "0042_task_delivery_capacity",
    ],
)
def test_consolidation_preserves_either_delivered_history(tmp_path, prior_head):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    config = alembic_config(settings)
    assert ScriptDirectory.from_config(config).get_heads() == ["0043_automations_homes_merge"]
    command.upgrade(config, "0041_ui804_consolidation")
    root = tmp_path / "project"
    root.mkdir()
    with sqlite3.connect(settings.db_path) as db:
        db.execute(
            "INSERT INTO projects (id,slug,name,domain,locale,is_active,created_at,updated_at) "
            "VALUES (1,'saved','Saved project','fixture.test','en-US',1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO workspace_bindings "
            "(id,project_id,repo_fingerprint,last_known_root,created_at,updated_at) "
            "VALUES (1,1,'path:saved',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (str(root),),
        )
        db.execute("UPDATE projects SET primary_workspace_binding_id=1 WHERE id=1")
        db.execute(
            "INSERT INTO scheduled_jobs (id,project_id,kind,name,cron_expr,timezone,"
            "enabled,inputs_json,execution_json,last_run_status) "
            "VALUES (1,1,'saved','Saved automation','0 9 * * mon','UTC',1,?,?,'success')",
            (
                json.dumps({"goal": "Original goal", "context": {"saved": True}}),
                json.dumps(
                    {
                        "project_dir": str(root),
                        "host_key": "codex",
                        "agents": {"delivery_agent": {"model": "historical-pin"}},
                    }
                ),
            ),
        )
    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            AgentRequestRepository(session).create(
                project_id=1,
                request_key="admitted",
                title="Already admitted occurrence",
                source_kind="schedule",
                metadata_json={
                    "schedule_id": 1,
                    "frozen": {"agents": {"delivery_agent": {"model": "historical-pin"}}},
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, prior_head)
    with sqlite3.connect(settings.db_path) as db:
        if prior_head == "0042_task_delivery_capacity":
            db.execute("INSERT INTO task_delivery_capacity VALUES (1, 7)")
        if prior_head == "0042_schedule_main_thread":
            db.execute("UPDATE scheduled_jobs SET goal='Edited goal' WHERE id=1")
        snapshots = {
            table: db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("projects", "workspace_bindings", "agent_requests")
        }
        saved_inputs = db.execute("SELECT inputs_json FROM scheduled_jobs").fetchall()

    upgrade_to_head(settings)
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0043_automations_homes_merge",)
        ]
        assert {
            table: db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in snapshots
        } == snapshots
        assert db.execute("SELECT inputs_json FROM scheduled_jobs").fetchall() == saved_inputs
        goal, enabled, execution, status = db.execute(
            "SELECT goal,enabled,execution_json,last_run_status FROM scheduled_jobs WHERE id=1"
        ).fetchone()
        expected_goal = (
            "Edited goal" if prior_head == "0042_schedule_main_thread" else "Original goal"
        )
        assert (goal, enabled, status) == (expected_goal, 1, "success")
        assert json.loads(execution) == {"project_dir": str(root), "host_key": "codex"}
        assert db.execute("SELECT * FROM task_delivery_capacity").fetchall() == (
            [(1, 7)] if prior_head == "0042_task_delivery_capacity" else []
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
