"""Runs router — REST surface for the audit layer.

``RunRepository`` owns the audit rows. REST exposes read + abort +
heartbeat; concrete run execution is driven through workflow templates and
run plans.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlmodel import Session

from content_stack.api.deps import get_session
from content_stack.api.envelopes import WriteResponse, write_response
from content_stack.api.pagination import (
    PageResponse,
    PaginationParams,
    page_response,
    pagination_params,
)
from content_stack.db.models import RunKind, RunStatus
from content_stack.repositories.runs import RunOut, RunRepository

project_router = APIRouter(prefix="/api/v1/projects", tags=["runs"])
run_router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


class HeartbeatResponse(BaseModel):
    """Wire shape for ``POST /runs/{id}/heartbeat`` (idempotent)."""

    data: RunOut | None
    run_id: int | None
    project_id: int | None


@project_router.get(
    "/{project_id}/runs",
    response_model=PageResponse[RunOut],
)
async def list_runs(
    project_id: int,
    page: PaginationParams = Depends(pagination_params),
    kind: RunKind | None = None,
    status: RunStatus | None = None,
    parent_run_id: int | None = None,
    session: Session = Depends(get_session),
) -> PageResponse[RunOut]:
    """Cursor-paginated runs list, scoped to a project."""
    return page_response(
        RunRepository(session).list(
            project_id=project_id,
            kind=kind,
            status=status,
            parent_run_id=parent_run_id,
            limit=page.limit,
            after_id=page.after,
        )
    )


@run_router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: int, session: Session = Depends(get_session)) -> RunOut:
    """Fetch one run by id."""
    return RunRepository(session).get(run_id)


@run_router.get("/{run_id}/children", response_model=list[RunOut])
async def get_run_children(
    run_id: int,
    session: Session = Depends(get_session),
) -> list[RunOut]:
    """Direct children of a parent run."""
    return RunRepository(session).children(run_id)


@run_router.post(
    "/{run_id}/abort",
    response_model=WriteResponse[RunOut],
    status_code=status.HTTP_200_OK,
)
async def abort_run(
    run_id: int,
    cascade: bool = Query(False),
    session: Session = Depends(get_session),
) -> WriteResponse[RunOut]:
    """Abort a run; ``?cascade=true`` walks live children too."""
    return write_response(RunRepository(session).abort(run_id, cascade=cascade))


@run_router.post(
    "/{run_id}/heartbeat",
    response_model=HeartbeatResponse,
)
async def heartbeat(
    run_id: int,
    session: Session = Depends(get_session),
) -> HeartbeatResponse:
    """Idempotent heartbeat update.

    The repo returns ``Envelope[RunOut | None]`` — None when the row was
    reaped between the load and this call. We propagate that None so the
    M9 daemon's heartbeat loop can keep polling without crashing.
    """
    env = RunRepository(session).heartbeat(run_id)
    return HeartbeatResponse(data=env.data, run_id=env.run_id, project_id=env.project_id)


# NOTE: ``POST /runs`` (start) and ``POST /runs/{id}/finish`` are intentionally
# absent here. Run plans open and close their linked audit rows through the
# MCP/API run-plan surface.

# Reference imports kept so docs cross-link.
_ = (Any, BaseModel)

__all__ = ["project_router", "run_router"]
