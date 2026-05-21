"""Workflow template REST read surface for generic UI renderers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from content_stack.api.deps import get_session
from content_stack.workflows.template_loader import (
    LoadedWorkflowTemplate,
    WorkflowTemplateListOut,
    WorkflowTemplateLoader,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["workflow-templates"])


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


__all__ = ["router"]
