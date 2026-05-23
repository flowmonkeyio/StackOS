"""Runs and idempotency-keys repository module.

Implements the audit-trail layer plus crash-recovery (audit B-13) and
idempotency (audit M-20). Run plans drive ``RunRepository`` rows, while
``RunStepRepository`` / ``RunStepCallRepository`` keep audit grain for
agent and tool activity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from stackos.db.models import (
    RUN_STATUS_TRANSITIONS,
    IdempotencyKey,
    Run,
    RunKind,
    RunStatus,
    RunStep,
    RunStepCall,
    RunStepStatus,
)
from stackos.repositories.base import (
    Envelope,
    IdempotencyReplayError,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
    validate_transition,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Output models.
# ---------------------------------------------------------------------------


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    kind: RunKind
    parent_run_id: int | None
    client_session_id: str | None
    started_at: datetime
    ended_at: datetime | None
    status: RunStatus
    error: str | None
    heartbeat_at: datetime | None
    last_step: str | None
    last_step_at: datetime | None
    metadata_json: dict[str, Any] | None


class RunStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    step_index: int
    skill_name: str
    started_at: datetime | None
    ended_at: datetime | None
    status: RunStepStatus
    input_snapshot_json: dict[str, Any] | None
    output_snapshot_json: dict[str, Any] | None
    error: str | None
    cost_cents: int
    integration_calls_json: dict[str, Any] | None


class RunStepCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_step_id: int
    mcp_tool: str
    request_json: dict[str, Any] | None
    response_json: dict[str, Any] | None
    duration_ms: int | None
    error: str | None
    cost_cents: int


class IdempotencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    tool_name: str
    idempotency_key: str
    run_id: int | None
    response_json: dict[str, Any] | None
    created_at: datetime


# ---------------------------------------------------------------------------
# RunRepository.
# ---------------------------------------------------------------------------


class RunRepository:
    """Top-level run audit + crash-recovery sweep."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def start(
        self,
        *,
        project_id: int | None,
        kind: RunKind,
        parent_run_id: int | None = None,
        client_session_id: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[RunOut]:
        """Insert a new ``status='running'`` row."""
        now = _utcnow()
        row = Run(
            project_id=project_id,
            kind=kind,
            parent_run_id=parent_run_id,
            client_session_id=client_session_id,
            started_at=now,
            heartbeat_at=now,
            status=RunStatus.RUNNING,
            metadata_json=metadata_json,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=project_id,
        )

    def finish(
        self,
        run_id: int,
        *,
        status: Literal["success", "failed", "aborted"] | RunStatus,
        error: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[RunOut]:
        """Move the run to a terminal status.

        ``validate_transition`` enforces ``running → terminal`` only.
        """
        if not isinstance(status, RunStatus):
            status = RunStatus(status)
        row = self._fetch(run_id)
        validate_transition(row.status, status, RUN_STATUS_TRANSITIONS, label="run.status")
        row.status = status
        row.ended_at = _utcnow()
        if error is not None:
            row.error = error
        if metadata_json is not None:
            # Shallow-merge so child state machinery (e.g. cost) survives.
            current = dict(row.metadata_json or {})
            current.update(metadata_json)
            row.metadata_json = current
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=row.project_id,
        )

    def heartbeat(self, run_id: int) -> Envelope[RunOut | None]:
        """Update ``heartbeat_at`` to now.

        Idempotent if the row is missing — we return a stub ``Envelope``
        with a ``None`` ``data`` and ``run_id`` so the M9 daemon's
        heartbeat loop doesn't crash if a run was just reaped between
        the load and the call.
        """
        row = self._s.get(Run, run_id)
        if row is None:
            return Envelope[RunOut | None](data=None, run_id=None)
        row.heartbeat_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope[RunOut | None](
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=row.project_id,
        )

    def abort(self, run_id: int, *, cascade: bool = False) -> Envelope[RunOut]:
        """Move to ``aborted``; optionally cascade through child runs.

        Cascade traverses ``parent_run_id`` recursively for any child
        still in ``running``. Children that already terminated are left
        alone.
        """
        row = self._fetch(run_id)
        if row.status == RunStatus.RUNNING:
            validate_transition(
                row.status, RunStatus.ABORTED, RUN_STATUS_TRANSITIONS, label="run.status"
            )
            row.status = RunStatus.ABORTED
            row.ended_at = _utcnow()
            self._s.add(row)
        if cascade:
            assert row.id is not None
            self._cascade_abort(row.id)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=RunOut.model_validate(row),
            run_id=row.id,
            project_id=row.project_id,
        )

    def list(
        self,
        *,
        project_id: int | None = None,
        kind: RunKind | None = None,
        status: RunStatus | None = None,
        parent_run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[RunOut]:
        """Cursor-paginated list."""
        stmt = select(Run)
        if project_id is not None:
            stmt = stmt.where(Run.project_id == project_id)
        if kind is not None:
            stmt = stmt.where(Run.kind == kind)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        if parent_run_id is not None:
            stmt = stmt.where(Run.parent_run_id == parent_run_id)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=Run.id,
            limit=limit,
            after_id=after_id,
            converter=RunOut.model_validate,
        )

    def get(self, run_id: int) -> RunOut:
        """Fetch one run by id."""
        return RunOut.model_validate(self._fetch(run_id))

    def children(self, parent_run_id: int) -> list[RunOut]:  # type: ignore[valid-type]
        """Direct children of a parent run."""
        rows = self._s.exec(
            select(Run).where(Run.parent_run_id == parent_run_id).order_by(Run.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [RunOut.model_validate(r) for r in rows]

    def cost(self, project_id: int, *, month: str | None = None) -> dict[str, Any]:
        """Sum ``run_steps.cost_cents`` per ``runs.kind`` for the month.

        ``month`` is ``YYYY-MM``; default is the current month per
        ``_utcnow()``. The cost-of-truth is ``run_steps.cost_cents``;
        ``runs.metadata_json.cost`` is denormalised for fast UI display
        but we sum from the steps for accuracy.
        """
        target = month or _utcnow().strftime("%Y-%m")
        # Parse safely — SQLite's strftime is fine but we prefer Python
        # date parsing so a malformed month raises ValidationError, not a
        # cryptic SQL error.
        try:
            year_s, month_s = target.split("-")
            year, month_n = int(year_s), int(month_s)
            if month_n < 1 or month_n > 12:
                raise ValueError
        except (ValueError, IndexError) as exc:
            raise ValidationError(
                f"month must be YYYY-MM (got {target!r})", data={"month": target}
            ) from exc
        period_start = datetime(year, month_n, 1)
        period_end = datetime(year + 1, 1, 1) if month_n == 12 else datetime(year, month_n + 1, 1)

        # Pull all runs in the project + month, then their steps.
        run_rows = self._s.exec(
            select(Run).where(
                Run.project_id == project_id,
                Run.started_at >= period_start,
                Run.started_at < period_end,
            )
        ).all()
        run_ids = [r.id for r in run_rows if r.id is not None]
        cost_by_kind: dict[str, int] = {}
        if run_ids:
            step_rows = self._s.exec(
                select(RunStep).where(RunStep.run_id.in_(run_ids))  # type: ignore[union-attr,attr-defined]
            ).all()
            kind_by_run: dict[int, str] = {r.id: r.kind.value for r in run_rows if r.id is not None}
            for s in step_rows:
                k = kind_by_run.get(s.run_id, "unknown")
                cost_by_kind[k] = cost_by_kind.get(k, 0) + (s.cost_cents or 0)
        return {
            "project_id": project_id,
            "month": target,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "by_kind_cents": cost_by_kind,
            "total_cents": sum(cost_by_kind.values()),
        }

    def reap_stale(self, *, stale_after_seconds: int = 300) -> int:
        """Crash-recovery sweep per audit B-13 / PLAN.md L1366-L1375.

        Finds rows with ``status='running' AND heartbeat_at < now() - delta``
        and transitions them to ``aborted`` with
        ``error='daemon-restart-orphan'``. Returns the number of rows reaped.
        Cascade abort is applied to each reaped run so live-orphan child
        chains are cleaned in the same sweep.
        """
        cutoff = _utcnow() - timedelta(seconds=stale_after_seconds)
        rows = self._s.exec(
            select(Run).where(
                Run.status == RunStatus.RUNNING,
                Run.heartbeat_at < cutoff,  # type: ignore[operator]
            )
        ).all()
        for row in rows:
            row.status = RunStatus.ABORTED
            row.error = "daemon-restart-orphan"
            row.ended_at = _utcnow()
            self._s.add(row)
            assert row.id is not None
            self._cascade_abort(row.id)
        self._s.commit()
        return len(rows)

    # -------- Internal --------

    def _fetch(self, run_id: int) -> Run:
        row = self._s.get(Run, run_id)
        if row is None:
            raise NotFoundError(f"run {run_id} not found")
        return row

    def _cascade_abort(self, root_id: int) -> None:
        """Recursively abort children of ``root_id`` that are still running."""
        frontier = [root_id]
        while frontier:
            current = frontier.pop()
            children = self._s.exec(
                select(Run).where(
                    Run.parent_run_id == current,
                    Run.status == RunStatus.RUNNING,
                )
            ).all()
            for c in children:
                c.status = RunStatus.ABORTED
                c.error = c.error or "parent-aborted"
                c.ended_at = _utcnow()
                self._s.add(c)
                if c.id is not None:
                    frontier.append(c.id)


# ---------------------------------------------------------------------------
# RunStepRepository.
# ---------------------------------------------------------------------------


class RunStepRepository:
    """Per-agent/tool audit grain inside a run."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def insert_step(
        self,
        *,
        run_id: int,
        step_index: int,
        skill_name: str,
        status: RunStepStatus = RunStepStatus.PENDING,
        input_snapshot_json: dict[str, Any] | None = None,
    ) -> Envelope[RunStepOut]:
        """Insert a per-skill step row."""
        row = RunStep(
            run_id=run_id,
            step_index=step_index,
            skill_name=skill_name,
            status=status,
            started_at=_utcnow() if status == RunStepStatus.RUNNING else None,
            input_snapshot_json=input_snapshot_json,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=RunStepOut.model_validate(row), run_id=run_id)

    def advance_step(
        self,
        step_pk: int,
        *,
        status: RunStepStatus,
        output_snapshot_json: dict[str, Any] | None = None,
        error: str | None = None,
        cost_cents: int | None = None,
        integration_calls_json: dict[str, Any] | None = None,
    ) -> Envelope[RunStepOut]:
        """Update a step's status / outputs."""
        row = self._s.get(RunStep, step_pk)
        if row is None:
            raise NotFoundError(f"run step {step_pk} not found")
        now = _utcnow()
        if row.status == RunStepStatus.PENDING and status == RunStepStatus.RUNNING:
            row.started_at = now
        if status in (RunStepStatus.SUCCESS, RunStepStatus.FAILED, RunStepStatus.SKIPPED):
            row.ended_at = now
        row.status = status
        if output_snapshot_json is not None:
            row.output_snapshot_json = output_snapshot_json
        if error is not None:
            row.error = error
        if cost_cents is not None:
            row.cost_cents = cost_cents
        if integration_calls_json is not None:
            row.integration_calls_json = integration_calls_json
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=RunStepOut.model_validate(row), run_id=row.run_id)

    def list_steps(self, run_id: int) -> list[RunStepOut]:
        """All run steps for a run, ordered by step_index."""
        rows = self._s.exec(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.step_index.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [RunStepOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# RunStepCallRepository.
# ---------------------------------------------------------------------------


class RunStepCallRepository:
    """Per-MCP-tool grain inside a per-skill step."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def record_call(
        self,
        *,
        run_step_id: int,
        mcp_tool: str,
        request_json: dict[str, Any] | None = None,
        response_json: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        cost_cents: int = 0,
    ) -> Envelope[RunStepCallOut]:
        """Insert a per-MCP-tool call row."""
        row = RunStepCall(
            run_step_id=run_step_id,
            mcp_tool=mcp_tool,
            request_json=request_json,
            response_json=response_json,
            duration_ms=duration_ms,
            error=error,
            cost_cents=cost_cents,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=RunStepCallOut.model_validate(row))

    def list(self, run_step_id: int) -> list[RunStepCallOut]:
        """All calls for a step in id order."""
        rows = self._s.exec(
            select(RunStepCall)
            .where(RunStepCall.run_step_id == run_step_id)
            .order_by(RunStepCall.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [RunStepCallOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# IdempotencyKeyRepository.
# ---------------------------------------------------------------------------


# 24-hour dedup window per audit M-20 / PLAN.md L722-L729.
IDEMPOTENCY_WINDOW = timedelta(hours=24)


class IdempotencyKeyRepository:
    """24-hour dedup keyed on ``(project_id, tool_name, idempotency_key)``."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def check_or_create(
        self,
        *,
        project_id: int,
        tool_name: str,
        idempotency_key: str,
        run_id: int | None = None,
        response_json: dict[str, Any] | None = None,
    ) -> tuple[IdempotencyOut, bool]:
        """Look up the key; create if absent.

        Returns ``(row, created)``. If ``created=False`` the caller is
        replaying within the 24h window and should return the cached
        ``response_json``. Raises ``IdempotencyReplayError`` to give the
        transport layer a typed signal it can map to a 200 with the
        cached envelope; the existing ``run_id`` is in ``data['run_id']``
        for surfacing.
        """
        existing = self._s.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.project_id == project_id,
                IdempotencyKey.tool_name == tool_name,
                IdempotencyKey.idempotency_key == idempotency_key,
            )
        ).first()
        now = _utcnow()
        if existing is not None:
            if now - existing.created_at <= IDEMPOTENCY_WINDOW:
                # Replay — short-circuit.
                raise IdempotencyReplayError(
                    f"idempotency key {idempotency_key!r} replayed within 24h",
                    data={
                        "idempotency_key_id": existing.id,
                        "run_id": existing.run_id,
                        "tool_name": tool_name,
                        "project_id": project_id,
                        "response_json": existing.response_json,
                    },
                )
            # Stale: delete the old row and treat as fresh.
            self._s.delete(existing)
            self._s.flush()
        row = IdempotencyKey(
            project_id=project_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            run_id=run_id,
            response_json=response_json,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return IdempotencyOut.model_validate(row), True

    def update_response(
        self,
        *,
        project_id: int,
        tool_name: str,
        idempotency_key: str,
        response_json: dict[str, Any],
    ) -> Envelope[IdempotencyOut]:
        """Persist the response body once the original call returns.

        The transport layer calls this *after* the underlying repo method
        succeeds so subsequent replays see the actual response shape.
        """
        row = self._s.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.project_id == project_id,
                IdempotencyKey.tool_name == tool_name,
                IdempotencyKey.idempotency_key == idempotency_key,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                "idempotency key not found",
                data={"tool_name": tool_name, "project_id": project_id},
            )
        row.response_json = response_json
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=IdempotencyOut.model_validate(row), project_id=project_id)


# Helper type aliases re-exported for transport layers.
RunOutType = RunOut
RunStepOutType = RunStepOut
RunStepCallOutType = RunStepCallOut


__all__ = [
    "IdempotencyKeyRepository",
    "IdempotencyOut",
    "RunOut",
    "RunRepository",
    "RunStepCallOut",
    "RunStepCallRepository",
    "RunStepOut",
    "RunStepRepository",
]
