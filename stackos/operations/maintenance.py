"""Explicit installation-wide upgrade preparation, never project-agent authority."""

from typing import Literal

from pydantic import ConfigDict, Field
from starlette.concurrency import run_in_threadpool

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
from stackos.repositories.managed_upgrades import ManagedUpgradeRepository
from stackos.task_delivery import prepare_managed_upgrade


class UpgradeStatusInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"
    upgrade_id: str | None = Field(default=None, min_length=1, max_length=160)


class PrepareUpgradeInput(UpgradeStatusInput):
    upgrade_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,160}$")
    source_identity: str = Field(min_length=1, max_length=240)
    target_identity: str = Field(min_length=1, max_length=240)
    expected_revision: int = Field(ge=0, strict=True)


def _authorize(inp, ctx):
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("auth_scope") != "daemon-admin"
        or ctx.extras.get("trusted_local_admin") is not True
        or inp.run_token is not None
    ):
        raise ToolNotGrantedError(
            "Managed upgrade requires explicit installation administrator authority",
            data={"code": "local_admin_required", "next_action": "use_local_admin_rest_or_cli"},
        )


async def upgrade_status(inp, ctx, _emitter):
    _authorize(inp, ctx)
    return ManagedUpgradeRepository(ctx.session).status(inp.upgrade_id)


async def prepare_upgrade(inp, ctx, _emitter):
    _authorize(inp, ctx)
    scheduler = ctx.extras.get("scheduler")
    factory = ctx.extras.get("session_factory")
    if scheduler is None or factory is None:
        raise ValidationError("Upgrade preparation requires the running local daemon")
    result = await run_in_threadpool(
        prepare_managed_upgrade,
        session_factory=factory,
        scheduler=scheduler,
        upgrade_id=inp.upgrade_id,
        source_identity=inp.source_identity,
        target_identity=inp.target_identity,
        expected_revision=inp.expected_revision,
    )
    return WriteEnvelope(data=result)


def operation_specs():
    return [
        OperationSpec(
            name=name,
            summary=summary,
            input_model=model,
            output_model=WriteEnvelope[dict] if mutating else dict,
            handler=handler,
            mutating=mutating,
            grant_policy="local-admin-maintenance",
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True, browser_safe=False, path=f"/api/v1/operations/{name}/call"
                ),
                cli=OperationSurface(enabled=True, command=f"ops call {name}"),
            ),
            response_policy=OperationResponsePolicy(
                default_mode="compact",
                allowed_modes=("compact", "raw"),
                ack_safe=False,
                compact_notes=(
                    "Retain build identity, revision, fence, checkpoint refs and repair context.",
                ),
            ),
            purpose=(
                "Fence installation admission and checkpoint authorized task delivery without "
                "changing workflow lifecycle. Retire exact native handles and callbacks; uncertain "
                "retirement holds replacement. Preparation does not install or continue work."
            ),
            prerequisites=("Explicit installation administrator authority; no project/run token.",),
            returns=(
                "Upgrade identity/revision, fence status and task-owned checkpoint references.",
            ),
            examples=(OperationExample(title="Inspect active upgrade", arguments={}),)
            if not mutating
            else (
                OperationExample(
                    title="Prepare managed upgrade",
                    arguments={
                        "upgrade_id": "upgrade-123",
                        "source_identity": "source-build",
                        "target_identity": "target-build",
                        "expected_revision": 0,
                    },
                ),
            ),
        )
        for name, summary, model, handler, mutating in (
            (
                "maintenance.upgradeStatus",
                "Inspect managed-upgrade preparation.",
                UpgradeStatusInput,
                upgrade_status,
                False,
            ),
            (
                "maintenance.prepareUpgrade",
                "Fence and quiesce task delivery for a managed upgrade.",
                PrepareUpgradeInput,
                prepare_upgrade,
                True,
            ),
        )
    ]
