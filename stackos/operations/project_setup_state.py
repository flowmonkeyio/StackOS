"""Shared read projection and explicit local reminder preferences."""

from typing import Literal

from pydantic import ConfigDict, Field

from stackos.db.models import AgentSession
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
from stackos.repositories.project_setup import (
    ProjectSetupOut,
    ProjectSetupRepository,
    SetupAttempt,
    SetupReport,
    SetupStartOut,
)


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


class ProjectSetupStartInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, ge=1)
    session_id: int = Field(strict=True, ge=1)
    client_session_id: str = Field(min_length=1, max_length=160)
    entry_kind: Literal["existing_agent"] = "existing_agent"
    mode: Literal["setup", "update"] = "setup"
    workflow_keys: list[str] = Field(min_length=1, max_length=100)
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


class ProjectSetupReportInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, ge=1)
    session_id: int = Field(strict=True, ge=1)
    client_session_id: str = Field(min_length=1, max_length=160)
    request_id: int = Field(strict=True, ge=1)
    claim_token: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(strict=True, ge=1)
    report: SetupReport
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


async def project_setup_start(
    inp: ProjectSetupStartInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[SetupStartOut]:
    _require_setup_session(inp, ctx)
    return WriteEnvelope[SetupStartOut](
        project_id=inp.project_id,
        data=ProjectSetupRepository(ctx.session).start(
            inp.project_id,
            session_id=inp.session_id,
            mode=inp.mode,
            workflow_keys=inp.workflow_keys,
        ),
    )


async def project_setup_report(
    inp: ProjectSetupReportInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[SetupAttempt]:
    _require_setup_session(inp, ctx)
    return WriteEnvelope[SetupAttempt](
        project_id=inp.project_id,
        data=ProjectSetupRepository(ctx.session).report(
            inp.project_id,
            session_id=inp.session_id,
            request_id=inp.request_id,
            claim_token=inp.claim_token,
            expected_revision=inp.expected_revision,
            report=inp.report,
        ),
    )


def _require_setup_session(
    inp: ProjectSetupStartInput | ProjectSetupReportInput, ctx: MCPContext
) -> None:
    session = ctx.session.get(AgentSession, inp.session_id)
    if (
        session is None
        or session.project_id != inp.project_id
        or session.client_session_id != inp.client_session_id
    ):
        raise ToolNotGrantedError(
            "setup requires this bridge's project session; call workspace.startSession first",
            data={
                "project_id": inp.project_id,
                "session_id": inp.session_id,
                "next_action": "workspace.startSession",
            },
        )


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
        *[
            OperationSpec(
                name=name,
                summary=summary,
                input_model=input_model,
                output_model=output_model,
                handler=handler,
                grant_policy="direct-setup-write",
                mutating=True,
                response_policy=OperationResponsePolicy(),
                category="project",
                surfaces=OperationSurfaces(
                    mcp=OperationSurface(enabled=True),
                    rest=OperationSurface(enabled=False),
                    cli=OperationSurface(enabled=False),
                ),
                purpose=summary,
                prerequisites=(
                    "Use the selected project's existing agent session. "
                    "No filesystem authority is granted.",
                ),
                returns=(
                    "Keep the initial claim token in the executing session; "
                    "attached observers receive no writer token.",
                    "Compact retains attempt/revision, workflow outcomes, "
                    "question and repair context. "
                    "Raw retains report history.",
                    "Setup creates no business run, tracker task or native launch.",
                ),
                examples=(example,),
            )
            for name, summary, input_model, output_model, handler, example in (
                (
                    "projectSetup.start",
                    "Start or attach to one existing-session setup attempt.",
                    ProjectSetupStartInput,
                    WriteEnvelope[SetupStartOut],
                    project_setup_start,
                    OperationExample(
                        title="Start in the current session",
                        arguments={
                            "project_id": 1,
                            "session_id": 1,
                            "client_session_id": "<bridge-injected>",
                            "workflow_keys": ["engineering.tracked-delivery"],
                        },
                    ),
                ),
                (
                    "projectSetup.report",
                    "Report checked workflow results from the owning setup session.",
                    ProjectSetupReportInput,
                    WriteEnvelope[SetupAttempt],
                    project_setup_report,
                    OperationExample(
                        title="Report bounded progress",
                        arguments={
                            "project_id": 1,
                            "session_id": 1,
                            "client_session_id": "<bridge-injected>",
                            "request_id": 1,
                            "claim_token": "<initial-claim-token>",
                            "expected_revision": 1,
                            "report": {
                                "status": "running",
                                "progress": {
                                    "message": "Inspecting existing adaptations",
                                    "evidence_ref": "session:inspection",
                                },
                            },
                        },
                    ),
                ),
            )
        ],
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
