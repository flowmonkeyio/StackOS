"""Internal StackOS action executor foundation."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from content_stack.actions.connectors import (
    DEFAULT_ACTION_CONNECTORS,
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionValidationIssue,
)
from content_stack.actions.manifest import ExecutableActionManifest, parse_action_manifest
from content_stack.artifacts import redact_secret_text
from content_stack.auth_providers import AuthRepository, ResolvedCredential
from content_stack.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    Credential,
    Plugin,
    Provider,
    Run,
    RunPlan,
    RunPlanStep,
)
from content_stack.repositories.base import (
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
)
from content_stack.repositories.plugins import PluginRepository
from content_stack.repositories.projects import IntegrationBudgetRepository
from content_stack.workflows.run_plan_schema import find_run_plan_secret_paths


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


_TEXT_REDACT_KEYS = frozenset(
    {
        "auth_ref",
        "credential_ref",
    }
)
_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _TEXT_REDACT_KEYS or normalized.endswith("_credential_ref"):
        return False
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact_for_audit(value: Any) -> Any:
    """Return a deep-redacted copy for agent-visible output and audit."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            redacted[key] = "[redacted]" if _is_secret_key(key) else _redact_for_audit(raw_value)
        return redacted
    if isinstance(value, list):
        return [_redact_for_audit(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_for_audit(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _schema_type_matches(expected: str, value: Any) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _schema_issues(
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: str = "$",
) -> list[ActionValidationIssue]:
    """Small JSON-schema validator for the manifest subset StackOS owns.

    It intentionally handles the common schema fields in current manifests
    without adding executor decisions or provider logic.
    """
    issues: list[ActionValidationIssue] = []
    raw_type = schema.get("type")
    expected_types: list[str] = []
    if isinstance(raw_type, str):
        expected_types = [raw_type]
    elif isinstance(raw_type, list):
        expected_types = [str(item) for item in raw_type if isinstance(item, str)]
    if expected_types and not any(
        _schema_type_matches(expected, value) for expected in expected_types
    ):
        issues.append(
            ActionValidationIssue(
                path=path,
                message=f"expected {' or '.join(expected_types)}, got {_json_type_name(value)}",
                code="type_mismatch",
            )
        )
        return issues

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        issues.append(
            ActionValidationIssue(
                path=path,
                message="value is not one of the allowed enum values",
                code="enum_mismatch",
            )
        )

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    issues.append(
                        ActionValidationIssue(
                            path=f"{path}.{key}",
                            message="required field is missing",
                            code="required",
                        )
                    )
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    issues.extend(_schema_issues(child_schema, value[key], path=f"{path}.{key}"))
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            allowed = {str(key) for key in properties}
            for key in value:
                if str(key) not in allowed:
                    issues.append(
                        ActionValidationIssue(
                            path=f"{path}.{key}",
                            message="additional property is not allowed",
                            code="additional_property",
                        )
                    )

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(_schema_issues(item_schema, item, path=f"{path}[{index}]"))
    return issues


class ActionCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    run_plan_id: int | None
    run_plan_step_id: int | None
    action_id: int | None
    credential_id: int | None
    action_key: str
    plugin_slug: str
    provider_key: str | None
    connector_key: str | None
    operation: str
    status: ActionCallStatus
    dry_run: bool
    idempotency_key: str | None
    credential_ref: str | None
    request_json: dict[str, Any] | None
    response_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    cost_cents: int
    duration_ms: int | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class ActionCallAuditOut(BaseModel):
    """Public action-call audit row for UI/read APIs.

    Internal DB identifiers such as ``credential_id`` and replay inputs such as
    ``idempotency_key`` stay out of this shape. The opaque ``credential_ref`` is
    safe to display because it is designed for agent-visible references.
    """

    id: int
    project_id: int
    run_id: int | None
    run_plan_id: int | None
    run_plan_step_id: int | None
    action_key: str
    plugin_slug: str
    provider_key: str | None
    connector_key: str | None
    operation: str
    status: ActionCallStatus
    dry_run: bool
    credential_ref: str | None
    request_json: dict[str, Any] | None
    response_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    cost_cents: int
    duration_ms: int | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class ActionDescribeOut(BaseModel):
    manifest: ExecutableActionManifest
    connector_registered: bool
    execution_available: bool
    agent_execute_available: bool = False


class ActionValidationOut(BaseModel):
    valid: bool
    manifest: ExecutableActionManifest
    issues: list[ActionValidationIssue] = Field(default_factory=list)
    connector_registered: bool
    estimated_cost_cents: int | None = None
    credential_ref: str | None = None


class ActionExecutionOut(BaseModel):
    action_call: ActionCallAuditOut
    output_json: dict[str, Any]
    metadata_json: dict[str, Any] | None = None
    cost_cents: int = 0
    dry_run: bool = False
    replayed: bool = False
    credential_ref: str | None = None


class ActionRepository:
    """Internal action manifest/executor service.

    The repository executes explicit action payloads only. It never chooses a
    provider, edits campaign strategy, decides workflow order, or returns
    secret material to callers.
    """

    def __init__(
        self,
        session: Session,
        *,
        connectors: ActionConnectorRegistry | None = None,
        asset_dir: Path | None = None,
    ) -> None:
        self._s = session
        self._connectors = connectors or DEFAULT_ACTION_CONNECTORS
        self._asset_dir = asset_dir

    def describe(
        self,
        *,
        action_ref: str | None = None,
        plugin_slug: str | None = None,
        action_key: str | None = None,
    ) -> ActionDescribeOut:
        manifest = self._manifest(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        registered = (
            manifest.connector_key is not None
            and manifest.connector_key in self._connectors.list_keys()
        )
        return ActionDescribeOut(
            manifest=manifest,
            connector_registered=registered,
            execution_available=registered,
            agent_execute_available=registered,
        )

    def query_calls(
        self,
        *,
        project_id: int,
        run_id: int | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        plugin_slug: str | None = None,
        action_key: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ActionCallAuditOut]:
        self._require_project(project_id)
        filters = [ActionCall.project_id == project_id]
        if run_id is not None:
            filters.append(ActionCall.run_id == run_id)
        if run_plan_id is not None:
            filters.append(ActionCall.run_plan_id == run_plan_id)
        if run_plan_step_id is not None:
            filters.append(ActionCall.run_plan_step_id == run_plan_step_id)
        if plugin_slug is not None:
            filters.append(ActionCall.plugin_slug == plugin_slug)
        if action_key is not None:
            filters.append(ActionCall.action_key == action_key)
        stmt = select(ActionCall).where(*filters)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=ActionCall.id,
            limit=limit,
            after_id=after_id,
            converter=self._call_audit_out,
        )

    def validate(
        self,
        *,
        project_id: int | None = None,
        action_ref: str | None = None,
        plugin_slug: str | None = None,
        action_key: str | None = None,
        input_json: dict[str, Any] | None = None,
        credential_ref: str | None = None,
    ) -> ActionValidationOut:
        manifest = self._manifest(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        payload, resolved_ref = self._normalize_payload_and_ref(
            input_json or {},
            credential_ref=credential_ref,
        )
        issues = self._validate_payload(manifest=manifest, payload=payload)
        issues.extend(
            self._credential_ref_issues(
                project_id=project_id,
                manifest=manifest,
                credential_ref=resolved_ref,
            )
        )
        connector_registered = False
        estimated_cost_cents: int | None = None
        if manifest.connector_key is not None:
            try:
                connector = self._connectors.get(manifest.connector_key)
                connector_registered = True
                request = self._connector_request(
                    project_id=project_id or 0,
                    manifest=manifest,
                    input_json=payload,
                    credential=None,
                    dry_run=True,
                )
                issues.extend(connector.validate(request))
                estimated_cost_cents = connector.estimate_cost_cents(request)
            except NotFoundError:
                issues.append(
                    ActionValidationIssue(
                        path="$.connector",
                        message=f"connector {manifest.connector_key!r} is not registered",
                        code="connector_missing",
                    )
                )
        elif manifest.requires_credential or manifest.risk_level != "read":
            issues.append(
                ActionValidationIssue(
                    path="$.connector",
                    message="action has no connector configured for execution",
                    code="connector_missing",
                )
            )
        return ActionValidationOut(
            valid=not issues,
            manifest=manifest,
            issues=issues,
            connector_registered=connector_registered,
            estimated_cost_cents=estimated_cost_cents,
            credential_ref=resolved_ref,
        )

    async def execute(
        self,
        *,
        project_id: int,
        action_ref: str | None = None,
        plugin_slug: str | None = None,
        action_key: str | None = None,
        input_json: dict[str, Any] | None = None,
        credential_ref: str | None = None,
        run_id: int | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        idempotency_key: str | None = None,
        dry_run: bool = False,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[ActionExecutionOut]:
        self._require_project(project_id)
        manifest = self._manifest(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        payload, resolved_ref = self._normalize_payload_and_ref(
            input_json or {},
            credential_ref=credential_ref,
        )
        validation = self.validate(
            project_id=project_id,
            action_ref=manifest.action_ref,
            input_json=payload,
            credential_ref=resolved_ref,
        )
        if not validation.valid:
            raise ValidationError(
                "action payload is invalid",
                data={
                    "action_ref": manifest.action_ref,
                    "issues": [issue.model_dump(mode="json") for issue in validation.issues],
                },
            )
        if manifest.connector_key is None:
            raise ValidationError(
                "action has no connector configured for execution",
                data={"action_ref": manifest.action_ref},
            )

        self._check_run_scope(
            project_id=project_id,
            run_id=run_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
        )
        if idempotency_key is not None:
            replay = self._idempotency_replay(
                project_id=project_id,
                manifest=manifest,
                idempotency_key=idempotency_key,
                request_json=payload,
                credential_ref=resolved_ref,
                dry_run=dry_run,
            )
            if replay is not None:
                return Envelope(data=replay, project_id=project_id, run_id=run_id)
        connector = self._connectors.get(manifest.connector_key)
        dry_request = self._connector_request(
            project_id=project_id,
            manifest=manifest,
            input_json=payload,
            credential=None,
            dry_run=True,
        )
        estimated_cost_cents = max(0, connector.estimate_cost_cents(dry_request))
        if dry_run:
            row = self._record_call(
                project_id=project_id,
                manifest=manifest,
                credential=None,
                credential_ref=resolved_ref,
                run_id=run_id,
                run_plan_id=run_plan_id,
                run_plan_step_id=run_plan_step_id,
                idempotency_key=idempotency_key,
                request_json=payload,
                response_json={
                    "dry_run": True,
                    "valid": True,
                    "estimated_cost_cents": estimated_cost_cents,
                },
                metadata_json=metadata_json,
                status=ActionCallStatus.DRY_RUN,
                dry_run=True,
                cost_cents=estimated_cost_cents,
                duration_ms=0,
            )
            return Envelope(
                data=ActionExecutionOut(
                    action_call=self._call_audit_out(row),
                    output_json=row.response_json or {},
                    metadata_json=row.metadata_json,
                    cost_cents=row.cost_cents,
                    dry_run=True,
                    credential_ref=row.credential_ref,
                ),
                project_id=project_id,
                run_id=run_id,
            )

        credential = self._resolve_credential(
            project_id=project_id,
            manifest=manifest,
            credential_ref=resolved_ref,
        )
        if manifest.enforce_budget and manifest.budget_kind and estimated_cost_cents:
            IntegrationBudgetRepository(self._s).record_call(
                project_id=project_id,
                kind=manifest.budget_kind,
                cost_usd=estimated_cost_cents / 100,
            )
        request = self._connector_request(
            project_id=project_id,
            manifest=manifest,
            input_json=payload,
            credential=credential,
            dry_run=False,
        )
        started = time.perf_counter()
        try:
            result = await connector.execute(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            row = self._record_call(
                project_id=project_id,
                manifest=manifest,
                credential=credential,
                credential_ref=resolved_ref,
                run_id=run_id,
                run_plan_id=run_plan_id,
                run_plan_step_id=run_plan_step_id,
                idempotency_key=idempotency_key,
                request_json=payload,
                response_json=None,
                metadata_json=metadata_json,
                status=ActionCallStatus.FAILED,
                dry_run=False,
                cost_cents=estimated_cost_cents,
                duration_ms=duration_ms,
                error=redact_secret_text(str(exc)),
            )
            raise ConflictError(
                "action connector failed",
                data={
                    "action_ref": manifest.action_ref,
                    "action_call_id": row.id,
                    "connector": manifest.connector_key,
                },
            ) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        output_json = _redact_for_audit(result.output_json)
        result_metadata = _redact_for_audit(result.metadata_json) if result.metadata_json else None
        row = self._record_call(
            project_id=project_id,
            manifest=manifest,
            credential=credential,
            credential_ref=resolved_ref,
            run_id=run_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
            idempotency_key=idempotency_key,
            request_json=payload,
            response_json=output_json,
            metadata_json={
                **(_redact_for_audit(metadata_json) if metadata_json else {}),
                **(result_metadata or {}),
            }
            or None,
            status=ActionCallStatus.SUCCESS,
            dry_run=False,
            cost_cents=max(estimated_cost_cents, result.cost_cents),
            duration_ms=duration_ms,
        )
        return Envelope(
            data=ActionExecutionOut(
                action_call=self._call_audit_out(row),
                output_json=row.response_json or {},
                metadata_json=row.metadata_json,
                cost_cents=row.cost_cents,
                dry_run=False,
                credential_ref=row.credential_ref,
            ),
            project_id=project_id,
            run_id=run_id,
        )

    def _manifest(
        self,
        *,
        action_ref: str | None,
        plugin_slug: str | None,
        action_key: str | None,
    ) -> ExecutableActionManifest:
        PluginRepository(self._s).sync_builtin_plugins()
        resolved_plugin, resolved_action = self._resolve_action_key(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        stmt = (
            select(Action, Plugin, Provider)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .outerjoin(Provider, col(Action.provider_id) == col(Provider.id))
            .where(col(Plugin.slug) == resolved_plugin, col(Action.key) == resolved_action)
        )
        row = self._s.exec(stmt).first()
        if row is None:
            raise NotFoundError(
                f"action {resolved_plugin}.{resolved_action!r} not found",
                data={"plugin_slug": resolved_plugin, "action_key": resolved_action},
            )
        action, plugin, provider = row
        return parse_action_manifest(action=action, plugin=plugin, provider=provider)

    def _resolve_action_key(
        self,
        *,
        action_ref: str | None,
        plugin_slug: str | None,
        action_key: str | None,
    ) -> tuple[str, str]:
        if action_ref is not None:
            if "." not in action_ref:
                raise ValidationError("action_ref must be '<plugin>.<action_key>'")
            resolved_plugin, resolved_action = action_ref.split(".", 1)
            if not resolved_plugin or not resolved_action:
                raise ValidationError("action_ref must be '<plugin>.<action_key>'")
            return resolved_plugin, resolved_action
        if plugin_slug is None or action_key is None:
            raise ValidationError("action_ref or plugin_slug/action_key is required")
        return plugin_slug, action_key

    def _normalize_payload_and_ref(
        self,
        input_json: dict[str, Any],
        *,
        credential_ref: str | None,
    ) -> tuple[dict[str, Any], str | None]:
        payload = dict(input_json)
        embedded = payload.pop("credential_ref", None)
        if embedded is not None and not isinstance(embedded, str):
            raise ValidationError("credential_ref must be a string")
        resolved_ref = credential_ref or embedded
        secret_paths = find_run_plan_secret_paths(payload)
        if secret_paths:
            raise ValidationError(
                "action input must not contain secrets; use opaque credential_ref values",
                data={"paths": secret_paths[:8]},
            )
        return payload, resolved_ref

    def _validate_payload(
        self,
        *,
        manifest: ExecutableActionManifest,
        payload: dict[str, Any],
    ) -> list[ActionValidationIssue]:
        if not manifest.input_schema_json:
            return []
        return _schema_issues(manifest.input_schema_json, payload)

    def _credential_ref_issues(
        self,
        *,
        project_id: int | None,
        manifest: ExecutableActionManifest,
        credential_ref: str | None,
    ) -> list[ActionValidationIssue]:
        if credential_ref is not None and not manifest.allows_credential:
            return [
                ActionValidationIssue(
                    path="$.credential_ref",
                    message="credential_ref is not allowed for this action",
                    code="credential_not_allowed",
                )
            ]
        if not manifest.requires_credential and credential_ref is None:
            return []
        if credential_ref is None:
            return [
                ActionValidationIssue(
                    path="$.credential_ref",
                    message="credential_ref is required for this action",
                    code="credential_required",
                )
            ]
        if project_id is None:
            return [
                ActionValidationIssue(
                    path="$.project_id",
                    message="project_id is required when credential_ref is supplied",
                    code="credential_project_required",
                )
            ]
        credential = self._s.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).first()
        if credential is None:
            return [
                ActionValidationIssue(
                    path="$.credential_ref",
                    message="credential_ref was not found",
                    code="credential_not_found",
                )
            ]
        if credential.revoked_at is not None:
            return [
                ActionValidationIssue(
                    path="$.credential_ref",
                    message="credential is revoked",
                    code="credential_revoked",
                )
            ]
        if credential.project_id is not None and credential.project_id != project_id:
            return [
                ActionValidationIssue(
                    path="$.credential_ref",
                    message="credential does not belong to this project",
                    code="credential_project_mismatch",
                )
            ]
        if manifest.provider_key is not None and credential.provider_key != manifest.provider_key:
            return [
                ActionValidationIssue(
                    path="$.credential_ref",
                    message="credential provider does not match action provider",
                    code="credential_provider_mismatch",
                )
            ]
        return []

    def _resolve_credential(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        credential_ref: str | None,
    ) -> ResolvedCredential | None:
        if credential_ref is not None and not manifest.allows_credential:
            raise ValidationError(
                "credential_ref is not allowed for this action",
                data={"action_ref": manifest.action_ref},
            )
        if not manifest.requires_credential and credential_ref is None:
            return None
        return AuthRepository(self._s).resolve_for_execution(
            project_id=project_id,
            provider_key=manifest.provider_key,
            credential_ref=credential_ref,
            operation=f"action.{manifest.action_ref}",
        )

    def _connector_request(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        input_json: dict[str, Any],
        credential: ResolvedCredential | None,
        dry_run: bool,
    ) -> ActionConnectorRequest:
        return ActionConnectorRequest(
            project_id=project_id,
            plugin_slug=manifest.plugin_slug,
            action_key=manifest.action_key,
            action_ref=manifest.action_ref,
            provider_key=manifest.provider_key,
            operation=manifest.operation,
            input_json=input_json,
            config_json=manifest.config_json,
            credential=credential,
            asset_dir=self._asset_dir,
            dry_run=dry_run,
        )

    def _record_call(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        credential: ResolvedCredential | None,
        credential_ref: str | None,
        run_id: int | None,
        run_plan_id: int | None,
        run_plan_step_id: int | None,
        idempotency_key: str | None,
        request_json: dict[str, Any],
        response_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
        status: ActionCallStatus,
        dry_run: bool,
        cost_cents: int,
        duration_ms: int | None,
        error: str | None = None,
    ) -> ActionCall:
        now = _utcnow()
        row = ActionCall(
            project_id=project_id,
            run_id=run_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
            action_id=manifest.action_id,
            credential_id=credential.credential_id if credential is not None else None,
            action_key=manifest.action_key,
            plugin_slug=manifest.plugin_slug,
            provider_key=manifest.provider_key,
            connector_key=manifest.connector_key,
            operation=manifest.operation,
            status=status,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            credential_ref=credential_ref,
            request_json=_redact_for_audit(request_json),
            response_json=_redact_for_audit(response_json) if response_json is not None else None,
            metadata_json=_redact_for_audit(metadata_json) if metadata_json is not None else None,
            cost_cents=cost_cents,
            duration_ms=duration_ms,
            error=redact_secret_text(error) if error is not None else None,
            created_at=now,
            completed_at=now,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return row

    def _call_out(self, row: ActionCall) -> ActionCallOut:
        assert row.id is not None
        return ActionCallOut(
            id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            run_plan_id=row.run_plan_id,
            run_plan_step_id=row.run_plan_step_id,
            action_id=row.action_id,
            credential_id=row.credential_id,
            action_key=row.action_key,
            plugin_slug=row.plugin_slug,
            provider_key=row.provider_key,
            connector_key=row.connector_key,
            operation=row.operation,
            status=row.status,
            dry_run=row.dry_run,
            idempotency_key=row.idempotency_key,
            credential_ref=row.credential_ref,
            request_json=_redact_for_audit(row.request_json),
            response_json=_redact_for_audit(row.response_json),
            metadata_json=_redact_for_audit(row.metadata_json),
            cost_cents=row.cost_cents,
            duration_ms=row.duration_ms,
            error=redact_secret_text(row.error) if row.error is not None else None,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    def _call_audit_out(self, row: ActionCall) -> ActionCallAuditOut:
        assert row.id is not None
        return ActionCallAuditOut(
            id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            run_plan_id=row.run_plan_id,
            run_plan_step_id=row.run_plan_step_id,
            action_key=row.action_key,
            plugin_slug=row.plugin_slug,
            provider_key=row.provider_key,
            connector_key=row.connector_key,
            operation=row.operation,
            status=row.status,
            dry_run=row.dry_run,
            credential_ref=row.credential_ref,
            request_json=_redact_for_audit(row.request_json),
            response_json=_redact_for_audit(row.response_json),
            metadata_json=_redact_for_audit(row.metadata_json),
            cost_cents=row.cost_cents,
            duration_ms=row.duration_ms,
            error=redact_secret_text(row.error) if row.error is not None else None,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    def _idempotency_replay(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        idempotency_key: str,
        request_json: dict[str, Any],
        credential_ref: str | None,
        dry_run: bool,
    ) -> ActionExecutionOut | None:
        row = self._s.exec(
            select(ActionCall)
            .where(
                ActionCall.project_id == project_id,
                ActionCall.plugin_slug == manifest.plugin_slug,
                ActionCall.action_key == manifest.action_key,
                ActionCall.idempotency_key == idempotency_key,
                ActionCall.status.in_([ActionCallStatus.SUCCESS, ActionCallStatus.DRY_RUN]),  # type: ignore[attr-defined]
            )
            .order_by(ActionCall.id.desc())  # type: ignore[union-attr]
        ).first()
        if row is None:
            return None
        if (
            row.request_json != _redact_for_audit(request_json)
            or row.credential_ref != credential_ref
            or row.dry_run != dry_run
        ):
            raise ConflictError(
                "idempotency key replayed with different action request",
                data={
                    "project_id": project_id,
                    "action_ref": manifest.action_ref,
                    "idempotency_key": idempotency_key,
                    "action_call_id": row.id,
                },
            )
        return ActionExecutionOut(
            action_call=self._call_audit_out(row),
            output_json=row.response_json or {},
            metadata_json=row.metadata_json,
            cost_cents=row.cost_cents,
            dry_run=row.dry_run,
            replayed=True,
            credential_ref=row.credential_ref,
        )

    def _check_run_scope(
        self,
        *,
        project_id: int,
        run_id: int | None,
        run_plan_id: int | None,
        run_plan_step_id: int | None,
    ) -> None:
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None:
                raise NotFoundError(f"run {run_id} not found")
            if run.project_id is not None and run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found for project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        if run_plan_id is not None:
            plan = self._s.get(RunPlan, run_plan_id)
            if plan is None or plan.project_id != project_id:
                raise NotFoundError(
                    f"run plan {run_plan_id} not found for project {project_id}",
                    data={"project_id": project_id, "run_plan_id": run_plan_id},
                )
            if run_id is not None and plan.run_id != run_id:
                raise ConflictError(
                    "run plan is not linked to the supplied run",
                    data={"run_id": run_id, "run_plan_id": run_plan_id},
                )
        if run_plan_step_id is not None:
            step = self._s.get(RunPlanStep, run_plan_step_id)
            if step is None:
                raise NotFoundError(f"run plan step {run_plan_step_id} not found")
            step_plan = self._s.get(RunPlan, step.run_plan_id)
            if step_plan is None or step_plan.project_id != project_id:
                raise NotFoundError(
                    f"run plan step {run_plan_step_id} not found for project {project_id}",
                    data={"project_id": project_id, "run_plan_step_id": run_plan_step_id},
                )
            if run_plan_id is not None and step.run_plan_id != run_plan_id:
                raise ConflictError(
                    "run plan step is not linked to the supplied run plan",
                    data={"run_plan_id": run_plan_id, "run_plan_step_id": run_plan_step_id},
                )
            if run_id is not None and step_plan.run_id != run_id:
                raise ConflictError(
                    "run plan step is not linked to the supplied run",
                    data={"run_id": run_id, "run_plan_step_id": run_plan_step_id},
                )

    def _require_project(self, project_id: int) -> None:
        from content_stack.db.models import Project

        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")


__all__ = [
    "ActionCallAuditOut",
    "ActionCallOut",
    "ActionDescribeOut",
    "ActionExecutionOut",
    "ActionRepository",
    "ActionValidationOut",
    "_redact_for_audit",
]
