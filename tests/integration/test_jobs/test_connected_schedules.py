"""Connected cron reuses native stream lifetime and invocation readiness."""

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from stackos.auth import UserPrincipal
from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import (
    AgentRequest,
    AgentRequestStatus,
    LocalRuntimeConnection,
    Project,
    RunPlan,
    ScheduledJob,
)
from stackos.host_mcp import service as native_host
from stackos.jobs.scheduler import build_scheduler
from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime
from stackos.local_runtime.exchange import InvocationExchange, InvocationReport, NativeResult
from stackos.repositories.projects import ScheduledJobRepository, ScheduleExecution
from stackos.repositories.tracker import TrackerRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


def test_schedule_selects_exactly_one_execution_target():
    connection_id = str(uuid4())
    assert ScheduleExecution(connection_id=connection_id).connection_id == connection_id
    assert ScheduleExecution(project_dir="/local").project_dir == "/local"
    for values in (
        {"project_dir": "/local", "connection_id": connection_id},
        {"connection_id": connection_id, "mcp_config_ref": {"path": "/local", "sha256": "a" * 64}},
    ):
        with pytest.raises(ValidationError):
            ScheduleExecution(**values)


@pytest.fixture
def engine(engine, tmp_path):
    database = make_engine(tmp_path / "connected-schedules.sqlite")
    restore_database_snapshot(database, capture_database_snapshot(engine))
    try:
        yield database
    finally:
        native_host.shutdown_usage()
        database.dispose()


@pytest.fixture
def connected_schedule(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(native_host, "agent_selection_error", lambda *a, **kw: None)
    principal = UserPrincipal(str(uuid4()), "schedule-proof")
    exchange = InvocationExchange()
    settings = Settings()
    settings = settings.model_copy(
        update={"process_role": "server", "environment_id": principal.environment_id}
    )
    services = {"settings": settings, "local_invocations": exchange}
    clock = [datetime(2030, 1, 1, tzinfo=UTC)]
    with Session(engine) as session:
        project = Project(
            slug="connected-cron",
            name="Cron",
            domain="example.test",
            locale="en-US",
            owner_user_id=principal.user_id,
            environment_id=principal.environment_id,
        )
        session.add(project)
        session.flush()
        connection = LocalRuntimeConnection(
            id=str(uuid4()),
            project_id=project.id,
            owner_user_id=principal.user_id,
            environment_id=principal.environment_id,
            server_origin="http://127.0.0.1:20460",
            credential_digest=uuid4().hex,
            expires_at=time.time() + 3600,
        )
        session.add(connection)
        session.commit()
        session.info.update(
            user_principal=principal,
            server_environment_id=principal.environment_id,
            operation_services=services,
        )
        spec = WorkflowTemplateSpec.model_validate(
            {
                "schema_version": "stackos.workflow-template.v1",
                "key": "proof.connected-cron",
                "name": "Cron",
                "version": "0.1.0",
                "steps": [{"id": "work", "title": "Work"}],
            }
        )
        WorkflowTemplateLoader(session).save_project_template(project_id=project.id, spec=spec)
        row = (
            ScheduledJobRepository(session)
            .set(
                project_id=project.id,
                kind="proof",
                name="Proof",
                goal="Review the connected project's current context",
                cron_expr="* * * * *",
                timezone="UTC",
                template_key=spec.key,
                inputs_json={},
                execution_json={"connection_id": connection.id},
                enabled=True,
            )
            .data
        )
        session.expunge(connection)
    scheduler = build_scheduler(Settings(), engine)
    runtime = WorkflowScheduleRuntime(
        scheduler, lambda: Session(engine), operation_services=services, now=lambda: clock[0]
    )
    return runtime, scheduler, clock, connection, exchange, row.id


@pytest.mark.asyncio
async def test_connected_stream_is_scoped_and_reconnect_is_future_only(connected_schedule, engine):
    runtime, scheduler, clock, connection, _exchange, row_id = connected_schedule
    scheduler.start(paused=True)
    try:
        local = runtime.connect()  # Standalone presence cannot arm online schedules.
        foreign = runtime.connect(connection_id=str(uuid4()))
        assert row_id not in runtime.registrations
        native = runtime.connect(connection_id=connection.id)
        old = runtime.registrations[row_id]
        runtime.disconnect(local)
        runtime.disconnect(foreign)
        assert runtime.registrations[row_id] is old
        runtime.disconnect(native)
        clock[0] = old.due + timedelta(minutes=10)
        current = runtime.connect(connection_id=connection.id)
        assert runtime.registrations[row_id].due > clock[0]
        runtime.disconnect(native)
        await runtime._fire(row_id=row_id, registration=old)
        with Session(engine) as session:
            assert session.exec(select(AgentRequest)).all() == []
        runtime.disconnect(current)
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unavailable", ["never_polled", "stale", "revoked", "expired", "unresolved"]
)
async def test_admission_rechecks_bound_computer_before_creating_work(
    connected_schedule, engine, unavailable
):
    runtime, _scheduler, clock, connection, exchange, row_id = connected_schedule
    runtime.connect(connection_id=connection.id)
    if unavailable != "never_polled":
        exchange.poll(connection, None)
    if unavailable == "stale":
        identity, seen = exchange._seen[connection.id]
        exchange._seen[connection.id] = identity, seen - 31
    if unavailable == "unresolved":
        with Session(engine) as session:
            TrackerRepository(session).create_task(
                project_id=connection.project_id,
                key="uncertain",
                title="Unknown prior outcome",
                metadata_json={
                    "delivery": {"connection_id": connection.id, "needs_attention": True}
                },
            )
    if unavailable in {"revoked", "expired"}:
        with Session(engine) as session:
            row = session.get(LocalRuntimeConnection, connection.id)
            if unavailable == "revoked":
                row.revoked_at = datetime.now(UTC).replace(tzinfo=None)
            else:
                row.expires_at = time.time() - 1
            session.add(row)
            session.commit()
    registration = runtime.registrations[row_id]
    clock[0] = registration.due
    await runtime._fire(row_id=row_id, registration=registration)
    with Session(engine) as session:
        assert session.exec(select(AgentRequest)).all() == []
        assert session.get(ScheduledJob, row_id).last_run_status == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "blocked", "unknown"])
async def test_ready_connected_schedule_runs_project_main_without_creating_workflow(
    connected_schedule, engine, monkeypatch, outcome
):
    runtime, _scheduler, clock, connection, exchange, row_id = connected_schedule
    exchange.poll(connection, None)
    runtime.connect(connection_id=connection.id)
    commands = []
    offer = exchange.offer

    def report_native_result(connection, call, launch):
        handoff = offer(connection, call, launch)
        commands.append(exchange.poll(connection, None))
        exchange.report(
            connection,
            InvocationReport(
                invocation_id=call.invocation_id,
                attempt=call.scope.attempt,
                state="unknown" if outcome == "unknown" else "completed",
                outcome=None
                if outcome == "unknown"
                else NativeResult(
                    exit_code=0,
                    thread_id="connected-main",
                    result={
                        "status": outcome,
                        "summary": "Observed native result",
                        "run_plan_id": None,
                    },
                ),
            ),
        )
        return handoff

    monkeypatch.setattr(exchange, "offer", report_native_result)
    registration = runtime.registrations[row_id]
    clock[0] = registration.due
    await runtime._fire(row_id=row_id, registration=registration)
    with Session(engine) as session:
        recorded = session.exec(select(AgentRequest)).all()
        assert len(commands) == 1, [
            request.metadata_json.get("schedule_error") for request in recorded
        ]
    command = commands[0]
    assert command["scope"]["agent"] == "project-main"
    assert command["scope"]["request_id"] is not None
    assert "project_dir" not in command["launch"]["prompt"]
    assert "scheduler has created no plan" in command["launch"]["instructions"]
    with Session(engine) as session:
        request = session.exec(select(AgentRequest)).one()
        assert request.id == command["scope"]["request_id"]
        assert request.status == (
            AgentRequestStatus.RESOLVED if outcome == "success" else AgentRequestStatus.CLAIMED
        )
        assert request.metadata_json["native_execution"]["snapshot"]["project_dir"] is None
        assert session.exec(select(RunPlan)).all() == []
        assert session.get(ScheduledJob, row_id).last_run_status == (
            "failed" if outcome == "unknown" else outcome
        )
    if outcome == "unknown":
        assert exchange.snapshot(connection)["needs_attention"]
    else:
        assert connection.id not in exchange._calls
    # The same occurrence cannot be admitted twice, including blocked/unknown work.
    await runtime._launch(
        runtime._current(row_id, runtime.registrations[row_id]), registration.due, lambda: True
    )
    assert len(commands) == 1
