"""Run-plan completion waits for the step's durable background action calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    Run,
    RunPlan,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
)
from stackos.mcp.permissions import active_run_plan_step
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository


def _running_plan(session: Session, project_id: int, *, key: str = "pending-actions") -> RunPlan:
    repo = RunPlanRepository(session)
    created = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": key,
            "title": "Wait for background work",
            "steps": [
                {"id": "work", "title": "Perform work"},
                {"id": "later", "title": "Review work"},
            ],
        },
    ).data
    started = repo.start(created.id, project_id=project_id).data
    repo.claim_step(run_plan_id=created.id, run_id=started.run_id, step_id="work")
    plan = session.get(RunPlan, created.id)
    assert plan is not None
    return plan


def _action(
    session: Session,
    plan: RunPlan,
    *,
    status: ActionCallStatus = ActionCallStatus.RUNNING,
    step_id: str = "work",
    project_id: int | None = None,
) -> ActionCall:
    step = session.exec(
        select(RunPlanStep).where(
            RunPlanStep.run_plan_id == plan.id,
            RunPlanStep.step_id == step_id,
        )
    ).one()
    call = ActionCall(
        project_id=project_id if project_id is not None else plan.project_id,
        run_id=plan.run_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        action_key="work.perform",
        plugin_slug="background-test",
        operation="work.perform",
        status=status,
    )
    session.add(call)
    session.commit()
    session.refresh(call)
    return call


def _state(session: Session, plan: RunPlan) -> dict:
    """Capture lifecycle, frozen grants, audit heartbeat, and tracker mirror."""
    session.refresh(plan)
    run = session.get(Run, plan.run_id)
    assert run is not None
    session.refresh(run)
    return {
        "plan": plan.model_dump(),
        "run": run.model_dump(),
        "steps": [
            step.model_dump()
            for step in session.exec(
                select(RunPlanStep)
                .where(RunPlanStep.run_plan_id == plan.id)
                .order_by(RunPlanStep.position)
            )
        ],
        "tracker": TrackerRepository(session)
        .get(project_id=plan.project_id, task_key=f"workflow-{plan.id}", include_graph=False)
        .model_dump(),
    }


@pytest.mark.parametrize("status", [RunPlanStepStatus.SUCCESS, RunPlanStepStatus.SKIPPED])
def test_record_step_rejects_pending_actions_without_changing_state_or_grants(
    session: Session, project_id: int, status: RunPlanStepStatus
) -> None:
    plan = _running_plan(session, project_id)
    first = _action(session, plan)
    second = _action(session, plan)
    before = _state(session, plan)
    repo = RunPlanRepository(session)

    with pytest.raises(ValidationError, match="action calls are still running") as exc_info:
        repo.record_step(
            run_plan_id=plan.id,
            run_id=plan.run_id,
            project_id=project_id,
            step_id="work",
            status=status,
            result_json={"summary": "Premature result"},
        )

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["step_id"] == "work"
    assert exc_info.value.data["action_call_ids"] == [first.id, second.id]
    assert exc_info.value.data["pending_actions"] == [
        {
            "action_call_id": call.id,
            "poll_operation": "actionCall.get",
            "poll_arguments": {"action_call_id": call.id},
        }
        for call in (first, second)
    ]
    assert exc_info.value.data["next_operations"] == ["actionCall.get", "runPlan.recordStep"]
    assert _state(session, plan) == before
    _, active_step = active_run_plan_step(
        SimpleNamespace(run=session.get(Run, plan.run_id), run_id=plan.run_id, session=session),
        "action.execute",
    )
    assert active_step.step_id == "work"

    for call in (first, second):
        call.status = ActionCallStatus.SUCCESS
        session.add(call)
    session.commit()
    recorded = repo.record_step(
        run_plan_id=plan.id,
        run_id=plan.run_id,
        step_id="work",
        status=status,
        result_json={"summary": "Background work has completed"},
    ).data
    assert recorded.steps[0].status == status


@pytest.mark.parametrize("scope", ["project", "plan", "step", "direct"])
def test_pending_actions_outside_current_step_do_not_block_completion(
    session: Session, project_id: int, scope: str
) -> None:
    plan = _running_plan(session, project_id)
    if scope == "project":
        other_project = (
            ProjectRepository(session)
            .create(slug="other", name="Other Project", domain="other.example", locale="en-US")
            .data
        )
        _action(session, plan, project_id=other_project.id)
    elif scope == "plan":
        other_plan = _running_plan(session, project_id, key="other-plan")
        _action(session, other_plan)
    elif scope == "step":
        _action(session, plan, step_id="later")
    else:
        direct = _action(session, plan)
        direct.run_plan_id = None
        direct.run_plan_step_id = None
        session.add(direct)
        session.commit()

    recorded = (
        RunPlanRepository(session)
        .record_step(
            run_plan_id=plan.id,
            run_id=plan.run_id,
            step_id="work",
            status=RunPlanStepStatus.SUCCESS,
        )
        .data
    )

    assert recorded.steps[0].status == RunPlanStepStatus.SUCCESS


@pytest.mark.parametrize(
    "action_status", [ActionCallStatus.SUCCESS, ActionCallStatus.FAILED, ActionCallStatus.DRY_RUN]
)
def test_terminal_action_does_not_decide_step_outcome(
    session: Session, project_id: int, action_status: ActionCallStatus
) -> None:
    plan = _running_plan(session, project_id)
    _action(session, plan, status=action_status)

    recorded = (
        RunPlanRepository(session)
        .record_step(
            run_plan_id=plan.id,
            run_id=plan.run_id,
            step_id="work",
            status=RunPlanStepStatus.SUCCESS,
        )
        .data
    )

    assert recorded.steps[0].status == RunPlanStepStatus.SUCCESS


@pytest.mark.parametrize("status", [RunPlanStepStatus.FAILED, RunPlanStepStatus.BLOCKED])
def test_pending_action_does_not_prevent_explicit_failure_or_blocker(
    session: Session, project_id: int, status: RunPlanStepStatus
) -> None:
    plan = _running_plan(session, project_id)
    call = _action(session, plan)

    recorded = (
        RunPlanRepository(session)
        .record_step(
            run_plan_id=plan.id,
            run_id=plan.run_id,
            step_id="work",
            status=status,
            error="Operator requested recovery",
        )
        .data
    )

    assert recorded.steps[0].status == status
    assert recorded.status == (
        RunPlanStatus.FAILED if status == RunPlanStepStatus.FAILED else RunPlanStatus.STARTED
    )
    session.refresh(call)
    assert call.status == ActionCallStatus.RUNNING


def test_pending_action_does_not_prevent_explicit_run_plan_abort(
    session: Session, project_id: int
) -> None:
    plan = _running_plan(session, project_id)
    call = _action(session, plan)

    aborted = (
        RunPlanRepository(session)
        .abort(run_plan_id=plan.id, project_id=project_id, reason="Operator ended this run")
        .data
    )

    assert aborted.status == RunPlanStatus.ABORTED
    run = session.get(Run, plan.run_id)
    assert run is not None
    assert run.status == RunStatus.ABORTED
    session.refresh(call)
    assert call.status == ActionCallStatus.RUNNING
