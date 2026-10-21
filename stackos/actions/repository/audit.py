"""Action call ledger, redaction, and idempotency replay."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from stackos import controlled_process_receipt as _receipt
from stackos.actions.manifest import ExecutableActionManifest
from stackos.artifacts import redact_secret_text
from stackos.auth_providers import ResolvedCredential
from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    DurableActionAttempt,
    DurableActionItem,
    DurableActionJob,
    IdempotencyKey,
    WorkItemOwnership,
)
from stackos.generated_inventory import (
    generated_action_audit_key,
    generated_action_public_audit_metadata,
)
from stackos.repositories.base import (
    ConflictError,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate_desc,
)

from .audit_queries import audit_predicates
from .schema import (
    ActionCallAuditOut,
    ActionCallOut,
    ActionExecutionOut,
)
from .utils import _redact_for_audit, utcnow


@dataclass(frozen=True)
class _ControlledEffectSnapshot:
    status: Literal["known_no_effect", "known_settled", "unknown"]
    effect_identity: str
    rows: tuple[dict[str, Any], ...]
    descriptor_digest: str
    issues: tuple[str, ...]


class ActionAuditMixin:
    """Persist action-call audit rows with public-safe output shapes."""

    def _controlled_effect_snapshot(
        self,
        *,
        project_id: int,
        bindings: tuple[_receipt._Binding, ...],
        effect_identity: str,
        command_manifests: tuple[dict[str, Any], ...],
        _resolved_runtimes: dict[int, dict[str, Any]] | None = None,
        _owner_scope: tuple[str, str | None] | None = None,
    ) -> _ControlledEffectSnapshot:
        """Canonical read-only raw ledger scope. A reference/worker flag is not settlement."""
        from stackos.actions.controlled_command import (
            _canonical_digest,
            _current_original_runtime,
        )

        if not _receipt._integer(project_id) or not _receipt._token(effect_identity, 1000):
            raise ValidationError("invalid controlled effect scope")
        issues: set[str] = set()
        if len(bindings) != len(command_manifests) or any(
            not _receipt._valid_binding(binding) or binding.project_id != project_id
            for binding in bindings
        ):
            raise ValidationError("invalid controlled effect bindings")
        # Only the finite original selection is observed from stored data. The
        # entire runtime is reconstructed from current sources and Settings;
        # no arbitrary manifest can declare itself effect-free.
        if _resolved_runtimes is None:
            connection = self._s.connection()
            if (
                connection.dialect.name == "sqlite"
                and getattr(connection.connection.driver_connection, "in_transaction", None)
                is not False
            ):
                raise ConflictError("effect source resolution must precede writer interval")
            actuals = {}
            for original_binding, manifest in zip(bindings, command_manifests, strict=True):
                try:
                    actuals[original_binding.attempt_run_id] = _current_original_runtime(
                        self._s, binding=original_binding, manifest=manifest
                    )
                except (ConflictError, ValueError):
                    issues.add("effect_command_unknown")
        else:
            actuals = deepcopy(_resolved_runtimes)
        if any(
            manifest != actuals.get(binding.attempt_run_id)
            or _canonical_digest(manifest) != binding.manifest_digest
            for binding, manifest in zip(bindings, command_manifests, strict=True)
        ):
            issues.add("effect_command_unknown")
        keys = {
            "controlled:"
            + _canonical_digest(
                dict(
                    project_id=project_id,
                    controller_run_id=b.controller_run_id,
                    canonical_item_id=b.canonical_item_id,
                    epoch=b.epoch,
                    intent_ref=b.intent_ref,
                    dry_run=False,
                )
            ): b
            for b in bindings
        }
        scopes = {(b.controller_run_id, b.run_plan_id, b.run_plan_step_id) for b in bindings}
        rows: list[dict[str, Any]] = []
        published = False

        def publication_in_scope(row: ActionCall) -> bool:
            if (row.plugin_slug, row.action_key) != ("engineering", "publish-candidate.v1"):
                return False
            try:
                from stackos.actions.controlled_command import _controlled_host_settings
                from stackos.actions.controlled_publication import _verify_admission
                from stackos.crypto.seed import load_seed

                settings = _controlled_host_settings(self._s)
                if settings is None:
                    return True
                request = _verify_admission(row, load_seed(settings.seed_path))
                return _owner_scope is None or request.canonical_item_id == _owner_scope[0]
            except Exception:
                # Untrusted membership cannot make an unresolved row disappear.
                return True

        with self._s.no_autoflush:
            registered_calls: set[int] = set()
            registered_jobs: set[int] = set()
            if _owner_scope is not None:
                owners = self._s.exec(
                    select(WorkItemOwnership)
                    .where(
                        WorkItemOwnership.project_id == project_id,
                        WorkItemOwnership.canonical_item_id == _owner_scope[0],
                    )
                    .execution_options(populate_existing=True)
                ).all()
                if len(owners) != 1:
                    issues.add("effect_owner_registration_unknown")
                else:
                    continuity = owners[0].continuity_json
                    registrations = (
                        continuity.get("durable_action_registrations", {})
                        if type(continuity) is dict
                        else None
                    )
                    if registrations:
                        rows.append(
                            {"kind": "owner_durable_registrations", "rows": deepcopy(registrations)}
                        )
                    if type(registrations) is not dict:
                        issues.add("effect_owner_registration_unknown")
                    else:
                        for key, registration in registrations.items():
                            if type(registration) is not dict:
                                issues.add("effect_owner_registration_unknown")
                                continue
                            job_id = registration.get("job_id")
                            call_id = registration.get("action_call_id")
                            if (
                                type(job_id) is not int
                                or job_id < 1
                                or key != str(job_id)
                                or type(call_id) is not int
                                or call_id < 1
                            ):
                                issues.add("effect_owner_registration_unknown")
                                continue
                            registered_jobs.add(job_id)
                            registered_calls.add(call_id)
            # Include contradictory key bindings as well as every row in the
            # bound original controller/step. No status-filtered absence proof.
            calls = self._s.exec(
                select(ActionCall)
                .where(
                    or_(
                        col(ActionCall.project_id) == project_id,
                        col(ActionCall.idempotency_key).in_(tuple(keys)),
                        col(ActionCall.run_id).in_(tuple(b.controller_run_id for b in bindings)),
                        col(ActionCall.id).in_(tuple(registered_calls)),
                    )
                )
                .order_by(col(ActionCall.id))
                .execution_options(populate_existing=True)
            ).all()
            selected = [
                row
                for row in calls
                if row.idempotency_key in keys
                or row.id in registered_calls
                or (row.run_id, row.run_plan_id, row.run_plan_step_id) in scopes
                or (row.project_id == project_id and publication_in_scope(row))
            ]
            # With no launches there are no original controllers; any project
            # controlled/durable row is unresolved, rather than inferred absent.
            if not bindings:
                selected = [
                    row
                    for row in calls
                    if (
                        (row.plugin_slug, row.action_key) != ("engineering", "publish-candidate.v1")
                        or publication_in_scope(row)
                    )
                ]
            for row in selected:
                rows.append(
                    {
                        "kind": "call",
                        **{
                            name: (
                                getattr(row, name).isoformat()
                                if isinstance(getattr(row, name), datetime)
                                else deepcopy(getattr(row, name))
                            )
                            for name in (
                                "id",
                                "project_id",
                                "run_id",
                                "run_plan_id",
                                "run_plan_step_id",
                                "plugin_slug",
                                "action_key",
                                "provider_key",
                                "credential_id",
                                "idempotency_key",
                                "status",
                                "request_json",
                                "response_json",
                                "metadata_json",
                                "error",
                                "dry_run",
                                "completed_at",
                            )
                        },
                    }
                )
                if row.plugin_slug == "engineering" and row.action_key == "publish-candidate.v1":
                    try:
                        from stackos.actions.controlled_command import _controlled_host_settings
                        from stackos.actions.controlled_publication import _verify_terminal
                        from stackos.crypto.seed import load_seed

                        settings = _controlled_host_settings(self._s)
                        if settings is None or row.project_id != project_id:
                            raise ConflictError("publication effect storage unknown")
                        publication_result = _verify_terminal(row, load_seed(settings.seed_path))
                        published = published or publication_result["outcome"] == "published"
                    except Exception:
                        issues.add("publication_effect_unknown")
                    continue
                binding = keys.get(row.idempotency_key or "")
                if binding is None and row.status == ActionCallStatus.DRY_RUN:
                    binding = next(
                        (
                            b
                            for b in bindings
                            if (row.run_id, row.run_plan_id, row.run_plan_step_id)
                            == (b.controller_run_id, b.run_plan_id, b.run_plan_step_id)
                        ),
                        None,
                    )
                admission = (row.metadata_json or {}).get("controlled_admission", {})
                if binding is None or not (
                    row.project_id == project_id
                    and row.plugin_slug == "engineering"
                    and row.action_key == "controlled-proof.v1"
                    and row.provider_key is None
                    and row.credential_id is None
                    and row.status
                    in {ActionCallStatus.SUCCESS, ActionCallStatus.FAILED, ActionCallStatus.DRY_RUN}
                    and type(admission) is dict
                    and admission.get("intent_ref") == binding.intent_ref
                    and admission.get("epoch") == binding.epoch
                    and admission.get("scope_digest") == binding.scope_digest
                    and admission.get("canonical_item_id") == binding.canonical_item_id
                    and admission.get("owner_ref") == binding.owner_ref
                    and admission.get("project_id") == binding.project_id
                    and admission.get("controller_run_id") == binding.controller_run_id
                    and admission.get("run_plan_id") == binding.run_plan_id
                    and admission.get("run_plan_step_id") == binding.run_plan_step_id
                    and (row.run_id, row.run_plan_id, row.run_plan_step_id)
                    == (binding.controller_run_id, binding.run_plan_id, binding.run_plan_step_id)
                ):
                    issues.add("effect_call_unknown")
                    continue
                try:
                    from stackos.actions.controlled_command import _ControlledCommandInput

                    request = _ControlledCommandInput.model_validate(row.request_json)
                    actual = actuals.get(binding.attempt_run_id)
                    if actual is None:
                        issues.add("effect_call_unknown")
                        continue
                    if (
                        request.canonical_item_id != binding.canonical_item_id
                        or request.owner_ref != binding.owner_ref
                        or request.expected_epoch != binding.epoch
                        or request.scope_digest != binding.scope_digest
                        or request.intent_ref != binding.intent_ref
                        or request.command_digest != _canonical_digest(actual["command"])
                        or admission.get("request_digest")
                        != _canonical_digest(request.model_dump())
                        or admission.get("command_digest") != request.command_digest
                        or admission.get("revision") != request.expected_revision
                    ):
                        issues.add("effect_call_unknown")
                    result = self._public_action_result(row)
                    native = (row.metadata_json or {}).get("controlled_native")
                    if native is not None:
                        valid = self._controlled_native_effect(row, binding, actual)
                    else:
                        valid = bool(
                            result
                            and result.get("outcome_unknown") is False
                            and result.get("host_dispatched") is False
                            and result.get("provider_executed") is False
                            and row.status in {ActionCallStatus.FAILED, ActionCallStatus.DRY_RUN}
                        )
                    if not valid:
                        issues.add("effect_call_unknown")
                except (ConflictError, ValueError):
                    issues.add("effect_call_unknown")
            call_ids = {row.id for row in selected}
            if not registered_calls.issubset(call_ids):
                issues.add("effect_owner_registration_unknown")
            jobs = self._s.exec(
                select(DurableActionJob)
                .where(
                    or_(
                        col(DurableActionJob.project_id) == project_id,
                        col(DurableActionJob.action_call_id).in_(
                            tuple(value for value in call_ids if value is not None)
                        ),
                        col(DurableActionJob.idempotency_key).in_(tuple(keys)),
                        col(DurableActionJob.id).in_(tuple(registered_jobs)),
                    )
                )
                .order_by(col(DurableActionJob.id))
                .execution_options(populate_existing=True)
            ).all()
            if not registered_jobs.issubset({job.id for job in jobs}):
                issues.add("effect_owner_registration_unknown")
            for job in jobs:
                if (
                    not bindings
                    or job.id in registered_jobs
                    or job.action_call_id in call_ids
                    or job.idempotency_key in keys
                    or (job.run_id, job.run_plan_id, job.run_plan_step_id) in scopes
                ):
                    # Fixed foreground commands have no durable producer. Any such
                    # job needs its real delivery owner; do not settle it here.
                    issues.add("effect_durable_unknown")
                    rows.append({"kind": "job", "row": job.model_dump(mode="json")})
                    for item in self._s.exec(
                        select(DurableActionItem)
                        .where(DurableActionItem.job_id == job.id)
                        .order_by(col(DurableActionItem.id))
                        .execution_options(populate_existing=True)
                    ).all():
                        rows.append({"kind": "item", "row": item.model_dump(mode="json")})
                        for attempt in self._s.exec(
                            select(DurableActionAttempt)
                            .where(DurableActionAttempt.item_id == item.id)
                            .order_by(col(DurableActionAttempt.id))
                            .execution_options(populate_existing=True)
                        ).all():
                            rows.append(
                                {"kind": "effect_attempt", "row": attempt.model_dump(mode="json")}
                            )
        descriptor = dict(
            project_id=project_id,
            effect_identity=effect_identity,
            bindings=[_receipt._table(b) for b in bindings],
            manifests=command_manifests,
            rows=rows,
        )
        return _ControlledEffectSnapshot(
            "unknown" if issues else "known_settled" if published else "known_no_effect",
            effect_identity,
            tuple(rows),
            _canonical_digest(descriptor),
            tuple(sorted(issues)),
        )

    def _controlled_native_effect(self, row: ActionCall, binding, runtime: dict[str, Any]) -> bool:
        from dataclasses import asdict

        from stackos.actions.controlled_command import (
            _canonical_digest,
            _ControlledNativeAudit,
            _ControlledNativeResult,
        )
        from stackos.db.models import Run
        from stackos.repositories.runs import _parse_terminal_facts, _parse_terminal_slot

        try:
            native = _ControlledNativeAudit.model_validate(
                (row.metadata_json or {}).get("controlled_native")
            )
            result = _ControlledNativeResult.model_validate(row.response_json)
            original = self._s.get(Run, binding.attempt_run_id, populate_existing=True)
            if original is None:
                return False
            metadata = original.metadata_json or {}
            slot = _parse_terminal_slot(metadata.get("native_terminal_slot"))
            facts = _parse_terminal_facts(metadata.get("native_terminal_facts"), binding)
            recovery = metadata.get("native_recovery_private", {})
            return bool(
                native.binding == asdict(binding)
                and native.action_call_id == row.id
                and native.runtime_digest == _canonical_digest(runtime)
                and native.command_digest == _canonical_digest(runtime["command"])
                and native.result_digest == _canonical_digest(row.response_json)
                and not result.outcome_unknown
                and result.cleanup_known
                and slot["phase"] == "COMMITTED"
                and slot["binding"] == asdict(binding)
                and slot["revision"] == native.terminal_slot_revision
                and slot["receipt_digest"] == native.terminal_receipt_digest
                and facts["revision"] == 1
                and facts["entries"][0]["receipt_digest"] == native.terminal_receipt_digest
                and recovery.get("binding") == asdict(binding)
                and recovery.get("manifest") == runtime
            )
        except (ValueError, TypeError, KeyError, ConflictError):
            return False

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
        action_call_id: int | None = None,
        provider_key: str | None = None,
        created_from: datetime | None = None,
        created_before: datetime | None = None,
        dry_run: bool | None = None,
        sort: Literal["id", "created_at"] = "id",
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ActionCallAuditOut]:
        self._require_project(project_id)
        return self._query_audit_calls(
            project_id=project_id,
            run_id=run_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
            plugin_slug=plugin_slug,
            action_key=action_key,
            status=status,
            action_call_id=action_call_id,
            provider_key=provider_key,
            created_from=created_from,
            created_before=created_before,
            dry_run=dry_run,
            sort=sort,
            limit=limit,
            after_id=after_id,
        )

    def _query_audit_calls(
        self,
        *,
        project_id: int | None,
        limit: int | None = None,
        after_id: int | None = None,
        sort: Literal["id", "created_at"] = "id",
        **filters: Any,
    ) -> Page[ActionCallAuditOut]:
        if sort not in {"id", "created_at"}:
            raise ValidationError("sort must be id or created_at")
        stmt = select(ActionCall).where(*audit_predicates(project_id=project_id, **filters))
        return cursor_paginate_desc(
            self._s,
            stmt,
            id_col=ActionCall.id,
            limit=limit,
            after_id=after_id,
            converter=self._call_audit_out,
            order_col=col(ActionCall.created_at) if sort == "created_at" else None,
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
        durable_schedule_json: dict[str, Any] | None = None,
        commit_guard: Callable[[], None] | None = None,
        replay_guard: Callable[[ActionCall], None] | None = None,
        commit: bool = True,
    ) -> tuple[ActionCall, bool]:
        fingerprint = _background_request_fingerprint(
            action_ref=manifest.action_ref,
            request_json=request_json,
            provider_context_json=provider_context_json,
            credential_ref=credential_ref,
            durable_schedule_json=durable_schedule_json,
        )
        scope = _background_idempotency_scope(
            action_ref=manifest.action_ref,
            metadata_json=metadata_json,
        )
        internal_key = (
            _background_idempotency_key(scope, idempotency_key)
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
            self._s.flush()
            if commit:
                if commit_guard is not None:
                    commit_guard()
                self._s.commit()
                self._s.refresh(row)
            return row, False
        except IntegrityError:
            self._s.rollback()
            replay = self._background_replay(
                project_id=project_id,
                manifest=manifest,
                credential_ref=credential_ref,
                idempotency_key=idempotency_key,
                request_json=request_json,
                provider_context_json=provider_context_json,
                metadata_json=metadata_json,
                durable_schedule_json=durable_schedule_json,
            )
            if replay is None:
                raise
            if replay_guard is not None:
                replay_guard(replay)
            elif commit_guard is not None:
                commit_guard()
            return replay, True
        except BaseException:
            self._s.rollback()
            raise

    def _background_replay(
        self,
        *,
        project_id: int,
        manifest: ExecutableActionManifest,
        credential_ref: str | None,
        idempotency_key: str | None,
        request_json: dict[str, Any],
        provider_context_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
        durable_schedule_json: dict[str, Any] | None,
    ) -> ActionCall | None:
        """Return one exact background reservation without creating an audit row."""

        if idempotency_key is None:
            return None
        scope = _background_idempotency_scope(
            action_ref=manifest.action_ref,
            metadata_json=metadata_json,
        )
        reservation = self._s.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.project_id == project_id,
                IdempotencyKey.tool_name == "action.background",
                IdempotencyKey.idempotency_key
                == _background_idempotency_key(scope, idempotency_key),
            )
        ).first()
        if reservation is None:
            return None
        response = reservation.response_json
        fingerprint = _background_request_fingerprint(
            action_ref=manifest.action_ref,
            request_json=request_json,
            provider_context_json=provider_context_json,
            credential_ref=credential_ref,
            durable_schedule_json=durable_schedule_json,
        )
        if response is None or response.get("request_fingerprint") != fingerprint:
            raise ConflictError(
                "idempotency key replayed with different action request",
                data={
                    "project_id": project_id,
                    "action_ref": manifest.action_ref,
                    "idempotency_key": idempotency_key,
                },
            )
        action_call_id = response.get("action_call_id")
        row = self._s.get(ActionCall, action_call_id)
        if row is None or row.project_id != project_id:
            raise ConflictError(
                "background action reservation is missing its action call",
                data={"project_id": project_id, "action_ref": manifest.action_ref},
            )
        return row

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
        """Recover durable calls, then hold other orphaned background calls unknown.

        A durable item's persisted lease is the only proof that an effect may
        already have started.  Its own recovery marks that item unknown-hold;
        the generic background reconciler must not finalize the parent first.
        """
        self.reconcile_durable_action_jobs()
        durable_action_call_ids = select(DurableActionJob.action_call_id)
        result = self._s.execute(
            update(ActionCall)
            .where(
                ActionCall.status == ActionCallStatus.RUNNING,  # type: ignore[arg-type]
                ActionCall.id.not_in(durable_action_call_ids),  # type: ignore[union-attr]
            )
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
            response_json=_redact_for_audit(self._public_action_result(row)),
            metadata_json=_redact_for_audit(
                self._public_action_metadata(
                    plugin_slug=row.plugin_slug,
                    action_key=row.action_key,
                    metadata_json=row.metadata_json,
                )
            ),
            cost_cents=row.cost_cents,
            duration_ms=row.duration_ms,
            error=redact_secret_text(row.error) if row.error is not None else None,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    def _call_audit_out(self, row: ActionCall) -> ActionCallAuditOut:
        assert row.id is not None
        metadata_json = generated_action_public_audit_metadata(row.metadata_json)
        metadata_json = self._public_action_metadata(
            plugin_slug=row.plugin_slug,
            action_key=row.action_key,
            metadata_json=metadata_json,
        )
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
            response_json=_redact_for_audit(self._public_action_result(row)),
            metadata_json=_redact_for_audit(metadata_json),
            cost_cents=row.cost_cents,
            duration_ms=row.duration_ms,
            error=redact_secret_text(row.error) if row.error is not None else None,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    def _public_action_metadata(
        self,
        *,
        plugin_slug: str,
        action_key: str,
        metadata_json: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if (plugin_slug, action_key) == ("engineering", "publish-candidate.v1"):
            return {}
        if (plugin_slug, action_key) != ("engineering", "controlled-proof.v1"):
            return metadata_json
        from stackos.actions.controlled_command import _ControlledCommandInput

        source = (metadata_json or {}).get("controlled_admission")
        if not isinstance(source, dict):
            raise ConflictError("controlled_history_unknown")
        keys = (
            "schema_version",
            "request_digest",
            "command_digest",
            "scope_digest",
            "intent_ref",
            "epoch",
        )
        public = {key: source.get(key) for key in keys}
        if (
            public["schema_version"] != "stackos.controlled-command-admission.v1"
            or type(public["request_digest"]) is not str
            or len(public["request_digest"]) != 64
            or any(c not in "0123456789abcdef" for c in public["request_digest"])
        ):
            raise ConflictError("controlled_history_unknown")
        try:
            _ControlledCommandInput.model_validate(
                {
                    "canonical_item_id": "task:1",
                    "owner_ref": "projection",
                    "expected_epoch": public["epoch"],
                    "expected_revision": 1,
                    "scope_digest": public["scope_digest"],
                    "intent_ref": public["intent_ref"],
                    "command_key": "noop-v1",
                    "command_digest": public["command_digest"],
                }
            )
        except ValueError as exc:
            raise ConflictError("controlled_history_unknown") from exc
        return {"controlled_admission": public}

    def _public_action_result(self, row: ActionCall) -> dict[str, Any] | None:
        if (row.plugin_slug, row.action_key) == ("engineering", "publish-candidate.v1"):
            if row.status == ActionCallStatus.RUNNING and row.response_json is None:
                return None
            from stackos.actions.controlled_command import _controlled_host_settings
            from stackos.actions.controlled_publication import _verify_terminal
            from stackos.crypto.seed import load_seed

            settings = _controlled_host_settings(self._s)
            if settings is None:
                raise ConflictError("publication audit storage unavailable")
            if row.error == "daemon-restart-orphan":
                return {"outcome_unknown": True, "retry_safe": False}
            return _verify_terminal(row, load_seed(settings.seed_path))
        if (row.plugin_slug, row.action_key) != ("engineering", "controlled-proof.v1"):
            return row.response_json
        if row.status == ActionCallStatus.RUNNING and row.response_json is None:
            # A reserved call has no terminal result yet. Readers still validate
            # its metadata and expose the existing RUNNING/poll contract only.
            return None
        output = row.response_json
        if (
            type(output) is dict
            and output.get("schema_version") == "stackos.controlled-command-result.v2"
        ):
            from stackos.actions.controlled_command import _ControlledNativeResult

            try:
                native = _ControlledNativeResult.model_validate(output)
            except ValueError as exc:
                raise ConflictError(
                    "controlled_history_unknown", data={"action_call_id": row.id}
                ) from exc
            public = self._public_action_metadata(
                plugin_slug=row.plugin_slug,
                action_key=row.action_key,
                metadata_json=row.metadata_json,
            )
            if (
                public is None
                or native.command_digest != public["controlled_admission"]["command_digest"]
                or native.intent_ref != public["controlled_admission"]["intent_ref"]
                or row.status
                != (
                    ActionCallStatus.SUCCESS
                    if native.status == "succeeded"
                    else ActionCallStatus.FAILED
                )
            ):
                raise ConflictError("controlled_history_unknown", data={"action_call_id": row.id})
            return native.model_dump()
        if (
            row.status == ActionCallStatus.FAILED
            and row.error == "daemon-restart-orphan"
            and type(output) is dict
            and output.keys() == {"outcome_unknown", "retry_safe"}
            and output["outcome_unknown"] is True
            and output["retry_safe"] is False
        ):
            # Canonical restart evidence remains inspectable, but supplies no
            # admission/result assertion. Controlled replay still holds unknown.
            return {"outcome_unknown": True, "retry_safe": False}
        required = {
            "schema_version",
            "status",
            "code",
            "admission_verified",
            "command_digest",
            "intent_ref",
            "host_dispatched",
            "provider_executed",
            "outcome_unknown",
            "retry_safe",
            "certified",
        }
        if (
            type(output) is not dict
            or output.keys() != required
            or output.get("schema_version") != "stackos.controlled-command-result.v1"
            or output.get("status") != "failed"
            or output.get("code")
            not in {
                "controlled_host_unavailable",
                "authority_changed",
                "interrupted_before_dispatch",
            }
            or any(
                type(output.get(k)) is not bool
                for k in (
                    "admission_verified",
                    "host_dispatched",
                    "provider_executed",
                    "outcome_unknown",
                    "retry_safe",
                    "certified",
                )
            )
            or any(
                output[k]
                for k in ("host_dispatched", "provider_executed", "retry_safe", "certified")
            )
        ):
            raise ConflictError("controlled_history_unknown", data={"action_call_id": row.id})
        public = self._public_action_metadata(
            plugin_slug=row.plugin_slug,
            action_key=row.action_key,
            metadata_json=row.metadata_json,
        )
        assert public is not None
        binding = public["controlled_admission"]
        if (
            output["command_digest"] != binding["command_digest"]
            or output["intent_ref"] != binding["intent_ref"]
        ):
            raise ConflictError("controlled_history_unknown", data={"action_call_id": row.id})
        return dict(output)

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
            output_json=self._public_action_result(row) or {},
            metadata_json=self._public_action_metadata(
                plugin_slug=row.plugin_slug,
                action_key=row.action_key,
                metadata_json=row.metadata_json,
            ),
            cost_cents=row.cost_cents,
            dry_run=row.dry_run,
            replayed=True,
            credential_ref=row.credential_ref,
        )


def _background_idempotency_key(scope: str, idempotency_key: str) -> str:
    return hashlib.sha256(f"{scope}\0{idempotency_key}".encode()).hexdigest()


def _background_idempotency_scope(
    *,
    action_ref: str,
    metadata_json: dict[str, Any] | None,
) -> str:
    """Keep one communication intent exclusive across payload-selected action refs."""

    operation = metadata_json.get("operation") if isinstance(metadata_json, dict) else None
    if isinstance(operation, str) and operation in {
        "communication.send",
        "communication.sendBatch",
        "communication.reply",
    }:
        return operation
    return action_ref


def _background_request_fingerprint(
    *,
    action_ref: str,
    request_json: dict[str, Any],
    provider_context_json: dict[str, Any] | None,
    credential_ref: str | None,
    durable_schedule_json: dict[str, Any] | None,
) -> str:
    canonical = json.dumps(
        {
            "action_ref": action_ref,
            "request_json": request_json,
            "provider_context_json": provider_context_json,
            "credential_ref": credential_ref,
            "durable_schedule_json": durable_schedule_json,
            "dry_run": False,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
