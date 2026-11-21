"""Antigravity 1.5.13 headless protocol; process ownership stays shared.

Contract: https://antigravity.google/docs/cli/headless/ (2026-11-21).
step_update usage is per step; result usage is cumulative within a session.
Neither documentation nor the bounded native probe establishes cross-process
resume accounting, so resumed usage remains unavailable, even with a baseline.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from stackos.host_mcp.adapters.antigravity import resolve_antigravity_bin
from stackos.host_mcp.agent_execution import (
    ManagedProcessControl,
    execute_json_agent,
    structured_prompt,
)
from stackos.host_mcp.result import AgentRunResult, AgentSandbox, usage_snapshot

# Bounds the whole native lifetime, including authentication and startup.
RUN_TIMEOUT_SECONDS = 900


def permission_error(diagnostic: str) -> str | None:
    if re.search(
        r"\bdenied\b|\bdenial\b|not permitted|soft[- ]den(?:ied|ial)|"
        r"permission[^\n]{0,120}(?:denied|required)|"
        r"(?:requires?|requiring)[^\n]{0,40}(?:approval|confirmation)|"
        r"(?:approval|confirmation)[^\n]{0,80}(?:denied|required)|"
        r"not allowed[^\n]{0,80}(?:headless|permission)",
        diagnostic,
        re.IGNORECASE,
    ):
        return "Antigravity soft-denied a required tool action; review native permissions locally."
    return None


def _usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return usage_snapshot(
        "antigravity.fresh-process",
        "invocation",
        value.get("input_tokens"),
        value.get("output_tokens"),
        value.get("total_tokens"),
        cache_read=value.get("cache_read_tokens"),
        reasoning=value.get("thinking_tokens"),
    )


def decode_result(
    text: str, *, resumed: bool = False, project_dir: Path | None = None
) -> AgentRunResult:
    session: str | None = None
    terminal: dict[str, Any] | None = None
    error: str | None = None
    steps: dict[int, dict[str, Any]] = {}
    usage = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("expected event object")
            kind = event.get("event")
            payload = event.get(kind) if isinstance(kind, str) else event
            if not isinstance(payload, dict):
                raise ValueError("invalid event payload")
            if terminal is not None:
                raise ValueError("output after terminal result")
            identity = payload.get("conversation_id", event.get("conversation_id"))
            if "conversation_id" in event and event["conversation_id"] != identity:
                raise ValueError("conflicting conversation identities")
            if identity is not None:
                if not isinstance(identity, str) or not identity.strip():
                    raise ValueError("invalid conversation identity")
                if session is not None and session != identity:
                    raise ValueError("conversation identity changed in native stream")
                session = identity
            if kind == "init":
                if payload.get("permission_mode") != "request-review":
                    raise ValueError("unexpected native permission mode")
                if project_dir is not None and payload.get("cwd") != str(project_dir.resolve()):
                    raise ValueError("unexpected native working directory")
            elif kind == "step_update":
                info = payload.get("tool_info", {})
                if isinstance(info, dict) and permission_error(str(info.get("error", ""))):
                    error = permission_error(str(info["error"]))
                index = payload.get("step_index")
                if (
                    not resumed
                    and type(index) is int
                    and index >= 0
                    and payload.get("state") == "DONE"
                ):
                    counts = _usage(payload.get("usage"))
                    if counts is not None:
                        # Repeated state/delta events describe one step, not new work.
                        steps[index] = counts["snapshot"]
            elif kind == "result" or kind is None:
                terminal = payload
            elif kind != "init":
                raise ValueError("unsupported native event")
        except (ValueError, TypeError) as exc:
            detail = (
                "No credentials for selected model; authenticate Antigravity locally."
                if re.search(r"credentials|authentication|not authenticated", line, re.IGNORECASE)
                else str(exc)
            )
            error = error or f"Invalid Antigravity result: {detail}"
    if not resumed:
        if terminal is not None and terminal.get("conversation_id") == session:
            usage = _usage(terminal.get("usage"))
        if usage is None and steps:
            sums = {
                key: sum(row[key] for row in steps.values())
                if all(row[key] is not None for row in steps.values())
                else None
                for key in ("input", "output", "total", "cache_read", "reasoning")
            }
            usage = usage_snapshot(
                "antigravity.fresh-process",
                "invocation",
                sums["input"],
                sums["output"],
                sums["total"],
                cache_read=sums["cache_read"],
                reasoning=sums["reasoning"],
            )
    if terminal is None:
        error = error or "Invalid Antigravity result: missing terminal result."
    elif terminal.get("status") != "SUCCESS" or terminal.get("error"):
        error = (
            error
            or f"Antigravity terminal failure: {terminal.get('error') or terminal.get('status')}"
        )
    elif not terminal.get("conversation_id"):
        error = error or "Invalid Antigravity result: missing terminal conversation identity."
    if session is None:
        error = error or "Invalid Antigravity result: missing conversation identity."
    value = terminal.get("structured_output") if terminal is not None else None
    if not isinstance(value, dict):
        error = error or "Invalid Antigravity result: missing native structured_output."
        value = None
    return AgentRunResult(0, thread_id=session, result=value, error=error, usage=usage)


def run_agent(
    project_dir: Path,
    instructions: str,
    prompt: str,
    output_schema: dict[str, Any],
    *,
    sandbox: AgentSandbox = "workspace-write",
    model: str | None = None,
    model_reasoning_effort: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
    resume_thread_id: str | None = None,
    process_control: ManagedProcessControl | None = None,
) -> AgentRunResult:
    if sandbox not in {"workspace-write", "normal"} or provider is not None or profile is not None:
        return AgentRunResult(None, error="Unsupported Antigravity permissions/provider/profile.")
    cli = resolve_antigravity_bin()
    if cli is None:
        return AgentRunResult(None, error="No runnable Antigravity CLI (agy) was found.")
    # Pin native permission policy, overriding a saved always-proceed preset.
    # accept-edits permits workspace file edits; normal retains request-review.
    # Neither is an enforced filesystem sandbox or a strict read-only reviewer.
    command = [
        cli,
        "--print",
        structured_prompt(instructions + "\n\n" + prompt, output_schema),
        "--output-format",
        "stream-json",
        "--json-schema",
        json.dumps(output_schema),
        "--disable-slash-commands",
        "--print-timeout",
        f"{RUN_TIMEOUT_SECONDS}s",
        "--settings",
        json.dumps(
            {
                "agentMode": "accept-edits" if sandbox == "workspace-write" else "",
                "toolPermission": "request-review",
            }
        ),
    ]
    if model is not None:
        command += ["--model", model]
    if model_reasoning_effort is not None:
        command += ["--effort", model_reasoning_effort]
    if resume_thread_id is not None:
        command += ["--conversation", resume_thread_id]
    result = execute_json_agent(
        command,
        project_dir,
        "",
        output_schema,
        "Antigravity",
        lambda text: decode_result(
            text, resumed=resume_thread_id is not None, project_dir=project_dir
        ),
        process_control=process_control,
        timeout=RUN_TIMEOUT_SECONDS,
        stderr_error=permission_error,
    )
    # Plain-text auth diagnostics are useful, but cannot supply structured results.
    if result.error and result.result is not None:
        return replace(result, result=None)
    return result
