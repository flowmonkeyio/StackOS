"""Local adapter from the finite invocation exchange to the existing host owner."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from stackos.host_mcp import service as hosts
from stackos.host_mcp.result import AgentRunResult
from stackos.local_runtime.connection import LocalConnectionStore
from stackos.local_runtime.exchange import InvocationCommand
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

    def __call__(self, request: dict, root: Path) -> AgentRunResult:
        command = InvocationCommand.model_validate(request)
        binding = self.store.load()
        if (
            binding is None
            or command.scope.project_id != binding.project_id
            or command.scope.attempt is None
            or command.scope.attempt < 1
        ):
            raise ValueError("an exact locally bound invocation is required")
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
            execution_scope=command.scope,
            mcp_config_ref=self.mcp_config_ref,
            launch_allowed=self.launch_allowed,
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
