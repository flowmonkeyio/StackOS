"""Canonical event recording and context index maintenance."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import select

from stackos.db.models import ContextIndexEntry, Decision, Experiment, Learning, ProjectEvent
from stackos.events import StackOSEvent, StackOSEventSource
from stackos.repositories.project_events import StackOSEventEmitter

from .support import ContextRepositorySupport
from .utils import _normalise_tags, _safe_json, _safe_text


class ContextIndexMixin(ContextRepositorySupport):
    def _record_event(
        self,
        *,
        project_id: int,
        source_type: str,
        event_type: str,
        source_id: int | None = None,
        run_id: int | None = None,
        title: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> ProjectEvent:
        return StackOSEventEmitter(self._s).emit(
            StackOSEvent(
                project_id=project_id,
                run_id=run_id,
                source=StackOSEventSource(type=source_type, id=source_id),
                event_type=event_type,
                title=title,
                summary=summary,
                tags=tags or [],
                metadata=metadata_json or {},
                schema_version=None,
            )
        )

    def _upsert_index(
        self,
        *,
        project_id: int,
        source_type: str,
        source_id: int | None,
        title: str | None,
        summary: str | None,
        domain: str | None,
        provider_key: str | None,
        status: str | None,
        tags: list[str] | None,
        metadata_json: dict[str, Any] | None,
        occurred_at: datetime,
    ) -> None:
        row = self._s.exec(
            select(ContextIndexEntry).where(
                ContextIndexEntry.project_id == project_id,
                ContextIndexEntry.source_type == source_type,
                ContextIndexEntry.source_id == source_id,
            )
        ).first()
        if row is None:
            row = ContextIndexEntry(
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
            )
        row.title = _safe_text(title)
        row.summary = _safe_text(summary)
        row.domain = domain
        row.provider_key = provider_key
        row.status = status
        row.tags_json = _normalise_tags(tags)
        row.metadata_json = _safe_json(metadata_json) if metadata_json is not None else None
        row.occurred_at = occurred_at
        self._s.add(row)

    def _index_learning(self, row: Learning) -> None:
        self._upsert_index(
            project_id=row.project_id,
            source_type="learning",
            source_id=row.id,
            title=row.statement[:120],
            summary=row.statement,
            domain=row.domain,
            provider_key=None,
            status=row.review_state,
            tags=row.tags_json,
            metadata_json={"confidence": row.confidence, "status": row.status},
            occurred_at=row.updated_at,
        )

    def _index_experiment(self, row: Experiment) -> None:
        self._upsert_index(
            project_id=row.project_id,
            source_type="experiment",
            source_id=row.id,
            title=row.name or row.key,
            summary=row.hypothesis,
            domain=row.domain,
            provider_key=None,
            status=row.status,
            tags=(row.metadata_json or {}).get("tags"),
            metadata_json={"metric_targets_json": row.metric_targets_json},
            occurred_at=row.updated_at,
        )

    def _index_decision(self, row: Decision) -> None:
        self._upsert_index(
            project_id=row.project_id,
            source_type="decision",
            source_id=row.id,
            title=row.title,
            summary=row.decision,
            domain=None,
            provider_key=None,
            status=row.status,
            tags=row.tags_json,
            metadata_json={"experiment_id": row.experiment_id, "run_id": row.run_id},
            occurred_at=row.created_at,
        )
