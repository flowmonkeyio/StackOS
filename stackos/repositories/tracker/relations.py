# mypy: disable-error-code=attr-defined
"""Tracker dependency, reference, and link helpers."""

from __future__ import annotations

from typing import Any

from sqlmodel import (
    col,
    select,
)

from stackos.artifacts import (
    redact_secret_text,
    redact_secrets,
)
from stackos.db.models import (
    TaskTracker,
    TrackerLinkKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketDependency,
    TrackerTicketLink,
    TrackerTicketReference,
)
from stackos.repositories.base import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from stackos.repositories.tracker.schema import (
    TrackerDependencyOut,
    TrackerLinkOut,
    TrackerOwnerItemOut,
)
from stackos.repositories.tracker.utils import (
    ACTIVATES_DEPENDENCY_TYPE,
    BLOCKS_DEPENDENCY_TYPE,
    _clean_text,
    _required_id,
    _utcnow,
)


class TrackerRelationsMixin:
    """Tracker dependency, reference, and link helpers."""

    def resolve_owner_item(
        self,
        *,
        project_id: int,
        canonical_item_id: str | None = None,
        alias_ref: str | None = None,
    ) -> TrackerOwnerItemOut:
        """Resolve one native task/ticket or exact existing external reference.

        Providers register opaque references through existing tracker records;
        this boundary performs no provider-specific parsing or title matching.
        Resolution reads state without creating a tracker or claiming ownership.
        """
        if (canonical_item_id is None) == (alias_ref is None):
            raise ValidationError("provide exactly one canonical_item_id or alias_ref")
        if canonical_item_id is not None:
            kind, _, raw_id = canonical_item_id.partition(":")
            if (
                kind not in {"task", "ticket"}
                or not raw_id.isascii()
                or not raw_id.isdecimal()
                or raw_id.startswith("0")
                or len(raw_id) > 19
                or int(raw_id) > 2**63 - 1
            ):
                raise ValidationError("canonical_item_id must be task:<id> or ticket:<id>")
            candidates = {(kind, int(raw_id))}
        else:
            if not alias_ref or len(alias_ref) > 1000:
                raise ValidationError("alias_ref must be a nonempty registered reference")
            references = self._s.exec(
                select(TrackerTicketReference.ticket_id)
                .where(
                    TrackerTicketReference.project_id == project_id,
                    TrackerTicketReference.ref == alias_ref,
                )
                .distinct()
            ).all()
            candidates = {("ticket", item_id) for item_id in references}
            links = self._s.exec(
                select(TrackerTicketLink.ticket_id, TrackerTicketLink.task_id)
                .where(
                    TrackerTicketLink.project_id == project_id,
                    TrackerTicketLink.link_kind == TrackerLinkKind.EXTERNAL,
                    TrackerTicketLink.ref == alias_ref,
                )
                .distinct()
            ).all()
            for ticket_id, task_id in links:
                if ticket_id is not None:
                    candidates.add(("ticket", ticket_id))
                elif task_id is not None:
                    candidates.add(("task", task_id))

        if not candidates:
            raise NotFoundError("ownership alias not found in project")
        if len(candidates) != 1:
            raise ConflictError(
                "ownership alias is ambiguous", data={"match_count": len(candidates)}
            )
        kind, item_id = candidates.pop()
        row = (
            self._s.get(TrackerTicket, item_id, populate_existing=True)
            if kind == "ticket"
            else self._s.get(TrackerTask, item_id, populate_existing=True)
        )
        if row is None or row.project_id != project_id:
            raise NotFoundError("ownership item not found in project")
        return TrackerOwnerItemOut(
            project_id=project_id,
            item_kind="ticket" if kind == "ticket" else "task",
            item_id=item_id,
            canonical_item_id=f"{kind}:{item_id}",
        )

    def require_owner_execution_binding(
        self,
        *,
        project_id: int,
        canonical_item_id: str,
        run_plan_id: int,
        run_plan_step_id: int,
    ) -> TrackerOwnerItemOut:
        item = self.resolve_owner_item(project_id=project_id, canonical_item_id=canonical_item_id)
        if item.item_kind == "ticket":
            native = self._s.get(TrackerTicket, item.item_id, populate_existing=True)
            matches = (
                native is not None
                and native.run_plan_id == run_plan_id
                and native.run_plan_step_id == run_plan_step_id
            )
        else:
            task = self._s.get(TrackerTask, item.item_id, populate_existing=True)
            binding = task.source_json if task is not None else None
            matches = (
                isinstance(binding, dict)
                and type(binding.get("run_plan_id")) is int
                and type(binding.get("run_plan_step_id")) is int
                and binding.get("run_plan_id") == run_plan_id
                and binding.get("run_plan_step_id") == run_plan_step_id
            )
        if not matches:
            raise ConflictError(
                "canonical work item is not bound to the current workflow step",
                data={
                    "canonical_item_id": canonical_item_id,
                    "run_plan_id": run_plan_id,
                    "run_plan_step_id": run_plan_step_id,
                },
            )
        return item

    def _dependency_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketDependency]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    TrackerTicketDependency.ticket_id == ticket_id
                )
            )
        )

    def _dependent_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketDependency]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    TrackerTicketDependency.depends_on_ticket_id == ticket_id
                )
            )
        )

    def _reference_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketReference]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(
                select(TrackerTicketReference).where(TrackerTicketReference.ticket_id == ticket_id)
            )
        )

    def _link_rows_for_ticket(self, ticket_id: int | None) -> list[TrackerTicketLink]:
        if ticket_id is None:
            return []
        return list(
            self._s.exec(select(TrackerTicketLink).where(TrackerTicketLink.ticket_id == ticket_id))
        )

    def _link_out_for_scope(
        self,
        tracker_id: int | None,
        task_ids: set[int | None],
        ticket_ids: set[int | None],
    ) -> list[TrackerLinkOut]:
        tracker_id = _required_id(tracker_id, "tracker")
        rows = list(
            self._s.exec(
                select(TrackerTicketLink).where(TrackerTicketLink.tracker_id == tracker_id)
            )
        )
        return [
            TrackerLinkOut.model_validate(row)
            for row in rows
            if row.task_id in task_ids or row.ticket_id in ticket_ids
        ]

    def _dependency_out_for_tickets(
        self,
        tickets: list[TrackerTicket],
    ) -> list[TrackerDependencyOut]:
        ids = {ticket.id for ticket in tickets}
        if not ids:
            return []
        rows = list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    col(TrackerTicketDependency.ticket_id).in_(ids)
                )
            )
        )
        by_id = {ticket.id: ticket.key for ticket in tickets}
        for dependency in rows:
            if dependency.depends_on_ticket_id not in by_id:
                dep_ticket = self._s.get(TrackerTicket, dependency.depends_on_ticket_id)
                if dep_ticket is not None:
                    by_id[dep_ticket.id] = dep_ticket.key
        return [
            TrackerDependencyOut(
                id=row.id or 0,
                ticket_key=by_id.get(row.ticket_id, ""),
                depends_on_ticket_key=by_id.get(row.depends_on_ticket_id, ""),
                dependency_type=row.dependency_type,
                metadata_json=row.metadata_json,
            )
            for row in rows
        ]

    def _add_dependency(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        dependency_ticket: TrackerTicket,
    ) -> None:
        if ticket.id == dependency_ticket.id:
            raise ValidationError("ticket cannot depend on itself", data={"ticket_key": ticket.key})
        dependency_type = (
            ACTIVATES_DEPENDENCY_TYPE
            if ticket.parent_ticket_id == dependency_ticket.id
            and ticket.run_plan_id is not None
            and ticket.run_plan_id == dependency_ticket.run_plan_id
            and ticket.run_plan_step_id is not None
            and ticket.run_plan_step_id == dependency_ticket.run_plan_step_id
            and dependency_ticket.parent_ticket_id is None
            else BLOCKS_DEPENDENCY_TYPE
        )
        existing = self._s.exec(
            select(TrackerTicketDependency).where(
                TrackerTicketDependency.ticket_id == ticket.id,
                TrackerTicketDependency.depends_on_ticket_id == dependency_ticket.id,
            )
        ).first()
        if existing is not None:
            if existing.dependency_type != dependency_type:
                existing.dependency_type = dependency_type
                self._s.add(existing)
            return
        if self._would_create_dependency_cycle(ticket, dependency_ticket):
            raise ConflictError(
                "ticket dependency would create a cycle",
                data={"ticket_key": ticket.key, "depends_on": dependency_ticket.key},
            )
        self._s.add(
            TrackerTicketDependency(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                ticket_id=ticket.id,
                depends_on_ticket_id=dependency_ticket.id,
                dependency_type=dependency_type,
                created_at=_utcnow(),
            )
        )

    def _add_reference(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        reference: dict[str, Any],
    ) -> None:
        ref_type = str(reference.get("ref_type") or reference.get("type") or "note")
        ref = str(reference.get("ref") or "")
        if not ref:
            raise ValidationError("ticket reference ref is required")
        self._s.add(
            TrackerTicketReference(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                ticket_id=ticket.id,
                ref_type=ref_type,
                ref=redact_secret_text(ref),
                title=_clean_text(reference.get("title")) or None,
                metadata_json=redact_secrets(reference.get("metadata_json"))
                if isinstance(reference.get("metadata_json"), dict)
                else None,
                created_at=_utcnow(),
            )
        )

    def _add_link(
        self,
        tracker: TaskTracker,
        *,
        link_kind: TrackerLinkKind,
        task_id: int | None = None,
        ticket_id: int | None = None,
        ref: str | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        run_id: int | None = None,
        agent_request_id: int | None = None,
        resource_record_id: int | None = None,
        artifact_id: int | None = None,
        action_call_id: int | None = None,
        title: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        existing = self._s.exec(
            select(TrackerTicketLink).where(
                TrackerTicketLink.tracker_id == tracker.id,
                TrackerTicketLink.task_id == task_id,
                TrackerTicketLink.ticket_id == ticket_id,
                TrackerTicketLink.link_kind == link_kind,
                TrackerTicketLink.ref == ref,
                TrackerTicketLink.run_plan_id == run_plan_id,
                TrackerTicketLink.run_plan_step_id == run_plan_step_id,
                TrackerTicketLink.agent_request_id == agent_request_id,
            )
        ).first()
        if existing is not None:
            return
        self._s.add(
            TrackerTicketLink(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                task_id=task_id,
                ticket_id=ticket_id,
                link_kind=link_kind,
                ref=redact_secret_text(ref) if ref else None,
                run_plan_id=run_plan_id,
                run_plan_step_id=run_plan_step_id,
                run_id=run_id,
                agent_request_id=agent_request_id,
                resource_record_id=resource_record_id,
                artifact_id=artifact_id,
                action_call_id=action_call_id,
                title=_clean_text(title) or None,
                metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
                created_at=_utcnow(),
            )
        )

    def _would_create_dependency_cycle(
        self,
        ticket: TrackerTicket,
        dependency_ticket: TrackerTicket,
    ) -> bool:
        target = ticket.id
        stack = [dependency_ticket.id]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current is None or current in seen:
                continue
            seen.add(current)
            rows = list(
                self._s.exec(
                    select(TrackerTicketDependency).where(
                        TrackerTicketDependency.ticket_id == current
                    )
                )
            )
            stack.extend(row.depends_on_ticket_id for row in rows)
        return False


__all__ = [
    "TrackerRelationsMixin",
]
