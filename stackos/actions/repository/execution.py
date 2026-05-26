"""Action execution pipeline and connector dispatch."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import time
from typing import Any

from stackos.action_availability import build_action_availability
from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.manifest import ExecutableActionManifest
from stackos.artifacts import redact_secret_text
from stackos.auth_providers import AuthRepository, ResolvedCredential
from stackos.db.models import ActionCallStatus
from stackos.repositories.base import ConflictError, Envelope, ValidationError
from stackos.repositories.projects import IntegrationBudgetRepository

from .schema import ActionExecutionOut
from .utils import _redact_for_audit


class ActionExecutionMixin:
    """Execute explicit actions through the canonical connector boundary."""

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
        manifest, provider_config_json = self._manifest_with_provider_config(
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
        availability = build_action_availability(
            self._s,
            manifest=manifest,
            connector_keys=set(self._connectors.list_keys()),
            project_id=project_id,
            provider_config_json=provider_config_json,
            plugin_disabled=self._plugin_disabled_for_project(
                project_id=project_id,
                plugin_slug=manifest.plugin_slug,
            ),
        )
        if availability.status in {"plugin_disabled", "provider_disabled"}:
            raise ValidationError(
                "action is disabled for this project",
                data={
                    "action_ref": manifest.action_ref,
                    "status": availability.status,
                    "reasons": availability.reasons,
                },
            )
        if not dry_run and not availability.executable:
            raise ValidationError(
                "action is not executable for this project",
                data={
                    "action_ref": manifest.action_ref,
                    "status": availability.status,
                    "reasons": availability.reasons,
                },
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
            safe_error = redact_secret_text(str(exc))
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
                error=safe_error,
            )
            raise ConflictError(
                "action connector failed",
                data={
                    "action_ref": manifest.action_ref,
                    "action_call_id": row.id,
                    "connector": manifest.connector_key,
                    "error": safe_error,
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
            session=self._s,
            dry_run=dry_run,
        )
