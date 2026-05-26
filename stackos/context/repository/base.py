"""Session ownership and row lookup guards for context repositories."""

from __future__ import annotations

from sqlmodel import Session

from stackos.db.models import ContextSnapshot, Decision, Experiment, Learning, Project
from stackos.repositories.base import NotFoundError

from .support import ContextRepositorySupport


class ContextRepositoryBase(ContextRepositorySupport):
    def __init__(self, session: Session) -> None:
        self._s = session

    def _get_learning(self, project_id: int, learning_id: int) -> Learning:
        row = self._s.get(Learning, learning_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"learning {learning_id} not found in project {project_id}",
                data={"project_id": project_id, "learning_id": learning_id},
            )
        return row

    def _get_experiment(self, project_id: int, experiment_id: int) -> Experiment:
        row = self._s.get(Experiment, experiment_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"experiment {experiment_id} not found in project {project_id}",
                data={"project_id": project_id, "experiment_id": experiment_id},
            )
        return row

    def _get_decision(self, project_id: int, decision_id: int) -> Decision:
        row = self._s.get(Decision, decision_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"decision {decision_id} not found in project {project_id}",
                data={"project_id": project_id, "decision_id": decision_id},
            )
        return row

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _require_snapshot(self, project_id: int, snapshot_id: int | None) -> None:
        if snapshot_id is None:
            return
        row = self._s.get(ContextSnapshot, snapshot_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"context snapshot {snapshot_id} not found in project {project_id}",
                data={"project_id": project_id, "snapshot_id": snapshot_id},
            )

    def _require_learning(self, project_id: int, learning_id: int | None) -> None:
        if learning_id is None:
            return
        self._get_learning(project_id, learning_id)
