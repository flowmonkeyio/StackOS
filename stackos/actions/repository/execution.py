"""Action execution pipeline and connector dispatch."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stackos.action_availability import build_action_availability, build_action_exposure
from stackos.action_output_contract import ACTION_OUTPUT_SCHEMA_REF, action_output_schema_hint
from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.manifest import ExecutableActionManifest
from stackos.artifacts import redact_secret_text
from stackos.auth_providers import AuthRepository, ResolvedCredential
from stackos.config import Settings
from stackos.db.models import ActionCallStatus
from stackos.repositories.base import ConflictError, Envelope, ValidationError
from stackos.repositories.projects import IntegrationBudgetRepository

from .schema import ActionExecutionOut
from .utils import _redact_for_audit
from .validation import RuntimeActionContext

ACTION_OUTPUT_SCHEMA_VERSION = ACTION_OUTPUT_SCHEMA_REF


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
        run_id: int | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        idempotency_key: str | None = None,
        dry_run: bool = False,
        metadata_json: dict[str, Any] | None = None,
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
        )
        validation_issues = _dedupe_validation_issues([*runtime_context.issues, *validation.issues])
        if validation_issues:
            raise ValidationError(
                "action payload is invalid",
                data={
                    "action_ref": manifest.action_ref,
                    "status": availability.status,
                    "reasons": availability.reasons,
                    "exposure": exposure.model_dump(mode="json"),
                    "issues": [issue.model_dump(mode="json") for issue in validation_issues],
                },
            )
        effective_output_policy = _effective_output_policy(
            manifest=manifest,
            runtime_context=runtime_context,
            default_external_file_output=default_external_file_output,
        )
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
        if idempotency_key is not None:
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
        dry_request = self._connector_request(
            project_id=project_id,
            manifest=manifest,
            input_json=payload,
            provider_context_json=provider_context,
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
            provider_context_json=provider_context,
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
                provider_context_json=provider_context_for_audit,
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
            **(_redact_for_audit(metadata_json) if metadata_json else {}),
            **(result_metadata or {}),
        } or None
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
                provider_context_json=provider_context_for_audit,
                response_json={
                    "output_persistence_failed": True,
                    "response_summary": _json_summary(output_json),
                },
                metadata_json=success_metadata,
                status=ActionCallStatus.FAILED,
                dry_run=False,
                cost_cents=actual_cost_cents,
                duration_ms=duration_ms,
                error=safe_error,
            )
            raise ConflictError(
                "action output persistence failed",
                data={
                    "action_ref": manifest.action_ref,
                    "action_call_id": row.id,
                    "connector": manifest.connector_key,
                    "side_effect": "provider_executed_output_not_persisted",
                    "error": safe_error,
                },
            ) from exc
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
        provider_context_json: dict[str, Any],
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
            provider_context_json=provider_context_json,
            credential=credential,
            asset_dir=self._asset_dir,
            session=self._s,
            dry_run=dry_run,
        )

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
    if mode not in {"inline", "file_if_large", "always_file"}:
        raise ValidationError(
            "invalid output policy mode",
            data={"mode": mode, "accepted": ["always_file", "file_if_large", "inline"]},
        )
    max_inline_bytes = policy.get("max_inline_bytes") if isinstance(policy, dict) else None
    if max_inline_bytes is None:
        max_inline_bytes = 16000
    if (
        not isinstance(max_inline_bytes, int)
        or isinstance(max_inline_bytes, bool)
        or max_inline_bytes < 1
    ):
        raise ValidationError("output_policy_json.max_inline_bytes must be a positive integer")
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
