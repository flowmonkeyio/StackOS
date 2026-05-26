"""Context snapshot write and read paths."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import col, select

from stackos.db.models import ContextSnapshot, Run
from stackos.repositories.base import Envelope, NotFoundError, Page

from .schema import ContextSnapshotOut
from .support import ContextRepositorySupport
from .utils import _normalise_limit, _required_id, _safe_json, _scalar_count


class ContextSnapshotMixin(ContextRepositorySupport):
    def create_snapshot(
        self,
        *,
        project_id: int,
        run_id: int | None = None,
        name: str | None = None,
        query_json: dict[str, Any] | None = None,
        selected_sources_json: list[dict[str, Any]] | None = None,
        summary_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[ContextSnapshotOut]:
        self._require_project(project_id)
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found in project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        row = ContextSnapshot(
            project_id=project_id,
            run_id=run_id,
            name=name,
            query_json=_safe_json(query_json or {}),
            selected_sources_json=_safe_json(selected_sources_json or []),
            summary_json=_safe_json(summary_json) if summary_json is not None else None,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._record_event(
            project_id=project_id,
            run_id=run_id,
            source_type="context_snapshot",
            source_id=row.id,
            event_type="context.snapshot",
            title=name or "Context snapshot",
            summary=None,
            tags=None,
            metadata_json={"snapshot_id": row.id},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._snapshot_out(row), project_id=project_id)

    def query_snapshots(
        self,
        *,
        project_id: int,
        run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ContextSnapshotOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(ContextSnapshot.project_id) == project_id]
        if run_id is not None:
            filters.append(col(ContextSnapshot.run_id) == run_id)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ContextSnapshot).where(*filters),
        )
        stmt = select(ContextSnapshot).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ContextSnapshot.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(ContextSnapshot.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._snapshot_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )
