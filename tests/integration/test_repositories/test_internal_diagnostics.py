"""Execute recorded incident evidence using the real durable run-plan engine.

No test launches a host process, calls the incident's external service, or
claims installed-package reproduction. The repository fixtures use temporary
SQLite state only.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from sqlmodel import Session, select

from stackos.db.models import RunPlan, RunPlanStepStatus
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.resources import ResourceRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec
from tests.helpers.internal_diagnostics import STAGES, incident, inputs, receipt, workflow


def create_plan(session: Session, project_id: int):
    # Exercise runtime-data authoring, not only built-in template discovery.
    spec = WorkflowTemplateSpec.model_validate(workflow())
    WorkflowTemplateLoader(session).save_project_template(
        project_id=project_id, spec=spec, source="project", created_by="diagnostic-test-agent"
    )
    repo = RunPlanRepository(session)
    check = repo.validate_plan(
        project_id=project_id,
        template_key=spec.key,
        inputs_json=inputs(project_id),
        enforce_required_inputs=True,
    )
    assert check.valid, check.errors
    assert not check.warnings, check.warnings
    plan = repo.create(
        project_id=project_id,
        template_key=spec.key,
        key=incident()["case_id"],
        inputs_json=inputs(project_id),
        metadata_json={"incident_key": incident()["case_id"], "evidence_mode": "recorded"},
    ).data
    return plan, repo.start(plan.id, project_id=project_id).data


def test_recorded_incident_resumes_same_plan_without_repeating_checks(
    session: Session, project_id: int
) -> None:
    plan, started = create_plan(session, project_id)
    repo = RunPlanRepository(session)
    first = receipt("resume-provenance", project_id, plan.id)
    repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="resume-provenance",
        claimed_by="agent-one",
    )
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="resume-provenance",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"incident_resume": first},
    )
    collected = receipt("collect-classify", project_id, plan.id)
    repo.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="collect-classify",
        claimed_by="agent-one",
    )
    matching_grants = [
        grant
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
        if grant.get("tool") == "resource.upsert" and grant.get("step_id") == "collect-classify"
    ]
    assert len(matching_grants) == 1
    assert matching_grants[0]["resource_key"] == "support-investigation"
    index_data = {
        "title": "Launch and handshake diagnostics",
        "summary": "Recorded evidence; packaged reproduction and leaf exception unavailable.",
        "status": "blocked",
        "root_cause": (
            "Unresolved. External launch and internal initialization remain separate observations."
        ),
        "evidence_refs": collected["evidence_refs"],
        "task_key": f"workflow-{plan.id}",
    }
    first_index = (
        ResourceRepository(session)
        .upsert_record(
            project_id=project_id,
            plugin_slug="support",
            resource_key="support-investigation",
            external_id=incident()["case_id"],
            data_json=index_data,
            provenance_json={"run_plan_id": plan.id, "evidence_mode": "recorded"},
        )
        .data
    )
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="collect-classify",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"diagnostic_findings": collected},
        error="handshake-leaf-and-published-runtime-missing",
    )

    # A fresh Session/controller, not in-memory conversational state, rehydrates
    # the same durable plan and the complete already-consumed attempt ledger.
    with Session(session.get_bind()) as replacement_session:
        replacement = RunPlanRepository(replacement_session)
        resources = ResourceRepository(replacement_session)
        indexes = resources.query_records(
            project_id=project_id, plugin_slug="support", resource_key="support-investigation"
        ).items
        exact = [index for index in indexes if index.external_id == incident()["case_id"]]
        assert len(exact) == 1
        recovered_plan_id = exact[0].provenance_json["run_plan_id"]
        restored = replacement.get(recovered_plan_id, project_id=project_id)
        recovered_run_id = restored.run_id
        assert restored.status == "started"
        assert restored.steps[0].status == "success"
        assert restored.steps[0].result_json["incident_resume"] == first
        assert restored.steps[1].result_json["diagnostic_findings"] == collected
        assert restored.steps[1].status == "blocked"
        assert not replacement.check_consistency(recovered_plan_id, project_id=project_id).issues
        durable_collected = deepcopy(restored.steps[1].result_json["diagnostic_findings"])
        resumed = replacement.claim_step(
            run_plan_id=recovered_plan_id,
            run_id=recovered_run_id,
            step_id="collect-classify",
            claimed_by="replacement-agent",
        ).data
        assert resumed.result_json["diagnostic_findings"]["attempts"] == collected["attempts"]
        assert resumed.direct_dependency_handoffs[0].step_id == "resume-provenance"
        replacement.record_step(
            run_plan_id=recovered_plan_id,
            run_id=recovered_run_id,
            step_id="collect-classify",
            status=RunPlanStepStatus.SUCCESS,
            result_json={"diagnostic_findings": durable_collected},
        )
        for stage, key in STAGES[2:]:
            replacement.claim_step(
                run_plan_id=recovered_plan_id,
                run_id=recovered_run_id,
                step_id=stage,
                claimed_by="replacement-agent",
            )
            packet = receipt(stage, project_id, recovered_plan_id)
            packet["attempts"] = deepcopy(durable_collected["attempts"])
            assert packet["attempts"] == collected["attempts"]
            if stage == "verify-escalate":
                second_index = resources.upsert_record(
                    project_id=project_id,
                    plugin_slug="support",
                    resource_key="support-investigation",
                    external_id=packet["incident_key"],
                    data_json=exact[0].data_json,
                    provenance_json=exact[0].provenance_json,
                ).data
                assert second_index.id == first_index.id
            replacement.record_step(
                run_plan_id=recovered_plan_id,
                run_id=recovered_run_id,
                step_id=stage,
                status=RunPlanStepStatus.SUCCESS,
                result_json={key: packet},
            )
        final = replacement.get(recovered_plan_id, project_id=project_id)
        assert final.status == "completed"  # The investigation, not the incident, is complete.
        final_receipt = final.steps[-1].result_json["incident_receipt"]
        assert final_receipt["status"] == "unresolved"
        assert final_receipt["verification"]["symptom_check"] == "not_run"
        assert final_receipt["next_action"]["disposition"] == "escalate"
        assert len(list(replacement_session.exec(select(RunPlan)))) == 1
        assert all(
            a["retry_disposition"] in {"no_retry", "reconcile_first"}
            for a in final_receipt["attempts"]
        )

        assert (
            len(
                resources.query_records(
                    project_id=project_id,
                    plugin_slug="support",
                    resource_key="support-investigation",
                ).items
            )
            == 1
        )

        if output := os.environ.get("STACKOS_DIAGNOSTICS_DEMO_OUT"):
            Path(output).write_text(
                json.dumps(
                    {
                        "demonstration": (
                            "isolated source-test replay of recorded real incident evidence"
                        ),
                        "source_revision": incident()["source_revision"],
                        "workflow_key": final.template_key,
                        "workflow_status": final.status,
                        "controller_handoff": (
                            "agent-one to replacement-agent using a fresh database Session"
                        ),
                        "same_run_plan": True,
                        "duplicate_incident_indexes": 0,
                        "new_external_attempts": 0,
                        "live_packaged_reproduction": False,
                        "final_receipt": final_receipt,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )


def test_success_rejects_incomplete_receipt_and_preserves_running_step(
    session: Session, project_id: int
) -> None:
    plan, started = create_plan(session, project_id)
    repo = RunPlanRepository(session)
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="resume-provenance")
    invalid = receipt("resume-provenance", project_id, plan.id)
    del invalid["attempts"]
    with pytest.raises(ValidationError, match="expected outputs"):
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="resume-provenance",
            status=RunPlanStepStatus.SUCCESS,
            result_json={"incident_resume": invalid},
        )
    assert repo.get_step(plan.id, "resume-provenance").status == "running"


def test_adaptation_preserves_frozen_snapshot_and_existing_evidence(
    session: Session, project_id: int
) -> None:
    plan, started = create_plan(session, project_id)
    repo = RunPlanRepository(session)
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="resume-provenance")
    prior = receipt("resume-provenance", project_id, plan.id)
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="resume-provenance",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"incident_resume": prior},
    )
    changed = deepcopy(workflow())
    changed["steps"][1]["instructions"].append(
        "Preserve the original bridge error before reviewing a retry."
    )
    loader = WorkflowTemplateLoader(session)
    with pytest.raises(ConflictError, match="different content"):
        loader.save_project_template(
            project_id=project_id, spec=WorkflowTemplateSpec.model_validate(changed)
        )
    changed["version"] = "0.1.1"
    loader.save_project_template(
        project_id=project_id, spec=WorkflowTemplateSpec.model_validate(changed)
    )
    current = repo.get(plan.id)
    assert current.template_snapshot_json["version"] == "0.1.0"
    assert current.workflow_contract.status == "mismatch"
    assert current.steps[0].result_json["incident_resume"] == prior


def test_bounded_tracker_cursor_does_not_skip_unread_revisions(
    session: Session, project_id: int
) -> None:
    create_plan(session, project_id)
    tracker = TrackerRepository(session)
    first = tracker.changed(project_id=project_id, since_rev=0, limit=1)
    assert first.changes
    cursor = first.changes[-1].rev
    consumed = [item.rev for item in first.changes]
    while cursor < first.current_rev:
        page = tracker.changed(project_id=project_id, since_rev=cursor, limit=1)
        assert page.changes
        consumed.extend(item.rev for item in page.changes)
        cursor = page.changes[-1].rev
    assert consumed == sorted(set(consumed))
    assert consumed[-1] == first.current_rev
