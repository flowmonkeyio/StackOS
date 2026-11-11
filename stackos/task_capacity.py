"""The single local-data-directory task limit; no project or account quota."""

from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from stackos.db.model_tracker import TaskDeliveryCapacity
from stackos.repositories.base import ValidationError

DEFAULT_TASK_CAPACITY = 3
MAX_TASK_CAPACITY = 32


def configured_limit(session: Session) -> int:
    value = session.exec(select(TaskDeliveryCapacity.concurrent_tasks)).first()
    return value if value is not None else DEFAULT_TASK_CAPACITY


def save_limit(session: Session, concurrent_tasks: int) -> None:
    if type(concurrent_tasks) is not int or not 1 <= concurrent_tasks <= MAX_TASK_CAPACITY:
        raise ValidationError("concurrent_tasks must be an integer from 1 through 32")
    try:
        statement = insert(TaskDeliveryCapacity).values(id=1, concurrent_tasks=concurrent_tasks)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["id"], set_={"concurrent_tasks": concurrent_tasks}
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
