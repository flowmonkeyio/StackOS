"""Transient desktop admission and future-only timers on the daemon scheduler.

Timer mutations and request admission run on the daemon event loop. The launch
consumer must call the supplied guard after async preflight, immediately before
creating work. Nothing here owns an admitted workflow's lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import time
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

from stackos.artifacts import redact_secret_text
from stackos.db.models import (
    AgentRequest,
    AgentRequestStatus,
    LocalRuntimeConnection,
    Project,
    RunPlan,
    ScheduledJob,
    TrackerTask,
)
from stackos.host_mcp import service as native_host
from stackos.host_mcp.service import AgentExecutionScope
from stackos.local_runtime.exchange import NativeLaunch
from stackos.repositories.agent_requests import AgentRequestExecution, AgentRequestRepository
from stackos.repositories.base import RepositoryError
from stackos.repositories.projects import ScheduleConfiguration, ScheduledJobRepository
from stackos.repositories.run_plan_state import TERMINAL_PLAN_STATUSES
from stackos.repositories.tracker.utils import TERMINAL_TRACKER_STATUSES

WORKFLOW_JOB_PREFIX = "workflow-schedule-"
MAIN_AGENT_BOOTSTRAP = """You are the main agent for this project automation.
Read the repository's current instructions and project context before working.
Carry out the supplied operator goal under ordinary tool authority and approvals.
If a workflow reference is supplied, resolve its effective project workflow,
extension, main-agent guidance and applicable roles before deciding how to execute it.
Coordinate and delegate as appropriate. Create or execute a real run plan only
through ordinary authorized tools when applicable. The scheduler has created no plan.
Preserve evidence and report the actual outcome. Return run_plan_id only for a real
plan in this project, otherwise null. A blocked outcome remains open for inspection.
"""
MAIN_AGENT_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "blocked", "failed"]},
        "summary": {"type": "string"},
        "run_plan_id": {"type": ["integer", "null"], "minimum": 1},
    },
    "required": ["status", "summary", "run_plan_id"],
    "additionalProperties": False,
}
Launch = Callable[[ScheduledJob, datetime, Callable[[], bool]], Awaitable[None]]


@dataclass(frozen=True)
class Registration:
    connection: object
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
        operation_services: dict[str, Any] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.session_factory = session_factory
        self.launch = launch or self._launch
        self.now = now
        self.operation_services = operation_services or {}
        self.connections: dict[str | None, object] = {}
        self.registrations: dict[int, Registration] = {}
        self.revisions: dict[int, object] = {}
        scheduler.add_listener(self._missed, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)

    @property
    def connection(self) -> object | None:
        return self.connections.get(None)

    def connect(self, *, connection_id: str | None = None) -> object:
        # The newest main-process stream owns presence. Superseded cleanup is inert.
        previous = self.connections.get(connection_id)
        if previous is not None:
            self.disconnect(previous)
        connection = self.connections[connection_id] = object()
        with self.session_factory() as session:
            rows = session.exec(select(ScheduledJob).where(ScheduledJob.enabled == True)).all()  # noqa: E712
            for row in rows:
                if (row.execution_json or {}).get("connection_id") == connection_id:
                    self._arm(row)
        return connection

    def disconnect(self, connection: object) -> None:
        for key, current in list(self.connections.items()):
            if connection is current:
                del self.connections[key]
                for row_id, registration in list(self.registrations.items()):
                    if registration.connection is connection:
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
            if row is not None:
                self._arm(row)

    def _arm(self, row: ScheduledJob) -> None:
        if not row.enabled or not row.goal or not row.timezone:
            return
        connection = self.connections.get((row.execution_json or {}).get("connection_id"))
        if connection is None:
            return
        assert row.id is not None
        trigger = CronTrigger.from_crontab(row.cron_expr, timezone=ZoneInfo(row.timezone))
        due = trigger.get_next_fire_time(None, self.now() + timedelta(microseconds=1))
        if due is None:
            return
        registration = Registration(
            connection,
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
        key = (registration.config.get("execution_json") or {}).get("connection_id")
        if self.connections.get(key) is not registration.connection:
            return None
        if self.revisions.get(row_id) is not registration.revision:
            return None
        with self.session_factory() as session:
            row = session.get(ScheduledJob, row_id)
            if row is None or not row.enabled or self._config(row) != registration.config:
                return None
            project = session.get(Project, row.project_id)
            if project is None or project.cleanup_json is not None:
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

    async def _launch(self, row: ScheduledJob, due: datetime, admit: Callable[[], bool]) -> None:
        """One admitted request, one native project-main conversation, no workflow dispatch."""
        row_id = row.id
        assert row_id is not None
        request_id = None
        claim_token = None
        connection_id = None
        principal = None
        handoffs = []
        persisted = False

        def execution_session() -> Session:
            session = self.session_factory()
            session.info["operation_services"] = self.operation_services
            if principal is not None:
                session.info.update(
                    user_principal=principal,
                    server_environment_id=principal.environment_id,
                )
            return session

        try:
            with execution_session() as session:
                from stackos.repositories.managed_upgrades import ManagedUpgradeRepository

                if ManagedUpgradeRepository(session).active():
                    return
                current = session.get(ScheduledJob, row.id)
                if (
                    current is None
                    or current.project_id != row.project_id
                    or not current.enabled
                    or self._config(current) != self._config(row)
                ):
                    return
                requests = AgentRequestRepository(session)
                request_key = f"schedule:{row.id}:{due.astimezone(UTC).isoformat()}"
                if requests.find_by_key(project_id=row.project_id, request_key=request_key):
                    return
                if self._unfinished(session, current):
                    self._result(session, current, due, "overlap")
                    return
                connection_id = (current.execution_json or {}).get("connection_id")
                if connection_id:
                    from stackos.auth import UserPrincipal

                    connection = session.get(LocalRuntimeConnection, connection_id)
                    project = session.get(Project, row.project_id)
                    server = self.operation_services["settings"]
                    if (
                        connection is None
                        or project is None
                        or server.process_role != "server"
                        or connection.project_id != project.id
                        or connection.owner_user_id != project.owner_user_id
                        or connection.environment_id != project.environment_id
                        or connection.environment_id != server.environment_id
                    ):
                        raise ValueError("Computer authorization unavailable")
                    principal = UserPrincipal(connection.owner_user_id, connection.environment_id)
                    session.info.update(
                        user_principal=principal,
                        server_environment_id=principal.environment_id,
                    )
                settings = ScheduledJobRepository(session).validate_configuration(
                    row.project_id, self._config(current)
                )
                assert settings is not None
                if connection_id:
                    from stackos.task_delivery import connection_execution_state

                    exchange = self.operation_services["local_invocations"]
                    exchange.require_available(connection)
                    if connection_execution_state(session, connection, exchange)["needs_attention"]:
                        raise ValueError("Computer work needs manual reconciliation")
                    settings.pop("connection_id")
                    settings["project_dir"] = None
                if not admit():
                    return
                # The due-time window ends at admission. Retain the existing
                # generation/configuration identity for later native preparation.
                admitted_registration = Registration(
                    self.connections.get(connection_id),
                    self.revisions.get(row_id),
                    due,
                    self._config(current),
                )
                context = {
                    "project_id": row.project_id,
                    "schedule_id": row.id,
                    "schedule_name": current.name,
                    "schedule_due_at": due.astimezone(UTC).isoformat(),
                    "goal": current.goal,
                    "template_key": current.template_key,
                    "inputs_json": current.inputs_json,
                }
                request = requests.create(
                    project_id=row.project_id,
                    request_key=request_key,
                    title=current.name or current.kind,
                    body_preview=current.goal or "",
                    source_kind="schedule",
                    metadata_json=context,
                ).data
                request_id = request.id
                current.last_request_id = request_id
                self._result(session, current, due, "admitted")
                claim = requests.claim(
                    project_id=row.project_id, request_id=request_id, claimed_by="schedule"
                ).data
                claim_token = claim.claim_token
            prompt = json.dumps(
                {
                    **context,
                    "request_id": request_id,
                    "request_key": request_key,
                    **({"project_dir": settings["project_dir"]} if not connection_id else {}),
                },
                ensure_ascii=False,
            )

            # Native service invokes this fence at registry admission and process spawn.
            # Disconnect/remove never cancels an already spawned occurrence.
            def launch_allowed() -> bool:
                if self._current(row_id, admitted_registration) is None:
                    return False
                from stackos.repositories.projects import ProjectRepository

                with execution_session() as check:
                    from stackos.repositories.managed_upgrades import ManagedUpgradeRepository

                    if ManagedUpgradeRepository(check).active():
                        return False
                    if connection_id:
                        from stackos.repositories.workspaces import LocalRuntimeConnectionRepository

                        try:
                            current_connection = LocalRuntimeConnectionRepository(check).owned(
                                row.project_id, connection_id
                            )
                        except RepositoryError:
                            return False
                        return (
                            current_connection.revoked_at is None
                            and current_connection.expires_at > time.time()
                        )
                    project = ProjectRepository(check).get(row.project_id)
                    return project.project_dir == settings["project_dir"]

            def transport(call):
                from stackos.repositories.workspaces import LocalRuntimeConnectionRepository

                with execution_session() as session:
                    connection = LocalRuntimeConnectionRepository(session).owned(
                        row.project_id, connection_id
                    )
                    session.expunge(connection)
                return exchange.invoke(
                    connection,
                    call,
                    NativeLaunch(
                        instructions=MAIN_AGENT_BOOTSTRAP,
                        prompt=prompt,
                        output_schema=MAIN_AGENT_RESULT_SCHEMA,
                        **{
                            key: settings[key]
                            for key in (
                                "sandbox",
                                "provider",
                                "profile",
                                "model_reasoning_effort",
                                "native_settings",
                            )
                            if key in settings
                        },
                    ),
                    launch_allowed=launch_allowed,
                    on_offer=lambda handoff: handoffs.append((connection, call, handoff)),
                )

            result = await asyncio.to_thread(
                native_host.run_agent,
                instructions=MAIN_AGENT_BOOTSTRAP,
                prompt=prompt,
                output_schema=MAIN_AGENT_RESULT_SCHEMA,
                execution_scope=AgentExecutionScope(
                    project_id=row.project_id,
                    request_id=request_id,
                    agent="project-main",
                    attempt=1 if connection_id else None,
                ),
                request_execution=AgentRequestExecution(
                    project_id=row.project_id,
                    request_id=request_id,
                    claim_token=claim_token,
                    session_factory=execution_session,
                    snapshot=context,
                ),
                launch_allowed=launch_allowed,
                invocation_transport=transport if connection_id else None,
                **settings,
            )
            with execution_session() as session:
                requests = AgentRequestRepository(session)
                if not result.ok:
                    raise RuntimeError(
                        result.error or f"Native launch exited with {result.exit_code}"
                    )
                from stackos.host_mcp.result import validate_agent_result

                result = validate_agent_result(
                    result, MAIN_AGENT_RESULT_SCHEMA, settings["host_key"]
                )
                if not result.ok:
                    raise RuntimeError(result.error)
                assert result.result is not None
                plan_id = result.result["run_plan_id"]
                if plan_id is not None:
                    requests.link_run_plan(
                        project_id=row.project_id,
                        request_id=request_id,
                        claim_token=claim_token,
                        run_plan_id=plan_id,
                    )
                outcome = result.result["status"]
                if outcome != "blocked":
                    requests.complete(
                        project_id=row.project_id,
                        request_id=request_id,
                        claim_token=claim_token,
                        status=AgentRequestStatus.RESOLVED
                        if outcome == "success"
                        else AgentRequestStatus.FAILED,
                    )
                current = session.get(ScheduledJob, row.id)
                if current is not None and current.last_request_id == request_id:
                    self._result(session, current, due, outcome)
                persisted = True
        except Exception as error:
            # Never retry. Preserve unobserved native exit / persistence failures as unresolved.
            with execution_session() as session:
                if request_id is not None:
                    requests = AgentRequestRepository(session)
                    request = requests.get(project_id=row.project_id, request_id=request_id)
                    evidence = (request.metadata_json or {}).get("native_execution")
                    diagnostic = {"schedule_error": redact_secret_text(str(error))}
                    uncertain = any(handoff.state != "completed" for _, _, handoff in handoffs)
                    if (
                        claim_token
                        and not uncertain
                        and (evidence is None or evidence.get("finished_at"))
                    ):
                        requests.complete(
                            project_id=row.project_id,
                            request_id=request_id,
                            claim_token=claim_token,
                            status=AgentRequestStatus.FAILED,
                            metadata_json=diagnostic,
                        )
                    else:
                        request_row = session.get(AgentRequest, request_id)
                        assert request_row is not None
                        request_row.metadata_json = {
                            **(request_row.metadata_json or {}),
                            **diagnostic,
                        }
                        session.add(request_row)
                        session.commit()
                current = session.get(ScheduledJob, row.id)
                if current is not None and (
                    request_id is None or current.last_request_id == request_id
                ):
                    self._result(session, current, due, "failed" if request_id else "unavailable")
                persisted = True
        if persisted:
            for connection, call, handoff in handoffs:
                if handoff.state == "completed" and call.finished:
                    exchange.release(connection, call)

    @staticmethod
    def _unfinished(session: Session, row: ScheduledJob) -> bool:
        if row.last_request_id is not None:
            last = session.get(AgentRequest, row.last_request_id)
            if last is None or last.project_id != row.project_id:
                return True
        # Include admission interrupted before last_request_id was committed and
        # older linked unfinished plans. Neither lease expiry nor null plan is completion.
        requests = session.exec(
            select(AgentRequest).where(
                AgentRequest.project_id == row.project_id,
                AgentRequest.source_kind == "schedule",
                AgentRequest.metadata_json["schedule_id"].as_integer() == row.id,  # type: ignore[index]
            )
        ).all()
        for request in requests:
            if request.status not in {
                AgentRequestStatus.RESOLVED,
                AgentRequestStatus.FAILED,
                AgentRequestStatus.IGNORED,
            }:
                return True
            if request.run_plan_id is not None:
                plan = session.get(RunPlan, request.run_plan_id)
                if (
                    plan is None
                    or plan.project_id != row.project_id
                    or plan.status not in TERMINAL_PLAN_STATUSES
                ):
                    return True
                task = session.exec(
                    select(TrackerTask).where(
                        TrackerTask.project_id == row.project_id,
                        TrackerTask.key == f"workflow-{plan.id}",
                    )
                ).first()
                if task is None or task.status not in TERMINAL_TRACKER_STATUSES:
                    return True
        return False

    @staticmethod
    def _result(session: Session, row: ScheduledJob, due: datetime, status: str) -> None:
        row.last_run_at = due.astimezone(UTC).replace(tzinfo=None)
        row.last_run_status = status
        session.add(row)
        session.commit()

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
