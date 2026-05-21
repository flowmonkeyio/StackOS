"""Per-call MCP request scope.

Mirrors the REST request-id middleware (``content_stack.api.errors``):
each MCP call resolves a small ``MCPContext`` dataclass holding the
fields every audit log line needs. The context is bound to a
``ContextVar`` so structlog's processor injects it into every log
record without explicit threading.

Building the context is a two-step process the dispatch layer drives:

1. ``build_context`` runs at the top of every ``call_tool`` handler.
   It extracts ``run_token``, ``project_id``, ``idempotency_key`` from
   the inbound arguments and resolves the run row + skill name via
   ``permissions.resolve_run_token``. The resulting ``MCPContext`` is
   stamped with a fresh request-id (UUID4).
2. The handler ``with bind_context(ctx)`` sets the ContextVar; on exit
   it restores the prior value (so nested calls in tests survive).

The context's ``session`` field is the **per-request SQLModel session**
the dispatch layer opens against the engine on ``app.state``. The
session lifecycle is owned by the dispatch layer, not by this module.
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from content_stack.db.models import Run
from content_stack.mcp.permissions import resolve_run_token

# ContextVar bound for the lifetime of one tool call. The structlog
# processor reads this so logs emitted under a tool inherit the
# request-id / run-id / project-id without explicit binding.
mcp_context_var: contextvars.ContextVar[MCPContext | None] = contextvars.ContextVar(
    "mcp_context", default=None
)


@dataclass
class MCPContext:
    """Per-call request scope for an MCP tool handler.

    Fields:

    - ``request_id`` — UUID4 stamped at handler entry; mirrors the REST
      request-id contract (logs join across the two transports on this).
    - ``run_token`` — value the caller passed in; ``None`` for
      system/UI calls.
    - ``run`` — the resolved ``Run`` row from
      ``permissions.resolve_run_token`` (or ``None``).
    - ``run_id`` — denormalised from ``run.id`` for log convenience.
    - ``project_id`` — caller-supplied; mutating tools propagate into
      ``WriteEnvelope.project_id``.
    - ``idempotency_key`` — caller-supplied; checked by the dispatcher
      against ``IdempotencyKeyRepository`` for mutating tools.
    - ``skill_name`` — resolved by ``resolve_run_token``; ``__system__``
      for direct REST/UI traffic, ``__test__`` for the test harness.
    - ``session`` — the SQLModel session bound to this request.
    """

    session: Session
    request_id: str
    run_token: str | None = None
    run: Run | None = None
    run_id: int | None = None
    project_id: int | None = None
    idempotency_key: str | None = None
    skill_name: str = "__system__"
    expected_etag: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_log_dict(self) -> dict[str, Any]:
        """Serialise the context for structlog binding."""
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "skill_name": self.skill_name,
            "idempotency_key": self.idempotency_key,
        }


def build_context(
    arguments: dict[str, Any] | None,
    session: Session,
) -> MCPContext:
    """Construct an ``MCPContext`` from raw tool arguments.

    Pulls the four cross-cutting fields from the inbound dict:

    - ``run_token`` (string)
    - ``project_id`` (int)
    - ``idempotency_key`` (string)
    - ``expected_etag`` (string)

    Resolves the run row + skill name; returns the populated context.
    Argument values are not popped — the per-tool ``MCPInput`` parser
    re-validates them with strict-extra ``forbid``, so the dispatcher
    leaves them in place.
    """
    args = dict(arguments or {})
    run_token = args.get("run_token")
    project_id = args.get("project_id")
    idempotency_key = args.get("idempotency_key")
    expected_etag = args.get("expected_etag")

    run, skill_name = resolve_run_token(run_token, session)
    return MCPContext(
        session=session,
        request_id=str(uuid.uuid4()),
        run_token=run_token,
        run=run,
        run_id=run.id if run is not None else None,
        project_id=int(project_id) if isinstance(project_id, int) else project_id,
        idempotency_key=idempotency_key,
        skill_name=skill_name,
        expected_etag=expected_etag,
    )


@contextmanager
def bind_context(ctx: MCPContext) -> Iterator[MCPContext]:
    """Set the context-var for the duration of a tool handler.

    Restores the prior value on exit so nested calls (used in unit
    tests that exercise repository methods through the same MCP shim)
    don't trample each other.
    """
    token = mcp_context_var.set(ctx)
    try:
        yield ctx
    finally:
        mcp_context_var.reset(token)


def current_context() -> MCPContext | None:
    """Return the active ``MCPContext`` or ``None`` if outside a call."""
    return mcp_context_var.get()


__all__ = [
    "MCPContext",
    "bind_context",
    "build_context",
    "current_context",
    "mcp_context_var",
]
