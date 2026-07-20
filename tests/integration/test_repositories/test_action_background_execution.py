"""Background action lifecycle integration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from stackos.actions import (
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionRepository,
)
from stackos.db.connection import make_engine
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    IdempotencyKey,
    Plugin,
    PluginSource,
)
from stackos.repositories.projects import ProjectRepository


class _GatedConnector:
    key = "test.gated"

    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finishing = asyncio.Event()
        self.execution_sessions: list[object] = []

    def validate(self, request: ActionConnectorRequest) -> list[object]:
        del request
        return []

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        del request
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        self.calls += 1
        self.execution_sessions.append(request.session)
        self.started.set()
        await self.release.wait()
        self.finishing.set()
        return ActionConnectorResult(output_json={"transferred": True})


def _database(tmp_path: Path) -> tuple[Engine, int]:
    engine = make_engine(tmp_path / "background-actions.sqlite")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        project = (
            ProjectRepository(session)
            .create(
                slug="background-actions",
                name="Background Actions",
                domain="background-actions.example.test",
                locale="en-US",
            )
            .data
        )
        assert project.id is not None
        return engine, project.id


def _seed_action(session: Session, *, background: bool) -> str:
    plugin = Plugin(
        slug="background-test",
        name="Background Test",
        version="0.1.0",
        source=PluginSource.PROJECT,
        manifest_json={},
    )
    session.add(plugin)
    session.flush()
    assert plugin.id is not None
    config: dict[str, object] = {
        "schema_version": "stackos.action.v1",
        "connector": _GatedConnector.key,
        "operation": "file.transfer",
        "requires_credential": False,
    }
    if background:
        config["execution_mode"] = "background"
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=None,
            key="file.transfer",
            name="Transfer file",
            description="Deterministic background execution fixture.",
            capability_key=None,
            risk_level="write",
            input_schema_json={
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
            },
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json=config,
        )
    )
    session.commit()
    return "background-test.file.transfer"


def _registry(connector: _GatedConnector) -> ActionConnectorRegistry:
    registry = ActionConnectorRegistry()
    registry.register(connector)
    return registry


@pytest.mark.asyncio
async def test_background_execute_returns_running_receipt_then_finalizes_same_call(
    tmp_path: Path,
) -> None:
    engine, project_id = _database(tmp_path)
    connector = _GatedConnector()
    registry = _registry(connector)
    with Session(engine) as submission_session:
        action_ref = _seed_action(submission_session, background=True)
        submission = asyncio.create_task(
            ActionRepository(submission_session, connectors=registry).execute(
                project_id=project_id,
                action_ref=action_ref,
                input_json={"path": "/remote/file.bin"},
                idempotency_key="background-transfer",
            )
        )
        await connector.started.wait()
        returned_before_release = submission.done()
        if not returned_before_release:
            connector.release.set()
            await submission
        assert returned_before_release, "background execute waited for the connector"

        accepted = (await submission).data
        action_call_id = accepted.action_call.id
        assert accepted.action_call.status == ActionCallStatus.RUNNING
        assert accepted.poll_operation == "actionCall.get"
        assert accepted.poll_arguments == {"action_call_id": action_call_id}
        assert accepted.next_poll_after_ms > 0

        with Session(engine) as observer:
            running = observer.get(ActionCall, action_call_id)
            assert running is not None
            assert running.status == ActionCallStatus.RUNNING
            assert running.completed_at is None

        assert len(connector.execution_sessions) == 1
        assert connector.execution_sessions[0] is not None
        assert connector.execution_sessions[0] is not submission_session
        connector.release.set()
        await connector.finishing.wait()

    with Session(engine) as observer:
        completed = observer.get(ActionCall, action_call_id)
        assert completed is not None
        assert completed.status == ActionCallStatus.SUCCESS
        assert completed.response_json == {"transferred": True}
        assert completed.completed_at is not None
    engine.dispose()


@pytest.mark.asyncio
async def test_background_same_idempotency_key_has_one_winner_across_sessions(
    tmp_path: Path,
) -> None:
    engine, project_id = _database(tmp_path)
    connector = _GatedConnector()
    registry = _registry(connector)
    with Session(engine) as first_session, Session(engine) as second_session:
        action_ref = _seed_action(first_session, background=True)
        first = asyncio.create_task(
            ActionRepository(first_session, connectors=registry).execute(
                project_id=project_id,
                action_ref=action_ref,
                input_json={"path": "/remote/file.bin"},
                idempotency_key="shared-transfer",
            )
        )
        second = asyncio.create_task(
            ActionRepository(second_session, connectors=registry).execute(
                project_id=project_id,
                action_ref=action_ref,
                input_json={"path": "/remote/file.bin"},
                idempotency_key="shared-transfer",
            )
        )
        await connector.started.wait()
        returned_before_release = first.done() and second.done()
        if not returned_before_release:
            connector.release.set()
            await asyncio.gather(first, second)
        assert returned_before_release, "idempotent background submissions did not return"

        first_out, second_out = (await first).data, (await second).data
        assert first_out.action_call.id == second_out.action_call.id
        assert first_out.action_call.status == ActionCallStatus.RUNNING
        assert second_out.action_call.status == ActionCallStatus.RUNNING

        with Session(engine) as observer:
            assert len(observer.exec(select(ActionCall)).all()) == 1
            assert len(observer.exec(select(IdempotencyKey)).all()) == 1

        connector.release.set()
        await connector.finishing.wait()

    assert connector.calls == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_failure_wins_over_late_background_completion(
    tmp_path: Path,
) -> None:
    engine, project_id = _database(tmp_path)
    connector = _GatedConnector()
    registry = _registry(connector)
    with Session(engine) as submission_session:
        action_ref = _seed_action(submission_session, background=True)
        submission = asyncio.create_task(
            ActionRepository(submission_session, connectors=registry).execute(
                project_id=project_id,
                action_ref=action_ref,
                input_json={"path": "/remote/file.bin"},
                idempotency_key="orphaned-transfer",
            )
        )
        await connector.started.wait()
        returned_before_release = submission.done()
        if not returned_before_release:
            connector.release.set()
            await submission
        assert returned_before_release, "background execute waited for the connector"
        action_call_id = (await submission).data.action_call.id

        with Session(engine) as reconciler_session:
            reconciled = ActionRepository(reconciler_session).reconcile_running_calls()
        assert reconciled == 1

        with Session(engine) as observer:
            reconciled_call = observer.get(ActionCall, action_call_id)
            assert reconciled_call is not None
            assert reconciled_call.status == ActionCallStatus.FAILED
            assert reconciled_call.response_json is not None
            assert reconciled_call.response_json["outcome_unknown"] is True
            assert reconciled_call.response_json["retry_safe"] is False

        connector.release.set()
        await connector.finishing.wait()

    with Session(engine) as observer:
        final_call = observer.get(ActionCall, action_call_id)
        assert final_call is not None
        assert final_call.status == ActionCallStatus.FAILED
        assert final_call.response_json is not None
        assert final_call.response_json["outcome_unknown"] is True
        assert final_call.response_json["retry_safe"] is False
    engine.dispose()
