"""Scoped readiness checks for agent workflow/action execution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.actions import ActionRepository
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationSpec
from stackos.provider_setup import ProviderSetupOut, provider_local_setup_url
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.plugins import PluginRepository
from stackos.workflows import WorkflowTemplateLoader
from stackos.workflows.template_schema import ActionContractSpec, AuthRequirementSpec

ReadinessScope = Literal["action", "workflow"]
ReadinessResponseMode = Literal["compact", "raw", "standard", "verbose"]


class ReadinessCheckInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"project_id": 1, "action_ref": "utils.sitemap.fetch"},
                {"project_id": 1, "workflow_key": "engineering.tracked-delivery"},
            ]
        },
    )

    project_id: int | None = None
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None
    workflow_key: str | None = None
    repo_root: str | None = None
    source: str | None = None
    response_mode: ReadinessResponseMode = Field(
        default="compact",
        description=(
            "compact is the normal agent shape; raw/standard/verbose include more contract detail."
        ),
    )


class ReadinessMissingItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    code: str
    message: str
    required_for: str = "execution"
    action_ref: str | None = None
    action_refs: list[str] = Field(default_factory=list)
    workflow_key: str | None = None
    provider_key: str | None = None
    credential_refs: list[str] = Field(default_factory=list)
    budget_kind: str | None = None
    next_tool: str | None = None
    ui_url: str | None = None
    setup: ProviderSetupOut | None = None


class ReadinessActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_ref: str
    name: str | None = None
    provider_key: str | None = None
    capability_key: str | None = None
    risk_level: str | None = None
    executable: bool
    availability_status: str
    availability_reasons: list[str] = Field(default_factory=list)
    credential_state: str | None = None
    budget_state: str | None = None
    setup: ProviderSetupOut | None = None
    missing: list[ReadinessMissingItemOut] = Field(default_factory=list)


class ReadinessWorkflowOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_key: str
    name: str
    plugin_slug: str | None = None
    action_contract_count: int = 0
    scoped_action_count: int = 0
    required_agent_roles: list[str] = Field(default_factory=list)
    recommended_agent_roles: list[str] = Field(default_factory=list)
    skill_refs: list[str] = Field(default_factory=list)


class ReadinessNextStepOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    reason: str
    arguments: dict[str, object] = Field(default_factory=dict)
    ui_url: str | None = None
    setup: ProviderSetupOut | None = None


class ReadinessCheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ReadinessScope
    project_id: int
    ready: bool
    execution_ready: bool
    missing: list[ReadinessMissingItemOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[ReadinessNextStepOut] = Field(default_factory=list)
    action: ReadinessActionOut | None = None
    workflow: ReadinessWorkflowOut | None = None
    actions: list[ReadinessActionOut] = Field(default_factory=list)


async def readiness_check(
    inp: ReadinessCheckInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ReadinessCheckOut:
    project_id = _project_id(inp.project_id, ctx.project_id)
    mode = inp.response_mode
    has_action = (
        inp.action_ref is not None or inp.plugin_slug is not None or inp.action_key is not None
    )
    has_workflow = inp.workflow_key is not None
    if has_action == has_workflow:
        raise ValidationError(
            "pass exactly one of workflow_key or action_ref/plugin_slug/action_key"
        )
    if has_action:
        return _action_readiness(inp, ctx, project_id=project_id, mode=mode)
    return _workflow_readiness(inp, ctx, project_id=project_id, mode=mode)


def _project_id(input_project_id: int | None, ctx_project_id: int | None) -> int:
    project_id = input_project_id if input_project_id is not None else ctx_project_id
    if project_id is None:
        raise ValidationError(
            "project_id is required unless the agent bridge resolved the workspace project"
        )
    return project_id


def _action_readiness(
    inp: ReadinessCheckInput,
    ctx: MCPContext,
    *,
    project_id: int,
    mode: ReadinessResponseMode,
) -> ReadinessCheckOut:
    described = ActionRepository(ctx.session).describe(
        project_id=project_id,
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
    )
    action = _action_out(
        project_id=project_id,
        action_ref=described.manifest.action_ref,
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
        provider_setup=described.provider_setup,
    )
    next_steps = _next_steps_for_action(project_id=project_id, action=action)
    return ReadinessCheckOut(
        scope="action",
        project_id=project_id,
        ready=action.executable,
        execution_ready=action.executable,
        missing=action.missing,
        next_steps=next_steps,
        action=action,
        actions=[] if mode == "compact" else [action],
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
    missing = _dedupe_missing([item for action in actions for item in action.missing])
    blocking_missing = [
        item for item in missing if item.required_for != "optional_action_execution"
    ]
    contract_blocked = any(item.code == "action_not_found" for item in missing)
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
        actions = [_compact_action_summary(item) for item in actions]
    return ReadinessCheckOut(
        scope="workflow",
        project_id=project_id,
        ready=not contract_blocked,
        execution_ready=not blocking_missing and not contract_blocked,
        missing=missing,
        warnings=warnings,
        next_steps=next_steps,
        workflow=workflow,
        actions=actions,
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
                )
            )
            continue
        action = _action_out(
            project_id=project_id,
            action_ref=described.manifest.action_ref,
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
            provider_setup=described.provider_setup,
        )
        actions.append(action)
    return actions, warnings


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


def _action_out(
    *,
    project_id: int,
    action_ref: str,
    name: str | None,
    provider_key: str | None,
    capability_key: str | None,
    risk_level: str | None,
    executable: bool,
    availability_status: str,
    availability_reasons: list[str],
    credential_state: str | None,
    budget_state: str | None,
    budget_kind: str | None,
    credential_refs: list[str],
    workflow_key: str | None = None,
    optional_auth: bool = False,
    optional_action: bool = False,
    provider_setup: ProviderSetupOut | None = None,
) -> ReadinessActionOut:
    missing = [
        _missing_item(
            project_id=project_id,
            action_ref=action_ref,
            workflow_key=workflow_key,
            provider_key=provider_key,
            budget_kind=budget_kind,
            credential_refs=credential_refs,
            reason=reason,
            optional_auth=optional_auth,
            optional_action=optional_action,
            provider_setup=provider_setup,
        )
        for reason in availability_reasons
    ]
    return ReadinessActionOut(
        action_ref=action_ref,
        name=name,
        provider_key=provider_key,
        capability_key=capability_key,
        risk_level=risk_level,
        executable=executable,
        availability_status=availability_status,
        availability_reasons=list(availability_reasons),
        credential_state=credential_state,
        budget_state=budget_state,
        setup=provider_setup,
        missing=[item for item in missing if item is not None],
    )


def _missing_item(
    *,
    project_id: int,
    action_ref: str,
    workflow_key: str | None,
    provider_key: str | None,
    budget_kind: str | None,
    credential_refs: list[str],
    reason: str,
    optional_auth: bool,
    optional_action: bool,
    provider_setup: ProviderSetupOut | None,
) -> ReadinessMissingItemOut | None:
    if reason == "credential_required":
        required_for = _required_for(
            default="action_execution",
            optional_auth=optional_auth,
            optional_action=optional_action,
        )
        return ReadinessMissingItemOut(
            kind="credential",
            code=reason,
            message=(
                f"Connect provider {provider_key!r} before executing {action_ref}."
                if provider_key
                else f"Connect the required provider credential before executing {action_ref}."
            ),
            required_for=required_for,
            action_ref=action_ref,
            action_refs=[action_ref],
            workflow_key=workflow_key,
            provider_key=provider_key,
            next_tool="auth.status",
            ui_url=_connections_url(project_id, provider_key),
            setup=provider_setup,
        )
    if reason == "credential_not_connected":
        required_for = _required_for(
            default="action_execution",
            optional_auth=False,
            optional_action=optional_action,
        )
        return ReadinessMissingItemOut(
            kind="credential",
            code=reason,
            message=f"Existing credential for {action_ref} is not connected.",
            required_for=required_for,
            action_ref=action_ref,
            action_refs=[action_ref],
            workflow_key=workflow_key,
            provider_key=provider_key,
            credential_refs=credential_refs,
            next_tool="auth.test",
            ui_url=_connections_url(project_id, provider_key),
            setup=provider_setup,
        )
    if reason == "budget_required":
        required_for = _required_for(
            default="action_execution",
            optional_auth=False,
            optional_action=optional_action,
        )
        return ReadinessMissingItemOut(
            kind="budget",
            code=reason,
            message=f"Set a {budget_kind!r} budget before executing {action_ref}.",
            required_for=required_for,
            action_ref=action_ref,
            action_refs=[action_ref],
            workflow_key=workflow_key,
            provider_key=provider_key,
            budget_kind=budget_kind,
            next_tool="budget.set",
        )
    if reason == "budget_exhausted":
        required_for = _required_for(
            default="action_execution",
            optional_auth=False,
            optional_action=optional_action,
        )
        return ReadinessMissingItemOut(
            kind="budget",
            code=reason,
            message=f"Budget {budget_kind!r} is exhausted for {action_ref}.",
            required_for=required_for,
            action_ref=action_ref,
            action_refs=[action_ref],
            workflow_key=workflow_key,
            provider_key=provider_key,
            budget_kind=budget_kind,
            next_tool="budget.update",
        )
    if reason in {"plugin_disabled", "provider_disabled", "connector_not_registered"}:
        required_for = _required_for(
            default="action_execution",
            optional_auth=False,
            optional_action=optional_action,
        )
        return ReadinessMissingItemOut(
            kind="setup",
            code=reason,
            message=f"Setup issue blocks {action_ref}: {reason}.",
            required_for=required_for,
            action_ref=action_ref,
            action_refs=[action_ref],
            workflow_key=workflow_key,
            provider_key=provider_key,
            next_tool="action.describe",
        )
    if reason.startswith("execution_mode:"):
        required_for = _required_for(
            default="action_execution",
            optional_auth=False,
            optional_action=optional_action,
        )
        return ReadinessMissingItemOut(
            kind="setup",
            code="execution_mode_not_directly_executable",
            message=f"{action_ref} is not directly executable through action.execute/run.",
            required_for=required_for,
            action_ref=action_ref,
            action_refs=[action_ref],
            workflow_key=workflow_key,
            provider_key=provider_key,
            next_tool="action.describe",
        )
    if reason.startswith("project_id_required"):
        return None
    return None


def _required_for(
    *,
    default: str,
    optional_auth: bool,
    optional_action: bool,
) -> str:
    if optional_action:
        return "optional_action_execution"
    if optional_auth:
        return "action_execution_optional_provider"
    return default


def _dedupe_missing(items: list[ReadinessMissingItemOut]) -> list[ReadinessMissingItemOut]:
    key_type = tuple[str, str, str | None, str | None, str | None, str]
    grouped: dict[key_type, ReadinessMissingItemOut] = {}
    for item in items:
        key = (
            item.kind,
            item.code,
            item.provider_key,
            item.budget_kind,
            item.workflow_key,
            item.required_for,
        )
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = item.model_copy(deep=True)
            continue
        refs = list(dict.fromkeys([*existing.action_refs, *item.action_refs]))
        existing.action_refs = refs
        if existing.action_ref is None and item.action_ref is not None:
            existing.action_ref = item.action_ref
        existing.credential_refs = list(
            dict.fromkeys([*existing.credential_refs, *item.credential_refs])
        )
        if existing.setup is None and item.setup is not None:
            existing.setup = item.setup
    return list(grouped.values())


def _next_steps_for_action(
    *,
    project_id: int,
    action: ReadinessActionOut,
) -> list[ReadinessNextStepOut]:
    if action.executable:
        return [
            ReadinessNextStepOut(
                tool="action.validate",
                reason="Action setup is ready; validate the payload before execution.",
                arguments={"project_id": project_id, "action_ref": action.action_ref},
            ),
            ReadinessNextStepOut(
                tool="action.run",
                reason="Use direct action.run for one explicit action outside a workflow.",
                arguments={"project_id": project_id, "action_ref": action.action_ref},
            ),
        ]
    missing = action.missing[0] if action.missing else None
    if missing is None:
        return [
            ReadinessNextStepOut(
                tool="action.describe",
                reason="Action is not executable; inspect the exact action manifest.",
                arguments={"project_id": project_id, "action_ref": action.action_ref},
            )
        ]
    return [
        ReadinessNextStepOut(
            tool=missing.next_tool or "action.describe",
            reason=missing.message,
            arguments={
                key: value
                for key, value in {
                    "project_id": project_id,
                    "provider_key": missing.provider_key,
                }.items()
                if value is not None
            },
            ui_url=missing.ui_url,
            setup=missing.setup,
        )
    ]


def _compact_action_summary(action: ReadinessActionOut) -> ReadinessActionOut:
    return ReadinessActionOut(
        action_ref=action.action_ref,
        name=action.name,
        provider_key=action.provider_key,
        capability_key=action.capability_key,
        risk_level=action.risk_level,
        executable=action.executable,
        availability_status=action.availability_status,
        availability_reasons=action.availability_reasons,
        credential_state=action.credential_state,
        budget_state=action.budget_state,
        setup=action.setup,
        missing=action.missing,
    )


def _connections_url(project_id: int, provider_key: str | None = None) -> str:
    return provider_local_setup_url(project_id, provider_key) or (
        f"http://127.0.0.1:5180/projects/{project_id}/connections"
    )


def operation_specs() -> list[OperationSpec]:
    return [
        operation_spec(
            name="readiness.check",
            summary="Check scoped workflow or action readiness without broad setup noise.",
            input_model=ReadinessCheckInput,
            output_model=ReadinessCheckOut,
            handler=readiness_check,
            purpose=(
                "Use this when an agent needs to know whether one workflow or action can be "
                "executed now, and exactly which credentials, budgets, connectors, or setup "
                "items are missing for that scope."
            ),
            when_to_use=(
                "Before telling the operator credentials are missing for a selected workflow.",
                "Before broad auth.status when the agent already knows the workflow or action.",
                "Before runPlan.create/start when provider setup might block execution.",
            ),
            prerequisites=(
                "Pass exactly one workflow_key or action_ref/plugin_slug/action_key.",
                "Use workflow readiness for setup; use action readiness for one explicit action.",
                (
                    "Do not treat global auth.status gaps as blockers until readiness.check says "
                    "the selected scope needs them."
                ),
            ),
            returns=(
                "ready/execution_ready booleans for the selected scope.",
                "Only missing items tied to that workflow/action, with next tool or UI link.",
                (
                    "Workflow responses still allow planning/runPlan.create when provider "
                    "execution setup is incomplete."
                ),
            ),
            examples=(
                OperationExample(
                    title="Check a workflow before setup",
                    arguments={"project_id": 1, "workflow_key": "engineering.tracked-delivery"},
                ),
                OperationExample(
                    title="Check one action",
                    arguments={"project_id": 1, "action_ref": "utils.image.generate"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            category="setup",
        )
    ]


__all__ = [
    "ReadinessCheckInput",
    "ReadinessCheckOut",
    "operation_specs",
    "readiness_check",
]
