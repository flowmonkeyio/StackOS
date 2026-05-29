# mypy: disable-error-code=attr-defined
"""Tracker task/ticket mutation helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import select

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    TRACKER_ITEM_STATUS_TRANSITIONS,
    TaskTracker,
    TrackerItemStatus,
    TrackerRevision,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketDependency,
    TrackerTicketKind,
)
from stackos.repositories.base import (
    ConflictError,
    Envelope,
    NotFoundError,
    ValidationError,
    validate_transition,
)
from stackos.repositories.tracker.schema import (
    TrackerListItemResultOut,
    TrackerMutationOut,
)
from stackos.repositories.tracker.utils import (
    TERMINAL_TICKET_STATUSES,
    _clean_text,
    _jsonable,
    _required_id,
    _slug,
    _status_value,
    _utcnow,
)

_TASK_PATCH_FIELDS = frozenset(
    {
        "status",
        "title",
        "goal",
        "description",
        "owner",
        "task_type",
        "priority_key",
        "lane_key",
        "source_json",
        "definition_of_done_json",
        "constraints_json",
        "expected_outcomes_json",
        "completion_evidence_json",
        "context_json",
        "metadata_json",
    }
)
_TICKET_PATCH_FIELDS = frozenset(
    {
        "status",
        "title",
        "goal",
        "assignee",
        "priority_key",
        "lane_key",
        "blocker_reason",
        "outcome",
        "effort",
        "kind",
        "parent_ticket_key",
        "source_json",
        "definition_of_done_json",
        "constraints_json",
        "expected_changes_json",
        "allowed_paths_json",
        "completion_evidence_json",
        "context_json",
        "metadata_json",
        "dependency_keys",
        "add_dependency_keys",
        "remove_dependency_keys",
        "references_json",
    }
)


class TrackerMutationMixin:
    """Tracker task/ticket mutation helpers."""

    def create_task(
        self,
        *,
        project_id: int,
        key: str,
        title: str,
        goal: str = "",
        description: str = "",
        status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED,
        priority_key: str = "p2",
        lane_key: str = "implementation",
        owner: str | None = None,
        task_type: str = "task",
        source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL,
        source_json: dict[str, Any] | None = None,
        definition_of_done_json: list[str] | None = None,
        constraints_json: list[str] | None = None,
        expected_outcomes_json: list[str] | None = None,
        completion_evidence_json: dict[str, Any] | None = None,
        context_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        created_by: str | None = None,
        create_default_ticket: bool = False,
        commit: bool = True,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        key = _slug(key, fallback="task", max_length=180)
        if self._task_by_key(tracker.id, key, missing_ok=True) is not None:
            raise ConflictError("tracker task key already exists", data={"task_key": key})
        now = _utcnow()
        row = TrackerTask(
            tracker_id=tracker.id,
            project_id=project_id,
            key=key,
            title=_clean_text(title) or key,
            goal=_clean_text(goal),
            description=_clean_text(description),
            status=status,
            priority_key=priority_key,
            lane_key=lane_key,
            owner=owner,
            task_type=task_type,
            order_index=self._next_task_position(tracker.id),
            source_kind=source_kind,
            source_json=redact_secrets(source_json) if source_json is not None else None,
            definition_of_done_json=definition_of_done_json or [],
            constraints_json=constraints_json or [],
            expected_outcomes_json=expected_outcomes_json or [],
            completion_evidence_json=(
                redact_secrets(completion_evidence_json)
                if completion_evidence_json is not None
                else None
            ),
            context_json=redact_secrets(context_json) if context_json is not None else None,
            metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            started_at=now if status == TrackerItemStatus.IN_PROGRESS else None,
            completed_at=now if status == TrackerItemStatus.COMPLETE else None,
        )
        self._s.add(row)
        self._s.flush()
        assert row.id is not None
        created_tickets: list[TrackerTicket] = []
        if create_default_ticket:
            created_tickets.append(
                self._create_ticket_row(
                    tracker=tracker,
                    task=row,
                    key=f"{key}-work",
                    title=title,
                    goal=goal,
                    status=status,
                    priority_key=priority_key,
                    lane_key=lane_key,
                    assignee=owner,
                    source_kind=source_kind,
                    source_json=source_json,
                    definition_of_done_json=definition_of_done_json,
                    constraints_json=constraints_json,
                    expected_changes_json=expected_outcomes_json,
                    completion_evidence_json=completion_evidence_json,
                    created_by=created_by,
                    now=now,
                )
            )
        self._record_revision(
            tracker,
            actor=created_by,
            change_kind="create",
            entity_kind="task",
            entity_id=row.id,
            entity_key=row.key,
            summary=f"Created task {row.key}.",
            after_json=self._task_snapshot(row),
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(row)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(row),
                tickets=self._ticket_out_many(created_tickets),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def create_ticket(
        self,
        *,
        project_id: int,
        task_key: str,
        key: str,
        title: str,
        goal: str = "",
        status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED,
        kind: TrackerTicketKind = TrackerTicketKind.TICKET,
        assignee: str | None = None,
        priority_key: str = "p2",
        lane_key: str = "implementation",
        parent_ticket_key: str | None = None,
        dependency_keys: list[str] | None = None,
        blocker_reason: str | None = None,
        outcome: str | None = None,
        effort: str | None = None,
        source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL,
        source_json: dict[str, Any] | None = None,
        definition_of_done_json: list[str] | None = None,
        constraints_json: list[str] | None = None,
        expected_changes_json: list[str] | None = None,
        allowed_paths_json: list[str] | None = None,
        references_json: list[dict[str, Any]] | None = None,
        completion_evidence_json: dict[str, Any] | None = None,
        context_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        created_by: str | None = None,
        commit: bool = True,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        task = self._task_by_key(tracker.id, task_key)
        key = _slug(key, fallback="ticket", max_length=180)
        if self._ticket_by_key(tracker.id, key, missing_ok=True) is not None:
            raise ConflictError("tracker ticket key already exists", data={"ticket_key": key})
        parent_id: int | None = None
        if parent_ticket_key:
            parent = self._ticket_by_key(tracker.id, parent_ticket_key)
            parent_id = parent.id
        now = _utcnow()
        ticket = self._create_ticket_row(
            tracker=tracker,
            task=task,
            key=key,
            title=title,
            goal=goal,
            status=status,
            kind=kind,
            assignee=assignee,
            priority_key=priority_key,
            lane_key=lane_key,
            parent_ticket_id=parent_id,
            blocker_reason=blocker_reason,
            outcome=outcome,
            effort=effort,
            source_kind=source_kind,
            source_json=source_json,
            definition_of_done_json=definition_of_done_json,
            constraints_json=constraints_json,
            expected_changes_json=expected_changes_json,
            allowed_paths_json=allowed_paths_json,
            completion_evidence_json=completion_evidence_json,
            context_json=context_json,
            metadata_json=metadata_json,
            created_by=created_by,
            now=now,
        )
        for dependency_key in dependency_keys or []:
            dependency = self._ticket_by_key(tracker.id, dependency_key)
            self._add_dependency(tracker, ticket, dependency)
        for reference in references_json or []:
            self._add_reference(tracker, ticket, reference)
        self._sync_task_status(task, now=now)
        self._record_revision(
            tracker,
            actor=created_by,
            change_kind="create",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Created ticket {ticket.key}.",
            after_json=self._ticket_snapshot(ticket),
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(ticket)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                ticket=self._ticket_out(ticket),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def update_task(
        self,
        *,
        project_id: int,
        task_key: str,
        patch_json: dict[str, Any],
        actor: str | None = None,
        commit: bool = True,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        task = self._task_by_key(tracker.id, task_key)
        before = self._task_snapshot(task)
        self._apply_task_patch(task, patch_json)
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="update",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Updated task {task.key}.",
            before_json=before,
            after_json=self._task_snapshot(task),
            patch_json=patch_json,
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(task)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def reject_task(
        self,
        *,
        project_id: int,
        task_key: str | None = None,
        run_plan_id: int | None = None,
        reason: str,
        actor: str | None = None,
        commit: bool = True,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        if (
            task_key is not None
            and run_plan_id is not None
            and task_key != f"workflow-{run_plan_id}"
        ):
            raise ValidationError(
                "task_key must match workflow-{run_plan_id} when both rejection "
                "targets are provided",
                data={"task_key": task_key, "run_plan_id": run_plan_id},
            )
        resolved_task_key = task_key or (
            f"workflow-{run_plan_id}" if run_plan_id is not None else None
        )
        if not resolved_task_key:
            raise ValidationError("task_key or run_plan_id is required to reject a task")
        reason = _clean_text(reason)
        if not reason:
            raise ValidationError("reason is required to reject a task")
        task = self._task_by_key(tracker.id, resolved_task_key)
        before = self._task_snapshot(task)
        now = _utcnow()
        rejection = {
            "decision": "rejected",
            "reason": reason,
            "rejected_at": now.isoformat(),
            **({"rejected_by": actor} if actor else {}),
            **({"run_plan_id": run_plan_id} if run_plan_id is not None else {}),
        }
        task.status = TrackerItemStatus.DEFERRED
        task.lane_key = "done"
        task.completed_at = now
        task.updated_at = now
        task.completion_evidence_json = {
            **(task.completion_evidence_json or {}),
            **rejection,
        }
        task.metadata_json = {
            **(task.metadata_json or {}),
            "rejected": True,
            "rejection_reason": reason,
            "rejected_at": now.isoformat(),
            **({"rejected_by": actor} if actor else {}),
        }
        self._s.add(task)

        changed_tickets: list[TrackerTicket] = []
        previous_ticket_statuses: dict[str, str] = {}
        for ticket in self._ticket_rows_for_task(task.id):
            previous_ticket_statuses[ticket.key] = ticket.status.value
            # Task rejection is an operator terminal override; every child closes
            # as deferred so the parent cannot look partially deliverable later.
            ticket.metadata_json = {
                **(ticket.metadata_json or {}),
                "parent_task_rejected": True,
                "rejection_reason": reason,
                "rejected_at": now.isoformat(),
                **({"rejected_by": actor} if actor else {}),
            }
            ticket.status = TrackerItemStatus.DEFERRED
            ticket.lane_key = "done"
            ticket.blocker_reason = None
            ticket.completed_at = now
            ticket.outcome = f"Rejected before completion. Reason: {reason}"
            ticket.updated_at = now
            self._s.add(ticket)
            changed_tickets.append(ticket)

        self._record_revision(
            tracker,
            actor=actor,
            change_kind="reject",
            entity_kind="task",
            entity_id=task.id,
            entity_key=task.key,
            summary=f"Rejected task {task.key}.",
            before_json=before,
            after_json=self._task_snapshot(task),
            patch_json={
                "task_key": task.key,
                "run_plan_id": run_plan_id,
                "reason": reason,
                "closed_ticket_count": sum(
                    1 for ticket in changed_tickets if ticket.status == TrackerItemStatus.DEFERRED
                ),
            },
            metadata_json={
                "previous_ticket_statuses": previous_ticket_statuses,
            },
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(task)
            for ticket in changed_tickets:
                self._s.refresh(ticket)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                tickets=self._ticket_out_many(changed_tickets),
                results=[
                    TrackerListItemResultOut(
                        index=index,
                        action="rejected",
                        key=ticket.key,
                        id=ticket.id,
                        changed_fields=[
                            "status",
                            "lane_key",
                            "blocker_reason",
                            "outcome",
                            "metadata_json",
                        ],
                        ticket=self._ticket_out(ticket),
                    )
                    for index, ticket in enumerate(changed_tickets)
                ],
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def update_ticket(
        self,
        *,
        project_id: int,
        ticket_key: str,
        patch_json: dict[str, Any],
        actor: str | None = None,
        commit: bool = True,
        dry_run: bool = False,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        ticket = self._ticket_by_key(tracker.id, ticket_key)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is None:
            raise NotFoundError("ticket task not found", data={"ticket_key": ticket_key})
        self._validate_ticket_patch_fields(patch_json)
        if dry_run:
            dependency_preview = self._preview_dependency_patch(tracker, ticket, patch_json)
            warnings = dependency_preview.warnings if dependency_preview is not None else []
            return Envelope(
                data=TrackerMutationOut(
                    tracker=self._tracker_out(tracker),
                    task=self._task_out(task),
                    ticket=self._ticket_out(ticket),
                    dependency_preview=dependency_preview,
                    results=[
                        TrackerListItemResultOut(
                            index=0,
                            action="validated",
                            key=ticket.key,
                            id=ticket.id,
                            changed_fields=list(patch_json.keys()),
                            dependency_preview=dependency_preview,
                            ticket=self._ticket_out(ticket),
                        )
                    ],
                    warnings=warnings,
                    dry_run=True,
                    rev=tracker.rev,
                ),
                project_id=project_id,
            )
        before = self._ticket_snapshot(ticket)
        self._apply_ticket_patch(tracker, ticket, patch_json)
        self._sync_task_status(task, now=_utcnow())
        self._record_revision(
            tracker,
            actor=actor,
            change_kind="update",
            entity_kind="ticket",
            entity_id=ticket.id,
            entity_key=ticket.key,
            summary=f"Updated ticket {ticket.key}.",
            before_json=before,
            after_json=self._ticket_snapshot(ticket),
            patch_json=patch_json,
            commit=False,
        )
        if commit:
            self._s.commit()
            self._s.refresh(ticket)
        else:
            self._s.flush()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task),
                ticket=self._ticket_out(ticket),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def patch(
        self,
        *,
        project_id: int,
        patch_json: dict[str, Any],
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self.ensure_tracker(project_id=project_id)
        changed_tickets: list[TrackerTicket] = []
        changed_task: TrackerTask | None = None
        tasks_patch = patch_json.get("tasks")
        tickets_patch = patch_json.get("tickets")
        if "tasks" not in patch_json and "tickets" not in patch_json:
            raise ValidationError("patch_json must include tasks or tickets")
        if tasks_patch is not None and not isinstance(tasks_patch, dict):
            raise ValidationError("patch_json.tasks must be an object keyed by task key")
        if tickets_patch is not None and not isinstance(tickets_patch, dict):
            raise ValidationError("patch_json.tickets must be an object keyed by ticket key")
        if isinstance(tasks_patch, dict):
            for task_key, task_patch in tasks_patch.items():
                if not isinstance(task_patch, dict):
                    raise ValidationError("task patch entries must be objects")
                task = self._task_by_key(tracker.id, str(task_key))
                before = self._task_snapshot(task)
                self._apply_task_patch(task, task_patch)
                changed_task = task
                self._record_revision(
                    tracker,
                    actor=actor,
                    change_kind="update",
                    entity_kind="task",
                    entity_id=task.id,
                    entity_key=task.key,
                    summary=f"Patched task {task.key}.",
                    before_json=before,
                    after_json=self._task_snapshot(task),
                    patch_json=task_patch,
                    commit=False,
                )
        if isinstance(tickets_patch, dict):
            for ticket_key, ticket_patch in tickets_patch.items():
                if not isinstance(ticket_patch, dict):
                    raise ValidationError("ticket patch entries must be objects")
                ticket = self._ticket_by_key(tracker.id, str(ticket_key))
                ticket_task = self._s.get(TrackerTask, ticket.task_id)
                if ticket_task is None:
                    raise NotFoundError("ticket task not found", data={"ticket_key": ticket.key})
                before = self._ticket_snapshot(ticket)
                self._apply_ticket_patch(tracker, ticket, ticket_patch)
                self._sync_task_status(ticket_task, now=_utcnow())
                changed_task = ticket_task
                changed_tickets.append(ticket)
                self._record_revision(
                    tracker,
                    actor=actor,
                    change_kind="update",
                    entity_kind="ticket",
                    entity_id=ticket.id,
                    entity_key=ticket.key,
                    summary=f"Patched ticket {ticket.key}.",
                    before_json=before,
                    after_json=self._ticket_snapshot(ticket),
                    patch_json=ticket_patch,
                    commit=False,
                )
        self._s.commit()
        return Envelope(
            data=TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(changed_task) if changed_task is not None else None,
                tickets=self._ticket_out_many(changed_tickets),
                rev=tracker.rev,
            ),
            project_id=project_id,
        )

    def pick(
        self,
        *,
        project_id: int,
        ticket_key: str | None = None,
        assignee: str,
    ) -> Envelope[TrackerMutationOut]:
        if not assignee.strip():
            raise ValidationError("assignee is required")
        tracker = self.ensure_tracker(project_id=project_id)
        ticket = (
            self._ticket_by_key(tracker.id, ticket_key)
            if ticket_key is not None
            else next(iter(self._ready_ticket_rows(tracker.id)), None)
        )
        if ticket is None:
            raise NotFoundError("no ready tracker ticket found")
        patch = {"status": TrackerItemStatus.IN_PROGRESS.value, "assignee": assignee}
        return self.update_ticket(
            project_id=project_id,
            ticket_key=ticket.key,
            patch_json=patch,
            actor=assignee,
        )

    def release(
        self,
        *,
        project_id: int,
        ticket_key: str,
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        return self.update_ticket(
            project_id=project_id,
            ticket_key=ticket_key,
            patch_json={"assignee": None},
            actor=actor,
        )

    def _create_ticket_row(
        self,
        *,
        tracker: TaskTracker,
        task: TrackerTask,
        key: str,
        title: str,
        goal: str = "",
        status: TrackerItemStatus = TrackerItemStatus.NOT_STARTED,
        kind: TrackerTicketKind = TrackerTicketKind.TICKET,
        assignee: str | None = None,
        priority_key: str = "p2",
        lane_key: str = "implementation",
        parent_ticket_id: int | None = None,
        blocker_reason: str | None = None,
        outcome: str | None = None,
        effort: str | None = None,
        source_kind: TrackerSourceKind = TrackerSourceKind.MANUAL,
        source_json: dict[str, Any] | None = None,
        definition_of_done_json: list[str] | None = None,
        constraints_json: list[str] | None = None,
        expected_changes_json: list[str] | None = None,
        allowed_paths_json: list[str] | None = None,
        completion_evidence_json: dict[str, Any] | None = None,
        context_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        run_plan_id: int | None = None,
        run_plan_step_id: int | None = None,
        run_id: int | None = None,
        agent_request_id: int | None = None,
        created_by: str | None = None,
        now: datetime | None = None,
        order_index: int | None = None,
    ) -> TrackerTicket:
        now = now or _utcnow()
        row = TrackerTicket(
            tracker_id=tracker.id,
            project_id=tracker.project_id,
            task_id=task.id,
            parent_ticket_id=parent_ticket_id,
            run_plan_id=run_plan_id,
            run_plan_step_id=run_plan_step_id,
            run_id=run_id,
            agent_request_id=agent_request_id,
            key=key,
            title=_clean_text(title) or key,
            goal=_clean_text(goal),
            status=status,
            kind=kind,
            assignee=assignee,
            priority_key=priority_key,
            lane_key=lane_key,
            order_index=(
                order_index if order_index is not None else self._next_ticket_position(task.id)
            ),
            blocker_reason=_clean_text(blocker_reason) if blocker_reason else None,
            outcome=_clean_text(outcome) if outcome else None,
            effort=effort,
            source_kind=source_kind,
            source_json=redact_secrets(source_json) if source_json is not None else None,
            definition_of_done_json=definition_of_done_json or [],
            constraints_json=constraints_json or [],
            expected_changes_json=expected_changes_json or [],
            allowed_paths_json=allowed_paths_json or [],
            completion_evidence_json=(
                redact_secrets(completion_evidence_json)
                if completion_evidence_json is not None
                else None
            ),
            context_json=redact_secrets(context_json) if context_json is not None else None,
            metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            created_by=created_by,
            claimed_at=now if assignee and status == TrackerItemStatus.IN_PROGRESS else None,
            created_at=now,
            updated_at=now,
            started_at=now if status == TrackerItemStatus.IN_PROGRESS else None,
            completed_at=now if status == TrackerItemStatus.COMPLETE else None,
        )
        self._s.add(row)
        self._s.flush()
        return row

    def _apply_task_patch(self, task: TrackerTask, patch_json: dict[str, Any]) -> None:
        self._validate_task_patch_fields(patch_json)
        now = _utcnow()
        if "status" in patch_json:
            new_status = _status_value(str(patch_json["status"]))
            if task.status != new_status:
                validate_transition(
                    task.status,
                    new_status,
                    TRACKER_ITEM_STATUS_TRANSITIONS,
                    label="tracker_task.status",
                )
                task.status = new_status
                if new_status == TrackerItemStatus.IN_PROGRESS:
                    task.started_at = task.started_at or now
                    task.completed_at = None
                elif new_status in TERMINAL_TICKET_STATUSES:
                    task.completed_at = now
                else:
                    task.completed_at = None
        for field in (
            "title",
            "goal",
            "description",
            "owner",
            "task_type",
            "priority_key",
            "lane_key",
        ):
            if field in patch_json:
                value = patch_json[field]
                setattr(task, field, _clean_text(value) if isinstance(value, str) else value)
        for field in (
            "source_json",
            "definition_of_done_json",
            "constraints_json",
            "expected_outcomes_json",
            "completion_evidence_json",
            "context_json",
            "metadata_json",
        ):
            if field in patch_json:
                setattr(task, field, redact_secrets(_jsonable(patch_json[field])))
        task.updated_at = now
        self._s.add(task)

    def _apply_ticket_patch(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        patch_json: dict[str, Any],
    ) -> None:
        self._validate_ticket_patch_fields(patch_json)
        now = _utcnow()
        if "status" in patch_json:
            new_status = _status_value(str(patch_json["status"]))
            if ticket.status != new_status:
                validate_transition(
                    ticket.status,
                    new_status,
                    TRACKER_ITEM_STATUS_TRANSITIONS,
                    label="tracker_ticket.status",
                )
                ticket.status = new_status
                if new_status == TrackerItemStatus.IN_PROGRESS:
                    ticket.started_at = ticket.started_at or now
                    ticket.completed_at = None
                    ticket.claimed_at = (
                        ticket.claimed_at or now if ticket.assignee else ticket.claimed_at
                    )
                elif new_status in TERMINAL_TICKET_STATUSES:
                    ticket.completed_at = now
                    if new_status == TrackerItemStatus.COMPLETE:
                        ticket.blocker_reason = None
                else:
                    ticket.completed_at = None
        for field in (
            "title",
            "goal",
            "assignee",
            "priority_key",
            "lane_key",
            "blocker_reason",
            "outcome",
            "effort",
        ):
            if field in patch_json:
                value = patch_json[field]
                setattr(ticket, field, _clean_text(value) if isinstance(value, str) else value)
                if field == "assignee" and value:
                    ticket.claimed_at = ticket.claimed_at or now
        if "kind" in patch_json:
            ticket.kind = TrackerTicketKind(str(patch_json["kind"]))
        if "parent_ticket_key" in patch_json:
            parent_key = patch_json["parent_ticket_key"]
            parent = self._ticket_by_key(tracker.id, str(parent_key)) if parent_key else None
            ticket.parent_ticket_id = parent.id if parent is not None else None
        for field in (
            "source_json",
            "definition_of_done_json",
            "constraints_json",
            "expected_changes_json",
            "allowed_paths_json",
            "completion_evidence_json",
            "context_json",
            "metadata_json",
        ):
            if field in patch_json:
                setattr(ticket, field, redact_secrets(_jsonable(patch_json[field])))
        if "dependency_keys" in patch_json:
            if "add_dependency_keys" in patch_json or "remove_dependency_keys" in patch_json:
                raise ValidationError(
                    "dependency_keys cannot be combined with add_dependency_keys or "
                    "remove_dependency_keys"
                )
            if not isinstance(patch_json["dependency_keys"], list):
                raise ValidationError("dependency_keys must be a list")
            for dep in self._dependency_rows_for_ticket(ticket.id):
                self._s.delete(dep)
            self._s.flush()
            for dependency_key in patch_json["dependency_keys"]:
                dependency = self._ticket_by_key(tracker.id, str(dependency_key))
                self._add_dependency(tracker, ticket, dependency)
        if "add_dependency_keys" in patch_json:
            if not isinstance(patch_json["add_dependency_keys"], list):
                raise ValidationError("add_dependency_keys must be a list")
            for dependency_key in dict.fromkeys(
                str(key) for key in patch_json["add_dependency_keys"]
            ):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                self._add_dependency(tracker, ticket, dependency)
        if "remove_dependency_keys" in patch_json:
            if not isinstance(patch_json["remove_dependency_keys"], list):
                raise ValidationError("remove_dependency_keys must be a list")
            for dependency_key in dict.fromkeys(
                str(key) for key in patch_json["remove_dependency_keys"]
            ):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                edge = self._s.exec(
                    select(TrackerTicketDependency).where(
                        TrackerTicketDependency.ticket_id == ticket.id,
                        TrackerTicketDependency.depends_on_ticket_id == dependency.id,
                    )
                ).first()
                if edge is None:
                    raise ValidationError(
                        "ticket dependency edge does not exist",
                        data={"ticket_key": ticket.key, "depends_on": dependency.key},
                    )
                self._s.delete(edge)
        if "references_json" in patch_json:
            if not isinstance(patch_json["references_json"], list):
                raise ValidationError("references_json must be a list")
            for reference in patch_json["references_json"]:
                if not isinstance(reference, dict):
                    raise ValidationError("references_json entries must be objects")
                self._add_reference(tracker, ticket, reference)
        ticket.updated_at = now
        self._s.add(ticket)

    def _validate_task_patch_fields(self, patch_json: dict[str, Any]) -> None:
        self._validate_patch_fields(patch_json, allowed=_TASK_PATCH_FIELDS, entity_kind="task")

    def _validate_ticket_patch_fields(self, patch_json: dict[str, Any]) -> None:
        self._validate_patch_fields(patch_json, allowed=_TICKET_PATCH_FIELDS, entity_kind="ticket")

    def _validate_patch_fields(
        self,
        patch_json: dict[str, Any],
        *,
        allowed: frozenset[str],
        entity_kind: str,
    ) -> None:
        unknown = sorted(set(patch_json) - allowed)
        if unknown:
            raise ValidationError(
                f"unsupported tracker {entity_kind} patch fields: {', '.join(unknown)}",
                data={"fields": unknown, "entity_kind": entity_kind},
            )

    def _sync_task_status(self, task: TrackerTask, *, now: datetime) -> None:
        tickets = list(self._s.exec(select(TrackerTicket).where(TrackerTicket.task_id == task.id)))
        if not tickets:
            return
        old = task.status
        if (
            task.metadata_json
            and task.metadata_json.get("rejected") is True
            and all(ticket.status in TERMINAL_TICKET_STATUSES for ticket in tickets)
        ):
            task.status = TrackerItemStatus.DEFERRED
            task.completed_at = task.completed_at if old == task.status else now
            task.lane_key = "done"
        elif all(ticket.status == TrackerItemStatus.COMPLETE for ticket in tickets):
            task.status = TrackerItemStatus.COMPLETE
            task.completed_at = task.completed_at if old == task.status else now
            task.lane_key = "done"
        elif all(ticket.status == TrackerItemStatus.DEFERRED for ticket in tickets):
            task.status = TrackerItemStatus.DEFERRED
            task.completed_at = task.completed_at if old == task.status else now
        elif all(ticket.status in TERMINAL_TICKET_STATUSES for ticket in tickets):
            task.status = TrackerItemStatus.COMPLETE
            task.completed_at = task.completed_at if old == task.status else now
            task.lane_key = "done"
        elif any(
            ticket.status in (TrackerItemStatus.IN_PROGRESS, *TERMINAL_TICKET_STATUSES)
            for ticket in tickets
        ):
            task.status = TrackerItemStatus.IN_PROGRESS
            task.started_at = task.started_at or now
            task.completed_at = None
        else:
            task.status = TrackerItemStatus.NOT_STARTED
            task.completed_at = None
        if task.status != old:
            task.updated_at = now
            self._s.add(task)

    def _next_task_position(self, tracker_id: int | None) -> int:
        rows = self._task_rows(tracker_id)
        return max((row.order_index for row in rows), default=-1) + 1

    def _next_ticket_position(self, task_id: int | None) -> int:
        task_id = _required_id(task_id, "task")
        rows = list(self._s.exec(select(TrackerTicket).where(TrackerTicket.task_id == task_id)))
        return max((row.order_index for row in rows), default=-1) + 1

    def _ticket_rows_for_task(self, task_id: int | None) -> list[TrackerTicket]:
        task_id = _required_id(task_id, "task")
        return list(
            self._s.exec(
                select(TrackerTicket)
                .where(TrackerTicket.task_id == task_id)
                .order_by(TrackerTicket.order_index)
            )
        )

    def _record_revision(
        self,
        tracker: TaskTracker,
        *,
        actor: str | None,
        change_kind: str,
        entity_kind: str,
        entity_id: int | None,
        entity_key: str | None,
        summary: str,
        before_json: dict[str, Any] | None = None,
        after_json: dict[str, Any] | None = None,
        patch_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
        commit: bool = False,
    ) -> None:
        now = _utcnow()
        tracker.rev += 1
        tracker.updated_at = now
        self._s.add(tracker)
        self._s.add(
            TrackerRevision(
                tracker_id=tracker.id,
                project_id=tracker.project_id,
                rev=tracker.rev,
                actor=actor,
                change_kind=change_kind,
                entity_kind=entity_kind,
                entity_id=entity_id,
                entity_key=entity_key,
                summary=summary,
                before_json=redact_secrets(before_json) if before_json is not None else None,
                after_json=redact_secrets(after_json) if after_json is not None else None,
                patch_json=redact_secrets(patch_json) if patch_json is not None else None,
                metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
                created_at=now,
            )
        )
        if commit:
            self._s.commit()
        else:
            self._s.flush()


__all__ = [
    "TrackerMutationMixin",
]
