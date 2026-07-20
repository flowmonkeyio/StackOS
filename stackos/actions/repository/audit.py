"""Action call ledger, redaction, and idempotency replay."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from stackos.actions.manifest import ExecutableActionManifest
from stackos.artifacts import redact_secret_text
from stackos.auth_providers import ResolvedCredential
from stackos.db.models import ActionCall, ActionCallStatus, IdempotencyKey
from stackos.generated_inventory import (
    generated_action_audit_key,
    generated_action_public_audit_metadata,
)
from stackos.repositories.base import ConflictError, NotFoundError, Page, cursor_paginate_desc

from .schema import ActionCallAuditOut, ActionCallOut, ActionExecutionOut
from .utils import _redact_for_audit, utcnow


class ActionAuditMixin:
    """Persist action-call audit rows with public-safe output shapes."""

    def get_call(self, *, project_id: int, action_call_id: int) -> ActionCallAuditOut:
        self._require_project(project_id)
        row = self._s.exec(
            select(ActionCall).where(
                ActionCall.project_id == project_id,
                ActionCall.id == action_call_id,
            )
        ).first()
        if row is None:
            raise NotFoundError("action call not found")
        return self._call_audit_out(row)

    def query_calls(
        self,
        *,
        project_id: int,
        run_id: int | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        plugin_slug: str | None = None,
        action_key: str | None = None,
        status: ActionCallStatus | None = None,
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
        if status is not None:
            filters.append(ActionCall.status == status)
        stmt = select(ActionCall).where(*filters)
        return cursor_paginate_desc(
            self._s,
            stmt,
            id_col=ActionCall.id,
            limit=limit,
            after_id=after_id,
            converter=self._call_audit_out,
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
        provider_context_json: dict[str, Any] | None,
        response_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
        status: ActionCallStatus,
        dry_run: bool,
        cost_cents: int,
        duration_ms: int | None,
        error: str | None = None,
        commit: bool = True,
        action_call_id: int | None = None,
    ) -> ActionCall:
        if action_call_id is not None:
            return self._finalize_running_call(
                project_id=project_id,
                action_call_id=action_call_id,
                credential=credential,
                response_json=response_json,
                metadata_json=metadata_json,
                status=status,
                cost_cents=cost_cents,
                duration_ms=duration_ms,
                error=error,
                commit=commit,
            )
        now = utcnow()
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
            provider_context_json=_redact_for_audit(provider_context_json)
            if provider_context_json is not None
            else None,
            response_json=_redact_for_audit(response_json) if response_json is not None else None,
            metadata_json=_redact_for_audit(metadata_json) if metadata_json is not None else None,
            cost_cents=cost_cents,
            duration_ms=duration_ms,
            error=redact_secret_text(error) if error is not None else None,
            created_at=now,
            completed_at=None if status == ActionCallStatus.RUNNING else now,
        )
        self._s.add(row)
        if not commit:
            self._s.flush()
            return row
        self._s.commit()
        self._s.refresh(row)
        return row

    def _reserve_background_call(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        credential_ref: str | None,
        run_id: int | None,
        run_plan_id: int | None,
        run_plan_step_id: int | None,
        idempotency_key: str | None,
        request_json: dict[str, Any],
        provider_context_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
        estimated_cost_cents: int,
    ) -> tuple[ActionCall, bool]:
        fingerprint = _background_request_fingerprint(
            action_ref=manifest.action_ref,
            request_json=request_json,
            provider_context_json=provider_context_json,
            credential_ref=credential_ref,
        )
        internal_key = (
            _background_idempotency_key(manifest.action_ref, idempotency_key)
            if idempotency_key is not None
            else None
        )
        try:
            row = self._record_call(
                project_id=project_id,
                manifest=manifest,
                credential=None,
                credential_ref=credential_ref,
                run_id=run_id,
                run_plan_id=run_plan_id,
                run_plan_step_id=run_plan_step_id,
                idempotency_key=idempotency_key,
                request_json=request_json,
                provider_context_json=provider_context_json,
                response_json=None,
                metadata_json=metadata_json,
                status=ActionCallStatus.RUNNING,
                dry_run=False,
                cost_cents=estimated_cost_cents,
                duration_ms=None,
                commit=False,
            )
            assert row.id is not None
            if internal_key is not None:
                self._s.add(
                    IdempotencyKey(
                        project_id=project_id,
                        tool_name="action.background",
                        idempotency_key=internal_key,
                        run_id=run_id,
                        response_json={
                            "action_call_id": row.id,
                            "request_fingerprint": fingerprint,
                        },
                    )
                )
            self._s.commit()
            self._s.refresh(row)
            return row, False
        except IntegrityError:
            self._s.rollback()
            if internal_key is None:
                raise
            reservation = self._s.exec(
                select(IdempotencyKey).where(
                    IdempotencyKey.project_id == project_id,
                    IdempotencyKey.tool_name == "action.background",
                    IdempotencyKey.idempotency_key == internal_key,
                )
            ).first()
            response = reservation.response_json if reservation is not None else None
            if response is None or response.get("request_fingerprint") != fingerprint:
                raise ConflictError(
                    "idempotency key replayed with different action request",
                    data={
                        "project_id": project_id,
                        "action_ref": manifest.action_ref,
                        "idempotency_key": idempotency_key,
                    },
                ) from None
            action_call_id = response.get("action_call_id")
            row = self._s.get(ActionCall, action_call_id)
            if row is None or row.project_id != project_id:
                raise ConflictError(
                    "background action reservation is missing its action call",
                    data={"project_id": project_id, "action_ref": manifest.action_ref},
                ) from None
            return row, True

    def _finalize_running_call(
        self,
        *,
        project_id: int,
        action_call_id: int,
        credential: ResolvedCredential | None,
        response_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
        status: ActionCallStatus,
        cost_cents: int,
        duration_ms: int | None,
        error: str | None,
        commit: bool,
    ) -> ActionCall:
        if status not in {ActionCallStatus.SUCCESS, ActionCallStatus.FAILED}:
            raise ValueError("a running action call can only finalize to success or failed")
        result = self._s.execute(
            update(ActionCall)
            .where(
                ActionCall.project_id == project_id,  # type: ignore[arg-type]
                ActionCall.id == action_call_id,  # type: ignore[arg-type]
                ActionCall.status == ActionCallStatus.RUNNING,  # type: ignore[arg-type]
            )
            .values(
                credential_id=credential.credential_id if credential is not None else None,
                response_json=(
                    _redact_for_audit(response_json) if response_json is not None else None
                ),
                metadata_json=(
                    _redact_for_audit(metadata_json) if metadata_json is not None else None
                ),
                status=status,
                cost_cents=cost_cents,
                duration_ms=duration_ms,
                error=redact_secret_text(error) if error is not None else None,
                completed_at=utcnow(),
            )
        )
        if result.rowcount != 1:
            self._s.rollback()
            raise ConflictError(
                "background action call is no longer running",
                data={"project_id": project_id, "action_call_id": action_call_id},
            )
        if commit:
            self._s.commit()
        row = self._s.get(ActionCall, action_call_id)
        if row is None:  # pragma: no cover - guarded update proves the row exists
            raise RuntimeError("finalized action call is missing")
        if commit:
            self._s.refresh(row)
        return row

    def reconcile_running_calls(self) -> int:
        """Mark background calls orphaned by daemon restart as outcome-unknown."""
        result = self._s.execute(
            update(ActionCall)
            .where(ActionCall.status == ActionCallStatus.RUNNING)  # type: ignore[arg-type]
            .values(
                status=ActionCallStatus.FAILED,
                response_json={"outcome_unknown": True, "retry_safe": False},
                error="daemon-restart-orphan",
                completed_at=utcnow(),
            )
        )
        count = int(result.rowcount or 0)
        self._s.commit()
        return count

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
            provider_context_json=_redact_for_audit(row.provider_context_json),
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
        metadata_json = generated_action_public_audit_metadata(row.metadata_json)
        return ActionCallAuditOut(
            id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            run_plan_id=row.run_plan_id,
            run_plan_step_id=row.run_plan_step_id,
            action_key=generated_action_audit_key(row.action_key) or row.action_key,
            plugin_slug=row.plugin_slug,
            provider_key=row.provider_key,
            connector_key=row.connector_key,
            operation=row.operation,
            status=row.status,
            dry_run=row.dry_run,
            credential_ref=row.credential_ref,
            request_json=_redact_for_audit(row.request_json),
            provider_context_json=_redact_for_audit(row.provider_context_json),
            response_json=_redact_for_audit(row.response_json),
            metadata_json=_redact_for_audit(metadata_json),
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
        provider_context_json: dict[str, Any] | None,
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
            or row.provider_context_json != _redact_for_audit(provider_context_json)
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


def _background_idempotency_key(action_ref: str, idempotency_key: str) -> str:
    return hashlib.sha256(f"{action_ref}\0{idempotency_key}".encode()).hexdigest()


def _background_request_fingerprint(
    *,
    action_ref: str,
    request_json: dict[str, Any],
    provider_context_json: dict[str, Any] | None,
    credential_ref: str | None,
) -> str:
    canonical = json.dumps(
        {
            "action_ref": action_ref,
            "request_json": request_json,
            "provider_context_json": provider_context_json,
            "credential_ref": credential_ref,
            "dry_run": False,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
