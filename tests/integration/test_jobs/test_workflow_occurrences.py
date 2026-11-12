"""Real request/native registry lifecycle; only the native adapter is substituted."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import AgentRequest, Run, RunPlan, ScheduledJob, TrackerTask
from stackos.host_mcp import service
from stackos.host_mcp.agent_execution import InvocationCancelled
from stackos.host_mcp.result import AgentRunResult, usage_snapshot
from stackos.jobs.scheduler import build_scheduler
from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.projects import ProjectRepository, ScheduledJobRepository
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot
from tests.integration.test_repositories.test_schedule_main_thread import bind_folder
from tests.integration.test_repositories.test_schedule_main_thread import (
    native_ready as native_ready,
)


@pytest.fixture
def occurrence(engine, tmp_path, monkeypatch, native_ready):
    database = make_engine(tmp_path / "occurrence.sqlite")
    restore_database_snapshot(database, capture_database_snapshot(engine))
    with Session(database) as session:
        project = (
            ProjectRepository(session)
            .create(slug="occurrence", name="Proof", domain="proof.test")
            .data
        )
        bind_folder(session, project.id, tmp_path)
        row = (
            ScheduledJobRepository(session)
            .set(
                project_id=project.id,
                kind="proof",
                name="Main proof",
                goal="Benign project goal",
                cron_expr="* * * * *",
                timezone="UTC",
                enabled=True,
            )
            .data
        )
    clock = [datetime(2030, 1, 1, tzinfo=UTC)]
    scheduler = build_scheduler(Settings(), database)
    runtime = WorkflowScheduleRuntime(scheduler, lambda: Session(database), now=lambda: clock[0])
    runtime.connect()
    calls = []

    def runner(directory, instructions, prompt, schema, on_session, **options):
        on_session(f"native-main-{len(calls) + 1}")
        calls.append((str(directory), instructions, json.loads(prompt), schema, options))
        return AgentRunResult(
            0,
            thread_id=f"native-main-{len(calls)}",
            result={"status": "success", "summary": "Observed benign result", "run_plan_id": None},
            usage=usage_snapshot("proof", "invocation", 37, 6),
        )

    monkeypatch.setattr(service, "agent_selection_error", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    try:
        yield row.id, project.id, runtime, clock, database, calls
    finally:
        service.shutdown_usage()
        database.dispose()


async def fire(occurrence):
    row_id, _, runtime, clock, _, _ = occurrence
    registration = runtime.registrations[row_id]
    clock[0] = registration.due
    await runtime._fire(row_id=row_id, registration=registration)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", [None, "core.project-memory-review"])
async def test_one_main_invocation_request_history_and_no_plan(occurrence, workflow):
    row_id, project_id, runtime, clock, engine, calls = occurrence
    with Session(engine) as session:
        row = session.get(ScheduledJob, row_id)
        row.template_key = workflow
        session.add(row)
        session.commit()
        runtime.reconcile(project_id=project_id, job_id=row_id)
    await fire(occurrence)
    with Session(engine) as session:
        request = session.exec(select(AgentRequest)).one()
        assert request.status == "resolved" and request.run_plan_id is None
        for model in (RunPlan, Run, TrackerTask):
            assert session.exec(select(model)).all() == []
        row = session.get(ScheduledJob, row_id)
        await runtime._launch(row, clock[0], lambda: True)
        assert len(calls) == 1
        history = ScheduledJobRepository(session).runs(project_id=project_id).items[0]
        assert history.native_execution.thread_id == "native-main-1"
        assert history.status == "resolved" and history.summary == "Observed benign result"
        assert history.run_plan_id is None and history.task_key is None and history.run_id is None
        assert history.usage["measured"]["total"] == 43
        assert history.agent_stats is None and history.active_execution is None
        assert history.work_duration is None
        assert history.duration_seconds == (history.ended_at - history.started_at).total_seconds()
        assert "claim_token" not in history.model_dump_json()
        assert "instructions" not in history.model_dump_json()
        assert "prompt" not in history.model_dump_json()
        ScheduledJobRepository(session).remove(row_id, project_id=project_id)
        assert (
            ScheduledJobRepository(session).runs(project_id=project_id).items[0].request_id
            == request.id
        )
    assert calls[0][2]["template_key"] == workflow
    assert calls[0][2]["goal"] == "Benign project goal"
    assert "claim_token" not in json.dumps(calls[0][2])
    assert not {"model", "provider", "profile", "native_settings"} & calls[0][4].keys()
    assert "repository" in calls[0][1] and "main agent" in calls[0][1]


@pytest.mark.asyncio
async def test_live_request_beyond_lease_blocks_overlap_and_retains_real_liveness(
    occurrence, monkeypatch
):
    from stackos.repositories import agent_requests

    row_id, project_id, _runtime, _clock, engine, calls = occurrence
    entered, finish = Event(), Event()
    original = service._definition_by_key("codex").run_agent

    def runner(*args, **kwargs):
        kwargs["on_session"]("native-main-1")
        entered.set()
        assert finish.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    task = asyncio.create_task(fire(occurrence))
    assert await asyncio.to_thread(entered.wait, 10)
    try:
        with Session(engine) as session:
            request = session.exec(select(AgentRequest)).one()
            now = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1200)
            monkeypatch.setattr(agent_requests, "_utcnow", lambda: now)
            history = ScheduledJobRepository(session).runs(project_id=project_id).items[0]
            assert history.active_execution.thread_id == "native-main-1"
            await fire(occurrence)
            assert session.exec(select(AgentRequest)).all() == [request]
            session.expire_all()
            assert session.get(ScheduledJob, row_id).last_run_status == "overlap"
    finally:
        finish.set()
        await task
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["new", "claimed", "run-created"])
async def test_unresolved_or_orphan_admission_blocks_without_plan_or_lease(occurrence, status):
    row_id, project_id, _, _, engine, calls = occurrence
    with Session(engine) as session:
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key="older",
                title="Interrupted admission",
                source_kind="schedule",
                metadata_json={"schedule_id": row_id},
            )
            .data
        )
        row = session.get(AgentRequest, request.id)
        row.status = status
        row.claim_expires_at = datetime(2000, 1, 1)
        session.add(row)
        session.commit()
    await fire(occurrence)
    assert not calls
    with Session(engine) as session:
        assert session.get(ScheduledJob, row_id).last_run_status == "overlap"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["blocked", "failed", "crash", "launch-error"])
async def test_failures_are_truthful_no_replay_and_blocked_remains_open(
    occurrence, monkeypatch, outcome
):
    row_id, _project_id, runtime, clock, engine, calls = occurrence

    def runner(*args, **kwargs):
        calls.append(1)
        if outcome == "launch-error":
            raise RuntimeError("Launch failed")
        kwargs["on_session"]("native-failure")
        if outcome == "crash":
            return AgentRunResult(7, thread_id="native-failure", error="Native crash")
        return AgentRunResult(
            0,
            thread_id="native-failure",
            result={
                "status": outcome,
                "summary": "Needs attention",
                "run_plan_id": None,
            },
        )

    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    await fire(occurrence)
    with Session(engine) as session:
        request = session.exec(select(AgentRequest)).one()
        assert request.status == ("claimed" if outcome == "blocked" else "failed")
        row = session.get(ScheduledJob, row_id)
        await runtime._launch(row, clock[0], lambda: True)
        assert len(calls) == 1
        assert request.run_plan_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["disable", "delete", "edit", "disconnect", "folder"])
async def test_native_spawn_guard_fences_changes_after_admission(occurrence, monkeypatch, mutation):
    row_id, _project_id, runtime, _, engine, _ = occurrence
    spawned = []

    def runner(*args, process_control, **kwargs):
        if mutation == "disconnect":
            runtime.disconnect(runtime.connection)
        elif mutation == "folder":
            with Session(engine) as session:
                project = ProjectRepository(session).get(_project_id)
                from pathlib import Path

                changed = Path(project.project_dir) / "changed-folder"
                changed.mkdir()
                bind_folder(session, _project_id, changed)
        else:
            with Session(engine) as session:
                row = session.get(ScheduledJob, row_id)
                if mutation == "delete":
                    session.delete(row)
                else:
                    row.enabled = mutation != "disable"
                    row.goal = "Changed"
                    session.add(row)
                session.commit()
        with pytest.raises(InvocationCancelled, match="cancelled"):
            process_control.spawn([sys.executable, "-c", "print('must not run')"])
            spawned.append(True)
        return AgentRunResult(None, error="Fenced before spawn")

    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    await fire(occurrence)
    assert not spawned
    with Session(engine) as session:
        assert session.exec(select(AgentRequest)).one().status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("delay_boundary", ["registration", "spawn"])
async def test_admitted_occurrence_survives_native_preparation_delay(
    occurrence, monkeypatch, delay_boundary
):
    row_id, _project_id, runtime, clock, engine, _calls = occurrence
    spawned = []
    run_agent = service.run_agent

    def delayed_registration(*args, **kwargs):
        if delay_boundary == "registration":
            clock[0] += timedelta(seconds=2)
        return run_agent(*args, **kwargs)

    def runner(*args, process_control, on_session, **kwargs):
        if delay_boundary == "spawn":
            clock[0] += timedelta(seconds=2)
        process = process_control.spawn([sys.executable, "-c", "pass"])
        assert process.wait(timeout=10) == 0
        spawned.append(True)
        on_session("delayed-native-ready")
        return AgentRunResult(
            0,
            thread_id="delayed-native-ready",
            result={"status": "success", "summary": "Completed", "run_plan_id": None},
            usage=usage_snapshot("proof", "invocation", 37, 6),
        )

    monkeypatch.setattr(service, "run_agent", delayed_registration)
    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    due = runtime.registrations[row_id].due
    await fire(occurrence)
    with Session(engine) as session:
        request = session.exec(select(AgentRequest)).one()
        assert request.status == "resolved", request.metadata_json
        native = request.metadata_json["native_execution"]
        assert native["execution"]["thread_id"] == "delayed-native-ready"
        assert native["usage"]["measured"]["total"] == 43
        assert not native["cancelled"]
        await runtime._launch(session.get(ScheduledJob, row_id), due, lambda: True)
        assert len(session.exec(select(AgentRequest)).all()) == 1
    assert spawned == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_actual_plan_link_is_project_checked_and_unfinished_plan_blocks(
    occurrence, monkeypatch, foreign
):
    from stackos.repositories import agent_requests
    from stackos.repositories.run_plans import RunPlanRepository

    row_id, project_id, _runtime, _clock, engine, calls = occurrence
    plans = []

    def runner(*args, on_session, **kwargs):
        on_session("native-with-plan")
        with Session(engine) as session:
            target = project_id
            if foreign:
                target = (
                    ProjectRepository(session)
                    .create(slug="other", name="Other", domain="other.test")
                    .data.id
                )
            plan = (
                RunPlanRepository(session)
                .create(
                    project_id=target,
                    run_plan_json={
                        "key": "agent-created",
                        "title": "Real plan",
                        "steps": [{"id": "work", "title": "Work"}],
                    },
                )
                .data
            )
            plans.append(plan.id)
        # Final native handoff/link retains the original owner beyond its lease.
        later = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1200)
        monkeypatch.setattr(agent_requests, "_utcnow", lambda: later)
        calls.append(1)
        return AgentRunResult(
            0,
            thread_id="native-with-plan",
            result={
                "status": "success",
                "summary": "Created real plan",
                "run_plan_id": plan.id,
            },
        )

    monkeypatch.setattr(service, "_definition_by_key", lambda _: SimpleNamespace(run_agent=runner))
    await fire(occurrence)
    with Session(engine) as session:
        request = session.exec(select(AgentRequest)).one()
        history = ScheduledJobRepository(session).runs(project_id=project_id).items[0]
        assert request.run_plan_id == (None if foreign else plans[0])
        assert history.run_plan_id == (None if foreign else plans[0])
        assert history.work_duration is None
        assert history.duration_seconds == (history.ended_at - history.started_at).total_seconds()
        assert request.status == ("failed" if foreign else "resolved")
        if foreign:
            assert "project" in history.error
        else:
            assert history.task_key == f"workflow-{plans[0]}"
    if not foreign:
        await fire(occurrence)
        assert len(calls) == 1
        with Session(engine) as session:
            assert session.get(ScheduledJob, row_id).last_run_status == "overlap"
