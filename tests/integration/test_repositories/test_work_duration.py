"""Independent wall-interval expectations through the retained tracker outputs."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from stackos.db.models import TrackerItemStatus
from stackos.host_mcp import service
from stackos.operations.registry import build_operation_registry
from stackos.operations.responses import shape_operation_response
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.agent_stats import (
    query_task_agent_stats,
    query_ticket_agent_stats,
    usage_refresh,
)
from tests.integration.test_repositories.test_ticket_overviews import _ticket

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def stamp(seconds):
    return (EPOCH + timedelta(seconds=seconds)).isoformat()


def receipt(start, end):
    return {
        "launch": {"started_at": stamp(start), "agent": "worker", "host_key": "codex"},
        **({"finished_at": stamp(end), "exit_code": 0} if end is not None else {}),
    }


def store(session, project_id, key, automatic):
    row = _ticket(session, project_id, key, status=TrackerItemStatus.IN_PROGRESS)
    # Lifecycle dates intentionally span weeks. They must never fill receipt gaps.
    row.started_at = datetime(2025, 12, 1)
    row.completed_at = datetime(2026, 2, 1)
    row.completion_evidence_json = {"automatic_delivery": automatic}
    session.add(row)
    session.commit()
    return row


def test_union_repairs_dedup_and_all_work_outputs(session, project_id):
    first = {"attempt": 1, "delivery": receipt(0, 10), "review": receipt(20, 30)}
    current = {"attempt": 2, "delivery": receipt(5, 15), "review": receipt(40, 50)}
    store(session, project_id, "repaired", {**current, "attempts": [first, deepcopy(current)]})
    store(session, project_id, "parallel", {"attempt": 1, "delivery": receipt(10, 25)})
    repo = TrackerRepository(session)
    # [0,15] + [20,30] + [40,50] = 35; task [0,30] + [40,50] = 40, not 50.
    brief = repo.brief(project_id=project_id, ticket_key="repaired")
    assert brief.ticket.work_duration.total_seconds == 35
    assert brief.task.work_duration.total_seconds == 40
    assert brief.ticket.work_duration.coverage == "complete"
    assert brief.ticket.work_duration.recorded_intervals == 4
    assert brief.ticket.work_duration.endpoint_at == stamp(50)
    assert brief.ticket.work_duration.live == []
    full = repo.get(project_id=project_id)
    index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
    focused = repo.get(project_id=project_id, ticket_keys=["parallel"])
    for task in [*full.tasks, *index.tasks, *focused.tasks]:
        assert task.work_duration.total_seconds == 40
    nodes = {n.id: n.data for n in full.graph.nodes}
    assert nodes["task:overview"]["work_duration"]["total_seconds"] == 40
    assert nodes["ticket:repaired"]["work_duration"]["total_seconds"] == 35
    registry = build_operation_registry()
    for mode in ["raw", "compact"]:
        shaped = shape_operation_response(
            registry.get("tracker.get"), full.model_dump(mode="json"), response_mode=mode
        )
        shaped = shaped.get("data", shaped)
        assert shaped["tasks"][0]["work_duration"]["total_seconds"] == 40
        assert shaped["tickets"][0]["work_duration"]["coverage"] == "complete"
    refreshed = usage_refresh(session, project_id, None)
    assert refreshed["tasks"][0]["work_duration"]["total_seconds"] == 40
    assert refreshed["tickets"][0]["work_duration"]["total_seconds"] == 35


@pytest.mark.parametrize(
    "intervals,expected",
    [([(20, 30), (0, 10)], 20), ([(0, 10), (5, 15)], 15), ([(0, 0)], 0)],
)
def test_independent_disjoint_overlap_and_zero_fixtures(session, project_id, intervals, expected):
    attempts = [
        {"attempt": i + 1, "delivery": receipt(start, end)}
        for i, (start, end) in enumerate(intervals)
    ]
    store(session, project_id, "intervals", {**attempts[-1], "attempts": attempts})
    result = TrackerRepository(session).brief(project_id=project_id, ticket_key="intervals")
    for duration in [result.ticket.work_duration, result.task.work_duration]:
        assert duration.total_seconds == expected
        assert duration.coverage == "complete"


def test_unrecorded_history_is_stably_unavailable(session, project_id):
    _ticket(session, project_id, "unrecorded", status=TrackerItemStatus.IN_PROGRESS)
    repo = TrackerRepository(session)
    before = repo.brief(project_id=project_id, ticket_key="unrecorded").ticket.work_duration
    after = repo.brief(project_id=project_id, ticket_key="unrecorded").ticket.work_duration
    assert before == after
    assert before.total_seconds is None
    assert before.observed_at is None
    assert before.coverage == "unavailable"
    assert before.issues == ["no_recorded_invocations"]


@pytest.mark.parametrize("status", list(TrackerItemStatus))
def test_lifecycle_gaps_do_not_extend_recorded_work(session, project_id, status):
    row = store(session, project_id, "gaps", {"attempt": 1, "delivery": receipt(0, 10)})
    row.status = status
    session.add(row)
    session.commit()
    duration = TrackerRepository(session).brief(project_id=project_id, ticket_key="gaps")
    assert duration.ticket.work_duration.total_seconds == 10
    assert duration.task.work_duration.total_seconds == 10


@pytest.mark.parametrize(
    "broken,reason",
    [
        ({"launch": {}}, "missing_start"),
        ({"launch": {"started_at": "0001-01-01T00:00:00+10:00"}}, "missing_start"),
        ({}, "missing_start"),
        (receipt(20, 10), "invalid_order"),
        (receipt(0, None), "missing_finish"),
        ({**receipt(0, 10), "finished_at": "bad"}, "missing_finish"),
        ({**receipt(0, 10), "finished_at": "2026-01-01T00:00:10"}, "missing_finish"),
        (receipt(0, 10000000000), "future_endpoint"),
    ],
)
def test_missing_and_anomalous_endpoints_are_disclosed(session, project_id, broken, reason):
    store(
        session, project_id, "partial", {"attempt": 1, "delivery": receipt(0, 10), "review": broken}
    )
    store(session, project_id, "unknown", {"attempt": 1, "delivery": broken})
    repo = TrackerRepository(session)
    partial = repo.brief(project_id=project_id, ticket_key="partial").ticket.work_duration
    assert partial.total_seconds == 10
    assert partial.coverage == "partial"
    assert partial.unavailable_intervals == 1
    assert reason in partial.issues
    unknown = repo.brief(project_id=project_id, ticket_key="unknown").ticket.work_duration
    assert unknown.total_seconds is None
    assert unknown.coverage == "unavailable"


def test_owned_live_without_tokens_and_final_replacement(session, project_id, monkeypatch):
    now = datetime.now(UTC)
    start = (now - timedelta(seconds=10)).isoformat()
    execution = {
        "invocation_id": "owned",
        "agent": "worker",
        "host_key": "codex",
        "model": None,
        "ticket_key": "live",
        "started_at": start,
        "live_usage": None,
    }
    current = {"launch": {"started_at": start}, "execution": deepcopy(execution)}
    row = store(session, project_id, "live", {"attempt": 2, "delivery": current})
    monkeypatch.setattr(
        service,
        "invocation_timings",
        lambda: [
            {
                "project_id": project_id,
                "task_key": "overview",
                "attempt": 2,
                "phase": "delivery",
                **execution,
            }
        ],
    )
    # The projection observes the real registry API, independently of token telemetry.
    repo = TrackerRepository(session)
    live = repo.brief(project_id=project_id, ticket_key="live").ticket.work_duration
    assert 10 <= live.total_seconds < 20
    assert live.coverage == "complete"
    assert live.live[0].invocation_id == "owned"
    assert live.endpoint_at == live.observed_at
    current["finished_at"] = (now - timedelta(seconds=1)).isoformat()
    current["exit_code"] = 0
    row.completion_evidence_json = {
        "automatic_delivery": {
            "attempt": 2,
            "delivery": deepcopy(current),
            "attempts": [{"attempt": 2, "delivery": {"launch": {"started_at": start}}}],
        }
    }
    session.add(row)
    session.commit()
    for _ in range(2):
        final = repo.brief(project_id=project_id, ticket_key="live").ticket.work_duration
        assert final.total_seconds == 9
        assert final.live == []
        assert final.recorded_intervals == 1


@pytest.mark.parametrize(
    "mismatch",
    ["invocation_id", "started_at", "ticket_key", "project_id", "task_key", "attempt", "phase"],
)
def test_stale_live_cannot_invent_a_finish(session, project_id, monkeypatch, mismatch):
    execution = {
        "invocation_id": "old",
        "agent": "worker",
        "host_key": "codex",
        "model": None,
        "started_at": stamp(0),
        "ticket_key": "stale",
    }
    store(
        session,
        project_id,
        "stale",
        {
            "attempt": 1,
            "delivery": {
                **receipt(0, None),
                "execution": execution,
            },
        },
    )
    live = {
        "project_id": project_id,
        "task_key": "overview",
        "attempt": 1,
        "phase": "delivery",
        **execution,
    }
    live[mismatch] = "different" if mismatch != "project_id" else project_id + 1
    monkeypatch.setattr(service, "invocation_timings", lambda: [live])
    duration = (
        TrackerRepository(session)
        .brief(project_id=project_id, ticket_key="stale")
        .ticket.work_duration
    )
    assert duration.total_seconds is None
    assert duration.coverage == "unavailable"
    assert "stale_live_state" in duration.issues
    assert duration.live == []


@pytest.mark.parametrize("exit_kind", ["success", "failure", "exception", "cancel"])
def test_real_managed_timing_handoff_persists_and_freezes(
    session, project_id, tmp_path, monkeypatch, exit_kind
):
    from sqlmodel import Session

    from stackos.host_mcp.result import AgentRunResult
    from stackos.task_delivery import _record_execution_transition

    started_at = datetime.now(UTC).isoformat()
    store(
        session,
        project_id,
        "managed",
        {
            "attempt": 1,
            "delivery": {
                "launch": {"started_at": started_at},
            },
        },
    )
    repo = TrackerRepository(session)
    observed = []

    def runner(*args, **kwargs):
        running = repo.brief(project_id=project_id, ticket_key="managed").ticket.work_duration
        assert running.coverage == "complete"
        assert running.live[0].ticket_key == "managed"
        assert running.total_seconds >= 0
        if exit_kind in {"exception", "cancel"}:
            raise RuntimeError("native failed") if exit_kind == "exception" else KeyboardInterrupt()
        return AgentRunResult(0 if exit_kind == "success" else 1)

    def transition(call, active):
        if not active:
            # The finished registry entry owns a canonical close during the DB
            # handoff, even before the receipt commit and without any tokens.
            frozen = repo.brief(project_id=project_id, ticket_key="managed").ticket.work_duration
            assert frozen.coverage == "complete"
            assert frozen.live == []
            observed.append(frozen.total_seconds)
        _record_execution_transition(lambda: Session(session.get_bind()), call, active)

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    kwargs = {
        "execution_scope": service.AgentExecutionScope(
            project_id, "overview", "managed", "worker", 1, "delivery"
        ),
        "on_execution_change": transition,
    }
    if exit_kind in {"exception", "cancel"}:
        with pytest.raises(RuntimeError if exit_kind == "exception" else KeyboardInterrupt):
            service.run_agent("fixture", tmp_path, "Instructions", "Prompt", {}, **kwargs)
    else:
        service.run_agent("fixture", tmp_path, "Instructions", "Prompt", {}, **kwargs)
    session.expire_all()
    for _ in range(2):
        final = repo.brief(project_id=project_id, ticket_key="managed").ticket
        retained = final.completion_evidence_json["automatic_delivery"]["delivery"]
        expected = (
            datetime.fromisoformat(retained["finished_at"]) - datetime.fromisoformat(started_at)
        ).total_seconds()
        assert final.work_duration.total_seconds == expected == observed[0]
        assert final.work_duration.coverage == "complete"
        assert final.work_duration.live == []
    assert service.invocation_timings() == []


def test_scheduled_run_uses_canonical_work_time(session, project_id):
    from stackos.db.models import AgentRequest
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.projects import ScheduledJobRepository
    from stackos.repositories.run_plans import RunPlanRepository

    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "duration.run",
                "title": "Duration",
                "steps": [{"id": "work", "title": "Work"}],
            },
        )
        .data
    )
    request = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key="duration-request",
            title="Duration",
            source_kind="schedule",
            metadata_json={"schedule_id": 17},
        )
        .data
    )
    row = session.get(AgentRequest, request.id)
    row.run_plan_id = plan.id
    session.add(row)
    session.commit()
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-work",
        patch_json={
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 1,
                    "delivery": receipt(0, 10),
                }
            }
        },
    )
    scheduled = ScheduledJobRepository(session).runs(project_id=project_id).items[0]
    assert scheduled.duration_seconds == scheduled.work_duration.total_seconds == 10
    assert scheduled.work_duration.coverage == "complete"
    assert scheduled.started_at is None and scheduled.ended_at is None


def test_unknown_child_history_remains_partial_in_every_task_projection(session, project_id):
    store(session, project_id, "known", {"attempt": 1, "delivery": receipt(0, 10)})
    store(session, project_id, "unrecorded", {})
    repo = TrackerRepository(session)
    brief = repo.brief(project_id=project_id, ticket_key="unrecorded")
    full = repo.get(project_id=project_id)
    index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
    filtered = repo.get(project_id=project_id, ticket_keys=["known"])
    tasks = [brief.task, *full.tasks, *index.tasks, *filtered.tasks]
    assert brief.ticket.work_duration.coverage == "unavailable"
    for task in tasks:
        assert task.work_duration.total_seconds == 10
        assert task.work_duration.coverage == "partial"
        assert task.work_duration.issues == ["no_recorded_invocations"]
        assert task.work_duration.unavailable_intervals == 0
    for mode in ("raw", "compact"):
        payload = shape_operation_response(
            build_operation_registry().get("tracker.get"),
            full.model_dump(mode="json"),
            response_mode=mode,
        )
        payload = payload.get("data", payload)
        assert payload["tasks"][0]["work_duration"]["coverage"] == "partial"
    node = next(node for node in full.graph.nodes if node.type == "task")
    assert node.data["work_duration"]["total_seconds"] == 10
    assert node.data["work_duration"]["coverage"] == "partial"
    refreshed = usage_refresh(session, project_id, None)
    assert refreshed["tasks"][0]["work_duration"]["coverage"] == "partial"


@pytest.mark.parametrize("level", ["ticket", "task", "refresh"])
@pytest.mark.parametrize("handoff_at", ["before_copy", "after_copy", "before_commit"])
def test_final_timing_survives_receipt_handoff_and_retirement(
    session, project_id, monkeypatch, level, handoff_at
):
    from sqlmodel import Session

    from stackos.task_delivery import _record_execution_transition

    start = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    finish = (datetime.fromisoformat(start) + timedelta(seconds=9)).isoformat()
    call = service.ActiveInvocation(
        "timing-retirement",
        service.AgentExecutionScope(project_id, "overview", "racing", "worker", 1, "delivery"),
        "codex",
        None,
        start,
    )
    row = store(
        session,
        project_id,
        "racing",
        {
            "attempt": 1,
            "delivery": {
                "launch": {"started_at": start},
                "execution": call.projection(),
            },
        },
    )
    service._active_invocations[call.invocation_id] = call
    original = service.invocation_timings

    def crossing():
        before = original()
        service._finish(call, None, finished_at=finish)
        monkeypatch.setattr(service, "invocation_timings", original)
        if handoff_at != "before_commit":
            _record_execution_transition(lambda: Session(session.get_bind()), call, False)
            service._unregister(call)
        return before if handoff_at == "after_copy" else original()

    monkeypatch.setattr(service, "invocation_timings", crossing)
    durations = {}
    try:
        if level == "ticket":
            query_ticket_agent_stats(session, ticket_ids={row.id}, work_durations=durations)
            actual = durations[row.id]
        elif level == "task":
            query_task_agent_stats(
                session,
                tracker_id=row.tracker_id,
                task_ids={row.task_id},
                task_keys={row.task_id: "overview"},
                work_durations=durations,
            )
            actual = durations[row.task_id]
        else:
            refreshed = usage_refresh(session, project_id, None)
            for scope in ("tasks", "tickets"):
                duration = refreshed[scope][0]["work_duration"]
                assert duration["total_seconds"] == 9
                assert duration["coverage"] == "complete"
                assert duration["live"] == []
        if level != "refresh":
            assert actual.total_seconds == 9
            assert actual.coverage == "complete"
            assert actual.live == []
        if handoff_at == "before_commit":
            _record_execution_transition(lambda: Session(session.get_bind()), call, False)
            service._unregister(call)
            final = TrackerRepository(session).brief(project_id=project_id, ticket_key="racing")
            assert final.ticket.work_duration.total_seconds == 9
            assert final.task.work_duration.total_seconds == 9
            assert final.ticket.work_duration.live == []
    finally:
        service._unregister(call)


def test_retirement_after_sql_preserves_the_bounded_owned_observation(
    session, project_id, monkeypatch
):
    from sqlmodel import Session

    from stackos.task_delivery import _record_execution_transition

    start = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    call = service.ActiveInvocation(
        "after-sql",
        service.AgentExecutionScope(project_id, "overview", "after-sql", "worker", 1, "delivery"),
        "codex",
        None,
        start,
    )
    row = store(
        session,
        project_id,
        "after-sql",
        {
            "attempt": 1,
            "delivery": {
                "launch": {"started_at": start},
                "execution": call.projection(),
            },
        },
    )
    ticket_id = row.id
    service._active_invocations[call.invocation_id] = call
    snapshots = []
    original_timings = service.invocation_timings
    original_execute = session.execute
    armed = True

    def remember():
        snapshot = original_timings()
        snapshots.append(snapshot)
        return snapshot

    def crossing(*args, **kwargs):
        nonlocal armed
        result = original_execute(*args, **kwargs)
        if not armed:
            return result
        armed = False
        rows = result.all()
        service._finish(call, None)
        _record_execution_transition(lambda: Session(session.get_bind()), call, False)
        service._unregister(call)
        return SimpleNamespace(all=lambda: rows)

    monkeypatch.setattr(service, "invocation_timings", remember)
    monkeypatch.setattr(session, "execute", crossing)
    durations = {}
    try:
        query_ticket_agent_stats(session, ticket_ids={ticket_id}, work_durations=durations)
        observed = snapshots[0][0]["observed_at"]
        actual = durations[ticket_id]
        assert (
            actual.total_seconds
            == (datetime.fromisoformat(observed) - datetime.fromisoformat(start)).total_seconds()
        )
        assert actual.observed_at == observed
        assert actual.coverage == "complete"
        assert service.active_execution(project_id, "overview", row.key) is None
        query_ticket_agent_stats(session, ticket_ids={ticket_id}, work_durations=durations)
        assert durations[ticket_id].total_seconds >= actual.total_seconds
        assert durations[ticket_id].live == []
    finally:
        service._unregister(call)
