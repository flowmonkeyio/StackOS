"""Removal-only boundary for positively owned obsolete Gemini CLI wiring.

No discovery, registration, execution, saved-default migration or credentials.
Historical host keys/snapshots remain Gemini; callers must reselect retired hosts.
"""

from pathlib import Path

from stackos.host_mcp.bridge import MCP_SERVER_NAME, is_stackos_bridge_command
from stackos.host_mcp.json_config import read_json_object, remove_mcp_server
from stackos.host_mcp.result import HostMcpResult, looks_secretish


def remove_owned_registration(home: Path) -> HostMcpResult | None:
    path = home / ".gemini" / "settings.json"
    loaded = read_json_object(path)
    servers = loaded.data.get("mcpServers")
    if not loaded.ok or not isinstance(servers, dict):
        return None
    entry = servers.get(MCP_SERVER_NAME)
    if not isinstance(entry, dict) or entry.get("env") or looks_secretish(entry):
        return None
    if any(key in entry for key in ("url", "serverUrl", "httpUrl")):
        return None
    args = entry.get("args")
    if not isinstance(entry.get("command"), str) or not isinstance(args, list):
        return None
    command = [entry["command"], *args]
    if not all(isinstance(arg, str) for arg in command) or not is_stackos_bridge_command(command):
        return None
    if args.count("--runtime") != 1 or args[-2:] != ["--runtime", "gemini-cli"]:
        return None
    try:
        ok, _, _ = remove_mcp_server(path, MCP_SERVER_NAME)
    except OSError:
        ok = False
    return HostMcpResult(
        host_key="gemini-cli",
        surface="retired-config",
        status="removed" if ok else "remove_failed",
        message="Removed obsolete owned Gemini CLI wiring."
        if ok
        else "Could not remove owned Gemini CLI wiring.",
        ok=ok,
        available=False,
        blocking=not ok,
        managed=True,
        config_path=str(path),
        repair=None
        if ok
        else "Review ~/.gemini/settings.json permissions locally and rerun Repair.",
    )
