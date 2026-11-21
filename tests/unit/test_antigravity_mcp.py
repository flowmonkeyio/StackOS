"""Antigravity's unpublished lifecycle producer uses isolated native/config fixtures."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from stackos.host_mcp import discovery, service
from stackos.host_mcp.adapters import antigravity
from stackos.host_mcp.bridge import resolve_bridge_command

POLICY = service.HostMcpPolicy("antigravity", "Antigravity", "automatic")


def _executable(path: Path, body: str = "printf '1.5.13\\n'\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def native_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    for key in ("STACKOS_MCP_BRIDGE_COMMAND", "STACKOS_WORKSPACE_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("STACKOS_HOME", str(tmp_path))
    monkeypatch.setenv("STACKOS_ANTIGRAVITY_BIN", str(_executable(tmp_path / "bin/agy")))
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(_executable(tmp_path / "stackos")))
    token = tmp_path / ".local/state/stackos/auth.token"
    token.parent.mkdir(parents=True)
    token.write_text("fixture-only")
    yield tmp_path


def _config(home: Path, payload: object) -> Path:
    path = home / ".gemini/config/mcp_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def _entry(command: list[str]) -> dict[str, object]:
    return {"command": command[0], "args": command[1:]}


def _state(home: Path):
    return service.normalize_result(antigravity.inspect(home), POLICY)


def test_owned_lifecycle_preserves_siblings_and_custom_daemon_context(native_home, monkeypatch):
    home = native_home
    for key, value in {
        "STACKOS_HOST": "localhost",
        "STACKOS_PORT": "5199",
        "STACKOS_DATA_DIR": str(home / "custom-data"),
        "STACKOS_STATE_DIR": str(home / "custom-state"),
    }.items():
        monkeypatch.setenv(key, value)
    token = home / "custom-state/auth.token"
    token.parent.mkdir()
    token.write_text("fixture-only")
    siblings = {"foreign": {"command": "other-server", "args": ["--keep"]}}
    settings = {"theme": "dark", "mcpServers": siblings.copy()}
    path = _config(home, settings)
    # Gemini CLI settings and workspace overrides belong to other owners.
    legacy = home / ".gemini/settings.json"
    legacy.write_text('{"keep":true}')
    workspace = home / ".agents/mcp_config.json"
    workspace.parent.mkdir()
    workspace.write_text('{"keep":true}')
    assert _state(home).connection_state == "available"
    registered = antigravity.register(home)
    assert registered.ok and registered.selected and registered.managed
    expected = [
        str(home / "stackos"),
        "mcp-bridge",
        "--host",
        "localhost",
        "--port",
        "5199",
        "--data-dir",
        str(home / "custom-data"),
        "--state-dir",
        str(home / "custom-state"),
        "--runtime",
        "antigravity",
    ]
    assert json.loads(path.read_text()) == {
        **settings,
        "mcpServers": {**siblings, "stackos": _entry(expected)},
    }
    assert _state(home).connection_state == "connected"
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert antigravity.register(home).ok
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert antigravity.remove(home).ok
    assert json.loads(path.read_text()) == settings
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert antigravity.remove(home).ok
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert legacy.read_text() == '{"keep":true}'
    assert workspace.read_text() == '{"keep":true}'


@pytest.mark.parametrize(
    "change", ["launcher", "runtime", "workspace", "host", "port", "data", "state"]
)
def test_owned_context_mismatch_is_actionable_and_repairable(native_home, change):
    command = resolve_bridge_command(runtime="antigravity")
    if change == "launcher":
        command[0] = "/old/stackos"
    elif change == "runtime":
        command[-1] = "wrong-host"
    else:
        command += [
            {
                "workspace": "--workspace-root",
                "host": "--host",
                "port": "--port",
                "data": "--data-dir",
                "state": "--state-dir",
            }[change],
            "5199" if change == "port" else "/different",
        ]
    path = _config(native_home, {"mcpServers": {"stackos": _entry(command)}})
    before = path.read_bytes(), path.stat().st_mtime_ns
    stale = _state(native_home)
    assert stale.connection_state == "repair_needed" and stale.repairable
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert antigravity.register(native_home).ok
    assert _state(native_home).connection_state == "connected"


def test_explicit_canonical_defaults_do_not_cause_rewrite(native_home):
    command = resolve_bridge_command(runtime="antigravity")
    command += [
        "--host",
        "127.0.0.1",
        "--port",
        "5180",
        "--data-dir",
        str(native_home / ".local/share/stackos"),
        "--state-dir",
        str(native_home / ".local/state/stackos"),
    ]
    path = _config(native_home, {"mcpServers": {"stackos": _entry(command)}})
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert _state(native_home).connection_state == "connected"
    assert antigravity.register(native_home).ok
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_disabled_owned_entry_requires_repair_before_connected(native_home):
    path = _config(
        native_home,
        {
            "mcpServers": {
                "stackos": {
                    **_entry(resolve_bridge_command(runtime="antigravity")),
                    "disabled": True,
                }
            }
        },
    )
    assert _state(native_home).connection_state == "repair_needed"
    assert antigravity.register(native_home).ok
    assert "disabled" not in json.loads(path.read_text())["mcpServers"]["stackos"]
    assert _state(native_home).connection_state == "connected"


@pytest.mark.parametrize(
    "entry",
    [
        {"command": "/foreign/server", "args": []},
        {"serverUrl": "https://example.invalid/mcp"},
        {"command": "/foreign/server", "args": ["mcp-bridge"]},
        {"command": "/old/stackos", "args": ["mcp-bridge"], "env": {"STACKOS_PORT": "5199"}},
        {"command": "/old/stackos", "args": ["mcp-bridge"], "env": {"API_KEY": "fixture-private"}},
    ],
)
def test_foreign_or_unsafe_same_name_entry_is_never_mutated(native_home, entry):
    path = _config(native_home, {"mcpServers": {"stackos": entry, "sibling": {"command": "keep"}}})
    before = path.read_bytes(), path.stat().st_mtime_ns
    for action in (antigravity.inspect, antigravity.register, antigravity.remove):
        result = action(native_home)
        assert result.selected and not result.managed
        normalized = service.normalize_result(result, POLICY)
        assert normalized.connection_state == "review_required" and not normalized.repairable
        assert "fixture-private" not in json.dumps(result.to_info())
        assert "/foreign/server" not in json.dumps(result.to_info())
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize(
    "contents",
    [
        "{broken private-content",
        "[]",
        '{"mcpServers":[]}',
        '{"mcpServers":null}',
        '{"mcpServers":{"stackos":null}}',
        '{"mcpServers":{"stackos":[]}}',
        '{"mcpServers":{"stackos":{"command":"stackos","serverUrl":"https://example.invalid"}}}',
        '{"mcpServers":{"stackos":{"command":"stackos","env":[]}}}',
        '{"mcpServers":{"stackos":{"command":"stackos","disabled":"false"}}}',
        '{"mcpServers":{"stackos":{"command":"stackos","args":"private-content"}}}',
    ],
)
def test_malformed_config_is_preserved_with_safe_diagnostic(native_home, contents):
    path = _config(native_home, {})
    path.write_text(contents)
    before = path.read_bytes(), path.stat().st_mtime_ns
    for action in (antigravity.inspect, antigravity.register, antigravity.remove):
        result = action(native_home)
        assert result.status == "config_unreadable"
        assert not service.host_installation_ok(service.normalize_result(result, POLICY))
        assert "private-content" not in json.dumps(result.to_info())
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_registration_requires_token_and_handles_write_failure_safely(native_home, monkeypatch):
    token = native_home / ".local/state/stackos/auth.token"
    token.unlink()
    assert antigravity.register(native_home).status == "token_missing"
    assert not antigravity.config_path(native_home).exists()
    token.write_text("fixture-only")
    path = _config(native_home, {"mcpServers": {"stackos": _entry(["/old/stackos", "mcp-bridge"])}})
    before = path.read_bytes()

    def deny_write(*args):
        raise PermissionError("private-content")

    monkeypatch.setattr("stackos.host_mcp.json_config._atomic_write_json", deny_write)
    for action, status in (
        (antigravity.register, "register_failed"),
        (antigravity.remove, "remove_failed"),
    ):
        result = action(native_home)
        assert not result.ok and result.status == status
        assert "private-content" not in json.dumps(result.to_info())
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    "kind", ["missing", "dangling", "nonexecutable", "broken-shim", "empty-shim"]
)
def test_unrunnable_override_is_advisory_and_does_not_touch_config(native_home, monkeypatch, kind):
    candidate = native_home / "candidate-agy"
    if kind == "dangling":
        candidate.symlink_to(native_home / "missing")
    elif kind == "nonexecutable":
        candidate.write_text("1.5.13")
    elif kind.endswith("shim"):
        _executable(candidate, "exit 127\n" if kind == "broken-shim" else "exit 0\n")
    monkeypatch.setenv("STACKOS_ANTIGRAVITY_BIN", str(candidate))
    path = _config(native_home, {"mcpServers": {"stackos": _entry(["/old/stackos", "mcp-bridge"])}})
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert antigravity.resolve_antigravity_bin() is None
    for action in (antigravity.inspect, antigravity.register, antigravity.remove):
        result = service.normalize_result(action(native_home), POLICY)
        assert result.connection_state == "unavailable"
        assert result.advisory and not result.blocking and service.host_installation_ok(result)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_discovery_skips_broken_path_shim_for_runnable_known_candidate(native_home, monkeypatch):
    shim = _executable(native_home / "cmux/agy", "exit 127\n")
    real = _executable(native_home / "native/agy")
    monkeypatch.delenv("STACKOS_ANTIGRAVITY_BIN")
    monkeypatch.setattr(discovery.shutil, "which", lambda _name: str(shim))
    monkeypatch.setattr(discovery, "discover_with_login_shell", lambda _name: None)
    monkeypatch.setattr(antigravity, "COMMON_ANTIGRAVITY_CLI_CANDIDATES", (str(real),))
    assert antigravity.resolve_antigravity_bin() == str(real)
    assert antigravity.resolve_antigravity_bin(str(shim)) is None  # explicit means no fallback
    assert antigravity.resolve_antigravity_bin(str(real)) == str(real)


@pytest.mark.parametrize("failure", ["timeout", "os-error"])
def test_discovery_probe_is_bounded_and_safe(native_home, monkeypatch, failure):
    def probe(argv, **kwargs):
        assert argv == [str(native_home / "bin/agy"), "--version"]
        assert kwargs["timeout"] == 3
        assert kwargs["stdin"] == subprocess.DEVNULL
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 3)
        raise OSError("private-content")

    monkeypatch.setattr(antigravity.subprocess, "run", probe)
    assert antigravity.resolve_antigravity_bin() is None
