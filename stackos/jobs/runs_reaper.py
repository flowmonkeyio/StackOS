"""Orphan-runs reaper job — M8.

Per audit BLOCKER-13 / PLAN.md L1366-L1391: every 5 minutes (and once
at daemon startup) we sweep ``runs`` for rows whose ``status='running'``
and ``heartbeat_at`` is older than 5 minutes. Process-bound stale rows
flip to ``aborted`` with ``error='daemon-restart-orphan'`` and any live
child runs cascade-abort. Live started run plans are durable workflow
state, so the sweep preserves them and refreshes the linked audit
heartbeat instead of aborting normal long-running agent work.

The ``RunRepository.reap_stale`` method is the canonical implementation
(M1.B); this module is the *job wrapper* that APScheduler invokes.
Keeping the wrapper thin makes the job's effects easy to observe in
tests — the bulk of the logic lives in the repo where it's exercised
by the M1.B reap_stale tests too.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session

from stackos.logging import get_logger
from stackos.repositories.runs import RunRepository

# Default stale threshold matches PLAN.md L1374 — runs whose heartbeat
# is older than 5 minutes are considered orphaned.
DEFAULT_STALE_AFTER_SECONDS = 300

_log = get_logger(__name__)


async def reap_orphaned_runs(
    *,
    session_factory: Callable[[], Session],
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> dict[str, int]:
    """Scan + reap stale runs.

    Returns ``{"reaped": <int>}`` so tests + ops dashboards can surface
    the count. Wraps ``RunRepository.reap_stale`` in a fresh session so
    each job firing has its own transaction boundary.
    """
    with session_factory() as session:
        repo = RunRepository(session)
        reaped = repo.reap_stale(stale_after_seconds=stale_after_seconds)
    if reaped:
        _log.info("jobs.reaper.reaped_orphans", count=reaped)
    return {"reaped": reaped}


def make_session_factory(engine: Engine) -> Callable[[], Session]:
    """Return a no-arg callable that opens a fresh session.

    APScheduler can't pickle a ``Session`` so we close over the engine
    instead and open one lazily inside the job body.
    """

    def _factory() -> Session:
        return Session(engine)

    return _factory


__all__ = [
    "DEFAULT_STALE_AFTER_SECONDS",
    "make_session_factory",
    "reap_orphaned_runs",
]
