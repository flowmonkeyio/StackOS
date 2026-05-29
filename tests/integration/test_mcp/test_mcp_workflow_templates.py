"""MCP tests for StackOS workflow template tools."""

from __future__ import annotations

from pathlib import Path

from .conftest import MCPClient


def _template_json(key: str = "company.review") -> dict:
    return {
        "schema_version": "stackos.workflow-template.v1",
        "key": key,
        "name": "Company Review",
        "version": "0.1.0",
        "steps": [{"id": "review", "title": "Review"}],
        "outputs": [{"key": "summary", "type": "object"}],
    }


def test_workflow_template_read_tools_are_callable(
    mcp_client: MCPClient,
    tmp_path: Path,
) -> None:
    override = tmp_path / ".stackos" / "workflows" / "project-memory-review.yaml"
    override.parent.mkdir(parents=True)
    override.write_text(
        """
schema_version: stackos.workflow-template.v1
key: core.project-memory-review
name: Repo Project Memory Review
version: 0.1.0
steps:
  - id: review
    title: Review from repo
outputs:
  - key: summary
    type: object
""",
        encoding="utf-8",
    )

    listing = mcp_client.call_tool_structured(
        "workflowTemplate.list",
        {"repo_root": str(tmp_path), "include_shadowed": True},
    )
    described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {"key": "core.project-memory-review", "repo_root": str(tmp_path)},
    )
    validation = mcp_client.call_tool_structured(
        "workflowTemplate.validate",
        {"template_json": _template_json()},
    )
    validation_by_key = mcp_client.call_tool_structured(
        "workflowTemplate.validate",
        {"key": "core.project-memory-review", "repo_root": str(tmp_path)},
    )

    sources = {
        item["source"]
        for item in listing["templates"]
        if item["key"] == "core.project-memory-review"
    }
    assert sources == {"plugin", "repo"}
    assert "gtm.account-research" in {item["key"] for item in listing["templates"]}
    assert "engineering.customer-support-investigation" in {
        item["key"] for item in listing["templates"]
    }
    assert "engineering.tracked-delivery" in {item["key"] for item in listing["templates"]}
    assert "media-buying.campaign-launch" in {item["key"] for item in listing["templates"]}
    assert described["summary"]["source"] == "repo"
    assert described["summary"]["name"] == "Repo Project Memory Review"
    assert validation["valid"] is True
    assert validation["template"]["key"] == "company.review"
    assert validation_by_key["valid"] is True
    assert validation_by_key["template"]["key"] == "core.project-memory-review"
    assert validation_by_key["template"]["name"] == "Repo Project Memory Review"

    gtm_listing = mcp_client.call_tool_structured(
        "workflowTemplate.list",
        {"plugin_slug": "gtm"},
    )
    assert {item["key"] for item in gtm_listing["templates"]} >= {
        "gtm.account-research",
        "gtm.pipeline-risk-review",
    }
    gtm_described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {"key": "gtm.account-research", "plugin_slug": "gtm"},
    )
    assert gtm_described["spec"]["agent_requirements"][0]["agent_preset_ref"] == (
        "gtm.workflow.account-research"
    )
    assert gtm_described["spec"]["skill_requirements"][0]["skill_ref"] == "stackos:stackos"

    engineering_listing = mcp_client.call_tool_structured(
        "workflowTemplate.list",
        {"plugin_slug": "engineering"},
    )
    assert [item["key"] for item in engineering_listing["templates"]] == [
        "engineering.customer-support-investigation",
        "engineering.tracked-delivery",
    ]
    support_described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {
            "key": "engineering.customer-support-investigation",
            "plugin_slug": "engineering",
        },
    )
    assert support_described["spec"]["agent_requirements"][0]["agent_preset_ref"] == (
        "communications.workflow.customer-support-thread"
    )
    support_agent_refs = {
        item["agent_preset_ref"] for item in support_described["spec"]["agent_requirements"]
    }
    assert support_agent_refs == {
        "communications.workflow.customer-support-thread",
        "stackos.sdlc.support-investigation-analyst",
        "stackos.sdlc.codebase-explorer",
        "stackos.sdlc.planning",
        "stackos.sdlc.delivery-reviewer",
    }
    assert support_described["spec"]["metadata_json"]["workflow_family"] == (
        "customer_support_investigation"
    )
    assert support_described["spec"]["metadata_json"]["handoff_workflow"] == (
        "engineering.tracked-delivery"
    )
    assert support_described["spec"]["metadata_json"]["canonical_surface"] == "slack_thread"
    assert "chat_reference_continuity" in {
        item["key"] for item in support_described["spec"]["policies"]
    }
    assert "media_handoff_fidelity" in {
        item["key"] for item in support_described["spec"]["policies"]
    }
    assert [item["id"] for item in support_described["spec"]["steps"]] == [
        "intake-feedback",
        "establish-canonical-thread",
        "add-investigation-reaction",
        "read-full-thread",
        "ask-thread-clarifications",
        "investigate-support-issue",
        "post-support-conclusion-in-thread",
        "wait-for-thread-instructions",
        "create-delivery-task",
        "post-task-handoff-in-thread",
        "add-task-created-reaction",
        "conclude-and-handoff",
    ]
    assert all(not item["approval_refs"] for item in support_described["spec"]["steps"])
    support_steps = {item["id"]: item for item in support_described["spec"]["steps"]}
    assert "source_media_refs" in support_steps["intake-feedback"]["input_refs"]
    assert "communicationTarget.resolve" in " ".join(
        support_steps["intake-feedback"]["instructions"]
    )
    assert "target resolution alone is not enough" in " ".join(
        support_steps["establish-canonical-thread"]["instructions"]
    )
    assert "Preflight media forwarding" in " ".join(
        support_steps["establish-canonical-thread"]["instructions"]
    )
    engineering_described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {"key": "engineering.tracked-delivery", "plugin_slug": "engineering"},
    )
    assert engineering_described["spec"]["agent_requirements"][0]["agent_preset_ref"] == (
        "stackos.sdlc.requirements-flow-definer"
    )
    engineering_agent_refs = {
        item["agent_preset_ref"] for item in engineering_described["spec"]["agent_requirements"]
    }
    assert engineering_agent_refs == {
        "stackos.sdlc.requirements-flow-definer",
        "stackos.sdlc.codebase-explorer",
        "stackos.sdlc.planning",
        "stackos.sdlc.architecture",
        "stackos.sdlc.test-designer",
        "stackos.sdlc.delivery",
        "stackos.sdlc.delivery-reviewer",
        "stackos.sdlc.release-ops",
    }
    assert engineering_described["spec"]["skill_requirements"][0]["skill_ref"] == (
        "stackos:stackos"
    )

    media_listing = mcp_client.call_tool_structured(
        "workflowTemplate.list",
        {"plugin_slug": "media-buying"},
    )
    assert {item["key"] for item in media_listing["templates"]} >= {
        "media-buying.campaign-launch",
        "media-buying.performance-diagnosis",
    }


def test_workflow_template_validate_rejects_secrets(mcp_client: MCPClient) -> None:
    template = _template_json()
    template["metadata"] = {"api_key": "value"}

    validation = mcp_client.call_tool_structured(
        "workflowTemplate.validate",
        {"template_json": template},
    )

    assert validation["valid"] is False
    assert "must not contain secrets" in validation["errors"][0]["message"]


def test_workflow_template_validate_rejects_ambiguous_key_aliases(
    mcp_client: MCPClient,
) -> None:
    validation = mcp_client.call_tool_structured(
        "workflowTemplate.validate",
        {"key": "core.project-memory-review", "workflow_key": "seo.keyword-research"},
    )

    assert validation["valid"] is False
    assert validation["errors"][0]["code"] == "ambiguous_template_key"


def test_workflow_extension_tools_configure_project_overlay(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    missing = mcp_client.call_tool_structured(
        "workflowExtension.get",
        {
            "project_id": project_id,
            "workflow_key": "engineering.customer-support-investigation",
        },
    )
    validation = mcp_client.call_tool_structured(
        "workflowExtension.validate",
        {
            "project_id": project_id,
            "workflow_key": "engineering.customer-support-investigation",
            "plugin_slug": "engineering",
            "required_input_keys_json": ["feedback_summary", "communication_route_ref"],
            "input_defaults_json": {
                "communication_route_ref": "communication-route:support-feedback",
                "canonical_slack_target_ref": "communication-target:support-triage",
            },
            "step_overrides_json": {
                "establish-canonical-thread": {
                    "extra_instructions": ["Use the configured support route."]
                }
            },
            "template_overrides_json": {
                "description": "Project-specific support investigation flow."
            },
        },
    )
    upserted = mcp_client.call_tool_structured(
        "workflowExtension.upsert",
        {
            "project_id": project_id,
            "workflow_key": "engineering.customer-support-investigation",
            "plugin_slug": "engineering",
            "required_input_keys_json": ["feedback_summary", "communication_route_ref"],
            "input_defaults_json": {
                "communication_route_ref": "communication-route:support-feedback",
                "canonical_slack_target_ref": "communication-target:support-triage",
            },
            "selected_context_json": {
                "communication": {
                    "route_ref": "communication-route:support-feedback",
                    "target_ref": "communication-target:support-triage",
                }
            },
            "step_overrides_json": {
                "establish-canonical-thread": {
                    "extra_instructions": ["Use the configured support route."]
                }
            },
            "template_overrides_json": {
                "description": "Project-specific support investigation flow."
            },
        },
    )
    listed = mcp_client.call_tool_structured(
        "workflowExtension.list",
        {"project_id": project_id},
    )
    described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {
            "project_id": project_id,
            "key": "engineering.customer-support-investigation",
            "plugin_slug": "engineering",
        },
    )

    assert missing["extension"] is None
    assert validation["valid"] is True
    assert upserted["data"]["workflow_key"] == "engineering.customer-support-investigation"
    assert upserted["data"]["template_overrides_json"]["description"] == (
        "Project-specific support investigation flow."
    )
    assert listed["extensions"][0]["id"] == upserted["data"]["id"]
    assert described["project_extension"]["id"] == upserted["data"]["id"]
    assert described["spec"]["description"] == "Project-specific support investigation flow."
    assert described["summary"]["project_extension_enabled"] is True


def test_workflow_template_writes_are_registered_but_not_system_granted(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    for tool_name, arguments in [
        (
            "workflowTemplate.save",
            {"project_id": project_id, "template_json": _template_json()},
        ),
        (
            "workflowTemplate.fork",
            {
                "project_id": project_id,
                "key": "core.project-memory-review",
                "new_key": "company.project-memory-review",
            },
        ),
    ]:
        err = mcp_client.call_tool_error(tool_name, arguments)
        assert err["code"] == -32007
        assert err["message"] == "ToolNotGrantedError"
