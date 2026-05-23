"""Tests for IdempotencyKeyRepository — 24h dedup window."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from stackos.db.models import RunKind
from stackos.repositories.base import IdempotencyReplayError
from stackos.repositories.runs import IdempotencyKeyRepository, RunRepository


def _make_run(session: Session, project_id: int) -> int:
    """Helper: start a real run so the FK on idempotency_keys.run_id resolves."""
    env = RunRepository(session).start(project_id=project_id, kind=RunKind.SKILL_RUN)
    rid = env.data.id
    assert rid is not None
    return rid


def test_first_call_creates_key(session: Session, project_id: int) -> None:
    repo = IdempotencyKeyRepository(session)
    rid = _make_run(session, project_id)
    out, created = repo.check_or_create(
        project_id=project_id,
        tool_name="resource.upsert",
        idempotency_key="abc-123",
        run_id=rid,
    )
    assert created is True
    assert out.idempotency_key == "abc-123"
    assert out.run_id == rid


def test_replay_within_24h_raises(session: Session, project_id: int) -> None:
    repo = IdempotencyKeyRepository(session)
    rid = _make_run(session, project_id)
    rid2 = _make_run(session, project_id)
    repo.check_or_create(
        project_id=project_id,
        tool_name="resource.upsert",
        idempotency_key="dup-key",
        run_id=rid,
        response_json={"id": 1},
    )
    with pytest.raises(IdempotencyReplayError) as exc_info:
        repo.check_or_create(
            project_id=project_id,
            tool_name="resource.upsert",
            idempotency_key="dup-key",
            run_id=rid2,
        )
    assert exc_info.value.data["run_id"] == rid
    assert exc_info.value.data["response_json"] == {"id": 1}


def test_stale_key_treated_fresh(session: Session, project_id: int) -> None:
    """A key older than 24h is deleted and the new call succeeds."""
    repo = IdempotencyKeyRepository(session)
    rid = _make_run(session, project_id)
    repo.check_or_create(
        project_id=project_id,
        tool_name="resource.upsert",
        idempotency_key="stale-key",
        run_id=rid,
    )
    # Backdate the row's created_at past 24h.
    from sqlmodel import select

    from stackos.db.models import IdempotencyKey

    row = session.exec(
        select(IdempotencyKey).where(IdempotencyKey.idempotency_key == "stale-key")
    ).first()
    assert row is not None
    row.created_at = row.created_at - timedelta(hours=25)
    session.add(row)
    session.commit()

    # Should be fresh now.
    rid2 = _make_run(session, project_id)
    _, created = repo.check_or_create(
        project_id=project_id,
        tool_name="resource.upsert",
        idempotency_key="stale-key",
        run_id=rid2,
    )
    assert created is True


def test_different_tool_name_does_not_collide(session: Session, project_id: int) -> None:
    repo = IdempotencyKeyRepository(session)
    rid = _make_run(session, project_id)
    rid2 = _make_run(session, project_id)
    repo.check_or_create(
        project_id=project_id,
        tool_name="resource.upsert",
        idempotency_key="same-key",
        run_id=rid,
    )
    _, created = repo.check_or_create(
        project_id=project_id,
        tool_name="action.execute",
        idempotency_key="same-key",
        run_id=rid2,
    )
    assert created is True


def test_update_response_persists(session: Session, project_id: int) -> None:
    repo = IdempotencyKeyRepository(session)
    rid = _make_run(session, project_id)
    rid2 = _make_run(session, project_id)
    repo.check_or_create(
        project_id=project_id,
        tool_name="t",
        idempotency_key="k",
        run_id=rid,
    )
    repo.update_response(
        project_id=project_id,
        tool_name="t",
        idempotency_key="k",
        response_json={"data": "ok"},
    )
    with pytest.raises(IdempotencyReplayError) as exc_info:
        repo.check_or_create(project_id=project_id, tool_name="t", idempotency_key="k", run_id=rid2)
    assert exc_info.value.data["response_json"] == {"data": "ok"}
