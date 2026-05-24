"""MCP tool-grant matrix for StackOS core tools.

The matrix is the security boundary for MCP calls: every MCP
tool call resolves to a skill name (via the ``run_token`` ↔
``runs.client_session_id`` lookup), then this module checks the tool
against the skill's allow-list.

The matrix keeps two different agent pathways explicit:

- ``__system__`` — the bootstrap/configuration grant used before a run token
  exists. It can create/select projects, inspect catalogs, start run plans, and
  maintain project setup such as schedules and budgets. It must not execute
  workflow work: resource/artifact writes, memory writes, decision writes, and
  external actions require a run-plan step grant.
- ``__test__`` — a reserved full-grant sentinel for tests that bind it
  explicitly. Unmatched tokens never resolve to it.
Run-plan controller grants are dynamic: the stored run plan defines which
generic tools an agent may use for the active step.
"""

from __future__ import annotations

from typing import Any, NoReturn

from sqlmodel import Session, col, select

from stackos.db.models import Run, RunPlan, RunPlanStatus, RunPlanStep, RunPlanStepStatus
from stackos.mcp.errors import ToolNotGrantedError
from stackos.workflows.run_plan_grants import (
    RUN_PLAN_GRANTABLE_TOOL_NAMES,
    RunPlanMcpToolGrant,
    parse_run_plan_mcp_tool_grants,
)

# ---------------------------------------------------------------------------
# Sentinel skill names.
# ---------------------------------------------------------------------------


SYSTEM_SKILL = "__system__"
TEST_SKILL = "__test__"
INVALID_SKILL = "__invalid__"
RUN_PLAN_CONTROLLER_SKILL = "stackos/run-plan-controller"

_DEFAULT_CONTEXT_SOURCES: tuple[str, ...] = ("runs", "learnings", "experiments", "decisions")
_SAFE_CONTEXT_FIELDS: dict[str, frozenset[str]] = {
    "runs": frozenset({"kind", "status", "last_step", "metadata_json"}),
    "events": frozenset({"event_type", "title", "summary", "tags", "metadata_json"}),
    "index": frozenset(
        {"source_type", "source_id", "title", "summary", "domain", "status", "tags"}
    ),
    "snapshots": frozenset({"name", "query_json", "selected_sources_json", "summary_json"}),
    "learnings": frozenset({"statement", "domain", "confidence", "status", "review_state", "tags"}),
    "experiments": frozenset(
        {"name", "domain", "hypothesis", "status", "metric_targets_json", "variants"}
    ),
    "decisions": frozenset({"title", "decision", "rationale", "status", "tags"}),
    "metrics": frozenset({"metric_key", "metric_value", "dimensions_json", "captured_at"}),
}


# ---------------------------------------------------------------------------
# Tool-grant matrix.
# ---------------------------------------------------------------------------


_SYSTEM_TOOLS: frozenset[str] = frozenset(
    {
        "action.describe",
        "action.run",
        "action.validate",
        "agentRequest.claim",
        "agentRequest.complete",
        "agentRequest.get",
        "agentRequest.ignore",
        "agentRequest.linkRunPlan",
        "agentRequest.list",
        "agentRequest.prepareRunPlan",
        "agentRequest.release",
        "artifact.get",
        "artifact.query",
        "auth.status",
        "auth.test",
        "localAgentChat.createMessage",
        "ingressEndpoint.configure",
        "ingressEndpoint.refresh",
        "ingressEndpoint.routes",
        "ingressEndpoint.sync",
        "ingressEndpoint.status",
        "communicationContact.list",
        "communicationContact.upsert",
        "communicationContext.query",
        "communicationMembership.list",
        "communicationMembership.upsert",
        "communicationProfile.get",
        "communicationProfile.list",
        "communicationProfile.upsert",
        "communicationSurface.list",
        "communicationSurface.upsert",
        "communicationTarget.list",
        "communicationTarget.resolve",
        "communicationTarget.upsert",
        "communicationRoute.list",
        "communicationRoute.upsert",
        "toolProfile.resolve",
        "budget.list",
        "budget.queryProject",
        "budget.set",
        "budget.update",
        "context.query",
        "context.timeline",
        "cost.queryAll",
        "cost.queryProject",
        "catalog.describe",
        "catalog.list",
        "capability.describe",
        "capability.list",
        "decision.query",
        "experiment.query",
        "learning.query",
        "meta.enums",
        "plugin.list",
        "project.create",
        "project.delete",
        "project.get",
        "project.getActive",
        "project.list",
        "project.setActive",
        "project.update",
        "run.abort",
        "run.children",
        "run.cost",
        "run.finish",
        "run.get",
        "run.heartbeat",
        "run.insertStep",
        "run.list",
        "run.listStepCalls",
        "run.listSteps",
        "run.recordStepCall",
        "run.start",
        "runPlan.create",
        "runPlan.get",
        "runPlan.list",
        "runPlan.start",
        "runPlan.validate",
        "provider.describe",
        "provider.list",
        "resource.get",
        "resource.query",
        "schedule.list",
        "schedule.remove",
        "schedule.set",
        "schedule.toggle",
        "sitemap.fetch",
        "workflowTemplate.describe",
        "workflowTemplate.list",
        "workflowTemplate.validate",
        "workspace.connect",
        "workspace.listBindings",
        "workspace.resolve",
        "workspace.startSession",
        "workspace.updateProfile",
    }
)


_RUN_PLAN_CONTROL: frozenset[str] = frozenset(
    {
        "run.get",
        "run.heartbeat",
        "runPlan.claimStep",
        "runPlan.get",
        "runPlan.list",
        "runPlan.recordStep",
    }
)
_RUN_PLAN_DYNAMIC_TOOLS: frozenset[str] = frozenset(RUN_PLAN_GRANTABLE_TOOL_NAMES)
_RUN_PLAN_CONTROLLER_TOOLS: frozenset[str] = _RUN_PLAN_CONTROL | _RUN_PLAN_DYNAMIC_TOOLS


# The matrix proper. ``__system__`` is explicit bootstrap/setup authority, not
# a workflow execution grant. Operational work remains gated by run-plan step
# grants and provider calls flow through generic action connectors.
SKILL_TOOL_GRANTS: dict[str, frozenset[str]] = {
    SYSTEM_SKILL: _SYSTEM_TOOLS,
    TEST_SKILL: frozenset(),  # full grant; sentinel-checked in check_grant
    RUN_PLAN_CONTROLLER_SKILL: _RUN_PLAN_CONTROLLER_TOOLS,
}


# ---------------------------------------------------------------------------
# Public helpers.
# ---------------------------------------------------------------------------


def is_full_grant(skill_name: str) -> bool:
    """Return ``True`` iff ``skill_name`` carries an unrestricted grant.

    Only the explicit test sentinel bypasses the whitelist. System MCP
    calls use their explicit product-operation grant in ``SKILL_TOOL_GRANTS``.
    """
    return skill_name == TEST_SKILL


def resolve_run_token(
    token: str | None,
    session: Session,
) -> tuple[Run | None, str]:
    """Resolve a request's ``run_token`` to its calling skill.

    Lookup contract:

    - ``token=None`` → ``(None, "__system__")``. Direct MCP calls without
      a run token use the explicit bootstrap/setup allow-list.
    - ``token`` matches a row's ``runs.client_session_id`` →
      ``(run, skill_name)`` where ``skill_name`` comes from
      ``runs.metadata_json.skill_name``.
    - Token does not match any row → ``(None, "__invalid__")``.
    """
    if token is None or token == "":
        return None, SYSTEM_SKILL
    row = session.exec(select(Run).where(Run.client_session_id == token)).first()
    if row is None:
        return None, INVALID_SKILL
    metadata = row.metadata_json or {}
    skill_name = metadata.get("skill_name") or INVALID_SKILL
    return row, skill_name


def check_grant(tool_name: str, skill_name: str) -> None:
    """Raise ``ToolNotGrantedError`` if the skill cannot call the tool.

    The ``__test__`` sentinel remains a full-grant fixture escape hatch,
    but normal system/bootstrap calls use an explicit allow-list.
    """
    if is_full_grant(skill_name):
        return
    allowed = SKILL_TOOL_GRANTS.get(skill_name, frozenset())
    if tool_name in allowed:
        return
    raise ToolNotGrantedError(
        f"skill {skill_name!r} is not granted tool {tool_name!r}",
        data={
            "tool": tool_name,
            "skill": skill_name,
            "allowed": sorted(allowed),
        },
    )


def _model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, dict):
        return dict(value)
    return {}


def _arg_string_set(arguments: dict[str, Any], key: str) -> set[str] | None:
    raw = arguments.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return set(raw)
    return None


def _requested_strings(arguments: dict[str, Any], key: str) -> list[str] | None:
    raw = arguments.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return list(raw)
    return None


def _deny_context_fields(
    tool_name: str,
    *,
    source: str,
    denied_fields: set[str],
    allowed_fields: set[str],
) -> NoReturn:
    raise ToolNotGrantedError(
        "context fields beyond the direct safe set require a run-plan grant",
        data={
            "tool": tool_name,
            "skill": SYSTEM_SKILL,
            "source": source,
            "denied_fields": sorted(denied_fields),
            "allowed_fields": sorted(allowed_fields),
        },
    )


def _check_direct_context_fields(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    source: str | None = None,
) -> None:
    if source is not None:
        sources = [source]
    else:
        sources = _requested_strings(arguments, "sources") or list(_DEFAULT_CONTEXT_SOURCES)
    requested_fields = _requested_strings(arguments, "fields")
    for item in sources:
        allowed = _SAFE_CONTEXT_FIELDS.get(item)
        if allowed is None:
            continue
        fields = set(requested_fields or allowed)
        denied = fields - allowed
        if denied:
            _deny_context_fields(
                tool_name,
                source=item,
                denied_fields=denied,
                allowed_fields=set(allowed),
            )


def _grant_matches_arguments(
    grant: RunPlanMcpToolGrant,
    arguments: dict[str, Any],
) -> bool:
    if grant.action_refs:
        requested_action_ref = arguments.get("action_ref")
        if not isinstance(requested_action_ref, str):
            plugin_slug = arguments.get("plugin_slug")
            action_key = arguments.get("action_key")
            if isinstance(plugin_slug, str) and isinstance(action_key, str):
                requested_action_ref = f"{plugin_slug}.{action_key}"
        if requested_action_ref not in set(grant.action_refs):
            return False
    if grant.plugin_slug is not None and arguments.get("plugin_slug") != grant.plugin_slug:
        return False
    if grant.resource_key is not None and arguments.get("resource_key") != grant.resource_key:
        return False
    if grant.sources:
        requested_sources = _arg_string_set(arguments, "sources")
        if requested_sources is None or not requested_sources <= set(grant.sources):
            return False
    if grant.fields:
        requested_fields = _arg_string_set(arguments, "fields")
        if requested_fields is None or not requested_fields <= set(grant.fields):
            return False
    return True


def _deny_run_plan_tool(
    tool_name: str,
    *,
    reason: str,
    run_id: int | None = None,
    run_plan_id: int | None = None,
    step_id: str | None = None,
    allowed: set[str] | None = None,
) -> None:
    raise ToolNotGrantedError(
        reason,
        data={
            "tool": tool_name,
            "skill": RUN_PLAN_CONTROLLER_SKILL,
            "run_id": run_id,
            "run_plan_id": run_plan_id,
            "step_id": step_id,
            "allowed": sorted(allowed or set()),
        },
    )


def _running_run_plan_step(ctx: Any, tool_name: str) -> tuple[RunPlan, RunPlanStep]:
    run = getattr(ctx, "run", None)
    run_id = getattr(ctx, "run_id", None)
    session = getattr(ctx, "session", None)
    if run is None or session is None:
        _deny_run_plan_tool(
            tool_name,
            reason="run-plan scoped tools require a valid run token",
            run_id=run_id,
        )
    assert run is not None
    assert session is not None
    metadata_json = getattr(run, "metadata_json", None)
    metadata: dict[str, Any] = metadata_json if isinstance(metadata_json, dict) else {}
    run_plan_id = metadata.get("run_plan_id")
    if metadata.get("stackos_type") != "run-plan" or not isinstance(run_plan_id, int):
        _deny_run_plan_tool(
            tool_name,
            reason="run token is not bound to a StackOS run plan",
            run_id=run_id,
        )
    plan = session.get(RunPlan, run_plan_id)
    if plan is None or plan.run_id != run_id:
        _deny_run_plan_tool(
            tool_name,
            reason="run token is not scoped to this run plan",
            run_id=run_id,
            run_plan_id=run_plan_id,
        )
    if plan.status != RunPlanStatus.STARTED:
        _deny_run_plan_tool(
            tool_name,
            reason="run plan must be started with a running step for this tool",
            run_id=run_id,
            run_plan_id=plan.id,
        )
    steps = list(
        session.exec(
            select(RunPlanStep)
            .where(
                col(RunPlanStep.run_plan_id) == plan.id,
                col(RunPlanStep.status) == RunPlanStepStatus.RUNNING,
            )
            .order_by(col(RunPlanStep.position).asc())
        ).all()
    )
    if len(steps) != 1:
        _deny_run_plan_tool(
            tool_name,
            reason="run-plan scoped tools require exactly one running step",
            run_id=run_id,
            run_plan_id=plan.id,
        )
    return plan, steps[0]


def active_run_plan_step(ctx: Any, tool_name: str) -> tuple[RunPlan, RunPlanStep]:
    """Return the single running step for a valid run-plan controller token."""
    return _running_run_plan_step(ctx, tool_name)


def _check_run_plan_dynamic_grant(tool_name: str, ctx: Any, parsed_arguments: Any) -> None:
    arguments = _model_to_dict(parsed_arguments)
    plan, step = _running_run_plan_step(ctx, tool_name)
    requested_project_id = arguments.get("project_id")
    if requested_project_id != plan.project_id:
        _deny_run_plan_tool(
            tool_name,
            reason="run-plan scoped mutations must target the plan project",
            run_id=getattr(ctx, "run_id", None),
            run_plan_id=plan.id,
            step_id=step.step_id,
        )
    requested_run_id = arguments.get("run_id")
    if requested_run_id is not None and requested_run_id != getattr(ctx, "run_id", None):
        _deny_run_plan_tool(
            tool_name,
            reason="run-plan scoped mutations cannot spoof another run id",
            run_id=getattr(ctx, "run_id", None),
            run_plan_id=plan.id,
            step_id=step.step_id,
        )
    try:
        grants = [
            grant
            for grant in parse_run_plan_mcp_tool_grants(plan.grant_snapshot_json)
            if grant.step_id == step.step_id and grant.tool_name == tool_name
        ]
    except ValueError as exc:
        _deny_run_plan_tool(
            tool_name,
            reason=f"invalid run-plan grant snapshot: {exc}",
            run_id=getattr(ctx, "run_id", None),
            run_plan_id=plan.id,
            step_id=step.step_id,
        )
    allowed = {
        grant.tool_name
        for grant in parse_run_plan_mcp_tool_grants(plan.grant_snapshot_json)
        if grant.step_id == step.step_id
    }
    if not grants:
        _deny_run_plan_tool(
            tool_name,
            reason="tool is not granted to the active run-plan step",
            run_id=getattr(ctx, "run_id", None),
            run_plan_id=plan.id,
            step_id=step.step_id,
            allowed=allowed,
        )
    if tool_name == "action.execute":
        requested_action_ref = arguments.get("action_ref")
        if not isinstance(requested_action_ref, str):
            plugin_slug = arguments.get("plugin_slug")
            action_key = arguments.get("action_key")
            if isinstance(plugin_slug, str) and isinstance(action_key, str):
                requested_action_ref = f"{plugin_slug}.{action_key}"
        if not isinstance(requested_action_ref, str) or requested_action_ref not in set(
            step.action_refs_json or []
        ):
            _deny_run_plan_tool(
                tool_name,
                reason="action.execute must target an action_ref declared on the active step",
                run_id=getattr(ctx, "run_id", None),
                run_plan_id=plan.id,
                step_id=step.step_id,
                allowed=allowed,
            )
    if not any(_grant_matches_arguments(grant, arguments) for grant in grants):
        _deny_run_plan_tool(
            tool_name,
            reason="tool arguments do not match the active run-plan step grant",
            run_id=getattr(ctx, "run_id", None),
            run_plan_id=plan.id,
            step_id=step.step_id,
            allowed=allowed,
        )


def check_call_grant(tool_name: str, ctx: Any, parsed_arguments: Any | None = None) -> None:
    """Context-aware grant check used by the MCP dispatcher.

    Run-plan controller tokens also pass through a dynamic step check for
    generic mutation tools so a stored run plan, not agent discretion, defines
    what can be called.
    """
    skill_name = getattr(ctx, "skill_name", INVALID_SKILL)
    check_grant(tool_name, skill_name)
    if skill_name == SYSTEM_SKILL:
        arguments = _model_to_dict(parsed_arguments)
        if tool_name == "context.query":
            _check_direct_context_fields(tool_name, arguments)
        elif tool_name == "context.timeline":
            _check_direct_context_fields(tool_name, arguments, source="events")
        elif tool_name == "learning.query":
            _check_direct_context_fields(tool_name, arguments, source="learnings")
        elif tool_name == "experiment.query":
            _check_direct_context_fields(tool_name, arguments, source="experiments")
        elif tool_name == "decision.query":
            _check_direct_context_fields(tool_name, arguments, source="decisions")
        return
    if skill_name != RUN_PLAN_CONTROLLER_SKILL:
        return
    if tool_name not in _RUN_PLAN_DYNAMIC_TOOLS:
        return
    _check_run_plan_dynamic_grant(tool_name, ctx, parsed_arguments)


__all__ = [
    "INVALID_SKILL",
    "RUN_PLAN_CONTROLLER_SKILL",
    "SKILL_TOOL_GRANTS",
    "SYSTEM_SKILL",
    "TEST_SKILL",
    "active_run_plan_step",
    "check_call_grant",
    "check_grant",
    "is_full_grant",
    "resolve_run_token",
]
