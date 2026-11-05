"""Claude Code MCP lifecycle adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stackos import claude_mcp
from stackos.host_mcp.agent_execution import execute_json_agent, json_object
from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    is_stackos_bridge_command,
    resolve_bridge_command,
)
from stackos.host_mcp.result import AgentRunResult, AgentSandbox, HostMcpResult, looks_secretish

HOST_KEY = "claude-code"
SURFACE = "cli"


def run_agent(
    project_dir: Path,
    instructions: str,
    prompt: str,
    output_schema: dict[str, Any],
    *,
    sandbox: AgentSandbox = "workspace-write",
    model: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
    resume_thread_id: str | None = None,
) -> AgentRunResult:
    cli = claude_mcp.resolve_claude_bin()
    if cli is None:
        return AgentRunResult(None, error="No Claude Code CLI was found by host discovery.")
    command = [
        cli,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(output_schema),
        "--append-system-prompt",
        instructions,
        "--permission-mode",
        "auto" if sandbox == "workspace-write" else "dontAsk",
    ]
    if sandbox == "read-only":
        command += ["--tools", "Read,Glob,Grep", "--disallowedTools", "mcp__*"]
    if model is not None:
        command += ["--model", model]
    if resume_thread_id is not None:
        command += ["--resume", resume_thread_id]
    return execute_json_agent(
        command, project_dir, prompt, output_schema, "Claude Code", _agent_result
    )


def _agent_result(stdout: str) -> AgentRunResult:
    value = json_object(stdout)
    error = None
    if value.get("is_error") or str(value.get("subtype", "")).startswith("error"):
        error = str(value.get("errors") or value.get("result") or "Claude Code failed.")
    elif value.get("permission_denials"):
        error = "Claude Code denied required tool permissions."
    return AgentRunResult(
        0,
        thread_id=value.get("session_id"),
        result=value.get("structured_output"),
        error=error,
    )


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
        "unsafe": "registered_unsafe",
        "registration_failed": "register_failed",
        "token_missing": "token_missing",
    }.get(result.status, "register_failed")
    selected = result.status in {"healthy", "registered", "stale", "unsafe"}
    managed = result.status in {"healthy", "registered"} or (
        result.status == "stale" and is_stackos_bridge_command(result.command)
    )
    if result.status == "unsafe":
        managed = False
    elif result.status == "stale" and not managed:
        status = "registered_unsafe" if looks_secretish(result.command) else "registered_unmanaged"
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
        selected=selected,
        managed=managed,
    )
