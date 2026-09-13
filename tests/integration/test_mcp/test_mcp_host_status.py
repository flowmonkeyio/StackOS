"""The console can read safe host status; scoped agents cannot inspect other hosts."""

from threading import get_ident

import pytest

from stackos.host_mcp import service
from stackos.host_mcp.result import HostMcpResult
from stackos.operations import build_operation_registry

from .conftest import MCPClient
from .test_mcp_bridge_agent_path import (
    _initialize,
    _operation_data,
    _scoped_bridge,
    _send,
    _toolbox_call,
)


@pytest.fixture
def inspected(monkeypatch: pytest.MonkeyPatch):
    threads: list[int] = []

    def inspect():
        threads.append(get_ident())
        result = service.normalize_result(
            HostMcpResult(
                host_key="codex",
                surface="cli",
                status="registered_current",
                message="Healthy api_key=synthetic-secret",
                ok=True,
                available=True,
                command=["stackos", "raw-config-fragment"],
                config_path="/private/host-config.json",
                target={"profile": "private-profile"},
                targets=[{"profile": "private-profile"}],
                selected=True,
                managed=True,
            ),
            service.HostMcpPolicy("codex", "ChatGPT / Codex", "automatic"),
        )
        return service.HostMcpAggregate(ok=True, results=[result])

    monkeypatch.setattr(service, "inspect_all", inspect)
    return threads


def test_host_status_has_one_read_only_local_admin_contract() -> None:
    spec = build_operation_registry().get("hostMcp.status")
    assert spec.mutating is False
    assert spec.grant_policy == "local-admin-read"
    assert all(spec.surfaces.is_enabled(surface) for surface in ("mcp", "rest", "cli"))
    assert spec.response_policy.default_mode == "raw"
    assert spec.response_policy.allowed_modes == ("raw",)


def test_console_reads_only_normalized_host_status(mcp_client: MCPClient, inspected) -> None:
    client = mcp_client.test_client
    response = client.post(
        "/api/v1/operations/hostMcp.status/call",
        json={"arguments": {}},
        headers={"Authorization": f"Bearer {client.app.state.ui_token}"},
    )
    assert response.status_code == 200, response.text
    result = _operation_data(response.json())
    assert result["ok"] is True
    row = result["items"][0]
    assert row["host_key"] == "codex"
    assert row["display_name"] == "ChatGPT / Codex"
    assert row["connection_state"] == "connected"
    assert row["status_label"] == "Connected"
    assert row["selected"] is True
    assert row["managed"] is True
    assert not {"command", "config_path", "target", "targets"} & row.keys()
    for secret in (
        "synthetic-secret",
        "raw-config-fragment",
        "private-profile",
        "/private/host-config",
    ):
        assert secret not in response.text
    assert len(inspected) == 1


@pytest.mark.parametrize(
    "arguments", [{"home": "/private/home"}, {"profile": "other"}, {"project_id": 1}]
)
def test_host_status_rejects_inspection_targets(
    mcp_client: MCPClient, inspected, arguments
) -> None:
    response = mcp_client.test_client.post(
        "/api/v1/operations/hostMcp.status/call",
        json={"arguments": arguments},
        headers=mcp_client._headers(),
    )
    assert response.status_code == 422, response.text
    assert inspected == []


def test_scoped_agent_cannot_inspect_host_status(
    mcp_client: MCPClient, seeded_project: dict, inspected
) -> None:
    project_id = seeded_project["data"]["id"]
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "path:host-status",
            "last_known_root": "/tmp/host-status",
        },
    )
    proxy, client = _scoped_bridge(
        mcp_client, cwd="/tmp/host-status", repo_fingerprint="path:host-status"
    )
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list")
    result = _toolbox_call(proxy, client, "hostMcp.status", {})
    assert result["result"]["isError"] is True, result
    assert "local-admin" in str(result), result
    assert inspected == []
