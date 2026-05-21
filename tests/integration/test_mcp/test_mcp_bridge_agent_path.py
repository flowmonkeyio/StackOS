"""Agent-facing bridge E2E tests.

These tests exercise ``AgentBridgeProxy`` against a real in-process daemon MCP
app. The bridge is what installable plugins use, so this locks the black-box
agent path: direct tool visibility stays compact while former browser actions
remain reachable through ``toolbox.call``.
"""

from __future__ import annotations

import json
from typing import Any

from pytest_httpx import HTTPXMock

from content_stack.mcp.bridge import _AGENT_VISIBLE_TOOL_ORDER, AgentBridgeProxy

from .conftest import MCPClient


class _BridgeHttpClient:
    """httpx-like adapter that posts bridge requests into TestClient."""

    def __init__(self, mcp: MCPClient) -> None:
        self._mcp = mcp

    def post(self, _url: str, *, content: str, headers: dict[str, str]) -> Any:
        return self._mcp.test_client.post("/mcp", content=content, headers=headers)


def _bridge(mcp: MCPClient) -> tuple[AgentBridgeProxy, _BridgeHttpClient]:
    headers = {
        "Authorization": f"Bearer {mcp.auth_token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    return AgentBridgeProxy(url="http://daemon.test/mcp", headers=headers), _BridgeHttpClient(mcp)


def _rpc(method: str, params: dict[str, Any] | None = None, request_id: object = 1) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )


def _send(
    proxy: AgentBridgeProxy,
    client: _BridgeHttpClient,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    request_id: object = 1,
) -> dict[str, Any]:
    line = _rpc(method, params, request_id)
    return json.loads(
        proxy.handle(client, payload=json.loads(line), line=line, request_id=request_id)
    )


def _initialize(proxy: AgentBridgeProxy, client: _BridgeHttpClient) -> dict[str, Any]:
    return _send(
        proxy,
        client,
        method="initialize",
        params={
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "pytest-bridge-client", "version": "0.1"},
        },
        request_id="init",
    )


def _tool_call(
    proxy: AgentBridgeProxy,
    client: _BridgeHttpClient,
    name: str,
    arguments: dict[str, Any] | None = None,
    request_id: object = 1,
) -> dict[str, Any]:
    return _send(
        proxy,
        client,
        method="tools/call",
        params={"name": name, "arguments": arguments or {}},
        request_id=request_id,
    )


def _structured(envelope: dict[str, Any]) -> dict[str, Any]:
    return envelope["result"].get("structuredContent") or envelope["result"]


def test_bridge_lists_only_agent_surface(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)

    envelope = _send(proxy, client, method="tools/list", request_id="tools")
    names = [tool["name"] for tool in envelope["result"]["tools"]]

    assert names[: len(_AGENT_VISIBLE_TOOL_ORDER)] == list(_AGENT_VISIBLE_TOOL_ORDER)
    assert "toolbox.describe" in names
    assert "toolbox.call" in names
    assert "schedule.remove" not in names
    assert "integration.set" not in names
    assert "context.query" in names
    assert "learning.query" in names
    assert "workflowTemplate.list" in names
    assert "workflowTemplate.validate" in names
    assert "runPlan.create" in names
    assert "runPlan.start" in names
    assert "learning.create" not in names
    assert "decision.record" not in names
    assert "workflowTemplate.save" not in names
    assert "runPlan.claimStep" not in names
    assert "action.execute" not in names
    assert "dataforseo.serp" not in names


def test_bridge_describes_setup_tools_and_denies_vendor_tools(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    envelope = _tool_call(
        proxy,
        client,
        "toolbox.describe",
        {
            "tool_names": [
                "project.setActive",
                "schedule.remove",
                "auth.test",
                "auth.start",
                "dataforseo.serp",
            ]
        },
        request_id="describe",
    )
    payload = _structured(envelope)

    assert [tool["name"] for tool in payload["described_tools"]] == [
        "project.setActive",
        "schedule.remove",
        "auth.test",
    ]
    assert payload["denied_tool_names"] == ["auth.start", "dataforseo.serp"]
    assert "admin_gated_tool_names" not in payload


def test_bridge_toolbox_operates_setup_actions(
    mcp_client: MCPClient,
    httpx_mock: HTTPXMock,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    created = _structured(
        _tool_call(
            proxy,
            client,
            "project.create",
            {
                "slug": "bridge-agent-path",
                "name": "Bridge Agent Path",
                "domain": "bridge-agent-path.example",
                "locale": "en-US",
            },
            request_id="project-create",
        )
    )
    project_id = created["data"]["id"]

    activated = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": "project.setActive",
                "arguments": {"project_id": project_id},
            },
            request_id="project-activate",
        )
    )
    assert activated["data"]["is_active"] is True

    budget = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": "budget.set",
                "arguments": {
                    "project_id": project_id,
                    "kind": "firecrawl",
                    "monthly_budget_usd": 25.0,
                },
            },
            request_id="budget-set",
        )
    )
    assert budget["data"]["monthly_budget_usd"] == 25.0

    schedule = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": "schedule.set",
                "arguments": {
                    "project_id": project_id,
                    "kind": "weekly-review",
                    "cron_expr": "0 4 * * *",
                    "enabled": True,
                },
            },
            request_id="schedule-set",
        )
    )
    removed_schedule = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": "schedule.remove",
                "arguments": {"job_id": schedule["data"]["id"]},
            },
            request_id="schedule-remove",
        )
    )
    assert removed_schedule["data"]["enabled"] is False

    httpx_mock.add_response(
        method="POST",
        url="https://api.firecrawl.dev/v2/scrape",
        json={"data": {"markdown": "# ok"}},
    )
    credential_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/auth/firecrawl/credentials",
        json={"plaintext_payload": "fc-key"},
        headers=mcp_client._headers(),
    )
    credential_resp.raise_for_status()
    status = _structured(
        _tool_call(
            proxy,
            client,
            "auth.status",
            {"project_id": project_id, "provider_key": "firecrawl"},
            request_id="auth-status",
        )
    )
    credential_ref = status["connections"][0]["credential_ref"]
    tested = _structured(
        _tool_call(
            proxy,
            client,
            "auth.test",
            {"project_id": project_id, "credential_ref": credential_ref},
            request_id="auth-test",
        )
    )
    assert tested["data"]["ok"] is True
    assert tested["data"]["provider_key"] == "firecrawl"


def test_bridge_allows_started_run_plan_controller_tools(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    created_project = _structured(
        _tool_call(
            proxy,
            client,
            "project.create",
            {
                "slug": "bridge-run-plan",
                "name": "Bridge Run Plan",
                "domain": "bridge-run-plan.example",
                "locale": "en-US",
            },
            request_id="project-create",
        )
    )
    project_id = created_project["data"]["id"]
    created_plan = _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "bridge.review.run",
                    "title": "Bridge review",
                    "steps": [{"id": "review", "title": "Review"}],
                },
            },
            request_id="run-plan-create",
        )
    )
    run_plan_id = created_plan["data"]["id"]
    started = _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": run_plan_id},
            request_id="run-plan-start",
        )
    )
    run_id = started["data"]["run_id"]
    second_plan = _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "bridge.review.second",
                    "title": "Bridge review second",
                    "steps": [{"id": "review", "title": "Review"}],
                },
            },
            request_id="run-plan-create-second",
        )
    )
    _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": second_plan["data"]["id"]},
            request_id="run-plan-start-second",
        )
    )

    described = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {"run_id": run_id, "tool_names": ["runPlan.claimStep"]},
            request_id="describe-run-plan",
        )
    )
    cross_plan = _tool_call(
        proxy,
        client,
        "toolbox.call",
        {
            "run_id": run_id,
            "tool_name": "runPlan.claimStep",
            "arguments": {"run_plan_id": second_plan["data"]["id"], "step_id": "review"},
        },
        request_id="claim-run-plan-cross",
    )
    claimed = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "run_id": run_id,
                "tool_name": "runPlan.claimStep",
                "arguments": {"run_plan_id": run_plan_id, "step_id": "review"},
            },
            request_id="claim-run-plan",
        )
    )
    completed = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "run_id": run_id,
                "tool_name": "runPlan.recordStep",
                "arguments": {
                    "run_plan_id": run_plan_id,
                    "step_id": "review",
                    "status": "success",
                    "result_json": {"summary": "ok"},
                },
            },
            request_id="record-run-plan",
        )
    )

    assert [tool["name"] for tool in described["described_tools"]] == ["runPlan.claimStep"]
    assert cross_plan["result"]["isError"] is True
    assert cross_plan["result"]["structuredContent"]["code"] == -32008
    assert claimed["data"]["status"] == "running"
    assert completed["data"]["status"] == "completed"


def test_bridge_exposes_run_plan_granted_generic_tool_after_claim(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    created_project = _structured(
        _tool_call(
            proxy,
            client,
            "project.create",
            {
                "slug": "bridge-run-plan-grant",
                "name": "Bridge Run Plan Grant",
                "domain": "bridge-run-plan-grant.example",
                "locale": "en-US",
            },
            request_id="project-create",
        )
    )
    project_id = created_project["data"]["id"]
    created_plan = _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "bridge.resource.run",
                    "title": "Bridge resource write",
                    "grants": {
                        "mcp_tool_grants": [
                            {
                                "step_id": "write",
                                "tool": "resource.upsert",
                                "plugin_slug": "core",
                                "resource_key": "learning",
                            }
                        ]
                    },
                    "steps": [{"id": "write", "title": "Write resource"}],
                },
            },
            request_id="run-plan-create",
        )
    )
    run_plan_id = created_plan["data"]["id"]
    started = _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": run_plan_id},
            request_id="run-plan-start",
        )
    )
    run_id = started["data"]["run_id"]
    before_claim = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {"run_id": run_id, "tool_names": ["resource.upsert"]},
            request_id="describe-before-claim",
        )
    )
    _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "run_id": run_id,
                "tool_name": "runPlan.claimStep",
                "arguments": {"run_plan_id": run_plan_id, "step_id": "write"},
            },
            request_id="claim-run-plan",
        )
    )
    after_claim = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {"run_id": run_id, "tool_names": ["resource.upsert"]},
            request_id="describe-after-claim",
        )
    )
    written = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "run_id": run_id,
                "tool_name": "resource.upsert",
                "arguments": {
                    "project_id": project_id,
                    "plugin_slug": "core",
                    "resource_key": "learning",
                    "data_json": {"body": "bridge injected run token"},
                },
            },
            request_id="resource-upsert",
        )
    )

    assert before_claim["denied_tool_names"] == ["resource.upsert"]
    assert [tool["name"] for tool in after_claim["described_tools"]] == ["resource.upsert"]
    assert written["data"]["data_json"] == {"body": "bridge injected run token"}


def test_bridge_executes_run_plan_granted_action_with_injected_token(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    created_project = _structured(
        _tool_call(
            proxy,
            client,
            "project.create",
            {
                "slug": "bridge-action-grant",
                "name": "Bridge Action Grant",
                "domain": "bridge-action-grant.example",
                "locale": "en-US",
            },
            request_id="project-create",
        )
    )
    project_id = created_project["data"]["id"]
    cred_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/auth/openai-images/credentials",
        json={"plaintext_payload": "sk-openai"},
        headers=mcp_client._headers(),
    )
    cred_resp.raise_for_status()
    auth_status = _structured(
        _tool_call(
            proxy,
            client,
            "auth.status",
            {"project_id": project_id, "provider_key": "openai-images"},
            request_id="auth-status",
        )
    )
    credential_ref = auth_status["connections"][0]["credential_ref"]
    created_plan = _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "bridge.action.run",
                    "title": "Bridge action",
                    "grants": {
                        "mcp_tool_grants": [
                            {
                                "step_id": "generate",
                                "tool": "action.execute",
                                "action_refs": ["utils.image.generate"],
                            }
                        ]
                    },
                    "steps": [
                        {
                            "id": "generate",
                            "title": "Generate",
                            "action_refs": ["utils.image.generate"],
                        }
                    ],
                },
            },
            request_id="run-plan-create",
        )
    )
    run_plan_id = created_plan["data"]["id"]
    started = _structured(
        _tool_call(
            proxy,
            client,
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": run_plan_id},
            request_id="run-plan-start",
        )
    )
    run_id = started["data"]["run_id"]
    _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "run_id": run_id,
                "tool_name": "runPlan.claimStep",
                "arguments": {"run_plan_id": run_plan_id, "step_id": "generate"},
            },
            request_id="claim-run-plan",
        )
    )
    described = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {"run_id": run_id, "tool_names": ["action.execute"]},
            request_id="describe-action-execute",
        )
    )
    executed = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "run_id": run_id,
                "tool_name": "action.execute",
                "arguments": {
                    "project_id": project_id,
                    "action_ref": "utils.image.generate",
                    "input_json": {"prompt": "editorial hero"},
                    "credential_ref": credential_ref,
                    "dry_run": True,
                },
            },
            request_id="action-execute",
        )
    )

    assert [tool["name"] for tool in described["described_tools"]] == ["action.execute"]
    assert executed["data"]["dry_run"] is True
    assert executed["data"]["credential_ref"] == credential_ref


def test_bridge_refuses_ungranted_vendor_tool(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    envelope = _tool_call(
        proxy,
        client,
        "toolbox.call",
        {
            "tool_name": "dataforseo.serp",
            "arguments": {"project_id": 1, "keyword": "sportsbook"},
        },
        request_id="denied-vendor",
    )
    result = envelope["result"]

    assert result["isError"] is True
    assert result["structuredContent"]["code"] == -32007
    assert result["structuredContent"]["data"]["tool"] == "dataforseo.serp"
