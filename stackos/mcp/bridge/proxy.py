"""Stateful MCP bridge proxy used by one plugin stdio session."""

from __future__ import annotations

import json
from typing import Any, cast

from .catalog import (
    _bridge_filter_tool_list_response,
    _bridge_tool_accepts_project_id,
    _bridge_tool_catalog,
)
from .constants import (
    _AGENT_ADMIN_GATED_TOOL_NAMES,
    _AGENT_RUN_PLAN_GATED_TOOL_NAMES,
    _AGENT_STEP_GATED_TOOL_NAMES,
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
    _bridge_mcp_request_headers,
    _bridge_mcp_request_meta,
    _bridge_negotiated_protocol_version,
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
    _bridge_cache_controller_run_context,
    _bridge_cache_step_context,
    _bridge_structured_content,
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
        deliberate_workspace_root: bool = False,
    ) -> None:
        self.url = url
        self.headers = headers
        self.runtime = runtime
        self.cwd = cwd
        self.repo_fingerprint = repo_fingerprint
        self.git_remote_url = git_remote_url
        self.client_session_id = client_session_id
        self.deliberate_workspace_root = deliberate_workspace_root
        self.tool_catalog: dict[str, dict[str, Any]] = {}
        self.allowed_by_run: dict[int, set[str]] = {}
        self.tokens_by_run: dict[int, str] = {}
        self.plans_by_run: dict[int, int] = {}
        self.projects_by_run: dict[int, int] = {}
        self.global_session: bool | None = None
        self.workspace_alias: str | None = None
        self.workspace_scope_checked = False
        self.workspace_scope_error: str | None = None
        self.scoped_project_id: int | None = None
        self.protocol_version: str | None = None
        self.modern_request_meta: dict[str, Any] | None = None

    def request_daemon(self, client: Any, body: str) -> str:
        try:
            payload: object = json.loads(body)
        except json.JSONDecodeError:
            payload = None
        request_meta = _bridge_mcp_request_meta(payload)
        if request_meta is not None:
            self.modern_request_meta = request_meta
        elif isinstance(payload, dict) and self.modern_request_meta is not None:
            forward_payload = cast(
                dict[str, Any],
                json.loads(json.dumps(payload, default=str)),
            )
            params = forward_payload.get("params")
            if not isinstance(params, dict):
                params = {}
                forward_payload["params"] = params
            meta = params.get("_meta")
            if not isinstance(meta, dict):
                meta = {}
            params["_meta"] = {**self.modern_request_meta, **meta}
            payload = forward_payload
            body = json.dumps(forward_payload, default=str)
        negotiated_headers = (
            {"MCP-Protocol-Version": self.protocol_version}
            if self.protocol_version is not None
            else {}
        )
        headers = {
            **self.headers,
            **negotiated_headers,
            **_bridge_mcp_request_headers(payload),
        }
        response = client.post(self.url, content=body, headers=headers)
        response.raise_for_status()
        response_text = _bridge_response_text(response.text)
        if isinstance(payload, dict) and payload.get("method") == "initialize":
            self.protocol_version = _bridge_negotiated_protocol_version(response_text)
        return response_text

    def handle(self, client: Any, *, payload: object, line: str, request_id: object) -> str:
        if not isinstance(payload, dict):
            return self.request_daemon(client, line)
        request_meta = _bridge_mcp_request_meta(payload)
        if request_meta is not None:
            self.modern_request_meta = request_meta
        if payload.get("method") == "tools/list":
            self._ensure_workspace_scope(client)
            out = self.request_daemon(client, line)
            self.tool_catalog = _bridge_tool_catalog(out) or self.tool_catalog
            return _bridge_filter_tool_list_response(
                out,
                scoped_project_id=self.scoped_project_id,
                injected_fields=self._injected_fields(),
                global_session=self.global_session is True,
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
            project_error = self._validate_project(client, scoped_args.get("project_id"))
            if project_error is not None:
                return _bridge_call_error(
                    request_id, -32007, "Project selection failed.", project_error
                )
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
                self._update_workspace_scope(out, tool_name=tool_name, arguments=workspace_args)
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
        if not self._has_workspace_hints():
            self.workspace_scope_checked = True
            return
        arguments = {
            key: value
            for key, value in {
                "cwd": self.cwd,
                "repo_fingerprint": self.repo_fingerprint,
                "git_remote_url": self.git_remote_url,
            }.items()
            if value
        }
        try:
            out = self.request_daemon(
                client,
                _bridge_make_tool_call_payload(
                    "stackos-bridge-scope",
                    "workspace.resolve",
                    {**arguments, "response_mode": "raw"},
                ),
            )
            data = _bridge_structured_content(out)
            if data is None:
                raise ValueError("workspace.resolve returned no resolution")
            data = data.get("data", data)
            if not isinstance(data, dict) or "needs_connect" not in data:
                raise ValueError("workspace.resolve returned an invalid resolution")
        except Exception:
            self.workspace_scope_error = "workspace.resolve failed; retry startup diagnostics"
            return
        self.workspace_scope_checked = True
        self.workspace_scope_error = None
        self.scoped_project_id = _bridge_as_int(data.get("project_id"))
        if self.scoped_project_id is not None or self.deliberate_workspace_root:
            self.global_session = False

    def _update_workspace_scope(
        self, response_text: str, *, tool_name: str, arguments: dict[str, Any]
    ) -> None:
        structured = _bridge_structured_content(response_text)
        if structured is None:
            return
        data = structured.get("data", structured)
        if not isinstance(data, dict):
            return
        if self.global_session is True or self.scoped_project_id is not None:
            return
        if tool_name == "workspace.startSession":
            if not isinstance(data.get("global_session"), bool):
                return
            self.global_session = data["global_session"]
            self.workspace_scope_checked = True
            self.workspace_scope_error = None
            if self.global_session:
                self.scoped_project_id = None
                return
        # Diagnostic lookups never select a different project for a pending chat.
        if self.global_session is False and tool_name != "workspace.resolve":
            project_id = _bridge_extract_project_id(response_text)
            if project_id is not None:
                self.scoped_project_id = project_id
                alias = arguments.get("workspace_alias")
                if isinstance(alias, str):
                    self.workspace_alias = alias
                self.workspace_scope_error = None

    def _has_workspace_hints(self) -> bool:
        return any((self.cwd, self.repo_fingerprint, self.git_remote_url))

    def _injected_fields(self) -> set[str]:
        fields: set[str] = set()
        if self.scoped_project_id is not None:
            fields.add("project_id")
        if self.cwd and self.global_session is not True:
            fields.update({"cwd", "last_known_root"})
        if self.repo_fingerprint and self.global_session is not True:
            fields.add("repo_fingerprint")
        if self.git_remote_url and self.global_session is not True:
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
        arguments = dict(arguments)
        if tool_name == "workspace.startSession":
            if "global_session" in arguments and not isinstance(arguments["global_session"], bool):
                return None, {"reason": "invalid_global_session", "hint": "Use a JSON boolean."}
            if arguments.get("global_session") is True and (
                self.global_session is False or self.deliberate_workspace_root
            ):
                return None, {
                    "reason": "workspace_scope_protected",
                    "project_id": self.scoped_project_id,
                }
            if self.global_session is True:
                if arguments.get("global_session") is False:
                    return None, {"reason": "global_session_already_started"}
                arguments["global_session"] = True
        if self.global_session is False and arguments.get("rebind_existing") is True:
            return None, {
                "reason": "workspace_scope_protected",
                "project_id": self.scoped_project_id,
            }
        if self.scoped_project_id is not None and tool_name in {
            "workspace.startSession",
            "workspace.bootstrap",
            "workspace.connect",
        }:
            alias = arguments.get("workspace_alias")
            if (
                (alias is not None and alias != self.workspace_alias)
                or arguments.get("project_slug") is not None
                or arguments.get("project_name") is not None
            ):
                return None, {
                    "reason": "workspace_scope_protected",
                    "project_id": self.scoped_project_id,
                }
            if self.workspace_alias is not None:
                arguments["workspace_alias"] = self.workspace_alias
            if tool_name == "workspace.startSession":
                arguments["global_session"] = False
        # Keep launch hints on the first handshake for the daemon's authoritative
        # binding recheck. After a global handshake they are never scope anchors.
        global_started = self.global_session is True
        return _bridge_workspace_scoped_arguments(
            tool_name=tool_name,
            arguments=arguments,
            runtime=self.runtime,
            cwd=None if global_started else self.cwd,
            repo_fingerprint=None if global_started else self.repo_fingerprint,
            git_remote_url=None if global_started else self.git_remote_url,
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
            global_session=self.global_session,
            accepts_project_id=_bridge_tool_accepts_project_id(self.tool_catalog, tool_name),
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

    def _validate_project(self, client: Any, project_id: object) -> dict[str, Any] | None:
        if self.global_session is not True or project_id is None:
            return None
        if not isinstance(project_id, int) or isinstance(project_id, bool) or project_id <= 0:
            return {"reason": "project_required"}
        try:
            response = self.request_daemon(
                client,
                _bridge_make_tool_call_payload(
                    "stackos-bridge-project",
                    "project.get",
                    {"project_id": project_id, "response_mode": "raw"},
                ),
            )
            data = _bridge_structured_content(response)
            if isinstance(data, dict):
                data = data.get("data", data)
            if isinstance(data, dict) and data.get("id") == project_id:
                return None
        except Exception:
            pass
        return {
            "reason": "project_unavailable",
            "project_id": project_id,
            "hint": (
                "Confirm an existing project explicitly; no replacement or fallback is selected."
            ),
        }

    def _clear_run_context(self, run_id: int) -> None:
        self.allowed_by_run.pop(run_id, None)
        self.tokens_by_run.pop(run_id, None)
        self.plans_by_run.pop(run_id, None)
        self.projects_by_run.pop(run_id, None)

    def _refresh_run_context(
        self,
        client: Any,
        run_id: int | None,
        *,
        run_plan_id: int | None = None,
        project_id: int | None = None,
    ) -> int | None:
        project_id = project_id if project_id is not None else self.scoped_project_id
        if run_id is not None:
            self._clear_run_context(run_id)
        if project_id is None:
            return None

        def read(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            out = self.request_daemon(
                client,
                _bridge_make_tool_call_payload(
                    "stackos-bridge-run-context",
                    name,
                    {**arguments, "project_id": project_id, "response_mode": "raw"},
                ),
            )
            structured = _bridge_structured_content(out)
            data = structured.get("data", structured) if isinstance(structured, dict) else None
            if not isinstance(data, dict):
                raise ValueError("Invalid run context")
            return out, data

        try:
            plan_response = None
            plan = None
            if run_plan_id is not None:
                plan_response, plan = read("runPlan.get", {"run_plan_id": run_plan_id})
                if plan.get("id") != run_plan_id or plan.get("project_id") != project_id:
                    raise ValueError("Wrong plan owner")
                plan_run_id = _bridge_as_int(plan.get("run_id"))
                if run_id is not None and plan_run_id != run_id:
                    raise ValueError("Wrong run/plan pair")
                run_id = plan_run_id
            if run_id is None:
                return None
            self._clear_run_context(run_id)
            run_response, run = read("run.get", {"run_id": run_id})
            if run.get("id") != run_id or run.get("project_id") != project_id:
                raise ValueError("Wrong run owner")
            if run.get("status") != "running":
                raise ValueError("Run is not running")
            metadata = run.get("metadata_json")
            actual_plan_id = (
                _bridge_as_int(metadata.get("run_plan_id")) if isinstance(metadata, dict) else None
            )
            if actual_plan_id is None:
                _, listing = read("runPlan.list", {"run_id": run_id})
                items = listing.get("items")
                if not isinstance(items, list) or len(items) != 1:
                    raise ValueError("No unique run plan")
                actual_plan_id = _bridge_as_int(items[0].get("id"))
            if actual_plan_id is None or (
                run_plan_id is not None and actual_plan_id != run_plan_id
            ):
                raise ValueError("Wrong controller plan")
            if plan is None:
                plan_response, plan = read("runPlan.get", {"run_plan_id": actual_plan_id})
            if (
                plan.get("id") != actual_plan_id
                or plan.get("project_id") != project_id
                or plan.get("run_id") != run_id
                or not isinstance(plan.get("steps"), list)
                or plan.get("status") != "started"
            ):
                raise ValueError("Invalid current plan")
            running = [
                step
                for step in plan["steps"]
                if isinstance(step, dict) and step.get("status") == "running"
            ]
            if len(running) > 1 or any(
                not isinstance(step.get("allowed_tools"), list) for step in running
            ):
                raise ValueError("Invalid current step grants")
            _bridge_cache_controller_run_context(
                run_response,
                allowed_by_run=self.allowed_by_run,
                tokens_by_run=self.tokens_by_run,
                plans_by_run=self.plans_by_run,
            )
            if run_id not in self.tokens_by_run:
                raise ValueError("No verified controller token")
            assert plan_response is not None
            _bridge_cache_step_context(
                plan_response,
                allowed_by_run=self.allowed_by_run,
                tokens_by_run=self.tokens_by_run,
                plans_by_run=self.plans_by_run,
            )
            self.projects_by_run[run_id] = project_id
            return run_id
        except Exception:
            if run_id is not None:
                self._clear_run_context(run_id)
            return None

    def _handle_toolbox_describe(
        self,
        client: Any,
        request_id: object,
        arguments: dict[str, Any],
    ) -> str:
        requested_raw = arguments.get("tool_names")
        exact_schema_request = (
            arguments.get("include_schemas") is True
            and isinstance(requested_raw, list)
            and bool(requested_raw)
        )
        self._ensure_tool_catalog(client, refresh=exact_schema_request)
        if (
            not exact_schema_request
            and isinstance(requested_raw, list)
            and any(
                isinstance(name, str) and name not in self.tool_catalog for name in requested_raw
            )
        ):
            self._ensure_tool_catalog(client, refresh=True)
        run_id = _bridge_as_int(arguments.get("run_id"))
        run_plan_id = _bridge_as_int(arguments.get("run_plan_id"))
        project_id = arguments.get("project_id", self.scoped_project_id)
        if self.scoped_project_id is not None and project_id != self.scoped_project_id:
            return _bridge_call_error(
                request_id,
                -32007,
                "Cross-project discovery refused.",
                {"reason": "project_scope_mismatch"},
            )
        if run_id is not None or run_plan_id is not None:
            error = self._scope_visibility_error("runPlan.get", {"project_id": project_id})
            if error is None and project_id is None:
                error = {"reason": "project_required"}
            if error is None:
                error = self._validate_project(client, project_id)
            if error is not None:
                return _bridge_call_error(
                    request_id, -32007, "Run discovery requires project scope.", error
                )
            run_id = self._refresh_run_context(
                client,
                run_id,
                run_plan_id=run_plan_id,
                project_id=project_id,
            )
            if run_id is None:
                return _bridge_call_error(
                    request_id,
                    -32007,
                    "Run context could not be verified.",
                    {"reason": "run_context_unavailable"},
                )
        return _bridge_toolbox_describe(
            request_id,
            catalog=self.tool_catalog,
            arguments=arguments,
            run_id=run_id,
            allowed_by_run=self.allowed_by_run,
            injected_fields=self._injected_fields(),
            global_session=self.global_session is True,
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
        if not isinstance(target_name, str) or not target_name:
            return _bridge_call_error(
                request_id, -32602, "toolbox.call requires a non-empty tool_name."
            )
        if target_name in _TOOLBOX_TOOL_NAMES:
            return _bridge_call_error(
                request_id, -32602, "toolbox.call cannot call toolbox virtual tools."
            )
        if not isinstance(target_args, dict):
            return _bridge_call_error(
                request_id, -32602, "toolbox.call arguments must be an object."
            )
        if target_name not in self.tool_catalog:
            self._ensure_tool_catalog(client, refresh=True)
        if target_name not in self.tool_catalog:
            return _bridge_call_error(request_id, -32601, f"Unknown StackOS tool {target_name!r}.")
        # Refuse administrative operations without obtaining run authority.
        if _grant_policy_is_local_admin(
            _bridge_tool_grant_policy(self.tool_catalog.get(target_name))
        ):
            return _bridge_call_error(
                request_id,
                -32007,
                f"Bridge refused hidden tool {target_name!r}.",
                _toolbox_call_denial_repair(
                    tool_name=target_name,
                    run_id=None,
                    allowed_by_run=self.allowed_by_run,
                    catalog=self.tool_catalog,
                ),
            )
        workspace_args, error = self._scope_workspace_arguments(target_name, target_args)
        if error is None:
            error = self._scope_visibility_error(target_name, target_args)
        if error is not None:
            return _bridge_call_error(
                request_id, -32007, "Bridge requires valid session/project scope.", error
            )
        assert workspace_args is not None
        scoped_args, error = _bridge_scoped_arguments(
            catalog=self.tool_catalog,
            tool_name=target_name,
            arguments=workspace_args,
            scoped_project_id=self.scoped_project_id,
        )
        if error is not None:
            return _bridge_call_error(
                request_id, -32007, "Bridge refused cross-project agent call.", error
            )
        assert scoped_args is not None
        project_id = scoped_args.get("project_id", self.scoped_project_id)
        error = self._validate_project(client, project_id)
        if error is not None:
            return _bridge_call_error(request_id, -32007, "Project selection failed.", error)
        run_id = _bridge_as_int(arguments.get("run_id"))
        # Direct reads/audit writes use inner IDs as targets, not execution authority.
        # The daemon validates those targets against the already checked project.
        direct_target = _bridge_tool_grant_policy(self.tool_catalog.get(target_name)) in {
            "direct-read",
            "direct-run-audit-write",
        }
        inner_run_id = None if direct_target else _bridge_as_int(target_args.get("run_id"))
        if run_id is not None and inner_run_id is not None and run_id != inner_run_id:
            return _bridge_call_error(
                request_id, -32007, "Conflicting run IDs.", {"reason": "run_scope_mismatch"}
            )
        run_id = run_id if run_id is not None else inner_run_id
        run_plan_id = None if direct_target else _bridge_as_int(target_args.get("run_plan_id"))
        refresh_required = run_id is not None or target_name in (
            _AGENT_RUN_PLAN_GATED_TOOL_NAMES | _AGENT_STEP_GATED_TOOL_NAMES
        )
        if refresh_required:
            run_id = self._refresh_run_context(
                client,
                run_id,
                run_plan_id=run_plan_id,
                project_id=project_id,
            )
            if run_id is None or self.projects_by_run.get(run_id) != project_id:
                return _bridge_call_error(
                    request_id,
                    -32007,
                    "Run context could not be verified.",
                    _toolbox_call_denial_repair(
                        tool_name=target_name,
                        run_id=None,
                        allowed_by_run=self.allowed_by_run,
                        catalog=self.tool_catalog,
                    )
                    | {"reason": "run_context_unavailable"},
                )
            supplied_token = scoped_args.get("run_token")
            if supplied_token is not None and supplied_token != self.tokens_by_run.get(run_id):
                return _bridge_call_error(
                    request_id,
                    -32007,
                    "Run token does not match this run.",
                    {"reason": "run_scope_mismatch"},
                )
        allowed = _bridge_allowed_tool_names(run_id, self.allowed_by_run, catalog=self.tool_catalog)
        active = self.allowed_by_run.get(run_id, set()) if run_id is not None else set()
        if target_name not in allowed or (
            run_id is not None
            and target_name in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
            and target_name not in active
        ):
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
        response_mode = _bridge_response_mode(target_args)
        forwarded_args = _bridge_forward_arguments(
            catalog=self.tool_catalog,
            tool_name=target_name,
            arguments=scoped_args,
            response_mode=response_mode,
        )
        if run_id is not None and target_name in active and "run_token" not in forwarded_args:
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
            self._update_workspace_scope(out, tool_name=target_name, arguments=workspace_args)
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
    if run_id is not None and active_step_tools and tool_name not in active_step_tools:
        data["reason"] = "not_granted_to_active_step"
        data["repair"] = {
            "hint": (
                "Use a tool granted to the running step, or move to a step that grants this tool."
            )
        }
        return data
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
    data["reason"] = "tool_not_available_in_current_bridge_scope"
    return data


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
