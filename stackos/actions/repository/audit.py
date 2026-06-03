"""Action call ledger, redaction, and idempotency replay."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlmodel import select

from stackos.actions.manifest import ExecutableActionManifest
from stackos.artifacts import redact_secret_text
from stackos.auth_providers import ResolvedCredential
from stackos.db.models import ActionCall, ActionCallStatus
from stackos.repositories.base import ConflictError, Page, cursor_paginate_desc

from .schema import ActionCallAuditOut, ActionCallOut, ActionExecutionOut
from .utils import _redact_for_audit, utcnow


class ActionAuditMixin:
    """Persist action-call audit rows with public-safe output shapes."""

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
    ) -> ActionCall:
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
            provider_context_json=_redact_for_audit(row.provider_context_json),
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
