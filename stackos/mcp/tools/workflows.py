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
    WorkflowAuthoringGuideOut,
    WorkflowTemplateExtensionDeleteOut,
    WorkflowTemplateExtensionGetOut,
    WorkflowTemplateExtensionListOut,
    WorkflowTemplateExtensionUpsertOut,
    WorkflowTemplateExtensionValidationOut,
    WorkflowTemplateListOut,
    WorkflowTemplateLoader,
    WorkflowTemplateValidationOut,
    parse_workflow_template_obj,
    parse_workflow_template_yaml,
    workflow_authoring_guide,
)
from stackos.workflows.template_schema import WorkflowTemplateIssue

_TEMPLATE_SOURCE_PATTERN = "^(plugin|project|user|repo)$"
_TEMPLATE_SOURCE_DESCRIPTION = (
    "Optional template-origin filter: plugin, project, user, or repo. "
    "This is not workflow provenance; omit it unless selecting a specific origin."
)


class WorkflowTemplateAuthoringGuideInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {}})


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
    source: str | None = Field(
        default=None,
        pattern=_TEMPLATE_SOURCE_PATTERN,
        description=_TEMPLATE_SOURCE_DESCRIPTION,
    )


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
    source: str | None = Field(
        default=None,
        pattern=_TEMPLATE_SOURCE_PATTERN,
        description=_TEMPLATE_SOURCE_DESCRIPTION,
    )


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
                "workflow_key": "communications.customer-feedback-intake",
            }
        },
    )

    project_id: int
    workflow_key: str
    include_disabled: bool = True


class WorkflowExtensionDeleteInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "workflow_key": "communications.customer-feedback-intake",
            }
        },
    )

    project_id: int
    workflow_key: str


class WorkflowExtensionValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "workflow_key": "communications.customer-feedback-intake",
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
    enabled: bool | None = Field(
        default=None,
        description="Optional enabled state to validate. Omit to preserve existing state.",
    )
    input_defaults_json: dict[str, Any] | None = None
    selected_context_json: dict[str, Any] | None = None
    required_input_keys_json: list[str] | None = None
    guardrails_json: dict[str, Any] | None = None
    step_overrides_json: dict[str, Any] | None = None
    template_overrides_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    update_mode: str = Field(
        default="merge",
        pattern="^(merge|replace)$",
        description=(
            "merge validates the effective extension after preserving omitted existing "
            "fields; replace validates a full rewrite where omitted fields reset."
        ),
    )
    clear_fields_json: list[str] | None = Field(
        default=None,
        description="Extension JSON fields to intentionally clear during merge validation.",
    )
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = Field(
        default=None,
        pattern=_TEMPLATE_SOURCE_PATTERN,
        description=_TEMPLATE_SOURCE_DESCRIPTION,
    )


class WorkflowExtensionUpsertInput(WorkflowExtensionValidateInput):
    enabled: bool | None = Field(
        default=None,
        description=(
            "Optional enabled state to save. Omit to preserve an existing extension's state; "
            "new extensions default to enabled."
        ),
    )
    created_by: str | None = Field(
        default=None,
        description=(
            "Optional audit actor for the extension write; use this, not source, "
            "for provenance."
        ),
    )


async def _template_authoring_guide(
    _inp: WorkflowTemplateAuthoringGuideInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WorkflowAuthoringGuideOut:
    return workflow_authoring_guide()


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


async def _extension_delete(
    inp: WorkflowExtensionDeleteInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[WorkflowTemplateExtensionDeleteOut]:
    env = WorkflowTemplateLoader(ctx.session).delete_extension(
        project_id=inp.project_id,
        workflow_key=inp.workflow_key,
    )
    return WriteEnvelope[WorkflowTemplateExtensionDeleteOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _extension_validate(
    inp: WorkflowExtensionValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WorkflowTemplateExtensionValidationOut:
    provided = inp.model_fields_set
    return WorkflowTemplateLoader(ctx.session).validate_extension(
        project_id=inp.project_id,
        workflow_key=inp.workflow_key,
        enabled=inp.enabled if "enabled" in provided else None,
        input_defaults_json=(
            inp.input_defaults_json if "input_defaults_json" in provided else None
        ),
        selected_context_json=(
            inp.selected_context_json if "selected_context_json" in provided else None
        ),
        required_input_keys_json=(
            inp.required_input_keys_json if "required_input_keys_json" in provided else None
        ),
        guardrails_json=inp.guardrails_json if "guardrails_json" in provided else None,
        step_overrides_json=(
            inp.step_overrides_json if "step_overrides_json" in provided else None
        ),
        template_overrides_json=(
            inp.template_overrides_json if "template_overrides_json" in provided else None
        ),
        metadata_json=inp.metadata_json if "metadata_json" in provided else None,
        update_mode=inp.update_mode,
        clear_fields_json=inp.clear_fields_json,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
    )


async def _extension_upsert(
    inp: WorkflowExtensionUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[WorkflowTemplateExtensionUpsertOut]:
    provided = inp.model_fields_set
    env = WorkflowTemplateLoader(ctx.session).upsert_extension(
        project_id=inp.project_id,
        workflow_key=inp.workflow_key,
        enabled=inp.enabled if "enabled" in provided else None,
        input_defaults_json=(
            inp.input_defaults_json if "input_defaults_json" in provided else None
        ),
        selected_context_json=(
            inp.selected_context_json if "selected_context_json" in provided else None
        ),
        required_input_keys_json=(
            inp.required_input_keys_json if "required_input_keys_json" in provided else None
        ),
        guardrails_json=inp.guardrails_json if "guardrails_json" in provided else None,
        step_overrides_json=(
            inp.step_overrides_json if "step_overrides_json" in provided else None
        ),
        template_overrides_json=(
            inp.template_overrides_json if "template_overrides_json" in provided else None
        ),
        metadata_json=inp.metadata_json if "metadata_json" in provided else None,
        update_mode=inp.update_mode,
        clear_fields_json=inp.clear_fields_json,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
        created_by=inp.created_by,
    )
    return WriteEnvelope[WorkflowTemplateExtensionUpsertOut](
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
        "runPlan.getStep",
        "runPlan.checkConsistency",
        "runPlan.list",
        "runPlan.update",
        "runPlan.abort",
        "runPlan.recover",
        "runPlan.reopen",
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
            "workflowExtension.delete",
            "workflowExtension.validate",
            "workflowExtension.upsert",
            "workflowTemplate.authoringGuide",
            "workflowTemplate.list",
            "workflowTemplate.describe",
            "workflowTemplate.validate",
            "workflowTemplate.save",
            "workflowTemplate.fork",
        ),
    )
    register_mcp_operations(registry, _run_plan_operations())


__all__ = ["register"]
