"""Stateful MCP bridge proxy used by one plugin stdio session."""

from __future__ import annotations

import json
from typing import Any

from .catalog import _bridge_filter_tool_list_response, _bridge_tool_catalog
from .constants import (
    _AGENT_VISIBLE_TOOL_NAMES,
    _TOOLBOX_CALL_TOOL,
    _TOOLBOX_DESCRIBE_TOOL,
    _TOOLBOX_TOOL_NAMES,
)
from .protocol import (
    _bridge_as_int,
    _bridge_call_error,
    _bridge_extract_project_id,
    _bridge_make_tool_call_payload,
    _bridge_replace_tool_call_arguments,
    _bridge_response_text,
    _bridge_tool_call_arguments,
    _bridge_tool_call_name,
)
from .response import (
    _bridge_compact_tool_response,
    _bridge_forward_arguments,
    _bridge_response_mode,
)
from .toolbox import (
    _bridge_allowed_tool_names,
    _bridge_cache_step_context,
    _bridge_toolbox_describe,
)
from .workspace import (
    _bridge_scope_visibility_error,
    _bridge_scoped_arguments,
    _bridge_workspace_scoped_arguments,
)


class AgentBridgeProxy:
    """Stateful bridge adapter for one plugin stdio session."""

    def __init__(
        self,
        *,
        url: str,
        headers: dict[str, str],
        runtime: str = "codex",
        cwd: str | None = None,
        repo_fingerprint: str | None = None,
        git_remote_url: str | None = None,
        client_session_id: str | None = None,
    ) -> None:
        self.url = url
        self.headers = headers
        self.runtime = runtime
        self.cwd = cwd
        self.repo_fingerprint = repo_fingerprint
        self.git_remote_url = git_remote_url
        self.client_session_id = client_session_id
        self.tool_catalog: dict[str, dict[str, Any]] = {}
        self.allowed_by_run: dict[int, set[str]] = {}
        self.tokens_by_run: dict[int, str] = {}
        self.plans_by_run: dict[int, int] = {}
        self.workspace_scope_checked = False
        self.workspace_scope_error: str | None = None
        self.scoped_project_id: int | None = None

    def request_daemon(self, client: Any, body: str) -> str:
        response = client.post(self.url, content=body, headers=self.headers)
        response.raise_for_status()
        return _bridge_response_text(response.text)

    def handle(self, client: Any, *, payload: object, line: str, request_id: object) -> str:
        if not isinstance(payload, dict):
            return self.request_daemon(client, line)
        if payload.get("method") == "tools/list":
            self._ensure_workspace_scope(client)
            out = self.request_daemon(client, line)
            self.tool_catalog = _bridge_tool_catalog(out) or self.tool_catalog
            return _bridge_filter_tool_list_response(
                out,
                scoped_project_id=self.scoped_project_id,
                injected_fields=self._injected_fields(),
            )
        if payload.get("method") != "tools/call":
            return self.request_daemon(client, line)

        tool_name = _bridge_tool_call_name(payload)
        arguments = _bridge_tool_call_arguments(payload)
        self._ensure_workspace_scope(client)
        if tool_name == _TOOLBOX_DESCRIBE_TOOL:
            return self._handle_toolbox_describe(client, request_id, arguments)
        if tool_name == _TOOLBOX_CALL_TOOL:
            return self._handle_toolbox_call(client, request_id, arguments)
        if tool_name in _AGENT_VISIBLE_TOOL_NAMES:
            self._ensure_tool_catalog(client)
            response_mode = _bridge_response_mode(arguments)
            visibility_error = self._scope_visibility_error(tool_name, arguments)
            if visibility_error is not None:
                return _bridge_call_error(
                    request_id,
                    -32007,
                    "Bridge requires the current workspace project for this call.",
                    visibility_error,
                )
            workspace_args, workspace_error = self._scope_workspace_arguments(
                tool_name,
                arguments,
            )
            if workspace_error is not None:
                return _bridge_call_error(
                    request_id,
                    -32007,
                    "Bridge refused cross-workspace agent call.",
                    workspace_error,
                )
            assert workspace_args is not None
            scoped_args, scope_error = _bridge_scoped_arguments(
                catalog=self.tool_catalog,
                tool_name=tool_name,
                arguments=workspace_args,
                scoped_project_id=self.scoped_project_id,
            )
            if scope_error is not None:
                return _bridge_call_error(
                    request_id,
                    -32007,
                    "Bridge refused cross-project agent call.",
                    scope_error,
                )
            assert scoped_args is not None
            forwarded_args = _bridge_forward_arguments(
                catalog=self.tool_catalog,
                tool_name=tool_name,
                arguments=scoped_args,
                response_mode=response_mode,
            )
            out = self.request_daemon(
                client,
                _bridge_replace_tool_call_arguments(payload, arguments=forwarded_args),
            )
            if tool_name in {
                "workspace.bootstrap",
                "workspace.connect",
                "workspace.resolve",
                "workspace.startSession",
            }:
                self._update_workspace_scope(out)
            self._cache_step_context(out)
            return _bridge_compact_tool_response(
                tool_name=tool_name,
                response_text=out,
                response_mode=response_mode,
            )
        return _bridge_call_error(
            request_id,
            -32007,
            f"{tool_name or 'This tool'} is hidden behind toolbox.call.",
            {
                "tool": tool_name,
                "hint": (
                    "Call toolbox.describe for the tool schema, then "
                    "toolbox.call with tool_name and arguments."
                ),
            },
        )

    def _ensure_tool_catalog(self, client: Any) -> None:
        if self.tool_catalog:
            return
        self.tool_catalog = _bridge_tool_catalog(
            self.request_daemon(client, self._tool_list_body())
        )

    def _ensure_workspace_scope(self, client: Any) -> None:
        if self.workspace_scope_checked:
            return
        self.workspace_scope_checked = True
        if not any((self.cwd, self.repo_fingerprint, self.git_remote_url, self.client_session_id)):
            return
        arguments: dict[str, Any] = {"runtime": self.runtime}
        if self.cwd:
            arguments["cwd"] = self.cwd
        if self.repo_fingerprint:
            arguments["repo_fingerprint"] = self.repo_fingerprint
        if self.git_remote_url:
            arguments["git_remote_url"] = self.git_remote_url
        if self.client_session_id:
            arguments["client_session_id"] = self.client_session_id
        try:
            out = self.request_daemon(
                client,
                _bridge_make_tool_call_payload(
                    "stackos-bridge-session",
                    "workspace.startSession",
                    arguments,
                ),
            )
        except Exception:
            self.workspace_scope_error = "workspace.startSession failed"
            return
        self.scoped_project_id = _bridge_extract_project_id(out)
        self.workspace_scope_error = None

    def _update_workspace_scope(self, response_text: str) -> None:
        project_id = _bridge_extract_project_id(response_text)
        if project_id is not None:
            self.scoped_project_id = project_id
            self.workspace_scope_error = None

    def _has_workspace_hints(self) -> bool:
        return any((self.cwd, self.repo_fingerprint, self.git_remote_url, self.client_session_id))

    def _injected_fields(self) -> set[str]:
        fields: set[str] = set()
        if self.scoped_project_id is not None:
            fields.add("project_id")
        if self.cwd:
            fields.update({"cwd", "last_known_root"})
        if self.repo_fingerprint:
            fields.add("repo_fingerprint")
        if self.git_remote_url:
            fields.add("git_remote_url")
        if self.client_session_id:
            fields.add("client_session_id")
        if self.runtime:
            fields.add("runtime")
        return fields

    def _scope_workspace_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        return _bridge_workspace_scoped_arguments(
            tool_name=tool_name,
            arguments=arguments,
            runtime=self.runtime,
            cwd=self.cwd,
            repo_fingerprint=self.repo_fingerprint,
            git_remote_url=self.git_remote_url,
            client_session_id=self.client_session_id,
        )

    def _scope_visibility_error(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        return _bridge_scope_visibility_error(
            tool_name=tool_name,
            arguments=arguments,
            has_workspace_hints=self._has_workspace_hints(),
            scoped_project_id=self.scoped_project_id,
            workspace_scope_error=self.workspace_scope_error,
        )

    @staticmethod
    def _tool_list_body() -> str:
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "stackos-bridge-tools",
                "method": "tools/list",
                "params": {},
            }
        )

    def _refresh_run_context(self, client: Any, run_id: int | None) -> None:
        if run_id is None:
            return
        run_plan_id = self.plans_by_run.get(run_id)
        if run_plan_id is None:
            return
        body = _bridge_make_tool_call_payload(
            f"stackos-bridge-plan-{run_plan_id}",
            "runPlan.get",
            {"run_plan_id": run_plan_id},
        )
        try:
            out = self.request_daemon(client, body)
        except Exception:
            return
        self._cache_step_context(out)

    def _cache_step_context(self, response_text: str) -> None:
        _bridge_cache_step_context(
            response_text,
            allowed_by_run=self.allowed_by_run,
            tokens_by_run=self.tokens_by_run,
            plans_by_run=self.plans_by_run,
        )

    def _handle_toolbox_describe(
        self,
        client: Any,
        request_id: object,
        arguments: dict[str, Any],
    ) -> str:
        self._ensure_tool_catalog(client)
        run_id = _bridge_as_int(arguments.get("run_id"))
        self._refresh_run_context(client, run_id)
        return _bridge_toolbox_describe(
            request_id,
            catalog=self.tool_catalog,
            arguments=arguments,
            run_id=run_id,
            allowed_by_run=self.allowed_by_run,
        )

    def _handle_toolbox_call(
        self,
        client: Any,
        request_id: object,
        arguments: dict[str, Any],
    ) -> str:
        self._ensure_tool_catalog(client)
        target_name = arguments.get("tool_name")
        target_args = arguments.get("arguments")
        run_id = _bridge_as_int(arguments.get("run_id"))
        if run_id is None and isinstance(target_args, dict):
            run_id = _bridge_as_int(target_args.get("run_id"))
        self._refresh_run_context(client, run_id)

        if not isinstance(target_name, str) or not target_name:
            return _bridge_call_error(
                request_id,
                -32602,
                "toolbox.call requires a non-empty tool_name.",
            )
        if target_name in _TOOLBOX_TOOL_NAMES:
            return _bridge_call_error(
                request_id,
                -32602,
                "toolbox.call cannot call toolbox virtual tools.",
                {"tool": target_name},
            )
        if target_name not in self.tool_catalog:
            return _bridge_call_error(
                request_id,
                -32601,
                f"Unknown StackOS tool {target_name!r}.",
                {"tool": target_name},
            )
        if target_name not in _bridge_allowed_tool_names(run_id, self.allowed_by_run):
            return _bridge_call_error(
                request_id,
                -32007,
                f"Bridge refused hidden tool {target_name!r}.",
                {
                    "tool": target_name,
                    "run_id": run_id,
                    "hint": (
                        "Use setup tools, a started run plan's controller tools, "
                        "or a running run-plan step whose grants include this tool."
                    ),
                },
            )
        if not isinstance(target_args, dict):
            return _bridge_call_error(
                request_id,
                -32602,
                "toolbox.call arguments must be an object.",
                {"tool": target_name},
            )
        response_mode = _bridge_response_mode(target_args)
        visibility_error = self._scope_visibility_error(target_name, target_args)
        if visibility_error is not None:
            return _bridge_call_error(
                request_id,
                -32007,
                "Bridge requires the current workspace project for this call.",
                visibility_error,
            )
        workspace_args, workspace_error = self._scope_workspace_arguments(
            target_name,
            target_args,
        )
        if workspace_error is not None:
            return _bridge_call_error(
                request_id,
                -32007,
                "Bridge refused cross-workspace agent call.",
                workspace_error,
            )
        assert workspace_args is not None
        scoped_args, scope_error = _bridge_scoped_arguments(
            catalog=self.tool_catalog,
            tool_name=target_name,
            arguments=workspace_args,
            scoped_project_id=self.scoped_project_id,
        )
        if scope_error is not None:
            return _bridge_call_error(
                request_id,
                -32007,
                "Bridge refused cross-project agent call.",
                scope_error,
            )

        assert scoped_args is not None
        forwarded_args = _bridge_forward_arguments(
            catalog=self.tool_catalog,
            tool_name=target_name,
            arguments=scoped_args,
            response_mode=response_mode,
        )
        step_allowed = self.allowed_by_run.get(run_id, set()) if run_id is not None else set()
        if (
            run_id is not None
            and target_name in step_allowed
            and "run_token" not in forwarded_args
            and run_id in self.tokens_by_run
        ):
            forwarded_args["run_token"] = self.tokens_by_run[run_id]
        out = self.request_daemon(
            client,
            _bridge_make_tool_call_payload(
                request_id,
                target_name,
                forwarded_args,
            ),
        )
        self._cache_step_context(out)
        return _bridge_compact_tool_response(
            tool_name=target_name,
            response_text=out,
            response_mode=response_mode,
        )
