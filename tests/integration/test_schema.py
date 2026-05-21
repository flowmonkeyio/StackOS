"""Integration tests for the schema and seed data."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, select

from content_stack.db.connection import make_engine
from content_stack.db.models import (
    Article,
    ArticleStatus,
    Author,
    EeatCategory,
    EeatCriterion,
    EeatTier,
    InternalLink,
    InternalLinkStatus,
    Project,
    PublishTarget,
    PublishTargetKind,
    SchemaEmit,
)
from content_stack.db.seed import seed_eeat_criteria

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "action_calls",
        "action_versions",
        "actions",
        "agent_sessions",
        "approval_requests",
        "artifacts",
        "article_assets",
        "article_publishes",
        "article_versions",
        "articles",
        "authors",
        "auth_providers",
        "clusters",
        "capabilities",
        "compliance_rules",
        "context_index_entries",
        "context_snapshots",
        "credential_accounts",
        "credential_refresh_events",
        "credential_scopes",
        "credential_usage_events",
        "credentials",
        "decisions",
        "drift_baselines",
        "eeat_criteria",
        "eeat_evaluations",
        "experiment_observations",
        "experiment_variants",
        "experiments",
        "gsc_metrics",
        "gsc_metrics_daily",
        "idempotency_keys",
        "integration_budgets",
        "integration_credentials",
        "internal_links",
        "learnings",
        "metric_snapshots",
        "oauth_states",
        "plugins",
        "procedure_run_steps",
        "project_events",
        "projects",
        "project_plugins",
        "providers",
        "publish_targets",
        "redirects",
        "research_sources",
        "resource_records",
        "resources",
        "run_step_calls",
        "run_steps",
        "run_plan_steps",
        "run_plans",
        "runs",
        "scheduled_jobs",
        "schema_emits",
        "topics",
        "voice_profiles",
        "workflow_template_versions",
        "workflow_templates",
        "project_workflow_templates",
        "workspace_bindings",
    }
)


@pytest.fixture
def isolated_alembic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run alembic CLI against an isolated tmp `data_dir`/`state_dir`.

    Yields the resolved DB path so the test can introspect the file
    SQLite created. The CONTENT_STACK_* env vars are honoured by
    Settings via pydantic-settings, so alembic env.py picks them up.
    """
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


def test_alembic_upgrade_creates_expected_tables(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    tables = _list_tables(isolated_alembic)
    assert tables == EXPECTED_TABLES, (
        f"Missing: {EXPECTED_TABLES - tables}; Extra: {tables - EXPECTED_TABLES}"
    )
    assert len(tables) == len(EXPECTED_TABLES)


def test_alembic_downgrade_then_upgrade_idempotent(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    _run_alembic(["downgrade", "base"])
    # After downgrade to base, no project tables should remain.
    assert _list_tables(isolated_alembic) == set()
    _run_alembic(["upgrade", "head"])
    assert _list_tables(isolated_alembic) == EXPECTED_TABLES


def test_schema_emits_seed_populates_default_templates(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    engine = make_engine(isolated_alembic)
    with Session(engine) as session:
        rows = session.exec(
            select(SchemaEmit).where(SchemaEmit.article_id.is_(None))  # type: ignore[union-attr]
        ).all()
    types = sorted(r.type for r in rows)
    assert types == ["Article", "BlogPosting", "Organization", "Product", "Review"]


def test_eeat_seed_populates_80_items_with_3_cores(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    engine = make_engine(isolated_alembic)
    with Session(engine) as session:
        project = Project(slug="seed-test", name="seed", domain="example.com", locale="en-US")
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None
        inserted = seed_eeat_criteria(session, project.id)
        assert inserted == 80

        rows = session.exec(
            select(EeatCriterion).where(EeatCriterion.project_id == project.id)
        ).all()
        assert len(rows) == 80

        # Idempotence: a second call inserts zero new rows.
        again = seed_eeat_criteria(session, project.id)
        assert again == 0

        cores = [r for r in rows if r.tier == EeatTier.CORE]
        core_codes = sorted(c.code for c in cores)
        assert core_codes == ["C01", "R10", "T04"]
        for c in cores:
            assert c.required is True
            assert c.active is True

        # Sanity: 8 dimensions x 10 items each.
        from collections import Counter

        cats = Counter(r.category for r in rows)
        assert cats == {
            EeatCategory.C: 10,
            EeatCategory.O: 10,
            EeatCategory.R: 10,
            EeatCategory.E: 10,
            EeatCategory.EXP: 10,
            EeatCategory.EPT: 10,
            EeatCategory.A: 10,
            EeatCategory.T: 10,
        }


def test_partial_unique_internal_links(isolated_alembic: Path) -> None:
    """Live duplicates are blocked, including the default ``position=NULL`` bucket."""
    _run_alembic(["upgrade", "head"])
    engine = make_engine(isolated_alembic)
    with Session(engine) as session:
        project = Project(slug="il", name="il", domain="example.com", locale="en-US")
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None

        a1 = Article(
            project_id=project.id,
            slug="a-one",
            title="A1",
            status=ArticleStatus.BRIEFING,
        )
        a2 = Article(
            project_id=project.id,
            slug="a-two",
            title="A2",
            status=ArticleStatus.BRIEFING,
        )
        session.add(a1)
        session.add(a2)
        session.commit()
        session.refresh(a1)
        session.refresh(a2)
        assert a1.id is not None and a2.id is not None

        link1 = InternalLink(
            project_id=project.id,
            from_article_id=a1.id,
            to_article_id=a2.id,
            anchor_text="see the other one",
            position=None,
            status=InternalLinkStatus.SUGGESTED,
        )
        session.add(link1)
        session.commit()

        link_dup_dismissed = InternalLink(
            project_id=project.id,
            from_article_id=a1.id,
            to_article_id=a2.id,
            anchor_text="see the other one",
            position=None,
            status=InternalLinkStatus.DISMISSED,
        )
        session.add(link_dup_dismissed)
        session.commit()  # OK — partial index excludes 'dismissed'.

        link_dup_live = InternalLink(
            project_id=project.id,
            from_article_id=a1.id,
            to_article_id=a2.id,
            anchor_text="see the other one",
            position=None,
            status=InternalLinkStatus.APPLIED,
        )
        session.add(link_dup_live)
        with pytest.raises(Exception) as exc_info:
            session.commit()
        # SQLAlchemy wraps the IntegrityError; just assert message.
        assert "uq_internal_links_unique" in str(exc_info.value) or "UNIQUE" in str(exc_info.value)


def test_publish_targets_only_one_primary_per_project(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    engine = make_engine(isolated_alembic)
    with Session(engine) as session:
        project = Project(slug="pt", name="pt", domain="example.com", locale="en-US")
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None

        primary = PublishTarget(
            project_id=project.id,
            kind=PublishTargetKind.NUXT_CONTENT,
            is_primary=True,
            is_active=True,
        )
        session.add(primary)
        session.commit()

        # Adding a second non-primary target is fine.
        secondary = PublishTarget(
            project_id=project.id,
            kind=PublishTargetKind.WORDPRESS,
            is_primary=False,
            is_active=True,
        )
        session.add(secondary)
        session.commit()

        # A second is_primary=true row violates the partial unique index.
        third = PublishTarget(
            project_id=project.id,
            kind=PublishTargetKind.GHOST,
            is_primary=True,
            is_active=True,
        )
        session.add(third)
        with pytest.raises(Exception) as exc_info:
            session.commit()
        assert "uq_publish_targets_primary" in str(exc_info.value) or "UNIQUE" in str(
            exc_info.value
        )


def test_articles_fks_set_null_on_author_delete(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    engine = make_engine(isolated_alembic)
    with Session(engine) as session:
        project = Project(slug="setnull", name="sn", domain="example.com", locale="en-US")
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None

        author = Author(project_id=project.id, name="Test Author", slug="test-author")
        session.add(author)
        session.commit()
        session.refresh(author)
        assert author.id is not None

        article = Article(
            project_id=project.id,
            slug="article-one",
            title="One",
            status=ArticleStatus.BRIEFING,
            author_id=author.id,
        )
        session.add(article)
        session.commit()
        session.refresh(article)
        article_id = article.id
        assert article_id is not None

        session.delete(author)
        session.commit()

        # Re-fetch fresh — FK ON DELETE SET NULL should have nulled author_id.
        session.expire_all()
        refetched = session.get(Article, article_id)
        assert refetched is not None
        assert refetched.author_id is None
