"""Action availability signals for generic catalog and setup surfaces."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from content_stack.db.models import Credential, IntegrationBudget

if TYPE_CHECKING:
    from content_stack.actions.manifest import ExecutableActionManifest


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


def _provider_disabled(provider_config_json: dict[str, Any] | None) -> bool:
    if not isinstance(provider_config_json, dict):
        return False
    return provider_config_json.get("enabled") is False


def _credential_state(
    session: Session,
    *,
    project_id: int | None,
    manifest: ExecutableActionManifest,
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

    rows = session.exec(
        select(Credential)
        .where(
            Credential.provider_key == manifest.provider_key,
            col(Credential.revoked_at).is_(None),
            (Credential.project_id == project_id) | col(Credential.project_id).is_(None),
        )
        .order_by(col(Credential.project_id).desc(), col(Credential.created_at).desc())
    ).all()
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
) -> tuple[str, list[str]]:
    if not manifest.enforce_budget or not manifest.budget_kind:
        return "not_enforced", []
    if project_id is None:
        return "unknown", ["project_id_required_for_budget_status"]
    row = session.exec(
        select(IntegrationBudget).where(
            IntegrationBudget.project_id == project_id,
            IntegrationBudget.kind == manifest.budget_kind,
        )
    ).first()
    if row is None:
        return "missing", ["budget_required"]
    now = _utcnow()
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
    )
    budget_state, budget_reasons = _budget_state(
        session,
        project_id=project_id,
        manifest=manifest,
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


__all__ = ["ActionAvailabilityOut", "build_action_availability"]
