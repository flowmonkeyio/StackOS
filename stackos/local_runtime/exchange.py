"""One in-memory handoff attached to an existing host invocation per computer.

The host/controller owns attempts and retirement. This transport has no disk
queue, recovery, retries, process signals or task lifecycle authority.
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from threading import Event, RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stackos.db.model_runtime import LocalRuntimeConnection
from stackos.host_mcp.result import AgentRunResult, AgentSandbox
from stackos.host_mcp.service import ActiveInvocation, AgentExecutionScope
from stackos.repositories.base import ConflictError, ValidationError

StopOutcome = Literal["stopped", "prevented", "unconfirmed"]


class NativeLaunch(BaseModel):
    """Finite host input. Local configuration alone chooses folder and MCP paths."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: str = Field(min_length=1)
    prompt: str
    output_schema: dict[str, Any]
    sandbox: AgentSandbox = "workspace-write"
    provider: str | None = None
    profile: str | None = None
    resume_thread_id: str | None = None
    candidate: dict[str, str] | None = None


class NativeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    exit_code: int | None
    thread_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None

    def host_result(self) -> AgentRunResult:
        return AgentRunResult(**self.model_dump())


class InvocationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: str = Field(min_length=1, max_length=64)
    scope: AgentExecutionScope
    state: Literal["launch"]
    needs_attention: Literal[False]
    host_key: str
    model: str | None
    launch: NativeLaunch


class InvocationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: str = Field(min_length=1, max_length=64)
    attempt: int = Field(gt=0)
    state: Literal["completed", "unknown", "stopped"]
    outcome: NativeResult | None = None
    stop_outcome: StopOutcome | None = None

    @model_validator(mode="after")
    def terminal_shape(self):
        if self.stop_outcome is not None:
            expected = "unknown" if self.stop_outcome == "unconfirmed" else "stopped"
            if self.state != expected:
                raise ValueError("stop state must reflect the owned stop outcome")
            return self
        if self.state == "stopped":
            raise ValueError("stopped requires an owned stop outcome")
        if (self.state == "completed") != (self.outcome is not None):
            raise ValueError("completed requires an outcome; unknown has no outcome")
        return self


@dataclass
class InvocationHandoff:
    call: ActiveInvocation
    identity: tuple[str, str, int, str, str]
    launch: NativeLaunch
    state: str = "pending"
    report: InvocationReport | None = None
    ready: Event = field(default_factory=Event)
    consumer_uncertain: bool = False
    stop_confirmed: Event = field(default_factory=Event)
    stop_outcome: str | None = None

    def outcome(self) -> AgentRunResult:
        if self.report is not None and self.report.outcome is not None:
            return self.report.outcome.host_result()
        return AgentRunResult(
            None, error="Local invocation outcome unknown; reconciliation required."
        )


def _identity(connection: LocalRuntimeConnection) -> tuple[str, str, int, str, str]:
    return (
        connection.owner_user_id,
        connection.environment_id,
        connection.project_id,
        connection.server_origin,
        connection.id,
    )


class InvocationExchange:
    """Bounded transport state, using the host's exact invocation/attempt identity.

    Poll consumes a launch before writing the response. A lost response cannot
    relaunch it. Reports remain retryable only while this handoff is retained.
    The controller explicitly releases a known terminal handoff after persisting
    it through its existing owner. Unknown work cannot be released here.
    """

    def __init__(self) -> None:
        self._calls: dict[str, InvocationHandoff] = {}
        self._lock = RLock()
        self._seen: dict[str, tuple[tuple, float]] = {}
        self._seen_at: dict[str, float] = {}

    def snapshot(self, connection: LocalRuntimeConnection) -> dict:
        """Observe last-known work without changing its lifetime or liveness."""
        with self._lock:
            seen = self._seen.get(connection.id)
            connected = bool(
                seen
                and seen[0] == _identity(connection)
                and time.monotonic() - seen[1] <= 30
                and connection.revoked_at is None
                and connection.expires_at > time.time()
            )
            handoff = self._calls.get(connection.id)
            if handoff is not None and handoff.identity != _identity(connection):
                handoff = None
            state = handoff.state if handoff else None
            attention = bool(
                handoff
                and (
                    handoff.consumer_uncertain
                    or state == "unknown"
                    or handoff.call.cancelled.is_set()
                    or (
                        (handoff.call.retired.is_set() or handoff.call.finished)
                        and state != "completed"
                    )
                )
            )
            return {
                "connection_state": "needs_attention"
                if attention
                else "connected"
                if connected
                else "disconnected",
                "needs_attention": attention,
                "last_seen_at": self._seen_at.get(connection.id) if seen else None,
                "invocation_id": handoff.call.invocation_id if handoff else None,
                "last_known_state": state,
            }

    def reconcile(self, connection: LocalRuntimeConnection, invocation_id: str | None) -> None:
        """Forget only a retired handoff after explicit owner confirmation."""
        with self._lock:
            handoff = self._calls.get(connection.id)
            if handoff is None:
                return
            if (
                handoff.identity != _identity(connection)
                or handoff.call.invocation_id != invocation_id
                or not handoff.call.finished
            ):
                raise ConflictError(
                    "Invocation still owned or identity changed; wait for retirement"
                )
            del self._calls[connection.id]

    def require_available(self, connection: LocalRuntimeConnection) -> None:
        with self._lock:
            seen = self._seen.get(connection.id)
            if (
                connection.revoked_at is not None
                or connection.expires_at <= time.time()
                or seen is None
                or seen[0] != _identity(connection)
                or time.monotonic() - seen[1] > 30
            ):
                raise ConflictError("Computer disconnected; connect and poll before admission")
            if connection.id in self._calls:
                raise ConflictError("Computer has unresolved work; manual reconciliation required")

    def offer(
        self, connection: LocalRuntimeConnection, call: ActiveInvocation, launch: NativeLaunch
    ) -> InvocationHandoff:
        with self._lock:
            if (
                connection.revoked_at is not None
                or connection.expires_at <= time.time()
                or call.scope.project_id != connection.project_id
                or not call.scope.attempt
                or call.scope.attempt < 1
                or call.finished
                or call.cancelled.is_set()
                or call.retired.is_set()
            ):
                raise ValidationError("an active, bound host invocation is required")
            existing = self._calls.get(connection.id)
            if existing is not None:
                if existing.call is call and existing.launch == launch:
                    return existing
                raise ConflictError(
                    "computer has an unresolved invocation; reconcile before admission"
                )
            for offered in self._calls.values():
                if (
                    offered.call.invocation_id == call.invocation_id
                    or offered.call.scope == call.scope
                ):
                    raise ConflictError(
                        "attempt already bound to a computer; refusing duplicate admission"
                    )
            handoff = InvocationHandoff(call, _identity(connection), launch.model_copy(deep=True))
            call.process_control.delegate_stop(lambda timeout: self._stop(handoff, timeout))
            self._calls[connection.id] = handoff
            return handoff

    def _stop(self, handoff: InvocationHandoff, timeout: float) -> str:
        with self._lock:
            handoff.call.cancelled.set()
            if handoff.state == "pending":
                handoff.state = "stopped"
                handoff.stop_outcome = "prevented"
                handoff.stop_confirmed.set()
                handoff.ready.set()
            if handoff.stop_confirmed.is_set():
                return handoff.stop_outcome or "unconfirmed"
        # Wait only for this computer's exact owned-stop acknowledgement. The
        # controller may already have retired; its cancellation fence survives.
        handoff.stop_confirmed.wait(timeout * 2 + min(timeout, 1))
        return handoff.stop_outcome or "unconfirmed"

    def poll(
        self,
        connection: LocalRuntimeConnection,
        invocation_id: str | None,
        *,
        allow_dispatch: bool = True,
    ) -> dict:
        with self._lock:
            self._seen[connection.id] = (_identity(connection), time.monotonic())
            self._seen_at[connection.id] = time.time()
            handoff = self._calls.get(connection.id)
            if handoff is None or handoff.identity != _identity(connection):
                attention = bool(invocation_id) or not allow_dispatch
                return {
                    "state": "unknown" if attention else "idle",
                    "needs_attention": attention,
                }
            call = handoff.call
            if invocation_id is not None and invocation_id != call.invocation_id:
                # A delayed poll names other work; it cannot change this handoff.
                return {"state": "unknown", "needs_attention": True}
            if call.cancelled.is_set() or call.process_control.cancelled.is_set():
                if invocation_id == call.invocation_id and handoff.state != "pending":
                    return {
                        "state": "stop",
                        "invocation_id": call.invocation_id,
                        "attempt": call.scope.attempt,
                        "needs_attention": True,
                    }
                return {"state": "unknown", "needs_attention": True}
            if not allow_dispatch:
                # Uncertainty fences all progression, but the exact retained
                # stop intent above must still reach its owned local process.
                return {"state": "unknown", "needs_attention": True}
            if (call.retired.is_set() or call.finished) and handoff.state != "completed":
                return {"state": "unknown", "needs_attention": True}
            if handoff.state == "dispatched" and invocation_id is None:
                # This consumer cannot identify already accepted work. Retain
                # the uncertainty for status readers, without closing the slot
                # for a known late report from the original invocation.
                handoff.consumer_uncertain = True
            response = {
                "invocation_id": call.invocation_id,
                "scope": asdict(call.scope),
                "state": handoff.state,
                "needs_attention": handoff.consumer_uncertain or handoff.state == "unknown",
            }
            if handoff.state == "pending":
                handoff.state = "dispatched"
                response.update(
                    state="launch",
                    host_key=call.host_key,
                    model=call.model,
                    launch=handoff.launch.model_dump(),
                )
            return deepcopy(response)

    def report(self, connection: LocalRuntimeConnection, report: InvocationReport) -> dict:
        with self._lock:
            handoff = self._calls.get(connection.id)
            if (
                handoff is None
                or handoff.identity != _identity(connection)
                or handoff.call.invocation_id != report.invocation_id
                or handoff.call.scope.attempt != report.attempt
            ):
                raise ConflictError("invocation unknown; reconcile without relaunch")
            if handoff.report is not None:
                if handoff.report != report:
                    raise ConflictError(
                        "terminal invocation report conflicts with accepted outcome"
                    )
                self._seen[connection.id] = (_identity(connection), time.monotonic())
                self._seen_at[connection.id] = time.time()
                return {
                    "invocation_id": report.invocation_id,
                    "state": handoff.state,
                    "replayed": True,
                }
            expected_session = handoff.launch.resume_thread_id or handoff.call.session_id
            outcome = report.outcome
            no_session_prevention = (
                report.stop_outcome == "prevented"
                and outcome is not None
                and outcome.thread_id is None
                and outcome.usage is None
                and outcome.result is None
            )
            if (
                expected_session is not None
                and outcome is not None
                and outcome.thread_id != expected_session
                and not no_session_prevention
            ):
                raise ConflictError("native session identity mismatch; reconciliation required")
            if report.stop_outcome is not None:
                if not handoff.call.cancelled.is_set() or handoff.state == "pending":
                    raise ConflictError("No matching dispatched stop intent")
                handoff.report = report.model_copy(deep=True)
                handoff.stop_outcome = report.stop_outcome
                handoff.state = report.state
                if report.stop_outcome in {"stopped", "prevented"}:
                    handoff.stop_confirmed.set()
                handoff.ready.set()
                return {
                    "invocation_id": report.invocation_id,
                    "state": report.state,
                    "replayed": False,
                }
            if (
                handoff.state != "dispatched"
                or handoff.call.finished
                or handoff.call.cancelled.is_set()
                or handoff.call.retired.is_set()
            ):
                raise ConflictError(
                    "invocation is not accepting results; reconcile without relaunch"
                )
            handoff.report = report.model_copy(deep=True)
            self._seen[connection.id] = (_identity(connection), time.monotonic())
            self._seen_at[connection.id] = time.time()
            handoff.state = report.state
            handoff.consumer_uncertain = report.state == "unknown"
            handoff.ready.set()
            return {
                "invocation_id": report.invocation_id,
                "state": handoff.state,
                "replayed": False,
            }

    def release(self, connection: LocalRuntimeConnection, call: ActiveInvocation) -> None:
        """Controller-only retirement after a known result; no REST release/retry."""
        with self._lock:
            handoff = self._calls.get(connection.id)
            if (
                handoff is None
                or handoff.call is not call
                or handoff.identity != _identity(connection)
                or not (
                    handoff.state == "completed"
                    or (handoff.state == "stopped" and handoff.stop_confirmed.is_set())
                )
                or not call.finished
            ):
                raise ConflictError("only the owning known terminal invocation can be released")
            del self._calls[connection.id]
