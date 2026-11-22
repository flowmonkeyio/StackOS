"""Antigravity 1.5.14 headless protocol; process ownership stays shared.

Contract: https://antigravity.google/docs/cli/headless/ (2026-11-21).
step_update usage is per step; result usage is cumulative within a session.
The 1.5.14 capture establishes that native input/total exclude cache reads.
No exact-session native pre-invocation baseline is read, so resumed usage remains
unavailable. Prior StackOS receipts and the first post-work event cannot supply it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from stackos.host_mcp.adapters.antigravity import resolve_antigravity_bin
from stackos.host_mcp.agent_execution import (
    ManagedProcessControl,
    execute_json_agent,
    structured_prompt,
)
from stackos.host_mcp.bridge import default_home
from stackos.host_mcp.json_config import read_json_object
from stackos.host_mcp.result import AgentRunResult, AgentSandbox, usage_snapshot

# Bounds the whole native lifetime, including authentication and startup.
RUN_TIMEOUT_SECONDS = 900

# Native permission resources, not operation grants. The bridge still checks
# project scope, tool visibility, active step grants and daemon-side approvals.
STACKOS_TOOLS = (
    "workspace_startSession",
    "workspace_resolve",
    "toolbox_describe",
    "toolbox_call",
    "browser_runtime_status",
    "browser_profile_list",
    "browser_profile_create",
    "browser_session_list",
    "browser_session_start",
    "browser_session_status",
    "browser_session_stop",
)


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
    # Validate the native arithmetic before translating to StackOS full input.
    # Cache is a separate bucket in this headless contract, not an input subset.
    uncached, output, total, cache = (
        value.get(key)
        for key in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens")
    )
    if (
        type(uncached) is not int
        or type(output) is not int
        or type(total) is not int
        or type(cache) is not int
    ):
        return None
    if any(n < 0 for n in (uncached, output, total, cache)):
        return None
    if total != uncached + output:
        return None
    return usage_snapshot(
        "antigravity.headless",
        "invocation",
        uncached + cache,
        output,
        total + cache,
        cache_read=cache,
        reasoning=value.get("thinking_tokens"),
    )


class _Stream:
    """One native stream decoder shared by live observations and final validation."""

    def __init__(self, *, resumed: bool = False, project_dir: Path | None = None):
        self.resumed = resumed
        self.project_dir = project_dir
        self.session: str | None = None
        self.terminal: dict[str, Any] | None = None
        self.error: str | None = None
        self.steps: dict[int, dict[str, Any]] = {}
        self.initialized = False

    def feed(self, line: str) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("expected event object")
            kind = event.get("event")
            payload = event.get(kind) if isinstance(kind, str) else event
            if not isinstance(payload, dict):
                raise ValueError("invalid event payload")
            if self.terminal is not None:
                raise ValueError("output after terminal result")
            identity = payload.get("conversation_id", event.get("conversation_id"))
            if "conversation_id" in event and event["conversation_id"] != identity:
                raise ValueError("conflicting conversation identities")
            if identity is not None:
                if not isinstance(identity, str) or not identity.strip():
                    raise ValueError("invalid conversation identity")
                if self.session is not None and self.session != identity:
                    raise ValueError("conversation identity changed in native stream")
                self.session = identity
            if kind == "init":
                if self.initialized:
                    raise ValueError("duplicate init event")
                if payload.get("permission_mode") not in {"request-review", "proceed-in-sandbox"}:
                    raise ValueError("unexpected native permission mode")
                if self.project_dir is not None and payload.get("cwd") != str(
                    self.project_dir.resolve()
                ):
                    raise ValueError("unexpected native working directory")
                if self.session is None:
                    raise ValueError("missing init conversation identity")
                self.initialized = True
            elif kind == "step_update":
                info = payload.get("tool_info", {})
                if isinstance(info, dict) and permission_error(str(info.get("error", ""))):
                    self.error = permission_error(str(info["error"]))
                index = payload.get("step_index")
                if (
                    not self.resumed
                    and type(index) is int
                    and index >= 0
                    and payload.get("state") == "DONE"
                ):
                    counts = _usage(payload.get("usage"))
                    if counts is not None:
                        # Repeated state/delta events describe one step, not new work.
                        self.steps[index] = counts["snapshot"]
            elif kind == "result" or kind is None:
                self.terminal = payload
            elif kind != "init":
                raise ValueError("unsupported native event")
        except (ValueError, TypeError) as exc:
            detail = (
                "No credentials for selected model; authenticate Antigravity locally."
                if re.search(r"credentials|authentication|not authenticated", line, re.IGNORECASE)
                else str(exc)
            )
            self.error = self.error or f"Invalid Antigravity result: {detail}"

    def usage(self) -> dict[str, Any] | None:
        if self.resumed:
            return None
        if self.terminal is not None and self.terminal.get("conversation_id") == self.session:
            usage = _usage(self.terminal.get("usage"))
            if usage is not None:
                return usage
        if self.steps:
            sums = {
                key: sum(row[key] for row in self.steps.values())
                if all(row[key] is not None for row in self.steps.values())
                else None
                for key in ("input", "output", "total", "cache_read", "reasoning")
            }
            return usage_snapshot(
                "antigravity.headless",
                "invocation",
                sums["input"],
                sums["output"],
                sums["total"],
                cache_read=sums["cache_read"],
                reasoning=sums["reasoning"],
            )
        return None

    def result(self) -> AgentRunResult:
        terminal, error = self.terminal, self.error
        if terminal is None:
            error = error or "Invalid Antigravity result: missing terminal result."
        elif terminal.get("status") != "SUCCESS" or terminal.get("error"):
            detail = terminal.get("error") or terminal.get("status")
            error = error or f"Antigravity terminal failure: {detail}"
        elif not terminal.get("conversation_id"):
            error = error or "Invalid Antigravity result: missing terminal conversation identity."
        if self.session is None:
            error = error or "Invalid Antigravity result: missing conversation identity."
        value = terminal.get("structured_output") if terminal is not None else None
        if not isinstance(value, dict):
            error = error or "Invalid Antigravity result: missing native structured_output."
            value = None
        return AgentRunResult(
            0, thread_id=self.session, result=value, error=error, usage=self.usage()
        )


def decode_result(
    text: str, *, resumed: bool = False, project_dir: Path | None = None
) -> AgentRunResult:
    stream = _Stream(resumed=resumed, project_dir=project_dir)
    for line in text.splitlines():
        stream.feed(line)
    return stream.result()


def _session_settings(sandbox: AgentSandbox, home: Path) -> dict[str, Any]:
    # --settings overrides object-valued keys too. Carry existing approval/deny
    # rules explicitly rather than replacing them with our MCP allow list.
    loaded = read_json_object(home / ".gemini/antigravity-cli/settings.json")
    if not loaded.ok:
        raise ValueError("Cannot safely read Antigravity permission settings.")
    permissions = loaded.data.get("permissions", {})
    if not isinstance(permissions, dict) or any(
        not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules)
        for rules in permissions.values()
    ):
        raise ValueError("Invalid Antigravity permission rules; review native settings locally.")
    if loaded.data.get("toolPermission") == "strict":
        raise ValueError("Antigravity strict tool approval requires an interactive native session.")
    return {
        "agentMode": "accept-edits" if sandbox == "workspace-write" else "",
        "toolPermission": "proceed-in-sandbox",
        "enableTerminalSandbox": True,
        "allowNonWorkspaceAccess": False,
        "permissions": {
            **permissions,
            "allow": list(
                dict.fromkeys(
                    [
                        *permissions.get("allow", []),
                        *[f"mcp(stackos/{name})" for name in STACKOS_TOOLS],
                    ]
                )
            ),
        },
    }


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
    on_session: Callable[[str], None] | None = None,
    on_usage: Callable[[dict[str, Any]], None] | None = None,
) -> AgentRunResult:
    if sandbox not in {"workspace-write", "normal"} or provider is not None or profile is not None:
        return AgentRunResult(None, error="Unsupported Antigravity permissions/provider/profile.")
    try:
        settings = _session_settings(sandbox, default_home())
    except ValueError as exc:
        return AgentRunResult(None, error=str(exc))
    cli = resolve_antigravity_bin()
    if cli is None:
        return AgentRunResult(None, error="No runnable Antigravity CLI (agy) was found.")
    # Pin native permission policy, overriding a saved always-proceed preset.
    # accept-edits permits workspace file edits; normal keeps native diff review.
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
        json.dumps(settings),
    ]
    if model is not None:
        command += ["--model", model]
    if model_reasoning_effort is not None:
        command += ["--effort", model_reasoning_effort]
    if resume_thread_id is not None:
        command += ["--conversation", resume_thread_id]
    stream = _Stream(resumed=resume_thread_id is not None, project_dir=project_dir)
    attached = False
    last_usage = None

    def observe(line: str) -> None:
        nonlocal attached, last_usage
        stream.feed(line)
        if stream.error or not stream.initialized:
            return
        if not attached:
            if on_session is not None:
                assert stream.session is not None
                on_session(stream.session)
            attached = True
        usage = stream.usage()
        if usage is not None and usage != last_usage and on_usage is not None:
            on_usage(usage)
            last_usage = usage

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
        on_output_line=observe if on_session is not None or on_usage is not None else None,
    )
    # Plain-text auth diagnostics are useful, but cannot supply structured results.
    if result.error and result.result is not None:
        return replace(result, result=None)
    return result
