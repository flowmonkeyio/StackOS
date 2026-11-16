"""Project-owned admission limits; reads never materialize default rows."""

from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from stackos.db.model_tracker import TaskDeliveryCapacity
from stackos.db.models import Project
from stackos.repositories.base import NotFoundError, ValidationError

DEFAULT_TASK_CAPACITY = 3
MAX_TASK_CAPACITY = 32
MAX_CAPACITY_UPDATES = 100


def configured_limit(session: Session, project_id: int) -> int:
    if session.get(Project, project_id) is None:
        raise NotFoundError("Project not found", data={"project_id": project_id})
    value = session.exec(
        select(TaskDeliveryCapacity.concurrent_tasks).where(
            TaskDeliveryCapacity.project_id == project_id
        )
    ).first()
    return value if value is not None else DEFAULT_TASK_CAPACITY


def save_limits(session: Session, updates: dict[int, int]) -> list[int]:
    """Commit only changed rows together. The caller drains after this returns."""
    if not 1 <= len(updates) <= MAX_CAPACITY_UPDATES:
        raise ValidationError("Provide between 1 and 100 project capacity updates")
    for project_id, limit in updates.items():
        if type(project_id) is not int or project_id <= 0:
            raise ValidationError("project_id must be a positive integer")
        if type(limit) is not int or not 1 <= limit <= MAX_TASK_CAPACITY:
            raise ValidationError("max_concurrent_tasks must be an integer from 1 through 32")
    try:
        # Serialize comparison and writes, including independent REST/CLI sessions.
        session.execute(
            text("UPDATE task_delivery_capacity SET concurrent_tasks=concurrent_tasks WHERE 0")
        )
        current = {project_id: configured_limit(session, project_id) for project_id in updates}
        changed = [
            project_id for project_id, limit in updates.items() if current[project_id] != limit
        ]
        for project_id in changed:
            statement = insert(TaskDeliveryCapacity).values(
                project_id=project_id, concurrent_tasks=updates[project_id]
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["project_id"], set_={"concurrent_tasks": updates[project_id]}
                )
            )
        session.commit()
        return changed
    except Exception:
        session.rollback()
        raise
