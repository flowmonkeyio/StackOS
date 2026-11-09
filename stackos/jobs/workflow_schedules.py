"""Transient desktop admission and future-only timers on the daemon scheduler.

All mutations and admission checks run on the daemon event loop. The launch
consumer must call the supplied guard after async preflight, immediately before
creating work. Nothing here owns an admitted workflow's lifecycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED, JobEvent
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlmodel import Session, select

from stackos.db.models import ScheduledJob
from stackos.repositories.projects import ScheduleConfiguration

WORKFLOW_JOB_PREFIX = "workflow-schedule-"
Launch = Callable[[ScheduledJob, datetime, Callable[[], bool]], Awaitable[None]]


@dataclass(frozen=True)
class Registration:
    generation: int
    revision: object
    due: datetime
    config: dict[str, Any]


class WorkflowScheduleRuntime:
    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        session_factory: Callable[[], Session],
        *,
        launch: Launch | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.scheduler = scheduler
        self.session_factory = session_factory
        self.launch = launch
        self.now = now
        self.generation = 0
        self.connection: object | None = None
        self.registrations: dict[int, Registration] = {}
        self.revisions: dict[int, object] = {}
        scheduler.add_listener(self._missed, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)

    def connect(self) -> object:
        # The newest main-process stream owns presence. Superseded cleanup is inert.
        self._clear()
        self.generation += 1
        self.connection = object()
        with self.session_factory() as session:
            rows = session.exec(select(ScheduledJob).where(ScheduledJob.enabled == True)).all()  # noqa: E712
            for row in rows:
                self._arm(row)
        return self.connection

    def disconnect(self, connection: object) -> None:
        if connection is self.connection:
            self.connection = None
            self.generation += 1
            self._clear()

    def _clear(self) -> None:
        for row_id in list(self.registrations):
            self._remove(row_id)

    def _remove(self, row_id: int) -> None:
        self.registrations.pop(row_id, None)
        self.revisions.pop(row_id, None)
        # A fired date job has already been removed by APScheduler.
        with suppress(JobLookupError):
            self.scheduler.remove_job(f"{WORKFLOW_JOB_PREFIX}{row_id}", jobstore="memory")

    @staticmethod
    def _config(row: ScheduledJob) -> dict[str, Any]:
        return {key: getattr(row, key) for key in ScheduleConfiguration.model_fields}

    def reconcile(self, *, project_id: int, job_id: int) -> None:
        with self.session_factory() as session:
            row = session.get(ScheduledJob, job_id)
            if row is not None and row.project_id != project_id:
                return
            self._remove(job_id)
            if self.connection is not None and row is not None:
                self._arm(row)

    def _arm(self, row: ScheduledJob) -> None:
        if not row.enabled or not row.template_key or not row.execution_json or not row.timezone:
            return
        assert row.id is not None
        trigger = CronTrigger.from_crontab(row.cron_expr, timezone=ZoneInfo(row.timezone))
        due = trigger.get_next_fire_time(None, self.now() + timedelta(microseconds=1))
        if due is None:
            return
        registration = Registration(
            self.generation,
            self.revisions.setdefault(row.id, object()),
            due.astimezone(UTC),
            self._config(row),
        )
        self.registrations[row.id] = registration
        # One date job per next cron instant preserves the exact occurrence time.
        # On execute/misfire we calculate the next future instant, never backlog.
        self.scheduler.add_job(
            self._fire,
            trigger=DateTrigger(run_date=registration.due),
            kwargs={"row_id": row.id, "registration": registration},
            id=f"{WORKFLOW_JOB_PREFIX}{row.id}",
            jobstore="memory",
            replace_existing=True,
            misfire_grace_time=1,
            coalesce=True,
            max_instances=1,
        )

    def _current(self, row_id: int, registration: Registration) -> ScheduledJob | None:
        if self.connection is None or self.generation != registration.generation:
            return None
        if self.revisions.get(row_id) is not registration.revision:
            return None
        with self.session_factory() as session:
            row = session.get(ScheduledJob, row_id)
            if row is None or not row.enabled or self._config(row) != registration.config:
                return None
            return row

    async def _fire(self, *, row_id: int, registration: Registration) -> None:
        if self.registrations.get(row_id) is not registration:
            return
        row = self._current(row_id, registration)
        if row is None:
            self._remove(row_id)
            return
        self._arm(row)
        if not 0 <= (self.now() - registration.due).total_seconds() <= 1:
            return
        if self.launch is not None:
            await self.launch(
                row,
                registration.due,
                lambda: (
                    self._current(row_id, registration) is not None
                    and 0 <= (self.now() - registration.due).total_seconds() <= 1
                ),
            )

    def _missed(self, event: JobEvent) -> None:
        if not event.job_id.startswith(WORKFLOW_JOB_PREFIX):
            return
        row_id = int(event.job_id.removeprefix(WORKFLOW_JOB_PREFIX))
        registration = self.registrations.get(row_id)
        times = getattr(event, "scheduled_run_times", [getattr(event, "scheduled_run_time", None)])
        if registration is not None and registration.due in times:
            with self.session_factory() as session:
                row = session.get(ScheduledJob, row_id)
                if row is not None and self._current(row_id, registration) is not None:
                    self._arm(row)
