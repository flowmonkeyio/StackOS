"""Workflow template REST read surface for generic UI renderers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from stackos.api.deps import get_session
from stackos.mcp.contract import WriteEnvelope
from stackos.workflows.template_loader import (
    LoadedWorkflowTemplate,
    WorkflowTemplateExtensionDeleteOut,
    WorkflowTemplateExtensionListOut,
    WorkflowTemplateExtensionOut,
    WorkflowTemplateExtensionValidationOut,
    WorkflowTemplateListOut,
    WorkflowTemplateLoader,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["workflow-templates"])


class WorkflowTemplateExtensionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    input_defaults_json: dict[str, Any] | None = None
    selected_context_json: dict[str, Any] | None = None
    required_input_keys_json: list[str] | None = None
    guardrails_json: dict[str, Any] | None = None
    step_overrides_json: dict[str, Any] | None = None
    template_overrides_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    plugin_slug: str | None = None
    source: str | None = None
    created_by: str | None = None


@router.get("/workflow-templates", response_model=WorkflowTemplateListOut)
async def list_workflow_templates(
    project_id: int,
    plugin_slug: str | None = Query(default=None),
    include_shadowed: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> WorkflowTemplateListOut:
    """List effective reusable workflow templates for a project."""
    return WorkflowTemplateLoader(session).list_templates(
        project_id=project_id,
        plugin_slug=plugin_slug,
        include_shadowed=include_shadowed,
    )


@router.get("/workflow-templates/{template_key}", response_model=LoadedWorkflowTemplate)
async def describe_workflow_template(
    project_id: int,
    template_key: str,
    plugin_slug: str | None = Query(default=None),
    source: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> LoadedWorkflowTemplate:
    """Describe one reusable workflow template without creating a run."""
    return WorkflowTemplateLoader(session).describe_template(
        key=template_key,
        project_id=project_id,
        plugin_slug=plugin_slug,
        source=source,
    )


@router.get(
    "/workflow-template-extensions",
    response_model=WorkflowTemplateExtensionListOut,
)
async def list_workflow_template_extensions(
    project_id: int,
    session: Session = Depends(get_session),
) -> WorkflowTemplateExtensionListOut:
    """List project workflow configuration extensions."""
    return WorkflowTemplateLoader(session).list_extensions(project_id=project_id)


@router.get(
    "/workflow-templates/{template_key}/extension",
    response_model=WorkflowTemplateExtensionOut | None,
)
async def get_workflow_template_extension(
    project_id: int,
    template_key: str,
    include_disabled: bool = Query(default=True),
    session: Session = Depends(get_session),
) -> WorkflowTemplateExtensionOut | None:
    """Get the project workflow extension for one template."""
    return WorkflowTemplateLoader(session).get_extension(
        project_id=project_id,
        workflow_key=template_key,
        include_disabled=include_disabled,
    )


@router.post(
    "/workflow-templates/{template_key}/extension/validate",
    response_model=WorkflowTemplateExtensionValidationOut,
)
async def validate_workflow_template_extension(
    project_id: int,
    template_key: str,
    body: WorkflowTemplateExtensionBody,
    session: Session = Depends(get_session),
) -> WorkflowTemplateExtensionValidationOut:
    """Validate a project workflow extension without saving it."""
    return WorkflowTemplateLoader(session).validate_extension(
        project_id=project_id,
        workflow_key=template_key,
        input_defaults_json=body.input_defaults_json,
        selected_context_json=body.selected_context_json,
        required_input_keys_json=body.required_input_keys_json,
        guardrails_json=body.guardrails_json,
        step_overrides_json=body.step_overrides_json,
        template_overrides_json=body.template_overrides_json,
        metadata_json=body.metadata_json,
        plugin_slug=body.plugin_slug,
        source=body.source,
    )


@router.put(
    "/workflow-templates/{template_key}/extension",
    response_model=WriteEnvelope[WorkflowTemplateExtensionOut],
)
async def upsert_workflow_template_extension(
    project_id: int,
    template_key: str,
    body: WorkflowTemplateExtensionBody,
    session: Session = Depends(get_session),
) -> WriteEnvelope[WorkflowTemplateExtensionOut]:
    """Save project workflow configuration for one template."""
    env = WorkflowTemplateLoader(session).upsert_extension(
        project_id=project_id,
        workflow_key=template_key,
        enabled=body.enabled,
        input_defaults_json=body.input_defaults_json,
        selected_context_json=body.selected_context_json,
        required_input_keys_json=body.required_input_keys_json,
        guardrails_json=body.guardrails_json,
        step_overrides_json=body.step_overrides_json,
        template_overrides_json=body.template_overrides_json,
        metadata_json=body.metadata_json,
        plugin_slug=body.plugin_slug,
        source=body.source,
        created_by=body.created_by,
    )
    return WriteEnvelope[WorkflowTemplateExtensionOut](
        data=env.data,
        project_id=env.project_id,
    )


@router.delete(
    "/workflow-templates/{template_key}/extension",
    response_model=WriteEnvelope[WorkflowTemplateExtensionDeleteOut],
)
async def delete_workflow_template_extension(
    project_id: int,
    template_key: str,
    session: Session = Depends(get_session),
) -> WriteEnvelope[WorkflowTemplateExtensionDeleteOut]:
    """Delete project workflow configuration for one template."""
    env = WorkflowTemplateLoader(session).delete_extension(
        project_id=project_id,
        workflow_key=template_key,
    )
    return WriteEnvelope[WorkflowTemplateExtensionDeleteOut](
        data=env.data,
        project_id=env.project_id,
    )


__all__ = ["router"]
