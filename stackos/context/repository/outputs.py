"""Sanitized repository output model shaping."""

from __future__ import annotations

from sqlmodel import col, select

from stackos.db.models import (
    ContextSnapshot,
    Decision,
    Experiment,
    ExperimentObservation,
    ExperimentVariant,
    Learning,
    MetricSnapshot,
    ProjectEvent,
)

from .schema import (
    ContextSnapshotOut,
    DecisionOut,
    ExperimentObservationOut,
    ExperimentOut,
    ExperimentVariantOut,
    LearningOut,
    MetricSnapshotOut,
    ProjectEventOut,
)
from .support import ContextRepositorySupport
from .utils import _required_id, _safe_json, _safe_text


class ContextOutputMixin(ContextRepositorySupport):
    def _event_out(self, row: ProjectEvent) -> ProjectEventOut:
        assert row.id is not None
        return ProjectEventOut(
            id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            source_type=row.source_type,
            source_id=row.source_id,
            event_type=row.event_type,
            title=_safe_text(row.title),
            summary=_safe_text(row.summary),
            tags=list(row.tags_json or []),
            metadata_json=_safe_json(row.metadata_json),
            occurred_at=row.occurred_at,
            created_at=row.created_at,
        )

    def _snapshot_out(self, row: ContextSnapshot) -> ContextSnapshotOut:
        assert row.id is not None
        return ContextSnapshotOut(
            id=row.id,
            project_id=row.project_id,
            run_id=row.run_id,
            name=row.name,
            query_json=_safe_json(row.query_json),
            selected_sources_json=_safe_json(row.selected_sources_json),
            summary_json=_safe_json(row.summary_json),
            metadata_json=_safe_json(row.metadata_json),
            created_at=row.created_at,
        )

    def _learning_out(self, row: Learning) -> LearningOut:
        assert row.id is not None
        return LearningOut(
            id=row.id,
            project_id=row.project_id,
            source_snapshot_id=row.source_snapshot_id,
            supersedes_learning_id=row.supersedes_learning_id,
            statement=_safe_text(row.statement) or "",
            domain=row.domain,
            confidence=row.confidence,
            status=row.status,
            review_state=row.review_state,
            created_by=row.created_by,
            tags=list(row.tags_json or []),
            applies_to_json=_safe_json(row.applies_to_json),
            evidence_json=_safe_json(row.evidence_json),
            metadata_json=_safe_json(row.metadata_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _variant_outs(self, experiment: Experiment) -> list[ExperimentVariantOut]:
        assert experiment.id is not None
        rows = self._s.exec(
            select(ExperimentVariant)
            .where(col(ExperimentVariant.experiment_id) == experiment.id)
            .order_by(col(ExperimentVariant.id).asc())
        ).all()
        return [
            ExperimentVariantOut(
                id=_required_id(row.id),
                experiment_id=row.experiment_id,
                key=row.key,
                name=row.name,
                resources_json=_safe_json(row.resources_json),
                metadata_json=_safe_json(row.metadata_json),
                created_at=row.created_at,
            )
            for row in rows
        ]

    def _experiment_out(self, row: Experiment) -> ExperimentOut:
        assert row.id is not None
        return ExperimentOut(
            id=row.id,
            project_id=row.project_id,
            key=row.key,
            name=row.name,
            domain=row.domain,
            hypothesis=_safe_text(row.hypothesis) or "",
            status=row.status,
            linked_template_ids_json=row.linked_template_ids_json,
            linked_run_ids_json=row.linked_run_ids_json,
            metric_targets_json=_safe_json(row.metric_targets_json),
            decision_policy_json=_safe_json(row.decision_policy_json),
            metadata_json=_safe_json(row.metadata_json),
            variants=self._variant_outs(row),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _observation_out(self, row: ExperimentObservation) -> ExperimentObservationOut:
        assert row.id is not None
        return ExperimentObservationOut(
            id=row.id,
            project_id=row.project_id,
            experiment_id=row.experiment_id,
            run_id=row.run_id,
            variant_key=row.variant_key,
            metrics_json=_safe_json(row.metrics_json),
            summary=_safe_text(row.summary),
            metadata_json=_safe_json(row.metadata_json),
            observed_at=row.observed_at,
            created_at=row.created_at,
        )

    def _metric_out(self, row: MetricSnapshot) -> MetricSnapshotOut:
        assert row.id is not None
        return MetricSnapshotOut(
            id=row.id,
            project_id=row.project_id,
            source_type=row.source_type,
            source_id=row.source_id,
            metric_key=row.metric_key,
            metric_value=row.metric_value,
            dimensions_json=_safe_json(row.dimensions_json),
            metadata_json=_safe_json(row.metadata_json),
            captured_at=row.captured_at,
            created_at=row.created_at,
        )

    def _decision_out(self, row: Decision) -> DecisionOut:
        assert row.id is not None
        return DecisionOut(
            id=row.id,
            project_id=row.project_id,
            experiment_id=row.experiment_id,
            run_id=row.run_id,
            title=_safe_text(row.title),
            decision=_safe_text(row.decision) or "",
            rationale=_safe_text(row.rationale),
            status=row.status,
            decided_by=row.decided_by,
            tags=list(row.tags_json or []),
            evidence_json=_safe_json(row.evidence_json),
            metadata_json=_safe_json(row.metadata_json),
            created_at=row.created_at,
        )
