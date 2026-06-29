"""Gemini CLI MCP lifecycle adapter."""

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

HOST_KEY = "gemini-cli"
SURFACE = "cli-config"
GEMINI_BIN_ENV = "STACKOS_GEMINI_BIN"
COMMON_GEMINI_CLI_CANDIDATES = (
    "~/.local/bin/gemini",
    "~/bin/gemini",
    "~/.npm-global/bin/gemini",
    "/opt/homebrew/bin/gemini",
    "/usr/local/bin/gemini",
)
GEMINI_COMPAT_WARNING = (
    "Gemini CLI support is compatibility-only while consumer Gemini CLI transitions."
)


def inspect(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    del home
    gemini_bin = resolve_gemini_bin()
    if gemini_bin is None:
        return _absent()
    command = resolve_bridge_command(runtime=HOST_KEY)
    listed = _run_gemini(gemini_bin, ["mcp", "list"])
    if listed.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="unsupported_host_version",
            message="Gemini CLI MCP status could not be inspected.",
            ok=True,
            available=True,
            advisory=True,
            repair="Check `gemini mcp --help`, then rerun `stackos install --mcp-only`.",
            warnings=[GEMINI_COMPAT_WARNING],
        )
    rows = _stackos_rows(listed.stdout, server_name)
    if not rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="available_unregistered",
            message="StackOS is not registered with Gemini CLI.",
            ok=True,
            available=True,
            advisory=True,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
            warnings=[GEMINI_COMPAT_WARNING],
        )
    if any(looks_secretish(row) for row in rows):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Gemini CLI has a StackOS MCP entry that appears to contain secret material.",
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install --mcp-only` or desktop Repair.",
            warnings=["unsafe Gemini MCP entry redacted"],
        )
    if any(command_line_mentions(command, row) for row in rows):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message="Gemini CLI StackOS MCP registration is healthy.",
            ok=True,
            available=True,
            command=command,
            warnings=[GEMINI_COMPAT_WARNING],
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_stale",
        message="Gemini CLI has a StackOS MCP entry, but it is not the local stdio bridge.",
        ok=False,
        available=True,
        blocking=True,
        repair="Run `stackos install --mcp-only` or desktop Repair.",
        warnings=["stale Gemini MCP entry redacted"],
    )


def register(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    gemini_bin = resolve_gemini_bin()
    if gemini_bin is None:
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
            repair="Run `stackos install` or desktop Repair before registering Gemini CLI MCP.",
        )
    current = inspect(home, server_name=server_name)
    if current.ok and current.status != "available_unregistered":
        return current
    if current.available and current.status != "available_unregistered":
        removed = _run_gemini(gemini_bin, ["mcp", "remove", server_name])
        if removed.returncode != 0:
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="remove_failed",
                message="Gemini CLI MCP registration failed while removing the stale user entry.",
                ok=False,
                available=True,
                blocking=True,
                repair=(f"Run `gemini mcp remove {server_name}`, then rerun StackOS Repair."),
            )
    command = resolve_bridge_command(runtime=HOST_KEY)
    added = _run_gemini(gemini_bin, ["mcp", "add", server_name, *command])
    if added.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="register_failed",
            message="Gemini CLI MCP registration failed.",
            ok=False,
            available=True,
            blocking=True,
            repair="Check `gemini mcp add --help`, then rerun `stackos install --mcp-only`.",
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered",
        message=f"Registered MCP '{server_name}' with Gemini CLI.",
        ok=True,
        available=True,
        command=command,
        warnings=[GEMINI_COMPAT_WARNING],
    )


def remove(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    del home
    gemini_bin = resolve_gemini_bin()
    if gemini_bin is None:
        return _absent(message="Gemini CLI not found; skipped Gemini MCP removal.")
    current = _run_gemini(gemini_bin, ["mcp", "list"])
    rows = _stackos_rows(current.stdout, server_name) if current.returncode == 0 else []
    if not rows:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="removed",
            message=f"MCP '{server_name}' not registered with Gemini CLI; nothing to remove.",
            ok=True,
            available=True,
            warnings=[GEMINI_COMPAT_WARNING],
        )
    removed = _run_gemini(gemini_bin, ["mcp", "remove", server_name])
    if removed.returncode != 0:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="remove_failed",
            message="Gemini CLI MCP removal failed.",
            ok=False,
            available=True,
            blocking=True,
            repair=f"Run `gemini mcp remove {server_name}` and retry uninstall.",
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="removed",
        message=f"Unregistered MCP '{server_name}' from Gemini CLI.",
        ok=True,
        available=True,
        warnings=[GEMINI_COMPAT_WARNING],
    )


def _absent(message: str = "Gemini CLI not on PATH; skipping MCP registration.") -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="absent",
        message=message,
        ok=True,
        available=False,
        advisory=True,
        repair=(
            "Install a supported Gemini/Google coding CLI, then run "
            "`stackos install --mcp-only` or desktop Repair."
        ),
        warnings=[GEMINI_COMPAT_WARNING],
    )


def resolve_gemini_bin(gemini_bin: str | None = None) -> str | None:
    return resolve_cli_bin(
        "gemini",
        explicit=gemini_bin,
        env_var=GEMINI_BIN_ENV,
        common_candidates=COMMON_GEMINI_CLI_CANDIDATES,
    )


def _run_gemini(gemini_bin: str, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [gemini_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
            env=subprocess_env_for_cli(gemini_bin),
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            [gemini_bin, *args],
            returncode=124,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr) or "Gemini CLI timed out.",
        )
    except Exception as exc:
        return subprocess.CompletedProcess(
            [gemini_bin, *args],
            returncode=1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )


def _stackos_rows(stdout: str, server_name: str) -> list[str]:
    return [
        line.strip() for line in stdout.splitlines() if output_row_matches_server(line, server_name)
    ]


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
