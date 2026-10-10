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

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import AgentSession, Credential, Project, WorkspaceBinding
from stackos.mcp.bridge import _AGENT_VISIBLE_TOOL_ORDER, AgentBridgeProxy

from .conftest import MODERN_PROTOCOL_VERSION, MCPClient
from .test_mcp_actions import _mock_action_plan_json

_PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
_CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
_CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"


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


def _send_modern(
    proxy: AgentBridgeProxy,
    client: _BridgeHttpClient,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    request_id: object = 1,
) -> dict[str, Any]:
    request_params = dict(params or {})
    request_params["_meta"] = {
        _PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
        _CLIENT_INFO_META_KEY: {"name": "pytest-modern-bridge-client", "version": "0.1"},
        _CLIENT_CAPABILITIES_META_KEY: {},
    }
    line = _rpc(method, request_params, request_id)
    return json.loads(
        proxy.handle(client, payload=json.loads(line), line=line, request_id=request_id)
    )


def _initialize(proxy: AgentBridgeProxy, client: _BridgeHttpClient) -> dict[str, Any]:
    return _send(
        proxy,
        client,
        method="initialize",
        params={
            "protocolVersion": "2025-11-25",
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


def _operation_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


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


@pytest.mark.parametrize("host", ["chatgpt", "claude"])
def test_global_scope_discovery_does_not_bootstrap(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
) -> None:
    from stackos.cli.daemon_commands import _mcp_bridge_workspace_hints

    root = tmp_path / host / "hi"
    root.mkdir(parents=True)
    monkeypatch.delenv("STACKOS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    if host == "claude":
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    hints = _mcp_bridge_workspace_hints(root if host == "chatgpt" else Path("/"))
    proxy, client = _bridge(mcp_client)
    proxy = AgentBridgeProxy(url=proxy.url, headers=proxy.headers, **hints)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list")
    engine = make_engine(mcp_settings.db_path)
    with Session(engine) as session:
        assert session.exec(select(Project)).all() == []
        assert session.exec(select(WorkspaceBinding)).all() == []
        assert session.exec(select(AgentSession)).all() == []
    selected = _create_project(mcp_client, f"chosen-{host}")
    for arguments in (
        {"project_id": selected},
        {"project_id": selected, "global_session": False},
        {"project_id": 99999, "global_session": True},
    ):
        failed = _tool_call(proxy, client, "workspace.startSession", arguments)
        assert failed["result"]["isError"] is True
        assert proxy.global_session is None
    with Session(engine) as session:
        assert session.exec(select(WorkspaceBinding)).all() == []
        assert session.exec(select(AgentSession)).all() == []
        assert len(session.exec(select(Project)).all()) == 1
    started = _tool_call(
        proxy, client, "workspace.startSession", {"global_session": True, "project_id": selected}
    )
    assert not started["result"].get("isError"), started
    assert proxy.global_session is True and proxy.scoped_project_id is None
    with Session(engine) as session:
        assert session.exec(select(WorkspaceBinding)).all() == []
        rows = session.exec(select(AgentSession)).all()
        assert len(rows) == 1 and rows[0].project_id == selected
        assert rows[0].cwd is None and rows[0].repo_fingerprint is None
    engine.dispose()


def test_global_scope_resolve_never_selects_a_default(mcp_client: MCPClient) -> None:
    selected = mcp_client.call_tool_structured("workspace.bootstrap", {"project_name": "Named"})
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    started = _tool_call(proxy, client, "workspace.startSession", {"global_session": True})
    assert not started["result"].get("isError")
    result = _tool_call(proxy, client, "workspace.resolve", {"workspace_alias": "named"})
    assert _structured(result)["project_id"] == selected["project_id"]
    assert proxy.scoped_project_id is None
    denied = _toolbox_call(
        proxy, client, "resource.query", {"plugin_slug": "core", "resource_key": "learning"}
    )
    assert denied["result"]["isError"] is True


@pytest.mark.parametrize("operation", ["workspace.connect", "workspace.bootstrap"])
def test_global_scope_named_setup_never_promotes_project(
    mcp_client: MCPClient,
    tmp_path: Path,
    operation: str,
) -> None:
    a = _create_project(mcp_client, "named-a")
    b = _create_project(mcp_client, "named-b")
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path / "ambient-hi"))
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {"global_session": True, "project_id": a})
    result = _toolbox_call(
        proxy, client, operation, {"project_id": b, "workspace_alias": "selected-b"}
    )
    assert not result["result"].get("isError"), result
    assert _structured(result)["project_id"] == b
    assert proxy.scoped_project_id is None
    created = _toolbox_call(proxy, client, "workspace.bootstrap", {"project_name": "Deliberate C"})
    assert not created["result"].get("isError"), created
    c = _structured(created)["project_id"]
    assert c not in {a, b}
    for project in (a, b, a, c):
        selected = _toolbox_call(proxy, client, "project.get", {"project_id": project})
        assert _operation_data(_structured(selected))["id"] == project
    _tool_call(proxy, client, "workspace.startSession", {"project_id": b})
    assert proxy.scoped_project_id is None
    denied = _toolbox_call(proxy, client, "workflowTemplate.list", {})
    assert denied["result"]["isError"] is True
    assert (
        _toolbox_call(proxy, client, "workflowTemplate.list", {"project_id": a})["result"][
            "isError"
        ]
        is False
    )


def test_first_named_workspace_startup_preserves_protected_alias(mcp_client: MCPClient) -> None:
    named = mcp_client.call_tool_structured(
        "workspace.bootstrap", {"project_name": "Named", "workspace_alias": "known"}
    )
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    selected = _tool_call(proxy, client, "workspace.startSession", {"workspace_alias": "known"})
    assert _structured(selected)["project_id"] == named["project_id"]
    assert proxy.scoped_project_id == named["project_id"]
    assert proxy.global_session is False
    assert _toolbox_call(proxy, client, "workflowTemplate.list", {})["result"]["isError"] is False
    denied = _tool_call(proxy, client, "workspace.startSession", {"global_session": True})
    assert denied["result"]["isError"] is True


def test_global_scope_deliberate_root_waits_for_binding(
    mcp_client: MCPClient, tmp_path: Path
) -> None:
    other = _create_project(mcp_client, "other")
    root = tmp_path / "chosen-folder"
    root.mkdir()
    proxy, client = _scoped_bridge(mcp_client, cwd=str(root))
    proxy.deliberate_workspace_root = True
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list")
    denied = _toolbox_call(proxy, client, "tracker.get", {"project_id": other})
    assert _is_bridge_scope_error(denied), denied
    assert proxy.scoped_project_id is None
    selected = _tool_call(proxy, client, "workspace.startSession", {})
    assert not selected["result"].get("isError"), selected
    assert proxy.scoped_project_id != other
    assert _toolbox_call(proxy, client, "tracker.get", {})["result"]["isError"] is False


@pytest.mark.parametrize("failure", ["exception", "malformed"])
@pytest.mark.parametrize("global_value", [True, "true", 1])
def test_global_scope_deliberate_root_lookup_failure_cannot_downgrade(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    global_value: Any,
) -> None:
    root = tmp_path / "chosen-folder"
    root.mkdir()
    proxy, client = _scoped_bridge(mcp_client, cwd=str(root))
    proxy.deliberate_workspace_root = True
    original = proxy.request_daemon

    def request(client: Any, body: str) -> str:
        payload = json.loads(body)
        if payload.get("params", {}).get("name") == "workspace.resolve":
            if failure == "exception":
                raise RuntimeError("fixture unavailable")
            return json.dumps({"result": {"structuredContent": {"unexpected": True}}})
        return original(client, body)

    monkeypatch.setattr(proxy, "request_daemon", request)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list")
    result = _tool_call(proxy, client, "workspace.startSession", {"global_session": global_value})
    assert _is_bridge_scope_error(result), result
    assert proxy.global_session is not True
    engine = make_engine(mcp_settings.db_path)
    with Session(engine) as session:
        assert session.exec(select(AgentSession)).all() == []
        assert session.exec(select(WorkspaceBinding)).all() == []
        assert session.exec(select(Project)).all() == []
    engine.dispose()


def test_global_scope_success_after_lookup_failure_stays_global(
    mcp_client: MCPClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _create_project(mcp_client, "retry-a")
    b = _create_project(mcp_client, "retry-b")
    root = str(tmp_path / "ambient-retry")
    proxy, client = _scoped_bridge(mcp_client, cwd=root)
    _initialize(proxy, client)
    original = proxy.request_daemon

    def request(client: Any, body: str) -> str:
        if json.loads(body).get("params", {}).get("name") == "workspace.resolve":
            raise RuntimeError("initial lookup unavailable")
        return original(client, body)

    monkeypatch.setattr(proxy, "request_daemon", request)
    started = _tool_call(
        proxy, client, "workspace.startSession", {"global_session": True, "project_id": a}
    )
    assert not started["result"].get("isError"), started
    monkeypatch.setattr(proxy, "request_daemon", original)
    mcp_client.call_tool_structured("workspace.bootstrap", {"cwd": root, "project_id": b})
    _send(proxy, client, method="tools/list")
    assert proxy.global_session is True and proxy.scoped_project_id is None
    assert _is_bridge_scope_error(_toolbox_call(proxy, client, "tracker.get", {}))


def test_global_scope_manual_run_lifecycle_keeps_project_ownership(mcp_client: MCPClient) -> None:
    a = _create_project(mcp_client, "manual-a")
    b = _create_project(mcp_client, "manual-b")
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {"global_session": True})
    for terminal in ("run.finish", "run.abort"):
        started = _toolbox_call(proxy, client, "run.start", {"project_id": a, "kind": "skill-run"})
        assert not started["result"].get("isError"), started
        run_id = _operation_data(_structured(started))["run_id"]
        terminal_args = {"status": "success"} if terminal == "run.finish" else {}
        for operation, extra in (("run.heartbeat", {}), (terminal, terminal_args)):
            crossed = _toolbox_call(
                proxy, client, operation, {"project_id": b, "run_id": run_id, **extra}
            )
            assert crossed["result"]["isError"] is True, crossed
            owned = _toolbox_call(
                proxy, client, operation, {"project_id": a, "run_id": run_id, **extra}
            )
            assert not owned["result"].get("isError"), owned
        assert run_id not in proxy.tokens_by_run and run_id not in proxy.allowed_by_run


def test_global_scope_historical_run_reads_keep_project_ownership(mcp_client: MCPClient) -> None:
    a = _create_project(mcp_client, "history-a")
    b = _create_project(mcp_client, "history-b")
    running = mcp_client.call_tool_structured("run.start", {"project_id": a, "kind": "skill-run"})[
        "data"
    ]
    completed = mcp_client.call_tool_structured(
        "run.start", {"project_id": a, "kind": "skill-run"}
    )["data"]
    mcp_client.call_tool_structured(
        "run.finish", {"project_id": a, "run_id": completed["run_id"], "status": "success"}
    )
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {"global_session": True})
    for run in (running, completed):
        for name in ("run.get", "run.children"):
            run_key = "parent_run_id" if name == "run.children" else "run_id"
            result = _toolbox_call(proxy, client, name, {"project_id": a, run_key: run["run_id"]})
            assert not result["result"].get("isError"), result
            crossed = _toolbox_call(proxy, client, name, {"project_id": b, run_key: run["run_id"]})
            assert crossed["result"]["isError"] is True
    denied = _tool_call(
        proxy,
        client,
        "toolbox.call",
        {
            "tool_name": "resource.upsert",
            "run_id": completed["run_id"],
            "arguments": {
                "project_id": a,
                "plugin_slug": "core",
                "resource_key": "learning",
                "data_json": {"body": "denied"},
            },
        },
    )
    assert _is_bridge_scope_error(denied)


@pytest.mark.parametrize(
    "operation",
    [
        "workspace.resolve",
        "workspace.startSession",
        "workspace.connect",
        "workspace.bootstrap",
        "repeat",
        "repeat-false",
    ],
)
def test_global_scope_named_binding_cannot_move_or_downgrade(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    operation: str,
) -> None:
    a = mcp_client.call_tool_structured(
        "workspace.bootstrap", {"project_name": "Bound A", "workspace_alias": "alias-a"}
    )["project_id"]
    b = mcp_client.call_tool_structured(
        "workspace.bootstrap", {"project_name": "Bound B", "workspace_alias": "alias-b"}
    )["project_id"]
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {"workspace_alias": "alias-a"})
    if operation.startswith("repeat"):
        result = _tool_call(
            proxy,
            client,
            "workspace.startSession",
            {"global_session": False} if operation == "repeat-false" else {},
        )
        assert not result["result"].get("isError"), result
        assert _operation_data(_structured(result))["global_session"] is False
    else:
        call = (
            _tool_call
            if operation in {"workspace.resolve", "workspace.startSession"}
            else _toolbox_call
        )
        result = call(proxy, client, operation, {"workspace_alias": "alias-b"})
        if operation == "workspace.resolve":
            assert not result["result"].get("isError"), result
            assert _structured(result)["project_id"] == b
        else:
            assert _is_bridge_scope_error(result), result
        for selector in ({"project_name": "Unwanted C"}, {"project_slug": "unwanted-c"}):
            assert _is_bridge_scope_error(
                _toolbox_call(proxy, client, "workspace.bootstrap", selector)
            )
    assert proxy.global_session is False
    assert proxy.scoped_project_id == a
    assert _toolbox_call(proxy, client, "tracker.get", {})["result"]["isError"] is False
    engine = make_engine(mcp_settings.db_path)
    with Session(engine) as session:
        assert len(session.exec(select(Project)).all()) == 2
        assert len(session.exec(select(WorkspaceBinding)).all()) == 2
        assert all(
            row.project_id == a and row.workspace_binding_id
            for row in session.exec(select(AgentSession)).all()
        )
    engine.dispose()


def test_global_scope_interleaves_granted_actions_and_rejects_crossed_authority(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _create_project(mcp_client, "global-a")
    b = _create_project(mcp_client, "global-b")
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    assert not _tool_call(
        proxy,
        client,
        "workspace.startSession",
        {
            "global_session": True,
            "project_id": a,
        },
    )["result"].get("isError")

    def call(tool: str, arguments: dict, run_id: int | None = None) -> dict:
        result = _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": tool,
                "arguments": arguments,
                **({"run_id": run_id} if run_id else {}),
            },
        )
        assert not result["result"].get("isError"), result
        return _operation_data(_structured(result))

    contexts = {}
    for project in (a, b):
        account = mcp_client.test_client.post(
            "/api/v1/auth/accounts/mock-provider",
            json={
                "auth_method_key": "api_key",
                "display_name": f"Global mock {project}",
                "attach_project_id": project,
                "fields": {"api_key": f"secret-{project}"},
            },
            headers=mcp_client._headers(),
        )
        account.raise_for_status()
        credential = account.json()["data"]["credential_ref"]
        plan = call(
            "runPlan.create", {"project_id": project, "run_plan_json": _mock_action_plan_json()}
        )
        started = call("runPlan.start", {"project_id": project, "run_plan_id": plan["id"]})
        claimed = call(
            "runPlan.claimStep",
            {
                "project_id": project,
                "run_plan_id": plan["id"],
                "step_id": "execute-mock",
            },
            started["run_id"],
        )
        contexts[project] = (
            plan["id"],
            started["run_id"],
            credential,
            claimed["id"],
            started["run_token"],
        )
    for project in (a, b, a):
        plan_id, run_id, credential, step_id, _token = contexts[project]
        out = call(
            "action.execute",
            {
                "project_id": project,
                "action_ref": "utils.mock.echo",
                "credential_ref": credential,
                "input_json": {
                    "message": f"project-{project}",
                    "echo": {"project_id": 999, "run_id": 888},
                },
                "output_policy_json": {"mode": "inline"},
                "response_mode": "raw",
            },
            run_id,
        )
        assert out["action_call"]["project_id"] == project
        assert out["action_call"]["run_id"] == run_id
        assert out["action_call"]["run_plan_id"] == plan_id
        assert out["action_call"]["run_plan_step_id"] == step_id
        assert out["output_json"]["echo"] == {"project_id": 999, "run_id": 888}
        assert "secret-" not in json.dumps(out)
    a_plan, a_run, a_credential, _, _ = contexts[a]
    b_plan, b_run, b_credential, _, b_token = contexts[b]
    historical = mcp_client.call_tool_structured(
        "run.start", {"project_id": a, "kind": "skill-run"}
    )["data"]["run_id"]
    mcp_client.call_tool_structured(
        "run.finish", {"project_id": a, "run_id": historical, "status": "success"}
    )
    assert call("run.get", {"project_id": a, "run_id": historical}, a_run)["id"] == historical
    base = {
        "project_id": a,
        "action_ref": "utils.mock.echo",
        "credential_ref": a_credential,
        "input_json": {"message": "denied"},
    }
    for run_id, arguments in [
        (b_run, base),
        (a_run, {**base, "run_token": b_token}),
        (a_run, {**base, "credential_ref": b_credential}),
        (a_run, {key: value for key, value in base.items() if key != "project_id"}),
    ]:
        denied = _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": "action.execute",
                "run_id": run_id,
                "arguments": arguments,
            },
        )
        assert denied["result"]["isError"] is True, denied
    crossed = _tool_call(
        proxy,
        client,
        "toolbox.describe",
        {
            "project_id": a,
            "run_id": a_run,
            "run_plan_id": b_plan,
            "tool_names": ["action.execute"],
        },
    )
    assert crossed["result"]["isError"] is True
    for project, expected in ((a, 2), (b, 1)):
        audit = mcp_client.test_client.get(
            f"/api/v1/projects/{project}/action-calls", headers=mcp_client._headers()
        ).json()
        assert audit["total_estimate"] == expected
    # Fresh verified authority must disappear on a malformed/failed refresh.
    original = proxy.request_daemon
    for failure in ("exception", "malformed", "wrong-owner"):

        def fail_refresh(http_client, line, failure=failure):
            payload = json.loads(line)
            if payload.get("params", {}).get("name") == "runPlan.get":
                if failure == "exception":
                    raise RuntimeError("transport unavailable")
                if failure == "malformed":
                    return "{}"
                return json.dumps(
                    {
                        "result": {
                            "structuredContent": {
                                "id": a_plan,
                                "run_id": a_run,
                                "project_id": b,
                                "status": "started",
                                "steps": [],
                            }
                        }
                    }
                )
            return original(http_client, line)

        monkeypatch.setattr(proxy, "request_daemon", fail_refresh)
        denied = _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {
                "project_id": a,
                "run_id": a_run,
                "tool_names": ["action.execute"],
            },
        )
        assert denied["result"]["isError"] is True
        assert a_run not in proxy.tokens_by_run and a_run not in proxy.allowed_by_run
    monkeypatch.setattr(proxy, "request_daemon", original)
    call(
        "runPlan.recordStep",
        {"project_id": a, "run_plan_id": a_plan, "step_id": "execute-mock", "status": "success"},
        a_run,
    )
    after = _tool_call(
        proxy,
        client,
        "toolbox.call",
        {
            "run_id": a_run,
            "tool_name": "action.execute",
            "arguments": base,
        },
    )
    assert after["result"]["isError"] is True
    assert "action.execute" not in proxy.allowed_by_run.get(a_run, set())


def test_global_scope_direct_browser_and_describe_require_explicit_project(
    mcp_client: MCPClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = _create_project(mcp_client, "browser-a")
    b = _create_project(mcp_client, "browser-b")
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {"global_session": True, "project_id": a})
    listed = _send(proxy, client, method="tools/list")["result"]["tools"]
    profile_schema = next(tool for tool in listed if tool["name"] == "browser.profile.list")[
        "inputSchema"
    ]
    assert "project_id" in profile_schema["required"]
    catalog_before = json.dumps(proxy.tool_catalog, sort_keys=True)
    described = _tool_call(proxy, client, "toolbox.describe", {"tool_names": ["tracker.get"]})
    assert "project_id" in _structured(described)["described_tools"][0]["inputSchema"]["required"]
    assert json.dumps(proxy.tool_catalog, sort_keys=True) == catalog_before

    def no_refresh(*args: Any, **kwargs: Any) -> None:
        pytest.fail("missing project scope must be rejected before authority refresh")

    monkeypatch.setattr(proxy, "_refresh_run_context", no_refresh)
    for arguments in ({"run_id": 123}, {"run_plan_id": 123}):
        assert _is_bridge_scope_error(_tool_call(proxy, client, "toolbox.describe", arguments))
    assert _is_bridge_scope_error(_tool_call(proxy, client, "browser.profile.list", {}))
    for project in (a, b):
        created = _tool_call(
            proxy,
            client,
            "browser.profile.create",
            {"project_id": project, "profile_key": f"proof-{project}"},
        )
        assert not created["result"].get("isError"), created
    for project in (a, b, a):
        result = _tool_call(
            proxy, client, "browser.profile.list", {"project_id": project, "response_mode": "raw"}
        )
        rows = _operation_data(_structured(result))["items"]
        assert len(rows) == 1 and rows[0]["project_id"] == project
    other_proxy, other_client = _bridge(mcp_client)
    _initialize(other_proxy, other_client)
    _tool_call(
        other_proxy,
        other_client,
        "workspace.startSession",
        {"global_session": True, "project_id": b},
    )
    assert _is_bridge_scope_error(_tool_call(other_proxy, other_client, "browser.profile.list", {}))
    assert _is_bridge_scope_error(_tool_call(proxy, client, "browser.profile.list", {}))


@pytest.mark.parametrize("replacement_slug", ["saved-global", "replacement-global"])
def test_global_scope_saved_id_survives_rename_and_refuses_natural_replacement(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    replacement_slug: str,
) -> None:
    from stackos.repositories.projects import ProjectRepository

    b = _create_project(mcp_client, "other-global")
    saved = _create_project(mcp_client, "saved-global")
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(
        proxy, client, "workspace.startSession", {"global_session": True, "project_id": saved}
    )
    engine = make_engine(mcp_settings.db_path)
    with Session(engine) as session:
        ProjectRepository(session).update(saved, name="Renamed current project")
    for project in (b, saved):
        selected = _toolbox_call(proxy, client, "project.get", {"project_id": project})
        assert _operation_data(_structured(selected))["id"] == project
    fresh, fresh_client = _bridge(mcp_client)
    _initialize(fresh, fresh_client)
    selected = _tool_call(
        fresh, fresh_client, "workspace.startSession", {"global_session": True, "project_id": saved}
    )
    assert _structured(selected)["project_id"] == saved
    with Session(engine) as session:
        ProjectRepository(session).delete(saved, hard=True)
    replacement = _create_project(mcp_client, replacement_slug)
    assert replacement > saved
    for bridge, http_client in ((proxy, client), _bridge(mcp_client)):
        _initialize(bridge, http_client)
        denied = _tool_call(
            bridge,
            http_client,
            "workspace.startSession",
            {"global_session": True, "project_id": saved},
        )
        assert denied["result"]["isError"] is True
        assert bridge.scoped_project_id is None
    engine.dispose()


def test_bridge_lists_only_agent_surface(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})

    envelope = _send(proxy, client, method="tools/list", request_id="tools")
    names = [tool["name"] for tool in envelope["result"]["tools"]]

    assert names[: len(_AGENT_VISIBLE_TOOL_ORDER)] == list(_AGENT_VISIBLE_TOOL_ORDER)
    assert "toolbox.describe" in names
    assert "toolbox.call" in names
    assert names == [*_AGENT_VISIBLE_TOOL_ORDER, "toolbox.describe", "toolbox.call"]
    assert "workspace.bootstrap" not in names
    assert "action.run" not in names
    assert "toolProfile.resolve" not in names
    assert "schedule.remove" not in names
    assert "integration.set" not in names
    assert "project.list" not in names
    assert "project.create" not in names
    assert "project.get" not in names
    assert "project.setActive" not in names
    assert "browser.cli.run" not in names
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


def test_bridge_native_browser_status_exposes_handoff_without_relay(
    mcp_client: MCPClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import stackos.operations.browser as browser_ops
    from stackos.browser.runtime import (
        BrowserRuntime,
        NativeCliContext,
        NativeSessionState,
    )

    runtime = BrowserRuntime()
    states: dict[str, NativeSessionState] = {}
    native = NativeCliContext(
        executable=str(tmp_path / "browse"),
        cwd=str(tmp_path),
        env={"BROWSE_STATE_FILE": str(tmp_path / "owned.json")},
    )

    async def start(**kwargs: Any) -> NativeSessionState:
        state = NativeSessionState(
            session_ref=kwargs["session_ref"],
            profile_ref=kwargs["profile_ref"],
            status="running",
            owned=True,
            healthy=True,
            pid=123,
            repair=None,
        )
        states[state.session_ref] = state
        return state

    async def inspect(**kwargs: Any) -> NativeSessionState:
        return states[kwargs["session_ref"]]

    async def session_context(**kwargs: Any) -> NativeCliContext:
        assert kwargs["observed"] is states[kwargs["session_ref"]]
        return native

    monkeypatch.setattr(runtime, "start_session", start)
    monkeypatch.setattr(runtime, "inspect_session", inspect)
    monkeypatch.setattr(runtime, "session_context", session_context)
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: runtime)
    workspace = tmp_path / "native-workspace"
    workspace.mkdir()
    proxy, client = _scoped_bridge(mcp_client, cwd=str(workspace))
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    _operation_data(_structured(_tool_call(proxy, client, "workspace.startSession")))
    started = _operation_data(
        _structured(
            _tool_call(
                proxy,
                client,
                "browser.session.start",
                {"profile_key": "proof", "session_key": "main"},
            )
        )
    )
    session_ref = started["session_ref"]
    assert started["cli_argv"] == ["stackos.browser", "--session", session_ref]
    listed = _operation_data(_structured(_tool_call(proxy, client, "browser.session.list")))
    assert listed["items"][0]["cli_argv"] == ["stackos.browser", "--session", session_ref]
    selected = _operation_data(
        _structured(
            _tool_call(
                proxy,
                client,
                "browser.session.status",
                {"session_ref": session_ref, "response_mode": "raw"},
            )
        )
    )
    assert selected["native_cli"] == native.to_dict()
    assert selected["cli_argv"] == ["stackos.browser", "--session", session_ref]
    assert "BROWSE_NO_AUTOSTART" not in selected["native_cli"]["env"]
    assert "browser.cli.run" not in _AGENT_VISIBLE_TOOL_ORDER


@pytest.mark.parametrize("source", ["project", "user"])
def test_bound_bridge_authors_templates_without_run_grants(
    mcp_client: MCPClient,
    tmp_path: Path,
    source: str,
) -> None:
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path / "customer-workspace"))
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    bound = _structured(_tool_call(proxy, client, "workspace.startSession"))
    project_id = bound["project_id"]
    foreign_project = _create_project(mcp_client, "other-authoring-workspace")
    draft = {
        "schema_version": "stackos.workflow-template.v1",
        "key": "customer.monthly-close",
        "name": "Monthly Close",
        "version": "0.1.0",
        "inputs": [{"key": "period", "type": "string", "required": True}],
        "agent_requirements": [
            {
                "role": "reviewer",
                "agent_preset_ref": "stackos.workflow.project-memory-review",
                "applies_to_steps": ["review"],
            }
        ],
        "skill_preset_requirements": [
            {
                "skill_preset_ref": "stackos.workflow-orchestrator",
                "requirement": "required",
            }
        ],
        "steps": [{"id": "review", "title": "Review"}],
    }

    def call(operation: str, arguments: dict | None = None) -> dict:
        response = _toolbox_call(proxy, client, operation, arguments or {})
        assert response["result"]["isError"] is False, response
        return _operation_data(_structured(response))

    guide = call("workflowTemplate.authoringGuide", {"response_mode": "raw"})
    assert "author_project" in {mode["key"] for mode in guide["intent_modes"]}
    assert call("workflowTemplate.validate", {"template_json": draft})["valid"] is True
    before_runs = call("runPlan.list")
    saved = _toolbox_call(
        proxy,
        client,
        "workflowTemplate.save",
        {
            "template_json": draft,
            "source": source,
        },
    )
    assert saved["result"]["isError"] is False, saved
    assert _structured(saved)["project_id"] == project_id
    forked = _toolbox_call(
        proxy,
        client,
        "workflowTemplate.fork",
        {
            "key": draft["key"],
            "new_key": "customer.review-close",
        },
    )
    assert forked["result"]["isError"] is False, forked
    assert _structured(forked)["project_id"] == project_id
    assert draft["key"] in {
        item["key"] for item in call("workflowTemplate.list", {"response_mode": "raw"})["templates"]
    }
    assert call("workflowTemplate.describe", {"key": draft["key"]})["summary"]["source"] == source
    resolved = call("agentPreset.resolveForWorkflow", {"workflow_key": draft["key"]})
    assert len(resolved["required_agents"]) == 1
    assert len(resolved["required_skill_presets"]) == 1
    assert resolved["unresolved_requirements"] == []
    assert resolved["unresolved_skill_preset_requirements"] == []
    readiness = call("readiness.check", {"workflow_key": draft["key"]})
    assert readiness["structurally_ready"] is True
    assert readiness["required_providers_ready"] is True
    assert call("runPlan.validate", {"workflow_key": draft["key"]})["valid"] is True
    assert (
        call("runPlan.validate", {"workflow_key": draft["key"], "enforce_required_inputs": True})[
            "valid"
        ]
        is False
    )
    assert (
        call(
            "runPlan.validate",
            {
                "workflow_key": draft["key"],
                "enforce_required_inputs": True,
                "inputs_json": {"period": "2026-08"},
            },
        )["valid"]
        is True
    )
    assert call("runPlan.list") == before_runs
    for operation, arguments in (
        ("workflowTemplate.save", {"template_json": draft, "source": source}),
        ("workflowTemplate.fork", {"key": draft["key"], "new_key": "customer.foreign-copy"}),
    ):
        cross_project = _toolbox_call(
            proxy,
            client,
            operation,
            {
                **arguments,
                "project_id": foreign_project,
            },
        )
        assert _is_bridge_scope_error(cross_project)
    absent = mcp_client.call_tool_error(
        "workflowTemplate.describe",
        {
            "project_id": foreign_project,
            "key": draft["key"],
        },
    )
    assert absent["code"] == -32004

    # A separate execution request can use the saved template through normal grants.
    plan = call(
        "runPlan.create",
        {
            "workflow_key": draft["key"],
            "inputs_json": {"period": "2026-08"},
        },
    )
    assert plan["status"] == "draft"
    started = call("runPlan.start", {"run_plan_id": plan["id"]})
    for operation, extra in (
        ("runPlan.claimStep", {}),
        ("runPlan.recordStep", {"status": "success", "result_json": {"summary": "Reviewed"}}),
    ):
        response = _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "run_id": started["run_id"],
                "tool_name": operation,
                "arguments": {"run_plan_id": plan["id"], "step_id": "review", **extra},
            },
        )
        assert response["result"]["isError"] is False, response
    assert call("runPlan.get", {"run_plan_id": plan["id"]})["status"] == "completed"


def test_bridge_supports_modern_discovery_catalog_and_local_tool_results(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)

    discovery = _send_modern(
        proxy,
        client,
        method="server/discover",
        request_id="modern-discover",
    )
    assert discovery["result"]["supportedVersions"] == [MODERN_PROTOCOL_VERSION]
    assert mcp_client._initialized is False

    catalog = _send_modern(proxy, client, method="tools/list", request_id="modern-tools")
    names = [tool["name"] for tool in catalog["result"]["tools"]]
    assert names == [*_AGENT_VISIBLE_TOOL_ORDER, "toolbox.describe", "toolbox.call"]
    assert catalog["result"]["resultType"] == "complete"

    described = _send_modern(
        proxy,
        client,
        method="tools/call",
        params={
            "name": "toolbox.describe",
            "arguments": {"tool_names": ["workspace.resolve"]},
        },
        request_id="modern-describe",
    )
    assert described["result"]["isError"] is False
    assert described["result"]["resultType"] == "complete"
    assert described["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == (
        "stackos-agent-bridge"
    )


def test_bridge_discovers_hidden_operations_with_compact_grouped_list(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})

    described = _structured(
        _tool_call(proxy, client, "toolbox.describe", {}, request_id="describe")
    )
    assert described["described_tools"] == []
    assert described["discovery"]["next_calls"][0]["arguments"] == {
        "tool_name": "operation.list",
        "arguments": {
            "surface": "mcp",
            "mode": "grouped",
            "response_mode": "compact",
        },
    }
    assert "inputSchema" not in json.dumps(described)

    listed = _structured(
        _toolbox_call(
            proxy,
            client,
            "operation.list",
            {"surface": "mcp", "mode": "grouped", "response_mode": "compact"},
            request_id="operation-list",
        )
    )
    data = _operation_data(listed)
    groups = data["groups"]
    names = {
        operation_name for group in groups for operation_name in group.get("operation_names", [])
    }

    assert "operation.list" in names
    assert "tracker.status" in names
    assert "runPlan.create" in names
    assert "action.execute" in names
    assert all(group["count"] == len(group["operation_names"]) for group in groups)
    assert "inputSchema" not in json.dumps(data)


def test_bridge_describes_typed_telegram_retention_selection(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    described = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {
                "tool_names": ["communicationProfile.upsert"],
                "include_schemas": True,
            },
            request_id="telegram-retention-schema",
        )
    )
    tool = described["described_tools"][0]
    assert tool["name"] == "communicationProfile.upsert"
    selector = tool["inputSchema"]["properties"]["visibility_policy"]
    assert "updateNewMessage" in selector["properties"]["allowed_update_types"]["items"]["enum"]
    assert (
        selector["properties"]["allowed_surface_refs"]["items"]["anyOf"][0]["pattern"]
        == r"^telegram-chat:-?[1-9][0-9]*$"
    )
    assert selector["x-telegram-retention"]["account_scoped_surface_mode"] == "all"


def test_bridge_refreshes_existing_tool_schema_for_exact_describe(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="initial-catalog")

    # Model an app replacement in a long-lived bridge: the daemon has the new
    # schema, but the bridge still holds the old schema under the same tool name.
    stale_schema = proxy.tool_catalog["communicationProfile.upsert"]["inputSchema"]
    stale_schema["properties"]["visibility_policy"] = {
        "additionalProperties": True,
        "type": "object",
    }

    described = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {
                "tool_names": ["communicationProfile.upsert"],
                "include_schemas": True,
            },
            request_id="refreshed-telegram-retention-schema",
        )
    )
    selector = described["described_tools"][0]["inputSchema"]["properties"]["visibility_policy"]
    assert "updateNewMessage" in selector["properties"]["allowed_update_types"]["items"]["enum"]
    assert selector["x-telegram-retention"]["account_scoped_surface_mode"] == "all"


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
        "/api/v1/auth/accounts/mock-provider",
        json={
            "auth_method_key": "api_key",
            "display_name": "Mock Provider - Primary",
            "attach_project_id": project_id,
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
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="tools")

    compact = _structured(
        _toolbox_call(
            proxy,
            client,
            "connection.list",
            {},
            request_id="auth-compact",
        )
    )
    standard = _structured(
        _toolbox_call(
            proxy,
            client,
            "connection.list",
            {"provider_key": "mock-provider", "response_mode": "standard"},
            request_id="auth-standard",
        )
    )
    missing_connection = _structured(
        _toolbox_call(
            proxy,
            client,
            "connection.list",
            {"provider_key": "openai-images"},
            request_id="auth-missing-provider",
        )
    )
    integration_description = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {
                "tool_names": ["integration.list"],
                "include_schemas": True,
            },
            request_id="integration-description",
        )
    )
    integrations = _structured(
        _toolbox_call(
            proxy,
            client,
            "integration.list",
            {},
            request_id="integrations-compact",
        )
    )
    integration_catalog = _structured(
        _toolbox_call(
            proxy,
            client,
            "integration.list",
            {"include_unavailable": True},
            request_id="integrations-catalog",
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
    compact_data = _operation_data(compact)
    standard_data = _operation_data(standard)
    missing_connection_data = _operation_data(missing_connection)
    integration_data = _operation_data(integrations)
    integration_catalog_data = _operation_data(integration_catalog)
    resolved_compact_data = _operation_data(resolved_compact)
    resolved_standard_data = _operation_data(resolved_standard)

    assert compact["project_id"] == project_id
    assert compact_data["accounts"][0]["credential_ref"].startswith("cred_")
    assert compact_data["accounts"][0]["provider_key"] == "mock-provider"
    assert compact_data["accounts"][0]["status"] == "connected"
    assert missing_connection_data["accounts"] == []
    assert [provider["key"] for provider in missing_connection_data["providers"]] == [
        "openai-images"
    ]
    assert missing_connection_data["providers"][0]["status"] == "missing"
    assert missing_connection_data["providers"][0]["setup_required"] is True
    integration_schema = integration_description["described_tools"][0]["inputSchema"]
    include_unavailable_schema = integration_schema["properties"]["include_unavailable"]
    assert include_unavailable_schema["default"] is False
    assert "setup/catalog" in include_unavailable_schema["description"]
    ready_provider_keys = {item["provider_key"] for item in integration_data["items"]}
    catalog_provider_keys = {item["provider_key"] for item in integration_catalog_data["items"]}
    assert "jina" in ready_provider_keys
    assert "openai-images" not in ready_provider_keys
    assert "openai-images" in catalog_provider_keys
    assert [provider["key"] for provider in compact_data["providers"]] == ["mock-provider"]
    assert "auth_methods" not in compact_data["providers"][0]
    assert "auth_methods" in standard_data["providers"][0]
    assert len(json.dumps(compact)) < len(json.dumps(standard))
    assert len(json.dumps(integrations)) < len(json.dumps(integration_catalog))
    assert resolved_compact["project_id"] == project_id
    assert resolved_compact_data["ready"] is True
    assert resolved_compact_data["credential"]["credential_ref"].startswith("cred_")
    assert "scopes" in resolved_compact_data["credential"]
    assert "scopes" in resolved_standard_data["credential"]
    assert "mock-secret" not in json.dumps(compact)
    assert "mock-secret" not in json.dumps(resolved_compact)


@pytest.mark.parametrize("account_status", ["pending", "repair-required"])
def test_bridge_integration_list_marks_unready_attached_account_for_repair(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    account_status: str,
) -> None:
    project_id = _create_project(mcp_client, f"bridge-repair-{account_status}")
    workspace_ref = f"path:bridge-repair-{account_status}"
    workspace_root = f"/tmp/bridge-repair-{account_status}"
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": workspace_ref,
            "last_known_root": workspace_root,
        },
    )
    response = mcp_client.test_client.post(
        "/api/v1/auth/accounts/mock-provider",
        json={
            "auth_method_key": "api_key",
            "display_name": f"Mock Provider - {account_status}",
            "attach_project_id": project_id,
            "fields": {"api_key": "mock-secret"},
        },
        headers=mcp_client._headers(),
    )
    response.raise_for_status()
    credential_ref = response.json()["data"]["credential_ref"]

    engine = make_engine(mcp_settings.db_path)
    try:
        with Session(engine) as session:
            credential = session.exec(
                select(Credential).where(Credential.credential_ref == credential_ref)
            ).one()
            credential.status = account_status
            session.add(credential)
            session.commit()
    finally:
        engine.dispose()

    proxy, client = _scoped_bridge(
        mcp_client,
        cwd=workspace_root,
        repo_fingerprint=workspace_ref,
    )
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    listed = _structured(
        _toolbox_call(
            proxy,
            client,
            "integration.list",
            {
                "provider_key": "mock-provider",
                "include_actions": True,
            },
            request_id=f"integration-repair-{account_status}",
        )
    )
    data = _operation_data(listed)

    assert data["count"] == 1
    assert data["ready_count"] == 0
    assert data["items"][0]["state"] == "repair_required"
    assert data["items"][0]["connected"] is False
    assert data["items"][0]["connected_credential_count"] == 0
    assert data["items"][0]["next_action"]["tool"] == "account.test"
    assert data["items"][0]["next_action"]["arguments"]["credential_ref"] == credential_ref


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
    other_grant_plan = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": other_project_id,
            "run_plan_json": {
                "schema_version": "stackos.run-plan.v1",
                "key": "bridge.other.granted.run",
                "title": "Other project granted plan",
                "grants": {
                    "mcp_tool_grants": [
                        {
                            "step_id": "other-write",
                            "tool": "resource.upsert",
                            "plugin_slug": "core",
                            "resource_key": "learning",
                        }
                    ]
                },
                "steps": [{"id": "other-write", "title": "Other write"}],
            },
        },
    )
    other_grant_plan_id = other_grant_plan["data"]["id"]
    other_grant_started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": other_project_id, "run_plan_id": other_grant_plan_id},
    )
    other_granted_run_id = other_grant_started["data"]["run_id"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "project_id": other_project_id,
            "run_plan_id": other_grant_plan_id,
            "step_id": "other-write",
            "run_token": other_grant_started["data"]["run_token"],
        },
    )
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
    _tool_call(proxy, client, "workspace.startSession", {})

    _send(proxy, client, method="tools/list", request_id="tools")
    described = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {
                "tool_names": [
                    "connection.list",
                    "toolProfile.resolve",
                    "workspace.connect",
                ]
            },
            request_id="describe-schemas",
        )
    )
    by_tool = {tool["name"]: tool for tool in described["described_tools"]}
    auth_tool = by_tool["connection.list"]
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
            "connection.list",
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
        "connection.list",
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
    cross_grant_describe = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {"run_id": other_granted_run_id, "tool_names": ["resource.upsert"]},
            request_id="describe-cross-run-grant",
        )
    )
    resolved_tool_data = _operation_data(resolved_tool)

    assert "project_id" not in auth_required
    assert "project_id" not in resolver_required
    assert "project_id" not in workspace_connect_required
    assert "repo_fingerprint" not in workspace_connect_required
    assert resolved["project_id"] == project_id
    assert status["project_id"] == project_id
    assert resolved_tool["project_id"] == project_id
    assert resolved_tool_data["provider"]["provider_key"] == "mock-provider"
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
    assert cross_grant_describe["code"] == -32007
    assert cross_grant_describe["data"]["reason"] == "run_context_unavailable"


def test_bridge_unbound_workspace_autobootstraps_and_unlocks_project_scoped_tools(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _scoped_bridge(
        mcp_client,
        cwd="/tmp/bridge-auto-start",
        repo_fingerprint="path:bridge-auto-start",
    )
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
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
    resolved_data = _operation_data(resolved)
    bindings_data = _operation_data(bindings)
    assert project_scoped_after_tool_list["result"]["isError"] is False
    assert resolved_data["setup_state"]["workspace_bound"] is True
    assert resolved_data["needs_connect"] is False
    assert resolved["project_id"] is not None
    assert tracker_after_tool_list["project_id"] == resolved["project_id"]
    assert discovery["result"]["isError"] is False
    assert refreshed["project_id"] == resolved["project_id"]
    assert project_scoped_after_refresh["result"]["isError"] is False
    assert bindings_data["items"][0]["project_id"] == resolved["project_id"]


def test_bridge_app_bundle_cwd_does_not_autobootstrap_workspace(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _scoped_bridge(
        mcp_client,
        cwd="/Applications/StackOS.app/Contents/Resources",
        repo_fingerprint="path:app-bundle-runtime",
    )
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="tools")

    project_scoped = _toolbox_call(
        proxy,
        client,
        "workflowTemplate.list",
        {},
        request_id="workflow-template-list-app-bundle",
    )
    resolved = _structured(
        _tool_call(
            proxy,
            client,
            "workspace.resolve",
            {},
            request_id="resolve-app-bundle",
        )
    )
    bindings = mcp_client.call_tool_structured("workspace.listBindings", {})

    assert _is_bridge_scope_error(project_scoped)
    assert resolved["project_id"] is None
    assert resolved["data"]["project_id"] is None
    assert resolved["data"].get("workspace_binding_id") is None
    assert resolved["data"]["needs_connect"] is True
    assert bindings["items"] == []


def test_bridge_no_hint_start_session_lists_named_workspace_candidates(
    mcp_client: MCPClient,
) -> None:
    bootstrapped = mcp_client.call_tool_structured(
        "workspace.bootstrap",
        {"project_name": "Flowmonkey", "workspace_alias": "flowmonkey"},
    )
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list", request_id="tools")

    started = _structured(
        _tool_call(
            proxy,
            client,
            "workspace.startSession",
            {},
            request_id="workspace-start-no-hints",
        )
    )
    data = _operation_data(started)

    assert started["project_id"] is None
    assert data["setup_state"]["workspace_bound"] is False
    assert data["needs_connect"] is True
    assert proxy.scoped_project_id is None
    assert data["next_step"]["status"] == "project_selection_required"
    assert data["candidate_workspaces"] == [
        {
            "workspace_alias": "flowmonkey",
            "binding_id": bootstrapped["data"]["binding"]["id"],
            "project_id": bootstrapped["project_id"],
            "project_slug": "flowmonkey",
            "project_name": "Flowmonkey",
            "ui_paths": {
                "setup": f"/projects/{bootstrapped['project_id']}/setup",
                "connections": f"/projects/{bootstrapped['project_id']}/connections",
                "tasks": f"/projects/{bootstrapped['project_id']}/tasks",
                "workflow_templates": f"/projects/{bootstrapped['project_id']}/workflow-templates",
            },
            "ui_urls": {
                "setup": f"http://127.0.0.1:5180/projects/{bootstrapped['project_id']}/setup",
                "connections": (
                    f"http://127.0.0.1:5180/projects/{bootstrapped['project_id']}/connections"
                ),
                "tasks": f"http://127.0.0.1:5180/projects/{bootstrapped['project_id']}/tasks",
                "workflow_templates": (
                    "http://127.0.0.1:5180/projects/"
                    f"{bootstrapped['project_id']}/workflow-templates"
                ),
            },
        }
    ]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "direct"),
    [
        ("workspace.resolve", {"cwd": "/tmp/bridge-synthetic-anchor"}, True),
        ("workspace.startSession", {"cwd": "/tmp/bridge-synthetic-anchor"}, True),
        (
            "workspace.bootstrap",
            {
                "cwd": "/tmp/bridge-synthetic-anchor",
                "repo_fingerprint": "path:bridge-synthetic-anchor",
                "project_id": "<project_id>",
            },
            False,
        ),
        (
            "workspace.connect",
            {
                "last_known_root": "/tmp/bridge-synthetic-anchor",
                "project_id": "<project_id>",
            },
            False,
        ),
    ],
)
def test_bridge_no_hint_workspace_tools_reject_synthetic_anchors(
    mcp_client: MCPClient,
    tool_name: str,
    arguments: dict[str, Any],
    direct: bool,
) -> None:
    project_id = _create_project(mcp_client, "bridge-toolbox-synthetic-anchor")
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="tools")
    resolved_arguments = {
        key: project_id if value == "<project_id>" else value for key, value in arguments.items()
    }

    rejected = (
        _tool_call(
            proxy,
            client,
            tool_name,
            resolved_arguments,
            request_id=f"{tool_name}-synthetic-anchor",
        )
        if direct
        else _tool_call(
            proxy,
            client,
            "toolbox.call",
            {"tool_name": tool_name, "arguments": resolved_arguments},
            request_id=f"toolbox-{tool_name}-synthetic-anchor",
        )
    )
    envelope = rejected["result"]
    data = envelope["structuredContent"]["data"]
    synthetic_anchor_fields = {
        "cwd",
        "git_remote_url",
        "last_known_root",
        "normalized_repo_name",
        "repo_fingerprint",
    }

    assert envelope["isError"] is True
    assert envelope["structuredContent"]["code"] == -32007
    assert data["reason"] == "workspace_anchor_missing_from_host"
    assert data["supplied_fields"] == sorted(
        key for key in resolved_arguments if key in synthetic_anchor_fields
    )
    assert proxy.scoped_project_id is None


def test_bridge_toolbox_bootstrap_reuses_bound_workspace(
    mcp_client: MCPClient,
) -> None:
    project_id = _create_project(mcp_client, "bridge-toolbox-connect-later")
    proxy, client = _scoped_bridge(
        mcp_client,
        cwd="/tmp/bridge-toolbox-connect-later",
        repo_fingerprint="path:bridge-toolbox-connect-later",
    )
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="tools")

    connected = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.call",
            {
                "tool_name": "workspace.bootstrap",
                "arguments": {},
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
    assert connected["data"]["binding_was_created"] is False
    assert proxy.scoped_project_id == project_id
    assert project_scoped_after_bootstrap["result"]["isError"] is False


def test_bridge_describes_setup_tools_and_treats_removed_vendor_tools_as_unknown(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="tools")

    envelope = _tool_call(
        proxy,
        client,
        "toolbox.describe",
        {
            "tool_names": [
                "project.delete",
                "schedule.remove",
                "account.test",
                "account.start",
                "connection.attach",
                "connection.detach",
                "dataforseo.serp",
            ]
        },
        request_id="describe",
    )
    payload = _structured(envelope)

    assert [tool["name"] for tool in payload["described_tools"]] == [
        "schedule.remove",
        "account.test",
    ]
    assert payload["denied_tool_names"] == [
        "project.delete",
        "account.start",
        "connection.attach",
        "connection.detach",
    ]
    assert payload["unknown_tool_names"] == ["dataforseo.serp"]
    assert "admin_gated_tool_names" not in payload


def test_bridge_toolbox_operates_setup_actions(
    mcp_client: MCPClient,
    httpx_mock: HTTPXMock,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="tools")

    project_id = _create_project(mcp_client, "bridge-agent-path")

    secret = _structured(
        _toolbox_call(
            proxy,
            client,
            "secret.set",
            {
                "project_id": project_id,
                "value": "bridge-payload-transit-canary",
            },
            request_id="secret-set",
        )
    )
    assert secret["data"]["secret_ref"].startswith("secret_")
    assert "bridge-payload-transit-canary" not in json.dumps(secret)

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
                "arguments": {"project_id": project_id, "job_id": schedule["data"]["id"]},
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
        "/api/v1/auth/accounts/firecrawl",
        json={
            "auth_method_key": "api_key",
            "display_name": "Firecrawl - Default",
            "attach_project_id": project_id,
            "fields": {"api_key": "fc-key"},
        },
        headers=mcp_client._headers(),
    )
    credential_resp.raise_for_status()
    status = _structured(
        _toolbox_call(
            proxy,
            client,
            "connection.list",
            {"project_id": project_id, "provider_key": "firecrawl"},
            request_id="auth-status",
        )
    )
    credential_ref = _operation_data(status)["accounts"][0]["credential_ref"]
    tested = _structured(
        _toolbox_call(
            proxy,
            client,
            "account.test",
            {"project_id": project_id, "credential_ref": credential_ref},
            request_id="auth-test",
        )
    )
    assert tested["data"]["ok"] is True
    assert tested["data"]["provider_key"] == "firecrawl"
    assert tested["project_id"] == project_id


def test_bridge_allows_started_run_plan_controller_tools(mcp_client: MCPClient) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
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
            {"project_id": project_id, "run_id": run_id, "tool_names": ["runPlan.claimStep"]},
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
                "arguments": {
                    "project_id": project_id,
                    "run_plan_id": run_plan_id,
                    "step_id": "review",
                },
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
                    "project_id": project_id,
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
    assert cross_plan["result"]["structuredContent"]["code"] == -32007
    assert claimed["data"]["status"] == "running"
    assert completed["data"]["status"] == "completed"


def test_bridge_resumes_started_run_plan_controller_tools_in_new_session(
    mcp_client: MCPClient,
) -> None:
    starter_proxy, starter_client = _bridge(mcp_client)
    _initialize(starter_proxy, starter_client)
    _tool_call(starter_proxy, starter_client, "workspace.startSession", {})
    _send(starter_proxy, starter_client, method="tools/list", request_id="starter-tools")

    project_id = _create_project(mcp_client, "bridge-run-plan-resume")
    created_plan = _structured(
        _toolbox_call(
            starter_proxy,
            starter_client,
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "bridge.resume.run",
                    "title": "Bridge resume",
                    "steps": [{"id": "review", "title": "Review"}],
                },
            },
            request_id="run-plan-create",
        )
    )
    run_plan_id = created_plan["data"]["id"]
    started = _structured(
        _toolbox_call(
            starter_proxy,
            starter_client,
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": run_plan_id},
            request_id="run-plan-start",
        )
    )
    run_id = started["data"]["run_id"]

    resume_proxy, resume_client = _bridge(mcp_client)
    _initialize(resume_proxy, resume_client)
    _tool_call(resume_proxy, resume_client, "workspace.startSession", {})
    _send(resume_proxy, resume_client, method="tools/list", request_id="resume-tools")
    described = _structured(
        _tool_call(
            resume_proxy,
            resume_client,
            "toolbox.describe",
            {"project_id": project_id, "run_id": run_id, "tool_names": ["runPlan.claimStep"]},
            request_id="describe-resume-run-plan",
        )
    )
    claimed = _structured(
        _tool_call(
            resume_proxy,
            resume_client,
            "toolbox.call",
            {
                "run_id": run_id,
                "tool_name": "runPlan.claimStep",
                "arguments": {
                    "project_id": project_id,
                    "run_plan_id": run_plan_id,
                    "step_id": "review",
                },
            },
            request_id="claim-resume-run-plan",
        )
    )

    assert [tool["name"] for tool in described["described_tools"]] == ["runPlan.claimStep"]
    assert claimed["data"]["status"] == "running"


def test_bridge_resumes_started_run_plan_controller_tools_from_run_plan_id(
    mcp_client: MCPClient,
) -> None:
    starter_proxy, starter_client = _bridge(mcp_client)
    _initialize(starter_proxy, starter_client)
    _tool_call(starter_proxy, starter_client, "workspace.startSession", {})
    _send(starter_proxy, starter_client, method="tools/list", request_id="starter-tools")

    project_id = _create_project(mcp_client, "bridge-run-plan-resume-plan-id")
    created_plan = _structured(
        _toolbox_call(
            starter_proxy,
            starter_client,
            "runPlan.create",
            {
                "project_id": project_id,
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "bridge.resume.plan.id.run",
                    "title": "Bridge resume by plan id",
                    "steps": [{"id": "review", "title": "Review"}],
                },
            },
            request_id="run-plan-create",
        )
    )
    run_plan_id = created_plan["data"]["id"]
    _structured(
        _toolbox_call(
            starter_proxy,
            starter_client,
            "runPlan.start",
            {"project_id": project_id, "run_plan_id": run_plan_id},
            request_id="run-plan-start",
        )
    )

    resume_proxy, resume_client = _bridge(mcp_client)
    _initialize(resume_proxy, resume_client)
    _tool_call(resume_proxy, resume_client, "workspace.startSession", {})
    _send(resume_proxy, resume_client, method="tools/list", request_id="resume-tools")
    described = _structured(
        _tool_call(
            resume_proxy,
            resume_client,
            "toolbox.describe",
            {
                "project_id": project_id,
                "run_plan_id": run_plan_id,
                "tool_names": ["runPlan.claimStep"],
            },
            request_id="describe-resume-plan-id",
        )
    )
    claimed = _structured(
        _tool_call(
            resume_proxy,
            resume_client,
            "toolbox.call",
            {
                "tool_name": "runPlan.claimStep",
                "arguments": {
                    "project_id": project_id,
                    "run_plan_id": run_plan_id,
                    "step_id": "review",
                },
            },
            request_id="claim-resume-plan-id",
        )
    )

    assert [tool["name"] for tool in described["described_tools"]] == ["runPlan.claimStep"]
    assert claimed["data"]["status"] == "running"


def test_bridge_exposes_run_plan_granted_generic_tool_after_claim(
    mcp_client: MCPClient,
) -> None:
    proxy, client = _bridge(mcp_client)
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {})
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
            {"project_id": project_id, "run_id": run_id, "tool_names": ["resource.upsert"]},
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
                "arguments": {
                    "project_id": project_id,
                    "run_plan_id": run_plan_id,
                    "step_id": "write",
                },
            },
            request_id="claim-run-plan",
        )
    )
    after_claim = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {"project_id": project_id, "run_id": run_id, "tool_names": ["resource.upsert"]},
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
                    "response_mode": "raw",
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
    _tool_call(proxy, client, "workspace.startSession", {})
    _send(proxy, client, method="tools/list", request_id="tools")

    project_id = _create_project(mcp_client, "bridge-action-grant")
    cred_resp = mcp_client.test_client.post(
        "/api/v1/auth/accounts/openai-images",
        json={
            "auth_method_key": "api_key",
            "display_name": "OpenAI Images - Default",
            "attach_project_id": project_id,
            "fields": {"api_key": "sk-openai"},
        },
        headers=mcp_client._headers(),
    )
    cred_resp.raise_for_status()
    auth_status = _structured(
        _toolbox_call(
            proxy,
            client,
            "connection.list",
            {"project_id": project_id, "provider_key": "openai-images"},
            request_id="auth-status",
        )
    )
    credential_ref = _operation_data(auth_status)["accounts"][0]["credential_ref"]
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
                "arguments": {
                    "project_id": project_id,
                    "run_plan_id": run_plan_id,
                    "step_id": "generate",
                },
            },
            request_id="claim-run-plan",
        )
    )
    described = _structured(
        _tool_call(
            proxy,
            client,
            "toolbox.describe",
            {"project_id": project_id, "run_id": run_id, "tool_names": ["action.execute"]},
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
    _tool_call(proxy, client, "workspace.startSession", {})
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
