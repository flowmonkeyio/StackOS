"""Agent-facing MCP bridge policy for installable plugins.

The daemon exposes the full MCP catalog for UI/tests/internal automation. Plugin
clients do not need that much context, so this module filters the advertised
tool list to a compact control surface and provides the bridge-local toolbox
helpers used to reach setup/current-step tools intentionally.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from stackos.workflows.run_plan_grants import (
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
    "auth.status",
    "auth.test",
    "toolProfile.resolve",
    "localAgentChat.createMessage",
    "communicationProfile.list",
    "communicationProfile.get",
    "communicationProfile.upsert",
    "communicationSurface.list",
    "communicationSurface.upsert",
    "communicationContact.list",
    "communicationContact.upsert",
    "communicationMembership.list",
    "communicationMembership.upsert",
    "communicationTarget.list",
    "communicationTarget.resolve",
    "communicationTarget.upsert",
    "communicationRoute.list",
    "communicationRoute.upsert",
    "communicationContext.query",
    "communicationBotProfile.list",
    "communicationBotProfile.get",
    "communicationBotProfile.upsert",
    "action.describe",
    "action.validate",
    "action.run",
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
_AGENT_COMPACT_DEFAULT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "workspace.startSession",
        "workspace.resolve",
        "workspace.connect",
        "auth.status",
        "toolProfile.resolve",
        "communicationProfile.list",
        "communicationProfile.get",
        "communicationTarget.list",
        "communicationTarget.resolve",
        "communicationBotProfile.list",
        "communicationBotProfile.get",
        "action.describe",
        "catalog.describe",
    }
)
_AGENT_RESPONSE_MODE_FIELD = "response_mode"
_AGENT_GLOBAL_DISCOVERY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "workspace.startSession",
        "workspace.resolve",
        "workspace.connect",
        "plugin.list",
        "catalog.list",
        "catalog.describe",
        "capability.list",
        "capability.describe",
        "provider.list",
        "provider.describe",
        "action.describe",
        "action.validate",
        "meta.enums",
    }
)
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
            "_meta": {"stackos_bridge_virtual": True},
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
            "_meta": {"stackos_bridge_virtual": True},
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


def _bridge_filter_tool_list_response(
    response_text: str,
    *,
    scoped_project_id: int | None = None,
    injected_fields: set[str] | frozenset[str] | None = None,
) -> str:
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
    injected = set(injected_fields or ())
    if scoped_project_id is not None:
        injected.add("project_id")
    filtered = [
        _bridge_agent_tool_schema(tool, injected_fields=injected) for tool in filtered
    ]
    filtered.extend(_bridge_toolbox_specs())
    result["tools"] = filtered
    return json.dumps(envelope, default=str)


def _bridge_agent_tool_schema(
    tool: dict[str, Any],
    *,
    injected_fields: set[str],
) -> dict[str, Any]:
    clone = _bridge_relax_injected_schema(tool, injected_fields=injected_fields)
    if clone.get("name") in _AGENT_COMPACT_DEFAULT_TOOL_NAMES:
        clone = _bridge_add_response_mode_schema(clone)
    return clone


def _bridge_relax_injected_schema(
    tool: dict[str, Any],
    *,
    injected_fields: set[str],
) -> dict[str, Any]:
    """Make bridge-injected fields optional in advertised schemas."""
    clone = json.loads(json.dumps(tool, default=str))
    schema = clone.get("inputSchema")
    if not isinstance(schema, dict):
        return clone
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return clone
    present = injected_fields & set(properties)
    if not present:
        return clone
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [item for item in required if item not in present]
    for field in present:
        prop = properties.get(field)
        if isinstance(prop, dict):
            description = prop.get("description")
            suffix = "Injected from the current workspace by the StackOS bridge."
            prop["description"] = f"{description} {suffix}".strip() if description else suffix
    return clone


def _bridge_add_response_mode_schema(tool: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(tool, default=str))
    schema = clone.get("inputSchema")
    if not isinstance(schema, dict):
        return clone
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        return clone
    properties.setdefault(
        _AGENT_RESPONSE_MODE_FIELD,
        {
            "type": "string",
            "enum": ["compact", "standard", "verbose"],
            "default": "compact",
            "description": (
                "Agent response shape. compact is default; standard/verbose returns "
                "the full daemon payload for diagnostics."
            ),
        },
    )
    return clone


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


def _bridge_structured_content(response_text: str) -> dict[str, Any] | None:
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
    return structured if isinstance(structured, dict) else result


def _bridge_extract_project_id(response_text: str) -> int | None:
    structured = _bridge_structured_content(response_text)
    if structured is None:
        return None
    value = _bridge_as_int(structured.get("project_id"))
    if value is not None:
        return value
    data = structured.get("data")
    if isinstance(data, dict):
        value = _bridge_as_int(data.get("project_id"))
        if value is not None:
            return value
    binding = structured.get("binding")
    if isinstance(binding, dict):
        return _bridge_as_int(binding.get("project_id"))
    return None


def _bridge_tool_accepts_project_id(catalog: dict[str, dict[str, Any]], tool_name: str) -> bool:
    return _bridge_tool_accepts_field(catalog, tool_name, "project_id")


def _bridge_tool_accepts_field(
    catalog: dict[str, dict[str, Any]],
    tool_name: str,
    field: str,
) -> bool:
    schema = catalog.get(tool_name, {}).get("inputSchema")
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    return isinstance(properties, dict) and field in properties


def _bridge_scoped_arguments(
    *,
    catalog: dict[str, dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    scoped_project_id: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if scoped_project_id is None or not _bridge_tool_accepts_project_id(catalog, tool_name):
        return dict(arguments), None
    current = arguments.get("project_id")
    if current is None:
        merged = dict(arguments)
        merged["project_id"] = scoped_project_id
        return merged, None
    current_id = _bridge_as_int(current)
    if current_id == scoped_project_id:
        return dict(arguments), None
    return None, {
        "tool": tool_name,
        "scoped_project_id": scoped_project_id,
        "requested_project_id": current,
    }


def _bridge_normalized_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except OSError:
        return str(Path(value).expanduser().absolute())


def _bridge_path_is_same_or_child(path: str, root: str) -> bool:
    normalized_path = _bridge_normalized_path(path)
    normalized_root = _bridge_normalized_path(root)
    return normalized_path == normalized_root or normalized_path.startswith(
        normalized_root.rstrip("/") + "/"
    )


def _bridge_exact_path_match(path: str, expected: str) -> bool:
    return _bridge_normalized_path(path) == _bridge_normalized_path(expected)


def _bridge_apply_expected_argument(
    *,
    out: dict[str, Any],
    tool_name: str,
    field: str,
    expected: str | None,
    path_policy: str | None = None,
) -> dict[str, Any] | None:
    if expected is None:
        return None
    requested = out.get(field)
    if requested is None:
        out[field] = expected
        return None
    if not isinstance(requested, str):
        return {"tool": tool_name, "field": field, "expected": expected, "requested": requested}
    if path_policy == "same-or-child":
        matches = _bridge_path_is_same_or_child(requested, expected)
    elif path_policy == "exact":
        matches = _bridge_exact_path_match(requested, expected)
    else:
        matches = requested == expected
    if matches:
        return None
    return {"tool": tool_name, "field": field, "expected": expected, "requested": requested}


def _bridge_workspace_scoped_arguments(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    runtime: str,
    cwd: str | None,
    repo_fingerprint: str | None,
    git_remote_url: str | None,
    client_session_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if tool_name not in {"workspace.resolve", "workspace.startSession", "workspace.connect"}:
        return dict(arguments), None

    out = dict(arguments)
    checks: list[tuple[str, str | None, str | None]] = []
    if tool_name in {"workspace.resolve", "workspace.startSession"}:
        checks.extend(
            [
                ("cwd", cwd, "same-or-child"),
                ("repo_fingerprint", repo_fingerprint, None),
                ("git_remote_url", git_remote_url, None),
            ]
        )
    if tool_name == "workspace.startSession":
        out.setdefault("runtime", runtime)
        checks.append(("client_session_id", client_session_id, None))
    if tool_name == "workspace.connect":
        checks.extend(
            [
                ("repo_fingerprint", repo_fingerprint, None),
                ("git_remote_url", git_remote_url, None),
                ("last_known_root", cwd, "exact"),
            ]
        )

    for field, expected, path_policy in checks:
        error = _bridge_apply_expected_argument(
            out=out,
            tool_name=tool_name,
            field=field,
            expected=expected,
            path_policy=path_policy,
        )
        if error is not None:
            return None, error
    return out, None


def _bridge_scope_visibility_error(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    has_workspace_hints: bool,
    scoped_project_id: int | None,
    workspace_scope_error: str | None,
) -> dict[str, Any] | None:
    if not has_workspace_hints:
        return None
    if workspace_scope_error is not None and tool_name not in {
        "workspace.connect",
        "workspace.resolve",
        "workspace.startSession",
    }:
        return {
            "tool": tool_name,
            "reason": "workspace_scope_failed",
            "detail": workspace_scope_error,
        }
    if scoped_project_id is not None:
        return None
    if tool_name == "workspace.connect":
        return None
    if tool_name in _AGENT_GLOBAL_DISCOVERY_TOOL_NAMES and arguments.get("project_id") is None:
        return None
    return {
        "tool": tool_name,
        "reason": "workspace_not_connected",
        "hint": "Bind this repository with workspace.connect before using project-scoped tools.",
    }


def _bridge_replace_tool_call_arguments(
    payload: dict[str, Any],
    *,
    arguments: dict[str, Any],
) -> str:
    cloned = json.loads(json.dumps(payload, default=str))
    params = cloned.setdefault("params", {})
    if isinstance(params, dict):
        params["arguments"] = arguments
    return json.dumps(cloned, default=str)


def _bridge_response_mode(arguments: dict[str, Any]) -> str:
    raw = arguments.get(_AGENT_RESPONSE_MODE_FIELD)
    if raw in {"compact", "standard", "verbose"}:
        return str(raw)
    return "compact"


def _bridge_forward_arguments(
    *,
    catalog: dict[str, dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    response_mode: str,
) -> dict[str, Any]:
    forwarded = dict(arguments)
    forwarded.pop(_AGENT_RESPONSE_MODE_FIELD, None)
    if (
        response_mode == "verbose"
        and _bridge_tool_accepts_field(catalog, tool_name, "verbose")
        and "verbose" not in forwarded
    ):
        forwarded["verbose"] = True
    return forwarded


def _bridge_compact_tool_response(
    *,
    tool_name: str,
    response_text: str,
    response_mode: str,
) -> str:
    if response_mode != "compact" or tool_name not in _AGENT_COMPACT_DEFAULT_TOOL_NAMES:
        return response_text
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return response_text
    if not isinstance(envelope, dict):
        return response_text
    result = envelope.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return response_text
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return response_text
    compact = _bridge_compact_structured(tool_name, structured)
    if compact is None:
        return response_text
    text = json.dumps(compact, default=str, sort_keys=True)
    result["structuredContent"] = compact
    result["content"] = [{"type": "text", "text": text}]
    return json.dumps(envelope, default=str)


def _bridge_compact_structured(tool_name: str, structured: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name in {"workspace.startSession", "workspace.resolve", "workspace.connect"}:
        return _bridge_compact_workspace(structured)
    if tool_name == "auth.status":
        return _bridge_compact_auth_status(structured)
    if tool_name == "toolProfile.resolve":
        return _bridge_compact_tool_profile_resolve(structured)
    if tool_name == "communicationBotProfile.list":
        return _bridge_compact_profile_page(structured)
    if tool_name == "communicationBotProfile.get":
        return _bridge_compact_profile(structured)
    if tool_name == "action.describe":
        return _bridge_compact_action_describe(structured)
    if tool_name == "catalog.describe":
        return _bridge_compact_catalog_describe(structured)
    return None


def _bridge_compact_workspace(structured: dict[str, Any]) -> dict[str, Any]:
    data = structured.get("data") if isinstance(structured.get("data"), dict) else structured
    assert isinstance(data, dict)
    project_id = _bridge_as_int(structured.get("project_id")) or _bridge_as_int(
        data.get("project_id")
    )
    binding_id = _bridge_as_int(data.get("workspace_binding_id")) or _bridge_as_int(data.get("id"))
    compact_data = {
        "workspace_bound": project_id is not None,
        "project_id": project_id,
        "workspace_binding_id": binding_id,
    }
    if isinstance(data.get("runtime"), str):
        compact_data["runtime"] = data["runtime"]
    if isinstance(data.get("client_session_id"), str):
        compact_data["client_session_id"] = data["client_session_id"]
    if "data" in structured:
        return {
            "data": compact_data,
            "project_id": project_id,
            "run_id": structured.get("run_id"),
        }
    return compact_data


def _bridge_compact_auth_status(structured: dict[str, Any]) -> dict[str, Any]:
    connections = [
        _bridge_compact_connection(item)
        for item in structured.get("connections", [])
        if isinstance(item, dict)
    ]
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for connection in connections:
        key = str(connection.get("provider_key") or "")
        by_provider.setdefault(key, []).append(connection)
    providers: list[dict[str, Any]] = []
    for provider in structured.get("providers", []):
        if not isinstance(provider, dict):
            continue
        key = str(provider.get("key") or "")
        provider_connections = by_provider.get(key, [])
        providers.append(
            {
                "key": key,
                "name": provider.get("name"),
                "auth_type": provider.get("auth_type"),
                "status": "connected" if provider_connections else "missing",
                "credential_refs": [
                    item["credential_ref"]
                    for item in provider_connections
                    if isinstance(item.get("credential_ref"), str)
                ],
                "profile_keys": [
                    item["profile_key"]
                    for item in provider_connections
                    if isinstance(item.get("profile_key"), str)
                ],
                "setup_required": not bool(provider_connections),
            }
        )
    return {
        "project_id": structured.get("project_id"),
        "provider_key": structured.get("provider_key"),
        "providers": providers,
        "connections": connections,
    }


def _bridge_compact_connection(connection: dict[str, Any]) -> dict[str, Any]:
    account = connection.get("account") if isinstance(connection.get("account"), dict) else {}
    return {
        "credential_ref": connection.get("credential_ref"),
        "provider_key": connection.get("provider_key"),
        "profile_key": connection.get("profile_key"),
        "status": connection.get("status"),
        "auth_type": connection.get("auth_type"),
        "display_name": account.get("display_name"),
        "provider_account_id": account.get("provider_account_id"),
        "setup_required": bool(connection.get("setup_required", False)),
    }


def _bridge_compact_tool_profile_resolve(structured: dict[str, Any]) -> dict[str, Any]:
    provider = structured.get("provider") if isinstance(structured.get("provider"), dict) else {}
    credential = (
        structured.get("credential") if isinstance(structured.get("credential"), dict) else {}
    )
    profile = (
        structured.get("tool_profile") if isinstance(structured.get("tool_profile"), dict) else {}
    )
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    access = profile.get("access_policy") if isinstance(profile.get("access_policy"), dict) else {}
    trigger = (
        profile.get("trigger_policy") if isinstance(profile.get("trigger_policy"), dict) else {}
    )
    account = credential.get("account") if isinstance(credential.get("account"), dict) else {}
    return {
        "project_id": structured.get("project_id"),
        "provider_key": structured.get("provider_key"),
        "ready": bool(structured.get("ready")),
        "missing": structured.get("missing", []),
        "warnings": structured.get("warnings", []),
        "next_action": structured.get("next_action"),
        "provider": {
            "provider_key": provider.get("provider_key"),
            "plugin_slug": provider.get("plugin_slug"),
            "auth_type": provider.get("auth_type"),
            "setup_required": bool(provider.get("setup_required", False)),
        },
        "credential": {
            "credential_ref": credential.get("credential_ref"),
            "provider_key": credential.get("provider_key"),
            "profile_key": credential.get("profile_key"),
            "status": credential.get("status"),
            "display_name": account.get("display_name"),
            "provider_account_id": account.get("provider_account_id"),
            "setup_required": bool(credential.get("setup_required", False)),
        }
        if credential
        else None,
        "tool_profile": {
            "kind": profile.get("kind"),
            "key": profile.get("key"),
            "ref": profile.get("ref"),
            "enabled": profile.get("enabled"),
            "auth_profile_key": profile.get("auth_profile_key"),
            "identity": {
                "display_name": identity.get("display_name"),
                "purpose": identity.get("purpose"),
                "voice": identity.get("voice"),
            },
            "access": {
                "dm_mode": access.get("dm_mode"),
                "group_mode": access.get("group_mode"),
                "user_mode": access.get("user_mode"),
                "allowed_chat_refs": access.get("allowed_chat_refs", []),
                "allowed_user_refs": access.get("allowed_user_refs", []),
            },
            "trigger": {
                "dm_trigger": trigger.get("dm_trigger"),
                "group_trigger": trigger.get("group_trigger"),
                "command_count": len(trigger.get("commands", []) or []),
                "mention_patterns": trigger.get("mention_patterns", []),
            },
        }
        if profile
        else None,
    }


def _bridge_compact_profile_page(structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "items": [
            _bridge_compact_profile(item)
            for item in structured.get("items", [])
            if isinstance(item, dict)
        ],
        "next_cursor": structured.get("next_cursor"),
        "total_estimate": structured.get("total_estimate"),
    }


def _bridge_compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    access = profile.get("access_policy") if isinstance(profile.get("access_policy"), dict) else {}
    trigger = (
        profile.get("trigger_policy") if isinstance(profile.get("trigger_policy"), dict) else {}
    )
    identity = profile.get("identity") if isinstance(profile.get("identity"), dict) else {}
    response = (
        profile.get("response_policy") if isinstance(profile.get("response_policy"), dict) else {}
    )
    commands = [
        {
            "command": item.get("command"),
            "enabled": item.get("enabled", True),
            "description": item.get("description"),
        }
        for item in trigger.get("commands", [])
        if isinstance(item, dict)
    ]
    return {
        "record_id": profile.get("record_id"),
        "project_id": profile.get("project_id"),
        "profile_ref": profile.get("external_id"),
        "key": profile.get("key"),
        "provider_key": profile.get("provider_key"),
        "auth_profile_key": profile.get("auth_profile_key"),
        "enabled": profile.get("enabled"),
        "bot_username": profile.get("bot_username"),
        "ingress_mode": profile.get("ingress_mode"),
        "allowed_updates": profile.get("allowed_updates", []),
        "identity": {
            "display_name": identity.get("display_name"),
            "purpose": identity.get("purpose"),
            "voice": identity.get("voice"),
        },
        "access": {
            "dm_mode": access.get("dm_mode"),
            "group_mode": access.get("group_mode"),
            "user_mode": access.get("user_mode"),
            "allowed_chat_refs": access.get("allowed_chat_refs", []),
            "allowed_user_refs": access.get("allowed_user_refs", []),
            "denied_chat_refs_count": len(access.get("denied_chat_refs", []) or []),
            "denied_user_refs_count": len(access.get("denied_user_refs", []) or []),
        },
        "trigger": {
            "dm_trigger": trigger.get("dm_trigger"),
            "group_trigger": trigger.get("group_trigger"),
            "mention_patterns": trigger.get("mention_patterns", []),
            "reply_to_bot_triggers": trigger.get("reply_to_bot_triggers"),
            "commands": commands,
        },
        "response_policy": response,
        "refs": profile.get("refs", {}),
    }


def _bridge_compact_action_describe(structured: dict[str, Any]) -> dict[str, Any]:
    manifest = structured.get("manifest") if isinstance(structured.get("manifest"), dict) else {}
    availability = (
        structured.get("availability") if isinstance(structured.get("availability"), dict) else {}
    )
    input_schema = (
        manifest.get("input_schema_json")
        if isinstance(manifest.get("input_schema_json"), dict)
        else {}
    )
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else {}
    required = input_schema.get("required") if isinstance(input_schema, dict) else []
    compact_properties: dict[str, Any] = {}
    if isinstance(properties, dict):
        for key, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            compact_properties[str(key)] = {
                name: prop.get(name)
                for name in ("type", "enum", "description")
                if prop.get(name) is not None
            }
    return {
        "action_ref": manifest.get("action_ref"),
        "plugin_slug": manifest.get("plugin_slug"),
        "action_key": manifest.get("action_key"),
        "provider_key": manifest.get("provider_key"),
        "capability_key": manifest.get("capability_key"),
        "risk_level": manifest.get("risk_level"),
        "operation": manifest.get("operation"),
        "requires_credential": manifest.get("requires_credential"),
        "connector_registered": structured.get("connector_registered"),
        "execution_available": structured.get("execution_available"),
        "availability": {
            "status": availability.get("status"),
            "executable": availability.get("executable"),
            "reasons": availability.get("reasons", []),
            "credential_refs": availability.get("credential_refs", []),
            "budget_state": availability.get("budget_state"),
        },
        "input": {
            "required": required if isinstance(required, list) else [],
            "properties": compact_properties,
        },
        "docs": (manifest.get("config_json") or {}).get("docs", [])
        if isinstance(manifest.get("config_json"), dict)
        else [],
    }


def _bridge_compact_catalog_describe(structured: dict[str, Any]) -> dict[str, Any]:
    plugins = []
    for item in structured.get("plugins", []):
        if not isinstance(item, dict):
            continue
        plugin = item.get("plugin") if isinstance(item.get("plugin"), dict) else {}
        actions = item.get("actions") if isinstance(item.get("actions"), list) else []
        resources = item.get("resources") if isinstance(item.get("resources"), list) else []
        providers = item.get("providers") if isinstance(item.get("providers"), list) else []
        plugins.append(
            {
                "slug": plugin.get("slug"),
                "name": plugin.get("name"),
                "version": plugin.get("version"),
                "enabled_for_project": plugin.get("enabled_for_project"),
                "providers": [
                    provider.get("key") for provider in providers if isinstance(provider, dict)
                ],
                "actions": [
                    {
                        "action_ref": action.get("action_ref"),
                        "risk_level": action.get("risk_level"),
                        "provider_key": action.get("provider_key"),
                        "status": (action.get("availability") or {}).get("status")
                        if isinstance(action.get("availability"), dict)
                        else None,
                    }
                    for action in actions
                    if isinstance(action, dict)
                ],
                "resources": [
                    resource.get("key") for resource in resources if isinstance(resource, dict)
                ],
            }
        )
    return {"plugins": plugins}


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
            if tool_name in {"workspace.connect", "workspace.resolve", "workspace.startSession"}:
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
