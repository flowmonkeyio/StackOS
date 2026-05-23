"""Health endpoint — `GET /api/v1/health`.

This is the only endpoint whitelisted from bearer-token auth; doctor uses
it to probe liveness *before* it has the token resolved.

Response shape (M0 subset; later milestones add `auth_token_mode_ok`,
`seed_file_ok`, and `integrations_reachable`):

    {
      "daemon_uptime_s": 12.3,
      "db_status": "ok" | "unreachable",
      "scheduler_running": false,
      "version": "1.0.0",
      "milestone": "M10"
    }

Latency target: < 50 ms p99. Implemented with a synchronous `SELECT 1` on
the engine — SQLite is in-process so this is cheap.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from stackos import __milestone__, __version__


class HealthResponse(BaseModel):
    """M0 health payload — narrower than the full PLAN.md L1319-1339 shape."""

    daemon_uptime_s: float
    db_status: Literal["ok", "unreachable"]
    scheduler_running: bool
    version: str
    milestone: str


router = APIRouter(prefix="/api/v1", tags=["health"])


def _check_db(engine: Engine | None) -> Literal["ok", "unreachable"]:
    """Return 'ok' if a `SELECT 1` round-trips, 'unreachable' otherwise.

    We catch the broadest SQLAlchemy exception class plus OSError because the
    failure modes here (file missing, locked, corrupted) are all "unreachable"
    from the health-check's perspective — the caller wants liveness, not a
    stack trace.
    """
    if engine is None:
        return "unreachable"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        return "unreachable"
    return "ok"


@router.get("/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    """Return daemon liveness + version. Auth-whitelisted (see auth.py)."""
    state = request.app.state
    started_at: float = getattr(state, "started_at", time.monotonic())
    engine: Engine | None = getattr(state, "engine", None)
    scheduler_running: bool = getattr(state, "scheduler_running", False)

    return HealthResponse(
        daemon_uptime_s=round(time.monotonic() - started_at, 3),
        db_status=_check_db(engine),
        scheduler_running=scheduler_running,
        version=__version__,
        milestone=__milestone__,
    )
