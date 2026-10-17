"""Runs and idempotency-keys repository module.

Implements the audit-trail layer plus crash-recovery (audit B-13) and
idempotency (audit M-20). Run plans drive ``RunRepository`` rows, while
``RunStepRepository`` / ``RunStepCallRepository`` keep audit grain for
agent and tool activity.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from stackos import controlled_process_receipt as _receipt
from stackos.artifacts import redact_secrets
from stackos.db.model_enums import (
    RUN_ATTEMPT_DISPOSITION_STATUS,
    RUN_OBSERVATION_TRANSITIONS,
    RunAttemptDisposition,
    RunObservationState,
)
from stackos.db.models import (
    RUN_STATUS_TRANSITIONS,
    IdempotencyKey,
    Run,
    RunKind,
    RunStatus,
    RunStep,
    RunStepCall,
    RunStepStatus,
    TrackerItemStatus,
    TrackerTask,
    TrackerTicket,
    WorkItemOwnership,
)
from stackos.events import StackOSEvent, StackOSEventSource
from stackos.repositories.base import (
    ConflictError,
    Envelope,
    IdempotencyReplayError,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
    validate_transition,
)
from stackos.repositories.project_events import StackOSEventEmitter


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Output models.
# ---------------------------------------------------------------------------


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    kind: RunKind
    parent_run_id: int | None
    client_session_id: str | None
    started_at: datetime
    ended_at: datetime | None
    status: RunStatus
    error: str | None
    heartbeat_at: datetime | None
    last_step: str | None
    last_step_at: datetime | None
    metadata_json: dict[str, Any] | None


class RunStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    step_index: int
    skill_name: str
    started_at: datetime | None
    ended_at: datetime | None
    status: RunStepStatus
    input_snapshot_json: dict[str, Any] | None
    output_snapshot_json: dict[str, Any] | None
    error: str | None
    cost_cents: int
    integration_calls_json: dict[str, Any] | None


class RunStepCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_step_id: int
    mcp_tool: str
    request_json: dict[str, Any] | None
    response_json: dict[str, Any] | None
    duration_ms: int | None
    error: str | None
    cost_cents: int


class IdempotencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    tool_name: str
    idempotency_key: str
    run_id: int | None
    response_json: dict[str, Any] | None
    created_at: datetime


class WorkItemOwnershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    canonical_item_id: str
    owner_ref: str | None
    previous_owner_ref: str | None
    epoch: int
    revision: int
    scope_digest: str
    checkpoint_revision: int
    checkpoint_ref: str | None
    continuity_json: dict[str, Any]
    quiescence_ref: str | None
    quiescence_revision: int | None


class OwnershipAcquisitionOut(BaseModel):
    acquired: bool
    ownership: WorkItemOwnershipOut


class OwnershipAttemptOut(BaseModel):
    ownership: WorkItemOwnershipOut
    attempt: RunOut


class _LaunchReservationOut(OwnershipAttemptOut):
    launch_permit: bool


class _LaunchReceiptOut(OwnershipAttemptOut):
    recorded: bool


type _SlotFailureReason = Literal[
    "unsupported_fs",
    "unsafe_directory",
    "unsafe_entry",
    "entry_changed",
    "invalid_key",
    "invalid_schema",
    "invalid_json",
    "noncanonical",
    "authentication_failed",
    "binding_mismatch",
    "io_limit",
    "io_failure",
    "conflict",
    "admission_rejected",
    "state_conflict",
    "context_lost",
    "io_outcome_unconfirmed",
    "close_unconfirmed",
    "canonical_ack_unconfirmed",
]
_SLOT_CONFIRMED = frozenset(
    (
        "unsupported_fs",
        "unsafe_directory",
        "unsafe_entry",
        "entry_changed",
        "invalid_key",
        "invalid_schema",
        "invalid_json",
        "noncanonical",
        "authentication_failed",
        "binding_mismatch",
        "io_limit",
        "io_failure",
        "conflict",
        "admission_rejected",
        "state_conflict",
    )
)
_SLOT_UNKNOWN = frozenset(
    (
        "context_lost",
        "io_outcome_unconfirmed",
        "close_unconfirmed",
        "canonical_ack_unconfirmed",
    )
)


class _SlotCommon(TypedDict):
    schema_version: Literal[1]
    slot_revision: int
    binding: dict[str, object]
    binding_digest: str
    name: str
    allocation_reservation_revision: int
    evidence_ref: str


class _SlotAllocation(TypedDict):
    dalloc: list[int]
    fempty: list[int]
    status_flags: int
    offset: int


class _SlotClaim(TypedDict):
    sealed_digest: str


class _PendingSlot(_SlotCommon):
    phase: Literal["ALLOCATION_PENDING"]


class _ReadySlot(_SlotCommon, _SlotAllocation):
    phase: Literal["ALLOCATED"]


class _ClaimedSlot(_SlotCommon, _SlotAllocation, _SlotClaim):
    phase: Literal["CLAIMED"]


class _CommittedSlot(_SlotCommon, _SlotAllocation, _SlotClaim):
    phase: Literal["COMMITTED"]
    receipt_digest: str


class _SlotHoldFields(TypedDict):
    phase: Literal["FAILED_HOLD", "UNKNOWN_HOLD"]
    hold_reason: _SlotFailureReason


class _PendingSlotHold(_SlotCommon, _SlotHoldFields):
    hold_from: Literal["ALLOCATION_PENDING"]


class _ReadySlotHold(_SlotCommon, _SlotAllocation, _SlotHoldFields):
    hold_from: Literal["ALLOCATED"]


class _ClaimedSlotHold(_SlotCommon, _SlotAllocation, _SlotClaim, _SlotHoldFields):
    hold_from: Literal["CLAIMED"]


type _ReceiptSlotRecord = (
    _PendingSlot
    | _ReadySlot
    | _ClaimedSlot
    | _CommittedSlot
    | _PendingSlotHold
    | _ReadySlotHold
    | _ClaimedSlotHold
)


@dataclass(frozen=True)
class _SlotTransitionOut:
    applied: bool
    slot: _ReceiptSlotRecord
    ownership: WorkItemOwnershipOut
    attempt: RunOut


@dataclass(frozen=True)
class _SlotFence:
    project_id: int
    canonical_item_id: str
    owner_ref: str
    scope_digest: str
    expected_epoch: int
    expected_revision: int
    controller_run_id: int
    intent_ref: str
    manifest_digest: str
    attempt_run_id: int
    expected_slot_revision: int
    evidence_ref: str


def _slot_evidence(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 1000 and bool(value.strip())


def _slot_binding(value: object) -> _receipt._Binding:
    if type(value) is not dict or value.keys() != {f.name for f in fields(_receipt._Binding)}:
        raise ValidationError("invalid private receipt slot binding")
    binding = _receipt._Binding(**value)
    if not _receipt._valid_binding(binding):
        raise ValidationError("invalid private receipt slot binding")
    return binding


def _slot_vector(value: object, length: int, *, native: bool = False) -> list[int]:
    expected = tuple if native else list
    if type(value) is not expected or not isinstance(value, list | tuple):
        raise ValidationError("invalid private receipt slot fingerprint")
    if len(value) != length or any(type(part) is not int for part in value):
        raise ValidationError("invalid private receipt slot fingerprint")
    return list(value)


def _slot_allocation_facts(
    directory: object,
    empty_file: object,
    status_flags: object,
    offset: object,
    *,
    native: bool = False,
) -> dict[str, object]:
    """Validate exact supplied facts before any equality/readback shortcut."""
    dalloc = _slot_vector(directory, 6, native=native)
    fempty = _slot_vector(empty_file, 9, native=native)
    if (
        not stat.S_ISDIR(dalloc[4])
        or stat.S_IMODE(dalloc[4]) != 0o700
        or not stat.S_ISREG(fempty[4])
        or stat.S_IMODE(fempty[4]) != 0o600
        or fempty[5] != 1
        or fempty[6] != 0
        or dalloc[2] != fempty[2]
        or type(status_flags) is not int
        or status_flags < 0
        or status_flags & os.O_ACCMODE != os.O_WRONLY
        or status_flags & os.O_APPEND
        or type(offset) is not int
        or offset != 0
    ):
        raise ValidationError("invalid private receipt slot allocation facts")
    return {"dalloc": dalloc, "fempty": fempty, "status_flags": status_flags, "offset": offset}


def _parse_receipt_slot(value: object) -> _ReceiptSlotRecord:
    """Exact phase schema; this is data validation, never physical custody proof."""
    if type(value) is not dict:
        raise ValidationError("invalid private receipt slot record")
    common = {
        "schema_version",
        "phase",
        "slot_revision",
        "binding",
        "binding_digest",
        "name",
        "allocation_reservation_revision",
        "evidence_ref",
    }
    phase = value.get("phase")
    if type(phase) is not str:
        raise ValidationError("invalid private receipt slot phase")
    origin = value.get("hold_from") if phase in {"FAILED_HOLD", "UNKNOWN_HOLD"} else phase
    if type(origin) is not str:
        raise ValidationError("invalid private receipt slot hold origin")
    expected = set(common)
    if origin in {"ALLOCATED", "CLAIMED", "COMMITTED"}:
        expected.update(("dalloc", "fempty", "status_flags", "offset"))
    if origin in {"CLAIMED", "COMMITTED"}:
        expected.add("sealed_digest")
    if phase == "COMMITTED":
        expected.add("receipt_digest")
    elif phase in {"FAILED_HOLD", "UNKNOWN_HOLD"}:
        if origin not in {"ALLOCATION_PENDING", "ALLOCATED", "CLAIMED"}:
            raise ValidationError("invalid private receipt slot hold origin")
        expected.update(("hold_from", "hold_reason"))
        reason = value.get("hold_reason")
        allowed = _SLOT_UNKNOWN if phase == "UNKNOWN_HOLD" else _SLOT_CONFIRMED
        if type(reason) is not str or reason not in allowed:
            raise ValidationError("invalid private receipt slot hold reason")
    elif phase not in {"ALLOCATION_PENDING", "ALLOCATED", "CLAIMED"}:
        raise ValidationError("invalid private receipt slot phase")
    if value.keys() != expected:
        raise ValidationError("private receipt slot has missing or extra fields")
    binding = _slot_binding(value["binding"])
    name = _receipt._name(binding)
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or not _receipt._integer(value["slot_revision"])
        or type(value["name"]) is not str
        or value["name"] != name
        or type(value["binding_digest"]) is not str
        or value["binding_digest"] != name[:-8]
        or not _receipt._integer(value["allocation_reservation_revision"])
        or value["allocation_reservation_revision"] != binding.reservation_revision
        or not _slot_evidence(value["evidence_ref"])
    ):
        raise ValidationError("invalid private receipt slot common fields")
    if "dalloc" in expected:
        _slot_allocation_facts(
            value["dalloc"], value["fempty"], value["status_flags"], value["offset"]
        )
    if "sealed_digest" in expected and not _receipt._hex(value["sealed_digest"]):
        raise ValidationError("invalid private receipt slot claim digest")
    if phase == "COMMITTED" and (
        not _receipt._hex(value["receipt_digest"])
        or value["receipt_digest"] != value["sealed_digest"]
    ):
        raise ValidationError("invalid private receipt slot committed digest")
    # Checked cast: exact key set, every field type, literal phase/origin and
    # phase-specific invariants above establish the complete TypedDict union.
    return cast(_ReceiptSlotRecord, deepcopy(value))


# ---------------------------------------------------------------------------
# RunRepository.
# ---------------------------------------------------------------------------


class RunRepository:
    """Top-level run audit + crash-recovery sweep."""

    def __init__(
        self,
        session: Session,
        *,
        audit_run_id: int | None = None,
        owner_commit_guard: Callable[[], None] | None = None,
    ) -> None:
        self._s = session
        self._audit_run_id = audit_run_id
        self._owner_commit_guard = owner_commit_guard

    def owner_get(self, *, project_id: int, canonical_item_id: str) -> WorkItemOwnershipOut:
        return WorkItemOwnershipOut.model_validate(
            self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        )

    def owner_acquire(
        self,
        *,
        project_id: int,
        owner_ref: str,
        scope_digest: str,
        canonical_item_id: str | None = None,
        alias_ref: str | None = None,
    ) -> OwnershipAcquisitionOut:
        """Reserve once; repeated triggers return the same assignment without dispatch."""
        from stackos.repositories.tracker import TrackerRepository

        self._validate_owner_ref(owner_ref)
        item = TrackerRepository(self._s).resolve_owner_item(
            project_id=project_id, canonical_item_id=canonical_item_id, alias_ref=alias_ref
        )
        row = self._s.exec(
            select(WorkItemOwnership)
            .where(
                WorkItemOwnership.project_id == project_id,
                WorkItemOwnership.canonical_item_id == item.canonical_item_id,
            )
            .execution_options(populate_existing=True)
        ).first()
        if row is not None:
            self._owner_admissible(row, scope_digest)
            if row.owner_ref == owner_ref:
                return OwnershipAcquisitionOut(
                    acquired=False, ownership=WorkItemOwnershipOut.model_validate(row)
                )
            if row.owner_ref is None and row.quiescence_ref:
                reassigned = self._owner_cas(
                    row,
                    owner_ref=None,
                    expected_epoch=row.epoch,
                    expected_revision=row.revision,
                    changes={
                        "owner_ref": owner_ref,
                        "epoch": WorkItemOwnership.epoch + 1,
                        "quiescence_revision": None,
                    },
                    event_type="WORK_ITEM_OWNER_ACQUIRED",
                    admission_scope_digest=scope_digest,
                )
                return OwnershipAcquisitionOut(acquired=True, ownership=reassigned)
            raise ConflictError("work item already has an owner; explicit reassignment required")
        native = (
            self._s.get(TrackerTicket, item.item_id, populate_existing=True)
            if item.item_kind == "ticket"
            else self._s.get(TrackerTask, item.item_id, populate_existing=True)
        )
        assert native is not None
        continuity = (native.metadata_json or {}).get("ownership_continuity")
        if isinstance(continuity, dict) and "launch_reservations" in continuity:
            raise ValidationError("launch reservations are derived by the private run owner")
        if not isinstance(continuity, dict) or any(
            not isinstance(continuity.get(key), str)
            or not continuity[key].strip()
            or len(continuity[key]) > 1000
            for key in ("budget_ref", "stop_scope_ref", "history_ref")
        ):
            raise ValidationError(
                "canonical work item needs corroborated ownership_continuity refs; "
                "missing history is not a fresh budget"
            )
        if (
            continuity.get("scope_digest") != scope_digest
            or type(continuity.get("stopped")) is not bool
            or type(continuity.get("denied")) is not bool
            or any(
                type(continuity.get(key)) is not int or continuity[key] < 0
                for key in ("attempts_used", "failures_used")
            )
        ):
            raise ValidationError("ownership continuity needs known scope, stops and counters")
        row = WorkItemOwnership(
            project_id=project_id,
            canonical_item_id=item.canonical_item_id,
            owner_ref=owner_ref,
            scope_digest=scope_digest,
            continuity_json=redact_secrets(deepcopy(continuity)),
        )
        self._owner_admissible(row, scope_digest)
        try:
            self._s.add(row)
            self._s.flush()
            ownership = self._owner_commit(row, "WORK_ITEM_OWNER_ACQUIRED")
        except IntegrityError as exc:
            self._s.rollback()
            raise ConflictError(
                "ownership acquisition raced; read the existing assignment"
            ) from exc
        except Exception:
            self._s.rollback()
            raise
        return OwnershipAcquisitionOut(acquired=True, ownership=ownership)

    def owner_checkpoint(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        checkpoint_ref: str,
    ) -> WorkItemOwnershipOut:
        self._validate_owner_ref(owner_ref)
        if not checkpoint_ref or len(checkpoint_ref) > 1000:
            raise ValidationError("checkpoint_ref must be a bounded evidence reference")
        row = self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        self._owner_admissible(row, scope_digest)
        return self._owner_cas(
            row,
            owner_ref=owner_ref,
            expected_epoch=expected_epoch,
            expected_revision=expected_revision,
            changes={
                "checkpoint_ref": checkpoint_ref,
                "checkpoint_revision": WorkItemOwnership.checkpoint_revision + 1,
                "quiescence_revision": None,
            },
            event_type="WORK_ITEM_OWNER_CHECKPOINTED",
            admission_scope_digest=scope_digest,
        )

    def owner_reassign(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        new_owner_ref: str | None,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
    ) -> WorkItemOwnershipOut:
        """Explicit reassignment/revocation needs a current backend-certified receipt."""
        self._validate_owner_ref(owner_ref)
        if new_owner_ref is not None:
            self._validate_owner_ref(new_owner_ref)
        if new_owner_ref == owner_ref:
            raise ValidationError("replacement owner must differ from the current owner")
        row = self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        if row.scope_digest != scope_digest:
            raise ConflictError("ownership scope changed; adjudication required")
        if row.quiescence_revision != row.revision or not row.quiescence_ref:
            raise ConflictError("current quiescence and effect-disposition evidence required")
        # Stopped scopes may be fenced/revoked but cannot admit another owner.
        if new_owner_ref is not None:
            self._owner_admissible(row, scope_digest)
        return self._owner_cas(
            row,
            owner_ref=owner_ref,
            expected_epoch=expected_epoch,
            expected_revision=expected_revision,
            require_quiescence=True,
            changes={
                "previous_owner_ref": owner_ref,
                "owner_ref": new_owner_ref,
                "epoch": WorkItemOwnership.epoch + 1,
                "quiescence_revision": None,
            },
            event_type="WORK_ITEM_OWNER_REASSIGNED",
            admission_scope_digest=scope_digest if new_owner_ref is not None else None,
        )

    def owner_stop(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        expected_epoch: int,
        expected_revision: int,
        reason_ref: str,
    ) -> WorkItemOwnershipOut:
        """Persist a stop without clearing counts, authority or the assigned owner."""
        self._validate_owner_ref(owner_ref)
        if not reason_ref or len(reason_ref) > 1000:
            raise ValidationError("bounded stop evidence reference required")
        row = self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        continuity = deepcopy(row.continuity_json)
        continuity["stopped"] = True
        continuity["stop_reason_ref"] = reason_ref
        return self._owner_cas(
            row,
            owner_ref=owner_ref,
            expected_epoch=expected_epoch,
            expected_revision=expected_revision,
            changes={"continuity_json": continuity, "quiescence_revision": None},
            event_type="WORK_ITEM_OWNER_STOPPED",
        )

    def _certify_owner_quiescence(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        expected_epoch: int,
        expected_revision: int,
        evidence_ref: str,
    ) -> WorkItemOwnershipOut:
        """Trusted host integration only; NEVER expose as an agent operation.

        The runtime must independently verify owned-process quiescence and settle
        effects before calling. A worker string/boolean/claimed receipt is not
        that verification. MYB-95 supplies and proves this producer.
        """
        if not evidence_ref or len(evidence_ref) > 1000:
            raise ValidationError("quiescence evidence reference required")
        row = self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        return self._owner_cas(
            row,
            owner_ref=owner_ref,
            expected_epoch=expected_epoch,
            expected_revision=expected_revision,
            changes={
                "quiescence_ref": evidence_ref,
                "quiescence_revision": WorkItemOwnership.revision + 1,
            },
            event_type="WORK_ITEM_OWNER_QUIESCENCE_CERTIFIED",
        )

    def owner_authority(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        ownership: WorkItemOwnershipOut | None = None,
        allow_stopped: bool = False,
        require_budget: bool = False,
    ) -> dict[str, Any]:
        """Read current canonical continuity, never caller-supplied replacement policy."""
        from stackos.repositories.tracker import TrackerRepository

        item = TrackerRepository(self._s).resolve_owner_item(
            project_id=project_id, canonical_item_id=canonical_item_id
        )
        native = (
            self._s.get(TrackerTicket, item.item_id, populate_existing=True)
            if item.item_kind == "ticket"
            else self._s.get(TrackerTask, item.item_id, populate_existing=True)
        )
        assert native is not None
        current = (native.metadata_json or {}).get("ownership_continuity")
        if not isinstance(current, dict):
            raise ConflictError("current canonical continuity is unknown; adjudication required")
        if "launch_reservations" in current:
            raise ConflictError("canonical metadata cannot supply private launch reservations")
        if any(type(current.get(key)) is not bool for key in ("stopped", "denied")) or any(
            type(current.get(key)) is not int or current[key] < 0
            for key in ("attempts_used", "failures_used")
        ):
            raise ConflictError("current stop/counter authority is unknown")
        if ownership is not None:
            for key in (
                "scope_digest",
                "budget_ref",
                "stop_scope_ref",
                "history_ref",
                "effect_identity",
                "deadline_ref",
                "attempt_limit",
                "failure_limit",
            ):
                if current.get(key) != ownership.continuity_json.get(key):
                    raise ConflictError(
                        "canonical continuity contract changed; adjudication required",
                        data={"field": key, "canonical_item_id": canonical_item_id},
                    )
        if require_budget and any(
            type(current.get(key)) is not int or current[key] < 1
            for key in ("attempt_limit", "failure_limit")
        ):
            raise ConflictError("current cumulative budget limits are unknown")
        stored = ownership.continuity_json if ownership is not None else current
        if not allow_stopped and any(
            source.get("stopped") or source.get("denied") for source in (stored, current)
        ):
            raise ConflictError("original/current stop or denial remains binding")
        return redact_secrets(deepcopy(current))

    def owner_consume(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        attempts_delta: int,
        failures_delta: int,
        evidence_ref: str,
    ) -> WorkItemOwnershipOut:
        """Monotone accounting; no caller can rewrite limits, refs or clear a stop."""
        if any(type(value) is not int or value < 0 for value in (attempts_delta, failures_delta)):
            raise ValidationError("consumption deltas must be nonnegative integers")
        if not evidence_ref or len(evidence_ref) > 1000:
            raise ValidationError("bounded consumption evidence reference required")
        row = self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        continuity = self._owner_consumption_values(
            ownership=WorkItemOwnershipOut.model_validate(row),
            scope_digest=scope_digest,
            attempts_delta=attempts_delta,
            failures_delta=failures_delta,
            evidence_ref=evidence_ref,
        )
        return self._owner_cas(
            row,
            owner_ref=owner_ref,
            expected_epoch=expected_epoch,
            expected_revision=expected_revision,
            changes={"continuity_json": continuity, "quiescence_revision": None},
            event_type="WORK_ITEM_OWNER_CONSUMPTION_RECORDED",
        )

    def _owner_consumption_values(
        self,
        *,
        ownership: WorkItemOwnershipOut,
        scope_digest: str,
        attempts_delta: int,
        failures_delta: int,
        evidence_ref: str,
    ) -> dict[str, Any]:
        """Calculate accounting without committing, for the existing writer transaction."""
        if ownership.scope_digest != scope_digest:
            raise ConflictError("ownership scope changed; adjudication required")
        current = self.owner_authority(
            project_id=ownership.project_id,
            canonical_item_id=ownership.canonical_item_id,
            ownership=ownership,
            allow_stopped=True,
            require_budget=True,
        )
        continuity = deepcopy(ownership.continuity_json)
        stopped = bool(continuity["stopped"] or current["stopped"])
        denied = bool(continuity["denied"] or current["denied"])
        attempts = max(continuity["attempts_used"], current["attempts_used"])
        failures = max(continuity["failures_used"], current["failures_used"]) + failures_delta
        if attempts_delta and (
            stopped
            or denied
            or attempts + attempts_delta > current["attempt_limit"]
            or failures >= current["failure_limit"]
        ):
            raise ConflictError("cumulative stop/budget cannot admit another attempt")
        continuity.update(
            attempts_used=attempts + attempts_delta,
            failures_used=failures,
            stopped=stopped or failures >= current["failure_limit"],
            denied=denied,
            consumption_evidence_ref=evidence_ref,
        )
        if failures >= current["failure_limit"] and not continuity.get("stop_reason_ref"):
            continuity["stop_reason_ref"] = evidence_ref
        return continuity

    def _require_launch_controller(self, project_id: int, controller_run_id: int) -> None:
        if (
            self._owner_commit_guard is None
            or type(controller_run_id) is not int
            or self._audit_run_id != controller_run_id
        ):
            raise ConflictError("private launch admission requires the trusted controller guard")
        controller = self._s.get(Run, controller_run_id, populate_existing=True)
        if (
            controller is None
            or controller.project_id != project_id
            or controller.kind != RunKind.RUN_PLAN
            or controller.status != RunStatus.RUNNING
        ):
            raise ConflictError("launch controller is not current in this project")

    def _launch_owner(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
    ) -> WorkItemOwnership:
        self._validate_owner_ref(owner_ref)
        self._require_launch_controller(project_id, controller_run_id)
        row = self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        if (
            type(expected_epoch) is not int
            or type(expected_revision) is not int
            or row.epoch != expected_epoch
            or row.revision != expected_revision
            or row.owner_ref != owner_ref
        ):
            raise ConflictError("stale ownership fence")
        self._owner_admissible(row, scope_digest)
        self.owner_authority(
            project_id=project_id,
            canonical_item_id=canonical_item_id,
            ownership=WorkItemOwnershipOut.model_validate(row),
            require_budget=True,
        )
        return row

    @staticmethod
    def _validate_launch_refs(intent_ref: str, manifest_digest: str, evidence_ref: str) -> None:
        if not isinstance(intent_ref, str) or not intent_ref.strip() or len(intent_ref) > 160:
            raise ValidationError("bounded stable launch intent required")
        if (
            not isinstance(evidence_ref, str)
            or not evidence_ref.strip()
            or len(evidence_ref) > 1000
        ):
            raise ValidationError("bounded launch evidence reference required")
        if (
            not isinstance(manifest_digest, str)
            or len(manifest_digest) != 64
            or any(c not in "0123456789abcdef" for c in manifest_digest)
        ):
            raise ValidationError("launch digest must be SHA-256")

    def _launch_attempt(
        self,
        row: WorkItemOwnership,
        entry: Any,
        *,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
    ) -> Run:
        if not isinstance(entry, dict) or type(entry.get("attempt_run_id")) is not int:
            raise ConflictError("launch reservation is unknown; reconcile original intent")
        binding = dict(
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            epoch=row.epoch,
            scope_digest=row.scope_digest,
        )
        if entry != dict(
            attempt_run_id=entry["attempt_run_id"],
            controller_run_id=controller_run_id,
            owner_ref=row.owner_ref,
            epoch=row.epoch,
            scope_digest=row.scope_digest,
            manifest_digest=manifest_digest,
        ):
            raise ConflictError("stable launch intent has a different binding; no relaunch")
        attempt = self._s.get(Run, entry["attempt_run_id"], populate_existing=True)
        metadata = (attempt.metadata_json or {}) if attempt else {}
        if (
            attempt is None
            or attempt.project_id != row.project_id
            or attempt.parent_run_id != controller_run_id
            or attempt.kind != RunKind.SKILL_RUN
            or metadata.get("ownership_binding") != binding
            or metadata.get("launch_reservation")
            != dict(intent_ref=intent_ref, manifest_digest=manifest_digest)
        ):
            raise ConflictError("attempt does not match the reserved launch binding")
        if attempt.status != RunStatus.RUNNING:
            raise ConflictError("terminal launch attempt cannot admit or replay a receipt")
        return attempt

    def _reserve_owner_launch(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
        evidence_ref: str,
    ) -> _LaunchReservationOut:
        """Private accounting seam; no process, barrier release or execution authority.

        A future trusted host must verify the admitted manifest and supply the
        existing current step/grant commit guard. This source slice has no host consumer.
        """
        self._validate_launch_refs(intent_ref, manifest_digest, evidence_ref)
        row = self._launch_owner(
            project_id=project_id,
            canonical_item_id=canonical_item_id,
            owner_ref=owner_ref,
            scope_digest=scope_digest,
            expected_epoch=expected_epoch,
            expected_revision=expected_revision,
            controller_run_id=controller_run_id,
        )
        ledger = deepcopy(row.continuity_json.get("launch_reservations", {}))
        if not isinstance(ledger, dict):
            raise ConflictError("launch reservation ledger is unknown")
        if intent_ref in ledger:
            attempt: Run | None = self._launch_attempt(
                row,
                ledger[intent_ref],
                controller_run_id=controller_run_id,
                intent_ref=intent_ref,
                manifest_digest=manifest_digest,
            )
            assert self._owner_commit_guard is not None
            self._owner_commit_guard()
            return _LaunchReservationOut(
                launch_permit=False,
                ownership=WorkItemOwnershipOut.model_validate(row),
                attempt=RunOut.model_validate(attempt),
            )
        before = WorkItemOwnershipOut.model_validate(row)
        accounting = self._owner_consumption_values(
            ownership=before,
            scope_digest=scope_digest,
            attempts_delta=1,
            failures_delta=0,
            evidence_ref=evidence_ref,
        )
        try:
            child = self.start(
                project_id=project_id,
                kind=RunKind.SKILL_RUN,
                parent_run_id=controller_run_id,
                metadata_json={
                    "ownership_binding": dict(
                        canonical_item_id=canonical_item_id,
                        owner_ref=owner_ref,
                        epoch=expected_epoch,
                        scope_digest=scope_digest,
                    )
                },
                _commit=False,
            ).data
            attempt = self._s.get(Run, child.id)
            assert attempt is not None
            attempt.metadata_json = dict(attempt.metadata_json or {}) | {
                "launch_reservation": dict(intent_ref=intent_ref, manifest_digest=manifest_digest)
            }
            self._s.add(attempt)
            ledger[intent_ref] = dict(
                attempt_run_id=child.id,
                controller_run_id=controller_run_id,
                owner_ref=owner_ref,
                epoch=expected_epoch,
                scope_digest=scope_digest,
                manifest_digest=manifest_digest,
            )
            continuity = deepcopy(accounting) | {"launch_reservations": ledger}

            def recheck() -> None:
                self._require_launch_controller(project_id, controller_run_id)
                current = self._owner_consumption_values(
                    ownership=before,
                    scope_digest=scope_digest,
                    attempts_delta=1,
                    failures_delta=0,
                    evidence_ref=evidence_ref,
                )
                if current != accounting:
                    raise ConflictError(
                        "canonical accounting changed before launch reservation commit"
                    )

            ownership = self._owner_cas(
                row,
                owner_ref=owner_ref,
                expected_epoch=expected_epoch,
                expected_revision=expected_revision,
                changes={"continuity_json": continuity, "quiescence_revision": None},
                event_type="WORK_ITEM_OWNER_LAUNCH_RESERVED",
                admission_scope_digest=scope_digest,
                before_commit=recheck,
                event_metadata={"intent_ref": intent_ref, "attempt_run_id": child.id},
            )
            return _LaunchReservationOut(
                launch_permit=True, ownership=ownership, attempt=RunOut.model_validate(attempt)
            )
        except Exception:
            self._s.rollback()
            raise

    def _record_owner_launch_receipt(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
        evidence_ref: str,
        attempt_run_id: int,
        receipt_digest: str,
    ) -> _LaunchReceiptOut:
        """Retain a caller-verified nonterminal receipt fingerprint; never certify cleanup."""
        self._validate_launch_refs(intent_ref, manifest_digest, evidence_ref)
        self._validate_launch_refs(intent_ref, receipt_digest, evidence_ref)
        row = self._launch_owner(
            project_id=project_id,
            canonical_item_id=canonical_item_id,
            owner_ref=owner_ref,
            scope_digest=scope_digest,
            expected_epoch=expected_epoch,
            expected_revision=expected_revision,
            controller_run_id=controller_run_id,
        )
        ledger = row.continuity_json.get("launch_reservations", {})
        if not isinstance(ledger, dict):
            raise ConflictError("launch reservation ledger is unknown")
        attempt = self._launch_attempt(
            row,
            ledger.get(intent_ref),
            controller_run_id=controller_run_id,
            intent_ref=intent_ref,
            manifest_digest=manifest_digest,
        )
        if type(attempt_run_id) is not int or attempt.id != attempt_run_id:
            raise ConflictError("receipt names a different attempt")
        fingerprint = dict(digest=receipt_digest, evidence_ref=evidence_ref)
        metadata = deepcopy(attempt.metadata_json or {})
        if "receipt_slot" in metadata:
            raise ConflictError("private receipt slot must use its atomic commit owner")
        if "launch_receipt" in metadata:
            if metadata["launch_receipt"] != fingerprint:
                raise ConflictError("contradictory launch receipt; reconcile original facts")
            assert self._owner_commit_guard is not None
            self._owner_commit_guard()
            return _LaunchReceiptOut(
                recorded=False,
                ownership=WorkItemOwnershipOut.model_validate(row),
                attempt=RunOut.model_validate(attempt),
            )
        metadata["launch_receipt"] = fingerprint
        try:
            result = cast(
                CursorResult[Any],
                self._s.execute(
                    update(Run)
                    .where(
                        col(Run.id) == attempt.id,
                        col(Run.status) == RunStatus.RUNNING,
                        col(Run.metadata_json) == attempt.metadata_json,
                    )
                    .values(metadata_json=metadata)
                ),
            )
            if result.rowcount != 1:
                raise ConflictError("stale or terminal launch receipt")
            self._s.refresh(attempt)
            ownership = self._owner_cas(
                row,
                owner_ref=owner_ref,
                expected_epoch=expected_epoch,
                expected_revision=expected_revision,
                changes={"quiescence_revision": None},
                event_type="WORK_ITEM_OWNER_LAUNCH_RECEIPT_RECORDED",
                before_commit=lambda: self._require_launch_controller(
                    project_id, controller_run_id
                ),
                event_metadata={"intent_ref": intent_ref, "attempt_run_id": attempt.id},
            )
            return _LaunchReceiptOut(
                recorded=True, ownership=ownership, attempt=RunOut.model_validate(attempt)
            )
        except Exception:
            self._s.rollback()
            raise

    def _slot_context(
        self, fence: _SlotFence
    ) -> tuple[WorkItemOwnership, Run, dict[str, Any], _ReceiptSlotRecord | None]:
        if (
            any(
                not _receipt._integer(value)
                for value in (
                    fence.project_id,
                    fence.expected_epoch,
                    fence.expected_revision,
                    fence.controller_run_id,
                    fence.attempt_run_id,
                )
            )
            or not _receipt._integer(fence.expected_slot_revision, 0)
            or not _receipt._token(fence.owner_ref, 160)
            or not _receipt._token(fence.intent_ref, 128)
            or not _receipt._hex(fence.scope_digest)
            or not _receipt._hex(fence.manifest_digest)
            or type(fence.canonical_item_id) is not str
            or _receipt._ITEM.fullmatch(fence.canonical_item_id) is None
            or int(fence.canonical_item_id.split(":")[1]) > _receipt._MAX_INT
            or not _slot_evidence(fence.evidence_ref)
        ):
            raise ValidationError("invalid private receipt slot fence")
        try:
            row = self._launch_owner(
                project_id=fence.project_id,
                canonical_item_id=fence.canonical_item_id,
                owner_ref=fence.owner_ref,
                scope_digest=fence.scope_digest,
                expected_epoch=fence.expected_epoch,
                expected_revision=fence.expected_revision,
                controller_run_id=fence.controller_run_id,
            )
        except NotFoundError:
            raise ConflictError("private receipt slot canonical owner is missing") from None
        authority = self.owner_authority(
            project_id=fence.project_id,
            canonical_item_id=fence.canonical_item_id,
            ownership=WorkItemOwnershipOut.model_validate(row),
            require_budget=True,
        )
        for source in (row.continuity_json, authority):
            for used, limit in (
                ("attempts_used", "attempt_limit"),
                ("failures_used", "failure_limit"),
            ):
                if (
                    type(source.get(used)) is not int
                    or type(source.get(limit)) is not int
                    or source[used] < 0
                    or source[used] >= source[limit]
                ):
                    raise ConflictError("private receipt slot budget is exhausted or unknown")
        ledger = row.continuity_json.get("launch_reservations", {})
        if type(ledger) is not dict:
            raise ConflictError("private receipt slot reservation is unknown")
        attempt = self._launch_attempt(
            row,
            ledger.get(fence.intent_ref),
            controller_run_id=fence.controller_run_id,
            intent_ref=fence.intent_ref,
            manifest_digest=fence.manifest_digest,
        )
        if attempt.id != fence.attempt_run_id:
            raise ConflictError("private receipt slot names another attempt")
        metadata = deepcopy(attempt.metadata_json or {})
        slot = _parse_receipt_slot(metadata["receipt_slot"]) if "receipt_slot" in metadata else None
        if slot is not None:
            self._slot_check_binding(fence, _slot_binding(slot["binding"]))
        assert self._owner_commit_guard is not None
        self._owner_commit_guard()
        return row, attempt, metadata, slot

    @staticmethod
    def _slot_check_binding(fence: _SlotFence, binding: _receipt._Binding) -> None:
        if not _receipt._valid_binding(binding):
            raise ValidationError("invalid private receipt slot binding")
        if (
            binding.project_id != fence.project_id
            or binding.canonical_item_id != fence.canonical_item_id
            or binding.owner_ref != fence.owner_ref
            or binding.epoch != fence.expected_epoch
            or binding.scope_digest != fence.scope_digest
            or binding.controller_run_id != fence.controller_run_id
            or binding.attempt_run_id != fence.attempt_run_id
            or binding.intent_ref != fence.intent_ref
            or binding.manifest_digest != fence.manifest_digest
        ):
            raise ConflictError("private receipt slot binding disagrees with canonical attempt")

    @staticmethod
    def _slot_existing(fence: _SlotFence, slot: _ReceiptSlotRecord | None) -> _ReceiptSlotRecord:
        if slot is None or slot["slot_revision"] != fence.expected_slot_revision:
            raise ConflictError("stale or missing private receipt slot revision")
        return slot

    @staticmethod
    def _slot_readback(
        row: WorkItemOwnership, attempt: Run, slot: _ReceiptSlotRecord
    ) -> _SlotTransitionOut:
        return _SlotTransitionOut(
            False,
            deepcopy(slot),
            WorkItemOwnershipOut.model_validate(row).model_copy(deep=True),
            RunOut.model_validate(attempt).model_copy(deep=True),
        )

    def _slot_apply(
        self,
        fence: _SlotFence,
        row: WorkItemOwnership,
        attempt: Run,
        metadata: dict[str, Any],
        candidate: dict[str, object],
        *,
        receipt_digest: str | None = None,
    ) -> _SlotTransitionOut:
        previous = (
            _parse_receipt_slot(metadata["receipt_slot"]) if "receipt_slot" in metadata else None
        )
        revision = previous["slot_revision"] if previous is not None else 0
        if revision >= _receipt._MAX_INT:
            raise ConflictError("private receipt slot revision exhausted")
        candidate["slot_revision"] = revision + 1
        candidate["evidence_ref"] = fence.evidence_ref
        slot = _parse_receipt_slot(candidate)
        changed = deepcopy(metadata)
        changed["receipt_slot"] = slot
        if receipt_digest is not None:
            if "launch_receipt" in metadata:
                raise ConflictError("private receipt slot cannot replace an existing receipt")
            changed["launch_receipt"] = dict(digest=receipt_digest, evidence_ref=fence.evidence_ref)
        try:
            result = cast(
                CursorResult[Any],
                self._s.execute(
                    update(Run)
                    .where(
                        col(Run.id) == attempt.id,
                        col(Run.project_id) == fence.project_id,
                        col(Run.status) == RunStatus.RUNNING,
                        col(Run.metadata_json) == attempt.metadata_json,
                    )
                    .values(metadata_json=changed)
                ),
            )
            if result.rowcount != 1:
                raise ConflictError("stale or terminal private receipt slot")
            self._s.refresh(attempt)

            def recheck() -> None:
                self._require_launch_controller(fence.project_id, fence.controller_run_id)
                authority = self.owner_authority(
                    project_id=fence.project_id,
                    canonical_item_id=fence.canonical_item_id,
                    ownership=WorkItemOwnershipOut.model_validate(row),
                    require_budget=True,
                )
                for source in (row.continuity_json, authority):
                    for used, limit in (
                        ("attempts_used", "attempt_limit"),
                        ("failures_used", "failure_limit"),
                    ):
                        if (
                            type(source.get(used)) is not int
                            or type(source.get(limit)) is not int
                            or source[used] < 0
                            or source[used] >= source[limit]
                        ):
                            raise ConflictError("private receipt slot budget changed at commit")

            ownership = self._owner_cas(
                row,
                owner_ref=fence.owner_ref,
                expected_epoch=fence.expected_epoch,
                expected_revision=fence.expected_revision,
                changes={"quiescence_revision": None},
                event_type="WORK_ITEM_OWNER_RECEIPT_SLOT_TRANSITIONED",
                admission_scope_digest=fence.scope_digest,
                before_commit=recheck,
                event_metadata={
                    "attempt_run_id": attempt.id,
                    "intent_ref": fence.intent_ref,
                    "receipt_slot_before": deepcopy(previous),
                    "receipt_slot_after": deepcopy(slot),
                },
            )
            return _SlotTransitionOut(
                True,
                deepcopy(slot),
                ownership.model_copy(deep=True),
                RunOut.model_validate(attempt).model_copy(deep=True),
            )
        except Exception:
            self._s.rollback()
            raise

    def _reserve_receipt_slot(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
        attempt_run_id: int,
        expected_slot_revision: int,
        evidence_ref: str,
        binding: _receipt._Binding,
    ) -> _SlotTransitionOut:
        fence = _SlotFence(
            project_id,
            canonical_item_id,
            owner_ref,
            scope_digest,
            expected_epoch,
            expected_revision,
            controller_run_id,
            intent_ref,
            manifest_digest,
            attempt_run_id,
            expected_slot_revision,
            evidence_ref,
        )
        row, attempt, metadata, slot = self._slot_context(fence)
        self._slot_check_binding(fence, binding)
        if expected_slot_revision != 0:
            raise ConflictError("private receipt slot reserve requires absent revision zero")
        name = _receipt._name(binding)
        candidate: dict[str, object] = {
            "schema_version": 1,
            "phase": "ALLOCATION_PENDING",
            "slot_revision": 1,
            "binding": _receipt._table(binding),
            "binding_digest": name[:-8],
            "name": name,
            "allocation_reservation_revision": binding.reservation_revision,
            "evidence_ref": evidence_ref,
        }
        if slot is not None:
            if slot != candidate:
                raise ConflictError("private receipt slot already progressed or differs")
            return self._slot_readback(row, attempt, slot)
        if "launch_receipt" in metadata:
            raise ConflictError("legacy receipt cannot be adopted as a private slot")
        # The original reservation revision is an immutable binding fact, not
        # the changing current owner CAS revision or proof of live admission.
        return self._slot_apply(fence, row, attempt, metadata, candidate)

    def _record_receipt_slot_allocated(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
        attempt_run_id: int,
        expected_slot_revision: int,
        evidence_ref: str,
        dalloc: _receipt.D6,
        fempty: _receipt.F9,
        status_flags: int,
        offset: int,
    ) -> _SlotTransitionOut:
        fence = _SlotFence(
            project_id,
            canonical_item_id,
            owner_ref,
            scope_digest,
            expected_epoch,
            expected_revision,
            controller_run_id,
            intent_ref,
            manifest_digest,
            attempt_run_id,
            expected_slot_revision,
            evidence_ref,
        )
        row, attempt, metadata, current = self._slot_context(fence)
        slot = self._slot_existing(fence, current)
        facts = _slot_allocation_facts(dalloc, fempty, status_flags, offset, native=True)
        if slot["phase"] == "ALLOCATED":
            if (
                any(dict(slot)[key] != value for key, value in facts.items())
                or slot["evidence_ref"] != evidence_ref
            ):
                raise ConflictError("private receipt slot allocation facts differ")
            return self._slot_readback(row, attempt, slot)
        if slot["phase"] != "ALLOCATION_PENDING":
            raise ConflictError("private receipt slot cannot allocate after progress")
        candidate = dict(slot) | facts | {"phase": "ALLOCATED"}
        return self._slot_apply(fence, row, attempt, metadata, candidate)

    def _claim_receipt_slot(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
        attempt_run_id: int,
        expected_slot_revision: int,
        evidence_ref: str,
        sealed_digest: str,
    ) -> _SlotTransitionOut:
        fence = _SlotFence(
            project_id,
            canonical_item_id,
            owner_ref,
            scope_digest,
            expected_epoch,
            expected_revision,
            controller_run_id,
            intent_ref,
            manifest_digest,
            attempt_run_id,
            expected_slot_revision,
            evidence_ref,
        )
        if not _receipt._hex(sealed_digest):
            raise ValidationError("private receipt slot requires an exact digest")
        row, attempt, metadata, current = self._slot_context(fence)
        slot = self._slot_existing(fence, current)
        if slot["phase"] == "CLAIMED":
            if slot["sealed_digest"] != sealed_digest or slot["evidence_ref"] != evidence_ref:
                raise ConflictError("private receipt slot claim differs")
            return self._slot_readback(row, attempt, slot)
        if slot["phase"] != "ALLOCATED":
            raise ConflictError("private receipt slot cannot claim after progress")
        candidate = dict(slot) | {"phase": "CLAIMED", "sealed_digest": sealed_digest}
        return self._slot_apply(fence, row, attempt, metadata, candidate)

    def _commit_receipt_slot(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
        attempt_run_id: int,
        expected_slot_revision: int,
        evidence_ref: str,
        receipt_digest: str,
    ) -> _SlotTransitionOut:
        fence = _SlotFence(
            project_id,
            canonical_item_id,
            owner_ref,
            scope_digest,
            expected_epoch,
            expected_revision,
            controller_run_id,
            intent_ref,
            manifest_digest,
            attempt_run_id,
            expected_slot_revision,
            evidence_ref,
        )
        if not _receipt._hex(receipt_digest):
            raise ValidationError("private receipt slot requires an exact digest")
        row, attempt, metadata, current = self._slot_context(fence)
        slot = self._slot_existing(fence, current)
        if slot["phase"] == "COMMITTED":
            if (
                slot["receipt_digest"] != receipt_digest
                or slot["evidence_ref"] != evidence_ref
                or metadata.get("launch_receipt")
                != dict(digest=receipt_digest, evidence_ref=evidence_ref)
            ):
                raise ConflictError("private receipt slot committed fingerprint differs")
            return self._slot_readback(row, attempt, slot)
        if slot["phase"] != "CLAIMED" or slot["sealed_digest"] != receipt_digest:
            raise ConflictError("private receipt slot commit requires its one claim")
        candidate = dict(slot) | {"phase": "COMMITTED", "receipt_digest": receipt_digest}
        return self._slot_apply(
            fence, row, attempt, metadata, candidate, receipt_digest=receipt_digest
        )

    def _hold_receipt_slot(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        intent_ref: str,
        manifest_digest: str,
        attempt_run_id: int,
        expected_slot_revision: int,
        evidence_ref: str,
        unknown: bool,
        fixed_reason: _SlotFailureReason,
    ) -> _SlotTransitionOut:
        fence = _SlotFence(
            project_id,
            canonical_item_id,
            owner_ref,
            scope_digest,
            expected_epoch,
            expected_revision,
            controller_run_id,
            intent_ref,
            manifest_digest,
            attempt_run_id,
            expected_slot_revision,
            evidence_ref,
        )
        if type(unknown) is not bool:
            raise ValidationError("private receipt slot hold requires an exact boolean")
        allowed = _SLOT_UNKNOWN if unknown else _SLOT_CONFIRMED
        if type(fixed_reason) is not str or fixed_reason not in allowed:
            raise ValidationError("private receipt slot hold requires a fixed matching reason")
        row, attempt, metadata, current = self._slot_context(fence)
        slot = self._slot_existing(fence, current)
        phase = "UNKNOWN_HOLD" if unknown else "FAILED_HOLD"
        if slot["phase"] in {"FAILED_HOLD", "UNKNOWN_HOLD"}:
            if (
                slot["phase"] != phase
                or dict(slot)["hold_reason"] != fixed_reason
                or slot["evidence_ref"] != evidence_ref
            ):
                raise ConflictError("private receipt slot hold differs")
            return self._slot_readback(row, attempt, slot)
        if slot["phase"] not in {"ALLOCATION_PENDING", "ALLOCATED", "CLAIMED"}:
            raise ConflictError("private receipt slot cannot hold a committed record")
        candidate = dict(slot) | {
            "phase": phase,
            "hold_from": slot["phase"],
            "hold_reason": fixed_reason,
        }
        return self._slot_apply(fence, row, attempt, metadata, candidate)

    def _record_owner_attempt(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        owner_ref: str,
        scope_digest: str,
        expected_epoch: int,
        expected_revision: int,
        controller_run_id: int,
        attempt_run_id: int,
        evidence_ref: str,
        observation: RunObservationState | None = None,
        disposition: RunAttemptDisposition | None = None,
        clearance_ref: str | None = None,
    ) -> OwnershipAttemptOut:
        """Fenced existing-run annotations. Verified observations are backend-only.

        Public operations expose reported states only. MYB-95/acceptance owners
        must supply trusted host/acceptance producers for the other states.
        Ending audit does not signal a process or prove quiescence.
        """
        if (observation is None) == (disposition is None):
            raise ValidationError("record exactly one observation or terminal disposition")
        if not evidence_ref or len(evidence_ref) > 1000:
            raise ValidationError("bounded attempt evidence reference required")
        row = self._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
        if row.scope_digest != scope_digest:
            raise ConflictError("ownership scope changed; adjudication required")
        attempt = self._s.get(Run, attempt_run_id, populate_existing=True)
        binding = {
            "canonical_item_id": canonical_item_id,
            "owner_ref": owner_ref,
            "epoch": expected_epoch,
            "scope_digest": scope_digest,
        }
        metadata = deepcopy(attempt.metadata_json or {}) if attempt is not None else {}
        actual_binding = metadata.get("ownership_binding")
        if (
            attempt is None
            or attempt.project_id != project_id
            or attempt.parent_run_id != controller_run_id
            or attempt.kind != RunKind.SKILL_RUN
            or not isinstance(actual_binding, dict)
            or type(actual_binding.get("epoch")) is not int
            or actual_binding != binding
        ):
            raise ConflictError("attempt is not bound to this controller/item/epoch")
        status = attempt.status
        previous = metadata.get("ownership_observation", {}).get("state")
        accepted_annotation = observation == RunObservationState.ACCEPTED_COMPLETION
        review_annotation = previous == RunObservationState.REVIEW_READY and observation in {
            RunObservationState.BLOCKED,
            RunObservationState.SCOPE_DRIFT,
        }
        if status != RunStatus.RUNNING and not (
            status == RunStatus.SUCCESS and (accepted_annotation or review_annotation)
        ):
            raise ConflictError("terminal attempt cannot reactivate or change disposition")
        target_status: RunStatus
        if observation is not None:
            if previous is None and observation != RunObservationState.RUNNING:
                raise ValidationError("first observation must be running")
            if previous is not None:
                validate_transition(
                    RunObservationState(previous),
                    observation,
                    RUN_OBSERVATION_TRANSITIONS,
                    label="run.observation",
                )
            if previous in {"blocked", "scope_drift"} and observation in {
                RunObservationState.RUNNING,
                RunObservationState.WAITING,
            }:
                if not clearance_ref or len(clearance_ref) > 1000:
                    raise ValidationError("resume observation needs bounded adjudication evidence")
                self._owner_admissible(row, scope_digest)
                self.owner_authority(
                    project_id=project_id,
                    canonical_item_id=canonical_item_id,
                    ownership=WorkItemOwnershipOut.model_validate(row),
                    require_budget=True,
                )
            if accepted_annotation:
                self._owner_admissible(row, scope_digest)
                self.owner_authority(
                    project_id=project_id,
                    canonical_item_id=canonical_item_id,
                    ownership=WorkItemOwnershipOut.model_validate(row),
                    require_budget=True,
                )
                kind, value = canonical_item_id.split(":")
                native = (
                    self._s.get(TrackerTicket, int(value), populate_existing=True)
                    if kind == "ticket"
                    else self._s.get(TrackerTask, int(value), populate_existing=True)
                )
                if (
                    native is None
                    or native.status != TrackerItemStatus.COMPLETE
                    or not native.completion_evidence_json
                ):
                    raise ConflictError("canonical acceptance evidence is required")
            metadata["ownership_observation"] = {
                "state": observation.value,
                "evidence_ref": evidence_ref,
                "recorded_at": _utcnow().isoformat(),
                "clearance_ref": clearance_ref,
            }
            target_status = status
        else:
            assert disposition is not None
            if disposition == RunAttemptDisposition.SUPERSEDED and (
                row.quiescence_revision != row.revision or not row.quiescence_ref
            ):
                raise ConflictError("supersession needs current backend quiescence/effect evidence")
            metadata["ownership_disposition"] = {
                "state": disposition.value,
                "evidence_ref": evidence_ref,
                "recorded_at": _utcnow().isoformat(),
            }
            target_status = RUN_ATTEMPT_DISPOSITION_STATUS[disposition]
        try:
            # The metadata snapshot is also a CAS predicate: observations on a
            # retained Session cannot overwrite another result or transition.
            result = cast(
                CursorResult[Any],
                self._s.execute(
                    update(Run)
                    .where(
                        col(Run.id) == attempt_run_id,
                        col(Run.project_id) == project_id,
                        col(Run.parent_run_id) == controller_run_id,
                        col(Run.status) == status,
                        col(Run.metadata_json) == attempt.metadata_json,
                    )
                    .values(
                        status=target_status,
                        metadata_json=redact_secrets(metadata),
                        ended_at=_utcnow() if disposition is not None else attempt.ended_at,
                    )
                ),
            )
            if result.rowcount != 1:
                raise ConflictError("stale attempt observation/disposition")
            self._s.refresh(attempt)
            attempt_receipt = RunOut.model_validate(attempt)
            ownership = self._owner_cas(
                row,
                owner_ref=owner_ref,
                expected_epoch=expected_epoch,
                expected_revision=expected_revision,
                changes={"quiescence_revision": None},
                event_type="WORK_ITEM_OWNER_ATTEMPT_RECORDED",
                event_metadata={
                    "attempt_run_id": attempt_run_id,
                    "observation": observation,
                    "disposition": disposition,
                },
            )
        except Exception:
            self._s.rollback()
            raise
        return OwnershipAttemptOut(ownership=ownership, attempt=attempt_receipt)

    def _owner_cas(
        self,
        row: WorkItemOwnership,
        *,
        owner_ref: str | None,
        expected_epoch: int,
        expected_revision: int,
        changes: dict[str, Any],
        event_type: str,
        require_quiescence: bool = False,
        admission_scope_digest: str | None = None,
        event_metadata: dict[str, Any] | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> WorkItemOwnershipOut:
        if (
            row.epoch != expected_epoch
            or row.revision != expected_revision
            or row.owner_ref != owner_ref
        ):
            raise ConflictError("stale ownership fence")
        statement = update(WorkItemOwnership).where(
            col(WorkItemOwnership.id) == row.id,
            col(WorkItemOwnership.owner_ref) == owner_ref,
            col(WorkItemOwnership.epoch) == expected_epoch,
            col(WorkItemOwnership.revision) == expected_revision,
        )
        if admission_scope_digest is not None:
            # Admission and stop fencing share the update, even when a Session
            # retained an older identity-map row or a stop races the preflight.
            statement = statement.where(
                col(WorkItemOwnership.scope_digest) == admission_scope_digest,
                col(WorkItemOwnership.continuity_json)["stopped"].as_boolean().is_(False),
                col(WorkItemOwnership.continuity_json)["denied"].as_boolean().is_(False),
            )
        if require_quiescence:
            statement = statement.where(
                col(WorkItemOwnership.quiescence_revision) == col(WorkItemOwnership.revision),
                col(WorkItemOwnership.quiescence_ref).is_not(None),
            )
        try:
            result = cast(
                CursorResult[Any],
                self._s.execute(
                    statement.values(
                        **changes, revision=WorkItemOwnership.revision + 1, updated_at=_utcnow()
                    )
                ),
            )
            if result.rowcount != 1:
                raise ConflictError("stale ownership fence")
            self._s.refresh(row)
            if before_commit is not None:
                before_commit()
            return self._owner_commit(row, event_type, event_metadata)
        except Exception:
            self._s.rollback()
            raise

    def _owner_commit(
        self,
        row: WorkItemOwnership,
        event_type: str,
        event_metadata: dict[str, Any] | None = None,
    ) -> WorkItemOwnershipOut:
        try:
            if self._owner_commit_guard is not None:
                # The write transaction holds SQLite's writer lock while the
                # canonical operation owner revalidates current authority.
                self._owner_commit_guard()
            self._owner_event(row, event_type, event_metadata)
            receipt = WorkItemOwnershipOut.model_validate(row)
            self._s.commit()
        except Exception:
            self._s.rollback()
            raise
        return receipt

    def _owner_event(
        self,
        row: WorkItemOwnership,
        event_type: str,
        event_metadata: dict[str, Any] | None = None,
    ) -> None:
        kind, native_id = row.canonical_item_id.split(":")
        StackOSEventEmitter(self._s).emit(
            StackOSEvent(
                project_id=row.project_id,
                run_id=self._audit_run_id,
                source=StackOSEventSource(type=f"tracker_{kind}", id=int(native_id)),
                event_type=event_type,
                actor=row.owner_ref,
                metadata=WorkItemOwnershipOut.model_validate(row).model_dump()
                | (event_metadata or {}),
            )
        )

    def _owner_row(self, *, project_id: int, canonical_item_id: str) -> WorkItemOwnership:
        row = self._s.exec(
            select(WorkItemOwnership)
            .where(
                WorkItemOwnership.project_id == project_id,
                WorkItemOwnership.canonical_item_id == canonical_item_id,
            )
            .execution_options(populate_existing=True)
        ).first()
        if row is None:
            raise NotFoundError("ownership not found in project")
        return row

    @staticmethod
    def _validate_owner_ref(owner_ref: str) -> None:
        if not owner_ref or len(owner_ref) > 160:
            raise ValidationError("owner_ref must be a bounded assignment reference")

    @staticmethod
    def _owner_admissible(row: WorkItemOwnership, scope_digest: str) -> None:
        if len(scope_digest) != 64 or any(char not in "0123456789abcdef" for char in scope_digest):
            raise ValidationError("scope_digest must be a SHA-256 digest")
        if row.scope_digest != scope_digest:
            raise ConflictError("ownership scope changed; adjudication required")
        if row.continuity_json.get("stopped") or row.continuity_json.get("denied"):
            raise ConflictError("original stop/denial remains binding; no new owner admitted")

    def start(
        self,
        *,
        project_id: int | None,
        kind: RunKind,
        parent_run_id: int | None = None,
        client_session_id: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        _commit: bool = True,
    ) -> Envelope[RunOut]:
        """Insert a new ``status='running'`` row."""
        self._validate_run_annotation_owner(metadata_json)
        now = _utcnow()
        row = Run(
            project_id=project_id,
            kind=kind,
            parent_run_id=parent_run_id,
            client_session_id=client_session_id,
            started_at=now,
            heartbeat_at=now,
            status=RunStatus.RUNNING,
            metadata_json=metadata_json,
        )
        self._s.add(row)
        if _commit:
            self._s.commit()
            self._s.refresh(row)
        else:
            self._s.flush()
        return Envelope(
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=project_id,
        )

    def finish(
        self,
        run_id: int,
        *,
        status: Literal["success", "failed", "aborted"] | RunStatus,
        error: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        project_id: int | None = None,
        _commit: bool = True,
    ) -> Envelope[RunOut]:
        """Move the run to a terminal status.

        ``validate_transition`` enforces ``running → terminal`` only.
        """
        self._validate_run_annotation_owner(metadata_json)
        if not isinstance(status, RunStatus):
            status = RunStatus(status)
        row = self._fetch(run_id)
        self._require_project_match(row, project_id)

        from stackos.repositories.run_plan_lifecycle import RunPlanLifecycleReconciler

        RunPlanLifecycleReconciler(self._s).validate_direct_run_finish(row, status)
        validate_transition(row.status, status, RUN_STATUS_TRANSITIONS, label="run.status")
        row.status = status
        row.ended_at = _utcnow()
        if error is not None:
            row.error = error
        if metadata_json is not None:
            # Shallow-merge so child state machinery (e.g. cost) survives.
            current = dict(row.metadata_json or {})
            current.update(metadata_json)
            row.metadata_json = current
        self._s.add(row)
        if _commit:
            self._s.commit()
            self._s.refresh(row)
        else:
            self._s.flush()
        return Envelope(
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=row.project_id,
        )

    @staticmethod
    def _validate_run_annotation_owner(metadata_json: dict[str, Any] | None) -> None:
        if (
            metadata_json
            and {
                "ownership_observation",
                "ownership_disposition",
                "launch_reservation",
                "launch_receipt",
                "receipt_slot",
            }
            & metadata_json.keys()
        ):
            raise ValidationError("ownership annotations must use the fenced attempt owner")

    def heartbeat(self, run_id: int, *, project_id: int | None = None) -> Envelope[RunOut | None]:
        """Update ``heartbeat_at`` to now.

        Idempotent if the row is missing — we return a stub ``Envelope``
        with a ``None`` ``data`` and ``run_id`` so the M9 daemon's
        heartbeat loop doesn't crash if a run was just reaped between
        the load and the call.
        """
        row = self._s.get(Run, run_id)
        if row is None:
            return Envelope[RunOut | None](data=None, run_id=None)
        self._require_project_match(row, project_id)
        row.heartbeat_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope[RunOut | None](
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=row.project_id,
        )

    def abort(
        self,
        run_id: int,
        *,
        cascade: bool = False,
        project_id: int | None = None,
    ) -> Envelope[RunOut]:
        """Move to ``aborted``; optionally cascade through child runs.

        Cascade traverses ``parent_run_id`` recursively for any child
        still in ``running``. Children that already terminated are left
        alone.
        """
        from stackos.repositories.run_plan_lifecycle import RunPlanLifecycleReconciler

        reconciler = RunPlanLifecycleReconciler(self._s)
        row = self._fetch(run_id)
        self._require_project_match(row, project_id)
        if row.status == RunStatus.RUNNING:
            validate_transition(
                row.status, RunStatus.ABORTED, RUN_STATUS_TRANSITIONS, label="run.status"
            )
            row.status = RunStatus.ABORTED
            row.ended_at = _utcnow()
            self._s.add(row)
        if row.status == RunStatus.ABORTED:
            reconciler.reconcile_aborted_run_plan(row, reason=row.error or "run-aborted")
        if cascade:
            assert row.id is not None
            self._cascade_abort(row.id)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=row.project_id,
        )

    def list(
        self,
        *,
        project_id: int | None = None,
        kind: RunKind | None = None,
        status: RunStatus | None = None,
        parent_run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[RunOut]:
        """Cursor-paginated list."""
        stmt = select(Run)
        if project_id is not None:
            stmt = stmt.where(Run.project_id == project_id)
        if kind is not None:
            stmt = stmt.where(Run.kind == kind)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        if parent_run_id is not None:
            stmt = stmt.where(Run.parent_run_id == parent_run_id)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=Run.id,
            limit=limit,
            after_id=after_id,
            converter=RunOut.model_validate,
        )

    def get(self, run_id: int, *, project_id: int | None = None) -> RunOut:
        """Fetch one run by id."""
        row = self._fetch(run_id)
        self._require_project_match(row, project_id)
        return RunOut.model_validate(row)

    def children(
        self,
        parent_run_id: int,
        *,
        project_id: int | None = None,
    ) -> list[RunOut]:  # type: ignore[valid-type]
        """Direct children of a parent run."""
        parent = self._fetch(parent_run_id)
        self._require_project_match(parent, project_id)
        rows = self._s.exec(
            select(Run).where(Run.parent_run_id == parent_run_id).order_by(Run.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [RunOut.model_validate(r) for r in rows]

    def cost(self, project_id: int, *, month: str | None = None) -> dict[str, Any]:
        """Sum ``run_steps.cost_cents`` per ``runs.kind`` for the month.

        ``month`` is ``YYYY-MM``; default is the current month per
        ``_utcnow()``. The cost-of-truth is ``run_steps.cost_cents``;
        ``runs.metadata_json.cost`` is denormalised for fast UI display
        but we sum from the steps for accuracy.
        """
        target = month or _utcnow().strftime("%Y-%m")
        # Parse safely — SQLite's strftime is fine but we prefer Python
        # date parsing so a malformed month raises ValidationError, not a
        # cryptic SQL error.
        try:
            year_s, month_s = target.split("-")
            year, month_n = int(year_s), int(month_s)
            if month_n < 1 or month_n > 12:
                raise ValueError
        except (ValueError, IndexError) as exc:
            raise ValidationError(
                f"month must be YYYY-MM (got {target!r})", data={"month": target}
            ) from exc
        period_start = datetime(year, month_n, 1)
        period_end = datetime(year + 1, 1, 1) if month_n == 12 else datetime(year, month_n + 1, 1)

        # Pull all runs in the project + month, then their steps.
        run_rows = self._s.exec(
            select(Run).where(
                Run.project_id == project_id,
                Run.started_at >= period_start,
                Run.started_at < period_end,
            )
        ).all()
        run_ids = [r.id for r in run_rows if r.id is not None]
        cost_by_kind: dict[str, int] = {}
        if run_ids:
            step_rows = self._s.exec(
                select(RunStep).where(RunStep.run_id.in_(run_ids))  # type: ignore[union-attr,attr-defined]
            ).all()
            kind_by_run: dict[int, str] = {r.id: r.kind.value for r in run_rows if r.id is not None}
            for s in step_rows:
                k = kind_by_run.get(s.run_id, "unknown")
                cost_by_kind[k] = cost_by_kind.get(k, 0) + (s.cost_cents or 0)
        return {
            "project_id": project_id,
            "month": target,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "by_kind_cents": cost_by_kind,
            "total_cents": sum(cost_by_kind.values()),
        }

    def reap_stale(self, *, stale_after_seconds: int = 300) -> int:
        """Crash-recovery sweep per audit B-13 / PLAN.md L1366-L1375.

        Finds rows with ``status='running' AND heartbeat_at < now() - delta``
        and transitions process-bound stale rows to ``aborted`` with
        ``error='daemon-restart-orphan'``. Live run plans are durable workflow
        state, not daemon jobs; if the stale row is linked to a started run
        plan, the audit heartbeat is refreshed and the plan remains resumable.
        Returns the number of rows reaped. Cascade abort is applied to each
        reaped run so live-orphan child chains are cleaned in the same sweep.
        """
        from stackos.repositories.run_plan_lifecycle import (
            STALE_RUN_ERROR,
            RunPlanLifecycleReconciler,
        )

        reconciler = RunPlanLifecycleReconciler(self._s)
        reconciler.reconcile_orphaned_run_plans(reason=STALE_RUN_ERROR)
        cutoff = _utcnow() - timedelta(seconds=stale_after_seconds)
        rows = self._s.exec(
            select(Run).where(
                Run.status == RunStatus.RUNNING,
                Run.heartbeat_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        reaped = 0
        for row in rows:
            if reconciler.should_preserve_live_run_plan(row):
                row.heartbeat_at = _utcnow()
                self._s.add(row)
                continue
            row.status = RunStatus.ABORTED
            row.error = STALE_RUN_ERROR
            row.ended_at = _utcnow()
            self._s.add(row)
            reconciler.reconcile_aborted_run_plan(row, reason=STALE_RUN_ERROR)
            assert row.id is not None
            self._cascade_abort(row.id)
            reaped += 1
        self._s.commit()
        return reaped

    # -------- Internal --------

    def _fetch(self, run_id: int) -> Run:
        row = self._s.get(Run, run_id)
        if row is None:
            raise NotFoundError(f"run {run_id} not found")
        return row

    @staticmethod
    def _require_project_match(row: Run, project_id: int | None) -> None:
        if project_id is None or row.project_id == project_id:
            return
        raise NotFoundError(
            f"run {row.id} not found in project {project_id}",
            data={"project_id": project_id, "run_id": row.id},
        )

    def _cascade_abort(self, root_id: int) -> None:
        """Recursively abort children of ``root_id`` that are still running."""
        from stackos.repositories.run_plan_lifecycle import RunPlanLifecycleReconciler

        reconciler = RunPlanLifecycleReconciler(self._s)
        frontier = [root_id]
        while frontier:
            current = frontier.pop()
            children = self._s.exec(
                select(Run).where(
                    Run.parent_run_id == current,
                    Run.status == RunStatus.RUNNING,
                )
            ).all()
            for c in children:
                c.status = RunStatus.ABORTED
                c.error = c.error or "parent-aborted"
                c.ended_at = _utcnow()
                self._s.add(c)
                reconciler.reconcile_aborted_run_plan(c, reason=c.error)
                if c.id is not None:
                    frontier.append(c.id)


# ---------------------------------------------------------------------------
# RunStepRepository.
# ---------------------------------------------------------------------------


class RunStepRepository:
    """Per-agent/tool audit grain inside a run."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def insert_step(
        self,
        *,
        run_id: int,
        step_index: int,
        skill_name: str,
        status: RunStepStatus = RunStepStatus.PENDING,
        input_snapshot_json: dict[str, Any] | None = None,
        project_id: int | None = None,
    ) -> Envelope[RunStepOut]:
        """Insert a per-skill step row."""
        run = self._fetch_run(run_id, project_id=project_id)
        row = RunStep(
            run_id=run_id,
            step_index=step_index,
            skill_name=skill_name,
            status=status,
            started_at=_utcnow() if status == RunStepStatus.RUNNING else None,
            input_snapshot_json=input_snapshot_json,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=RunStepOut.model_validate(row),
            run_id=run_id,
            project_id=run.project_id,
        )

    def advance_step(
        self,
        step_pk: int,
        *,
        status: RunStepStatus,
        output_snapshot_json: dict[str, Any] | None = None,
        error: str | None = None,
        cost_cents: int | None = None,
        integration_calls_json: dict[str, Any] | None = None,
    ) -> Envelope[RunStepOut]:
        """Update a step's status / outputs."""
        row = self._s.get(RunStep, step_pk)
        if row is None:
            raise NotFoundError(f"run step {step_pk} not found")
        now = _utcnow()
        if row.status == RunStepStatus.PENDING and status == RunStepStatus.RUNNING:
            row.started_at = now
        if status in (RunStepStatus.SUCCESS, RunStepStatus.FAILED, RunStepStatus.SKIPPED):
            row.ended_at = now
        row.status = status
        if output_snapshot_json is not None:
            row.output_snapshot_json = output_snapshot_json
        if error is not None:
            row.error = error
        if cost_cents is not None:
            row.cost_cents = cost_cents
        if integration_calls_json is not None:
            row.integration_calls_json = integration_calls_json
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=RunStepOut.model_validate(row), run_id=row.run_id)

    def list_steps(self, run_id: int, *, project_id: int | None = None) -> list[RunStepOut]:
        """All run steps for a run, ordered by step_index."""
        self._fetch_run(run_id, project_id=project_id)
        rows = self._s.exec(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_index.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [RunStepOut.model_validate(r) for r in rows]

    def _fetch_run(self, run_id: int, *, project_id: int | None = None) -> Run:
        row = self._s.get(Run, run_id)
        if row is None:
            raise NotFoundError(f"run {run_id} not found")
        RunRepository._require_project_match(row, project_id)
        return row


# ---------------------------------------------------------------------------
# RunStepCallRepository.
# ---------------------------------------------------------------------------


class RunStepCallRepository:
    """Per-MCP-tool grain inside a per-skill step."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def record_call(
        self,
        *,
        run_step_id: int,
        mcp_tool: str,
        request_json: dict[str, Any] | None = None,
        response_json: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        cost_cents: int = 0,
        project_id: int | None = None,
    ) -> Envelope[RunStepCallOut]:
        """Insert a per-MCP-tool call row."""
        step, run = self._fetch_step_and_run(run_step_id, project_id=project_id)
        row = RunStepCall(
            run_step_id=step.id,
            mcp_tool=mcp_tool,
            request_json=request_json,
            response_json=response_json,
            duration_ms=duration_ms,
            error=error,
            cost_cents=cost_cents,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=RunStepCallOut.model_validate(row),
            run_id=run.id,
            project_id=run.project_id,
        )

    def list(self, run_step_id: int, *, project_id: int | None = None) -> list[RunStepCallOut]:
        """All calls for a step in id order."""
        self._fetch_step_and_run(run_step_id, project_id=project_id)
        rows = self._s.exec(
            select(RunStepCall)
            .where(RunStepCall.run_step_id == run_step_id)
            .order_by(RunStepCall.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [RunStepCallOut.model_validate(r) for r in rows]

    def _fetch_step_and_run(
        self,
        run_step_id: int,
        *,
        project_id: int | None = None,
    ) -> tuple[RunStep, Run]:
        row = self._s.get(RunStep, run_step_id)
        if row is None:
            raise NotFoundError(f"run step {run_step_id} not found")
        run = self._s.get(Run, row.run_id)
        if run is None:
            raise NotFoundError(f"run {row.run_id} not found")
        RunRepository._require_project_match(run, project_id)
        return row, run


# ---------------------------------------------------------------------------
# IdempotencyKeyRepository.
# ---------------------------------------------------------------------------


# 24-hour dedup window per audit M-20 / PLAN.md L722-L729.
IDEMPOTENCY_WINDOW = timedelta(hours=24)


class IdempotencyKeyRepository:
    """24-hour dedup keyed on ``(project_id, tool_name, idempotency_key)``."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def check_or_create(
        self,
        *,
        project_id: int,
        tool_name: str,
        idempotency_key: str,
        run_id: int | None = None,
        response_json: dict[str, Any] | None = None,
    ) -> tuple[IdempotencyOut, bool]:
        """Look up the key; create if absent.

        Returns ``(row, created)``. If ``created=False`` the caller is
        replaying within the 24h window and should return the cached
        ``response_json``. Raises ``IdempotencyReplayError`` to give the
        transport layer a typed signal it can map to a 200 with the
        cached envelope; the existing ``run_id`` is in ``data['run_id']``
        for surfacing.
        """
        existing = self._s.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.project_id == project_id,
                IdempotencyKey.tool_name == tool_name,
                IdempotencyKey.idempotency_key == idempotency_key,
            )
        ).first()
        now = _utcnow()
        if existing is not None:
            if now - existing.created_at <= IDEMPOTENCY_WINDOW:
                # Replay — short-circuit.
                raise IdempotencyReplayError(
                    f"idempotency key {idempotency_key!r} replayed within 24h",
                    data={
                        "idempotency_key_id": existing.id,
                        "run_id": existing.run_id,
                        "tool_name": tool_name,
                        "project_id": project_id,
                        "response_json": existing.response_json,
                    },
                )
            # Stale: delete the old row and treat as fresh.
            self._s.delete(existing)
            self._s.flush()
        row = IdempotencyKey(
            project_id=project_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            run_id=run_id,
            response_json=response_json,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return IdempotencyOut.model_validate(row), True

    def update_response(
        self,
        *,
        project_id: int,
        tool_name: str,
        idempotency_key: str,
        response_json: dict[str, Any],
    ) -> Envelope[IdempotencyOut]:
        """Persist the response body once the original call returns.

        The transport layer calls this *after* the underlying repo method
        succeeds so subsequent replays see the actual response shape.
        """
        row = self._s.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.project_id == project_id,
                IdempotencyKey.tool_name == tool_name,
                IdempotencyKey.idempotency_key == idempotency_key,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                "idempotency key not found",
                data={"tool_name": tool_name, "project_id": project_id},
            )
        row.response_json = response_json
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=IdempotencyOut.model_validate(row), project_id=project_id)


# Helper type aliases re-exported for transport layers.
RunOutType = RunOut
RunStepOutType = RunStepOut
RunStepCallOutType = RunStepCallOut


__all__ = [
    "IdempotencyKeyRepository",
    "IdempotencyOut",
    "RunOut",
    "RunRepository",
    "RunStepCallOut",
    "RunStepCallRepository",
    "RunStepOut",
    "RunStepRepository",
]
