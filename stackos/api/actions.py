"""StackOS action audit REST routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from stackos.actions import ActionCallAuditOut, ActionRepository
from stackos.api.deps import get_session
from stackos.api.pagination import (
    PageResponse,
    PaginationParams,
    page_response,
    pagination_params,
)
from stackos.db.models import ActionCallStatus

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["actions"])


@router.get("/action-calls", response_model=PageResponse[ActionCallAuditOut])
async def query_action_calls(
    project_id: int,
    run_id: int | None = Query(default=None),
    run_plan_id: int | None = Query(default=None),
    run_plan_step_id: int | None = Query(default=None),
    plugin_slug: str | None = Query(default=None),
    action_key: str | None = Query(default=None),
    status: ActionCallStatus | None = Query(default=None),
    page: PaginationParams = Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ActionCallAuditOut]:
    """Query redacted action execution audit rows for generic run renderers."""
    return page_response(
        ActionRepository(session).query_calls(
            project_id=project_id,
            run_id=run_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
            plugin_slug=plugin_slug,
            action_key=action_key,
            status=status,
            limit=page.limit,
            after_id=page.after,
        )
    )


__all__ = ["router"]
