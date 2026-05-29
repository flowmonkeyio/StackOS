"""MCP tests for StackOS agent preset tools."""

from __future__ import annotations

from .conftest import MCPClient


def test_agent_preset_tools_are_callable(mcp_client: MCPClient) -> None:
    listing = mcp_client.call_tool_structured(
        "agentPreset.list",
        {"workflow_key": "core.project-memory-review"},
    )
    described = mcp_client.call_tool_structured(
        "agentPreset.describe",
        {"key": "stackos.sdlc.planning"},
    )
    resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {"workflow_key": "core.project-memory-review"},
    )
    engineering_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {"workflow_key": "engineering.tracked-delivery", "plugin_slug": "engineering"},
    )
    intake_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "communications.customer-feedback-intake",
            "plugin_slug": "communications",
        },
    )
    support_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "support.issue-investigation",
            "plugin_slug": "support",
        },
    )
    handoff_resolved = mcp_client.call_tool_structured(
        "agentPreset.resolveForWorkflow",
        {
            "workflow_key": "support.delivery-task-handoff",
            "plugin_slug": "support",
        },
    )

    assert "stackos.workflow.project-memory-review" in {item["key"] for item in listing["presets"]}
    assert described["preset"]["summary"]["key"] == "stackos.sdlc.planning"
    assert described["preset"]["summary"]["plugin_slug"] == "engineering"
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
        "stackos.sdlc.release-ops",
    }
    assert engineering_resolved["skill_requirements"][0]["skill_ref"] == "stackos:stackos"
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
