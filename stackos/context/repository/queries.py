"""Bounded project context and timeline read paths."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import col, select

from stackos.db.models import ProjectEvent
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.tracker.events import (
    TRACKER_TASK_STATUS_CHANGED,
    TRACKER_TICKET_STATUS_CHANGED,
)

from .schema import ContextItemOut, ContextPageOut, ContextQueryOut, ProjectEventOut
from .support import ContextRepositorySupport
from .utils import (
    _DEFAULT_FIELDS,
    _FIELD_MAP,
    _normalise_limit,
    _normalise_tags,
    _required_id,
    _scalar_count,
)


class ContextQueryMixin(ContextRepositorySupport):
    def query_context(
        self,
        *,
        project_id: int,
        sources: list[str] | None = None,
        fields: list[str] | None = None,
        limit: int | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        statuses: list[str] | None = None,
    ) -> ContextQueryOut:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        selected_sources = sources or ["runs", "learnings", "experiments", "decisions"]
        for source in selected_sources:
            if source not in _FIELD_MAP:
                raise ValidationError("unknown context source", data={"source": source})
        requested_fields = list(fields or ())
        used_fields: set[str] = set()
        for source in selected_sources:
            source_fields = requested_fields or list(_DEFAULT_FIELDS[source])
            invalid = sorted(set(source_fields) - _FIELD_MAP[source])
            if invalid:
                raise ValidationError(
                    "unsupported fields for context source",
                    data={"source": source, "fields": invalid},
                )
            used_fields.update(source_fields)

        items: list[ContextItemOut] = []
        total = 0
        tag_filter = _normalise_tags(tags)
        for source in selected_sources:
            source_items = self._source_items(
                source=source,
                project_id=project_id,
                fields=requested_fields or list(_DEFAULT_FIELDS[source]),
                limit=n,
                tags=tag_filter,
                domain=domain,
                statuses=statuses,
            )
            total += len(source_items)
            items.extend(source_items)

        items.sort(key=lambda item: item.occurred_at or datetime.min, reverse=True)
        bounded = items[:n]
        return ContextQueryOut(
            project_id=project_id,
            sources=selected_sources,
            fields=sorted(used_fields),
            limit=n,
            items=bounded,
            total_estimate=total,
        )

    def timeline(
        self,
        *,
        project_id: int,
        limit: int | None = None,
        after_id: int | None = None,
        event_type: str | None = None,
        descending: bool = False,
    ) -> Page[ProjectEventOut]:
        """Page the project event timeline.

        Default order is ascending by id (oldest first) so the desktop
        notification poller can follow `after_id` watermarks forward. Pass
        ``descending=True`` for a newest-first activity feed; the `after_id`
        cursor then walks backward (older events) one page at a time.
        """
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters: list[Any] = [col(ProjectEvent.project_id) == project_id]
        if event_type is not None:
            filters.append(col(ProjectEvent.event_type) == event_type)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ProjectEvent).where(*filters),
        )
        stmt = select(ProjectEvent).where(*filters)
        if after_id is not None:
            stmt = stmt.where(
                col(ProjectEvent.id) < after_id if descending else col(ProjectEvent.id) > after_id
            )
        order_clause = col(ProjectEvent.id).desc() if descending else col(ProjectEvent.id).asc()
        rows = list(self._s.exec(stmt.order_by(order_clause).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._event_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def tracker_status_timeline(
        self,
        *,
        project_id: int,
        limit: int | None = None,
        after_id: int | None = None,
        task_key: str | None = None,
    ) -> Page[ProjectEventOut]:
        """Page tracker task/ticket status events for realtime UI streams."""
        self._require_project(project_id)
        n = _normalise_limit(limit)
        event_types = [TRACKER_TASK_STATUS_CHANGED, TRACKER_TICKET_STATUS_CHANGED]
        filters: list[Any] = [
            col(ProjectEvent.project_id) == project_id,
            col(ProjectEvent.event_type).in_(event_types),
        ]
        if task_key:
            metadata_json: Any = ProjectEvent.metadata_json
            filters.append(metadata_json["task_key"].as_string() == task_key)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ProjectEvent).where(*filters),
        )
        stmt = select(ProjectEvent).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ProjectEvent.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(ProjectEvent.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._event_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def latest_tracker_status_event_id(
        self,
        *,
        project_id: int,
        task_key: str | None = None,
    ) -> int | None:
        self._require_project(project_id)
        event_types = [TRACKER_TASK_STATUS_CHANGED, TRACKER_TICKET_STATUS_CHANGED]
        filters: list[Any] = [
            col(ProjectEvent.project_id) == project_id,
            col(ProjectEvent.event_type).in_(event_types),
        ]
        if task_key:
            metadata_json: Any = ProjectEvent.metadata_json
            filters.append(metadata_json["task_key"].as_string() == task_key)
        row = self._s.exec(
            select(ProjectEvent).where(*filters).order_by(col(ProjectEvent.id).desc()).limit(1)
        ).first()
        return _required_id(row.id) if row is not None else None

    def query_event_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        requested = fields or list(_DEFAULT_FIELDS["events"])
        invalid = sorted(set(requested) - _FIELD_MAP["events"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "events", "fields": invalid},
            )
        filters = [col(ProjectEvent.project_id) == project_id]
        if event_type is not None:
            filters.append(col(ProjectEvent.event_type) == event_type)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ProjectEvent).where(*filters),
        )
        stmt = select(ProjectEvent).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ProjectEvent.id) > after_id)
        rows = list(self._s.exec(stmt.order_by(col(ProjectEvent.id).asc()).limit(n + 1)).all())
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return ContextPageOut(
            items=[self._item_from_event(row, requested) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )
