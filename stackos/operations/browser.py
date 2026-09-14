"""Project-scoped browser lifecycle and an unchanged native CLI transport."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stackos.browser.runtime import (
    NativeSessionState,
    browser_profile_dir,
    get_browser_runtime,
    gstack_state_file,
    safe_browser_key,
)
from stackos.config import get_settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations._helpers import operation_spec
from stackos.operations.browser_contracts import (
    BROWSER_RAW_POLICY,
    BrowserCliRunInput,
    BrowserCliRunOut,
    BrowserProfileCreateInput,
    BrowserProfileListInput,
    BrowserRuntimeStatusInput,
    BrowserSessionListInput,
    BrowserSessionRefInput,
    BrowserSessionStartInput,
)
from stackos.repositories.base import Page
from stackos.repositories.browser import (
    BrowserProfileOut,
    BrowserRepository,
    BrowserRuntimeStatusOut,
    BrowserSessionOut,
)


def _settings(ctx: MCPContext):
    return ctx.extras.get("settings") or get_settings()


def _native_session_args(
    *, ctx: MCPContext, project_id: int, session_ref: str, profile_ref: str, profile_key: str
) -> dict[str, Any]:
    data_dir = Path(_settings(ctx).data_dir)
    return {
        "session_ref": session_ref,
        "profile_ref": profile_ref,
        "profile_dir": browser_profile_dir(
            data_dir, project_id=project_id, profile_key=profile_key
        ),
        "state_file": gstack_state_file(
            data_dir,
            project_id=project_id,
            profile_key=profile_key,
            session_key=session_ref.rsplit(":", 1)[-1],
        ),
        "data_dir": data_dir,
    }


async def _inspect_project_sessions(
    repo: BrowserRepository, ctx: MCPContext, project_id: int
) -> dict[str, NativeSessionState]:
    states = {}
    for item in repo.list_sessions(project_id=project_id).items:
        row, profile = repo.get_session(project_id=project_id, session_ref=item.session_ref)
        states[row.session_ref] = await get_browser_runtime().inspect_session(
            **_native_session_args(
                ctx=ctx,
                project_id=project_id,
                session_ref=row.session_ref,
                profile_ref=profile.profile_ref,
                profile_key=profile.profile_key,
            )
        )
    repo.reconcile_sessions(project_id=project_id, states=states)
    return states


async def _browser_runtime_status(
    inp: BrowserRuntimeStatusInput, ctx: MCPContext, _emit: ProgressEmitter
) -> BrowserRuntimeStatusOut:
    states = {}
    if inp.project_id is not None:
        states = await _inspect_project_sessions(
            BrowserRepository(ctx.session), ctx, inp.project_id
        )
    status = (
        get_browser_runtime()
        .status(data_dir=Path(_settings(ctx).data_dir))
        .to_dict(project_id=inp.project_id)
    )
    status["live_session_refs"] = [ref for ref, state in states.items() if state.owned]
    return BrowserRuntimeStatusOut.model_validate(status)


async def _browser_profile_create(
    inp: BrowserProfileCreateInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[BrowserProfileOut]:
    key = safe_browser_key(inp.profile_key)
    env = BrowserRepository(ctx.session).create_profile(
        project_id=inp.project_id,
        profile_key=key,
        name=inp.name or key,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope(data=env.data, run_id=ctx.run_id, project_id=inp.project_id)


async def _browser_profile_list(
    inp: BrowserProfileListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> Page[BrowserProfileOut]:
    return BrowserRepository(ctx.session).list_profiles(project_id=inp.project_id)


async def _browser_session_start(
    inp: BrowserSessionStartInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[BrowserSessionOut]:
    repo = BrowserRepository(ctx.session)
    if inp.profile_ref:
        profile = repo.get_profile(project_id=inp.project_id, profile_ref=inp.profile_ref)
    else:
        key = safe_browser_key(inp.profile_key)
        env = repo.create_profile(
            project_id=inp.project_id,
            profile_key=key,
            name=inp.name or key,
            metadata_json=inp.metadata_json,
        )
        profile = repo.get_profile(project_id=inp.project_id, profile_ref=env.data.profile_ref)
    session_ref = repo.session_ref(
        project_id=inp.project_id,
        profile_key=profile.profile_key,
        session_key=safe_browser_key(inp.session_key),
    )
    state = await get_browser_runtime().start_session(
        **_native_session_args(
            ctx=ctx,
            project_id=inp.project_id,
            session_ref=session_ref,
            profile_ref=profile.profile_ref,
            profile_key=profile.profile_key,
        )
    )
    repo.create_or_update_session(
        project_id=inp.project_id,
        profile=profile,
        session_ref=session_ref,
        metadata_json=inp.metadata_json,
    )
    repo.reconcile_sessions(project_id=inp.project_id, states={session_ref: state})
    row, profile = repo.get_session(project_id=inp.project_id, session_ref=session_ref)
    return WriteEnvelope(
        data=repo.session_out(row, profile, state=state, include_native_cli=True),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def _browser_session_stop(
    inp: BrowserSessionRefInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[BrowserSessionOut]:
    repo = BrowserRepository(ctx.session)
    row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    state = await get_browser_runtime().stop_session(
        **_native_session_args(
            ctx=ctx,
            project_id=inp.project_id,
            session_ref=row.session_ref,
            profile_ref=profile.profile_ref,
            profile_key=profile.profile_key,
        )
    )
    repo.reconcile_sessions(project_id=inp.project_id, states={row.session_ref: state})
    return WriteEnvelope(
        data=repo.session_out(row, profile, state=state),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def _browser_session_list(
    inp: BrowserSessionListInput, ctx: MCPContext, _emit: ProgressEmitter
) -> Page[BrowserSessionOut]:
    repo = BrowserRepository(ctx.session)
    states = await _inspect_project_sessions(repo, ctx, inp.project_id)
    page = repo.list_sessions(project_id=inp.project_id)
    return Page(
        items=[
            repo.session_out(
                *repo.get_session(project_id=inp.project_id, session_ref=item.session_ref),
                state=states[item.session_ref],
            )
            for item in page.items
        ],
        total_estimate=page.total_estimate,
    )


async def _browser_session_status(
    inp: BrowserSessionRefInput, ctx: MCPContext, _emit: ProgressEmitter
) -> BrowserSessionOut:
    repo = BrowserRepository(ctx.session)
    row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    state = await get_browser_runtime().inspect_session(
        **_native_session_args(
            ctx=ctx,
            project_id=inp.project_id,
            session_ref=row.session_ref,
            profile_ref=profile.profile_ref,
            profile_key=profile.profile_key,
        )
    )
    repo.reconcile_sessions(project_id=inp.project_id, states={row.session_ref: state})
    return repo.session_out(row, profile, state=state, include_native_cli=True)


async def _browser_cli_run(
    inp: BrowserCliRunInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[BrowserCliRunOut]:
    repo = BrowserRepository(ctx.session)
    row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    runtime = get_browser_runtime()
    result = await runtime.run_native(
        **_native_session_args(
            ctx=ctx,
            project_id=inp.project_id,
            session_ref=row.session_ref,
            profile_ref=profile.profile_ref,
            profile_key=profile.profile_key,
        ),
        argv=inp.argv,
        stdin=inp.stdin,
    )
    return WriteEnvelope(
        data=BrowserCliRunOut(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            encoding=result.encoding,
        ),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


def operation_specs():
    control = "direct-browser-control-or-run-plan-step-grant"
    definitions = (
        (
            "browser.runtime.status",
            "Inspect installed gstack runtime readiness.",
            BrowserRuntimeStatusInput,
            BrowserRuntimeStatusOut,
            _browser_runtime_status,
            False,
            False,
        ),
        (
            "browser.profile.create",
            "Create or update a persistent browser profile.",
            BrowserProfileCreateInput,
            WriteEnvelope[BrowserProfileOut],
            _browser_profile_create,
            True,
            True,
        ),
        (
            "browser.profile.list",
            "List project browser profiles.",
            BrowserProfileListInput,
            Page[BrowserProfileOut],
            _browser_profile_list,
            False,
            False,
        ),
        (
            "browser.session.start",
            "Create or reuse a visible native gstack session.",
            BrowserSessionStartInput,
            WriteEnvelope[BrowserSessionOut],
            _browser_session_start,
            True,
            True,
        ),
        (
            "browser.session.list",
            "Discover project sessions from owned native state.",
            BrowserSessionListInput,
            Page[BrowserSessionOut],
            _browser_session_list,
            False,
            False,
        ),
        (
            "browser.session.status",
            "Inspect a session and obtain its native CLI context.",
            BrowserSessionRefInput,
            BrowserSessionOut,
            _browser_session_status,
            False,
            True,
        ),
        (
            "browser.session.stop",
            "Stop a session and wait for native process retirement.",
            BrowserSessionRefInput,
            WriteEnvelope[BrowserSessionOut],
            _browser_session_stop,
            True,
            True,
        ),
        (
            "browser.cli.run",
            "Run the native gstack CLI in a selected session.",
            BrowserCliRunInput,
            WriteEnvelope[BrowserCliRunOut],
            _browser_cli_run,
            True,
            True,
        ),
    )
    return [
        operation_spec(
            name=name,
            summary=summary,
            input_model=input_model,
            output_model=output_model,
            handler=handler,
            purpose=(
                "Pass upstream argv and optional stdin unchanged. Native nonzero exit codes remain "
                "data; streams are exact UTF-8 or both base64 when either stream is not UTF-8. "
                "Use session.start/status native_cli for direct local CLI access."
                if name == "browser.cli.run"
                else summary
            ),
            mutating=mutating,
            grant_policy=control if mutating else "direct-read",
            secret_policy="raw-browser-output" if raw else "no-secret-output",
            category="browser",
            response_policy=BROWSER_RAW_POLICY if raw else None,
        )
        for name, summary, input_model, output_model, handler, mutating, raw in definitions
    ]


__all__ = ["operation_specs"]
