"""Host status reads do not repair registrations or rewrite restart markers."""

import asyncio
import json
import subprocess
import tomllib
from dataclasses import asdict
from pathlib import Path
from threading import get_ident

import pytest
from typer.testing import CliRunner

from stackos import claude_mcp
from stackos.cli import app
from stackos.cli.doctor_commands import _check_mcp_hosts
from stackos.host_mcp import restart_state, service
from stackos.host_mcp.adapters import claude_desktop, codex, gemini_cli, grok, hermes
from stackos.host_mcp.result import HostMcpResult
from stackos.install import copy_managed_stackos_skill_to
from stackos.operations.system import HostMcpStatusInput, host_mcp_status


@pytest.fixture
def native_registrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real saved host formats; substitute native discovery/CLI reads only."""
    for key in ("STACKOS_MCP_BRIDGE_COMMAND", "STACKOS_WORKSPACE_ROOT", "GROK_HOME", "HERMES_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("STACKOS_HOME", str(tmp_path))
    launcher = tmp_path / "stackos"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(launcher))
    desktop = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(claude_desktop, "config_path", lambda _home: desktop)
    monkeypatch.setattr(claude_desktop, "_available", lambda _path: True)
    monkeypatch.setattr(codex, "resolve_codex_bins", lambda: ["/fixture/codex"])
    monkeypatch.setattr(claude_mcp, "resolve_claude_bin", lambda _bin: "/fixture/claude")
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fixture/grok")
    monkeypatch.setattr(gemini_cli, "resolve_gemini_bin", lambda: None)
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fixture/hermes")
    copy_managed_stackos_skill_to(tmp_path / ".grok/skills")
    codex_config = tmp_path / ".codex/config.toml"
    grok_config = tmp_path / ".grok/config.toml"
    claude_config = tmp_path / ".claude.json"
    paths = [codex_config, grok_config, claude_config, desktop]

    def write(extra=(), *, executable=None, runtime=None):
        for host in ("codex", "grok", "claude-code", "claude-desktop"):
            options = {"--host": "127.0.0.1", "--port": "5180", "--runtime": runtime or host}
            options.update(zip(extra[::2], extra[1::2], strict=True))
            command = [
                executable or str(launcher),
                "mcp-bridge",
                *(part for pair in options.items() for part in pair),
            ]
            entry = {"command": command[0], "args": command[1:]}
            if host in {"codex", "grok"}:
                path = codex_config if host == "codex" else grok_config
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "[mcp_servers.stackos]\n"
                    f"command = {json.dumps(entry['command'])}\n"
                    f"args = {json.dumps(entry['args'])}\n"
                )
            else:
                path = claude_config if host == "claude-code" else desktop
                path.write_text(json.dumps({"mcpServers": {"stackos": entry}}))
        return [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]

    def codex_read(_binary, args):
        assert args == ["mcp", "get", "stackos", "--json"], "unexpected CLI mutation"
        entry = tomllib.loads(codex_config.read_text())["mcp_servers"]["stackos"]
        payload = {"name": "stackos", "enabled": True, "transport": {"type": "stdio", **entry}}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    def grok_help(_binary, args, *, home):
        assert home == tmp_path
        assert args == ["mcp", "add", "--help"], "unexpected CLI mutation"
        return subprocess.CompletedProcess(args, 0, "--scope user --transport stdio", "")

    monkeypatch.setattr(codex, "_run_codex", codex_read)
    monkeypatch.setattr(grok, "_run_grok", grok_help)
    return tmp_path, write, paths


@pytest.mark.parametrize("context", ["omitted", "host-port", "all"])
def test_default_bridge_context_agrees_across_status_callers(
    native_registrations, monkeypatch: pytest.MonkeyPatch, context: str
) -> None:
    home, write, paths = native_registrations
    before = write()
    if context != "omitted":
        monkeypatch.setenv("STACKOS_HOST", "127.0.0.1")
        monkeypatch.setenv("STACKOS_PORT", "5180")
    if context == "all":
        monkeypatch.setenv("STACKOS_DATA_DIR", str(home / ".local/share/stackos"))
        monkeypatch.setenv("STACKOS_STATE_DIR", str(home / ".local/state/stackos"))
    ok, doctor_rows = _check_mcp_hosts(home)
    cli = CliRunner().invoke(app, ["mcp-host-status", "--json"], catch_exceptions=False)
    daemon = asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None)).model_dump()
    assert ok, [(row["host_key"], row["status"]) for row in doctor_rows]
    assert cli.exit_code == 0
    assert daemon["ok"]
    for rows in (doctor_rows, json.loads(cli.stdout)["hosts"], daemon["items"]):
        states = {row["host_key"]: row for row in rows}
        for key in ("codex", "claude-code", "claude-desktop", "grok"):
            assert states[key]["connection_state"] == "connected", states[key]
            assert not states[key]["blocking"]
        assert states["gemini-cli"]["connection_state"] == "unavailable"
        assert states["hermes"]["connection_state"] == "available"
        assert not states["gemini-cli"]["blocking"] and not states["hermes"]["blocking"]
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before


@pytest.mark.parametrize(
    "change", ["launcher", "runtime", "workspace", "host", "port", "data", "state", "foreign"]
)
def test_host_context_mismatches_stay_actionable(native_registrations, change: str) -> None:
    home, write, paths = native_registrations
    changes = {
        "launcher": {"executable": "/old/stackos"},
        "runtime": {"runtime": "wrong-host"},
        "workspace": {"extra": ["--workspace-root", str(home)]},
        "host": {"extra": ["--host", "localhost"]},
        "port": {"extra": ["--port", "5199"]},
        "data": {"extra": ["--data-dir", str(home / "custom-data")]},
        "state": {"extra": ["--state-dir", str(home / "custom-state")]},
        "foreign": {"executable": "/foreign/server"},
    }
    before = write(**changes[change])
    ok, rows = _check_mcp_hosts(home)
    assert not ok
    for row in rows:
        if row["host_key"] in {"codex", "claude-code", "claude-desktop", "grok"}:
            assert row["connection_state"] == (
                "review_required" if change == "foreign" else "repair_needed"
            ), row
            assert row["repair"] and row["blocking"]
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before


def test_restart_marker_retains_equivalent_default_context(native_registrations, monkeypatch):
    home, write, _paths = native_registrations
    write()
    desktop = claude_desktop.config_path(home)
    entry = json.loads(desktop.read_text())["mcpServers"]["stackos"]
    restart_state.mark_restart_required(
        "claude-desktop",
        surface="desktop-json",
        config_path=str(desktop),
        command=[entry["command"], *entry["args"]],
        home=home,
    )
    marker = restart_state.state_path(home)
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "running")
    monkeypatch.setattr(claude_desktop, "_claude_desktop_started_after", lambda _at: False)
    monkeypatch.setenv("STACKOS_DATA_DIR", str(home / ".local/share/stackos"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(home / ".local/state/stackos"))
    result = claude_desktop.inspect(home)
    assert result.status == "restart_required" and result.needs_restart
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before


@pytest.mark.parametrize("state", ["pending", "closed", "restarted", "expired"])
def test_claude_desktop_status_preserves_config_and_restart_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    config = tmp_path / "claude_desktop_config.json"
    command = ["stackos", "mcp-bridge", "--runtime", "claude-desktop"]
    config.write_text(
        json.dumps({"mcpServers": {"stackos": {"command": command[0], "args": command[1:]}}})
    )
    monkeypatch.delenv("STACKOS_STATE_DIR", raising=False)
    marker = restart_state.state_path(tmp_path)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "claude-desktop": {
                    "config_path": str(config),
                    "command": command,
                    "marked_at": 1 if state == "expired" else 10_000,
                }
            }
        )
    )
    original = [(path.read_bytes(), path.stat().st_mtime_ns) for path in (config, marker)]
    monkeypatch.setattr(restart_state.time, "time", lambda: 10_100)
    monkeypatch.setattr(claude_desktop, "config_path", lambda _home: config)
    monkeypatch.setattr(claude_desktop, "_available", lambda _path: True)
    monkeypatch.setattr(claude_desktop, "resolve_bridge_command", lambda **_kwargs: command)
    monkeypatch.setattr(
        claude_desktop,
        "_claude_desktop_running",
        lambda: "not_running" if state == "closed" else "running",
    )
    monkeypatch.setattr(
        claude_desktop, "_claude_desktop_started_after", lambda _at: state == "restarted"
    )

    result = claude_desktop.inspect(tmp_path)

    assert result.status == ("restart_required" if state == "pending" else "registered_current")
    assert result.needs_restart is (state == "pending")
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in (config, marker)] == original


def test_host_inspection_does_not_return_raw_exception_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenAdapter:
        HOST_KEY = "codex"

        @staticmethod
        def inspect(_home: Path):
            raise RuntimeError("private-config-fragment api_key=synthetic-secret")

    monkeypatch.setattr(
        service,
        "_definitions",
        lambda: [
            service.HostMcpDefinition(
                adapter=BrokenAdapter,
                policy=service.HostMcpPolicy("codex", "ChatGPT / Codex", "automatic"),
            )
        ],
    )
    result = service.inspect_all(home=tmp_path)
    assert result.ok is False
    assert result.results[0].status == "register_failed"
    assert "RuntimeError" in result.results[0].message
    assert "private-config-fragment" not in str(result.to_info())
    assert "synthetic-secret" not in str(result.to_info())


def test_host_status_offloads_inspection_without_context_or_filesystem_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller_thread = get_ident()
    inspected_threads: list[int] = []

    def inspect():
        inspected_threads.append(get_ident())
        return service.HostMcpAggregate(ok=True, results=[])

    monkeypatch.setattr(service, "inspect_all", inspect)
    result = asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None))
    assert result.model_dump() == {"ok": True, "items": []}
    assert len(inspected_threads) == 1
    assert inspected_threads[0] != caller_thread


def test_host_status_projects_shared_execution_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    definitions = service._definitions()
    aggregate = service.HostMcpAggregate(
        ok=True,
        results=[
            service.normalize_result(
                HostMcpResult(d.policy.host_key, "fixture", "absent", "Not installed", True, False),
                d.policy,
            )
            for d in definitions
        ],
    )
    monkeypatch.setattr(service, "inspect_all", lambda: aggregate)

    result = asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None)).model_dump(mode="json")

    for row, definition in zip(result["items"], definitions, strict=True):
        expected = asdict(definition.policy.agent_execution)
        expected.pop("auth_status_command")
        expected.update(credential_status="not_checked", model_access="not_checked")
        assert row["agent_execution"] == json.loads(json.dumps(expected))
        assert row["available"] is False
        assert row["agent_execution"]["credential_status"] == "not_checked"
        assert row["agent_execution"]["model_access"] == "not_checked"
