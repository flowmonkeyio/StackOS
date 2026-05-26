"""Learning query, create, and update paths."""

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from stackos.db.models import Learning
from stackos.repositories.base import Envelope, Page, ValidationError

from .schema import ContextPageOut, LearningOut
from .support import ContextRepositorySupport
from .utils import (
    _DEFAULT_FIELDS,
    _FIELD_MAP,
    _has_all_tags,
    _normalise_limit,
    _normalise_tags,
    _required_id,
    _safe_json,
    _safe_text,
    _utcnow,
)


class ContextLearningMixin(ContextRepositorySupport):
    def query_learnings(
        self,
        *,
        project_id: int,
        domain: str | None = None,
        status: str | None = None,
        review_state: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[LearningOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(Learning.project_id) == project_id]
        if domain is not None:
            filters.append(col(Learning.domain) == domain)
        if status is not None:
            filters.append(col(Learning.status) == status)
        if review_state is not None:
            filters.append(col(Learning.review_state) == review_state)
        stmt = select(Learning).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(Learning.id) > after_id)
        tag_filter = _normalise_tags(tags)
        all_rows = list(self._s.exec(stmt.order_by(col(Learning.id).asc())).all())
        filtered = [row for row in all_rows if _has_all_tags(row.tags_json, tag_filter)]
        page_rows = filtered[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(filtered) > n and page_rows else None
        return Page(
            items=[self._learning_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=len(filtered),
        )

    def query_learning_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        domain: str | None = None,
        status: str | None = None,
        review_state: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        page = self.query_learnings(
            project_id=project_id,
            domain=domain,
            status=status,
            review_state=review_state,
            tags=tags,
            limit=limit,
            after_id=after_id,
        )
        requested = fields or list(_DEFAULT_FIELDS["learnings"])
        invalid = sorted(set(requested) - _FIELD_MAP["learnings"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "learnings", "fields": invalid},
            )
        rows = [self._get_learning(project_id, item.id) for item in page.items]
        return ContextPageOut(
            items=[self._item_from_learning(row, requested) for row in rows],
            next_cursor=page.next_cursor,
            total_estimate=page.total_estimate,
        )

    def create_learning(
        self,
        *,
        project_id: int,
        statement: str,
        domain: str | None = None,
        confidence: str = "unknown",
        status: str = "active",
        review_state: str = "proposed",
        created_by: str | None = None,
        tags: list[str] | None = None,
        applies_to_json: dict[str, Any] | None = None,
        evidence_json: dict[str, Any] | None = None,
        source_snapshot_id: int | None = None,
        supersedes_learning_id: int | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[LearningOut]:
        self._require_project(project_id)
        self._require_snapshot(project_id, source_snapshot_id)
        self._require_learning(project_id, supersedes_learning_id)
        row = Learning(
            project_id=project_id,
            statement=_safe_text(statement) or "",
            domain=domain,
            confidence=confidence,
            status=status,
            review_state=review_state,
            created_by=created_by,
            tags_json=_normalise_tags(tags),
            applies_to_json=_safe_json(applies_to_json) if applies_to_json is not None else None,
            evidence_json=_safe_json(evidence_json) if evidence_json is not None else None,
            source_snapshot_id=source_snapshot_id,
            supersedes_learning_id=supersedes_learning_id,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._index_learning(row)
        self._record_event(
            project_id=project_id,
            source_type="learning",
            source_id=row.id,
            event_type="learning.create",
            title="Learning recorded",
            summary=row.statement,
            tags=row.tags_json,
            metadata_json={"review_state": row.review_state, "status": row.status},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._learning_out(row), project_id=project_id)

    def update_learning(
        self,
        *,
        project_id: int,
        learning_id: int,
        statement: str | None = None,
        domain: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        review_state: str | None = None,
        tags: list[str] | None = None,
        applies_to_json: dict[str, Any] | None = None,
        evidence_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[LearningOut]:
        row = self._get_learning(project_id, learning_id)
        if statement is not None:
            row.statement = _safe_text(statement) or ""
        if domain is not None:
            row.domain = domain
        if confidence is not None:
            row.confidence = confidence
        if status is not None:
            row.status = status
        if review_state is not None:
            row.review_state = review_state
        if tags is not None:
            row.tags_json = _normalise_tags(tags)
        if applies_to_json is not None:
            row.applies_to_json = _safe_json(applies_to_json)
        if evidence_json is not None:
            row.evidence_json = _safe_json(evidence_json)
        if metadata_json is not None:
            row.metadata_json = _safe_json(metadata_json)
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.flush()
        self._index_learning(row)
        self._record_event(
            project_id=project_id,
            source_type="learning",
            source_id=row.id,
            event_type="learning.update",
            title="Learning updated",
            summary=row.statement,
            tags=row.tags_json,
            metadata_json={"review_state": row.review_state, "status": row.status},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._learning_out(row), project_id=project_id)
