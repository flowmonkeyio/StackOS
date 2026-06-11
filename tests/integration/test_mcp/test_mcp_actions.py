"""MCP tests for StackOS action describe/validate tools."""

from __future__ import annotations

import base64
import json

from pytest_httpx import HTTPXMock

from stackos.config import Settings

from .conftest import MCPClient


def _png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def test_action_describe_and_validate_are_read_only_discovery_tools(
    mcp_client: MCPClient,
) -> None:
    tools = {tool["name"] for tool in mcp_client.list_tools()}

    assert "action.list" in tools
    assert "action.describe" in tools
    assert "action.validate" in tools
    assert "action.execute" in tools
    assert "action.run" in tools

    listed = mcp_client.call_tool_structured("action.list", {"query": "catalog"})
    described = mcp_client.call_tool_structured(
        "action.describe",
        {"action_ref": "core.catalog.describe"},
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {"action_ref": "core.catalog.describe", "input_json": {}},
    )

    assert listed["count"] >= 1
    assert any(item["action_ref"] == "core.catalog.describe" for item in listed["items"])
    assert listed["items"][0]["availability_status"]
    assert described["manifest"]["action_ref"] == "core.catalog.describe"
    assert described["execution_available"] is False
    assert described["agent_execute_available"] is False
    assert validation["valid"] is True
    assert validation["issues"] == []


def test_action_describe_reports_project_availability(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    _create_openai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "openai-images", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200

    described = mcp_client.call_tool_structured(
        "action.describe",
        {"project_id": project_id, "action_ref": "utils.image.generate"},
    )
    listed = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "image", "executable": True},
    )

    assert described["availability"]["status"] == "ready"
    assert described["exposure"]["state"] == "external_connected"
    assert described["exposure"]["visible_by_default"] is True
    assert described["execution_available"] is True
    assert described["availability"]["credential_state"] == "available"
    assert described["availability"]["budget_state"] == "available"
    assert described["provider_setup"]["provider_key"] == "openai-images"
    assert described["provider_setup"]["local_setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert described["provider_setup"]["api_key_url"] == "https://platform.openai.com/api-keys"
    assert described["provider_setup"]["billing_url"].startswith("https://platform.openai.com/")
    assert described["execution_context"]["discover"]["operation"] == "executionContext.discover"
    assert described["execution_context"]["discover"]["arguments"] == {
        "action_ref": "utils.image.generate",
        "project_id": project_id,
        "provider_key": "openai-images",
    }
    assert described["execution_context"]["pass_context_ref_to"] == [
        "action.validate",
        "action.run",
        "action.execute",
    ]
    assert described["execution_context"]["payload_boundary"]["provider_context_json"].startswith(
        "reusable provider/account scope"
    )
    capability_metadata = described["manifest"]["config_json"]["capability_metadata"]
    assert capability_metadata["modes"] == ["text-to-image"]
    assert capability_metadata["limits"]["prompt_max_chars"] == 32000
    assert described["manifest"]["output_schema_json"]["properties"]["data"]["type"] == "array"
    image_items = [item for item in listed["items"] if item["action_ref"] == "utils.image.generate"]
    assert image_items
    assert image_items[0]["exposure"]["visible_by_default"] is True


def test_execution_context_discover_compact_returns_create_guidance(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    discovered = mcp_client.call_tool_structured(
        "executionContext.discover",
        {
            "project_id": project_id,
            "plugin_slug": "trackbooth",
            "provider_key": "trackbooth",
            "action_ref": "trackbooth.api.offers_findall",
            "ticket_key": "copy-offers",
        },
    )

    assert discovered["context_refs"] == []
    assert discovered["next_calls"]["create"]["operation"] == "executionContext.create"
    assert discovered["next_calls"]["create"]["arguments"] == {
        "project_id": project_id,
        "name": "trackbooth.api.offers_findall context",
        "plugin_slug": "trackbooth",
        "provider_key": "trackbooth",
        "action_ref": "trackbooth.api.offers_findall",
        "ticket_key": "copy-offers",
    }


def test_action_list_hides_disconnected_external_integrations_by_default(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    hidden = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "image"},
    )
    full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "utils",
            "query": "image",
            "include_unavailable_integrations": True,
        },
    )
    local = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "sitemap"},
    )
    deferred = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "utils", "query": "extract"},
    )
    deferred_full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "utils",
            "query": "extract",
            "include_unavailable_integrations": True,
        },
    )

    assert not any(item["action_ref"] == "utils.image.generate" for item in hidden["items"])
    assert hidden["hidden_count"] >= 1
    image_items = [item for item in full["items"] if item["action_ref"] == "utils.image.generate"]
    assert image_items
    assert image_items[0]["exposure"]["state"] == "external_not_connected"
    assert image_items[0]["exposure"]["visible_by_default"] is False
    assert image_items[0]["exposure"]["hidden_reason"] == "integration_not_connected"
    assert any(item["action_ref"] == "utils.sitemap.fetch" for item in local["items"])
    assert not any(item["action_ref"] == "utils.web.extract" for item in deferred["items"])
    extract_items = [
        item for item in deferred_full["items"] if item["action_ref"] == "utils.web.extract"
    ]
    assert extract_items
    assert extract_items[0]["exposure"]["visible_by_default"] is False
    assert extract_items[0]["exposure"]["hidden_reason"] == "action_deferred"


def test_action_list_exposes_serper_only_after_connection(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    hidden = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "seo", "provider_key": "serper"},
    )
    full = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "seo",
            "provider_key": "serper",
            "include_unavailable_integrations": True,
        },
    )

    assert not any(item["action_ref"] == "seo.serper.search" for item in hidden["items"])
    assert hidden["hidden_count"] >= 1
    serper_items = [item for item in full["items"] if item["action_ref"] == "seo.serper.search"]
    assert serper_items
    assert serper_items[0]["availability_status"] == "missing_credential"
    assert serper_items[0]["exposure"]["state"] == "external_not_connected"
    assert serper_items[0]["exposure"]["visible_by_default"] is False
    assert serper_items[0]["exposure"]["hidden_reason"] == "integration_not_connected"

    _create_serper_credential(mcp_client, project_id)
    ready = mcp_client.call_tool_structured(
        "action.list",
        {"project_id": project_id, "plugin_slug": "seo", "provider_key": "serper"},
    )

    ready_items = [item for item in ready["items"] if item["action_ref"] == "seo.serper.search"]
    assert ready_items
    assert ready_items[0]["availability_status"] == "ready"
    assert ready_items[0]["exposure"]["state"] == "external_connected"
    assert ready_items[0]["exposure"]["visible_by_default"] is True


def test_integration_list_summarizes_hidden_external_actions(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    listed = mcp_client.call_tool_structured(
        "integration.list",
        {
            "project_id": project_id,
            "provider_key": "openai-images",
            "include_actions": True,
        },
    )

    assert listed["count"] == 1
    row = listed["items"][0]
    assert row["provider_key"] == "openai-images"
    assert row["state"] == "not_connected"
    assert row["connected"] is False
    assert row["hidden_action_count"] >= 1
    assert row["next_action"]["tool"] == "auth.status"
    assert row["next_action"]["ui_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert row["setup"]["provider_key"] == "openai-images"
    assert row["setup"]["local_setup_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )
    assert row["setup"]["api_key_url"] == "https://platform.openai.com/api-keys"
    assert row["setup"]["docs_url"].endswith("/image-generation")
    assert row["next_action"]["setup"]["api_key_url"] == "https://platform.openai.com/api-keys"
    assert any(action["action_ref"] == "utils.image.generate" for action in row["actions"])


def test_action_run_unavailable_external_action_reports_exposure_repair_context(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    err = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "A tiny product mockup"},
            "confirm_direct": True,
            "intent_summary": "Verify unavailable integration repair context.",
        },
    )

    assert err["code"] == -32602
    assert err["data"]["status"] == "missing_credential"
    exposure = err["data"]["exposure"]
    assert exposure["state"] == "external_not_connected"
    assert exposure["hidden_reason"] == "integration_not_connected"
    assert exposure["next_action"]["ui_url"].endswith(
        f"/projects/{project_id}/connections?provider_key=openai-images"
    )


def test_action_validate_rejects_raw_secret_payloads(mcp_client: MCPClient) -> None:
    err = mcp_client.call_tool_error(
        "action.validate",
        {
            "action_ref": "core.catalog.describe",
            "input_json": {"api_key": "sk-leak"},
        },
    )

    assert err["code"] == -32602
    assert "must not contain secrets" in err["data"]["detail"]


def _create_openai_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/openai-images/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "sk-openai"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "openai-images"},
    )
    return status["connections"][0]["credential_ref"]


def _create_xai_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/xai-imagine/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "xai-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "xai-imagine"},
    )
    return status["connections"][0]["credential_ref"]


def _create_google_veo_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/google-veo/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "gemini-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "google-veo"},
    )
    return status["connections"][0]["credential_ref"]


def _create_reve_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/reve/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "reve-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "reve"},
    )
    return status["connections"][0]["credential_ref"]


def _create_google_gemini_image_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/google-gemini-image/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "gemini-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "google-gemini-image"},
    )
    return status["connections"][0]["credential_ref"]


def _create_ideogram_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/ideogram/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "ideo-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "ideogram"},
    )
    return status["connections"][0]["credential_ref"]


def _create_byteplus_ark_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/byteplus-ark/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "ark-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "byteplus-ark"},
    )
    return status["connections"][0]["credential_ref"]


def _create_alibaba_wan_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/alibaba-wan/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "dashscope-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "alibaba-wan"},
    )
    return status["connections"][0]["credential_ref"]


def _create_kling_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/kling/credentials",
        json={
            "auth_method_key": "access_key_secret",
            "fields": {"access_key": "ak-test", "secret_key": "sk-test"},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "kling"},
    )
    return status["connections"][0]["credential_ref"]


def _create_firecrawl_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "fc-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "firecrawl"},
    )
    return status["connections"][0]["credential_ref"]


def _create_dataforseo_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/dataforseo/credentials",
        json={
            "auth_method_key": "basic",
            "fields": {"login": "login@example.com", "password": "password"},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "dataforseo"},
    )
    return status["connections"][0]["credential_ref"]


def _create_serper_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/serper/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "serper-key"}},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "serper"},
    )
    return status["connections"][0]["credential_ref"]


def _create_wordpress_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/wordpress/credentials",
        json={
            "auth_method_key": "application_password",
            "fields": {
                "username": "editor",
                "application_password": "app pass",
                "wp_url": "https://wp.example",
            },
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "wordpress"},
    )
    return status["connections"][0]["credential_ref"]


def _create_ghost_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/ghost/credentials",
        json={
            "auth_method_key": "admin_api_key",
            "fields": {
                "admin_api_key": "keyid:00112233445566778899aabbccddeeff",
                "ghost_url": "https://ghost.example",
                "api_version": "v5.0",
            },
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "ghost"},
    )
    return status["connections"][0]["credential_ref"]


def _create_mock_credential(
    mcp: MCPClient, project_id: int, secret: str = "mock-mcp-secret"
) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/mock-provider/credentials",
        json={
            "auth_method_key": "api_key",
            "profile_key": "primary",
            "label": "Mock MCP Primary",
            "fields": {"api_key": secret},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "mock-provider"},
    )
    return status["connections"][0]["credential_ref"]


def _image_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.image-action.run",
        "title": "Image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-image",
                    "tool": "action.execute",
                    "action_refs": ["utils.image.generate"],
                }
            ]
        },
        "steps": [
            {
                "id": "generate-image",
                "title": "Generate image",
                "action_refs": ["utils.image.generate"],
            }
        ],
    }


def _xai_video_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.xai-video-action.run",
        "title": "xAI video action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-xai-video",
                    "tool": "action.execute",
                    "action_refs": ["utils.xai.video.generate"],
                }
            ]
        },
        "steps": [
            {
                "id": "generate-xai-video",
                "title": "Generate xAI video",
                "action_refs": ["utils.xai.video.generate"],
            }
        ],
    }


def _single_video_action_plan_json(action_ref: str, step_id: str) -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": f"{action_ref}.run",
        "title": f"{action_ref} action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": step_id,
                    "tool": "action.execute",
                    "action_refs": [action_ref],
                }
            ]
        },
        "steps": [
            {
                "id": step_id,
                "title": f"Execute {action_ref}",
                "action_refs": [action_ref],
            }
        ],
    }


def _reve_image_action_plan_json() -> dict:
    action_refs = [
        "utils.reve.image.generate",
        "utils.reve.image.edit",
        "utils.reve.image.remix",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.reve-image-action.run",
        "title": "Reve image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-reve-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-reve-image",
                "title": "Generate Reve image",
                "action_refs": action_refs,
            }
        ],
    }


def _google_gemini_image_action_plan_json() -> dict:
    action_refs = [
        "utils.google.image.generate",
        "utils.google.image.edit",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.google-gemini-image-action.run",
        "title": "Google Gemini image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-google-gemini-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-google-gemini-image",
                "title": "Generate Google Gemini image",
                "action_refs": action_refs,
            }
        ],
    }


def _ideogram_image_action_plan_json() -> dict:
    action_refs = [
        "utils.ideogram.image.generate",
        "utils.ideogram.image.remix",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.ideogram-image-action.run",
        "title": "Ideogram image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-ideogram-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-ideogram-image",
                "title": "Generate Ideogram image",
                "action_refs": action_refs,
            }
        ],
    }


def _byteplus_seedream_image_action_plan_json() -> dict:
    action_refs = [
        "utils.byteplus.image.generate",
        "utils.byteplus.image.edit",
    ]
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.byteplus-seedream-image-action.run",
        "title": "BytePlus Seedream image action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "generate-byteplus-seedream-image",
                    "tool": "action.execute",
                    "action_refs": action_refs,
                }
            ]
        },
        "steps": [
            {
                "id": "generate-byteplus-seedream-image",
                "title": "Generate BytePlus Seedream image",
                "action_refs": action_refs,
            }
        ],
    }


def _image_edit_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.image-edit-action.run",
        "title": "Image edit action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "edit-image",
                    "tool": "action.execute",
                    "action_refs": ["utils.image.edit"],
                }
            ]
        },
        "steps": [
            {
                "id": "edit-image",
                "title": "Edit image",
                "action_refs": ["utils.image.edit"],
            }
        ],
    }


def _firecrawl_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.firecrawl-action.run",
        "title": "Firecrawl action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "scrape-page",
                    "tool": "action.execute",
                    "action_refs": ["utils.web.scrape"],
                }
            ]
        },
        "steps": [
            {
                "id": "scrape-page",
                "title": "Scrape page",
                "action_refs": ["utils.web.scrape"],
            }
        ],
    }


def _sitemap_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.sitemap-action.run",
        "title": "Sitemap action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "fetch-sitemap",
                    "tool": "action.execute",
                    "action_refs": ["utils.sitemap.fetch"],
                }
            ]
        },
        "steps": [
            {
                "id": "fetch-sitemap",
                "title": "Fetch sitemap",
                "action_refs": ["utils.sitemap.fetch"],
            }
        ],
    }


def _mock_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.mock-provider.run",
        "title": "Mock provider action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "execute-mock",
                    "tool": "action.execute",
                    "action_refs": ["utils.mock.echo"],
                }
            ]
        },
        "steps": [
            {
                "id": "execute-mock",
                "title": "Execute mock provider",
                "action_refs": ["utils.mock.echo"],
            }
        ],
    }


def _dataforseo_paa_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "seo.paa-action.run",
        "title": "PAA action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "extract-paa",
                    "tool": "action.execute",
                    "action_refs": ["seo.paa.extract"],
                }
            ]
        },
        "steps": [
            {
                "id": "extract-paa",
                "title": "Extract PAA",
                "action_refs": ["seo.paa.extract"],
            }
        ],
    }


def _wordpress_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "publishing.wordpress-action.run",
        "title": "WordPress action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "create-wordpress-post",
                    "tool": "action.execute",
                    "action_refs": ["publishing.wordpress.post.create"],
                }
            ]
        },
        "steps": [
            {
                "id": "create-wordpress-post",
                "title": "Create WordPress post",
                "action_refs": ["publishing.wordpress.post.create"],
            }
        ],
    }


def _ghost_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "publishing.ghost-action.run",
        "title": "Ghost action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "create-ghost-post",
                    "tool": "action.execute",
                    "action_refs": ["publishing.ghost.post.create"],
                }
            ]
        },
        "steps": [
            {
                "id": "create-ghost-post",
                "title": "Create Ghost post",
                "action_refs": ["publishing.ghost.post.create"],
            }
        ],
    }


def test_action_execute_requires_run_plan_grant(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    err = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero"},
        },
    )

    assert err["code"] == -32007
    assert err["data"]["tool"] == "action.execute"


def test_action_execute_requires_step_action_ref(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    plan = _image_action_plan_json()
    plan["steps"][0]["action_refs"] = ["utils.web.scrape"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-image",
            "run_token": run_token,
        },
    )

    err = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero"},
            "run_token": run_token,
        },
    )

    assert err["code"] == -32007
    assert "declared on the active step" in err["data"]["detail"]


def test_action_execute_requires_matching_grant_action_ref(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    plan = _image_action_plan_json()
    plan["grants"]["mcp_tool_grants"][0]["action_refs"] = ["utils.web.scrape"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": plan},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-image",
            "run_token": run_token,
        },
    )

    err = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero"},
            "run_token": run_token,
        },
    )

    assert err["code"] == -32007
    assert "arguments do not match" in err["data"]["detail"]


def test_action_execute_mock_provider_vertical_slice_through_mcp(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id)
    described = mcp_client.call_tool_structured(
        "action.describe",
        {"project_id": project_id, "action_ref": "utils.mock.echo"},
    )
    assert described["availability"]["status"] == "ready"
    assert described["availability"]["credential_state"] == "available"

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _mock_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "execute-mock",
            "run_token": run_token,
        },
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "run_token": run_token,
            "input_json": {
                "message": "hello from mcp mock provider",
                "echo": {"campaign": "mock-campaign"},
                "cost_cents": 11,
            },
        },
    )

    data = out["data"]
    rendered = json.dumps(data)
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert data["action_call"]["provider_key"] == "mock-provider"
    assert data["action_call"]["connector_key"] == "mock-provider"
    assert data["output_json"]["status"] == "success"
    assert data["output_json"]["credential_ref"] == credential_ref
    assert data["output_json"]["leak_check"] == {
        "authorization": "[redacted]",
        "api_key": "[redacted]",
    }
    assert data["metadata_json"]["access_token"] == "[redacted]"
    assert data["cost_cents"] == 11
    assert "mock-mcp-secret" not in rendered

    audit_resp = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": created["data"]["id"],
            "run_plan_step_id": claimed["data"]["id"],
            "status": "success",
        },
        headers=mcp_client._headers(),
    )
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    assert audit["total_estimate"] == 1
    assert "mock-mcp-secret" not in json.dumps(audit)


def test_action_run_direct_mock_provider_returns_raw_output(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id, secret="direct-secret")

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "input_json": {
                "message": "hello direct action" + ("." * 600),
                "echo": {"kind": "smoke"},
                "cost_cents": 3,
            },
            "idempotency_key": "direct-mock-provider-1",
        },
    )

    data = out["data"]
    rendered = json.dumps(out)
    assert data["status"] == "success"
    assert data["action_ref"] == "utils.mock.echo"
    assert data["provider_key"] == "mock-provider"
    assert data["operation"] == "echo"
    assert data["compact"]["message"].startswith("hello direct action")
    assert len(data["compact"]["message"]) <= 503
    assert data["compact"]["status"] == "success"
    assert data["cost_cents"] == 3
    assert data["action_call"]["provider_key"] == "mock-provider"
    assert data["output_json"]["message"].startswith("hello direct action")
    assert "direct-secret" not in rendered


def test_action_run_verbose_includes_redacted_full_payload(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_mock_credential(mcp_client, project_id, secret="verbose-secret")

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.mock.echo",
            "credential_ref": credential_ref,
            "input_json": {"message": "hello verbose"},
            "verbose": True,
        },
    )

    data = out["data"]
    rendered = json.dumps(out)
    assert data["action_call"]["provider_key"] == "mock-provider"
    assert data["output_json"]["leak_check"] == {
        "authorization": "[redacted]",
        "api_key": "[redacted]",
    }
    assert "verbose-secret" not in rendered


def test_action_run_rejects_non_read_without_direct_confirmation(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    err = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": seeded_project["data"]["id"],
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {"post": {"title": "x", "content": "x"}},
        },
    )

    assert err["code"] == -32602
    assert "confirm_direct=true" in err["data"]["detail"]


def test_action_run_derives_idempotency_for_confirmed_non_read(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_wordpress_credential(mcp_client, project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://wp.example/wp-json/wp/v2/posts",
        json={"id": 55, "link": "https://wp.example/direct/"},
    )

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {"post": {"title": "x", "content": "x", "status": "draft"}},
            "credential_ref": credential_ref,
            "confirm_direct": True,
            "intent_summary": "User asked to create one post directly.",
        },
    )

    data = out["data"]
    assert data["status"] == "success"
    assert data["compact"]["id"] == 55
    assert data["action_call"]["provider_key"] == "wordpress"
    assert data["output_json"]["id"] == 55


def test_action_execute_openai_images_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_openai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "openai-images", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-image",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/generations",
        json={"data": [{"b64_json": base64.b64encode(b"webp").decode("ascii")}]},
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.generate",
            "input_json": {"prompt": "editorial hero", "n": 1},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    item = data["output_json"]["data"][0]
    rendered = json.dumps(data)
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["credential_ref"] == credential_ref
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert item["url"].startswith("/generated-assets/openai-images/openai-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert data["output_json"]["artifact_refs"] == [item["url"]]
    assert "b64_json" not in item
    assert "credential_id" not in rendered
    assert "sk-openai" not in rendered
    assert "Authorization" not in rendered
    path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"webp"
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_rows = [row for row in artifacts["items"] if row["id"] == item["artifact_id"]]
    assert artifact_rows
    artifact = mcp_client.call_tool_structured(
        "artifact.get",
        {"artifact_id": item["artifact_id"]},
    )
    assert artifact["uri"] == item["url"]
    assert artifact["plugin_slug"] == "utils"


def test_action_execute_xai_video_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_xai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "xai-imagine", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _xai_video_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-xai-video",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/videos/generations",
        json={"request_id": "req_123"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.x.ai/v1/videos/req_123",
        json={
            "status": "done",
            "model": "grok-imagine-video",
            "video": {"url": "https://cdn.x.ai/video.mp4", "duration": 5},
            "usage": {"cost_in_usd_ticks": 3100000000},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://cdn.x.ai/video.mp4",
        content=b"video-bytes",
        headers={"content-type": "video/mp4"},
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.xai.video.generate",
            "input_json": {
                "prompt": "editorial product motion",
                "duration": 5,
                "resolution": "480p",
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    item = data["output_json"]["data"][0]
    rendered = json.dumps(data)
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "xai-imagine"
    assert data["action_call"]["connector_key"] == "xai-imagine"
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == created["data"]["id"]
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert data["cost_cents"] == 31
    assert data["output_json"]["request_id"] == "req_123"
    assert item["url"].startswith("/generated-assets/xai-imagine/xai-video-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert data["output_json"]["artifact_refs"] == [item["url"]]
    assert "https://cdn.x.ai/video.mp4" not in rendered
    assert "credential_id" not in rendered
    assert "xai-key" not in rendered
    path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"video-bytes"
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "video"},
    )
    assert any(row["id"] == item["artifact_id"] for row in artifacts["items"])


def test_action_execute_provider_video_grants_return_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]

    cases = [
        {
            "action_ref": "utils.google.video.generate",
            "step_id": "generate-google-video",
            "provider": "google-veo",
            "connector": "google-veo",
            "budget_kind": "google-veo",
            "credential": _create_google_veo_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration_seconds": 6,
                "resolution": "720p",
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/google-veo/google-veo-video-",
            "provider_url": "https://generativelanguage.googleapis.com/download/mcp-video",
            "bytes": b"google-veo-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url=(
                        "https://generativelanguage.googleapis.com/v1beta/models/"
                        "veo-3.1-generate-preview:predictLongRunning"
                    ),
                    json={"name": "operations/mcp-veo"},
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://generativelanguage.googleapis.com/v1beta/operations/mcp-veo",
                    json={
                        "name": "operations/mcp-veo",
                        "done": True,
                        "response": {
                            "generateVideoResponse": {
                                "generatedSamples": [
                                    {
                                        "video": {
                                            "uri": (
                                                "https://generativelanguage.googleapis.com/"
                                                "download/mcp-video"
                                            )
                                        }
                                    }
                                ]
                            }
                        },
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://generativelanguage.googleapis.com/download/mcp-video",
                    content=b"google-veo-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "gemini-key",
        },
        {
            "action_ref": "utils.byteplus.video.generate",
            "step_id": "generate-byteplus-video",
            "provider": "byteplus-ark",
            "connector": "byteplus-seedance",
            "budget_kind": "byteplus-ark",
            "credential": _create_byteplus_ark_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/byteplus-ark/byteplus-seedance-video-",
            "provider_url": "https://ark-output.example/mcp-video.mp4",
            "bytes": b"byteplus-seedance-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url=(
                        "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"
                    ),
                    json={"id": "cgt-mcp"},
                ),
                httpx_mock.add_response(
                    method="GET",
                    url=(
                        "https://ark.ap-southeast.bytepluses.com/api/v3/"
                        "contents/generations/tasks/cgt-mcp"
                    ),
                    json={
                        "id": "cgt-mcp",
                        "status": "succeeded",
                        "content": {"video_url": "https://ark-output.example/mcp-video.mp4"},
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://ark-output.example/mcp-video.mp4",
                    content=b"byteplus-seedance-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "ark-key",
        },
        {
            "action_ref": "utils.alibaba.video.generate",
            "step_id": "generate-alibaba-video",
            "provider": "alibaba-wan",
            "connector": "alibaba-wan",
            "budget_kind": "alibaba-wan",
            "credential": _create_alibaba_wan_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/alibaba-wan/alibaba-wan-video-",
            "provider_url": "https://dashscope-result.example/mcp-video.mp4",
            "bytes": b"alibaba-wan-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url=(
                        "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/"
                        "video-generation/video-synthesis"
                    ),
                    json={"output": {"task_id": "task_mcp", "task_status": "PENDING"}},
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/task_mcp",
                    json={
                        "output": {
                            "task_id": "task_mcp",
                            "task_status": "SUCCEEDED",
                            "video_url": "https://dashscope-result.example/mcp-video.mp4",
                        }
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://dashscope-result.example/mcp-video.mp4",
                    content=b"alibaba-wan-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "dashscope-key",
        },
        {
            "action_ref": "utils.kling.video.generate",
            "step_id": "generate-kling-video",
            "provider": "kling",
            "connector": "kling-video",
            "budget_kind": "kling",
            "credential": _create_kling_credential,
            "input": {
                "prompt": "cinematic product motion",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            "asset_prefix": "/generated-assets/kling/kling-video-",
            "provider_url": "https://kling.example/mcp-video.mp4",
            "bytes": b"kling-video",
            "mock": lambda: (
                httpx_mock.add_response(
                    method="POST",
                    url="https://api-singapore.klingai.com/v1/videos/text2video",
                    json={
                        "code": 0,
                        "data": {"task_id": "task-mcp", "task_status": "submitted"},
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://api-singapore.klingai.com/v1/videos/text2video/task-mcp",
                    json={
                        "code": 0,
                        "data": {
                            "task_id": "task-mcp",
                            "task_status": "succeed",
                            "task_result": {
                                "videos": [
                                    {
                                        "id": "video-mcp",
                                        "url": "https://kling.example/mcp-video.mp4",
                                    }
                                ]
                            },
                        },
                    },
                ),
                httpx_mock.add_response(
                    method="GET",
                    url="https://kling.example/mcp-video.mp4",
                    content=b"kling-video",
                    headers={"content-type": "video/mp4"},
                ),
            ),
            "secret": "sk-test",
        },
    ]

    for case in cases:
        credential_ref = case["credential"](mcp_client, project_id)
        budget_resp = mcp_client.test_client.post(
            f"/api/v1/projects/{project_id}/budgets",
            json={"kind": case["budget_kind"], "monthly_budget_usd": 10.0},
            headers=mcp_client._headers(),
        )
        assert budget_resp.status_code == 200
        created = mcp_client.call_tool_structured(
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": _single_video_action_plan_json(
                    case["action_ref"],
                    case["step_id"],
                ),
            },
        )
        started = mcp_client.call_tool_structured(
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": created["data"]["id"]},
        )
        run_token = started["data"]["run_token"]
        claimed = mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {
                "run_plan_id": created["data"]["id"],
                "step_id": case["step_id"],
                "run_token": run_token,
            },
        )
        case["mock"]()

        out = mcp_client.call_tool_structured(
            "action.execute",
            {
                "project_id": project_id,
                "action_ref": case["action_ref"],
                "input_json": case["input"],
                "credential_ref": credential_ref,
                "run_token": run_token,
            },
        )

        data = out["data"]
        assert "output_json" in data, data
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == case["provider"]
        assert data["action_call"]["connector_key"] == case["connector"]
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert item["url"].startswith(case["asset_prefix"])
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert case["provider_url"] not in rendered
        assert case["secret"] not in rendered
        assert "credential_id" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == case["bytes"]
        step_calls = mcp_client.call_tool_structured(
            "run.listStepCalls",
            {"run_step_id": claimed["data"]["id"]},
        )
        rendered_step_calls = json.dumps(step_calls)
        assert case["provider_url"] not in rendered_step_calls
        assert case["secret"] not in rendered_step_calls


def test_action_execute_reve_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_reve_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "reve", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "reve-edit.png").write_bytes(b"reve-edit-source")
    (input_dir / "reve-remix-a.png").write_bytes(_png_header(1, 1))
    (input_dir / "reve-remix-b.png").write_bytes(_png_header(2, 1))
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _reve_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-reve-image",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/create",
        json={
            "image": base64.b64encode(b"reve-image").decode("ascii"),
            "version": "reve-create@20250915",
            "content_violation": False,
            "request_id": "rsid-create",
            "credits_used": 18,
            "credits_remaining": 982,
        },
    )

    def assert_reve_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_credits: int,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "reve"
        assert data["action_call"]["connector_key"] == "reve"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/reve/reve-image-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert data["output_json"]["usage"]["credits_used"] == expected_credits
        assert "image" not in data["output_json"]
        assert "credential_id" not in rendered
        assert "reve-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.reve.image.generate",
            "input_json": {"prompt": "editorial hero", "aspect_ratio": "3:2"},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    assert_reve_artifact(
        out,
        expected_bytes=b"reve-image",
        expected_credits=18,
        expected_cost_cents=2,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/edit",
        json={
            "image": base64.b64encode(b"reve-edit-image").decode("ascii"),
            "version": "reve-edit@20250915",
            "content_violation": False,
            "request_id": "rsid-edit",
            "credits_used": 30,
            "credits_remaining": 952,
        },
    )
    edit_out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.reve.image.edit",
            "input_json": {
                "edit_instruction": "make the product brighter",
                "input_image_ref": "/generated-assets/uploads/reve-edit.png",
                "aspect_ratio": "1:1",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_reve_artifact(
        edit_out,
        expected_bytes=b"reve-edit-image",
        expected_credits=30,
        expected_cost_cents=4,
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/remix",
        json={
            "image": base64.b64encode(b"reve-remix-image").decode("ascii"),
            "version": "reve-remix-fast@20251030",
            "content_violation": False,
            "request_id": "rsid-remix",
            "credits_used": 5,
            "credits_remaining": 947,
        },
    )
    remix_out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.reve.image.remix",
            "input_json": {
                "prompt": "combine the references into a new campaign image",
                "input_image_refs": [
                    "/generated-assets/uploads/reve-remix-a.png",
                    "/generated-assets/uploads/reve-remix-b.png",
                ],
                "version": "latest-fast",
                "aspect_ratio": "auto",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_reve_artifact(
        remix_out,
        expected_bytes=b"reve-remix-image",
        expected_credits=5,
        expected_cost_cents=1,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        edit_out["data"]["output_json"]["data"][0]["artifact_id"],
        remix_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


def test_action_execute_google_gemini_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_google_gemini_image_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "google-gemini-image", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "google-edit.png").write_bytes(b"google-edit-source")
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _google_gemini_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-google-gemini-image",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1/models/"
            "gemini-3.1-flash-image:generateContent"
        ),
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(b"google-image").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 1680},
        },
    )

    def assert_google_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "google-gemini-image"
        assert data["action_call"]["connector_key"] == "google-gemini-image"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/google-gemini-image/google-gemini-image-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert "candidates" not in data["output_json"]
        assert "credential_id" not in rendered
        assert "gemini-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.google.image.generate",
            "input_json": {
                "prompt": "editorial hero",
                "aspect_ratio": "16:9",
                "image_size": "2K",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    assert_google_artifact(out, expected_bytes=b"google-image", expected_cost_cents=10)
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1/models/gemini-3-pro-image:generateContent"
        ),
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/jpeg",
                                    "data": base64.b64encode(b"google-edit-image").decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        },
    )
    edit_out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.google.image.edit",
            "input_json": {
                "prompt": "make the product brighter",
                "input_image_refs": ["/generated-assets/uploads/google-edit.png"],
                "model": "gemini-3-pro-image",
                "aspect_ratio": "1:1",
                "image_size": "4K",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_google_artifact(
        edit_out,
        expected_bytes=b"google-edit-image",
        expected_cost_cents=24,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        edit_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


def test_action_execute_ideogram_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_ideogram_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "ideogram", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "ideogram-source.png").write_bytes(_png_header(32, 32))
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _ideogram_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-ideogram-image",
            "run_token": run_token,
        },
    )

    def assert_ideogram_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "ideogram"
        assert data["action_call"]["connector_key"] == "ideogram"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/ideogram/ideogram-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert "ephemeral" not in rendered
        assert "sig=secret" not in rendered
        assert "credential_id" not in rendered
        assert "ideo-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    generate_provider_url = "https://ideogram.ai/api/images/ephemeral/generated.png?sig=secret"
    httpx_mock.add_response(
        method="POST",
        url="https://api.ideogram.ai/v1/ideogram-v4/generate",
        json={
            "created": "2026-06-10 00:00:00+00:00",
            "data": [
                {
                    "prompt": "editorial hero",
                    "resolution": "2048x2048",
                    "is_image_safe": True,
                    "seed": 12345,
                    "url": generate_provider_url,
                }
            ],
            "response_type": "url",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=generate_provider_url,
        content=b"ideogram-image",
        headers={"content-type": "image/png"},
    )
    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.ideogram.image.generate",
            "input_json": {
                "text_prompt": "editorial hero",
                "resolution": "2048x2048",
                "rendering_speed": "TURBO",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_ideogram_artifact(out, expected_bytes=b"ideogram-image", expected_cost_cents=3)

    remix_provider_url = "https://ideogram.ai/api/images/ephemeral/remix.webp?sig=secret"
    httpx_mock.add_response(
        method="POST",
        url="https://api.ideogram.ai/v1/ideogram-v4/remix",
        json={
            "created": "2026-06-10 00:00:00+00:00",
            "data": [
                {
                    "prompt": "remix",
                    "resolution": "3072x1024",
                    "is_image_safe": True,
                    "seed": 67890,
                    "url": remix_provider_url,
                }
            ],
            "response_type": "url",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=remix_provider_url,
        content=b"ideogram-remix",
        headers={"content-type": "image/webp"},
    )
    remix_out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.ideogram.image.remix",
            "input_json": {
                "text_prompt": "remix",
                "input_image_ref": "/generated-assets/uploads/ideogram-source.png",
                "image_weight": 75,
                "resolution": "3072x1024",
                "rendering_speed": "QUALITY",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_ideogram_artifact(
        remix_out,
        expected_bytes=b"ideogram-remix",
        expected_cost_cents=10,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        remix_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


def test_action_execute_byteplus_seedream_image_grant_returns_sanitized_artifact_refs(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_byteplus_ark_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "byteplus-ark", "monthly_budget_usd": 10.0},
        headers=mcp_client._headers(),
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "byteplus-source.png").write_bytes(_png_header(32, 32))
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _byteplus_seedream_image_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "generate-byteplus-seedream-image",
            "run_token": run_token,
        },
    )

    def assert_byteplus_artifact(
        result: dict,
        *,
        expected_bytes: bytes,
        expected_cost_cents: int,
    ) -> None:
        data = result["data"]
        item = data["output_json"]["data"][0]
        rendered = json.dumps(data)
        assert data["credential_ref"] == credential_ref
        assert data["action_call"]["credential_ref"] == credential_ref
        assert data["action_call"]["provider_key"] == "byteplus-ark"
        assert data["action_call"]["connector_key"] == "byteplus-seedream"
        assert data["action_call"]["run_id"] == started["data"]["run_id"]
        assert data["action_call"]["run_plan_id"] == created["data"]["id"]
        assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
        assert data["cost_cents"] == expected_cost_cents
        assert item["url"].startswith("/generated-assets/byteplus-ark/byteplus-ark-")
        assert item["artifact_ref"] == item["url"]
        assert isinstance(item["artifact_id"], int)
        assert data["output_json"]["artifact_refs"] == [item["url"]]
        assert "ark-output.byteplus.test" not in rendered
        assert "token=secret" not in rendered
        assert "b64_json" not in rendered
        assert "credential_id" not in rendered
        assert "ark-key" not in rendered
        path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
        assert path.read_bytes() == expected_bytes

    provider_url = "https://ark-output.byteplus.test/generated.jpeg?token=secret"
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        json={
            "model": "seedream-5-0-lite-260128",
            "data": [{"url": provider_url, "size": "2048x2048"}],
            "usage": {"generated_images": 1},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url,
        content=b"byteplus-image",
        headers={"content-type": "image/jpeg"},
    )
    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.byteplus.image.generate",
            "input_json": {
                "prompt": "editorial hero",
                "size": "2K",
                "watermark": False,
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_byteplus_artifact(out, expected_bytes=b"byteplus-image", expected_cost_cents=4)

    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        json={
            "model": "seedream-4-0-250828",
            "data": [
                {
                    "b64_json": base64.b64encode(b"byteplus-edit").decode("ascii"),
                    "size": "2048x2048",
                }
            ],
            "usage": {"generated_images": 1},
        },
    )
    edit_out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.byteplus.image.edit",
            "input_json": {
                "prompt": "remix the product scene",
                "input_image_refs": ["/generated-assets/uploads/byteplus-source.png"],
                "model": "seedream-4-0-250828",
                "size": "2048x2048",
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )
    assert_byteplus_artifact(
        edit_out,
        expected_bytes=b"byteplus-edit",
        expected_cost_cents=3,
    )
    artifacts = mcp_client.call_tool_structured(
        "artifact.query",
        {"project_id": project_id, "kind": "image"},
    )
    artifact_ids = {
        out["data"]["output_json"]["data"][0]["artifact_id"],
        edit_out["data"]["output_json"]["data"][0]["artifact_id"],
    }
    assert artifact_ids.issubset({row["id"] for row in artifacts["items"]})


def test_action_execute_openai_image_edit_uses_input_reference_images(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mcp_settings: Settings,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_openai_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "openai-images", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    input_dir = mcp_settings.generated_assets_dir / "uploads"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "product.png").write_bytes(b"product-photo")
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _image_edit_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "edit-image",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/edits",
        json={"data": [{"b64_json": base64.b64encode(b"edited-webp").decode("ascii")}]},
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.image.edit",
            "input_json": {
                "prompt": "same product on a marble table",
                "input_image_refs": ["/generated-assets/uploads/product.png"],
                "n": 1,
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    item = data["output_json"]["data"][0]
    rendered = json.dumps(data)
    vendor_request = httpx_mock.get_requests()[-1]
    assert vendor_request.headers["content-type"].startswith("multipart/form-data; boundary=")
    vendor_body = vendor_request.content
    assert b'name="prompt"' in vendor_body
    assert b"same product on a marble table" in vendor_body
    assert b'name="image"; filename="product.png"' in vendor_body
    assert b"Content-Type: image/png" in vendor_body
    assert b"product-photo" in vendor_body
    assert b"data:image/png;base64" not in vendor_body
    assert item["url"].startswith("/generated-assets/openai-images/openai-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert "b64_json" not in item
    assert "sk-openai" not in rendered
    path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"edited-webp"


def test_action_execute_firecrawl_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_firecrawl_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "firecrawl", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _firecrawl_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "scrape-page",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# Hello"}},
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.web.scrape",
            "input_json": {"url": "https://example.com"},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    data = out["data"]
    rendered = json.dumps(data)
    assert data["credential_ref"] == credential_ref
    assert data["output_json"] == {"data": {"markdown": "# Hello"}}
    assert data["action_call"]["provider_key"] == "firecrawl"
    assert data["action_call"]["connector_key"] == "firecrawl"
    assert "credential_id" not in rendered
    assert "fc-key" not in rendered


def test_action_execute_sitemap_grant_uses_noauth_utility_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    described = mcp_client.call_tool_structured(
        "action.describe",
        {"project_id": project_id, "action_ref": "utils.sitemap.fetch"},
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "utils.sitemap.fetch",
            "input_json": {"urls": ["https://example.com/sitemap.xml"], "max_entries": 5},
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["requires_credential"] is False
    assert validation["valid"] is True
    assert validation["credential_ref"] is None

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _sitemap_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "fetch-sitemap",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://example.com/sitemap.xml",
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.com/a</loc></url>"
            "</urlset>"
        ),
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.sitemap.fetch",
            "input_json": {"urls": ["https://example.com/sitemap.xml"], "max_entries": 5},
            "run_token": run_token,
        },
    )

    data = out["data"]
    assert data["credential_ref"] is None
    assert data["action_call"]["provider_key"] is None
    assert data["action_call"]["connector_key"] == "sitemap"
    assert data["output_json"]["entries"][0]["url"] == "https://example.com/a"
    assert data["output_json"]["errors"] == []

    audit_resp = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": created["data"]["id"],
            "run_plan_step_id": claimed["data"]["id"],
            "plugin_slug": "utils",
            "action_key": "sitemap.fetch",
            "status": "success",
        },
        headers=mcp_client._headers(),
    )
    assert audit_resp.status_code == 200
    audit_body = audit_resp.json()
    audit_rendered = json.dumps(audit_body)
    assert audit_body["total_estimate"] == 1
    audit_row = audit_body["items"][0]
    assert audit_row["id"] == data["action_call"]["id"]
    assert audit_row["run_id"] == started["data"]["run_id"]
    assert audit_row["run_plan_id"] == created["data"]["id"]
    assert audit_row["run_plan_step_id"] == claimed["data"]["id"]
    assert audit_row["plugin_slug"] == "utils"
    assert audit_row["action_key"] == "sitemap.fetch"
    assert audit_row["credential_ref"] is None
    assert audit_row["response_json"]["entries"][0]["url"] == "https://example.com/a"
    assert "credential_id" not in audit_rendered
    assert "idempotency_key" not in audit_rendered


def test_action_execute_dataforseo_paa_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_dataforseo_credential(mcp_client, project_id)
    budget_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "dataforseo", "monthly_budget_usd": 10.0},
        headers={"authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert budget_resp.status_code == 200
    described = mcp_client.call_tool_structured(
        "action.describe",
        {"project_id": project_id, "action_ref": "seo.paa.extract"},
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "seo.paa.extract",
            "input_json": {"keyword": "seo tools"},
            "credential_ref": credential_ref,
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["connector_key"] == "dataforseo"
    assert validation["valid"] is True
    assert validation["credential_ref"] == credential_ref

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _dataforseo_paa_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "extract-paa",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        json={
            "tasks": [
                {
                    "cost": 0.001,
                    "result": [{"items": [{"type": "people_also_ask", "title": "What is SEO?"}]}],
                }
            ]
        },
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "seo.paa.extract",
            "input_json": {"keyword": "seo tools"},
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    data = out["data"]
    rendered = json.dumps(data)
    assert request_body[0]["keyword"] == "seo tools"
    assert request_body[0]["people_also_ask_click_depth"] == 1
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "dataforseo"
    assert data["action_call"]["connector_key"] == "dataforseo"
    assert data["output_json"]["tasks"][0]["result"][0]["items"][0]["title"] == "What is SEO?"
    assert "password" not in rendered
    assert "login@example.com" not in rendered


def test_action_execute_wordpress_post_create_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_wordpress_credential(mcp_client, project_id)
    described = mcp_client.call_tool_structured(
        "action.describe",
        {"project_id": project_id, "action_ref": "publishing.wordpress.post.create"},
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "content": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["connector_key"] == "wordpress"
    assert validation["valid"] is True

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _wordpress_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "create-wordpress-post",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://wp.example/wp-json/wp/v2/posts",
        json={"id": 42, "link": "https://wp.example/hello-world/"},
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "publishing.wordpress.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "content": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    request_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    data = out["data"]
    rendered = json.dumps(data)
    assert request_body == {
        "title": "Hello world",
        "content": "<p>Body</p>",
        "status": "draft",
    }
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "wordpress"
    assert data["action_call"]["connector_key"] == "wordpress"
    assert data["output_json"]["id"] == 42
    assert "app pass" not in rendered
    assert "editor" not in rendered


def test_action_execute_ghost_post_create_grant_uses_generic_connector(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _create_ghost_credential(mcp_client, project_id)
    described = mcp_client.call_tool_structured(
        "action.describe",
        {"project_id": project_id, "action_ref": "publishing.ghost.post.create"},
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "publishing.ghost.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "html": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
        },
    )

    assert described["availability"]["status"] == "ready"
    assert described["manifest"]["connector_key"] == "ghost"
    assert validation["valid"] is True

    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _ghost_action_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "create-ghost-post",
            "run_token": run_token,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://ghost.example/ghost/api/admin/posts/?source=html",
        json={"posts": [{"id": "post-1", "url": "https://ghost.example/hello/"}]},
    )

    out = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "publishing.ghost.post.create",
            "input_json": {
                "post": {
                    "title": "Hello world",
                    "html": "<p>Body</p>",
                    "status": "draft",
                }
            },
            "credential_ref": credential_ref,
            "run_token": run_token,
        },
    )

    request = httpx_mock.get_requests()[0]
    request_body = json.loads(request.content.decode("utf-8"))
    data = out["data"]
    rendered = json.dumps(data)
    assert request_body == {
        "posts": [
            {
                "title": "Hello world",
                "html": "<p>Body</p>",
                "status": "draft",
            }
        ]
    }
    assert request.headers["authorization"].startswith("Ghost ")
    assert data["credential_ref"] == credential_ref
    assert data["action_call"]["provider_key"] == "ghost"
    assert data["action_call"]["connector_key"] == "ghost"
    assert data["output_json"]["posts"][0]["id"] == "post-1"
    assert "00112233445566778899aabbccddeeff" not in rendered
    assert "keyid" not in rendered
