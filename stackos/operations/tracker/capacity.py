"""Explicit local operator control over project-owned task capacity."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from stackos.host_mcp.agent_execution import managed_launch_lock
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ValidationError
from stackos.task_capacity import MAX_CAPACITY_UPDATES, save_limits
from stackos.task_delivery import delivery_capacity, drain_delivery


class CapacityInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


class DeliveryCapacityInput(CapacityInput):
    project_id: int | None = Field(default=None, strict=True, gt=0)


class ProjectCapacityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, gt=0)
    max_concurrent_tasks: int = Field(strict=True, ge=1, le=32)


class SetDeliveryCapacityInput(CapacityInput):
    updates: list[ProjectCapacityUpdate] = Field(min_length=1, max_length=MAX_CAPACITY_UPDATES)

    @model_validator(mode="after")
    def unique_projects(self):
        if len({item.project_id for item in self.updates}) != len(self.updates):
            raise ValueError("Each project_id must appear only once in updates")
        return self


class ProjectDeliveryCapacityOut(BaseModel):
    project_id: int
    project_name: str
    project_slug: str
    configured_limit: int
    running_count: int
    queued_count: int


class DeliveryCapacityOut(BaseModel):
    scope: Literal["project"] = "project"
    projects: list[ProjectDeliveryCapacityOut]
    supported_min: int = 1
    supported_max: int = 32


def _runtime(inp, ctx):
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("trusted_local_admin") is not True
        or inp.run_token is not None
    ):
        raise ToolNotGrantedError("Task capacity requires an explicit local operator session.")
    scheduler = ctx.extras.get("scheduler")
    factory = ctx.extras.get("session_factory")
    if scheduler is None or factory is None:
        raise ValidationError("Task capacity requires the running local daemon.")
    return scheduler, factory


async def read_capacity(inp: DeliveryCapacityInput, ctx: MCPContext, _emitter):
    scheduler, _ = _runtime(inp, ctx)
    value = await run_in_threadpool(delivery_capacity, ctx.session, scheduler, inp.project_id)
    return DeliveryCapacityOut.model_validate(value)


async def set_capacity(inp: SetDeliveryCapacityInput, ctx: MCPContext, _emitter):
    scheduler, factory = _runtime(inp, ctx)

    def save():
        with managed_launch_lock:
            try:
                changed = save_limits(
                    ctx.session,
                    {item.project_id: item.max_concurrent_tasks for item in inp.updates},
                )
            except SQLAlchemyError as error:
                raise ValidationError(
                    "Task capacity could not be saved. The previous limits remain effective.",
                    data={"code": "delivery-capacity-save", "retryable": True},
                ) from error
        # Saving is complete. Per-task registration errors are recorded on the
        # affected delivery, and must not misreport a durable preference as failed.
        try:
            if changed:
                drain_delivery(session_factory=factory, scheduler=scheduler)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Queued task registration failed after capacity save"
            )
        return DeliveryCapacityOut.model_validate(delivery_capacity(ctx.session, scheduler))

    return WriteEnvelope(data=await run_in_threadpool(save))


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name=name,
            summary=summary,
            input_model=model,
            output_model=WriteEnvelope[DeliveryCapacityOut] if mutating else DeliveryCapacityOut,
            handler=handler,
            mutating=mutating,
            grant_policy="local-admin-console-write" if mutating else "local-admin-console-read",
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True, browser_safe=True, path=f"/api/v1/operations/{name}/call"
                ),
                cli=OperationSurface(enabled=True, command=f"ops call {name}"),
            ),
            response_policy=OperationResponsePolicy(
                default_mode="compact",
                allowed_modes=("compact", "raw"),
                ack_safe=False,
                compact_notes=(
                    "Preserve project identities, effective limits, "
                    "running/queued counts and bounds.",
                ),
            ),
            purpose=(
                "Read or atomically configure independent project task limits. "
                "Running means admitted task slots, including callbacks "
                "starting or retiring. Queued work has no slot yet. Default 3, range 1..32. "
                "Lowering never cancels work. No account isolation or cross-device quota."
            ),
            prerequisites=("Explicit local operator authority; no run token.",),
            returns=("Project identities, durable limits and current project admission counts.",),
            examples=(OperationExample(title="Read local capacity", arguments={}),)
            if not mutating
            else (
                OperationExample(
                    title="Save project limits atomically",
                    arguments={"updates": [{"project_id": 1, "max_concurrent_tasks": 6}]},
                ),
            ),
        )
        for name, summary, model, handler, mutating in (
            (
                "tracker.deliveryCapacity",
                "Read local task capacity.",
                DeliveryCapacityInput,
                read_capacity,
                False,
            ),
            (
                "tracker.setDeliveryCapacity",
                "Save local task capacity.",
                SetDeliveryCapacityInput,
                set_capacity,
                True,
            ),
        )
    ]
