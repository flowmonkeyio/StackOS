"""Tests for RunRepository — start, finish, abort cascade, reap_stale."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from stackos.db.models import RunKind, RunStatus
from stackos.repositories.base import ConflictError
from stackos.repositories.runs import RunRepository


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def test_start_finish_happy_path(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    assert env.data.status == RunStatus.RUNNING
    out = repo.finish(env.data.id, status="success")
    assert out.data.status == RunStatus.SUCCESS
    assert out.data.ended_at is not None


def test_finish_terminal_run_raises(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    repo.finish(env.data.id, status="success")
    with pytest.raises(ConflictError):
        repo.finish(env.data.id, status="failed")


def test_heartbeat_updates_timestamp(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    out = repo.heartbeat(env.data.id)
    assert out.data.heartbeat_at is not None


def test_heartbeat_missing_run_is_idempotent(session: Session) -> None:
    repo = RunRepository(session)
    out = repo.heartbeat(99999)
    assert out.run_id is None


def test_abort_cascade(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    parent = repo.start(project_id=project_id, kind=RunKind.RUN_PLAN).data
    child1 = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=parent.id).data
    child2 = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=parent.id).data
    grandchild = repo.start(
        project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=child1.id
    ).data
    repo.abort(parent.id, cascade=True)
    assert repo.get(parent.id).status == RunStatus.ABORTED
    assert repo.get(child1.id).status == RunStatus.ABORTED
    assert repo.get(child2.id).status == RunStatus.ABORTED
    assert repo.get(grandchild.id).status == RunStatus.ABORTED


def test_reap_stale_orphans(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    fresh = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    stale = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    # Manually backdate the stale heartbeat past the 5-minute threshold.
    from stackos.db.models import Run

    s_row = session.get(Run, stale.id)
    assert s_row is not None
    s_row.heartbeat_at = _now() - timedelta(minutes=10)
    session.add(s_row)
    session.commit()

    n = repo.reap_stale(stale_after_seconds=300)
    assert n == 1
    assert repo.get(stale.id).status == RunStatus.ABORTED
    assert repo.get(stale.id).error == "daemon-restart-orphan"
    assert repo.get(fresh.id).status == RunStatus.RUNNING


def test_cost_aggregation(session: Session, project_id: int) -> None:
    """Cost rollup sums run_steps.cost_cents per kind for a month."""
    from stackos.db.models import RunStep, RunStepStatus

    repo = RunRepository(session)
    run_a = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    run_b = repo.start(project_id=project_id, kind=RunKind.ACTION).data

    # Insert run_steps with cost.
    step1 = RunStep(
        run_id=run_a.id,
        step_index=0,
        skill_name="outline",
        status=RunStepStatus.SUCCESS,
        cost_cents=120,
    )
    step2 = RunStep(
        run_id=run_b.id,
        step_index=0,
        skill_name="action",
        status=RunStepStatus.SUCCESS,
        cost_cents=45,
    )
    session.add(step1)
    session.add(step2)
    session.commit()

    month = _now().strftime("%Y-%m")
    cost = repo.cost(project_id, month=month)
    assert cost["total_cents"] == 165
    assert cost["by_kind_cents"][RunKind.SKILL_RUN.value] == 120
    assert cost["by_kind_cents"][RunKind.ACTION.value] == 45
