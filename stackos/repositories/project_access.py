"""Shared persisted project ownership for authenticated server consumers.

This supplements existing project/run grants. Unadapted operations fail closed;
payload JSON is data and can never select an owner or grant authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlmodel import Session, col

from stackos.db.models import (
    ActionCall,
    Artifact,
    ContextSnapshot,
    Experiment,
    Learning,
    Project,
    ResourceRecord,
    Run,
    RunPlan,
    RunStep,
)
from stackos.mcp.errors import ToolNotGrantedError
from stackos.repositories.base import NotFoundError, ValidationError

if TYPE_CHECKING:
    from stackos.auth import UserPrincipal


def user_principal(session: Session) -> UserPrincipal | None:
    from stackos.auth import UserPrincipal

    principal = session.info.get("user_principal")
    if principal is not None and not isinstance(principal, UserPrincipal):
        raise ToolNotGrantedError("verified user principal required")
    if session.info.get("server_environment_id") is not None and (
        principal is None or principal.environment_id != session.info["server_environment_id"]
    ):
        raise ToolNotGrantedError("verified environment principal required")
    return principal


def project_owner_filter(session: Session):
    principal = user_principal(session)
    if principal is None:
        return True  # Existing standalone/local-admin repository usage.
    return (col(Project.owner_user_id) == principal.user_id) & (
        col(Project.environment_id) == principal.environment_id
    )


def require_project(session: Session, project_id: int | None) -> Project:
    principal = user_principal(session)
    row = session.get(Project, project_id) if project_id is not None else None
    if row is None or (
        principal is not None
        and (
            row.owner_user_id != principal.user_id or row.environment_id != principal.environment_id
        )
    ):
        # Foreign and absent identifiers have the same observable failure.
        raise NotFoundError("project or child not found")
    return row


_CHILD_MODELS: dict[str, type[Any]] = {
    "artifact_id": Artifact,
    "replacement_artifact_id": Artifact,
    "superseded_by_artifact_id": Artifact,
    "resource_record_id": ResourceRecord,
    "action_call_id": ActionCall,
    "run_id": Run,
    "parent_run_id": Run,
    "run_plan_id": RunPlan,
    "record_id": ResourceRecord,
    "experiment_id": Experiment,
    "learning_id": Learning,
    "supersedes_learning_id": Learning,
    "source_snapshot_id": ContextSnapshot,
    "snapshot_id": ContextSnapshot,
}


def authorize_project_refs(
    session: Session, values: dict[str, Any], *, run: Run | None = None
) -> int:
    """Resolve all admitted control identifiers, including a guessed child alone.

    Every supplied child must belong to the selected project even when the same
    user owns both projects. Existing run grants remain an additional boundary.
    """
    projects: set[int] = set()
    if values.get("project_id") is not None:
        projects.add(_identifier(values["project_id"]))
    if run is not None:
        if run.project_id is None:
            raise NotFoundError("project or child not found")
        projects.add(run.project_id)
    for key, value in values.items():
        if value is None or key in {
            "project_id",
            "after_id",
            "after",
            "step_id",
            "intent_id",
            "external_id",
        }:
            continue
        model = _CHILD_MODELS.get(key)
        if model is not None:
            row = session.get(model, _identifier(value))
            if row is None or row.project_id is None:
                raise NotFoundError("project or child not found")
            projects.add(row.project_id)
        elif key == "run_step_id":
            step = session.get(RunStep, _identifier(value))
            parent = session.get(Run, step.run_id) if step is not None else None
            if parent is None or parent.project_id is None:
                raise NotFoundError("project or child not found")
            projects.add(parent.project_id)
        elif key.endswith("_id"):
            raise ToolNotGrantedError("identifier requires an adapted owner boundary")
    if not projects:
        raise ToolNotGrantedError("an owned project scope is required")
    for project_id in projects:
        require_project(session, project_id)
    if len(projects) != 1:
        raise NotFoundError("project or child not found")
    return projects.pop()


def _identifier(value: Any) -> int:
    if isinstance(value, bool):
        raise ValidationError("invalid project or child identifier")
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ValidationError("invalid project or child identifier") from None
    if result <= 0:
        raise ValidationError("invalid project or child identifier")
    return result


# Only audited state consumers are enabled here. Account/file/runtime/action
# owners add their own authorization before their routes can be admitted.
_PROJECT_DISCOVERY = {"project.list", "project.portfolio", "project.get", "project.create"}
_PROJECT_OPERATIONS = {
    "artifact.get",
    "artifact.query",
    "artifact.read",
    "artifact.download",
    "artifact.create",
    "artifact.update",
    "artifact.archive",
    "artifact.supersede",
    "actionCall.download",
    "run.get",
    "run.list",
    "run.children",
    "run.cost",
    "run.listSteps",
    "run.listStepCalls",
    "runPlan.get",
    "runPlan.list",
    "resource.get",
    "resource.query",
    "resource.upsert",
    "context.query",
    "context.timeline",
    "context.snapshot",
    "learning.query",
    "learning.create",
    "learning.update",
    "experiment.query",
    "experiment.create",
    "experiment.recordObservation",
    "experiment.recordDecision",
    "decision.query",
    "decision.record",
    "tracker.status",
    "tracker.get",
    "tracker.brief",
    "tracker.why",
    "tracker.history",
    "tracker.changed",
    "tracker.search",
    "tracker.blockers",
    "tracker.next",
    "tracker.ticketCounts",
    "tracker.agentStats",
    "tracker.startDelivery",
}


def authorize_operation(
    session: Session, name: str, values: dict[str, Any], *, run=None
) -> int | None:
    if user_principal(session) is None:
        return None
    from stackos.operations.local_runtime import CONNECTION_OPERATIONS, RUNTIME_OPERATIONS

    runtime_id = session.info.get("local_runtime_connection_id")
    if runtime_id is not None and name not in RUNTIME_OPERATIONS:
        raise ToolNotGrantedError("computer credential cannot access this operation")
    from stackos.auth_providers.repository.access import ACCOUNT_OPERATIONS

    if name in ACCOUNT_OPERATIONS:
        project_id = values.get("project_id")
        for field in ("project_id", "attach_project_id"):
            if values.get(field) is not None:
                require_project(session, values[field])
        # The auth repository checks the Account independently of the project.
        # Keep run/step grants and project-bound test semantics in the dispatcher.
        if run is not None:
            return authorize_project_refs(session, values, run=run)
        return project_id
    if name in CONNECTION_OPERATIONS:
        from stackos.repositories.workspaces import LocalRuntimeConnectionRepository

        project_id = _identifier(values.get("project_id"))
        require_project(session, project_id)
        if values.get("connection_id") is not None:
            LocalRuntimeConnectionRepository(session).owned(project_id, values["connection_id"])
        return project_id
    if name == "tracker.startDelivery":
        from stackos.repositories.workspaces import LocalRuntimeConnectionRepository

        project_id = _identifier(values.get("project_id"))
        if not values.get("connection_id"):
            raise ToolNotGrantedError("server delivery requires an owned connected computer")
        LocalRuntimeConnectionRepository(session).owned(project_id, values["connection_id"])
        return authorize_project_refs(
            session, {k: v for k, v in values.items() if k != "connection_id"}, run=run
        )
    if name in {"operation.list", "operation.describe", "schema.get"}:
        return None  # Public contract metadata; no project state or execution authority.
    if name not in _PROJECT_DISCOVERY | _PROJECT_OPERATIONS:
        raise ToolNotGrantedError("operation requires an adapted owner boundary")
    if name in _PROJECT_DISCOVERY:
        if name == "project.create" and run is not None:
            raise ToolNotGrantedError("project creation requires direct setup without a run token")
        if values.get("project_id") is not None:
            require_project(session, values["project_id"])
        selected_project = None
        if values.get("id_or_slug") is not None:
            from stackos.repositories.projects import ProjectRepository

            selected_project = ProjectRepository(session).get(values["id_or_slug"]).id
            if values.get("project_id") is not None and selected_project != values["project_id"]:
                raise NotFoundError("project or child not found")
        if run is not None:
            project_id = authorize_project_refs(
                session, {"project_id": values.get("project_id")}, run=run
            )
            if selected_project is not None and selected_project != project_id:
                raise NotFoundError("project or child not found")
            return project_id
        return None
    return authorize_project_refs(session, values, run=run)
