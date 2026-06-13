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
    authoring_guide = mcp_client.call_tool_structured("workflowTemplate.authoringGuide", {})

    sources = {
        item["source"]
        for item in listing["templates"]
        if item["key"] == "core.project-memory-review"
    }
    assert sources == {"plugin", "repo"}
    assert "gtm.account-research" in {item["key"] for item in listing["templates"]}
    assert "communications.customer-feedback-intake" in {
        item["key"] for item in listing["templates"]
    }
    assert "support.issue-investigation" in {item["key"] for item in listing["templates"]}
    assert "support.delivery-task-handoff" in {item["key"] for item in listing["templates"]}
    assert "engineering.tracked-delivery" in {item["key"] for item in listing["templates"]}
    assert "media-buying.campaign-launch" in {item["key"] for item in listing["templates"]}
    assert described["summary"]["source"] == "repo"
    assert described["summary"]["name"] == "Repo Project Memory Review"
    assert validation["valid"] is True
    assert validation["template"]["key"] == "company.review"
    assert validation_by_key["valid"] is True
    assert validation_by_key["template"]["key"] == "core.project-memory-review"
    assert validation_by_key["template"]["name"] == "Repo Project Memory Review"
    assert authoring_guide["source_of_truth_operation"] == "workflowTemplate.authoringGuide"
    assert "Plugin manifest entries" in authoring_guide["complete_package_scope"][0]
    assert any(
        "agent presets, skill presets" in item for item in authoring_guide["package_authoring_path"]
    )
    assert any("one end-to-end job" in item for item in authoring_guide["principles"])
    assert any(
        "several new workflow templates" in item
        for item in authoring_guide["package_authoring_path"]
    )
    assert any("future memory" in item for item in authoring_guide["package_authoring_path"])
    assert any("private workflow chain" in item for item in authoring_guide["reasoning_gates"])
    assert any("readiness.check" in item for item in authoring_guide["mechanical_gates"])
    assert any("official provider docs" in item for item in authoring_guide["independent_signoff"])
    assert "workflowTemplate.validate" in {
        item["name"] for item in authoring_guide["canonical_operations"]
    }

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
        "engineering.tracked-delivery",
    ]
    intake_described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {
            "key": "communications.customer-feedback-intake",
            "plugin_slug": "communications",
        },
    )
    assert intake_described["spec"]["agent_requirements"][0]["agent_preset_ref"] == (
        "communications.workflow.customer-feedback-intake"
    )
    assert [item["id"] for item in intake_described["spec"]["steps"]] == [
        "capture-feedback",
        "establish-canonical-thread",
        "add-intake-reaction",
        "prepare-investigation-handoff",
    ]
    assert intake_described["spec"]["metadata_json"]["next_workflow"] == (
        "support.issue-investigation"
    )

    support_listing = mcp_client.call_tool_structured(
        "workflowTemplate.list",
        {"plugin_slug": "support"},
    )
    assert [item["key"] for item in support_listing["templates"]] == [
        "support.delivery-task-handoff",
        "support.issue-investigation",
    ]
    support_described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {"key": "support.issue-investigation", "plugin_slug": "support"},
    )
    support_agent_refs = {
        item["agent_preset_ref"] for item in support_described["spec"]["agent_requirements"]
    }
    assert support_agent_refs == {
        "support.workflow.issue-investigator",
        "stackos.sdlc.codebase-explorer",
    }
    assert support_described["spec"]["metadata_json"]["previous_workflow"] == (
        "communications.customer-feedback-intake"
    )
    assert support_described["spec"]["metadata_json"]["next_workflow"] == (
        "support.delivery-task-handoff"
    )
    assert "full_thread_source_of_truth" in {
        item["key"] for item in support_described["spec"]["policies"]
    }
    assert "no_task_creation_in_investigation" in {
        item["key"] for item in support_described["spec"]["policies"]
    }
    assert [item["id"] for item in support_described["spec"]["steps"]] == [
        "read-canonical-thread",
        "clarify-missing-context",
        "investigate-issue",
        "post-support-conclusion",
    ]
    handoff_described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {"key": "support.delivery-task-handoff", "plugin_slug": "support"},
    )
    assert handoff_described["spec"]["metadata_json"]["next_workflow"] == (
        "engineering.tracked-delivery"
    )
    assert [item["id"] for item in handoff_described["spec"]["steps"]] == [
        "confirm-thread-instruction",
        "create-delivery-task",
        "post-task-handoff",
        "add-task-created-reaction",
    ]
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
    }
    engineering_skill_refs = {
        item["skill_ref"] for item in engineering_described["spec"]["skill_requirements"]
    }
    assert engineering_skill_refs == {"stackos:stackos"}
    engineering_skill_preset_refs = {
        item["skill_preset_ref"]
        for item in engineering_described["spec"]["skill_preset_requirements"]
    }
    assert engineering_skill_preset_refs == {"stackos.sdlc.delivery-orchestrator"}
    engineering_text = str(engineering_described["spec"])
    assert engineering_described["spec"]["metadata_json"]["workflow_selection_invariant"] == (
        "explicit_workflow_intent_requires_run_plan_before_tracker_tickets"
    )
    assert "workflow_selection_precedence" in engineering_text
    assert "workflow-backed run plan before creating tracker tickets" in engineering_text
    assert "direct tracker task and a later workflow task" in engineering_text

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
            "workflow_key": "communications.customer-feedback-intake",
        },
    )
    validation = mcp_client.call_tool_structured(
        "workflowExtension.validate",
        {
            "project_id": project_id,
            "workflow_key": "communications.customer-feedback-intake",
            "plugin_slug": "communications",
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
            "template_overrides_json": {"description": "Project-specific support intake flow."},
            "response_mode": "raw",
        },
    )
    upserted = mcp_client.call_tool_structured(
        "workflowExtension.upsert",
        {
            "project_id": project_id,
            "workflow_key": "communications.customer-feedback-intake",
            "plugin_slug": "communications",
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
            "template_overrides_json": {"description": "Project-specific support intake flow."},
            "response_mode": "raw",
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
            "key": "communications.customer-feedback-intake",
            "plugin_slug": "communications",
        },
    )

    assert missing["extension"] is None
    assert validation["valid"] is True
    assert upserted["data"]["workflow_key"] == "communications.customer-feedback-intake"
    assert upserted["data"]["template_overrides_json"]["description"] == (
        "Project-specific support intake flow."
    )
    assert listed["extensions"][0]["id"] == upserted["data"]["id"]
    assert described["project_extension"]["id"] == upserted["data"]["id"]
    assert described["spec"]["description"] == "Project-specific support intake flow."
    assert described["summary"]["project_extension_enabled"] is True

    deleted = mcp_client.call_tool_structured(
        "workflowExtension.delete",
        {
            "project_id": project_id,
            "workflow_key": "communications.customer-feedback-intake",
        },
    )
    after_delete = mcp_client.call_tool_structured(
        "workflowExtension.get",
        {
            "project_id": project_id,
            "workflow_key": "communications.customer-feedback-intake",
        },
    )
    assert deleted["data"]["deleted"]["id"] == upserted["data"]["id"]
    assert after_delete["extension"] is None


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


def test_marketing_campaign_production_template_validates_and_describes(
    mcp_client: MCPClient,
) -> None:
    validation = mcp_client.call_tool_structured(
        "workflowTemplate.validate",
        {"key": "marketing.campaign-production"},
    )
    described = mcp_client.call_tool_structured(
        "workflowTemplate.describe",
        {"key": "marketing.campaign-production", "plugin_slug": "marketing"},
    )

    assert validation["valid"] is True
    assert validation["template"]["key"] == "marketing.campaign-production"

    spec = described["spec"]
    assert [item["id"] for item in spec["steps"]] == [
        "intake-brief",
        "setup-workspace",
        "plan-campaign",
        "produce-media",
        "build-landing-pages",
        "visual-signoff",
        "build-gallery",
        "closeout",
    ]
    assert {item["agent_preset_ref"] for item in spec["agent_requirements"]} == {
        "marketing.campaign.brief-analyst",
        "marketing.campaign.creative-director",
        "marketing.campaign.media-producer",
        "marketing.campaign.landing-page-builder",
        "marketing.campaign.visual-signoff-reviewer",
    }
    assert spec["skill_preset_requirements"][0]["skill_preset_ref"] == (
        "marketing.campaign-production-orchestrator"
    )
    media_request = next(item for item in spec["inputs"] if item["key"] == "media_request")
    assert media_request["required"] is True
    gates = {gate["key"]: gate for gate in spec["approval_gates"]}
    assert gates["plan_confirmation"]["required_when"] == "always"
    produce_media = next(step for step in spec["steps"] if step["id"] == "produce-media")
    assert "plan_confirmation" in produce_media["approval_refs"]
    assert produce_media["action_refs"] == [
        "image_generate",
        "image_edit",
        "google_video_generate",
        "byteplus_video_generate",
        "alibaba_video_generate",
        "kling_video_generate",
        "xai_video_generate",
    ]
    actions = {contract["action"] for contract in spec["action_contracts"]}
    assert actions == {
        "image.generate",
        "image.edit",
        "google.video.generate",
        "byteplus.video.generate",
        "alibaba.video.generate",
        "kling.video.generate",
        "xai.video.generate",
    }
    shared_resources = {contract["resource"] for contract in spec["resource_contracts"]}
    assert {"creative", "landing-page", "campaign-brief", "campaign-evidence"} <= shared_resources
