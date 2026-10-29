"""Grok Build user-scoped MCP lifecycle adapter.

Read the native TOML entry, never the merged Claude/project compatibility view.
Registration uses Grok's CLI; teardown removes owned assets without requiring it.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    is_stackos_bridge_command,
    resolve_bridge_command,
    token_preflight,
)
from stackos.host_mcp.discovery import resolve_cli_bin, subprocess_env_for_cli
from stackos.host_mcp.result import HostMcpResult, looks_secretish
from stackos.host_mcp.toml_config import remove_mcp_server

HOST_KEY = "grok"
SURFACE = "cli-config"
GROK_BIN_ENV = "STACKOS_GROK_BIN"
COMMON_GROK_CLI_CANDIDATES = (
    "~/.local/bin/grok",
    "~/bin/grok",
    "~/.npm-global/bin/grok",
    "/opt/homebrew/bin/grok",
    "/usr/local/bin/grok",
)
REPAIR = "Run `stackos install --mcp-only --mcp-host grok` or desktop Repair."


def grok_home(home: Path) -> Path:
    override = os.environ.get("GROK_HOME")
    return Path(override).expanduser().resolve() if override else home / ".grok"


def resolve_grok_bin(home: Path) -> str | None:
    return resolve_cli_bin(
        "grok",
        env_var=GROK_BIN_ENV,
        common_candidates=(
            str(grok_home(home) / "bin" / "grok"),
            str(home / ".grok" / "bin" / "grok"),
            *COMMON_GROK_CLI_CANDIDATES,
        ),
    )


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    current = _inspect_assets(home, server_name=server_name)
    grok_bin = resolve_grok_bin(home)
    if grok_bin is None:
        if current.status in {"config_unreadable", "registered_unsafe", "registered_unmanaged"}:
            return replace(current, available=False)
        return replace(
            current,
            status="absent",
            available=False,
            ok=True,
            advisory=True,
            blocking=False,
            message=(
                "Grok Build was not detected; its managed StackOS files can still be removed."
                if current.managed
                else "Grok Build was not detected; skipping MCP registration."
            ),
        )
    if current.status in {"config_unreadable", "registered_unsafe", "registered_unmanaged"}:
        return current
    capabilities = _run_grok(grok_bin, ["mcp", "add", "--help"], home=home)
    if capabilities.returncode != 0 or not all(
        flag in capabilities.stdout for flag in ("--scope", "--transport", "user")
    ):
        return replace(
            current,
            status="unsupported_host_version",
            message="Grok Build does not expose compatible user-scoped MCP commands.",
            ok=True,
            advisory=True,
            repair="Update Grok Build from https://docs.x.ai/build/overview, then rerun Repair.",
        )
    return current


def _inspect_assets(home: Path, *, server_name: str) -> HostMcpResult:
    from stackos.install import stackos_skill_is_current, stackos_skill_is_owned

    current = _inspect_config(home, server_name=server_name)
    skills_root = grok_home(home) / "skills"
    skill = skills_root / "stackos"
    receipt_owned = stackos_skill_is_owned(skills_root)
    skill_current = stackos_skill_is_current(skills_root, allow_extra=receipt_owned)
    owned = receipt_owned or skill_current
    if current.status in {"config_unreadable", "registered_unsafe", "registered_unmanaged"}:
        # Independent owned skill files remain removable even if a user has
        # replaced or damaged the native MCP entry.
        return replace(current, managed=owned)
    unsafe_skill_path = skills_root.is_symlink() or skill.is_symlink()
    if skill.is_dir() and not unsafe_skill_path and not receipt_owned:
        unsafe_skill_path = any(path.is_symlink() for path in skill.rglob("*"))
    if unsafe_skill_path or (skill.exists() and not current.managed and not owned):
        return replace(
            current,
            status="registered_unmanaged",
            message="Grok Build has an unmanaged StackOS skill; it was left unchanged.",
            ok=False,
            selected=bool(current.selected or owned),
            managed=bool(current.managed or owned),
            blocking=True,
            repair="Review Grok Build's skills/stackos folder before connecting StackOS.",
        )
    if current.status == "available_unregistered" and owned:
        current = replace(current, selected=True, managed=True)
    if current.status == "registered_current" and (not skill_current or not receipt_owned):
        return replace(
            current,
            status="registered_stale",
            message="Grok Build MCP is connected, but its StackOS skill installation needs repair.",
            ok=False,
            blocking=True,
            repair=REPAIR,
        )
    return current


def _inspect_config(home: Path, *, server_name: str) -> HostMcpResult:
    path = grok_home(home) / "config.toml"
    base = HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="available_unregistered",
        message="StackOS is not registered with Grok Build.",
        ok=True,
        available=True,
        advisory=True,
        selected=False,
        managed=False,
        config_path=str(path),
        repair=REPAIR,
    )
    try:
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return base
        servers = payload.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValueError("MCP servers must be a table")
        if server_name not in servers:
            return base
        entry = servers[server_name]
        if not isinstance(entry, dict):
            raise ValueError("MCP server must be a table")
    except (OSError, UnicodeError, ValueError):
        return replace(
            base,
            status="config_unreadable",
            message="Grok Build user MCP config could not be inspected safely.",
            ok=False,
            selected=True,
            blocking=True,
            repair="Check Grok Build's user config.toml, then rerun StackOS Repair.",
        )
    base = replace(base, selected=True, advisory=False, ok=False, blocking=True)
    if not _safe_entry(entry):
        return replace(
            base,
            status="registered_unsafe",
            message="Grok Build's StackOS entry has nonstandard transport or environment settings.",
            repair="Review the Grok Build StackOS entry before connecting it.",
            warnings=["unsafe Grok MCP entry redacted"],
        )
    stored = _stored_command(entry)
    expected = resolve_bridge_command(runtime=HOST_KEY)
    managed = is_stackos_bridge_command(stored)
    if stored == expected and entry.get("enabled", True) is True:
        return replace(
            base,
            status="registered_current",
            message="Grok Build StackOS MCP registration is healthy.",
            ok=True,
            blocking=False,
            managed=True,
            command=expected,
            repair=None,
        )
    return replace(
        base,
        status="registered_stale" if managed else "registered_unmanaged",
        message=(
            "Grok Build has a stale or disabled StackOS-owned MCP entry."
            if managed
            else "Grok Build has a stackos entry that StackOS does not own; it was left unchanged."
        ),
        managed=managed,
        repair=REPAIR if managed else "Review the Grok Build entry before connecting StackOS.",
    )


def register(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    current = inspect(home, server_name=server_name)
    if current.status != "available_unregistered" and not (
        current.status == "registered_stale" and current.managed
    ):
        return current
    token_error = token_preflight(home)
    if token_error:
        return replace(
            current, status="token_missing", message=token_error, ok=False, blocking=True
        )
    grok_bin = resolve_grok_bin(home)
    if grok_bin is None:
        return _absent()
    command = resolve_bridge_command(runtime=HOST_KEY)
    if current.command != command:
        added = _run_grok(
            grok_bin,
            ["mcp", "add", "--scope", "user", server_name, "--", *command],
            home=home,
        )
        verified = _inspect_config(home, server_name=server_name)
        if added.returncode != 0 or verified.status != "registered_current":
            return replace(
                current,
                status="register_failed",
                message="Grok Build MCP registration did not produce the expected user entry.",
                ok=False,
                blocking=True,
                repair=REPAIR,
            )
    from stackos.install import copy_managed_stackos_skill_to

    try:
        copy_managed_stackos_skill_to(
            grok_home(home) / "skills", adopt_existing=bool(current.managed)
        )
    except OSError:
        return replace(
            current,
            status="register_failed",
            message="Grok Build MCP is connected, but its StackOS skill could not be installed.",
            ok=False,
            blocking=True,
            selected=True,
            managed=True,
            repair=REPAIR,
        )
    verified = inspect(home, server_name=server_name)
    if verified.status != "registered_current":
        return verified
    return replace(
        verified,
        status="registered",
        message=f"Registered MCP '{server_name}' and the canonical StackOS skill with Grok Build.",
    )


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    from stackos.install import remove_managed_stackos_skill_from

    current = _inspect_assets(home, server_name=server_name)
    native = _inspect_config(home, server_name=server_name)
    if not current.managed and current.status != "available_unregistered":
        return current
    # Preserve proof for legacy copies until their cleanup succeeds. New copies
    # carry an independent receipt that also survives interrupted cleanup.
    if not remove_managed_stackos_skill_from(
        grok_home(home) / "skills", adopt_existing=bool(native.managed)
    ):
        return replace(
            current,
            status="remove_failed",
            message="Grok Build's managed StackOS skill could not be removed.",
            ok=False,
            blocking=True,
            repair="Check the StackOS skill ownership record and folder permissions, then retry.",
        )
    if native.status not in {"registered_current", "registered_stale", "available_unregistered"}:
        return replace(
            native,
            warnings=[
                *native.warnings,
                "Owned StackOS skill files were cleaned; native config was preserved.",
            ],
        )
    if native.managed:
        path = grok_home(home) / "config.toml"
        try:
            entry = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"][server_name]
            if not isinstance(entry, dict):
                raise ValueError("entry shape changed")
            command = _stored_command(entry)
            if not _safe_entry(entry) or not is_stackos_bridge_command(command):
                raise ValueError("entry ownership changed")
            ok, _error, _changed = remove_mcp_server(path, server_name, expected_entry=entry)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            return replace(
                current,
                status="remove_failed",
                message="Grok Build's StackOS config entry could not be removed safely.",
                ok=False,
                blocking=True,
                repair="Check config.toml permissions or concurrent changes, then retry uninstall.",
            )
    verified = _inspect_config(home, server_name=server_name)
    if verified.status != "available_unregistered":
        return replace(
            current,
            status="remove_failed",
            message="Grok Build's StackOS entry remains after cleanup.",
            ok=False,
            blocking=True,
            repair="Check Grok Build's config.toml, then retry uninstall.",
        )
    return replace(
        verified,
        status="removed",
        message="Removed Grok Build's StackOS registration and managed skill files.",
    )


def _stored_command(entry: dict[str, object]) -> list[str]:
    command = entry.get("command")
    args = entry.get("args", [])
    return (
        [command, *args]
        if isinstance(command, str)
        and isinstance(args, list)
        and all(isinstance(arg, str) for arg in args)
        else []
    )


def _safe_entry(entry: dict[str, object]) -> bool:
    return (
        not looks_secretish(json.dumps(entry, sort_keys=True, default=str))
        and not any(entry.get(key) for key in ("url", "headers", "env"))
        and entry.get("transport", "stdio") == "stdio"
    )


def _absent() -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="absent",
        message="Grok Build was not detected; skipping MCP registration.",
        ok=True,
        available=False,
        advisory=True,
    )


def _run_grok(
    grok_bin: str, args: Sequence[str], *, home: Path
) -> subprocess.CompletedProcess[str]:
    env = subprocess_env_for_cli(grok_bin)
    # Match the inspected scope, including explicit --home test/install contexts.
    env["GROK_HOME"] = str(grok_home(home))
    try:
        return subprocess.run(
            [grok_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env=env,
            cwd=home,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([grok_bin, *args], 124, "", "Grok Build CLI timed out.")
    except OSError:
        return subprocess.CompletedProcess([grok_bin, *args], 1, "", "Grok Build CLI failed.")
