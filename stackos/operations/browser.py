"""Browser automation operation contracts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from stackos.browser.manifest import browser_method_manifest, get_method_spec
from stackos.browser.runtime import (
    BROWSER_PROVIDER,
    browser_profile_dir,
    get_browser_runtime,
    safe_browser_key,
    sanitize_launch_options,
)
from stackos.config import get_settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationResponsePolicy
from stackos.repositories.base import Page, RepositoryError, ValidationError
from stackos.repositories.browser import (
    BrowserCallOut,
    BrowserProfileOut,
    BrowserRepository,
    BrowserRuntimeStatusOut,
    BrowserSessionOut,
)

_BROWSER_SIDE_EFFECT_POLICY = OperationResponsePolicy(
    default_mode="raw",
    allowed_modes=("raw",),
    ack_safe=False,
    raw_only_reason=(
        "Browser operations are live external side effects. Return the full redacted receipt, "
        "session refs, artifact refs, result value, and retry context."
    ),
)
_SAFE_INPUT_FIELD_NAMES = frozenset(
    {
        "button",
        "click_count",
        "delay_ms",
        "full_page",
        "key",
        "selector",
        "state",
        "timeout_ms",
        "wait_until",
    }
)


class BrowserRuntimeStatusInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1}},
    )

    project_id: int | None = None


class BrowserProfileCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "profile_key": "personal-brand",
                "name": "Personal Brand Browser",
            }
        },
    )

    project_id: int
    profile_key: str
    name: str | None = None
    allowed_origins_json: list[str] | None = None
    launch_options_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class BrowserProfileListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class BrowserSessionStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "profile_key": "personal-brand",
                "session_key": "linkedin",
                "headless": False,
            }
        },
    )

    project_id: int
    profile_key: str = "default"
    profile_ref: str | None = None
    session_key: str = "default"
    name: str | None = None
    headless: bool = False
    launch_options_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class BrowserSessionRefInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
            }
        },
    )

    project_id: int
    session_ref: str


class BrowserPageSnapshotInput(BrowserSessionRefInput):
    page_ref: str | None = None


class BrowserSessionListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class BrowserPageCallInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "method": "goto",
                "arguments": {"url": "https://example.com"},
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    method: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None


class BrowserContextCallInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "method": "cookies",
                "arguments": {},
            }
        },
    )

    project_id: int
    session_ref: str
    method: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None


class BrowserHandleCallInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "handle_ref": "browser-session:project-1:default:default:handle-1",
                "method": "click",
            }
        },
    )

    project_id: int
    session_ref: str
    handle_ref: str
    method: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None


class BrowserScriptRunInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "script": "() => document.title",
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    script: str
    arg: Any | None = None


class BrowserScriptInjectInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "script": "window.__stackosInjected = true;",
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    script: str


class BrowserScreenshotInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "full_page": True,
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    full_page: bool = True
    name: str | None = None


class BrowserMethodManifestOut(BaseModel):
    provider: str = BROWSER_PROVIDER
    parity_model: str = "full-control-public-api"
    methods: list[dict[str, Any]]
    notes: list[str]


def _settings(ctx: MCPContext):
    value = ctx.extras.get("settings")
    return value if value is not None else get_settings()


def _now_slug() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"data", "blob", "javascript"}:
        return f"{parsed.scheme}:<redacted>"
    if parsed.scheme in {"about", "file"}:
        return f"{parsed.scheme}:<redacted>" if parsed.scheme == "file" else value
    if parsed.scheme and parsed.netloc:
        redacted = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            redacted += "?<redacted>"
        if parsed.fragment:
            redacted += "#<redacted>"
        return redacted
    return value[:240]


def _origin(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _value_summary(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, str):
        return {"type": "str", "length": len(value), "sha256": _digest(value)}
    if isinstance(value, bool):
        return {"type": "bool"}
    if isinstance(value, int | float):
        return {"type": type(value).__name__}
    if isinstance(value, list | tuple):
        return {"type": "array", "count": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "key_count": len(value)}
    return {"type": type(value).__name__, "repr": repr(value)[:120]}


def _result_summary(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "method": result.method,
        "status": result.status,
        "page_ref": result.page_ref,
    }
    if result.url is not None:
        out["url"] = _redact_url(result.url)
    if result.title is not None:
        out["title_summary"] = _value_summary(result.title)
    if result.value is not None:
        out["value_summary"] = _value_summary(result.value)
    if result.page_refs is not None:
        out["page_refs"] = result.page_refs
    return out


def _failure_summary(
    *,
    method: str,
    page_ref: str | None,
    url: str | None,
    exc: Exception,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "method": method,
        "status": "failed",
        "error_type": type(exc).__name__,
    }
    if page_ref is not None:
        out["page_ref"] = page_ref
    if url is not None:
        out["url"] = _redact_url(url)
    return out


def _error_summary(exc: Exception) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: message_sha256={_digest(message)} length={len(message)}"


def _raise_safe_browser_error(
    *,
    operation: str,
    method: str,
    exc: Exception,
) -> None:
    raise RepositoryError(
        "browser operation failed",
        data={
            "operation": operation,
            "method": method,
            "error_type": type(exc).__name__,
            "error": _error_summary(exc),
            "receipt_recorded": True,
        },
    ) from exc


def _is_not_live_session_error(exc: Exception) -> bool:
    if not isinstance(exc, ValidationError):
        return False
    return exc.detail == "browser session is not live in this daemon process"


def _call_summary(
    method: str,
    arguments: dict[str, Any],
    args: list[Any] | None,
    kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"method": method}
    if args is not None:
        summary["arg_count"] = len(args)
    if kwargs is not None:
        summary["kwarg_keys"] = sorted(str(key) for key in kwargs)
    for key, value in arguments.items():
        if key in {"script", "handler", "value", "text"} and isinstance(value, str):
            summary[f"{key}_length"] = len(value)
            summary[f"{key}_sha256"] = _digest(value)
        elif key == "url" and isinstance(value, str):
            summary[key] = _redact_url(value)
        elif key == "arg":
            summary["arg_summary"] = _value_summary(value)
        elif key in _SAFE_INPUT_FIELD_NAMES:
            summary[key] = value
        else:
            summary[f"{key}_summary"] = _value_summary(value)
    return summary


def _session_key_from_ref(session_ref: str) -> str:
    return safe_browser_key(session_ref.rsplit(":", 1)[-1] or "default")


async def _browser_runtime_status(
    inp: BrowserRuntimeStatusInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> BrowserRuntimeStatusOut:
    runtime_status = get_browser_runtime().status()
    if inp.project_id is not None:
        BrowserRepository(ctx.session).reconcile_running_sessions(
            project_id=inp.project_id,
            live_session_refs=runtime_status.live_session_refs,
        )
    status = runtime_status.to_dict(project_id=inp.project_id)
    status["method_manifest"] = browser_method_manifest()
    return BrowserRuntimeStatusOut.model_validate(status)


async def _browser_method_manifest(
    _inp: BrowserRuntimeStatusInput,
    _ctx: MCPContext,
    _emit: ProgressEmitter,
) -> BrowserMethodManifestOut:
    return BrowserMethodManifestOut(
        methods=browser_method_manifest(),
        notes=[
            "The core policy is parity-first: public Playwright methods are callable.",
            "browser.page.call and browser.context.call accept raw method, args, and kwargs.",
            "Object results return handle_ref values that can be used with browser.handle.call.",
            "Convenience operations exist for script run/injection, snapshots, and screenshots.",
        ],
    )


async def _browser_profile_create(
    inp: BrowserProfileCreateInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserProfileOut]:
    key = safe_browser_key(inp.profile_key)
    launch_options = sanitize_launch_options(inp.launch_options_json)
    env = BrowserRepository(ctx.session).create_profile(
        project_id=inp.project_id,
        profile_key=key,
        name=inp.name or key,
        allowed_origins_json=inp.allowed_origins_json,
        launch_options_json=launch_options,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope(data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _browser_profile_list(
    inp: BrowserProfileListInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> Page[BrowserProfileOut]:
    return BrowserRepository(ctx.session).list_profiles(project_id=inp.project_id)


async def _browser_session_start(
    inp: BrowserSessionStartInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserSessionOut]:
    repo = BrowserRepository(ctx.session)
    profile_key = safe_browser_key(inp.profile_key)
    request_launch_options = sanitize_launch_options(inp.launch_options_json)
    if inp.profile_ref:
        profile = repo.get_profile(project_id=inp.project_id, profile_ref=inp.profile_ref)
        profile_key = profile.profile_key
        profile_launch_options = sanitize_launch_options(profile.launch_options_json)
        launch_options = {
            **(profile_launch_options or {}),
            **(request_launch_options or {}),
        } or None
    else:
        launch_options = request_launch_options
        profile_env = repo.create_profile(
            project_id=inp.project_id,
            profile_key=profile_key,
            name=inp.name or profile_key,
            allowed_origins_json=None,
            launch_options_json=launch_options,
            metadata_json=inp.metadata_json,
        )
        profile = repo.get_profile(
            project_id=inp.project_id,
            profile_ref=profile_env.data.profile_ref,
        )
    session_key = safe_browser_key(inp.session_key)
    session_ref = repo.session_ref(
        project_id=inp.project_id,
        profile_key=profile_key,
        session_key=session_key,
    )
    settings = _settings(ctx)
    input_summary = {"profile_key": profile_key, "session_key": session_key}
    try:
        live = await get_browser_runtime().start_session(
            session_ref=session_ref,
            profile_ref=profile.profile_ref,
            profile_dir=browser_profile_dir(
                Path(settings.data_dir),
                project_id=inp.project_id,
                profile_key=profile_key,
            ),
            launch_options=launch_options,
            headless=inp.headless,
        )
    except Exception as exc:
        repo.record_receipt(
            project_id=inp.project_id,
            profile_id=profile.id,
            session_id=None,
            artifact_id=None,
            session_ref=session_ref,
            page_ref=f"{session_ref}:page-1",
            operation="browser.session.start",
            method="start",
            side_effect_class="session",
            target_url=None,
            target_origin=None,
            status="failed",
            input_summary_json=input_summary,
            result_json=_failure_summary(
                method="start",
                page_ref=f"{session_ref}:page-1",
                url=None,
                exc=exc,
            ),
            error=_error_summary(exc),
        )
        _raise_safe_browser_error(
            operation="browser.session.start",
            method="start",
            exc=exc,
        )
    env = repo.create_or_update_session(
        project_id=inp.project_id,
        profile=profile,
        session_ref=session_ref,
        headless=inp.headless,
        page_refs=live.page_refs,
        current_url=getattr(live.page, "url", None),
        metadata_json=inp.metadata_json,
    )
    repo.record_receipt(
        project_id=inp.project_id,
        profile_id=profile.id,
        session_id=env.data.id,
        artifact_id=None,
        session_ref=session_ref,
        page_ref=live.page_ref,
        operation="browser.session.start",
        method="start",
        side_effect_class="session",
        target_url=_redact_url(env.data.current_url),
        target_origin=_origin(env.data.current_url),
        status="ok",
        input_summary_json=input_summary,
        result_json={"session_ref": session_ref, "page_refs": live.page_refs},
    )
    return WriteEnvelope(data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _browser_session_stop(
    inp: BrowserSessionRefInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserSessionOut]:
    runtime = get_browser_runtime()
    repo = BrowserRepository(ctx.session)
    session_row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    page_refs = session_row.page_refs_json or []
    fallback_page_ref = page_refs[0] if page_refs else f"{inp.session_ref}:page-1"
    try:
        stopped = await runtime.stop_session(session_ref=inp.session_ref)
        if not stopped:
            env = repo.mark_session_stale(
                project_id=inp.project_id,
                session_ref=inp.session_ref,
            )
            repo.record_receipt(
                project_id=inp.project_id,
                profile_id=env.data.profile_id,
                session_id=env.data.id,
                artifact_id=None,
                session_ref=inp.session_ref,
                page_ref=fallback_page_ref,
                operation="browser.session.stop",
                method="stop",
                side_effect_class="session",
                target_url=_redact_url(env.data.current_url),
                target_origin=_origin(env.data.current_url),
                status="stale",
                input_summary_json={"session_ref": inp.session_ref},
                result_json={
                    "session_ref": inp.session_ref,
                    "status": "stale",
                    "repair": "Start a fresh session with browser.session.start.",
                },
            )
            return WriteEnvelope(data=env.data, run_id=ctx.run_id, project_id=env.project_id)
    except Exception as exc:
        repo.record_receipt(
            project_id=inp.project_id,
            profile_id=profile.id,
            session_id=session_row.id,
            artifact_id=None,
            session_ref=inp.session_ref,
            page_ref=fallback_page_ref,
            operation="browser.session.stop",
            method="stop",
            side_effect_class="session",
            target_url=_redact_url(session_row.current_url),
            target_origin=_origin(session_row.current_url),
            status="failed",
            input_summary_json={"session_ref": inp.session_ref},
            result_json=_failure_summary(
                method="stop",
                page_ref=fallback_page_ref,
                url=session_row.current_url,
                exc=exc,
            ),
            error=_error_summary(exc),
        )
        _raise_safe_browser_error(
            operation="browser.session.stop",
            method="stop",
            exc=exc,
        )
    env = repo.stop_session(project_id=inp.project_id, session_ref=inp.session_ref)
    repo.record_receipt(
        project_id=inp.project_id,
        profile_id=env.data.profile_id,
        session_id=env.data.id,
        artifact_id=None,
        session_ref=inp.session_ref,
        page_ref=fallback_page_ref,
        operation="browser.session.stop",
        method="stop",
        side_effect_class="session",
        target_url=_redact_url(env.data.current_url),
        target_origin=_origin(env.data.current_url),
        status="ok",
        input_summary_json={"session_ref": inp.session_ref},
        result_json={"session_ref": inp.session_ref, "status": "stopped"},
    )
    return WriteEnvelope(data=env.data, run_id=ctx.run_id, project_id=env.project_id)


async def _browser_session_list(
    inp: BrowserSessionListInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> Page[BrowserSessionOut]:
    repo = BrowserRepository(ctx.session)
    repo.reconcile_running_sessions(
        project_id=inp.project_id,
        live_session_refs=get_browser_runtime().status().live_session_refs,
    )
    return repo.list_sessions(project_id=inp.project_id)


async def _browser_session_status(
    inp: BrowserSessionRefInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> BrowserSessionOut:
    repo = BrowserRepository(ctx.session)
    repo.reconcile_running_sessions(
        project_id=inp.project_id,
        live_session_refs=get_browser_runtime().status().live_session_refs,
    )
    session, profile = repo.get_session(
        project_id=inp.project_id,
        session_ref=inp.session_ref,
    )
    return repo.session_out(session, profile)


async def _browser_page_call(
    inp: BrowserPageCallInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserCallOut]:
    repo = BrowserRepository(ctx.session)
    session_row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    spec = get_method_spec(inp.method)
    side_effect_class = spec.side_effect if spec is not None else "dynamic"
    input_summary = _call_summary(inp.method, inp.arguments, inp.args, inp.kwargs)
    page_refs = session_row.page_refs_json or []
    fallback_page_ref = inp.page_ref or (page_refs[0] if page_refs else f"{inp.session_ref}:page-1")
    try:
        result = await get_browser_runtime().page_call(
            session_ref=inp.session_ref,
            spec=spec,
            method=inp.method,
            arguments=dict(inp.arguments),
            raw_args=inp.args,
            raw_kwargs=inp.kwargs,
            page_ref=inp.page_ref,
        )
    except Exception as exc:
        if _is_not_live_session_error(exc):
            repo.mark_session_stale(project_id=inp.project_id, session_ref=inp.session_ref)
        repo.record_receipt(
            project_id=inp.project_id,
            profile_id=profile.id,
            session_id=session_row.id,
            artifact_id=None,
            session_ref=inp.session_ref,
            page_ref=fallback_page_ref,
            operation="browser.page.call",
            method=inp.method,
            side_effect_class=side_effect_class,
            target_url=_redact_url(session_row.current_url),
            target_origin=_origin(session_row.current_url),
            status="failed",
            input_summary_json=input_summary,
            result_json=_failure_summary(
                method=inp.method,
                page_ref=fallback_page_ref,
                url=session_row.current_url,
                exc=exc,
            ),
            error=_error_summary(exc),
        )
        _raise_safe_browser_error(
            operation="browser.page.call",
            method=inp.method,
            exc=exc,
        )
    session_out = repo.update_session_url(
        project_id=inp.project_id,
        session_ref=inp.session_ref,
        current_url=result.url,
        page_refs=result.page_refs,
    )
    receipt = repo.record_receipt(
        project_id=inp.project_id,
        profile_id=profile.id,
        session_id=session_row.id,
        artifact_id=None,
        session_ref=inp.session_ref,
        page_ref=result.page_ref,
        operation="browser.page.call",
        method=inp.method,
        side_effect_class=side_effect_class,
        target_url=_redact_url(result.url),
        target_origin=_origin(result.url),
        status=result.status,
        input_summary_json=input_summary,
        result_json=_result_summary(result),
    )
    return WriteEnvelope(
        data=BrowserCallOut(receipt=receipt, session=session_out, result=result.to_public_result()),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def _browser_context_call(
    inp: BrowserContextCallInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserCallOut]:
    repo = BrowserRepository(ctx.session)
    session_row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    input_summary = _call_summary(inp.method, inp.arguments, inp.args, inp.kwargs)
    page_refs = session_row.page_refs_json or []
    fallback_page_ref = page_refs[0] if page_refs else f"{inp.session_ref}:page-1"
    try:
        result = await get_browser_runtime().context_call(
            session_ref=inp.session_ref,
            method=inp.method,
            arguments=dict(inp.arguments),
            raw_args=inp.args,
            raw_kwargs=inp.kwargs,
        )
    except Exception as exc:
        if _is_not_live_session_error(exc):
            repo.mark_session_stale(project_id=inp.project_id, session_ref=inp.session_ref)
        repo.record_receipt(
            project_id=inp.project_id,
            profile_id=profile.id,
            session_id=session_row.id,
            artifact_id=None,
            session_ref=inp.session_ref,
            page_ref=fallback_page_ref,
            operation="browser.context.call",
            method=inp.method,
            side_effect_class="context",
            target_url=_redact_url(session_row.current_url),
            target_origin=_origin(session_row.current_url),
            status="failed",
            input_summary_json=input_summary,
            result_json=_failure_summary(
                method=inp.method,
                page_ref=fallback_page_ref,
                url=session_row.current_url,
                exc=exc,
            ),
            error=_error_summary(exc),
        )
        _raise_safe_browser_error(
            operation="browser.context.call",
            method=inp.method,
            exc=exc,
        )
    session_out = repo.update_session_url(
        project_id=inp.project_id,
        session_ref=inp.session_ref,
        current_url=result.url,
        page_refs=result.page_refs,
    )
    receipt = repo.record_receipt(
        project_id=inp.project_id,
        profile_id=profile.id,
        session_id=session_row.id,
        artifact_id=None,
        session_ref=inp.session_ref,
        page_ref=result.page_ref,
        operation="browser.context.call",
        method=inp.method,
        side_effect_class="context",
        target_url=_redact_url(result.url),
        target_origin=_origin(result.url),
        status=result.status,
        input_summary_json=input_summary,
        result_json=_result_summary(result),
    )
    return WriteEnvelope(
        data=BrowserCallOut(receipt=receipt, session=session_out, result=result.to_public_result()),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def _browser_handle_call(
    inp: BrowserHandleCallInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserCallOut]:
    repo = BrowserRepository(ctx.session)
    session_row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    input_summary = _call_summary(inp.method, inp.arguments, inp.args, inp.kwargs)
    input_summary["handle_ref"] = inp.handle_ref
    page_refs = session_row.page_refs_json or []
    fallback_page_ref = page_refs[0] if page_refs else f"{inp.session_ref}:page-1"
    try:
        result = await get_browser_runtime().handle_call(
            session_ref=inp.session_ref,
            handle_ref=inp.handle_ref,
            method=inp.method,
            arguments=dict(inp.arguments),
            raw_args=inp.args,
            raw_kwargs=inp.kwargs,
        )
    except Exception as exc:
        if _is_not_live_session_error(exc):
            repo.mark_session_stale(project_id=inp.project_id, session_ref=inp.session_ref)
        repo.record_receipt(
            project_id=inp.project_id,
            profile_id=profile.id,
            session_id=session_row.id,
            artifact_id=None,
            session_ref=inp.session_ref,
            page_ref=fallback_page_ref,
            operation="browser.handle.call",
            method=inp.method,
            side_effect_class="handle",
            target_url=_redact_url(session_row.current_url),
            target_origin=_origin(session_row.current_url),
            status="failed",
            input_summary_json=input_summary,
            result_json=_failure_summary(
                method=inp.method,
                page_ref=fallback_page_ref,
                url=session_row.current_url,
                exc=exc,
            ),
            error=_error_summary(exc),
        )
        _raise_safe_browser_error(
            operation="browser.handle.call",
            method=inp.method,
            exc=exc,
        )
    session_out = repo.update_session_url(
        project_id=inp.project_id,
        session_ref=inp.session_ref,
        current_url=result.url,
        page_refs=result.page_refs,
    )
    receipt = repo.record_receipt(
        project_id=inp.project_id,
        profile_id=profile.id,
        session_id=session_row.id,
        artifact_id=None,
        session_ref=inp.session_ref,
        page_ref=result.page_ref,
        operation="browser.handle.call",
        method=inp.method,
        side_effect_class="handle",
        target_url=_redact_url(result.url),
        target_origin=_origin(result.url),
        status=result.status,
        input_summary_json=input_summary,
        result_json=_result_summary(result),
    )
    return WriteEnvelope(
        data=BrowserCallOut(receipt=receipt, session=session_out, result=result.to_public_result()),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def _browser_script_run(
    inp: BrowserScriptRunInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserCallOut]:
    arguments: dict[str, Any] = {"script": inp.script}
    if inp.arg is not None:
        arguments["arg"] = inp.arg
    return await _browser_page_call(
        BrowserPageCallInput(
            project_id=inp.project_id,
            session_ref=inp.session_ref,
            page_ref=inp.page_ref,
            method="evaluate",
            arguments=arguments,
            run_token=inp.run_token,
            idempotency_key=inp.idempotency_key,
            response_mode=inp.response_mode,
        ),
        ctx,
        _emit,
    )


async def _browser_script_inject(
    inp: BrowserScriptInjectInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserCallOut]:
    return await _browser_page_call(
        BrowserPageCallInput(
            project_id=inp.project_id,
            session_ref=inp.session_ref,
            page_ref=inp.page_ref,
            method="add_init_script",
            arguments={"script": inp.script},
            run_token=inp.run_token,
            idempotency_key=inp.idempotency_key,
            response_mode=inp.response_mode,
        ),
        ctx,
        _emit,
    )


async def _browser_page_snapshot(
    inp: BrowserPageSnapshotInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserCallOut]:
    repo = BrowserRepository(ctx.session)
    session_row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    page_refs = session_row.page_refs_json or []
    fallback_page_ref = inp.page_ref or (page_refs[0] if page_refs else f"{inp.session_ref}:page-1")
    input_summary = {"session_ref": inp.session_ref}
    if inp.page_ref is not None:
        input_summary["page_ref"] = inp.page_ref
    try:
        result = await get_browser_runtime().snapshot(
            session_ref=inp.session_ref,
            page_ref=inp.page_ref,
        )
    except Exception as exc:
        if _is_not_live_session_error(exc):
            repo.mark_session_stale(project_id=inp.project_id, session_ref=inp.session_ref)
        repo.record_receipt(
            project_id=inp.project_id,
            profile_id=profile.id,
            session_id=session_row.id,
            artifact_id=None,
            session_ref=inp.session_ref,
            page_ref=fallback_page_ref,
            operation="browser.page.snapshot",
            method="snapshot",
            side_effect_class="read",
            target_url=_redact_url(session_row.current_url),
            target_origin=_origin(session_row.current_url),
            status="failed",
            input_summary_json=input_summary,
            result_json=_failure_summary(
                method="snapshot",
                page_ref=fallback_page_ref,
                url=session_row.current_url,
                exc=exc,
            ),
            error=_error_summary(exc),
        )
        _raise_safe_browser_error(
            operation="browser.page.snapshot",
            method="snapshot",
            exc=exc,
        )
    session_out = repo.update_session_url(
        project_id=inp.project_id,
        session_ref=inp.session_ref,
        current_url=result.url,
        page_refs=result.page_refs,
    )
    receipt = repo.record_receipt(
        project_id=inp.project_id,
        profile_id=profile.id,
        session_id=session_row.id,
        artifact_id=None,
        session_ref=inp.session_ref,
        page_ref=result.page_ref,
        operation="browser.page.snapshot",
        method="snapshot",
        side_effect_class="read",
        target_url=_redact_url(result.url),
        target_origin=_origin(result.url),
        status=result.status,
        input_summary_json=input_summary,
        result_json=_result_summary(result),
    )
    return WriteEnvelope(
        data=BrowserCallOut(receipt=receipt, session=session_out, result=result.to_public_result()),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def _browser_page_screenshot(
    inp: BrowserScreenshotInput,
    ctx: MCPContext,
    _emit: ProgressEmitter,
) -> WriteEnvelope[BrowserCallOut]:
    settings = _settings(ctx)
    repo = BrowserRepository(ctx.session)
    session_row, profile = repo.get_session(project_id=inp.project_id, session_ref=inp.session_ref)
    filename_base = safe_browser_key(
        inp.name or f"{_session_key_from_ref(inp.session_ref)}-{_now_slug()}"
    )
    filename = f"{filename_base}-{uuid4().hex[:8]}.png"
    relative = Path("browser") / f"project-{inp.project_id}" / filename
    path = Path(settings.generated_assets_dir) / relative
    page_refs = session_row.page_refs_json or []
    fallback_page_ref = page_refs[0] if page_refs else f"{inp.session_ref}:page-1"
    try:
        result = await get_browser_runtime().screenshot(
            session_ref=inp.session_ref,
            path=path,
            full_page=inp.full_page,
            page_ref=inp.page_ref,
        )
    except Exception as exc:
        if _is_not_live_session_error(exc):
            repo.mark_session_stale(project_id=inp.project_id, session_ref=inp.session_ref)
        repo.record_receipt(
            project_id=inp.project_id,
            profile_id=profile.id,
            session_id=session_row.id,
            artifact_id=None,
            session_ref=inp.session_ref,
            page_ref=fallback_page_ref,
            operation="browser.page.screenshot",
            method="screenshot",
            side_effect_class="artifact",
            target_url=_redact_url(session_row.current_url),
            target_origin=_origin(session_row.current_url),
            status="failed",
            input_summary_json={"session_ref": inp.session_ref, "full_page": inp.full_page},
            result_json=_failure_summary(
                method="screenshot",
                page_ref=fallback_page_ref,
                url=session_row.current_url,
                exc=exc,
            ),
            error=_error_summary(exc),
        )
        _raise_safe_browser_error(
            operation="browser.page.screenshot",
            method="screenshot",
            exc=exc,
        )
    uri = f"/generated-assets/{relative.as_posix()}"
    artifact = repo.create_artifact(
        project_id=inp.project_id,
        uri=uri,
        name=filename,
        mime_type=result.screenshot_mime_type or "image/png",
        size_bytes=path.stat().st_size if path.exists() else None,
        metadata_json={
            "session_ref": inp.session_ref,
            "page_ref": result.page_ref,
            "full_page": inp.full_page,
            "url": _redact_url(result.url),
        },
        provenance_json={"operation": "browser.page.screenshot", "run_id": ctx.run_id},
    )
    session_out = repo.update_session_url(
        project_id=inp.project_id,
        session_ref=inp.session_ref,
        current_url=result.url,
        page_refs=result.page_refs,
    )
    receipt = repo.record_receipt(
        project_id=inp.project_id,
        profile_id=profile.id,
        session_id=session_row.id,
        artifact_id=artifact.id,
        session_ref=inp.session_ref,
        page_ref=result.page_ref,
        operation="browser.page.screenshot",
        method="screenshot",
        side_effect_class="artifact",
        target_url=_redact_url(result.url),
        target_origin=_origin(result.url),
        status=result.status,
        input_summary_json={"session_ref": inp.session_ref, "full_page": inp.full_page},
        result_json={**_result_summary(result), "artifact_ref": uri, "artifact_id": artifact.id},
    )
    return WriteEnvelope(
        data=BrowserCallOut(
            receipt=receipt,
            session=session_out,
            artifact={
                "id": artifact.id,
                "uri": uri,
                "name": artifact.name,
                "mime_type": artifact.mime_type,
                "size_bytes": artifact.size_bytes,
            },
            result={**result.to_public_result(), "artifact_ref": uri, "artifact_id": artifact.id},
        ),
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


def operation_specs():
    direct_browser = "direct-browser-control-or-run-plan-step-grant"
    raw_browser_output = "raw-browser-output"
    return [
        operation_spec(
            name="browser.runtime.status",
            summary="Inspect local Playwright browser runtime readiness.",
            input_model=BrowserRuntimeStatusInput,
            output_model=BrowserRuntimeStatusOut,
            handler=_browser_runtime_status,
            purpose=(
                "Use this before browser automation to confirm the package and "
                "browser binary are installed."
            ),
            returns=(
                "Playwright package status, Chromium install status, live session refs, "
                "and repair guidance.",
            ),
            mutating=False,
            grant_policy="direct-read",
            category="browser",
        ),
        operation_spec(
            name="browser.method.manifest",
            summary="Read the browser method parity manifest.",
            input_model=BrowserRuntimeStatusInput,
            output_model=BrowserMethodManifestOut,
            handler=_browser_method_manifest,
            purpose=(
                "Use this to understand the StackOS browser parity model and named "
                "convenience calls."
            ),
            mutating=False,
            grant_policy="direct-read",
            category="browser",
        ),
        operation_spec(
            name="browser.profile.create",
            summary="Create or update a daemon-owned browser profile.",
            input_model=BrowserProfileCreateInput,
            output_model=WriteEnvelope[BrowserProfileOut],
            handler=_browser_profile_create,
            purpose="Use this to prepare a persistent browser profile before starting a session.",
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.profile.list",
            summary="List project browser profiles.",
            input_model=BrowserProfileListInput,
            output_model=Page[BrowserProfileOut],
            handler=_browser_profile_list,
            purpose="Use this to discover existing browser profiles for a project.",
            mutating=False,
            grant_policy="direct-read",
            category="browser",
        ),
        operation_spec(
            name="browser.session.start",
            summary="Start a visible or headless Playwright browser session.",
            input_model=BrowserSessionStartInput,
            output_model=WriteEnvelope[BrowserSessionOut],
            handler=_browser_session_start,
            purpose=(
                "Use this to open a live persistent browser session. Defaults to visible mode "
                "so the operator can log in or observe platform posting."
            ),
            examples=(OperationExample(title="Open visible session", arguments={"project_id": 1}),),
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.session.stop",
            summary="Stop a live browser session.",
            input_model=BrowserSessionRefInput,
            output_model=WriteEnvelope[BrowserSessionOut],
            handler=_browser_session_stop,
            purpose="Use this to close a daemon-owned browser session and mark it stopped.",
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.session.list",
            summary="List project browser sessions.",
            input_model=BrowserSessionListInput,
            output_model=Page[BrowserSessionOut],
            handler=_browser_session_list,
            purpose="Use this to find browser session refs for follow-up page calls.",
            mutating=False,
            grant_policy="direct-read",
            category="browser",
        ),
        operation_spec(
            name="browser.session.status",
            summary="Read one browser session record.",
            input_model=BrowserSessionRefInput,
            output_model=BrowserSessionOut,
            handler=_browser_session_status,
            purpose="Use this to inspect a browser session ref before follow-up calls.",
            mutating=False,
            grant_policy="direct-read",
            category="browser",
        ),
        operation_spec(
            name="browser.page.call",
            summary="Call any public method on the active browser page.",
            input_model=BrowserPageCallInput,
            output_model=WriteEnvelope[BrowserCallOut],
            handler=_browser_page_call,
            purpose=(
                "Use this for full-control page automation. Pass the public page method name "
                "plus raw args/kwargs or named arguments; StackOS records a receipt."
            ),
            examples=(
                OperationExample(
                    title="Navigate",
                    arguments={
                        "project_id": 1,
                        "session_ref": "browser-session:project-1:default:default",
                        "method": "goto",
                        "arguments": {"url": "https://example.com"},
                    },
                ),
            ),
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.context.call",
            summary="Call any public method on the active browser context.",
            input_model=BrowserContextCallInput,
            output_model=WriteEnvelope[BrowserCallOut],
            handler=_browser_context_call,
            purpose=(
                "Use this for context-level browser control such as cookies, permissions, "
                "storage state, pages, routing, and downloads."
            ),
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.handle.call",
            summary="Call any public method or property on a live browser object handle.",
            input_model=BrowserHandleCallInput,
            output_model=WriteEnvelope[BrowserCallOut],
            handler=_browser_handle_call,
            purpose=(
                "Use this when a public page/context call returns an object such as a "
                "locator, download, popup, response, or other Playwright handle. Pass "
                "the returned handle_ref plus raw args/kwargs."
            ),
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.script.run",
            summary="Run arbitrary JavaScript in the active page.",
            input_model=BrowserScriptRunInput,
            output_model=WriteEnvelope[BrowserCallOut],
            handler=_browser_script_run,
            purpose="Use this to run unrestricted page JavaScript and get the returned value.",
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.script.inject",
            summary="Inject arbitrary JavaScript into future page loads.",
            input_model=BrowserScriptInjectInput,
            output_model=WriteEnvelope[BrowserCallOut],
            handler=_browser_script_inject,
            purpose="Use this to add an unrestricted init script to the active page context.",
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.page.snapshot",
            summary="Capture a text snapshot of the active page.",
            input_model=BrowserPageSnapshotInput,
            output_model=WriteEnvelope[BrowserCallOut],
            handler=_browser_page_snapshot,
            purpose="Use this to inspect the visible page state without a screenshot.",
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
        operation_spec(
            name="browser.page.screenshot",
            summary="Capture a PNG screenshot artifact from the active page.",
            input_model=BrowserScreenshotInput,
            output_model=WriteEnvelope[BrowserCallOut],
            handler=_browser_page_screenshot,
            purpose=(
                "Use this to create a generated-assets screenshot artifact for review or handoff."
            ),
            mutating=True,
            grant_policy=direct_browser,
            secret_policy=raw_browser_output,
            category="browser",
            response_policy=_BROWSER_SIDE_EFFECT_POLICY,
        ),
    ]


__all__ = [
    "BrowserContextCallInput",
    "BrowserHandleCallInput",
    "BrowserPageCallInput",
    "BrowserPageSnapshotInput",
    "BrowserProfileCreateInput",
    "BrowserProfileListInput",
    "BrowserRuntimeStatusInput",
    "BrowserScreenshotInput",
    "BrowserScriptInjectInput",
    "BrowserScriptRunInput",
    "BrowserSessionListInput",
    "BrowserSessionRefInput",
    "BrowserSessionStartInput",
    "operation_specs",
]
