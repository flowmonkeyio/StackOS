"""Real MCP reactivation callers must recheck a cleanup fence at their write owner."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlmodel import Session, select

from stackos.db.models import Run, RunPlan, RunPlanStep, RunPlanStepStatus, RunStatus
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository


def terminal_plan(engine, pid, operation):
    with Session(engine) as session:
        repo = RunPlanRepository(session)
        plan = repo.create(project_id=pid, run_plan_json={
            "schema_version": "stackos.run-plan.v1", "key": "cleanup-reactivation",
            "title": "Cleanup fixture", "steps": [{"id": "work", "title": "Work"}],
        }).data
        started = repo.start(plan.id, project_id=pid).data
        repo.claim_step(
            run_plan_id=plan.id, run_id=started.run_id, step_id="work", claimed_by="fixture"
        )
        repo.record_step(
            run_plan_id=plan.id, run_id=started.run_id, step_id="work",
            status=(RunPlanStepStatus.FAILED if operation == "runPlan.recover"
                    else RunPlanStepStatus.SUCCESS),
            error=("Blocked by tracker graph warnings; daemon rejects blocked status."
                   if operation == "runPlan.recover" else None),
        )
        return plan.id, started.run_id


def snapshot(engine, plan_id, run_id):
    with Session(engine) as session:
        plan = session.get(RunPlan, plan_id)
        run = session.get(Run, run_id)
        steps = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan_id)).all()
        return (
            plan.status, plan.completed_at, plan.metadata_json,
            run.status, run.ended_at, run.error, run.metadata_json,
            [(step.status, step.result_json, step.error, step.completed_at) for step in steps],
        )


def arguments(pid, plan_id, operation):
    result = {
        "project_id": pid, "run_plan_id": plan_id, "step_id": "work",
        "reason": "Recover old daemon blocked-status bug.", "response_mode": "raw",
    }
    if operation == "runPlan.recover":
        result["step_status"] = "blocked"
    return result


@pytest.mark.parametrize("operation", ["tracker.reopen", "runPlan.reopen", "runPlan.recover"])
@pytest.mark.parametrize("prefence", [False, True])
def test_reactivation_rejects_cleanup_after_initial_mcp_check(
    mcp_client, seeded_project, monkeypatch, operation, prefence
):
    target = seeded_project["data"]
    pid = target["id"]
    engine = mcp_client.test_client.app.state.engine
    settings = mcp_client.test_client.app.state.settings
    plan_id, run_id = terminal_plan(engine, pid, operation)
    before = snapshot(engine, plan_id, run_id)
    args = arguments(pid, plan_id, operation)
    with Session(engine) as session:
        foreign = ProjectRepository(session).create(
            slug="foreign-reactivation", name="Foreign", domain="test"
        ).data
    foreign_response = mcp_client.call_tool(operation, {**args, "project_id": foreign.id})
    foreign_result = foreign_response["result"]
    assert foreign_result["isError"]
    assert foreign_result["structuredContent"]["message"] == "NotFoundError"
    assert snapshot(engine, plan_id, run_id) == before

    admitted, proceed = Event(), Event()
    check = ProjectRepository.require_available

    def pause_after_check(repo, project_id, *, reserve=False):
        row = check(repo, project_id, reserve=reserve)
        if project_id == pid and not reserve:
            admitted.set()
            assert proceed.wait(10)
        return row

    def fail_files(pid):
        raise OSError("fixture retains real cleanup fence")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = None
        if prefence:
            monkeypatch.setattr(ProjectRepository, "require_available", pause_after_check)
            future = pool.submit(mcp_client.call_tool, operation, args)
            assert admitted.wait(5)
        try:
            with Session(engine) as session:
                cleanup = ProjectCleanupService(session, settings)
                monkeypatch.setattr(cleanup, "_clean_files", fail_files)
                result = asyncio.run(cleanup.delete(pid, confirmation={
                    "permanent": True, "project_id": pid, "project_slug": target["slug"],
                }))
                assert result.status == "incomplete"
        finally:
            proceed.set()
        envelope = (future.result(timeout=5) if future is not None
                    else mcp_client.call_tool(operation, args))
    response = envelope["result"]
    assert response["isError"]
    assert response["structuredContent"]["message"] == "ConflictError"
    assert snapshot(engine, plan_id, run_id) == before
    with Session(engine) as session:
        row = ProjectRepository(session).get(pid)
        assert not row.is_active and row.cleanup_json["status"] == "incomplete"


@pytest.mark.parametrize("operation", ["tracker.reopen", "runPlan.reopen", "runPlan.recover"])
def test_unfenced_owner_can_reactivate_through_mcp(mcp_client, seeded_project, operation):
    pid = seeded_project["data"]["id"]
    engine = mcp_client.test_client.app.state.engine
    plan_id, run_id = terminal_plan(engine, pid, operation)
    response = mcp_client.call_tool(operation, arguments(pid, plan_id, operation))["result"]
    assert not response.get("isError", False)
    assert response.get("structuredContent")
    with Session(engine) as session:
        assert session.get(Run, run_id).status == RunStatus.RUNNING
        assert session.get(RunPlan, plan_id).status.value == "started"
