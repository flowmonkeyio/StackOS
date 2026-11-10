"""Outbound-only consumer; the existing native adapter supplies the runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from stackos.host_mcp.result import AgentRunResult
from stackos.local_runtime.connection import (
    LocalBinding,
    LocalConnectionStore,
    _call,
    check_connection,
)
from stackos.local_runtime.exchange import InvocationCommand, InvocationReport, NativeResult


class OutboundWorker:
    def __init__(
        self,
        client,
        store: LocalConnectionStore,
        runner: Callable[[dict[str, Any], Path, LocalBinding], AgentRunResult] | None = None,
    ):
        if runner is None:
            from stackos.local_runtime.native import NativeAgentRunner

            runner = NativeAgentRunner(store)
        self.client, self.store, self.runner = client, store, runner
        self._lock = Lock()
        self._last_id: str | None = None
        self._report: InvocationReport | None = None
        self._acknowledged = False

    def exchange_once(self) -> dict:
        """One poll/launch/report, or report retry, without an automatic I/O loop."""
        with self._lock:
            binding = self.store.load()
            if binding is None:
                raise ValueError("connect this computer before polling")
            check_connection(self.client, binding)
            identity = {"project_id": binding.project_id, "connection_id": binding.connection_id}
            if self._report is not None and not self._acknowledged:
                return self._send_report(binding, identity)
            response = _call(self.client, binding.server_url, "poll", identity, binding.credential)
            if response.get("needs_attention"):
                raise ValueError("invocation outcome uncertain; explicit reconciliation required")
            if response.get("state") != "launch":
                return response
            command = InvocationCommand.model_validate(response)
            invocation_id, scope = command.invocation_id, command.scope
            if scope.attempt is None or scope.attempt < 1:
                raise ValueError("an exact invocation attempt is required")
            if invocation_id == self._last_id or scope.project_id != binding.project_id:
                raise ValueError("duplicate or foreign invocation; refusing relaunch")
            self._last_id = invocation_id  # Fence before entering any native runner.
            self._acknowledged = False
            self._report = InvocationReport(
                invocation_id=invocation_id, attempt=scope.attempt, state="unknown"
            )
            try:
                root = self.store.require_binding(binding)
                result = self.runner(command.model_dump(), root, binding.model_copy(deep=True))
                self._report = InvocationReport(
                    invocation_id=invocation_id,
                    attempt=scope.attempt,
                    state="completed",
                    outcome=NativeResult.model_validate(asdict(result)),
                )
            except Exception:
                # No guessed process identity, signals, or second launch. Report
                # uncertainty without sending exception text or local paths.
                self._send_report(binding, identity)
                raise
            return self._send_report(binding, identity)

    def _send_report(self, binding, identity) -> dict:
        assert self._report is not None
        response = _call(
            self.client,
            binding.server_url,
            "report",
            identity | {"report": self._report.model_dump()},
            binding.credential,
        )
        if (
            response.get("invocation_id") != self._report.invocation_id
            or response.get("state") != self._report.state
        ):
            raise ValueError("invocation report identity/state mismatch; reconciliation required")
        self._acknowledged = True
        return response
