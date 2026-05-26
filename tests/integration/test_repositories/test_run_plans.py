"""Repository tests for StackOS run plans and approval gates."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from stackos.context.repository import ContextRepository
from stackos.db.models import (
    ApprovalRequestStatus,
    ContextSnapshot,
    Run,
    RunPlanStepStatus,
    RunStatus,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec


def _run_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "media.launch.run",
        "title": "Launch Media Campaign",
        "approvals": [{"key": "launch-review", "title": "Launch review"}],
        "steps": [
            {
                "id": "create-campaign",
                "title": "Create campaign",
                "approval_refs": ["launch-review"],
                "action_refs": ["meta.campaign.create"],
                "action_payloads": [
                    {"campaign": {"name": "Agent selected"}, "credential_ref": "cred_abc"}
                ],
            }
        ],
    }


def _template(version: str = "0.1.0", *, name: str = "Company Review") -> WorkflowTemplateSpec:
    return WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": "company.review",
            "name": name,
            "version": version,
            "steps": [{"id": "review", "title": "Review"}],
            "outputs": [{"key": "summary", "type": "object"}],
        }
    )


def test_create_run_plan_from_template(session: Session, project_id: int) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            template_key="core.project-memory-review",
        )
        .data
    )

    assert plan.template_key == "core.project-memory-review"
    assert plan.template_snapshot_json["key"] == "core.project-memory-review"
    assert plan.steps
    assert plan.status == "draft"


def test_started_run_plan_keeps_template_snapshot_after_template_changes(
    session: Session,
    project_id: int,
) -> None:
    templates = WorkflowTemplateLoader(session)
    templates.save_project_template(project_id=project_id, spec=_template("0.1.0", name="Old"))
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            template_key="company.review",
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data

    templates.save_project_template(project_id=project_id, spec=_template("0.2.0", name="New"))
    fetched = RunPlanRepository(session).get(started.plan.id)

    assert fetched.template_snapshot_json["name"] == "Old"
    assert fetched.template_version == "0.1.0"


def test_start_links_selected_context_snapshot(session: Session, project_id: int) -> None:
    snapshot = (
        ContextRepository(session)
        .create_snapshot(
            project_id=project_id,
            name="Recent learnings",
            selected_sources_json=[{"source": "learnings", "ids": [1, 2]}],
        )
        .data
    )
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            template_key="core.project-memory-review",
            context_snapshot_id=snapshot.id,
        )
        .data
    )

    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data
    row = session.get(ContextSnapshot, snapshot.id)

    assert started.plan.context_snapshot_id == snapshot.id
    assert row is not None
    assert row.run_id == started.run_id


def test_approval_gate_transition_then_step_completion(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(project_id=project_id, run_plan_json=_run_plan_json()).data
    started = repo.start(plan.id, project_id=project_id).data

    with pytest.raises(ConflictError):
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="create-campaign")

    approved = repo.update(
        run_plan_id=plan.id,
        approval_key="launch-review",
        approval_status=ApprovalRequestStatus.APPROVED,
        decided_by="operator",
        decision_json={"approved": True},
    ).data
    step = repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="create-campaign",
        claimed_by="agent",
    ).data
    completed = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="create-campaign",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"campaign_id": "cmp_123"},
    ).data
    run = session.get(Run, started.run_id)

    assert approved.approval_requests[0].status == "approved"
    assert step.status == "running"
    assert completed.status == "completed"
    assert completed.steps[0].result_json == {"campaign_id": "cmp_123"}
    assert run is not None
    assert run.status == RunStatus.SUCCESS


def test_claimed_step_exposes_static_mcp_tool_grants(session: Session, project_id: int) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "ops.resource-write.run",
            "title": "Resource write",
            "grants": {"step_tools": {"write": ["resource.upsert"]}},
            "steps": [{"id": "write", "title": "Write resource"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data

    step = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="write").data
    fetched = repo.get(plan.id)

    assert step.allowed_tools == ["resource.upsert"]
    assert fetched.steps[0].allowed_tools == ["resource.upsert"]


def test_step_linked_approval_gate_blocks_claim(session: Session, project_id: int) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "media.step-linked-approval.run",
            "title": "Step linked approval",
            "approvals": [
                {
                    "key": "launch-review",
                    "title": "Launch review",
                    "step_id": "create-campaign",
                }
            ],
            "steps": [{"id": "create-campaign", "title": "Create campaign"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data

    with pytest.raises(ConflictError):
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="create-campaign")

    repo.update(
        run_plan_id=plan.id,
        approval_key="launch-review",
        approval_status=ApprovalRequestStatus.APPROVED,
    )
    step = repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="create-campaign",
    ).data

    assert step.status == RunPlanStepStatus.RUNNING


def test_started_plan_cannot_be_started_again_or_return_token(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(project_id=project_id, run_plan_json=_run_plan_json()).data
    first = repo.start(plan.id, project_id=project_id).data

    with pytest.raises(ConflictError) as exc_info:
        repo.start(plan.id, project_id=project_id)

    assert first.run_token
    assert "run_token" not in str(exc_info.value.data)


def test_run_plan_late_writes_reject_secrets(session: Session, project_id: int) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(project_id=project_id, run_plan_json=_run_plan_json()).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.update(
        run_plan_id=plan.id,
        approval_key="launch-review",
        approval_status=ApprovalRequestStatus.APPROVED,
    )
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="create-campaign")

    with pytest.raises(ValidationError) as meta_exc:
        repo.update(run_plan_id=plan.id, metadata_json={"api_key": "secret"})
    with pytest.raises(ValidationError) as decision_exc:
        repo.update(
            run_plan_id=plan.id,
            approval_key="launch-review",
            approval_status=ApprovalRequestStatus.APPROVED,
            decision_json={"authorization": "Bearer abc"},
        )
    with pytest.raises(ValidationError) as result_exc:
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="create-campaign",
            status=RunPlanStepStatus.SUCCESS,
            result_json={"refresh_token": "secret"},
        )

    assert "must not contain secrets" in str(meta_exc.value)
    assert "must not contain secrets" in str(decision_exc.value)
    assert "must not contain secrets" in str(result_exc.value)


def test_explicit_step_claim_enforces_dependencies(session: Session, project_id: int) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "ops.dependency.run",
            "title": "Dependency run",
            "steps": [
                {"id": "first", "title": "First"},
                {"id": "second", "title": "Second", "depends_on": ["first"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data

    with pytest.raises(ConflictError):
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="second")

    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="first")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="first",
        status=RunPlanStepStatus.SUCCESS,
    )
    second = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="second").data

    assert second.status == RunPlanStepStatus.RUNNING


def test_run_plan_allows_only_one_running_step(session: Session, project_id: int) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "ops.single-running-step.run",
            "title": "Single running step",
            "steps": [
                {"id": "first", "title": "First"},
                {"id": "second", "title": "Second"},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data

    first = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="first").data
    with pytest.raises(ConflictError, match="already has a running step") as exc_info:
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="second")

    assert first.status == RunPlanStepStatus.RUNNING
    assert exc_info.value.data["step_id"] == "first"

    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="first",
        status=RunPlanStepStatus.SUCCESS,
    )
    second = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="second").data

    assert second.status == RunPlanStepStatus.RUNNING
