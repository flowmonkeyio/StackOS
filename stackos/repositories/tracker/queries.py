# mypy: disable-error-code=attr-defined
"""Tracker read/query helpers and response shaping."""

from __future__ import annotations

from typing import (
    Any,
    Literal,
    overload,
)

from sqlalchemy import or_
from sqlmodel import (
    col,
    select,
)

from stackos.db.models import (
    RunPlanStep,
    TaskTracker,
    TaskTrackerLane,
    TaskTrackerPriority,
    TrackerItemStatus,
    TrackerRevision,
    TrackerTask,
    TrackerTicket,
)
from stackos.repositories.base import (
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
)
from stackos.repositories.tracker.schema import (
    TrackerBriefOut,
    TrackerChangedOut,
    TrackerHistoryOut,
    TrackerLaneOut,
    TrackerLinkOut,
    TrackerNextOut,
    TrackerPriorityOut,
    TrackerReferenceOut,
    TrackerSearchOut,
    TrackerSnapshotOut,
    TrackerStatusOut,
    TrackerSummaryOut,
    TrackerTaskOut,
    TrackerTicketOut,
    TrackerVerifyOut,
    TrackerWorkflowHandoffOut,
)
from stackos.repositories.tracker.utils import (
    TERMINAL_TICKET_STATUSES,
    _required_id,
)


class TrackerQueryMixin:
    """Tracker read/query helpers and response shaping."""

    def status(self, *, project_id: int) -> TrackerStatusOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerStatusOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                task_counts=self._count_statuses([]),
                ticket_counts=self._count_statuses([]),
                blocked_ticket_count=0,
                ready_ticket_count=0,
                in_progress_ticket_count=0,
                rev=0,
            )
        tasks = self._task_rows(tracker.id)
        tickets = self._ticket_rows(tracker.id)
        return TrackerStatusOut(
            tracker=self._tracker_out(tracker),
            task_counts=self._count_statuses([task.status for task in tasks]),
            ticket_counts=self._count_statuses([ticket.status for ticket in tickets]),
            blocked_ticket_count=sum(
                1 for ticket in tickets if self._ticket_blocks_active_work(tracker.id, ticket)
            ),
            ready_ticket_count=len(self._ready_ticket_rows(tracker.id, tickets=tickets)),
            in_progress_ticket_count=sum(
                1 for ticket in tickets if ticket.status == TrackerItemStatus.IN_PROGRESS
            ),
            rev=tracker.rev,
        )

    def get(
        self,
        *,
        project_id: int,
        statuses: list[TrackerItemStatus] | None = None,
        task_key: str | None = None,
        ticket_keys: list[str] | None = None,
        ticket_ids: list[int] | None = None,
        block_state: Literal["blocked", "open"] | None = None,
        dependency_ticket_key: str | None = None,
        workflow_key: str | None = None,
        run_plan_id: int | None = None,
        assignee: str | None = None,
        include_graph: bool = True,
    ) -> TrackerSnapshotOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerSnapshotOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                lanes=self._default_lane_out(),
                priorities=self._default_priority_out(),
                tasks=[],
                tickets=[],
                dependencies=[],
                links=[],
                graph=self._graph_out([], [], [], []) if include_graph else None,
            )
        tasks = self._task_rows(tracker.id)
        tickets = self._ticket_rows(tracker.id)
        if task_key is not None:
            task = self._task_by_key(tracker.id, task_key)
            tasks = [task]
            tickets = [ticket for ticket in tickets if ticket.task_id == task.id]
        if ticket_keys:
            key_set = {str(key) for key in ticket_keys}
            tickets = [ticket for ticket in tickets if ticket.key in key_set]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if ticket_ids:
            id_set = set(ticket_ids)
            tickets = [ticket for ticket in tickets if ticket.id in id_set]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if dependency_ticket_key is not None:
            focus = self._ticket_by_key(tracker.id, dependency_ticket_key)
            related_ids = {focus.id}
            related_ids.update(
                dep.depends_on_ticket_id for dep in self._dependency_rows_for_ticket(focus.id)
            )
            related_ids.update(dep.ticket_id for dep in self._dependent_rows_for_ticket(focus.id))
            tickets = [ticket for ticket in tickets if ticket.id in related_ids]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if statuses:
            status_set = set(statuses)
            tickets = [ticket for ticket in tickets if ticket.status in status_set]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids or task.status in status_set]
        if block_state:
            want_blocked = block_state == "blocked"
            tickets = [
                ticket
                for ticket in tickets
                if self._ticket_blocks_active_work(tracker.id, ticket) == want_blocked
            ]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if workflow_key is not None:
            tasks = [
                task
                for task in tasks
                if (task.source_json or {}).get("template_key") == workflow_key
                or (task.source_json or {}).get("run_plan_key") == workflow_key
            ]
            task_ids = {task.id for task in tasks if task.id is not None}
            tickets = [ticket for ticket in tickets if ticket.task_id in task_ids]
        if run_plan_id is not None:
            tickets = [ticket for ticket in tickets if ticket.run_plan_id == run_plan_id]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]
        if assignee is not None:
            tickets = [ticket for ticket in tickets if ticket.assignee == assignee]
            task_ids = {ticket.task_id for ticket in tickets}
            tasks = [task for task in tasks if task.id in task_ids]

        task_out = [self._task_out(task) for task in tasks]
        ticket_out = self._ticket_out_many(tickets)
        dependencies = self._dependency_out_for_tickets(tickets)
        links = self._link_out_for_scope(
            tracker.id,
            {task.id for task in tasks},
            {ticket.id for ticket in tickets},
        )
        graph = (
            self._graph_out(task_out, ticket_out, dependencies, links) if include_graph else None
        )
        return TrackerSnapshotOut(
            tracker=self._tracker_out(tracker),
            lanes=self._lane_out(tracker.id),
            priorities=self._priority_out(tracker.id),
            tasks=task_out,
            tickets=ticket_out,
            dependencies=dependencies,
            links=links,
            graph=graph,
        )

    def next(
        self,
        *,
        project_id: int,
        limit: int = 5,
        assignee: str | None = None,
        include_blocked: bool = True,
    ) -> TrackerNextOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerNextOut(
                tickets=[],
                blocked=[],
                explanation="No tracker work exists for this project yet.",
            )
        tickets = self._ticket_rows(tracker.id)
        if assignee is not None:
            tickets = [ticket for ticket in tickets if ticket.assignee in {None, assignee}]
        ready = self._ready_ticket_rows(tracker.id, tickets=tickets)
        ready = sorted(
            ready,
            key=lambda ticket: (
                self._priority_rank(tracker.id, ticket.priority_key),
                ticket.order_index,
                ticket.id or 0,
            ),
        )[: max(1, min(limit, 50))]
        blocked = []
        if include_blocked:
            blocked = [
                ticket
                for ticket in tickets
                if ticket.status not in TERMINAL_TICKET_STATUSES
                and ticket.key not in {item.key for item in ready}
                and self._ticket_blocks_active_work(tracker.id, ticket)
            ][: max(1, min(limit, 50))]
        return TrackerNextOut(
            tickets=self._ticket_out_many(ready),
            blocked=self._ticket_out_many(blocked),
            explanation=(
                "Ready tickets are non-terminal tickets without incomplete dependencies, "
                "ranked by priority then tracker order."
            ),
        )

    def blockers(self, *, project_id: int) -> TrackerNextOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerNextOut(
                tickets=[],
                blocked=[],
                explanation="No tracker work exists for this project yet.",
            )
        tickets = [
            ticket
            for ticket in self._ticket_rows(tracker.id)
            if self._ticket_blocks_active_work(tracker.id, ticket)
        ]
        return TrackerNextOut(
            tickets=[],
            blocked=self._ticket_out_many(tickets),
            explanation=(
                "Blocked tickets have an explicit blocker_reason or incomplete dependencies."
            ),
        )

    def brief(self, *, project_id: int, ticket_key: str) -> TrackerBriefOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            raise NotFoundError("tracker ticket not found", data={"ticket_key": ticket_key})
        ticket = self._ticket_by_key(tracker.id, ticket_key)
        task = self._s.get(TrackerTask, ticket.task_id)
        if task is None:
            raise NotFoundError("ticket task not found", data={"ticket_key": ticket.key})
        dependencies = [
            self._s.get(TrackerTicket, dep.depends_on_ticket_id)
            for dep in self._dependency_rows_for_ticket(ticket.id)
        ]
        dependents = [
            self._s.get(TrackerTicket, dep.ticket_id)
            for dep in self._dependent_rows_for_ticket(ticket.id)
        ]
        return TrackerBriefOut(
            ticket=self._ticket_out(ticket),
            task=self._task_out(task),
            dependencies=self._ticket_out_many([item for item in dependencies if item is not None]),
            dependents=self._ticket_out_many([item for item in dependents if item is not None]),
            references=[
                TrackerReferenceOut.model_validate(row)
                for row in self._reference_rows_for_ticket(ticket.id)
            ],
            links=[
                TrackerLinkOut.model_validate(row) for row in self._link_rows_for_ticket(ticket.id)
            ],
            workflow_handoff=self._workflow_handoff(ticket),
            suggested_next_actions=self._suggest_next_actions(ticket),
        )

    def verify(self, *, project_id: int, ticket_key: str) -> TrackerVerifyOut:
        brief = self.brief(project_id=project_id, ticket_key=ticket_key)
        ticket = brief.ticket
        checks: list[dict[str, Any]] = []
        checks.append(
            {
                "key": "dependencies-complete",
                "passed": all(
                    dep.status == TrackerItemStatus.COMPLETE for dep in brief.dependencies
                ),
                "detail": "All dependency tickets are complete.",
            }
        )
        checks.append(
            {
                "key": "definition-of-done-present",
                "passed": bool(ticket.definition_of_done_json),
                "detail": "Ticket has definition_of_done_json for verification.",
            }
        )
        checks.append(
            {
                "key": "no-open-blocker",
                "passed": ticket.blocker_reason is None,
                "detail": "Ticket has no explicit blocker_reason.",
            }
        )
        return TrackerVerifyOut(
            ticket=ticket,
            ready=all(item["passed"] for item in checks),
            checks=checks,
            suggested_next_actions=self._suggest_next_actions_raw(ticket, checks),
        )

    def history(
        self,
        *,
        project_id: int,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[TrackerHistoryOut]:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return Page(items=[], next_cursor=None, total_estimate=0)
        stmt = select(TrackerRevision).where(TrackerRevision.tracker_id == tracker.id)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=TrackerRevision.id,
            limit=limit,
            after_id=after_id,
            converter=TrackerHistoryOut.model_validate,
        )

    def changed(
        self,
        *,
        project_id: int,
        since_rev: int | None = None,
        limit: int = 50,
    ) -> TrackerChangedOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerChangedOut(since_rev=since_rev, current_rev=0, changes=[])
        stmt = select(TrackerRevision).where(TrackerRevision.tracker_id == tracker.id)
        if since_rev is not None:
            stmt = stmt.where(TrackerRevision.rev > since_rev)
        stmt = stmt.order_by(col(TrackerRevision.rev).asc()).limit(max(1, min(limit, 200)))
        rows = list(self._s.exec(stmt))
        return TrackerChangedOut(
            since_rev=since_rev,
            current_rev=tracker.rev,
            changes=[TrackerHistoryOut.model_validate(row) for row in rows],
        )

    def search(
        self,
        *,
        project_id: int,
        query: str,
        limit: int = 20,
    ) -> TrackerSearchOut:
        needle = f"%{query.strip()}%"
        if not query.strip():
            raise ValidationError("query is required")
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerSearchOut(tasks=[], tickets=[])
        tasks = list(
            self._s.exec(
                select(TrackerTask)
                .where(
                    TrackerTask.tracker_id == tracker.id,
                    or_(
                        col(TrackerTask.key).like(needle),
                        col(TrackerTask.title).like(needle),
                        col(TrackerTask.goal).like(needle),
                        col(TrackerTask.description).like(needle),
                    ),
                )
                .limit(max(1, min(limit, 100)))
            )
        )
        tickets = list(
            self._s.exec(
                select(TrackerTicket)
                .where(
                    TrackerTicket.tracker_id == tracker.id,
                    or_(
                        col(TrackerTicket.key).like(needle),
                        col(TrackerTicket.title).like(needle),
                        col(TrackerTicket.goal).like(needle),
                        col(TrackerTicket.outcome).like(needle),
                    ),
                )
                .limit(max(1, min(limit, 100)))
            )
        )
        return TrackerSearchOut(
            tasks=[self._task_out(row) for row in tasks],
            tickets=self._ticket_out_many(tickets),
        )

    def _tracker_out(self, row: TaskTracker) -> TrackerSummaryOut:
        return TrackerSummaryOut.model_validate(row)

    def _lane_out(self, tracker_id: int | None) -> list[TrackerLaneOut]:
        tracker_id = _required_id(tracker_id, "tracker")
        return [
            TrackerLaneOut.model_validate(row)
            for row in self._s.exec(
                select(TaskTrackerLane)
                .where(TaskTrackerLane.tracker_id == tracker_id)
                .order_by(col(TaskTrackerLane.position).asc())
            )
        ]

    def _priority_out(self, tracker_id: int | None) -> list[TrackerPriorityOut]:
        tracker_id = _required_id(tracker_id, "tracker")
        return [
            TrackerPriorityOut.model_validate(row)
            for row in self._s.exec(
                select(TaskTrackerPriority)
                .where(TaskTrackerPriority.tracker_id == tracker_id)
                .order_by(col(TaskTrackerPriority.position).asc())
            )
        ]

    def _task_out(self, row: TrackerTask) -> TrackerTaskOut:
        return TrackerTaskOut.model_validate(row)

    def _ticket_out(self, row: TrackerTicket) -> TrackerTicketOut:
        task = self._s.get(TrackerTask, row.task_id)
        parent = self._s.get(TrackerTicket, row.parent_ticket_id) if row.parent_ticket_id else None
        dependencies = self._dependency_rows_for_ticket(row.id)
        dependency_keys = [
            item.key
            for item in [
                self._s.get(TrackerTicket, dep.depends_on_ticket_id) for dep in dependencies
            ]
            if item is not None
        ]
        blocked_by = [
            item.key
            for item in [
                self._s.get(TrackerTicket, dep.depends_on_ticket_id) for dep in dependencies
            ]
            if item is not None and item.status != TrackerItemStatus.COMPLETE
        ]
        base = TrackerTicketOut.model_validate(row).model_dump(
            exclude={
                "task_key",
                "parent_ticket_key",
                "dependency_keys",
                "blocked_by",
                "reference_count",
                "link_count",
            }
        )
        return TrackerTicketOut(
            **base,
            task_key=task.key if task is not None else "",
            parent_ticket_key=parent.key if parent is not None else None,
            dependency_keys=dependency_keys,
            blocked_by=blocked_by,
            reference_count=len(self._reference_rows_for_ticket(row.id)),
            link_count=len(self._link_rows_for_ticket(row.id)),
        )

    def _ticket_out_many(self, rows: list[TrackerTicket]) -> list[TrackerTicketOut]:
        return [self._ticket_out(row) for row in rows]

    def _task_rows(self, tracker_id: int | None) -> list[TrackerTask]:
        tracker_id = _required_id(tracker_id, "tracker")
        return list(
            self._s.exec(
                select(TrackerTask)
                .where(TrackerTask.tracker_id == tracker_id)
                .order_by(col(TrackerTask.order_index).asc(), col(TrackerTask.id).asc())
            )
        )

    def _ticket_rows(self, tracker_id: int | None) -> list[TrackerTicket]:
        tracker_id = _required_id(tracker_id, "tracker")
        return list(
            self._s.exec(
                select(TrackerTicket)
                .where(TrackerTicket.tracker_id == tracker_id)
                .order_by(col(TrackerTicket.order_index).asc(), col(TrackerTicket.id).asc())
            )
        )

    def _ticket_rows_for_run_plan(
        self, tracker_id: int | None, run_plan_id: int
    ) -> list[TrackerTicket]:
        tracker_id = _required_id(tracker_id, "tracker")
        return list(
            self._s.exec(
                select(TrackerTicket).where(
                    TrackerTicket.tracker_id == tracker_id,
                    TrackerTicket.run_plan_id == run_plan_id,
                )
            )
        )

    @overload
    def _task_by_key(
        self,
        tracker_id: int | None,
        key: str,
        *,
        missing_ok: Literal[False] = False,
    ) -> TrackerTask: ...

    @overload
    def _task_by_key(
        self,
        tracker_id: int | None,
        key: str,
        *,
        missing_ok: Literal[True],
    ) -> TrackerTask | None: ...

    def _task_by_key(
        self,
        tracker_id: int | None,
        key: str,
        *,
        missing_ok: bool = False,
    ) -> TrackerTask | None:
        tracker_id = _required_id(tracker_id, "tracker")
        row = self._s.exec(
            select(TrackerTask).where(TrackerTask.tracker_id == tracker_id, TrackerTask.key == key)
        ).first()
        if row is None and not missing_ok:
            raise NotFoundError("tracker task not found", data={"task_key": key})
        return row

    @overload
    def _ticket_by_key(
        self,
        tracker_id: int | None,
        key: str | None,
        *,
        missing_ok: Literal[False] = False,
    ) -> TrackerTicket: ...

    @overload
    def _ticket_by_key(
        self,
        tracker_id: int | None,
        key: str | None,
        *,
        missing_ok: Literal[True],
    ) -> TrackerTicket | None: ...

    def _ticket_by_key(
        self,
        tracker_id: int | None,
        key: str | None,
        *,
        missing_ok: bool = False,
    ) -> TrackerTicket | None:
        tracker_id = _required_id(tracker_id, "tracker")
        if key is None:
            if missing_ok:
                return None
            raise ValidationError("ticket_key is required")
        row = self._s.exec(
            select(TrackerTicket).where(
                TrackerTicket.tracker_id == tracker_id,
                TrackerTicket.key == key,
            )
        ).first()
        if row is None and not missing_ok:
            raise NotFoundError("tracker ticket not found", data={"ticket_key": key})
        return row

    def _ticket_for_step(
        self,
        tracker_id: int | None,
        run_plan_id: int,
        step_id: str,
    ) -> TrackerTicket | None:
        tracker_id = _required_id(tracker_id, "tracker")
        step = self._s.exec(
            select(RunPlanStep).where(
                RunPlanStep.run_plan_id == run_plan_id,
                RunPlanStep.step_id == step_id,
            )
        ).first()
        if step is None:
            return None
        return self._s.exec(
            select(TrackerTicket).where(
                TrackerTicket.tracker_id == tracker_id,
                TrackerTicket.run_plan_id == run_plan_id,
                TrackerTicket.run_plan_step_id == step.id,
            )
        ).first()

    def _ready_ticket_rows(
        self,
        tracker_id: int | None,
        *,
        tickets: list[TrackerTicket] | None = None,
    ) -> list[TrackerTicket]:
        rows = tickets if tickets is not None else self._ticket_rows(tracker_id)
        return [
            ticket
            for ticket in rows
            if ticket.status not in TERMINAL_TICKET_STATUSES
            and not ticket.blocker_reason
            and not self._blocked_by_incomplete(tracker_id, ticket)
        ]

    def _ticket_blocks_active_work(self, tracker_id: int | None, ticket: TrackerTicket) -> bool:
        return ticket.status not in TERMINAL_TICKET_STATUSES and bool(
            ticket.blocker_reason or self._blocked_by_incomplete(tracker_id, ticket)
        )

    def _blocked_by_incomplete(self, tracker_id: int | None, ticket: TrackerTicket) -> list[str]:
        blockers: list[str] = []
        for dep in self._dependency_rows_for_ticket(ticket.id):
            dependency = self._s.get(TrackerTicket, dep.depends_on_ticket_id)
            if dependency is not None and dependency.status != TrackerItemStatus.COMPLETE:
                blockers.append(dependency.key)
        return blockers

    def _priority_rank(self, tracker_id: int | None, key: str) -> int:
        tracker_id = _required_id(tracker_id, "tracker")
        row = self._s.exec(
            select(TaskTrackerPriority).where(
                TaskTrackerPriority.tracker_id == tracker_id,
                TaskTrackerPriority.key == key,
            )
        ).first()
        return row.rank if row is not None else 100

    def _task_snapshot(self, task: TrackerTask) -> dict[str, Any]:
        return self._task_out(task).model_dump(mode="json")

    def _ticket_snapshot(self, ticket: TrackerTicket) -> dict[str, Any]:
        return self._ticket_out(ticket).model_dump(mode="json")

    def _count_statuses(self, statuses: list[TrackerItemStatus]) -> dict[str, int]:
        counts = {status.value: 0 for status in TrackerItemStatus}
        for status in statuses:
            counts[status.value] = counts.get(status.value, 0) + 1
        return counts

    def _suggest_next_actions(self, ticket: TrackerTicket) -> list[str]:
        checks = []
        if ticket.status == TrackerItemStatus.COMPLETE:
            return ["Review dependent tickets or start the next ready item."]
        blockers = self._blocked_by_incomplete(ticket.tracker_id, ticket)
        if blockers:
            checks.append(f"Complete dependencies first: {', '.join(blockers)}.")
        if ticket.blocker_reason:
            checks.append(f"Resolve blocker_reason: {ticket.blocker_reason}.")
        handoff = self._workflow_handoff(ticket)
        if handoff is not None and not checks:
            step_ref = handoff.step_id or str(ticket.run_plan_step_id or ticket.key)
            checks.append(
                "Workflow ticket: inspect runPlan.get, claim the matching step "
                f"{step_ref!r}, use the active step's allowed tools, then record "
                "the step with runPlan.recordStep."
            )
        if not checks:
            checks.append("Claim or continue the ticket, then update status/outcome when done.")
        return checks

    def _workflow_handoff(self, ticket: TrackerTicket) -> TrackerWorkflowHandoffOut | None:
        source = dict(ticket.source_json or {})
        run_plan_id = ticket.run_plan_id or source.get("run_plan_id")
        if run_plan_id is None:
            return None
        try:
            run_plan_id_int = int(run_plan_id)
        except (TypeError, ValueError):
            return None
        step_id = source.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            step_id = None
        run_plan_key = source.get("run_plan_key")
        if not isinstance(run_plan_key, str) or not run_plan_key:
            run_plan_key = None
        template_key = source.get("template_key")
        if not isinstance(template_key, str) or not template_key:
            template_key = None
        next_operations = ["runPlan.get"]
        if ticket.run_id is None:
            next_operations.append("runPlan.start")
        next_operations.extend(["runPlan.claimStep", "toolbox.describe", "runPlan.recordStep"])
        return TrackerWorkflowHandoffOut(
            run_plan_id=run_plan_id_int,
            run_plan_step_id=ticket.run_plan_step_id,
            run_id=ticket.run_id,
            step_id=step_id,
            run_plan_key=run_plan_key,
            template_key=template_key,
            next_operations=next_operations,
            notes=[
                "The tracker ticket is a navigation mirror; run-plan grants remain "
                "authoritative for workflow execution.",
                "Use the returned run_id/run_token from runPlan.start and the active "
                "step grants from runPlan.claimStep before calling step-gated tools.",
            ],
        )

    def _suggest_next_actions_raw(
        self,
        ticket: TrackerTicketOut,
        checks: list[dict[str, Any]],
    ) -> list[str]:
        failed = [item for item in checks if not item["passed"]]
        if not failed:
            return ["Ticket is verification-ready. Mark complete after final human/agent review."]
        return [str(item["detail"]) for item in failed]


__all__ = [
    "TrackerQueryMixin",
]
