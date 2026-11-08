"""Real host-service lifetimes projected through tracker reads and its existing SSE feed."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos.context import ContextRepository
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult, normalize_usage, usage_snapshot
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.events import record_execution_change


@pytest.mark.parametrize("exit_kind", ["success", "error", "exception", "cancel"])
def test_actual_call_identity_transitions_and_cleanup(
    session, project_id, monkeypatch, tmp_path, exit_kind
):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="live", title="Live")
    repo.create_ticket(project_id=project_id, task_key="live", key="child", title="Child")
    repo.create_ticket(project_id=project_id, task_key="live", key="parent", title="Parent")
    repo.update_ticket(
        project_id=project_id, ticket_key="child", patch_json={"status": "in-progress"}
    )
    context = ContextRepository(session)
    cursor = context.latest_tracker_status_event_id(project_id=project_id)
    assert repo.brief(project_id=project_id, ticket_key="child").ticket.active_execution is None
    seen = []

    def runner(*args, **kwargs):
        brief = repo.brief(project_id=project_id, ticket_key="child")
        current = brief.ticket.active_execution
        assert current is not None
        assert brief.task.active_execution == current
        index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
        assert next(task for task in index.tasks if task.key == "live").active_execution == current
        full = repo.get(project_id=project_id, task_key="live", include_graph=True)
        assert (
            next(ticket for ticket in full.tickets if ticket.key == "child").active_execution
            == current
        )
        assert (
            next(ticket for ticket in full.tickets if ticket.key == "parent").active_execution
            is None
        )
        assert service.active_execution(project_id + 100, "live") is None
        assert service.active_execution(project_id, "other") is None
        assert brief.ticket.status == "in-progress"
        seen.append(current)
        if exit_kind == "exception":
            raise RuntimeError("native failed")
        if exit_kind == "cancel":
            raise KeyboardInterrupt()
        return AgentRunResult(0 if exit_kind == "success" else 1, thread_id="same-native-session")

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: SimpleNamespace(run_agent=runner)
    )
    for name, model in [
        ("Project implementer", "model-v1"),
        ("Independent reviewer", "model-v2"),
        ("Project implementer", "model-v1"),
    ]:
        kwargs = dict(
            execution_scope=service.AgentExecutionScope(project_id, "live", "child", name),
            on_execution_change=lambda call, active: record_execution_change(session, call, active),
            model=model,
            resume_thread_id="same-native-session",
        )
        if exit_kind in {"exception", "cancel"}:
            with pytest.raises(RuntimeError if exit_kind == "exception" else KeyboardInterrupt):
                service.run_agent("fixture", tmp_path, "Instructions", "Prompt", {}, **kwargs)
        else:
            result = service.run_agent("fixture", tmp_path, "Instructions", "Prompt", {}, **kwargs)
            assert result.thread_id == "same-native-session"
        assert service.active_execution(project_id, "live") is None
        assert repo.brief(project_id=project_id, ticket_key="child").ticket.active_execution is None
    assert [value.agent for value in seen] == [
        "Project implementer",
        "Independent reviewer",
        "Project implementer",
    ]
    assert [value.model for value in seen] == ["model-v1", "model-v2", "model-v1"]
    assert len({value.invocation_id for value in seen}) == 3
    assert seen[0].started_at < seen[1].started_at < seen[2].started_at
    with Session(session.get_bind()) as reader:
        events = (
            ContextRepository(reader)
            .tracker_status_timeline(project_id=project_id, task_key="live", after_id=cursor)
            .items
        )
    assert [event.event_type for event in events] == ["tracker.execution.changed"] * 6
    assert [event.metadata_json["active"] for event in events] == [True, False] * 3
    assert (
        context.tracker_status_timeline(
            project_id=project_id, task_key="other", after_id=cursor
        ).items
        == []
    )
    assert (
        context.latest_tracker_status_event_id(project_id=project_id, task_key="live")
        == events[-1].id
    )
    assert service._active_invocations == {}


def test_invalid_launch_never_registers(monkeypatch, tmp_path):
    changes = []
    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: "Unsupported selection")
    result = service.run_agent(
        "fixture",
        tmp_path,
        "Instructions",
        "Prompt",
        {},
        execution_scope=service.AgentExecutionScope(1, "task", "ticket", "worker"),
        on_execution_change=lambda *args: changes.append(args),
    )
    assert result.error == "Unsupported selection"
    assert changes == []
    assert service.active_execution(1, "task") is None


def test_provisional_ticket_task_project_daily_groups_and_final_race(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="usage-live", title="Live")
    repo.create_ticket(
        project_id=project_id, task_key="usage-live", key="usage-child", title="Child"
    )
    stamp = datetime.now(UTC).isoformat()
    usage = normalize_usage(
        usage_snapshot("fixture", "invocation", 100, 5, cache_read=80),
        session_id="old",
        resumed=False,
    )
    old = {
        "launch": {
            "agent": "worker",
            "model": "selected",
            "host_key": "codex",
            "started_at": stamp,
        },
        "usage": usage,
        "exit_code": 0,
    }
    pending = {
        "launch": {"agent": "worker", "model": "selected", "host_key": "codex", "started_at": stamp}
    }
    evidence = {
        "automatic_delivery": {
            "attempt": 2,
            "attempts": [{"attempt": 1, "delivery": old}],
            "review": pending,
        }
    }
    repo.update_ticket(
        project_id=project_id,
        ticket_key="usage-child",
        patch_json={"completion_evidence_json": evidence},
    )
    call = service.ActiveInvocation(
        "usage-call",
        service.AgentExecutionScope(project_id, "usage-live", "usage-child", "worker", 2, "review"),
        "codex",
        "selected",
        stamp,
    )
    call.usage = normalize_usage(
        usage_snapshot("fixture", "invocation", 200, 8, cache_read=160),
        session_id="current",
        resumed=False,
    )
    call.measured_at = stamp
    service._active_invocations[call.invocation_id] = call
    try:

        def check():
            brief = repo.brief(project_id=project_id, ticket_key="usage-child")
            index = repo.get(project_id=project_id, task_index_only=True, include_graph=False)
            summary = repo.agent_stats(
                project_id=project_id,
                start_date=date.fromisoformat(stamp[:10]),
                end_date=date.fromisoformat(stamp[:10]),
                timezone="UTC",
            )
            global_summary = repo.agent_stats_all(
                start_date=date.fromisoformat(stamp[:10]),
                end_date=date.fromisoformat(stamp[:10]),
                timezone="UTC",
            )
            projections = [
                brief.ticket.agent_stats,
                brief.task.agent_stats,
                index.tasks[0].agent_stats,
                summary.totals,
                summary.daily[0],
                summary.rows[0],
                global_summary.totals,
            ]
            for metrics in projections:
                assert metrics.reported_usage.total == 313
                assert metrics.reported_usage.remaining_input == 60
                assert metrics.reported_usage.cache_read == 240
                assert metrics.reported_usage.output == 13
                assert metrics.launch_attempts == 2
            return brief

        before = deepcopy(evidence)
        assert check().ticket.completion_evidence_json == before
        # Existing final persistence can race with an already read checkpoint.
        evidence["automatic_delivery"]["review"] = {
            **pending,
            "usage": deepcopy(call.usage),
            "exit_code": 0,
        }
        repo.update_ticket(
            project_id=project_id,
            ticket_key="usage-child",
            patch_json={"completion_evidence_json": evidence},
        )
        call.usage = normalize_usage(
            usage_snapshot("fixture", "invocation", 500, 20, cache_read=400),
            session_id="current",
            resumed=False,
        )
        check()  # the persisted final wins; no double count or late replacement
        service._unregister(call)
        assert check().ticket.active_execution is None
    finally:
        service._unregister(call)


@pytest.mark.asyncio
async def test_usage_stream_initial_and_reconnect_replace_state_with_safe_projection(
    session, project_id
):
    from stackos.api.context import _timeline_stream

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="stream-live", title="Live")
    repo.create_ticket(
        project_id=project_id, task_key="stream-live", key="stream-child", title="Child"
    )
    stamp = datetime.now(UTC).isoformat()
    evidence = {
        "automatic_delivery": {
            "attempt": 1,
            "delivery": {"launch": {"agent": "worker", "started_at": stamp}},
        }
    }
    repo.update_ticket(
        project_id=project_id,
        ticket_key="stream-child",
        patch_json={"completion_evidence_json": evidence},
    )
    call = service.ActiveInvocation(
        "stream-call",
        service.AgentExecutionScope(
            project_id, "stream-live", "stream-child", "worker", 1, "delivery"
        ),
        "codex",
        "selected",
        stamp,
    )
    call.usage = normalize_usage(
        usage_snapshot("fixture", "invocation", 100, 5, cache_read=80),
        session_id="PRIVATE_NATIVE_ID",
        resumed=False,
    )
    call.measured_at = stamp
    service._active_invocations[call.invocation_id] = call

    async def disconnected():
        return False

    async def first():
        response = _timeline_stream(
            project_id,
            SimpleNamespace(is_disconnected=disconnected),
            None,
            None,
            25,
            1000,
            15000,
            None,
            False,
            session.get_bind(),
        )
        iterator = response.body_iterator
        frame = await anext(iterator)
        await iterator.aclose()
        assert "PRIVATE_NATIVE_ID" not in frame
        assert frame.startswith("event: tracker-usage\n")
        return json.loads(frame.split("data: ", 1)[1])

    try:
        initial = await first()
        assert initial["updates"]["replace"] is True
        assert initial["updates"]["tasks"][0]["agent_stats"]["reported_usage"]["total"] == 105
        assert set(initial["updates"]["tickets"][0]) == {"key", "agent_stats", "active_execution"}
        evidence["automatic_delivery"]["delivery"].update({"usage": call.usage, "exit_code": 0})
        repo.update_ticket(
            project_id=project_id,
            ticket_key="stream-child",
            patch_json={"completion_evidence_json": evidence},
        )
        service._unregister(call)
        reconnect = await first()
        assert reconnect["executions"] == []
        assert reconnect["updates"]["tickets"][0]["active_execution"] is None
        assert reconnect["updates"]["tickets"][0]["agent_stats"]["reported_usage"]["total"] == 105
    finally:
        service._unregister(call)
