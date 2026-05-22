"""Unit tests for StackOS run-plan schema."""

from __future__ import annotations

import pytest

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


def test_run_plan_schema_rejects_unknown_step_tool_grant() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "missing", "tool": "resource.upsert"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "unknown step" in result.errors[0].message


def test_run_plan_schema_rejects_admin_tool_grant() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "auth.start"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "admin/setup tool" in result.errors[0].message


def test_run_plan_schema_requires_action_execute_refs() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "action.execute"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "action_refs" in result.errors[0].message


@pytest.mark.parametrize("action_ref", ["utils.web.scrape", "seo.serp.analyze"])
def test_run_plan_schema_accepts_action_execute_with_refs(action_ref: str) -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [
            {
                "step_id": "create-campaign",
                "tool": "action.execute",
                "action_refs": [action_ref],
            }
        ],
    }
    data["steps"][0]["action_refs"] = [action_ref]

    result = validate_run_plan_obj(data)

    assert result.valid is True


def test_run_plan_schema_requires_context_query_grant_filters() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "context.query"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "sources and fields" in result.errors[0].message


def test_run_plan_schema_rejects_compact_context_query_grant() -> None:
    data = _plan_dict()
    data["grants"] = {"step_tools": {"create-campaign": ["context.query"]}}

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "sources and fields" in result.errors[0].message


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
