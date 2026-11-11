# mypy: disable-error-code=attr-defined
"""Tracker setup and default catalogue helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlmodel import select

from stackos.db.models import (
    Project,
    TaskTracker,
    TaskTrackerLane,
    TaskTrackerPriority,
)
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
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


def _delivery_role_defaults(defaults: dict[str, Any], role: str) -> dict[str, Any]:
    """Merge saved role fields across legacy and source-mapped configuration."""
    mapped = defaults.get("agents")
    if mapped is not None and not isinstance(mapped, dict):
        raise ValidationError("Task delivery agents must be an object")
    legacy = defaults.get(role) if role in {"delivery_agent", "review_agent"} else None
    selected = (mapped or {}).get(role)
    if any(value is not None and not isinstance(value, dict) for value in (legacy, selected)):
        raise ValidationError(f"Task delivery {role} must be an object")
    return {**(legacy or {}), **(selected or {})}


class TrackerSetupMixin:
    """Tracker setup and default catalogue helpers."""

    def delivery_defaults(self, *, project_id: int) -> dict[str, Any]:
        tracker = self._tracker_or_none(project_id=project_id)
        return deepcopy((tracker.config_json or {}).get("delivery", {})) if tracker else {}

    def adopt_delivery_sources(
        self, *, project_id: int, project_dir: str, sources: dict[str, Any], preview: bool
    ) -> dict[str, Any]:
        """Explicit local setup only: compare before replacing selected editable owners."""
        from pathlib import Path

        from stackos.task_delivery import _project_agent_source, resolve_delivery_settings

        current = self.delivery_defaults(project_id=project_id)
        proposed = deepcopy(current)
        roles = deepcopy(proposed.get("agents") or {})
        differences = {}
        for role, selection in sources.items():
            source = _project_agent_source(
                self._s,
                project_id,
                Path(project_dir).expanduser().resolve(),
                selection["source_ref"],
            )
            if source.digest != selection["expected_digest"]:
                raise ConflictError(
                    "Project agent stale_source",
                    data={"code": "stale_source", "source_ref": selection["source_ref"]},
                )
            if source.fields is None:
                raise ValidationError("Project agent unsupported_setting")
            before = _delivery_role_defaults(current, role)
            differences[role] = {
                key: {"saved": before.get(key), "source": value}
                for key, value in {
                    "name": source.source_ref.native_name,
                    "instructions": source.fields.developer_instructions,
                    **source.native_settings,
                }.items()
                if before.get(key) != value
            }
            after = {**before, "source_ref": source.source_ref.model_dump()}
            after.pop("instructions", None)
            roles[role] = after
            proposed.pop(role, None)
        proposed["agents"] = roles
        # Validate only migrated selections; unrelated hosts/settings are preserved verbatim.
        effective = resolve_delivery_settings(
            self._s,
            project_id=project_id,
            project_dir=project_dir,
            agents={role: roles[role] for role in sources},
            role_keys=set(sources),
        )
        adoption = {
            "preview": preview,
            "differences": differences,
            "delivery": proposed,
            "agents": effective["agents"],
        }
        if preview:
            return {"adoption": adoption}
        # Re-read sources after comparison, before any durable replacement.
        for selection in sources.values():
            source = _project_agent_source(
                self._s,
                project_id,
                Path(project_dir).expanduser().resolve(),
                selection["source_ref"],
            )
            if source.digest != selection["expected_digest"]:
                raise ConflictError("Project agent stale_source")
        result = self.configure_delivery(project_id=project_id, delivery=proposed)
        if self.delivery_defaults(project_id=project_id) != proposed:
            raise ValidationError("Delivery adoption readback failed")
        return {**result.model_dump(mode="json"), "adoption": adoption}

    def configure_delivery(self, *, project_id: int, delivery: dict[str, Any]) -> TrackerSummaryOut:
        """Replace only delivery defaults; preserve unrelated tracker configuration."""
        tracker = self.ensure_tracker(project_id=project_id)
        before = deepcopy(tracker.config_json)
        tracker.config_json = {**(tracker.config_json or {}), "delivery": deepcopy(delivery)}
        self._record_revision(
            tracker,
            actor="local-admin",
            change_kind="update",
            entity_kind="tracker",
            entity_id=tracker.id,
            entity_key=tracker.key,
            summary="Updated project task delivery defaults.",
            before_json={"config_json": before},
            after_json={"config_json": tracker.config_json},
            commit=True,
        )
        return TrackerSummaryOut.model_validate(tracker)

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
