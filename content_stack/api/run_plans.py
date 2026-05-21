"""Run-plan REST read surface for generic UI renderers."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from content_stack.api.deps import get_session
from content_stack.api.pagination import (
    PageResponse,
    PaginationParams,
    page_response,
    pagination_params,
)
from content_stack.db.models import RunPlanStatus
from content_stack.repositories.run_plans import (
    RunPlanOut,
    RunPlanRepository,
    RunPlanSummaryOut,
)

project_router = APIRouter(prefix="/api/v1/projects", tags=["run-plans"])
run_plan_router = APIRouter(prefix="/api/v1/run-plans", tags=["run-plans"])


@project_router.get(
    "/{project_id}/run-plans",
    response_model=PageResponse[RunPlanSummaryOut],
)
async def list_run_plans(
    project_id: int,
    page: PaginationParams = Depends(pagination_params),
    run_id: int | None = None,
    status: RunPlanStatus | None = None,
    template_key: str | None = None,
    session: Session = Depends(get_session),
) -> PageResponse[RunPlanSummaryOut]:
    """Cursor-paginated run-plan list scoped to a project."""
    return page_response(
        RunPlanRepository(session).list(
            project_id=project_id,
            run_id=run_id,
            status=status,
            template_key=template_key,
            limit=page.limit,
            after_id=page.after,
        )
    )


@run_plan_router.get("/{run_plan_id}", response_model=RunPlanOut)
async def get_run_plan(
    run_plan_id: int,
    session: Session = Depends(get_session),
) -> RunPlanOut:
    """Fetch one run plan with concrete steps and approval gates."""
    return RunPlanRepository(session).get(run_plan_id)


__all__ = ["project_router", "run_plan_router"]
