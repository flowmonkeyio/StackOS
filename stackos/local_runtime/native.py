"""Local adapter from the finite invocation exchange to the existing host owner."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, RLock

from stackos.host_mcp import service as hosts
from stackos.host_mcp.result import AgentRunResult
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.local_runtime.exchange import InvocationCommand, StopOutcome
from stackos.local_runtime.source import check_codex_source
from stackos.repositories.base import ValidationError
from stackos.task_delivery import _candidate_commit


class NativeAgentRunner:
    """Keep subscriptions, tools, folder and owned process handles on this computer.

    The server supplies an existing attempt and selection, never a local path or
    executable. There is no session search, retry, host/model fallback or lifecycle
    decision here. The current first-flow tool identity check supports Codex.
    """

    def __init__(
        self,
        store: LocalConnectionStore,
        *,
        mcp_config_ref: dict[str, str] | None = None,
        launch_allowed: Callable[[], bool] | None = None,
    ):
        self.store = store
        self.mcp_config_ref = dict(mcp_config_ref) if mcp_config_ref is not None else None
        self.launch_allowed = launch_allowed
        self._stop_lock = RLock()
        self._command: InvocationCommand | None = None
        self._cancelled = Event()
        self._call: hosts.ActiveInvocation | None = None

    def prepare(self, command: InvocationCommand) -> None:
        """Fence a stop arriving before discovery or host registration completes."""
        with self._stop_lock:
            if self._command == command:
                return
            if self._call is not None and not self._call.finished:
                raise ValueError("native invocation still owned")
            self._command = command
            self._cancelled = Event()
            self._call = None

    def stop(self, invocation_id: str, attempt: int) -> StopOutcome:
        with self._stop_lock:
            if self._command is None or (
                self._command.invocation_id,
                self._command.scope.attempt,
            ) != (invocation_id, attempt):
                return "unconfirmed"
            self._cancelled.set()
            call = self._call
            if call is not None:
                call.cancelled.set()
        outcome = call.process_control.stop() if call is not None else "prevented"
        if outcome == "stopped":
            return "stopped"
        if outcome == "prevented":
            return "prevented"
        return "unconfirmed"

    def __call__(self, request: dict, root: Path, binding: LocalBinding) -> AgentRunResult:
        command = InvocationCommand.model_validate(request)
        self.prepare(command)
        if (
            not isinstance(binding, LocalBinding)
            or command.scope.project_id != binding.project_id
            or command.scope.attempt is None
            or command.scope.attempt < 1
        ):
            raise ValueError("an exact locally bound invocation is required")
        binding = binding.model_copy(deep=True)
        self.store.require_binding(binding)
        root = self.store.require_folder(binding, root)
        if command.host_key != "codex":
            raise ValueError("local source/tool validation is unavailable for the selected host")
        launch = command.launch
        check_codex_source(
            self.store,
            binding,
            candidate=launch.candidate,
            mcp_config_ref=self.mcp_config_ref,
            sandbox=launch.sandbox,
            model=command.model,
            provider=launch.provider,
            profile=launch.profile,
        )
        # Discovery can run subprocesses. The authenticated poll's binding must
        # still be present afterwards; never adopt a new same-folder connection.
        self.store.require_binding(binding)

        def launch_allowed() -> bool:
            if self._cancelled.is_set():
                return False
            if self.launch_allowed is not None and not self.launch_allowed():
                return False
            try:
                self.store.require_binding(binding)
            except (ValueError, OSError):
                return False
            return True

        def execution_changed(call: hosts.ActiveInvocation, active: bool) -> None:
            with self._stop_lock:
                self._call = call

        result = hosts.run_agent(
            command.host_key,
            root,
            launch.instructions,
            launch.prompt,
            launch.output_schema,
            sandbox=launch.sandbox,
            model=command.model,
            provider=launch.provider,
            profile=launch.profile,
            resume_thread_id=launch.resume_thread_id,
            model_reasoning_effort=launch.model_reasoning_effort,
            native_settings=launch.native_settings,
            # A server request's claim/repository owner never crosses onto this
            # computer. Keep the authenticated transport identity for local process
            # ownership; the original request remains on the server command/report.
            execution_scope=(
                replace(
                    command.scope,
                    request_id=None,
                    remote_invocation_id=command.invocation_id,
                )
                if command.scope.request_id is not None
                else command.scope
            ),
            mcp_config_ref=self.mcp_config_ref,
            # The existing host checks this before registration and its owned
            # process handle checks it again immediately before spawning.
            launch_allowed=launch_allowed,
            on_execution_change=execution_changed,
        )
        if result.ok and (
            not result.thread_id
            or result.thread_id != result.thread_id.strip()
            or (launch.resume_thread_id is not None and result.thread_id != launch.resume_thread_id)
        ):
            return replace(
                result,
                result=None,
                error="Native session identity unavailable or mismatched; reconciliation required.",
            )
        # The server cannot inspect local Git. Validate a returned candidate through
        # the same owner used by standalone delivery, retaining native usage/session
        # even if candidate proof fails. Non-Git/domain results need no fake commit.
        if result.ok and result.result is not None:
            try:
                outputs = result.result.get("outputs", {})
                if isinstance(outputs, str):
                    outputs = json.loads(outputs)
                if isinstance(outputs, dict) and "commit_sha" in outputs:
                    commit_sha = _candidate_commit(
                        str(root),
                        outputs["commit_sha"],
                        (launch.candidate or {}).get("base_commit_sha"),
                    )
                    outputs = {**outputs, "commit_sha": commit_sha}
                    result = replace(
                        result,
                        result={
                            **result.result,
                            "outputs": (
                                json.dumps(outputs)
                                if isinstance(result.result.get("outputs"), str)
                                else outputs
                            ),
                        },
                    )
            except (ValueError, ValidationError):
                return replace(
                    result,
                    result=None,
                    error="Local candidate validation failed; reconcile before admission.",
                )
        return result
