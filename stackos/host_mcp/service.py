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
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import uuid4

from stackos import claude_mcp
from stackos.host_mcp.accounts import (
    DesiredAccount,
    HostAccountError,
    HostAccounts,
    NativeAccountAdapter,
)
from stackos.host_mcp.adapters import claude_code, claude_desktop, codex, gemini_cli, grok, hermes
from stackos.host_mcp.adapters.claude_accounts import accounts as claude_accounts
from stackos.host_mcp.adapters.grok_accounts import GrokAccounts
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
    ModelObservation,
    normalize_model_observation,
    normalize_usage,
)
from stackos.host_mcp.subscription_claude import read_claude
from stackos.host_mcp.subscription_codex import read_codex
from stackos.host_mcp.subscription_grok import read_grok

Action = Literal["inspect", "register", "repair", "remove"]
if TYPE_CHECKING:
    from stackos.repositories.agent_requests import AgentRequestExecution
AdapterFn = Callable[[Path], HostMcpResult]
ForceAdapterFn = Callable[[Path, bool], HostMcpResult]
ProfileAdapterFn = Callable[[Path, str | None], HostMcpResult]


@dataclass(frozen=True)
class AgentExecutionScope:
    """Safe attribution supplied by the caller, never inferred from tracker status."""

    project_id: int
    task_key: str | None = None
    ticket_key: str | None = None
    agent: str = ""
    attempt: int | None = None
    phase: str | None = None
    request_id: int | None = None

    def __post_init__(self) -> None:
        if type(self.project_id) is not int or self.project_id <= 0 or not self.agent.strip():
            raise ValueError("Execution requires a project and agent")
        if self.request_id is not None:
            if (
                type(self.request_id) is not int
                or self.request_id <= 0
                or self.task_key is not None
                or self.ticket_key is not None
            ):
                raise ValueError("Request attribution must be exclusive of task/ticket attribution")
        elif not self.task_key or not self.ticket_key:
            raise ValueError("Execution requires task/ticket or request attribution")


@dataclass
class _AccountSwitch:
    released: Event = field(default_factory=Event)
    resume: bool = True


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
    thread_id: str | None = None
    request_execution: AgentRequestExecution | None = None
    reader: CodexCounterReader | None = None
    usage: dict[str, Any] | None = None
    model_observation: ModelObservation | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=120))
    measured_at: str | None = None
    finished: bool = False
    finished_at: str | None = None
    process_control: ManagedProcessControl = field(default_factory=ManagedProcessControl)
    # Explicit invocation cancellation is separate from native I/O cleanup stops.
    cancelled: Event = field(default_factory=Event)
    retired: Event = field(default_factory=Event)
    # Exceptional cleanup keeps exact handles until group retirement is confirmed.
    process_retirement: dict[str, Any] | None = None
    cleanup_complete: bool = False
    # An early ownership rejection must also fence the terminal identity.
    session_error: str | None = None
    account_switch: _AccountSwitch | None = None
    restart_of: str | None = None

    def projection(self, *, include_usage: bool = True) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "agent": self.scope.agent,
            "host_key": self.host_key,
            "model": self.model,
            "thread_id": self.thread_id,
            **({"restart_of": self.restart_of} if self.restart_of else {}),
            "model_observation": deepcopy(self.model_observation),
            "started_at": self.started_at,
            "ticket_key": self.scope.ticket_key,
            **(
                {"request_id": self.scope.request_id, "thread_id": self.thread_id}
                if self.scope.request_id is not None
                else {}
            ),
            "live_usage": {
                "usage": {
                    "unit": "tokens",
                    "status": "measured",
                    "measured": deepcopy(self.usage["measured"]),
                },
                "measured_at": self.measured_at,
                "samples": deepcopy(list(self.samples)),
            }
            if include_usage and self.usage is not None
            else None,
        }


# One invocation registry owns identity, reader and bounded provisional counters.
# Neither UI subscriptions nor tracker reads start collection.
_active_invocations: dict[str, ActiveInvocation] = {}
_account_switches: dict[str, _AccountSwitch] = {}
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
            and (not call.finished or call.cleanup_complete)
        ]
        for call in calls:
            call.cancelled.set()
            call.process_control.cancelled.set()
        return calls


def quiesce_task_invocations(project_id: int, task_key: str) -> list[ActiveInvocation]:
    """Fence exact upgrade-owned handles without turning maintenance into cancellation.

    Include the bounded native-exit handoff: a finished native call whose result
    has not crossed the controller acceptance boundary is still an uncertain owner.
    """
    with managed_launch_lock, _active_invocations_lock:
        calls = [
            call
            for call in _active_invocations.values()
            if call.scope.project_id == project_id and call.scope.task_key == task_key
        ]
        for call in calls:
            call.process_control.cancelled.set()
        return calls


def uncheckpointed_invocations(scopes: set[tuple[int, str]]) -> list[dict[str, Any]]:
    """An installation replacement cannot certify owners outside its selected tasks."""
    with managed_launch_lock, _active_invocations_lock:
        return [
            {
                "invocation_id": call.invocation_id,
                "project_id": call.scope.project_id,
                "task_key": call.scope.task_key,
                "request_id": call.scope.request_id,
            }
            for call in _active_invocations.values()
            if (call.scope.project_id, call.scope.task_key) not in scopes
        ]


def cancel_request_invocations(project_id: int, request_id: int) -> list[ActiveInvocation]:
    """Fence only the exact request owner; callers stop the returned managed handles."""
    with managed_launch_lock, _active_invocations_lock:
        calls = [
            call
            for call in _active_invocations.values()
            if call.scope.project_id == project_id
            and call.scope.request_id == request_id
            and (not call.finished or call.cleanup_complete)
        ]
        for call in calls:
            call.cancelled.set()
            call.process_control.cancelled.set()
        return calls


def owns_request_invocation(call: ActiveInvocation, *, finished: bool = False) -> bool:
    """Exact registered object ownership, including the synchronous exit handoff."""
    with _active_invocations_lock:
        return (
            _active_invocations.get(call.invocation_id) is call
            and call.scope.request_id is not None
            and call.finished == finished
        )


def active_request_execution(
    project_id: int,
    request_id: int,
    *,
    include_handoff: bool = False,
) -> dict[str, Any] | None:
    """Copy request observation before SQL, including the bounded exit handoff if requested."""
    with _active_invocations_lock:
        for call in _active_invocations.values():
            if (
                call.scope.project_id == project_id
                and call.scope.request_id == request_id
                and (include_handoff or not call.finished)
            ):
                return {
                    **call.projection(),
                    **(
                        {"finished": call.finished, "usage": deepcopy(call.usage)}
                        if include_handoff
                        else {}
                    ),
                }
    return None


def _sample_invocations(now: datetime | None = None) -> None:
    # Reuse the native collector. Never hold its registry lock during SQL.
    with _active_invocations_lock:
        requests = [
            call
            for call in _active_invocations.values()
            if call.request_execution is not None
            and not call.finished
            and not call.cancelled.is_set()
        ]
    for call in requests:
        stop = False
        with managed_launch_lock:
            if not owns_request_invocation(call) or call.cancelled.is_set():
                continue
            try:
                assert call.request_execution is not None
                call.request_execution.renew(call)
            except Exception:
                # Fence ownership loss before completion can cross this boundary.
                call.cancelled.set()
                call.process_control.cancelled.set()
                stop = True
                logging.getLogger(__name__).exception("Could not renew native request ownership")
        if stop:
            # Native I/O must not hold the launch or registry lock.
            retire_invocation(call)
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
                samples = call.reader.read()
                call.model_observation = deepcopy(getattr(call.reader, "model_observation", None))
            except (OSError, ValueError):
                # Losing the exact-session reader loses evidence for the rest
                # of the invocation, not counters or the requested selection.
                # Retain explicit ambiguity rather than enabling selection fallback.
                call.model_observation = {
                    "status": "ambiguous",
                    "model": None,
                    "source": "codex.turn_context",
                    "session_id": call.reader.session_id,
                }
                call.reader.close()
                call.reader = None
                continue
            retained = list(call.samples)
            for stamp, raw in samples:
                measured = datetime.fromisoformat(stamp)
                if measured < datetime.fromisoformat(call.started_at):
                    continue
                usage = normalize_usage(
                    raw,
                    session_id=call.session_id,
                    resumed=call.resumed,
                    previous=call.previous_usage,
                )
                if usage["status"] != "measured":
                    continue
                # Current totals follow the newest source instant. That fence
                # must not discard unseen history or same-time contradictions.
                if not call.measured_at or measured > datetime.fromisoformat(call.measured_at):
                    call.usage = usage
                    call.measured_at = stamp
                if measured.timestamp() >= cutoff:
                    duplicate = any(
                        datetime.fromisoformat(point["at"]) == measured
                        and all(point.get(key) == value for key, value in usage["measured"].items())
                        for point in retained
                    )
                    if not duplicate:
                        retained.append({"at": stamp, "source": "native", **usage["measured"]})
            # Bound by source time, not arrival order: a late old point must not
            # evict a newer checkpoint. Keep conflicts for projection to expose.
            retained.sort(key=lambda point: datetime.fromisoformat(point["at"]))
            call.samples.clear()
            call.samples.extend(retained[-120:])


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
        call.cleanup_complete = True
        if call.reader:
            call.reader.close()
            call.reader = None
        call.samples.clear()
        call.usage = None
        call.model_observation = None
        call.previous_usage = None
        call.request_execution = None
        call.session_id = None
        if call.process_retirement and call.process_retirement["outcome"] not in {
            "stopped",
            "prevented",
        }:
            return  # Retain only the exact handle/scope; no reader or usage collection.
        _active_invocations.pop(call.invocation_id, None)
        if not _active_invocations:
            _collector_wake.set()
        call.retired.set()


def retire_invocation(call: ActiveInvocation) -> str:
    """Stop the registered handle and release an exceptional retirement hold only on proof."""
    with _active_invocations_lock:
        call.process_retirement = {"outcome": "pending"}
    try:
        outcome = call.process_control.stop()
    except Exception as error:
        outcome = "unconfirmed"
        call.process_control.stop_error = f"Native retirement failed: {type(error).__name__}"
    with _active_invocations_lock:
        call.process_retirement = {"outcome": outcome}
        if call.process_control.stop_error:
            call.process_retirement["error"] = call.process_control.stop_error
        if call.cleanup_complete:
            _unregister(call)
    return outcome


def _finish(
    call: ActiveInvocation,
    result: AgentRunResult | None,
    resume_thread_id: str | None = None,
    *,
    finished_at: str | None = None,
) -> None:
    """End native observation; hold only its snapshot for the synchronous receipt handoff."""
    with managed_launch_lock, _active_invocations_lock:
        call.finished_at = finished_at or datetime.now(UTC).isoformat()
        if result is not None and (
            resume_thread_id is None or result.thread_id == resume_thread_id
        ):
            call.thread_id = call.thread_id or result.thread_id
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
                    call.samples.append(
                        {"at": call.measured_at, "source": "final", **final["measured"]}
                    )
                call.usage = final
        call.finished = True
        if call.reader:
            call.reader.close()
            call.model_observation = deepcopy(
                getattr(call.reader, "model_observation", call.model_observation)
            )
            call.reader = None
        # Keep only the bounded observation through the synchronous receipt
        # handoff. _unregister releases it even if notification fails.
        call.previous_usage = None
        call.session_id = None
        if not any(not other.finished for other in _active_invocations.values()):
            _collector_wake.set()


def shutdown_usage() -> None:
    # Live task controllers retain their stop lifecycle. Once their cleanup
    # returns, this registry still owns any unconfirmed exact handle left behind.
    with managed_launch_lock, _active_invocations_lock:
        requests = [
            call
            for call in _active_invocations.values()
            if (call.scope.request_id is not None or call.cleanup_complete)
            and not call.retired.is_set()
        ]
        for call in requests:
            call.cancelled.set()
            call.process_control.cancelled.set()
    for call in requests:
        retire_invocation(call)
    with _active_invocations_lock:
        for call in list(_active_invocations.values()):
            if call.scope.request_id is None or call.retired.is_set():
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


def invocation_timings() -> list[dict[str, Any]]:
    """Copy owned timing through the synchronous final-receipt handoff, without I/O."""
    with _active_invocations_lock:
        observed_at = datetime.now(UTC).isoformat()
        return [
            {
                "project_id": call.scope.project_id,
                "task_key": call.scope.task_key,
                "attempt": call.scope.attempt,
                "phase": call.scope.phase,
                "finished_at": call.finished_at,
                "observed_at": observed_at,
                **{
                    key: value
                    for key, value in call.projection(include_usage=False).items()
                    if key != "live_usage"
                },
            }
            for call in _active_invocations.values()
        ]


type ProvisionalUsage = dict[tuple[int, str], list[tuple[int | None, str | None, dict[str, Any]]]]


def provisional_usage(project_id: int | None = None) -> ProvisionalUsage:
    """Copy bounded telemetry before SQL; never retain native readers."""
    with _active_invocations_lock:
        snapshot: ProvisionalUsage = {}
        for call in _active_invocations.values():
            if (
                call.scope.ticket_key is not None
                and call.usage is not None
                and (project_id is None or call.scope.project_id == project_id)
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
    subscription: Callable[[], dict | None] | None = None
    account_adapter: NativeAccountAdapter | None = None


def host_accounts(data_dir: Path) -> HostAccounts:
    adapters = {
        item.policy.host_key: item.account_adapter
        for item in _definitions()
        if item.account_adapter is not None
    }
    return HostAccounts(data_dir, adapters)


def select_account(
    accounts: HostAccounts, host_key: str, account_ref: str, expected_revision: int
) -> DesiredAccount:
    """Switch native credentials between exact, resumable managed invocations."""
    if host_key != "codex":
        return accounts.select(host_key, account_ref, expected_revision)
    with managed_launch_lock:
        if host_key in _account_switches:
            raise HostAccountError("account_switch_busy")
        desired = accounts.resolve(host_key).desired
        if desired.revision != expected_revision:
            raise HostAccountError("stale_selection")
        inventory = accounts.inventory().hosts.get(host_key)
        if inventory is None or not any(
            account.account_ref == account_ref for account in inventory.accounts
        ):
            raise HostAccountError("unknown_account")
        with _active_invocations_lock:
            calls = [call for call in _active_invocations.values() if call.host_key == host_key]
        for call in calls:
            if (
                call.scope.request_id is not None
                or call.scope.phase not in {"delivery", "review"}
                or call.session_error is not None
                or not call.thread_id
            ):
                raise HostAccountError("account_switch_unresumable_worker")
        switch = _AccountSwitch()
        _account_switches[host_key] = switch
        for call in calls:
            call.account_switch = switch
            call.process_control.account_switch = True
            call.process_control.cancelled.set()
    try:
        # Native exit and its callbacks must retire before credentials change.
        # Never hold the launch lock while those callbacks persist their receipts.
        for call in calls:
            if retire_invocation(call) not in {"stopped", "prevented"} or not call.retired.wait(2):
                raise HostAccountError("account_switch_retirement_unconfirmed")
        try:
            return accounts.select(host_key, account_ref, expected_revision)
        except HostAccountError as error:
            if error.code == "selection_restore_failed":
                switch.resume = False
            raise
    finally:
        with managed_launch_lock:
            _account_switches.pop(host_key, None)
            switch.released.set()


def _read_subscription(binary: str | None, reader: Callable[[str], dict | None]) -> dict | None:
    return reader(binary) if binary else None


def subscription_readers() -> dict[str, Callable[[], dict | None]]:
    return {item.policy.host_key: item.subscription for item in _definitions() if item.subscription}


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
    model_reasoning_effort: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
    resume_thread_id: str | None = None,
    execution_scope: AgentExecutionScope | None = None,
    on_execution_change: Callable[[ActiveInvocation, bool], None] | None = None,
    on_session_attached: Callable[[ActiveInvocation], None] | None = None,
    previous_usage: Any = None,
    mcp_config_ref: dict[str, str] | None = None,
    native_settings: dict[str, Any] | None = None,
    launch_allowed: Callable[[], bool] | None = None,
    request_execution: AgentRequestExecution | None = None,
) -> AgentRunResult:
    """Keep the controller's assignment while restarting an account-switched child."""
    restart_of = None
    while True:
        observed: ActiveInvocation | None = None
        usage = None

        def execution_changed(call: ActiveInvocation, active: bool) -> None:
            nonlocal observed, usage
            observed = call
            if not active:
                usage = deepcopy(call.usage)
            if on_execution_change is not None:
                on_execution_change(call, active)

        result = _run_agent_once(
            host_key,
            project_dir,
            instructions,
            prompt,
            output_schema,
            sandbox=sandbox,
            model=model,
            model_reasoning_effort=model_reasoning_effort,
            provider=provider,
            profile=profile,
            resume_thread_id=resume_thread_id,
            execution_scope=execution_scope,
            on_execution_change=execution_changed,
            on_session_attached=on_session_attached,
            previous_usage=previous_usage,
            mcp_config_ref=mcp_config_ref,
            native_settings=native_settings,
            launch_allowed=launch_allowed,
            request_execution=request_execution,
            _restart_of=restart_of,
        )
        if observed is None or observed.account_switch is None:
            return result
        observed.account_switch.released.wait()
        if (
            not observed.account_switch.resume
            or observed.cancelled.is_set()
            or not observed.retired.is_set()
            or not observed.thread_id
            or observed.session_error is not None
            or (result.ok and result.result is not None)
            or (launch_allowed is not None and not launch_allowed())
        ):
            return result
        resume_thread_id = observed.thread_id
        previous_usage = usage
        restart_of = observed.invocation_id
        prompt = "proceed"


def _run_agent_once(
    host_key: str,
    project_dir: str | Path,
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
    execution_scope: AgentExecutionScope | None = None,
    on_execution_change: Callable[[ActiveInvocation, bool], None] | None = None,
    on_session_attached: Callable[[ActiveInvocation], None] | None = None,
    previous_usage: Any = None,
    mcp_config_ref: dict[str, str] | None = None,
    native_settings: dict[str, Any] | None = None,
    launch_allowed: Callable[[], bool] | None = None,
    request_execution: AgentRequestExecution | None = None,
    _restart_of: str | None = None,
) -> AgentRunResult:
    """Run a project agent through the same host registry used by Overview."""
    if not instructions.strip():
        return AgentRunResult(None, error="Project-agent instructions must not be empty.")
    request_id = execution_scope.request_id if execution_scope else None
    if (request_id is not None) != (request_execution is not None) or (
        request_execution is not None
        and execution_scope is not None
        and (
            request_execution.project_id != execution_scope.project_id
            or request_execution.request_id != request_id
        )
    ):
        return AgentRunResult(None, error="Request execution requires its exact repository owner.")
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
        model_reasoning_effort=model_reasoning_effort,
        provider=provider,
        profile=profile,
        native_settings=native_settings,
    )
    if error:
        return AgentRunResult(None, error=error)
    sandbox = effective_agent_sandbox(host_key, sandbox, native_settings)
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
            restart_of=_restart_of,
            process_control=ManagedProcessControl(launch_allowed),
        )
        if execution_scope is not None
        else None
    )
    progress_kwargs: dict[str, Any] = {}
    if model_reasoning_effort is not None:
        if host_key == "codex":
            native_settings = {
                **(native_settings or {}),
                "model_reasoning_effort": model_reasoning_effort,
            }
        else:
            progress_kwargs["model_reasoning_effort"] = model_reasoning_effort
    if native_settings is not None:
        progress_kwargs["native_settings"] = native_settings
    if mcp_config_ref is not None:
        progress_kwargs["mcp_config_ref"] = mcp_config_ref
    result: AgentRunResult | None = None
    registered = False
    request_admitted = False
    if call is not None and host_key == "codex":
        progress_kwargs["on_session"] = lambda session_id: _attach_session(
            call, session_id, Path(project_dir), resume_thread_id, on_session_attached
        )
    if call is not None:
        progress_kwargs["process_control"] = call.process_control
    try:
        if call is not None:
            while True:
                with managed_launch_lock:
                    switch = _account_switches.get(host_key)
                    if switch is None:
                        if launch_allowed is not None and not launch_allowed():
                            return AgentRunResult(
                                None, error="Invocation cancelled before registration"
                            )
                        call.started_at = datetime.now(UTC).isoformat()
                        _register(call)
                        registered = True
                        if request_execution is not None:
                            request_execution.admit(
                                call,
                                {
                                    "host_key": host_key,
                                    "project_dir": str(Path(project_dir).expanduser().resolve()),
                                    "instructions": instructions,
                                    "prompt": prompt,
                                    "output_schema": output_schema,
                                    "sandbox": sandbox,
                                    "model": model,
                                    "model_reasoning_effort": model_reasoning_effort,
                                    "provider": provider,
                                    "profile": profile,
                                    "resume_thread_id": resume_thread_id,
                                    "mcp_config_ref": mcp_config_ref,
                                    "native_settings": native_settings,
                                },
                            )
                            request_admitted = True
                            call.request_execution = request_execution
                        _notify_execution(on_execution_change, call, True)
                        break
                switch.released.wait()
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
        if call is not None and call.session_error is not None:
            # Keep rejected native IDs out of terminal-only recovery fallback.
            # Hosts with no early-ID event never set this rejection.
            result = replace(result, thread_id=None, error=call.session_error, usage=None)
        elif (
            call is not None and call.thread_id is not None and (result.thread_id != call.thread_id)
        ):
            result = replace(
                result,
                error="Native request session identity mismatch"
                if request_id is not None
                else "Native session identity mismatch",
                usage=None,
            )
    finally:
        if call is not None and registered:
            retirement = call.process_control.retirement_status()
            if (
                result is None
                or result.exit_code is None
                or retirement == "unconfirmed"
                or call.process_control.stop_error is not None
            ):
                # Returned adapter errors can hide failed post-spawn I/O too.
                # Reconcile the actual group before releasing either owner.
                outcome = retire_invocation(call)
                if result is not None and outcome == "unconfirmed":
                    result = replace(
                        result, error=result.error or "Native process retirement is unconfirmed"
                    )
            elif call.process_retirement is not None:
                # A concurrent cancellation's stop may finish before its registry
                # update. Persist this current observation, never a stale pending.
                call.process_retirement = {"outcome": retirement}
            finished_at = datetime.now(UTC).isoformat()
            try:
                _sample_invocations()
                _finish(call, result, resume_thread_id, finished_at=finished_at)
                if result is not None:
                    result = replace(
                        result,
                        model_observation=normalize_model_observation(
                            call.model_observation,
                            session_id=result.thread_id,
                            host_key=host_key,
                        ),
                    )
                if request_execution is not None and request_admitted:
                    request_execution.finish(call, result)
                # The caller may persist usage after native exit. During that
                # short handoff reads still have the snapshot, never a live
                # identity/reader. Cleanup also runs if the callback fails.
                _notify_execution(on_execution_change, call, False)
            except BaseException:
                if call.process_retirement is None:
                    retire_invocation(call)
                raise
            finally:
                _unregister(call)
    assert result is not None
    if request_id is not None and call is not None and call.cancelled.is_set():
        return replace(result, error="Native request invocation cancelled")
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
    call: ActiveInvocation,
    session_id: str,
    project_dir: Path,
    resume_thread_id: str | None,
    on_session_attached: Callable[[ActiveInvocation], None] | None = None,
) -> None:
    # Serialize identity persistence with cancellation/rehandoff, but never do
    # SQL under the invocation registry lock used by telemetry readers.
    try:
        with managed_launch_lock:
            with _active_invocations_lock:
                if (
                    _active_invocations.get(call.invocation_id) is not call
                    or call.finished
                    or call.cancelled.is_set()
                    or call.session_id is not None
                    or call.session_error is not None
                    or not session_id.strip()
                ):
                    return
                if resume_thread_id is not None and session_id != resume_thread_id:
                    call.session_error = "Native session resume identity mismatch"
                    return
                if any(
                    other.session_id == session_id and not other.finished
                    for other in _active_invocations.values()
                    if other is not call
                ):
                    call.session_error = "Native session ownership conflict"
                    return
                call.session_id = session_id
                call.thread_id = session_id
                call.reader = CodexCounterReader(session_id, project_dir, resumed=call.resumed)
            if call.request_execution is not None:
                try:
                    call.request_execution.renew(call)
                except Exception:
                    call.cancelled.set()
                    raise
            if on_session_attached is not None:
                on_session_attached(call)
    except Exception:
        # Durability failures propagate. Native cleanup must release the launch
        # lock first: stop() owns its process lock before taking the launch lock.
        retire_invocation(call)
        raise


def effective_agent_sandbox(
    host_key: str, sandbox: AgentSandbox, native_settings: dict[str, Any] | None = None
) -> AgentSandbox:
    """Resolve the reduction used by native launch without granting permissions."""
    if host_key == "codex":
        return codex.native_agent_sandbox(sandbox, native_settings or {})
    return sandbox


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
    model_reasoning_effort: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
    native_settings: dict[str, Any] | None = None,
) -> str | None:
    """Validate explicit choices and existing source syntax without resolving defaults.

    Explicit reasoning selections require a verified model/effort pair. Codex
    source settings retain their native syntax contract, including effort with
    an inherited model; the capability catalog cannot establish that model.
    Neither path changes saved settings or establishes model access.
    """
    definition = _definition_by_key(host_key)
    if definition is None or definition.run_agent is None:
        return f"Host {host_key!r} does not support headless agent execution."
    capability = definition.policy.agent_execution
    if sandbox not in capability.sandboxes:
        return f"Host {host_key!r} does not support {sandbox} execution. {capability.notes}"
    if native_settings is not None:
        if host_key != "codex":
            return "Native project settings are unsupported by this host."
        try:
            codex.native_agent_overrides(native_settings)
        except ValueError as native_error:
            return str(native_error)
        if (
            model_reasoning_effort is not None
            and native_settings.get("model_reasoning_effort", model_reasoning_effort)
            != model_reasoning_effort
        ):
            return "Conflicting explicit native reasoning selections."
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
    if model_reasoning_effort is not None:
        efforts = capability.reasoning_efforts.get(model or "", ())
        if model_reasoning_effort not in efforts:
            choices = ", ".join(efforts) or "native default only"
            return (
                f"Host {host_key!r}, model {model or 'native default'!r} does not support "
                f"reasoning effort {model_reasoning_effort!r}. Supported: {choices}."
            )
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


_grok_accounts = GrokAccounts(lambda: grok.resolve_grok_bin(default_home()))


def _definitions() -> tuple[HostMcpDefinition, ...]:
    codex_reasoning: dict[str, tuple[str, ...]] = {
        model: ("low", "medium", "high", "xhigh", "max")
        for model in (
            "gpt-6.2-sol",
            "gpt-6.2-astra",
            "gpt-6-astra",
            "gpt-6.1-sol",
            "gpt-6.1-terra",
            "gpt-6-luna",
        )
    }
    for model in ("gpt-6.2-sol", "gpt-6.2-astra"):
        codex_reasoning[model] = (*codex_reasoning[model], "ultra")
    for model in ("gpt-6.1-terra", "gpt-6-luna"):
        codex_reasoning[model] = ("none", *codex_reasoning[model])
    claude_reasoning: dict[str, tuple[str, ...]] = {
        model: ("low", "medium", "high", "xhigh", "max")
        for model in (
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "claude-fable-5-1",
        )
    }
    grok_reasoning: dict[str, tuple[str, ...]] = {
        model: ("low", "medium", "high", "xhigh") for model in ("grok-4.6", "grok-4.5")
    }
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
                    models=tuple(codex_reasoning),
                    reasoning_efforts=codex_reasoning,
                    checked_on="2026-11-17",
                    recommended_model="gpt-6.2-sol",
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
            subscription=lambda: _read_subscription(codex.resolve_codex_bin(), read_codex),
            account_adapter=codex.accounts,
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
                    models=tuple(claude_reasoning),
                    reasoning_efforts=claude_reasoning,
                    checked_on="2026-11-13",
                    auth_status_command=("claude", "auth", "status"),
                    notes=(
                        "Review uses normal auto permissions with shell available; "
                        "native tool controls, not an OS sandbox."
                    ),
                    sources=(
                        "https://code.claude.com/docs/en/cli-reference",
                        "https://code.claude.com/docs/en/headless",
                        "https://platform.claude.com/docs/en/models/overview",
                        "https://code.claude.com/docs/en/model-config#adjust-effort-level",
                    ),
                ),
            ),
            run_agent=claude_code.run_agent,
            subscription=lambda: _read_subscription(claude_mcp.resolve_claude_bin(), read_claude),
            account_adapter=claude_accounts,
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
                    models=tuple(grok_reasoning),
                    reasoning_efforts=grok_reasoning,
                    checked_on="2026-11-13",
                    custom_models=True,
                    notes=(
                        "Native sandbox. Verified built-in CLI models; custom aliases retain "
                        "native reasoning. Use grok models for installed CLI selections. "
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
            account_adapter=_grok_accounts,
            subscription=lambda: _read_subscription(
                grok.resolve_grok_bin(default_home()), read_grok
            ),
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
