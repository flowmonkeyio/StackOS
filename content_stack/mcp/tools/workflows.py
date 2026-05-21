"""StackOS workflow template MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from content_stack.db.models import (
    ApprovalRequestStatus,
    RunPlanStatus,
    RunPlanStepStatus,
)
from content_stack.mcp.context import MCPContext
from content_stack.mcp.contract import MCPInput, WriteEnvelope
from content_stack.mcp.server import ToolRegistry, ToolSpec
from content_stack.mcp.streaming import ProgressEmitter
from content_stack.repositories.base import Page, ValidationError
from content_stack.repositories.run_plans import (
    RunPlanOut,
    RunPlanRepository,
    RunPlanStartOut,
    RunPlanStepOut,
    RunPlanSummaryOut,
)
from content_stack.workflows import (
    LoadedWorkflowTemplate,
    RunPlanValidationOut,
    WorkflowTemplateListOut,
    WorkflowTemplateLoader,
    WorkflowTemplateValidationOut,
    parse_workflow_template_obj,
    parse_workflow_template_yaml,
)


class WorkflowTemplateListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    include_shadowed: bool = False


class WorkflowTemplateDescribeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"key": "core.project-memory-review"}},
    )

    key: str
    project_id: int | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None


class WorkflowTemplateValidateInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"template_json": {}}})

    template_json: dict[str, Any] | None = None
    template_yaml: str | None = None


class WorkflowTemplateSaveInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int
    template_json: dict[str, Any] | None = None
    template_yaml: str | None = None
    source: str = Field(default="project", pattern="^(project|user)$")
    origin_path: str | None = None
    created_by: str | None = None
    enabled: bool = True


class WorkflowTemplateForkInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "core.project-memory-review",
                "new_key": "company.project-memory-review",
            }
        },
    )

    project_id: int
    key: str
    new_key: str
    repo_root: str | None = None
    name: str | None = None
    version: str = "0.1.0"
    created_by: str | None = None


class RunPlanValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"template_key": "core.project-memory-review"}},
    )

    project_id: int | None = None
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None


class RunPlanCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "template_key": "core.project-memory-review"}
        },
    )

    project_id: int
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
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


class RunPlanListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None
    status: RunPlanStatus | None = None
    template_key: str | None = None
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
    metadata_json: dict[str, Any] | None = None
    approval_key: str | None = None
    approval_status: ApprovalRequestStatus | None = None
    decided_by: str | None = None
    decision_json: dict[str, Any] | None = None


class RunPlanClaimStepInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"run_plan_id": 1, "step_id": "review"}},
    )

    run_plan_id: int
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
    step_id: str
    status: RunPlanStepStatus
    result_json: dict[str, Any] | None = None
    error: str | None = None


async def _template_list(
    inp: WorkflowTemplateListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WorkflowTemplateListOut:
    return WorkflowTemplateLoader(ctx.session).list_templates(
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        include_shadowed=inp.include_shadowed,
    )


async def _template_describe(
    inp: WorkflowTemplateDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> LoadedWorkflowTemplate:
    return WorkflowTemplateLoader(ctx.session).describe_template(
        key=inp.key,
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )


async def _template_validate(
    inp: WorkflowTemplateValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WorkflowTemplateValidationOut:
    return WorkflowTemplateLoader(ctx.session).validate_template(
        template_json=inp.template_json,
        template_yaml=inp.template_yaml,
    )


def _parse_input_template(
    *,
    template_json: dict[str, Any] | None,
    template_yaml: str | None,
):
    if template_json is None and template_yaml is None:
        raise ValidationError("template_json or template_yaml is required")
    if template_json is not None and template_yaml is not None:
        raise ValidationError("pass only one of template_json or template_yaml")
    if template_json is not None:
        return parse_workflow_template_obj(template_json)
    assert template_yaml is not None
    return parse_workflow_template_yaml(template_yaml)


async def _template_save(
    inp: WorkflowTemplateSaveInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[LoadedWorkflowTemplate]:
    spec = _parse_input_template(template_json=inp.template_json, template_yaml=inp.template_yaml)
    env = WorkflowTemplateLoader(ctx.session).save_project_template(
        project_id=inp.project_id,
        spec=spec,
        source=inp.source,
        origin_path=inp.origin_path,
        created_by=inp.created_by,
        enabled=inp.enabled,
    )
    return WriteEnvelope[LoadedWorkflowTemplate](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _template_fork(
    inp: WorkflowTemplateForkInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[LoadedWorkflowTemplate]:
    env = WorkflowTemplateLoader(ctx.session).fork_template(
        project_id=inp.project_id,
        key=inp.key,
        new_key=inp.new_key,
        repo_root=inp.repo_root,
        name=inp.name,
        version=inp.version,
        created_by=inp.created_by,
    )
    return WriteEnvelope[LoadedWorkflowTemplate](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _run_plan_validate(
    inp: RunPlanValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanValidationOut:
    return RunPlanRepository(ctx.session).validate_plan(
        run_plan_json=inp.run_plan_json,
        template_key=inp.template_key,
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )


async def _run_plan_create(
    inp: RunPlanCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).create(
        project_id=inp.project_id,
        run_plan_json=inp.run_plan_json,
        template_key=inp.template_key,
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


async def _run_plan_start(
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


async def _run_plan_get(
    inp: RunPlanGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> RunPlanOut:
    return RunPlanRepository(ctx.session).get(inp.run_plan_id)


async def _run_plan_list(
    inp: RunPlanListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[RunPlanSummaryOut]:
    return RunPlanRepository(ctx.session).list(
        project_id=inp.project_id,
        status=inp.status,
        template_key=inp.template_key,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _run_plan_update(
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
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


async def _run_plan_claim_step(
    inp: RunPlanClaimStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanStepOut]:
    env = RunPlanRepository(ctx.session).claim_step(
        run_plan_id=inp.run_plan_id,
        run_id=ctx.run_id,
        step_id=inp.step_id,
        claimed_by=inp.claimed_by,
    )
    return WriteEnvelope[RunPlanStepOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


async def _run_plan_record_step(
    inp: RunPlanRecordStepInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[RunPlanOut]:
    env = RunPlanRepository(ctx.session).record_step(
        run_plan_id=inp.run_plan_id,
        run_id=ctx.run_id,
        step_id=inp.step_id,
        status=inp.status,
        result_json=inp.result_json,
        error=inp.error,
    )
    return WriteEnvelope[RunPlanOut](data=env.data, run_id=env.run_id, project_id=env.project_id)


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "workflowTemplate.list",
            "List effective reusable workflow templates without executing them.",
            WorkflowTemplateListInput,
            WorkflowTemplateListOut,
            _template_list,
        )
    )
    registry.register(
        ToolSpec(
            "workflowTemplate.describe",
            "Describe one reusable workflow template without executing it.",
            WorkflowTemplateDescribeInput,
            LoadedWorkflowTemplate,
            _template_describe,
        )
    )
    registry.register(
        ToolSpec(
            "workflowTemplate.validate",
            "Validate a workflow template specification without saving or executing it.",
            WorkflowTemplateValidateInput,
            WorkflowTemplateValidationOut,
            _template_validate,
        )
    )
    registry.register(
        ToolSpec(
            "workflowTemplate.save",
            "Local-admin operation to save a project/user workflow template.",
            WorkflowTemplateSaveInput,
            WriteEnvelope[LoadedWorkflowTemplate],
            _template_save,
        )
    )
    registry.register(
        ToolSpec(
            "workflowTemplate.fork",
            "Local-admin operation to fork a workflow template for a project.",
            WorkflowTemplateForkInput,
            WriteEnvelope[LoadedWorkflowTemplate],
            _template_fork,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.validate",
            "Validate a concrete run plan without saving or executing it.",
            RunPlanValidateInput,
            RunPlanValidationOut,
            _run_plan_validate,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.create",
            "Create a concrete agent-authored run plan from a template or plan JSON.",
            RunPlanCreateInput,
            WriteEnvelope[RunPlanOut],
            _run_plan_create,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.start",
            "Start a run plan and open its linked run audit row.",
            RunPlanStartInput,
            WriteEnvelope[RunPlanStartOut],
            _run_plan_start,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.get",
            "Fetch one run plan with steps and approval gates.",
            RunPlanGetInput,
            RunPlanOut,
            _run_plan_get,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.list",
            "List run plans with cursor pagination.",
            RunPlanListInput,
            Page[RunPlanSummaryOut],
            _run_plan_list,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.update",
            "Update run-plan metadata or explicit approval gate state.",
            RunPlanUpdateInput,
            WriteEnvelope[RunPlanOut],
            _run_plan_update,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.claimStep",
            "Claim a concrete run-plan step after required approvals are satisfied.",
            RunPlanClaimStepInput,
            WriteEnvelope[RunPlanStepOut],
            _run_plan_claim_step,
        )
    )
    registry.register(
        ToolSpec(
            "runPlan.recordStep",
            "Record the current agent's run-plan step result.",
            RunPlanRecordStepInput,
            WriteEnvelope[RunPlanOut],
            _run_plan_record_step,
        )
    )


__all__ = ["register"]
