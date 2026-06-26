"""MCP tests for StackOS agent preset tools."""

from __future__ import annotations

from .conftest import MCPClient


def test_agent_preset_tools_are_callable(mcp_client: MCPClient) -> None:
    listing = mcp_client.call_tool_structured(
        "agentPreset.list",
        {"workflow_key": "core.project-memory-review", "response_mode": "raw"},
    )
    described = mcp_client.call_tool_structured(
        "agentPreset.describe",
        {"key": "stackos.sdlc.planning", "response_mode": "raw"},
    )
    resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {"workflow_key": "core.project-memory-review", "response_mode": "raw"},
    )
    engineering_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "engineering.tracked-delivery",
            "plugin_slug": "engineering",
            "response_mode": "raw",
        },
    )
    intake_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "communications.customer-feedback-intake",
            "plugin_slug": "communications",
            "response_mode": "raw",
        },
    )
    support_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "support.issue-investigation",
            "plugin_slug": "support",
            "response_mode": "raw",
        },
    )
    handoff_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "support.delivery-task-handoff",
            "plugin_slug": "support",
            "response_mode": "raw",
        },
    )
    skill_listing = mcp_client.call_tool_structured(
        "skillPreset.list",
        {"workflow_key": "engineering.tracked-delivery", "response_mode": "raw"},
    )
    skill_described = mcp_client.call_tool_structured(
        "skillPreset.describe",
        {"key": "stackos.sdlc.delivery-orchestrator", "response_mode": "raw"},
    )
    skill_resolved = mcp_client.call_tool_structured(
        "skillPreset.resolveForWorkflow",
        {
            "workflow_key": "engineering.tracked-delivery",
            "plugin_slug": "engineering",
            "response_mode": "raw",
        },
    )

    assert "stackos.workflow.project-memory-review" in {item["key"] for item in listing["presets"]}
    assert "stackos.sdlc.delivery-orchestrator" in {
        item["key"] for item in skill_listing["presets"]
    }
    assert skill_described["preset"]["summary"]["key"] == "stackos.sdlc.delivery-orchestrator"
    assert skill_described["preset"]["summary"]["plugin_slug"] == "engineering"
    assert skill_described["project_adaptation"]["adaptation_required"] is True
    assert skill_resolved["required_skill_presets"][0]["preset"]["summary"]["key"] == (
        "stackos.sdlc.delivery-orchestrator"
    )
    assert described["preset"]["summary"]["key"] == "stackos.sdlc.planning"
    assert described["preset"]["summary"]["plugin_slug"] == "engineering"
    planning_contract = described["preset"]["preset"]["prompt_contract"]
    assert "workflow graph check" in " ".join(planning_contract["responsibilities"])
    planning_text = " ".join(planning_contract["must_do"])
    assert "attachment/provenance only" in planning_text
    assert "tracker.updateTicket" in planning_text
    planning_text = " ".join(
        [
            *planning_contract["responsibilities"],
            *planning_contract["must_do"],
            *planning_contract["handoff_outputs"],
            *planning_contract["success_criteria"],
            *planning_contract["self_check"],
        ]
    ).lower()
    assert "workflow-backed run plan before tracker.createtask" in planning_text
    assert "direct tracker tasks only" in planning_text
    assert "canonical workflow-backed task/run plan" in planning_text
    reviewer = mcp_client.call_tool_structured(
        "agentPreset.describe",
        {"key": "stackos.sdlc.delivery-reviewer", "response_mode": "raw"},
    )
    reviewer_contract = reviewer["preset"]["preset"]["prompt_contract"]
    assert "blocking findings" in " ".join(reviewer_contract["responsibilities"])
    assert "detached workflow spine" in " ".join(reviewer_contract["self_check"])
    assert described["project_adaptation"]["adaptation_required"] is True
    assert described["project_adaptation"]["do_not_use_verbatim"] is True
    assert "tracker" in described["project_adaptation"]["required_agent_action"].lower()
    assert resolved["workflow"]["key"] == "core.project-memory-review"
    assert resolved["required_agents"][0]["preset"]["summary"]["key"] == (
        "stackos.workflow.project-memory-review"
    )
    assert resolved["required_agents"][0]["preset"]["summary"]["plugin_slug"] == "core"
    assert resolved["recommended_agents"][0]["preset"]["summary"]["key"] == (
        "stackos.sdlc.planning"
    )
    assert resolved["skill_requirements"][0]["skill_ref"] == "stackos:stackos"
    assert "Workflow templates are inert" in " ".join(resolved["setup_guidance"])
    assert engineering_resolved["workflow"]["key"] == "engineering.tracked-delivery"
    required_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in engineering_resolved["required_agents"]
    }
    assert required_agent_keys == {
        "stackos.sdlc.requirements-flow-definer",
        "stackos.sdlc.planning",
        "stackos.sdlc.architecture",
        "stackos.sdlc.test-designer",
        "stackos.sdlc.delivery",
        "stackos.sdlc.delivery-reviewer",
    }
    recommended_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in engineering_resolved["recommended_agents"]
    }
    assert recommended_agent_keys == {
        "stackos.sdlc.codebase-explorer",
    }
    engineering_skill_refs = {
        item["skill_ref"] for item in engineering_resolved["skill_requirements"]
    }
    assert engineering_skill_refs == {"stackos:stackos"}
    engineering_skill_preset_refs = {
        item["skill_preset_ref"] for item in engineering_resolved["skill_preset_requirements"]
    }
    assert engineering_skill_preset_refs == {"stackos.sdlc.delivery-orchestrator"}
    assert {
        item["preset"]["summary"]["key"] for item in engineering_resolved["required_skill_presets"]
    } == {"stackos.sdlc.delivery-orchestrator"}
    assert intake_resolved["workflow"]["key"] == "communications.customer-feedback-intake"
    intake_required_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in intake_resolved["required_agents"]
    }
    assert intake_required_agent_keys == {
        "communications.workflow.customer-feedback-intake",
    }
    assert intake_resolved["skill_requirements"][0]["skill_ref"] == "stackos:stackos"

    assert support_resolved["workflow"]["key"] == "support.issue-investigation"
    support_required_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in support_resolved["required_agents"]
    }
    assert support_required_agent_keys == {
        "support.workflow.issue-investigator",
    }
    support_recommended_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in support_resolved["recommended_agents"]
    }
    assert support_recommended_agent_keys == {
        "stackos.sdlc.codebase-explorer",
    }
    assert support_resolved["skill_requirements"][0]["skill_ref"] == "stackos:stackos"

    assert handoff_resolved["workflow"]["key"] == "support.delivery-task-handoff"
    handoff_required_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in handoff_resolved["required_agents"]
    }
    assert handoff_required_agent_keys == {
        "support.workflow.delivery-handoff",
    }
    handoff_recommended_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in handoff_resolved["recommended_agents"]
    }
    assert handoff_recommended_agent_keys == {
        "stackos.sdlc.planning",
    }
    handoff_tool_text = " ".join(
        handoff_required_agent_keys
        | {
            tool
            for agent in handoff_resolved["required_agents"]
            for tool in agent["preset"]["preset"]["recommended_tools"]
        }
    )
    assert "action.execute" in handoff_tool_text


def test_branding_content_production_presets_resolve_end_to_end(
    mcp_client: MCPClient,
) -> None:
    resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "branding.content-production",
            "plugin_slug": "branding",
            "response_mode": "raw",
        },
    )
    skill_resolved = mcp_client.call_tool_structured(
        "skillPreset.resolveForWorkflow",
        {
            "workflow_key": "branding.content-production",
            "plugin_slug": "branding",
            "response_mode": "raw",
        },
    )

    assert resolved["workflow"]["key"] == "branding.content-production"
    assert resolved["unresolved_requirements"] == []
    assert resolved["unresolved_skill_preset_requirements"] == []
    assert skill_resolved["unresolved_skill_preset_requirements"] == []
    required_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in resolved["required_agents"]
    }
    assert required_agent_keys == {
        "branding.evidence-curator",
        "branding.channel-strategist",
        "branding.narrative-writer",
        "branding.claim-auditor",
        "branding.voice-reviewer",
        "branding.sanitization-reviewer",
    }
    skill_preset = skill_resolved["required_skill_presets"][0]
    assert skill_preset["preset"]["summary"]["key"] == "branding.brand-orchestrator"
    assert skill_preset["applies_to_steps"] == [
        "interview-capture",
        "research-fact-collection",
        "angle-and-structure",
        "draft-canonical",
        "editorial-review",
        "sanitization-review",
        "produce-optional-images",
        "render-channel-packets",
        "approve-and-record",
        "execute-publication",
    ]
    recommended_tools = set(skill_preset["preset"]["preset"]["recommended_tools"])
    assert {
        "workspace.startSession",
        "runPlan.start",
        "readiness.check",
        "agentPreset.resolveForWorkflow",
        "skillPreset.resolveForWorkflow",
    } <= recommended_tools


def test_marketing_campaign_production_presets_resolve_end_to_end(
    mcp_client: MCPClient,
) -> None:
    resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "marketing.campaign-production",
            "plugin_slug": "marketing",
            "response_mode": "raw",
        },
    )
    skill_resolved = mcp_client.call_tool_structured(
        "skillPreset.resolveForWorkflow",
        {
            "workflow_key": "marketing.campaign-production",
            "plugin_slug": "marketing",
            "response_mode": "raw",
        },
    )

    assert resolved["workflow"]["key"] == "marketing.campaign-production"
    required_agent_keys = {
        agent["preset"]["summary"]["key"] for agent in resolved["required_agents"]
    }
    assert required_agent_keys == {
        "marketing.campaign.brief-analyst",
        "marketing.campaign.creative-director",
        "marketing.campaign.media-producer",
        "marketing.campaign.landing-page-builder",
        "marketing.campaign.visual-signoff-reviewer",
    }
    assert resolved["unresolved_requirements"] == []
    assert resolved["unresolved_skill_preset_requirements"] == []
    assert all(
        agent["preset"]["summary"]["plugin_slug"] == "marketing"
        for agent in resolved["required_agents"]
    )

    assert skill_resolved["required_skill_presets"][0]["preset"]["summary"]["key"] == (
        "marketing.campaign-production-orchestrator"
    )
    assert skill_resolved["unresolved_skill_preset_requirements"] == []

    producer = mcp_client.call_tool_structured(
        "agentPreset.describe",
        {"key": "marketing.campaign.media-producer", "response_mode": "raw"},
    )
    producer_contract = producer["preset"]["preset"]["prompt_contract"]
    producer_text = " ".join(
        [
            *producer_contract["responsibilities"],
            *producer_contract["must_do"],
            *producer_contract["must_not_do"],
        ]
    ).lower()
    assert "preserve" in producer_text
    assert producer["project_adaptation"]["adaptation_required"] is True
