"""Current authenticated admission for the opt-in Engineering action.

The private object is cooperative data. Every use repeats canonical checks;
possession, request ids, and a running step alone confer no authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError as ModelValidationError
from sqlmodel import select

from stackos.actions.controlled_command import (
    _CONTROLLED_ACTION_REF,
    _canonical_digest,
    _check_controlled_manifest,
    _ControlledCommandInput,
    _resolve_controlled_command,
    _ResolvedControlledCommand,
)
from stackos.db.models import ActionCall, ActionCallStatus, ApprovalRequest, ApprovalRequestStatus
from stackos.mcp.context import MCPContext, current_context
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.permissions import (
    RUN_PLAN_CONTROLLER_SKILL,
    active_run_plan_step,
    check_call_grant,
    resolve_run_token,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.runs import RunRepository
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
        or owner.revision != request.expected_revision
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
        if type(floor) is not int or value < floor or value >= continuity[limit]:
            raise ConflictError("controlled continuity is exhausted or regressed")
    from stackos.actions import ActionRepository

    described = ActionRepository(ctx.session).describe(
        project_id=inp.project_id, action_ref=_CONTROLLED_ACTION_REF
    )
    _check_controlled_manifest(described.manifest)
    if (
        described.availability.status != "not_executable"
        or "controlled_host_unavailable" not in described.availability.reasons
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


def _validate_controlled_cached_replay(
    inp: ActionExecuteInput,
    ctx: MCPContext,
    cached: dict[str, Any],
) -> dict[str, Any]:
    data = cached.get("data")
    call = data.get("action_call") if isinstance(data, dict) else None
    row = ctx.session.get(ActionCall, call.get("id")) if isinstance(call, dict) else None
    requested = _action_ref(inp) == _CONTROLLED_ACTION_REF
    canonical = row is not None and f"{row.plugin_slug}.{row.action_key}" == _CONTROLLED_ACTION_REF
    if not requested and not canonical:
        return cached
    if not requested or row is None or not canonical:
        raise ConflictError("controlled cached linkage is unknown")
    authority = _issue_controlled_authority(inp, ctx)
    from stackos.actions import ActionRepository

    repo = ActionRepository(ctx.session)
    out = repo._controlled_replay_out(row=row, authority=authority)
    if (
        row.project_id != inp.project_id
        or row.run_id != authority.fence.controller_run_id
        or row.run_plan_id != authority.fence.run_plan_id
        or row.run_plan_step_id != authority.fence.run_plan_step_id
        or row.status != ActionCallStatus.FAILED
    ):
        raise ConflictError("controlled cached history is incompatible")
    result = deepcopy(cached)
    result["data"] = out.model_dump(mode="json")
    return result
