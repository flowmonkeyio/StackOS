"""Bridge-local toolbox grants, descriptions, and run context cache."""

from __future__ import annotations

import json
from typing import Any

from stackos.mcp.permissions import RUN_PLAN_CONTROLLER_SKILL

from .catalog import _bridge_agent_tool_schema
from .constants import (
    _AGENT_ADMIN_GATED_TOOL_NAMES,
    _AGENT_BASE_TOOLBOX_NAMES,
    _AGENT_RUN_PLAN_GATED_TOOL_NAMES,
    _AGENT_SETUP_TOOLBOX_NAMES,
    _AGENT_STEP_GATED_TOOL_NAMES,
    _AGENT_VISIBLE_TOOL_ORDER,
    _TOOLBOX_CALL_TOOL,
    _TOOLBOX_DESCRIBE_TOOL,
)
from .protocol import _bridge_as_int, _bridge_tool_result


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
    return structured if isinstance(structured, dict) else None


def _bridge_cache_controller_run_context(
    response_text: str,
    *,
    allowed_by_run: dict[int, set[str]],
    tokens_by_run: dict[int, str],
    plans_by_run: dict[int, int] | None = None,
) -> None:
    structured = _bridge_structured_content(response_text)
    if structured is None:
        return
    data = structured.get("data") if isinstance(structured.get("data"), dict) else structured
    if not isinstance(data, dict):
        return
    run_id = _bridge_as_int(data.get("id")) or _bridge_as_int(structured.get("run_id"))
    if run_id is None:
        return
    metadata = data.get("metadata_json")
    if not isinstance(metadata, dict):
        return
    if metadata.get("skill_name") != RUN_PLAN_CONTROLLER_SKILL:
        return
    if data.get("status") != "running":
        return
    token = data.get("client_session_id")
    if not isinstance(token, str) or not token:
        return
    tokens_by_run[run_id] = token
    allowed_by_run.setdefault(run_id, set()).update(_AGENT_STEP_GATED_TOOL_NAMES)
    plan_id = _bridge_as_int(metadata.get("run_plan_id"))
    if plan_id is not None and plans_by_run is not None:
        plans_by_run[run_id] = plan_id


def _bridge_cache_step_context(
    response_text: str,
    *,
    allowed_by_run: dict[int, set[str]],
    tokens_by_run: dict[int, str],
    plans_by_run: dict[int, int] | None = None,
) -> None:
    structured = _bridge_structured_content(response_text)
    if structured is None:
        return
    context = _bridge_step_context(structured)
    if context is None:
        return
    run_id = _bridge_as_int(context.get("run_id"))
    if run_id is None:
        run_id = _bridge_as_int(structured.get("run_id"))
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
    has_run_token = run_id in tokens_by_run
    plan = context.get("plan")
    if not isinstance(plan, dict) and isinstance(data, dict):
        plan = data.get("plan")
    if isinstance(plan, dict):
        plan_id = _bridge_as_int(plan.get("id"))
        if plan_id is not None and plans_by_run is not None:
            plans_by_run[run_id] = plan_id
    step_package = data if isinstance(data, dict) else context
    if isinstance(step_package, dict) and isinstance(step_package.get("step_id"), str):
        if not has_run_token:
            return
        allowed_tools = step_package.get("allowed_tools")
        if isinstance(allowed_tools, list):
            allowed_by_run[run_id] = set(_AGENT_STEP_GATED_TOOL_NAMES) | {
                name for name in allowed_tools if isinstance(name, str) and name
            }
            return
    plan_package = data if isinstance(data, dict) and "steps" in data else context
    if isinstance(plan_package, dict) and isinstance(plan_package.get("steps"), list):
        if not has_run_token:
            return
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
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> set[str]:
    allowed = set(_AGENT_BASE_TOOLBOX_NAMES)
    allowed.update(_bridge_direct_operation_tool_names(catalog))
    if run_id is not None:
        allowed.update(allowed_by_run.get(run_id, set()))
    return allowed


def _bridge_operation_backed_names(
    *,
    catalog: dict[str, dict[str, Any]],
    names: set[str],
) -> list[str]:
    backed: list[str] = []
    for name in names:
        meta = catalog.get(name, {}).get("_meta")
        if isinstance(meta, dict) and isinstance(meta.get("operation_name"), str):
            backed.append(name)
    return sorted(backed)


def _tool_operation_context(tool: dict[str, Any]) -> dict[str, Any] | None:
    meta = tool.get("_meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("operation_name"), str):
        return None
    out: dict[str, Any] = {
        "name": meta["operation_name"],
        "describe_tool": "operation.describe",
        "describe_arguments": {"name": meta["operation_name"]},
    }
    for source_key, output_key in (
        ("operation_category", "category"),
        ("grant_policy", "grant_policy"),
        ("secret_policy", "secret_policy"),
    ):
        value = meta.get(source_key)
        if isinstance(value, str) and value:
            out[output_key] = value
    purpose = meta.get("purpose")
    if isinstance(purpose, str) and purpose:
        out["summary"] = purpose.split(". ", 1)[0].strip()
    response_policy = meta.get("response_policy")
    if isinstance(response_policy, dict):
        out["response_policy"] = response_policy
    return out


def _grant_policy_is_local_admin(grant_policy: str | None) -> bool:
    if grant_policy is None:
        return False
    normalized = grant_policy.strip().lower()
    return normalized == "admin-only" or normalized.startswith("local-admin")


def _grant_policy_is_agent_direct(grant_policy: str | None) -> bool:
    if grant_policy is None:
        return False
    normalized = grant_policy.strip().lower()
    return normalized.startswith("direct-") and not _grant_policy_is_local_admin(normalized)


def _bridge_direct_operation_tool_names(catalog: dict[str, dict[str, Any]] | None) -> set[str]:
    if not catalog:
        return set()
    names: set[str] = set()
    for name, tool in catalog.items():
        operation = _tool_operation_context(tool)
        if operation is None:
            continue
        grant_policy = operation.get("grant_policy")
        if isinstance(grant_policy, str) and _grant_policy_is_agent_direct(grant_policy):
            names.add(name)
    return names


def _denied_status(
    *,
    name: str,
    run_id: int | None,
    active_step_tools: set[str],
    grant_policy: str | None,
) -> dict[str, Any]:
    if name in _AGENT_ADMIN_GATED_TOOL_NAMES or _grant_policy_is_local_admin(grant_policy):
        return {
            "reason_code": "local_admin_required",
            "category": "admin",
            "grant_policy": grant_policy,
            "repair": {
                "hint": (
                    "Use an explicit operator/admin setup flow; this is not available "
                    "to the normal agent toolbox."
                ),
            },
        }
    if run_id is not None and active_step_tools and name not in active_step_tools:
        return {
            "reason_code": "not_granted_to_active_step",
            "category": "not_granted",
            "requires_active_step": True,
            "active_step_tool_names": sorted(active_step_tools),
            "repair": {
                "hint": (
                    "Use a tool granted to the running step, or move to a step "
                    "that grants this tool."
                ),
            },
        }
    if name in _AGENT_RUN_PLAN_GATED_TOOL_NAMES:
        return {
            "reason_code": "run_plan_step_grant_required",
            "category": "run_plan_step",
            "requires_run_id": True,
            "requires_active_step": True,
            "repair": {
                "steps": [
                    "Create or choose a run plan whose step grants this tool.",
                    "Start the run plan and claim the intended step.",
                    "Retry toolbox.describe/toolbox.call with run_id.",
                ],
            },
        }
    if name in _AGENT_STEP_GATED_TOOL_NAMES:
        return {
            "reason_code": "run_plan_controller_requires_run",
            "category": "run_plan_controller",
            "requires_run_id": True,
            "repair": {
                "hint": (
                    "Pass run_id from runPlan.start or runPlan.get so the bridge can "
                    "refresh controller grants."
                ),
            },
        }
    return {
        "reason_code": "tool_not_available_in_current_bridge_scope",
        "category": "not_available",
        "repair": {
            "hint": (
                "Use operation.list to find supported setup/workflow tools, or ask "
                "the operator for an admin flow."
            ),
        },
    }


def _bridge_tool_statuses(
    *,
    catalog: dict[str, dict[str, Any]],
    requested: list[str],
    allowed: set[str],
    run_id: int | None,
    active_step_tools: set[str],
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for name in requested:
        tool = catalog.get(name)
        if tool is None:
            statuses.append(
                {
                    "name": name,
                    "exists": False,
                    "allowed": False,
                    "reason_code": "unknown_tool",
                    "repair": {
                        "hint": "Call operation.list with a query to find the exact tool name."
                    },
                }
            )
            continue
        allowed_now = name in allowed
        direct_visible = name in _AGENT_VISIBLE_TOOL_ORDER
        row: dict[str, Any] = {
            "name": name,
            "exists": True,
            "allowed": allowed_now,
            "call_via": "direct_or_toolbox.call" if direct_visible else "toolbox.call",
        }
        operation = _tool_operation_context(tool)
        if operation is not None:
            row["operation"] = operation
        grant_policy = (
            operation.get("grant_policy")
            if isinstance(operation, dict) and isinstance(operation.get("grant_policy"), str)
            else None
        )
        if allowed_now:
            if name in _AGENT_STEP_GATED_TOOL_NAMES:
                row["reason_code"] = "run_plan_controller"
                row["category"] = "run_plan_controller"
            elif name in active_step_tools:
                row["reason_code"] = "active_step_granted"
                row["category"] = "run_plan_step"
            else:
                row["reason_code"] = "available"
                row["category"] = "setup"
            statuses.append(row)
            continue
        row.update(
            _denied_status(
                name=name,
                run_id=run_id,
                active_step_tools=active_step_tools,
                grant_policy=grant_policy,
            )
        )
        statuses.append(row)
    return statuses


def _bridge_toolbox_describe(
    request_id: object,
    *,
    catalog: dict[str, dict[str, Any]],
    arguments: dict[str, Any],
    run_id: int | None,
    allowed_by_run: dict[int, set[str]],
    injected_fields: set[str] | frozenset[str] | None = None,
) -> str:
    allowed = _bridge_allowed_tool_names(run_id, allowed_by_run, catalog=catalog)
    requested_raw = arguments.get("tool_names")
    requested: list[str]
    if isinstance(requested_raw, list):
        requested = [name for name in requested_raw if isinstance(name, str) and name]
    elif arguments.get("include_schemas") is True:
        requested = sorted(name for name in allowed if name in catalog)
    else:
        requested = []

    described = [
        _bridge_agent_tool_schema(catalog[name], injected_fields=set(injected_fields or ()))
        for name in requested
        if name in catalog and name in allowed
    ]
    denied = [name for name in requested if name in catalog and name not in allowed]
    unknown = [name for name in requested if name not in catalog]
    available_tool_names = sorted(name for name in allowed if name in catalog)
    active_step_tool_set = allowed_by_run.get(run_id, set()) if run_id is not None else set()
    controller_tools = sorted(active_step_tool_set & _AGENT_STEP_GATED_TOOL_NAMES)
    step_granted_tools = sorted(active_step_tool_set - _AGENT_STEP_GATED_TOOL_NAMES)
    active_step_tools = sorted(active_step_tool_set)
    setup_count = len(
        (_AGENT_SETUP_TOOLBOX_NAMES | _bridge_direct_operation_tool_names(catalog)) & set(catalog)
    )
    direct_visible = [
        name
        for name in (
            *_AGENT_VISIBLE_TOOL_ORDER,
            _TOOLBOX_DESCRIBE_TOOL,
            _TOOLBOX_CALL_TOOL,
        )
        if name in catalog or name in {_TOOLBOX_DESCRIBE_TOOL, _TOOLBOX_CALL_TOOL}
    ]
    discovery = {
        "normal_flow": (
            "If you know exact hidden operation/tool names, pass them in tool_names. "
            "If you need discovery, call operation.list through toolbox.call with grouped "
            "compact output, then describe only the exact names you intend to use."
        ),
        "next_calls": [
            {
                "tool": "toolbox.call",
                "arguments": {
                    "tool_name": "operation.list",
                    "arguments": {
                        "surface": "mcp",
                        "mode": "grouped",
                        "response_mode": "compact",
                    },
                },
            },
            {
                "tool": "toolbox.describe",
                "arguments": {"tool_names": ["tracker.status"]},
            },
        ],
        "diagnostics": (
            "Use include_schemas=true only for debugging; it can return a large schema dump."
        ),
    }
    payload = {
        "visible_tool_names": direct_visible,
        "active_step_tool_names": active_step_tools,
        "run_plan_controller_tool_names": controller_tools,
        "step_granted_tool_names": step_granted_tools,
        "available_tool_count": len(available_tool_names),
        "tool_categories": {
            "direct_visible": direct_visible,
            "setup_toolbox_count": setup_count,
            "active_step": active_step_tools,
            "operation_backed": _bridge_operation_backed_names(
                catalog=catalog,
                names={tool["name"] for tool in described if isinstance(tool.get("name"), str)},
            ),
        },
        "described_tools": described,
        "denied_tool_names": denied,
        "unknown_tool_names": unknown,
        "tool_statuses": _bridge_tool_statuses(
            catalog=catalog,
            requested=requested,
            allowed=allowed,
            run_id=run_id,
            active_step_tools=active_step_tool_set,
        ),
        "discovery": discovery,
        "usage": (
            "Use workspace.startSession to bind the current workspace, then use "
            "toolbox.describe/toolbox.call for setup helpers, workflow tools, and active "
            "run-plan step grants. Pass run_id when working inside a run plan."
        ),
    }
    if arguments.get("include_schemas") is True and not isinstance(requested_raw, list):
        payload["available_tool_names"] = available_tool_names
    return _bridge_tool_result(request_id, payload, is_error=False)
