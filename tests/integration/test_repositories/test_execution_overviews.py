"""Project directory cursors and canonical action audit compatibility."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    Project,
    RunPlan,
    RunPlanStatus,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from tests.integration.test_repositories.test_ticket_overviews import _ticket


def _call(session: Session, project_id: int, at: datetime, **values: object) -> ActionCall:
    row = ActionCall(
        project_id=project_id,
        plugin_slug="utils",
        action_key="sitemap.fetch",
        provider_key="sitemap",
        operation="fetch",
        status=ActionCallStatus.SUCCESS,
        created_at=at.replace(tzinfo=None),
        request_json={"private_customer": "must-not-appear-in-summary"},
    )
    for key, value in values.items():
        setattr(row, key, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_portfolio_metadata_is_not_activity_and_null_cursors_remain_pageable(
    session: Session, project_id: int
) -> None:
    repo = ProjectRepository(session)
    first_empty = repo.create(slug="empty-a", name="Empty A", domain="a.test").data
    second_empty = repo.create(slug="empty-b", name="Empty B", domain="b.test").data
    for project in session.exec(select(Project)).all():
        project.updated_at = datetime(2030, 1, 1)
        session.add(project)
    session.commit()
    at = datetime(2025, 1, 1, tzinfo=UTC)
    _ticket(session, project_id, "activity", at=at.replace(tzinfo=None))
    first = repo.portfolio(limit=1)
    assert first.items[0].id == project_id
    assert first.items[0].last_activity_at == at.replace(tzinfo=None)
    second = repo.portfolio(limit=1, after_id=first.next_cursor)
    assert second.items[0].id == first_empty.id
    assert second.items[0].last_activity_at is None
    third = repo.portfolio(limit=1, after_id=second.next_cursor)
    assert third.items[0].id == second_empty.id
    assert third.items[0].last_activity_at is None
    assert third.next_cursor is None


def test_portfolio_name_cursor_uses_the_database_sort_key(session: Session) -> None:
    repo = ProjectRepository(session)
    first = repo.create(slug="eclair-one", name="Éclair", domain="one.test").data
    second = repo.create(slug="eclair-two", name="Éclair", domain="two.test").data
    zulu = repo.create(slug="zulu", name="Zulu", domain="zulu.test").data
    unpaged = repo.portfolio(sort="name")
    page = repo.portfolio(sort="name", limit=2)
    assert [row.id for row in page.items] == [zulu.id, first.id]
    remaining = repo.portfolio(sort="name", after_id=page.next_cursor, limit=2)
    assert [row.id for row in remaining.items] == [second.id]
    assert page.items + remaining.items == unpaged.items
    assert remaining.next_cursor is None


def test_portfolio_search_uses_database_case_rules_and_literal_substrings(session: Session) -> None:
    repo = ProjectRepository(session)
    exact = repo.create(slug="eclair", name="Éclair_100%", domain="eclair.test").data
    repo.create(slug="plain", name="Another project", domain="plain.test")
    for query in ("Éclair", "CLAIR", "_", "%", "ECLAIR.TEST"):
        page = repo.portfolio(query=query)
        assert [item.id for item in page.items] == [exact.id], query
        assert page.total_estimate == 1


def test_chronological_audit_order_pages_by_timestamp_then_id_without_changing_default(
    session: Session, project_id: int
) -> None:
    repo = ActionRepository(session)
    now = datetime(2026, 9, 7, tzinfo=UTC)
    newest = _call(session, project_id, now)
    oldest = _call(session, project_id, now - timedelta(days=2))
    tied = _call(session, project_id, now)
    middle = _call(session, project_id, now - timedelta(days=1))
    excluded = _call(session, project_id, now + timedelta(days=1), dry_run=True)
    default = repo.query_calls(project_id=project_id, dry_run=False)
    assert [row.id for row in default.items] == [middle.id, tied.id, oldest.id, newest.id]
    page = repo.query_calls(project_id=project_id, dry_run=False, sort="created_at", limit=2)
    assert [row.id for row in page.items] == [tied.id, newest.id]
    assert page.total_estimate == 4
    remaining = repo.query_calls(
        project_id=project_id, dry_run=False, sort="created_at", limit=2, after_id=page.next_cursor
    )
    assert [row.id for row in remaining.items] == [middle.id, oldest.id]
    assert remaining.total_estimate == 4
    assert remaining.next_cursor is None
    for filters in (
        {"after_id": excluded.id},
        {"after_id": newest.id, "provider_key": "stripe"},
        {"after_id": newest.id, "created_before": now},
    ):
        with pytest.raises(ValidationError, match=r"cursor.*refresh"):
            repo.query_calls(project_id=project_id, dry_run=False, sort="created_at", **filters)
    other = (
        ProjectRepository(session).create(slug="foreign-cursor", name="Other", domain="x.test").data
    )
    foreign = _call(session, other.id, now)
    with pytest.raises(ValidationError, match=r"cursor.*refresh"):
        repo.query_calls(project_id=project_id, sort="created_at", after_id=foreign.id)


def test_existing_run_plan_pagination_keeps_descending_ids(
    session: Session, project_id: int
) -> None:
    rows = []
    for index in range(3):
        row = RunPlan(
            project_id=project_id,
            key=f"ordering-{index}",
            title=f"Ordering {index}",
            status=RunPlanStatus.DRAFT,
            created_at=datetime(2026, 9, 7 - index),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        rows.append(row)
    repo = RunPlanRepository(session)
    page = repo.list(project_id=project_id, limit=2)
    assert [row.id for row in page.items] == [rows[2].id, rows[1].id]
    assert page.total_estimate == 3
    remaining = repo.list(project_id=project_id, after_id=page.next_cursor, limit=2)
    assert [row.id for row in remaining.items] == [rows[0].id]
    assert remaining.total_estimate == 3
    assert remaining.next_cursor is None
