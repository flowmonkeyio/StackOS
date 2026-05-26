"""Internal typing contract shared by context repository mixins."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from stackos.db.models import (
    ContextSnapshot,
    Decision,
    Experiment,
    ExperimentObservation,
    Learning,
    MetricSnapshot,
    ProjectEvent,
)
from stackos.repositories.base import Envelope

from .schema import (
    ContextItemOut,
    ContextSnapshotOut,
    DecisionOut,
    ExperimentObservationOut,
    ExperimentOut,
    ExperimentVariantOut,
    LearningOut,
    MetricSnapshotOut,
    ProjectEventOut,
)


class ContextRepositorySupport:
    """Method signatures mixins rely on; concrete behavior lives in one mixin."""

    _s: Session

    def _require_project(self, project_id: int) -> None:
        raise NotImplementedError

    def _require_snapshot(self, project_id: int, snapshot_id: int | None) -> None:
        raise NotImplementedError

    def _require_learning(self, project_id: int, learning_id: int | None) -> None:
        raise NotImplementedError

    def _get_learning(self, project_id: int, learning_id: int) -> Learning:
        raise NotImplementedError

    def _get_experiment(self, project_id: int, experiment_id: int) -> Experiment:
        raise NotImplementedError

    def _get_decision(self, project_id: int, decision_id: int) -> Decision:
        raise NotImplementedError

    def _source_items(
        self,
        *,
        source: str,
        project_id: int,
        fields: list[str],
        limit: int,
        tags: list[str] | None,
        domain: str | None,
        statuses: list[str] | None,
    ) -> list[ContextItemOut]:
        raise NotImplementedError

    def _item_from_event(self, row: ProjectEvent, fields: list[str]) -> ContextItemOut:
        raise NotImplementedError

    def _item_from_learning(self, row: Learning, fields: list[str]) -> ContextItemOut:
        raise NotImplementedError

    def _item_from_experiment(self, row: Experiment, fields: list[str]) -> ContextItemOut:
        raise NotImplementedError

    def _item_from_decision(self, row: Decision, fields: list[str]) -> ContextItemOut:
        raise NotImplementedError

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
        raise NotImplementedError

    def _index_learning(self, row: Learning) -> None:
        raise NotImplementedError

    def _index_experiment(self, row: Experiment) -> None:
        raise NotImplementedError

    def _index_decision(self, row: Decision) -> None:
        raise NotImplementedError

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
        raise NotImplementedError

    def _event_out(self, row: ProjectEvent) -> ProjectEventOut:
        raise NotImplementedError

    def _snapshot_out(self, row: ContextSnapshot) -> ContextSnapshotOut:
        raise NotImplementedError

    def _learning_out(self, row: Learning) -> LearningOut:
        raise NotImplementedError

    def _variant_outs(self, experiment: Experiment) -> list[ExperimentVariantOut]:
        raise NotImplementedError

    def _experiment_out(self, row: Experiment) -> ExperimentOut:
        raise NotImplementedError

    def _observation_out(self, row: ExperimentObservation) -> ExperimentObservationOut:
        raise NotImplementedError

    def _metric_out(self, row: MetricSnapshot) -> MetricSnapshotOut:
        raise NotImplementedError

    def _decision_out(self, row: Decision) -> DecisionOut:
        raise NotImplementedError

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
        raise NotImplementedError
