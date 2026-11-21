"""Antigravity CLI discovery and user-scoped MCP lifecycle.

The CLI reads ~/.gemini/config/mcp_config.json (https://antigravity.google/docs/mcp/).
Use the shared JSON owner rather than native add/remove, which can overwrite a
foreign same-name entry. Workspace and legacy Gemini CLI settings are untouched.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    command_matches,
    is_stackos_bridge_command,
    resolve_bridge_command,
    token_preflight,
)
from stackos.host_mcp.discovery import resolve_cli_bins, subprocess_env_for_cli
from stackos.host_mcp.json_config import (
    CONFIG_CHANGED,
    JsonConfigRead,
    read_json_object,
    remove_mcp_server,
    upsert_mcp_server,
)
from stackos.host_mcp.result import HostMcpResult, HostMcpStatus, looks_secretish

HOST_KEY = "antigravity"
SURFACE = "cli-json"
ANTIGRAVITY_BIN_ENV = "STACKOS_ANTIGRAVITY_BIN"
COMMON_ANTIGRAVITY_CLI_CANDIDATES = (
    "~/.local/bin/agy",
    "/opt/homebrew/bin/agy",
    "/usr/local/bin/agy",
)
REPAIR = "Run `stackos install --mcp-only` or desktop Repair."


def resolve_antigravity_bin(explicit: str | None = None) -> str | None:
    """Probe runnable agy, not just an executable cmux shim or dangling link.

    Explicit paths and STACKOS_ANTIGRAVITY_BIN are authoritative (no fallback).
    Otherwise use shared PATH, known-install and login-shell candidate ordering.
    A bounded, noninteractive --version probe must return the native version.
    """
    for candidate in resolve_cli_bins(
        "agy",
        env_var=ANTIGRAVITY_BIN_ENV,
        explicit=explicit,
        common_candidates=COMMON_ANTIGRAVITY_CLI_CANDIDATES,
    ):
        try:
            result = subprocess.run(
                [candidate, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
                timeout=3,
                env=subprocess_env_for_cli(candidate),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and re.fullmatch(
            r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", result.stdout.strip()
        ):
            return candidate
    return None


def config_path(home: Path) -> Path:
    return home / ".gemini" / "config" / "mcp_config.json"


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    return _inspect_snapshot(home, server_name=server_name)[0]


def _inspect_snapshot(
    home: Path, *, server_name: str
) -> tuple[HostMcpResult, JsonConfigRead | None]:
    path = config_path(home)
    if resolve_antigravity_bin() is None:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="absent",
            message="Runnable Antigravity CLI (agy) not found; skipped optional MCP integration.",
            ok=True,
            available=False,
            advisory=True,
        ), None
    loaded = read_json_object(path)
    return _inspect_config(home, loaded, server_name=server_name), loaded


def _inspect_config(home: Path, loaded: JsonConfigRead, *, server_name: str) -> HostMcpResult:
    path = config_path(home)
    if not loaded.ok:
        return _config_error(path)
    servers = loaded.data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return _config_error(path)
    if server_name not in servers:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="available_unregistered",
            message="StackOS is not registered with Antigravity.",
            ok=True,
            available=True,
            advisory=True,
            config_path=str(path),
            repair=REPAIR,
            selected=False,
            managed=False,
        )
    server = servers[server_name]
    if not isinstance(server, dict):
        return _config_error(path)
    if looks_secretish(server):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Antigravity's same-name MCP entry may contain secrets; it was left unchanged.",
            ok=False,
            available=True,
            blocking=True,
            config_path=str(path),
            repair="Review the Antigravity entry locally before connecting StackOS.",
            selected=True,
            managed=False,
        )
    executable = server.get("command")
    args = server.get("args", [])
    remote = any(key in server for key in ("serverUrl", "url", "httpUrl"))
    if (
        (executable is not None and remote)
        or not isinstance(server.get("disabled", False), bool)
        or not isinstance(server.get("env", {}), dict)
    ):
        return _config_error(path)
    if executable is None and remote:
        command = []  # A foreign remote server is never a StackOS stdio bridge.
    elif (
        isinstance(executable, str)
        and executable
        and isinstance(args, list)
        and all(isinstance(arg, str) for arg in args)
    ):
        command = [executable, *args]
    else:
        return _config_error(path)
    expected = resolve_bridge_command(runtime=HOST_KEY)
    # Generated entries are self-contained argv. An added launch environment
    # can redirect the bridge's daemon context; preserve it for local review.
    managed = is_stackos_bridge_command(command) and not server.get("env")
    if managed and command_matches(expected, command) and not server.get("disabled", False):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message="Antigravity StackOS MCP registration is current.",
            ok=True,
            available=True,
            config_path=str(path),
            command=expected,
            selected=True,
            managed=True,
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_stale" if managed else "registered_unmanaged",
        message=(
            "Antigravity has a stale StackOS-owned MCP entry."
            if managed
            else "Antigravity's same-name MCP entry is not owned by StackOS; it was left unchanged."
        ),
        ok=False,
        available=True,
        blocking=True,
        config_path=str(path),
        repair=REPAIR if managed else "Review the Antigravity entry before connecting StackOS.",
        selected=True,
        managed=managed,
    )


def register(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    current, loaded = _inspect_snapshot(home, server_name=server_name)
    if loaded is None or current.status not in {"available_unregistered", "registered_stale"}:
        return current
    token_error = token_preflight(home)
    if token_error:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="token_missing",
            message=token_error,
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install` or desktop Repair before registering Antigravity MCP.",
        )
    path = config_path(home)
    command = resolve_bridge_command(runtime=HOST_KEY)
    try:
        ok, _error = upsert_mcp_server(
            path,
            server_name,
            {"command": command[0], "args": command[1:]},
            expected_entry=loaded.data.get("mcpServers", {}).get(server_name),
        )
    except OSError:
        ok, _error = False, None
    if not ok:
        return _config_error(path, status="register_failed", changed=_error == CONFIG_CHANGED)
    return inspect(home, server_name=server_name)


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    current, loaded = _inspect_snapshot(home, server_name=server_name)
    if loaded is None or current.status not in {
        "available_unregistered",
        "registered_current",
        "registered_stale",
    }:
        return current
    path = config_path(home)
    try:
        ok, _error, _removed = remove_mcp_server(
            path, server_name, expected_entry=loaded.data.get("mcpServers", {}).get(server_name)
        )
    except OSError:
        ok, _error = False, None
    if not ok:
        return _config_error(path, status="remove_failed", changed=_error == CONFIG_CHANGED)
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="removed",
        message="StackOS MCP entry removed from Antigravity; unrelated settings were preserved.",
        ok=True,
        available=True,
        config_path=str(path),
        selected=False,
        managed=False,
    )


def _config_error(
    path: Path, *, status: HostMcpStatus = "config_unreadable", changed: bool = False
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status=status,
        message="Antigravity MCP configuration changed since inspection; inspect it again."
        if changed
        else "Antigravity MCP configuration could not be read or changed safely.",
        ok=False,
        available=True,
        blocking=True,
        config_path=str(path),
        repair=(
            "Check ~/.gemini/config/mcp_config.json and its permissions, then rerun StackOS Repair."
        ),
        selected=True,
        managed=False,
    )
