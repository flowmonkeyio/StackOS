"""Explicit same-project work relationships and task-bound keyset cursors."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, false, func, or_, true
from sqlmodel import Session, col, select

from stackos.db.models import (
    ExecutionContext,
    ExecutionContextLink,
    Run,
    RunPlan,
    TrackerTask,
    TrackerTicket,
    TrackerTicketLink,
)
from stackos.repositories.base import NotFoundError, ValidationError


def object_json(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def validate_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValidationError("Task inspection limit must be between 1 and 200")


def typed_match(column: Any, key: str, values: Any, *, numeric: bool = False) -> Any:
    """No coercion of malformed legacy IDs, bools, or nested/lookalike keys."""
    return and_(
        func.json_type(column, f"$.{key}") == ("integer" if numeric else "text"),
        func.json_extract(column, f"$.{key}").in_(list(values)),
    )


class InspectionScope:
    def __init__(self, session: Session, project_id: int, task_key: str):
        self.project_id = project_id
        task = session.exec(
            select(TrackerTask).where(
                TrackerTask.project_id == project_id, TrackerTask.key == task_key
            )
        ).first()
        if task is None:
            raise NotFoundError("Task not found in this project")
        assert task.id is not None
        self.task_id = task.id
        self.task = task
        self.tickets = {
            t.id: t
            for t in session.exec(
                select(TrackerTicket).where(
                    TrackerTicket.project_id == project_id, TrackerTicket.task_id == task.id
                )
            ).all()
        }
        self.ticket_keys = {t.key for t in self.tickets.values()}
        self.links = list(
            session.exec(
                select(TrackerTicketLink).where(
                    TrackerTicketLink.project_id == project_id,
                    or_(
                        col(TrackerTicketLink.task_id) == task.id,
                        col(TrackerTicketLink.ticket_id).in_(self.tickets),
                    ),
                    or_(
                        col(TrackerTicketLink.task_id).is_(None),
                        col(TrackerTicketLink.task_id) == task.id,
                    ),
                    or_(
                        col(TrackerTicketLink.ticket_id).is_(None),
                        col(TrackerTicketLink.ticket_id).in_(self.tickets),
                    ),
                )
            ).all()
        )
        plan_ids = {t.run_plan_id for t in self.tickets.values()}
        plan_ids.update(link.run_plan_id for link in self.links)
        source_plan = object_json(task.source_json).get("run_plan_id")
        if type(source_plan) is int:
            plan_ids.add(source_plan)
        self.plans = {
            p.id: p
            for p in session.exec(
                select(RunPlan).where(
                    RunPlan.project_id == project_id, col(RunPlan.id).in_(plan_ids - {None})
                )
            ).all()
        }
        run_ids = {t.run_id for t in self.tickets.values()}
        run_ids.update(link.run_id for link in self.links)
        run_ids.update(p.run_id for p in self.plans.values())
        self.runs = {
            r.id
            for r in session.exec(
                select(Run).where(Run.project_id == project_id, col(Run.id).in_(run_ids - {None}))
            ).all()
        }
        self.warnings = []
        if (plan_ids - {None}) - set(self.plans) or (run_ids - {None}) - self.runs:
            self.warnings.append("Missing or foreign run/plan references were excluded.")
        refs = [("task", task_key)] + [("ticket", key) for key in sorted(self.ticket_keys)]
        refs += [("run_plan", str(i)) for i in self.plans]
        refs += [("run", str(i)) for i in self.runs]
        self.context_links = list(
            session.exec(
                select(ExecutionContextLink)
                .join(ExecutionContext, col(ExecutionContext.id) == ExecutionContextLink.context_id)
                .where(
                    ExecutionContextLink.project_id == project_id,
                    ExecutionContext.project_id == project_id,
                    or_(
                        *(
                            and_(
                                col(ExecutionContextLink.link_type) == kind,
                                col(ExecutionContextLink.link_ref) == ref,
                            )
                            for kind, ref in refs
                        )
                    ),
                )
            ).all()
        )
        self.contexts = {
            c.id: c
            for c in session.exec(
                select(ExecutionContext).where(
                    ExecutionContext.project_id == project_id,
                    col(ExecutionContext.id).in_({link.context_id for link in self.context_links}),
                )
            ).all()
        }
        self.provenance_values: dict[str, set[Any]] = {
            "task_key": {task_key},
            "ticket_key": self.ticket_keys,
            "run_plan_id": set(self.plans),
            "run_id": self.runs,
            "context_ref": {c.context_ref for c in self.contexts.values()},
        }

    def provenance_condition(self, column: Any) -> Any:
        return or_(
            *(
                typed_match(column, key, values, numeric=key.endswith("_id"))
                for key, values in self.provenance_values.items()
            )
        )

    def provenance_reasons(self, value: Any) -> list[tuple[str, str, str]]:
        value = object_json(value)
        return [
            (f"provenance.{key}", str(value[key]), f"provenance_json.{key}")
            for key, values in self.provenance_values.items()
            if type(value.get(key)) is (int if key.endswith("_id") else str)
            and value[key] in values
        ]


def decode_cursor(cursor: str | None, scope: list[Any]) -> tuple[datetime, str, int] | None:
    if cursor is None:
        return None
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        if raw["scope"] != scope or raw["v"] != 1:
            raise ValueError
        stamp, kind, row_id = raw["last"]
        if not isinstance(kind, str) or type(row_id) is not int or row_id < 1:
            raise ValueError
        return datetime.fromisoformat(stamp), kind, row_id
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise ValidationError("Invalid task inspection cursor or cursor scope") from error


def encode_cursor(item: Any, scope: list[Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(
            {
                "v": 1,
                "scope": scope,
                "last": [item.occurred_at.isoformat(), item.source_kind, item.source_id],
            },
            separators=(",", ":"),
        ).encode()
    ).decode()


def older_than(
    timestamp: Any, row_id: Any, kind: str, last: tuple[datetime, str, int] | None
) -> Any:
    if last is None:
        return True
    stamp, last_kind, last_id = last
    return or_(
        timestamp < stamp,
        and_(
            timestamp == stamp,
            true() if kind < last_kind else row_id < last_id if kind == last_kind else false(),
        ),
    )


def page_items(items: list[Any], limit: int, scope: list[Any]) -> tuple[list[Any], str | None]:
    items.sort(key=lambda item: (item.occurred_at, item.source_kind, item.source_id), reverse=True)
    page = items[:limit]
    return page, encode_cursor(page[-1], scope) if len(items) > limit else None
