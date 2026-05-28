"""Project, credential, budget, and schedule MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, model_validator

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import Page
from stackos.repositories.projects import (
    IntegrationBudgetOut,
    IntegrationBudgetRepository,
    ProjectOut,
    ProjectRepository,
    ScheduledJobOut,
    ScheduledJobRepository,
)


class ProjectListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"active_only": False, "limit": 50}},
    )

    active_only: bool = False
    limit: int | None = None
    after_id: int | None = None


class ProjectCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "slug": "acme",
                "name": "Acme",
                "domain": "example.com",
                "locale": "en-US",
            }
        },
    )

    slug: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=255)
    niche: str | None = None
    locale: str = Field(default="en-US", max_length=16)
    schedule_json: dict[str, Any] | None = None


class ProjectGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"id_or_slug": "acme"},
            "examples": [
                {"id_or_slug": "acme"},
                {"id_or_slug": 1},
                {"project_id": 1},
            ],
        },
    )

    id_or_slug: int | str | None = None
    project_id: int | None = None

    @model_validator(mode="after")
    def _requires_identifier(self) -> ProjectGetInput:
        if self.id_or_slug is None and self.project_id is None:
            raise ValueError("id_or_slug or project_id is required")
        return self


class ProjectUpdateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "patch": {"name": "Acme Pro"}}},
    )

    project_id: int
    patch: dict[str, Any]


class ProjectIdInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class BudgetListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class BudgetSetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "kind": "openai",
                "monthly_budget_usd": 100,
            }
        },
    )

    project_id: int
    kind: str
    monthly_budget_usd: float
    alert_threshold_pct: int = 80
    qps: float = 1.0


class BudgetUpdateInput(BudgetSetInput):
    pass


class BudgetQueryProjectInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"example": {"project_id": 1, "kind": "openai"}}
    )

    project_id: int
    kind: str


class ScheduleListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class ScheduleSetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "kind": "weekly-review", "cron_expr": "0 9 * * 1"}
        },
    )

    project_id: int
    kind: str
    cron_expr: str
    enabled: bool = True


class ScheduleToggleInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "job_id": 1, "enabled": False}},
    )

    job_id: int
    project_id: int | None = None
    enabled: bool


class ScheduleRemoveInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "job_id": 1}},
    )

    job_id: int
    project_id: int | None = None


async def _project_list(
    inp: ProjectListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> Page[ProjectOut]:
    return ProjectRepository(ctx.session).list(
        active_only=inp.active_only,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def _project_create(
    inp: ProjectCreateInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ProjectOut]:
    env = ProjectRepository(ctx.session).create(
        slug=inp.slug,
        name=inp.name,
        domain=inp.domain,
        niche=inp.niche,
        locale=inp.locale,
        schedule_json=inp.schedule_json,
    )
    return WriteEnvelope[ProjectOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _project_get(inp: ProjectGetInput, ctx: MCPContext, _emit: ProgressEmitter) -> ProjectOut:
    identifier = inp.id_or_slug if inp.id_or_slug is not None else inp.project_id
    assert identifier is not None
    return ProjectRepository(ctx.session).get(identifier)


async def _project_update(
    inp: ProjectUpdateInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ProjectOut]:
    env = ProjectRepository(ctx.session).update(inp.project_id, **inp.patch)
    return WriteEnvelope[ProjectOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _project_delete(
    inp: ProjectIdInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ProjectOut]:
    env = ProjectRepository(ctx.session).delete(inp.project_id)
    return WriteEnvelope[ProjectOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _budget_list(
    inp: BudgetListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> list[IntegrationBudgetOut]:
    return IntegrationBudgetRepository(ctx.session).list(inp.project_id)


async def _budget_set(
    inp: BudgetSetInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[IntegrationBudgetOut]:
    env = IntegrationBudgetRepository(ctx.session).set(
        **inp.model_dump(exclude={"idempotency_key", "run_token", "expected_etag"})
    )
    return WriteEnvelope[IntegrationBudgetOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _budget_update(
    inp: BudgetUpdateInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[IntegrationBudgetOut]:
    env = IntegrationBudgetRepository(ctx.session).set(
        **inp.model_dump(exclude={"idempotency_key", "run_token", "expected_etag"})
    )
    return WriteEnvelope[IntegrationBudgetOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _budget_query_project(
    inp: BudgetQueryProjectInput, ctx: MCPContext, _emit: ProgressEmitter
) -> IntegrationBudgetOut:
    return IntegrationBudgetRepository(ctx.session).get(inp.project_id, inp.kind)


async def _schedule_list(
    inp: ScheduleListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> list[ScheduledJobOut]:
    return ScheduledJobRepository(ctx.session).list(inp.project_id)


async def _schedule_set(
    inp: ScheduleSetInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ScheduledJobOut]:
    env = ScheduledJobRepository(ctx.session).set(
        project_id=inp.project_id,
        kind=inp.kind,
        cron_expr=inp.cron_expr,
        enabled=inp.enabled,
    )
    return WriteEnvelope[ScheduledJobOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _schedule_toggle(
    inp: ScheduleToggleInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ScheduledJobOut]:
    env = ScheduledJobRepository(ctx.session).toggle(
        inp.job_id,
        enabled=inp.enabled,
        project_id=inp.project_id,
    )
    return WriteEnvelope[ScheduledJobOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _schedule_remove(
    inp: ScheduleRemoveInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ScheduledJobOut]:
    env = ScheduledJobRepository(ctx.session).toggle(
        inp.job_id,
        enabled=False,
        project_id=inp.project_id,
    )
    return WriteEnvelope[ScheduledJobOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


def register(registry: ToolRegistry) -> None:
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        (
            "project.list",
            "project.create",
            "project.get",
            "project.update",
            "project.delete",
            "budget.list",
            "budget.set",
            "budget.update",
            "budget.queryProject",
            "schedule.list",
            "schedule.set",
            "schedule.toggle",
            "schedule.remove",
        ),
    )


__all__ = ["register"]
