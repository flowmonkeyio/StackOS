"""MCP tests for scoped workflow/action readiness."""

from __future__ import annotations

from .conftest import MCPClient


def test_readiness_check_reports_ready_no_auth_action(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {"project_id": project_id, "action_ref": "utils.sitemap.fetch"},
    )

    assert readiness["scope"] == "action"
    assert readiness["ready"] is True
    assert readiness["execution_ready"] is True
    assert readiness["missing"] == []
    assert readiness["action"]["action_ref"] == "utils.sitemap.fetch"
    assert [step["tool"] for step in readiness["next_steps"]] == [
        "action.validate",
        "action.run",
    ]


def test_readiness_check_reports_scoped_action_missing_setup(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {"project_id": project_id, "action_ref": "utils.image.generate"},
    )

    assert readiness["scope"] == "action"
    assert readiness["ready"] is False
    assert readiness["execution_ready"] is False
    missing_codes = {item["code"] for item in readiness["missing"]}
    assert {"credential_required", "budget_required"} <= missing_codes
    credential = next(
        item for item in readiness["missing"] if item["code"] == "credential_required"
    )
    assert credential["provider_key"] == "openai-images"
    assert credential["ui_url"].endswith(f"/projects/{project_id}/connections")


def test_readiness_check_keeps_engineering_workflow_usable_without_provider_noise(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {"project_id": project_id, "workflow_key": "engineering.tracked-delivery"},
    )

    assert readiness["scope"] == "workflow"
    assert readiness["ready"] is True
    assert readiness["execution_ready"] is True
    assert readiness["missing"] == []
    assert readiness["workflow"]["workflow_key"] == "engineering.tracked-delivery"
    assert "planning" in readiness["workflow"]["required_agent_roles"]
    assert readiness["next_steps"][0]["tool"] == "runPlan.create"


def test_readiness_check_reports_customer_support_workflow_slack_setup(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {"project_id": project_id, "workflow_key": "communications.customer-feedback-intake"},
    )

    assert readiness["scope"] == "workflow"
    assert readiness["ready"] is True
    assert readiness["execution_ready"] is False
    assert readiness["workflow"]["workflow_key"] == "communications.customer-feedback-intake"
    assert (
        "communications-customer-feedback-intake" in readiness["workflow"]["required_agent_roles"]
    )
    providers = {item["provider_key"] for item in readiness["missing"]}
    assert providers == {"slack-bot", "telegram-bot"}
    required_providers = {
        item["provider_key"]
        for item in readiness["missing"]
        if item["required_for"] == "action_execution"
    }
    optional_providers = {
        item["provider_key"]
        for item in readiness["missing"]
        if item["required_for"] == "action_execution_optional_provider"
    }
    assert required_providers == {"slack-bot"}
    assert optional_providers == {"telegram-bot"}
    assert readiness["next_steps"][0]["tool"] == "runPlan.create"


def test_readiness_check_reports_only_selected_workflow_provider_gaps(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {"project_id": project_id, "workflow_key": "seo.keyword-research"},
    )

    assert readiness["scope"] == "workflow"
    assert readiness["ready"] is True
    assert readiness["execution_ready"] is False
    providers = {item["provider_key"] for item in readiness["missing"]}
    assert providers == {"dataforseo", "ahrefs"}
    assert "openai-images" not in providers
    dataforseo = next(item for item in readiness["missing"] if item["provider_key"] == "dataforseo")
    assert {"seo.keyword.research", "seo.serp.analyze"} <= set(dataforseo["action_refs"])
    assert readiness["next_steps"][0]["tool"] == "runPlan.create"
    assert readiness["next_steps"][1]["tool"] == "auth.status"


def test_readiness_check_resolves_cross_plugin_utility_action_contracts(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    gtm = mcp_client.call_tool_structured(
        "readiness.check",
        {"project_id": project_id, "workflow_key": "gtm.account-research"},
    )
    media = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "media-buying.creative-variant-generation",
        },
    )

    gtm_refs = {item["action_ref"] for item in gtm["actions"]}
    media_refs = {item["action_ref"] for item in media["actions"]}
    assert "utils.web.read" in gtm_refs
    assert "utils.web.scrape" in gtm_refs
    assert "gtm.web.read" not in gtm_refs
    assert "utils.image.generate" in media_refs
    assert "media-buying.image.generate" not in media_refs
    assert all(item["code"] != "action_not_found" for item in gtm["missing"])
    assert all(item["code"] != "action_not_found" for item in media["missing"])
