"""APScheduler config matches PLAN.md L1346-L1358 + audit MAJOR-23."""

from __future__ import annotations

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from stackos.config import Settings
from stackos.jobs.scheduler import build_scheduler


def test_scheduler_has_default_and_long_executors(engine: object) -> None:
    """The runner needs async jobs plus capacity for three long jobs."""
    s = build_scheduler(Settings(), engine)  # type: ignore[arg-type]
    assert isinstance(s._executors["default"], AsyncIOExecutor)
    assert isinstance(s._executors["long"], ThreadPoolExecutor)
    assert s._executors["long"]._pool._max_workers == 3
    assert isinstance(s._executors["task-delivery"], ThreadPoolExecutor)
    assert s._executors["task-delivery"]._pool._max_workers == 32


def test_scheduler_has_sqlalchemy_default_jobstore_and_memory(engine: object) -> None:
    """Scheduled run jobs persist via SQLAlchemyJobStore; closures use MemoryJobStore."""
    s = build_scheduler(Settings(), engine)  # type: ignore[arg-type]
    assert isinstance(s._jobstores["default"], SQLAlchemyJobStore)
    assert isinstance(s._jobstores["memory"], MemoryJobStore)


def test_scheduler_job_defaults_match_plan(engine: object) -> None:
    """``coalesce=True``, ``max_instances=1``, ``misfire_grace_time=3600``."""
    s = build_scheduler(Settings(), engine)  # type: ignore[arg-type]
    defaults = s._job_defaults
    assert defaults["coalesce"] is True
    assert defaults["max_instances"] == 1
    assert defaults["misfire_grace_time"] == 3600


def test_scheduler_timezone_is_utc(engine: object) -> None:
    """Scheduler global timezone is UTC; per-job timezones live in CronTrigger."""
    s = build_scheduler(Settings(), engine)  # type: ignore[arg-type]
    assert str(s.timezone) == "UTC"
