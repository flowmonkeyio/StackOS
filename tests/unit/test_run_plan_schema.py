"""Unit tests for StackOS run-plan schema."""

from __future__ import annotations

from content_stack.workflows.run_plan_schema import RunPlanSpec, validate_run_plan_obj


def _plan_dict() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "media.launch.run",
        "title": "Launch Meta Campaign",
        "goal": "Create configured provider objects after approval.",
        "inputs": {"budget": 250},
        "grants": {"credential_ref": "cred_abc"},
        "approvals": [{"key": "launch-review", "title": "Launch review"}],
        "steps": [
            {
                "id": "create-campaign",
                "title": "Create campaign",
                "approval_refs": ["launch-review"],
                "action_refs": ["meta.campaign.create"],
                "action_payloads": {
                    "provider_object_id": "act_123",
                    "campaign": {"name": "Agent selected campaign"},
                    "credential_ref": "cred_abc",
                },
            }
        ],
    }


def test_run_plan_schema_accepts_concrete_provider_payloads() -> None:
    plan = RunPlanSpec.model_validate(_plan_dict())

    assert plan.key == "media.launch.run"
    assert plan.steps[0].action_payloads_json[0]["provider_object_id"] == "act_123"
    assert plan.grant_snapshot_json == {"credential_ref": "cred_abc"}


def test_run_plan_schema_rejects_unknown_approval_refs() -> None:
    data = _plan_dict()
    data["steps"][0]["approval_refs"] = ["missing"]

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "missing" in result.errors[0].message


def test_run_plan_schema_rejects_secrets_but_allows_credential_refs() -> None:
    data = _plan_dict()
    data["steps"][0]["action_payloads"]["api_key"] = "real-secret"

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "must not contain secrets" in result.errors[0].message
    assert "api_key" in result.errors[0].message


def test_run_plan_schema_rejects_cyclic_step_dependencies() -> None:
    data = _plan_dict()
    data["steps"] = [
        {"id": "first", "title": "First", "depends_on": ["second"]},
        {"id": "second", "title": "Second", "depends_on": ["first"]},
    ]
    data["approvals"] = []

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "cyclic step dependencies" in result.errors[0].message
