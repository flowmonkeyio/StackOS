"""Repository tests for StackOS run plans and approval gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from stackos.context.repository import ContextRepository
from stackos.db.models import (
    ApprovalRequestStatus,
    ContextSnapshot,
    Run,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
    TrackerItemStatus,
    TrackerTicket,
)
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.permissions import active_run_plan_step
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.events import (
    TRACKER_TASK_STATUS_CHANGED,
    TRACKER_TICKET_STATUS_CHANGED,
)
from stackos.repositories.tracker.workflow import workflow_step_ticket_key
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


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


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
            inputs_json={"goal": "Review recent project memory."},
        )
        .data
    )

    assert plan.template_key == "core.project-memory-review"
    assert plan.key == "core.project-memory-review.run"
    assert plan.template_snapshot_json["key"] == "core.project-memory-review"
    assert plan.steps
    assert plan.status == "draft"
    clarify = next(step for step in plan.steps if step.step_id == "clarify-goal")
    assert clarify.expected_outputs_json["context_summary"]["description"] == (
        "Compact summary of relevant evidence with provenance."
    )
    assert clarify.expected_outputs_json["context_summary"]["schema_json"]["required"] == [
        "current_facts",
        "active_work",
        "stale_candidates",
        "gaps",
        "source_refs",
    ]
    artifact_grants = [
        grant
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
        if "artifact.create" in grant.get("tools", [])
    ]
    assert artifact_grants == []


def test_create_run_plan_requires_template_required_inputs(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)

    with pytest.raises(ValidationError, match="goal"):
        repo.create(project_id=project_id, template_key="core.project-memory-review")

    structural = repo.validate_plan(
        project_id=project_id,
        template_key="core.project-memory-review",
    )
    concrete = repo.validate_plan(
        project_id=project_id,
        template_key="core.project-memory-review",
        enforce_required_inputs=True,
    )
    with_inputs = repo.validate_plan(
        project_id=project_id,
        template_key="core.project-memory-review",
        inputs_json={"goal": "Review project memory."},
        enforce_required_inputs=True,
    )

    assert structural.valid is True
    assert concrete.valid is False
    assert "goal" in concrete.errors[0].message
    assert with_inputs.valid is True


def test_create_run_plan_applies_project_workflow_extension(
    session: Session,
    project_id: int,
) -> None:
    base = WorkflowTemplateLoader(session).describe_template(
        project_id=project_id,
        key="communications.customer-feedback-intake",
        plugin_slug="communications",
        include_extension=False,
    )
    steps = [step.model_dump(mode="json") for step in base.spec.steps]
    canonical_step_override = next(
        step for step in steps if step["id"] == "establish-canonical-thread"
    )
    canonical_step_override["title"] = "Establish Project Canonical Thread"
    WorkflowTemplateLoader(session).upsert_extension(
        project_id=project_id,
        workflow_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        required_input_keys_json=["feedback_summary", "communication_route_ref"],
        input_defaults_json={
            "communication_route_ref": "communication-route:support-feedback",
            "canonical_slack_target_ref": "communication-target:support-triage",
        },
        selected_context_json={
            "communication": {
                "route_ref": "communication-route:support-feedback",
                "target_ref": "communication-target:support-triage",
            }
        },
        step_overrides_json={
            "establish-canonical-thread": {
                "extra_instructions": [
                    "Use the project extension route and target refs before copying "
                    "non-Slack feedback."
                ]
            }
        },
        template_overrides_json={
            "description": "Project-specific support intake run.",
            "steps": steps,
        },
    )

    repo = RunPlanRepository(session)
    with pytest.raises(ValidationError, match="feedback_summary"):
        repo.create(
            project_id=project_id,
            template_key="communications.customer-feedback-intake",
            plugin_slug="communications",
        )

    plan = repo.create(
        project_id=project_id,
        template_key="communications.customer-feedback-intake",
        plugin_slug="communications",
        inputs_json={"feedback_summary": "Telegram report includes a screenshot."},
        selected_context_json={"operator": {"instruction_source": "same-thread"}},
    ).data
    canonical_step = next(
        step for step in plan.steps if step.step_id == "establish-canonical-thread"
    )

    assert plan.inputs_json["feedback_summary"] == "Telegram report includes a screenshot."
    assert plan.inputs_json["communication_route_ref"] == "communication-route:support-feedback"
    assert plan.goal == "Project-specific support intake run."
    assert plan.selected_context_json == {
        "communication": {
            "route_ref": "communication-route:support-feedback",
            "target_ref": "communication-target:support-triage",
        },
        "operator": {"instruction_source": "same-thread"},
    }
    assert plan.metadata_json["workflow_extension"]["workflow_key"] == (
        "communications.customer-feedback-intake"
    )
    assert "template_overrides_json" in plan.metadata_json["workflow_extension"]
    assert canonical_step.title == "Establish Project Canonical Thread"
    assert "project extension route" in " ".join(canonical_step.instructions_json)


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
            inputs_json={"goal": "Review recent project memory."},
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
    task_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TASK_STATUS_CHANGED,
        )
        .items
    )
    ticket_events = (
        ContextRepository(session)
        .timeline(
            project_id=project_id,
            event_type=TRACKER_TICKET_STATUS_CHANGED,
        )
        .items
    )
    assert task_events[-1].metadata_json["task_key"] == f"workflow-{plan.id}"
    assert task_events[-1].metadata_json["new_status"] == "complete"
    ticket_key = workflow_step_ticket_key(plan.id, "create-campaign")
    mirrored_ticket = session.exec(
        select(TrackerTicket).where(TrackerTicket.key == ticket_key)
    ).one()
    step_ticket_events = [
        event for event in ticket_events if event.metadata_json["ticket_key"] == ticket_key
    ]
    assert [event.metadata_json["new_status"] for event in step_ticket_events] == [
        "in-progress",
        "complete",
    ]
    assert [event.run_id for event in step_ticket_events] == [
        started.run_id,
        started.run_id,
    ]
    assert [event.source_type for event in step_ticket_events] == [
        "tracker_ticket",
        "tracker_ticket",
    ]
    assert [event.source_id for event in step_ticket_events] == [
        mirrored_ticket.id,
        mirrored_ticket.id,
    ]
    assert step_ticket_events[-1].metadata_json["schema_version"] == 1


def test_successful_step_result_must_match_frozen_expected_outputs(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "validate-output.run",
            "title": "Validate output",
            "steps": [
                {
                    "id": "produce-report",
                    "title": "Produce report",
                    "output_refs": ["report"],
                    "expected_outputs": {
                        "report": {
                            "key": "report",
                            "type": "object",
                            "required": True,
                            "schema_json": {
                                "type": "object",
                                "required": ["count"],
                                "properties": {"count": {"type": "integer", "minimum": 0}},
                            },
                        }
                    },
                }
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="produce-report",
    )

    with pytest.raises(ValidationError, match="expected outputs") as missing:
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="produce-report",
            status=RunPlanStepStatus.SUCCESS,
            result_json={},
        )
    assert missing.value.data["issues"][0]["path"] == "$.report"

    with pytest.raises(ValidationError, match="expected outputs") as nested_missing:
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="produce-report",
            status=RunPlanStepStatus.SUCCESS,
            result_json={"report": {}},
        )
    assert nested_missing.value.data["issues"][0]["path"] == "$.report.count"

    with pytest.raises(ValidationError, match="expected outputs") as invalid:
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="produce-report",
            status=RunPlanStepStatus.SUCCESS,
            result_json={"report": {"count": -1}},
        )
    assert invalid.value.data["issues"][0]["path"] == "$.report.count"
    running = session.exec(
        select(RunPlanStep).where(
            RunPlanStep.run_plan_id == plan.id,
            RunPlanStep.step_id == "produce-report",
        )
    ).one()
    assert running.status == RunPlanStepStatus.RUNNING
    assert running.result_json is None

    completed = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="produce-report",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"report": {"count": 1}},
    ).data

    assert completed.status == RunPlanStatus.COMPLETED


def test_failed_step_does_not_require_success_outputs(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "failed-output.run",
            "title": "Failed output",
            "steps": [
                {
                    "id": "produce-report",
                    "title": "Produce report",
                    "output_refs": ["report"],
                    "expected_outputs": {
                        "report": {
                            "key": "report",
                            "type": "object",
                            "required": True,
                            "schema_json": {"type": "object"},
                        }
                    },
                }
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="produce-report",
    )

    failed = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="produce-report",
        status=RunPlanStepStatus.FAILED,
        error="provider failed",
    ).data

    assert failed.status == RunPlanStatus.FAILED


def test_reopen_completed_run_plan_revives_run_and_tracker_mirror(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "reopen.workflow.run",
            "title": "Reopen workflow run",
            "steps": [
                {"id": "scope-work", "title": "Scope work"},
                {"id": "deliver-tickets", "title": "Deliver tickets"},
                {"id": "verify-delivery", "title": "Verify delivery"},
                {"id": "release-closeout", "title": "Release closeout"},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data

    for step_id in (
        "scope-work",
        "deliver-tickets",
        "verify-delivery",
        "release-closeout",
    ):
        repo.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step_id,
            claimed_by="agent",
        )
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step_id,
            status=RunPlanStepStatus.SUCCESS,
            result_json={"summary": f"{step_id} complete"},
        )

    closed_run = session.get(Run, started.run_id)
    assert repo.get(plan.id).status == RunPlanStatus.COMPLETED
    assert closed_run is not None
    assert closed_run.status == RunStatus.SUCCESS
    assert closed_run.ended_at is not None

    reopened = repo.reopen(
        run_plan_id=plan.id,
        project_id=project_id,
        reason="More follow-up work was found after closeout.",
        actor="codex",
    ).data
    run = session.get(Run, started.run_id)
    step_rows = {
        step.step_id: step
        for step in session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id))
    }

    assert reopened.plan.status == RunPlanStatus.STARTED
    assert reopened.plan.completed_at is None
    assert reopened.run_id == started.run_id
    assert reopened.run_token == started.run_token
    assert reopened.reopened_step_id == "deliver-tickets"
    assert reopened.reset_step_ids == [
        "deliver-tickets",
        "verify-delivery",
        "release-closeout",
    ]
    assert run is not None
    assert run.status == RunStatus.RUNNING
    assert run.ended_at is None
    assert run.error is None
    assert step_rows["scope-work"].status == RunPlanStepStatus.SUCCESS
    assert step_rows["deliver-tickets"].status == RunPlanStepStatus.PENDING
    assert step_rows["verify-delivery"].status == RunPlanStepStatus.PENDING
    assert step_rows["release-closeout"].status == RunPlanStepStatus.PENDING

    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    task = snapshot.tasks[0]
    tickets = {ticket.key: ticket for ticket in snapshot.tickets}
    assert task.status == TrackerItemStatus.IN_PROGRESS
    assert task.completed_at is None
    assert tickets[f"workflow-{plan.id}-scope-work"].status == TrackerItemStatus.COMPLETE
    assert tickets[f"workflow-{plan.id}-deliver-tickets"].status == (TrackerItemStatus.NOT_STARTED)
    assert tickets[f"workflow-{plan.id}-verify-delivery"].status == (TrackerItemStatus.NOT_STARTED)

    claimed = repo.claim_step(
        run_plan_id=plan.id,
        run_id=reopened.run_id,
        step_id=reopened.reopened_step_id,
        claimed_by="agent",
    ).data
    assert claimed.status == RunPlanStepStatus.RUNNING


def test_blocked_step_keeps_run_plan_recoverable(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recoverable.blocked.run",
            "title": "Recoverable blocked run",
            "steps": [{"id": "graph-check", "title": "Graph check"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="graph-check",
        claimed_by="agent",
    )

    blocked = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="graph-check",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"blocking_issue": "tracker graph has warnings"},
        error="tracker-graph-warning",
    ).data
    run = session.get(Run, started.run_id)

    assert blocked.status == RunPlanStatus.STARTED
    assert blocked.steps[0].status == RunPlanStepStatus.BLOCKED
    assert blocked.steps[0].completed_at is None
    assert run is not None
    assert run.status == RunStatus.RUNNING
    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    blocked_ticket = next(
        ticket for ticket in snapshot.tickets if ticket.key.endswith("graph-check")
    )
    assert blocked_ticket.status == "in-progress"
    assert blocked_ticket.blocker_reason == "tracker-graph-warning"
    assert blocked_ticket.outcome == "blocked: tracker-graph-warning"

    reclaimed = repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="graph-check",
        claimed_by="agent",
    ).data
    completed = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="graph-check",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "graph repaired"},
    ).data

    assert reclaimed.status == RunPlanStepStatus.RUNNING
    assert completed.status == RunPlanStatus.COMPLETED


def test_record_step_success_allows_open_workflow_child_warnings(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "workflow.child.closeout.run",
            "title": "Workflow child closeout run",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    step = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="plan").data

    TrackerRepository(session).create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=workflow_step_ticket_key(plan.id, "plan"),
        key="plan-child",
        title="Plan child",
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        dependency_keys=[workflow_step_ticket_key(plan.id, "plan")],
    )

    completed = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="plan",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "closing step; child remains visible in tracker"},
    ).data

    assert completed.status == RunPlanStatus.COMPLETED
    assert completed.steps[0].status == RunPlanStepStatus.SUCCESS
    after = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=True,
    )
    assert after.graph is not None
    assert any(
        f"Workflow step {workflow_step_ticket_key(plan.id, 'plan')} is complete while" in warning
        for warning in after.graph.warnings
    )
    child = next(ticket for ticket in after.tickets if ticket.key == "plan-child")
    assert child.status == TrackerItemStatus.NOT_STARTED


def test_claim_step_allows_missing_terminal_child_handoff_warning(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "workflow.child.handoff.run",
            "title": "Workflow child handoff run",
            "steps": [
                {"id": "plan", "title": "Plan"},
                {"id": "deliver", "title": "Deliver", "depends_on": ["plan"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    plan_step = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="plan").data
    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=workflow_step_ticket_key(plan.id, "plan"),
        key="plan-terminal-child",
        title="Plan terminal child",
        run_plan_id=plan.id,
        run_plan_step_id=plan_step.id,
        dependency_keys=[workflow_step_ticket_key(plan.id, "plan")],
    )
    tracker.update_ticket(
        project_id=project_id,
        ticket_key="plan-terminal-child",
        patch_json={"status": "in-progress"},
    )
    tracker.update_ticket(
        project_id=project_id,
        ticket_key="plan-terminal-child",
        patch_json={"status": "complete", "completion_evidence_json": {"summary": "done"}},
    )
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="plan",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "plan child complete"},
    )

    snapshot = tracker.get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=True,
    )
    assert snapshot.graph is not None
    deliver_ticket_key = workflow_step_ticket_key(plan.id, "deliver")
    assert any(
        (
            f"Workflow step {deliver_ticket_key} depends on prior step "
            f"{workflow_step_ticket_key(plan.id, 'plan')} but not its terminal child tickets"
        )
        in warning
        for warning in snapshot.graph.warnings
    )

    deliver = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="deliver").data

    assert deliver.status == RunPlanStepStatus.RUNNING


def test_claim_step_rejects_incomplete_transitive_dependency(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "workflow.transitive.claim.run",
            "title": "Workflow transitive claim run",
            "steps": [
                {"id": "scope", "title": "Scope"},
                {"id": "discover", "title": "Discover", "depends_on": ["scope"]},
                {"id": "plan", "title": "Plan", "depends_on": ["discover"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="scope")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="scope",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "scope ok"},
    )
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="discover")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="discover",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "discover ok"},
    )

    # Simulate legacy graph repair reopening an ancestor after a later direct
    # dependency was already marked successful.
    scope = session.exec(
        select(RunPlanStep).where(
            RunPlanStep.run_plan_id == plan.id,
            RunPlanStep.step_id == "scope",
        )
    ).one()
    scope.status = RunPlanStepStatus.BLOCKED
    scope.error = "legacy graph repair needed"
    scope.completed_at = None
    session.add(scope)
    session.commit()

    with pytest.raises(ConflictError, match="dependencies are not complete") as exc_info:
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="plan")

    assert exc_info.value.data["missing"] == ["scope"]

    plan_step = session.exec(
        select(RunPlanStep).where(
            RunPlanStep.run_plan_id == plan.id,
            RunPlanStep.step_id == "plan",
        )
    ).one()
    plan_step.status = RunPlanStepStatus.RUNNING
    plan_step.started_at = _now()
    session.add(plan_step)
    session.commit()

    with pytest.raises(ConflictError, match="dependencies are not complete"):
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="plan",
            status=RunPlanStepStatus.SUCCESS,
            result_json={"summary": "incorrectly succeeding out of order"},
        )


def test_recover_blocked_step_with_incomplete_dependencies_to_pending(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "workflow.out-of-order.recover.run",
            "title": "Workflow out-of-order recover run",
            "steps": [
                {"id": "scope", "title": "Scope"},
                {"id": "discover", "title": "Discover", "depends_on": ["scope"]},
                {"id": "plan", "title": "Plan", "depends_on": ["discover"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    steps = {
        step.step_id: step
        for step in session.exec(
            select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)
        ).all()
    }
    steps["scope"].status = RunPlanStepStatus.BLOCKED
    steps["scope"].error = "legacy graph repair needed"
    steps["scope"].started_at = _now()
    steps["discover"].status = RunPlanStepStatus.SUCCESS
    steps["discover"].completed_at = _now()
    steps["plan"].status = RunPlanStepStatus.BLOCKED
    steps["plan"].error = "claimed out of order before upstream repair finished"
    steps["plan"].started_at = _now()
    for step in steps.values():
        session.add(step)
    session.commit()
    TrackerRepository(session).mirror_run_plan_recovered(
        plan=repo._fetch_plan(plan.id),
        steps=list(steps.values()),
        actor="legacy",
    )
    session.commit()

    check = repo.check_consistency(plan.id, project_id=project_id)
    assert any(
        issue.code == "step-progress-incomplete-dependencies"
        and issue.step_id == "plan"
        and issue.data["repairable"] is True
        for issue in check.issues
    )

    recovered = repo.recover(
        run_plan_id=plan.id,
        project_id=project_id,
        step_id="plan",
        step_status=RunPlanStepStatus.PENDING,
        reason="out-of-order blocked step repair",
        actor="codex",
    ).data

    assert recovered.steps[2].status == RunPlanStepStatus.PENDING
    assert recovered.steps[2].error is None
    assert recovered.steps[2].claimed_at is None
    assert recovered.steps[2].started_at is None
    run = session.get(Run, started.run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING
    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    tickets = {ticket.key: ticket for ticket in snapshot.tickets}
    assert tickets[workflow_step_ticket_key(plan.id, "plan")].status == "not-started"


def test_recover_blocked_step_without_child_progress_to_pending(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "workflow.probe-cleanup.run",
            "title": "Workflow probe cleanup run",
            "steps": [{"id": "design", "title": "Design"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="design",
        claimed_by="probe",
    )
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="design",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"summary": "probe cleanup"},
        error="probe cleanup",
    )

    recovered = repo.recover(
        run_plan_id=plan.id,
        project_id=project_id,
        step_id="design",
        step_status=RunPlanStepStatus.PENDING,
        reason="probe cleanup",
        actor="codex",
    ).data

    assert recovered.steps[0].status == RunPlanStepStatus.PENDING
    assert recovered.steps[0].started_at is None
    assert recovered.steps[0].claimed_at is None
    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    assert snapshot.tickets[0].status == "not-started"


def test_recover_started_plan_rejects_graph_warning_only_success_reopen(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "workflow.success.recover.run",
            "title": "Workflow success recover run",
            "steps": [
                {"id": "plan", "title": "Plan"},
                {"id": "deliver", "title": "Deliver", "depends_on": ["plan"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    plan_step = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="plan").data
    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=workflow_step_ticket_key(plan.id, "plan"),
        key="legacy-open-child",
        title="Legacy open child",
        run_plan_id=plan.id,
        run_plan_step_id=plan_step.id,
        dependency_keys=[workflow_step_ticket_key(plan.id, "plan")],
    )

    step_row = session.get(RunPlanStep, plan_step.id)
    assert step_row is not None
    step_row.status = RunPlanStepStatus.SUCCESS
    step_row.completed_at = _now()
    session.add(step_row)
    tracker_row = tracker.ensure_tracker(project_id=project_id)
    mirror = tracker._ticket_by_key(
        tracker_row.id,
        workflow_step_ticket_key(plan.id, "plan"),
    )
    mirror.status = TrackerItemStatus.COMPLETE
    mirror.completed_at = _now()
    session.add(mirror)
    session.commit()

    snapshot = tracker.get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=True,
    )
    assert snapshot.graph is not None
    assert any(
        "attached child tickets remain open" in warning for warning in snapshot.graph.warnings
    )

    with pytest.raises(ConflictError, match="not marked as system-recoverable"):
        repo.recover(
            run_plan_id=plan.id,
            project_id=project_id,
            step_id="plan",
            step_status=RunPlanStepStatus.BLOCKED,
            reason="tracker graph topology repair",
            actor="codex",
            result_json={"blocking_issue": "legacy child ticket remains open"},
            error="tracker graph warnings",
        )

    snapshot = tracker.get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    ticket_by_key = {ticket.key: ticket for ticket in snapshot.tickets}
    assert ticket_by_key[workflow_step_ticket_key(plan.id, "plan")].status == "complete"
    assert ticket_by_key["legacy-open-child"].status == "not-started"


def test_recover_failed_blocker_restores_live_run_plan(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recover.failed.blocker.run",
            "title": "Recover failed blocker",
            "steps": [
                {"id": "scope", "title": "Scope"},
                {"id": "graph-check", "title": "Graph Check", "depends_on": ["scope"]},
                {"id": "deliver", "title": "Deliver", "depends_on": ["graph-check"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="scope")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="scope",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "scoped"},
    )
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="graph-check")
    failed = repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="graph-check",
        status=RunPlanStepStatus.FAILED,
        result_json={"blocking_issue": "tracker graph has warnings"},
        error="Blocked by tracker graph warnings; daemon rejects blocked status.",
    ).data

    assert failed.status == RunPlanStatus.FAILED
    run = session.get(Run, started.run_id)
    assert run is not None
    assert run.status == RunStatus.FAILED

    recovered = repo.recover(
        run_plan_id=plan.id,
        project_id=project_id,
        step_id="graph-check",
        step_status=RunPlanStepStatus.BLOCKED,
        reason="Recover old daemon blocked-status bug.",
        actor="codex",
        result_json={"blocking_issue": "tracker graph still has warnings"},
        error="tracker graph warnings",
    ).data

    run = session.get(Run, started.run_id)
    assert run is not None
    assert recovered.status == RunPlanStatus.STARTED
    assert recovered.completed_at is None
    assert run.status == RunStatus.RUNNING
    assert run.error is None
    assert run.ended_at is None
    assert run.last_step == "graph-check"
    statuses = {step.step_id: step.status for step in recovered.steps}
    assert statuses == {
        "scope": RunPlanStepStatus.SUCCESS,
        "graph-check": RunPlanStepStatus.BLOCKED,
        "deliver": RunPlanStepStatus.PENDING,
    }
    graph_step = next(step for step in recovered.steps if step.step_id == "graph-check")
    assert graph_step.completed_at is None
    assert recovered.metadata_json is not None
    assert recovered.metadata_json["last_recovery_step_id"] == "graph-check"
    assert recovered.metadata_json["recovery_events"][0]["previous_plan_status"] == "failed"

    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    workflow_task = snapshot.tasks[0]
    assert workflow_task.status == "in-progress"
    assert workflow_task.lane_key == "planning"
    ticket_by_key = {ticket.key: ticket for ticket in snapshot.tickets}
    graph_ticket = ticket_by_key[workflow_step_ticket_key(plan.id, "graph-check")]
    assert graph_ticket.status == "in-progress"
    assert graph_ticket.blocker_reason == "tracker graph warnings"
    assert graph_ticket.outcome == "blocked: tracker graph warnings"
    assert graph_ticket.completed_at is None
    deliver_ticket = ticket_by_key[workflow_step_ticket_key(plan.id, "deliver")]
    assert deliver_ticket.status == "not-started"
    assert deliver_ticket.outcome is None

    reclaimed = repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="graph-check",
        claimed_by="agent",
    ).data
    assert reclaimed.status == RunPlanStepStatus.RUNNING


def test_recover_daemon_orphan_abort_restores_steps_approvals_and_child_tickets(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recover.orphan.abort.run",
            "title": "Recover orphan abort",
            "approvals": [
                {"key": "scope-review", "title": "Scope Review", "step_id": "scope"},
                {"key": "release-review", "title": "Release Review", "step_id": "release"},
            ],
            "steps": [
                {"id": "scope", "title": "Scope"},
                {"id": "plan", "title": "Plan", "depends_on": ["scope"]},
                {"id": "release", "title": "Release", "depends_on": ["plan"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.update(
        run_plan_id=plan.id,
        approval_key="scope-review",
        approval_status=ApprovalRequestStatus.APPROVED,
        decided_by="operator",
        decision_json={"approved": True},
    )
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="scope")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="scope",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "scope ok"},
    )
    plan_step = next(step for step in repo.get(plan.id).steps if step.step_id == "plan")
    tracker = TrackerRepository(session)
    tracker.create_ticket(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        parent_ticket_key=workflow_step_ticket_key(plan.id, "plan"),
        key="plan-child",
        title="Plan child",
        run_plan_id=plan.id,
        run_plan_step_id=plan_step.id,
        lane_key="planning",
        commit=False,
    )
    session.commit()

    repo.abort(
        run_plan_id=plan.id,
        project_id=project_id,
        reason="daemon-restart-orphan",
        actor="system",
    )

    aborted = repo.get(plan.id)
    assert aborted.status == RunPlanStatus.ABORTED
    assert {step.step_id: step.status for step in aborted.steps} == {
        "scope": RunPlanStepStatus.SUCCESS,
        "plan": RunPlanStepStatus.SKIPPED,
        "release": RunPlanStepStatus.SKIPPED,
    }
    approval_status = {item.approval_key: item.status for item in aborted.approval_requests}
    assert approval_status == {"scope-review": "approved", "release-review": "cancelled"}

    recovered = repo.recover(
        run_plan_id=plan.id,
        project_id=project_id,
        step_id="plan",
        step_status=RunPlanStepStatus.BLOCKED,
        reason="tracker graph topology repair",
        actor="codex",
        result_json={"blocking_issue": "topology repair needed"},
        error="topology warnings",
    ).data

    assert recovered.status == RunPlanStatus.STARTED
    assert {step.step_id: step.status for step in recovered.steps} == {
        "scope": RunPlanStepStatus.SUCCESS,
        "plan": RunPlanStepStatus.BLOCKED,
        "release": RunPlanStepStatus.PENDING,
    }
    approval_by_key = {item.approval_key: item for item in recovered.approval_requests}
    assert approval_by_key["scope-review"].status == "approved"
    assert approval_by_key["release-review"].status == "pending"
    assert approval_by_key["release-review"].decided_at is None
    run = session.get(Run, started.run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING
    assert run.error is None
    assert run.ended_at is None

    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    ticket_by_key = {ticket.key: ticket for ticket in snapshot.tickets}
    assert ticket_by_key[workflow_step_ticket_key(plan.id, "plan")].status == "in-progress"
    assert ticket_by_key[workflow_step_ticket_key(plan.id, "release")].status == "not-started"
    child_ticket = ticket_by_key["plan-child"]
    assert child_ticket.status == "not-started"
    assert child_ticket.completed_at is None
    assert child_ticket.outcome is None
    assert child_ticket.blocker_reason is None


def test_recover_live_orphan_recovery_restores_future_steps_from_previous_abort_metadata(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recover.live.orphan.run",
            "title": "Recover live orphan",
            "steps": [
                {"id": "scope", "title": "Scope"},
                {"id": "plan", "title": "Plan", "depends_on": ["scope"]},
                {"id": "deliver", "title": "Deliver", "depends_on": ["plan"]},
            ],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="scope")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="scope",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "scope ok"},
    )
    repo.abort(
        run_plan_id=plan.id,
        project_id=project_id,
        reason="daemon-restart-orphan",
        actor="system",
    )

    # Simulate the legacy bad recovery shape observed in project 2: the plan
    # was made live again and the current step was blocked, but later skipped
    # steps and tracker mirrors still carried the original orphan abort state.
    plan_row = repo._fetch_plan(plan.id)
    steps = {
        step.step_id: step
        for step in session.exec(
            select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)
        ).all()
    }
    run = session.get(Run, started.run_id)
    assert run is not None
    plan_row.status = RunPlanStatus.STARTED
    plan_row.completed_at = None
    plan_row.metadata_json = {
        "recovery_events": [
            {
                "recovered_at": _now().isoformat(),
                "step_id": "plan",
                "step_status": "blocked",
                "previous_plan_status": "aborted",
                "previous_run_status": "aborted",
                "actor": "codex",
                "reason": "tracker graph topology repair",
                "previous_abort": {
                    "abort_reason": "daemon-restart-orphan",
                    "linked_run_error": "daemon-restart-orphan",
                },
            }
        ],
        "last_recovered_at": _now().isoformat(),
        "last_recovery_step_id": "plan",
    }
    run.status = RunStatus.RUNNING
    run.error = None
    run.ended_at = None
    steps["plan"].status = RunPlanStepStatus.BLOCKED
    steps["plan"].error = "Legacy live step remained blocked after daemon restart."
    steps["plan"].completed_at = None
    session.add(plan_row)
    session.add(run)
    for step in steps.values():
        session.add(step)
    session.commit()

    tracker = TrackerRepository(session)
    tracker.mirror_run_plan_recovered(plan=plan_row, steps=list(steps.values()), actor="legacy")
    session.commit()
    legacy_snapshot = tracker.get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    legacy_tickets = {ticket.key: ticket for ticket in legacy_snapshot.tickets}
    assert legacy_tickets[workflow_step_ticket_key(plan.id, "deliver")].status == "aborted"

    recovered = repo.recover(
        run_plan_id=plan.id,
        project_id=project_id,
        step_id="plan",
        step_status=RunPlanStepStatus.BLOCKED,
        reason="tracker graph topology repair",
        actor="codex",
        result_json={"blocking_issue": "topology repair still needed"},
        error="tracker graph warnings",
    ).data

    statuses = {step.step_id: step.status for step in recovered.steps}
    assert statuses == {
        "scope": RunPlanStepStatus.SUCCESS,
        "plan": RunPlanStepStatus.BLOCKED,
        "deliver": RunPlanStepStatus.PENDING,
    }
    snapshot = tracker.get(project_id=project_id, task_key=f"workflow-{plan.id}")
    ticket_by_key = {ticket.key: ticket for ticket in snapshot.tickets}
    assert ticket_by_key[workflow_step_ticket_key(plan.id, "plan")].status == "in-progress"
    assert ticket_by_key[workflow_step_ticket_key(plan.id, "deliver")].status == "not-started"


def test_recover_can_restore_failed_blocker_to_pending_for_clean_rerun(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recover.pending.run",
            "title": "Recover Pending",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="plan")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="plan",
        status=RunPlanStepStatus.FAILED,
        result_json={"blocking_issue": "recoverable setup issue"},
        error="blocked by setup issue",
    )

    recovered = repo.recover(
        run_plan_id=plan.id,
        project_id=project_id,
        step_id="plan",
        step_status=RunPlanStepStatus.PENDING,
        reason="recoverable setup issue",
        actor="codex",
    ).data

    assert recovered.status == RunPlanStatus.STARTED
    assert recovered.steps[0].status == RunPlanStepStatus.PENDING
    assert recovered.steps[0].claimed_at is None
    assert recovered.steps[0].started_at is None
    assert recovered.steps[0].completed_at is None
    snapshot = TrackerRepository(session).get(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        include_graph=False,
    )
    assert snapshot.tickets[0].status == "not-started"
    assert snapshot.tickets[0].outcome is None
    assert repo.claim_step(run_plan_id=plan.id, run_id=started.run_id).data.step_id == "plan"


def test_recover_rejects_nonrecoverable_failed_step(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recover.reject.failed.run",
            "title": "Recover Reject Failed",
            "steps": [{"id": "test", "title": "Test"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="test")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="test",
        status=RunPlanStepStatus.FAILED,
        result_json={"summary": "tests failed"},
        error="unit test failed",
    )

    with pytest.raises(ConflictError) as exc_info:
        repo.recover(
            run_plan_id=plan.id,
            project_id=project_id,
            step_id="test",
            step_status=RunPlanStepStatus.BLOCKED,
            reason="operator regret",
        )

    assert "system-recoverable" in str(exc_info.value)
    assert repo.get(plan.id).status == RunPlanStatus.FAILED


def test_recover_rejects_completed_plan(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recover.reject.completed.run",
            "title": "Recover Reject Completed",
            "steps": [{"id": "done", "title": "Done"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="done")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="done",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "done"},
    )

    with pytest.raises(ConflictError) as exc_info:
        repo.recover(
            run_plan_id=plan.id,
            project_id=project_id,
            step_id="done",
            step_status=RunPlanStepStatus.BLOCKED,
            reason="not allowed",
        )

    assert "completed run plans cannot be recovered" in str(exc_info.value)
    assert repo.get(plan.id).status == RunPlanStatus.COMPLETED


def test_recover_rejects_terminal_step_status_request(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recover.reject.status.run",
            "title": "Recover Reject Status",
            "steps": [{"id": "plan", "title": "Plan"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="plan")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="plan",
        status=RunPlanStepStatus.FAILED,
        result_json={"blocking_issue": "recoverable"},
        error="blocked",
    )

    with pytest.raises(ConflictError) as exc_info:
        repo.recover(
            run_plan_id=plan.id,
            project_id=project_id,
            step_id="plan",
            step_status=RunPlanStepStatus.SUCCESS,
            reason="recoverable",
        )

    assert "blocked or pending" in str(exc_info.value)
    assert repo.get(plan.id).status == RunPlanStatus.FAILED


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


def test_claim_step_refreshes_linked_run_heartbeat(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "heartbeat.claim.run",
            "title": "Heartbeat Claim",
            "steps": [{"id": "write", "title": "Write"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    run = session.get(Run, started.run_id)
    assert run is not None
    stale_at = _now() - timedelta(minutes=10)
    run.heartbeat_at = stale_at
    session.add(run)
    session.commit()

    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="write")

    refreshed = session.get(Run, started.run_id)
    assert refreshed is not None
    assert refreshed.heartbeat_at is not None
    assert refreshed.heartbeat_at > stale_at


def test_claim_step_rejects_terminal_linked_run(session: Session, project_id: int) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "terminal-run.claim.run",
            "title": "Terminal Run Claim",
            "steps": [{"id": "write", "title": "Write"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    run = session.get(Run, started.run_id)
    assert run is not None
    run.status = RunStatus.ABORTED
    run.error = "daemon-restart-orphan"
    session.add(run)
    session.commit()

    with pytest.raises(ConflictError) as exc_info:
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="write")

    assert exc_info.value.data["run_id"] == started.run_id
    assert exc_info.value.data["run_status"] == "aborted"
    assert "runPlan.checkConsistency" in exc_info.value.data["next_operations"]


def test_record_step_rejects_terminal_linked_run(session: Session, project_id: int) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "terminal-run.record.run",
            "title": "Terminal Run Record",
            "steps": [{"id": "write", "title": "Write"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="write")
    run = session.get(Run, started.run_id)
    assert run is not None
    run.status = RunStatus.ABORTED
    run.error = "daemon-restart-orphan"
    session.add(run)
    session.commit()

    with pytest.raises(ConflictError) as exc_info:
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="write",
            status=RunPlanStepStatus.SUCCESS,
        )

    assert exc_info.value.data["run_id"] == started.run_id
    assert exc_info.value.data["run_status"] == "aborted"
    assert session.get(Run, started.run_id).status == RunStatus.ABORTED
    assert repo.get(plan.id).status == RunPlanStatus.STARTED


def test_run_plan_grant_gate_rejects_terminal_linked_run(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "terminal-run.grant.run",
            "title": "Terminal Run Grant",
            "steps": [{"id": "write", "title": "Write"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="write")
    run = session.get(Run, started.run_id)
    assert run is not None
    run.status = RunStatus.ABORTED
    run.error = "daemon-restart-orphan"
    session.add(run)
    session.commit()

    with pytest.raises(ToolNotGrantedError) as exc_info:
        active_run_plan_step(
            SimpleNamespace(run=run, run_id=started.run_id, session=session),
            "resource.upsert",
        )

    assert exc_info.value.data["run_id"] == started.run_id
    assert "running audit run" in str(exc_info.value)


def test_run_plan_get_surfaces_consistency_issues_for_terminal_run(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "terminal-run.diagnostic.run",
            "title": "Terminal Run Diagnostic",
            "steps": [{"id": "write", "title": "Write"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    run = session.get(Run, started.run_id)
    assert run is not None
    run.status = RunStatus.ABORTED
    run.error = "daemon-restart-orphan"
    session.add(run)
    session.commit()

    fetched = repo.get(plan.id)

    assert fetched.consistency_issues
    assert fetched.consistency_issues[0].code == "terminal-run-live-plan"
    assert fetched.consistency_issues[0].severity == "error"


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
                {"id": "first", "title": "First", "output_refs": ["brief"]},
                {
                    "id": "second",
                    "title": "Second",
                    "purpose": "Use the first step's reviewed brief.",
                    "depends_on": ["first"],
                },
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
        result_json={"summary": "Reviewed brief", "decision": "proceed"},
    )
    second = repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="second").data

    assert second.status == RunPlanStepStatus.RUNNING
    assert len(second.direct_dependency_handoffs) == 1
    handoff = second.direct_dependency_handoffs[0]
    assert handoff.step_id == "first"
    assert handoff.output_refs_json == ["brief"]
    assert handoff.result_json == {"summary": "Reviewed brief", "decision": "proceed"}
    assert handoff.truncated is False


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
