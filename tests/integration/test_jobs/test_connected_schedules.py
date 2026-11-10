"""Connected cron reuses native stream lifetime and invocation readiness."""

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from stackos.auth import UserPrincipal
from stackos.config import Settings
from stackos.db.models import AgentRequest, LocalRuntimeConnection, Project, ScheduledJob
from stackos.jobs.scheduler import build_scheduler
from stackos.jobs.workflow_schedules import WorkflowScheduleRuntime
from stackos.local_runtime.exchange import InvocationExchange
from stackos.repositories.projects import ScheduledJobRepository, ScheduleExecution
from stackos.repositories.tracker import TrackerRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from stackos.workflows.template_schema import WorkflowTemplateSpec


def test_schedule_selects_exactly_one_execution_target():
    connection_id = str(uuid4())
    assert ScheduleExecution(connection_id=connection_id).connection_id == connection_id
    assert ScheduleExecution(project_dir="/local").project_dir == "/local"
    for values in (
        {},
        {"project_dir": "/local", "connection_id": connection_id},
        {"connection_id": connection_id, "mcp_config_ref": {"path": "/local", "sha256": "a" * 64}},
    ):
        with pytest.raises(ValidationError):
            ScheduleExecution(**values)


@pytest.fixture
def connected_schedule(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("stackos.task_delivery.agent_selection_error", lambda *a, **kw: None)
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
        TrackerRepository(session).configure_delivery(
            project_id=project.id,
            delivery={
                role: {"name": role, "instructions": "Synthetic proof only."}
                for role in ("delivery_agent", "review_agent")
            },
        )
        row = (
            ScheduledJobRepository(session)
            .set(
                project_id=project.id,
                kind="proof",
                name="Proof",
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
async def test_ready_connected_schedule_uses_existing_controller(
    connected_schedule, engine, monkeypatch
):
    runtime, _scheduler, clock, connection, exchange, row_id = connected_schedule
    exchange.poll(connection, None)
    runtime.connect(connection_id=connection.id)
    calls = []
    monkeypatch.setattr(
        "stackos.jobs.workflow_schedules.start_delivery", lambda **kw: calls.append(kw)
    )
    registration = runtime.registrations[row_id]
    clock[0] = registration.due
    await runtime._fire(row_id=row_id, registration=registration)
    assert len(calls) == 1
    assert calls[0]["connection_id"] == connection.id
    assert calls[0]["local_invocations"] is exchange
    assert calls[0].get("project_dir") is None
    with Session(engine) as session:
        assert len(session.exec(select(AgentRequest)).all()) == 1
        assert session.get(ScheduledJob, row_id).last_run_status == "started"
