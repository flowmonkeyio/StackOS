"""Antigravity CLI discovery, user-scoped MCP and StackOS skill lifecycle.

The CLI reads ~/.gemini/config/mcp_config.json (https://antigravity.google/docs/mcp/).
Use the shared JSON owner rather than native add/remove, which can overwrite a
foreign same-name entry. Workspace and legacy Gemini CLI settings are untouched.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
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


def native_models(cli: str) -> tuple[str, ...] | None:
    """Exact account-eligible native IDs from `agy models`, never display names."""
    try:
        result = subprocess.run(
            [cli, "models"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=8,
            env=subprocess_env_for_cli(cli),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    rows = [line.split("\t", 1)[0] for line in result.stdout.splitlines() if "\t" in line]
    if not rows or any(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,199}", row) is None for row in rows):
        return None
    return tuple(dict.fromkeys(rows))


def skills_root(home: Path) -> Path:
    return config_path(home).parent / "skills"


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    return _inspect_snapshot(home, server_name=server_name)[0]


def _inspect_snapshot(home: Path, *, server_name: str) -> tuple[HostMcpResult, JsonConfigRead]:
    loaded = read_json_object(config_path(home))
    current = _inspect_assets(home, loaded, server_name=server_name)
    if resolve_antigravity_bin() is None:
        return replace(
            current,
            status="absent",
            message="Runnable Antigravity CLI (agy) not found; skipped optional MCP integration.",
            ok=True,
            available=False,
            advisory=True,
            blocking=False,
        ), loaded
    return current, loaded


def _inspect_assets(home: Path, loaded: JsonConfigRead, *, server_name: str) -> HostMcpResult:
    from stackos.install import stackos_skill_is_current, stackos_skill_is_owned

    current = _inspect_config(home, loaded, server_name=server_name)
    root = skills_root(home)
    target = root / "stackos"
    receipt_owned = stackos_skill_is_owned(root)
    skill_current = stackos_skill_is_current(root, allow_extra=receipt_owned)
    owned = receipt_owned or skill_current
    if current.status in {"config_unreadable", "registered_unsafe", "registered_unmanaged"}:
        # Skill receipts remain independently removable after MCP config changes.
        return replace(current, managed=owned)
    if root.is_symlink() or target.is_symlink() or (target.exists() and not owned):
        return replace(
            current,
            status="registered_unmanaged",
            message="Antigravity has an unmanaged StackOS skill; it was left unchanged.",
            ok=False,
            blocking=True,
            repair="Review ~/.gemini/config/skills/stackos before connecting StackOS.",
        )
    if current.status == "available_unregistered" and owned:
        current = replace(current, selected=True, managed=True)
    if current.status == "registered_current" and (not skill_current or not receipt_owned):
        return replace(
            current,
            status="registered_stale",
            message="Antigravity MCP is connected, but its StackOS skill needs repair.",
            ok=False,
            blocking=True,
            repair=REPAIR,
        )
    return current


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
    if current.status not in {"available_unregistered", "registered_stale"}:
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
    native = _inspect_config(home, loaded, server_name=server_name)
    if native.status != "registered_current":
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
    from stackos.install import copy_managed_stackos_skill_to

    try:
        copy_managed_stackos_skill_to(skills_root(home))
    except OSError:
        return replace(
            current,
            status="register_failed",
            message="Antigravity MCP is connected, but its StackOS skill could not be installed.",
            ok=False,
            blocking=True,
            selected=True,
            managed=True,
            repair=REPAIR,
        )
    return inspect(home, server_name=server_name)


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    from stackos.install import remove_managed_stackos_skill_from

    path = config_path(home)
    loaded = read_json_object(path)
    current = _inspect_assets(home, loaded, server_name=server_name)
    native = _inspect_config(home, loaded, server_name=server_name)
    if not current.managed and current.status != "available_unregistered":
        return current
    if not remove_managed_stackos_skill_from(skills_root(home)):
        return replace(
            current,
            status="remove_failed",
            message="Antigravity's managed StackOS skill could not be removed safely.",
            ok=False,
            blocking=True,
            repair="Check the StackOS skill ownership record and folder permissions, then retry.",
        )
    if native.status not in {
        "available_unregistered",
        "registered_current",
        "registered_stale",
    }:
        return replace(
            native,
            warnings=[
                *native.warnings,
                "Owned StackOS skill files were cleaned; config was preserved.",
            ],
        )
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
        message="Removed Antigravity's StackOS MCP entry and managed skill; kept unrelated files.",
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
