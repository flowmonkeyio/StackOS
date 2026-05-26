"""Run and run-plan linkage validation for action calls."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from stackos.db.models import Run, RunPlan, RunPlanStep
from stackos.repositories.base import ConflictError, NotFoundError


class ActionRunScopeMixin:
    """Validate optional run/run-plan linkage before audit writes."""

    def _check_run_scope(
        self,
        *,
        project_id: int,
        run_id: int | None,
        run_plan_id: int | None,
        run_plan_step_id: int | None,
    ) -> None:
        if run_id is not None:
            run = self._s.get(Run, run_id)
            if run is None:
                raise NotFoundError(f"run {run_id} not found")
            if run.project_id is not None and run.project_id != project_id:
                raise NotFoundError(
                    f"run {run_id} not found for project {project_id}",
                    data={"project_id": project_id, "run_id": run_id},
                )
        if run_plan_id is not None:
            plan = self._s.get(RunPlan, run_plan_id)
            if plan is None or plan.project_id != project_id:
                raise NotFoundError(
                    f"run plan {run_plan_id} not found for project {project_id}",
                    data={"project_id": project_id, "run_plan_id": run_plan_id},
                )
            if run_id is not None and plan.run_id != run_id:
                raise ConflictError(
                    "run plan is not linked to the supplied run",
                    data={"run_id": run_id, "run_plan_id": run_plan_id},
                )
        if run_plan_step_id is not None:
            step = self._s.get(RunPlanStep, run_plan_step_id)
            if step is None:
                raise NotFoundError(f"run plan step {run_plan_step_id} not found")
            step_plan = self._s.get(RunPlan, step.run_plan_id)
            if step_plan is None or step_plan.project_id != project_id:
                raise NotFoundError(
                    f"run plan step {run_plan_step_id} not found for project {project_id}",
                    data={"project_id": project_id, "run_plan_step_id": run_plan_step_id},
                )
            if run_plan_id is not None and step.run_plan_id != run_plan_id:
                raise ConflictError(
                    "run plan step is not linked to the supplied run plan",
                    data={"run_plan_id": run_plan_id, "run_plan_step_id": run_plan_step_id},
                )
            if run_id is not None and step_plan.run_id != run_id:
                raise ConflictError(
                    "run plan step is not linked to the supplied run",
                    data={"run_id": run_id, "run_plan_step_id": run_plan_step_id},
                )
