"""Unit tests for StackOS workflow template schema."""

from __future__ import annotations

from stackos.workflows.template_schema import (
    WorkflowTemplateSpec,
    parse_workflow_template_yaml,
    validate_workflow_template_obj,
)


def _template_dict() -> dict:
    return {
        "schema_version": "stackos.workflow-template.v1",
        "key": "media.campaign-launch",
        "name": "Campaign Launch",
        "version": "0.1.0",
        "experience": {
            "problem": "Campaign work can skip evidence and approval boundaries.",
            "outcome": "A reviewed launch plan with an explicit execution boundary.",
            "operator_path": ["State the goal and constraints.", "Review the plan."],
            "agent_path": ["Read bounded context.", "Prepare the reviewed plan."],
            "safe_stopping_points": ["Stop after the recommendation when launch is not approved."],
            "handoffs": [
                {
                    "workflow_key": "media.performance-review",
                    "relationship": "next",
                    "when": "The launch has enough outcome data to review.",
                }
            ],
        },
        "public": {
            "audience": "Growth teams",
            "setup": "connection-required",
            "prerequisites": ["A campaign goal and budget boundary"],
            "proof": ["Approval and validation evidence"],
            "featured": True,
        },
        "inputs": [{"key": "goal", "type": "string", "required": True}],
        "context_requirements": [
            {
                "id": "recent_learnings",
                "source": "learnings",
                "filters": {"tags": ["creative"]},
                "fields": ["statement", "confidence"],
                "max_items": 5,
            }
        ],
        "capability_requirements": [{"key": "media-buying"}],
        "auth_requirements": [{"key": "meta-auth", "provider": "meta"}],
        "action_contracts": [
            {
                "key": "create-campaign",
                "capability": "media-buying",
                "provider": "meta",
                "auth_ref": "meta-auth",
                "route_group": "campaign-write",
                "route_key": "meta",
                "input_schema": {"type": "object"},
            }
        ],
        "policies": [{"key": "human-approval", "kind": "approval"}],
        "approval_gates": [{"key": "approve-launch"}],
        "steps": [
            {
                "id": "plan",
                "title": "Plan launch",
                "input_refs": ["goal"],
                "context_refs": ["recent_learnings"],
                "action_refs": ["create-campaign"],
                "policy_refs": ["human-approval"],
                "approval_refs": ["approve-launch"],
                "output_refs": ["plan_summary"],
            }
        ],
        "outputs": [{"key": "plan_summary", "type": "object"}],
    }


def test_template_schema_accepts_generic_contracts() -> None:
    template = WorkflowTemplateSpec.model_validate(_template_dict())

    assert template.key == "media.campaign-launch"
    assert template.context_requirements[0].filters_json == {"tags": ["creative"]}
    assert template.action_contracts[0].input_schema_json == {"type": "object"}
    assert template.experience is not None
    assert template.experience.handoffs[0].workflow_key == "media.performance-review"
    assert template.public is not None
    assert template.public.setup == "connection-required"
    assert template.action_contracts[0].route_group == "campaign-write"


def test_template_schema_accepts_agent_skill_and_skill_preset_requirements() -> None:
    data = _template_dict()
    data["agent_requirements"] = [
        {
            "role": "planning",
            "requirement": "required",
            "agent_preset_ref": "stackos.sdlc.planning",
            "applies_to_steps": ["plan"],
        }
    ]
    data["skill_requirements"] = [
        {
            "skill_ref": "stackos:stackos",
            "requirement": "recommended",
            "applies_to_steps": ["plan"],
        }
    ]
    data["skill_preset_requirements"] = [
        {
            "skill_preset_ref": "stackos.sdlc.delivery-orchestrator",
            "requirement": "required",
            "applies_to_steps": ["plan"],
        }
    ]

    template = WorkflowTemplateSpec.model_validate(data)

    assert template.agent_requirements[0].agent_preset_ref == "stackos.sdlc.planning"
    assert template.skill_requirements[0].skill_ref == "stackos:stackos"
    assert (
        template.skill_preset_requirements[0].skill_preset_ref
        == "stackos.sdlc.delivery-orchestrator"
    )


def test_template_schema_rejects_unknown_agent_requirement_step_refs() -> None:
    data = _template_dict()
    data["agent_requirements"] = [
        {
            "role": "planning",
            "agent_preset_ref": "stackos.sdlc.planning",
            "applies_to_steps": ["missing"],
        }
    ]

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "agent requirement" in result.errors[0].message
    assert "missing" in result.errors[0].message


def test_template_schema_rejects_unknown_skill_requirement_step_refs() -> None:
    data = _template_dict()
    data["skill_requirements"] = [{"skill_ref": "stackos:stackos", "applies_to_steps": ["missing"]}]

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "skill requirement" in result.errors[0].message
    assert "missing" in result.errors[0].message


def test_template_schema_rejects_unknown_skill_preset_requirement_step_refs() -> None:
    data = _template_dict()
    data["skill_preset_requirements"] = [
        {
            "skill_preset_ref": "stackos.sdlc.delivery-orchestrator",
            "applies_to_steps": ["missing"],
        }
    ]

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "skill preset requirement" in result.errors[0].message
    assert "missing" in result.errors[0].message


def test_template_schema_rejects_unknown_references() -> None:
    data = _template_dict()
    data["steps"][0]["action_refs"] = ["missing-action"]

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "missing-action" in result.errors[0].message


def test_template_schema_rejects_secret_values() -> None:
    data = _template_dict()
    data["metadata"] = {"api_key": "real-value"}

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "must not contain secrets" in result.errors[0].message


def test_template_schema_requires_complete_route_identity() -> None:
    data = _template_dict()
    del data["action_contracts"][0]["route_key"]

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "route_group and route_key" in result.errors[0].message


def test_template_schema_rejects_concrete_action_payloads() -> None:
    data = _template_dict()
    data["action_contracts"][0]["config"] = {
        "payload": {"name": "Specific launch"},
        "ad_account_id": "act_123",
    }

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "final action payloads" in result.errors[0].message
    assert "ad_account_id" in result.errors[0].message


def test_template_schema_rejects_hard_coded_business_decisions() -> None:
    data = _template_dict()
    data["extensions"] = {"business_decision": "winner is variant A"}

    result = validate_workflow_template_obj(data)

    assert result.valid is False
    assert "hard-coded business decisions" in result.errors[0].message


def test_template_yaml_parser_supports_authoring_aliases() -> None:
    template = parse_workflow_template_yaml(
        """
schema_version: stackos.workflow-template.v1
key: core.simple-review
name: Simple Review
steps:
  - id: read
    title: Read context
context_requirements:
  - id: recent
    source: runs
    filters:
      statuses: [success]
    max_items: 3
outputs:
  - key: summary
    schema:
      type: object
metadata:
  safe: true
"""
    )

    assert template.context_requirements[0].filters_json == {"statuses": ["success"]}
    assert template.outputs[0].schema_data == {"type": "object"}
    assert template.metadata_json == {"safe": True}
