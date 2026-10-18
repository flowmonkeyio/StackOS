"""Current authenticated admission for the opt-in Engineering action.

The private object is cooperative data. Every use repeats canonical checks;
possession, request ids, and a running step alone confer no authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from stackos.controlled_process_host import _NativePolicy

from pydantic import ValidationError as ModelValidationError
from sqlalchemy import event
from sqlmodel import col, select

from stackos.actions.controlled_command import (
    _CONTROLLED_ACTION_REF,
    _canonical_digest,
    _check_controlled_manifest,
    _controlled_host_settings,
    _ControlledCommandInput,
    _ControlledNativeAudit,
    _resolve_controlled_command,
    _ResolvedControlledCommand,
)
from stackos.config import Settings
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
    Plugin,
    Run,
    RunPlan,
    RunPlanStep,
    WorkItemOwnership,
)
from stackos.mcp.context import MCPContext, current_context
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.permissions import (
    RUN_PLAN_CONTROLLER_SKILL,
    active_run_plan_step,
    check_call_grant,
    resolve_run_token,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.runs import RunOut, RunRepository, WorkItemOwnershipOut
from stackos.repositories.tracker import TrackerRepository

from .schemas import ActionExecuteInput


def _action_ref(inp: ActionExecuteInput) -> str | None:
    return inp.action_ref or (
        f"{inp.plugin_slug}.{inp.action_key}" if inp.plugin_slug and inp.action_key else None
    )


@dataclass(frozen=True)
class _ControlledFence:
    project_id: int
    controller_run_id: int
    run_plan_id: int
    run_plan_step_id: int
    canonical_item_id: str
    owner_ref: str
    epoch: int
    revision: int
    scope_digest: str
    continuity_digest: str
    approval_id: int
    approval_updated_at: datetime | None
    approval_decision_digest: str
    grant_digest: str
    step_actions_digest: str


@dataclass(frozen=True)
class _ControlledAuthority:
    _ctx: MCPContext = field(repr=False)
    _inp: ActionExecuteInput = field(repr=False)
    request: _ControlledCommandInput
    command: _ResolvedControlledCommand
    fence: _ControlledFence

    def revalidate(self) -> _ControlledFence:
        current = _issue_controlled_authority(self._inp, self._ctx)
        if (
            current.fence != self.fence
            or current.command != self.command
            or current.request != self.request
        ):
            raise ConflictError("controlled authority changed")
        return current.fence


def _check_controlled_options(inp: ActionExecuteInput) -> None:
    if _action_ref(inp) != _CONTROLLED_ACTION_REF:
        raise ValidationError("controlled action mismatch")
    for name in (
        "context_ref",
        "credential_ref",
        "provider_context_json",
        "output_policy_json",
        "metadata_json",
        "due_at",
        "expires_at",
        "account_interval_seconds",
        "destination_interval_seconds",
    ):
        if getattr(inp, name) is not None:
            raise ValidationError("controlled option is forbidden", data={"field": name})
    if inp.dry_run and inp.idempotency_key is not None:
        raise ValidationError("controlled dry-run cannot select replay")


def _check_approval_binding(
    decision: dict[str, Any],
    expected: dict[str, Any],
    command: _ResolvedControlledCommand,
) -> None:
    supplied = decision.get("controlled_command")
    if type(supplied) is not dict or supplied.keys() != expected.keys():
        raise ConflictError("controlled approval binding mismatch")
    for name in ("project_id", "controller_run_id", "run_plan_id", "run_plan_step_id", "epoch"):
        value = supplied[name]
        if type(value) is not int or not 1 <= value <= 9223372036854775807:
            raise ConflictError("controlled approval binding mismatch")
    # Canonical JSON comparison preserves native types; True must not equal 1.
    if _canonical_digest(supplied) != _canonical_digest(expected) or supplied != expected:
        raise ConflictError("controlled approval binding mismatch")
    if supplied["command"] != command.descriptor():
        raise ConflictError("controlled command changed")


def _issue_controlled_authority(inp: ActionExecuteInput, ctx: MCPContext) -> _ControlledAuthority:
    return _current_controlled_authority(inp, ctx)


def _current_controlled_authority(
    inp: ActionExecuteInput,
    ctx: MCPContext,
    *,
    native_revision: int | None = None,
    charged: bool = False,
) -> _ControlledAuthority:
    _check_controlled_options(inp)
    if (
        current_context() is not ctx
        or ctx.extras.get("trusted_local_admin")
        or ctx.skill_name != RUN_PLAN_CONTROLLER_SKILL
        or not ctx.run_token
    ):
        raise ToolNotGrantedError("controlled action requires current authenticated controller")
    ctx.session.expire_all()
    run, skill = resolve_run_token(ctx.run_token, ctx.session)
    if (
        run is None
        or skill != RUN_PLAN_CONTROLLER_SKILL
        or run.id != ctx.run_id
        or run.project_id != inp.project_id
        or ctx.project_id != inp.project_id
    ):
        raise ToolNotGrantedError("controlled controller binding mismatch")
    ctx.run = run
    check_call_grant("action.execute", ctx, inp)
    plan, step = active_run_plan_step(ctx, "action.execute")
    if plan.id is None or step.id is None or run.id is None:
        raise ConflictError("controlled current scope is incomplete")
    try:
        request = _ControlledCommandInput.model_validate(inp.input_json)
    except ModelValidationError as exc:
        raise ValidationError("invalid controlled command binding") from exc
    TrackerRepository(ctx.session).require_owner_execution_binding(
        project_id=inp.project_id,
        canonical_item_id=request.canonical_item_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
    )
    owners = RunRepository(ctx.session)
    owner = owners.owner_get(project_id=inp.project_id, canonical_item_id=request.canonical_item_id)
    if (
        owner.owner_ref != request.owner_ref
        or owner.epoch != request.expected_epoch
        or owner.revision
        != (request.expected_revision if native_revision is None else native_revision)
        or owner.scope_digest != request.scope_digest
    ):
        raise ConflictError("controlled current owner mismatch")
    continuity = owners.owner_authority(
        project_id=inp.project_id,
        canonical_item_id=request.canonical_item_id,
        ownership=owner,
        require_budget=True,
    )
    if continuity.get("scope_digest") != request.scope_digest:
        raise ConflictError("controlled continuity scope mismatch")
    for used, limit in (("attempts_used", "attempt_limit"), ("failures_used", "failure_limit")):
        value = continuity[used]
        floor = owner.continuity_json.get(used)
        if native_revision is not None and type(floor) is int:
            value = max(value, floor)
        exhausted = (
            value > continuity[limit]
            if charged and used == "attempts_used"
            else value >= continuity[limit]
        )
        if type(floor) is not int or value < floor or exhausted:
            raise ConflictError("controlled continuity is exhausted or regressed")
    from stackos.actions import ActionRepository

    described = ActionRepository(ctx.session).describe(
        project_id=inp.project_id, action_ref=_CONTROLLED_ACTION_REF
    )
    _check_controlled_manifest(described.manifest)
    if not (
        described.availability.executable
        or (
            described.availability.status == "not_executable"
            and "controlled_host_unavailable" in described.availability.reasons
        )
    ):
        raise ConflictError("controlled manifest is unavailable")
    command = _resolve_controlled_command()
    approval_id = 0
    approval_updated_at = None
    decision_digest = ""
    if not inp.dry_run:
        if command.digest != request.command_digest:
            raise ConflictError("controlled command digest changed")
        from .execution import _approval_ref_for_action

        key = _approval_ref_for_action(plan.grant_snapshot_json or {}, _CONTROLLED_ACTION_REF)
        if key is None:
            raise ConflictError("controlled approval mapping is missing")
        approval = ctx.session.exec(
            select(ApprovalRequest).where(
                ApprovalRequest.run_plan_id == plan.id,
                ApprovalRequest.approval_key == key,
            )
        ).first()
        if (
            approval is None
            or approval.id is None
            or approval.status != ApprovalRequestStatus.APPROVED
            or type(approval.decision_json) is not dict
        ):
            raise ConflictError("controlled approval is not current and approved")
        expected = {
            "schema_version": "stackos.controlled-command-approval.v1",
            "action_ref": _CONTROLLED_ACTION_REF,
            "project_id": inp.project_id,
            "controller_run_id": run.id,
            "run_plan_id": plan.id,
            "run_plan_step_id": step.id,
            "canonical_item_id": request.canonical_item_id,
            "owner_ref": request.owner_ref,
            "epoch": request.expected_epoch,
            "scope_digest": request.scope_digest,
            "intent_ref": request.intent_ref,
            "command_digest": command.digest,
            "command": command.descriptor(),
        }
        _check_approval_binding(approval.decision_json, expected, command)
        approval_id, approval_updated_at = approval.id, approval.updated_at
        decision_digest = _canonical_digest(approval.decision_json)
    fence = _ControlledFence(
        project_id=inp.project_id,
        controller_run_id=run.id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        canonical_item_id=request.canonical_item_id,
        owner_ref=request.owner_ref,
        epoch=owner.epoch,
        revision=owner.revision,
        scope_digest=owner.scope_digest,
        continuity_digest=_canonical_digest(continuity),
        approval_id=approval_id,
        approval_updated_at=approval_updated_at,
        approval_decision_digest=decision_digest,
        grant_digest=_canonical_digest(plan.grant_snapshot_json),
        step_actions_digest=_canonical_digest(step.action_refs_json),
    )
    return _ControlledAuthority(ctx, inp.model_copy(deep=True), request, command, fence)


@dataclass(frozen=True)
class _NativeAdmission:
    original: _ControlledAuthority = field(repr=False)
    policy: _NativePolicy
    runtime_digest: str
    continuity: dict[str, Any] = field(repr=False)
    settings: Settings = field(repr=False)
    storage: tuple[str, str] = field(repr=False)
    storage_digest: str
    seed: bytes = field(repr=False)

    @property
    def state_dir(self) -> Path:
        return Path(self.storage[0])


def _native_settings(ctx: MCPContext, *, require_enabled: bool = True) -> Settings:
    settings = _controlled_host_settings(ctx.session)
    if settings is None:
        raise ConflictError("controlled_host_unavailable")
    context_settings: object = ctx.extras.get("settings")
    if context_settings is not settings:
        raise ConflictError("controlled_host_unavailable")
    if require_enabled and settings.controlled_host_enabled is not True:
        raise ConflictError("controlled_host_unavailable")
    return settings


def _native_storage(settings: Settings) -> tuple[tuple[str, str], bytes, str]:
    """Use the canonical explicit seed API; never consult ambient crypto state."""
    from stackos.crypto.seed import load_seed

    storage = (str(settings.state_dir), str(settings.seed_path))
    try:
        seed = load_seed(settings.seed_path)
    except Exception as exc:
        raise ConflictError("native original storage is unavailable") from exc
    context = dict(
        state_dir=storage[0], seed_path=storage[1], seed_digest=_canonical_digest(seed.hex())
    )
    return storage, seed, _canonical_digest(context)


def _issue_native_admission(inp: ActionExecuteInput, ctx: MCPContext) -> _NativeAdmission:
    """Real forward admission, including the daemon's default-off capability."""
    from stackos.controlled_process_host import _native_manifest, _NativePolicy

    settings = _native_settings(ctx)
    original = _issue_controlled_authority(inp, ctx)
    if inp.dry_run:
        raise ConflictError("native dry-run is not admission")
    approval = ctx.session.get(ApprovalRequest, original.fence.approval_id)
    assert approval is not None
    runtime = _canonical_digest(_native_manifest(original.command))
    policy = _NativePolicy.from_decision(
        (approval.decision_json or {}).get("controlled_host"),
        command_digest=original.command.digest,
        runtime_digest=runtime,
    )
    owner = RunRepository(ctx.session).owner_get(
        project_id=inp.project_id, canonical_item_id=original.request.canonical_item_id
    )
    if owner.continuity_json.get("deadline_ref") != policy.deadline_ref:
        raise ConflictError("native original deadline binding is missing")
    continuity = RunRepository(ctx.session).owner_authority(
        project_id=inp.project_id,
        canonical_item_id=original.request.canonical_item_id,
        ownership=owner,
        require_budget=True,
    )
    storage, seed, storage_digest = _native_storage(settings)
    return _NativeAdmission(
        original,
        policy,
        runtime,
        deepcopy(continuity),
        settings,
        storage,
        storage_digest,
        seed,
    )


def _revalidate_native_context(
    admission: _NativeAdmission, *, revision: int | None = None, charged: bool = False
) -> None:
    from stackos.controlled_process_host import _native_manifest, _NativePolicy

    if type(admission) is not _NativeAdmission or type(admission.policy) is not _NativePolicy:
        raise ConflictError("native admission is invalid")
    original = admission.original
    settings = _native_settings(original._ctx)
    storage, seed, storage_digest = _native_storage(settings)
    if (
        settings is not admission.settings
        or storage != admission.storage
        or seed != admission.seed
        or storage_digest != admission.storage_digest
    ):
        raise ConflictError("native storage configuration changed")
    approval = original._ctx.session.get(ApprovalRequest, original.fence.approval_id)
    if approval is None or _canonical_digest(
        (approval.decision_json or {}).get("controlled_host")
    ) != _canonical_digest(admission.policy._decision):
        raise ConflictError("native policy is not the original approved decision")
    expected_policy = _NativePolicy.from_decision(
        (approval.decision_json or {}).get("controlled_host"),
        command_digest=original.command.digest,
        runtime_digest=admission.runtime_digest,
    )
    if _canonical_digest(asdict(expected_policy)) != _canonical_digest(asdict(admission.policy)):
        raise ConflictError("native policy fields are not the original approved values")

    def reject_nested_commit(session: object) -> None:
        # Catalog describe may synchronize a newly changed builtin generation.
        # A guard may never commit its caller's partially staged owner CAS.
        raise ConflictError("native authority check cannot commit staged writes")

    event.listen(original._ctx.session, "before_commit", reject_nested_commit)
    try:
        current = _current_controlled_authority(
            original._inp, original._ctx, native_revision=revision, charged=charged
        )
    finally:
        event.remove(original._ctx.session, "before_commit", reject_nested_commit)
    owners = RunRepository(original._ctx.session)
    owner = owners.owner_get(
        project_id=original.fence.project_id, canonical_item_id=original.request.canonical_item_id
    )
    continuity = owners.owner_authority(
        project_id=original.fence.project_id,
        canonical_item_id=original.request.canonical_item_id,
        ownership=owner,
        require_budget=True,
    )
    counters = {"attempts_used", "failures_used"}
    if {k: v for k, v in continuity.items() if k not in counters} != {
        k: v for k, v in admission.continuity.items() if k not in counters
    } or any(
        type(continuity.get(k)) is not int or continuity[k] < admission.continuity[k]
        for k in counters
    ):
        raise ConflictError("native continuity regressed or changed")
    # Only own acknowledged revision/accounting may advance. Every other fence,
    # whole approval and descriptor remains exactly the original admitted value.
    compared = replace(
        current.fence,
        revision=original.fence.revision,
        continuity_digest=original.fence.continuity_digest,
    )
    if (
        compared != original.fence
        or current.command != original.command
        or current.request != original.request
        or _canonical_digest(_native_manifest(current.command)) != admission.runtime_digest
    ):
        raise ConflictError("native original authority drifted")


class _NativeCommitGuard:
    """Cooperative cursor; every call verifies actual current authority."""

    def __init__(self, admission: _NativeAdmission) -> None:
        _revalidate_native_context(admission)
        self.admission = admission
        self.ownership = RunRepository(admission.original._ctx.session).owner_get(
            project_id=admission.original.fence.project_id,
            canonical_item_id=admission.original.request.canonical_item_id,
        )
        self.attempt_id: int | None = None
        self._staged_revision: int | None = None
        self._pending_ack: tuple[object, WorkItemOwnershipOut, RunOut] | None = None
        self._terminal_original: dict[str, Any] | None = None

    def retain_terminal_original(self, original: object, custody: object) -> None:
        from stackos.controlled_process_host import (
            _native_manifest,
            _NativeCustody,
            _TerminalOriginal,
        )

        self()
        if (
            type(original) is not _TerminalOriginal
            or type(custody) is not _NativeCustody
            or custody.admission is not self.admission
            or not custody.active
            or custody.ever_spawn_invoked
            or self._terminal_original is not None
        ):
            raise ConflictError("original terminal custody is not this pre-spawn admission")
        binding = original.binding
        if (
            binding.attempt_run_id != self.attempt_id
            or binding.manifest_digest != self.admission.runtime_digest
            or original.policy != self.admission.policy
            or original.manifest != _native_manifest(self.admission.original.command)
        ):
            raise ConflictError("original terminal policy binding changed")
        self._terminal_original = original.record()

    def retain_terminal_transfer(self, owner: object) -> None:
        from stackos.controlled_process_host import _TerminalOwner

        self()
        if (
            type(owner) is not _TerminalOwner
            or owner.custody.admission is not self.admission
            or self._terminal_original != owner.original.record()
            or owner.slot.close_state != "CLOSED"
            or owner.transfer_directory.state != "closed"
        ):
            raise ConflictError("terminal transferred copies are not actually closed")
        from dataclasses import replace

        closed = {
            "schema_version": "stackos.native-terminal-transfer.v1",
            "binding_digest": owner.slot.binding_digest,
            "dalloc": list(owner.slot.dalloc),
            "fempty": list(owner.slot.fempty),
            "d_writer_closed": True,
            "d_directory_closed": True,
        }
        owner.original = replace(owner.original, transfer_close=closed)
        self._terminal_original = owner.original.record()

    def __call__(self) -> None:
        _revalidate_native_context(
            self.admission, revision=self.ownership.revision, charged=self.attempt_id is not None
        )

    def _owns_reserved_attempt(self, row: object, attempt: RunOut | object) -> bool:
        from stackos.db.models import Run, WorkItemOwnership

        if type(row) is not WorkItemOwnership or not isinstance(attempt, (Run, RunOut)):
            return False
        original = self.admission.original
        ledger = row.continuity_json.get("launch_reservations", {})
        item = ledger.get(original.request.intent_ref) if type(ledger) is dict else None
        return (
            self.attempt_id == attempt.id
            and row.id == self.ownership.id
            and row.owner_ref == original.request.owner_ref
            and row.epoch == original.request.expected_epoch
            and row.scope_digest == original.request.scope_digest
            and type(item) is dict
            and item.get("attempt_run_id") == attempt.id
            and item.get("controller_run_id") == original.fence.controller_run_id
            and item.get("manifest_digest") == self.admission.runtime_digest
        )

    def _check_owner_commit(self, row: object) -> None:
        from stackos.db.models import WorkItemOwnership

        if (
            type(row) is not WorkItemOwnership
            or row.id != self.ownership.id
            or row.revision != self.ownership.revision + 1
            or row.epoch != self.ownership.epoch
            or row.owner_ref != self.ownership.owner_ref
            or row.scope_digest != self.ownership.scope_digest
        ):
            raise ConflictError("native commit is not the fenced own CAS")
        # Called only from _owner_commit after its exact old-revision CAS while
        # SQLite's writer lock is held. It cannot refresh the acknowledged cursor.
        ledger = row.continuity_json.get("launch_reservations", {})
        entry = (
            ledger.get(self.admission.original.request.intent_ref) if type(ledger) is dict else None
        )
        charged = (
            type(entry) is dict and entry.get("manifest_digest") == self.admission.runtime_digest
        )
        _revalidate_native_context(self.admission, revision=row.revision, charged=charged)
        self._staged_revision = row.revision

    def _retain_own_ack(self, repository: RunRepository, result: object) -> None:
        from stackos.repositories.runs import (
            _LaunchReservationOut,
            _NativeTransitionOut,
            _SlotTransitionOut,
        )

        if (
            repository._s is not self.admission.original._ctx.session
            or repository._owner_commit_guard is not self
            or self._pending_ack is not None
        ):
            raise ConflictError("native acknowledgment ownership is unknown")
        if (type(result) is _LaunchReservationOut and result.launch_permit) or (
            type(result) is _SlotTransitionOut and result.applied
        ):
            ownership, attempt = result.ownership, result.attempt
        elif type(result) is _NativeTransitionOut and result.applied:
            ownership, attempt = result.cursor.ownership, result.cursor.attempt
        else:
            raise ConflictError("readback cannot regenerate a native acknowledgment")
        if (
            ownership.revision != self.ownership.revision + 1
            or ownership.revision != self._staged_revision
        ):
            raise ConflictError("native acknowledgment is not this committed transition")
        self._pending_ack = (result, ownership.model_copy(deep=True), attempt.model_copy(deep=True))
        self._staged_revision = None

    def advance(self, acknowledgment: object) -> None:
        from stackos.db.models import Run
        from stackos.repositories.runs import (
            _LaunchReservationOut,
            _NativeTransitionOut,
            _SlotTransitionOut,
        )

        pending = self._pending_ack
        if pending is None or pending[0] is not acknowledgment:
            raise ConflictError("native cursor requires its exact own committed acknowledgment")
        ownership, attempt = pending[1:]
        self._pending_ack = None  # consume once, including denial/lost authority
        if type(acknowledgment) is _NativeTransitionOut:
            supplied = (acknowledgment.cursor.ownership, acknowledgment.cursor.attempt)
        elif (
            type(acknowledgment) is _SlotTransitionOut
            or type(acknowledgment) is _LaunchReservationOut
        ):
            supplied = (acknowledgment.ownership, acknowledgment.attempt)
        else:
            raise ConflictError("native acknowledgment type changed")
        if supplied != (ownership, attempt):
            raise ConflictError("native acknowledgment facts changed")
        session = self.admission.original._ctx.session
        actual = RunRepository(session).owner_get(
            project_id=ownership.project_id, canonical_item_id=ownership.canonical_item_id
        )
        if (
            actual != ownership
            or ownership.id != self.ownership.id
            or ownership.epoch != self.ownership.epoch
            or ownership.owner_ref != self.ownership.owner_ref
            or ownership.scope_digest != self.ownership.scope_digest
            or ownership.revision != self.ownership.revision + 1
        ):
            raise ConflictError("native acknowledgment is not current own progress")
        original = self.admission.original
        entry = ownership.continuity_json.get("launch_reservations", {}).get(
            original.request.intent_ref
        )
        row = session.get(Run, attempt.id)
        if (
            row is None
            or RunOut.model_validate(row) != attempt
            or type(entry) is not dict
            or entry.get("attempt_run_id") != attempt.id
            or entry.get("manifest_digest") != self.admission.runtime_digest
        ):
            raise ConflictError("native acknowledgment names a foreign reservation")
        self.ownership, self.attempt_id = ownership.model_copy(deep=True), attempt.id
        self()


def _controlled_intent_key(authority: _ControlledAuthority) -> str:
    fence, request = authority.fence, authority.request
    return "controlled:" + _canonical_digest(
        {
            "project_id": fence.project_id,
            "controller_run_id": fence.controller_run_id,
            "canonical_item_id": fence.canonical_item_id,
            "epoch": fence.epoch,
            "intent_ref": request.intent_ref,
            "dry_run": False,
        }
    )


@dataclass(frozen=True)
class _ControlledReadback:
    """Read observations, never a forward capability. Every consumer revalidates."""

    input: ActionExecuteInput = field(repr=False)
    context: MCPContext = field(repr=False)
    original_action_call_id: int
    intent_key: str
    request_digest: str
    call_fingerprint: str
    current_owner_revision: int
    normalized_original_revision: int
    route: Literal["repository", "dispatcher-cache"]


def _readback_scope(
    inp: ActionExecuteInput, ctx: MCPContext
) -> tuple[_ControlledCommandInput, Run, RunPlan, RunPlanStep, str]:
    """Authenticate a read without issuing or normalizing a forward authority."""
    _check_controlled_options(inp)
    if (
        current_context() is not ctx
        or ctx.extras.get("trusted_local_admin")
        or ctx.skill_name != RUN_PLAN_CONTROLLER_SKILL
        or not ctx.run_token
    ):
        raise ToolNotGrantedError("controlled action requires current authenticated controller")
    ctx.session.expire_all()
    run, skill = resolve_run_token(ctx.run_token, ctx.session)
    if (
        run is None
        or run.id != ctx.run_id
        or skill != RUN_PLAN_CONTROLLER_SKILL
        or run.project_id != inp.project_id
        or ctx.project_id != inp.project_id
    ):
        raise ToolNotGrantedError("controlled controller binding mismatch")
    ctx.run = run
    check_call_grant("action.execute", ctx, inp)
    plan, step = active_run_plan_step(ctx, "action.execute")
    request = _ControlledCommandInput.model_validate(inp.input_json)
    if plan.id is None or step.id is None:
        raise ToolNotGrantedError("controlled running step identity is missing")
    TrackerRepository(ctx.session).require_owner_execution_binding(
        project_id=inp.project_id,
        canonical_item_id=request.canonical_item_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
    )
    key = "controlled:" + _canonical_digest(
        dict(
            project_id=inp.project_id,
            controller_run_id=run.id,
            canonical_item_id=request.canonical_item_id,
            epoch=request.expected_epoch,
            intent_ref=request.intent_ref,
            dry_run=False,
        )
    )
    if inp.idempotency_key is not None and inp.idempotency_key != key:
        raise ConflictError("controlled intent key mismatch")
    return request, run, plan, step, key


def _resolve_controlled_readback(
    inp: ActionExecuteInput,
    ctx: MCPContext,
    *,
    cached_action_call_id: int | None = None,
) -> _ControlledReadback | None:
    if inp.dry_run or _action_ref(inp) != _CONTROLLED_ACTION_REF:
        return None
    session = ctx.session
    if session.new or session.dirty or session.deleted:
        raise ConflictError("controlled readback requires a clean request session")
    connection = session.connection()
    driver = connection.connection.driver_connection
    if connection.dialect.name != "sqlite" or driver is None or driver.in_transaction:
        raise ConflictError("controlled readback cannot join caller transaction")
    request, run, plan, step, key = _readback_scope(inp, ctx)
    rows = session.exec(
        select(ActionCall)
        .where(ActionCall.idempotency_key == key)
        .execution_options(populate_existing=True)
    ).all()
    if not rows:
        if cached_action_call_id is not None:
            raise ConflictError("controlled cached linkage is unknown")
        return None
    if len(rows) != 1:
        raise ConflictError("controlled original intent is ambiguous")
    row = rows[0]
    if row.id is None:
        raise ConflictError("controlled original identity is unknown")
    if cached_action_call_id is not None and (
        type(cached_action_call_id) is not int or row.id != cached_action_call_id
    ):
        raise ConflictError("controlled cached linkage is unknown")
    if (
        row.project_id != inp.project_id
        or row.run_id != run.id
        or row.run_plan_id != plan.id
        or row.run_plan_step_id != step.id
        or (row.plugin_slug, row.action_key) != ("engineering", "controlled-proof.v1")
        or _canonical_digest(row.request_json) != _canonical_digest(request.model_dump())
    ):
        raise ConflictError("controlled original binding changed")
    if row.status == ActionCallStatus.RUNNING:
        raise ConflictError("controlled_call_in_flight", data={"action_call_id": row.id})
    native_value = (row.metadata_json or {}).get("controlled_native")
    if native_value is None:
        # Historical no-dispatch results retain their exact initial fence.
        authority = _issue_controlled_authority(inp, ctx)
        from stackos.actions import ActionRepository

        ActionRepository(session)._controlled_replay_out(row=row, authority=authority)
        return _ControlledReadback(
            inp.model_copy(deep=True),
            ctx,
            row.id,
            key,
            _canonical_digest(request.model_dump()),
            _canonical_digest(row.model_dump(mode="json")),
            authority.fence.revision,
            authority.fence.revision,
            "dispatcher-cache" if cached_action_call_id is not None else "repository",
        )
    from stackos import controlled_process_receipt as receipt
    from stackos.actions.manifest import parse_action_manifest
    from stackos.controlled_process_host import _native_manifest
    from stackos.repositories.runs import _parse_terminal_facts, _parse_terminal_slot

    native = _ControlledNativeAudit.model_validate(native_value)
    settings = _native_settings(ctx, require_enabled=False)
    _storage, _seed, storage_digest = _native_storage(settings)
    command = _resolve_controlled_command()
    runtime_digest = _canonical_digest(_native_manifest(command))
    owner = session.exec(
        select(WorkItemOwnership)
        .where(
            WorkItemOwnership.project_id == inp.project_id,
            WorkItemOwnership.canonical_item_id == request.canonical_item_id,
        )
        .execution_options(populate_existing=True)
    ).one()
    repository = RunRepository(session)
    try:
        if owner.revision == native.owner_revision_at_finalize + 1:
            # This owner opens/validates its own short readset interval.
            repository._assert_native_certificate_current(owner)
        else:
            connection.exec_driver_sql("BEGIN")
        request, run, plan, step, key = _readback_scope(inp, ctx)
        session.refresh(row)
        session.refresh(owner)
        native = _ControlledNativeAudit.model_validate(
            (row.metadata_json or {}).get("controlled_native")
        )
        if (
            owner.owner_ref != request.owner_ref
            or owner.epoch != request.expected_epoch
            or owner.scope_digest != request.scope_digest
            or owner.revision
            not in {native.owner_revision_at_finalize, native.owner_revision_at_finalize + 1}
            or _canonical_digest(
                repository._native_owner_basis(owner, native.owner_revision_at_finalize)
            )
            != native.owner_basis_digest
        ):
            raise ConflictError("controlled readback owner changed")
        repository.owner_authority(
            project_id=inp.project_id,
            canonical_item_id=request.canonical_item_id,
            ownership=WorkItemOwnershipOut.model_validate(owner),
            require_budget=False,
        )
        action, plugin = session.exec(
            select(Action, Plugin)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .where(Plugin.slug == "engineering", Action.key == "controlled-proof.v1")
            .execution_options(populate_existing=True)
        ).one()
        _check_controlled_manifest(
            parse_action_manifest(action=action, plugin=plugin, provider=None)
        )
        from .execution import _approval_ref_for_action

        approval_key = _approval_ref_for_action(
            plan.grant_snapshot_json or {}, _CONTROLLED_ACTION_REF
        )
        approval = session.exec(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.run_plan_id == plan.id,
                ApprovalRequest.approval_key == approval_key,
            )
            .execution_options(populate_existing=True)
        ).one_or_none()
        admission = (row.metadata_json or {}).get("controlled_admission", {})
        expected = dict(
            schema_version="stackos.controlled-command-approval.v1",
            action_ref=_CONTROLLED_ACTION_REF,
            project_id=inp.project_id,
            controller_run_id=run.id,
            run_plan_id=plan.id,
            run_plan_step_id=step.id,
            canonical_item_id=request.canonical_item_id,
            owner_ref=request.owner_ref,
            epoch=request.expected_epoch,
            scope_digest=request.scope_digest,
            intent_ref=request.intent_ref,
            command_digest=command.digest,
            command=command.descriptor(),
        )
        if approval is None or approval.status != ApprovalRequestStatus.APPROVED:
            raise ConflictError("controlled approval is not current and approved")
        _check_approval_binding(approval.decision_json or {}, expected, command)
        if (
            native.action_call_id != row.id
            or native.command_digest != command.digest
            or request.command_digest != command.digest
            or native.runtime_digest != runtime_digest
            or native.storage_digest != storage_digest
            or native.result_digest != _canonical_digest(row.response_json)
            or admission.get("request_digest") != _canonical_digest(request.model_dump())
            or admission.get("project_id") != inp.project_id
            or admission.get("controller_run_id") != run.id
            or admission.get("run_plan_id") != plan.id
            or admission.get("run_plan_step_id") != step.id
            or admission.get("canonical_item_id") != request.canonical_item_id
            or admission.get("owner_ref") != request.owner_ref
            or admission.get("epoch") != request.expected_epoch
            or admission.get("revision") != request.expected_revision
            or admission.get("scope_digest") != request.scope_digest
            or admission.get("approval_id") != approval.id
            or admission.get("approval_updated_at")
            != (approval.updated_at.isoformat() if approval.updated_at else None)
            or admission.get("approval_decision_digest")
            != _canonical_digest(approval.decision_json)
            or admission.get("grant_digest") != _canonical_digest(plan.grant_snapshot_json)
            or admission.get("step_actions_digest") != _canonical_digest(step.action_refs_json)
        ):
            raise ConflictError("controlled readback provenance changed")
        binding = receipt._Binding(**native.binding)
        attempt = session.get(Run, binding.attempt_run_id, populate_existing=True)
        if (
            binding.project_id != inp.project_id
            or binding.canonical_item_id != request.canonical_item_id
            or binding.owner_ref != request.owner_ref
            or binding.epoch != request.expected_epoch
            or binding.scope_digest != request.scope_digest
            or binding.controller_run_id != run.id
            or binding.run_plan_id != plan.id
            or binding.run_plan_step_id != step.id
            or binding.intent_ref != request.intent_ref
            or binding.manifest_digest != runtime_digest
            or attempt is None
        ):
            raise ConflictError("controlled readback original is foreign")
        metadata = attempt.metadata_json or {}
        slot = _parse_terminal_slot(metadata.get("native_terminal_slot"))
        facts = _parse_terminal_facts(metadata.get("native_terminal_facts"), binding)
        if (
            slot["phase"] != "COMMITTED"
            or slot["receipt_digest"] != native.terminal_receipt_digest
            or slot["revision"] != native.terminal_slot_revision
            or facts["revision"] != 1
            or facts["entries"][0]["receipt_digest"] != native.terminal_receipt_digest
        ):
            raise ConflictError("controlled readback terminal is unknown")
        from stackos.actions import ActionRepository

        result = ActionRepository(session)._public_action_result(row)
        if result is None or result["outcome_unknown"]:
            raise ConflictError("controlled_history_unknown", data={"action_call_id": row.id})
        return _ControlledReadback(
            inp.model_copy(deep=True),
            ctx,
            row.id,
            key,
            _canonical_digest(request.model_dump()),
            _canonical_digest(row.model_dump(mode="json")),
            owner.revision,
            native.owner_revision_at_finalize,
            "dispatcher-cache" if cached_action_call_id is not None else "repository",
        )
    finally:
        # No caller work/physical transaction was present on entry. Release only
        # the owned read/certificate verification interval; no persistent writes.
        session.rollback()


def _validate_controlled_cached_replay(
    inp: ActionExecuteInput,
    ctx: MCPContext,
    cached: dict[str, Any],
) -> dict[str, Any]:
    data = cached.get("data")
    call = data.get("action_call") if isinstance(data, dict) else None
    call_id = call.get("id") if type(call) is dict else None
    row = (
        ctx.session.get(ActionCall, call_id, populate_existing=True)
        if type(call_id) is int
        else None
    )
    requested = _action_ref(inp) == _CONTROLLED_ACTION_REF
    canonical = row is not None and f"{row.plugin_slug}.{row.action_key}" == _CONTROLLED_ACTION_REF
    if not requested and not canonical:
        return cached
    if not requested or row is None or not canonical:
        raise ConflictError("controlled cached linkage is unknown")
    if (
        type(cached.get("project_id")) is not int
        or cached["project_id"] != ctx.project_id
        or type(cached.get("run_id")) is not int
        or cached["run_id"] != ctx.run_id
    ):
        raise ConflictError("controlled cached envelope identity is unknown")
    readback = _resolve_controlled_readback(inp, ctx, cached_action_call_id=row.id)
    from stackos.actions import ActionRepository

    repo = ActionRepository(ctx.session)
    if readback is None:
        raise ConflictError("controlled cached linkage is unknown")
    out = repo._controlled_native_readback(readback=readback)
    from stackos.mcp.contract import WriteEnvelope

    # Cached data is linkage only. Rebuild the WHOLE declared public envelope;
    # no outer extension, private field or cached identity can escape raw mode.
    return WriteEnvelope(data=out, project_id=ctx.project_id, run_id=ctx.run_id).model_dump(
        mode="json"
    )
