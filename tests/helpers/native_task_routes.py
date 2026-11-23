"""Owned native configuration for retained settings/lifecycle consumer tests."""

import json
from dataclasses import replace

import pytest
import tomlkit
import yaml

from stackos.host_mcp import bridge, service


@pytest.fixture
def controlled_profile_host(monkeypatch):
    """Exercise shared selectors with a controlled, managed-capable native edge.

    Keep Grok's supported managed lifetime and real workspace route reader, but
    declare provider/profile selectors for these propagation tests only. This is
    not native Grok provider/profile certification or permission to admit Hermes.
    """
    original = service._definition_by_key
    definition = original("grok")
    assert definition.policy.agent_execution.managed_execution
    controlled = replace(
        definition,
        policy=replace(
            definition.policy,
            agent_execution=replace(
                definition.policy.agent_execution, provider_selection=True, profile_selection=True
            ),
        ),
    )
    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: controlled if key == "grok" else original(key)
    )
    assert service.managed_execution_error("hermes") is not None
    return "grok"


def write_native_route(home, root, host, profile=None):
    command = bridge.resolve_bridge_command(runtime=host, workspace_root=root)
    entry = {"command": command[0], "args": command[1:]}
    if host == "claude-code":
        path = home / ".claude.json"
        data = json.dumps({"mcpServers": {"stackos": entry}})
    elif host == "antigravity":
        path = home / ".gemini/config/mcp_config.json"
        data = json.dumps({"mcpServers": {"stackos": entry}})
    elif host == "grok":
        path = home / ".grok/config.toml"
        data = tomlkit.dumps({"mcp_servers": {"stackos": entry}})
    else:
        base = home / ".hermes"
        if profile and profile != "default":
            base = base / "profiles" / profile
        path = base / "config.yaml"
        data = yaml.safe_dump({"mcp_servers": {"stackos": entry}})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


@pytest.fixture(autouse=True)
def native_route_defaults(monkeypatch, tmp_path):
    """Supply real native defaults to tests of settings rather than routing errors."""
    from pathlib import Path

    from stackos import task_delivery
    from stackos.repositories.tracker import TrackerRepository
    from stackos.repositories.workspaces import WorkspaceRepository

    original = task_delivery.resolve_delivery_settings

    def resolve(session, **kwargs):
        if not kwargs.get("mcp_config_ref") and kwargs.get("_verify_native_routes", True):
            project_id = kwargs["project_id"]
            root = Path(kwargs["project_dir"]).resolve()
            defaults = TrackerRepository(session).delivery_defaults(project_id=project_id)
            home = tmp_path.parent / f"{tmp_path.name}-native-home"
            roles = kwargs.get("role_keys") or {
                "delivery_agent",
                "review_agent",
                *(kwargs.get("agents") or {}),
                *(defaults.get("agents") or {}),
            }
            for role in roles:
                selected = {
                    **task_delivery._delivery_role_defaults(defaults, role),
                    **(kwargs.get("agents") or {}).get(role, {}),
                    **(kwargs.get(role) or {}),
                }
                host = (
                    selected.get("host_key") or kwargs.get("host_key") or defaults.get("host_key")
                )
                if host not in {"claude-code", "antigravity", "grok", "hermes"}:
                    continue
                monkeypatch.setattr(service, "default_home", lambda: home)
                monkeypatch.setattr(bridge, "default_home", lambda: home)
                monkeypatch.delenv("GROK_HOME", raising=False)
                monkeypatch.delenv("HERMES_HOME", raising=False)
                write_native_route(home, root, host, selected.get("profile"))
                bindings = WorkspaceRepository(session).list_bindings()
                if not any(
                    b.last_known_root and Path(b.last_known_root).resolve() == root
                    for b in bindings
                ):
                    WorkspaceRepository(session).connect(
                        project_id=project_id,
                        last_known_root=str(root),
                        repo_fingerprint=f"fixture:{root}",
                    )
        return original(session, **kwargs)

    monkeypatch.setattr(task_delivery, "resolve_delivery_settings", resolve)
