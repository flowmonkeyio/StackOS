"""Current ticket-status totals and project directory use identical tracker scope."""

from datetime import datetime

from sqlalchemy import event
from sqlmodel import Session, select

from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    Project,
    RunPlanStepStatus,
    TaskTracker,
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketKind,
)
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository


def _ticket(
    session: Session,
    project_id: int,
    key: str,
    *,
    status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED,
    kind: TrackerTicketKind = TrackerTicketKind.TICKET,
    source: TrackerSourceKind = TrackerSourceKind.MANUAL,
    tracker_key: str = "default",
    at: datetime | None = None,
) -> TrackerTicket:
    tracker = TrackerRepository(session).ensure_tracker(project_id=project_id, key=tracker_key)
    task = session.exec(select(TrackerTask).where(TrackerTask.tracker_id == tracker.id)).first()
    if task is None:
        task = TrackerTask(
            tracker_id=tracker.id,
            project_id=project_id,
            key="overview",
            title="Overview",
            status=TrackerItemStatus.NOT_STARTED,
            source_kind=TrackerSourceKind.MANUAL,
            updated_at=at or datetime(2026, 1, 1),
        )
        session.add(task)
        session.flush()
    row = TrackerTicket(
        tracker_id=tracker.id,
        project_id=project_id,
        task_id=task.id,
        key=key,
        title=key,
        status=status,
        kind=kind,
        source_kind=source,
        updated_at=at or datetime(2026, 1, 1),
        context_json={"private_note": "must-not-appear-in-counts"},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_ticket_counts_are_exact_default_tracker_snapshots_without_hydration(
    session: Session, project_id: int, monkeypatch
) -> None:
    repo = TrackerRepository(session)
    empty = repo.ticket_counts(project_id=project_id)
    assert empty.total_count == 0
    assert set(empty.ticket_counts) == {status.value for status in TrackerItemStatus}
    assert not session.exec(select(TaskTracker)).all()
    for status in TrackerItemStatus:
        _ticket(session, project_id, status.value, status=status)
    _ticket(
        session,
        project_id,
        "workflow-group",
        kind=TrackerTicketKind.GROUP,
        source=TrackerSourceKind.WORKFLOW,
        status=TrackerItemStatus.IN_PROGRESS,
    )
    _ticket(session, project_id, "other-tracker", tracker_key="other")
    expected = repo.status(project_id=project_id).ticket_counts
    monkeypatch.setattr(repo, "_ticket_rows", lambda *args: (_ for _ in ()).throw(AssertionError()))
    result = repo.ticket_counts(project_id=project_id)
    assert result.ticket_counts == expected
    assert result.total_count == 8
    assert result.ticket_counts["in-progress"] == 2
    assert "blocked" not in result.ticket_counts
    assert "must-not-appear" not in result.model_dump_json()


def test_global_counts_match_filtered_paginated_portfolio_and_actual_ticket_activity(
    session: Session, project_id: int
) -> None:
    projects = ProjectRepository(session)
    second = projects.create(slug="second", name="Second", domain="second.test").data
    empty = projects.create(slug="empty", name="Empty", domain="empty.test").data
    archived = projects.create(slug="archived", name="Archived", domain="archived.test").data
    projects.update(archived.id, is_active=False)
    _ticket(session, project_id, "first", status=TrackerItemStatus.FAILED, at=datetime(2026, 2, 1))
    _ticket(session, second.id, "second", status=TrackerItemStatus.FAILED, at=datetime(2026, 3, 1))
    _ticket(session, second.id, "complete", status=TrackerItemStatus.COMPLETE)
    _ticket(session, archived.id, "archive", status=TrackerItemStatus.COMPLETE)
    # Neither project metadata nor external action calls count as tracker activity.
    row = session.get(Project, empty.id)
    row.updated_at = datetime(2030, 1, 1)
    session.add(row)
    session.add(
        ActionCall(
            project_id=empty.id,
            plugin_slug="utils",
            action_key="fixture",
            operation="fixture",
            status=ActionCallStatus.SUCCESS,
            created_at=datetime(2031, 1, 1),
        )
    )
    session.commit()
    repo = TrackerRepository(session)
    assert repo.ticket_counts_all().total_count == 3
    assert repo.ticket_counts_all(is_active=False).total_count == 1
    assert repo.ticket_counts_all(is_active=None).total_count == 4
    assert repo.ticket_counts(project_id=archived.id).total_count == 1
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", capture)
    try:
        first = projects.portfolio(ticket_status=TrackerItemStatus.FAILED, limit=1)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture)
    assert len(statements) <= 3
    assert first.total_estimate == 2
    assert first.items[0].id == second.id
    assert first.items[0].ticket_count == 2
    assert first.items[0].ticket_counts["failed"] == 1
    assert first.items[0].last_activity_at == datetime(2026, 3, 1)
    remaining = projects.portfolio(
        ticket_status=TrackerItemStatus.FAILED, after_id=first.next_cursor, limit=1
    )
    assert remaining.items[0].id == project_id
    assert remaining.next_cursor is None
    assert (
        projects.portfolio(query="Second", ticket_status=TrackerItemStatus.FAILED).total_estimate
        == 1
    )
    assert projects.portfolio(is_active=False).items[0].ticket_count == 1
    empty_row = projects.portfolio(project_id=empty.id).items[0]
    assert empty_row.last_activity_at is None
    assert empty_row.ticket_count == 0
    assert "latest_action" not in empty_row.model_dump()
    all_rows = projects.portfolio(is_active=None).items
    assert (
        sum(row.ticket_count for row in all_rows)
        == repo.ticket_counts_all(is_active=None).total_count
    )


def test_counts_follow_workflow_ticket_mirrors_without_counting_runs_or_tasks(
    session: Session, project_id: int
) -> None:
    runs = RunPlanRepository(session)
    plan = runs.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "ticket-counts.workflow",
            "title": "Ticket count mirror",
            "steps": [{"id": "prepare", "title": "Prepare", "success_criteria": ["Prepared"]}],
        },
    ).data
    tracker = TrackerRepository(session)
    before = tracker.ticket_counts(project_id=project_id)
    assert before.total_count == before.ticket_counts["not-started"] == 1
    started = runs.start(plan.id, project_id=project_id).data
    runs.claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="prepare", claimed_by="fixture"
    )
    running = tracker.ticket_counts(project_id=project_id)
    assert running.total_count == running.ticket_counts["in-progress"] == 1
    runs.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"summary": "Needs operator input"},
    )
    blocked = tracker.ticket_counts(project_id=project_id)
    assert blocked.total_count == blocked.ticket_counts["in-progress"] == 1
    assert "blocked" not in blocked.ticket_counts
    assert blocked.ticket_counts == tracker.status(project_id=project_id).ticket_counts
    directory = ProjectRepository(session).portfolio(ticket_status=TrackerItemStatus.IN_PROGRESS)
    assert directory.items[0].ticket_counts == blocked.ticket_counts
    assert directory.items[0].latest_task.id == tracker.get(project_id=project_id).tasks[0].id
