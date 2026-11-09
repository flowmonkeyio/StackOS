"""Claude Code MCP lifecycle adapter."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from stackos import claude_mcp
from stackos.host_mcp.agent_execution import ManagedProcessControl, execute_json_agent, json_object
from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    is_stackos_bridge_command,
    resolve_bridge_command,
)
from stackos.host_mcp.discovery import subprocess_env_for_cli
from stackos.host_mcp.result import (
    AgentRunResult,
    AgentSandbox,
    HostMcpResult,
    looks_secretish,
    usage_snapshot,
)

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
    process_control: ManagedProcessControl | None = None,
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
        command,
        project_dir,
        prompt,
        output_schema,
        "Claude Code",
        lambda stdout: _agent_result(stdout, native_version=_native_version(cli, project_dir)),
        **({"process_control": process_control} if process_control else {}),
    )


def _native_version(cli: str, project_dir: Path) -> str | None:
    """Optional evidence from the selected executable; never an execution gate."""
    try:
        process = subprocess.run(
            [cli, "--version"],
            cwd=project_dir,
            env=subprocess_env_for_cli(cli),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    return process.stdout if process.returncode == 0 and isinstance(process.stdout, str) else None


def _agent_result(stdout: str, *, native_version: str | None = None) -> AgentRunResult:
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
        usage=_agent_usage(value, native_version),
    )


def _agent_usage(value: dict[str, Any], native_version: str | None) -> dict[str, Any] | None:
    # Official cost-tracking contract: stable 2.1 releases before 277 report
    # per-call totals; 277+ restore session totals. Other versions are unproven.
    # The terminal JSON has no documented version field; trust only the probe.
    version = re.fullmatch(
        r"2\.1\.([0-9]{1,6})(?: \(Claude Code\))?", (native_version or "").strip()
    )
    if version is None:
        return None
    basis = "session" if int(version[1]) >= 277 else "invocation"
    # usage is narrower (main loop only); never mix it with modelUsage.
    models = value.get("modelUsage")
    if (
        not isinstance(models, dict)
        or not models
        or value.get("subtype") == "error_during_execution"
    ):
        return None
    inputs = outputs = 0
    cache_read = cache_write = 0
    reasoning: int | None = 0
    for row in models.values():
        if not isinstance(row, dict):
            return None
        counts: list[Any] = [
            row.get(k)
            for k in (
                "inputTokens",
                "cacheReadInputTokens",
                "cacheCreationInputTokens",
                "outputTokens",
            )
        ]
        if any(type(n) is not int or n < 0 for n in counts):
            return None
        inputs += sum(counts[:3])
        outputs += counts[3]
        cache_read += counts[1]
        cache_write += counts[2]
        # A partial model subtotal is not a full-scope reasoning measurement.
        thinking = row.get("thinkingTokens")
        if reasoning is not None and type(thinking) is int and 0 <= thinking <= counts[3]:
            reasoning += thinking
        else:
            reasoning = None
    # A versioned scope cannot reuse an older, unproven cumulative baseline.
    return usage_snapshot(
        "claude.model-usage.v2.1",
        basis,
        inputs,
        outputs,
        cache_read=cache_read,
        cache_write=cache_write,
        reasoning=reasoning,
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
