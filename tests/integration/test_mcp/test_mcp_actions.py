"""MCP tests for StackOS action describe/validate tools."""

from __future__ import annotations

import base64
import json

from pytest_httpx import HTTPXMock

from content_stack.config import Settings

from .conftest import MCPClient


def test_action_describe_and_validate_are_read_only_discovery_tools(
    mcp_client: MCPClient,
) -> None:
    tools = {tool["name"] for tool in mcp_client.list_tools()}

    assert "action.describe" in tools
    assert "action.validate" in tools
    assert "action.execute" in tools

    described = mcp_client.call_tool_structured(
        "action.describe",
        {"action_ref": "core.catalog.describe"},
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {"action_ref": "core.catalog.describe", "input_json": {}},
    )

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

    assert described["availability"]["status"] == "ready"
    assert described["execution_available"] is True
    assert described["availability"]["credential_state"] == "available"
    assert described["availability"]["budget_state"] == "available"


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
        json={"plaintext_payload": "sk-openai"},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "openai-images"},
    )
    return status["connections"][0]["credential_ref"]


def _create_firecrawl_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={"plaintext_payload": "fc-key"},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "firecrawl"},
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
    assert "b64_json" not in item
    assert "credential_id" not in rendered
    assert "sk-openai" not in rendered
    assert "Authorization" not in rendered
    path = mcp_settings.generated_assets_dir / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"webp"


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
