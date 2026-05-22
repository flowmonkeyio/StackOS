"""StackOS generic action describe/validate MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from content_stack.actions import (
    ActionDescribeOut,
    ActionExecutionOut,
    ActionRepository,
    ActionValidationOut,
)
from content_stack.mcp.context import MCPContext
from content_stack.mcp.contract import MCPInput, WriteEnvelope
from content_stack.mcp.permissions import active_run_plan_step
from content_stack.mcp.server import ToolRegistry, ToolSpec
from content_stack.mcp.streaming import ProgressEmitter


class ActionDescribeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "action_ref": "utils.image.generate"}},
    )

    project_id: int | None = None
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None


class ActionValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "action_ref": "utils.image.generate",
                "input_json": {"prompt": "Product photo"},
                "credential_ref": "cred_...",
            }
        },
    )

    project_id: int | None = None
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None
    input_json: dict[str, Any] | None = None
    credential_ref: str | None = None


class ActionExecuteInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "action_ref": "utils.image.generate",
                "input_json": {"prompt": "Product photo"},
                "credential_ref": "cred_...",
            }
        },
    )

    project_id: int
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None
    input_json: dict[str, Any] | None = None
    credential_ref: str | None = None
    idempotency_key: str | None = None
    dry_run: bool = False
    metadata_json: dict[str, Any] | None = None


async def _action_describe(
    inp: ActionDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionDescribeOut:
    return ActionRepository(ctx.session).describe(
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        project_id=inp.project_id,
    )


async def _action_validate(
    inp: ActionValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionValidationOut:
    return ActionRepository(ctx.session).validate(
        project_id=inp.project_id,
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        input_json=inp.input_json,
        credential_ref=inp.credential_ref,
    )


async def _action_execute(
    inp: ActionExecuteInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ActionExecutionOut]:
    plan, step = active_run_plan_step(ctx, "action.execute")
    settings = ctx.extras.get("settings")
    asset_dir = getattr(settings, "generated_assets_dir", None)
    env = await ActionRepository(ctx.session, asset_dir=asset_dir).execute(
        project_id=inp.project_id,
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        input_json=inp.input_json,
        credential_ref=inp.credential_ref,
        run_id=ctx.run_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        idempotency_key=inp.idempotency_key,
        dry_run=inp.dry_run,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[ActionExecutionOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="action.describe",
            description="Describe a StackOS action manifest and execution connector availability.",
            input_model=ActionDescribeInput,
            output_model=ActionDescribeOut,
            handler=_action_describe,
        )
    )
    registry.register(
        ToolSpec(
            name="action.validate",
            description="Validate a StackOS action payload without executing the action.",
            input_model=ActionValidateInput,
            output_model=ActionValidationOut,
            handler=_action_validate,
        )
    )
    registry.register(
        ToolSpec(
            name="action.execute",
            description="Execute a StackOS action inside an explicitly granted run-plan step.",
            input_model=ActionExecuteInput,
            output_model=WriteEnvelope[ActionExecutionOut],
            handler=_action_execute,
        )
    )


__all__ = ["register"]
