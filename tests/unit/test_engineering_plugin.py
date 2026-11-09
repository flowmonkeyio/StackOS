"""Automatic role selection survives the source template's frozen plan boundary."""

from pathlib import Path

import yaml

from stackos.workflows.run_plan_schema import run_plan_from_template
from stackos.workflows.template_loader import LoadedWorkflowTemplate, WorkflowTemplateSummaryOut
from stackos.workflows.template_schema import WorkflowTemplateSpec


def test_automatic_role_defaults_freeze_without_model_or_phase_runtime_policy() -> None:
    path = (
        Path(__file__).resolve().parents[2] / "plugins/engineering/workflows/tracked-delivery.yaml"
    )
    spec = WorkflowTemplateSpec.model_validate(yaml.safe_load(path.read_text()))
    loaded = LoadedWorkflowTemplate(
        summary=WorkflowTemplateSummaryOut(
            key=spec.key,
            name=spec.name,
            version=spec.version,
            source="plugin",
            precedence=10,
            plugin_slug="engineering",
        ),
        spec=spec,
    )
    plan = run_plan_from_template(loaded, inputs_json={"goal": "Verify role metadata"})
    roles = {step.id: step.metadata_json["agent_assignment"] for step in plan.steps}

    assert loaded.spec.version == "0.8.2"
    assert roles == {
        "scope-work": "delivery_agent",
        "define-requirements": "delivery_agent",
        "discover-impact": "delivery_agent",
        "design-approach": "delivery_agent",
        "design-tests": "delivery_agent",
        "plan-tickets": "delivery_agent",
        "review-design": "review_agent",
        "verify-delivery": "delivery_agent",
        "review-delivery": "review_agent",
        "audit-tracker": "review_agent",
        "release-closeout": "review_agent",
    }
    for source_step, frozen_step in zip(loaded.spec.steps, plan.steps, strict=True):
        assert frozen_step.metadata_json == source_step.extensions_json
        assert frozen_step.instructions == source_step.instructions
        assert set(frozen_step.metadata_json) == {"agent_assignment"}
    # Source edits cannot change already-materialized role or output contracts.
    loaded.spec.steps[-1].extensions_json["agent_assignment"] = "delivery_agent"
    assert plan.steps[-1].metadata_json["agent_assignment"] == "review_agent"
    assert plan.steps[-1].expected_outputs_json["release_summary"]["schema_json"]["required"]
