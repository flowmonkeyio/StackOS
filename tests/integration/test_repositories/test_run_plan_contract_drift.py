"""Upgrade/resume diagnostics preserve historical run instructions and effects."""

import json
from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.db.models import RunPlan, RunPlanStepStatus
from stackos.repositories.run_plans import RunPlanRepository
from stackos.workflows import template_loader
from stackos.workflows.template_loader import WorkflowTemplateLoader


@pytest.fixture
def contract_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plugins = tmp_path / "plugins"
    workflow = plugins / "example" / "workflows" / "review.yaml"
    workflow.parent.mkdir(parents=True)
    monkeypatch.setattr(template_loader, "_clone_plugins_root", lambda: plugins)
    monkeypatch.setattr(template_loader, "_bundled_plugins_root", lambda: None)
    _write_contract(workflow, "0.5.2", "Use the old review instructions.")
    return workflow


def _write_contract(path: Path, version: str, instruction: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "stackos.workflow-template.v1",
                "key": "example.review",
                "name": "Review external object",
                "version": version,
                "steps": [
                    {"id": "prepare", "title": "Prepare"},
                    {
                        "id": "review",
                        "title": "Review",
                        "depends_on": ["prepare"],
                        "instructions": [instruction],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("installed_version", ["0.5.2", "0.5.3"])
def test_upgrade_resume_reports_contract_drift_without_replaying_history(
    session: Session, project_id: int, contract_file: Path, installed_version: str
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(project_id=project_id, template_key="example.review").data
    assert plan.workflow_contract.status == "current"
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="prepare")
    evidence = {"object_ref": "external:existing-object", "evidence_ref": "external:receipt"}
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json=evidence,
    )
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="review")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="review",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"reason": "Await independent review"},
    )
    before = repo.get(plan.id)

    # Simulate a plugin upgrade between sessions, including a release that forgot its version bump.
    _write_contract(contract_file, installed_version, "Require independent presentation evidence.")
    resumed = RunPlanRepository(session)
    inspected = resumed.get(plan.id)
    diagnostic = inspected.workflow_contract
    assert diagnostic.status == "mismatch"
    assert diagnostic.frozen_version == "0.5.2"
    assert diagnostic.installed_version == installed_version
    assert diagnostic.frozen_digest != diagnostic.installed_digest
    assert "$.steps[1].instructions[0]" in diagnostic.changed_paths
    if installed_version == "0.5.2":
        assert "same version" in diagnostic.message
    assert "Do not replay completed steps" in diagnostic.next_action
    assert inspected.template_snapshot_json == before.template_snapshot_json
    assert inspected.grant_snapshot_json == before.grant_snapshot_json
    assert inspected.approval_requests == before.approval_requests
    assert inspected.steps == [
        step.model_copy(update={"workflow_contract": diagnostic}) for step in before.steps
    ]
    assert resumed.get_step(plan.id, "review").workflow_contract == diagnostic
    claimed = resumed.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="review").data
    assert claimed.workflow_contract == diagnostic
    assert claimed.instructions_json == ["Use the old review instructions."]
    assert claimed.direct_dependency_handoffs[0].result_json == evidence
    assert resumed.get(plan.id).steps[0].status == "success"


def test_project_override_drift_is_compared_without_changing_frozen_snapshot(
    session: Session, project_id: int, contract_file: Path
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(project_id=project_id, template_key="example.review").data
    loader = WorkflowTemplateLoader(session)
    current = loader.describe_template(key="example.review", project_id=project_id)
    steps = [step.model_dump(mode="json") for step in current.spec.steps]
    steps[1]["instructions"] = ["Project-specific revised review."]
    loader.upsert_extension(
        project_id=project_id,
        workflow_key="example.review",
        template_overrides_json={"steps": steps},
    )
    inspected = repo.get(plan.id)
    assert inspected.workflow_contract.status == "mismatch"
    assert (
        inspected.workflow_contract.frozen_version == inspected.workflow_contract.installed_version
    )
    assert inspected.template_snapshot_json == plan.template_snapshot_json


def test_missing_installed_contract_remains_inspectable_and_resume_reports_unknown(
    session: Session, project_id: int, contract_file: Path
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(project_id=project_id, template_key="example.review").data
    contract_file.unlink()
    inspected = repo.get(plan.id)
    assert inspected.workflow_contract.status == "unavailable"
    assert inspected.workflow_contract.frozen_digest
    assert inspected.template_snapshot_json == plan.template_snapshot_json
    started = repo.start(plan.id, project_id=project_id).data.plan
    assert started.workflow_contract == inspected.workflow_contract


def test_legacy_missing_snapshot_is_unknown_and_inspection_does_not_commit(
    session: Session, project_id: int, contract_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(project_id=project_id, template_key="example.review").data
    row = session.get(RunPlan, plan.id)
    row.template_snapshot_json = None
    session.add(row)
    session.commit()
    assert repo.get(plan.id).workflow_contract.status == "unavailable"
    row.template_snapshot_json = plan.template_snapshot_json
    session.add(row)
    session.commit()

    def reject_sync(_self: WorkflowTemplateLoader) -> None:
        pytest.fail("Inspection must not sync or commit the plugin registry")

    monkeypatch.setattr(WorkflowTemplateLoader, "_sync_builtin_plugins", reject_sync)
    assert repo.get(plan.id).workflow_contract.status == "current"
