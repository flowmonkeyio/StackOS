"""Project, credential, budget, and schedule MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from stackos.config import Settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.project_cleanup import ProjectCleanupResult, ProjectCleanupService
from stackos.repositories.base import Page, RepositoryError, ValidationError
from stackos.repositories.projects import (
    IntegrationBudgetOut,
    IntegrationBudgetRepository,
    ProjectName,
    ProjectOut,
    ProjectRepository,
    ScheduleConfiguration,
    ScheduledJobOut,
    ScheduledJobRepository,
    ScheduledRunOut,
    ScheduleRemovalOut,
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
    name: ProjectName
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
    scope: Literal["project", "catalog"] = "project"


class ProjectMoveInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    placement: Literal["top", "before", "after"]
    anchor_id: int | None = Field(default=None, ge=1)
    scope: Literal["project", "catalog"] = "project"

    @model_validator(mode="after")
    def _anchor_shape(self) -> ProjectMoveInput:
        if (self.placement == "top") != (self.anchor_id is None):
            raise ValueError("top requires no anchor; before/after require an anchor_id")
        return self


class ProjectDeleteConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    permanent: Literal[True]
    project_id: int = Field(ge=1)
    project_slug: str = Field(min_length=1, max_length=80)

    @field_validator("permanent", mode="before")
    @classmethod
    def _permanent(cls, value: Any) -> Literal[True]:
        if value is not True:
            raise ValueError("permanent deletion requires permanent=true")
        return True


class ProjectDeleteInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(strict=True, ge=1)
    confirmation: ProjectDeleteConfirmation
    scope: Literal["project", "catalog"] = "project"
    response_mode: Literal["compact", "raw", "standard", "verbose"] | None = None


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


class ScheduleSetInput(MCPInput, ScheduleConfiguration):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "kind": "weekly-review", "cron_expr": "0 9 * * 1"}
        },
    )

    project_id: int


class ScheduleToggleInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "job_id": 1, "enabled": False}},
    )

    job_id: int
    project_id: int
    enabled: bool


class ScheduleRemoveInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "job_id": 1}},
    )

    job_id: int
    project_id: int


class ScheduleRunsInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    schedule_id: int | None = Field(default=None, ge=1)
    limit: int = Field(default=50, ge=1, le=200)
    after_id: int | None = Field(default=None, ge=1)


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
    _project_management_scope(inp.scope, ctx)
    if ctx.extras.get("auth_scope") != "daemon-admin" and set(inp.patch) != {"name"}:
        raise ToolNotGrantedError(
            "project.update permits only display name outside local administration"
        )
    env = ProjectRepository(ctx.session).update(inp.project_id, **inp.patch)
    return WriteEnvelope[ProjectOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


def _project_management_scope(scope: str, ctx: MCPContext) -> None:
    if ctx.extras.get("auth_scope") == "ui-console-operation" and scope != "catalog":
        raise ToolNotGrantedError("console project management requires explicit catalog scope")
    if scope == "catalog" and not (
        ctx.extras.get("surface") == "rest"
        and ctx.extras.get("auth_scope") in {"daemon-admin", "ui-console-operation"}
    ):
        raise ToolNotGrantedError(
            "catalog project management requires explicit local-admin REST authority"
        )


async def _project_move(
    inp: ProjectMoveInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ProjectOut]:
    _project_management_scope(inp.scope, ctx)
    env = ProjectRepository(ctx.session).move(
        inp.project_id, placement=inp.placement, anchor_id=inp.anchor_id
    )
    return WriteEnvelope[ProjectOut](data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _project_delete(
    inp: ProjectDeleteInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ProjectCleanupResult]:
    _project_management_scope(inp.scope, ctx)
    settings = ctx.extras.get("settings")
    if not isinstance(settings, Settings):
        raise ValidationError("project cleanup settings are unavailable")
    try:
        result = await ProjectCleanupService(ctx.session, settings).delete(
            inp.project_id, confirmation=inp.confirmation.model_dump()
        )
    except RepositoryError:
        raise
    except Exception as exc:
        # Never expose filesystem paths or provider/process error text.
        raise RepositoryError(
            "project cleanup could not complete; inspect the target before retrying",
            data={"project_id": inp.project_id, "error_code": type(exc).__name__},
        ) from None
    # Do not dereference an expired ORM Project/Run after the final purge.
    return WriteEnvelope[ProjectCleanupResult](data=result, run_id=None, project_id=inp.project_id)


async def _budget_list(
    inp: BudgetListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> list[IntegrationBudgetOut]:
    return IntegrationBudgetRepository(ctx.session).list(inp.project_id)


async def _budget_set(
    inp: BudgetSetInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[IntegrationBudgetOut]:
    env = IntegrationBudgetRepository(ctx.session).set(
        **inp.model_dump(exclude={"idempotency_key", "run_token", "expected_etag", "response_mode"})
    )
    return WriteEnvelope[IntegrationBudgetOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _budget_update(
    inp: BudgetUpdateInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[IntegrationBudgetOut]:
    env = IntegrationBudgetRepository(ctx.session).set(
        **inp.model_dump(exclude={"idempotency_key", "run_token", "expected_etag", "response_mode"})
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
        name=inp.name,
        timezone=inp.timezone,
        template_key=inp.template_key,
        inputs_json=inp.inputs_json,
        execution_json=inp.execution_json.model_dump(exclude_unset=True)
        if inp.execution_json
        else None,
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
) -> WriteEnvelope[ScheduleRemovalOut]:
    env = ScheduledJobRepository(ctx.session).remove(
        inp.job_id,
        project_id=inp.project_id,
    )
    return WriteEnvelope[ScheduleRemovalOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _schedule_runs(
    inp: ScheduleRunsInput, ctx: MCPContext, _emit: ProgressEmitter
) -> Page[ScheduledRunOut]:
    return ScheduledJobRepository(ctx.session).runs(
        project_id=inp.project_id,
        schedule_id=inp.schedule_id,
        limit=inp.limit,
        after_id=inp.after_id,
    )


def register(registry: ToolRegistry) -> None:
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        (
            "project.list",
            "project.portfolio",
            "project.create",
            "project.get",
            "project.update",
            "project.move",
            "project.delete",
            "budget.list",
            "budget.set",
            "budget.update",
            "budget.queryProject",
            "schedule.list",
            "schedule.set",
            "schedule.toggle",
            "schedule.remove",
            "schedule.runs",
        ),
    )


__all__ = ["register"]
