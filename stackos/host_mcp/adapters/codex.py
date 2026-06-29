"""Codex MCP lifecycle adapter."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    command_line_mentions,
    output_row_matches_server,
    resolve_bridge_command,
    token_preflight,
)
from stackos.host_mcp.discovery import resolve_cli_bin, subprocess_env_for_cli
from stackos.host_mcp.result import HostMcpResult, looks_secretish

HOST_KEY = "codex"
SURFACE = "shared-config"
CODEX_BIN_ENV = "STACKOS_CODEX_BIN"
COMMON_CODEX_CLI_CANDIDATES = (
    "~/.local/bin/codex",
    "~/bin/codex",
    "~/.npm-global/bin/codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
)


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    del home
    codex_bin = resolve_codex_bin()
    if codex_bin is None:
        return _absent()
    command = resolve_bridge_command(runtime=HOST_KEY)
    listed = _run_codex(codex_bin, ["mcp", "list"])
    if listed.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="unsupported_host_version",
            message="Codex MCP status could not be inspected.",
            ok=False,
            available=True,
            blocking=True,
            repair="Check `codex mcp --help`, then rerun `stackos install --mcp-only`.",
        )
    rows = _stackos_rows(listed.stdout, server_name)
    if not rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="available_unregistered",
            message="StackOS is not registered with Codex.",
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
        )
    if any(looks_secretish(row) for row in rows):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Codex has a StackOS MCP entry that appears to contain secret material.",
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
            warnings=["unsafe Codex MCP entry redacted"],
        )
    bridge_rows = [row for row in rows if _line_is_bridge(row, command, server_name)]
    if bridge_rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message="Codex StackOS MCP registration is healthy.",
            ok=True,
            available=True,
            command=command,
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_stale",
        message="Codex has a StackOS MCP entry, but it is not the local stdio bridge.",
        ok=False,
        available=True,
        blocking=True,
        repair="Run `stackos install --mcp-only` or desktop Repair.",
        warnings=["stale Codex MCP entry redacted"],
    )


def register(
    home: Path,
    *,
    server_name: str = MCP_SERVER_NAME,
    force: bool = False,
) -> HostMcpResult:
    codex_bin = resolve_codex_bin()
    if codex_bin is None:
        return _absent()
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
            repair="Run `stackos install` or desktop Repair before registering Codex MCP.",
        )

    current = inspect(home, server_name=server_name)
    if current.ok and not force:
        command = resolve_bridge_command(runtime=HOST_KEY)
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message=f"MCP '{server_name}' already registered with Codex CLI",
            ok=True,
            available=True,
            command=command,
        )
    if current.available and current.status != "available_unregistered":
        removed = _run_codex(codex_bin, ["mcp", "remove", server_name])
        if removed.returncode != 0:
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="remove_failed",
                message="Codex MCP registration failed while removing the stale entry.",
                ok=False,
                available=True,
                blocking=True,
                repair=f"Run `codex mcp remove {server_name}`, then rerun StackOS Repair.",
            )
    command = resolve_bridge_command(runtime=HOST_KEY)
    added = _run_codex(codex_bin, ["mcp", "add", server_name, "--", *command])
    if added.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="register_failed",
            message="Codex MCP registration failed.",
            ok=False,
            available=True,
            blocking=True,
            repair="Check `codex mcp add --help`, then rerun `stackos install --mcp-only`.",
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered",
        message=f"Registered MCP '{server_name}' with Codex.",
        ok=True,
        available=True,
        command=command,
    )


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    del home
    codex_bin = resolve_codex_bin()
    if codex_bin is None:
        return _absent(message="Codex CLI not found; skipped Codex MCP removal.")
    current = _run_codex(codex_bin, ["mcp", "list"])
    rows = _stackos_rows(current.stdout, server_name) if current.returncode == 0 else []
    if not rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="removed",
            message=f"MCP '{server_name}' not registered with Codex; nothing to remove.",
            ok=True,
            available=True,
        )
    removed = _run_codex(codex_bin, ["mcp", "remove", server_name])
    if removed.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="remove_failed",
            message="Codex MCP removal failed.",
            ok=False,
            available=True,
            blocking=True,
            repair=f"Run `codex mcp remove {server_name}` and retry uninstall.",
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="removed",
        message=f"Unregistered MCP '{server_name}' from Codex.",
        ok=True,
        available=True,
    )


def _absent(
    message: str = "Codex CLI not on PATH; skipping Codex MCP registration.",
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="absent",
        message=message,
        ok=True,
        available=False,
        advisory=True,
        repair="Install Codex, then run `stackos install --mcp-only` or desktop Repair.",
    )


def resolve_codex_bin(codex_bin: str | None = None) -> str | None:
    return resolve_cli_bin(
        "codex",
        explicit=codex_bin,
        env_var=CODEX_BIN_ENV,
        common_candidates=COMMON_CODEX_CLI_CANDIDATES,
    )


def _run_codex(codex_bin: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [codex_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env=subprocess_env_for_cli(codex_bin),
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            [codex_bin, *args],
            returncode=124,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr) or "Codex CLI timed out.",
        )
    except Exception as exc:
        return subprocess.CompletedProcess(
            [codex_bin, *args],
            returncode=1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )


def _stackos_rows(stdout: str, server_name: str) -> list[str]:
    return [
        line.strip() for line in stdout.splitlines() if output_row_matches_server(line, server_name)
    ]


def _line_is_bridge(
    line: str,
    command: Sequence[str],
    server_name: str = MCP_SERVER_NAME,
) -> bool:
    normalized = line.strip()
    if not output_row_matches_server(normalized, server_name):
        return False
    lowered = normalized.lower()
    forbidden = ("/mcp", "--url", "--bearer-token-env-var", "authorization", "bearer")
    if any(token in lowered for token in forbidden):
        return False
    return command_line_mentions(command, normalized)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
