"""Shared setup admission, reports, read projection and reminder preferences."""

import hashlib
import hmac
import json
from typing import Any, Literal

from pydantic import ConfigDict, Field
from pydantic import ValidationError as ModelValidationError

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
from stackos.project_setup_execution import MainAgentPreferences
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ConflictError
from stackos.repositories.project_setup import (
    ProjectSetupOut,
    ProjectSetupRepository,
    SetupAttempt,
    SetupReport,
    SetupScope,
    SetupStartOut,
)

SETUP_REPLAY_DIGEST_FIELD = "_project_setup_request_digest"


class ProjectSetupGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, ge=1)
    workflow_key: str | None = Field(default=None, min_length=1, max_length=200)
    resolve_launch_preferences: bool = Field(
        default=False,
        strict=True,
        description="Console-only preview of effective native launch choices; "
        "ordinary reads retain saved preferences without inspecting hosts.",
    )
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
    session_id: int | None = Field(default=None, strict=True, ge=1)
    client_session_id: str | None = Field(default=None, min_length=1, max_length=160)
    entry_kind: Literal["existing_agent", "managed_ui"] = "existing_agent"
    mode: Literal["setup", "update"] = "setup"
    requested_scope: SetupScope = Field(
        default="ready_to_use",
        description="Ordinary setup/update completes activation. Select infrastructure_only "
        "explicitly for preparation; scope grants no local-admin or business-action authority.",
    )
    workflow_keys: list[str] = Field(default_factory=list, max_length=100)
    preferences: MainAgentPreferences | None = None
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
    if inp.entry_kind == "managed_ui":
        from starlette.concurrency import run_in_threadpool

        from stackos.project_setup_execution import start_managed

        _require_console(ctx)
        # Commit dispatcher's idempotency reservation before the background owner
        # opens another connection. No native work runs on this request's session.
        ctx.session.commit()
        result = await run_in_threadpool(
            start_managed,
            project_id=inp.project_id,
            session_factory=ctx.extras["session_factory"],
            scheduler=ctx.extras["scheduler"],
            mode=inp.mode,
            requested_scope=inp.requested_scope,
            workflow_keys=inp.workflow_keys,
            preferences=inp.preferences.model_dump(exclude_unset=True) if inp.preferences else None,
        )
        return WriteEnvelope(project_id=inp.project_id, data=result)
    if inp.preferences is not None:
        raise ToolNotGrantedError("existing-session entry cannot select a managed agent")
    if ctx.extras.get("surface") != "mcp":
        raise ToolNotGrantedError("existing-session setup requires its native MCP bridge")
    _require_setup_session(inp, ctx)
    return WriteEnvelope[SetupStartOut](
        project_id=inp.project_id,
        data=ProjectSetupRepository(ctx.session).start(
            inp.project_id,
            session_id=inp.session_id,
            mode=inp.mode,
            requested_scope=inp.requested_scope,
            workflow_keys=inp.workflow_keys,
        ),
    )


async def project_setup_report(
    inp: ProjectSetupReportInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[SetupAttempt]:
    _require_setup_session(inp, ctx)
    row = AgentRequestRepository(ctx.session)._fetch(
        project_id=inp.project_id, request_id=inp.request_id
    )
    attempt = SetupAttempt.model_validate((row.metadata_json or {}).get("project_setup"))
    return WriteEnvelope[SetupAttempt](
        project_id=inp.project_id,
        data=ProjectSetupRepository(ctx.session).report(
            inp.project_id,
            session_id=inp.session_id,
            request_id=inp.request_id,
            claim_token=inp.claim_token,
            expected_revision=inp.expected_revision,
            report=inp.report,
            _managed=attempt.entry_kind == "managed_ui",
            _session_report=attempt.entry_kind == "managed_ui",
        ),
    )


def _require_setup_session(
    inp: ProjectSetupStartInput | ProjectSetupReportInput, ctx: MCPContext
) -> None:
    session = ctx.session.get(AgentSession, inp.session_id) if inp.session_id is not None else None
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


def _require_console(ctx, *, require_executor=True):
    scopes = {"daemon-admin", "ui-console-operation"}
    if not require_executor:
        scopes.add("ui-read-operation")
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("client_surface", "rest") != "rest"
        or ctx.run is not None
    ) or not (
        ctx.extras.get("trusted_local_admin") is True or ctx.extras.get("auth_scope") in scopes
    ):
        raise ToolNotGrantedError("setup requires authenticated local-console authority")
    if require_executor and (
        not ctx.extras.get("scheduler") or not ctx.extras.get("session_factory")
    ):
        raise ToolNotGrantedError("setup requires the running daemon executor")


class ProjectSetupCancelInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, ge=1)
    request_id: int = Field(strict=True, ge=1)
    expected_revision: int | None = Field(default=None, strict=True, ge=1)
    external_work_stopped: bool = Field(default=False, strict=True)
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


class ProjectSetupContinueInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(strict=True, ge=1)
    request_id: int = Field(strict=True, ge=1)
    expected_revision: int = Field(strict=True, ge=1)
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


class ProjectSetupAnswerInput(ProjectSetupContinueInput):
    question_id: str = Field(min_length=1, max_length=120)
    question_revision: int = Field(strict=True, ge=1)
    answer: str = Field(min_length=1, max_length=8000)


async def project_setup_continue(inp: ProjectSetupContinueInput, ctx: MCPContext, _emit):
    from starlette.concurrency import run_in_threadpool

    from stackos.project_setup_execution import continue_managed

    _require_console(ctx)
    ctx.session.commit()
    answer = (
        {
            "question_id": inp.question_id,
            "question_revision": inp.question_revision,
            "answer": inp.answer,
        }
        if isinstance(inp, ProjectSetupAnswerInput)
        else {}
    )
    result = await run_in_threadpool(
        continue_managed,
        inp.project_id,
        inp.request_id,
        expected_revision=inp.expected_revision,
        session_factory=ctx.extras["session_factory"],
        scheduler=ctx.extras["scheduler"],
        **answer,
    )
    return WriteEnvelope(project_id=inp.project_id, data=result)


async def project_setup_cancel(inp: ProjectSetupCancelInput, ctx: MCPContext, _emit):
    from starlette.concurrency import run_in_threadpool

    from stackos.project_setup_execution import cancel_setup

    _require_console(ctx)
    ctx.session.commit()
    attempt = await run_in_threadpool(
        cancel_setup,
        inp.project_id,
        inp.request_id,
        expected_revision=inp.expected_revision,
        external_work_stopped=inp.external_work_stopped,
        session_factory=ctx.extras["session_factory"],
        scheduler=ctx.extras["scheduler"],
    )
    return WriteEnvelope(project_id=inp.project_id, data=attempt)


def setup_request_digest(
    inp: ProjectSetupStartInput
    | ProjectSetupReportInput
    | ProjectSetupContinueInput
    | ProjectSetupCancelInput,
) -> str:
    # Parsed defaults and canonical JSON normalize the intent. Only response
    # presentation and the lookup key may differ on a retry. Session, attempt,
    # token, expected revision and the complete report stay bound to the key.
    intent = inp.model_dump(mode="json", exclude={"response_mode", "idempotency_key"})
    return hashlib.sha256(
        json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_setup_replay(
    name: str,
    inp: ProjectSetupStartInput
    | ProjectSetupReportInput
    | ProjectSetupContinueInput
    | ProjectSetupCancelInput,
    ctx: MCPContext,
    cached: dict[str, Any],
) -> dict[str, Any]:
    """Reauthorize before exposing any cached token or reporting success."""
    if isinstance(inp, ProjectSetupCancelInput):
        _require_console(ctx)
        if cached.get(SETUP_REPLAY_DIGEST_FIELD) != setup_request_digest(inp):
            raise ConflictError("setup idempotency key belongs to another cancellation intent")
        saved = SetupAttempt.model_validate(cached["data"])
        row = AgentRequestRepository(ctx.session)._fetch(
            project_id=inp.project_id, request_id=inp.request_id
        )
        cancelled_current = SetupAttempt.model_validate(
            (row.metadata_json or {}).get("project_setup")
        )
        if (
            saved.request_id != cancelled_current.request_id
            or saved.attempt_ref != cancelled_current.attempt_ref
        ):
            raise ConflictError("cancellation retry target changed; inspect setup")
        return {
            key: value
            for key, value in {**cached, "data": cancelled_current.model_dump(mode="json")}.items()
            if key != SETUP_REPLAY_DIGEST_FIELD
        }
    if isinstance(inp, ProjectSetupContinueInput):
        _require_console(ctx)
        if cached.get(SETUP_REPLAY_DIGEST_FIELD) != setup_request_digest(inp):
            raise ConflictError("setup idempotency key belongs to another answer/continuation")
        continuation_saved = SetupStartOut.model_validate(cached["data"])
        requests = AgentRequestRepository(ctx.session)
        previous = requests._fetch(project_id=inp.project_id, request_id=inp.request_id)
        predecessor = SetupAttempt.model_validate(
            (previous.metadata_json or {}).get("project_setup")
        )
        row = requests._fetch(
            project_id=inp.project_id, request_id=continuation_saved.attempt.request_id
        )
        continuation_current = SetupAttempt.model_validate(
            (row.metadata_json or {}).get("project_setup")
        )
        if (
            predecessor.successor_request_id != continuation_current.request_id
            or continuation_current.predecessor_request_id != inp.request_id
            or continuation_current.attempt_ref != predecessor.attempt_ref
        ):
            raise ConflictError("continuation retry lineage changed; inspect setup")
        continuation_saved.attempt = continuation_current
        continuation_saved.claim_token = None
        return {
            key: value
            for key, value in {**cached, "data": continuation_saved.model_dump(mode="json")}.items()
            if key != SETUP_REPLAY_DIGEST_FIELD
        }
    if isinstance(inp, ProjectSetupStartInput) and inp.entry_kind == "managed_ui":
        _require_console(ctx)
        if cached.get(SETUP_REPLAY_DIGEST_FIELD) != setup_request_digest(inp):
            raise ConflictError("setup idempotency key belongs to another intent")
        managed_saved = SetupStartOut.model_validate(cached["data"])
        current = ProjectSetupRepository(ctx.session)._current_attempt(inp.project_id)
        if current is None or current.request_id != managed_saved.attempt.request_id:
            raise ConflictError("cached setup attempt is no longer current; inspect setup")
        managed_saved.attempt = current
        managed_saved.claim_token = None
        return {
            key: value
            for key, value in {**cached, "data": managed_saved.model_dump(mode="json")}.items()
            if key != SETUP_REPLAY_DIGEST_FIELD
        }
    _require_setup_session(inp, ctx)
    digest = cached.get(SETUP_REPLAY_DIGEST_FIELD)
    repair = {"operation": name, "project_id": inp.project_id, "next_action": "inspect_setup"}
    if not isinstance(digest, str) or not hmac.compare_digest(digest, setup_request_digest(inp)):
        raise ConflictError(
            "setup idempotency key belongs to another intent/session or lacks retry evidence",
            data=repair,
        )
    data = cached.get("data")
    try:
        result = (
            SetupStartOut.model_validate(data)
            if isinstance(inp, ProjectSetupStartInput)
            else SetupAttempt.model_validate(data)
        )
        saved = result.attempt if isinstance(result, SetupStartOut) else result
        requests = AgentRequestRepository(ctx.session)
        row = requests._fetch(project_id=inp.project_id, request_id=saved.request_id)
        current = SetupAttempt.model_validate((row.metadata_json or {}).get("project_setup"))
    except ModelValidationError as exc:
        raise ConflictError(
            "setup retry evidence is unreadable; inspect the attempt", data=repair
        ) from exc
    if (
        saved.project_id != inp.project_id
        or current.project_id != inp.project_id
        or current.request_id != saved.request_id
        or current.attempt_ref != saved.attempt_ref
        or current.root_request_id != saved.root_request_id
        or current.session_id != saved.session_id
    ):
        raise ConflictError("cached setup attempt identity changed", data=repair)
    if isinstance(inp, ProjectSetupStartInput):
        active = ProjectSetupRepository(ctx.session)._current_attempt(inp.project_id)
        if (
            current.status not in {"running", "needs_input"}
            or active is None
            or active.request_id != saved.request_id
        ):
            raise ConflictError(
                "cached setup start is no longer active; inspect current setup", data=repair
            )
        assert isinstance(result, SetupStartOut)
        if not result.attached:
            if current.session_id != inp.session_id or result.claim_token is None:
                raise ConflictError(
                    "cached setup start does not belong to this session", data=repair
                )
            requests._verify_claim(row, result.claim_token, _setup_owner=True)
        elif result.claim_token is not None:
            raise ConflictError(
                "cached setup attachment cannot grant writer authority", data=repair
            )
    else:
        if (
            current.session_id != inp.session_id
            or saved.request_id != inp.request_id
            or saved.revision != inp.expected_revision + 1
            or current.revision < saved.revision
        ):
            raise ConflictError(
                "cached report does not match this setup attempt/revision", data=repair
            )
        if current.status in {"running", "needs_input"}:
            requests._verify_claim(row, inp.claim_token, _setup_owner=True)
        elif saved.status in {"running", "needs_input"}:
            raise ConflictError("cached progress belongs to a finished setup attempt", data=repair)
        elif saved.status != current.status or saved.revision != current.revision:
            raise ConflictError("cached terminal report no longer matches its attempt", data=repair)
        # Terminal reports revoke the live claim. The original token/intent is
        # still checked by the saved digest above; its exact retry returns only
        # this historical request, without reacquiring or mutating any successor.
    return {key: value for key, value in cached.items() if key != SETUP_REPLAY_DIGEST_FIELD}


async def project_setup_get(
    inp: ProjectSetupGetInput, ctx: MCPContext, _emit: ProgressEmitter
) -> ProjectSetupOut:
    if inp.resolve_launch_preferences:
        _require_console(ctx, require_executor=False)
    state = ProjectSetupRepository(ctx.session).get(inp.project_id, workflow_key=inp.workflow_key)
    if inp.resolve_launch_preferences:
        from starlette.concurrency import run_in_threadpool

        from stackos.project_setup_execution import resolve_preferences

        state.launch_preferences = await run_in_threadpool(
            resolve_preferences, ctx.session, inp.project_id, validate_selection=False
        )
    return state


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
                    rest=OperationSurface(
                        enabled=name == "projectSetup.start",
                        browser_safe=name == "projectSetup.start",
                        path=f"/api/v1/operations/{name}/call"
                        if name == "projectSetup.start"
                        else None,
                    ),
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
                    "requested scope, activation coverage, question and repair context. "
                    "Raw retains report history.",
                    "Setup creates no business run or tracker task. Only managed_ui console "
                    "entry launches a native coordinator through shared capacity.",
                    "ready_to_use success requires current saved-default/native coverage. "
                    "Incomplete activation returns workflow/missing-claim repair context. "
                    "Adoption proposals are evidence only and never confer local-admin authority.",
                    "Retry the same session and normalized request with the same key; "
                    "only response_mode may change. Foreign/changed intent rejects before replay.",
                ),
                examples=(example,),
            )
            for name, summary, input_model, output_model, handler, example in (
                (
                    "projectSetup.start",
                    "Start or attach to project setup in an existing session or managed console.",
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
                            "requested_scope": "ready_to_use",
                        },
                    ),
                ),
                (
                    "projectSetup.report",
                    "Report checked workflow results from the owning setup session. "
                    "Source content may be omitted with exact inspected identities/digests; "
                    "the receipt owner verifies current sources and stores their full snapshots.",
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
        *[
            OperationSpec(
                name=name,
                summary=summary,
                input_model=model,
                output_model=WriteEnvelope[SetupStartOut],
                handler=project_setup_continue,
                grant_policy="local-admin-console-write",
                mutating=True,
                response_policy=OperationResponsePolicy(),
                category="project",
                surfaces=OperationSurfaces(
                    mcp=OperationSurface(enabled=False),
                    cli=OperationSurface(enabled=False),
                    rest=OperationSurface(
                        enabled=True, browser_safe=True, path=f"/api/v1/operations/{name}/call"
                    ),
                ),
                purpose="Link one successor request in the exact native session after retirement.",
                prerequisites=(
                    "Authenticated console; exact current request/revision; confirmed native exit.",
                ),
                returns=(
                    "Compact/raw retain lineage, question, revision, outcomes and recovery. "
                    "No writer token is exposed.",
                ),
                examples=(OperationExample(title=summary, arguments=example),),
            )
            for name, summary, model, example in (
                (
                    "projectSetup.answer",
                    "Answer the current managed setup question once.",
                    ProjectSetupAnswerInput,
                    {
                        "project_id": 1,
                        "request_id": 123,
                        "expected_revision": 2,
                        "question_id": "goal",
                        "question_revision": 1,
                        "answer": "Maintain this project",
                    },
                ),
                (
                    "projectSetup.continue",
                    "Recover remaining setup, or retry confirmed never-started work "
                    "with its frozen settings.",
                    ProjectSetupContinueInput,
                    {"project_id": 1, "request_id": 123, "expected_revision": 2},
                ),
            )
        ],
        OperationSpec(
            name="projectSetup.cancel",
            summary="Cancel this project's setup with explicit external-work acknowledgement.",
            input_model=ProjectSetupCancelInput,
            output_model=WriteEnvelope[SetupAttempt],
            handler=project_setup_cancel,
            grant_policy="local-admin-console-write",
            mutating=True,
            response_policy=OperationResponsePolicy(),
            category="project",
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                cli=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/projectSetup.cancel/call",
                ),
            ),
            purpose=(
                "Fence managed work until native retirement, or revoke an existing-agent "
                "claim after exact-revision operator acknowledgement. Preserve results."
            ),
            examples=(
                OperationExample(
                    title="Cancel managed setup", arguments={"project_id": 1, "request_id": 123}
                ),
                OperationExample(
                    title="Cancel stopped existing-agent setup",
                    arguments={
                        "project_id": 1,
                        "request_id": 123,
                        "expected_revision": 2,
                        "external_work_stopped": True,
                    },
                ),
            ),
        ),
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
                "Console-only resolve_launch_preferences previews effective launch choices "
                "through the launch resolver; normal reads do not inspect native hosts.",
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
