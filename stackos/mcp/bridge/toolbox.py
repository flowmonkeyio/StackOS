"""Bridge-local toolbox grants, descriptions, and run context cache."""

from __future__ import annotations

import json
from typing import Any

from .catalog import _bridge_agent_tool_schema
from .constants import (
    _AGENT_BASE_TOOLBOX_NAMES,
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


def _bridge_toolbox_describe(
    request_id: object,
    *,
    catalog: dict[str, dict[str, Any]],
    arguments: dict[str, Any],
    run_id: int | None,
    allowed_by_run: dict[int, set[str]],
    injected_fields: set[str] | frozenset[str] | None = None,
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

    described = [
        _bridge_agent_tool_schema(catalog[name], injected_fields=set(injected_fields or ()))
        for name in requested
        if name in catalog and name in allowed
    ]
    denied = [name for name in requested if name in catalog and name not in allowed]
    unknown = [name for name in requested if name not in catalog]
    available_tool_names = sorted(name for name in allowed if name in catalog)
    active_step_tools = sorted(allowed_by_run.get(run_id, set())) if run_id is not None else []
    setup_count = len(_AGENT_SETUP_TOOLBOX_NAMES & set(catalog))
    direct_visible = [
        name
        for name in (
            *_AGENT_VISIBLE_TOOL_ORDER,
            _TOOLBOX_DESCRIBE_TOOL,
            _TOOLBOX_CALL_TOOL,
        )
        if name in catalog or name in {_TOOLBOX_DESCRIBE_TOOL, _TOOLBOX_CALL_TOOL}
    ]
    payload = {
        "visible_tool_names": direct_visible,
        "active_step_tool_names": active_step_tools,
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
        "usage": (
            "Use workspace.startSession to bind the current workspace, then use "
            "toolbox.describe/toolbox.call for setup helpers, workflow tools, and active "
            "run-plan step grants. Pass run_id when working inside a run plan."
        ),
    }
    if arguments.get("include_schemas") is True and not isinstance(requested_raw, list):
        payload["available_tool_names"] = available_tool_names
    return _bridge_tool_result(request_id, payload, is_error=False)
