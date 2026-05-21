"""Integration tests for the clean StackOS schema."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "action_calls",
        "action_versions",
        "actions",
        "agent_sessions",
        "approval_requests",
        "artifacts",
        "auth_providers",
        "capabilities",
        "context_index_entries",
        "context_snapshots",
        "credential_accounts",
        "credential_refresh_events",
        "credential_scopes",
        "credential_usage_events",
        "credentials",
        "decisions",
        "experiment_observations",
        "experiment_variants",
        "experiments",
        "idempotency_keys",
        "integration_budgets",
        "integration_credentials",
        "learnings",
        "metric_snapshots",
        "oauth_states",
        "plugins",
        "project_events",
        "project_plugins",
        "project_workflow_templates",
        "projects",
        "providers",
        "resource_records",
        "resources",
        "run_plan_steps",
        "run_plans",
        "run_step_calls",
        "run_steps",
        "runs",
        "scheduled_jobs",
        "workflow_template_versions",
        "workflow_templates",
        "workspace_bindings",
    }
)

LEGACY_TABLES: frozenset[str] = frozenset(
    {
        "article_assets",
        "article_publishes",
        "article_versions",
        "articles",
        "authors",
        "clusters",
        "compliance_rules",
        "drift_baselines",
        "eeat_criteria",
        "eeat_evaluations",
        "gsc_metrics",
        "gsc_metrics_daily",
        "internal_links",
        "publish_targets",
        "redirects",
        "research_sources",
        "schema_emits",
        "topics",
        "voice_profiles",
    }
)


@pytest.fixture
def isolated_alembic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("CONTENT_STACK_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CONTENT_STACK_STATE_DIR", str(state_dir))
    yield data_dir / "content-stack.db"


def _run_alembic(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=cwd or repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _list_tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'alembic_%'"
        )
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def test_alembic_upgrade_creates_expected_stackos_tables(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    tables = _list_tables(isolated_alembic)

    assert tables == EXPECTED_TABLES, (
        f"Missing: {EXPECTED_TABLES - tables}; Extra: {tables - EXPECTED_TABLES}"
    )
    assert not (tables & LEGACY_TABLES)


def test_alembic_downgrade_then_upgrade_idempotent(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    _run_alembic(["downgrade", "base"])
    assert _list_tables(isolated_alembic) == set()

    _run_alembic(["upgrade", "head"])
    assert _list_tables(isolated_alembic) == EXPECTED_TABLES
