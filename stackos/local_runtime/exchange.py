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
    state: Literal["completed", "unknown"]
    outcome: NativeResult | None = None

    @model_validator(mode="after")
    def terminal_shape(self):
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
            self._calls[connection.id] = handoff
            return handoff

    def poll(self, connection: LocalRuntimeConnection, invocation_id: str | None) -> dict:
        with self._lock:
            handoff = self._calls.get(connection.id)
            if handoff is None or handoff.identity != _identity(connection):
                return {
                    "state": "unknown" if invocation_id else "idle",
                    "needs_attention": bool(invocation_id),
                }
            call = handoff.call
            if invocation_id is not None and invocation_id != call.invocation_id:
                return {"state": "unknown", "needs_attention": True}
            if call.cancelled.is_set() or call.retired.is_set() or call.finished:
                return {"state": "unknown", "needs_attention": True}
            response = {
                "invocation_id": call.invocation_id,
                "scope": asdict(call.scope),
                "state": handoff.state,
                "needs_attention": handoff.state in {"dispatched", "unknown"},
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
                return {
                    "invocation_id": report.invocation_id,
                    "state": handoff.state,
                    "replayed": True,
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
            expected_session = handoff.launch.resume_thread_id or handoff.call.session_id
            if (
                expected_session is not None
                and report.outcome is not None
                and report.outcome.thread_id != expected_session
            ):
                raise ConflictError("native session identity mismatch; reconciliation required")
            handoff.report = report.model_copy(deep=True)
            handoff.state = report.state
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
                or handoff.state != "completed"
                or not call.finished
            ):
                raise ConflictError("only the owning known terminal invocation can be released")
            del self._calls[connection.id]
