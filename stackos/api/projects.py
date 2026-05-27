"""Project, credential, budget, schedule, and utility routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from stackos.api.deps import get_session
from stackos.api.envelopes import WriteResponse, write_response
from stackos.api.pagination import PageResponse, page_response, pagination_params
from stackos.integrations.sitemap import fetch_sitemap_entries
from stackos.repositories.projects import (
    IntegrationBudgetOut,
    IntegrationBudgetRepository,
    ProjectOut,
    ProjectRepository,
    ScheduledJobOut,
    ScheduledJobRepository,
)
from stackos.repositories.runs import RunRepository

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "slug": "acme",
                "name": "Acme",
                "domain": "example.com",
                "locale": "en-US",
            }
        }
    )

    slug: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=255)
    niche: str | None = Field(default=None, max_length=200)
    locale: str = Field(default="en-US", max_length=16)
    schedule_json: dict[str, Any] | None = None


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=255)
    niche: str | None = Field(default=None, max_length=200)
    locale: str | None = Field(default=None, max_length=16)
    schedule_json: dict[str, Any] | None = None
    is_active: bool | None = None


class ScheduleUpsertRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=120)
    cron_expr: str = Field(min_length=1, max_length=120)
    enabled: bool = True


class BudgetUpsertRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=120)
    monthly_budget_usd: float = Field(default=50.0, ge=0)
    alert_threshold_pct: int = Field(default=80, ge=0, le=100)
    qps: float = Field(default=1.0, ge=0)


class CostResponse(BaseModel):
    """Read-only project cost rollup for the current UI budget surface."""

    project_id: int
    month: str
    period_start: str
    period_end: str
    by_integration: dict[str, float]
    total_usd: float


class SitemapFetchRequest(BaseModel):
    url: str = Field(min_length=1)
    limit: int = Field(default=500, ge=1, le=5000)


class SitemapFetchResponse(BaseModel):
    url: str
    entries: list[dict[str, Any]]
    errors: list[dict[str, Any]] = Field(default_factory=list)


@router.get("", response_model=PageResponse[ProjectOut])
async def list_projects(
    active_only: bool = Query(default=False),
    page=Depends(pagination_params),
    session: Session = Depends(get_session),
) -> PageResponse[ProjectOut]:
    return page_response(
        ProjectRepository(session).list(
            active_only=active_only,
            limit=page.limit,
            after_id=page.after,
        )
    )


@router.post("", response_model=WriteResponse[ProjectOut], status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[ProjectOut]:
    return write_response(ProjectRepository(session).create(**body.model_dump()))


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, session: Session = Depends(get_session)) -> ProjectOut:
    return ProjectRepository(session).get(project_id)


@router.patch("/{project_id}", response_model=WriteResponse[ProjectOut])
async def update_project(
    project_id: int,
    body: ProjectUpdateRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[ProjectOut]:
    patch = body.model_dump(exclude_unset=True)
    return write_response(ProjectRepository(session).update(project_id, **patch))


@router.delete("/{project_id}", response_model=WriteResponse[ProjectOut])
async def delete_project(
    project_id: int, session: Session = Depends(get_session)
) -> WriteResponse[ProjectOut]:
    return write_response(ProjectRepository(session).delete(project_id))


@router.get("/{project_id}/schedules", response_model=list[ScheduledJobOut])
async def list_schedules(
    project_id: int,
    session: Session = Depends(get_session),
) -> list[ScheduledJobOut]:
    return ScheduledJobRepository(session).list(project_id)


@router.post("/{project_id}/schedules", response_model=WriteResponse[ScheduledJobOut])
async def upsert_schedule(
    project_id: int,
    body: ScheduleUpsertRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[ScheduledJobOut]:
    return write_response(
        ScheduledJobRepository(session).set(
            project_id=project_id,
            kind=body.kind,
            cron_expr=body.cron_expr,
            enabled=body.enabled,
        )
    )


@router.patch("/{project_id}/schedules/{job_id}", response_model=WriteResponse[ScheduledJobOut])
async def toggle_schedule(
    project_id: int,
    job_id: int,
    enabled: bool,
    session: Session = Depends(get_session),
) -> WriteResponse[ScheduledJobOut]:
    _ = project_id
    return write_response(ScheduledJobRepository(session).toggle(job_id, enabled=enabled))


@router.delete("/{project_id}/schedules/{job_id}", response_model=WriteResponse[ScheduledJobOut])
async def delete_schedule(
    project_id: int,
    job_id: int,
    session: Session = Depends(get_session),
) -> WriteResponse[ScheduledJobOut]:
    _ = project_id
    return write_response(ScheduledJobRepository(session).toggle(job_id, enabled=False))


@router.get("/{project_id}/budgets", response_model=list[IntegrationBudgetOut])
async def list_budgets(
    project_id: int,
    session: Session = Depends(get_session),
) -> list[IntegrationBudgetOut]:
    return IntegrationBudgetRepository(session).list(project_id)


@router.get("/{project_id}/cost", response_model=CostResponse)
async def get_project_cost(
    project_id: int,
    month: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> CostResponse:
    raw = RunRepository(session).cost(project_id, month=month)
    by_kind_cents = raw["by_kind_cents"]
    return CostResponse(
        project_id=project_id,
        month=raw["month"],
        period_start=raw["period_start"],
        period_end=raw["period_end"],
        by_integration={kind: cents / 100 for kind, cents in by_kind_cents.items()},
        total_usd=raw["total_cents"] / 100,
    )


@router.get("/{project_id}/budgets/{kind}", response_model=IntegrationBudgetOut)
async def get_budget(
    project_id: int,
    kind: str,
    session: Session = Depends(get_session),
) -> IntegrationBudgetOut:
    return IntegrationBudgetRepository(session).get(project_id, kind)


@router.post("/{project_id}/budgets", response_model=WriteResponse[IntegrationBudgetOut])
async def create_budget(
    project_id: int,
    body: BudgetUpsertRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[IntegrationBudgetOut]:
    return write_response(
        IntegrationBudgetRepository(session).set(project_id=project_id, **body.model_dump())
    )


@router.patch("/{project_id}/budgets/{kind}", response_model=WriteResponse[IntegrationBudgetOut])
async def update_budget(
    project_id: int,
    kind: str,
    body: BudgetUpsertRequest,
    session: Session = Depends(get_session),
) -> WriteResponse[IntegrationBudgetOut]:
    data = body.model_dump()
    data["kind"] = kind
    return write_response(IntegrationBudgetRepository(session).set(project_id=project_id, **data))


@router.post("/{project_id}/sitemap/fetch", response_model=SitemapFetchResponse)
async def fetch_project_sitemap(
    project_id: int,
    body: SitemapFetchRequest,
    session: Session = Depends(get_session),
) -> SitemapFetchResponse:
    ProjectRepository(session).get(project_id)
    result = await fetch_sitemap_entries([body.url], max_entries=body.limit)
    return SitemapFetchResponse(
        url=body.url,
        entries=[
            {
                "url": entry.url,
                "lastmod": entry.lastmod,
                "changefreq": entry.changefreq,
                "priority": entry.priority,
                "source_sitemap": entry.source_sitemap,
            }
            for entry in result.entries
        ],
        errors=[{"url": error.url, "error": error.error} for error in result.errors],
    )


__all__ = ["router"]
