"""Agent-facing bridge E2E tests.

These tests exercise ``AgentBridgeProxy`` against a real in-process daemon MCP
app. The bridge is what installable plugins use, so this locks the black-box
agent path: direct tool visibility stays compact while former browser actions
remain reachable through ``toolbox.call``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pytest_httpx import HTTPXMock

from stackos.mcp.bridge import _AGENT_VISIBLE_TOOL_ORDER, AgentBridgeProxy

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


def _scoped_bridge(
    mcp: MCPClient,
    *,
    cwd: str,
    repo_fingerprint: str | None = None,
) -> tuple[AgentBridgeProxy, _BridgeHttpClient]:
    headers = {
        "Authorization": f"Bearer {mcp.auth_token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    return (
        AgentBridgeProxy(
            url="http://daemon.test/mcp",
            headers=headers,
            cwd=cwd,
            repo_fingerprint=repo_fingerprint,
            client_session_id="pytest-scoped-bridge",
        ),
        _BridgeHttpClient(mcp),
    )


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


def _toolbox_call(
    proxy: AgentBridgeProxy,
    client: _BridgeHttpClient,
    name: str,
    arguments: dict[str, Any] | None = None,
    request_id: object = 1,
) -> dict[str, Any]:
    return _tool_call(
        proxy,
        client,
        "toolbox.call",
        {"tool_name": name, "arguments": arguments or {}},
        request_id=request_id,
    )


def _structured(envelope: dict[str, Any]) -> dict[str, Any]:
    return envelope["result"].get("structuredContent") or envelope["result"]


def _create_project(mcp: MCPClient, slug: str) -> int:
    created = mcp.call_tool_structured(
        "project.create",
        {
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "domain": f"{slug}.example",
            "locale": "en-US",
        },
    )
    return int(created["data"]["id"])


def _is_bridge_scope_error(envelope: dict[str, Any]) -> bool:
    result = envelope["result"]
    return result["isError"] is True and result["structuredContent"]["code"] == -32007


def test_bridge_lists_only_agent_surface(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)

    envelope = _send(proxy, client, method="tools/list", request_id="tools")
    names = [tool["name"] for tool in envelope["result"]["tools"]]

    assert names[: len(_AGENT_VISIBLE_TOOL_ORDER)] == list(_AGENT_VISIBLE_TOOL_ORDER)
    assert "toolbox.describe" in names
    assert "toolbox.call" in names
    assert names == [
        "workspace.startSession",
        "workspace.resolve",
        "toolbox.describe",
        "toolbox.call",
    ]
    assert "workspace.bootstrap" not in names
    assert "action.run" not in names
    assert "toolProfile.resolve" not in names
    assert "schedule.remove" not in names
    assert "integration.set" not in names
    assert "project.list" not in names
    assert "project.create" not in names
    assert "project.get" not in names
    assert "project.setActive" not in names
    assert "agentRequest.list" not in names
    assert "agentRequest.claim" not in names
    assert "agentRequest.create" not in names
    assert "context.query" not in names
    assert "learning.query" not in names
    assert "workflowTemplate.list" not in names
    assert "workflowTemplate.validate" not in names
    assert "runPlan.create" not in names
    assert "runPlan.start" not in names
    assert "learning.create" not in names
    assert "decision.record" not in names
    assert "workflowTemplate.save" not in names
    assert "runPlan.claimStep" not in names
    assert "action.execute" not in names
    assert "dataforseo.serp" not in names
    describe_tool = next(
        tool for tool in envelope["result"]["tools"] if tool["name"] == "toolbox.describe"
    )
    assert "tool_names" in describe_tool["inputSchema"]["properties"]


def test_bridge_compacts_noisy_agent_responses_by_default(mcp_client: MCPClient) -> None:
    project_id = _create_project(mcp_client, "bridge-compact-project")
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "path:bridge-compact",
            "last_known_root": "/tmp/bridge-compact-project",
        },
    )
    credential = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/auth/mock-provider/credentials",
        json={
            "auth_method_key": "api_key",
            "profile_key": "primary",
            "fields": {"api_key": "mock-secret"},
        },
        headers=mcp_client._headers(),
    )
    credential.raise_for_status()
    proxy, client = _scoped_bridge(
        mcp_client,
        cwd="/tmp/bridge-compact-project",
        repo_fingerprint="path:bridge-compact",
    )
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    compact = _structured(
        _toolbox_call(
            proxy,
            client,
            "auth.status",
            {"provider_key": "mock-provider"},
            request_id="auth-compact",
        )
    )
    standard = _structured(
        _toolbox_call(
            proxy,
            client,
            "auth.status",
            {"provider_key": "mock-provider", "response_mode": "standard"},
            request_id="auth-standard",
        )
    )
    resolved_compact = _structured(
        _toolbox_call(
            proxy,
            client,
            "toolProfile.resolve",
            {"provider_key": "mock-provider"},
            request_id="resolver-compact",
        )
    )
    resolved_standard = _structured(
        _toolbox_call(
            proxy,
            client,
            "toolProfile.resolve",
            {"provider_key": "mock-provider", "response_mode": "standard"},
            request_id="resolver-standard",
        )
    )

    assert compact["project_id"] == project_id
    assert compact["connections"][0]["credential_ref"].startswith("cred_")
    assert compact["providers"][0]["status"] == "connected"
    assert "auth_methods" not in compact["providers"][0]
    assert "auth_methods" in standard["providers"][0]
    assert resolved_compact["project_id"] == project_id
    assert resolved_compact["ready"] is True
    assert resolved_compact["credential"]["credential_ref"].startswith("cred_")
    assert "scopes" not in resolved_compact["credential"]
    assert "scopes" in resolved_standard["credential"]
    assert "mock-secret" not in json.dumps(compact)
    assert "mock-secret" not in json.dumps(resolved_compact)


def test_bridge_scopes_project_from_workspace_and_injects_project_id(
    mcp_client: MCPClient,
) -> None:
    project_id = _create_project(mcp_client, "bridge-scoped-project")
    other_project_id = _create_project(mcp_client, "bridge-other-project")
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "path:bridge-scoped",
            "last_known_root": "/tmp/bridge-scoped-project",
        },
    )
    other_binding = mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": other_project_id,
            "repo_fingerprint": "path:bridge-other",
            "last_known_root": "/tmp/bridge-other-project",
        },
    )
    other_record_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{other_project_id}/resource-records",
        json={
            "plugin_slug": "core",
            "resource_key": "learning",
            "data_json": {"body": "other project"},
        },
        headers=mcp_client._headers(),
    )
    other_record_resp.raise_for_status()
    other_record_id = other_record_resp.json()["data"]["id"]
    other_artifact_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{other_project_id}/artifacts",
        json={"kind": "note", "uri": "/tmp/other.txt"},
        headers=mcp_client._headers(),
    )
    other_artifact_resp.raise_for_status()
    other_artifact_id = other_artifact_resp.json()["data"]["id"]
    other_run = mcp_client.call_tool_structured(
        "run.start",
        {"project_id": other_project_id, "kind": "skill-run"},
    )
    other_run_id = other_run["data"]["run_id"]
    other_plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": other_project_id,
            "run_plan_json": {
                "schema_version": "stackos.run-plan.v1",
                "key": "bridge.other.run",
                "title": "Other project plan",
                "steps": [{"id": "other", "title": "Other"}],
            },
        },
    )
    other_run_plan_id = other_plan["data"]["id"]
    other_schedule = mcp_client.call_tool_structured(
        "schedule.set",
        {
            "project_id": other_project_id,
            "kind": "other-weekly-review",
            "cron_expr": "0 6 * * 1",
            "enabled": True,
        },
    )
    other_schedule_id = other_schedule["data"]["id"]
    proxy, client = _scoped_bridge(
        mcp_client,
        cwd="/tmp/bridge-scoped-project",
        repo_fingerprint="path:bridge-scoped",
    )
    _initialize(proxy, client)

    _send(proxy, client, method="tools/list", request_id="tools")
    described = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {
                "tool_names": [
                    "auth.status",
                    "toolProfile.resolve",
                    "workspace.connect",
                ]
            },
            request_id="describe-schemas",
        )
    )
    by_tool = {tool["name"]: tool for tool in described["described_tools"]}
    auth_tool = by_tool["auth.status"]
    resolver_tool = by_tool["toolProfile.resolve"]
    workspace_connect_tool = by_tool["workspace.connect"]
    auth_required = auth_tool["inputSchema"].get("required", [])
    resolver_required = resolver_tool["inputSchema"].get("required", [])
    workspace_connect_required = workspace_connect_tool["inputSchema"].get("required", [])
    resolved = _structured(
        _tool_call(
            proxy,
            client,
            "workspace.resolve",
            {"cwd": "/tmp/bridge-scoped-project/packages/site"},
            request_id="workspace-resolve",
        )
    )
    status = _structured(
        _toolbox_call(
            proxy,
            client,
            "auth.status",
            {"provider_key": "mock-provider"},
            request_id="auth-status",
        )
    )
    resolved_tool = _structured(
        _toolbox_call(
            proxy,
            client,
            "toolProfile.resolve",
            {"provider_key": "mock-provider"},
            request_id="tool-profile-resolve",
        )
    )
    connected = _structured(
        _toolbox_call(
            proxy,
            client,
            "workspace.connect",
            {"response_mode": "standard"},
            request_id="workspace-connect",
        )
    )
    cross_project = _toolbox_call(
        proxy,
        client,
        "auth.status",
        {"project_id": project_id + 1000, "provider_key": "mock-provider"},
        request_id="auth-status-cross",
    )
    cross_workspace = _tool_call(
        proxy,
        client,
        "workspace.resolve",
        {"cwd": "/tmp/other-project"},
        request_id="workspace-resolve-cross",
    )
    cross_resource = _toolbox_call(
        proxy,
        client,
        "resource.get",
        {"record_id": other_record_id},
        request_id="resource-get-cross",
    )
    cross_artifact = _toolbox_call(
        proxy,
        client,
        "artifact.get",
        {"artifact_id": other_artifact_id},
        request_id="artifact-get-cross",
    )
    cross_run_plan = _toolbox_call(
        proxy,
        client,
        "runPlan.get",
        {"run_plan_id": other_run_plan_id},
        request_id="run-plan-get-cross",
    )
    cross_run = _toolbox_call(
        proxy,
        client,
        "run.get",
        {"run_id": other_run_id},
        request_id="run-get-cross",
    )
    cross_heartbeat = _toolbox_call(
        proxy,
        client,
        "run.heartbeat",
        {"run_id": other_run_id},
        request_id="run-heartbeat-cross",
    )
    cross_abort = _toolbox_call(
        proxy,
        client,
        "run.abort",
        {"run_id": other_run_id},
        request_id="run-abort-cross",
    )
    cross_binding_update = _toolbox_call(
        proxy,
        client,
        "workspace.updateProfile",
        {"binding_id": other_binding["data"]["id"], "framework": "next"},
        request_id="workspace-update-cross",
    )
    cross_schedule_remove = _tool_call(
        proxy,
        client,
        "toolbox.call",
        {"tool_name": "schedule.remove", "arguments": {"job_id": other_schedule_id}},
        request_id="schedule-remove-cross",
    )
    cross_schedule_toggle = _tool_call(
        proxy,
        client,
        "toolbox.call",
        {
            "tool_name": "schedule.toggle",
            "arguments": {"job_id": other_schedule_id, "enabled": False},
        },
        request_id="schedule-toggle-cross",
    )

    assert "project_id" not in auth_required
    assert "project_id" not in resolver_required
    assert "project_id" not in workspace_connect_required
    assert "repo_fingerprint" not in workspace_connect_required
    assert resolved["project_id"] == project_id
    assert status["project_id"] == project_id
    assert resolved_tool["project_id"] == project_id
    assert resolved_tool["provider"]["provider_key"] == "mock-provider"
    assert connected["project_id"] == project_id
    assert connected["data"]["repo_fingerprint"] == "path:bridge-scoped"
    assert connected["data"]["last_known_root"] == str(Path("/tmp/bridge-scoped-project").resolve())
    assert cross_project["result"]["isError"] is True
    assert cross_project["result"]["structuredContent"]["code"] == -32007
    assert _is_bridge_scope_error(cross_workspace)
    assert cross_resource["result"]["isError"] is True
    assert cross_artifact["result"]["isError"] is True
    assert cross_run_plan["result"]["isError"] is True
    assert cross_run["result"]["isError"] is True
    assert cross_heartbeat["result"]["isError"] is True
    assert cross_abort["result"]["isError"] is True
    assert cross_binding_update["result"]["isError"] is True
    assert cross_schedule_remove["result"]["isError"] is True
    assert cross_schedule_toggle["result"]["isError"] is True


def test_bridge_unbound_workspace_autobootstraps_and_unlocks_project_scoped_tools(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _scoped_bridge(
        mcp_client,
        cwd="/tmp/bridge-auto-start",
        repo_fingerprint="path:bridge-auto-start",
    )
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    project_scoped_after_tool_list = _toolbox_call(
        proxy,
        client,
        "workflowTemplate.list",
        {},
        request_id="workflow-template-list-after-tools",
    )
    tracker_after_tool_list = _structured(
        _toolbox_call(
            proxy,
            client,
            "tracker.status",
            {},
            request_id="tracker-status-after-tools",
        )
    )
    resolved = _structured(
        _tool_call(
            proxy,
            client,
            "workspace.resolve",
            {},
            request_id="resolve-bound",
        )
    )
    bindings = _structured(
        _toolbox_call(
            proxy,
            client,
            "workspace.listBindings",
            {},
            request_id="list-bindings-bound",
        )
    )
    discovery = _toolbox_call(
        proxy,
        client,
        "plugin.list",
        {},
        request_id="plugin-list-bound",
    )
    refreshed = _structured(
        _tool_call(
            proxy,
            client,
            "workspace.startSession",
            {},
            request_id="workspace-start-session-bound",
        )
    )
    project_scoped_after_refresh = _toolbox_call(
        proxy,
        client,
        "workflowTemplate.list",
        {},
        request_id="workflow-template-list-after-refresh",
    )
    assert project_scoped_after_tool_list["result"]["isError"] is False
    assert resolved["workspace_bound"] is True
    assert resolved["needs_connect"] is False
    assert resolved["project_id"] is not None
    assert tracker_after_tool_list["project_id"] == resolved["project_id"]
    assert discovery["result"]["isError"] is False
    assert refreshed["project_id"] == resolved["project_id"]
    assert project_scoped_after_refresh["result"]["isError"] is False
    assert bindings["items"][0]["project_id"] == resolved["project_id"]


def test_bridge_toolbox_bootstrap_promotes_workspace_scope(
    mcp_client: MCPClient,
) -> None:
    project_id = _create_project(mcp_client, "bridge-toolbox-connect-later")
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    connected = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": "workspace.bootstrap",
                "arguments": {
                    "cwd": "/tmp/bridge-toolbox-connect-later",
                    "repo_fingerprint": "path:bridge-toolbox-connect-later",
                    "project_slug": "bridge-toolbox-connect-later",
                },
            },
            request_id="toolbox-workspace-bootstrap-later",
        )
    )
    project_scoped_after_bootstrap = _toolbox_call(
        proxy,
        client,
        "workflowTemplate.list",
        {},
        request_id="workflow-template-list-bound",
    )

    assert connected["project_id"] == project_id
    assert connected["data"]["project_was_created"] is False
    assert connected["data"]["binding_was_created"] is True
    assert project_scoped_after_bootstrap["result"]["isError"] is False


def test_bridge_describes_setup_tools_and_treats_removed_vendor_tools_as_unknown(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    envelope = _tool_call(
        proxy,
        client,
        "toolbox.describe",
        {
            "tool_names": [
                "project.delete",
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
        "schedule.remove",
        "auth.test",
    ]
    assert payload["denied_tool_names"] == ["project.delete", "auth.start"]
    assert payload["unknown_tool_names"] == ["dataforseo.serp"]
    assert "admin_gated_tool_names" not in payload


def test_bridge_toolbox_operates_setup_actions(
    mcp_client: MCPClient,
    httpx_mock: HTTPXMock,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    project_id = _create_project(mcp_client, "bridge-agent-path")

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
        json={"auth_method_key": "api_key", "fields": {"api_key": "fc-key"}},
        headers=mcp_client._headers(),
    )
    credential_resp.raise_for_status()
    status = _structured(
        _toolbox_call(
            proxy,
            client,
            "auth.status",
            {"project_id": project_id, "provider_key": "firecrawl"},
            request_id="auth-status",
        )
    )
    credential_ref = status["connections"][0]["credential_ref"]
    tested = _structured(
        _toolbox_call(
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

    project_id = _create_project(mcp_client, "bridge-run-plan")
    created_plan = _structured(
        _toolbox_call(
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
        _toolbox_call(
            proxy,
            client,
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": run_plan_id},
            request_id="run-plan-start",
        )
    )
    run_id = started["data"]["run_id"]
    second_plan = _structured(
        _toolbox_call(
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
        _toolbox_call(
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

    project_id = _create_project(mcp_client, "bridge-run-plan-grant")
    created_plan = _structured(
        _toolbox_call(
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
        _toolbox_call(
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

    project_id = _create_project(mcp_client, "bridge-action-grant")
    cred_resp = mcp_client.test_client.post(
        f"/api/v1/projects/{project_id}/auth/openai-images/credentials",
        json={"auth_method_key": "api_key", "fields": {"api_key": "sk-openai"}},
        headers=mcp_client._headers(),
    )
    cred_resp.raise_for_status()
    auth_status = _structured(
        _toolbox_call(
            proxy,
            client,
            "auth.status",
            {"project_id": project_id, "provider_key": "openai-images"},
            request_id="auth-status",
        )
    )
    credential_ref = auth_status["connections"][0]["credential_ref"]
    created_plan = _structured(
        _toolbox_call(
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
        _toolbox_call(
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


def test_bridge_refuses_removed_vendor_tool(mcp_client: MCPClient) -> None:
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
    assert result["structuredContent"]["code"] == -32601
