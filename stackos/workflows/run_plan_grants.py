"""Pure helpers for StackOS run-plan MCP tool grants.

Run plans are agent-authored configuration, not executable policy code. This
module parses the static grant snapshot shape that templates/runs can store and
returns the exact MCP tools a running step is allowed to use. The daemon still
does the authoritative scope checks at call time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RUN_PLAN_CONTROLLER_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "runPlan.claimStep",
        "runPlan.recordStep",
    }
)

RUN_PLAN_GRANTABLE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "action.execute",
        "agentRequest.create",
        "artifact.create",
        "context.query",
        "context.snapshot",
        "decision.record",
        "experiment.create",
        "experiment.recordDecision",
        "experiment.recordObservation",
        "learning.create",
        "learning.update",
        "resource.upsert",
    }
)

RUN_PLAN_ADMIN_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "auth.revoke",
        "auth.start",
        "plugin.disable",
        "plugin.enable",
        "runPlan.update",
        "workflowTemplate.fork",
        "workflowTemplate.save",
    }
)


@dataclass(frozen=True)
class RunPlanMcpToolGrant:
    """One explicit MCP tool grant for one run-plan step."""

    step_id: str
    tool_name: str
    plugin_slug: str | None = None
    resource_key: str | None = None
    action_refs: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()


def _as_nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _as_string_list(value: Any, *, label: str) -> list[str]:
    if isinstance(value, str):
        return [_as_nonempty_string(value, label=label)]
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a string or list of strings")
    out: list[str] = []
    for index, item in enumerate(value):
        out.append(_as_nonempty_string(item, label=f"{label}[{index}]"))
    return out


def _optional_string(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _as_nonempty_string(value, label=label)


def _validate_grant_tool(tool_name: str) -> None:
    if tool_name in RUN_PLAN_ADMIN_ONLY_TOOL_NAMES:
        raise ValueError(f"{tool_name!r} is an admin/setup tool and cannot be run-plan granted")
    if tool_name not in RUN_PLAN_GRANTABLE_TOOL_NAMES:
        raise ValueError(f"{tool_name!r} is not a run-plan grantable tool")


def _require_context_query_filters(
    *,
    tool_name: str,
    sources: tuple[str, ...],
    fields: tuple[str, ...],
    label: str,
) -> None:
    if tool_name != "context.query":
        return
    if not sources or not fields:
        raise ValueError(f"{label} grants for 'context.query' must include sources and fields")


def _require_action_execute_refs(
    *,
    tool_name: str,
    action_refs: tuple[str, ...],
    label: str,
) -> None:
    if tool_name != "action.execute":
        return
    if not action_refs:
        raise ValueError(f"{label} grants for 'action.execute' must include action_refs")


def _validate_step(step_id: str, *, step_ids: set[str] | None) -> None:
    if step_ids is not None and step_id not in step_ids:
        raise ValueError(f"mcp tool grant references unknown step {step_id!r}")


def parse_run_plan_mcp_tool_grants(
    grant_snapshot_json: dict[str, Any] | None,
    *,
    step_ids: set[str] | None = None,
) -> list[RunPlanMcpToolGrant]:
    """Parse and validate the supported run-plan MCP grant shapes.

    Supported shapes:

    - ``{"mcp_tool_grants": [{"step_id": "write", "tool": "resource.upsert"}]}``
    - ``{"mcp_tool_grants": [{"step_id": "write", "tools": ["resource.upsert"]}]}``
    - ``{"step_tools": {"write": ["resource.upsert"]}}`` for compact templates.

    Other keys in the grant snapshot are ignored so existing metadata such as
    ``credential_ref`` remains valid while the MCP grant contract evolves.
    """
    if grant_snapshot_json is None:
        return []
    if not isinstance(grant_snapshot_json, dict):
        raise ValueError("run plan grants must be an object")

    grants: list[RunPlanMcpToolGrant] = []
    raw_entries = grant_snapshot_json.get("mcp_tool_grants")
    if raw_entries is None:
        raw_entries = grant_snapshot_json.get("tool_grants")
    if raw_entries is not None:
        if not isinstance(raw_entries, list):
            raise ValueError("mcp_tool_grants must be a list")
        for index, item in enumerate(raw_entries):
            if not isinstance(item, dict):
                raise ValueError(f"mcp_tool_grants[{index}] must be an object")
            step_id = _as_nonempty_string(
                item.get("step_id"),
                label=f"mcp_tool_grants[{index}].step_id",
            )
            _validate_step(step_id, step_ids=step_ids)
            tools: list[str] = []
            if "tool" in item:
                tools.extend(_as_string_list(item["tool"], label=f"mcp_tool_grants[{index}].tool"))
            if "tool_name" in item:
                tools.extend(
                    _as_string_list(
                        item["tool_name"],
                        label=f"mcp_tool_grants[{index}].tool_name",
                    )
                )
            if "tools" in item:
                tools.extend(
                    _as_string_list(item["tools"], label=f"mcp_tool_grants[{index}].tools")
                )
            if not tools:
                raise ValueError(f"mcp_tool_grants[{index}] must name tool or tools")
            sources = tuple(
                _as_string_list(
                    item.get("sources", []),
                    label=f"mcp_tool_grants[{index}].sources",
                )
            )
            fields = tuple(
                _as_string_list(
                    item.get("fields", []),
                    label=f"mcp_tool_grants[{index}].fields",
                )
            )
            action_refs = tuple(
                _as_string_list(
                    item.get("action_refs", item.get("action_ref", [])),
                    label=f"mcp_tool_grants[{index}].action_refs",
                )
            )
            for tool_name in dict.fromkeys(tools):
                _validate_grant_tool(tool_name)
                _require_context_query_filters(
                    tool_name=tool_name,
                    sources=sources,
                    fields=fields,
                    label=f"mcp_tool_grants[{index}]",
                )
                _require_action_execute_refs(
                    tool_name=tool_name,
                    action_refs=action_refs,
                    label=f"mcp_tool_grants[{index}]",
                )
                grants.append(
                    RunPlanMcpToolGrant(
                        step_id=step_id,
                        tool_name=tool_name,
                        plugin_slug=_optional_string(
                            item.get("plugin_slug"),
                            label=f"mcp_tool_grants[{index}].plugin_slug",
                        ),
                        resource_key=_optional_string(
                            item.get("resource_key"),
                            label=f"mcp_tool_grants[{index}].resource_key",
                        ),
                        action_refs=action_refs,
                        sources=sources,
                        fields=fields,
                    )
                )

    raw_step_tools = grant_snapshot_json.get("step_tools")
    if raw_step_tools is not None:
        if not isinstance(raw_step_tools, dict):
            raise ValueError("step_tools must be an object keyed by step id")
        for step_id_raw, tools_raw in raw_step_tools.items():
            step_id = _as_nonempty_string(step_id_raw, label="step_tools key")
            _validate_step(step_id, step_ids=step_ids)
            tools = _as_string_list(tools_raw, label=f"step_tools[{step_id!r}]")
            for tool_name in dict.fromkeys(tools):
                _validate_grant_tool(tool_name)
                _require_context_query_filters(
                    tool_name=tool_name,
                    sources=(),
                    fields=(),
                    label=f"step_tools[{step_id!r}]",
                )
                _require_action_execute_refs(
                    tool_name=tool_name,
                    action_refs=(),
                    label=f"step_tools[{step_id!r}]",
                )
                grants.append(RunPlanMcpToolGrant(step_id=step_id, tool_name=tool_name))

    return grants


def allowed_tools_for_run_plan_step(
    grant_snapshot_json: dict[str, Any] | None,
    *,
    step_id: str,
) -> set[str]:
    """Return the grantable MCP tool names explicitly enabled for ``step_id``."""
    return {
        grant.tool_name
        for grant in parse_run_plan_mcp_tool_grants(grant_snapshot_json)
        if grant.step_id == step_id
    }


def validate_run_plan_mcp_tool_grants(
    grant_snapshot_json: dict[str, Any] | None,
    *,
    step_ids: set[str],
) -> None:
    """Validate supported MCP grants inside a run-plan schema."""
    parse_run_plan_mcp_tool_grants(grant_snapshot_json, step_ids=step_ids)


__all__ = [
    "RUN_PLAN_ADMIN_ONLY_TOOL_NAMES",
    "RUN_PLAN_CONTROLLER_TOOL_NAMES",
    "RUN_PLAN_GRANTABLE_TOOL_NAMES",
    "RunPlanMcpToolGrant",
    "allowed_tools_for_run_plan_step",
    "parse_run_plan_mcp_tool_grants",
    "validate_run_plan_mcp_tool_grants",
]
