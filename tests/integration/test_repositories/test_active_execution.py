"""Real host-service lifetimes projected through tracker reads and its existing SSE feed."""

import json
import threading
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos.context import ContextRepository
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult, normalize_usage, usage_snapshot
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker import agent_stats as stats_projection
from stackos.repositories.tracker.events import record_execution_change


@pytest.mark.parametrize("mode", ["brief", "stream", "index", "get", "project", "global"])
@pytest.mark.parametrize("handoff_at", ["before_capture", "after_capture", "after_receipt_read"])
def test_overlapping_receipt_handoff_keeps_inflight_projections_consistent(
    session, project_id, monkeypatch, tmp_path, mode, handoff_at
):
    import asyncio

    from stackos.api.context import _timeline_stream
    from stackos.db.connection import make_engine
    from stackos.task_delivery import _record_execution_transition
    from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot

    # Independent thread connections need the production file/WAL engine, not
    # the per-thread in-memory repository fixture.
    engine = make_engine(tmp_path / "overlap.db")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    session = Session(engine)
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="overlap", title="Overlap")
    repo.create_ticket(
        project_id=project_id, task_key="overlap", key="overlap-child", title="Child"
    )
    stamp = datetime.now(UTC).isoformat()

    def measured(i, o, cache):
        return normalize_usage(
            usage_snapshot("fixture", "invocation", i, o, cache_read=cache),
            session_id="native",
            resumed=False,
        )

    repo.update_ticket(
        project_id=project_id,
        ticket_key="overlap-child",
        patch_json={
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 2,
                    "attempts": [
                        {
                            "attempt": 1,
                            "delivery": {
                                "launch": {
                                    "agent": "worker",
                                    "model": "selected",
                                    "started_at": stamp,
                                },
                                "exit_code": 0,
                                "usage": measured(100, 5, 80),
                            },
                        }
                    ],
                    "review": {
                        "launch": {"agent": "reviewer", "model": "selected", "started_at": stamp}
                    },
                }
            },
        },
    )
    started, native_exit, ready, persist, returned = [threading.Event() for _ in range(5)]
    errors = []

    def runner(*a, **kw):
        call = next(iter(service._active_invocations.values()))
        call.usage = measured(200, 8, 160)
        call.measured_at = stamp
        started.set()
        assert native_exit.wait(5)
        return AgentRunResult(
            0,
            thread_id="native",
            result={},
            usage=usage_snapshot("fixture", "invocation", 200, 8, cache_read=160),
        )

    def transition(call, active):
        if not active:
            ready.set()
            assert persist.wait(5)
        _record_execution_transition(lambda: Session(session.get_bind()), call, active)

    def launch():
        try:
            service.run_agent(
                "fixture",
                tmp_path,
                "Instructions",
                "Prompt",
                {},
                execution_scope=service.AgentExecutionScope(
                    project_id, "overlap", "overlap-child", "reviewer", 2, "review"
                ),
                on_execution_change=transition,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            returned.set()

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    thread = threading.Thread(target=launch)
    thread.start()
    try:
        assert started.wait(5)
        native_exit.set()
        assert ready.wait(5)
        assert service.active_execution(project_id, "overlap") is None
        original = stats_projection.provisional_usage
        reader_thread = threading.get_ident()
        armed = True

        def crossing(*args, **kwargs):
            nonlocal armed
            if threading.get_ident() != reader_thread or not armed:
                return original(*args, **kwargs)
            armed = False
            captured = original(*args, **kwargs) if handoff_at == "after_capture" else None
            persist.set()
            assert returned.wait(5)
            assert not errors
            assert not service._active_invocations
            return captured if handoff_at == "after_capture" else original(*args, **kwargs)

        original_merge = stats_projection.provisional_receipt

        def crossing_receipts(*args, **kwargs):
            nonlocal armed
            if threading.get_ident() == reader_thread and armed:
                # The SQL result is already loaded. Only the request's earlier
                # counter copy remains when the real callback unregisters now.
                armed = False
                persist.set()
                assert returned.wait(5)
                assert not errors and not service._active_invocations
            return original_merge(*args, **kwargs)

        with Session(session.get_bind()) as reader:
            read_repo = TrackerRepository(reader)
            # Keep the old ORM receipt cached, as an already-running request can.
            cached = read_repo._ticket_by_key(
                repo.status(project_id=project_id).tracker.id, "overlap-child"
            )
            assert "usage" not in cached.completion_evidence_json["automatic_delivery"]["review"]
            with monkeypatch.context() as patch:
                if handoff_at == "after_receipt_read":
                    patch.setattr(stats_projection, "provisional_receipt", crossing_receipts)
                else:
                    patch.setattr(stats_projection, "provisional_usage", crossing)
                if mode == "stream":

                    async def first():
                        async def disconnected():
                            return False

                        stream = _timeline_stream(
                            project_id,
                            SimpleNamespace(is_disconnected=disconnected),
                            None,
                            None,
                            25,
                            1,
                            15000,
                            None,
                            False,
                            session.get_bind(),
                        ).body_iterator
                        try:
                            data = json.loads((await anext(stream)).split("data: ", 1)[1])
                            return [
                                entry["agent_stats"]["reported_usage"]
                                for key in ("tasks", "tickets")
                                for entry in data["updates"][key]
                            ]
                        finally:
                            await stream.aclose()

                    usage = asyncio.run(first())
                elif mode in {"project", "global"}:
                    day = date.fromisoformat(stamp[:10])
                    kwargs = {"start_date": day, "end_date": day, "timezone": "UTC"}
                    summary = (
                        read_repo.agent_stats(project_id=project_id, **kwargs)
                        if mode == "project"
                        else read_repo.agent_stats_all(**kwargs)
                    )
                    usage = [
                        value.reported_usage.model_dump()
                        for value in (summary.totals, summary.daily[0])
                    ]
                    assert sorted(row.reported_usage.total for row in summary.rows) == [105, 208]
                    assert sorted(
                        row.reported_usage.total for row in summary.daily[0].by_agent
                    ) == [105, 208]
                elif mode == "brief":
                    brief = read_repo.brief(project_id=project_id, ticket_key="overlap-child")
                    usage = [
                        value.agent_stats.reported_usage.model_dump()
                        for value in (brief.ticket, brief.task)
                    ]
                else:
                    result = read_repo.get(
                        project_id=project_id, task_index_only=mode == "index", include_graph=False
                    )
                    usage = [
                        value.agent_stats.reported_usage.model_dump()
                        for value in [*result.tasks, *result.tickets]
                    ]
            assert not armed
            assert all(
                (value["total"], value["remaining_input"], value["cache_read"], value["output"])
                == (313, 60, 240, 13)
                for value in usage
            )
            assert (
                read_repo.brief(
                    project_id=project_id, ticket_key="overlap-child"
                ).ticket.agent_stats.reported_usage.total
                == 313
            )
    finally:
        native_exit.set()
        persist.set()
        thread.join(5)
        assert not thread.is_alive() and not errors
        session.close()
        engine.dispose()


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


def test_native_exit_hands_usage_to_receipt_before_return_without_active_resources(
    session, project_id, monkeypatch, tmp_path
):
    from stackos.task_delivery import _record_execution_transition

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="handoff", title="Handoff")
    repo.create_ticket(
        project_id=project_id, task_key="handoff", key="handoff-child", title="Child"
    )
    stamp = datetime.now(UTC).isoformat()

    def measured(i, o, cache):
        return normalize_usage(
            usage_snapshot("fixture", "invocation", i, o, cache_read=cache),
            session_id="native",
            resumed=False,
        )

    old = {
        "launch": {"agent": "reviewer", "model": "selected", "started_at": stamp},
        "usage": measured(100, 5, 80),
        "exit_code": 0,
    }
    pending = {"launch": {"agent": "reviewer", "model": "selected", "started_at": stamp}}
    evidence = {
        "automatic_delivery": {
            "attempt": 2,
            "attempts": [{"attempt": 1, "delivery": old}],
            "review": pending,
        }
    }
    repo.update_ticket(
        project_id=project_id,
        ticket_key="handoff-child",
        patch_json={"completion_evidence_json": evidence},
    )
    observations = []
    resources = SimpleNamespace(closed=False)

    def close():
        resources.closed = True

    def observe(stage):
        # Separate sessions reproduce the SSE/API reader, not a cached writer row.
        with Session(session.get_bind()) as reader:
            read_repo = TrackerRepository(reader)
            brief = read_repo.brief(project_id=project_id, ticket_key="handoff-child")
            day = date.fromisoformat(stamp[:10])
            project = read_repo.agent_stats(
                project_id=project_id, start_date=day, end_date=day, timezone="UTC"
            )
            global_stats = read_repo.agent_stats_all(start_date=day, end_date=day, timezone="UTC")
            index = read_repo.get(project_id=project_id, task_index_only=True, include_graph=False)
            projections = [
                brief.ticket.agent_stats,
                brief.task.agent_stats,
                index.tasks[0].agent_stats,
                project.totals,
                project.daily[0],
                project.rows[0],
                global_stats.totals,
            ]
            observations.append(
                (
                    stage,
                    [value.reported_usage.total for value in projections],
                    brief.ticket.active_execution is not None,
                )
            )

    def runner(*args, **kwargs):
        call = next(iter(service._active_invocations.values()))
        call.reader = SimpleNamespace(close=close, read=lambda: None)
        call.usage = measured(200, 8, 160)
        call.measured_at = stamp
        call.samples.append({"at": stamp, **call.usage["measured"]})
        observe("last-native-checkpoint")
        return AgentRunResult(
            0,
            thread_id="native",
            result={},
            usage=usage_snapshot("fixture", "invocation", 200, 8, cache_read=160),
        )

    transition_resources = []

    def transition(call, active):
        if not active:
            collector = service._collector
            if collector:
                collector.join(2)
            transition_resources.append(
                (resources.closed, bool(call.samples), bool(service.usage_executions(project_id)))
            )
            assert service._collector is None
            # Deliberately delay the receipt write with multiple intervening read
            # cycles after native exit. No sleep or persist-before-exit fixture.
            observe("native-exited-before-receipt")
            observe("delayed-receipt")
        _record_execution_transition(lambda: Session(session.get_bind()), call, active)

    monkeypatch.setattr(service, "agent_selection_error", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    service.run_agent(
        "fixture",
        tmp_path,
        "Instructions",
        "Prompt",
        {},
        execution_scope=service.AgentExecutionScope(
            project_id, "handoff", "handoff-child", "reviewer", 2, "review"
        ),
        on_execution_change=transition,
    )
    observe("returned-before-validation-and-final-receipt")
    session.expire_all()
    persisted = repo.brief(
        project_id=project_id, ticket_key="handoff-child"
    ).ticket.completion_evidence_json
    assert persisted["automatic_delivery"]["review"]["usage"] == measured(200, 8, 160)
    assert "result" not in persisted["automatic_delivery"]["review"]
    assert "exit_code" not in persisted["automatic_delivery"]["review"]
    persisted["automatic_delivery"]["review"].update(
        {"exit_code": 0, "result": {}, "usage": measured(200, 8, 160)}
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="handoff-child",
        patch_json={"completion_evidence_json": persisted},
    )
    observe("final-receipt")
    assert [row[1] for row in observations] == [[313] * 7] * 5
    assert [row[2] for row in observations] == [True, False, False, False, False]
    assert transition_resources == [(True, False, False)]
    assert not service._active_invocations


@pytest.mark.asyncio
async def test_receipt_only_commit_refreshes_existing_and_reconnected_streams(session, project_id):
    import asyncio

    from stackos.api.context import _timeline_stream

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="receipt-only", title="Receipt only")
    repo.create_ticket(
        project_id=project_id, task_key="receipt-only", key="receipt-child", title="Child"
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
        ticket_key="receipt-child",
        patch_json={"completion_evidence_json": evidence},
    )

    async def disconnected():
        return False

    def stream(pid):
        return _timeline_stream(
            pid,
            SimpleNamespace(is_disconnected=disconnected),
            None,
            None,
            25,
            1,
            15000,
            None,
            False,
            session.get_bind(),
        ).body_iterator

    project_stream, global_stream = stream(project_id), stream(None)
    try:
        await anext(project_stream)
        await anext(global_stream)
        cursor = ContextRepository(session).latest_tracker_status_event_id(project_id=project_id)
        final = normalize_usage(
            usage_snapshot("fixture", "invocation", 200, 8, cache_read=160),
            session_id="private-native",
            resumed=False,
        )
        evidence["automatic_delivery"]["delivery"].update(
            {"usage": final, "exit_code": 0, "result": {}}
        )
        # No task/ticket status or active-registry transition accompanies this write.
        repo.update_ticket(
            project_id=project_id,
            ticket_key="receipt-child",
            patch_json={"completion_evidence_json": evidence},
        )
        frame = await asyncio.wait_for(anext(project_stream), timeout=1)
        assert frame.startswith("event: tracker-usage\n")
        data = json.loads(frame.split("data: ", 1)[1])
        assert data["executions"] == []
        assert data["updates"]["tickets"][0]["key"] == "receipt-child"
        assert data["updates"]["tasks"][0]["agent_stats"]["reported_usage"]["total"] == 208
        assert "private-native" not in frame
        assert (await asyncio.wait_for(anext(global_stream), timeout=1)).startswith(
            "event: tracker-usage\n"
        )
        events = (
            ContextRepository(session)
            .tracker_status_timeline(project_id=project_id, after_id=cursor)
            .items
        )
        assert [event.event_type for event in events] == ["tracker.usage.changed"]
        last = events[-1].id
        repo.update_ticket(
            project_id=project_id,
            ticket_key="receipt-child",
            patch_json={"completion_evidence_json": deepcopy(evidence)},
        )
        assert (
            ContextRepository(session).latest_tracker_status_event_id(project_id=project_id) == last
        )
        reconnect = stream(project_id)
        try:
            frame = await anext(reconnect)
            data = json.loads(frame.split("data: ", 1)[1])
            assert data["updates"]["replace"] is True
            assert data["updates"]["tickets"][0]["agent_stats"]["reported_usage"]["total"] == 208
            assert data["updates"]["tickets"][0]["active_execution"] is None
        finally:
            await reconnect.aclose()
    finally:
        await project_stream.aclose()
        await global_stream.aclose()
