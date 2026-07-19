"""MCP call dispatch, scope, grants, idempotency, and error mapping."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast

from mcp.server.lowlevel.server import request_ctx
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete
from sqlmodel import Session

from stackos.db.models import IdempotencyKey
from stackos.logging import get_logger
from stackos.mcp.context import MCPContext, bind_context, build_context
from stackos.mcp.errors import (
    JSONRPC_INTERNAL,
    JSONRPC_VALIDATION,
    ToolNotGrantedError,
    map_repository_error,
)
from stackos.mcp.permissions import check_call_grant
from stackos.mcp.streaming import ProgressEmitter
from stackos.mcp.tool_registry import ToolRegistry, ToolSpec, _result_to_json
from stackos.repositories.base import ConflictError, RepositoryError
from stackos.repositories.runs import IdempotencyKeyRepository
from stackos.validation_errors import safe_validation_errors

_log = get_logger(__name__)


def _find_mismatched_project_id(value: Any, expected: int) -> int | None:
    """Find cross-project ownership while treating ``*_json`` as opaque payloads."""
    if isinstance(value, dict):
        raw = value.get("project_id")
        if isinstance(raw, int) and raw != expected:
            return raw
        for key, child in value.items():
            if key == "project_id" or key.endswith("_json"):
                continue
            found = _find_mismatched_project_id(child, expected)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_mismatched_project_id(child, expected)
            if found is not None:
                return found
    return None


@dataclass
class _CallResult:
    payload: dict[str, Any]
    is_error: bool = False


class MCPDispatcher:
    """Dispatch one registered MCP tool through scope, grants, and audit controls."""

    def __init__(
        self,
        registry: ToolRegistry,
        engine_resolver: Callable[[], Any],
        settings_resolver: Callable[[], Any] | None = None,
        operation_registry: Any | None = None,
    ) -> None:
        self._registry = registry
        self._engine_resolver = engine_resolver
        self._settings_resolver = settings_resolver
        self._operation_registry = operation_registry

    async def dispatch(self, name: str, arguments: dict[str, Any] | None) -> _CallResult:
        try:
            spec = self._registry.get(name)
        except KeyError:
            _log.warning("mcp.dispatch.unknown_tool", tool=name)
            return _CallResult(
                payload={
                    "error": {
                        "code": -32601,
                        "message": "MethodNotFoundError",
                        "data": {"detail": f"tool {name!r} is not registered"},
                    }
                },
                is_error=True,
            )

        engine = self._engine_resolver()
        with Session(engine) as session:
            if spec.operation_name is not None:
                return await self._dispatch_operation(spec, arguments, session)
            ctx = build_context(arguments, session)
            if self._settings_resolver is not None:
                with suppress(RuntimeError):
                    ctx.extras["settings"] = self._settings_resolver()
            with bind_context(ctx):
                emitter = self._build_emitter()
                try:
                    parsed = spec.input_model.model_validate(arguments or {})
                except PydanticValidationError as exc:
                    return self._validation_failure(exc)
                scope_error = self._scope_input_error(spec, ctx)
                if scope_error is not None:
                    return scope_error
                try:
                    check_call_grant(spec.name, ctx, parsed)
                except RepositoryError as exc:
                    return self._repo_error(exc)
                if (
                    not spec.read_only
                    and ctx.idempotency_key is not None
                    and ctx.project_id is not None
                ):
                    cached = self._idempotency_check(spec, ctx)
                    if cached is not None:
                        return cached
                try:
                    started = time.perf_counter()
                    result = await spec.handler(parsed, ctx, emitter)
                    duration_ms = int((time.perf_counter() - started) * 1000)
                except RepositoryError as exc:
                    if (
                        not spec.read_only
                        and ctx.idempotency_key is not None
                        and ctx.project_id is not None
                    ):
                        self._idempotency_forget(spec, ctx)
                    return self._repo_error(exc)
                except Exception as exc:
                    if (
                        not spec.read_only
                        and ctx.idempotency_key is not None
                        and ctx.project_id is not None
                    ):
                        self._idempotency_forget(spec, ctx)
                    _log.exception("mcp.dispatch.unexpected_error", tool=name, **ctx.to_log_dict())
                    return _CallResult(
                        payload={
                            "error": {
                                "code": JSONRPC_INTERNAL,
                                "message": "RepositoryError",
                                "data": {"detail": f"{type(exc).__name__}: {exc}"},
                            }
                        },
                        is_error=True,
                    )

                payload = _result_to_json(result)
                scope_error = self._scope_output_error(spec, ctx, payload)
                if scope_error is not None:
                    if (
                        not spec.read_only
                        and ctx.idempotency_key is not None
                        and ctx.project_id is not None
                    ):
                        self._idempotency_forget(spec, ctx)
                    return scope_error
                if (
                    not spec.read_only
                    and ctx.idempotency_key is not None
                    and ctx.project_id is not None
                ):
                    self._idempotency_record(spec, ctx, payload)

                _log.info(
                    "mcp.dispatch.ok",
                    tool=name,
                    duration_ms=duration_ms,
                    streaming=spec.streaming,
                    **ctx.to_log_dict(),
                )
                return _CallResult(payload=payload)

    async def _dispatch_operation(
        self,
        spec: ToolSpec,
        arguments: dict[str, Any] | None,
        session: Session,
    ) -> _CallResult:
        if self._operation_registry is None:
            return _CallResult(
                payload={
                    "error": {
                        "code": JSONRPC_INTERNAL,
                        "message": "RepositoryError",
                        "data": {"detail": f"operation registry missing for tool {spec.name!r}"},
                    }
                },
                is_error=True,
            )
        from stackos.operations.dispatcher import OperationDispatcher

        settings = None
        if self._settings_resolver is not None:
            with suppress(RuntimeError):
                settings = self._settings_resolver()
        operation_name = spec.operation_name
        assert operation_name is not None
        try:
            result = await OperationDispatcher(self._operation_registry).dispatch(
                operation_name,
                arguments,
                session=session,
                surface="mcp",
                settings=settings,
            )
        except RepositoryError as exc:
            return self._repo_error(exc)
        return _CallResult(payload=result.payload)

    def _build_emitter(self) -> ProgressEmitter:
        try:
            req_ctx = request_ctx.get()
        except LookupError:
            return ProgressEmitter(None, None)
        token: str | int | None = None
        if req_ctx.meta is not None:
            token = req_ctx.meta.progressToken
        return ProgressEmitter(req_ctx.session, token, request_id=req_ctx.request_id)

    def _validation_failure(self, exc: PydanticValidationError) -> _CallResult:
        return _CallResult(
            payload={
                "error": {
                    "code": JSONRPC_VALIDATION,
                    "message": "ValidationError",
                    "data": {
                        "detail": "input validation failed",
                        "errors": safe_validation_errors(exc.errors()),
                    },
                }
            },
            is_error=True,
        )

    def _repo_error(self, exc: RepositoryError) -> _CallResult:
        code, message, data = map_repository_error(exc)
        return _CallResult(
            payload={"error": {"code": code, "message": message, "data": data}},
            is_error=True,
        )

    def _scope_input_error(self, spec: ToolSpec, ctx: MCPContext) -> _CallResult | None:
        if ctx.run is None or ctx.project_id is None or ctx.run.project_id is None:
            return None
        if ctx.project_id == ctx.run.project_id:
            return None
        return self._repo_error(
            ToolNotGrantedError(
                "run_token is not scoped to this project",
                data={
                    "tool": spec.name,
                    "run_id": ctx.run_id,
                    "run_project_id": ctx.run.project_id,
                    "requested_project_id": ctx.project_id,
                },
            )
        )

    def _scope_output_error(
        self,
        spec: ToolSpec,
        ctx: MCPContext,
        payload: dict[str, Any],
    ) -> _CallResult | None:
        if ctx.run is None or ctx.run.project_id is None:
            return None

        expected = ctx.run.project_id
        seen = _find_mismatched_project_id(payload, expected)
        if seen is None:
            return None
        return self._repo_error(
            ToolNotGrantedError(
                "run_token cannot access data from another project",
                data={
                    "tool": spec.name,
                    "run_id": ctx.run_id,
                    "run_project_id": expected,
                    "result_project_id": seen,
                },
            )
        )

    def _idempotency_check(
        self,
        spec: ToolSpec,
        ctx: MCPContext,
    ) -> _CallResult | None:
        repo = IdempotencyKeyRepository(ctx.session)
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        try:
            repo.check_or_create(
                project_id=ctx.project_id,
                tool_name=spec.name,
                idempotency_key=ctx.idempotency_key,
                run_id=ctx.run_id,
            )
        except RepositoryError as exc:
            from stackos.repositories.base import IdempotencyReplayError

            if isinstance(exc, IdempotencyReplayError):
                cached = exc.data.get("response_json")
                if cached is not None:
                    log_extra = ctx.to_log_dict()
                    log_extra["replayed_run_id"] = exc.data.get("run_id")
                    _log.info("mcp.idempotency.replay", tool=spec.name, **log_extra)
                    return _CallResult(payload=cached)
                return self._repo_error(
                    ConflictError(
                        "idempotency key is already in-flight",
                        data={
                            "idempotency_key": ctx.idempotency_key,
                            "run_id": exc.data.get("run_id"),
                            "tool_name": spec.name,
                            "project_id": ctx.project_id,
                            "replay": True,
                            "in_flight": True,
                        },
                    )
                )
            return self._repo_error(exc)
        return None

    def _idempotency_forget(self, spec: ToolSpec, ctx: MCPContext) -> None:
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        ctx.session.exec(
            delete(IdempotencyKey).where(
                cast(Any, IdempotencyKey.project_id) == ctx.project_id,
                cast(Any, IdempotencyKey.tool_name) == spec.name,
                cast(Any, IdempotencyKey.idempotency_key) == ctx.idempotency_key,
            )
        )
        ctx.session.commit()

    def _idempotency_record(
        self,
        spec: ToolSpec,
        ctx: MCPContext,
        response_json: dict[str, Any],
    ) -> None:
        repo = IdempotencyKeyRepository(ctx.session)
        assert ctx.project_id is not None
        assert ctx.idempotency_key is not None
        try:
            repo.update_response(
                project_id=ctx.project_id,
                tool_name=spec.name,
                idempotency_key=ctx.idempotency_key,
                response_json=response_json,
            )
        except RepositoryError as exc:  # pragma: no cover - check/create row disappeared
            _log.warning(
                "mcp.idempotency.record_failed",
                tool=spec.name,
                detail=exc.detail,
                **ctx.to_log_dict(),
            )


__all__ = ["MCPDispatcher"]
