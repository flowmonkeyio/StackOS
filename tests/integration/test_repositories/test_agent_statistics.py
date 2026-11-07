"""Recorded invocations, rather than configuration or tracker history, own metrics."""

from copy import deepcopy

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from stackos.db.models import (
    TaskTracker,
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketKind,
)
from stackos.operations.registry import build_operation_registry
from stackos.operations.responses import shape_operation_response
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_ticket_overviews import _ticket


def _receipt(role="delivery_agent", outcome="success", total=30, model="recorded-sol"):
    return {
        "agent": "same-name",
        "selected_role": role,
        "launch": {"agent": "same-name", "host_key": "codex", "model": model},
        "validated_outcome": outcome,
        "usage": {
            "unit": "tokens",
            "status": "measured",
            "snapshot": {"input": 9000, "output": 9000, "total": 18000},
            "measured": {"input": total, "output": 0, "total": total},
        },
        "result": {"status": "repair", "summary": "PRIVATE PROMPT", "outputs": "PRIVATE OUTPUT"},
        "error": "PRIVATE ERROR",
        "instructions": "PRIVATE INSTRUCTIONS",
        "api_key": "PRIVATE CREDENTIAL",
    }


def _repaired():
    first = {
        "attempt": 1,
        "delivery": _receipt(total=10),
        "review": _receipt("review_agent", "repair", 20),
    }
    second = {
        "attempt": 2,
        "delivery": _receipt(total=30),
        "review": _receipt("review_agent", "success", 40),
    }
    return {**deepcopy(second), "attempts": [first, deepcopy(second)]}


def _store(session, project_id, key, automatic):
    _ticket(session, project_id, key)
    TrackerRepository(session).update_ticket(
        project_id=project_id,
        ticket_key=key,
        patch_json={"completion_evidence_json": {"automatic_delivery": automatic}},
    )


def test_task_and_ticket_projections_share_receipt_totals(session: Session, project_id: int):
    repo = TrackerRepository(session)
    _store(session, project_id, "repaired", _repaired())
    _store(
        session,
        project_id,
        "mirror",
        {"attempt": 1, "delivery": _receipt("review_agent", "repair", 7)},
    )
    _ticket(session, project_id, "unused-group", kind=TrackerTicketKind.GROUP)
    mirror = session.exec(select(TrackerTicket).where(TrackerTicket.key == "mirror")).one()
    repaired = session.exec(select(TrackerTicket).where(TrackerTicket.key == "repaired")).one()
    mirror.kind = TrackerTicketKind.GROUP
    mirror.source_kind = TrackerSourceKind.WORKFLOW
    repaired.parent_ticket_id = mirror.id
    session.add_all([mirror, repaired])
    session.commit()
    expected = repo.agent_stats(project_id=project_id).totals
    index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
    full = repo.get(project_id=project_id)
    focused = repo.get(project_id=project_id, task_key="overview", ticket_keys=["repaired"])
    assert index.tasks[0].agent_stats == expected
    assert full.tasks[0].agent_stats == expected
    assert focused.tasks[0].agent_stats == expected
    assert expected.reported_usage.total == 107
    assert expected.launch_attempts == 5
    tickets = {ticket.key: ticket.agent_stats for ticket in full.tickets}
    assert tickets["repaired"].reported_usage.total == 100
    assert tickets["repaired"].launch_attempts == 4
    assert tickets["mirror"].reported_usage.total == 7
    assert tickets["unused-group"].coverage.usage_status == "empty"
    nodes = {node.id: node for node in full.graph.nodes}
    assert nodes["task:overview"].data["agent_stats"] == expected.model_dump(mode="json")
    assert nodes["ticket:repaired"].data["agent_stats"] == tickets["repaired"].model_dump(
        mode="json"
    )
    brief = repo.brief(project_id=project_id, ticket_key="repaired")
    assert brief.ticket.agent_stats == tickets["repaired"]
    assert brief.task.agent_stats == expected
    assert repo.agent_stats_all().totals == expected
    # Mutation responses expose current metrics without persisting another total.
    updated = repo.update_task(
        project_id=project_id, task_key="overview", patch_json={"title": "Updated"}
    )
    assert updated.data.task.agent_stats == expected
    for entry in repo.history(project_id=project_id).items:
        assert "agent_stats" not in (entry.before_json or {})
        assert "agent_stats" not in (entry.after_json or {})


@pytest.mark.parametrize("state", ["empty", "unavailable", "complete", "partial"])
def test_projection_coverage_and_scope_isolation(session: Session, project_id: int, state: str):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="scope", title="Scope")
    if state != "empty":
        repo.create_ticket(project_id=project_id, task_key="scope", key="own", title="Own")
        unknown = _receipt()
        for key in ("usage", "selected_role", "validated_outcome"):
            unknown.pop(key)
        automatic = {
            "attempt": 1,
            "delivery": unknown if state != "complete" else _receipt(total=0),
        }
        if state == "partial":
            automatic["review"] = _receipt("review_agent", "success", 0)
        repo.update_ticket(
            project_id=project_id,
            ticket_key="own",
            patch_json={"completion_evidence_json": {"automatic_delivery": automatic}},
        )
    # Other tasks, trackers and projects must not enter this task's metrics.
    repo.create_task(project_id=project_id, key="other-task", title="Other task")
    repo.create_ticket(project_id=project_id, task_key="other-task", key="other", title="Other")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="other",
        patch_json={"completion_evidence_json": {"automatic_delivery": _repaired()}},
    )
    other_tracker = _ticket(session, project_id, "other-tracker", tracker_key="other")
    other_tracker.completion_evidence_json = {"automatic_delivery": _repaired()}
    session.add(other_tracker)
    session.commit()
    other_project = (
        ProjectRepository(session)
        .create(slug="other-scope", name="Other", domain="other.test")
        .data
    )
    _store(session, other_project.id, "other-project", _repaired())

    index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
    scoped = next(task.agent_stats for task in index.tasks if task.key == "scope")
    focused = repo.get(project_id=project_id, task_key="scope")
    assert focused.tasks[0].agent_stats == scoped
    assert scoped.coverage.usage_status == state
    assert scoped.coverage.classification_status == state
    assert scoped.reported_usage.total == (None if state == "unavailable" else 0)
    assert (
        scoped.launch_attempts == {"empty": 0, "unavailable": 1, "complete": 1, "partial": 2}[state]
    )
    if focused.tickets:
        assert focused.tickets[0].agent_stats == scoped
    assert (
        repo.agent_stats(project_id=project_id).totals.launch_attempts == scoped.launch_attempts + 4
    )


@pytest.mark.parametrize("task_count", [1, 20])
def test_task_metrics_batch_queries_stay_bounded(
    session: Session, project_id: int, task_count: int
):
    repo = TrackerRepository(session)
    tracker = repo.ensure_tracker(project_id=project_id)
    for index in range(task_count):
        task = TrackerTask(
            tracker_id=tracker.id,
            project_id=project_id,
            key=f"task-{index}",
            title="Task",
            status=TrackerItemStatus.NOT_STARTED,
            source_kind=TrackerSourceKind.MANUAL,
        )
        session.add(task)
        session.flush()
        session.add(
            TrackerTicket(
                tracker_id=tracker.id,
                project_id=project_id,
                task_id=task.id,
                key=f"ticket-{index}",
                title="Ticket",
                status=TrackerItemStatus.NOT_STARTED,
                kind=TrackerTicketKind.TICKET,
                source_kind=TrackerSourceKind.MANUAL,
                completion_evidence_json={"automatic_delivery": _repaired()},
            )
        )
    session.commit()
    session.expire_all()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", capture)
    try:
        index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
        assert len(statements) <= 8
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        statements.clear()
        full = repo.get(project_id=project_id, include_graph=False)
        assert len(statements) <= 16
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture)
    assert len(index.tasks) == task_count
    assert index.tickets == index.dependencies == index.links == []
    assert index.graph is None
    assert all(task.agent_stats.reported_usage.total == 100 for task in index.tasks)
    assert [task.agent_stats for task in full.tasks] == [task.agent_stats for task in index.tasks]


def test_repaired_current_archive_dedup_and_one_phase_review_mirror(
    session: Session, project_id: int
):
    repo = TrackerRepository(session)
    _store(session, project_id, "repaired", _repaired())
    stats = repo.agent_stats(project_id=project_id)
    assert stats.totals.launch_attempts == 4
    assert stats.totals.recorded_reviewer_rejects == 1
    assert stats.totals.reported_usage.total == 100
    assert stats.totals.coverage.usage_status == "complete"
    assert stats.totals.coverage.classification_status == "complete"
    assert len(stats.rows) == 2
    assert "PRIVATE" not in stats.model_dump_json()
    _store(
        session,
        project_id,
        "mirror",
        {"attempt": 1, "delivery": _receipt("review_agent", "repair", 0)},
    )
    _ticket(session, project_id, "unused-group")
    again = repo.agent_stats(project_id=project_id)
    assert again.totals.launch_attempts == 5
    assert again.totals.recorded_reviewer_rejects == 2
    assert again.totals.reported_usage.total == 100
    assert repo.agent_stats(project_id=project_id).totals == again.totals


def test_coverage_invalid_results_requeues_and_measured_zero(session: Session, project_id: int):
    repo = TrackerRepository(session)
    legacy = _receipt()
    for key in ("usage", "selected_role", "validated_outcome"):
        legacy.pop(key)
    _store(session, project_id, "legacy", {"attempt": 1, "review": legacy})
    unknown = repo.agent_stats(project_id=project_id)
    assert unknown.totals.reported_usage.total is None
    assert unknown.totals.coverage.usage_status == "unavailable"
    assert unknown.totals.coverage.classification_unknown_attempts == 1
    attempts = []
    for index, outcome in enumerate(("repair", "repair", "blocked", "failed", None), 1):
        receipt = _receipt("review_agent", outcome, 0)
        attempts.append({"attempt": index, "review": receipt})
    _store(session, project_id, "requeue", {**attempts[-1], "attempts": attempts})
    # Explicit null is a producer-validated native/schema/candidate failure, not a reject.
    result = repo.agent_stats(project_id=project_id)
    assert result.totals.launch_attempts == 6
    assert result.totals.recorded_reviewer_rejects == 2
    assert result.totals.reported_usage.total == 0
    assert result.totals.coverage.usage_measured_attempts == 5
    assert result.totals.coverage.usage_unavailable_attempts == 1
    assert result.totals.coverage.usage_status == "partial"
    assert result.totals.coverage.classification_status == "partial"
    assert next(row for row in result.rows if row.selected_role is None).launch_attempts == 1


def test_project_visibility_historical_identity_and_read_only_projection(
    session: Session, project_id: int
):
    projects = ProjectRepository(session)
    archived = projects.create(slug="archived-stats", name="Archived", domain="archive.test").data
    projects.update(archived.id, is_active=False)
    _store(session, project_id, "active", _repaired())
    _store(
        session,
        archived.id,
        "archived",
        {"attempt": 1, "delivery": _receipt(model="recorded-astra")},
    )
    repo = TrackerRepository(session)
    before = repo.get(project_id=project_id).model_dump()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", capture)
    try:
        assert repo.agent_stats_all().totals.launch_attempts == 4
        assert repo.agent_stats_all(is_active=False).totals.launch_attempts == 1
        all_stats = repo.agent_stats_all(is_active=None)
        assert all_stats.totals.launch_attempts == 5
        assert {(r.project_id, r.model) for r in all_stats.rows} == {
            (project_id, "recorded-sol"),
            (archived.id, "recorded-astra"),
        }
        assert repo.agent_stats(project_id=archived.id).totals.launch_attempts == 1
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture)
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert all("tracker_history" not in s and "tracker_revisions" not in s for s in statements)
    assert repo.get(project_id=project_id).model_dump() == before


def test_empty_read_does_not_create_a_tracker(session: Session, project_id: int):
    repo = TrackerRepository(session)
    result = repo.agent_stats(project_id=project_id)
    assert result.totals.launch_attempts == 0
    assert result.totals.coverage.usage_status == "empty"
    assert result.rows == []
    assert session.exec(select(TaskTracker)).all() == []
    other = _ticket(session, project_id, "nondefault", tracker_key="other")
    other.completion_evidence_json = {"automatic_delivery": _repaired()}
    session.add(other)
    session.commit()
    assert repo.agent_stats(project_id=project_id).totals.launch_attempts == 0


def test_malformed_usage_is_unavailable_and_defaults_do_not_relabel_history(
    session: Session, project_id: int
):
    repo = TrackerRepository(session)
    for index, measured in enumerate(
        (
            None,
            {},
            {"input": True, "output": 0, "total": 1},
            {"input": -1, "output": 1, "total": 0},
            {"input": 1, "output": 1, "total": 99},
        )
    ):
        receipt = _receipt(model=f"recorded-{index}")
        receipt["usage"]["measured"] = measured
        _store(session, project_id, f"bad-{index}", {"attempt": 1, "delivery": receipt})
    for index in range(12):
        _store(
            session,
            project_id,
            f"good-{index}",
            {"attempt": 1, "delivery": _receipt(model=f"measured-{index}")},
        )
    before = repo.agent_stats(project_id=project_id)
    tracker = repo.ensure_tracker(project_id=project_id)
    tracker.config_json = {
        "delivery": {
            "delivery_agent": {
                "name": "new-name",
                "model": "new-default",
                "instructions": "PRIVATE",
            }
        }
    }
    session.add(tracker)
    session.commit()
    after = repo.agent_stats(project_id=project_id)
    assert after.totals == before.totals
    assert after.rows == before.rows
    assert after.totals.reported_usage.total == 360
    assert after.totals.coverage.usage_unavailable_attempts == 5
    assert len(after.rows) == 17
    payload = after.model_dump(mode="json")
    for name in ("tracker.agentStats", "tracker.agentStatsAll"):
        assert (
            shape_operation_response(
                build_operation_registry().get(name), payload, response_mode="compact"
            )["data"]
            == payload
        )
