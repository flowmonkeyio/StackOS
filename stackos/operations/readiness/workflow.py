"""Workflow-scoped readiness evaluation and route projections."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from stackos.actions import ActionRepository
from stackos.mcp.context import MCPContext
from stackos.repositories.base import NotFoundError
from stackos.repositories.plugins import PluginRepository
from stackos.workflows import WorkflowTemplateLoader
from stackos.workflows.template_schema import ActionContractSpec, AuthRequirementSpec

from .action import (
    _action_out,
    _compact_action_summary,
    _compact_missing_item,
    _connections_url,
    _dedupe_missing,
)
from .schemas import (
    ReadinessActionOut,
    ReadinessCheckInput,
    ReadinessCheckOut,
    ReadinessMissingItemOut,
    ReadinessNextStepOut,
    ReadinessResponseMode,
    ReadinessRouteGroupOut,
    ReadinessRouteOut,
    ReadinessWorkflowOut,
)


def _workflow_readiness(
    inp: ReadinessCheckInput,
    ctx: MCPContext,
    *,
    project_id: int,
    mode: ReadinessResponseMode,
) -> ReadinessCheckOut:
    assert inp.workflow_key is not None
    loaded = WorkflowTemplateLoader(ctx.session).describe_template(
        key=inp.workflow_key,
        project_id=project_id,
        repo_root=inp.repo_root,
        source=inp.source,
    )
    workflow = ReadinessWorkflowOut(
        workflow_key=loaded.spec.key,
        name=loaded.spec.name,
        plugin_slug=loaded.summary.plugin_slug,
        action_contract_count=len(loaded.spec.action_contracts),
        scoped_action_count=len(_referenced_action_contract_keys(loaded.spec.steps)),
        required_agent_roles=[
            item.role for item in loaded.spec.agent_requirements if item.requirement == "required"
        ],
        recommended_agent_roles=[
            item.role
            for item in loaded.spec.agent_requirements
            if item.requirement == "recommended"
        ],
        skill_refs=[item.skill_ref for item in loaded.spec.skill_requirements],
        prerequisite_count=len(loaded.spec.public.prerequisites) if loaded.spec.public else 0,
        prerequisites=list(loaded.spec.public.prerequisites) if loaded.spec.public else [],
        safe_stopping_points=(
            list(loaded.spec.experience.safe_stopping_points) if loaded.spec.experience else []
        ),
    )
    actions, warnings = _workflow_action_readiness(
        ctx,
        project_id=project_id,
        workflow_key=loaded.spec.key,
        plugin_slug=loaded.summary.plugin_slug,
        contracts=loaded.spec.action_contracts,
        auth_requirements=loaded.spec.auth_requirements,
        referenced_contract_keys=_referenced_action_contract_keys(loaded.spec.steps),
    )
    route_groups = _workflow_route_readiness(actions)
    _annotate_route_missing(actions, route_groups)
    route_contract_keys = {
        action.contract_key
        for action in actions
        if action.route_group is not None and action.contract_key is not None
    }
    required_ungrouped = [
        action
        for action in actions
        if action.contract_key not in route_contract_keys
        and not _contract_optional(loaded.spec.action_contracts, action.contract_key)
        and not _contract_optional_auth(
            loaded.spec.action_contracts,
            loaded.spec.auth_requirements,
            action.contract_key,
        )
    ]
    missing = _dedupe_missing([item for action in required_ungrouped for item in action.missing])
    missing.extend(_route_group_missing(route_groups, workflow_key=loaded.spec.key))
    required_providers_ready = all(action.executable for action in required_ungrouped) and all(
        group.execution_ready for group in route_groups if group.required
    )
    contract_blocked = any(
        action.availability_status == "action_not_found" for action in required_ungrouped
    ) or any(not group.structurally_ready for group in route_groups if group.required)
    context_status: Literal["ready", "not_evaluated", "missing"] = (
        "not_evaluated" if loaded.spec.context_requirements or workflow.prerequisites else "ready"
    )
    if context_status == "not_evaluated":
        warnings.append(
            "Workflow context prerequisites were not evaluated. Readiness here covers the "
            "template contract and provider routes; inspect the listed prerequisites and "
            "selected project context before claiming execution readiness."
        )
    next_steps = [
        ReadinessNextStepOut(
            tool="runPlan.create",
            reason="Workflow template is usable; create a concrete run plan when ready.",
            arguments={"project_id": project_id, "template_key": loaded.spec.key},
        )
    ]
    if contract_blocked:
        next_steps = [
            ReadinessNextStepOut(
                tool="workflowTemplate.describe",
                reason=(
                    "Workflow references actions that are not registered; inspect or fix the "
                    "template/catalog contract before creating a run plan."
                ),
                arguments={"project_id": project_id, "key": loaded.spec.key},
            )
        ]
    elif missing:
        next_steps.append(
            ReadinessNextStepOut(
                tool="auth.status",
                reason=(
                    "Only the listed workflow action dependencies are missing; inspect or repair "
                    "those providers before executing affected run-plan steps."
                ),
                arguments={"project_id": project_id},
                ui_url=_connections_url(project_id),
            )
        )
    if mode == "compact":
        missing = [_compact_missing_item(item) for item in missing]
        actions = [_compact_action_summary(item) for item in actions]
        route_groups = [_compact_route_group(item) for item in route_groups]
    execution_ready = (
        required_providers_ready and not contract_blocked and context_status == "ready"
    )
    return ReadinessCheckOut(
        scope="workflow",
        project_id=project_id,
        structurally_ready=not contract_blocked,
        context_status=context_status,
        required_providers_ready=required_providers_ready,
        execution_ready=execution_ready,
        missing=missing,
        warnings=warnings,
        next_steps=next_steps,
        workflow=workflow,
        actions=actions,
        route_groups=route_groups,
    )


def _workflow_action_readiness(
    ctx: MCPContext,
    *,
    project_id: int,
    workflow_key: str,
    plugin_slug: str | None,
    contracts: list[ActionContractSpec],
    auth_requirements: list[AuthRequirementSpec],
    referenced_contract_keys: set[str],
) -> tuple[list[ReadinessActionOut], list[str]]:
    auth_by_key = {item.key: item for item in auth_requirements}
    plugin_slugs = {plugin.slug for plugin in PluginRepository(ctx.session).list_plugins()}
    action_index = _action_resolution_index(ctx, project_id=project_id)
    by_key = {contract.key: contract for contract in contracts}
    actions: list[ReadinessActionOut] = []
    warnings: list[str] = []
    for contract_key in sorted(referenced_contract_keys):
        contract = by_key.get(contract_key)
        if contract is None:
            warnings.append(
                f"Workflow {workflow_key} references unknown action contract {contract_key!r}."
            )
            continue
        action_ref = _contract_action_ref(
            contract,
            plugin_slug=plugin_slug,
            known_plugin_slugs=plugin_slugs,
            action_index=action_index,
        )
        if action_ref is None:
            warnings.append(
                f"Action contract {contract.key!r} has no concrete action; a run plan must "
                "resolve provider/action choice before execution readiness can be checked."
            )
            continue
        optional_auth = (
            contract.auth_ref is not None
            and (auth_by_key.get(contract.auth_ref) is not None)
            and auth_by_key[contract.auth_ref].optional
        )
        optional_action = contract.optional
        try:
            described = ActionRepository(ctx.session).describe(
                project_id=project_id,
                action_ref=action_ref,
            )
        except NotFoundError:
            actions.append(
                ReadinessActionOut(
                    action_ref=action_ref,
                    contract_key=contract.key,
                    executable=False,
                    availability_status="action_not_found",
                    availability_reasons=["action_not_found"],
                    missing=[
                        ReadinessMissingItemOut(
                            kind="action",
                            code="action_not_found",
                            message=(
                                f"Workflow {workflow_key} references action {action_ref!r}, "
                                "but the action is not registered."
                            ),
                            required_for="execution",
                            action_ref=action_ref,
                            workflow_key=workflow_key,
                            next_tool="action.list",
                        )
                    ],
                    route_group=contract.route_group,
                    route_key=contract.route_key,
                    optional=contract.optional,
                )
            )
            continue
        action = _action_out(
            project_id=project_id,
            action_ref=described.manifest.action_ref,
            contract_key=contract.key,
            name=described.manifest.name,
            provider_key=described.manifest.provider_key,
            capability_key=described.manifest.capability_key,
            risk_level=described.manifest.risk_level,
            executable=described.availability.executable,
            availability_status=described.availability.status,
            availability_reasons=described.availability.reasons,
            credential_state=described.availability.credential_state,
            budget_state=described.availability.budget_state,
            budget_kind=described.availability.budget_kind,
            credential_refs=described.availability.credential_refs,
            workflow_key=workflow_key,
            optional_auth=optional_auth,
            optional_action=optional_action,
            route_group=contract.route_group,
            route_key=contract.route_key,
            provider_setup=described.provider_setup,
        )
        actions.append(action)
    return actions, warnings


def _workflow_route_readiness(
    actions: list[ReadinessActionOut],
) -> list[ReadinessRouteGroupOut]:
    grouped: dict[str, dict[str, list[ReadinessActionOut]]] = {}
    for action in actions:
        if action.route_group is None or action.route_key is None:
            continue
        grouped.setdefault(action.route_group, {}).setdefault(action.route_key, []).append(action)

    out: list[ReadinessRouteGroupOut] = []
    for group_key in sorted(grouped):
        group_required = any(
            not action.optional
            for route_actions in grouped[group_key].values()
            for action in route_actions
        )
        routes: list[ReadinessRouteOut] = []
        for route_key in sorted(grouped[group_key]):
            route_actions = grouped[group_key][route_key]
            required_actions = [action for action in route_actions if not action.optional]
            if required_actions:
                structurally_ready = all(
                    action.availability_status != "action_not_found" for action in required_actions
                )
                executable = structurally_ready and all(
                    action.executable for action in required_actions
                )
            else:
                structurally_ready = any(
                    action.availability_status != "action_not_found" for action in route_actions
                )
                executable = any(action.executable for action in route_actions)
            routes.append(
                ReadinessRouteOut(
                    route_group=group_key,
                    route_key=route_key,
                    executable=executable,
                    structurally_ready=structurally_ready,
                    action_refs=[action.action_ref for action in route_actions],
                    missing=_dedupe_missing(
                        [item for action in route_actions for item in action.missing]
                    ),
                )
            )
        out.append(
            ReadinessRouteGroupOut(
                route_group=group_key,
                required=group_required,
                execution_ready=any(route.executable for route in routes),
                structurally_ready=any(route.structurally_ready for route in routes),
                available_route_keys=[route.route_key for route in routes if route.executable],
                routes=routes,
            )
        )
    return out


def _annotate_route_missing(
    actions: list[ReadinessActionOut],
    route_groups: list[ReadinessRouteGroupOut],
) -> None:
    groups = {group.route_group: group for group in route_groups}
    route_status = {
        (group.route_group, route.route_key): route.executable
        for group in route_groups
        for route in group.routes
    }
    for action in actions:
        if action.route_group is None or action.route_key is None:
            continue
        group = groups[action.route_group]
        selected_ready = route_status[(action.route_group, action.route_key)]
        for missing in action.missing:
            if group.execution_ready and not selected_ready:
                missing.required_for = "alternative_route_not_selected"
            elif group.required and not group.execution_ready:
                missing.required_for = f"route_option:{action.route_group}:{action.route_key}"
            else:
                missing.required_for = f"optional_route:{action.route_group}:{action.route_key}"
    by_ref = {action.action_ref: action for action in actions}
    for group in route_groups:
        for route in group.routes:
            route.missing = _dedupe_missing(
                [
                    missing
                    for action_ref in route.action_refs
                    for missing in by_ref[action_ref].missing
                ]
            )


def _route_group_missing(
    route_groups: list[ReadinessRouteGroupOut],
    *,
    workflow_key: str,
) -> list[ReadinessMissingItemOut]:
    return [
        ReadinessMissingItemOut(
            kind="route",
            code="no_executable_route",
            message=(
                f"Choose and configure one executable route for {group.route_group!r}; "
                "unavailable alternatives do not all need to be connected."
            ),
            required_for="execution",
            action_refs=[action_ref for route in group.routes for action_ref in route.action_refs],
            workflow_key=workflow_key,
            next_tool="readiness.check",
        )
        for group in route_groups
        if group.required and not group.execution_ready
    ]


def _contract_optional(
    contracts: list[ActionContractSpec],
    contract_key: str | None,
) -> bool:
    return any(item.key == contract_key and item.optional for item in contracts)


def _contract_optional_auth(
    contracts: list[ActionContractSpec],
    auth_requirements: list[AuthRequirementSpec],
    contract_key: str | None,
) -> bool:
    contract = next((item for item in contracts if item.key == contract_key), None)
    if contract is None or contract.auth_ref is None:
        return False
    auth = next(
        (item for item in auth_requirements if item.key == contract.auth_ref),
        None,
    )
    return bool(auth and auth.optional)


def _referenced_action_contract_keys(steps: Iterable[object]) -> set[str]:
    out: set[str] = set()
    for step in steps:  # pydantic model list
        out.update(getattr(step, "action_refs", []) or [])
    return out


def _contract_action_ref(
    contract: ActionContractSpec,
    *,
    plugin_slug: str | None,
    known_plugin_slugs: set[str],
    action_index: dict[str, list[str]],
) -> str | None:
    action = contract.action
    if not action:
        return None
    first_part = action.split(".", 1)[0]
    if first_part in known_plugin_slugs:
        return action
    candidates: list[str] = []
    if plugin_slug:
        local_ref = f"{plugin_slug}.{action}"
        if local_ref in action_index.get(action, []):
            candidates.append(local_ref)
    candidates.extend(action_index.get(action, []))
    if contract.provider:
        provider_matches = [
            item
            for item in candidates
            if item in action_index.get(f"provider:{contract.provider}", [])
        ]
        if provider_matches:
            return provider_matches[0]
    if contract.capability:
        capability_matches = [
            item
            for item in candidates
            if item in action_index.get(f"capability:{contract.capability}", [])
        ]
        if capability_matches:
            return capability_matches[0]
    if candidates:
        return candidates[0]
    return f"{plugin_slug}.{action}" if plugin_slug else None


def _action_resolution_index(ctx: MCPContext, *, project_id: int) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for action in PluginRepository(ctx.session).list_actions(project_id=project_id):
        index.setdefault(action.key, []).append(action.action_ref)
        if action.provider_key:
            index.setdefault(f"provider:{action.provider_key}", []).append(action.action_ref)
        if action.capability_key:
            index.setdefault(f"capability:{action.capability_key}", []).append(action.action_ref)
    return index


def _compact_route_group(group: ReadinessRouteGroupOut) -> ReadinessRouteGroupOut:
    return ReadinessRouteGroupOut(
        route_group=group.route_group,
        required=group.required,
        execution_ready=group.execution_ready,
        structurally_ready=group.structurally_ready,
        available_route_keys=list(group.available_route_keys),
        routes=[
            ReadinessRouteOut(
                route_group=route.route_group,
                route_key=route.route_key,
                executable=route.executable,
                structurally_ready=route.structurally_ready,
                action_refs=list(route.action_refs),
                missing=[_compact_missing_item(item) for item in route.missing],
            )
            for route in group.routes
        ],
    )
