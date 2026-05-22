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


def _create_dataforseo_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/dataforseo/credentials",
        json={
            "plaintext_payload": "password",
            "config_json": {"login": "login@example.com"},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "dataforseo"},
    )
    return status["connections"][0]["credential_ref"]


def _create_wordpress_credential(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/auth/wordpress/credentials",
        json={
            "plaintext_payload": json.dumps(
                {"username": "editor", "application_password": "app pass"}
            ),
            "config_json": {"wp_url": "https://wp.example"},
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
            "plaintext_payload": "keyid:00112233445566778899aabbccddeeff",
            "config_json": {"ghost_url": "https://ghost.example", "api_version": "v5.0"},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    status = mcp.call_tool_structured(
        "auth.status",
        {"project_id": project_id, "provider_key": "ghost"},
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
    mcp_client.call_tool_structured(
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
                    "result": [
                        {
                            "items": [
                                {"type": "people_also_ask", "title": "What is SEO?"}
                            ]
                        }
                    ],
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
    assert (
        data["output_json"]["tasks"][0]["result"][0]["items"][0]["title"]
        == "What is SEO?"
    )
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
