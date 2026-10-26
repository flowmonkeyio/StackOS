"""Fixed Codex recorded profile; native admission deliberately remains closed.

Transport/profile objects are daemon-injected dependencies, never action input.
Production acquisition is behind reviewed daemon-held pins. No capability-discovery
launch or native test is implied by the registered source implementation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.repository.background import (
    BackgroundActionTasks,
    CommandBinding,
    CommandSlot,
)

FIXED_ARGS = (
    "--no-daemon",
    "--sandbox",
    "read-only",
    "--ask-for-approval",
    "never",
    "-c",
    'service_tier="default"',
    "-c",
    'web_search="disabled"',
    "-c",
    "notify=[]",
    "-c",
    "allow_login_shell=false",
    "--disable",
    "apps",
    "--disable",
    "plugins",
    "--disable",
    "remote_plugin",
    "--disable",
    "shell_tool",
    "--disable",
    "shell_snapshot",
    "--disable",
    "multi_agent",
    "--disable",
    "computer_use",
    "--disable",
    "browser_use",
    "--disable",
    "image_generation",
    "--disable",
    "skill_mcp_dependency_install",
    "--disable",
    "hooks",
    "-c",
    "mcp_servers.computer-use.enabled=false",
    "-c",
    "mcp_servers.ct-mcp.enabled=false",
    "-c",
    "mcp_servers.fullscope.enabled=false",
    "-c",
    "mcp_servers.guardrail.enabled=false",
    "-c",
    "mcp_servers.hoplon.enabled=false",
    "-c",
    "mcp_servers.llm-tracker.enabled=false",
    "-c",
    "mcp_servers.node_repl.enabled=false",
    "-c",
    "mcp_servers.program-manager.enabled=false",
    "-c",
    "mcp_servers.stackos.enabled=false",
    "--disable",
    "js_repl",
    "exec",
    "--json",
    "--ephemeral",
    "--color",
    "never",
)


class CodexInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    prompt: str = Field(min_length=1, max_length=65536)
    canonical_item_id: str = Field(min_length=3, max_length=160)
    owner_ref: str = Field(min_length=1, max_length=160)
    epoch: int = Field(ge=1)
    revision: int = Field(ge=1)
    scope_digest: str = Field(min_length=1, max_length=160)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    attempt_run_id: int = Field(ge=1)


@dataclass(frozen=True)
class CodexProfile:
    """Reviewed pins and independently observed projections held by the daemon."""

    cwd: Path
    schema_path: Path
    source_revision: str
    approval_ref: str
    expected: tuple[str, str, str, str]  # binary digest, version, config/capability digests
    observed: tuple[str, str, str, str]
    binary: Path
    config_path: Path
    capability_path: Path
    environment: tuple[tuple[str, str], ...]

    def argv(self) -> tuple[str, ...]:
        if (
            not self.approval_ref
            or self.expected != self.observed
            or any(not value for value in self.expected)
            or any(len(self.expected[i]) != 64 for i in (0, 2, 3))
            or not self.cwd.is_absolute()
            or not self.schema_path.is_absolute()
            or not self.binary.is_absolute()
            or not self.config_path.is_absolute()
            or not self.capability_path.is_absolute()
            or any(
                key not in {"PATH", "HOME", "TMPDIR", "CODEX_HOME"}
                for key, _value in self.environment
            )
            or not {"PATH", "HOME", "TMPDIR", "CODEX_HOME"}.issubset(dict(self.environment))
        ):
            raise ValueError("reviewed binary/version/effective-config/capability pins required")
        return (
            str(self.binary),
            *FIXED_ARGS,
            "--cd",
            str(self.cwd),
            "--output-schema",
            str(self.schema_path),
            "-",
        )


class CodexTransport(Protocol):
    """Fixed private dependency; acquisition adopts each object at its birth."""

    async def prepare(self, slot: CommandSlot) -> None: ...

    def acquire(self, slot: CommandSlot, argv: tuple[str, ...], prompt: str) -> None: ...

    async def observe(self, slot: CommandSlot) -> None: ...

    async def cleanup(self, slot: CommandSlot, deadline: float) -> None: ...


class CodexPipeIO:
    """Bounded local binary/config reads and nonblocking original pipe operations."""

    def digest(self, path: Path, limit: int, deadline: float, clock: Callable) -> str:
        digest = hashlib.sha256()
        count = 0
        with path.open("rb") as stream:
            while clock() < deadline:
                chunk = stream.read(min(65536, limit + 1 - count))
                if not chunk:
                    return digest.hexdigest()
                count += len(chunk)
                if count > limit:
                    raise ValueError("reviewed pin file exceeds bounded size")
                digest.update(chunk)
        raise TimeoutError("original preparation allowance expired")

    def nonblocking(self, stream: Any) -> None:
        os.set_blocking(stream.fileno(), False)

    def read(self, stream: Any) -> bytes:
        return os.read(stream.fileno(), 16384)

    def write(self, stream: Any, payload: bytes) -> int:
        return os.write(stream.fileno(), payload[:16384])

    def close(self, stream: Any) -> None:
        stream.close()


@dataclass
class _CodexPipes:
    process: Any = None
    streams: dict[str, Any] = field(default_factory=dict)
    pending: bytes = field(default=b"", repr=False)
    stdout: bytes = field(default=b"", repr=False)
    text: str = field(default="", repr=False)
    thread_id: str | None = None
    sequence: int = 0
    received: int = 0
    eof: set[str] = field(default_factory=set)
    birth_unknown: bool = True
    failed_closes: set[str] = field(default_factory=set)


class CodexProcessTransport:
    """Production fixed Popen/pipe transport; dependencies are injectable for fakes.

    Nothing starts at construction. Missing deployment pins keep the registered
    consumer closed. No caller can replace executable, environment or fixed flags.
    """

    def __init__(
        self,
        profile: CodexProfile | None = None,
        *,
        process_factory: Callable | None = None,
        io: CodexPipeIO | None = None,
        clock: Callable = time.monotonic,
        pause: Callable = asyncio.sleep,
    ) -> None:
        self.profile = profile
        self._factory = process_factory if process_factory is not None else subprocess.Popen
        self._io = io if io is not None else CodexPipeIO()
        self._clock, self._pause = clock, pause

    async def prepare(self, slot: CommandSlot) -> None:
        profile = self.profile
        if profile is None:
            raise ValueError("reviewed native pins are missing")
        profile.argv()
        for path, expected, limit in (
            (profile.binary, profile.expected[0], 268435456),
            (profile.config_path, profile.expected[2], 1048576),
            (profile.capability_path, profile.expected[3], 1048576),
        ):
            if self._io.digest(path, limit, slot.work_deadline, self._clock) != expected:
                raise ValueError("current binary/config/capability pin changed")
        if self._clock() >= slot.work_deadline:
            raise TimeoutError("original preparation allowance expired")

    def acquire(self, slot: CommandSlot, argv: tuple[str, ...], prompt: str) -> None:
        profile = self.profile
        if profile is None or argv != profile.argv() or self._clock() >= slot.work_deadline:
            raise ValueError("fixed native admission changed")
        state = _CodexPipes(pending=(prompt + "\n").encode())
        # Retain the birth/accounting frame BEFORE Popen can allocate anything.
        slot.adopt("codex-io", state, custody=False)
        slot.birth_unknown = True
        process = self._factory(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(profile.cwd),
            env=dict(profile.environment),
            shell=False,
            close_fds=True,
            bufsize=0,
        )
        state.process = process
        slot.adopt("process", process)
        state.birth_unknown = False
        slot.birth_unknown = False
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name)
            if stream is None:
                raise RuntimeError("original process pipe acquisition is unknown")
            state.streams[name] = stream
            slot.adopt(name, stream)
            self._io.nonblocking(stream)

    def _event(self, slot: CommandSlot, state: _CodexPipes, raw: bytes) -> None:
        event = json.loads(raw)
        kind = event.get("type")
        state.sequence += 1
        if kind == "thread.started":
            identity = event.get("thread_id")
            if (
                not isinstance(identity, str)
                or not 0 < len(identity) <= 160
                or state.thread_id not in (None, identity)
            ):
                raise ValueError("original Codex correlation changed")
            state.thread_id = identity
        elif kind in {"permission.required", "permission.requested", "approval.requested"}:
            slot.observe(state.sequence, {"type": "permission.required"})
        elif kind in {"item.started", "item.completed"}:
            item = event.get("item", {})
            if item.get("type") not in {"agent_message", "reasoning"}:
                slot.failure = "unsupported-permission"
                raise ValueError("unreviewed tool/capability event; needs operator")
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text")
                if not isinstance(text, str) or len(text.encode()) > 65536:
                    raise ValueError("Codex result exceeds bounded output")
                state.text = text
        elif kind == "turn.completed":
            if state.thread_id is None:
                raise ValueError("completed turn lacks original correlation")
            usage = event.get("usage", {})
            if any(
                type(usage.get(key)) is not int or usage[key] < 0
                for key in ("input_tokens", "output_tokens")
            ):
                raise ValueError("completed usage is unknown")
            slot.observe(
                state.sequence,
                {
                    "result": {
                        "status": "completed",
                        "turn_id": state.thread_id,
                        "text": state.text,
                    },
                    "usage": {
                        "input_tokens": usage["input_tokens"],
                        "output_tokens": usage["output_tokens"],
                        "total_tokens": usage["input_tokens"] + usage["output_tokens"],
                    },
                },
            )
        elif kind in {"error", "turn.failed"}:
            slot.failure = "codex-turn-failed"
            raise RuntimeError("Codex reported turn failure")
        elif kind != "turn.started":
            raise ValueError("unsupported Codex event")

    def _pump(self, slot: CommandSlot, state: _CodexPipes) -> None:
        stdin = state.streams.get("stdin")
        if stdin is not None and not stdin.closed and "stdin" not in state.failed_closes:
            if state.pending:
                try:
                    written = self._io.write(stdin, state.pending)
                    state.pending = state.pending[written:]
                except BlockingIOError:
                    pass
            if not state.pending:
                try:
                    self._io.close(stdin)
                except BaseException:
                    state.failed_closes.add("stdin")
                    slot.fds_closed = False
                    slot.failure = "pipe-close-unknown"
                    raise
        for name in ("stdout", "stderr"):
            stream = state.streams.get(name)
            if stream is None or stream.closed or name in state.eof:
                continue
            try:
                chunk = self._io.read(stream)
            except BlockingIOError:
                continue
            if not chunk:
                state.eof.add(name)
            state.received += len(chunk)
            if state.received > 1048576:
                raise ValueError("Codex output exceeds bounded capture")
            if name == "stdout":
                state.stdout += chunk
                if len(state.stdout) > 65536:
                    raise ValueError("Codex event exceeds bounded frame")
                while b"\n" in state.stdout:
                    line, state.stdout = state.stdout.split(b"\n", 1)
                    if line:
                        self._event(slot, state, line)

    @staticmethod
    def _reap(slot: CommandSlot, state: _CodexPipes) -> bool:
        if state.process.poll() is None:
            return False
        slot.cli_exit = state.process.wait(timeout=0)
        slot.reaped = True
        return True

    async def observe(self, slot: CommandSlot) -> None:
        state = slot.resources["codex-io"]
        while self._clock() < slot.work_deadline:
            self._pump(slot, state)
            if self._reap(slot, state) and state.eof == {"stdout", "stderr"}:
                if state.stdout:
                    self._event(slot, state, state.stdout)
                    state.stdout = b""
                return
            await self._pause(min(0.01, max(0.0, slot.work_deadline - self._clock())))
        slot.cancellation_at = self._clock()
        slot.failure = slot.failure or "work-deadline"

    async def cleanup(self, slot: CommandSlot, deadline: float) -> None:
        state = slot.resources["codex-io"]
        process = state.process
        # A failed constructor may have uncertain birth; never reconstruct from PID.
        if process is None:
            slot.failure = "process-birth-unknown"
            return
        kill_at = min(deadline, self._clock() + min(6.0, max(0.0, deadline - self._clock()) / 2))
        killed = False
        try:
            if process.poll() is None:
                slot.cancellation_at = slot.cancellation_at or self._clock()
                process.terminate()
            while self._clock() < deadline:
                if self._reap(slot, state):
                    break
                if not killed and self._clock() >= kill_at:
                    process.kill()
                    killed = True
                await self._pause(min(0.01, max(0.0, deadline - self._clock())))
            if not slot.reaped:
                self._reap(slot, state)
        finally:
            for name in ("stdin", "stdout", "stderr"):
                stream = getattr(process, name, None)
                if stream is not None:
                    # Popen owns these from birth, even if setup failed before adopt.
                    state.streams.setdefault(name, stream)
                    if name not in slot.resources:
                        slot.adopt(name, stream)
                    try:
                        if not stream.closed and name not in state.failed_closes:
                            self._io.close(stream)
                    except BaseException:
                        state.failed_closes.add(name)
                        slot.failure = "pipe-close-unknown"
            slot.fds_closed = (
                set(state.streams) == {"stdin", "stdout", "stderr"}
                and all(stream.closed for stream in state.streams.values())
                and not state.failed_closes
            )


def fresh_admission(
    request: ActionConnectorRequest,
    binding: CommandBinding,
    *,
    cost_approval_ref: str | None = None,
) -> None:
    """Worker-owned fresh Session only; reuse current grants/approval/owner CAS."""
    from stackos.db.models import ActionCall, ActionCallStatus, Run, RunKind, RunStatus
    from stackos.mcp.context import MCPContext
    from stackos.mcp.permissions import (
        RUN_PLAN_CONTROLLER_SKILL,
        _check_run_plan_dynamic_grant,
        active_run_plan_step,
    )
    from stackos.operations.actions.execution import _ensure_action_contract_approval
    from stackos.repositories.runs import RunRepository

    session = request.session
    if session is None:
        raise ValueError("worker Session required for Codex admission")
    call = session.get(ActionCall, binding.action_call_id, populate_existing=True)
    if (
        call is None
        or (call.project_id, call.run_id, call.run_plan_id, call.run_plan_step_id, call.status)
        != (
            binding.project_id,
            binding.controller_run_id,
            binding.run_plan_id,
            binding.run_plan_step_id,
            ActionCallStatus.RUNNING,
        )
        or call.request_json != request.input_json
    ):
        raise ValueError("original ActionCall binding changed")
    controller = session.get(Run, binding.controller_run_id, populate_existing=True)
    attempt = session.get(Run, binding.attempt_run_id, populate_existing=True)
    if (
        controller is None
        or controller.project_id != binding.project_id
        or attempt is None
        or (
            attempt.project_id != binding.project_id
            or attempt.parent_run_id != binding.controller_run_id
            or attempt.status != RunStatus.RUNNING
            or attempt.kind != RunKind.SKILL_RUN
            or (attempt.metadata_json or {}).get("ownership_binding")
            != {
                "canonical_item_id": binding.canonical_item_id,
                "owner_ref": binding.owner_ref,
                "epoch": binding.epoch,
                "scope_digest": binding.scope_digest,
            }
        )
    ):
        raise ValueError("original controller/attempt binding changed")
    ctx = MCPContext(
        session=session,
        request_id="retained-codex-admission",
        run=controller,
        run_id=binding.controller_run_id,
        project_id=binding.project_id,
        skill_name=RUN_PLAN_CONTROLLER_SKILL,
    )
    arguments = {
        "project_id": binding.project_id,
        "action_ref": request.action_ref,
        "run_id": binding.controller_run_id,
        "input_json": request.input_json,
    }
    _check_run_plan_dynamic_grant("action.execute", ctx, arguments)
    plan, step = active_run_plan_step(ctx, "action.execute")
    if (plan.id, step.id) != (binding.run_plan_id, binding.run_plan_step_id):
        raise ValueError("original active step changed")
    _ensure_action_contract_approval(
        ctx, plan_id=plan.id, grant_snapshot=plan.grant_snapshot_json, action_ref=request.action_ref
    )
    from stackos.actions.repository.execution import _require_unknown_cost_approval

    _require_unknown_cost_approval(
        session,
        project_id=binding.project_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        action_ref=request.action_ref,
        profile_ref=cost_approval_ref,
    )
    from stackos.actions import ActionRepository

    ActionRepository(session).require_codex_prior_clear(
        project_id=binding.project_id,
        canonical_item_id=binding.canonical_item_id,
        action_call_id=binding.action_call_id,
    )
    runs = RunRepository(session, audit_run_id=binding.controller_run_id)
    owner = runs._owner_row(
        project_id=binding.project_id, canonical_item_id=binding.canonical_item_id
    )
    runs._owner_admissible(owner, binding.scope_digest)
    runs.owner_consume(
        project_id=binding.project_id,
        canonical_item_id=binding.canonical_item_id,
        owner_ref=binding.owner_ref,
        scope_digest=binding.scope_digest,
        expected_epoch=binding.epoch,
        expected_revision=binding.revision,
        attempts_delta=1,
        failures_delta=0,
        evidence_ref=f"actionCall:{binding.action_call_id}:codex-admission",
    )


class CodexWorkerConnector:
    key = "codex-worker"

    def __init__(
        self,
        owner: BackgroundActionTasks,
        *,
        profile: CodexProfile | None = None,
        transport: CodexTransport | None = None,
    ) -> None:
        self.owner, self.profile = owner, profile
        self.transport = transport if transport is not None else CodexProcessTransport(profile)

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        try:
            CodexInput.model_validate(request.input_json)
        except ValueError:
            return [ActionValidationIssue(path="input_json", message="invalid fixed Codex input")]
        return []

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> None:
        return None

    def unknown_cost_approval_ref(self, request: ActionConnectorRequest) -> str | None:
        """Private reviewed profile, never a caller-supplied spend posture."""
        if self.profile is None:
            return None
        self.profile.argv()
        payload = CodexInput.model_validate(request.input_json)
        if payload.source_revision != self.profile.source_revision:
            return None
        return self.profile.approval_ref

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        from stackos.db.models import ActionCall

        payload = CodexInput.model_validate(request.input_json)
        if request.operation != "codex.recorded-turn" or request.dry_run:
            return self._unsupported("fixed background action only")
        if self.profile is None or self.transport is None:
            return self._unsupported("reviewed native profile/transport not admitted")
        try:
            argv = self.profile.argv()
            if payload.source_revision != self.profile.source_revision:
                raise ValueError("source revision pin changed")
        except ValueError as exc:
            return self._unsupported(str(exc))
        if request.session is None or request.action_call_id is None:
            return self._unsupported("original background ActionCall required")
        if request.credential is not None or request.credential_ref is not None:
            return self._unsupported("fixed profile accepts no connector credentials")
        call = request.session.get(ActionCall, request.action_call_id, populate_existing=True)
        if call is None or any(
            value is None
            for value in (
                call.run_id,
                call.run_plan_id,
                call.run_plan_step_id,
            )
        ):
            return self._unsupported("original scoped ActionCall required")
        binding = CommandBinding(
            request.project_id,
            request.action_call_id,
            payload.attempt_run_id,
            call.run_id,
            call.run_plan_id,
            call.run_plan_step_id,
            payload.canonical_item_id,
            payload.owner_ref,
            payload.epoch,
            payload.revision,
            payload.scope_digest,
            payload.source_revision,
        )
        slot = self.owner.command_slot(binding)
        transport = self.transport
        bind = request.session.get_bind()
        services = dict(request.session.info.get("operation_services", {}))
        primitive_request = replace(request, session=None, progress_callback=None)
        cost_approval_ref = self.profile.approval_ref

        def authorize() -> None:
            from sqlmodel import Session

            with Session(bind) as session:
                session.info["operation_services"] = services
                fresh_admission(
                    replace(primitive_request, session=session),
                    binding,
                    cost_approval_ref=cost_approval_ref,
                )

        result = await self.owner.command(
            slot,
            prepare=transport.prepare,
            authorize=authorize,
            acquire=lambda retained: transport.acquire(retained, argv, payload.prompt),
            work=transport.observe,
            cleanup=transport.cleanup,
        )
        if not slot.admitted and not slot.acquired and slot.failure is not None:
            return self._unsupported(
                "original preparation/current admission denied before acquisition"
            )
        # Even a completed turn never converts unknown termination into success/retry.
        raise ActionConnectorError(
            "Codex command requires custody/termination review",
            output_json={
                "status": "held",
                **result,
                "outcome_unknown": True,
                "cost": {"actual_cents": None},
            },
        )

    @staticmethod
    def _unsupported(reason: str) -> Any:
        raise ActionConnectorError(
            "Codex profile needs operator admission",
            output_json={
                "status": "unsupported",
                "next_action": "needs-operator",
                "reason": reason,
                "provider_executed": False,
                "attempts_consumed": 0,
                "retry_safe": False,
            },
        )
