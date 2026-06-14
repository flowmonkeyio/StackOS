"""Unit tests for StackOS run-plan schema."""

from __future__ import annotations

import pytest

from stackos.workflows.run_plan_schema import (
    RunPlanSpec,
    run_plan_from_template,
    validate_run_plan_obj,
)
from stackos.workflows.template_loader import LoadedWorkflowTemplate, WorkflowTemplateSummaryOut
from stackos.workflows.template_schema import WorkflowTemplateSpec


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
    assert result.warnings == []


def test_run_plan_schema_requires_communication_send_targets() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "communication.send"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "targets" in result.errors[0].message


def test_run_plan_schema_accepts_communication_send_with_targets() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [
            {
                "step_id": "create-campaign",
                "tool": "communication.send",
                "targets": ["communication-target:ops-alerts"],
            }
        ],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is True


def test_run_plan_schema_warns_when_action_refs_lack_executable_grants() -> None:
    data = _plan_dict()

    result = validate_run_plan_obj(data)

    assert result.valid is True
    assert result.warnings
    assert result.warnings[0].code == "missing_action_execute_grant"
    assert "action.execute" in result.warnings[0].message


def test_run_plan_schema_builds_template_derived_grants() -> None:
    template = WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": "communications.outbound-notification",
            "name": "Outbound notification",
            "version": "0.1.0",
            "context_requirements": [
                {
                    "id": "recent_requests",
                    "source": "agent_requests",
                    "fields": ["status", "summary"],
                }
            ],
            "action_contracts": [
                {
                    "key": "send_email",
                    "action": "smtp.email.send",
                    "risk_level": "write",
                }
            ],
            "resource_contracts": [
                {
                    "key": "delivery_summary",
                    "resource": "communication-delivery",
                }
            ],
            "outputs": [{"key": "operator_receipt"}],
            "steps": [
                {
                    "id": "notify",
                    "title": "Notify",
                    "context_refs": ["recent_requests"],
                    "action_refs": ["send_email"],
                    "resource_refs": ["delivery_summary"],
                    "output_refs": ["operator_receipt"],
                }
            ],
        }
    )
    plan = run_plan_from_template(
        LoadedWorkflowTemplate(
            summary=WorkflowTemplateSummaryOut(
                key=template.key,
                name=template.name,
                version=template.version,
                source="plugin",
                precedence=10,
                plugin_slug="communications",
            ),
            spec=template,
        )
    )

    result = validate_run_plan_obj(plan.model_dump(mode="json"))

    assert result.valid is True
    assert result.warnings == []
    assert plan.steps[0].action_refs == ["communications.smtp.email.send"]
    grants = plan.grant_snapshot_json["mcp_tool_grants"]
    grant_pairs = {
        (grant["step_id"], tool)
        for grant in grants
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }
    assert grant_pairs == {
        ("notify", "action.execute"),
        ("notify", "resource.upsert"),
        ("notify", "context.query"),
        ("notify", "artifact.create"),
        ("notify", "artifact.update"),
        ("notify", "artifact.archive"),
        ("notify", "artifact.supersede"),
    }
    action_grant = next(grant for grant in grants if grant["tool"] == "action.execute")
    resource_grant = next(grant for grant in grants if grant["tool"] == "resource.upsert")
    context_grant = next(grant for grant in grants if grant["tool"] == "context.query")
    assert action_grant["action_refs"] == ["communications.smtp.email.send"]
    assert resource_grant["resource_key"] == "communication-delivery"
    assert context_grant["sources"] == ["agent_requests"]
    assert context_grant["fields"] == ["status", "summary"]


def test_run_plan_schema_allows_explicit_artifact_grant_policy_without_output_grants() -> None:
    template = WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": "branding.iterative-content",
            "name": "Iterative content",
            "version": "0.1.0",
            "metadata": {"artifact_grant_policy": "explicit"},
            "steps": [
                {
                    "id": "draft",
                    "title": "Draft",
                    "output_refs": ["draft_output"],
                }
            ],
            "outputs": [{"key": "draft_output", "type": "object"}],
        }
    )
    plan = run_plan_from_template(
        LoadedWorkflowTemplate(
            summary=WorkflowTemplateSummaryOut(
                key=template.key,
                name=template.name,
                version=template.version,
                source="plugin",
                precedence=10,
                plugin_slug="branding",
            ),
            spec=template,
        )
    )

    result = validate_run_plan_obj(plan.model_dump(mode="json"))

    assert result.valid is True
    assert result.warnings == []
    assert plan.grant_snapshot_json["artifact_grant_policy"] == "explicit"
    assert plan.grant_snapshot_json["mcp_tool_grants"] == []


def test_run_plan_schema_requires_communication_reply_sources() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "communication.reply"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "sources" in result.errors[0].message


def test_run_plan_schema_accepts_communication_reply_with_sources() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [
            {
                "step_id": "create-campaign",
                "tool": "communication.reply",
                "sources": ["telegram-bot", "slack-bot"],
            }
        ],
    }

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
