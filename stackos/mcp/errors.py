"""RepositoryError → JSON-RPC error mapping for the MCP transport.

Mirrors ``stackos.api.errors`` (REST transport adapter) but emits
the JSON-RPC error envelope every MCP client expects. Two M3-specific
typed errors live here:

- ``ToolNotGrantedError`` — JSON-RPC -32007. Raised by
  ``permissions.check_grant`` when the active skill is not allowed to
  call a given tool.
- ``MilestoneDeferralError`` — JSON-RPC -32601 (Method not found). Used
  by tools that depend on a future milestone (M5/M7/M8). The hint and
  ``data.milestone`` make the deferral explicit instead of surfacing as
  a -32603 internal error.

The catch-all ``map_repository_error`` function consumes any
``RepositoryError`` (including these two) and returns a tuple of
``(code, message, data)`` for the SDK's error envelope.
"""

from __future__ import annotations

from typing import Any

from stackos.repositories.base import (
    BudgetExceededError,
    ConflictError,
    IdempotencyReplayError,
    NotFoundError,
    RepositoryError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# JSON-RPC error codes — single source of truth for M3.
# ---------------------------------------------------------------------------


# JSON-RPC 2.0 reserved range is -32768..-32000; project-specific codes
# live in -32099..-32000 per MCP convention. We adopt the PLAN.md L745-L754
# table directly.
JSONRPC_VALIDATION = -32602
JSONRPC_NOT_FOUND = -32004
JSONRPC_FORBIDDEN = -32007
JSONRPC_CONFLICT = -32008
JSONRPC_INTEGRATION_DOWN = -32010
JSONRPC_RATE_LIMITED = -32011
JSONRPC_BUDGET = -32012
JSONRPC_INTERNAL = -32603
JSONRPC_METHOD_NOT_FOUND = -32601


# ---------------------------------------------------------------------------
# M3-only typed errors.
# ---------------------------------------------------------------------------


class ToolNotGrantedError(RepositoryError):
    """Raised when the active skill does not own the requested tool.

    Maps to JSON-RPC -32007 per PLAN.md L749. The error ``data`` carries
    ``tool``, ``skill``, and the ``allowed`` list so the calling LLM can
    surface a remediation hint to the operator.
    """

    code = JSONRPC_FORBIDDEN
    http_status = 403
    retryable = False


class MilestoneDeferralError(RepositoryError):
    """A tool is registered but its implementation lands in a later milestone.

    JSON-RPC -32601 ("Method not found") is the MCP-spec convention for a
    tool that doesn't currently exist. We use it here so deferred tools
    look like "not yet available" rather than "internal failure" to the
    client. The ``data`` dict explicitly names the milestone and points at
    the PLAN.md section so operators can self-serve.
    """

    code = JSONRPC_METHOD_NOT_FOUND
    http_status = 501
    retryable = False


class IntegrationDownError(RepositoryError):
    """An integration vendor is unreachable.

    Raised by the M4 vendor wrappers when a 5xx exhausts retries or a
    4xx (other than 429) surfaces immediately.
    """

    code = JSONRPC_INTEGRATION_DOWN
    http_status = 502
    retryable = True


class RateLimitedError(RepositoryError):
    """The integration limited us; the daemon will retry up to 3x per audit B-?."""

    code = JSONRPC_RATE_LIMITED
    http_status = 429
    retryable = True


# ---------------------------------------------------------------------------
# Mapping helper.
# ---------------------------------------------------------------------------


def map_repository_error(exc: RepositoryError) -> tuple[int, str, dict[str, Any]]:
    """Return ``(code, message, data)`` for an MCP JSON-RPC error envelope.

    Mirrors ``stackos.api.errors`` semantics but emits JSON-RPC
    codes instead of HTTP statuses. The error ``message`` is the typed
    error class name (so clients can ``error.message in {"NotFoundError",
    ...}``); the human-readable string lives in ``data.detail``.
    """
    # Typed deferrals first (M5/M7/M8 surface markers).
    if isinstance(exc, MilestoneDeferralError):
        return JSONRPC_METHOD_NOT_FOUND, "MilestoneDeferralError", _splat(exc)
    if isinstance(exc, ToolNotGrantedError):
        return JSONRPC_FORBIDDEN, "ToolNotGrantedError", _splat(exc)
    if isinstance(exc, IntegrationDownError):  # pragma: no cover — M5 marker
        return JSONRPC_INTEGRATION_DOWN, "IntegrationDownError", _splat(exc)
    if isinstance(exc, RateLimitedError):  # pragma: no cover — M5 marker
        return JSONRPC_RATE_LIMITED, "RateLimitedError", _splat(exc)
    # Repository typed errors.
    if isinstance(exc, NotFoundError):
        return JSONRPC_NOT_FOUND, "NotFoundError", _splat(exc)
    if isinstance(exc, ConflictError):
        return JSONRPC_CONFLICT, "ConflictError", _splat(exc)
    if isinstance(exc, ValidationError):
        return JSONRPC_VALIDATION, "ValidationError", _splat(exc)
    if isinstance(exc, BudgetExceededError):
        return JSONRPC_BUDGET, "BudgetExceededError", _splat(exc)
    if isinstance(exc, IdempotencyReplayError):
        # Replay is flow control, not failure — caller short-circuits to
        # the cached envelope. Reaching this code path means a replay
        # leaked outside the idempotency dispatcher; treat it as conflict
        # so the client surfaces something rather than a silent error.
        return JSONRPC_CONFLICT, "IdempotencyReplayError", _splat(exc)
    # Catch-all: a generic ``RepositoryError`` is internal.
    return JSONRPC_INTERNAL, "RepositoryError", _splat(exc)


def _splat(exc: RepositoryError) -> dict[str, Any]:
    """Build the JSON-RPC ``data`` dict from a typed repository error."""
    payload: dict[str, Any] = dict(exc.data)
    payload["detail"] = exc.detail
    payload["retryable"] = exc.retryable
    return payload


__all__ = [
    "JSONRPC_BUDGET",
    "JSONRPC_CONFLICT",
    "JSONRPC_FORBIDDEN",
    "JSONRPC_INTEGRATION_DOWN",
    "JSONRPC_INTERNAL",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_NOT_FOUND",
    "JSONRPC_RATE_LIMITED",
    "JSONRPC_VALIDATION",
    "IntegrationDownError",
    "MilestoneDeferralError",
    "RateLimitedError",
    "ToolNotGrantedError",
    "map_repository_error",
]
