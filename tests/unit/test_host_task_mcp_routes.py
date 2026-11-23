"""Task routing reads native owners, preserves config, and fails before spawn."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import tomlkit

from stackos.host_mcp import bridge, service
from stackos.host_mcp.adapters import antigravity_execution, claude_code, grok
from stackos.host_mcp.result import AgentRunResult

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}}


@pytest.mark.parametrize("host", ["claude-code", "antigravity", "grok"])
def test_default_registration_route_gets_an_invocation_local_root(tmp_path, monkeypatch, host):
    from dataclasses import replace

    tree, path, content, encode, _ref = route_fixture(tmp_path, monkeypatch, host)
    monkeypatch.delenv("STACKOS_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("STACKOS_MCP_BRIDGE_COMMAND", raising=False)
    command = bridge.resolve_bridge_command(runtime=host)
    key = "mcpServers" if host in {"claude-code", "antigravity"} else "mcp_servers"
    content[key]["stackos"] = {"command": command[0], "args": command[1:]}
    path.write_text(encode(content))
    before = path.read_bytes()
    route = service.task_mcp_route(host, tree, None)
    definition = service._definition_by_key(host)
    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, native_models=None)
    )
    monkeypatch.setenv("STACKOS_WORKSPACE_ROOT", str(tmp_path / "foreign-ambient"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "foreign-claude"))
    monkeypatch.setenv("RETAIN_NATIVE_ENV", "yes")
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/fixture/claude")
    monkeypatch.setattr(claude_code, "_native_version", lambda *a: "2.1.330")
    monkeypatch.setattr(antigravity_execution, "resolve_antigravity_bin", lambda: "/fixture/agy")
    monkeypatch.setattr(antigravity_execution, "default_home", lambda: tmp_path / "home")
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fixture/grok")
    adapter = {"claude-code": claude_code, "antigravity": antigravity_execution, "grok": grok}[host]
    calls = []

    def execute(command, directory, _prompt, _schema, _host, _decode, env, /, **kwargs):
        calls.append(command)
        assert directory == tree
        assert env["STACKOS_WORKSPACE_ROOT"] == str(tree.resolve())
        assert env["RETAIN_NATIVE_ENV"] == "yes"
        return AgentRunResult(0, thread_id="exact-session", result={"summary": "read"})

    monkeypatch.setattr(adapter, "execute_json_agent", execute)
    for resume in (None, "exact-session"):
        result = service.run_agent(
            host,
            tree,
            "assigned role",
            "read",
            SCHEMA,
            mcp_route_identity=route,
            resume_thread_id=resume,
        )
        assert result.ok, result.error
    assert len(calls) == 2
    assert path.read_bytes() == before


def route_fixture(tmp_path, monkeypatch, host):
    home, tree = tmp_path / "home", tmp_path / "tree"
    home.mkdir(exist_ok=True)
    tree.mkdir(exist_ok=True)
    monkeypatch.setattr(service, "default_home", lambda: home)
    monkeypatch.setattr(bridge, "default_home", lambda: home)
    monkeypatch.delenv("GROK_HOME", raising=False)
    command = bridge.resolve_bridge_command(runtime=host, workspace_root=tree)
    servers = {"stackos": {"command": command[0], "args": command[1:], "cwd": str(tree)}}
    if host == "claude-code":
        path = home / ".claude.json"
        content = {"mcpServers": servers, "theme": "retained"}
        encode = json.dumps
    elif host == "antigravity":
        path = tree / ".agents/mcp_config.json"
        content = {"mcpServers": servers, "other": "retained"}
        encode = json.dumps
    elif host == "grok":
        path = tree / ".grok/config.toml"
        content = {"mcp_servers": servers, "ui": {"theme": "retained"}}
        encode = tomlkit.dumps
    else:
        raise ValueError(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encode(content))
    fragment = tmp_path / "route.toml"
    fragment.write_text(tomlkit.dumps({"mcp_servers": {"stackos": {"args": command[1:]}}}))
    ref = {"path": str(fragment), "sha256": hashlib.sha256(fragment.read_bytes()).hexdigest()}
    return tree, path, content, encode, ref


@pytest.mark.parametrize("host", ["claude-code", "antigravity", "grok"])
@pytest.mark.parametrize("resume", [None, "exact-native-session"])
def test_native_routes_fresh_and_resume_are_verified_and_frozen(
    tmp_path, monkeypatch, host, resume
):
    tree, path, content, encode, ref = route_fixture(tmp_path, monkeypatch, host)
    before = path.read_bytes()
    identity = service.task_mcp_route(host, tree, ref)
    definition = service._definition_by_key(host)
    calls = []

    def execute(directory, instructions, prompt, schema, **kwargs):
        calls.append(kwargs)
        assert directory == tree
        assert instructions == "current assigned role"
        assert kwargs.get("resume_thread_id") == resume
        return AgentRunResult(0, thread_id=resume or "fresh-native", result={"summary": "read"})

    from dataclasses import replace

    monkeypatch.setattr(
        service,
        "_definition_by_key",
        lambda _: replace(definition, run_agent=execute, native_models=None),
    )
    result = service.run_agent(
        host,
        tree,
        "current assigned role",
        "read",
        SCHEMA,
        mcp_config_ref=ref,
        mcp_route_identity=identity,
        resume_thread_id=resume,
    )
    assert result.ok, result.error
    assert len(calls) == 1
    assert path.read_bytes() == before
    key = "mcpServers" if host in {"claude-code", "antigravity"} else "mcp_servers"
    # A command change with identical task leaves cannot masquerade as the same route.
    content[key]["stackos"]["command"] = str(Path(sys.executable).parent / "other-python")
    path.write_text(encode(content))
    changed = service.run_agent(
        host,
        tree,
        "current assigned role",
        "read",
        SCHEMA,
        mcp_config_ref=ref,
        mcp_route_identity=identity,
        resume_thread_id=resume,
    )
    assert not changed.ok
    assert "Frozen task MCP route changed" in changed.error
    assert len(calls) == 1


@pytest.mark.parametrize("host", ["claude-code", "antigravity", "grok"])
@pytest.mark.parametrize("defect", ["missing", "root", "daemon", "disabled", "serena"])
def test_native_route_target_controls(tmp_path, monkeypatch, host, defect):
    tree, path, content, encode, ref = route_fixture(tmp_path, monkeypatch, host)
    key = "mcpServers" if host in {"claude-code", "antigravity"} else "mcp_servers"
    entry = content[key]["stackos"]
    if defect == "missing":
        content[key] = {}
    elif defect == "root":
        entry["cwd"] = str(tmp_path)
    elif defect == "daemon":
        # Env overrides may change omitted daemon options too.
        entry["env"] = {"STACKOS_PORT": "5999"}
    elif defect == "disabled":
        entry["disabled"] = True
    else:
        content[key]["serena"] = {
            "command": "python3",
            "args": ["foreign-wrapper.py"],
            "env": {"SERENA_PROJECT": str(tmp_path)},
        }
        fragment = Path(ref["path"])
        fragment.write_text(
            fragment.read_text() + '\n[mcp_servers.serena]\nargs=["foreign-wrapper.py"]'
        )
        ref["sha256"] = hashlib.sha256(fragment.read_bytes()).hexdigest()
    path.write_text(encode(content))
    with pytest.raises(ValueError):
        service.task_mcp_route(host, tree, ref)


@pytest.mark.parametrize("host", ["antigravity", "grok"])
def test_workspace_routes_do_not_apply_external_overrides(tmp_path, monkeypatch, host):
    tree, path, _, _, ref = route_fixture(tmp_path, monkeypatch, host)
    before = path.read_bytes()
    fragment = Path(ref["path"])
    fragment.write_text('[mcp_servers.stackos]\nargs=["arbitrary-override"]')
    ref["sha256"] = hashlib.sha256(fragment.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="arbitrary external overrides are unsupported"):
        service.task_mcp_route(host, tree, ref)
    assert path.read_bytes() == before


@pytest.mark.parametrize("resume", [None, "exact-native-session"])
def test_claude_per_call_route_preserves_commands_and_role(tmp_path, monkeypatch, resume):
    tree, path, content, _, ref = route_fixture(tmp_path, monkeypatch, "claude-code")
    original = path.read_bytes()
    content["mcpServers"]["stackos"]["env"] = {"EXISTING_ROUTE_REF": "retained"}
    path.write_text(json.dumps(content))
    original = path.read_bytes()
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/native/claude")
    monkeypatch.setattr(claude_code, "_native_version", lambda *args: "2.1.330")
    calls = []

    def execute(command, directory, prompt, schema, *args, **kwargs):
        calls.append(command)
        assert directory == tree
        assert command[command.index("--append-system-prompt") + 1] == "current role"
        assert command[command.index("--system-prompt-snapshot") + 1] == "off"
        assert "--system-prompt" not in command  # Retain Claude's native default prompt.
        assert "--strict-mcp-config" in command
        actual = json.loads(command[command.index("--mcp-config") + 1])["mcpServers"]
        assert actual["stackos"] == content["mcpServers"]["stackos"]
        assert ("--resume" in command) == bool(resume)
        return AgentRunResult(0, thread_id=resume or "fresh", result={"summary": "read"})

    monkeypatch.setattr(claude_code, "execute_json_agent", execute)
    result = service.run_agent(
        "claude-code",
        tree,
        "current role",
        "read",
        SCHEMA,
        mcp_config_ref=ref,
        resume_thread_id=resume,
    )
    assert result.ok, result.error
    assert path.read_bytes() == original
    Path(ref["path"]).write_text("changed")
    assert not service.run_agent(
        "claude-code", tree, "current role", "read", SCHEMA, mcp_config_ref=ref
    ).ok
    assert len(calls) == 1


@pytest.mark.parametrize("host", ["claude-desktop", "hermes"])
def test_unsupported_host_has_no_task_route(tmp_path, host):
    with pytest.raises(ValueError, match="unsupported"):
        service.task_mcp_route(host, tmp_path, {})


@pytest.mark.parametrize("resume", [None, "exact-native-session"])
def test_claude_disabled_native_server_stops_before_spawn(tmp_path, monkeypatch, resume):
    tree, path, content, _, ref = route_fixture(tmp_path, monkeypatch, "claude-code")
    content["projects"] = {str(tree): {"disabledMcpServers": ["stackos"]}}
    path.write_text(json.dumps(content))
    monkeypatch.setattr(claude_code, "execute_json_agent", lambda *a, **kw: pytest.fail("spawned"))
    before = path.read_bytes()
    result = service.run_agent(
        "claude-code",
        tree,
        "role",
        "read",
        SCHEMA,
        mcp_config_ref=ref,
        resume_thread_id=resume,
    )
    assert not result.ok and "disabled in native project settings" in result.error
    assert path.read_bytes() == before


def test_claude_worktree_retains_common_repository_mcp_disable(tmp_path, monkeypatch):
    tree, path, content, _, ref = route_fixture(tmp_path, monkeypatch, "claude-code")
    repository = tmp_path / "repository"
    repository.mkdir()
    for args in (
        ["init", str(repository)],
        [
            "-C",
            str(repository),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        ["-C", str(repository), "worktree", "add", "--detach", str(tree)],
    ):
        subprocess.run(["git", *args], check=True, capture_output=True)
    content["projects"] = {str(repository): {"disabledMcpServers": ["stackos"]}}
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="disabled in native project settings"):
        service.task_mcp_route("claude-code", tree, ref)


def test_claude_adapter_rechecks_frozen_route_at_payload_read(tmp_path, monkeypatch):
    tree, path, content, _, ref = route_fixture(tmp_path, monkeypatch, "claude-code")
    identity = service.task_mcp_route("claude-code", tree, ref)
    content["mcpServers"]["stackos"]["env"] = {"ROUTE_LABEL": "changed"}
    path.write_text(json.dumps(content))
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/native/claude")
    monkeypatch.setattr(claude_code, "execute_json_agent", lambda *a, **kw: pytest.fail("spawned"))
    result = claude_code.run_agent(
        tree,
        "role",
        "read",
        SCHEMA,
        mcp_config_ref=ref,
        mcp_route_identity=identity,
    )
    assert not result.ok and "changed before native launch" in result.error
