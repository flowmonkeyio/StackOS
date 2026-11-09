"""Shared host MCP registration and project-agent execution service."""

from __future__ import annotations

import atexit
import logging
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Literal, Protocol
from uuid import uuid4

from stackos.host_mcp.adapters import claude_code, claude_desktop, codex, gemini_cli, grok, hermes
from stackos.host_mcp.agent_execution import (
    ManagedProcessControl,
    managed_launch_lock,
)
from stackos.host_mcp.bridge import MCP_SERVER_NAME, default_home, token_preflight
from stackos.host_mcp.progress import CodexCounterReader
from stackos.host_mcp.result import (
    AgentCapabilities,
    AgentRunResult,
    AgentSandbox,
    HostMcpConnectionState,
    HostMcpResult,
    HostMcpSetupPolicy,
    normalize_usage,
)

Action = Literal["inspect", "register", "repair", "remove"]
AdapterFn = Callable[[Path], HostMcpResult]
ForceAdapterFn = Callable[[Path, bool], HostMcpResult]
ProfileAdapterFn = Callable[[Path, str | None], HostMcpResult]


@dataclass(frozen=True)
class AgentExecutionScope:
    """Safe attribution supplied by the caller, never inferred from tracker status."""

    project_id: int
    task_key: str
    ticket_key: str
    agent: str
    attempt: int | None = None
    phase: str | None = None


@dataclass
class ActiveInvocation:
    invocation_id: str
    scope: AgentExecutionScope
    host_key: str
    model: str | None
    started_at: str
    previous_usage: Any = None
    resumed: bool = False
    session_id: str | None = None
    reader: CodexCounterReader | None = None
    usage: dict[str, Any] | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=120))
    measured_at: str | None = None
    finished: bool = False
    process_control: ManagedProcessControl = field(default_factory=ManagedProcessControl)
    retired: Event = field(default_factory=Event)

    def projection(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "agent": self.scope.agent,
            "host_key": self.host_key,
            "model": self.model,
            "started_at": self.started_at,
            "ticket_key": self.scope.ticket_key,
            "live_usage": {
                "usage": {
                    "unit": "tokens",
                    "status": "measured",
                    "measured": deepcopy(self.usage["measured"]),
                },
                "measured_at": self.measured_at,
                "samples": deepcopy(list(self.samples)),
            }
            if self.usage is not None
            else None,
        }


# One invocation registry owns identity, reader and bounded provisional counters.
# Neither UI subscriptions nor tracker reads start collection.
_active_invocations: dict[str, ActiveInvocation] = {}
_active_invocations_lock = RLock()
_collector: Thread | None = None
_collector_wake = Event()


def cancel_task_invocations(project_id: int, task_key: str) -> list[ActiveInvocation]:
    """Fence exact registered calls while the caller holds the launch boundary."""
    with managed_launch_lock, _active_invocations_lock:
        calls = [
            call
            for call in _active_invocations.values()
            if call.scope.project_id == project_id
            and call.scope.task_key == task_key
            and not call.finished
        ]
        for call in calls:
            call.process_control.cancelled.set()
        return calls


def _sample_invocations(now: datetime | None = None) -> None:
    cutoff = (now or datetime.now(UTC)).timestamp() - 600
    with _active_invocations_lock:
        for call in _active_invocations.values():
            if call.finished:
                continue
            while (
                call.samples and datetime.fromisoformat(call.samples[0]["at"]).timestamp() < cutoff
            ):
                call.samples.popleft()
            if call.reader is None:
                continue
            try:
                sample = call.reader.read()
            except (OSError, ValueError):
                call.reader.close()
                call.reader = None
                continue
            if sample is None:
                continue
            stamp, raw = sample
            measured = datetime.fromisoformat(stamp)
            if measured < datetime.fromisoformat(call.started_at) or (
                call.measured_at and measured <= datetime.fromisoformat(call.measured_at)
            ):
                continue
            usage = normalize_usage(
                raw, session_id=call.session_id, resumed=call.resumed, previous=call.previous_usage
            )
            if usage["status"] != "measured":
                continue
            call.usage = usage
            call.measured_at = stamp
            if measured.timestamp() >= cutoff:
                call.samples.append({"at": stamp, **usage["measured"]})


def _collect() -> None:
    global _collector
    while True:
        _collector_wake.wait(5)
        _collector_wake.clear()
        with _active_invocations_lock:
            if not any(not call.finished for call in _active_invocations.values()):
                _collector = None
                return
        _sample_invocations()


def _register(call: ActiveInvocation) -> None:
    global _collector
    with _active_invocations_lock:
        _active_invocations[call.invocation_id] = call
        if _collector is None:
            _collector = Thread(target=_collect, name="stackos-agent-usage", daemon=True)
            _collector.start()


def _unregister(call: ActiveInvocation) -> None:
    with _active_invocations_lock:
        _active_invocations.pop(call.invocation_id, None)
        if call.reader:
            call.reader.close()
            call.reader = None
        call.samples.clear()
        call.usage = None
        call.previous_usage = None
        call.session_id = None
        if not _active_invocations:
            _collector_wake.set()
        call.retired.set()


def _finish(
    call: ActiveInvocation, result: AgentRunResult | None, resume_thread_id: str | None = None
) -> None:
    """End native observation; hold only its snapshot for the synchronous receipt handoff."""
    with _active_invocations_lock:
        if result is not None and (
            resume_thread_id is None or result.thread_id == resume_thread_id
        ):
            final = normalize_usage(
                result.usage,
                session_id=result.thread_id,
                resumed=call.resumed,
                previous=call.previous_usage,
            )
            if final["status"] == "measured":
                if (
                    not call.samples
                    or call.usage is None
                    or final["measured"] != call.usage["measured"]
                ):
                    call.measured_at = datetime.now(UTC).isoformat()
                    call.samples.append({"at": call.measured_at, **final["measured"]})
                call.usage = final
        call.finished = True
        if call.reader:
            call.reader.close()
            call.reader = None
        # Keep only the bounded observation through the synchronous receipt
        # handoff. _unregister releases it even if notification fails.
        call.previous_usage = None
        call.session_id = None
        if not any(not other.finished for other in _active_invocations.values()):
            _collector_wake.set()


def shutdown_usage() -> None:
    with _active_invocations_lock:
        for call in list(_active_invocations.values()):
            _unregister(call)


atexit.register(shutdown_usage)


def usage_executions(project_id: int | None = None) -> list[dict[str, Any]]:
    """Safe transient transport state; no native I/O or durable samples."""
    with _active_invocations_lock:
        return [
            {
                "project_id": call.scope.project_id,
                "task_key": call.scope.task_key,
                **call.projection(),
            }
            for call in _active_invocations.values()
            if not call.finished and (project_id is None or call.scope.project_id == project_id)
        ]


type ProvisionalUsage = dict[tuple[int, str], list[tuple[int | None, str | None, dict[str, Any]]]]


def provisional_usage(project_id: int | None = None) -> ProvisionalUsage:
    """Copy bounded telemetry before SQL; never retain native readers."""
    with _active_invocations_lock:
        snapshot: ProvisionalUsage = {}
        for call in _active_invocations.values():
            if call.usage is not None and (
                project_id is None or call.scope.project_id == project_id
            ):
                snapshot.setdefault((call.scope.project_id, call.scope.ticket_key), []).append(
                    (
                        call.scope.attempt,
                        call.scope.phase,
                        {**deepcopy(call.usage), "execution": call.projection()},
                    )
                )
        return snapshot


def active_execution(
    project_id: int, task_key: str, ticket_key: str | None = None
) -> dict[str, Any] | None:
    """Return the latest actual call in this scope (automatic task calls are serial)."""
    with _active_invocations_lock:
        matches = [
            call
            for call in _active_invocations.values()
            if call.scope.project_id == project_id
            and call.scope.task_key == task_key
            and not call.finished
            and (ticket_key is None or call.scope.ticket_key == ticket_key)
        ]
        return max(matches, key=lambda call: call.started_at).projection() if matches else None


def _notify_execution(
    callback: Callable[[ActiveInvocation, bool], None] | None,
    call: ActiveInvocation,
    active: bool,
) -> None:
    if callback is not None:
        try:
            callback(call, active)
        except Exception:
            # Observability must not change native execution/result semantics.
            logging.getLogger(__name__).exception("Could not notify agent execution transition")


class HostMcpAdapter(Protocol):
    HOST_KEY: str

    def inspect(self, home: Path) -> HostMcpResult: ...

    def register(self, home: Path) -> HostMcpResult: ...

    def remove(self, home: Path) -> HostMcpResult: ...


class AgentRunner(Protocol):
    def __call__(
        self,
        project_dir: Path,
        instructions: str,
        prompt: str,
        output_schema: dict[str, Any],
        *,
        sandbox: AgentSandbox,
        model: str | None = None,
        provider: str | None = None,
        profile: str | None = None,
        resume_thread_id: str | None = None,
    ) -> AgentRunResult: ...


@dataclass(frozen=True)
class HostMcpPolicy:
    host_key: str
    display_name: str
    setup_policy: HostMcpSetupPolicy
    agent_execution: AgentCapabilities = field(default_factory=AgentCapabilities)


@dataclass(frozen=True)
class HostMcpDefinition:
    adapter: HostMcpAdapter
    policy: HostMcpPolicy
    repair: AdapterFn | None = None
    force_register: ForceAdapterFn | None = None
    inspect_profile: ProfileAdapterFn | None = None
    register_profile: ProfileAdapterFn | None = None
    remove_profile: ProfileAdapterFn | None = None
    run_agent: AgentRunner | None = None


@dataclass(frozen=True)
class HostMcpAggregate:
    ok: bool
    results: list[HostMcpResult]

    @property
    def blocking_results(self) -> list[HostMcpResult]:
        return [result for result in self.results if result.blocking]

    def to_info(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "hosts": [result.to_info() for result in self.results],
            "blocking_hosts": [result.host_key for result in self.blocking_results],
        }

    def summary_lines(self) -> list[str]:
        return [
            f"{result.host_key}: {result.message}"
            + (f" Repair: {result.repair}" if result.repair and not result.ok else "")
            for result in self.results
        ]


def run_agent(
    host_key: str,
    project_dir: str | Path,
    instructions: str,
    prompt: str,
    output_schema: dict[str, Any],
    *,
    sandbox: AgentSandbox = "workspace-write",
    model: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
    resume_thread_id: str | None = None,
    execution_scope: AgentExecutionScope | None = None,
    on_execution_change: Callable[[ActiveInvocation, bool], None] | None = None,
    previous_usage: Any = None,
    mcp_config_ref: dict[str, str] | None = None,
    launch_allowed: Callable[[], bool] | None = None,
) -> AgentRunResult:
    """Run a project agent through the same host registry used by Overview."""
    if not instructions.strip():
        return AgentRunResult(None, error="Project-agent instructions must not be empty.")
    if mcp_config_ref is not None and host_key != "codex":
        return AgentRunResult(None, error="Task MCP configuration is unsupported by this host.")
    if resume_thread_id is not None and (
        not isinstance(resume_thread_id, str)
        or not resume_thread_id.strip()
        or resume_thread_id != resume_thread_id.strip()
        or resume_thread_id.startswith("-")
        or resume_thread_id.lower() in {"latest", "last"}
    ):
        return AgentRunResult(None, error="An exact native session ID is required for resume.")
    error = agent_selection_error(
        host_key,
        sandbox=sandbox,
        model=model,
        provider=provider,
        profile=profile,
    )
    if error:
        return AgentRunResult(None, error=error)
    definition = _definition_by_key(host_key)
    if definition is None or definition.run_agent is None:
        return AgentRunResult(None, error=f"Host {host_key!r} does not support agent execution.")
    call = (
        ActiveInvocation(
            invocation_id=uuid4().hex,
            scope=execution_scope,
            host_key=host_key,
            model=model,
            started_at=datetime.now(UTC).isoformat(),
            previous_usage=deepcopy(previous_usage),
            resumed=resume_thread_id is not None,
            process_control=ManagedProcessControl(launch_allowed),
        )
        if execution_scope is not None
        else None
    )
    progress_kwargs: dict[str, Any] = {}
    if mcp_config_ref is not None:
        progress_kwargs["mcp_config_ref"] = mcp_config_ref
    result: AgentRunResult | None = None
    registered = False
    if call is not None and host_key == "codex":
        progress_kwargs["on_session"] = lambda session_id: _attach_session(
            call, session_id, Path(project_dir), resume_thread_id
        )
    if call is not None:
        progress_kwargs["process_control"] = call.process_control
    try:
        if call is not None:
            with managed_launch_lock:
                if launch_allowed is not None and not launch_allowed():
                    return AgentRunResult(None, error="Invocation cancelled before registration")
                _register(call)
                registered = True
                _notify_execution(on_execution_change, call, True)
        result = definition.run_agent(
            Path(project_dir).expanduser().resolve(),
            instructions,
            prompt,
            output_schema,
            sandbox=sandbox,
            **progress_kwargs,
            **{
                key: value
                for key, value in {
                    "model": model,
                    "provider": provider,
                    "profile": profile,
                    "resume_thread_id": resume_thread_id,
                }.items()
                if value is not None
            },
        )
    finally:
        if call is not None and registered:
            try:
                _sample_invocations()
                _finish(call, result, resume_thread_id)
                # The caller may persist usage after native exit. During that
                # short handoff reads still have the snapshot, never a live
                # identity/reader. Cleanup also runs if the callback fails.
                _notify_execution(on_execution_change, call, False)
            finally:
                _unregister(call)
    assert result is not None
    if resume_thread_id is not None and result.thread_id != resume_thread_id:
        return AgentRunResult(
            result.exit_code,
            thread_id=result.thread_id,
            usage=result.usage,
            error=(
                f"Native session resume identity mismatch: expected {resume_thread_id!r}, "
                f"received {result.thread_id!r}." + (f" {result.error}" if result.error else "")
            ),
        )
    return result


def _attach_session(
    call: ActiveInvocation, session_id: str, project_dir: Path, resume_thread_id: str | None
) -> None:
    with _active_invocations_lock:
        if (
            call.invocation_id not in _active_invocations
            or call.finished
            or call.session_id is not None
        ):
            return
        if resume_thread_id is not None and session_id != resume_thread_id:
            return
        if any(
            other.session_id == session_id and other.reader is not None
            for other in _active_invocations.values()
            if other is not call
        ):
            return
        call.session_id = session_id
        call.reader = CodexCounterReader(session_id, project_dir, resumed=call.resumed)


def agent_review_sandbox(host_key: str) -> AgentSandbox:
    """Choose the declared native review mode; normal mode adds no isolation."""
    definition = _definition_by_key(host_key)
    if definition is not None and definition.policy.agent_execution.review_sandbox is not None:
        return definition.policy.agent_execution.review_sandbox
    # Unsupported hosts still fail the shared selection validation before launch.
    return "read-only"


def agent_selection_error(
    host_key: str,
    *,
    sandbox: AgentSandbox,
    model: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> str | None:
    """Validate declared native choices without claiming installation or model access."""
    definition = _definition_by_key(host_key)
    if definition is None or definition.run_agent is None:
        return f"Host {host_key!r} does not support headless agent execution."
    capability = definition.policy.agent_execution
    if sandbox not in capability.sandboxes:
        return f"Host {host_key!r} does not support {sandbox} execution. {capability.notes}"
    for key, value in {"model": model, "provider": provider, "profile": profile}.items():
        if value is not None and (not value.strip() or value.startswith("-")):
            return f"{key} must be a nonempty native selection, not a CLI option."
    if provider is not None and not capability.provider_selection:
        return f"Host {host_key!r} does not support a native provider selector."
    if profile is not None and not capability.profile_selection:
        return f"Host {host_key!r} does not support a native execution profile selector."
    if capability.requires_provider_model and (not model or not provider):
        return f"Host {host_key!r} requires explicit provider and model."
    if (
        model is not None
        and model not in capability.models
        and not (capability.custom_models or provider is not None)
    ):
        return f"Model {model!r} is not in the current {host_key} native model catalog."
    return None


def inspect_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    definitions = _definitions()
    with ThreadPoolExecutor(
        max_workers=len(definitions),
        thread_name_prefix="stackos-host-inspect",
    ) as executor:
        futures = [
            executor.submit(
                _call_adapter,
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            )
            for definition in definitions
        ]
        results = [
            normalize_result(future.result(), definition.policy)
            for future, definition in zip(futures, definitions, strict=True)
        ]
    return _aggregate(results)


def repair_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    token_error = token_preflight(home_dir)
    if token_error:
        policy = HostMcpPolicy("all-hosts", "AI tool connections", "automatic")
        return _aggregate(
            [
                normalize_result(
                    HostMcpResult(
                        host_key="all-hosts",
                        surface="local-lifecycle",
                        status="token_missing",
                        message=token_error,
                        ok=False,
                        available=True,
                        blocking=True,
                        repair=(
                            "Run `stackos install` or desktop Repair before MCP host registration."
                        ),
                    ),
                    policy,
                )
            ]
        )
    results: list[HostMcpResult] = []
    for definition in _definitions():
        current = normalize_result(
            _call_adapter(
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            ),
            definition.policy,
        )
        should_connect = (
            current.connection_state == "available"
            and definition.policy.setup_policy == "automatic"
        )
        should_repair = current.connection_state == "repair_needed" and current.repairable
        if should_connect or should_repair:
            lifecycle_action: Action = "register"
            if should_repair and definition.repair is not None:
                lifecycle_action = "repair"
            current = normalize_result(
                _invoke_adapter(definition, lifecycle_action, home_dir),
                definition.policy,
            )
        results.append(current)
    return _aggregate(results)


def remove_all(*, home: Path | None = None) -> HostMcpAggregate:
    home_dir = home or default_home()
    results = []
    for definition in _definitions():
        current = normalize_result(
            _call_adapter(
                definition.adapter.inspect,
                home_dir,
                definition.policy.host_key,
            ),
            definition.policy,
        )
        if current.selected and current.managed:
            current = normalize_result(
                _invoke_adapter(definition, "remove", home_dir),
                definition.policy,
            )
        results.append(current)
    return _aggregate(results)


def register_host(
    host_key: str,
    *,
    home: Path | None = None,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="register", home=home, force=force, profile=profile)


def remove_host(
    host_key: str,
    *,
    home: Path | None = None,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="remove", home=home, profile=profile)


def inspect_host(
    host_key: str,
    *,
    home: Path | None = None,
    profile: str | None = None,
) -> HostMcpResult:
    return _single(host_key, action="inspect", home=home, profile=profile)


def _single(
    host_key: str,
    *,
    action: Action,
    home: Path | None,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    home_dir = home or default_home()
    definition = _definition_by_key(host_key)
    if definition is None:
        return normalize_result(
            HostMcpResult(
                host_key=host_key,
                surface="unknown",
                status="register_failed",
                message=f"Unknown MCP host {host_key!r}.",
                ok=False,
                available=False,
                blocking=True,
                repair="Use one of: codex, claude-code, claude-desktop, gemini-cli, hermes, grok.",
            ),
            HostMcpPolicy(host_key, host_key, "automatic"),
        )
    if profile is not None and definition.inspect_profile is None:
        return normalize_result(
            HostMcpResult(
                host_key=host_key,
                surface="host-lifecycle",
                status="register_failed",
                message=f"{definition.policy.display_name} does not support profile targeting.",
                ok=False,
                available=True,
                blocking=True,
                repair="Remove the profile selector and retry.",
            ),
            definition.policy,
        )
    if action == "inspect":
        return normalize_result(
            _invoke_adapter(definition, action, home_dir, profile=profile),
            definition.policy,
        )
    if action == "remove":
        current = normalize_result(
            _invoke_adapter(definition, "inspect", home_dir, profile=profile),
            definition.policy,
        )
        if current.selected and not current.managed:
            return current
        return normalize_result(
            _invoke_adapter(definition, action, home_dir, profile=profile),
            definition.policy,
        )
    return normalize_result(
        _invoke_adapter(
            definition,
            action,
            home_dir,
            force=force,
            profile=profile,
        ),
        definition.policy,
    )


def _definitions() -> tuple[HostMcpDefinition, ...]:
    return (
        HostMcpDefinition(
            codex,
            HostMcpPolicy(
                "codex",
                "ChatGPT / Codex",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write", "read-only"),
                    review_sandbox="workspace-write",
                    models=("gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"),
                    provider_selection=True,
                    auth_status_command=("codex", "login", "status"),
                    notes="Native sandbox; login status establishes credential presence only.",
                    sources=(
                        "https://learn.chatgpt.com/docs/models",
                        "https://learn.chatgpt.com/docs/cli/reference",
                    ),
                ),
            ),
            force_register=lambda home, force: codex.register(home, force=force),
            run_agent=codex.run_agent,
        ),
        HostMcpDefinition(
            claude_code,
            HostMcpPolicy(
                "claude-code",
                "Claude Code",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write", "read-only"),
                    review_sandbox="workspace-write",
                    models=(
                        "claude-opus-5-5",
                        "claude-sonnet-5-5",
                        "claude-haiku-5-5",
                        "claude-fable-5-1",
                    ),
                    auth_status_command=("claude", "auth", "status"),
                    notes=(
                        "Review uses normal auto permissions with shell available; "
                        "native tool controls, not an OS sandbox."
                    ),
                    sources=(
                        "https://code.claude.com/docs/en/cli-reference",
                        "https://code.claude.com/docs/en/headless",
                        "https://platform.claude.com/docs/en/models/overview",
                    ),
                ),
            ),
            run_agent=claude_code.run_agent,
        ),
        HostMcpDefinition(
            claude_desktop,
            HostMcpPolicy(
                "claude-desktop",
                "Claude Desktop",
                "automatic",
                AgentCapabilities(
                    notes="Claude Desktop does not support scripting, --print or output-format.",
                    sources=("https://code.claude.com/docs/en/desktop",),
                ),
            ),
        ),
        HostMcpDefinition(
            gemini_cli,
            HostMcpPolicy(
                "gemini-cli",
                "Gemini CLI",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write", "normal"),
                    review_sandbox="normal",
                    models=(
                        "gemini-3.1-pro-preview",
                        "gemini-3-pro-preview",
                        "gemini-3-flash-preview",
                        "gemini-2.5-pro",
                        "gemini-2.5-flash",
                    ),
                    notes=(
                        "Delivery uses auto_edit; unapproved shell calls are denied. "
                        "Review uses normal native permissions with review-only instructions; "
                        "no enforced read-only isolation. Auth-status CLI is unverified."
                    ),
                    sources=(
                        "https://geminicli.com/docs/cli/headless/",
                        "https://geminicli.com/docs/cli/model/",
                        "https://geminicli.com/docs/cli/plan-mode/#non-interactive-execution",
                    ),
                ),
            ),
            run_agent=gemini_cli.run_agent,
        ),
        HostMcpDefinition(
            hermes,
            HostMcpPolicy(
                "hermes",
                "Hermes",
                "explicit",
                AgentCapabilities(
                    sandboxes=("workspace-write", "normal"),
                    review_sandbox="normal",
                    custom_models=True,
                    provider_selection=True,
                    profile_selection=True,
                    requires_provider_model=True,
                    auth_status_command=("hermes", "auth", "status"),
                    notes=(
                        "Explicit provider/model; native approvals retained. "
                        "Review uses normal native permissions with review-only instructions; "
                        "no enforced read-only isolation. "
                        "Installed shims may not be runnable."
                    ),
                    sources=(
                        "https://hermes-agent.nousresearch.com/docs/reference/cli-commands",
                        "https://hermes-agent.nousresearch.com/docs/user-guide/security",
                    ),
                ),
            ),
            run_agent=hermes.run_agent,
            repair=hermes.repair,
            inspect_profile=lambda home, profile: hermes.inspect_profile(
                home,
                profile=profile,
            ),
            register_profile=lambda home, profile: hermes.register(
                home,
                profile=profile,
            ),
            remove_profile=lambda home, profile: hermes.remove(
                home,
                profile=profile,
            ),
        ),
        HostMcpDefinition(
            grok,
            HostMcpPolicy(
                "grok",
                "Grok",
                "automatic",
                AgentCapabilities(
                    sandboxes=("workspace-write", "read-only"),
                    review_sandbox="workspace-write",
                    models=("grok-build",),
                    custom_models=True,
                    notes=(
                        "Native sandbox. Use grok models for installed CLI selections, "
                        "including configured aliases; grok-build is not the grok-4.7 API ID. "
                        "Auth presence is unverified."
                    ),
                    sources=(
                        "https://docs.x.ai/build/cli/reference",
                        "https://docs.x.ai/build/cli/headless-scripting",
                        "https://docs.x.ai/build/features/sandbox",
                        "https://docs.x.ai/build/features/permissions",
                    ),
                ),
            ),
            run_agent=grok.run_agent,
        ),
    )


def _definition_by_key(host_key: str) -> HostMcpDefinition | None:
    for definition in _definitions():
        if definition.policy.host_key == host_key:
            return definition
    return None


def _call_adapter(
    fn: AdapterFn,
    home: Path,
    host_key: str | None = None,
) -> HostMcpResult:
    try:
        return fn(home)
    except Exception as exc:
        resolved_host_key = host_key or (
            getattr(fn, "__module__", "host").rsplit(".", maxsplit=1)[-1].replace("_", "-")
        )
        return HostMcpResult(
            host_key=resolved_host_key,
            surface="unknown",
            status="register_failed",
            message=f"Host MCP adapter failed: {type(exc).__name__}.",
            ok=False,
            available=True,
            blocking=True,
            repair=(
                "Run `stackos install --mcp-only` or inspect the "
                f"{MCP_SERVER_NAME} MCP host config."
            ),
        )


def _invoke_adapter(
    definition: HostMcpDefinition,
    action: Action,
    home: Path,
    *,
    force: bool = False,
    profile: str | None = None,
) -> HostMcpResult:
    adapter = definition.adapter
    if action == "repair":
        if definition.repair is None:
            raise ValueError(f"{definition.policy.host_key} has no bulk repair capability")
        return _call_adapter(
            definition.repair,
            home,
            definition.policy.host_key,
        )
    inspect_profile = definition.inspect_profile
    if action == "inspect" and profile is not None and inspect_profile is not None:
        return _call_adapter(
            lambda current_home: inspect_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    register_profile = definition.register_profile
    if action == "register" and register_profile is not None:
        return _call_adapter(
            lambda current_home: register_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    remove_profile = definition.remove_profile
    if action == "remove" and remove_profile is not None:
        return _call_adapter(
            lambda current_home: remove_profile(current_home, profile),
            home,
            definition.policy.host_key,
        )
    force_register = definition.force_register
    if action == "register" and force_register is not None:
        return _call_adapter(
            lambda current_home: force_register(current_home, force),
            home,
            definition.policy.host_key,
        )
    fn = {
        "inspect": adapter.inspect,
        "register": adapter.register,
        "remove": adapter.remove,
    }[action]
    return _call_adapter(fn, home, definition.policy.host_key)


def _aggregate(results: list[HostMcpResult]) -> HostMcpAggregate:
    return HostMcpAggregate(
        ok=not any(result.blocking for result in results),
        results=results,
    )


def normalize_result(result: HostMcpResult, policy: HostMcpPolicy) -> HostMcpResult:
    """Apply the shared host lifecycle policy to adapter-native facts."""

    configured_statuses = {
        "registered_current",
        "registered",
        "registered_stale",
        "registered_unsafe",
        "registered_unmanaged",
        "restart_required",
        "shadowed",
    }
    selected = (
        result.selected if result.selected is not None else result.status in configured_statuses
    )
    managed = (
        result.managed
        if result.managed is not None
        else result.status in {"registered_current", "registered", "restart_required"}
    )

    state: HostMcpConnectionState
    if result.status in {"registered_current", "registered"}:
        state = "connected"
    elif result.status in {"available_unregistered", "removed"}:
        state = "available"
    elif result.status == "absent":
        state = "unavailable"
    elif result.status == "restart_required":
        state = "restart_required"
    elif result.status == "unsupported_host_version":
        state = "update_required"
    elif result.status == "registered_stale" and managed:
        state = "repair_needed"
    elif result.status in {
        "registered_stale",
        "registered_unsafe",
        "registered_unmanaged",
        "shadowed",
        "config_unreadable",
    }:
        state = "review_required"
    else:
        state = "error"

    labels: dict[HostMcpConnectionState, str] = {
        "connected": "Connected",
        "available": "Available",
        "repair_needed": "Repair needed",
        "review_required": "Review required",
        "update_required": "Update needed",
        "restart_required": "Restart needed",
        "unavailable": "Not detected",
        "error": "Setup failed",
    }
    repairable = state == "repair_needed" and bool(selected and managed)
    blocking = state in {
        "repair_needed",
        "review_required",
        "update_required",
        "restart_required",
        "error",
    } and bool(selected or result.host_key == "all-hosts" or (state == "error" and result.blocking))
    ok = not blocking
    return replace(
        result,
        agent_execution={
            **asdict(policy.agent_execution),
            "credential_status": "not_checked",
            "model_access": "not_checked",
        },
        display_name=policy.display_name,
        connection_state=state,
        status_label=labels[state],
        selected=selected,
        managed=managed,
        repairable=repairable,
        setup_policy=policy.setup_policy,
        ok=ok,
        blocking=blocking,
        advisory=not blocking and state != "connected",
    )
