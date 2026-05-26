"""Metric snapshot read paths."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import col, select

from stackos.db.models import MetricSnapshot
from stackos.repositories.base import Page

from .schema import MetricSnapshotOut
from .support import ContextRepositorySupport
from .utils import _normalise_limit, _required_id, _scalar_count


class ContextMetricMixin(ContextRepositorySupport):
    def query_metrics(
        self,
        *,
        project_id: int,
        metric_key: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[MetricSnapshotOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(MetricSnapshot.project_id) == project_id]
        if metric_key is not None:
            filters.append(col(MetricSnapshot.metric_key) == metric_key)
        if source_type is not None:
            filters.append(col(MetricSnapshot.source_type) == source_type)
        if source_id is not None:
            filters.append(col(MetricSnapshot.source_id) == source_id)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(MetricSnapshot).where(*filters),
        )
        stmt = select(MetricSnapshot).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(MetricSnapshot.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(MetricSnapshot.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._metric_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )
