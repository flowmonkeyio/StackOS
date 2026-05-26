"""Action payload validation and credential-ref checks."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlmodel import select

from stackos.actions.connectors import ActionValidationIssue
from stackos.actions.manifest import ExecutableActionManifest
from stackos.db.models import Credential
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

from .schema import ActionValidationOut
from .utils import _schema_issues


class ActionValidationMixin:
    """Validate payloads and setup references without connector side effects."""

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
        if not (manifest.execution_mode is not None and manifest.connector_key is None):
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
        elif manifest.execution_mode is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.execution_mode",
                    message=manifest.deferred_reason
                    or f"action execution mode is {manifest.execution_mode}",
                    code="execution_deferred",
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
        if credential.status != "connected":
            return [
                ActionValidationIssue(
                    path="$.credential_ref",
                    message=f"credential is {credential.status}",
                    code="credential_not_connected",
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
