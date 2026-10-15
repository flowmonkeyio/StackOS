"""Run audit operation contracts."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, model_validator

from stackos.db.model_enums import RunAttemptDisposition, RunObservationState
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.permissions import active_run_plan_step, check_call_grant
from stackos.mcp.streaming import ProgressEmitter
from stackos.mcp.tools.runs import (
    RunAbortInput,
    RunChildrenInput,
    RunCostInput,
    RunFinishInput,
    RunGetInput,
    RunHeartbeatInput,
    RunListInput,
    RunStartInput,
    RunStartOutput,
    RunStepCallListInput,
    RunStepCallRecordInput,
    RunStepInsertInput,
    RunStepListInput,
    _run_abort,
    _run_children,
    _run_cost,
    _run_finish,
    _run_get,
    _run_heartbeat,
    _run_list,
    _run_start,
    _run_step_call_list,
    _run_step_call_record,
    _run_step_insert,
    _run_step_list,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import (
    OperationExample,
    OperationHandler,
    OperationResponsePolicy,
    OperationSpec,
)
from stackos.repositories.base import ConflictError, NotFoundError, Page
from stackos.repositories.runs import (
    RunOut,
    RunRepository,
    RunStepCallOut,
    RunStepOut,
    WorkItemOwnershipOut,
)
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.tracker.schema import TrackerOwnerItemOut


class OwnerResolveInput(MCPInput):
    project_id: StrictInt = Field(gt=0)
    canonical_item_id: str | None = None
    alias_ref: str | None = None


class OwnerGetInput(MCPInput):
    project_id: StrictInt = Field(gt=0)
    canonical_item_id: str = Field(pattern=r"^(task|ticket):[1-9][0-9]*$")


class OwnerAcquireInput(OwnerGetInput):
    owner_ref: str = Field(min_length=1, max_length=160)
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class OwnerFenceInput(OwnerAcquireInput):
    expected_epoch: StrictInt = Field(gt=0)
    expected_revision: StrictInt = Field(gt=0)


class OwnerCheckpointInput(OwnerFenceInput):
    checkpoint_ref: str = Field(min_length=1, max_length=1000)


class OwnerStopInput(OwnerFenceInput):
    reason_ref: str = Field(min_length=1, max_length=1000)


class OwnerReassignInput(OwnerFenceInput):
    new_owner_ref: str | None = Field(min_length=1, max_length=160)


class OwnerConsumeInput(OwnerFenceInput):
    attempts_delta: StrictInt = Field(default=0, ge=0)
    failures_delta: StrictInt = Field(default=0, ge=0)
    evidence_ref: str = Field(min_length=1, max_length=1000)


class OwnerRecordAttemptInput(OwnerFenceInput):
    attempt_run_id: StrictInt = Field(gt=0)
    observation: (
        Literal["running", "waiting", "suspected_stall", "blocked", "scope_drift", "review_ready"]
        | None
    ) = None
    disposition: RunAttemptDisposition | None = None
    evidence_ref: str = Field(min_length=1, max_length=1000)
    clearance_ref: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def one_annotation(self) -> OwnerRecordAttemptInput:
        if (self.observation is None) == (self.disposition is None):
            raise ValueError("record exactly one reported observation or terminal disposition")
        return self


class OwnerInspectionOut(BaseModel):
    ownership: WorkItemOwnershipOut
    captured_at: datetime
    authority_valid: bool
    authority_issues: list[str]


class OwnerMutationOut(BaseModel):
    ownership: WorkItemOwnershipOut
    request_digest: str
    acquired: bool | None = None
    attempt: RunOut | None = None


async def _owner_resolve(
    inp: OwnerResolveInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> TrackerOwnerItemOut:
    return TrackerRepository(ctx.session).resolve_owner_item(
        project_id=inp.project_id,
        canonical_item_id=inp.canonical_item_id,
        alias_ref=inp.alias_ref,
    )


async def _owner_get(
    inp: OwnerGetInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> OwnerInspectionOut:
    repo = RunRepository(ctx.session)
    ownership = repo.owner_get(project_id=inp.project_id, canonical_item_id=inp.canonical_item_id)
    issues = []
    try:
        repo.owner_authority(
            project_id=inp.project_id,
            canonical_item_id=inp.canonical_item_id,
            ownership=ownership,
            require_budget=True,
        )
    except (ConflictError, NotFoundError) as exc:
        issues.append(str(exc))
    return OwnerInspectionOut(
        ownership=ownership,
        captured_at=datetime.now(UTC),
        authority_valid=not issues,
        authority_issues=issues,
    )


def owner_request_digest(name: str, inp: OwnerAcquireInput, ctx: MCPContext) -> str:
    captured = ctx.extras.get("ownership_request_digest")
    if isinstance(captured, str):
        return captured
    plan, step = active_run_plan_step(ctx, name)
    source = {
        "operation": name,
        "run_id": ctx.run_id,
        "run_plan_id": plan.id,
        "step_id": step.id,
        "input": inp.model_dump(
            mode="json",
            exclude={
                "run_token",
                "idempotency_key",
                "response_mode",
                "expected_etag",
            },
        ),
    }
    digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    ctx.extras["ownership_request_digest"] = digest
    return digest


def _validate_owner_counter_floor(name: str, inp: OwnerAcquireInput, repo: RunRepository) -> None:
    if name in {"run.ownerStop", "run.ownerReassign", "run.ownerRecordAttempt"}:
        return
    row = repo.owner_get(project_id=inp.project_id, canonical_item_id=inp.canonical_item_id)
    source = repo.owner_authority(
        project_id=inp.project_id,
        canonical_item_id=inp.canonical_item_id,
        ownership=row,
        allow_stopped=True,
        require_budget=True,
    )
    if any(row.continuity_json[key] < source[key] for key in ("attempts_used", "failures_used")):
        raise ConflictError("canonical counters advanced; reconcile continuity before publication")


def _owner_preflight(name: str, inp: OwnerAcquireInput, ctx: MCPContext) -> RunRepository:
    plan, step = active_run_plan_step(ctx, name)
    execution_fence = (ctx.run_id, plan.id, step.id, inp.canonical_item_id)
    captured = ctx.extras.setdefault("ownership_execution_fence", execution_fence)
    if captured != execution_fence:
        raise ConflictError("ownership execution context changed during the call")
    owner_request_digest(name, inp, ctx)

    def current_commit_authority() -> None:
        ctx.session.expire_all()
        check_call_grant(name, ctx, inp)
        current_repo = _owner_preflight(name, inp, ctx)
        _validate_owner_counter_floor(name, inp, current_repo)

    repo = RunRepository(
        ctx.session, audit_run_id=ctx.run_id, owner_commit_guard=current_commit_authority
    )
    try:
        row = repo.owner_get(project_id=inp.project_id, canonical_item_id=inp.canonical_item_id)
    except NotFoundError:
        if name != "run.ownerAcquire":
            raise
        row = None
    if row is not None and row.scope_digest != inp.scope_digest:
        raise ConflictError("ownership scope changed; adjudication required")
    is_admission = name == "run.ownerAcquire" or (
        isinstance(inp, OwnerReassignInput) and inp.new_owner_ref is not None
    )
    # Stops/revocation and late accounting can preserve a stopped assignment.
    reported_only = isinstance(inp, OwnerRecordAttemptInput) and inp.clearance_ref is None
    if (
        name != "run.ownerStop"
        and not reported_only
        and not (isinstance(inp, OwnerReassignInput) and inp.new_owner_ref is None)
    ):
        current = repo.owner_authority(
            project_id=inp.project_id,
            canonical_item_id=inp.canonical_item_id,
            ownership=row,
            allow_stopped=isinstance(inp, OwnerConsumeInput) and inp.attempts_delta == 0,
            require_budget=True,
        )
        stored = row.continuity_json if row is not None else current
        if is_admission and (
            max(stored["attempts_used"], current["attempts_used"]) >= current["attempt_limit"]
            or max(stored["failures_used"], current["failures_used"]) >= current["failure_limit"]
        ):
            raise ConflictError("original cumulative budget is exhausted; no owner admission")
    return repo


def _owner_receipt(
    name: str,
    inp: OwnerAcquireInput,
    ctx: MCPContext,
    ownership: WorkItemOwnershipOut,
    *,
    acquired: bool | None = None,
    attempt: RunOut | None = None,
) -> WriteEnvelope[OwnerMutationOut]:
    return WriteEnvelope[OwnerMutationOut](
        data=OwnerMutationOut(
            ownership=ownership,
            request_digest=owner_request_digest(name, inp, ctx),
            acquired=acquired,
            attempt=attempt,
        ),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


def validate_owner_replay(
    name: str,
    inp: OwnerAcquireInput,
    ctx: MCPContext,
    cached: dict[str, Any],
) -> dict[str, Any]:
    data = cached.get("data", {})
    if data.get("request_digest") != owner_request_digest(name, inp, ctx):
        raise ConflictError("ownership idempotency key belongs to another request/context")
    repo = _owner_preflight(name, inp, ctx)
    _validate_owner_counter_floor(name, inp, repo)
    current = repo.owner_get(project_id=inp.project_id, canonical_item_id=inp.canonical_item_id)
    if current.model_dump(mode="json") != data.get("ownership"):
        raise ConflictError("cached ownership fence is stale; read current assignment")
    out = copy.deepcopy(cached)
    if name == "run.ownerAcquire":
        out["data"]["acquired"] = False
    return out


async def _owner_acquire(
    inp: OwnerAcquireInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[OwnerMutationOut]:
    repo = _owner_preflight("run.ownerAcquire", inp, ctx)
    result = repo.owner_acquire(
        project_id=inp.project_id,
        canonical_item_id=inp.canonical_item_id,
        owner_ref=inp.owner_ref,
        scope_digest=inp.scope_digest,
    )
    return _owner_receipt("run.ownerAcquire", inp, ctx, result.ownership, acquired=result.acquired)


def _fence_arguments(inp: OwnerFenceInput) -> dict[str, Any]:
    return inp.model_dump(
        include={
            "project_id",
            "canonical_item_id",
            "owner_ref",
            "scope_digest",
            "expected_epoch",
            "expected_revision",
        }
    )


async def _owner_checkpoint(
    inp: OwnerCheckpointInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[OwnerMutationOut]:
    repo = _owner_preflight("run.ownerCheckpoint", inp, ctx)
    row = repo.owner_checkpoint(**_fence_arguments(inp), checkpoint_ref=inp.checkpoint_ref)
    return _owner_receipt("run.ownerCheckpoint", inp, ctx, row)


async def _owner_stop(
    inp: OwnerStopInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[OwnerMutationOut]:
    repo = _owner_preflight("run.ownerStop", inp, ctx)
    arguments = _fence_arguments(inp)
    arguments.pop("scope_digest")
    row = repo.owner_stop(**arguments, reason_ref=inp.reason_ref)
    return _owner_receipt("run.ownerStop", inp, ctx, row)


async def _owner_reassign(
    inp: OwnerReassignInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[OwnerMutationOut]:
    repo = _owner_preflight("run.ownerReassign", inp, ctx)
    row = repo.owner_reassign(**_fence_arguments(inp), new_owner_ref=inp.new_owner_ref)
    return _owner_receipt("run.ownerReassign", inp, ctx, row)


async def _owner_consume(
    inp: OwnerConsumeInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[OwnerMutationOut]:
    repo = _owner_preflight("run.ownerConsume", inp, ctx)
    row = repo.owner_consume(
        **_fence_arguments(inp),
        attempts_delta=inp.attempts_delta,
        failures_delta=inp.failures_delta,
        evidence_ref=inp.evidence_ref,
    )
    return _owner_receipt("run.ownerConsume", inp, ctx, row)


async def _owner_record_attempt(
    inp: OwnerRecordAttemptInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[OwnerMutationOut]:
    repo = _owner_preflight("run.ownerRecordAttempt", inp, ctx)
    assert ctx.run_id is not None
    result = repo._record_owner_attempt(
        **_fence_arguments(inp),
        controller_run_id=ctx.run_id,
        attempt_run_id=inp.attempt_run_id,
        observation=RunObservationState(inp.observation) if inp.observation is not None else None,
        disposition=inp.disposition,
        clearance_ref=inp.clearance_ref,
        evidence_ref=inp.evidence_ref,
    )
    return _owner_receipt(
        "run.ownerRecordAttempt", inp, ctx, result.ownership, attempt=result.attempt
    )


def operation_specs() -> list[OperationSpec]:
    return [
        operation_spec(
            name="run.start",
            summary="Open an audited run and return its run token.",
            input_model=RunStartInput,
            output_model=WriteEnvelope[RunStartOutput],
            handler=_run_start,
            purpose=(
                "Use this for low-level audited execution. Workflow execution normally uses "
                "runPlan.start, which creates and binds the run plan run automatically."
            ),
            when_to_use=("A non-workflow tool flow still needs an audited run boundary.",),
            returns=("A write envelope containing run_id and run_token.",),
            examples=(
                OperationExample(
                    title="Start manual run",
                    arguments={
                        "project_id": 1,
                        "kind": "skill-run",
                        "metadata_json": {"stackos_type": "manual-run"},
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.finish",
            summary="Move an audited run to a terminal status.",
            input_model=RunFinishInput,
            output_model=WriteEnvelope[RunOut],
            handler=_run_finish,
            purpose="Use this to close a low-level audited run.",
            examples=(
                OperationExample(title="Finish run", arguments={"run_id": 1, "status": "success"}),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.heartbeat",
            summary="Refresh heartbeat for an audited run.",
            input_model=RunHeartbeatInput,
            output_model=WriteEnvelope[RunOut | None],
            handler=_run_heartbeat,
            purpose="Use this when a long-running audited process needs liveness.",
            examples=(OperationExample(title="Heartbeat run", arguments={"run_id": 1}),),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.abort",
            summary="Abort an audited run.",
            input_model=RunAbortInput,
            output_model=WriteEnvelope[RunOut],
            handler=_run_abort,
            purpose="Use this to explicitly stop a low-level audited run.",
            examples=(
                OperationExample(title="Abort run", arguments={"run_id": 1, "cascade": True}),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.list",
            summary="List audited runs.",
            input_model=RunListInput,
            output_model=Page[RunOut],
            handler=_run_list,
            purpose="Use this to inspect recent audited activity.",
            examples=(OperationExample(title="List runs", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.get",
            summary="Fetch one audited run.",
            input_model=RunGetInput,
            output_model=RunOut,
            handler=_run_get,
            purpose="Use this to inspect run metadata, status, and project scope.",
            examples=(OperationExample(title="Get run", arguments={"run_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.children",
            summary="List direct child runs.",
            input_model=RunChildrenInput,
            output_model=list[RunOut],
            handler=_run_children,
            purpose="Use this to navigate parent/child audited run structure.",
            examples=(OperationExample(title="List child runs", arguments={"parent_run_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.cost",
            summary="Sum run costs for one project and month.",
            input_model=RunCostInput,
            output_model=dict[str, Any],
            handler=_run_cost,
            purpose="Use this to inspect audited run spend by kind.",
            examples=(OperationExample(title="Read run cost", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.insertStep",
            summary="Insert a low-level run step audit row.",
            input_model=RunStepInsertInput,
            output_model=WriteEnvelope[RunStepOut],
            handler=_run_step_insert,
            purpose="Use this only for low-level audited execution outside run plans.",
            prerequisites=("Prefer runPlan.claimStep/recordStep for workflow execution.",),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.listSteps",
            summary="List low-level run step audit rows.",
            input_model=RunStepListInput,
            output_model=list[RunStepOut],
            handler=_run_step_list,
            purpose="Use this to inspect audit detail for one run.",
            examples=(OperationExample(title="List run steps", arguments={"run_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="run.recordStepCall",
            summary="Record one low-level MCP/tool call audit row.",
            input_model=RunStepCallRecordInput,
            output_model=WriteEnvelope[RunStepCallOut],
            handler=_run_step_call_record,
            purpose="Use this only for low-level audited execution outside run plans.",
            prerequisites=("Prefer run-plan step audit for workflow execution.",),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        operation_spec(
            name="run.listStepCalls",
            summary="List low-level tool-call audit rows for one run step.",
            input_model=RunStepCallListInput,
            output_model=list[RunStepCallOut],
            handler=_run_step_call_list,
            purpose="Use this to inspect recorded tool calls for a run step.",
            examples=(OperationExample(title="List step calls", arguments={"run_step_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        *_ownership_specs(),
    ]


def _ownership_specs() -> list[OperationSpec]:
    policy = OperationResponsePolicy(
        default_mode="compact",
        allowed_modes=("compact", "raw"),
        ack_safe=False,
        compact_notes=(
            "Keep canonical identity, full fence, continuity, request digest and replay.",
        ),
    )
    rows: list[tuple[str, type[MCPInput], Any, OperationHandler, str, bool]] = [
        (
            "run.ownerResolve",
            OwnerResolveInput,
            TrackerOwnerItemOut,
            _owner_resolve,
            "Resolve native identity or an existing external alias without acquiring ownership.",
            False,
        ),
        (
            "run.ownerGet",
            OwnerGetInput,
            OwnerInspectionOut,
            _owner_get,
            "Inspect stored ownership plus current canonical authority and capture time.",
            False,
        ),
        (
            "run.ownerAcquire",
            OwnerAcquireInput,
            WriteEnvelope[OwnerMutationOut],
            _owner_acquire,
            "Reserve one canonical owner without launching a worker.",
            True,
        ),
        (
            "run.ownerCheckpoint",
            OwnerCheckpointInput,
            WriteEnvelope[OwnerMutationOut],
            _owner_checkpoint,
            "Record a checkpoint at the current ownership fence.",
            True,
        ),
        (
            "run.ownerStop",
            OwnerStopInput,
            WriteEnvelope[OwnerMutationOut],
            _owner_stop,
            "Persist an irreversible scope stop without signalling a process.",
            True,
        ),
        (
            "run.ownerReassign",
            OwnerReassignInput,
            WriteEnvelope[OwnerMutationOut],
            _owner_reassign,
            "Explicitly revoke or replace ownership using backend-certified quiescence.",
            True,
        ),
        (
            "run.ownerConsume",
            OwnerConsumeInput,
            WriteEnvelope[OwnerMutationOut],
            _owner_consume,
            "Record monotone cumulative consumption from current canonical continuity.",
            True,
        ),
        (
            "run.ownerRecordAttempt",
            OwnerRecordAttemptInput,
            WriteEnvelope[OwnerMutationOut],
            _owner_record_attempt,
            "Annotate an existing bound child run; never launch or accept work.",
            True,
        ),
    ]
    return [
        operation_spec(
            name=name,
            input_model=model,
            output_model=output,
            handler=handler,
            summary=summary,
            purpose=summary,
            mutating=mutating,
            category="workflow",
            grant_policy="run-plan-step-item-grant" if mutating else "direct-read",
            response_policy=policy,
            prerequisites=(
                "Writes require a running controller/step, an explicit canonical-item target grant "
                "and persisted plan/step binding. Existing grant snapshots stay unchanged.",
                "Controller tokens stay controller-held; owner_ref names an assignment.",
                "No host certification operation is exposed. A receipt cannot authorize control.",
            )
            if mutating
            else (),
            examples=(
                OperationExample(
                    title="Inspect one native work item",
                    arguments={"project_id": 1, "canonical_item_id": "ticket:1"},
                ),
            )
            if not mutating
            else (),
        )
        for name, model, output, handler, summary, mutating in rows
    ]


__all__ = ["operation_specs"]
