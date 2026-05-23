"""Orphan-runs reaper job — sweeps stale ``running`` rows to ``aborted``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from stackos.db.models import (
    Run,
    RunKind,
    RunStatus,
)
from stackos.jobs.runs_reaper import (
    DEFAULT_STALE_AFTER_SECONDS,
    make_session_factory,
    reap_orphaned_runs,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _seed_run(
    *,
    session: Session,
    project_id: int | None = None,
    heartbeat_offset_seconds: int,
    status: RunStatus = RunStatus.RUNNING,
) -> int:
    """Insert a Run row with a heartbeat ``offset_seconds`` ago.

    Returns the new id. Negative offsets push the heartbeat into the
    past (orphaned); positive offsets keep it recent (live).
    """
    now = _utcnow()
    row = Run(
        project_id=project_id,
        kind=RunKind.RUN_PLAN,
        started_at=now - timedelta(seconds=abs(heartbeat_offset_seconds) + 1),
        heartbeat_at=now - timedelta(seconds=heartbeat_offset_seconds),
        status=status,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.id is not None
    return row.id


async def test_reaper_aborts_stale_running_runs(engine: object) -> None:
    """Heartbeat older than 5 min → aborted with daemon-restart-orphan."""
    with Session(engine) as s:  # type: ignore[arg-type]
        stale_id = _seed_run(session=s, heartbeat_offset_seconds=600)  # 10 min
        recent_id = _seed_run(session=s, heartbeat_offset_seconds=10)  # 10 sec

    factory = make_session_factory(engine)  # type: ignore[arg-type]
    summary = await reap_orphaned_runs(session_factory=factory)
    assert summary["reaped"] == 1

    with Session(engine) as s:  # type: ignore[arg-type]
        stale = s.get(Run, stale_id)
        recent = s.get(Run, recent_id)
        assert stale is not None and recent is not None
        assert stale.status == RunStatus.ABORTED
        assert stale.error == "daemon-restart-orphan"
        assert recent.status == RunStatus.RUNNING


async def test_reaper_no_op_when_no_stale_rows(engine: object) -> None:
    """All-recent rows → 0 reaped."""
    with Session(engine) as s:  # type: ignore[arg-type]
        _seed_run(session=s, heartbeat_offset_seconds=10)
        _seed_run(session=s, heartbeat_offset_seconds=30)

    summary = await reap_orphaned_runs(
        session_factory=make_session_factory(engine),  # type: ignore[arg-type]
    )
    assert summary["reaped"] == 0


async def test_reaper_uses_default_threshold() -> None:
    """The reaper's default constant matches PLAN.md L1374 (5 min)."""
    assert DEFAULT_STALE_AFTER_SECONDS == 300


async def test_reaper_cascades_to_children(engine: object) -> None:
    """Orphaned parent + live child → child also aborts (cascade)."""
    with Session(engine) as s:  # type: ignore[arg-type]
        parent_id = _seed_run(session=s, heartbeat_offset_seconds=600)
        child = Run(
            kind=RunKind.RUN_PLAN,
            parent_run_id=parent_id,
            started_at=_utcnow() - timedelta(seconds=120),
            heartbeat_at=_utcnow() - timedelta(seconds=10),  # recent
            status=RunStatus.RUNNING,
        )
        s.add(child)
        s.commit()
        s.refresh(child)
        child_id = child.id
        assert child_id is not None

    await reap_orphaned_runs(
        session_factory=make_session_factory(engine),  # type: ignore[arg-type]
    )
    with Session(engine) as s:  # type: ignore[arg-type]
        parent = s.get(Run, parent_id)
        child_row = s.get(Run, child_id)
        assert parent is not None and child_row is not None
        assert parent.status == RunStatus.ABORTED
        assert child_row.status == RunStatus.ABORTED
