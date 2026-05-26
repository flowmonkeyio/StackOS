"""Experiment, variant, observation, and experiment-decision paths."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import col, select

from stackos.db.models import Experiment, ExperimentObservation, ExperimentVariant, Run
from stackos.repositories.base import Envelope, NotFoundError, Page, ValidationError

from .schema import ContextPageOut, DecisionOut, ExperimentObservationOut, ExperimentOut
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
    _scalar_count,
    _utcnow,
)


class ContextExperimentMixin(ContextRepositorySupport):
    def query_experiments(
        self,
        *,
        project_id: int,
        domain: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ExperimentOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(Experiment.project_id) == project_id]
        if domain is not None:
            filters.append(col(Experiment.domain) == domain)
        if status is not None:
            filters.append(col(Experiment.status) == status)
        stmt = select(Experiment).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(Experiment.id) > after_id)
        tag_filter = _normalise_tags(tags)
        rows = list(self._s.exec(stmt.order_by(col(Experiment.id).asc())).all())
        filtered = [
            row for row in rows if _has_all_tags((row.metadata_json or {}).get("tags"), tag_filter)
        ]
        page_rows = filtered[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(filtered) > n and page_rows else None
        return Page(
            items=[self._experiment_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=len(filtered),
        )

    def query_experiment_context(
        self,
        *,
        project_id: int,
        fields: list[str] | None = None,
        domain: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> ContextPageOut:
        page = self.query_experiments(
            project_id=project_id,
            domain=domain,
            status=status,
            tags=tags,
            limit=limit,
            after_id=after_id,
        )
        requested = fields or list(_DEFAULT_FIELDS["experiments"])
        invalid = sorted(set(requested) - _FIELD_MAP["experiments"])
        if invalid:
            raise ValidationError(
                "unsupported fields for context source",
                data={"source": "experiments", "fields": invalid},
            )
        rows = [self._get_experiment(project_id, item.id) for item in page.items]
        return ContextPageOut(
            items=[self._item_from_experiment(row, requested) for row in rows],
            next_cursor=page.next_cursor,
            total_estimate=page.total_estimate,
        )

    def create_experiment(
        self,
        *,
        project_id: int,
        hypothesis: str,
        key: str | None = None,
        name: str | None = None,
        domain: str | None = None,
        status: str = "planned",
        linked_template_ids_json: list[str] | None = None,
        linked_run_ids_json: list[int] | None = None,
        metric_targets_json: dict[str, Any] | None = None,
        decision_policy_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        variants: list[dict[str, Any]] | None = None,
    ) -> Envelope[ExperimentOut]:
        self._require_project(project_id)
        row = Experiment(
            project_id=project_id,
            key=key,
            name=name,
            domain=domain,
            hypothesis=_safe_text(hypothesis) or "",
            status=status,
            linked_template_ids_json=linked_template_ids_json,
            linked_run_ids_json=linked_run_ids_json,
            metric_targets_json=_safe_json(metric_targets_json)
            if metric_targets_json is not None
            else None,
            decision_policy_json=_safe_json(decision_policy_json)
            if decision_policy_json is not None
            else None,
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        for variant in variants or []:
            if not variant.get("key"):
                raise ValidationError("experiment variant key is required")
            self._s.add(
                ExperimentVariant(
                    experiment_id=_required_id(row.id),
                    key=str(variant["key"]),
                    name=variant.get("name"),
                    resources_json=_safe_json(variant.get("resources_json"))
                    if variant.get("resources_json") is not None
                    else None,
                    metadata_json=_safe_json(variant.get("metadata_json"))
                    if variant.get("metadata_json") is not None
                    else None,
                )
            )
        self._index_experiment(row)
        self._record_event(
            project_id=project_id,
            source_type="experiment",
            source_id=row.id,
            event_type="experiment.create",
            title=row.name or "Experiment created",
            summary=row.hypothesis,
            tags=(row.metadata_json or {}).get("tags"),
            metadata_json={"status": row.status},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._experiment_out(row), project_id=project_id)

    def record_observation(
        self,
        *,
        project_id: int,
        experiment_id: int,
        metrics_json: dict[str, Any],
        variant_key: str | None = None,
        run_id: int | None = None,
        summary: str | None = None,
        observed_at: datetime | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[ExperimentObservationOut]:
        experiment = self._get_experiment(project_id, experiment_id)
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None or run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found in project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        row = ExperimentObservation(
            project_id=project_id,
            experiment_id=experiment_id,
            run_id=run_id,
            variant_key=variant_key,
            metrics_json=_safe_json(metrics_json),
            summary=_safe_text(summary),
            observed_at=observed_at or _utcnow(),
            metadata_json=_safe_json(metadata_json) if metadata_json is not None else None,
        )
        self._s.add(row)
        self._s.flush()
        self._record_event(
            project_id=project_id,
            run_id=run_id,
            source_type="experiment_observation",
            source_id=row.id,
            event_type="experiment.recordObservation",
            title=f"Observation for {experiment.name or experiment.key or experiment.id}",
            summary=row.summary,
            tags=(experiment.metadata_json or {}).get("tags"),
            metadata_json={"experiment_id": experiment_id, "variant_key": variant_key},
        )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._observation_out(row), project_id=project_id)

    def query_observations(
        self,
        *,
        project_id: int,
        experiment_id: int | None = None,
        run_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ExperimentObservationOut]:
        self._require_project(project_id)
        n = _normalise_limit(limit)
        filters = [col(ExperimentObservation.project_id) == project_id]
        if experiment_id is not None:
            filters.append(col(ExperimentObservation.experiment_id) == experiment_id)
        if run_id is not None:
            filters.append(col(ExperimentObservation.run_id) == run_id)
        total = _scalar_count(
            self._s,
            sa_select(func.count()).select_from(ExperimentObservation).where(*filters),
        )
        stmt = select(ExperimentObservation).where(*filters)
        if after_id is not None:
            stmt = stmt.where(col(ExperimentObservation.id) > after_id)
        rows = list(
            self._s.exec(stmt.order_by(col(ExperimentObservation.id).asc()).limit(n + 1)).all()
        )
        page_rows = rows[:n]
        next_cursor = _required_id(page_rows[-1].id) if len(rows) > n and page_rows else None
        return Page(
            items=[self._observation_out(row) for row in page_rows],
            next_cursor=next_cursor,
            total_estimate=total,
        )

    def record_experiment_decision(
        self,
        *,
        project_id: int,
        experiment_id: int,
        decision: str,
        title: str | None = None,
        rationale: str | None = None,
        status: str = "recorded",
        decided_by: str | None = None,
        tags: list[str] | None = None,
        evidence_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        run_id: int | None = None,
        experiment_status: str | None = None,
    ) -> Envelope[DecisionOut]:
        experiment = self._get_experiment(project_id, experiment_id)
        if experiment_status is not None:
            experiment.status = experiment_status
            experiment.updated_at = _utcnow()
            self._s.add(experiment)
        return self.record_decision(
            project_id=project_id,
            decision=decision,
            title=title or f"Decision for {experiment.name or experiment.key or experiment.id}",
            rationale=rationale,
            status=status,
            decided_by=decided_by,
            tags=tags,
            evidence_json=evidence_json,
            metadata_json=metadata_json,
            run_id=run_id,
            experiment_id=experiment_id,
        )
