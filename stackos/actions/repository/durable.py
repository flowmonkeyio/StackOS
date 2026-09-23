"""Durable ActionCall-linked dispatch state and shared delivery admission.

This module deliberately owns only persistence, leases, and receipt state. A
connector/session owner performs the provider effect after it receives a
persisted item lease, then reports one of the terminal/deferred outcomes here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    ActionDeliveryAdmission,
    Credential,
    DurableActionAttempt,
    DurableActionAttemptStatus,
    DurableActionItem,
    DurableActionItemStatus,
    DurableActionJob,
    DurableActionJobStatus,
    ProjectCredential,
)
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError

from .durable_artifacts import (
    attach_durable_action_artifact_pins,
    validate_durable_action_artifact_pins,
)
from .utils import utcnow

_ACCOUNT_SCOPE = "account"
_TERMINAL_ITEM_STATES = frozenset(
    {
        DurableActionItemStatus.SUCCEEDED,
        DurableActionItemStatus.FAILED,
        DurableActionItemStatus.CANCELLED,
        DurableActionItemStatus.UNKNOWN_HOLD,
    }
)


class DurableActionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    job_id: int
    ordinal: int
    destination_ref: str
    correlation_ref: str
    input_json: dict[str, Any]
    state: DurableActionItemStatus
    next_eligible_at: datetime
    attempt_ref: str | None = None
    lease_ref: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int
    result_json: dict[str, Any] | None = None
    error: str | None = None
    progress_json: dict[str, Any] | None = None
    temporary_message_ref: str | None = None
    final_message_ref: str | None = None
    provider_sending_id: str | None = None
    can_retry: bool = False


class DurableActionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    action_call_id: int
    credential_ref: str
    action_ref: str
    input_digest: str
    state: DurableActionJobStatus
    due_at: datetime
    expires_at: datetime | None = None
    pacing_json: dict[str, float]
    pacing_units_input_field: str | None = None
    destination_interval_multipliers: list[dict[str, Any]] = Field(default_factory=list)
    item_count: int
    pending_count: int
    leased_count: int
    completed_count: int
    failed_count: int
    cancelled_count: int
    unknown_count: int
    next_eligible_at: datetime | None = None
    can_cancel: bool = False
    paused_at: datetime | None = None
    pause_reason: str | None = None
    pause_item_id: int | None = None


class DeliveryAdmissionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admitted: bool
    credential_ref: str
    destination_ref: str
    lease_ref: str | None = None
    lease_expires_at: datetime | None = None
    next_eligible_at: datetime | None = None
    reason: Literal[
        "admitted",
        "account_quiesced",
        "account_busy",
        "destination_busy",
        "not_eligible",
    ]


class AccountDeliveryQuiesceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_ref: str
    state: Literal["draining", "quiesced"]
    active_lease_count: int
    reason: str
    acquired: bool


class DurableRecoveryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unknown_held_count: int = 0
    resumable_job_count: int = 0


class DurableActionMixin:
    """Repository methods for generic durable dispatch without provider decisions."""

    _s: Session
    _asset_dir: Path | None

    def validate_durable_action_items(
        self, items: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        """Normalize sealed item input before reserving the parent ActionCall."""

        if not isinstance(items, list) or not items:
            raise ValidationError("durable action job requires at least one item")
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValidationError(f"durable action item {index} must be an object")
            normalized.append(_normalized_item(item, ordinal=index))
        return normalized

    def validate_durable_action_schedule(
        self,
        *,
        due_at: datetime | None,
        expires_at: datetime | None,
        now: datetime | None = None,
    ) -> None:
        """Validate immutable durable timing before it is sealed or dry-run."""

        current = _as_naive_utc(now or utcnow())
        due = _as_naive_utc(due_at) if due_at is not None else current
        expiry = _as_naive_utc(expires_at) if expires_at is not None else None
        if expiry is not None and expiry <= current:
            raise ValidationError("durable action expires_at must be in the future")
        if expiry is not None and expiry <= due:
            raise ValidationError("durable action expires_at must be after due_at")

    def create_durable_action_job(
        self,
        *,
        project_id: int,
        action_call_id: int,
        credential_ref: str,
        action_ref: str,
        items: list[dict[str, Any]],
        due_at: datetime | None = None,
        expires_at: datetime | None = None,
        pacing_json: dict[str, Any] | None = None,
        pacing_units_input_field: str | None = None,
        destination_interval_multipliers: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> DurableActionJobOut:
        """Persist a sealed target snapshot before the first provider effect."""

        snapshot_items = self.validate_durable_action_items(items)
        if not action_ref.strip():
            raise ValidationError("durable action job requires action_ref")
        now = utcnow()
        due_input = _as_naive_utc(due_at) if due_at is not None else None
        due = due_input or now
        expiry = _as_naive_utc(expires_at) if expires_at is not None else None
        pacing = _normalize_pacing(pacing_json)
        units_field = _normalize_pacing_units_input_field(pacing_units_input_field)
        multipliers = _normalize_destination_interval_multipliers(destination_interval_multipliers)
        for item in snapshot_items:
            _pacing_units_for_input(item["input_json"], units_field)
        snapshot = {
            "action_ref": action_ref,
            "credential_ref": credential_ref,
            "items": snapshot_items,
            "schedule": durable_action_schedule_snapshot(
                due_at=due_input,
                expires_at=expiry,
                pacing_json=pacing,
            ),
        }
        if units_field is not None:
            snapshot["pacing_units_input_field"] = units_field
        if multipliers:
            snapshot["destination_interval_multipliers"] = multipliers
        digest = _digest_snapshot(snapshot)
        if idempotency_key is not None:
            existing = self._s.exec(
                select(DurableActionJob).where(
                    col(DurableActionJob.project_id) == project_id,
                    col(DurableActionJob.idempotency_key) == idempotency_key,
                )
            ).first()
            if existing is not None:
                if existing.input_digest != digest:
                    raise ConflictError(
                        "durable action idempotency key replayed with different input",
                        data={"job_id": existing.id, "idempotency_key": idempotency_key},
                    )
                return self._job_out(existing)
        self.validate_durable_action_schedule(due_at=due_input, expires_at=expiry, now=now)
        credential = self._credential_for_project(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        call = self._action_call(project_id=project_id, action_call_id=action_call_id)
        if call.status != ActionCallStatus.RUNNING:
            raise ConflictError(
                "durable action job requires a running action call",
                data={"action_call_id": action_call_id, "status": call.status.value},
            )
        if call.credential_ref not in {None, credential_ref}:
            raise ConflictError(
                "durable action job credential does not match action call",
                data={"action_call_id": action_call_id, "credential_ref": credential_ref},
            )
        job = DurableActionJob(
            project_id=project_id,
            action_call_id=action_call_id,
            run_id=call.run_id,
            run_plan_id=call.run_plan_id,
            run_plan_step_id=call.run_plan_step_id,
            credential_id=_required_id(credential.id),
            credential_ref=credential_ref,
            action_ref=action_ref,
            idempotency_key=idempotency_key,
            input_digest=digest,
            input_snapshot_json=snapshot,
            metadata_json=dict(metadata_json) if metadata_json else None,
            pacing_json=pacing,
            state=(
                DurableActionJobStatus.SCHEDULED if due > now else DurableActionJobStatus.RUNNING
            ),
            due_at=due,
            expires_at=expiry,
            created_at=now,
            updated_at=now,
        )
        self._s.add(job)
        self._s.flush()
        job_id = _required_id(job.id)
        attach_durable_action_artifact_pins(
            self._s,
            project_id=project_id,
            job_id=job_id,
            items=snapshot_items,
            asset_dir=self._asset_dir,
        )
        for item in snapshot_items:
            self._s.add(
                DurableActionItem(
                    job_id=job_id,
                    ordinal=int(item["ordinal"]),
                    destination_ref=str(item["destination_ref"]),
                    correlation_ref=str(item["correlation_ref"]),
                    input_digest=_digest_snapshot(item["input_json"]),
                    input_json=dict(item["input_json"]),
                    next_eligible_at=due,
                    created_at=now,
                    updated_at=now,
                )
            )
        call.credential_id = _required_id(credential.id)
        call.credential_ref = credential_ref
        call.metadata_json = {
            **(call.metadata_json or {}),
            "durable_action_job_id": job_id,
            "durable_input_digest": digest,
        }
        self._s.add(call)
        try:
            self._s.commit()
        except IntegrityError as exc:
            self._s.rollback()
            if idempotency_key is None:
                raise
            existing = self._s.exec(
                select(DurableActionJob).where(
                    col(DurableActionJob.project_id) == project_id,
                    col(DurableActionJob.idempotency_key) == idempotency_key,
                )
            ).first()
            if existing is not None and existing.input_digest == digest:
                return self._job_out(existing)
            raise ConflictError(
                "durable action idempotency key replayed with different input",
                data={"idempotency_key": idempotency_key},
            ) from exc
        self._s.refresh(job)
        return self._job_out(job)

    def get_durable_action_job(self, *, project_id: int, job_id: int) -> DurableActionJobOut:
        return self._job_out(self._job(project_id=project_id, job_id=job_id))

    def get_durable_action_job_for_action_call(
        self, *, project_id: int, action_call_id: int
    ) -> DurableActionJobOut | None:
        job = self._s.exec(
            select(DurableActionJob).where(
                col(DurableActionJob.project_id) == project_id,
                col(DurableActionJob.action_call_id) == action_call_id,
            )
        ).first()
        return self._job_out(job) if job is not None else None

    def list_durable_action_items(
        self,
        *,
        project_id: int,
        job_id: int,
    ) -> list[DurableActionItemOut]:
        job = self._job(project_id=project_id, job_id=job_id)
        return [
            self._item_out(item)
            for item in self._s.exec(
                select(DurableActionItem)
                .where(col(DurableActionItem.job_id) == _required_id(job.id))
                .order_by(col(DurableActionItem.ordinal))
            ).all()
        ]

    def list_dispatchable_durable_action_jobs(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[DurableActionJobOut]:
        """Return due jobs that a daemon dispatcher may attempt to claim.

        This is deliberately a read followed by per-item atomic claiming.  A
        process must never infer an effect from this list alone; the lease
        created by ``claim_durable_action_items`` is the durable authority.
        """

        if limit < 1:
            raise ValidationError("durable action dispatch limit must be positive")
        now = _as_naive_utc(now or utcnow())
        jobs = self._s.exec(
            select(DurableActionJob)
            .join(
                DurableActionItem,
                col(DurableActionItem.job_id) == col(DurableActionJob.id),
            )
            .outerjoin(
                ActionDeliveryAdmission,
                and_(
                    col(ActionDeliveryAdmission.credential_id)
                    == col(DurableActionJob.credential_id),
                    col(ActionDeliveryAdmission.scope_key) == _ACCOUNT_SCOPE,
                ),
            )
            .where(
                col(DurableActionJob.state).in_(
                    [DurableActionJobStatus.SCHEDULED, DurableActionJobStatus.RUNNING]
                ),
                col(DurableActionJob.due_at) <= now,
                or_(
                    col(DurableActionJob.expires_at).is_(None),
                    col(DurableActionJob.expires_at) > now,
                ),
                col(DurableActionItem.state).in_(
                    [DurableActionItemStatus.PENDING, DurableActionItemStatus.DEFERRED]
                ),
                col(DurableActionItem.next_eligible_at) <= now,
                or_(
                    col(ActionDeliveryAdmission.id).is_(None),
                    col(ActionDeliveryAdmission.quiesced_at).is_(None),
                ),
            )
            .order_by(col(DurableActionJob.due_at), col(DurableActionJob.id))
            .distinct()
            .limit(limit)
        ).all()
        return [self._job_out(job) for job in jobs]

    def expire_durable_action_jobs(self, *, now: datetime | None = None) -> int:
        """Persist deadline cancellation for work that never received a provider lease."""

        current = _as_naive_utc(now or utcnow())
        expired = list(
            self._s.exec(
                select(DurableActionJob).where(
                    DurableActionJob.expires_at.is_not(None),  # type: ignore[union-attr]
                    DurableActionJob.expires_at <= current,  # type: ignore[operator]
                )
            ).all()
        )
        changed = 0
        for job in expired:
            changed += int(self._expire_durable_action_job(job, now=current))
        if changed:
            self._s.commit()
        return changed

    def list_unknown_held_durable_action_items(
        self,
        *,
        credential_ref: str,
    ) -> list[DurableActionItemOut]:
        """Read held provider receipts for one Account without selecting a provider.

        A native adapter may filter each returned persisted ``progress_json``
        using its own provider update identifiers, then call
        ``reconcile_durable_action_item`` with the exact attempt reference.
        """

        rows = self._s.exec(
            select(DurableActionItem)
            .join(DurableActionJob, col(DurableActionItem.job_id) == col(DurableActionJob.id))
            .where(
                col(DurableActionJob.credential_ref) == credential_ref,
                col(DurableActionItem.state) == DurableActionItemStatus.UNKNOWN_HOLD,
            )
            .order_by(col(DurableActionItem.id))
        ).all()
        return [self._item_out(item) for item in rows]

    def durable_action_progress(self, *, action_call_id: int) -> dict[str, Any] | None:
        job = self._s.exec(
            select(DurableActionJob).where(col(DurableActionJob.action_call_id) == action_call_id)
        ).first()
        if job is None:
            return None
        counts = self._job_counts(_required_id(job.id))
        progress = {
            "phase": _job_phase(job.state),
            "total_count": counts.total,
            "pending_count": counts.pending,
            "leased_count": counts.leased,
            "completed_count": counts.completed,
            "failed_count": counts.failed,
            "unknown_count": counts.unknown,
            "cancelled_count": counts.cancelled,
            "next_eligible_at": counts.next_eligible_at.isoformat()
            if counts.next_eligible_at is not None
            else None,
        }
        if job.state == DurableActionJobStatus.PAUSED:
            pause = (job.metadata_json or {}).get("delivery_pause") or {}
            progress.update(
                {
                    "pause_reason": pause.get("reason"),
                    "pause_item_id": pause.get("item_id"),
                    "next_action": (
                        "inspect actionCall.items, then actionCall.resume or actionCall.cancel"
                    ),
                }
            )
        return progress

    def claim_durable_action_items(
        self,
        *,
        project_id: int,
        job_id: int,
        now: datetime | None = None,
        limit: int = 1,
        lease_seconds: int = 60,
        account_interval_seconds: float = 1.0,
        destination_interval_seconds: float = 1.0,
        overlap_account_leases: bool = False,
    ) -> list[DurableActionItemOut]:
        """Persist item attempt UUIDs and rate leases before connector dispatch."""

        if limit < 1:
            raise ValidationError("durable action item claim limit must be positive")
        if lease_seconds < 1:
            raise ValidationError("durable action item lease_seconds must be positive")
        now = _as_naive_utc(now or utcnow())
        job = self._job(project_id=project_id, job_id=job_id)
        if self._expire_durable_action_job(job, now=now):
            self._s.commit()
        if job.state in {
            DurableActionJobStatus.CANCELLED,
            DurableActionJobStatus.UNKNOWN_HOLD,
        }:
            return []
        if (
            job.state
            in {
                DurableActionJobStatus.PAUSED,
                DurableActionJobStatus.COMPLETED,
                DurableActionJobStatus.FAILED,
            }
            or job.due_at > now
        ):
            return []
        items = list(
            self._s.exec(
                select(DurableActionItem)
                .where(
                    col(DurableActionItem.job_id) == _required_id(job.id),
                    col(DurableActionItem.state).in_(
                        [DurableActionItemStatus.PENDING, DurableActionItemStatus.DEFERRED]
                    ),
                    col(DurableActionItem.next_eligible_at) <= now,
                )
                .order_by(col(DurableActionItem.ordinal))
                .limit(limit)
            ).all()
        )
        leased: list[DurableActionItem] = []
        units_field = _normalize_pacing_units_input_field(
            (job.input_snapshot_json or {}).get("pacing_units_input_field")
        )
        multipliers = _normalize_destination_interval_multipliers(
            (job.input_snapshot_json or {}).get("destination_interval_multipliers")
        )
        for item in items:
            units = _pacing_units_for_input(item.input_json, units_field)
            destination_factor = max(
                (
                    multiplier["multiplier"]
                    for multiplier in multipliers
                    if item.destination_ref.startswith(multiplier["destination_ref_prefix"])
                ),
                default=1.0,
            )
            admission = self._admit_delivery(
                project_id=project_id,
                credential_ref=job.credential_ref,
                destination_ref=item.destination_ref,
                now=now,
                lease_seconds=lease_seconds,
                account_interval_seconds=account_interval_seconds * units,
                destination_interval_seconds=(
                    destination_interval_seconds * units * destination_factor
                ),
                overlap_account_leases=overlap_account_leases,
                commit=False,
            )
            if not admission.admitted:
                if (
                    admission.reason not in {"account_busy", "destination_busy"}
                    and admission.next_eligible_at is not None
                ):
                    item.next_eligible_at = admission.next_eligible_at
                    item.updated_at = now
                    self._s.add(item)
                continue
            assert admission.lease_ref is not None and admission.lease_expires_at is not None
            attempt_ref = f"durable-attempt:{uuid4()}"
            item.state = DurableActionItemStatus.LEASED
            item.lease_ref = admission.lease_ref
            item.lease_expires_at = admission.lease_expires_at
            item.attempt_count += 1
            item.first_attempted_at = item.first_attempted_at or now
            item.updated_at = now
            self._s.add(item)
            self._s.add(
                DurableActionAttempt(
                    item_id=_required_id(item.id),
                    ordinal=item.attempt_count,
                    attempt_ref=attempt_ref,
                    state=DurableActionAttemptStatus.LEASED,
                    created_at=now,
                )
            )
            leased.append(item)
        if leased:
            job.state = DurableActionJobStatus.RUNNING
            job.updated_at = now
            self._s.add(job)
        self._s.commit()
        return [self._item_out(item, current_attempt_ref=True) for item in leased]

    def complete_durable_action_item(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
        result_json: dict[str, Any],
        now: datetime | None = None,
        provider_sending_id: str | None = None,
        temporary_message_ref: str | None = None,
        final_message_ref: str | None = None,
    ) -> DurableActionItemOut:
        now = _as_naive_utc(now or utcnow())
        job, item, attempt = self._active_attempt(
            project_id=project_id,
            item_id=item_id,
            attempt_ref=attempt_ref,
        )
        attempt.state = DurableActionAttemptStatus.SUCCEEDED
        attempt.provider_sending_id = provider_sending_id or attempt.provider_sending_id
        attempt.temporary_message_ref = temporary_message_ref or attempt.temporary_message_ref
        attempt.final_message_ref = final_message_ref
        attempt.receipt_json = dict(result_json)
        attempt.completed_at = now
        lease_ref = item.lease_ref
        if lease_ref is None:  # pragma: no cover - _active_attempt proves the state invariant
            raise RuntimeError("leased durable action item has no delivery lease")
        item.state = DurableActionItemStatus.SUCCEEDED
        item.lease_ref = None
        item.lease_expires_at = None
        item.completed_at = now
        item.result_json = dict(result_json)
        item.error = None
        item.updated_at = now
        self._s.add_all([attempt, item])
        self._release_delivery_admission(
            credential_id=job.credential_id,
            destination_ref=item.destination_ref,
            lease_ref=lease_ref,
            now=now,
            commit=False,
        )
        self._recompute_job(job, now=now)
        self._s.commit()
        return self._item_out(item)

    def record_durable_action_item_progress(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
        progress_json: dict[str, Any],
        temporary_message_ref: str | None = None,
        provider_sending_id: str | None = None,
        now: datetime | None = None,
    ) -> DurableActionItemOut:
        """Persist a provider-accepted temporary receipt before final confirmation.

        The caller remains responsible for a terminal ``complete``, ``defer``,
        ``fail``, or ``hold`` transition. A late native receipt may add progress
        to its exact held attempt, but cannot reopen or replay delivery.
        """

        if not isinstance(progress_json, dict):
            raise ValidationError("durable action progress must be an object")
        now = _as_naive_utc(now or utcnow())
        item = self._s.get(DurableActionItem, item_id)
        if item is None:
            raise NotFoundError(f"durable action item {item_id} not found")
        job = self._s.get(DurableActionJob, item.job_id)
        if job is None or job.project_id != project_id:
            raise NotFoundError(f"durable action item {item_id} not found for project {project_id}")
        if item.state == DurableActionItemStatus.UNKNOWN_HOLD:
            attempt = self._latest_attempt_for_item(item)
            if (
                attempt is None
                or attempt.attempt_ref != attempt_ref
                or attempt.state != DurableActionAttemptStatus.UNKNOWN_HOLD
            ):
                raise ConflictError(
                    "durable action progress does not match the held attempt",
                    data={"item_id": item_id, "attempt_ref": attempt_ref},
                )
        else:
            _job, item, attempt = self._active_attempt(
                project_id=project_id,
                item_id=item_id,
                attempt_ref=attempt_ref,
            )
        if temporary_message_ref is not None and not temporary_message_ref.strip():
            raise ValidationError("temporary_message_ref cannot be empty")
        attempt.progress_json = _merge_progress_json(attempt.progress_json, progress_json)
        if temporary_message_ref is not None:
            attempt.temporary_message_ref = temporary_message_ref
        if provider_sending_id is not None:
            attempt.provider_sending_id = provider_sending_id
        self._s.add(attempt)
        self._s.commit()
        return self._item_out(item)

    def defer_durable_action_item(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
        retry_at: datetime,
        reason: str,
        flood_wait_seconds: int | None = None,
        result_json: dict[str, Any] | None = None,
        retry_scope: Literal["account", "destination"] = "account",
        now: datetime | None = None,
    ) -> DurableActionItemOut:
        now = _as_naive_utc(now or utcnow())
        retry_at = _as_naive_utc(retry_at)
        if retry_at <= now:
            raise ValidationError("durable action retry_at must be in the future")
        if retry_scope not in {"account", "destination"}:
            raise ValidationError("durable action retry_scope must be account or destination")
        job, item, attempt = self._active_attempt(
            project_id=project_id,
            item_id=item_id,
            attempt_ref=attempt_ref,
        )
        attempt.state = DurableActionAttemptStatus.DEFERRED
        attempt.receipt_json = {
            **(result_json or {}),
            "status": "deferred",
            "reason": reason,
            "flood_wait_seconds": flood_wait_seconds,
            "retry_scope": retry_scope,
            "provider_executed": False,
            "retry_safe": True,
        }
        attempt.completed_at = now
        lease_ref = item.lease_ref
        if lease_ref is None:  # pragma: no cover - _active_attempt proves the state invariant
            raise RuntimeError("leased durable action item has no delivery lease")
        item.state = DurableActionItemStatus.DEFERRED
        item.lease_ref = None
        item.lease_expires_at = None
        item.next_eligible_at = retry_at
        item.error = reason
        item.result_json = dict(result_json) if result_json else None
        item.updated_at = now
        self._s.add_all([attempt, item])
        self._release_delivery_admission(
            credential_id=job.credential_id,
            destination_ref=item.destination_ref,
            lease_ref=lease_ref,
            now=now,
            commit=False,
        )
        self._advance_delivery_cooldown(
            credential_id=job.credential_id,
            destination_ref=item.destination_ref,
            until=retry_at,
            scope=retry_scope,
            now=now,
        )
        job.state = DurableActionJobStatus.SCHEDULED
        job.updated_at = now
        self._s.add(job)
        self._s.commit()
        return self._item_out(item)

    def fail_durable_action_item(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
        error: str,
        result_json: dict[str, Any] | None = None,
        pause_remaining_reason: str | None = None,
        cooldown_until: datetime | None = None,
        cooldown_scope: Literal["account", "destination"] = "account",
        now: datetime | None = None,
    ) -> DurableActionItemOut:
        now = _as_naive_utc(now or utcnow())
        job, item, attempt = self._active_attempt(
            project_id=project_id,
            item_id=item_id,
            attempt_ref=attempt_ref,
        )
        attempt.state = DurableActionAttemptStatus.FAILED
        attempt.error = error
        attempt.receipt_json = dict(result_json) if result_json else None
        attempt.completed_at = now
        lease_ref = item.lease_ref
        if lease_ref is None:  # pragma: no cover - _active_attempt proves the state invariant
            raise RuntimeError("leased durable action item has no delivery lease")
        item.state = DurableActionItemStatus.FAILED
        item.lease_ref = None
        item.lease_expires_at = None
        item.completed_at = now
        item.result_json = dict(result_json) if result_json else None
        item.error = error
        item.updated_at = now
        self._s.add_all([attempt, item])
        self._release_delivery_admission(
            credential_id=job.credential_id,
            destination_ref=item.destination_ref,
            lease_ref=lease_ref,
            now=now,
            commit=False,
        )
        if cooldown_until is not None:
            self._advance_delivery_cooldown(
                credential_id=job.credential_id,
                destination_ref=item.destination_ref,
                until=_as_naive_utc(cooldown_until),
                scope=cooldown_scope,
                now=now,
            )
        self._recompute_job(job, now=now)
        if pause_remaining_reason and job.state in {
            DurableActionJobStatus.SCHEDULED,
            DurableActionJobStatus.RUNNING,
        }:
            job.state = DurableActionJobStatus.PAUSED
            job.paused_at = now
            job.metadata_json = {
                **(job.metadata_json or {}),
                "delivery_pause": {
                    "reason": pause_remaining_reason,
                    "item_id": item_id,
                    "at": now.isoformat(),
                },
            }
            self._s.add(job)
        self._s.commit()
        return self._item_out(item)

    def hold_durable_action_item_unknown(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
        reason: str,
        result_json: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> DurableActionItemOut:
        now = _as_naive_utc(now or utcnow())
        job, item, attempt = self._active_attempt(
            project_id=project_id,
            item_id=item_id,
            attempt_ref=attempt_ref,
        )
        self._hold_unknown(
            job=job,
            item=item,
            attempt=attempt,
            reason=reason,
            result_json=result_json,
            now=now,
        )
        self._recompute_job(job, now=now)
        self._s.commit()
        return self._item_out(item)

    def reconcile_durable_action_item(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
        result_json: dict[str, Any],
        success: bool,
        now: datetime | None = None,
    ) -> DurableActionItemOut:
        """Apply a late provider receipt to its exact previously-held attempt.

        A late update never replays an effect and never claims or releases an
        admission lease.  It can repair only an ``UNKNOWN_HOLD`` item whose
        latest persisted attempt has the supplied correlation reference.
        """

        if not isinstance(result_json, dict):
            raise ValidationError("durable action reconciliation result must be an object")
        now = _as_naive_utc(now or utcnow())
        item = self._s.get(DurableActionItem, item_id)
        if item is None:
            raise NotFoundError(f"durable action item {item_id} not found")
        job = self._s.get(DurableActionJob, item.job_id)
        if job is None or job.project_id != project_id:
            raise NotFoundError(f"durable action item {item_id} not found for project {project_id}")
        if item.state != DurableActionItemStatus.UNKNOWN_HOLD:
            raise ConflictError(
                "durable action item is not awaiting a late receipt",
                data={"item_id": item_id, "state": item.state.value},
            )
        attempt = self._latest_attempt_for_item(item)
        if (
            attempt is None
            or attempt.attempt_ref != attempt_ref
            or attempt.state != DurableActionAttemptStatus.UNKNOWN_HOLD
        ):
            raise ConflictError(
                "late receipt does not match the held durable action attempt",
                data={"item_id": item_id, "attempt_ref": attempt_ref},
            )
        attempt.state = (
            DurableActionAttemptStatus.SUCCEEDED if success else DurableActionAttemptStatus.FAILED
        )
        attempt.receipt_json = dict(result_json)
        attempt.completed_at = now
        item.state = (
            DurableActionItemStatus.SUCCEEDED if success else DurableActionItemStatus.FAILED
        )
        item.result_json = dict(result_json)
        item.error = None if success else str(result_json.get("error") or "late-provider-failure")
        item.completed_at = now
        item.updated_at = now
        self._s.add_all([attempt, item])
        self._recompute_job(job, now=now)
        self._s.commit()
        return self._item_out(item)

    def pause_durable_action_job(self, *, project_id: int, job_id: int) -> DurableActionJobOut:
        job = self._job(project_id=project_id, job_id=job_id)
        now = utcnow()
        if self._expire_durable_action_job(job, now=now):
            self._s.commit()
        if job.state in {
            DurableActionJobStatus.COMPLETED,
            DurableActionJobStatus.FAILED,
            DurableActionJobStatus.CANCELLED,
            DurableActionJobStatus.UNKNOWN_HOLD,
        }:
            raise ConflictError("durable action job is already terminal", data={"job_id": job_id})
        job.state = DurableActionJobStatus.PAUSED
        job.paused_at = now
        job.updated_at = now
        self._s.add(job)
        self._s.commit()
        return self._job_out(job)

    def resume_durable_action_job(
        self,
        *,
        project_id: int,
        job_id: int,
        now: datetime | None = None,
    ) -> DurableActionJobOut:
        job = self._job(project_id=project_id, job_id=job_id)
        now = _as_naive_utc(now or utcnow())
        if self._expire_durable_action_job(job, now=now):
            self._s.commit()
        if job.state != DurableActionJobStatus.PAUSED:
            raise ConflictError(
                "only a paused durable action job can resume", data={"job_id": job_id}
            )
        job.state = (
            DurableActionJobStatus.SCHEDULED if job.due_at > now else DurableActionJobStatus.RUNNING
        )
        job.paused_at = None
        if job.metadata_json and "delivery_pause" in job.metadata_json:
            job.metadata_json = {
                key: value for key, value in job.metadata_json.items() if key != "delivery_pause"
            }
        job.updated_at = now
        self._s.add(job)
        self._s.commit()
        return self._job_out(job)

    def cancel_durable_action_job(
        self,
        *,
        project_id: int,
        job_id: int,
        now: datetime | None = None,
    ) -> DurableActionJobOut:
        """Cancel the remaining receipt-proven-no-effect item work only."""

        job = self._job(project_id=project_id, job_id=job_id)
        current = _as_naive_utc(now or utcnow())
        if self._expire_durable_action_job(job, now=current):
            self._s.commit()
        cancellable = [
            item for item in self._job_items(_required_id(job.id)) if self._can_cancel_item(item)
        ]
        if not cancellable:
            raise ConflictError(
                "durable action job has no remaining receipt-proven-no-effect items to cancel",
                data={"job_id": job_id},
            )
        for item in cancellable:
            prior_state = item.state
            item.state = DurableActionItemStatus.CANCELLED
            item.completed_at = current
            item.updated_at = current
            item.error = (
                "cancelled-before-attempt"
                if item.attempt_count == 0
                else f"cancelled-after-no-effect-{prior_state.value}"
            )
            self._s.add(item)
        job.cancelled_at = current
        job.updated_at = current
        self._s.add(job)
        self._recompute_job(job, now=current)
        self._s.commit()
        return self._job_out(job)

    def retry_durable_action_items(
        self,
        *,
        project_id: int,
        job_id: int,
        item_ids: list[int],
        now: datetime | None = None,
    ) -> DurableActionJobOut:
        """Requeue an explicit set of receipts that prove no provider effect occurred."""

        if not item_ids or any(isinstance(item_id, bool) or item_id < 1 for item_id in item_ids):
            raise ValidationError("durable action retry requires one or more positive item_ids")
        if len(set(item_ids)) != len(item_ids):
            raise ValidationError("durable action retry item_ids must be unique")
        current = _as_naive_utc(now or utcnow())
        job = self._job(project_id=project_id, job_id=job_id)
        validate_durable_action_artifact_pins(
            self._s,
            project_id=project_id,
            job_id=_required_id(job.id),
            asset_dir=self._asset_dir,
        )
        if self._expire_durable_action_job(job, now=current):
            self._s.commit()
        if job.expires_at is not None and job.expires_at <= current:
            raise ConflictError(
                "durable action deadline has passed; selected items cannot retry",
                data={"job_id": job_id, "expires_at": job.expires_at.isoformat()},
            )
        selected = {
            _required_id(item.id): item for item in self._job_items(_required_id(job.id)) if item.id
        }
        missing = sorted(set(item_ids) - set(selected))
        if missing:
            raise NotFoundError(
                "durable action retry item does not belong to this job",
                data={"job_id": job_id, "item_ids": missing},
            )
        rejected = [
            item_id
            for item_id in item_ids
            if not self._is_retryable_no_effect_item(selected[item_id])
        ]
        if rejected:
            raise ConflictError(
                "durable action retry requires failed, cancelled, or deferred items with a "
                "receipt proving no provider effect",
                data={"job_id": job_id, "item_ids": rejected},
            )
        if self._job_has_unknown_hold(_required_id(job.id)):
            raise ConflictError(
                "durable action retry cannot proceed while any item awaits an unknown receipt",
                data={"job_id": job_id},
            )
        for item_id in item_ids:
            item = selected[item_id]
            item.state = DurableActionItemStatus.PENDING
            item.next_eligible_at = current
            item.lease_ref = None
            item.lease_expires_at = None
            item.completed_at = None
            item.updated_at = current
            self._s.add(item)
        job.state = (
            DurableActionJobStatus.SCHEDULED
            if job.due_at > current
            else DurableActionJobStatus.RUNNING
        )
        job.cancelled_at = None
        job.completed_at = None
        job.updated_at = current
        self._reopen_parent_call_for_retry(job=job)
        self._s.add(job)
        self._s.commit()
        return self._job_out(job)

    def admit_delivery(
        self,
        *,
        project_id: int,
        credential_ref: str,
        destination_ref: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
        account_interval_seconds: float = 1.0,
        destination_interval_seconds: float = 1.0,
    ) -> DeliveryAdmissionOut:
        now = _as_naive_utc(now or utcnow())
        return self._admit_delivery(
            project_id=project_id,
            credential_ref=credential_ref,
            destination_ref=destination_ref,
            now=now,
            lease_seconds=lease_seconds,
            account_interval_seconds=account_interval_seconds,
            destination_interval_seconds=destination_interval_seconds,
            commit=True,
        )

    def release_delivery_admission(
        self,
        *,
        credential_ref: str,
        destination_ref: str,
        lease_ref: str,
        next_eligible_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        credential = self._credential_by_ref(credential_ref)
        self._release_delivery_admission(
            credential_id=_required_id(credential.id),
            destination_ref=destination_ref,
            lease_ref=lease_ref,
            next_eligible_at=_as_naive_utc(next_eligible_at) if next_eligible_at else None,
            now=_as_naive_utc(now or utcnow()),
            commit=True,
        )

    def quiesce_account_delivery(
        self,
        *,
        credential_ref: str,
        reason: str,
        now: datetime | None = None,
    ) -> AccountDeliveryQuiesceOut:
        """Acquire an Account delivery hold without replacing another owner's hold.

        Callers that may later resume delivery must use a unique reason token
        and pass that token as ``expected_reason`` to release.  A repeated
        acquisition with that exact token is idempotent; an existing different
        reason remains authoritative.
        """

        if not reason.strip():
            raise ValidationError("account delivery quiesce requires a reason token")
        now = _as_naive_utc(now or utcnow())
        credential = self._credential_by_ref(credential_ref)
        account = self._admission_row(
            credential_id=_required_id(credential.id),
            scope_key=_ACCOUNT_SCOPE,
            now=now,
        )
        acquired = account.quiesced_at is None or account.quiesce_reason == reason
        if acquired:
            account.quiesced_at = now
            account.quiesce_reason = reason
            account.updated_at = now
            self._s.add(account)
            self._s.commit()
        active = self._active_delivery_lease_count(
            credential_id=_required_id(credential.id), now=now
        )
        return AccountDeliveryQuiesceOut(
            credential_ref=credential_ref,
            state="draining" if active else "quiesced",
            active_lease_count=active,
            reason=account.quiesce_reason or reason,
            acquired=acquired,
        )

    def release_account_delivery_quiesce(
        self,
        *,
        credential_ref: str,
        expected_reason: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Release a hold only when its owner matches, unless explicitly manual.

        Omitting ``expected_reason`` is reserved for an explicit operator
        repair path. Automated Account transitions must preserve the token
        returned by ``quiesce_account_delivery`` and use it here.
        """

        if expected_reason is not None and not expected_reason.strip():
            raise ValidationError("expected account delivery hold reason cannot be empty")
        credential = self._credential_by_ref(credential_ref)
        now = _as_naive_utc(now or utcnow())
        account = self._admission_row(
            credential_id=_required_id(credential.id), scope_key=_ACCOUNT_SCOPE, now=now
        )
        if expected_reason is not None and account.quiesce_reason != expected_reason:
            return False
        if self._active_delivery_lease_count(credential_id=_required_id(credential.id), now=now):
            raise ConflictError(
                "account delivery still has in-flight leases",
                data={"credential_ref": credential_ref},
            )
        account.quiesced_at = None
        account.quiesce_reason = None
        account.updated_at = now
        self._s.add(account)
        self._s.commit()
        return True

    def reconcile_durable_action_jobs(self, *, now: datetime | None = None) -> DurableRecoveryOut:
        """Hold in-flight leases unknown after a process restart; never resend them."""

        now = _as_naive_utc(now or utcnow())
        for job in self._s.exec(
            select(DurableActionJob).where(
                DurableActionJob.expires_at.is_not(None),  # type: ignore[union-attr]
                DurableActionJob.expires_at <= now,  # type: ignore[operator]
            )
        ).all():
            self._expire_durable_action_job(job, now=now)
        unknown_held = 0
        jobs: dict[int, DurableActionJob] = {}
        leased = list(
            self._s.exec(
                select(DurableActionItem).where(
                    col(DurableActionItem.state) == DurableActionItemStatus.LEASED
                )
            ).all()
        )
        for item in leased:
            leased_job = self._s.get(DurableActionJob, item.job_id)
            if leased_job is None:
                continue
            attempt = self._active_attempt_for_item(item)
            if attempt is None:
                continue
            self._hold_unknown(
                job=leased_job,
                item=item,
                attempt=attempt,
                reason="daemon-restart-inflight-unknown",
                now=now,
            )
            jobs[_required_id(leased_job.id)] = leased_job
            unknown_held += 1
        resumable = 0
        for job in self._s.exec(
            select(DurableActionJob).where(
                col(DurableActionJob.state).in_(
                    [DurableActionJobStatus.SCHEDULED, DurableActionJobStatus.RUNNING]
                )
            )
        ).all():
            if any(
                item.state in {DurableActionItemStatus.PENDING, DurableActionItemStatus.DEFERRED}
                for item in self._job_items(_required_id(job.id))
            ):
                resumable += 1
        for job in jobs.values():
            self._recompute_job(job, now=now)
        self._s.commit()
        return DurableRecoveryOut(unknown_held_count=unknown_held, resumable_job_count=resumable)

    def _admit_delivery(
        self,
        *,
        project_id: int,
        credential_ref: str,
        destination_ref: str,
        now: datetime,
        lease_seconds: int,
        account_interval_seconds: float,
        destination_interval_seconds: float,
        commit: bool,
        overlap_account_leases: bool = False,
    ) -> DeliveryAdmissionOut:
        if not destination_ref.strip():
            raise ValidationError("delivery admission requires destination_ref")
        if lease_seconds < 1:
            raise ValidationError("delivery admission lease_seconds must be positive")
        if account_interval_seconds < 0 or destination_interval_seconds < 0:
            raise ValidationError("delivery admission intervals cannot be negative")
        credential = self._credential_for_project(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        credential_id = _required_id(credential.id)
        account = self._admission_row(
            credential_id=credential_id,
            scope_key=_ACCOUNT_SCOPE,
            now=now,
        )
        destination = self._admission_row(
            credential_id=credential_id,
            scope_key=_destination_scope(destination_ref),
            now=now,
        )
        if account.quiesced_at is not None:
            if commit:
                self._s.commit()
            return DeliveryAdmissionOut(
                admitted=False,
                credential_ref=credential_ref,
                destination_ref=destination_ref,
                next_eligible_at=account.next_eligible_at,
                reason="account_quiesced",
            )
        active_account = _is_active_lease(account, now)
        active_destination = _is_active_lease(destination, now)
        if active_account or active_destination:
            if commit:
                self._s.commit()
            return DeliveryAdmissionOut(
                admitted=False,
                credential_ref=credential_ref,
                destination_ref=destination_ref,
                next_eligible_at=max(
                    _lease_or_eligible(account, now), _lease_or_eligible(destination, now)
                ),
                reason="account_busy" if active_account else "destination_busy",
            )
        if account.next_eligible_at > now or destination.next_eligible_at > now:
            if commit:
                self._s.commit()
            return DeliveryAdmissionOut(
                admitted=False,
                credential_ref=credential_ref,
                destination_ref=destination_ref,
                next_eligible_at=max(account.next_eligible_at, destination.next_eligible_at),
                reason="not_eligible",
            )
        lease_ref = f"delivery-lease:{uuid4()}"
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        # A paced fan-out reserves the Account's next submission slot while the
        # destination retains the in-flight receipt lease. Disconnect/quiesce
        # still sees each live destination lease, and a serial direct caller's
        # Account lease still blocks this path.
        if not overlap_account_leases:
            account.lease_ref = lease_ref
            account.lease_expires_at = lease_expires_at
        account.next_eligible_at = now + timedelta(seconds=account_interval_seconds)
        account.updated_at = now
        destination.lease_ref = lease_ref
        destination.lease_expires_at = lease_expires_at
        destination.next_eligible_at = now + timedelta(seconds=destination_interval_seconds)
        destination.updated_at = now
        self._s.add_all([account, destination])
        if commit:
            self._s.commit()
        return DeliveryAdmissionOut(
            admitted=True,
            credential_ref=credential_ref,
            destination_ref=destination_ref,
            lease_ref=lease_ref,
            lease_expires_at=lease_expires_at,
            next_eligible_at=max(account.next_eligible_at, destination.next_eligible_at),
            reason="admitted",
        )

    def _release_delivery_admission(
        self,
        *,
        credential_id: int,
        destination_ref: str,
        lease_ref: str,
        now: datetime,
        next_eligible_at: datetime | None = None,
        commit: bool,
    ) -> None:
        for scope_key in (_ACCOUNT_SCOPE, _destination_scope(destination_ref)):
            row = self._s.exec(
                select(ActionDeliveryAdmission).where(
                    col(ActionDeliveryAdmission.credential_id) == credential_id,
                    col(ActionDeliveryAdmission.scope_key) == scope_key,
                )
            ).first()
            if row is None or row.lease_ref != lease_ref:
                continue
            row.lease_ref = None
            row.lease_expires_at = None
            if next_eligible_at is not None and next_eligible_at > row.next_eligible_at:
                row.next_eligible_at = next_eligible_at
            row.updated_at = now
            self._s.add(row)
        if commit:
            self._s.commit()

    def _advance_delivery_cooldown(
        self,
        *,
        credential_id: int,
        destination_ref: str,
        until: datetime,
        scope: Literal["account", "destination"],
        now: datetime,
    ) -> None:
        if scope not in {"account", "destination"}:
            raise ValidationError("delivery cooldown scope must be account or destination")
        if until <= now:
            return
        scopes = [_destination_scope(destination_ref)]
        if scope == "account":
            scopes.append(_ACCOUNT_SCOPE)
        for scope_key in scopes:
            admission = self._admission_row(
                credential_id=credential_id,
                scope_key=scope_key,
                now=now,
            )
            if until > admission.next_eligible_at:
                admission.next_eligible_at = until
                admission.updated_at = now
                self._s.add(admission)

    def _hold_unknown(
        self,
        *,
        job: DurableActionJob,
        item: DurableActionItem,
        attempt: DurableActionAttempt,
        reason: str,
        now: datetime,
        result_json: dict[str, Any] | None = None,
    ) -> None:
        lease_ref = item.lease_ref
        attempt.state = DurableActionAttemptStatus.UNKNOWN_HOLD
        attempt.error = reason
        unknown_receipt = {
            **(result_json or {}),
            "outcome_unknown": True,
            "retry_safe": False,
            "reason": reason,
        }
        attempt.receipt_json = unknown_receipt
        attempt.completed_at = now
        item.state = DurableActionItemStatus.UNKNOWN_HOLD
        item.lease_ref = None
        item.lease_expires_at = None
        item.completed_at = now
        item.result_json = unknown_receipt
        item.error = reason
        item.updated_at = now
        self._s.add_all([attempt, item])
        if lease_ref:
            self._release_delivery_admission(
                credential_id=job.credential_id,
                destination_ref=item.destination_ref,
                lease_ref=lease_ref,
                now=now,
                commit=False,
            )
        job.state = DurableActionJobStatus.UNKNOWN_HOLD
        job.updated_at = now
        self._s.add(job)

    def _active_attempt(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
    ) -> tuple[DurableActionJob, DurableActionItem, DurableActionAttempt]:
        item = self._s.get(DurableActionItem, item_id)
        if item is None:
            raise NotFoundError(f"durable action item {item_id} not found")
        job = self._s.get(DurableActionJob, item.job_id)
        if job is None or job.project_id != project_id:
            raise NotFoundError(f"durable action item {item_id} not found for project {project_id}")
        if item.state != DurableActionItemStatus.LEASED:
            raise ConflictError(
                "durable action item is not leased",
                data={"item_id": item_id, "state": item.state.value},
            )
        attempt = self._s.exec(
            select(DurableActionAttempt).where(
                col(DurableActionAttempt.item_id) == item_id,
                col(DurableActionAttempt.attempt_ref) == attempt_ref,
                col(DurableActionAttempt.state) == DurableActionAttemptStatus.LEASED,
            )
        ).first()
        if attempt is None:
            raise ConflictError(
                "durable action attempt is no longer active",
                data={"item_id": item_id, "attempt_ref": attempt_ref},
            )
        return job, item, attempt

    def _active_attempt_for_item(self, item: DurableActionItem) -> DurableActionAttempt | None:
        return self._s.exec(
            select(DurableActionAttempt)
            .where(
                col(DurableActionAttempt.item_id) == _required_id(item.id),
                col(DurableActionAttempt.state) == DurableActionAttemptStatus.LEASED,
            )
            .order_by(col(DurableActionAttempt.ordinal).desc())
        ).first()

    def _latest_attempt_for_item(self, item: DurableActionItem) -> DurableActionAttempt | None:
        return self._s.exec(
            select(DurableActionAttempt)
            .where(col(DurableActionAttempt.item_id) == _required_id(item.id))
            .order_by(col(DurableActionAttempt.ordinal).desc())
        ).first()

    def _credential_for_project(self, *, project_id: int, credential_ref: str) -> Credential:
        credential = self._credential_by_ref(credential_ref)
        attached = self._s.exec(
            select(ProjectCredential).where(
                col(ProjectCredential.project_id) == project_id,
                col(ProjectCredential.credential_id) == _required_id(credential.id),
            )
        ).first()
        if attached is None:
            raise NotFoundError(
                f"credential {credential_ref!r} is not attached to project {project_id}",
                data={"project_id": project_id, "credential_ref": credential_ref},
            )
        return credential

    def _credential_by_ref(self, credential_ref: str) -> Credential:
        credential = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            raise NotFoundError(f"credential {credential_ref!r} not found")
        return credential

    def _admission_row(
        self,
        *,
        credential_id: int,
        scope_key: str,
        now: datetime,
    ) -> ActionDeliveryAdmission:
        row = self._s.exec(
            select(ActionDeliveryAdmission).where(
                col(ActionDeliveryAdmission.credential_id) == credential_id,
                col(ActionDeliveryAdmission.scope_key) == scope_key,
            )
        ).first()
        if row is not None:
            return row
        row = ActionDeliveryAdmission(
            credential_id=credential_id,
            scope_key=scope_key,
            next_eligible_at=now,
            updated_at=now,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def _active_delivery_lease_count(self, *, credential_id: int, now: datetime) -> int:
        rows = self._s.exec(
            select(ActionDeliveryAdmission).where(
                col(ActionDeliveryAdmission.credential_id) == credential_id,
                col(ActionDeliveryAdmission.lease_expires_at) > now,
            )
        ).all()
        return len({row.lease_ref for row in rows if row.lease_ref})

    def _job(self, *, project_id: int, job_id: int) -> DurableActionJob:
        job = self._s.get(DurableActionJob, job_id)
        if job is None or job.project_id != project_id:
            raise NotFoundError(f"durable action job {job_id} not found for project {project_id}")
        return job

    def _action_call(self, *, project_id: int, action_call_id: int) -> ActionCall:
        call = self._s.get(ActionCall, action_call_id)
        if call is None or call.project_id != project_id:
            raise NotFoundError(f"action call {action_call_id} not found for project {project_id}")
        return call

    def _job_items(self, job_id: int) -> list[DurableActionItem]:
        return list(
            self._s.exec(
                select(DurableActionItem)
                .where(col(DurableActionItem.job_id) == job_id)
                .order_by(col(DurableActionItem.ordinal))
            ).all()
        )

    def _job_counts(self, job_id: int) -> _DurableCounts:
        items = self._job_items(job_id)
        return _DurableCounts(
            total=len(items),
            pending=sum(
                item.state in {DurableActionItemStatus.PENDING, DurableActionItemStatus.DEFERRED}
                for item in items
            ),
            leased=sum(item.state == DurableActionItemStatus.LEASED for item in items),
            completed=sum(item.state == DurableActionItemStatus.SUCCEEDED for item in items),
            failed=sum(item.state == DurableActionItemStatus.FAILED for item in items),
            cancelled=sum(item.state == DurableActionItemStatus.CANCELLED for item in items),
            unknown=sum(item.state == DurableActionItemStatus.UNKNOWN_HOLD for item in items),
            next_eligible_at=min(
                (
                    item.next_eligible_at
                    for item in items
                    if item.state
                    in {DurableActionItemStatus.PENDING, DurableActionItemStatus.DEFERRED}
                ),
                default=None,
            ),
        )

    def _attempt_history(self, item: DurableActionItem) -> list[DurableActionAttempt]:
        return list(
            self._s.exec(
                select(DurableActionAttempt)
                .where(col(DurableActionAttempt.item_id) == _required_id(item.id))
                .order_by(col(DurableActionAttempt.ordinal))
            ).all()
        )

    def _has_proven_no_effect_history(self, item: DurableActionItem) -> bool:
        attempts = self._attempt_history(item)
        return not attempts or all(_attempt_proves_no_effect(attempt) for attempt in attempts)

    def _can_cancel_item(self, item: DurableActionItem) -> bool:
        return item.state in {
            DurableActionItemStatus.PENDING,
            DurableActionItemStatus.DEFERRED,
        } and self._has_proven_no_effect_history(item)

    def _is_retryable_no_effect_item(self, item: DurableActionItem) -> bool:
        return item.state in {
            DurableActionItemStatus.FAILED,
            DurableActionItemStatus.CANCELLED,
            DurableActionItemStatus.DEFERRED,
        } and self._has_proven_no_effect_history(item)

    def _job_has_unknown_hold(self, job_id: int) -> bool:
        return any(
            item.state == DurableActionItemStatus.UNKNOWN_HOLD for item in self._job_items(job_id)
        )

    def _expire_durable_action_job(self, job: DurableActionJob, *, now: datetime) -> bool:
        """Seal a passed deadline without overwriting active or unknown receipts."""

        if job.expires_at is None or job.expires_at > now:
            return False
        changed = False
        for item in self._job_items(_required_id(job.id)):
            if not self._can_cancel_item(item):
                continue
            prior_state = item.state
            item.state = DurableActionItemStatus.CANCELLED
            item.completed_at = now
            item.updated_at = now
            item.error = (
                "expired-before-attempt"
                if item.attempt_count == 0
                else f"expired-after-no-effect-{prior_state.value}"
            )
            self._s.add(item)
            changed = True
        previous_state = job.state
        if changed:
            job.cancelled_at = now
        self._recompute_job(job, now=now)
        changed = changed or job.state != previous_state
        return changed

    def _recompute_job(self, job: DurableActionJob, *, now: datetime) -> None:
        counts = self._job_counts(_required_id(job.id))
        call = self._action_call(project_id=job.project_id, action_call_id=job.action_call_id)
        deadline_expired = job.expires_at is not None and job.expires_at <= now
        if counts.unknown:
            job.state = DurableActionJobStatus.UNKNOWN_HOLD
            job.completed_at = now
            response = _terminal_response(state="unknown-hold", counts=counts)
            response.update({"outcome_unknown": True, "retry_safe": False})
            self._complete_parent_call(
                call=call, status=ActionCallStatus.FAILED, response=response, now=now
            )
        elif counts.leased or counts.pending:
            if deadline_expired:
                job.state = DurableActionJobStatus.CANCELLED
                job.cancelled_at = job.cancelled_at or now
            elif job.state not in {DurableActionJobStatus.PAUSED, DurableActionJobStatus.CANCELLED}:
                job.state = (
                    DurableActionJobStatus.SCHEDULED
                    if counts.pending
                    else DurableActionJobStatus.RUNNING
                )
            job.completed_at = None
            if (
                (
                    counts.leased
                    or job.state
                    in {DurableActionJobStatus.SCHEDULED, DurableActionJobStatus.RUNNING}
                )
                and call.status == ActionCallStatus.FAILED
                and isinstance(call.response_json, dict)
                and call.response_json.get("outcome_unknown") is True
            ):
                self._reopen_parent_call_for_retry(job=job)
        elif counts.failed:
            job.state = DurableActionJobStatus.FAILED
            job.completed_at = now
            self._complete_parent_call(
                call=call,
                status=ActionCallStatus.FAILED,
                response=_terminal_response(state="failed", counts=counts),
                now=now,
            )
        elif counts.cancelled:
            job.state = DurableActionJobStatus.CANCELLED
            job.completed_at = now
            self._complete_parent_call(
                call=call,
                status=ActionCallStatus.FAILED,
                response=_terminal_response(state="cancelled", counts=counts),
                now=now,
            )
        else:
            job.state = DurableActionJobStatus.COMPLETED
            job.completed_at = now
            self._complete_parent_call(
                call=call,
                status=ActionCallStatus.SUCCESS,
                response=_terminal_response(state="completed", counts=counts),
                now=now,
            )
        job.updated_at = now
        self._s.add_all([job, call])

    def _reopen_parent_call_for_retry(self, *, job: DurableActionJob) -> None:
        call = self._action_call(project_id=job.project_id, action_call_id=job.action_call_id)
        call.status = ActionCallStatus.RUNNING
        call.completed_at = None
        call.response_json = None
        call.error = None
        self._s.add(call)

    def _complete_parent_call(
        self,
        *,
        call: ActionCall,
        status: ActionCallStatus,
        response: dict[str, Any],
        now: datetime,
    ) -> None:
        prior_unknown_hold = (
            call.status == ActionCallStatus.FAILED
            and isinstance(call.response_json, dict)
            and call.response_json.get("outcome_unknown") is True
        )
        if call.status != ActionCallStatus.RUNNING and not prior_unknown_hold:
            return
        call.status = status
        call.response_json = response
        call.completed_at = now

    def _job_out(self, job: DurableActionJob) -> DurableActionJobOut:
        counts = self._job_counts(_required_id(job.id))
        pause = (job.metadata_json or {}).get("delivery_pause") or {}
        return DurableActionJobOut(
            id=_required_id(job.id),
            project_id=job.project_id,
            action_call_id=job.action_call_id,
            credential_ref=job.credential_ref,
            action_ref=job.action_ref,
            input_digest=job.input_digest,
            state=job.state,
            due_at=job.due_at,
            expires_at=job.expires_at,
            pacing_json={key: float(value) for key, value in (job.pacing_json or {}).items()},
            pacing_units_input_field=(job.input_snapshot_json or {}).get(
                "pacing_units_input_field"
            ),
            destination_interval_multipliers=list(
                (job.input_snapshot_json or {}).get("destination_interval_multipliers") or []
            ),
            item_count=counts.total,
            pending_count=counts.pending,
            leased_count=counts.leased,
            completed_count=counts.completed,
            failed_count=counts.failed,
            cancelled_count=counts.cancelled,
            unknown_count=counts.unknown,
            next_eligible_at=counts.next_eligible_at,
            can_cancel=(
                (job.expires_at is None or job.expires_at > utcnow())
                and any(
                    self._can_cancel_item(item) for item in self._job_items(_required_id(job.id))
                )
            ),
            paused_at=job.paused_at,
            pause_reason=(
                pause.get("reason") if job.state == DurableActionJobStatus.PAUSED else None
            ),
            pause_item_id=(
                pause.get("item_id") if job.state == DurableActionJobStatus.PAUSED else None
            ),
        )

    def _item_out(
        self,
        item: DurableActionItem,
        *,
        current_attempt_ref: bool = False,
    ) -> DurableActionItemOut:
        attempt = (
            self._active_attempt_for_item(item)
            if current_attempt_ref
            else self._latest_attempt_for_item(item)
        )
        job = self._s.get(DurableActionJob, item.job_id)
        if job is None:  # pragma: no cover - database foreign key invariant
            raise RuntimeError(f"durable action item {item.id} has no job")
        return DurableActionItemOut(
            id=_required_id(item.id),
            project_id=job.project_id,
            job_id=item.job_id,
            ordinal=item.ordinal,
            destination_ref=item.destination_ref,
            correlation_ref=item.correlation_ref,
            input_json=dict(item.input_json or {}),
            state=item.state,
            next_eligible_at=item.next_eligible_at,
            attempt_ref=attempt.attempt_ref if attempt is not None else None,
            lease_ref=item.lease_ref,
            lease_expires_at=item.lease_expires_at,
            attempt_count=item.attempt_count,
            result_json=dict(item.result_json) if item.result_json else None,
            error=item.error,
            progress_json=(
                dict(attempt.progress_json) if attempt and attempt.progress_json else None
            ),
            temporary_message_ref=attempt.temporary_message_ref if attempt else None,
            final_message_ref=attempt.final_message_ref if attempt else None,
            provider_sending_id=attempt.provider_sending_id if attempt else None,
            can_retry=(
                self._is_retryable_no_effect_item(item)
                and (job.expires_at is None or job.expires_at > utcnow())
                and not self._job_has_unknown_hold(_required_id(job.id))
            ),
        )


class _DurableCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    pending: int
    leased: int
    completed: int
    failed: int
    cancelled: int
    unknown: int
    next_eligible_at: datetime | None = None


def _normalized_item(value: dict[str, Any], *, ordinal: int) -> dict[str, Any]:
    destination_ref = value.get("destination_ref")
    correlation_ref = value.get("correlation_ref")
    input_json = value.get("input_json")
    if not isinstance(destination_ref, str) or not destination_ref.strip():
        raise ValidationError(f"durable action item {ordinal} requires destination_ref")
    if not isinstance(input_json, dict):
        raise ValidationError(f"durable action item {ordinal} requires object input_json")
    normalized_destination = destination_ref.strip()
    normalized_input = dict(input_json)
    if correlation_ref is None:
        correlation_ref = "durable-item:" + _digest_snapshot(
            {
                "ordinal": ordinal,
                "destination_ref": normalized_destination,
                "input_json": normalized_input,
            }
        )
    if not isinstance(correlation_ref, str) or not correlation_ref.strip():
        raise ValidationError(
            f"durable action item {ordinal} correlation_ref must be a non-empty string"
        )
    return {
        "ordinal": ordinal,
        "destination_ref": normalized_destination,
        "correlation_ref": correlation_ref.strip(),
        "input_json": normalized_input,
    }


def _normalize_pacing_units_input_field(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValidationError("durable pacing units input field must be a top-level field name")
    return value


def _pacing_units_for_input(input_json: dict[str, Any], field: str | None) -> int:
    if field is None:
        return 1
    values = input_json.get(field)
    if not isinstance(values, list) or not 1 <= len(values) <= 1000:
        raise ValidationError(
            "durable pacing units input field must contain 1 to 1000 items",
            data={"field": field},
        )
    return len(values)


def _normalize_destination_interval_multipliers(
    value: Any,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValidationError(
            "durable destination interval multipliers must be a list of at most 32"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {
            "destination_ref_prefix",
            "multiplier",
        }:
            raise ValidationError(
                "durable destination interval multiplier entry has invalid fields"
            )
        prefix = entry["destination_ref_prefix"]
        multiplier = entry["multiplier"]
        if not isinstance(prefix, str) or not prefix or len(prefix) > 300 or prefix in seen:
            raise ValidationError("durable destination interval prefix must be unique and nonempty")
        if (
            isinstance(multiplier, bool)
            or not isinstance(multiplier, int | float)
            or not math.isfinite(multiplier)
            or multiplier < 1
        ):
            raise ValidationError("durable destination interval multiplier must be at least 1")
        seen.add(prefix)
        normalized.append({"destination_ref_prefix": prefix, "multiplier": float(multiplier)})
    return normalized


def durable_action_schedule_snapshot(
    *,
    due_at: datetime | None,
    expires_at: datetime | None,
    pacing_json: dict[str, float],
) -> dict[str, Any]:
    """Canonical caller schedule; immediate dispatch stays ``None`` across replays."""

    return {
        "due_at": due_at.isoformat() if due_at is not None else None,
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
        "pacing_json": dict(pacing_json),
    }


def _attempt_proves_no_effect(attempt: DurableActionAttempt) -> bool:
    """Retry/cancel only exact receipts that deny every provider-side effect."""

    if attempt.state not in {
        DurableActionAttemptStatus.FAILED,
        DurableActionAttemptStatus.DEFERRED,
    }:
        return False
    receipt = attempt.receipt_json
    return (
        isinstance(receipt, dict)
        and receipt.get("provider_executed") is False
        and receipt.get("retry_safe") is True
        and not _receipt_has_effect_evidence(receipt)
        and not attempt.progress_json
        and attempt.provider_sending_id is None
        and attempt.temporary_message_ref is None
        and attempt.final_message_ref is None
    )


_PROVIDER_EFFECT_RECEIPT_KEYS = frozenset(
    {
        "confirmed_message_refs",
        "final_message_ref",
        "final_message_refs",
        "message_id",
        "message_ids",
        "message_ref",
        "message_refs",
        "messages",
        "provider_receipts",
        "provider_sending_id",
        "provider_sending_ids",
        "temporary_message_id",
        "temporary_message_ids",
        "temporary_message_ref",
        "temporary_message_refs",
    }
)


def _receipt_has_effect_evidence(value: Any) -> bool:
    """Reject an asserted no-effect receipt that carries a provider receipt."""

    if isinstance(value, list):
        return any(_receipt_has_effect_evidence(item) for item in value)
    if not isinstance(value, dict):
        return False
    for key, nested in value.items():
        if key in _PROVIDER_EFFECT_RECEIPT_KEYS and nested not in (None, "", [], {}):
            return True
        if _receipt_has_effect_evidence(nested):
            return True
    return False


def _normalize_pacing(value: dict[str, Any] | None) -> dict[str, float]:
    raw = value or {}
    if not isinstance(raw, dict):
        raise ValidationError("durable action pacing must be an object")
    normalized: dict[str, float] = {}
    for key in ("account_interval_seconds", "destination_interval_seconds"):
        candidate = raw.get(key, 1.0)
        if isinstance(candidate, bool) or not isinstance(candidate, int | float) or candidate <= 0:
            raise ValidationError(f"durable action {key} must be a positive number")
        normalized[key] = float(candidate)
    return normalized


def _merge_progress_json(
    previous: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    """Accumulate generic receipt fragments without replacing earlier evidence."""

    merged = dict(previous or {})
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_progress_json(existing, value)
        else:
            merged[key] = value
    return merged


def _digest_snapshot(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("durable action input must be JSON serializable") from exc
    return hashlib.sha256(raw.encode()).hexdigest()


def _destination_scope(destination_ref: str) -> str:
    return f"destination:{destination_ref.strip()}"


def _is_active_lease(row: ActionDeliveryAdmission, now: datetime) -> bool:
    return (
        row.lease_ref is not None
        and row.lease_expires_at is not None
        and row.lease_expires_at > now
    )


def _lease_or_eligible(row: ActionDeliveryAdmission, now: datetime) -> datetime:
    return (
        row.lease_expires_at
        if _is_active_lease(row, now) and row.lease_expires_at
        else row.next_eligible_at
    )


def _as_naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("persisted durable action record has no id")
    return value


def _job_phase(state: DurableActionJobStatus) -> str:
    return "completed" if state == DurableActionJobStatus.COMPLETED else state.value


def _terminal_response(*, state: str, counts: _DurableCounts) -> dict[str, Any]:
    return {
        "status": state,
        "total_count": counts.total,
        "completed_count": counts.completed,
        "failed_count": counts.failed,
        "unknown_count": counts.unknown,
        "cancelled_count": counts.cancelled,
    }


__all__ = [
    "AccountDeliveryQuiesceOut",
    "DeliveryAdmissionOut",
    "DurableActionItemOut",
    "DurableActionJobOut",
    "DurableActionMixin",
    "DurableRecoveryOut",
    "durable_action_schedule_snapshot",
]
