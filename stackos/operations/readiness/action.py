"""Action-scoped readiness evaluation and shared action projections."""

from __future__ import annotations

from stackos.actions import ActionRepository
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.provider_setup import ProviderSetupOut, provider_local_setup_url
from stackos.repositories.base import ValidationError

from .schemas import (
    ReadinessActionOut,
    ReadinessCheckInput,
    ReadinessCheckOut,
    ReadinessMissingItemOut,
    ReadinessNextStepOut,
    ReadinessResponseMode,
)


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

    from .workflow import _workflow_readiness

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
        structurally_ready=action.availability_status != "action_not_found",
        context_status="ready",
        required_providers_ready=action.executable,
        execution_ready=action.executable,
        missing=action.missing,
        next_steps=next_steps,
        action=action,
        actions=[] if mode == "compact" else [action],
    )


def _action_out(
    *,
    project_id: int,
    action_ref: str,
    contract_key: str | None = None,
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
    route_group: str | None = None,
    route_key: str | None = None,
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
        contract_key=contract_key,
        name=name,
        provider_key=provider_key,
        capability_key=capability_key,
        risk_level=risk_level,
        executable=executable,
        availability_status=availability_status,
        availability_reasons=list(availability_reasons),
        credential_state=credential_state,
        budget_state=budget_state,
        optional=optional_action,
        route_group=route_group,
        route_key=route_key,
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
            optional_auth=optional_auth,
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
            optional_auth=optional_auth,
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
            optional_auth=optional_auth,
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
            optional_auth=optional_auth,
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
            ui_url=_connections_url(project_id, provider_key) if provider_key else None,
            setup=provider_setup,
        )
    if reason.startswith("execution_mode:"):
        required_for = _required_for(
            default="action_execution",
            optional_auth=optional_auth,
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
            ui_url=_connections_url(project_id, provider_key) if provider_key else None,
            setup=provider_setup,
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
            arguments=_next_step_arguments(project_id=project_id, missing=missing),
            ui_url=missing.ui_url,
            setup=missing.setup,
        )
    ]


def _next_step_arguments(
    *,
    project_id: int,
    missing: ReadinessMissingItemOut,
) -> dict[str, object]:
    tool = missing.next_tool or "action.describe"
    if tool == "action.describe":
        return {
            key: value
            for key, value in {
                "project_id": project_id,
                "action_ref": missing.action_ref,
            }.items()
            if value is not None
        }
    if tool in {"auth.status", "auth.test"}:
        return {
            key: value
            for key, value in {
                "project_id": project_id,
                "provider_key": missing.provider_key,
            }.items()
            if value is not None
        }
    if tool in {"budget.set", "budget.update"}:
        return {
            key: value
            for key, value in {
                "project_id": project_id,
                "budget_kind": missing.budget_kind,
                "provider_key": missing.provider_key,
            }.items()
            if value is not None
        }
    return {"project_id": project_id}


def _compact_action_summary(action: ReadinessActionOut) -> ReadinessActionOut:
    return ReadinessActionOut(
        action_ref=action.action_ref,
        contract_key=action.contract_key,
        name=action.name,
        provider_key=action.provider_key,
        capability_key=action.capability_key,
        risk_level=action.risk_level,
        executable=action.executable,
        availability_status=action.availability_status,
        availability_reasons=action.availability_reasons,
        credential_state=action.credential_state,
        budget_state=action.budget_state,
        optional=action.optional,
        route_group=action.route_group,
        route_key=action.route_key,
        setup=None,
        missing=[_compact_missing_item(item) for item in action.missing],
    )


def _compact_missing_item(item: ReadinessMissingItemOut) -> ReadinessMissingItemOut:
    return ReadinessMissingItemOut(
        kind=item.kind,
        code=item.code,
        message=item.message,
        required_for=item.required_for,
        action_ref=item.action_ref,
        action_refs=list(item.action_refs),
        workflow_key=item.workflow_key,
        provider_key=item.provider_key,
        credential_refs=list(item.credential_refs),
        budget_kind=item.budget_kind,
        next_tool=item.next_tool,
        ui_url=item.ui_url,
        setup=None,
    )


def _connections_url(project_id: int, provider_key: str | None = None) -> str:
    return provider_local_setup_url(project_id, provider_key) or (
        f"http://127.0.0.1:5180/projects/{project_id}/connections"
    )
