"""StackOS run-plan operation registrations."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.db.models import (
    ApprovalRequestStatus,
    RunPlanStatus,
    RunPlanStepStatus,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.run_plans import (
    RunPlanConsistencyOut,
    RunPlanOut,
    RunPlanReopenOut,
    RunPlanRepository,
    RunPlanStartOut,
    RunPlanStepOut,
    RunPlanSummaryOut,
)
from stackos.workflows import RunPlanValidationOut


class RunPlanValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"workflow_key": "core.project-memory-review"}},
    )

    project_id: int | None = None
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    workflow_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    inputs_json: dict[str, Any] | None = None
    selected_context_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    enforce_required_inputs: bool = False


class RunPlanCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "workflow_key": "core.project-memory-review",
                "inputs_json": {"goal": "Review recent project memory"},
            }
        },
    )

    project_id: int
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    workflow_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    key: str | None = None
    title: str | None = None
    inputs_json: dict[str, Any] | None = None
    context_snapshot_id: int | None = None
    selected_context_json: dict[str, Any] | None = None
    created_by: str | None = None
    metadata_json: dict[str, Any] | None = None


class RunPlanStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "run_plan_id": 1}},
    )

    project_id: int
    run_plan_id: int


class RunPlanGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"run_plan_id": 1}})

    run_plan_id: int
    project_id: int | None = None


class RunPlanGetStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1, "step_id": "review"}},
    )

    run_plan_id: int
    step_id: str
    project_id: int | None = None


class RunPlanCheckConsistencyInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1}},
    )

    run_plan_id: int
    project_id: int | None = None


class RunPlanRecoverInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "step_id": "plan-tickets",
                "step_status": "blocked",
                "reason": "Recover stale daemon/controller failure as a live blocker.",
                "error": "Recoverable controller failure needs review.",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str
    step_status: RunPlanStepStatus = RunPlanStepStatus.BLOCKED
    reason: str | None = None
    actor: str | None = None
    result_json: dict[str, Any] | None = None
    error: str | None = None


class RunPlanReopenInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 27,
                "reason": (
                    "Admin selected this closed run plan as the canonical in-place recovery target."
                ),
                "actor": "stackos-admin",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str | None = None
    reason: str
    actor: str | None = None


class RunPlanListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None
    run_id: int | None = None
    status: RunPlanStatus | None = None
    template_key: str | None = None
    workflow_key: str | None = None
    limit: int | None = None
    after_id: int | None = None


class RunPlanUpdateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "approval_key": "launch-review",
                "approval_status": "approved",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    metadata_json: dict[str, Any] | None = None
    approval_key: str | None = None
    approval_status: ApprovalRequestStatus | None = None
    decided_by: str | None = None
    decision_json: dict[str, Any] | None = None


class RunPlanAbortInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "reason": "Superseded by a newer support workflow run.",
                "actor": "codex",
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    reason: str | None = None
    actor: str | None = None


class RunPlanClaimStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1, "step_id": "review"}},
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str | None = None
    claimed_by: str | None = None


class RunPlanRecordStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "run_plan_id": 1,
                "step_id": "review",
                "status": "success",
                "result_json": {"summary": "done"},
            }
        },
    )

    run_plan_id: int
    project_id: int | None = None
    step_id: str
    status: RunPlanStepStatus
    result_json: dict[str, Any] | None = None
    error: str | None = None


def _template_key(template_key: str | None, workflow_key: str | None) -> str | None:
    if template_key is not None and workflow_key is not None and template_key != workflow_key:
        raise ValidationError(
            "template_key and workflow_key must match when both are provided",
            data={"template_key": template_key, "workflow_key": workflow_key},
        )
    return template_key if template_key is not None else workflow_key


async def run_plan_validate(
    inp: RunPlanValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanValidationOut:
    return RunPlanRepository(ctx.session).validate_plan(
        run_plan_json=inp.run_plan_json,
        template_key=_template_key(inp.template_key, inp.workflow_key),
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
        inputs_json=inp.inputs_json,
        selected_context_json=inp.selected_context_json,
        metadata_json=inp.metadata_json,
        enforce_required_inputs=inp.enforce_required_inputs,
    )


async def run_plan_create(
    inp: RunPlanCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).create(
        project_id=inp.project_id,
        run_plan_json=inp.run_plan_json,
        template_key=_template_key(inp.template_key, inp.workflow_key),
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
        key=inp.key,
        title=inp.title,
        inputs_json=inp.inputs_json,
        context_snapshot_id=inp.context_snapshot_id,
        selected_context_json=inp.selected_context_json,
        created_by=inp.created_by,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_start(
    inp: RunPlanStartInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanStartOut]:
    env = RunPlanRepository(ctx.session).start(inp.run_plan_id, project_id=inp.project_id)
    return WriteEnvelope[RunPlanStartOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


async def run_plan_get(
    inp: RunPlanGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanOut:
    return RunPlanRepository(ctx.session).get(inp.run_plan_id, project_id=inp.project_id)


async def run_plan_get_step(
    inp: RunPlanGetStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanStepOut:
    return RunPlanRepository(ctx.session).get_step(
        inp.run_plan_id,
        inp.step_id,
        project_id=inp.project_id,
    )


async def run_plan_check_consistency(
    inp: RunPlanCheckConsistencyInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanConsistencyOut:
    return RunPlanRepository(ctx.session).check_consistency(
        inp.run_plan_id,
        project_id=inp.project_id,
    )


async def run_plan_recover(
    inp: RunPlanRecoverInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).recover(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        step_id=inp.step_id,
        step_status=inp.step_status,
        reason=inp.reason,
        actor=inp.actor,
        result_json=inp.result_json,
        error=inp.error,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_reopen(
    inp: RunPlanReopenInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanReopenOut]:
    env = RunPlanRepository(ctx.session).reopen(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        step_id=inp.step_id,
        reason=inp.reason,
        actor=inp.actor,
    )
    return WriteEnvelope[RunPlanReopenOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


async def run_plan_list(
    inp: RunPlanListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[RunPlanSummaryOut]:
    return RunPlanRepository(ctx.session).list(
        project_id=inp.project_id,
        run_id=inp.run_id,
        status=inp.status,
        template_key=_template_key(inp.template_key, inp.workflow_key),
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def run_plan_update(
    inp: RunPlanUpdateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).update(
        run_plan_id=inp.run_plan_id,
        metadata_json=inp.metadata_json,
        approval_key=inp.approval_key,
        approval_status=inp.approval_status,
        decided_by=inp.decided_by,
        decision_json=inp.decision_json,
        project_id=inp.project_id,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_abort(
    inp: RunPlanAbortInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).abort(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        reason=inp.reason,
        actor=inp.actor,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def run_plan_claim_step(
    inp: RunPlanClaimStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanStepOut]:
    env = RunPlanRepository(ctx.session).claim_step(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        run_id=ctx.run_id,
        step_id=inp.step_id,
        claimed_by=inp.claimed_by,
    )
    return WriteEnvelope[RunPlanStepOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


async def run_plan_record_step(
    inp: RunPlanRecordStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).record_step(
        run_plan_id=inp.run_plan_id,
        project_id=inp.project_id,
        run_id=ctx.run_id,
        step_id=inp.step_id,
        status=inp.status,
        result_json=inp.result_json,
        error=inp.error,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


def _surfaces(name: str, command: str | None = None) -> OperationSurfaces:
    rest_path = f"/api/v1/operations/{name}/call"
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=rest_path),
        cli=OperationSurface(enabled=True, command=command or f"ops call {name}"),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="runPlan.validate",
            summary="Validate a concrete run plan or template-derived plan without saving it.",
            input_model=RunPlanValidateInput,
            output_model=RunPlanValidationOut,
            handler=run_plan_validate,
            surfaces=_surfaces("runPlan.validate", "run-plans validate"),
            purpose=(
                "Use this before creating a run plan to verify the workflow shape, "
                "grants, approvals, context filters, and secret hygiene."
            ),
            when_to_use=(
                "An agent has authored run_plan_json and wants a schema and policy check.",
                "A script wants to verify that a workflow template can become a run plan.",
            ),
            prerequisites=(
                "Pass run_plan_json for an explicit plan or workflow_key/template_key for a "
                "template-derived plan.",
                "Pass project_id when the validation depends on project templates.",
                "Set enforce_required_inputs=true and pass inputs_json when validating a "
                "concrete template-derived create request.",
            ),
            returns=(
                "valid=true with the normalized plan when validation passes.",
                "Structured validation issues with paths and machine-readable codes.",
            ),
            examples=(
                OperationExample(
                    title="Validate a template-derived plan",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "core.project-memory-review",
                        "inputs_json": {"goal": "Review recent project memory"},
                        "enforce_required_inputs": True,
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.create",
            summary="Create a durable run plan from a template or explicit plan JSON.",
            input_model=RunPlanCreateInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_create,
            surfaces=_surfaces("runPlan.create", "run-plans create"),
            purpose=(
                "Use this after selecting or authoring the workflow setup. Creation freezes "
                "the plan, step order, grants, context filters, approvals, and output contract."
            ),
            when_to_use=(
                "The agent is ready to turn reusable workflow setup into a specific execution.",
                "A script wants to create a repeatable run instance without starting it yet.",
            ),
            prerequisites=(
                "Pass project_id.",
                "Pass either run_plan_json or workflow_key/template_key.",
                "Do not place secrets in metadata_json, selected_context_json, or run_plan_json.",
            ),
            returns=(
                "A WriteEnvelope containing the saved run plan, steps, approval gates, "
                "and snapshots.",
                "The saved run plan remains draft until runPlan.start is called.",
            ),
            examples=(
                OperationExample(
                    title="Create a plan from a project template",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "core.project-memory-review",
                        "inputs_json": {"goal": "Review recent project memory"},
                    },
                ),
                OperationExample(
                    title="Create a one-step explicit plan",
                    arguments={
                        "project_id": 1,
                        "run_plan_json": {
                            "schema_version": "stackos.run-plan.v1",
                            "key": "manual.review.run",
                            "title": "Manual review",
                            "steps": [{"id": "review", "title": "Review"}],
                        },
                    },
                ),
            ),
            grant_policy="direct-bootstrap-write",
        ),
        OperationSpec(
            name="runPlan.start",
            summary="Start a draft run plan and return the scoped run token.",
            input_model=RunPlanStartInput,
            output_model=WriteEnvelope[RunPlanStartOut],
            handler=run_plan_start,
            surfaces=_surfaces("runPlan.start", "run-plans start"),
            purpose=(
                "Use this to begin execution. The returned run_token is the only value an "
                "agent should pass to run-plan step tools; it is not a credential secret."
            ),
            when_to_use=(
                "A draft plan is approved enough to execute.",
                "A CLI or REST caller wants to hand an agent a scoped run token for "
                "step execution.",
            ),
            prerequisites=(
                "Pass project_id and run_plan_id.",
                "The run plan must be in draft status.",
            ),
            returns=(
                "The started plan.",
                "The linked run audit row.",
                "A run_token scoped to the run-plan controller.",
            ),
            examples=(
                OperationExample(
                    title="Start a run plan",
                    arguments={"project_id": 1, "run_plan_id": 42},
                ),
            ),
            grant_policy="direct-bootstrap-write",
        ),
        OperationSpec(
            name="runPlan.get",
            summary="Fetch one run plan with steps, approvals, grants, and snapshots.",
            input_model=RunPlanGetInput,
            output_model=RunPlanOut,
            handler=run_plan_get,
            surfaces=_surfaces("runPlan.get", "run-plans get"),
            purpose=(
                "Use this to inspect the current plan state before choosing the next tool call "
                "or to refresh context after a step changes status."
            ),
            when_to_use=(
                "The agent needs current step, approval, or output state.",
                "A human or script wants to inspect a saved plan.",
            ),
            prerequisites=("Pass run_plan_id.",),
            returns=("The full run plan object, including steps and approval requests.",),
            examples=(OperationExample(title="Fetch a run plan", arguments={"run_plan_id": 42}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.getStep",
            summary="Fetch one run-plan step with its complete persisted result.",
            input_model=RunPlanGetStepInput,
            output_model=RunPlanStepOut,
            handler=run_plan_get_step,
            surfaces=_surfaces("runPlan.getStep", "run-plans get-step"),
            purpose=(
                "Use this to recover one prior step result or inspect one current step without "
                "loading the full run plan into agent context."
            ),
            when_to_use=(
                "A direct dependency handoff says its result was truncated.",
                "The agent knows the run_plan_id and step_id and needs only that step.",
            ),
            prerequisites=("Pass run_plan_id and step_id.",),
            returns=(
                "The selected run-plan step, including its persisted result_json and "
                "execution metadata.",
            ),
            examples=(
                OperationExample(
                    title="Fetch one prior step result",
                    arguments={
                        "run_plan_id": 42,
                        "step_id": "prepare",
                        "response_mode": "raw",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.checkConsistency",
            summary="Check one run plan for run, step, and tracker lifecycle mismatches.",
            input_model=RunPlanCheckConsistencyInput,
            output_model=RunPlanConsistencyOut,
            handler=run_plan_check_consistency,
            surfaces=_surfaces("runPlan.checkConsistency", "run-plans check-consistency"),
            purpose=(
                "Use this when run-plan, run audit, or tracker state looks inconsistent, "
                "especially after daemon restart recovery or denied step/tracker writes."
            ),
            when_to_use=(
                "A linked run is terminal while the run plan still looks live.",
                "Tracker tickets show progress that does not match run-plan step state.",
                "The UI or an agent needs model-readable repair guidance before continuing.",
            ),
            prerequisites=("Pass run_plan_id.",),
            returns=(
                "Structured consistency issues with severity, codes, affected ids, and "
                "next operations.",
            ),
            examples=(
                OperationExample(
                    title="Check run-plan consistency",
                    arguments={"run_plan_id": 42},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.recover",
            summary="Recover a system-failed run plan into a live blocked or pending step.",
            input_model=RunPlanRecoverInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_recover,
            surfaces=_surfaces("runPlan.recover", "run-plans recover"),
            purpose=(
                "Use this for daemon/controller lifecycle failures where the existing run "
                "plan remains the canonical workflow and should continue in-place rather "
                "than being replaced by a duplicate plan."
            ),
            when_to_use=(
                "A run plan was failed because an older daemon rejected a recoverable "
                "blocked step.",
                "A daemon-restart orphan abort closed a workflow that should remain a "
                "live recoverable blocker.",
                "runPlan.checkConsistency shows terminal state but the project should "
                "continue the same workflow audit trail.",
            ),
            prerequisites=(
                "Pass run_plan_id and the step_id to restore.",
                "The plan must be failed or aborted for a system-recoverable reason.",
                "Use step_status=blocked when the agent must repair a blocker before "
                "continuing; use pending only when the step should be cleanly re-run.",
            ),
            returns=(
                "A WriteEnvelope containing the recovered started run plan.",
                "The linked audit run is put back into running state and tracker mirrors "
                "are reopened consistently.",
            ),
            examples=(
                OperationExample(
                    title="Recover a controller blocker",
                    arguments={
                        "run_plan_id": 42,
                        "step_id": "plan-tickets",
                        "step_status": "blocked",
                        "reason": "Recover old daemon blocked-status bug.",
                        "error": "Recoverable controller failure needs review.",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        OperationSpec(
            name="runPlan.reopen",
            summary="Reopen a closed workflow run plan and its mirrored tracker task.",
            input_model=RunPlanReopenInput,
            output_model=WriteEnvelope[RunPlanReopenOut],
            handler=run_plan_reopen,
            surfaces=_surfaces("runPlan.reopen", "run-plans reopen"),
            purpose=(
                "Use this lower-level lifecycle operation for controller or admin recovery "
                "when the same closed run-plan audit trail must continue in-place. Normal "
                "agent follow-up after closeout should use tracker.reopen so StackOS owns "
                "the tracker/run-plan mirror decision."
            ),
            when_to_use=(
                "A controller is explicitly reopening a known closed run plan after "
                "tracker.reopen or operator/admin review selected that canonical plan.",
                "A closed workflow task has open child tickets and an admin needs to revive "
                "the exact run-plan step rather than create a replacement plan.",
            ),
            prerequisites=(
                "Pass run_plan_id and a human-readable reason.",
                "Optional step_id chooses where to resume; omit it to let StackOS reopen "
                "the delivery step or the last step.",
            ),
            returns=(
                "A WriteEnvelope with the reopened run plan, revived run, run_token, "
                "reopened_step_id, and reset_step_ids.",
            ),
            examples=(
                OperationExample(
                    title="Admin reopens a selected closed workflow",
                    arguments={
                        "run_plan_id": 27,
                        "reason": (
                            "Admin selected this closed run plan as the canonical in-place "
                            "recovery target."
                        ),
                        "actor": "stackos-admin",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-run-audit-write",
        ),
        OperationSpec(
            name="runPlan.list",
            summary="List run plans with cursor pagination and optional filters.",
            input_model=RunPlanListInput,
            output_model=Page[RunPlanSummaryOut],
            handler=run_plan_list,
            surfaces=_surfaces("runPlan.list", "run-plans list"),
            purpose=(
                "Use this to find recent or relevant run instances for a project, template, "
                "or status without loading every plan into context."
            ),
            when_to_use=(
                "An agent needs recent history before creating or resuming work.",
                "A UI or script needs a paginated run-plan index.",
            ),
            prerequisites=(
                "Pass project_id when querying within a project.",
                "Use limit and after_id to keep responses small.",
            ),
            returns=("A cursor-paginated page of run-plan summaries.",),
            examples=(
                OperationExample(
                    title="List recent project run plans",
                    arguments={"project_id": 1, "limit": 10},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="runPlan.update",
            summary="Update run-plan metadata or explicit approval-gate state.",
            input_model=RunPlanUpdateInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_update,
            surfaces=_surfaces("runPlan.update", "run-plans approve"),
            purpose=(
                "Use this for controlled plan administration such as recording an approval "
                "decision or safe metadata. Direct MCP agents are not granted this operation; "
                "local REST and CLI calls are the admin approval surface."
            ),
            when_to_use=(
                "A trusted controller has an approval decision to persist.",
                "A local admin path needs to attach non-secret metadata to a plan.",
            ),
            prerequisites=(
                "Pass run_plan_id.",
                "Pass approval_key and approval_status together when updating approvals.",
                "Use the local REST or CLI admin path for human approval decisions.",
                "Never include secrets in metadata_json or decision_json.",
            ),
            returns=("A WriteEnvelope containing the updated run plan.",),
            examples=(
                OperationExample(
                    title="Approve a gate",
                    arguments={
                        "run_plan_id": 42,
                        "approval_key": "launch-review",
                        "approval_status": "approved",
                        "decided_by": "operator",
                    },
                ),
            ),
            grant_policy="admin-only",
        ),
        OperationSpec(
            name="runPlan.abort",
            summary="Abort a draft or started run plan and retire its tracker mirror.",
            input_model=RunPlanAbortInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_abort,
            surfaces=_surfaces("runPlan.abort", "run-plans abort"),
            purpose=(
                "Use this when a run plan is obsolete, superseded, or intentionally stopped. "
                "The operation closes the run-plan lifecycle, skips unfinished steps, "
                "cancels pending approvals, aborts the linked run audit row, and mirrors "
                "the workflow task/tickets as aborted."
            ),
            when_to_use=(
                "A started workflow should no longer continue.",
                "A draft run plan was created by mistake and should not be executed.",
                "An old rehearsal or abandoned run must not appear as live blocked work.",
            ),
            prerequisites=(
                "Pass run_plan_id.",
                "The run plan must be draft or started.",
                "Do not use this to hide a completed or failed audit result.",
            ),
            returns=(
                "A WriteEnvelope containing the aborted run plan.",
                "Skipped pending/running steps and cancelled pending approvals.",
            ),
            examples=(
                OperationExample(
                    title="Abort a superseded run plan",
                    arguments={
                        "run_plan_id": 42,
                        "reason": "Superseded by run plan 43.",
                        "actor": "codex",
                    },
                ),
            ),
            grant_policy="direct-run-audit-write",
        ),
        OperationSpec(
            name="runPlan.claimStep",
            summary="Claim the next eligible run-plan step for the active run token.",
            input_model=RunPlanClaimStepInput,
            output_model=WriteEnvelope[RunPlanStepOut],
            handler=run_plan_claim_step,
            surfaces=_surfaces("runPlan.claimStep", "run-plans claim-step"),
            purpose=(
                "Use this after runPlan.start to move one approved pending step into running "
                "state and activate its frozen tool grants."
            ),
            when_to_use=(
                "The agent has a run_token and is ready to execute the next step.",
                "A specific step_id should be claimed after its dependencies are complete.",
            ),
            prerequisites=(
                "Pass run_token from runPlan.start.",
                "Pass run_plan_id and optionally step_id.",
                "The plan must be started and any referenced approval gates must be satisfied.",
            ),
            returns=(
                "A WriteEnvelope containing the claimed running step.",
                "The step's allowed_tools list, derived from the run-plan grant snapshot.",
            ),
            examples=(
                OperationExample(
                    title="Claim a specific step",
                    arguments={"run_plan_id": 42, "step_id": "review", "run_token": "token"},
                ),
            ),
            grant_policy="run-plan-controller",
        ),
        OperationSpec(
            name="runPlan.recordStep",
            summary="Record a result for the active running run-plan step.",
            input_model=RunPlanRecordStepInput,
            output_model=WriteEnvelope[RunPlanOut],
            handler=run_plan_record_step,
            surfaces=_surfaces("runPlan.recordStep", "run-plans record-step"),
            purpose=(
                "Use this when the current step is done or blocked. Recording the final "
                "terminal step closes the plan and linked run audit row."
            ),
            when_to_use=(
                "The agent completed, failed, or intentionally skipped the claimed step.",
                "The agent hit a recoverable blocker that should pause the step without "
                "terminally failing the run plan.",
                "A controller must persist structured step output for future context retrieval.",
            ),
            prerequisites=(
                "Pass run_token from runPlan.start.",
                "Pass run_plan_id, step_id, and status success, failed, skipped, or blocked.",
                "Keep result_json concise and free of secrets.",
            ),
            returns=(
                "A WriteEnvelope containing the updated run plan.",
                "Completed or failed plan status when the recorded step is terminal for the plan.",
            ),
            examples=(
                OperationExample(
                    title="Record step success",
                    arguments={
                        "run_plan_id": 42,
                        "step_id": "review",
                        "status": "success",
                        "result_json": {"summary": "done"},
                        "run_token": "token",
                    },
                ),
            ),
            grant_policy="run-plan-controller",
        ),
    ]


__all__ = [
    "RunPlanClaimStepInput",
    "RunPlanCreateInput",
    "RunPlanGetInput",
    "RunPlanGetStepInput",
    "RunPlanListInput",
    "RunPlanRecordStepInput",
    "RunPlanRecoverInput",
    "RunPlanReopenInput",
    "RunPlanStartInput",
    "RunPlanUpdateInput",
    "RunPlanValidateInput",
    "operation_specs",
    "run_plan_claim_step",
    "run_plan_create",
    "run_plan_get",
    "run_plan_get_step",
    "run_plan_list",
    "run_plan_record_step",
    "run_plan_recover",
    "run_plan_reopen",
    "run_plan_start",
    "run_plan_update",
    "run_plan_validate",
]
