"""StackOS workflow template MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.adapters.mcp import register_mcp_operation_names, register_mcp_operations
from stackos.operations.registry import OperationRegistry, build_operation_registry
from stackos.repositories.base import ValidationError
from stackos.workflows import (
    LoadedWorkflowTemplate,
    WorkflowTemplateExtensionGetOut,
    WorkflowTemplateExtensionListOut,
    WorkflowTemplateExtensionOut,
    WorkflowTemplateExtensionValidationOut,
    WorkflowTemplateListOut,
    WorkflowTemplateLoader,
    WorkflowTemplateValidationOut,
    parse_workflow_template_obj,
    parse_workflow_template_yaml,
)
from stackos.workflows.template_schema import WorkflowTemplateIssue


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
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"key": "core.project-memory-review"},
                {"template_json": {}},
            ]
        },
    )

    template_json: dict[str, Any] | None = None
    template_yaml: str | None = None
    key: str | None = None
    workflow_key: str | None = None
    project_id: int | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None


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


class WorkflowExtensionListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class WorkflowExtensionGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "workflow_key": "engineering.customer-support-investigation",
            }
        },
    )

    project_id: int
    workflow_key: str
    include_disabled: bool = True


class WorkflowExtensionValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "workflow_key": "engineering.customer-support-investigation",
                "input_defaults_json": {
                    "communication_route_ref": "communication-route:support-feedback"
                },
                "required_input_keys_json": ["communication_route_ref"],
                "template_overrides_json": {
                    "description": "Project-specific support investigation flow."
                },
            }
        },
    )

    project_id: int
    workflow_key: str
    input_defaults_json: dict[str, Any] | None = None
    selected_context_json: dict[str, Any] | None = None
    required_input_keys_json: list[str] | None = None
    guardrails_json: dict[str, Any] | None = None
    step_overrides_json: dict[str, Any] | None = None
    template_overrides_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None


class WorkflowExtensionUpsertInput(WorkflowExtensionValidateInput):
    enabled: bool = True
    created_by: str | None = None


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
    if inp.key is not None and inp.workflow_key is not None and inp.key != inp.workflow_key:
        return WorkflowTemplateValidationOut(
            valid=False,
            errors=[
                WorkflowTemplateIssue(
                    path="$",
                    message="key and workflow_key must match when both are provided",
                    code="ambiguous_template_key",
                )
            ],
        )
    key = inp.key or inp.workflow_key
    return WorkflowTemplateLoader(ctx.session).validate_template(
        template_json=inp.template_json,
        template_yaml=inp.template_yaml,
        key=key,
        project_id=inp.project_id,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )


async def _extension_list(
    inp: WorkflowExtensionListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WorkflowTemplateExtensionListOut:
    return WorkflowTemplateLoader(ctx.session).list_extensions(project_id=inp.project_id)


async def _extension_get(
    inp: WorkflowExtensionGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WorkflowTemplateExtensionGetOut:
    return WorkflowTemplateExtensionGetOut(
        extension=WorkflowTemplateLoader(ctx.session).get_extension(
            project_id=inp.project_id,
            workflow_key=inp.workflow_key,
            include_disabled=inp.include_disabled,
        )
    )


async def _extension_validate(
    inp: WorkflowExtensionValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WorkflowTemplateExtensionValidationOut:
    return WorkflowTemplateLoader(ctx.session).validate_extension(
        project_id=inp.project_id,
        workflow_key=inp.workflow_key,
        input_defaults_json=inp.input_defaults_json,
        selected_context_json=inp.selected_context_json,
        required_input_keys_json=inp.required_input_keys_json,
        guardrails_json=inp.guardrails_json,
        step_overrides_json=inp.step_overrides_json,
        template_overrides_json=inp.template_overrides_json,
        metadata_json=inp.metadata_json,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )


async def _extension_upsert(
    inp: WorkflowExtensionUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[WorkflowTemplateExtensionOut]:
    env = WorkflowTemplateLoader(ctx.session).upsert_extension(
        project_id=inp.project_id,
        workflow_key=inp.workflow_key,
        enabled=inp.enabled,
        input_defaults_json=inp.input_defaults_json,
        selected_context_json=inp.selected_context_json,
        required_input_keys_json=inp.required_input_keys_json,
        guardrails_json=inp.guardrails_json,
        step_overrides_json=inp.step_overrides_json,
        template_overrides_json=inp.template_overrides_json,
        metadata_json=inp.metadata_json,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
        created_by=inp.created_by,
    )
    return WriteEnvelope[WorkflowTemplateExtensionOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
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


def _run_plan_operations() -> OperationRegistry:
    operations = OperationRegistry()
    all_operations = build_operation_registry()
    for name in (
        "runPlan.validate",
        "runPlan.create",
        "runPlan.start",
        "runPlan.get",
        "runPlan.list",
        "runPlan.update",
        "runPlan.abort",
        "runPlan.claimStep",
        "runPlan.recordStep",
    ):
        operations.register(all_operations.get(name))
    return operations


def register(registry: ToolRegistry) -> None:
    register_mcp_operation_names(
        registry,
        (
            "workflowExtension.list",
            "workflowExtension.get",
            "workflowExtension.validate",
            "workflowExtension.upsert",
            "workflowTemplate.list",
            "workflowTemplate.describe",
            "workflowTemplate.validate",
            "workflowTemplate.save",
            "workflowTemplate.fork",
        ),
    )
    register_mcp_operations(registry, _run_plan_operations())


__all__ = ["register"]
