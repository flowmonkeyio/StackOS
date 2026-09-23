"""Action execution pipeline and connector dispatch."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session

from stackos.action_availability import build_action_availability, build_action_exposure
from stackos.action_output_contract import ACTION_OUTPUT_SCHEMA_REF, action_output_schema_hint
from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionProgressCallback,
)
from stackos.actions.manifest import ExecutableActionManifest
from stackos.artifacts import redact_secret_text
from stackos.auth_providers import AuthRepository, ResolvedCredential
from stackos.config import Settings
from stackos.db.models import ActionCall, ActionCallStatus
from stackos.repositories.base import ConflictError, Envelope, ValidationError
from stackos.repositories.projects import IntegrationBudgetRepository
from stackos.repositories.secrets import PayloadSecretRepository
from stackos.secret_refs import (
    materialize_secret_refs,
    project_secret_refs,
    redact_secret_values,
)

from .background import BACKGROUND_ACTION_TASKS
from .durable import durable_action_schedule_snapshot
from .schema import ActionExecutionOut
from .utils import _redact_for_audit, utcnow
from .validation import RuntimeActionContext

ACTION_OUTPUT_SCHEMA_VERSION = ACTION_OUTPUT_SCHEMA_REF
MAX_TRANSIENT_OUTPUT_BYTES = 262_144


@dataclass(frozen=True)
class _PreparedActionExecution:
    project_id: int
    manifest: ExecutableActionManifest
    payload: dict[str, Any]
    provider_context: dict[str, Any]
    provider_context_for_audit: dict[str, Any] | None
    credential_ref: str | None
    runtime_context: RuntimeActionContext
    effective_output_policy: dict[str, Any]
    metadata_json: dict[str, Any] | None
    estimated_cost_cents: int
    run_id: int | None
    run_plan_id: int | None
    run_plan_step_id: int | None
    idempotency_key: str | None


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
        context_ref: str | None = None,
        provider_context_json: dict[str, Any] | None = None,
        credential_ref: str | None = None,
        output_policy_json: dict[str, Any] | None = None,
        default_external_file_output: bool = False,
        allow_transient_response: bool = False,
        transient_replay_requested: bool = False,
        derived_workflow_idempotency: bool = False,
        run_id: int | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        idempotency_key: str | None = None,
        dry_run: bool = False,
        metadata_json: dict[str, Any] | None = None,
        durable_items: list[dict[str, Any]] | None = None,
        durable_due_at: datetime | None = None,
        durable_expires_at: datetime | None = None,
        durable_account_interval_seconds: float | None = None,
        durable_destination_interval_seconds: float | None = None,
    ) -> Envelope[ActionExecutionOut]:
        self._require_project(project_id)
        payload, resolved_ref = self._normalize_payload_and_ref(
            input_json or {},
            credential_ref=credential_ref,
        )
        manifest, provider_config_json = self._manifest_with_provider_config(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
            project_id=project_id,
            context_ref=context_ref,
            credential_ref=resolved_ref,
        )
        explicit_provider_context = self._normalize_provider_context(provider_context_json)
        runtime_context = self._resolve_runtime_context(
            project_id=project_id,
            manifest=manifest,
            context_ref=context_ref,
            credential_ref=resolved_ref,
            provider_context_json=explicit_provider_context,
        )
        if output_policy_json is not None:
            runtime_context = replace(
                runtime_context,
                output_policy_json=dict(output_policy_json),
            )
        resolved_ref = runtime_context.credential_ref
        provider_context = runtime_context.provider_context_json
        provider_context_for_audit = provider_context or None
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
        exposure = build_action_exposure(
            availability,
            project_id=project_id,
            plugin_slug=manifest.plugin_slug,
            provider_key=manifest.provider_key,
            requires_credential=manifest.requires_credential,
            allows_credential=manifest.allows_credential,
        )
        validation = self.validate(
            project_id=project_id,
            action_ref=manifest.action_ref,
            input_json=payload,
            provider_context_json=provider_context,
            credential_ref=resolved_ref,
            idempotency_key=idempotency_key,
        )
        validation_issues = _dedupe_validation_issues([*runtime_context.issues, *validation.issues])
        if validation_issues:
            credential_repair_operation = manifest.config_json.get("credential_repair_operation")
            repair = (
                {
                    "status": "not_connected",
                    "credential_ref": resolved_ref,
                    "next_action": credential_repair_operation,
                    "provider_executed": False,
                    "retry_safe": True,
                }
                if isinstance(credential_repair_operation, str)
                and credential_repair_operation
                and any(issue.code == "credential_not_connected" for issue in validation_issues)
                else {}
            )
            raise ValidationError(
                "action payload is invalid",
                data={
                    "action_ref": manifest.action_ref,
                    "status": availability.status,
                    "reasons": availability.reasons,
                    "exposure": exposure.model_dump(mode="json"),
                    "issues": [issue.model_dump(mode="json") for issue in validation_issues],
                    **repair,
                },
            )
        effective_output_policy = _effective_output_policy(
            manifest=manifest,
            runtime_context=runtime_context,
            default_external_file_output=default_external_file_output,
        )
        if derived_workflow_idempotency and (
            manifest.risk_level == "read" or effective_output_policy["mode"] == "transient"
        ):
            if run_plan_id is None or run_plan_step_id is None:
                raise ValidationError(
                    "derived workflow idempotency requires an active run-plan step",
                    data={"action_ref": manifest.action_ref, "side_effect": "not_started"},
                )
            # A new workflow read observes current provider state. Reusing an
            # automatically derived key would replay a pre-mutation snapshot.
            # Explicit caller keys still select replay, including for reads.
            idempotency_key = None
            metadata_json = dict(metadata_json or {})
            metadata_json.pop("dedupe_source", None)
        metadata_json = _metadata_with_execution_context(metadata_json, runtime_context)
        if manifest.connector_key is None:
            raise ValidationError(
                "action has no connector configured for execution",
                data={"action_ref": manifest.action_ref},
            )
        if availability.status in {"plugin_disabled", "provider_disabled"}:
            raise ValidationError(
                "action is disabled for this project",
                data={
                    "action_ref": manifest.action_ref,
                    "status": availability.status,
                    "reasons": availability.reasons,
                    "exposure": exposure.model_dump(mode="json"),
                },
            )
        if not dry_run and not availability.executable:
            raise ValidationError(
                "action is not executable for this project",
                data={
                    "action_ref": manifest.action_ref,
                    "status": availability.status,
                    "reasons": availability.reasons,
                    "exposure": exposure.model_dump(mode="json"),
                },
            )

        self._check_run_scope(
            project_id=project_id,
            run_id=run_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
        )
        background_execution = manifest.execution_mode == "background" and not dry_run
        durable_capable = durable_items is not None or bool(
            manifest.config_json.get("durable_delivery")
        )
        durable_execution = durable_capable and not dry_run
        if effective_output_policy["mode"] == "transient":
            if not allow_transient_response:
                raise ValidationError(
                    "transient output requires a raw foreground action response",
                    data={"action_ref": manifest.action_ref, "side_effect": "not_started"},
                )
            if (
                dry_run
                or manifest.risk_level != "read"
                or manifest.provider_key is None
                or background_execution
                or durable_capable
                or idempotency_key is not None
                or transient_replay_requested
            ):
                raise ValidationError(
                    "transient output supports only non-replayable foreground provider reads",
                    data={"action_ref": manifest.action_ref, "side_effect": "not_started"},
                )
        durable_options_requested = any(
            value is not None
            for value in (
                durable_due_at,
                durable_expires_at,
                durable_account_interval_seconds,
                durable_destination_interval_seconds,
            )
        )
        if durable_options_requested and not durable_capable:
            raise ValidationError(
                "due_at, expires_at, and pacing are only available for durable background actions",
                data={"action_ref": manifest.action_ref},
            )
        if durable_execution and not background_execution:
            raise ValidationError(
                "durable action dispatch requires a background action manifest",
                data={"action_ref": manifest.action_ref},
            )
        durable_pacing = (
            _effective_durable_pacing(
                manifest=manifest,
                auth_method_key=(
                    self._credential_for_project(
                        project_id=project_id,
                        credential_ref=resolved_ref,
                    ).auth_method_key
                    if durable_execution
                    and "durable_pacing_by_auth_method_json" in manifest.config_json
                    and resolved_ref is not None
                    else None
                ),
                account_interval_seconds=durable_account_interval_seconds,
                destination_interval_seconds=durable_destination_interval_seconds,
            )
            if durable_capable
            else None
        )
        durable_schedule = (
            durable_action_schedule_snapshot(
                due_at=durable_due_at,
                expires_at=durable_expires_at,
                pacing_json=durable_pacing,
            )
            if durable_pacing is not None
            else None
        )
        if durable_execution:
            replay = self._background_replay(
                project_id=project_id,
                manifest=manifest,
                credential_ref=resolved_ref,
                idempotency_key=idempotency_key,
                request_json=payload,
                provider_context_json=provider_context_for_audit,
                metadata_json=metadata_json,
                durable_schedule_json=durable_schedule,
            )
            if replay is not None:
                return Envelope(
                    data=ActionExecutionOut(
                        action_call=self._call_audit_out(replay),
                        output_json=replay.response_json or {},
                        metadata_json=replay.metadata_json,
                        cost_cents=replay.cost_cents,
                        replayed=True,
                        credential_ref=replay.credential_ref,
                        poll_operation=(
                            "actionCall.get" if replay.status == ActionCallStatus.RUNNING else None
                        ),
                        poll_arguments=(
                            {"action_call_id": replay.id}
                            if replay.status == ActionCallStatus.RUNNING
                            else None
                        ),
                        next_poll_after_ms=(
                            500 if replay.status == ActionCallStatus.RUNNING else None
                        ),
                    ),
                    project_id=project_id,
                    run_id=run_id,
                )
        if durable_capable:
            self.validate_durable_action_schedule(
                due_at=durable_due_at,
                expires_at=durable_expires_at,
            )
        if idempotency_key is not None and not background_execution:
            replay = self._idempotency_replay(
                project_id=project_id,
                manifest=manifest,
                idempotency_key=idempotency_key,
                request_json=payload,
                provider_context_json=provider_context_for_audit,
                credential_ref=resolved_ref,
                dry_run=dry_run,
            )
            if replay is not None:
                return Envelope(data=replay, project_id=project_id, run_id=run_id)
        connector = self._connectors.get(manifest.connector_key)
        projected_payload = project_secret_refs(payload)
        dry_request = self._connector_request(
            project_id=project_id,
            manifest=manifest,
            input_json=projected_payload,
            provider_context_json=provider_context,
            credential=None,
            dry_run=True,
            idempotency_key=idempotency_key,
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
                provider_context_json=provider_context_for_audit,
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

        prepared = _PreparedActionExecution(
            project_id=project_id,
            manifest=manifest,
            payload=payload,
            provider_context=provider_context,
            provider_context_for_audit=provider_context_for_audit,
            credential_ref=resolved_ref,
            runtime_context=runtime_context,
            effective_output_policy=effective_output_policy,
            metadata_json=metadata_json,
            estimated_cost_cents=estimated_cost_cents,
            run_id=run_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
            idempotency_key=idempotency_key,
        )
        if background_execution:
            row, replayed = self._reserve_background_call(
                project_id=project_id,
                manifest=manifest,
                credential_ref=resolved_ref,
                run_id=run_id,
                run_plan_id=run_plan_id,
                run_plan_step_id=run_plan_step_id,
                idempotency_key=idempotency_key,
                request_json=payload,
                provider_context_json=provider_context_for_audit,
                metadata_json=metadata_json,
                estimated_cost_cents=estimated_cost_cents,
                durable_schedule_json=durable_schedule,
            )
            assert row.id is not None
            if durable_execution:
                existing_job = self.get_durable_action_job_for_action_call(
                    project_id=project_id, action_call_id=row.id
                )
                if existing_job is None:
                    if row.status != ActionCallStatus.RUNNING:
                        raise ConflictError(
                            "durable action reservation is terminal without a durable job",
                            data={"action_call_id": row.id, "status": row.status.value},
                        )
                    if resolved_ref is None:
                        raise ValidationError(
                            "durable action dispatch requires an attached Account",
                            data={"action_ref": manifest.action_ref},
                        )
                    try:
                        credential = await self._resolve_credential(
                            project_id=project_id,
                            manifest=manifest,
                            credential_ref=resolved_ref,
                        )
                        if durable_items is not None:
                            normalized_durable_items = self.validate_durable_action_items(
                                durable_items
                            )
                        else:
                            prepare_delivery = getattr(connector, "prepare_delivery", None)
                            if not callable(prepare_delivery):
                                raise ValidationError(
                                    "durable action manifest requires connector "
                                    "delivery preparation",
                                    data={
                                        "action_ref": manifest.action_ref,
                                        "connector": manifest.connector_key,
                                    },
                                )
                            prepared_items = await prepare_delivery(
                                self._connector_request(
                                    project_id=project_id,
                                    manifest=manifest,
                                    input_json=payload,
                                    provider_context_json=provider_context,
                                    credential=credential,
                                    dry_run=False,
                                    idempotency_key=idempotency_key,
                                )
                            )
                            normalized_durable_items = self.validate_durable_action_items(
                                prepared_items
                            )
                        self.create_durable_action_job(
                            project_id=project_id,
                            action_call_id=row.id,
                            credential_ref=resolved_ref,
                            action_ref=manifest.action_ref,
                            items=normalized_durable_items,
                            due_at=durable_due_at,
                            expires_at=durable_expires_at,
                            pacing_json=durable_pacing,
                            pacing_units_input_field=manifest.config_json.get(
                                "durable_pacing_units_input_field"
                            ),
                            destination_interval_multipliers=manifest.config_json.get(
                                "durable_destination_interval_multipliers_json"
                            ),
                            idempotency_key=idempotency_key,
                            metadata_json=metadata_json,
                        )
                    except Exception as exc:
                        self._record_durable_preparation_failure(
                            action_call_id=row.id,
                            error=redact_secret_text(str(exc)),
                        )
                        raise
                return Envelope(
                    data=ActionExecutionOut(
                        action_call=self._call_audit_out(row),
                        output_json=row.response_json or {},
                        metadata_json=row.metadata_json,
                        cost_cents=row.cost_cents,
                        replayed=replayed,
                        credential_ref=row.credential_ref,
                        poll_operation=(
                            "actionCall.get" if row.status == ActionCallStatus.RUNNING else None
                        ),
                        poll_arguments=(
                            {"action_call_id": row.id}
                            if row.status == ActionCallStatus.RUNNING
                            else None
                        ),
                        next_poll_after_ms=500 if row.status == ActionCallStatus.RUNNING else None,
                    ),
                    project_id=project_id,
                    run_id=run_id,
                )
            if not replayed:
                bind = self._s.get_bind()
                repository_type = type(self)
                connectors = self._connectors
                asset_dir = self._asset_dir
                action_call_id = row.id
                BACKGROUND_ACTION_TASKS.start(
                    action_call_id,
                    lambda report: _execute_background_action(
                        repository_type=repository_type,
                        bind=bind,
                        connectors=connectors,
                        asset_dir=asset_dir,
                        prepared=prepared,
                        action_call_id=action_call_id,
                        progress_callback=report,
                    ),
                )
            return Envelope(
                data=ActionExecutionOut(
                    action_call=self._call_audit_out(row),
                    output_json=row.response_json or {},
                    metadata_json=row.metadata_json,
                    cost_cents=row.cost_cents,
                    replayed=replayed,
                    credential_ref=row.credential_ref,
                    poll_operation=(
                        "actionCall.get" if row.status == ActionCallStatus.RUNNING else None
                    ),
                    poll_arguments=(
                        {"action_call_id": row.id}
                        if row.status == ActionCallStatus.RUNNING
                        else None
                    ),
                    next_poll_after_ms=500 if row.status == ActionCallStatus.RUNNING else None,
                ),
                project_id=project_id,
                run_id=run_id,
            )

        row, transient_output = await self._execute_prepared(prepared)
        return Envelope(
            data=ActionExecutionOut(
                action_call=self._call_audit_out(row),
                output_json=(
                    transient_output if transient_output is not None else row.response_json or {}
                ),
                metadata_json=row.metadata_json,
                cost_cents=row.cost_cents,
                dry_run=False,
                credential_ref=row.credential_ref,
            ),
            project_id=project_id,
            run_id=run_id,
        )

    async def _execute_prepared(
        self,
        prepared: _PreparedActionExecution,
        *,
        action_call_id: int | None = None,
        progress_callback: ActionProgressCallback | None = None,
    ) -> Any:
        project_id = prepared.project_id
        manifest = prepared.manifest
        payload = prepared.payload
        provider_context = prepared.provider_context
        provider_context_for_audit = prepared.provider_context_for_audit
        resolved_ref = prepared.credential_ref
        runtime_context = prepared.runtime_context
        effective_output_policy = prepared.effective_output_policy
        metadata_json = prepared.metadata_json
        estimated_cost_cents = prepared.estimated_cost_cents
        run_id = prepared.run_id
        run_plan_id = prepared.run_plan_id
        run_plan_step_id = prepared.run_plan_step_id
        idempotency_key = prepared.idempotency_key
        connector = self._connectors.get(manifest.connector_key or "")
        credential = await self._resolve_credential(
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
        materialized_payload, sensitive_values = materialize_secret_refs(
            payload,
            resolve=lambda secret_ref: PayloadSecretRepository(self._s).resolve(
                project_id=project_id,
                secret_ref=secret_ref,
            ),
        )
        request = self._connector_request(
            project_id=project_id,
            manifest=manifest,
            input_json=materialized_payload,
            provider_context_json=provider_context,
            credential=credential,
            dry_run=False,
            idempotency_key=idempotency_key,
            progress_callback=progress_callback,
            action_call_id=action_call_id,
        )
        started = time.perf_counter()
        try:
            try:
                result = await connector.execute(request)
            finally:
                del request
                del materialized_payload
        except ActionConnectorError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._s.rollback()
            transient = effective_output_policy["mode"] == "transient"
            credential_repair_operation = manifest.config_json.get("credential_repair_operation")
            transient_repair = (
                {
                    "status": "not_connected",
                    "credential_ref": resolved_ref,
                    "next_action": credential_repair_operation,
                    "provider_executed": False,
                    "retry_safe": True,
                }
                if transient
                and exc.output_json.get("status") == "not_connected"
                and isinstance(credential_repair_operation, str)
                and credential_repair_operation
                else {
                    "status": "retryable_timeout",
                    "next_action": manifest.action_ref,
                    "retry_safe": True,
                }
                if transient
                and exc.output_json.get("status") == "retryable_timeout"
                and exc.output_json.get("retry_safe") is True
                else {}
            )
            output_json = (
                {
                    "output_mode": "transient",
                    "retained": False,
                    "replayable": False,
                    "result_available": False,
                    "retry_safe": True,
                    **(
                        {"provider_status_code": exc.provider_status_code}
                        if isinstance(exc.provider_status_code, int)
                        else {}
                    ),
                }
                if transient
                else _redact_for_audit(redact_secret_values(exc.output_json, sensitive_values))
            )
            connector_metadata = (
                _redact_for_audit(redact_secret_values(exc.metadata_json, sensitive_values))
                if exc.metadata_json and not transient
                else {}
            )
            failed_metadata = {
                **(
                    _redact_for_audit(redact_secret_values(metadata_json, sensitive_values))
                    if metadata_json
                    else {}
                ),
                **connector_metadata,
            } or None
            safe_error = (
                "transient provider read failed"
                if transient
                else redact_secret_text(redact_secret_values(exc.detail, sensitive_values))
            )
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
                provider_context_json=provider_context_for_audit,
                response_json=output_json,
                metadata_json=failed_metadata,
                status=ActionCallStatus.FAILED,
                dry_run=False,
                cost_cents=estimated_cost_cents,
                duration_ms=duration_ms,
                error=safe_error,
                action_call_id=action_call_id,
            )
            raise ConflictError(
                "action connector failed",
                data={
                    **_connector_failure_data(
                        manifest=manifest,
                        row_id=int(row.id),
                        connector_key=manifest.connector_key,
                        error=safe_error,
                        output_json=output_json,
                    ),
                    **transient_repair,
                },
            ) from exc
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._s.rollback()
            transient = effective_output_policy["mode"] == "transient"
            safe_error = (
                "transient provider read failed"
                if transient
                else redact_secret_text(redact_secret_values(str(exc), sensitive_values))
            )
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
                provider_context_json=provider_context_for_audit,
                response_json={
                    "output_mode": "transient",
                    "retained": False,
                    "replayable": False,
                    "result_available": False,
                    "retry_safe": True,
                }
                if transient
                else None,
                metadata_json=(
                    redact_secret_values(metadata_json, sensitive_values) if metadata_json else None
                ),
                status=ActionCallStatus.FAILED,
                dry_run=False,
                cost_cents=estimated_cost_cents,
                duration_ms=duration_ms,
                error=safe_error,
                action_call_id=action_call_id,
            )
            raise ConflictError(
                "action connector failed",
                data=_connector_failure_data(
                    manifest=manifest,
                    row_id=int(row.id),
                    connector_key=manifest.connector_key,
                    error=safe_error,
                    output_json=None,
                ),
            ) from exc

        duration_ms = int((time.perf_counter() - started) * 1000)
        output_json = _redact_for_audit(redact_secret_values(result.output_json, sensitive_values))
        result_metadata = (
            _redact_for_audit(redact_secret_values(result.metadata_json, sensitive_values))
            if result.metadata_json
            else None
        )
        actual_cost_cents = max(0, result.cost_cents)
        if (
            manifest.enforce_budget
            and manifest.budget_kind
            and actual_cost_cents > estimated_cost_cents
        ):
            IntegrationBudgetRepository(self._s).record_call(
                project_id=project_id,
                kind=manifest.budget_kind,
                cost_usd=(actual_cost_cents - estimated_cost_cents) / 100,
            )
        success_metadata = {
            **(
                _redact_for_audit(redact_secret_values(metadata_json, sensitive_values))
                if metadata_json
                else {}
            ),
            **(result_metadata or {}),
        } or None
        if effective_output_policy["mode"] == "transient":
            output_bytes = len(_json_bytes(output_json))
            receipt = _transient_output_receipt(output_json, output_bytes=output_bytes)
            transient_metadata = (
                _redact_for_audit(redact_secret_values(metadata_json, sensitive_values))
                if metadata_json
                else None
            )
            within_limit = output_bytes <= effective_output_policy["max_inline_bytes"]
            if not within_limit:
                receipt = {**receipt, "output_limit_exceeded": True, "retry_safe": True}
            row = self._record_call(
                project_id=project_id,
                manifest=manifest,
                credential=credential,
                credential_ref=resolved_ref,
                run_id=run_id,
                run_plan_id=run_plan_id,
                run_plan_step_id=run_plan_step_id,
                idempotency_key=None,
                request_json=payload,
                provider_context_json=provider_context_for_audit,
                response_json=receipt,
                metadata_json=transient_metadata,
                status=ActionCallStatus.SUCCESS if within_limit else ActionCallStatus.FAILED,
                dry_run=False,
                cost_cents=actual_cost_cents,
                duration_ms=duration_ms,
                error=None if within_limit else "transient provider result exceeds response limit",
            )
            if not within_limit:
                raise ConflictError(
                    "transient provider result exceeds response limit",
                    data={
                        "action_ref": manifest.action_ref,
                        "action_call_id": row.id,
                        "output_bytes": output_bytes,
                        "max_inline_bytes": effective_output_policy["max_inline_bytes"],
                        "provider_executed": True,
                        "retry_safe": True,
                    },
                )
            return row, output_json
        try:
            if effective_output_policy["mode"] == "inline":
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
                    provider_context_json=provider_context_for_audit,
                    response_json=output_json,
                    metadata_json=success_metadata,
                    status=ActionCallStatus.SUCCESS,
                    dry_run=False,
                    cost_cents=actual_cost_cents,
                    duration_ms=duration_ms,
                    action_call_id=action_call_id,
                )
            else:
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
                    provider_context_json=provider_context_for_audit,
                    response_json=None,
                    metadata_json=success_metadata,
                    status=ActionCallStatus.SUCCESS,
                    dry_run=False,
                    cost_cents=actual_cost_cents,
                    duration_ms=duration_ms,
                    commit=False,
                    action_call_id=action_call_id,
                )
                row = self._apply_output_policy(
                    project_id=project_id,
                    manifest=manifest,
                    input_json=payload,
                    provider_context_json=provider_context_for_audit,
                    credential_ref=resolved_ref,
                    runtime_context=runtime_context,
                    policy=effective_output_policy,
                    output_json=output_json,
                    row=row,
                )
        except Exception as exc:
            self._s.rollback()
            safe_error = redact_secret_text(redact_secret_values(str(exc), sensitive_values))
            # Dispatch already succeeded. Keep this distinction in the durable audit,
            # not only the immediate error, so a resumed agent cannot mistake a local
            # output failure for permission to repeat a provider mutation.
            mutation = manifest.risk_level != "read"
            persistence_diagnosis = {
                "output_persistence_failed": True,
                "provider_executed": True,
                "outcome_unknown": mutation,
                "retry_safe": not mutation,
                "reconcile_before_retry": mutation,
            }
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
                provider_context_json=provider_context_for_audit,
                response_json={
                    **persistence_diagnosis,
                    "response_summary": _json_summary(output_json),
                },
                metadata_json=success_metadata,
                status=ActionCallStatus.FAILED,
                dry_run=False,
                cost_cents=actual_cost_cents,
                duration_ms=duration_ms,
                error=safe_error,
                action_call_id=action_call_id,
            )
            raise ConflictError(
                "action output persistence failed",
                data={
                    **persistence_diagnosis,
                    "action_ref": manifest.action_ref,
                    "action_call_id": row.id,
                    "connector": manifest.connector_key,
                    "side_effect": "provider_executed_output_not_persisted",
                    "error": safe_error,
                },
            ) from exc
        return row, None

    async def _resolve_credential(
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
        try:
            return await AuthRepository(self._s).resolve_for_execution(
                project_id=project_id,
                provider_key=manifest.provider_key,
                credential_ref=credential_ref,
                operation=f"action.{manifest.action_ref}",
                required_scopes=manifest.required_scopes,
            )
        except ConflictError as exc:
            credential_repair_operation = manifest.config_json.get("credential_repair_operation")
            if exc.data.get("status") and isinstance(credential_repair_operation, str):
                raise ConflictError(
                    exc.detail,
                    data={
                        **exc.data,
                        "next_action": credential_repair_operation,
                        "provider_executed": False,
                        "retry_safe": True,
                    },
                ) from exc
            raise

    def _connector_request(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        input_json: dict[str, Any],
        provider_context_json: dict[str, Any],
        credential: ResolvedCredential | None,
        dry_run: bool,
        credential_ref: str | None = None,
        idempotency_key: str | None = None,
        progress_callback: ActionProgressCallback | None = None,
        action_call_id: int | None = None,
        attempt_ref: str | None = None,
        correlation_ref: str | None = None,
        delivery_item_id: int | None = None,
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
            provider_context_json=provider_context_json,
            credential_ref=credential.credential_ref if credential is not None else credential_ref,
            credential=credential,
            asset_dir=self._asset_dir,
            session=self._s,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            action_call_id=action_call_id,
            attempt_ref=attempt_ref,
            correlation_ref=correlation_ref,
            delivery_item_id=delivery_item_id,
            progress_callback=progress_callback,
        )

    def _record_durable_preparation_failure(self, *, action_call_id: int, error: str) -> None:
        """Finalize a pre-effect durable reservation as a known local failure."""

        self._s.rollback()
        row = self._s.get(ActionCall, action_call_id)
        if row is None or row.status != ActionCallStatus.RUNNING:
            return
        row.status = ActionCallStatus.FAILED
        row.response_json = {
            "status": "failed",
            "phase": "delivery-preparation",
            "provider_executed": False,
            "retry_safe": True,
        }
        row.error = error
        row.completed_at = utcnow()
        self._s.add(row)
        self._s.commit()

    def _apply_output_policy(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        input_json: dict[str, Any],
        provider_context_json: dict[str, Any] | None,
        credential_ref: str | None,
        runtime_context: RuntimeActionContext,
        policy: dict[str, Any],
        output_json: dict[str, Any],
        row: Any,
    ) -> Any:
        if policy["mode"] == "inline":
            row.response_json = output_json
            self._s.add(row)
            self._s.commit()
            self._s.refresh(row)
            return row
        envelope = _action_output_envelope(
            project_id=project_id,
            manifest=manifest,
            input_json=input_json,
            provider_context_json=provider_context_json,
            credential_ref=credential_ref,
            runtime_context=runtime_context,
            row=row,
            output_json=output_json,
        )
        payload = _json_bytes(envelope)
        if policy["mode"] == "file_if_large" and len(payload) <= policy["max_inline_bytes"]:
            row.response_json = output_json
            self._s.add(row)
            self._s.commit()
            self._s.refresh(row)
            return row

        created_at = _utc_iso()
        semantic_name = _semantic_output_name(
            policy=policy,
            runtime_context=runtime_context,
            manifest=manifest,
            action_call_id=int(row.id),
            created_at=created_at,
        )
        absolute_path = _resolve_output_path(
            policy=policy,
            generated_assets_dir=self._asset_dir or Settings().generated_assets_dir,
            project_id=project_id,
            semantic_name=semantic_name,
        )
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(payload)
        sha256 = hashlib.sha256(payload).hexdigest()
        file_pointer = {
            "path": str(absolute_path),
            "content_type": policy["content_type"],
            "schema_version": ACTION_OUTPUT_SCHEMA_VERSION,
            **action_output_schema_hint(),
            "bytes": len(payload),
            "sha256": sha256,
            "semantic_name": semantic_name,
            "action_ref": manifest.action_ref,
            "provider_key": manifest.provider_key,
            "operation": manifest.operation,
            "created_at": created_at,
        }
        row.response_json = {
            "output_mode": "file",
            "file": file_pointer,
            "receipt": {
                "schema_version": ACTION_OUTPUT_SCHEMA_VERSION,
                "action_ref": manifest.action_ref,
                "provider_key": manifest.provider_key,
                "operation": manifest.operation,
                "cost_cents": row.cost_cents,
                "duration_ms": row.duration_ms,
            },
        }
        metadata = dict(row.metadata_json or {})
        metadata["file_backed_output"] = file_pointer
        metadata["output_policy_json"] = policy
        row.metadata_json = metadata
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return row


async def _execute_background_action(
    *,
    repository_type: type[Any],
    bind: Any,
    connectors: Any,
    asset_dir: Path | None,
    prepared: _PreparedActionExecution,
    action_call_id: int,
    progress_callback: ActionProgressCallback,
) -> None:
    with Session(bind) as session:
        repository = repository_type(session, connectors=connectors, asset_dir=asset_dir)
        try:
            await repository._execute_prepared(
                prepared,
                action_call_id=action_call_id,
                progress_callback=progress_callback,
            )
        except ConflictError:
            return
        except Exception:
            with suppress(ConflictError):
                repository._record_call(
                    project_id=prepared.project_id,
                    manifest=prepared.manifest,
                    credential=None,
                    credential_ref=prepared.credential_ref,
                    run_id=prepared.run_id,
                    run_plan_id=prepared.run_plan_id,
                    run_plan_step_id=prepared.run_plan_step_id,
                    idempotency_key=prepared.idempotency_key,
                    request_json=prepared.payload,
                    provider_context_json=prepared.provider_context_for_audit,
                    response_json={"outcome_unknown": True, "retry_safe": False},
                    metadata_json=prepared.metadata_json,
                    status=ActionCallStatus.FAILED,
                    dry_run=False,
                    cost_cents=prepared.estimated_cost_cents,
                    duration_ms=None,
                    error="background-action-worker-failed",
                    action_call_id=action_call_id,
                )


def _effective_durable_pacing(
    *,
    manifest: ExecutableActionManifest,
    auth_method_key: str | None = None,
    account_interval_seconds: float | None,
    destination_interval_seconds: float | None,
) -> dict[str, float]:
    """Apply caller pacing only as a slower bound over the manifest floor."""

    fields = {"account_interval_seconds", "destination_interval_seconds"}

    def config_object(raw: Any, *, field: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValidationError(
                "durable action pacing configuration must be an object",
                data={"action_ref": manifest.action_ref, "field": field},
            )
        for key in raw:
            if key not in fields:
                raise ValidationError(
                    "durable action pacing configuration has an unknown field",
                    data={"action_ref": manifest.action_ref, "field": str(key)},
                )
        return raw

    def positive_finite(value: Any, *, field: str, source: str) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            valid = False
            number = 0.0
        else:
            try:
                number = float(value)
                valid = math.isfinite(number) and number > 0
            except (OverflowError, ValueError):
                valid = False
                number = 0.0
        if not valid:
            raise ValidationError(
                f"durable action pacing {source} must be a positive finite number",
                data={"action_ref": manifest.action_ref, "field": field},
            )
        return number

    configured = config_object(
        manifest.config_json.get("durable_pacing_json", {}),
        field="durable_pacing_json",
    )
    by_method = manifest.config_json.get("durable_pacing_by_auth_method_json", [])
    if not isinstance(by_method, list):
        raise ValidationError(
            "durable action pacing auth-method overrides must be a list",
            data={
                "action_ref": manifest.action_ref,
                "field": "durable_pacing_by_auth_method_json",
            },
        )
    overrides: dict[str, dict[str, float]] = {}
    for entry in by_method:
        if not isinstance(entry, dict):
            raise ValidationError(
                "durable action pacing auth-method entry must be an object",
                data={
                    "action_ref": manifest.action_ref,
                    "field": "durable_pacing_by_auth_method_json",
                },
            )
        for key in entry:
            if key not in {"auth_method_key", "pacing_json"}:
                raise ValidationError(
                    "durable action pacing auth-method entry has an unknown field",
                    data={"action_ref": manifest.action_ref, "field": str(key)},
                )
        method_key = entry.get("auth_method_key")
        if not isinstance(method_key, str) or not method_key or method_key.strip() != method_key:
            raise ValidationError(
                "durable action pacing auth-method key must be a non-empty string",
                data={"action_ref": manifest.action_ref, "field": "auth_method_key"},
            )
        if method_key in overrides:
            raise ValidationError(
                "durable action pacing auth-method key is duplicated",
                data={"action_ref": manifest.action_ref, "field": "auth_method_key"},
            )
        method_config = config_object(entry.get("pacing_json"), field="pacing_json")
        overrides[method_key] = {
            key: positive_finite(value, field=key, source="floor")
            for key, value in method_config.items()
        }
    base = {
        key: positive_finite(value, field=key, source="floor") for key, value in configured.items()
    }
    selected = overrides.get(auth_method_key or "", {})
    requested = {
        "account_interval_seconds": account_interval_seconds,
        "destination_interval_seconds": destination_interval_seconds,
    }
    effective: dict[str, float] = {}
    for key, value in requested.items():
        floor = selected.get(key, base.get(key, 1.0))
        requested_interval = (
            positive_finite(value, field=key, source="request") if value is not None else floor
        )
        effective[key] = max(floor, requested_interval)
    return effective


def _metadata_with_execution_context(
    metadata_json: dict[str, Any] | None,
    runtime_context: RuntimeActionContext,
) -> dict[str, Any] | None:
    base = _redact_for_audit(metadata_json) if metadata_json else {}
    if runtime_context.context_ref is None:
        if runtime_context.output_policy_json:
            base["output_policy_json"] = runtime_context.output_policy_json
        return base or None
    base["execution_context"] = {
        key: value
        for key, value in {
            "context_ref": runtime_context.context_ref,
            "output_policy_json": runtime_context.output_policy_json,
            "request_budget_json": runtime_context.request_budget_json,
            "artifact_namespace": runtime_context.artifact_namespace,
        }.items()
        if value not in (None, {}, [])
    }
    return base


def _connector_failure_data(
    *,
    manifest: ExecutableActionManifest,
    row_id: int,
    connector_key: str | None,
    error: str,
    output_json: dict[str, Any] | None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "status": "failed",
        "action_ref": manifest.action_ref,
        "action_call_id": row_id,
        "provider_key": manifest.provider_key,
        "connector": connector_key,
        "error": error,
    }
    if isinstance(output_json, dict):
        provider_status_code = output_json.get("provider_status_code")
        provider_error = output_json.get("provider_error")
        if provider_status_code is not None:
            data["provider_status_code"] = provider_status_code
        if provider_error is not None:
            data["provider_error"] = provider_error
    return data


def _effective_output_policy(
    *,
    manifest: ExecutableActionManifest,
    runtime_context: RuntimeActionContext,
    default_external_file_output: bool,
) -> dict[str, Any]:
    if runtime_context.output_policy_json:
        return _normalise_output_policy(runtime_context.output_policy_json)
    manifest_policy = manifest.config_json.get("output_policy_json") or manifest.config_json.get(
        "default_output_policy_json"
    )
    if isinstance(manifest_policy, dict) and manifest_policy:
        return _normalise_output_policy(manifest_policy)
    if (
        default_external_file_output
        and manifest.provider_key is not None
        and manifest.connector_key is not None
    ):
        return _normalise_output_policy({"mode": "always_file"})
    return _normalise_output_policy({})


def _normalise_output_policy(policy: dict[str, Any]) -> dict[str, Any]:
    mode = str(policy.get("mode") or "inline") if isinstance(policy, dict) else "inline"
    if mode not in {"inline", "file_if_large", "always_file", "transient"}:
        raise ValidationError(
            "invalid output policy mode",
            data={
                "mode": mode,
                "accepted": ["always_file", "file_if_large", "inline", "transient"],
            },
        )
    max_inline_bytes = policy.get("max_inline_bytes") if isinstance(policy, dict) else None
    if max_inline_bytes is None:
        max_inline_bytes = 65_536 if mode == "transient" else 16000
    if (
        not isinstance(max_inline_bytes, int)
        or isinstance(max_inline_bytes, bool)
        or max_inline_bytes < 1
    ):
        raise ValidationError("output_policy_json.max_inline_bytes must be a positive integer")
    if mode == "transient" and max_inline_bytes > MAX_TRANSIENT_OUTPUT_BYTES:
        raise ValidationError(
            "output_policy_json.max_inline_bytes exceeds the transient response limit",
            data={"max_allowed_bytes": MAX_TRANSIENT_OUTPUT_BYTES},
        )
    content_type = policy.get("content_type") if isinstance(policy, dict) else None
    if not isinstance(content_type, str) or not content_type:
        content_type = "application/json"
    if content_type != "application/json":
        raise ValidationError(
            "output_policy_json.content_type must be application/json for file-backed outputs",
            data={"content_type": content_type},
        )
    semantic_name = policy.get("semantic_name") if isinstance(policy, dict) else None
    output_policy = {
        "mode": mode,
        "max_inline_bytes": max_inline_bytes,
        "content_type": content_type,
    }
    if isinstance(semantic_name, str) and semantic_name.strip():
        output_policy["semantic_name"] = semantic_name.strip()
    if isinstance(policy, dict) and any(
        isinstance(policy.get(key), str) and policy[key].strip()
        for key in ("file_path", "directory_path", "output_dir")
    ):
        raise ValidationError(
            "output_policy_json accepts only path for file-backed outputs; "
            "path must be a directory and StackOS generates the filename"
        )
    directory_path = policy.get("path") if isinstance(policy, dict) else None
    if mode == "transient" and (directory_path is not None or semantic_name is not None):
        raise ValidationError("transient output cannot declare a file path or semantic name")
    if isinstance(directory_path, str) and directory_path.strip():
        output_dir = Path(directory_path.strip()).expanduser()
        if not output_dir.is_absolute():
            raise ValidationError("output_policy_json.path must be an absolute directory path")
        output_policy["directory_path"] = str(output_dir)
    return output_policy


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def _action_output_envelope(
    *,
    project_id: int,
    manifest: ExecutableActionManifest,
    input_json: dict[str, Any],
    provider_context_json: dict[str, Any] | None,
    credential_ref: str | None,
    runtime_context: RuntimeActionContext,
    row: Any,
    output_json: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": ACTION_OUTPUT_SCHEMA_VERSION,
        "recorded_at": _utc_iso(),
        "project": {"project_id": project_id},
        "run": {
            "run_id": row.run_id,
            "run_plan_id": row.run_plan_id,
            "run_plan_step_id": row.run_plan_step_id,
        },
        "action_call": {
            "id": row.id,
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        },
        "action": {
            "action_ref": manifest.action_ref,
            "plugin_slug": manifest.plugin_slug,
            "action_key": manifest.action_key,
            "provider_key": manifest.provider_key,
            "connector_key": manifest.connector_key,
            "operation": manifest.operation,
            "risk_level": manifest.risk_level,
        },
        "request": {
            "input_json": _redact_for_audit(input_json),
            "provider_context_json": _redact_for_audit(provider_context_json)
            if provider_context_json is not None
            else None,
            "credential_ref": credential_ref,
            "context_ref": runtime_context.context_ref,
        },
        "response": {
            "output_json": _redact_for_audit(output_json),
            "metadata_json": _redact_for_audit(row.metadata_json),
            "cost_cents": row.cost_cents,
            "duration_ms": row.duration_ms,
            "dry_run": row.dry_run,
        },
        "summaries": {
            "request": _json_summary(input_json),
            "response": _json_summary(output_json),
        },
    }


def _json_summary(value: Any) -> dict[str, Any]:
    shape = _top_level_json_shape(value)
    summary: dict[str, Any] = {"top_level_shape": shape}
    if isinstance(value, dict):
        summary["keys"] = list(shape.get("keys") or [])
    elif isinstance(value, list):
        summary["length"] = len(value)
    return summary


def _transient_output_receipt(output_json: dict[str, Any], *, output_bytes: int) -> dict[str, Any]:
    """Keep useful audit counts without retaining provider-returned field values."""

    lists = [value for value in output_json.values() if isinstance(value, list)]
    return {
        "output_mode": "transient",
        "retained": False,
        "replayable": False,
        "result_available": False,
        "output_bytes": output_bytes,
        "top_level_type": "object",
        "field_count": len(output_json),
        "list_field_count": len(lists),
        "list_item_count": sum(len(value) for value in lists),
    }


def _semantic_output_name(
    *,
    policy: dict[str, Any],
    runtime_context: RuntimeActionContext,
    manifest: ExecutableActionManifest,
    action_call_id: int,
    created_at: str,
) -> str:
    stamp = re.sub(r"[^0-9TZ]+", "", created_at.replace("+00:00", "Z"))[:16]
    raw = (
        policy.get("semantic_name")
        or runtime_context.artifact_namespace
        or f"{manifest.provider_key or manifest.plugin_slug}-{manifest.action_key}"
    )
    base = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(raw)).strip("._-") or "action-output"
    return f"{base[:96]}-{stamp}-{action_call_id}"


def _resolve_output_path(
    *,
    policy: dict[str, Any],
    generated_assets_dir: Path,
    project_id: int,
    semantic_name: str,
) -> Path:
    requested_dir = policy.get("directory_path")
    if isinstance(requested_dir, str) and requested_dir.strip():
        output_dir = Path(requested_dir).expanduser().resolve()
        if output_dir.exists() and not output_dir.is_dir():
            raise ValidationError("output_policy_json.path must point to a directory")
        return output_dir / f"{semantic_name}.json"
    return (
        Path(generated_assets_dir).resolve()
        / "action-outputs"
        / f"project-{project_id}"
        / f"{semantic_name}.json"
    )


def _utc_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _top_level_json_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        items = [(str(key), item) for key, item in value.items()]
        items.sort(key=lambda item: item[0])
        keys = [key for key, _item in items]
        return {
            "type": "object",
            "keys": keys[:50],
            "key_count": len(keys),
            "fields": [{"name": key, "type": _json_type(item)} for key, item in items[:20]],
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "item_type": _json_type(value[0]) if value else None,
        }
    return {"type": _json_type(value)}


def _json_type(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return "number"
    if value is None:
        return "null"
    return "string"


def _dedupe_validation_issues(issues: list[Any]) -> list[Any]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Any] = []
    for issue in issues:
        key = (str(issue.path), str(issue.code), str(issue.message))
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out
