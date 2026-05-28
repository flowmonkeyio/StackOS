"""Stateful MCP bridge proxy used by one plugin stdio session."""

from __future__ import annotations

import json
from typing import Any

from .catalog import _bridge_filter_tool_list_response, _bridge_tool_catalog
from .constants import (
    _AGENT_ADMIN_GATED_TOOL_NAMES,
    _AGENT_RUN_PLAN_GATED_TOOL_NAMES,
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

_WORKSPACE_SCOPE_UPDATING_TOOL_NAMES = frozenset(
    {
        "workspace.bootstrap",
        "workspace.connect",
        "workspace.resolve",
        "workspace.startSession",
    }
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
            if tool_name in _WORKSPACE_SCOPE_UPDATING_TOOL_NAMES:
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

    def _ensure_tool_catalog(self, client: Any, *, refresh: bool = False) -> None:
        if self.tool_catalog and not refresh:
            return
        self.tool_catalog = (
            _bridge_tool_catalog(self.request_daemon(client, self._tool_list_body()))
            or self.tool_catalog
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
        scope_args: dict[str, Any] = {}
        if self.scoped_project_id is not None:
            scope_args["project_id"] = self.scoped_project_id
        run_plan_id = self.plans_by_run.get(run_id)
        if run_plan_id is None and run_id in self.allowed_by_run and run_id in self.tokens_by_run:
            return
        if run_plan_id is None:
            body = _bridge_make_tool_call_payload(
                f"stackos-bridge-plan-for-run-{run_id}",
                "runPlan.list",
                {"run_id": run_id, **scope_args},
            )
            try:
                out = self.request_daemon(client, body)
            except Exception:
                return
            run_plan_id = _bridge_first_run_plan_id(out)
            if run_plan_id is not None:
                self.plans_by_run[run_id] = run_plan_id
        if run_plan_id is None:
            return
        body = _bridge_make_tool_call_payload(
            f"stackos-bridge-plan-{run_plan_id}",
            "runPlan.get",
            {"run_plan_id": run_plan_id, **scope_args},
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
        requested_raw = arguments.get("tool_names")
        if isinstance(requested_raw, list) and any(
            isinstance(name, str) and name and name not in self.tool_catalog
            for name in requested_raw
        ):
            self._ensure_tool_catalog(client, refresh=True)
        run_id = _bridge_as_int(arguments.get("run_id"))
        self._refresh_run_context(client, run_id)
        return _bridge_toolbox_describe(
            request_id,
            catalog=self.tool_catalog,
            arguments=arguments,
            run_id=run_id,
            allowed_by_run=self.allowed_by_run,
            injected_fields=self._injected_fields(),
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
            self._ensure_tool_catalog(client, refresh=True)
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
                _toolbox_call_denial_repair(
                    tool_name=target_name,
                    run_id=run_id,
                    allowed_by_run=self.allowed_by_run,
                    catalog=self.tool_catalog,
                ),
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
        if target_name in _WORKSPACE_SCOPE_UPDATING_TOOL_NAMES:
            self._update_workspace_scope(out)
        self._cache_step_context(out)
        return _bridge_compact_tool_response(
            tool_name=target_name,
            response_text=out,
            response_mode=response_mode,
        )


def _toolbox_call_denial_repair(
    *,
    tool_name: str,
    run_id: int | None,
    allowed_by_run: dict[int, set[str]],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    active_step_tools = sorted(allowed_by_run.get(run_id, set())) if run_id is not None else []
    data: dict[str, Any] = {
        "tool": tool_name,
        "run_id": run_id,
        "hint": (
            "Use setup tools, a started run plan's controller tools, or a running "
            "run-plan step whose grants include this tool."
        ),
    }
    if active_step_tools:
        data["active_step_tool_names"] = active_step_tools
    grant_policy = _bridge_tool_grant_policy(catalog.get(tool_name))
    if tool_name in _AGENT_RUN_PLAN_GATED_TOOL_NAMES:
        data["reason"] = "run_plan_step_grant_required"
        data["repair"] = {
            "steps": [
                "Create or validate a run plan whose step grants this tool.",
                "Start the run plan with runPlan.start.",
                "Claim the intended step with runPlan.claimStep.",
                (
                    "Retry toolbox.call with run_id so the bridge can refresh grants "
                    "and inject run_token."
                ),
            ],
            "required_tool": tool_name,
            "retry_arguments": {
                "tool_name": tool_name,
                "run_id": "<run_id returned by runPlan.start or runPlan.claimStep>",
                "arguments": "<original arguments>",
            },
        }
        return data
    if tool_name in _AGENT_ADMIN_GATED_TOOL_NAMES or _grant_policy_is_local_admin(grant_policy):
        data["reason"] = "local_admin_required"
        data["grant_policy"] = grant_policy
        data["repair"] = {
            "hint": (
                "Use an explicit operator/admin setup flow; this is not available "
                "to the normal agent toolbox."
            )
        }
        return data
    data["reason"] = "tool_not_available_in_current_bridge_scope"
    return data


def _bridge_first_run_plan_id(response_text: str) -> int | None:
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelope, dict):
        return None
    result = envelope.get("result")
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return None
    items = structured.get("items")
    if not isinstance(items, list) and isinstance(structured.get("data"), dict):
        data = structured["data"]
        items = data.get("items")
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    return _bridge_as_int(first.get("id"))


def _bridge_tool_grant_policy(tool: dict[str, Any] | None) -> str | None:
    if not isinstance(tool, dict):
        return None
    meta = tool.get("_meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get("grant_policy")
    return value if isinstance(value, str) and value else None


def _grant_policy_is_local_admin(grant_policy: str | None) -> bool:
    if grant_policy is None:
        return False
    normalized = grant_policy.strip().lower()
    return normalized == "admin-only" or normalized.startswith("local-admin")
