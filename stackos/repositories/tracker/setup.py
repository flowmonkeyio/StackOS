# mypy: disable-error-code=attr-defined
"""Tracker setup and default catalogue helpers."""

from __future__ import annotations

from sqlmodel import select

from stackos.db.models import (
    Project,
    TaskTracker,
    TaskTrackerLane,
    TaskTrackerPriority,
)
from stackos.repositories.base import NotFoundError
from stackos.repositories.tracker.schema import (
    TrackerLaneOut,
    TrackerPriorityOut,
    TrackerSummaryOut,
)
from stackos.repositories.tracker.utils import (
    DEFAULT_LANES,
    DEFAULT_PRIORITIES,
    DEFAULT_TRACKER_KEY,
    _utcnow,
)


class TrackerSetupMixin:
    """Tracker setup and default catalogue helpers."""

    def _tracker_or_none(
        self,
        *,
        project_id: int,
        key: str = DEFAULT_TRACKER_KEY,
    ) -> TaskTracker | None:
        self._require_project(project_id)
        return self._s.exec(
            select(TaskTracker).where(TaskTracker.project_id == project_id, TaskTracker.key == key)
        ).first()

    def _empty_tracker_out(self, *, project_id: int) -> TrackerSummaryOut:
        now = _utcnow()
        return TrackerSummaryOut(
            id=0,
            project_id=project_id,
            key=DEFAULT_TRACKER_KEY,
            name="Project task tracker",
            description="Default project tracker for workflow and direct agent work.",
            rev=0,
            created_at=now,
            updated_at=now,
        )

    def _default_lane_out(self) -> list[TrackerLaneOut]:
        return [
            TrackerLaneOut(key=key, label=label, position=position)
            for position, (key, label) in enumerate(DEFAULT_LANES)
        ]

    def _default_priority_out(self) -> list[TrackerPriorityOut]:
        return [
            TrackerPriorityOut(key=key, label=label, rank=rank, position=position)
            for position, (key, label, rank) in enumerate(DEFAULT_PRIORITIES)
        ]

    def ensure_tracker(
        self,
        *,
        project_id: int,
        key: str = DEFAULT_TRACKER_KEY,
    ) -> TaskTracker:
        row = self._tracker_or_none(project_id=project_id, key=key)
        if row is not None:
            return row
        now = _utcnow()
        row = TaskTracker(
            project_id=project_id,
            key=key,
            name="Project task tracker",
            description="Default project tracker for workflow and direct agent work.",
            rev=0,
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        self._s.flush()
        assert row.id is not None
        for position, (lane_key, label) in enumerate(DEFAULT_LANES):
            self._s.add(
                TaskTrackerLane(
                    tracker_id=row.id,
                    key=lane_key,
                    label=label,
                    position=position,
                    created_at=now,
                    updated_at=now,
                )
            )
        for position, (priority_key, label, rank) in enumerate(DEFAULT_PRIORITIES):
            self._s.add(
                TaskTrackerPriority(
                    tracker_id=row.id,
                    key=priority_key,
                    label=label,
                    rank=rank,
                    position=position,
                    created_at=now,
                    updated_at=now,
                )
            )
        self._record_revision(
            row,
            actor="system",
            change_kind="create",
            entity_kind="tracker",
            entity_id=row.id,
            entity_key=row.key,
            summary="Created default project tracker.",
            commit=False,
        )
        self._s.flush()
        return row

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")


__all__ = [
    "TrackerSetupMixin",
]
