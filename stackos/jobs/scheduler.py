"""APScheduler configuration for the daemon.

The scheduler is built once at daemon startup and held on
``app.state.scheduler``. Job sources:

1. **Background ops jobs** — the runs reaper is registered explicitly in
   the lifespan hook.
2. **Future project automations** — each admitted occurrence creates one
   request-owned native main-agent thread. The agent may explicitly create an
   applicable run plan through ordinary tools and approvals.

The two jobstore tables are deliberately distinct:

- ``apscheduler_jobs`` — the SDK's own pickle-blob persistence, managed
  end-to-end by SQLAlchemyJobStore. Schema is *not* tracked by Alembic;
  the SDK creates it idempotently on first use. We never query this
  table from application code.
- ``scheduled_jobs`` — operator-facing per-project goal, optional workflow and
  cron configuration. Its runtime owns future-only request admission, while
  request/native owners retain admitted execution and history.

Per audit MAJOR-23 the executor map is:

- ``default`` — ``AsyncIOExecutor`` for short jobs.
- ``long`` — ``ThreadPoolExecutor(max_workers=3)`` for genuinely
  blocking calls, including encrypted backups.
- ``task-delivery`` — a separate bounded pool of 32 workers. The task
  admission owner applies the persisted 1..32 limit (default 3) before scheduling.

Job defaults:

- ``coalesce=True`` so a daemon that was offline for a week
  collapses missed cron firings into one (PLAN.md L1362).
- ``max_instances=1`` per ``job_id`` — APScheduler refuses to start a
  second instance while one is in-flight.
- ``misfire_grace_time=3600`` (1 h) — the daemon will still fire a job
  that drifted up to an hour from its scheduled time.

Timezone is ``UTC`` for the scheduler globally.
"""

from __future__ import annotations

import asyncio
from typing import Any

from apscheduler.events import EVENT_JOB_MISSED, EVENT_SCHEDULER_STARTED
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.engine import Engine
from sqlmodel import Session

from stackos.config import Settings
from stackos.task_capacity import MAX_TASK_CAPACITY

# Stable job_id prefixes so operators and tests can find / cancel jobs by
# predictable ids.
RUNNER_JOB_PREFIX = "run-"
RUNS_REAPER_JOB_ID = "background-runs-reaper"

# Misfire grace times — kept here so all jobs read the same numbers.
JOB_DEFAULT_MISFIRE_GRACE_SECONDS = 3600  # 1h — generous default
REAPER_MISFIRE_GRACE_SECONDS = 600  # 10 min — reaper should run promptly
RUNNER_MISFIRE_GRACE_SECONDS = 7200  # 2h


def build_scheduler(settings: Settings, engine: Engine) -> AsyncIOScheduler:
    """Return an ``AsyncIOScheduler`` wired against the daemon's engine.

    Per PLAN.md L1346-L1358 the executor + jobstore + job_defaults are
    fixed; per audit MAJOR-23 we assert this layout in tests so an
    accidental drift surfaces during release validation.

    Note ``settings`` is currently unused but accepted for symmetry —
    the lifespan hook always has both available, and a future tightening
    (``settings.scheduler_misfire_grace_seconds`` etc.) lands here
    without changing call sites.
    """
    _ = settings
    executors: dict[str, Any] = {
        "default": AsyncIOExecutor(),
        "long": ThreadPoolExecutor(max_workers=3),
        "task-delivery": ThreadPoolExecutor(max_workers=MAX_TASK_CAPACITY),
    }
    jobstores: dict[str, Any] = {
        # Kept available for simple picklable jobs.
        "default": SQLAlchemyJobStore(
            engine=engine,
            tablename="apscheduler_jobs",
        ),
        # Ops jobs rely on closures over the daemon-local engine/session
        # factory and therefore cannot be pickled into the SQL store. They
        # live in memory and the lifespan re-registers them on every boot.
        "memory": MemoryJobStore(),
    }
    job_defaults: dict[str, Any] = {
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": JOB_DEFAULT_MISFIRE_GRACE_SECONDS,
    }
    scheduler = AsyncIOScheduler(
        executors=executors,
        jobstores=jobstores,
        job_defaults=job_defaults,
        timezone="UTC",
    )
    from stackos.task_delivery import drain_delivery, missed_delivery_job

    def started(_event):
        scheduler.task_delivery_started = True

    scheduler.add_listener(started, EVENT_SCHEDULER_STARTED)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None  # Configuration-only callers need no live callback dispatcher.
    if loop is not None:
        scheduler.task_delivery_drain = lambda: loop.call_soon_threadsafe(
            lambda: drain_delivery(session_factory=lambda: Session(engine), scheduler=scheduler)
        )

    scheduler.add_listener(
        lambda event: missed_delivery_job(
            event, session_factory=lambda: Session(engine), scheduler=scheduler
        ),
        EVENT_JOB_MISSED,
    )
    return scheduler


__all__ = [
    "JOB_DEFAULT_MISFIRE_GRACE_SECONDS",
    "REAPER_MISFIRE_GRACE_SECONDS",
    "RUNNER_JOB_PREFIX",
    "RUNNER_MISFIRE_GRACE_SECONDS",
    "RUNS_REAPER_JOB_ID",
    "build_scheduler",
]
