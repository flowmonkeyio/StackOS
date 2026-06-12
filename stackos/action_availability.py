"""Action availability signals for generic catalog and setup surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from stackos.db.models import Credential, IntegrationBudget
from stackos.provider_setup import ProviderSetupOut, provider_local_setup_url

if TYPE_CHECKING:
    from stackos.actions.manifest import ExecutableActionManifest

_INTERNAL_PROVIDER_KEYS = frozenset({"local-agent-chat", "local-daemon"})


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


class ActionAvailabilityOut(BaseModel):
    """Project-aware execution readiness for one static action contract."""

    action_ref: str
    status: str
    executable: bool
    reasons: list[str] = Field(default_factory=list)
    connector_key: str | None = None
    execution_mode: str | None = None
    deferred_reason: str | None = None
    operation: str
    connector_registered: bool
    requires_credential: bool
    allows_credential: bool
    credential_state: str
    credential_refs: list[str] = Field(default_factory=list)
    budget_state: str
    budget_kind: str | None = None


class ActionExposureNextActionOut(BaseModel):
    """Safe repair pointer for hidden or blocked action exposure."""

    tool: str
    reason: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ui_url: str | None = None
    setup: ProviderSetupOut | None = None


class ActionExposureOut(BaseModel):
    """Agent-facing visibility classification for one action contract.

    Availability answers "can the daemon execute this action right now?".
    Exposure answers "should this action appear in normal agent discovery?".
    Setup and readiness surfaces can still opt into hidden provider actions.
    """

    state: str
    visible_by_default: bool
    hidden_reason: str | None = None
    external_provider: bool = False
    requires_integration: bool = False
    next_action: ActionExposureNextActionOut | None = None


@dataclass(frozen=True)
class ActionAvailabilityContext:
    """Project-scoped credential and budget state shared across action rows."""

    project_id: int | None
    credentials_by_provider: dict[str, list[Credential]] = field(default_factory=dict)
    budgets_by_kind: dict[str, IntegrationBudget] = field(default_factory=dict)
    now: datetime = field(default_factory=_utcnow)


def integration_setup_url(project_id: int | None, provider_key: str | None = None) -> str | None:
    """Return the local setup URL for a project/provider when known."""
    return provider_local_setup_url(project_id, provider_key)


def build_action_exposure(
    availability: ActionAvailabilityOut,
    *,
    project_id: int | None,
    plugin_slug: str,
    provider_key: str | None,
    requires_credential: bool,
    allows_credential: bool,
) -> ActionExposureOut:
    """Classify default agent visibility for one action.

    Normal discovery should surface currently usable actions. Setup and catalog
    inspection can still request unavailable action contracts explicitly.
    """
    external_provider = _is_external_provider(provider_key)
    requires_integration = external_provider and requires_credential
    base_arguments: dict[str, Any] = {"project_id": project_id} if project_id is not None else {}
    if provider_key:
        base_arguments["provider_key"] = provider_key

    if availability.status == "plugin_disabled":
        return ActionExposureOut(
            state="plugin_disabled",
            visible_by_default=False,
            hidden_reason="plugin_disabled",
            external_provider=external_provider,
            requires_integration=requires_integration,
            next_action=ActionExposureNextActionOut(
                tool="plugin.enable",
                reason=f"Plugin {plugin_slug!r} is disabled for this project.",
                arguments={"project_id": project_id, "plugin_slug": plugin_slug}
                if project_id is not None
                else {"plugin_slug": plugin_slug},
            ),
        )
    if availability.status == "provider_disabled":
        return ActionExposureOut(
            state="provider_disabled",
            visible_by_default=False,
            hidden_reason="provider_disabled",
            external_provider=external_provider,
            requires_integration=requires_integration,
            next_action=ActionExposureNextActionOut(
                tool="provider.describe",
                reason=f"Provider {provider_key!r} is disabled in the plugin manifest.",
                arguments=base_arguments,
            ),
        )
    if availability.status == "missing_connector":
        return ActionExposureOut(
            state="missing_connector",
            visible_by_default=False,
            hidden_reason="missing_connector",
            external_provider=external_provider,
            requires_integration=requires_integration,
            next_action=ActionExposureNextActionOut(
                tool="action.describe",
                reason="The action connector is not registered in this daemon.",
                arguments={"project_id": project_id, "action_ref": availability.action_ref}
                if project_id is not None
                else {"action_ref": availability.action_ref},
            ),
        )

    if requires_integration:
        if availability.credential_state == "available":
            if availability.executable:
                state = "external_connected"
            elif availability.status in {"missing_budget", "budget_blocked"}:
                state = "external_blocked"
            else:
                state = f"external_{availability.status}"
            return ActionExposureOut(
                state=state,
                visible_by_default=True,
                external_provider=True,
                requires_integration=True,
                next_action=_blocked_next_action(availability, base_arguments),
            )
        if availability.credential_state == "failed":
            return ActionExposureOut(
                state="external_blocked",
                visible_by_default=True,
                external_provider=True,
                requires_integration=True,
                next_action=ActionExposureNextActionOut(
                    tool="auth.test",
                    reason=f"Existing credential for provider {provider_key!r} is not connected.",
                    arguments={
                        **base_arguments,
                        "credential_ref": availability.credential_refs[0],
                    }
                    if availability.credential_refs
                    else base_arguments,
                    ui_url=integration_setup_url(project_id, provider_key),
                ),
            )
        if availability.credential_state in {"missing", "unknown"}:
            reason = (
                f"Connect provider {provider_key!r} before this action appears in normal "
                "agent discovery."
            )
            return ActionExposureOut(
                state="external_not_connected"
                if availability.credential_state == "missing"
                else "external_unknown",
                visible_by_default=False,
                hidden_reason="integration_not_connected"
                if availability.credential_state == "missing"
                else "project_id_required_for_integration_status",
                external_provider=True,
                requires_integration=True,
                next_action=ActionExposureNextActionOut(
                    tool="auth.status",
                    reason=reason,
                    arguments=base_arguments,
                    ui_url=integration_setup_url(project_id, provider_key),
                ),
            )

    if external_provider and not availability.executable:
        return ActionExposureOut(
            state=f"external_{availability.status}",
            visible_by_default=False,
            hidden_reason=_not_executable_hidden_reason(availability.status),
            external_provider=True,
            requires_integration=requires_integration,
            next_action=_blocked_next_action(availability, base_arguments)
            or ActionExposureNextActionOut(
                tool="action.describe",
                reason=availability.deferred_reason
                or f"Action {availability.action_ref!r} is not executable in this project.",
                arguments={"project_id": project_id, "action_ref": availability.action_ref}
                if project_id is not None
                else {"action_ref": availability.action_ref},
                ui_url=integration_setup_url(project_id, provider_key),
            ),
        )

    state = "local"
    if external_provider and allows_credential:
        state = "external_optional"
    elif not availability.executable and availability.status != "ready":
        state = "internal_not_executable"
    hide_internal_unavailable = availability.status in {"deferred", "project_local_required"}
    return ActionExposureOut(
        state=state,
        visible_by_default=not hide_internal_unavailable,
        external_provider=external_provider,
        requires_integration=requires_integration,
        hidden_reason=_not_executable_hidden_reason(availability.status)
        if hide_internal_unavailable
        else None,
        next_action=_blocked_next_action(availability, base_arguments),
    )


def _is_external_provider(provider_key: str | None) -> bool:
    return provider_key is not None and provider_key not in _INTERNAL_PROVIDER_KEYS


def _not_executable_hidden_reason(status: str) -> str:
    if status == "deferred":
        return "action_deferred"
    if status == "project_local_required":
        return "project_local_required"
    return "action_not_executable"


def _blocked_next_action(
    availability: ActionAvailabilityOut,
    base_arguments: dict[str, Any],
) -> ActionExposureNextActionOut | None:
    if availability.status == "missing_budget":
        return ActionExposureNextActionOut(
            tool="budget.set",
            reason=f"Set a {availability.budget_kind!r} budget before execution.",
            arguments={
                **base_arguments,
                "kind": availability.budget_kind,
            },
        )
    if availability.status == "budget_blocked":
        return ActionExposureNextActionOut(
            tool="budget.update",
            reason=f"Budget {availability.budget_kind!r} is exhausted for this project.",
            arguments={
                **base_arguments,
                "kind": availability.budget_kind,
            },
        )
    return None


def build_action_availability_context(
    session: Session,
    *,
    project_id: int | None,
) -> ActionAvailabilityContext:
    """Preload project state used by many action availability checks."""
    if project_id is None:
        return ActionAvailabilityContext(project_id=None)

    credential_rows = session.exec(
        select(Credential)
        .where(
            col(Credential.revoked_at).is_(None),
            (col(Credential.project_id) == project_id) | col(Credential.project_id).is_(None),
        )
        .order_by(
            col(Credential.provider_key).asc(),
            col(Credential.project_id).desc(),
            col(Credential.created_at).desc(),
        )
    ).all()
    credentials_by_provider: dict[str, list[Credential]] = {}
    for row in credential_rows:
        credentials_by_provider.setdefault(row.provider_key, []).append(row)

    budget_rows = session.exec(
        select(IntegrationBudget).where(IntegrationBudget.project_id == project_id)
    ).all()

    return ActionAvailabilityContext(
        project_id=project_id,
        credentials_by_provider=credentials_by_provider,
        budgets_by_kind={row.kind: row for row in budget_rows},
    )


def _provider_disabled(provider_config_json: dict[str, Any] | None) -> bool:
    if not isinstance(provider_config_json, dict):
        return False
    return provider_config_json.get("enabled") is False


def _credential_state(
    session: Session,
    *,
    project_id: int | None,
    manifest: ExecutableActionManifest,
    context: ActionAvailabilityContext | None = None,
) -> tuple[str, list[str], list[str]]:
    if manifest.execution_mode is not None and manifest.connector_key is None:
        return "not_applicable", [], []
    if not manifest.allows_credential:
        return "not_allowed", [], []
    if project_id is None:
        if manifest.requires_credential:
            return "unknown", [], ["project_id_required_for_credential_status"]
        return "not_required", [], []
    if manifest.provider_key is None:
        return "not_applicable", [], []

    if context is not None and context.project_id == project_id:
        rows = context.credentials_by_provider.get(manifest.provider_key, [])
    else:
        rows = list(
            session.exec(
                select(Credential)
                .where(
                    Credential.provider_key == manifest.provider_key,
                    col(Credential.revoked_at).is_(None),
                    (col(Credential.project_id) == project_id)
                    | col(Credential.project_id).is_(None),
                )
                .order_by(col(Credential.project_id).desc(), col(Credential.created_at).desc())
            ).all()
        )
    refs = [row.credential_ref for row in rows]
    connected_refs = [row.credential_ref for row in rows if row.status == "connected"]
    if connected_refs:
        state = "available" if manifest.requires_credential else "optional_available"
        return state, connected_refs, []
    if refs:
        return "failed", refs, ["credential_not_connected"]
    if manifest.requires_credential:
        return "missing", [], ["credential_required"]
    return "optional_missing", [], []


def _budget_state(
    session: Session,
    *,
    project_id: int | None,
    manifest: ExecutableActionManifest,
    context: ActionAvailabilityContext | None = None,
) -> tuple[str, list[str]]:
    if not manifest.enforce_budget or not manifest.budget_kind:
        return "not_enforced", []
    if project_id is None:
        return "unknown", ["project_id_required_for_budget_status"]
    if context is not None and context.project_id == project_id:
        row = context.budgets_by_kind.get(manifest.budget_kind)
    else:
        row = session.exec(
            select(IntegrationBudget).where(
                IntegrationBudget.project_id == project_id,
                IntegrationBudget.kind == manifest.budget_kind,
            )
        ).first()
    if row is None:
        return "missing", ["budget_required"]
    now = context.now if context is not None and context.project_id == project_id else _utcnow()
    effective_spend = row.current_month_spend
    if (row.last_reset.year, row.last_reset.month) != (now.year, now.month):
        effective_spend = 0.0
    if effective_spend >= row.monthly_budget_usd:
        return "blocked", ["budget_exhausted"]
    return "available", []


def build_action_availability(
    session: Session,
    *,
    manifest: ExecutableActionManifest,
    connector_keys: set[str],
    project_id: int | None = None,
    provider_config_json: dict[str, Any] | None = None,
    plugin_disabled: bool = False,
    context: ActionAvailabilityContext | None = None,
) -> ActionAvailabilityOut:
    """Build static, decision-free execution readiness for an action."""
    reasons: list[str] = []
    connector_registered = (
        manifest.connector_key is not None and manifest.connector_key in connector_keys
    )
    credential_state, credential_refs, credential_reasons = _credential_state(
        session,
        project_id=project_id,
        manifest=manifest,
        context=context,
    )
    budget_state, budget_reasons = _budget_state(
        session,
        project_id=project_id,
        manifest=manifest,
        context=context,
    )
    reasons.extend(credential_reasons)
    reasons.extend(budget_reasons)

    status = "ready"
    executable = True
    if plugin_disabled:
        status = "plugin_disabled"
        executable = False
        reasons.insert(0, "plugin_disabled")
    elif _provider_disabled(provider_config_json):
        status = "provider_disabled"
        executable = False
        reasons.insert(0, "provider_disabled")
    elif manifest.execution_mode is not None and manifest.connector_key is None:
        if manifest.execution_mode.startswith("deferred"):
            status = "deferred"
        elif manifest.execution_mode == "project-local-http":
            status = "project_local_required"
        else:
            status = "not_executable"
        executable = False
        reasons.insert(0, f"execution_mode:{manifest.execution_mode}")
        if manifest.deferred_reason:
            reasons.append(manifest.deferred_reason)
    elif manifest.connector_key is None:
        status = "not_executable"
        executable = False
        reasons.insert(0, "connector_not_configured")
    elif not connector_registered:
        status = "missing_connector"
        executable = False
        reasons.insert(0, "connector_not_registered")
    elif credential_state == "unknown":
        status = "unknown"
        executable = False
    elif credential_state in {"missing", "failed"}:
        status = "missing_credential" if credential_state == "missing" else "credential_failed"
        executable = False
    elif budget_state == "missing":
        status = "missing_budget"
        executable = False
    elif budget_state == "blocked":
        status = "budget_blocked"
        executable = False
    elif budget_state == "unknown":
        status = "unknown"
        executable = False

    return ActionAvailabilityOut(
        action_ref=manifest.action_ref,
        status=status,
        executable=executable,
        reasons=reasons,
        connector_key=manifest.connector_key,
        execution_mode=manifest.execution_mode,
        deferred_reason=manifest.deferred_reason,
        operation=manifest.operation,
        connector_registered=connector_registered,
        requires_credential=manifest.requires_credential,
        allows_credential=manifest.allows_credential,
        credential_state=credential_state,
        credential_refs=credential_refs,
        budget_state=budget_state,
        budget_kind=manifest.budget_kind,
    )


__all__ = [
    "ActionAvailabilityContext",
    "ActionAvailabilityOut",
    "ActionExposureNextActionOut",
    "ActionExposureOut",
    "build_action_availability",
    "build_action_availability_context",
    "build_action_exposure",
    "integration_setup_url",
]
