"""Tests for RunRepository — start, finish, abort cascade, reap_stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from stackos.db.models import Run, RunKind, RunPlan, RunPlanStep, RunPlanStepStatus, RunStatus
from stackos.repositories.base import ConflictError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def test_start_finish_happy_path(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    assert env.data.status == RunStatus.RUNNING
    out = repo.finish(env.data.id, status="success")
    assert out.data.status == RunStatus.SUCCESS
    assert out.data.ended_at is not None


def test_finish_terminal_run_raises(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    repo.finish(env.data.id, status="success")
    with pytest.raises(ConflictError):
        repo.finish(env.data.id, status="failed")


def test_heartbeat_updates_timestamp(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    out = repo.heartbeat(env.data.id)
    assert out.data.heartbeat_at is not None


def test_heartbeat_missing_run_is_idempotent(session: Session) -> None:
    repo = RunRepository(session)
    out = repo.heartbeat(99999)
    assert out.run_id is None


def test_abort_cascade(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    parent = repo.start(project_id=project_id, kind=RunKind.RUN_PLAN).data
    child1 = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=parent.id).data
    child2 = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=parent.id).data
    grandchild = repo.start(
        project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=child1.id
    ).data
    repo.abort(parent.id, cascade=True)
    assert repo.get(parent.id).status == RunStatus.ABORTED
    assert repo.get(child1.id).status == RunStatus.ABORTED
    assert repo.get(child2.id).status == RunStatus.ABORTED
    assert repo.get(grandchild.id).status == RunStatus.ABORTED


def test_reap_stale_orphans(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    fresh = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    stale = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    # Manually backdate the stale heartbeat past the 5-minute threshold.
    from stackos.db.models import Run

    s_row = session.get(Run, stale.id)
    assert s_row is not None
    s_row.heartbeat_at = _now() - timedelta(minutes=10)
    session.add(s_row)
    session.commit()

    n = repo.reap_stale(stale_after_seconds=300)
    assert n == 1
    assert repo.get(stale.id).status == RunStatus.ABORTED
    assert repo.get(stale.id).error == "daemon-restart-orphan"
    assert repo.get(fresh.id).status == RunStatus.RUNNING


def test_reap_stale_preserves_started_run_plan(session: Session, project_id: int) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "stale.live.workflow.run",
                "title": "Stale Live Workflow",
                "steps": [
                    {"id": "prepare", "title": "Prepare"},
                    {"id": "deliver", "title": "Deliver"},
                ],
            },
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data

    run = session.get(Run, started.run_id)
    plan_row = session.get(RunPlan, plan.id)
    assert run is not None and plan_row is not None
    run.heartbeat_at = _now() - timedelta(minutes=10)
    plan_row.updated_at = _now() - timedelta(minutes=10)
    session.add(run)
    session.add(plan_row)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    assert reaped == 0
    run = session.get(Run, started.run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING
    assert run.error is None
    assert run.heartbeat_at is not None
    assert run.heartbeat_at > _now() - timedelta(minutes=1)
    plan_row = session.get(RunPlan, plan.id)
    assert plan_row is not None
    assert plan_row.status == "started"
    steps = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).all()
    assert {step.status for step in steps} == {RunPlanStepStatus.PENDING}
    tracker_snapshot = TrackerRepository(session).get(project_id=project_id, run_plan_id=plan.id)
    assert tracker_snapshot.tasks[0].status == "in-progress"
    assert {ticket.status for ticket in tracker_snapshot.tickets} == {"not-started"}


def test_reap_stale_preserves_started_run_plan_with_recent_activity(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "recent-plan.workflow.run",
                "title": "Recent Plan Workflow",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data
    run = session.get(Run, started.run_id)
    plan_row = session.get(RunPlan, plan.id)
    assert run is not None and plan_row is not None
    run.heartbeat_at = _now() - timedelta(minutes=10)
    plan_row.updated_at = _now()
    session.add(run)
    session.add(plan_row)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    assert reaped == 0
    run_out = RunRepository(session).get(started.run_id)
    assert run_out.status == RunStatus.RUNNING
    assert run_out.heartbeat_at is not None
    assert run_out.heartbeat_at > _now() - timedelta(minutes=1)


def test_reap_stale_parent_reconciles_child_run_plan(
    session: Session,
    project_id: int,
) -> None:
    parent = RunRepository(session).start(project_id=project_id, kind=RunKind.RUN_PLAN).data
    child_plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "child.workflow.run",
                "title": "Child Workflow",
                "steps": [{"id": "child-step", "title": "Child Step"}],
            },
        )
        .data
    )
    child_started = RunPlanRepository(session).start(child_plan.id, project_id=project_id).data
    parent_run = session.get(Run, parent.id)
    child_run = session.get(Run, child_started.run_id)
    assert parent_run is not None and child_run is not None
    parent_run.heartbeat_at = _now() - timedelta(minutes=10)
    child_run.parent_run_id = parent.id
    session.add(parent_run)
    session.add(child_run)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    assert reaped == 1
    child_plan_row = session.get(RunPlan, child_plan.id)
    child_run_row = session.get(Run, child_started.run_id)
    assert child_plan_row is not None and child_run_row is not None
    assert child_run_row.status == RunStatus.ABORTED
    assert child_plan_row.status == "aborted"
    assert child_plan_row.metadata_json is not None
    assert child_plan_row.metadata_json["abort_reason"] == "parent-aborted"


def test_reap_stale_historical_aborted_parent_reconciles_live_child_run_plan(
    session: Session,
    project_id: int,
) -> None:
    parent = RunRepository(session).start(project_id=project_id, kind=RunKind.RUN_PLAN).data
    child_plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "historical-child.workflow.run",
                "title": "Historical Child Workflow",
                "steps": [{"id": "child-step", "title": "Child Step"}],
            },
        )
        .data
    )
    child_started = RunPlanRepository(session).start(child_plan.id, project_id=project_id).data
    parent_run = session.get(Run, parent.id)
    child_run = session.get(Run, child_started.run_id)
    assert parent_run is not None and child_run is not None
    parent_run.status = RunStatus.ABORTED
    parent_run.error = "daemon-restart-orphan"
    parent_run.ended_at = _now() - timedelta(minutes=5)
    child_run.parent_run_id = parent.id
    child_run.heartbeat_at = _now()
    session.add(parent_run)
    session.add(child_run)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    child_plan_row = session.get(RunPlan, child_plan.id)
    child_run_row = session.get(Run, child_started.run_id)
    assert reaped == 0
    assert child_plan_row is not None and child_run_row is not None
    assert child_run_row.status == RunStatus.ABORTED
    assert child_run_row.error == "parent-aborted"
    assert child_plan_row.status == "aborted"
    assert child_plan_row.metadata_json is not None
    assert child_plan_row.metadata_json["abort_reason"] == "parent-aborted"


def test_finish_rejects_live_run_plan_audit_run(session: Session, project_id: int) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "finish-live.workflow.run",
                "title": "Finish Live Workflow",
                "steps": [{"id": "write", "title": "Write"}],
            },
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data

    with pytest.raises(ConflictError) as exc_info:
        RunRepository(session).finish(started.run_id, status="success")

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["run_plan_status"] == "started"
    assert "runPlan.recordStep" in exc_info.value.data["next_operations"]
    assert session.get(Run, started.run_id).status == RunStatus.RUNNING
    assert session.get(RunPlan, plan.id).status == "started"


def test_cost_aggregation(session: Session, project_id: int) -> None:
    """Cost rollup sums run_steps.cost_cents per kind for a month."""
    from stackos.db.models import RunStep, RunStepStatus

    repo = RunRepository(session)
    run_a = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    run_b = repo.start(project_id=project_id, kind=RunKind.ACTION).data

    # Insert run_steps with cost.
    step1 = RunStep(
        run_id=run_a.id,
        step_index=0,
        skill_name="outline",
        status=RunStepStatus.SUCCESS,
        cost_cents=120,
    )
    step2 = RunStep(
        run_id=run_b.id,
        step_index=0,
        skill_name="action",
        status=RunStepStatus.SUCCESS,
        cost_cents=45,
    )
    session.add(step1)
    session.add(step2)
    session.commit()

    month = _now().strftime("%Y-%m")
    cost = repo.cost(project_id, month=month)
    assert cost["total_cents"] == 165
    assert cost["by_kind_cents"][RunKind.SKILL_RUN.value] == 120
    assert cost["by_kind_cents"][RunKind.ACTION.value] == 45
