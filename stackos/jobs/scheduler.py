"""APScheduler configuration for the daemon.

The scheduler is built once at daemon startup and held on
``app.state.scheduler``. Job sources:

1. **Background ops jobs** — the runs reaper is registered explicitly in
   the lifespan hook.
2. **Future workflow schedules** — scheduled project work should start
   native StackOS run plans, not a separate workflow engine.

The two jobstore tables are deliberately distinct:

- ``apscheduler_jobs`` — the SDK's own pickle-blob persistence, managed
  end-to-end by SQLAlchemyJobStore. Schema is *not* tracked by Alembic;
  the SDK creates it idempotently on first use. We never query this
  table from application code.
- ``scheduled_jobs`` — operator-facing per-project cron metadata. The UI's
  "Schedules" tab toggles ``enabled``; workflow-specific scheduling must
  create native run-plan starts.

Per audit MAJOR-23 the executor map is:

- ``default`` — ``AsyncIOExecutor`` for short jobs.
- ``long`` — ``ThreadPoolExecutor(max_workers=2)`` for genuinely
  blocking calls (e.g. a future synchronous backup tool).

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

from typing import Any

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.engine import Engine

from stackos.config import Settings

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
        "long": ThreadPoolExecutor(max_workers=2),
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
    return AsyncIOScheduler(
        executors=executors,
        jobstores=jobstores,
        job_defaults=job_defaults,
        timezone="UTC",
    )


__all__ = [
    "JOB_DEFAULT_MISFIRE_GRACE_SECONDS",
    "REAPER_MISFIRE_GRACE_SECONDS",
    "RUNNER_JOB_PREFIX",
    "RUNNER_MISFIRE_GRACE_SECONDS",
    "RUNS_REAPER_JOB_ID",
    "build_scheduler",
]
