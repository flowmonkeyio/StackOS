"""Action payload validation and credential-ref checks."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import select

from stackos.actions.connectors import ActionValidationIssue
from stackos.actions.manifest import ExecutableActionManifest
from stackos.db.models import Credential
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.execution_contexts import ExecutionContextRepository
from stackos.repositories.secrets import PayloadSecretRepository
from stackos.secret_refs import find_secret_ref_occurrences, project_secret_refs
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

from .schema import ActionValidationOut
from .utils import _schema_issues


@dataclass(frozen=True)
class RuntimeActionContext:
    credential_ref: str | None
    provider_context_json: dict[str, Any]
    context_ref: str | None = None
    output_policy_json: dict[str, Any] = field(default_factory=dict)
    request_budget_json: dict[str, Any] = field(default_factory=dict)
    artifact_namespace: str | None = None
    issues: list[ActionValidationIssue] = field(default_factory=list)


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
        context_ref: str | None = None,
        provider_context_json: dict[str, Any] | None = None,
        credential_ref: str | None = None,
    ) -> ActionValidationOut:
        payload, resolved_ref = self._normalize_payload_and_ref(
            input_json or {},
            credential_ref=credential_ref,
        )
        manifest = self._manifest(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
            project_id=project_id,
            context_ref=context_ref,
            credential_ref=resolved_ref,
        )
        provider_context = self._normalize_provider_context(provider_context_json)
        runtime_context = self._resolve_runtime_context(
            project_id=project_id,
            manifest=manifest,
            context_ref=context_ref,
            credential_ref=resolved_ref,
            provider_context_json=provider_context,
        )
        projected_payload = project_secret_refs(payload)
        secret_repository = PayloadSecretRepository(self._s)
        issues = [
            ActionValidationIssue(
                path=occurrence.path,
                message="payload secret reference is unavailable",
                code="secret_ref_unavailable",
            )
            for occurrence in find_secret_ref_occurrences(payload)
            if project_id is None
            or not secret_repository.is_available(
                project_id=project_id,
                secret_ref=occurrence.secret_ref,
            )
        ]
        issues.extend(self._validate_payload(manifest=manifest, payload=projected_payload))
        issues.extend(runtime_context.issues)
        issues.extend(
            self._validate_provider_context(
                manifest=manifest,
                provider_context_json=runtime_context.provider_context_json,
            )
        )
        if not (manifest.execution_mode is not None and manifest.connector_key is None):
            issues.extend(
                self._credential_ref_issues(
                    project_id=project_id,
                    manifest=manifest,
                    credential_ref=runtime_context.credential_ref,
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
                    input_json=projected_payload,
                    provider_context_json=runtime_context.provider_context_json,
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
        issues = _dedupe_action_issues(issues)
        return ActionValidationOut(
            valid=not issues,
            manifest=manifest,
            issues=issues,
            connector_registered=connector_registered,
            estimated_cost_cents=estimated_cost_cents,
            credential_ref=runtime_context.credential_ref,
        )

    def _resolve_runtime_context(
        self,
        *,
        project_id: int | None,
        manifest: ExecutableActionManifest,
        context_ref: str | None,
        credential_ref: str | None,
        provider_context_json: dict[str, Any],
    ) -> RuntimeActionContext:
        if context_ref is None:
            return RuntimeActionContext(
                credential_ref=credential_ref,
                provider_context_json=dict(provider_context_json),
            )
        if project_id is None:
            return RuntimeActionContext(
                credential_ref=credential_ref,
                provider_context_json=dict(provider_context_json),
                context_ref=context_ref,
                issues=[
                    ActionValidationIssue(
                        path="$.context_ref",
                        message="project_id is required to resolve context_ref",
                        code="execution_context_project_required",
                    )
                ],
            )
        try:
            resolved = ExecutionContextRepository(self._s).resolve(
                project_id=project_id,
                context_ref=context_ref,
                action_ref=manifest.action_ref,
            )
        except NotFoundError as exc:
            return RuntimeActionContext(
                credential_ref=credential_ref,
                provider_context_json=dict(provider_context_json),
                context_ref=context_ref,
                issues=[
                    ActionValidationIssue(
                        path="$.context_ref",
                        message=exc.detail,
                        code="execution_context_not_found",
                    )
                ],
            )
        issues = [_context_issue_to_action_issue(issue) for issue in resolved.issues]
        context = resolved.context
        merged_credential_ref = credential_ref or resolved.credential_ref
        if (
            credential_ref is not None
            and context.credential_locked
            and resolved.credential_ref is not None
            and credential_ref != resolved.credential_ref
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.credential_ref",
                    message="credential_ref is locked by the execution context",
                    code="execution_context_credential_locked",
                )
            )
        merged_provider_context = dict(resolved.provider_context_json or {})
        explicit_provider_context = dict(provider_context_json)
        for field_name in context.provider_context_locked_fields_json:
            key = _top_level_locked_key(field_name)
            if (
                key
                and key in explicit_provider_context
                and key in merged_provider_context
                and explicit_provider_context[key] != merged_provider_context[key]
            ):
                issues.append(
                    ActionValidationIssue(
                        path=f"$.provider_context_json.{key}",
                        message="field is locked by the execution context",
                        code="execution_context_field_locked",
                    )
                )
        merged_provider_context.update(explicit_provider_context)
        return RuntimeActionContext(
            credential_ref=merged_credential_ref,
            provider_context_json=merged_provider_context,
            context_ref=context_ref,
            output_policy_json=dict(resolved.output_policy_json or {}),
            request_budget_json=dict(resolved.request_budget_json or {}),
            artifact_namespace=resolved.artifact_namespace,
            issues=issues,
        )

    def _normalize_provider_context(
        self,
        provider_context_json: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if provider_context_json is None:
            return {}
        if not isinstance(provider_context_json, dict):
            raise ValidationError("provider_context_json must be an object")
        secret_paths = find_run_plan_secret_paths(provider_context_json)
        if secret_paths:
            raise ValidationError(
                "provider_context_json must not contain secrets; use opaque credential_ref values",
                data={"paths": secret_paths[:8]},
            )
        return dict(provider_context_json)

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
                "action input must not contain secrets as raw values; use secret.set and an exact "
                "$secret_ref marker for payload values; credential_ref is only for provider auth",
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

    def _validate_provider_context(
        self,
        *,
        manifest: ExecutableActionManifest,
        provider_context_json: dict[str, Any],
    ) -> list[ActionValidationIssue]:
        if not provider_context_json:
            return []
        schema = manifest.provider_context_schema_json
        if not schema:
            return [
                ActionValidationIssue(
                    path="$.provider_context_json",
                    message="provider context is not allowed for this action",
                    code="provider_context_not_allowed",
                )
            ]
        return _schema_issues(schema, provider_context_json, path="$.provider_context_json")

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


def _context_issue_to_action_issue(issue: dict[str, Any]) -> ActionValidationIssue:
    return ActionValidationIssue(
        path=str(issue.get("path") or "$.context_ref"),
        message=str(issue.get("message") or "execution context is not compatible"),
        code=str(issue.get("code") or "execution_context_incompatible"),
    )


def _dedupe_action_issues(issues: list[ActionValidationIssue]) -> list[ActionValidationIssue]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ActionValidationIssue] = []
    for issue in issues:
        key = (issue.path, issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _top_level_locked_key(value: str) -> str | None:
    key = value.strip()
    if not key:
        return None
    if key.startswith("$."):
        key = key[2:]
    if key.startswith("."):
        key = key[1:]
    if "." in key or "[" in key or "]" in key:
        return None
    return key or None
