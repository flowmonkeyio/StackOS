"""StackOS generic action describe/validate MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from content_stack.actions import ActionDescribeOut, ActionRepository, ActionValidationOut
from content_stack.mcp.context import MCPContext
from content_stack.mcp.contract import MCPInput
from content_stack.mcp.server import ToolRegistry, ToolSpec
from content_stack.mcp.streaming import ProgressEmitter


class ActionDescribeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"action_ref": "utils.image.generate"}},
    )

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


async def _action_describe(
    inp: ActionDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionDescribeOut:
    return ActionRepository(ctx.session).describe(
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
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


__all__ = ["register"]
