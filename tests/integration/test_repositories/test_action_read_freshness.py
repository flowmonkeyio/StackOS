"""Generic workflow read freshness without changing explicit replay semantics."""

import asyncio

import pytest
from sqlmodel import Session, select

from stackos.actions import ActionConnectorRegistry, ActionRepository
from stackos.db.models import Action, ActionCall
from stackos.repositories.run_plans import RunPlanRepository
from tests.integration.test_repositories.test_actions import (
    _credential_ref,
    _FakeConnector,
    _seed_action,
)


@pytest.mark.parametrize("risk", ["read", "write"])
@pytest.mark.parametrize("derived", [False, True])
def test_workflow_reads_are_fresh_but_explicit_replay_and_write_dedupe_remain(
    session: Session, project_id: int, risk: str, derived: bool
) -> None:
    _seed_action(session)
    action = session.exec(select(Action).where(Action.key == "echo.run")).one()
    action.risk_level = risk
    session.add(action)
    session.commit()
    credential = _credential_ref(session, project_id)
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "read-freshness-fixture",
                "title": "Read freshness",
                "steps": [{"id": "observe", "title": "Observe"}],
            },
        )
        .data
    )
    connector = _FakeConnector()
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry)
    arguments = {
        "project_id": project_id,
        "action_ref": "test-actions.echo.run",
        "input_json": {"name": "Synthetic fixture"},
        "credential_ref": credential,
        "run_plan_id": plan.id,
        "run_plan_step_id": plan.steps[0].id,
        "idempotency_key": "same-workflow-action",
        "derived_workflow_idempotency": derived,
        "metadata_json": {"dedupe_source": "workflow-step-action"} if derived else {},
    }
    first = asyncio.run(repo.execute(**arguments)).data
    second = asyncio.run(repo.execute(**arguments)).data
    fresh = risk == "read" and derived
    assert connector.calls == (2 if fresh else 1)
    assert first.replayed is False
    assert second.replayed is not fresh
    assert (first.action_call.id != second.action_call.id) is fresh
    assert len(session.exec(select(ActionCall)).all()) == (2 if fresh else 1)
    if fresh:
        assert connector.saw_idempotency_key is None
        persisted = session.get(ActionCall, first.action_call.id)
        assert persisted is not None and persisted.idempotency_key is None
        assert "dedupe_source" not in persisted.metadata_json
    else:
        assert connector.saw_idempotency_key == "same-workflow-action"
