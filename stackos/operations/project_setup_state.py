"""Shared read projection and explicit local reminder preferences."""

from typing import Literal

from pydantic import ConfigDict, Field

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.project_setup import ProjectSetupOut, ProjectSetupRepository


class ProjectSetupGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, ge=1)
    workflow_key: str | None = Field(default=None, min_length=1, max_length=200)
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


class ProjectSetupRemindersInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, ge=1)
    dismiss_setup: bool = Field(default=False, strict=True)
    dismiss_updates: bool = Field(default=False, strict=True)
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


async def project_setup_get(
    inp: ProjectSetupGetInput, ctx: MCPContext, _emit: ProgressEmitter
) -> ProjectSetupOut:
    return ProjectSetupRepository(ctx.session).get(inp.project_id, workflow_key=inp.workflow_key)


async def project_setup_set_reminders(
    inp: ProjectSetupRemindersInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[ProjectSetupOut]:
    if not (ctx.extras.get("surface") == "rest" and ctx.extras.get("trusted_local_admin") is True):
        raise ToolNotGrantedError("project reminder changes require local-admin REST authority")
    return WriteEnvelope[ProjectSetupOut](
        project_id=inp.project_id,
        data=ProjectSetupRepository(ctx.session).set_reminders(
            inp.project_id, dismiss_setup=inp.dismiss_setup, dismiss_updates=inp.dismiss_updates
        ),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        operation_spec(
            name="projectSetup.get",
            summary="Read verified per-workflow setup and available updates.",
            input_model=ProjectSetupGetInput,
            output_model=ProjectSetupOut,
            handler=project_setup_get,
            grant_policy="direct-read",
            mutating=False,
            browser_safe=True,
            category="project",
            purpose="Share truthful setup state between Home and the scoped agent.",
            returns=(
                "Compact keeps counts, receipt revisions, diagnostics and raw detail refs.",
                "Raw includes source snapshots, checks, adaptations and latest partial results.",
                "Unknown provenance is not proof that local setup is missing.",
            ),
            examples=(
                OperationExample(title="Inspect project setup", arguments={"project_id": 1}),
            ),
        ),
        OperationSpec(
            name="projectSetup.setReminders",
            summary="Permanently dismiss project setup/update reminders independently.",
            input_model=ProjectSetupRemindersInput,
            output_model=WriteEnvelope[ProjectSetupOut],
            handler=project_setup_set_reminders,
            grant_policy="local-admin-console-write",
            response_policy=OperationResponsePolicy(),
            mutating=True,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/projectSetup.setReminders/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call projectSetup.setReminders"),
            ),
            category="project",
            purpose="Dismiss unsolicited reminders without changing applied setup or quiet counts.",
            prerequisites=(
                "Explicit local-admin REST/console authority; unavailable via agent MCP.",
            ),
            returns=("Persistent independent timestamps; false/omitted never resets a dismissal.",),
            examples=(
                OperationExample(
                    title="Dismiss setup reminder",
                    arguments={"project_id": 1, "dismiss_setup": True},
                ),
            ),
        ),
    ]
