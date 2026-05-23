"""StackOS action operation registrations."""

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
from content_stack.mcp.streaming import ProgressEmitter
from content_stack.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)


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


async def action_describe(
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


async def action_validate(
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


async def action_execute(
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


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="action.describe",
            summary=(
                "Describe one action manifest, connector availability, auth state, "
                "and budget state."
            ),
            input_model=ActionDescribeInput,
            output_model=ActionDescribeOut,
            handler=action_describe,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.describe/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call action.describe"),
            ),
            purpose=(
                "Use this before a run to inspect the exact action contract and whether "
                "the current project is configured to execute it."
            ),
            when_to_use=(
                "The agent needs schema, provider, connector, credential, or budget status.",
                "A human or script wants to check why an action is not executable yet.",
            ),
            prerequisites=(
                "Pass either action_ref or plugin_slug plus action_key.",
                "Pass project_id when project-specific availability is needed.",
            ),
            returns=(
                "Static manifest details.",
                "Connector registration and executable availability.",
                "Safe credential refs and setup reasons; never plaintext secrets.",
            ),
            examples=(
                OperationExample(
                    title="Describe OpenAI image generation",
                    arguments={"project_id": 1, "action_ref": "utils.image.generate"},
                ),
            ),
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="action.validate",
            summary="Validate one explicit action payload without executing the connector.",
            input_model=ActionValidateInput,
            output_model=ActionValidationOut,
            handler=action_validate,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.validate/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call action.validate"),
            ),
            purpose=(
                "Use this to check a concrete payload against the action schema, "
                "credential policy, and connector validator before execution."
            ),
            when_to_use=(
                "The agent has chosen an action and built a candidate input payload.",
                "A script wants a dry validation gate before creating a run plan.",
            ),
            prerequisites=(
                "Pass either action_ref or plugin_slug plus action_key.",
                "Pass input_json with the exact payload the action would receive.",
                "Pass credential_ref only when the action manifest allows credentials.",
            ),
            returns=(
                "valid=true when schema, credential policy, and connector validation pass.",
                "Structured issues with paths and machine-readable codes when validation fails.",
            ),
            examples=(
                OperationExample(
                    title="Validate sitemap fetch payload",
                    arguments={
                        "project_id": 1,
                        "action_ref": "utils.sitemap.fetch",
                        "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="action.execute",
            summary="Execute one action inside an explicitly granted run-plan step.",
            input_model=ActionExecuteInput,
            output_model=WriteEnvelope[ActionExecutionOut],
            handler=action_execute,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.execute/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call action.execute"),
            ),
            purpose=(
                "Use this only after a run plan has started and the active claimed step "
                "grants the exact action ref. StackOS resolves credentials inside the daemon."
            ),
            when_to_use=(
                "A run-plan step is currently running and names the exact action_ref.",
                "The frozen run-plan grant snapshot includes action.execute for that ref.",
            ),
            prerequisites=(
                "Pass project_id and run_token from runPlan.start.",
                "Exactly one run-plan step must be running.",
                "The requested action_ref must match the step and mcp_tool_grants refs.",
                "Pass only credential_ref; never pass secret values.",
            ),
            returns=(
                "A WriteEnvelope containing the public ActionExecutionOut.",
                "A redacted audit row linked to run_id, run_plan_id, and run_plan_step_id.",
                "Connector output JSON with secrets and provider raw credentials removed.",
            ),
            examples=(
                OperationExample(
                    title="Execute no-auth sitemap fetch from a run-plan step",
                    arguments={
                        "project_id": 1,
                        "run_token": "run-plan-token",
                        "action_ref": "utils.sitemap.fetch",
                        "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                    },
                ),
            ),
            grant_policy="run-plan-step-action-ref",
        ),
    ]


__all__ = [
    "ActionDescribeInput",
    "ActionExecuteInput",
    "ActionValidateInput",
    "action_describe",
    "action_execute",
    "action_validate",
    "operation_specs",
]
