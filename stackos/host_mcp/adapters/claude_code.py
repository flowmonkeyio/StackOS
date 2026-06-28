"""Claude Code MCP lifecycle adapter."""

from __future__ import annotations

from pathlib import Path

from stackos import claude_mcp
from stackos.host_mcp.bridge import MCP_SERVER_NAME, resolve_bridge_command
from stackos.host_mcp.result import HostMcpResult

HOST_KEY = "claude-code"
SURFACE = "cli"


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    result = claude_mcp.inspect(
        home=home,
        expected_command=resolve_bridge_command(runtime=HOST_KEY),
        server_name=server_name,
    )
    return _convert(result)


def register(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    result = claude_mcp.register(
        home=home,
        bridge_command=resolve_bridge_command(runtime=HOST_KEY),
        server_name=server_name,
    )
    return _convert(result)


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    result = claude_mcp.remove(home=home, server_name=server_name)
    return _convert(result)


def _convert(result: claude_mcp.ClaudeMcpResult) -> HostMcpResult:
    absent = result.status == "claude_absent"
    ok = result.ok
    blocking = not ok and not absent
    status = {
        "healthy": "registered_current",
        "registered": "registered",
        "removed": "removed",
        "claude_absent": "absent",
        "unsupported_cli": "unsupported_host_version",
        "missing": "available_unregistered",
        "stale": "registered_stale",
        "registration_failed": "register_failed",
        "token_missing": "token_missing",
    }.get(result.status, "register_failed")
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status=status,  # type: ignore[arg-type]
        message=result.message,
        ok=ok,
        available=not absent,
        advisory=absent,
        blocking=blocking,
        command=list(result.command),
        repair=result.repair,
        warnings=[warning for warning in [result.legacy_json_error] if warning],
    )
