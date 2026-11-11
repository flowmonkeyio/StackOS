"""The one-time schedule transition retains input context and admitted history."""

import importlib
import json
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
from stackos.db.models import AgentRequest
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.run_plans import RunPlanRepository


def legacy(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    config = alembic_config(settings)
    command.upgrade(config, "0041_ui804_consolidation")
    root = tmp_path / "project"
    root.mkdir()
    with sqlite3.connect(settings.db_path) as db:
        db.execute(
            "INSERT INTO projects (id,slug,name,domain,locale,is_active,created_at,updated_at) "
            "VALUES (1,'migration','Migration','proof.test','en-US',1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO workspace_bindings "
            "(id,project_id,repo_fingerprint,last_known_root,created_at,updated_at) "
            "VALUES (1,1,'path:migration',?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (str(root),),
        )
        db.execute("UPDATE projects SET primary_workspace_binding_id=1")
        for job_id, saved_root, inputs, enabled in (
            (1, root, {"goal": "Keep this goal", "credential_ref": "cred_fixture"}, True),
            (2, tmp_path / "old", {"goal": "Keep mismatched goal", "context": ["original"]}, True),
            (3, root, {"context": {"opaque": "resource:original"}}, True),
            (
                4,
                root,
                {
                    "instructions": "Keep draft instructions",
                    "payload": {"$secret_ref": "secret_fixture_1234567890"},
                },
                False,
            ),
        ):
            execution = {
                "project_dir": str(saved_root),
                "host_key": "codex",
                "agents": {"delivery_agent": {"model": "historical-pin"}},
                "step_agent_assignments": {"work": "delivery_agent"},
            }
            db.execute(
                "INSERT INTO scheduled_jobs (id,project_id,kind,name,cron_expr,timezone,"
                "enabled,inputs_json,execution_json,last_run_status) "
                "VALUES (?,1,?,'Saved','0 9 * * mon','UTC',?,?,?,'success')",
                (job_id, f"saved-{job_id}", enabled, json.dumps(inputs), json.dumps(execution)),
            )
    return settings, config, root


def test_transition_preserves_history_context_and_does_not_retarget(tmp_path):
    settings, config, _root = legacy(tmp_path)
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        plan = (
            RunPlanRepository(session)
            .create(
                project_id=1,
                run_plan_json={
                    "key": "historic",
                    "title": "Historic plan",
                    "steps": [{"id": "work", "title": "Work"}],
                },
            )
            .data
        )
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=1,
                request_key="historic",
                title="Historic occurrence",
                source_kind="schedule",
                metadata_json={"schedule_id": 1, "frozen": {"agents": {"role": "historical"}}},
            )
            .data
        )
        row = session.get(AgentRequest, request.id)
        row.run_plan_id = plan.id
        session.add(row)
        session.commit()
    engine.dispose()
    with sqlite3.connect(settings.db_path) as db:
        snapshots = {
            table: db.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("agent_requests", "run_plans")
        }
        inputs = db.execute("SELECT id,inputs_json FROM scheduled_jobs ORDER BY id").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(settings.db_path) as db:
        assert {
            table: db.execute(f"SELECT * FROM {table}").fetchall() for table in snapshots
        } == snapshots
        assert (
            db.execute("SELECT id,inputs_json FROM scheduled_jobs ORDER BY id").fetchall() == inputs
        )
        rows = db.execute(
            "SELECT id,enabled,goal,execution_json,last_run_status FROM scheduled_jobs ORDER BY id"
        ).fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [
            (1, 1, "Keep this goal"),
            (2, 0, "Keep mismatched goal"),
            (3, 0, None),
            (4, 0, "Keep draft instructions"),
        ]
        assert json.loads(rows[1][3])["project_dir"] == str(tmp_path / "old")
        for row in rows:
            assert set(json.loads(row[3])) == {"host_key", "project_dir"}
            assert row[4] == "success"
        db.execute("UPDATE scheduled_jobs SET goal='Edited after transition' WHERE id=1")
        db.execute("UPDATE alembic_version SET version_num='0041_ui804_consolidation'")
    command.upgrade(config, "head")
    with sqlite3.connect(settings.db_path) as db:
        assert (
            db.execute("SELECT goal FROM scheduled_jobs WHERE id=1").fetchone()[0]
            == "Edited after transition"
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_transition_is_atomic_and_retryable(tmp_path):
    settings, config, _ = legacy(tmp_path)
    migration = importlib.import_module("stackos.db.migrations.versions.0042_schedule_main_thread")
    engine = make_engine(settings.db_path)
    with engine.connect() as connection:

        def fail(_conn, _cursor, statement, *_args):
            if statement.startswith("UPDATE scheduled_jobs"):
                raise RuntimeError("injected transition failure")

        sa.event.listen(connection, "after_cursor_execute", fail)
        context = MigrationContext.configure(connection)
        with (
            pytest.raises(RuntimeError, match="injected transition failure"),
            Operations.context(context),
        ):
            migration.upgrade()
        sa.event.remove(connection, "after_cursor_execute", fail)
        connection.rollback()
        assert "goal" not in {
            c[1] for c in connection.exec_driver_sql("PRAGMA table_info(scheduled_jobs)")
        }
        assert (
            connection.exec_driver_sql("SELECT enabled FROM scheduled_jobs WHERE id=1").scalar_one()
            == 1
        )
    engine.dispose()
    command.upgrade(config, "head")
