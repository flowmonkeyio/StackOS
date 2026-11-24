"""Accepted catalog and setup histories converge without losing either owner's data."""

import sqlite3

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from stackos.config import Settings
from stackos.db.migrate import alembic_config, upgrade_to_head


@pytest.mark.parametrize(
    "prior_head", ["base", "0046_pattern_key_index", "0044_project_setup_receipts"]
)
def test_patterns_and_setup_histories_converge_preserving_data(tmp_path, prior_head):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    config = alembic_config(settings)
    assert ScriptDirectory.from_config(config).get_heads() == ["0053_revision_delivery_index"]
    command.upgrade(config, prior_head)
    snapshots = {}
    with sqlite3.connect(settings.db_path) as db:
        if prior_head != "base":
            db.execute(
                "INSERT INTO projects (id,slug,name,domain,locale,is_active,created_at,updated_at) "
                "VALUES (1,'saved','Saved project','fixture.test','en-US',1,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        if prior_head == "0046_pattern_key_index":
            db.execute(
                "INSERT INTO patterns (id,key,title,summary,body,tags_json,revision,"
                "created_at,updated_at,bundled_id,bundled_version,bundled_base_hash,content_hash) "
                "VALUES (7,'saved','Saved','Summary','Operator body','[]',4,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'bundle.saved','v1','base','custom')"
            )
            db.execute(
                "INSERT INTO patterns (id,key,title,summary,body,tags_json,revision,"
                "created_at,updated_at) VALUES (90,'deleted','Deleted','','','[]',1,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
            db.execute("DELETE FROM patterns WHERE id=90")
            db.execute("INSERT INTO pattern_deletions VALUES ('bundle.deleted')")
            for table in ("patterns", "pattern_deletions"):
                snapshots[table] = db.execute(f"SELECT * FROM {table}").fetchall()
        if prior_head == "0044_project_setup_receipts":
            db.execute(
                "UPDATE projects SET setup_reminder_dismissed_at='2026-11-12 01:00:00',"
                "update_reminder_dismissed_at='2026-11-12 02:00:00' WHERE id=1"
            )
            db.execute(
                "INSERT INTO project_setup_receipts (id,project_id,workflow_key,revision,"
                "successful_json,latest_result_json,updated_at) VALUES "
                "(3,1,'engineering.tracked-delivery',4,'{\"saved\":true}',"
                '\'{"status":"partial"}\',CURRENT_TIMESTAMP)'
            )
            snapshots["project_setup_receipts"] = db.execute(
                "SELECT * FROM project_setup_receipts"
            ).fetchall()

    upgrade_to_head(settings)
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0053_revision_delivery_index",)
        ]
        assert {
            table: db.execute(f"SELECT * FROM {table}").fetchall() for table in snapshots
        } == snapshots
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert {"patterns", "pattern_deletions", "project_setup_receipts"} <= {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if prior_head == "0046_pattern_key_index":
            assert db.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='patterns'"
            ).fetchone() == (90,)
            with pytest.raises(sqlite3.IntegrityError, match=r"patterns\.key"):
                db.execute(
                    "INSERT INTO patterns (key,title,summary,body,tags_json,revision,"
                    "created_at,updated_at) VALUES ('saved','Duplicate','','','[]',1,"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
        if prior_head == "0044_project_setup_receipts":
            assert db.execute(
                "SELECT setup_reminder_dismissed_at,update_reminder_dismissed_at "
                "FROM projects WHERE id=1"
            ).fetchone() == ("2026-11-12 01:00:00", "2026-11-12 02:00:00")
