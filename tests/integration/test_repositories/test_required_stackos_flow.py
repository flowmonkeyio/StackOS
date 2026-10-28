"""A tracker task that requires a StackOS flow needs recorded execution."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from stackos.db.models import RunPlanStepStatus, TrackerItemStatus
from stackos.repositories.base import ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository

FLOW = {"flow_key": "postgres.revised", "required_step_ids": ["prepare"]}


def _task(session: Session, project_id: int, key: str = "postgres-migration") -> TrackerRepository:
    repo = TrackerRepository(session)
    repo.create_task(
        project_id=project_id,
        key=key,
        title="Revised Postgres migration",
        source_json={"required_stackos_flow": FLOW},
    )
    return repo


def _plan(session: Session, project_id: int):
    return (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "postgres.revised",
                "title": "Revised Postgres migration",
                "steps": [{"id": "prepare", "title": "Prepare", "success_criteria": ["Evidence"]}],
            },
        )
        .data
    )


def _evidence(plan_id: int, run_id: int) -> dict:
    return {"stackos_flow": {"mode": "run", "run_plan_id": plan_id, "run_id": run_id}}


def test_required_flow_rejects_untracked_and_fabricated_completion(
    session: Session, project_id: int
) -> None:
    repo = _task(session, project_id)
    with pytest.raises(ValidationError, match="StackOS flow"):
        repo.create_task(
            project_id=project_id,
            key="already-complete",
            title="Unexecuted flow",
            status=TrackerItemStatus.COMPLETE,
            source_json={"required_stackos_flow": FLOW},
        )
    with pytest.raises(ValidationError, match="StackOS flow"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"status": "complete", "completion_evidence_json": {"summary": "done"}},
        )
    with pytest.raises(ValidationError, match="run plan"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={
                "status": "complete",
                "completion_evidence_json": _evidence(999999, 999999),
            },
        )
    assert (
        repo.get(project_id=project_id, task_key="postgres-migration").tasks[0].status != "complete"
    )


def test_required_flow_requires_linked_completed_run_and_executed_step(
    session: Session, project_id: int
) -> None:
    repo = _task(session, project_id)
    plan = _plan(session, project_id)
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data
    evidence = _evidence(plan.id, started.run_id)
    with pytest.raises(ValidationError, match="linked"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"status": "complete", "completion_evidence_json": evidence},
        )
    repo.link_run_plan(project_id=project_id, task_key="postgres-migration", run_plan_id=plan.id)
    with pytest.raises(ValidationError, match="completed"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"status": "complete", "completion_evidence_json": evidence},
        )
    RunPlanRepository(session).claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="prepare", claimed_by="codex"
    )
    RunPlanRepository(session).record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Prepared and verified"},
    )
    completed = repo.update_task(
        project_id=project_id,
        task_key="postgres-migration",
        patch_json={"status": "complete", "completion_evidence_json": evidence},
    ).data.task
    assert completed.status == TrackerItemStatus.COMPLETE
    assert completed.completion_evidence_json == evidence


def test_manual_fallback_records_failure_and_cannot_claim_flow_completion(
    session: Session, project_id: int
) -> None:
    repo = _task(session, project_id)
    fallback = {
        "stackos_flow": {
            "mode": "manual_fallback",
            "failure_reason": "StackOS run admission failed",
            "failure_ref": "runPlan.start:denied",
        }
    }
    with pytest.raises(ValidationError, match="failure_reason"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={
                "status": "failed",
                "completion_evidence_json": {
                    "stackos_flow": {
                        "mode": "manual_fallback",
                        "failure_ref": "runPlan.start:denied",
                    }
                },
            },
        )
    failed = repo.update_task(
        project_id=project_id,
        task_key="postgres-migration",
        patch_json={"status": "failed", "completion_evidence_json": fallback},
    ).data.task
    assert failed.status == TrackerItemStatus.FAILED
    assert failed.completion_evidence_json == fallback
    assert (
        repo.get(project_id=project_id, task_key="postgres-migration")
        .tasks[0]
        .completion_evidence_json
        == fallback
    )
    with pytest.raises(ValidationError, match="manual fallback cannot complete"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"status": "complete"},
        )
    with pytest.raises(ValidationError, match="cannot be erased"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"completion_evidence_json": {}},
        )
    with pytest.raises(ValidationError, match="cannot be erased"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"completion_evidence_json": {"stackos_flow": {"mode": "run"}}},
        )


def test_required_flow_failure_can_be_recorded_without_claiming_a_manual_fallback(
    session: Session, project_id: int
) -> None:
    repo = _task(session, project_id)
    failed = repo.update_task(
        project_id=project_id,
        task_key="postgres-migration",
        patch_json={"status": "failed"},
    ).data.task
    assert failed.status == TrackerItemStatus.FAILED
    assert failed.completion_evidence_json is None


def test_link_run_plan_can_atomically_require_selected_steps(
    session: Session, project_id: int
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="postgres-migration", title="Migration")
    plan = _plan(session, project_id)
    with pytest.raises(ValidationError, match="explicit plan step IDs"):
        repo.link_run_plan(
            project_id=project_id,
            task_key="postgres-migration",
            run_plan_id=plan.id,
            require_execution=True,
        )
    linked = repo.link_run_plan(
        project_id=project_id,
        task_key="postgres-migration",
        run_plan_id=plan.id,
        require_execution=True,
        required_step_ids=["prepare"],
    ).data.task
    assert linked.source_json == {"required_stackos_flow": FLOW}
    with pytest.raises(ValidationError, match="StackOS flow"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"status": "complete"},
        )
    runs = RunPlanRepository(session)
    started = runs.start(plan.id, project_id=project_id).data
    runs.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        claimed_by="codex",
    )
    runs.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Recorded preparation"},
    )
    completed = repo.update_task(
        project_id=project_id,
        task_key="postgres-migration",
        patch_json={
            "status": "complete",
            "completion_evidence_json": _evidence(plan.id, started.run_id),
        },
    ).data.task
    assert completed.status == TrackerItemStatus.COMPLETE


@pytest.mark.parametrize(
    "step_status, result_json",
    [
        (RunPlanStepStatus.SUCCESS, None),
        (RunPlanStepStatus.FAILED, {"summary": "Failed"}),
    ],
)
def test_missing_step_evidence_or_failed_run_cannot_complete(
    session: Session,
    project_id: int,
    step_status: RunPlanStepStatus,
    result_json: dict | None,
) -> None:
    repo = _task(session, project_id)
    plan = _plan(session, project_id)
    repo.link_run_plan(project_id=project_id, task_key="postgres-migration", run_plan_id=plan.id)
    runs = RunPlanRepository(session)
    started = runs.start(plan.id, project_id=project_id).data
    runs.claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="prepare", claimed_by="codex"
    )
    runs.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=step_status,
        result_json=result_json,
    )
    with pytest.raises(ValidationError, match=r"completed|required steps"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={
                "status": "complete",
                "completion_evidence_json": _evidence(plan.id, started.run_id),
            },
        )
    assert (
        repo.get(project_id=project_id, task_key="postgres-migration").tasks[0].status != "complete"
    )


def test_required_flow_cannot_be_removed_or_bypassed_by_ticket_rollup(
    session: Session, project_id: int
) -> None:
    repo = _task(session, project_id)
    with pytest.raises(ValidationError, match="immutable"):
        repo.update_task(
            project_id=project_id,
            task_key="postgres-migration",
            patch_json={"source_json": {}},
        )
    with pytest.raises(ValidationError, match="StackOS flow"):
        repo.patch(
            project_id=project_id,
            patch_json={"tasks": {"postgres-migration": {"status": "complete"}}},
        )
    repo.create_ticket(
        project_id=project_id,
        task_key="postgres-migration",
        key="postgres-work",
        title="Do migration work",
    )
    with pytest.raises(ValidationError, match="StackOS flow"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="postgres-work",
            patch_json={"status": "complete"},
        )
