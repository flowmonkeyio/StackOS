"""Claude Desktop MCP lifecycle adapter."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    command_matches,
    resolve_bridge_command,
    token_preflight,
)
from stackos.host_mcp.json_config import read_json_object, remove_mcp_server, upsert_mcp_server
from stackos.host_mcp.restart_state import (
    clear_restart_required,
    mark_restart_required,
    pending_restart,
)
from stackos.host_mcp.result import HostMcpResult, looks_secretish

HOST_KEY = "claude-desktop"
SURFACE = "desktop-json"
CONFIG_ENV = "STACKOS_CLAUDE_DESKTOP_CONFIG"
RESTART_REPAIR = "Run `stackos install --mcp-only` or desktop Repair, then restart Claude Desktop."
RunningState = Literal["running", "not_running", "unknown"]


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    path = config_path(home)
    if not _available(path):
        return _absent(path)
    loaded = read_json_object(path)
    if not loaded.ok:
        return _config_error(path, loaded.error)
    servers = loaded.data.get("mcpServers")
    if servers is None:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="available_unregistered",
            message="Claude Desktop has no MCP servers configured for StackOS.",
            ok=False,
            available=True,
            blocking=True,
            config_path=str(path),
            repair=RESTART_REPAIR,
        )
    if not isinstance(servers, dict):
        return _config_error(path, "mcpServers must be a JSON object")
    server = servers.get(server_name)
    if server is None:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="available_unregistered",
            message="Claude Desktop is not registered with StackOS.",
            ok=False,
            available=True,
            blocking=True,
            config_path=str(path),
            repair=RESTART_REPAIR,
        )
    if not isinstance(server, dict):
        return _config_error(path, f"mcpServers.{server_name} must be a JSON object")
    command = _server_command(server)
    if looks_secretish(server):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Claude Desktop StackOS MCP entry appears to contain secret material.",
            ok=False,
            available=True,
            blocking=True,
            config_path=str(path),
            repair=RESTART_REPAIR,
        )
    expected_command = resolve_bridge_command(runtime=HOST_KEY)
    if command_matches(expected_command, command):
        restart_marker = pending_restart(
            HOST_KEY,
            config_path=str(path),
            command=expected_command,
            home=home,
        )
        if restart_marker:
            running_state = _claude_desktop_running()
            marked_at = restart_marker.get("marked_at")
            restarted_after_change = (
                _claude_desktop_started_after(float(marked_at))
                if isinstance(marked_at, int | float) and running_state == "running"
                else False
            )
            if running_state == "not_running" or restarted_after_change is True:
                clear_restart_required(HOST_KEY, home=home)
                return HostMcpResult(
                    host_key=HOST_KEY,
                    surface=SURFACE,
                    status="registered_current",
                    message=(
                        "Claude Desktop StackOS MCP registration is ready; "
                        "Claude Desktop will load it next time it starts."
                    ),
                    ok=True,
                    available=True,
                    config_path=str(path),
                    command=command,
                )
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="restart_required",
                message=(
                    "Claude Desktop config is updated; restart Claude Desktop to load StackOS MCP."
                ),
                ok=True,
                available=True,
                needs_restart=True,
                config_path=str(path),
                command=command,
                repair="Restart Claude Desktop so it reloads claude_desktop_config.json.",
            )
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message="Claude Desktop StackOS MCP registration is healthy.",
            ok=True,
            available=True,
            config_path=str(path),
            command=command,
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_stale",
        message="Claude Desktop has a StackOS MCP entry, but it is stale.",
        ok=False,
        available=True,
        blocking=True,
        config_path=str(path),
        command=command,
        repair=RESTART_REPAIR,
    )


def register(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    path = config_path(home)
    if not _available(path):
        return _absent(path)
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
            config_path=str(path),
            repair="Run `stackos install` or desktop Repair before registering Claude Desktop MCP.",
        )
    current = inspect(home, server_name=server_name)
    if current.status in {"registered_current", "restart_required"}:
        return current
    command = resolve_bridge_command(runtime=HOST_KEY)
    ok, error = upsert_mcp_server(
        path,
        server_name,
        {"command": command[0], "args": command[1:]},
    )
    if not ok:
        return _config_error(path, error)
    if _claude_desktop_running() == "not_running":
        clear_restart_required(HOST_KEY, home=home)
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message=(
                f"Registered MCP '{server_name}' with Claude Desktop; "
                "Claude Desktop will load it next time it starts."
            ),
            ok=True,
            available=True,
            config_path=str(path),
            command=command,
        )
    mark_restart_required(
        HOST_KEY,
        surface=SURFACE,
        config_path=str(path),
        command=command,
        home=home,
    )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="restart_required",
        message=f"Registered MCP '{server_name}' with Claude Desktop; restart Claude Desktop.",
        ok=True,
        available=True,
        needs_restart=True,
        config_path=str(path),
        command=command,
        repair="Restart Claude Desktop so it reloads claude_desktop_config.json.",
    )


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    path = config_path(home)
    if not _available(path):
        return _absent(
            path,
            message="Claude Desktop not found; skipped Claude Desktop MCP removal.",
        )
    ok, error, removed = remove_mcp_server(path, server_name)
    if not ok:
        return _config_error(path, error, status="remove_failed")
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="removed",
        message=(
            f"Removed MCP '{server_name}' from Claude Desktop; restart Claude Desktop."
            if removed
            else f"MCP '{server_name}' not registered with Claude Desktop; nothing to remove."
        ),
        ok=True,
        available=True,
        needs_restart=removed,
        config_path=str(path),
        repair=(
            "Restart Claude Desktop so it reloads claude_desktop_config.json." if removed else None
        ),
    )


def config_path(home: Path) -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def _available(path: Path) -> bool:
    if os.environ.get(CONFIG_ENV):
        return True
    if path.exists():
        return True
    if sys.platform != "darwin":
        return False
    return any(
        candidate.exists()
        for candidate in (
            Path("/Applications/Claude.app"),
            Path("/Applications/Claude Desktop.app"),
        )
    )


def _claude_desktop_running() -> RunningState:
    if sys.platform != "darwin":
        return "unknown"
    for process_name in ("Claude", "Claude Desktop"):
        try:
            result = subprocess.run(
                ["pgrep", "-x", process_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        if result.returncode == 0:
            return "running"
        if result.returncode != 1:
            return "unknown"
    return "not_running"


def _claude_desktop_started_after(marked_at: float) -> bool | None:
    """Return whether a current Claude app process started after a config write."""

    if sys.platform != "darwin":
        return None
    starts: list[float] = []
    for process_name in ("Claude", "Claude Desktop"):
        try:
            found = subprocess.run(
                ["pgrep", "-x", process_name],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if found.returncode == 1:
            continue
        if found.returncode != 0:
            return None
        for raw_pid in found.stdout.splitlines():
            pid = raw_pid.strip()
            if not pid.isdigit():
                continue
            try:
                started = subprocess.run(
                    ["ps", "-o", "lstart=", "-p", pid],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                    env={**os.environ, "LC_ALL": "C"},
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if started.returncode != 0:
                continue
            started_at = _parse_process_start(started.stdout)
            if started_at is not None:
                starts.append(started_at)
    if not starts:
        return None
    return any(started_at > marked_at for started_at in starts)


def _parse_process_start(value: str) -> float | None:
    normalized = " ".join(value.split())
    try:
        return time.mktime(time.strptime(normalized, "%a %b %d %H:%M:%S %Y"))
    except (OverflowError, ValueError):
        return None


def _absent(
    path: Path,
    message: str = "Claude Desktop not found; skipping MCP registration.",
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="absent",
        message=message,
        ok=True,
        available=False,
        advisory=True,
        config_path=str(path),
        repair="Install Claude Desktop, then run `stackos install --mcp-only` or desktop Repair.",
    )


def _config_error(
    path: Path,
    error: str | None,
    *,
    status: str = "config_unreadable",
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status=status,  # type: ignore[arg-type]
        message=f"Claude Desktop MCP config could not be updated: {error or 'unknown error'}.",
        ok=False,
        available=True,
        blocking=True,
        config_path=str(path),
        repair=(
            "Fix claude_desktop_config.json, then run `stackos install --mcp-only` "
            "or desktop Repair."
        ),
    )


def _server_command(server: dict[str, Any]) -> list[str]:
    command = server.get("command")
    args = server.get("args")
    if not isinstance(command, str):
        return []
    if isinstance(args, list) and all(isinstance(arg, str) for arg in args):
        return [command, *args]
    return [command]
