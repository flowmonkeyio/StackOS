"""Home reads reuse task-index facts without exposing private task packets."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlmodel import select

from stackos.db.models import Project, TaskTracker, TrackerItemStatus, TrackerTask
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_agent_statistics import _receipt


def seed_active_tasks(session, project_id):
    repo = TrackerRepository(session)
    other = (
        ProjectRepository(session)
        .create(slug="home-other", name="Other project", domain="other.test")
        .data
    )
    archived = (
        ProjectRepository(session)
        .create(slug="home-archived", name="Archived", domain="archived.test")
        .data
    )
    cleanup = (
        ProjectRepository(session)
        .create(slug="home-cleanup", name="Cleanup", domain="cleanup.test")
        .data
    )
    agent = {"name": "worker", "instructions": "PRIVATE INSTRUCTIONS"}
    for pid in (project_id, other.id, archived.id, cleanup.id):
        for status in TrackerItemStatus:
            repo.create_task(project_id=pid, key=status.value, title=status.value)
        for key in ("queued", "stopped", "manual", "selected-only"):
            repo.create_task(project_id=pid, key=key, title=key)
    for task in session.exec(select(TrackerTask)).all():
        task.status = (
            TrackerItemStatus(task.key)
            if task.key in {status.value for status in TrackerItemStatus}
            else TrackerItemStatus.IN_PROGRESS
        )
        task.started_at = datetime(2026, 11, 1, tzinfo=UTC)
        task.context_json = {"prompt": "PRIVATE CONTEXT"}
        task.metadata_json = {"selected_agent": agent}
        if task.key in {"queued", "stopped"}:
            task.metadata_json = {
                "delivery": {
                    "status": task.key,
                    "job_ref": "inert:never-run",
                    "delivery_agent": agent,
                    "review_agent": agent,
                    "ticket_key": "review",
                    "reason": "Required review blocked",
                }
            }
        session.add(task)
    session.commit()
    repo.create_ticket(project_id=project_id, task_key="stopped", key="review", title="Review")
    timed_receipt = _receipt(total=42)
    timed_receipt["launch"]["started_at"] = "2026-01-01T00:00:00Z"
    timed_receipt["finished_at"] = "2026-01-01T00:20:00Z"
    repo.update_ticket(
        project_id=project_id,
        ticket_key="review",
        patch_json={
            "status": "in-progress",
            "blocker_reason": "Required review blocked",
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 1,
                    "delivery": timed_receipt,
                }
            },
        },
    )
    repo.create_ticket(
        project_id=project_id, task_key="complete", key="historical", title="Historical usage"
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="historical",
        patch_json={
            "status": "complete",
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 1,
                    "delivery": _receipt(total=999),
                }
            },
        },
    )
    # Cleanup and archived projects cannot contribute global Home membership.
    row = session.get(Project, archived.id)
    row.is_active = False
    session.add(row)
    row = session.get(Project, cleanup.id)
    row.cleanup_json = {"status": "incomplete"}
    session.add(row)
    session.commit()
    return repo, other.id, cleanup.id


def test_active_membership_scope_paging_and_canonical_facts(session, project_id):
    repo, other_id, cleanup_id = seed_active_tasks(session, project_id)
    expected_keys = {"in-progress", "queued", "stopped", "manual", "selected-only"}
    scoped = repo.active_tasks(project_id=project_id)
    assert {item.key for item in scoped.items} == expected_keys
    assert {item.project_id for item in scoped.items} == {project_id}
    assert scoped.scope == "project" and scoped.as_of
    index = {
        task.key: task
        for task in repo.get(project_id=project_id, include_graph=False, task_index_only=True).tasks
    }
    for item in scoped.items:
        source = index[item.key]
        for field in (
            "agent_stats",
            "active_execution",
            "work_duration",
            "ticket_summary",
            "started_at",
            "status",
        ):
            assert getattr(item, field) == getattr(source, field)
        assert item.project.id == project_id
        assert item.project.name
    stopped = next(item for item in scoped.items if item.key == "stopped")
    assert stopped.delivery.status == "stopped"
    assert stopped.delivery.reason == "Required review blocked"
    assert stopped.ticket_summary.blocked_count == 1
    assert stopped.agent_stats.reported_usage.total == 42
    assert stopped.work_duration.total_seconds == 1200
    assert stopped.work_duration.coverage == "complete"
    assert stopped.work_duration.live == []
    assert next(item for item in scoped.items if item.key == "selected-only").delivery is None
    manual = next(item for item in scoped.items if item.key == "manual")
    assert manual.agent_stats.coverage.usage_status == "empty"
    assert manual.active_execution is None
    assert manual.work_duration.total_seconds is None
    assert manual.work_duration.coverage == "unavailable"
    assert "PRIVATE" not in scoped.model_dump_json()
    assert not {"context_json", "metadata_json", "completion_evidence_json"} & set(
        stopped.model_dump()
    )
    items = []
    cursor = None
    while True:
        page = repo.active_tasks(limit=3, after_id=cursor)
        items.extend(page.items)
        if page.next_cursor is None:
            break
        assert cursor is None or page.next_cursor > cursor
        cursor = page.next_cursor
    assert {(item.project_id, item.key) for item in items} == {
        (pid, key) for pid in (project_id, other_id) for key in expected_keys
    }
    assert len(items) == 10
    with pytest.raises(NotFoundError):
        repo.active_tasks(project_id=999999)
    with pytest.raises(ConflictError):
        repo.active_tasks(project_id=cleanup_id)


def test_active_projection_batches_metrics_and_rollups(session, project_id):
    repo, _, _ = seed_active_tasks(session, project_id)
    statements = []
    engine = session.get_bind()

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        repo.active_tasks(limit=200)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    # One membership query plus existing bulk rollup/dependency/usage owners;
    # this must not scale into task graphs or a read per row/project.
    assert len(statements) <= 6, statements
    assert sum("tracker_tickets.completion_evidence_json" in sql for sql in statements) == 1
    assert not any("tracker_ticket_reference" in sql for sql in statements)


def test_active_projection_uses_actual_invocation_and_keeps_unknown_usage(session, project_id):
    from stackos.host_mcp import service
    from stackos.host_mcp.result import normalize_usage, usage_snapshot

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="actual", title="Actual")
    repo.create_ticket(project_id=project_id, task_key="actual", key="child", title="Child")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="child",
        patch_json={
            "status": "in-progress",
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 1,
                    "delivery": {"launch": {"agent": "worker"}},
                }
            },
        },
    )
    before = repo.active_tasks(project_id=project_id).items[0]
    assert before.agent_stats.reported_usage.total is None
    assert before.agent_stats.coverage.usage_status == "unavailable"
    call = service.ActiveInvocation(
        "home-fixture-call",
        service.AgentExecutionScope(project_id, "actual", "child", "worker", 1, "delivery"),
        "codex",
        "exact-model",
        datetime.now(UTC).isoformat(),
    )
    call.usage = normalize_usage(
        usage_snapshot("fixture", "invocation", 100, 5, cache_read=80),
        session_id="PRIVATE_NATIVE",
        resumed=False,
    )
    call.measured_at = datetime.now(UTC).isoformat()
    service._active_invocations[call.invocation_id] = call
    try:
        active = repo.active_tasks().items[0]
        assert active.active_execution.invocation_id == call.invocation_id
        assert active.active_execution.model == "exact-model"
        assert active.agent_stats.reported_usage.total == 105
        assert active.agent_stats.reported_usage.cache_read == 80
        assert (
            active.agent_stats
            == repo.get(project_id=project_id, include_graph=False, task_index_only=True)
            .tasks[0]
            .agent_stats
        )
        assert "PRIVATE_NATIVE" not in active.model_dump_json()
    finally:
        service._unregister(call)
    assert repo.active_tasks().items[0].active_execution is None


def test_empty_active_read_does_not_create_tracker(session, project_id):
    repo = TrackerRepository(session)
    assert repo.active_tasks(project_id=project_id).items == []
    assert repo.active_tasks().items == []
    assert session.exec(select(TaskTracker)).all() == []
