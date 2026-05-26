"""Decision query and record paths."""

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from stackos.db.models import Decision, Run
from stackos.repositories.base import Envelope, NotFoundError, Page, ValidationError

from .schema import ContextPageOut, DecisionOut
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
)


class ContextDecisionMixin(ContextRepositorySupport):
    def query_decisions(
        self,
        *,
        project_id: int,
        experiment_id: int | None = None,
        run_id: int | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[DecisionOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(Decision.project_id) == project_id]
        if experiment_id is not None:
            filters.append(col(Decision.experiment_id) == experiment_id)
        if run_id is not None:
            filters.append(col(Decision.run_id) == run_id)
        if status is not None:
            filters.append(col(Decision.status) == status)
        stmt = select(Decision).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(Decision.id) > after_id)
        tag_filter = _normalise_tags(tags)
        rows = list(self._s.exec(stmt.order_by(col(Decision.id).asc())).all())
        filtered = [row for row in rows if _has_all_tags(row.tags_json, tag_filter)]
        page_rows = filtered[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(filtered) > n and page_rows else None
        return Page(
            items=[self._decision_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=len(filtered),
        )

    def query_decision_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        experiment_id: int | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        page = self.query_decisions(
            project_id=project_id,
            experiment_id=experiment_id,
            status=status,
            tags=tags,
            limit=limit,
            after_id=after_id,
        )
        requested = fields or list(_DEFAULT_FIELDS["decisions"])
        invalid = sorted(set(requested) - _FIELD_MAP["decisions"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "decisions", "fields": invalid},
            )
        rows = [self._get_decision(project_id, item.id) for item in page.items]
        return ContextPageOut(
            items=[self._item_from_decision(row, requested) for row in rows],
            next_cursor=page.next_cursor,
            total_estimate=page.total_estimate,
        )

    def record_decision(
        self,
        *,
        project_id: int,
        decision: str,
        title: str | None = None,
        rationale: str | None = None,
        status: str = "recorded",
        decided_by: str | None = None,
        tags: list[str] | None = None,
        evidence_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        run_id: int | None = None,
        experiment_id: int | None = None,
    ) -> Envelope[DecisionOut]:
        self._require_project(project_id)
        if experiment_id is not None:
            self._get_experiment(project_id, experiment_id)
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found in project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        row = Decision(
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            title=_safe_text(title),
            decision=_safe_text(decision) or "",
            rationale=_safe_text(rationale),
            status=status,
            decided_by=decided_by,
            tags_json=_normalise_tags(tags),
            evidence_json=_safe_json(evidence_json) if evidence_json is not None else None,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._index_decision(row)
        self._record_event(
            project_id=project_id,
            run_id=run_id,
            source_type="decision",
            source_id=row.id,
            event_type="decision.record",
            title=row.title or "Decision recorded",
            summary=row.decision,
            tags=row.tags_json,
            metadata_json={"status": row.status, "experiment_id": experiment_id},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._decision_out(row), project_id=project_id)
