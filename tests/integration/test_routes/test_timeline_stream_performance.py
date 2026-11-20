"""Real stream/read boundaries with controlled slow reads and disposable WAL history."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event
from sqlmodel import Session

from stackos.api import context
from stackos.context import ContextRepository
from tests.helpers.agent_statistics import seed_statistics_workload, seed_tracker_event_history


def stream_request(disconnected=None):
    async def connected():
        return disconnected is not None and disconnected.is_set()

    return SimpleNamespace(is_disconnected=connected)


async def stream(
    api,
    project_id,
    *,
    after=None,
    demand=None,
    request=None,
    task_key=None,
    limit=25,
    max_events=None,
):
    return await context.context_timeline_stream(
        project_id,
        request or stream_request(),
        task_key=task_key,
        after=after,
        limit=limit,
        poll_ms=1,
        heartbeat_ms=1,
        max_events=max_events,
        replay=False,
        ui_demand=demand,
        engine=api.app.state.engine,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["admission", "initial", "poll", "projection"])
async def test_slow_stream_reads_leave_health_shell_and_other_stream_responsive(
    api, project_id, monkeypatch, stage, record_property
):
    engine = api.app.state.engine
    with Session(engine) as session:
        seed_statistics_workload(session, project_id, tasks=2, receipts=4)
        if stage == "poll":
            seed_tracker_event_history(session, project_id, events=20_000)
    loop_thread = threading.get_ident()
    lifetimes = []

    class OwnedSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.owner = threading.get_ident()
            lifetimes.append([self.owner, None])
            self.lifetime = lifetimes[-1]

        def close(self):
            self.lifetime[1] = threading.get_ident()
            return super().close()

    monkeypatch.setattr(context, "Session", OwnedSession)
    owner, name = {
        "admission": (context, "_ui_usage_scope"),
        "initial": (ContextRepository, "latest_tracker_status_event_id"),
        "poll": (ContextRepository, "tracker_status_timeline"),
        "projection": (context, "usage_refresh"),
    }[stage]
    original = getattr(owner, name)
    entered = threading.Event()

    def slow(*args, **kwargs):
        pid = kwargs.get("project_id") if stage in {"initial", "poll"} else args[1]
        if pid == project_id and not entered.is_set():
            entered.set()
            time.sleep(0.3)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, slow)
    response = None

    async def first():
        nonlocal response
        response = await stream(
            api,
            project_id,
            after=0 if stage == "poll" else None,
            demand=json.dumps({"projects": {str(project_id): {}}})
            if stage == "admission"
            else None,
        )
        return await anext(response.body_iterator)

    start = time.perf_counter()
    pending = asyncio.create_task(first())
    try:
        # Scheduled during the deliberately slow read, on this same event loop.
        while not entered.is_set():
            await asyncio.sleep(0.001)
        dispatch_ms = (time.perf_counter() - start) * 1000
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://127.0.0.1:5180",
            headers=api.headers,
        ) as client:
            timings = {}
            for path in ("/api/v1/health", "/api/v1/projects?limit=50"):
                before = time.perf_counter()
                result = await client.get(path)
                timings[path] = (time.perf_counter() - before) * 1000
                assert result.status_code == 200, result.text
                assert timings[path] < 100
        record_property("responsiveness", json.dumps({"dispatch_ms": dispatch_ms, **timings}))
        second = await stream(api, project_id)
        try:
            second_frame = await asyncio.wait_for(anext(second.body_iterator), 0.15)
            assert "tracker-usage" in second_frame
            assert not pending.done()
        finally:
            await second.body_iterator.aclose()
        assert dispatch_ms < 100
        assert "tracker-usage" in await pending
        assert all(created != loop_thread and created == closed for created, closed in lifetimes)
    finally:
        await pending
        if response is not None:
            await response.body_iterator.aclose()
    # The app also runs daemon jobs; their unrelated pool checkouts are not
    # stream leaks. The per-operation creation/close pairs above are decisive.


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["disconnect", "cancel"])
async def test_stream_end_during_projection_retires_owned_session_without_late_frame(
    api, project_id, monkeypatch, end
):
    engine = api.app.state.engine
    with Session(engine) as session:
        seed_statistics_workload(session, project_id, tasks=1, receipts=1)
    entered, release, closed, disconnected = (threading.Event() for _ in range(4))
    original = context.usage_refresh

    def slow(session, *args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(session, *args, **kwargs)

    class OwnedSession(Session):
        def close(self):
            super().close()
            closed.set()

    monkeypatch.setattr(context, "Session", OwnedSession)
    monkeypatch.setattr(context, "usage_refresh", slow)
    response = await stream(api, project_id, after=0, request=stream_request(disconnected))
    closed.clear()
    pending = asyncio.create_task(anext(response.body_iterator))
    try:
        # A thread wait cannot hide an event-loop stall in this regression.
        start = time.perf_counter()
        while not entered.is_set():
            await asyncio.sleep(0.001)
        assert time.perf_counter() - start < 0.2
        if end == "cancel":
            pending.cancel()
        else:
            disconnected.set()
        release.set()
        with pytest.raises(asyncio.CancelledError if end == "cancel" else StopAsyncIteration):
            await pending
        assert await asyncio.to_thread(closed.wait, 1)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
    finally:
        release.set()
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_http_disconnect_during_read_closes_worker_without_publishing(
    api, project_id, monkeypatch
):
    """Real authenticated ASGI stream dispatch and its disconnect cancellation scope."""
    engine = api.app.state.engine
    with Session(engine) as session:
        seed_statistics_workload(session, project_id, tasks=1, receipts=1)
    entered, release = threading.Event(), threading.Event()
    original = context.usage_refresh

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(context, "usage_refresh", slow)
    incoming, messages = asyncio.Queue(), []
    await incoming.put({"type": "http.request", "body": b"", "more_body": False})

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "root_path": "",
        "path": f"/api/v1/projects/{project_id}/context/timeline/stream",
        "query_string": b"after=0",
        "headers": [
            (b"host", b"127.0.0.1:5180"),
            *[(key.encode(), value.encode()) for key, value in api.headers.items()],
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 5180),
    }
    request = asyncio.create_task(api.app(scope, incoming.get, send))
    try:
        async with asyncio.timeout(1):
            while not entered.is_set():
                if request.done():
                    request.result()
                    pytest.fail(f"Stream did not reach projection: {messages!r}")
                await asyncio.sleep(0.001)
        await incoming.put({"type": "http.disconnect"})
        # Let the actual streaming response observe disconnect while the worker
        # owns its Session, then allow that read to finish normally.
        await asyncio.sleep(0.01)
        release.set()
        await asyncio.wait_for(request, 1)
        assert any(message.get("status") == 200 for message in messages)
        assert not any(b"tracker-usage" in message.get("body", b"") for message in messages)
        assert engine.pool.checkedout() == 0
    finally:
        release.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("history", [200, 20_000])
def test_cursor_poll_avoids_history_count_and_keeps_explicit_count_consumers(
    api, project_id, history, record_property
):
    engine = api.app.state.engine
    with Session(engine) as session:
        cursor = seed_tracker_event_history(session, project_id, events=history)
        repo = ContextRepository(session)
        statements, plans = [], []

        def observe(connection, _cursor, sql, params, _execution, _many):
            if "project_events" in sql and sql.lstrip().upper().startswith("SELECT"):
                statements.append(sql)
                plans.extend(connection.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params).all())

        event.listen(engine, "before_cursor_execute", observe)
        try:
            for scope in (None, project_id):
                for task_key in (None, "perf-task-0"):
                    page = repo.tracker_status_timeline(
                        project_id=scope, task_key=task_key, after_id=cursor, include_total=False
                    )
                    assert not page.items and page.next_cursor is None
                    assert (
                        repo.latest_tracker_status_event_id(project_id=scope, task_key=task_key)
                        == cursor
                    )
        finally:
            event.remove(engine, "before_cursor_execute", observe)
        assert len(statements) == 8
        assert not any("count(" in sql.lower() for sql in statements)
        assert all("id >" in sql or "DESC" in sql for sql in statements)
        assert not any("TEMP B-TREE" in plan[3] for plan in plans)
        assert any("project_id=? AND id>?" in plan[3] for plan in plans)
        assert all("metadata_json" not in sql.split("FROM")[0] for sql in statements[1::2])
        explicit = repo.tracker_status_timeline(project_id=project_id, after_id=cursor - 2, limit=1)
        assert explicit.total_estimate == history
        assert len(explicit.items) == 1 and explicit.next_cursor == cursor - 1
        record_property("query_plans", json.dumps([tuple(plan) for plan in plans]))


@pytest.mark.asyncio
async def test_sparse_scope_watermark_skips_unmatched_history_without_advancing_status(
    api, project_id
):
    engine = api.app.state.engine
    with Session(engine) as session:
        seed_statistics_workload(session, project_id, tasks=1, receipts=1)
        # Newer events do not belong to this task. The emitted status cursor
        # must stay at zero while the private read watermark covers them once.
        seed_tracker_event_history(session, project_id, events=20_000, task_key="other")
    response = await stream(api, project_id, after=0, task_key="perf-task-0")
    statements = []

    def observe(_connection, _cursor, sql, *_):
        if "project_events" in sql and sql.startswith("SELECT"):
            statements.append(sql)

    try:
        assert "tracker-usage" in await anext(response.body_iterator)
        event.listen(engine, "before_cursor_execute", observe)
        for _ in range(3):
            statements.clear()
            while not statements:
                heartbeat = await anext(response.body_iterator)
                assert '"cursor":0' in heartbeat
            # Only the scalar max ID, never repeated task JSON filtering/history.
            assert len(statements) == 1
            assert "metadata_json" not in statements[0]
        event.remove(engine, "before_cursor_execute", observe)
        with Session(engine) as session:
            expected = seed_tracker_event_history(session, project_id, events=1)
        assert "tracker-usage" in await anext(response.body_iterator)
        assert f"id: {expected}\n" in await anext(response.body_iterator)
    finally:
        if event.contains(engine, "before_cursor_execute", observe):
            event.remove(engine, "before_cursor_execute", observe)
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_read_watermark_drains_pages_and_preserves_future_reconnect_cursor(api, project_id):
    engine = api.app.state.engine
    with Session(engine) as session:
        seed_statistics_workload(session, project_id, tasks=1, receipts=1)
        last = seed_tracker_event_history(session, project_id, events=3)
    response = await stream(api, project_id, after=0, limit=1, max_events=3)
    frames = [frame async for frame in response.body_iterator]
    ids = [
        int(line[4:]) for frame in frames for line in frame.splitlines() if line.startswith("id:")
    ]
    assert ids == list(range(last - 2, last + 1))
    # An after value beyond current history must never regress on an idle read.
    response = await stream(api, project_id, after=last + 100)
    try:
        assert "tracker-usage" in await anext(response.body_iterator)
        with Session(engine) as session:
            seed_tracker_event_history(session, project_id, events=1)
        for _ in range(2):
            assert f'"cursor":{last + 100}' in await asyncio.wait_for(
                anext(response.body_iterator), 1
            )
    finally:
        await response.body_iterator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tasks,receipts,events,mode",
    [(34, 591, 200, "legacy"), (34, 591, 200, "ui"), (340, 5910, 50_000, "ui")],
)
async def test_stream_phase_workload(
    api, project_id, monkeypatch, record_property, tasks, receipts, events, mode
):
    """Reusable raw local counters, not a Cartesian benchmark or percentile claim."""
    from stackos.repositories.tracker import TrackerRepository, agent_stats
    from tests.integration.test_repositories.test_agent_statistics import _receipt

    engine = api.app.state.engine
    with Session(engine) as session:
        seed_statistics_workload(session, project_id, tasks=tasks, receipts=receipts)
        seed_tracker_event_history(session, project_id, events=events)
    statements, projections, decodes, measurements = [], [], [], []
    original_refresh, original_decode = context.usage_refresh, agent_stats.decode_receipt
    owned = threading.local()

    class MeasuredSession(Session):
        def __enter__(self):
            owned.active = True
            return super().__enter__()

        def __exit__(self, *args):
            try:
                return super().__exit__(*args)
            finally:
                owned.active = False

    def refresh(*args, **kwargs):
        projections.append(1)
        return original_refresh(*args, **kwargs)

    def decode(*args, **kwargs):
        if getattr(owned, "active", False):
            decodes.append(1)
        return original_decode(*args, **kwargs)

    def observe(_connection, _cursor, sql, _params, _execution, _many):
        # The real app also runs daemon jobs. Count only this stream operation,
        # including admission and worker SQL, rather than unrelated checkouts.
        if getattr(owned, "active", False):
            statements.append(sql)

    monkeypatch.setattr(context, "usage_refresh", refresh)
    monkeypatch.setattr(agent_stats, "decode_receipt", decode)
    monkeypatch.setattr(context, "Session", MeasuredSession)
    event.listen(engine, "before_cursor_execute", observe)

    async def measured(label, operation):
        statements.clear()
        projections.clear()
        decodes.clear()
        start = time.perf_counter()
        result = await operation()
        measurements.append(
            {
                "phase": label,
                "ms": (time.perf_counter() - start) * 1000,
                "sql": len(statements),
                "event_counts": sum(
                    "count(" in sql.lower() and "project_events" in sql for sql in statements
                ),
                "projections": len(projections),
                "receipt_decodes": len(decodes),
            }
        )
        return result

    async def open_stream(after=None):
        return await context.context_timeline_stream(
            project_id,
            stream_request(),
            task_key="perf-task-0" if mode == "legacy" else None,
            after=after,
            limit=25,
            poll_ms=1,
            heartbeat_ms=1,
            max_events=None,
            replay=False,
            ui_demand=json.dumps({"projects": {str(project_id): {"task_keys": ["perf-task-0"]}}})
            if mode == "ui"
            else None,
            engine=engine,
        )

    try:
        for number in range(3):

            async def initial():
                response = await open_stream()
                return response, await anext(response.body_iterator)

            response, initial_frame = await measured("initial", initial)
            try:
                assert "tracker-usage" in initial_frame

                async def idle_poll(response=response):
                    # The initial poll may have a due heartbeat after its usage
                    # frame. Measure an actual subsequent database poll instead.
                    while True:
                        frame = await anext(response.body_iterator)
                        if any("project_events" in sql for sql in statements):
                            return frame

                idle = await measured("idle", idle_poll)
                assert "heartbeat" in idle
                with Session(engine) as session:
                    TrackerRepository(session).update_ticket(
                        project_id=project_id,
                        ticket_key="perf-ticket-0",
                        patch_json={
                            "completion_evidence_json": {
                                "automatic_delivery": {
                                    "attempt": 1,
                                    "delivery": _receipt(total=40 + number),
                                }
                            }
                        },
                    )
                    cursor = ContextRepository(session).latest_tracker_status_event_id(
                        project_id=project_id
                    )
                update = await measured(
                    "receipt", lambda response=response: anext(response.body_iterator)
                )
                assert "tracker-usage" in update and "\nid:" not in update
                status = await anext(response.body_iterator)
                assert f"id: {cursor}\n" in status
                reconnect = await open_stream(after=cursor)
                try:
                    replacement = await measured(
                        "reconnect", lambda reconnect=reconnect: anext(reconnect.body_iterator)
                    )
                    assert "tracker-usage" in replacement and "\nid:" not in replacement
                finally:
                    await reconnect.body_iterator.aclose()
            finally:
                await response.body_iterator.aclose()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        record_property(
            "stream_workload",
            json.dumps(
                {
                    "tasks": tasks,
                    "receipts": receipts,
                    "events": events,
                    "mode": mode,
                    "measurements": measurements,
                }
            ),
        )
    assert all(item["event_counts"] == 0 for item in measurements)
    assert all(
        item["projections"] == 0 and item["receipt_decodes"] == 0
        for item in measurements
        if item["phase"] == "idle"
    )
    assert engine.pool.checkedout() == 0
