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
        {
            "project_id": project_id,
            "action_ref": "utils.sitemap.fetch",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "action"
    assert "ready" not in readiness
    assert readiness["structurally_ready"] is True
    assert readiness["context_status"] == "ready"
    assert readiness["required_providers_ready"] is True
    assert readiness["execution_ready"] is True
    assert readiness["missing"] == []
    assert readiness["action"]["action_ref"] == "utils.sitemap.fetch"
    assert [step["tool"] for step in readiness["next_steps"]] == [
        "action.validate",
        "action.run",
    ]


def test_readiness_check_accepts_advertised_raw_response_mode(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "action_ref": "utils.sitemap.fetch",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "action"
    assert "ready" not in readiness
    assert readiness["actions"][0]["action_ref"] == "utils.sitemap.fetch"


def test_readiness_check_reports_scoped_action_missing_setup(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "action"
    assert "ready" not in readiness
    assert readiness["execution_ready"] is False
    missing_codes = {item["code"] for item in readiness["missing"]}
    assert {"credential_required", "budget_required"} <= missing_codes
    credential = next(
        item for item in readiness["missing"] if item["code"] == "credential_required"
    )
    assert credential["provider_key"] == "openai-images"
    assert credential["ui_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert credential["setup"]["local_setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert credential["setup"]["api_key_url"] == "https://platform.openai.com/api-keys"
    assert credential["setup"]["signup_url"] == "https://platform.openai.com/signup"
    assert credential["setup"]["docs_url"].endswith("/image-generation")
    assert readiness["next_steps"][0]["setup"]["api_key_url"] == (
        "https://platform.openai.com/api-keys"
    )
    assert readiness["next_steps"][0]["arguments"]["provider_key"] == "openai-images"


def test_readiness_check_keeps_engineering_workflow_usable_without_provider_noise(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "engineering.tracked-delivery",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "workflow"
    assert "ready" not in readiness
    assert readiness["structurally_ready"] is True
    assert readiness["context_status"] == "not_evaluated"
    assert readiness["required_providers_ready"] is True
    assert readiness["execution_ready"] is False
    assert readiness["missing"] == []
    assert readiness["workflow"]["workflow_key"] == "engineering.tracked-delivery"
    assert "planning" in readiness["workflow"]["required_agent_roles"]
    assert readiness["next_steps"][0]["tool"] == "runPlan.create"


def test_readiness_check_branding_content_optional_image_repair_setup(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "branding.content-production",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "workflow"
    assert "ready" not in readiness
    assert readiness["context_status"] == "not_evaluated"
    assert readiness["required_providers_ready"] is True
    assert readiness["execution_ready"] is False
    assert readiness["workflow"]["workflow_key"] == "branding.content-production"
    refs = {item["action_ref"] for item in readiness["actions"]}
    assert "utils.image.generate" in refs
    assert readiness["missing"] == []
    image_action = next(
        item for item in readiness["actions"] if item["provider_key"] == "openai-images"
    )
    assert {item["required_for"] for item in image_action["missing"]} == {
        "optional_action_execution"
    }


def test_readiness_check_deferred_branding_action_points_to_action_describe(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "action_ref": "branding.publish.x",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "action"
    assert "ready" not in readiness
    assert readiness["execution_ready"] is False
    missing = readiness["missing"][0]
    assert missing["code"] == "execution_mode_not_directly_executable"
    assert missing["action_ref"] == "branding.publish.x"
    assert missing["setup"]["provider_key"] == "x-api"
    assert missing["setup"]["console_url"] == "https://developer.x.com/en/portal/dashboard"
    assert missing["ui_url"].endswith(f"/projects/{project_id}/connections?provider_key=x-api")
    assert readiness["next_steps"][0]["tool"] == "action.describe"
    assert readiness["next_steps"][0]["arguments"] == {
        "project_id": project_id,
        "action_ref": "branding.publish.x",
    }
    assert readiness["next_steps"][0]["setup"]["provider_key"] == "x-api"


def test_readiness_check_branding_content_optional_image_setup(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "branding.content-production",
            "response_mode": "raw",
        },
    )

    image_action = next(
        item for item in readiness["actions"] if item.get("action_ref") == "utils.image.generate"
    )
    image_missing = image_action["missing"]
    assert "ready" not in readiness
    assert readiness["context_status"] == "not_evaluated"
    assert readiness["required_providers_ready"] is True
    assert readiness["execution_ready"] is False
    assert {item["code"] for item in image_missing} == {"credential_required", "budget_required"}
    credential_missing = next(
        item for item in image_missing if item["code"] == "credential_required"
    )
    assert credential_missing["required_for"] == "optional_action_execution"
    assert credential_missing["setup"]["provider_key"] == "openai-images"


def test_readiness_check_reports_customer_support_workflow_slack_setup(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "communications.customer-feedback-intake",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "workflow"
    assert "ready" not in readiness
    assert readiness["execution_ready"] is False
    assert readiness["workflow"]["workflow_key"] == "communications.customer-feedback-intake"
    assert (
        "communications-customer-feedback-intake" in readiness["workflow"]["required_agent_roles"]
    )
    providers = {item["provider_key"] for item in readiness["missing"]}
    assert providers == {"slack-bot"}
    required_providers = {
        item["provider_key"]
        for item in readiness["missing"]
        if item["required_for"] == "action_execution"
    }
    assert required_providers == {"slack-bot"}
    telegram_action = next(
        item for item in readiness["actions"] if item["provider_key"] == "telegram-bot"
    )
    assert {item["required_for"] for item in telegram_action["missing"]} == {
        "optional_action_execution"
    }
    assert readiness["next_steps"][0]["tool"] == "runPlan.create"


def test_readiness_check_reports_only_selected_workflow_provider_gaps(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "seo.keyword-research",
            "response_mode": "raw",
        },
    )

    assert readiness["scope"] == "workflow"
    assert "ready" not in readiness
    assert readiness["execution_ready"] is False
    providers = {item["provider_key"] for item in readiness["missing"]}
    assert providers == set()
    assert "openai-images" not in providers
    dataforseo_actions = [
        item for item in readiness["actions"] if item["provider_key"] == "dataforseo"
    ]
    assert {"seo.keyword.research", "seo.serp.analyze"} == {
        item["action_ref"] for item in dataforseo_actions
    }
    assert {
        missing["required_for"]
        for action in dataforseo_actions
        for missing in action["missing"]
    } == {"action_execution_optional_provider"}
    ahrefs_action = next(
        item for item in readiness["actions"] if item["provider_key"] == "ahrefs"
    )
    assert {item["required_for"] for item in ahrefs_action["missing"]} == {
        "optional_action_execution"
    }
    assert readiness["next_steps"][0]["tool"] == "runPlan.create"
    assert len(readiness["next_steps"]) == 1


def test_readiness_check_treats_media_providers_as_choose_one_routes(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "media-buying.campaign-launch",
            "response_mode": "raw",
        },
    )
    readiness = readiness.get("data", readiness)

    assert "ready" not in readiness
    assert readiness["required_providers_ready"] is False
    assert readiness["execution_ready"] is False
    assert len(readiness["route_groups"]) == 1
    group = readiness["route_groups"][0]
    assert group["route_group"] == "campaign_launch"
    assert group["required"] is True
    assert group["execution_ready"] is False
    assert {route["route_key"] for route in group["routes"]} == {
        "meta_ads",
        "google_ads",
        "taboola",
        "custom_media_tool",
    }
    route_blocker = next(
        item for item in readiness["missing"] if item["code"] == "no_executable_route"
    )
    assert "one executable route" in route_blocker["message"]
    assert [item for item in readiness["missing"] if item.get("provider_key") is not None] == []


def test_readiness_compact_response_preserves_dimensions_and_route_choices(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "media-buying.campaign-launch",
        },
    )
    readiness = readiness.get("data", readiness)

    assert "ready" not in readiness
    assert readiness["structurally_ready"] is True
    assert readiness["context_status"] == "not_evaluated"
    assert readiness["required_providers_ready"] is False
    assert readiness["execution_ready"] is False
    assert readiness["route_groups"][0]["route_group"] == "campaign_launch"
    assert readiness["route_groups"][0]["required"] is True
    assert {route["route_key"] for route in readiness["route_groups"][0]["routes"]} == {
        "meta_ads",
        "google_ads",
        "taboola",
        "custom_media_tool",
    }
    assert all(action.get("setup") is None for action in readiness["actions"])
    assert all(
        missing.get("setup") is None
        for action in readiness["actions"]
        for missing in action["missing"]
    )
    assert "docs_url" not in str(readiness)


def test_readiness_optional_routes_do_not_block_prepare_only_workflows(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "media-buying.performance-diagnosis",
            "response_mode": "raw",
        },
    )

    assert readiness["required_providers_ready"] is True
    assert readiness["execution_ready"] is False
    assert readiness["missing"] == []
    group = readiness["route_groups"][0]
    assert group["route_group"] == "metric_source"
    assert group["required"] is False
    assert group["execution_ready"] is False
    assert all(
        missing["required_for"].startswith("optional_route:")
        for action in readiness["actions"]
        for missing in action["missing"]
    )


def test_readiness_gtm_and_communications_expose_provider_choices(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    research = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "gtm.account-research",
            "response_mode": "raw",
        },
    )
    outbound = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "communications.outbound-notification",
            "response_mode": "raw",
        },
    )

    research_group = research["route_groups"][0]
    assert research_group["route_group"] == "research_source"
    assert research_group["required"] is True
    assert {route["route_key"] for route in research_group["routes"]} == {
        "jina",
        "firecrawl",
    }
    assert research_group["execution_ready"] is True
    assert research["required_providers_ready"] is True
    assert research["missing"] == []

    outbound_group = outbound["route_groups"][0]
    assert outbound_group["route_group"] == "delivery_channel"
    assert outbound_group["required"] is True
    assert {route["route_key"] for route in outbound_group["routes"]} == {
        "email",
        "telegram",
        "slack",
    }


def test_readiness_check_resolves_cross_plugin_utility_action_contracts(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    gtm = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "gtm.account-research",
            "response_mode": "raw",
        },
    )
    media = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "media-buying.creative-variant-generation",
            "response_mode": "raw",
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


def test_readiness_check_marketing_campaign_scopes_concrete_optional_video_actions(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    readiness = mcp_client.call_tool_structured(
        "readiness.check",
        {
            "project_id": project_id,
            "workflow_key": "marketing.campaign-production",
            "response_mode": "raw",
        },
    )

    refs = {item["action_ref"] for item in readiness["actions"]}
    providers = {item["provider_key"] for item in readiness["missing"]}
    assert {
        "utils.image.generate",
        "utils.image.edit",
        "utils.google.video.generate",
        "utils.byteplus.video.generate",
        "utils.alibaba.video.generate",
        "utils.kling.video.generate",
        "utils.xai.video.generate",
    } <= refs
    assert providers == {"openai-images"}
    assert "utils.video.generate" not in refs
    video_missing = [
        missing
        for action in readiness["actions"]
        if (action.get("action_ref") or "").endswith(".video.generate")
        for missing in action["missing"]
    ]
    assert video_missing
    assert {item["required_for"] for item in video_missing} == {"optional_action_execution"}
    assert all(item["code"] != "action_not_found" for item in readiness["missing"])
    assert readiness["next_steps"][0]["tool"] == "runPlan.create"
