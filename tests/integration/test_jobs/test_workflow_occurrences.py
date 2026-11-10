"""Cron admission composes real request/plan/tracker owners; only the host is stubbed."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from stackos.config import Settings
from stackos.db.models import AgentRequest, Run, RunPlan, ScheduledJob, TrackerTask
from stackos.host_mcp.result import AgentRunResult
from stackos.jobs.scheduler import build_scheduler
from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.projects import ProjectRepository, ScheduledJobRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec
from tests.integration.test_repositories.test_task_delivery_sources import source


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def project_id(session):
    return (
        ProjectRepository(session)
        .create(slug="occurrence", name="Occurrence proof", domain="example.test", locale="en-US")
        .data.id
    )


@pytest.fixture
def occurrence(session, project_id, engine, tmp_path, monkeypatch):
    monkeypatch.setattr("stackos.task_delivery.agent_selection_error", lambda *a, **k: None)
    spec = WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": "proof.harmless",
            "name": "Harmless preparation",
            "version": "0.1.0",
            "inputs": [{"key": "goal", "type": "string", "required": True}],
            # Deliberately list the dependent first: admission must use readiness.
            "steps": [
                {"id": "gather", "title": "Gather", "depends_on": ["shape"]},
                {"id": "shape", "title": "Shape", "instructions": ["Prepare gather children."]},
            ],
        }
    )
    WorkflowTemplateLoader(session).save_project_template(project_id=project_id, spec=spec)
    roles = {
        role: {
            "source_ref": source(
                session, project_id, tmp_path, role, "Execute only the assigned harmless contract."
            )
        }
        for role in ("delivery_agent", "review_agent")
    }
    TrackerRepository(session).configure_delivery(project_id=project_id, delivery=roles)
    row = (
        ScheduledJobRepository(session)
        .set(
            project_id=project_id,
            kind="harmless",
            name="Daily harmless work",
            cron_expr="* * * * *",
            timezone="UTC",
            template_key=spec.key,
            inputs_json={"goal": "Synthetic input"},
            execution_json={"project_dir": str(tmp_path), "agents": roles},
            enabled=True,
        )
        .data
    )
    clock = [datetime(2030, 1, 1, tzinfo=UTC)]
    scheduler = build_scheduler(Settings(), engine)
    runtime = WorkflowScheduleRuntime(scheduler, lambda: Session(engine), now=lambda: clock[0])
    runtime.connect()
    return row.id, runtime, scheduler, clock, spec


async def fire(occurrence):
    row_id, runtime, _, clock, _ = occurrence
    registration = runtime.registrations[row_id]
    clock[0] = registration.due
    await runtime._fire(row_id=row_id, registration=registration)


@pytest.mark.asyncio
async def test_occurrence_runs_first_ready_planning_children_and_remaining_work(
    occurrence, session, project_id, engine, monkeypatch
):
    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="unrelated", title="Unrelated")
    untouched = (
        repo.get(project_id=project_id, task_key="unrelated").tasks[0].model_dump(mode="json")
    )
    calls = []

    def host(_host, _directory, instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        ticket = packet["brief"]["ticket"]
        calls.append((ticket["key"], packet["phase"]))
        if packet["workflow_step"] and packet["workflow_step"]["step_id"] == "shape":
            assert "contract-declared child" in instructions
            plan_id = ticket["run_plan_id"]
            with Session(engine) as writer:
                plans = RunPlanRepository(writer)
                parent = plans.get_step(plan_id, "gather", project_id=project_id)
                TrackerRepository(writer).create_ticket(
                    project_id=project_id,
                    task_key=ticket["task_key"],
                    key="prepared-child",
                    title="Prepared harmless child",
                    parent_ticket_key=f"workflow-{plan_id}-gather",
                    dependency_keys=[f"workflow-{plan_id}-gather"],
                    run_plan_id=plan_id,
                    run_plan_step_id=parent.id,
                )
        return AgentRunResult(
            exit_code=0,
            thread_id=f"native-edge-{len(calls)}",
            result={"status": "success", "summary": "Checked", "outputs": "{}", "followups": []},
        )

    monkeypatch.setattr(task_delivery, "run_agent", host)
    await fire(occurrence)
    row_id, _, scheduler, _, _ = occurrence
    session.expire_all()
    row = session.get(ScheduledJob, row_id)
    request = AgentRequestRepository(session).get(
        project_id=project_id, request_id=row.last_request_id
    )
    plan = RunPlanRepository(session).get(request.run_plan_id, project_id=project_id)
    assert request.request_key == f"schedule:{row_id}:2030-01-01T00:01:00+00:00"
    assert request.metadata_json["schedule_name"] == "Daily harmless work"
    assert plan.inputs_json == {"goal": "Synthetic input"}
    assert [(s.step_id, s.status) for s in plan.steps] == [
        ("gather", "pending"),
        ("shape", "running"),
    ]
    jobs = [job for job in scheduler.get_jobs() if job.id.startswith("task-delivery:")]
    assert len(jobs) == 1
    jobs[0].func(**jobs[0].kwargs)
    session.expire_all()
    assert (
        repo.get(project_id=project_id, task_key=f"workflow-{plan.id}")
        .tasks[0]
        .metadata_json["delivery"]["reason"]
        is None
    )
    assert RunPlanRepository(session).get(plan.id, project_id=project_id).status == "completed", (
        repo.get(project_id=project_id, task_key=f"workflow-{plan.id}").tasks[0].metadata_json,
        calls,
    )
    assert calls == [
        (f"workflow-{plan.id}-shape", "delivery"),
        ("prepared-child", "delivery"),
        ("prepared-child", "review"),
        (f"workflow-{plan.id}-gather", "delivery"),
    ]
    state = (
        repo.get(project_id=project_id, task_key=f"workflow-{plan.id}")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["delivery_agent"]["instructions"] == "Execute only the assigned harmless contract."
    assert state["step_id"] == "shape"
    assert (
        repo.get(project_id=project_id, task_key="unrelated").tasks[0].model_dump(mode="json")
        == untouched
    )
    assert not (Path(state["project_dir"]) / ".git").exists()

    # Completion permits a new due key, while replay of the old one remains inert.
    await fire(occurrence)
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == 2
    assert len(session.exec(select(RunPlan)).all()) == 2


@pytest.mark.asyncio
async def test_duplicate_and_overlap_never_prepare_or_restart(occurrence, session, project_id):
    await fire(occurrence)
    row_id, runtime, scheduler, clock, _ = occurrence
    session.expire_all()
    row = session.get(ScheduledJob, row_id)
    assert row.last_request_id is not None
    request = session.get(AgentRequest, row.last_request_id)
    # An expired request lease is unrelated to the unfinished canonical plan/task.
    request.claim_expires_at = datetime(2000, 1, 1)
    session.add(request)
    session.commit()
    due = clock[0]
    await runtime.launch(row, due, lambda: True)
    await fire(occurrence)
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert len(session.exec(select(RunPlan)).all()) == 1
    assert len(session.exec(select(Run)).all()) == 1
    assert len([job for job in scheduler.get_jobs() if job.id.startswith("task-delivery:")]) == 1
    assert session.get(ScheduledJob, row_id).last_run_status == "overlap"
    assert request.run_plan_id is not None


@pytest.mark.asyncio
async def test_approval_blocks_without_bypass_or_lease_based_overlap(
    occurrence, session, project_id
):
    row_id, _, scheduler, _, spec = occurrence
    updated = spec.model_dump(mode="json")
    updated["version"] = "0.2.0"
    updated["approval_gates"] = [{"key": "operator", "required_when": "before work"}]
    updated["steps"][1]["approval_refs"] = ["operator"]
    WorkflowTemplateLoader(session).save_project_template(
        project_id=project_id, spec=WorkflowTemplateSpec.model_validate(updated)
    )
    await fire(occurrence)
    session.expire_all()
    row = session.get(ScheduledJob, row_id)
    request = session.get(AgentRequest, row.last_request_id)
    assert row.last_run_status == "blocked"
    assert "approval" in request.metadata_json["schedule_error"]
    plan = RunPlanRepository(session).get(request.run_plan_id, project_id=project_id)
    assert plan.status == "started"
    assert all(step.status == "pending" for step in plan.steps)
    assert plan.approval_requests[0].status == "pending"
    assert not [job for job in scheduler.get_jobs() if job.id.startswith("task-delivery:")]
    await fire(occurrence)
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert session.get(ScheduledJob, row_id).last_run_status == "overlap"


@pytest.mark.asyncio
async def test_role_overrides_preserve_partial_values_and_explicit_null(
    occurrence, session, project_id, tmp_path
):
    row_id, runtime, _, _, _ = occurrence
    repo = TrackerRepository(session)
    repo.configure_delivery(
        project_id=project_id,
        delivery={
            role: {
                "source_ref": source(
                    session, project_id, tmp_path, role, "Current prepared method"
                ),
                "model": "project-model",
            }
            for role in ("delivery_agent", "review_agent")
        },
    )
    row = session.get(ScheduledJob, row_id)
    config = json.loads(json.dumps(runtime._config(row)))
    config["execution_json"]["delivery_agent"] = {"model": None}
    ScheduledJobRepository(session).set(project_id=project_id, **config)
    # Re-arm after this explicit settings edit, as the daemon reconciliation seam does.
    runtime.reconcile(project_id=project_id, job_id=row_id)
    await fire(occurrence)
    session.expire_all()
    request = session.get(AgentRequest, session.get(ScheduledJob, row_id).last_request_id)
    state = (
        repo.get(project_id=project_id, task_key=f"workflow-{request.run_plan_id}")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert "model" not in state["delivery_agent"]
    assert state["delivery_agent"]["instructions"] == "Current prepared method"
    assert state["review_agent"]["model"] == "project-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["input", "host", "guard"])
async def test_preflight_and_final_admission_create_no_work(
    occurrence, session, monkeypatch, failure
):
    row_id, runtime, _, clock, _ = occurrence
    row = session.get(ScheduledJob, row_id)
    if failure == "input":
        row.inputs_json = {}
        session.add(row)
        session.commit()
    elif failure == "host":
        monkeypatch.setattr(
            "stackos.task_delivery.agent_selection_error", lambda *a, **k: "Host unavailable"
        )
    due = clock[0] + timedelta(minutes=1)
    clock[0] = due
    await runtime.launch(row, due, lambda: failure != "guard")
    session.expire_all()
    for model in (AgentRequest, RunPlan, Run, TrackerTask):
        assert session.exec(select(model)).all() == []
    assert session.get(ScheduledJob, row_id).last_run_status == (
        None if failure == "guard" else "unavailable"
    )
    if failure != "guard":
        assert ScheduledJobRepository(session).list(row.project_id)[0].setup_issue is not None


@pytest.mark.asyncio
async def test_existing_unprepared_due_key_is_not_claimed(occurrence, session, project_id):
    row_id, _, _, _, _ = occurrence
    existing = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key=f"schedule:{row_id}:2030-01-01T00:01:00+00:00",
            title="Existing unprepared occurrence",
            source_kind="schedule",
        )
        .data
    )
    await fire(occurrence)
    session.expire_all()
    assert session.get(AgentRequest, existing.id).status == "new"
    assert session.exec(select(RunPlan)).all() == []
    assert session.get(ScheduledJob, row_id).last_request_id is None


@pytest.mark.asyncio
async def test_handoff_failure_stays_visible_and_blocks_later_ticks(
    occurrence, session, project_id, monkeypatch
):
    row_id, _, scheduler, _, _ = occurrence
    add_job = scheduler.add_job

    def unavailable(*args, **kwargs):
        if kwargs.get("id", "").startswith("task-delivery:"):
            raise RuntimeError("Synthetic scheduler handoff failure")
        return add_job(*args, **kwargs)

    monkeypatch.setattr(scheduler, "add_job", unavailable)
    await fire(occurrence)
    session.expire_all()
    request = session.get(AgentRequest, session.get(ScheduledJob, row_id).last_request_id)
    plan = RunPlanRepository(session).get(request.run_plan_id, project_id=project_id)
    assert plan.status == "started"
    assert session.get(ScheduledJob, row_id).last_run_status == "blocked"
    assert request.metadata_json["schedule_error"] == "Synthetic scheduler handoff failure"
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key=f"workflow-{plan.id}")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["status"] == "stopped"
    await fire(occurrence)
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert session.get(ScheduledJob, row_id).last_run_status == "overlap"
