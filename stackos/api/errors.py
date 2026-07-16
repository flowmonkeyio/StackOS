"""Repository-error → HTTP mapping plus the unified ``ErrorResponse`` shape.

Per PLAN.md L740-L763 we map every typed repository error to a JSON-RPC
code + HTTP status. The wire body is the same across REST and MCP so
clients can write one error handler.

Idempotency is special: ``IdempotencyReplayError`` is a flow-control
signal, not a failure. The handler short-circuits with HTTP 200 and the
cached ``response_json`` from the original call (per PLAN.md L724-L727).
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from stackos.logging import get_logger
from stackos.repositories.base import (
    BudgetExceededError,
    ConflictError,
    IdempotencyReplayError,
    NotFoundError,
    RepositoryError,
    ValidationError,
)
from stackos.validation_errors import safe_validation_errors

_log = get_logger(__name__)

# Contextvar set by ``RequestIdMiddleware``; the structlog processor reads
# it directly. Routes can also surface it on the response via ``X-Request-Id``.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class ErrorResponse(BaseModel):
    """Uniform error envelope; same shape for REST + MCP per PLAN.md L740-L763."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "detail": "project 99 not found",
                "code": -32004,
                "retryable": False,
                "data": {"id_or_slug": 99},
                "retry_after": None,
                "hint": None,
            }
        }
    )

    detail: str
    code: int
    retryable: bool = False
    data: dict[str, Any] | None = None
    retry_after: int | None = None
    hint: str | None = None


# ---------------------------------------------------------------------------
# Request-id contextvar middleware.
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Stamp every request/response with a UUID4 ``X-Request-Id``.

    Reads an inbound ``X-Request-Id`` header if present (so an upstream
    proxy or test harness can inject a known id) and falls back to a
    fresh UUID4. The value is stored in a ``contextvars.ContextVar`` so
    structlog's ``_inject_contextvars`` processor binds it to every log
    line emitted under the request without explicit threading.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Bind a request-id and propagate it back to the client."""
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        return response


# ---------------------------------------------------------------------------
# Exception handlers.
# ---------------------------------------------------------------------------


def _payload(
    err: RepositoryError,
    *,
    hint: str | None = None,
    retry_after: int | None = None,
) -> dict[str, Any]:
    """Build the JSON body for an ``ErrorResponse``."""
    return ErrorResponse(
        detail=err.detail,
        code=err.code,
        retryable=err.retryable,
        data=dict(err.data) or None,
        hint=hint,
        retry_after=retry_after,
    ).model_dump(mode="json")


async def repository_error_handler(request: Request, exc: Exception) -> Response:
    """Catch-all for ``RepositoryError`` — branches to the typed handlers.

    Registered as a single handler so subclasses route through it without
    needing per-class registration; we dispatch on type below to keep the
    code paths visible side by side.
    """
    if isinstance(exc, IdempotencyReplayError):
        return await idempotency_replay_handler(request, exc)
    if isinstance(exc, NotFoundError):
        return await not_found_handler(request, exc)
    if isinstance(exc, ConflictError):
        return await conflict_handler(request, exc)
    if isinstance(exc, ValidationError):
        return await validation_handler(request, exc)
    if isinstance(exc, BudgetExceededError):
        return await budget_handler(request, exc)
    # Generic RepositoryError — internal.
    if isinstance(exc, RepositoryError):
        _log.error(
            "repository.error.internal",
            detail=exc.detail,
            data=exc.data,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=_payload(exc),
        )
    raise exc  # pragma: no cover — re-raise unknown types so FastAPI's default kicks in


async def not_found_handler(request: Request, exc: Exception) -> Response:
    """404 for ``NotFoundError`` (-32004)."""
    assert isinstance(exc, NotFoundError)
    _log.info("repository.not_found", detail=exc.detail, path=request.url.path)
    return JSONResponse(status_code=404, content=_payload(exc))


async def conflict_handler(request: Request, exc: Exception) -> Response:
    """409 (or 412 on etag mismatch) for ``ConflictError`` (-32008).

    Convention: if the error data carries an ``expected_etag`` field we
    surface 412 Precondition Failed instead of 409 — that's the dedicated
    HTTP status for stale optimistic-concurrency tokens (PLAN.md L803-L809
    surfaces the same data via ``If-Match``).
    """
    assert isinstance(exc, ConflictError)
    is_etag_mismatch = "expected_etag" in exc.data and "current_etag" in exc.data
    status_code = 412 if is_etag_mismatch else 409
    _log.info(
        "repository.conflict",
        detail=exc.detail,
        etag_mismatch=is_etag_mismatch,
        path=request.url.path,
    )
    return JSONResponse(status_code=status_code, content=_payload(exc))


async def validation_handler(request: Request, exc: Exception) -> Response:
    """422 for ``ValidationError`` (-32602)."""
    assert isinstance(exc, ValidationError)
    _log.info("repository.validation", detail=exc.detail, path=request.url.path)
    return JSONResponse(status_code=422, content=_payload(exc))


async def budget_handler(request: Request, exc: Exception) -> Response:
    """402 Payment Required for ``BudgetExceededError`` (-32012).

    The ``data`` dict carries ``monthly_budget_usd`` / ``current_month_spend``
    so the operator UI can render the cap diff. ``retry_after`` is left
    None — operator action is required (raise the cap or wait for month
    rollover); the daemon does not automatically retry budget breaches.
    """
    assert isinstance(exc, BudgetExceededError)
    _log.warning(
        "repository.budget_exceeded",
        detail=exc.detail,
        data=exc.data,
        path=request.url.path,
    )
    return JSONResponse(status_code=402, content=_payload(exc))


async def idempotency_replay_handler(request: Request, exc: Exception) -> Response:
    """Replay short-circuit per PLAN.md L724-L727.

    Returns 200 with the cached ``response_json`` if present. We restore
    the original envelope verbatim — the caller cannot tell whether they
    hit the original write or a replay other than via response headers
    (we add ``X-Idempotency-Replay: true`` for transparency).
    """
    assert isinstance(exc, IdempotencyReplayError)
    cached = exc.data.get("response_json")
    payload: Any = cached if cached is not None else _payload(exc)
    _log.info(
        "idempotency.replay",
        run_id=exc.data.get("run_id"),
        tool_name=exc.data.get("tool_name"),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=200,
        content=jsonable_encoder(payload),
        headers={"x-idempotency-replay": "true"},
    )


async def request_validation_handler(request: Request, exc: Exception) -> Response:
    """Wrap FastAPI ``RequestValidationError`` in our ``ErrorResponse`` shape.

    Default FastAPI 422s use a ``detail: [{loc, msg, type}, ...]`` array;
    we keep the array under ``data.errors`` so clients have one shape to
    deserialize across all error types.
    """
    assert isinstance(exc, RequestValidationError)
    body = ErrorResponse(
        detail="request validation failed",
        code=-32602,
        retryable=False,
        data={"errors": safe_validation_errors(exc.errors())},
    ).model_dump(mode="json")
    return JSONResponse(status_code=422, content=body)


def register_error_handlers(app: FastAPI) -> None:
    """Wire the typed exception handlers onto a FastAPI app."""
    app.add_exception_handler(RepositoryError, repository_error_handler)
    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(ConflictError, conflict_handler)
    app.add_exception_handler(ValidationError, validation_handler)
    app.add_exception_handler(BudgetExceededError, budget_handler)
    app.add_exception_handler(IdempotencyReplayError, idempotency_replay_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)


__all__ = [
    "ErrorResponse",
    "RequestIdMiddleware",
    "register_error_handlers",
    "request_id_var",
]
