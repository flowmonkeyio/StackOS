"""Agent-facing MCP bridge policy for installable plugins.

The daemon exposes the full MCP catalog for UI/tests/internal automation. Plugin
clients do not need that much context, so this module filters the advertised
tool list to a compact control surface and provides the bridge-local toolbox
helpers used to reach setup/current-step tools intentionally.
"""

from __future__ import annotations

import json
from typing import Any

from content_stack.workflows.run_plan_grants import (
    RUN_PLAN_ADMIN_ONLY_TOOL_NAMES,
    RUN_PLAN_CONTROLLER_TOOL_NAMES,
    RUN_PLAN_GRANTABLE_TOOL_NAMES,
)

_AGENT_VISIBLE_TOOL_ORDER: tuple[str, ...] = (
    # Repo/project setup.
    "workspace.startSession",
    "workspace.resolve",
    "workspace.connect",
    "workspace.listBindings",
    "workspace.updateProfile",
    "project.list",
    "project.create",
    "project.get",
    "project.update",
    "project.setActive",
    "project.getActive",
    "auth.status",
    "auth.test",
    "localAgentChat.createMessage",
    "communicationBotProfile.list",
    "communicationBotProfile.get",
    "communicationBotProfile.upsert",
    "action.describe",
    "action.validate",
    "agentRequest.list",
    "agentRequest.get",
    "agentRequest.claim",
    "agentRequest.release",
    "agentRequest.linkRunPlan",
    "agentRequest.prepareRunPlan",
    "agentRequest.complete",
    "agentRequest.ignore",
    "plugin.list",
    "catalog.list",
    "catalog.describe",
    "capability.list",
    "capability.describe",
    "provider.list",
    "provider.describe",
    "resource.get",
    "resource.query",
    "artifact.get",
    "artifact.query",
    "context.query",
    "context.timeline",
    "learning.query",
    "experiment.query",
    "decision.query",
    "workflowTemplate.list",
    "workflowTemplate.describe",
    "workflowTemplate.validate",
    "runPlan.create",
    "runPlan.validate",
    "runPlan.start",
    "runPlan.get",
    "runPlan.list",
    "meta.enums",
    "run.get",
    "run.list",
    "run.heartbeat",
    "run.abort",
)
_AGENT_VISIBLE_TOOL_NAMES = frozenset(_AGENT_VISIBLE_TOOL_ORDER)
_TOOLBOX_DESCRIBE_TOOL = "toolbox.describe"
_TOOLBOX_CALL_TOOL = "toolbox.call"
_TOOLBOX_TOOL_NAMES = frozenset({_TOOLBOX_DESCRIBE_TOOL, _TOOLBOX_CALL_TOOL})
_AGENT_ADMIN_GATED_TOOL_NAMES: frozenset[str] = frozenset(RUN_PLAN_ADMIN_ONLY_TOOL_NAMES)
_AGENT_RUN_PLAN_GATED_TOOL_NAMES: frozenset[str] = frozenset(RUN_PLAN_GRANTABLE_TOOL_NAMES)
_AGENT_STEP_GATED_TOOL_NAMES: frozenset[str] = frozenset(RUN_PLAN_CONTROLLER_TOOL_NAMES)
_AGENT_GATED_TOOL_NAMES: frozenset[str] = (
    _AGENT_ADMIN_GATED_TOOL_NAMES | _AGENT_RUN_PLAN_GATED_TOOL_NAMES
)

# Tools that stay out of the advertised MCP list but are still useful during
# setup when an agent explicitly asks the bridge to describe/call them.
_AGENT_SETUP_TOOLBOX_NAMES: frozenset[str] = frozenset(
    {
        "budget.list",
        "budget.queryProject",
        "budget.set",
        "budget.update",
        "cost.queryAll",
        "cost.queryProject",
        "project.delete",
        "run.children",
        "run.cost",
        "run.finish",
        "run.insertStep",
        "run.listStepCalls",
        "run.listSteps",
        "run.recordStepCall",
        "run.start",
        "schedule.list",
        "schedule.remove",
        "schedule.set",
        "schedule.toggle",
        "sitemap.fetch",
    }
)
_AGENT_BASE_TOOLBOX_NAMES = _AGENT_VISIBLE_TOOL_NAMES | _AGENT_SETUP_TOOLBOX_NAMES


def _bridge_response_text(text: str) -> str:
    """Extract a JSON-RPC body from either JSON or single-event SSE text."""
    stripped = text.strip()
    if not stripped.startswith("event:"):
        return stripped
    for line in stripped.splitlines():
        if line.startswith("data:"):
            return line.removeprefix("data:").strip()
    return stripped


def bridge_error(request_id: object, code: int, message: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _bridge_toolbox_specs() -> list[dict[str, Any]]:
    """Return bridge-local virtual tools shown to agent MCP clients."""
    flexible_object = {"type": "object", "additionalProperties": True}
    return [
        {
            "name": _TOOLBOX_DESCRIBE_TOOL,
            "description": (
                "Describe hidden StackOS daemon tools available through the bridge. "
                "Use this before toolbox.call when setup, a run-plan step, or the current "
                "run-plan step mentions a tool that is not listed directly."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional exact daemon tool names to describe.",
                    },
                    "run_id": {
                        "type": "integer",
                        "description": "Run id used to refresh run-plan grants.",
                    },
                    "include_schemas": {
                        "type": "boolean",
                        "description": "When true and tool_names is omitted, include schemas.",
                        "default": False,
                    },
                },
                "additionalProperties": False,
            },
            "outputSchema": flexible_object,
            "_meta": {"content_stack_bridge_virtual": True},
        },
        {
            "name": _TOOLBOX_CALL_TOOL,
            "description": (
                "Call one hidden daemon tool by name. The bridge permits setup tools and "
                "the active run-plan step's allowed tools only; pass run_id so "
                "the bridge can refresh grants and inject the run token when available."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Exact daemon tool name."},
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Arguments for the daemon tool.",
                    },
                    "run_id": {
                        "type": "integer",
                        "description": "Run id for step-scoped run-plan grants.",
                    },
                },
                "required": ["tool_name", "arguments"],
                "additionalProperties": False,
            },
            "outputSchema": flexible_object,
            "_meta": {"content_stack_bridge_virtual": True},
        },
    ]


def _bridge_tool_call_name(payload: dict[str, Any]) -> str | None:
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) else None


def _bridge_tool_call_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    params = payload.get("params")
    if not isinstance(params, dict):
        return {}
    arguments = params.get("arguments")
    return arguments if isinstance(arguments, dict) else {}


def _bridge_as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _bridge_tool_catalog(response_text: str) -> dict[str, dict[str, Any]]:
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(envelope, dict):
        return {}
    result = envelope.get("result")
    if not isinstance(result, dict):
        return {}
    tools = result.get("tools")
    if not isinstance(tools, list):
        return {}
    catalog: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            catalog[tool["name"]] = tool
    return catalog


def _bridge_filter_tool_list_response(response_text: str) -> str:
    """Filter daemon ``tools/list`` down to the agent-facing bridge surface."""
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return response_text
    if not isinstance(envelope, dict):
        return response_text
    result = envelope.get("result")
    if not isinstance(result, dict):
        return response_text
    tools = result.get("tools")
    if not isinstance(tools, list):
        return response_text

    by_name = {
        tool["name"]: tool
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    filtered = [by_name[name] for name in _AGENT_VISIBLE_TOOL_ORDER if name in by_name]
    filtered.extend(_bridge_toolbox_specs())
    result["tools"] = filtered
    return json.dumps(envelope, default=str)


def _bridge_tool_result(request_id: object, structured: dict[str, Any], *, is_error: bool) -> str:
    text = json.dumps(structured, default=str, sort_keys=True)
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": structured,
                "isError": is_error,
            },
        },
        default=str,
    )


def _bridge_call_error(
    request_id: object,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> str:
    return _bridge_tool_result(
        request_id,
        {"code": code, "message": message, "data": data or {}},
        is_error=True,
    )


def _bridge_step_context(structured: object) -> dict[str, Any] | None:
    if not isinstance(structured, dict):
        return None
    if (
        "run_token" in structured
        or "run_id" in structured
        or "allowed_tools" in structured
        or "plan" in structured
    ):
        return structured
    data = structured.get("data")
    if isinstance(data, dict) and (
        "run_token" in data or "run_id" in data or "allowed_tools" in data or "plan" in data
    ):
        return data
    return None


def _bridge_cache_step_context(
    response_text: str,
    *,
    allowed_by_run: dict[int, set[str]],
    tokens_by_run: dict[int, str],
    plans_by_run: dict[int, int] | None = None,
) -> None:
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return
    if not isinstance(envelope, dict):
        return
    result = envelope.get("result")
    if not isinstance(result, dict):
        return
    context = _bridge_step_context(result.get("structuredContent"))
    if context is None:
        return
    run_id = _bridge_as_int(context.get("run_id"))
    if run_id is None:
        run_id = _bridge_as_int(result.get("run_id"))
    data = context.get("data")
    if isinstance(data, dict) and run_id is None:
        run_id = _bridge_as_int(data.get("run_id"))
    if run_id is None:
        return
    run_token = context.get("run_token")
    if not isinstance(run_token, str) and isinstance(data, dict):
        run_token = data.get("run_token")
    if isinstance(run_token, str) and run_token:
        tokens_by_run[run_id] = run_token
    plan = context.get("plan")
    if not isinstance(plan, dict) and isinstance(data, dict):
        plan = data.get("plan")
    if isinstance(plan, dict):
        plan_id = _bridge_as_int(plan.get("id"))
        if plan_id is not None and plans_by_run is not None:
            plans_by_run[run_id] = plan_id
    step_package = data if isinstance(data, dict) else context
    if isinstance(step_package, dict) and isinstance(step_package.get("step_id"), str):
        allowed_tools = step_package.get("allowed_tools")
        if isinstance(allowed_tools, list):
            allowed_by_run[run_id] = set(_AGENT_STEP_GATED_TOOL_NAMES) | {
                name for name in allowed_tools if isinstance(name, str) and name
            }
            return
    plan_package = data if isinstance(data, dict) and "steps" in data else context
    if isinstance(plan_package, dict) and isinstance(plan_package.get("steps"), list):
        running_step_tools: set[str] = set()
        for step in plan_package["steps"]:
            if not isinstance(step, dict) or step.get("status") != "running":
                continue
            allowed_tools = step.get("allowed_tools")
            if isinstance(allowed_tools, list):
                running_step_tools.update(
                    name for name in allowed_tools if isinstance(name, str) and name
                )
        allowed_by_run[run_id] = set(_AGENT_STEP_GATED_TOOL_NAMES) | running_step_tools
        return
    if isinstance(plan, dict) and isinstance(run_token, str) and run_token:
        allowed_by_run.setdefault(run_id, set()).update(_AGENT_STEP_GATED_TOOL_NAMES)


def _bridge_allowed_tool_names(
    run_id: int | None,
    allowed_by_run: dict[int, set[str]],
) -> set[str]:
    allowed = set(_AGENT_BASE_TOOLBOX_NAMES)
    if run_id is not None:
        allowed.update(allowed_by_run.get(run_id, set()))
    return allowed


def _bridge_toolbox_describe(
    request_id: object,
    *,
    catalog: dict[str, dict[str, Any]],
    arguments: dict[str, Any],
    run_id: int | None,
    allowed_by_run: dict[int, set[str]],
) -> str:
    allowed = _bridge_allowed_tool_names(run_id, allowed_by_run)
    requested_raw = arguments.get("tool_names")
    requested: list[str]
    if isinstance(requested_raw, list):
        requested = [name for name in requested_raw if isinstance(name, str) and name]
    elif arguments.get("include_schemas") is True:
        requested = sorted(name for name in allowed if name in catalog)
    else:
        requested = []

    described = [catalog[name] for name in requested if name in catalog and name in allowed]
    denied = [name for name in requested if name in catalog and name not in allowed]
    unknown = [name for name in requested if name not in catalog]
    active_step_tools = sorted(allowed_by_run.get(run_id, set())) if run_id is not None else []
    payload = {
        "visible_tool_names": list(_AGENT_VISIBLE_TOOL_ORDER),
        "setup_toolbox_tool_names": sorted(_AGENT_SETUP_TOOLBOX_NAMES & set(catalog)),
        "active_step_tool_names": active_step_tools,
        "available_tool_names": sorted(name for name in allowed if name in catalog),
        "described_tools": described,
        "denied_tool_names": denied,
        "unknown_tool_names": unknown,
        "usage": (
            "Use direct visible tools for setup and run-plan control. Use toolbox.call "
            "only for setup helpers, run-plan controller tools, or run-plan step grants."
        ),
    }
    return _bridge_tool_result(request_id, payload, is_error=False)


def _bridge_make_tool_call_payload(
    request_id: object,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        default=str,
    )


class AgentBridgeProxy:
    """Stateful bridge adapter for one plugin stdio session."""

    def __init__(self, *, url: str, headers: dict[str, str]) -> None:
        self.url = url
        self.headers = headers
        self.tool_catalog: dict[str, dict[str, Any]] = {}
        self.allowed_by_run: dict[int, set[str]] = {}
        self.tokens_by_run: dict[int, str] = {}
        self.plans_by_run: dict[int, int] = {}

    def request_daemon(self, client: Any, body: str) -> str:
        response = client.post(self.url, content=body, headers=self.headers)
        response.raise_for_status()
        return _bridge_response_text(response.text)

    def handle(self, client: Any, *, payload: object, line: str, request_id: object) -> str:
        if not isinstance(payload, dict):
            return self.request_daemon(client, line)
        if payload.get("method") == "tools/list":
            out = self.request_daemon(client, line)
            self.tool_catalog = _bridge_tool_catalog(out) or self.tool_catalog
            return _bridge_filter_tool_list_response(out)
        if payload.get("method") != "tools/call":
            return self.request_daemon(client, line)

        tool_name = _bridge_tool_call_name(payload)
        arguments = _bridge_tool_call_arguments(payload)
        if tool_name == _TOOLBOX_DESCRIBE_TOOL:
            return self._handle_toolbox_describe(client, request_id, arguments)
        if tool_name == _TOOLBOX_CALL_TOOL:
            return self._handle_toolbox_call(client, request_id, arguments)
        if tool_name in _AGENT_VISIBLE_TOOL_NAMES:
            out = self.request_daemon(client, line)
            self._cache_step_context(out)
            return out
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

    @staticmethod
    def _tool_list_body() -> str:
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "content-stack-bridge-tools",
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
            f"content-stack-bridge-plan-{run_plan_id}",
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
                f"Unknown content-stack tool {target_name!r}.",
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

        forwarded_args = dict(target_args)
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
        return out


__all__ = [
    "_AGENT_ADMIN_GATED_TOOL_NAMES",
    "_AGENT_BASE_TOOLBOX_NAMES",
    "_AGENT_GATED_TOOL_NAMES",
    "_AGENT_RUN_PLAN_GATED_TOOL_NAMES",
    "_AGENT_SETUP_TOOLBOX_NAMES",
    "_AGENT_STEP_GATED_TOOL_NAMES",
    "_AGENT_VISIBLE_TOOL_ORDER",
    "AgentBridgeProxy",
    "_bridge_cache_step_context",
    "_bridge_filter_tool_list_response",
    "_bridge_toolbox_describe",
    "bridge_error",
]
