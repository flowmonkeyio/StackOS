"""Future-only native desktop admission using the real daemon scheduler."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.executors.base import run_coroutine_job
from sqlmodel import Session

from stackos.config import Settings
from stackos.db.models import Project, ScheduledJob
from stackos.jobs.scheduler import build_scheduler
from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime


@pytest.fixture
def timing(engine, tmp_path):
    clock = [datetime(2030, 1, 1, 12, 0, tzinfo=UTC)]
    with Session(engine) as session:
        project = Project(slug="cron-timing", name="Timing", domain="example.test", locale="en-US")
        session.add(project)
        session.flush()
        row = ScheduledJob(
            project_id=project.id,
            kind="proof",
            cron_expr="* * * * *",
            enabled=True,
            name="Proof",
            timezone="UTC",
            template_key="proof.flow",
            inputs_json={},
            execution_json={
                "project_dir": str(tmp_path),
                "delivery_agent": {"instructions": "proof"},
            },
        )
        session.add(row)
        session.commit()
        row_id = row.id
    scheduler = build_scheduler(Settings(), engine)
    launches = []

    async def launch(row, due, admit):
        if admit():
            launches.append((row.id, due))

    runtime = WorkflowScheduleRuntime(
        scheduler, lambda: Session(engine), launch=launch, now=lambda: clock[0]
    )
    return clock, scheduler, runtime, launches, row_id


@pytest.mark.asyncio
async def test_future_only_and_generation_fence(timing):
    clock, scheduler, runtime, launches, row_id = timing
    scheduler.start(paused=True)
    try:
        scheduler.add_job(lambda: None, "interval", minutes=5, id="maintenance", jobstore="memory")
        assert len(scheduler.get_jobs()) == 1  # daemon alone admits nothing
        first = runtime.connect()
        old = scheduler.get_job(f"workflow-schedule-{row_id}")
        assert old.next_run_time == clock[0] + timedelta(minutes=1)
        assert old.misfire_grace_time == 1
        assert old.max_instances == 1
        clock[0] = old.next_run_time
        await old.func(**old.kwargs)
        assert launches == [(row_id, clock[0])]
        admitted = list(launches)
        runtime.disconnect(first)
        assert [j.id for j in scheduler.get_jobs()] == ["maintenance"]
        clock[0] += timedelta(minutes=10)
        second = runtime.connect()
        new = scheduler.get_job(f"workflow-schedule-{row_id}")
        assert new.next_run_time == clock[0] + timedelta(minutes=1)
        runtime.disconnect(first)  # delayed old cleanup cannot clear new presence
        await old.func(**old.kwargs)
        assert launches == admitted
        assert scheduler.get_job(new.id) is new
        clock[0] = new.next_run_time
        await new.func(**new.kwargs)
        assert launches == [*admitted, (row_id, clock[0])]
        runtime.disconnect(second)
        assert len(launches) == 2  # admitted work is never cancelled
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["disable", "delete", "edit"])
async def test_queued_callback_rechecks_authoritative_row(timing, engine, mutation):
    clock, scheduler, runtime, launches, row_id = timing
    scheduler.start(paused=True)
    try:
        runtime.connect()
        job = scheduler.get_job(f"workflow-schedule-{row_id}")
        with Session(engine) as session:
            row = session.get(ScheduledJob, row_id)
            if mutation == "delete":
                session.delete(row)
            else:
                if mutation == "disable":
                    row.enabled = False
                else:
                    row.inputs_json = {"changed": True}
                session.add(row)
            session.commit()
        clock[0] = job.next_run_time
        await job.func(**job.kwargs)
        assert launches == []
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_late_tick_and_final_admission_guard(timing):
    clock, scheduler, runtime, launches, row_id = timing
    scheduler.start(paused=True)
    try:
        connection = runtime.connect()
        job = scheduler.get_job(f"workflow-schedule-{row_id}")
        clock[0] = job.next_run_time + timedelta(seconds=2)
        await job.func(**job.kwargs)
        assert launches == []
        future = scheduler.get_job(job.id)
        assert future.next_run_time > clock[0]

        async def disconnect_during_preflight(row, due, admit):
            runtime.disconnect(connection)
            clock[0] += timedelta(minutes=10)
            runtime.connect()
            assert not admit()

        runtime.launch = disconnect_during_preflight
        clock[0] = future.next_run_time
        await future.func(**future.kwargs)
        assert launches == []
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_project_cleanup_during_preflight_disarms_future_and_late_admission(
    timing, engine, tmp_path
):
    from stackos.project_cleanup import ProjectCleanupService

    clock, scheduler, runtime, launches, row_id = timing
    settings = Settings(data_dir=tmp_path / "cleanup-data", state_dir=tmp_path / "cleanup-state")
    settings.ensure_dirs()
    scheduler.start(paused=True)
    try:
        runtime.connect()
        job = scheduler.get_job(f"workflow-schedule-{row_id}")

        async def cleanup_during_preflight(row, due, admit):
            with Session(engine) as session:
                session.info["operation_services"] = {"schedule_reconcile": runtime.reconcile}
                project = session.get(Project, row.project_id)
                result = await ProjectCleanupService(session, settings).delete(
                    project.id,
                    confirmation={
                        "permanent": True,
                        "project_id": project.id,
                        "project_slug": project.slug,
                    },
                )
                assert result.status == "deleted"
            assert not admit()

        runtime.launch = cleanup_during_preflight
        clock[0] = job.next_run_time
        await job.func(**job.kwargs)
        assert launches == []
        assert scheduler.get_job(job.id) is None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_canonical_mutation_hook_removes_only_affected_timer(timing, engine):
    from stackos.repositories.projects import ScheduledJobRepository

    _clock, scheduler, runtime, _launches, row_id = timing
    scheduler.start(paused=True)
    try:
        runtime.connect()
        scheduler.add_job(lambda: None, "interval", minutes=5, id="backup", jobstore="memory")
        with Session(engine) as session:
            session.info["operation_services"] = {"schedule_reconcile": runtime.reconcile}
            row = session.get(ScheduledJob, row_id)
            project_id = row.project_id
            ScheduledJobRepository(session).toggle(row_id, project_id=project_id, enabled=False)
            assert scheduler.get_job(f"workflow-schedule-{row_id}") is None
            assert scheduler.get_job("backup") is not None
            row.enabled = True
            session.add(row)
            session.commit()
            runtime.reconcile(project_id=project_id, job_id=row_id)
            assert scheduler.get_job(f"workflow-schedule-{row_id}") is not None
            ScheduledJobRepository(session).remove(row_id, project_id=project_id)
            assert scheduler.get_job(f"workflow-schedule-{row_id}") is None
            assert scheduler.get_job("backup") is not None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_real_executor_misfire_rearms_future_without_launch(timing, monkeypatch):
    clock, scheduler, runtime, launches, row_id = timing
    scheduler.start(paused=True)
    try:
        runtime.connect()
        job = scheduler.get_job(f"workflow-schedule-{row_id}")
        due = job.next_run_time
        clock[0] = due + timedelta(seconds=2)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]

        monkeypatch.setattr("apscheduler.executors.base.datetime", Clock)
        events = await run_coroutine_job(job, "memory", [due], "test.workflow-cron")
        assert [e.code for e in events] == [EVENT_JOB_MISSED]
        missed = events[0]
        assert launches == []
        scheduler._dispatch_event(missed)
        future = scheduler.get_job(job.id)
        assert future.next_run_time == datetime(2030, 1, 1, 12, 2, tzinfo=UTC)
        clock[0] = future.next_run_time
        events = await run_coroutine_job(future, "memory", [clock[0]], "test.workflow-cron")
        assert [e.code for e in events] == [EVENT_JOB_EXECUTED]
        assert launches == [(row_id, clock[0])]
        # A late event for an older job must leave the new registration alone.
        current = scheduler.get_job(job.id)
        scheduler._dispatch_event(missed)
        assert scheduler.get_job(job.id) is current
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_reconcile_during_preflight_fences_even_identical_reenable(timing):
    clock, scheduler, runtime, launches, row_id = timing
    scheduler.start(paused=True)
    try:
        runtime.connect()
        job = scheduler.get_job(f"workflow-schedule-{row_id}")

        async def preflight(row, due, admit):
            runtime.reconcile(project_id=row.project_id, job_id=row_id)
            assert not admit()

        runtime.launch = preflight
        clock[0] = job.next_run_time
        await job.func(**job.kwargs)
        assert launches == []
        assert scheduler.get_job(job.id).next_run_time > clock[0]
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disconnect", "cancel"])
async def test_native_stream_actual_asgi_disconnect_and_cancel(settings, mode):
    from stackos.server import create_app

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        messages = asyncio.Queue()
        await messages.put({"type": "http.request", "body": b"", "more_body": False})
        ready = asyncio.Event()
        status = []

        async def send(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])
            if message["type"] == "http.response.body" and b"event: ready" in message.get(
                "body", b""
            ):
                ready.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "method": "POST",
            "path": "/api/v1/desktop/availability",
            "raw_path": b"/api/v1/desktop/availability",
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "http_version": "1.1",
            "server": ("127.0.0.1", 5180),
            "client": ("127.0.0.1", 12345),
            "headers": [
                (b"host", b"127.0.0.1:5180"),
                (b"authorization", f"Bearer {app.state.token}".encode()),
            ],
        }
        request = asyncio.create_task(app(scope, messages.get, send))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            assert status == [200]
            assert app.state.workflow_schedules.connection is not None
            if mode == "disconnect":
                await messages.put({"type": "http.disconnect"})
                await asyncio.wait_for(request, 5)
            else:
                request.cancel()
                await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), 5)
            assert app.state.workflow_schedules.connection is None
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)


def test_availability_requires_native_auth_and_has_no_agent_or_browser_setter(settings):
    from fastapi.testclient import TestClient

    from stackos.operations.registry import build_operation_registry
    from stackos.repositories.base import NotFoundError
    from stackos.server import create_app

    registry = build_operation_registry()
    spec = registry.get("desktop.availability", surface="rest")
    assert spec.surfaces.rest.browser_safe is False
    assert spec.response_policy.allowed_modes == ("raw",)
    for surface in ("mcp", "cli"):
        with pytest.raises(NotFoundError):
            registry.get(spec.name, surface=surface)
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:5180") as client:
        path = "/api/v1/desktop/availability"
        assert client.post(path).status_code == 401
        ui = {"Authorization": f"Bearer {app.state.ui_token}"}
        assert client.post(path, headers=ui).status_code == 403
        # A UI GET must not turn a browser stream into native availability.
        assert client.get(path, headers=ui).status_code == 404
        assert app.state.workflow_schedules.connection is None
