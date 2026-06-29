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
    RunPlan,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
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
    TERMINAL_TRACKER_STATUSES,
    _is_closed_tracker_scope,
    _is_terminal_tracker_status,
    _required_id,
)
from stackos.repositories.tracker.workflow import workflow_step_ticket_key


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
                if ticket.status not in TERMINAL_TRACKER_STATUSES
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
        ticket_row = self._ticket_by_key(ticket.tracker_id, ticket.key)
        checks: list[dict[str, Any]] = []
        if (
            ticket.status in TERMINAL_TRACKER_STATUSES
            and ticket.status != TrackerItemStatus.COMPLETE
        ):
            checks.append(
                {
                    "key": "ticket-open-or-complete",
                    "passed": False,
                    "detail": self._terminal_reopen_detail(ticket),
                }
            )
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
        checks.extend(self._workflow_graph_checks(ticket_row))
        return TrackerVerifyOut(
            ticket=ticket,
            ready=all(item["passed"] for item in checks),
            checks=checks,
            suggested_next_actions=self._suggest_next_actions_raw(ticket, checks),
        )

    def _workflow_graph_checks(self, ticket: TrackerTicket) -> list[dict[str, Any]]:
        if ticket.run_plan_id is None or ticket.run_plan_step_id is None:
            return []
        checks: list[dict[str, Any]] = []
        parent = (
            self._s.get(TrackerTicket, ticket.parent_ticket_id)
            if ticket.parent_ticket_id is not None
            else None
        )
        if (
            parent is not None
            and parent.run_plan_id == ticket.run_plan_id
            and parent.run_plan_step_id == ticket.run_plan_step_id
        ):
            siblings = self._workflow_child_rows(parent)
            if not _is_closed_tracker_scope(parent.status, [child.status for child in siblings]):
                directly_bridged = [
                    child.key for child in siblings if self._ticket_depends_on(child, parent)
                ]
                checks.append(
                    {
                        "key": "workflow-step-child-bridge",
                        "passed": bool(directly_bridged),
                        "detail": (
                            "Workflow-backed child tickets need an execution dependency bridge: "
                            f"the first executable child under {parent.key} must depend on "
                            f"{parent.key}. Passing run_plan_id and step_id is "
                            "attachment/provenance only; update child ticket status and "
                            "evidence with tracker.updateTicket."
                        ),
                    }
                )
                scope_ids = {item.id for item in siblings if item.id is not None}
                if parent.id is not None:
                    scope_ids.add(parent.id)
                checks.append(
                    {
                        "key": "workflow-child-reachable-from-step",
                        "passed": self._dependency_path_exists(
                            source_ticket=parent,
                            target_ticket=ticket,
                            scope_ids=scope_ids,
                        ),
                        "detail": (
                            f"Attached workflow child {ticket.key} must be reachable from "
                            f"workflow step ticket {parent.key} through dependency edges; "
                            "otherwise it can become ready outside the workflow spine."
                        ),
                    }
                )
                bypassing_gate_children = self._workflow_bypassing_gate_child_keys(siblings)
                if self._is_workflow_gate_child(ticket) or ticket.key in bypassing_gate_children:
                    checks.append(
                        {
                            "key": "workflow-child-gate-contained",
                            "passed": ticket.key not in bypassing_gate_children,
                            "detail": (
                                f"Workflow child {ticket.key} looks like verification, docs, "
                                "signoff, or release work. It must be downstream of a sibling "
                                f"delivery child under {parent.key}, not ready beside it."
                            ),
                        }
                    )
        if ticket.parent_ticket_id is None:
            children = self._workflow_child_rows(ticket)
            if children:
                open_children = [
                    child.key for child in children if not _is_terminal_tracker_status(child.status)
                ]
                if not _is_closed_tracker_scope(
                    ticket.status,
                    [child.status for child in children],
                ):
                    directly_bridged = [
                        child.key for child in children if self._ticket_depends_on(child, ticket)
                    ]
                    checks.append(
                        {
                            "key": "workflow-step-child-bridge",
                            "passed": bool(directly_bridged),
                            "detail": (
                                "Workflow-backed child tickets need an execution dependency "
                                "bridge: "
                                f"the first executable child under {ticket.key} must depend on "
                                f"{ticket.key}. Passing run_plan_id and step_id is "
                                "attachment/provenance only; update child ticket status and "
                                "evidence with tracker.updateTicket."
                            ),
                        }
                    )
                    scope_ids = {item.id for item in children if item.id is not None}
                    if ticket.id is not None:
                        scope_ids.add(ticket.id)
                    unreachable_children = [
                        child.key
                        for child in children
                        if not self._dependency_path_exists(
                            source_ticket=ticket,
                            target_ticket=child,
                            scope_ids=scope_ids,
                        )
                    ]
                    checks.append(
                        {
                            "key": "workflow-step-children-reachable",
                            "passed": not unreachable_children,
                            "detail": (
                                f"Workflow step ticket {ticket.key} has child ticket(s) outside "
                                f"the dependency spine: {', '.join(unreachable_children)}."
                            ),
                        }
                    )
                    checks.append(
                        {
                            "key": "workflow-step-open-children",
                            "passed": not open_children,
                            "detail": (
                                f"Workflow step ticket {ticket.key} is not ready for closeout "
                                "while attached child tickets remain open: "
                                f"{', '.join(open_children)}."
                            ),
                        }
                    )
                    bypassing_gate_children = self._workflow_bypassing_gate_child_keys(children)
                    checks.append(
                        {
                            "key": "workflow-step-gate-children-contained",
                            "passed": not bypassing_gate_children,
                            "detail": (
                                f"Workflow step ticket {ticket.key} has verification/docs/signoff/"
                                "release child ticket(s) that can bypass delivery work: "
                                f"{', '.join(bypassing_gate_children)}."
                            ),
                        }
                    )
            dependency_ids = {
                dep.depends_on_ticket_id for dep in self._dependency_rows_for_ticket(ticket.id)
            }
            if not _is_terminal_tracker_status(ticket.status):
                for prior_step in self._workflow_step_dependencies(ticket):
                    terminal_children = self._terminal_workflow_child_rows(prior_step)
                    if not terminal_children:
                        continue
                    missing = [
                        child.key for child in terminal_children if child.id not in dependency_ids
                    ]
                    checks.append(
                        {
                            "key": "workflow-next-step-terminal-children",
                            "passed": not missing,
                            "detail": (
                                f"Workflow step ticket {ticket.key} must depend on terminal child "
                                f"ticket(s) from prior step {prior_step.key}: {', '.join(missing)}."
                            ),
                        }
                    )
        return checks

    def _workflow_child_rows(self, step_ticket: TrackerTicket) -> list[TrackerTicket]:
        if step_ticket.id is None:
            return []
        return list(
            self._s.exec(
                select(TrackerTicket)
                .where(
                    TrackerTicket.tracker_id == step_ticket.tracker_id,
                    TrackerTicket.parent_ticket_id == step_ticket.id,
                )
                .order_by(col(TrackerTicket.order_index).asc(), col(TrackerTicket.id).asc())
            )
        )

    def _terminal_workflow_child_rows(self, step_ticket: TrackerTicket) -> list[TrackerTicket]:
        children = self._workflow_child_rows(step_ticket)
        child_ids = {child.id for child in children if child.id is not None}
        depended_on_by_sibling: set[int] = set()
        for child in children:
            for dependent in self._dependent_rows_for_ticket(child.id):
                if dependent.ticket_id in child_ids and child.id is not None:
                    depended_on_by_sibling.add(child.id)
        return [child for child in children if child.id not in depended_on_by_sibling]

    def _workflow_step_dependencies(self, ticket: TrackerTicket) -> list[TrackerTicket]:
        rows: list[TrackerTicket] = []
        for dep in self._dependency_rows_for_ticket(ticket.id):
            dependency = self._s.get(TrackerTicket, dep.depends_on_ticket_id)
            if (
                dependency is not None
                and dependency.run_plan_id == ticket.run_plan_id
                and dependency.run_plan_step_id is not None
                and dependency.parent_ticket_id is None
            ):
                rows.append(dependency)
        return rows

    def _workflow_bypassing_gate_child_keys(
        self,
        children: list[TrackerTicket],
    ) -> list[str]:
        gate_children = [child for child in children if self._is_workflow_gate_child(child)]
        delivery_children = self._workflow_delivery_child_rows(children)
        if not gate_children or not delivery_children:
            return []
        scope_ids = {child.id for child in children if child.id is not None}
        bypassing: list[str] = []
        for gate_child in gate_children:
            downstream_of_all_delivery = all(
                self._dependency_path_exists(
                    source_ticket=delivery_child,
                    target_ticket=gate_child,
                    scope_ids=scope_ids,
                )
                for delivery_child in delivery_children
            )
            if not downstream_of_all_delivery:
                bypassing.append(gate_child.key)
        return bypassing

    def _workflow_delivery_child_rows(
        self,
        children: list[TrackerTicket],
    ) -> list[TrackerTicket]:
        return [child for child in children if not self._is_workflow_gate_child(child)]

    def _is_workflow_gate_child(self, ticket: TrackerTicket) -> bool:
        haystack = " ".join(
            (
                ticket.key,
                ticket.title,
                ticket.goal,
                ticket.lane_key,
            )
        ).lower()
        if ticket.lane_key in {"verification", "review", "release", "docs", "qa"}:
            return True
        return any(
            keyword in haystack
            for keyword in (
                "qa",
                "review",
                "release",
                "sign-off",
                "signoff",
                "sign off",
                "test",
                "verification",
                "verify",
            )
        )

    def _ticket_depends_on(
        self,
        ticket: TrackerTicket,
        dependency_ticket: TrackerTicket,
    ) -> bool:
        return any(
            dep.depends_on_ticket_id == dependency_ticket.id
            for dep in self._dependency_rows_for_ticket(ticket.id)
        )

    def _dependency_path_exists(
        self,
        *,
        source_ticket: TrackerTicket,
        target_ticket: TrackerTicket,
        scope_ids: set[int],
    ) -> bool:
        if source_ticket.id is None or target_ticket.id is None:
            return False
        queue = [source_ticket.id]
        seen: set[int] = set()
        while queue:
            current = queue.pop(0)
            if current == target_ticket.id:
                return True
            if current in seen:
                continue
            seen.add(current)
            for dependent in self._dependent_rows_for_ticket(current):
                if dependent.ticket_id in scope_ids and dependent.ticket_id not in seen:
                    queue.append(dependent.ticket_id)
        return False

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
            return TrackerSearchOut(
                project_id=project_id,
                query=query.strip(),
                tasks=[],
                tickets=[],
                task_count=0,
                ticket_count=0,
            )
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
        task_out = [self._task_out(row) for row in tasks]
        ticket_out = self._ticket_out_many(tickets)
        return TrackerSearchOut(
            project_id=project_id,
            query=query.strip(),
            tasks=task_out,
            tickets=ticket_out,
            task_count=len(task_out),
            ticket_count=len(ticket_out),
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

    def workflow_step_ticket_context(
        self,
        *,
        project_id: int,
        run_plan_id: int,
        step_id: str,
    ) -> dict[str, Any]:
        tracker = self.ensure_tracker(project_id=project_id)
        plan = self._s.get(RunPlan, run_plan_id)
        if plan is None or plan.project_id != project_id:
            raise NotFoundError(
                "run plan not found in project",
                data={"project_id": project_id, "run_plan_id": run_plan_id},
            )
        step = self._s.exec(
            select(RunPlanStep).where(
                RunPlanStep.run_plan_id == run_plan_id,
                RunPlanStep.step_id == step_id,
            )
        ).first()
        if step is None:
            raise NotFoundError(
                "run-plan step not found",
                data={"run_plan_id": run_plan_id, "step_id": step_id},
            )
        task_key = f"workflow-{run_plan_id}"
        task = self._task_by_key(tracker.id, task_key, missing_ok=True)
        if task is None:
            raise NotFoundError(
                "workflow tracker task not found; create the run plan first",
                data={"run_plan_id": run_plan_id, "task_key": task_key},
            )
        ticket = self._ticket_for_step(tracker.id, run_plan_id, step_id)
        if ticket is None:
            raise NotFoundError(
                "workflow step ticket not found; create the run plan first",
                data={"run_plan_id": run_plan_id, "step_id": step_id},
            )
        return {
            "task_key": task.key,
            "parent_ticket_key": ticket.key,
            "run_plan_id": plan.id,
            "run_plan_key": plan.key,
            "run_plan_step_id": step.id,
            "step_id": step.step_id,
            "template_key": plan.template_key,
        }

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
        expected_key = workflow_step_ticket_key(run_plan_id, step.step_id)
        ticket = self._ticket_by_key(tracker_id, expected_key, missing_ok=True)
        if ticket is not None and ticket.run_plan_step_id == step.id:
            return ticket
        return self._s.exec(
            select(TrackerTicket).where(
                TrackerTicket.tracker_id == tracker_id,
                TrackerTicket.run_plan_id == run_plan_id,
                TrackerTicket.run_plan_step_id == step.id,
                col(TrackerTicket.parent_ticket_id).is_(None),
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
            if ticket.status not in TERMINAL_TRACKER_STATUSES
            and not self._ticket_task_is_terminal(ticket)
            and not ticket.blocker_reason
            and not self._blocked_by_incomplete(tracker_id, ticket)
        ]

    def _ticket_blocks_active_work(self, tracker_id: int | None, ticket: TrackerTicket) -> bool:
        if self._ticket_task_is_terminal(ticket):
            return False
        return ticket.status not in TERMINAL_TRACKER_STATUSES and bool(
            ticket.blocker_reason or self._blocked_by_incomplete(tracker_id, ticket)
        )

    def _ticket_task_is_terminal(self, ticket: TrackerTicket) -> bool:
        task = self._s.get(TrackerTask, ticket.task_id)
        return task is not None and _is_terminal_tracker_status(task.status)

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
        if ticket.status in TERMINAL_TRACKER_STATUSES:
            return [self._terminal_reopen_detail(ticket)]
        blockers = self._blocked_by_incomplete(ticket.tracker_id, ticket)
        if blockers:
            checks.append(f"Complete dependencies first: {', '.join(blockers)}.")
        if ticket.blocker_reason:
            checks.append(f"Resolve blocker_reason: {ticket.blocker_reason}.")
        handoff = self._workflow_handoff(ticket)
        if handoff is not None and not checks:
            step_ref = handoff.step_id or str(ticket.run_plan_step_id or ticket.key)
            if "runPlan.claimStep" in handoff.next_operations:
                checks.append(
                    "Workflow ticket: inspect runPlan.get, claim the matching step "
                    f"{step_ref!r}, use the active step's allowed tools, then record "
                    "the step with runPlan.recordStep."
                )
            elif "runPlan.recordStep" in handoff.next_operations:
                checks.append(
                    "Workflow ticket: the matching run-plan step is already running; "
                    "continue with its active grants, then record the step with "
                    "runPlan.recordStep."
                )
            else:
                checks.append("Workflow ticket: inspect the run plan before changing state.")
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
        plan = self._s.get(RunPlan, run_plan_id_int)
        step: RunPlanStep | None = None
        if ticket.run_plan_step_id is not None:
            candidate_step = self._s.get(RunPlanStep, ticket.run_plan_step_id)
            if candidate_step is not None and candidate_step.run_plan_id == run_plan_id_int:
                step = candidate_step
        if step is None and step_id is not None:
            step = self._s.exec(
                select(RunPlanStep).where(
                    RunPlanStep.run_plan_id == run_plan_id_int,
                    RunPlanStep.step_id == step_id,
                )
            ).first()
        if step is not None and step_id is None:
            step_id = step.step_id
        if plan is not None:
            if run_plan_key is None:
                run_plan_key = plan.key
            if template_key is None:
                template_key = plan.template_key

        next_operations = ["runPlan.get"]
        notes = [
            "The tracker ticket is a navigation mirror; run-plan grants remain "
            "authoritative for workflow execution.",
        ]
        if plan is not None and plan.status == RunPlanStatus.DRAFT:
            next_operations.append("runPlan.start")
            notes.append("The run plan is still draft; start it before claiming a workflow step.")
        elif plan is not None and plan.status in {
            RunPlanStatus.COMPLETED,
            RunPlanStatus.FAILED,
            RunPlanStatus.ABORTED,
        }:
            next_operations.append("tracker.reopen")
            notes.append(
                f"The run plan is {plan.status.value}; use tracker.reopen for normal "
                "agent follow-up after closeout, or inspect before admin recovery."
            )
        elif ticket.status in TERMINAL_TRACKER_STATUSES:
            notes.append(
                f"The mirrored workflow ticket is {ticket.status.value}; inspect the run "
                "plan before reopening or recovering workflow execution."
            )
        elif step is not None and step.status == RunPlanStepStatus.PENDING:
            next_operations.extend(["runPlan.claimStep", "toolbox.describe", "runPlan.recordStep"])
            notes.append(
                "The run-plan step is pending; claim it to activate the frozen step grants."
            )
        elif step is not None and step.status == RunPlanStepStatus.RUNNING:
            next_operations.extend(["toolbox.describe", "runPlan.recordStep"])
            notes.append(
                "The run-plan step is already running; do not claim it again. Continue "
                "with the active grants and record the step when finished."
            )
        elif step is not None and step.status in {
            RunPlanStepStatus.SUCCESS,
            RunPlanStepStatus.FAILED,
            RunPlanStepStatus.SKIPPED,
            RunPlanStepStatus.BLOCKED,
        }:
            notes.append(
                f"The run-plan step is {step.status.value}; inspect the plan before "
                "choosing the next workflow action."
            )
        else:
            if ticket.run_id is None:
                next_operations.append("runPlan.start")
            next_operations.extend(["runPlan.claimStep", "toolbox.describe", "runPlan.recordStep"])
            notes.append(
                "Use the returned run_id/run_token from runPlan.start and the active "
                "step grants from runPlan.claimStep before calling step-gated tools."
            )
        return TrackerWorkflowHandoffOut(
            run_plan_id=run_plan_id_int,
            run_plan_step_id=ticket.run_plan_step_id,
            run_id=ticket.run_id,
            step_id=step_id,
            run_plan_key=run_plan_key,
            template_key=template_key,
            next_operations=next_operations,
            notes=notes,
        )

    def _suggest_next_actions_raw(
        self,
        ticket: TrackerTicketOut,
        checks: list[dict[str, Any]],
    ) -> list[str]:
        if ticket.status == TrackerItemStatus.COMPLETE:
            return ["Ticket is already complete. Review dependent tickets or audit evidence."]
        if ticket.status in TERMINAL_TRACKER_STATUSES:
            return [
                f"{self._terminal_reopen_detail(ticket)} Do not mark it complete from verification."
            ]
        failed = [item for item in checks if not item["passed"]]
        if not failed:
            return ["Ticket is verification-ready. Mark complete after final human/agent review."]
        return [str(item["detail"]) for item in failed]

    def _terminal_reopen_detail(self, ticket: TrackerTicket | TrackerTicketOut) -> str:
        if ticket.run_plan_id is not None:
            return (
                f"Ticket is {ticket.status.value}; use tracker.reopen for normal follow-up. "
                "Use runPlan.get or runPlan.checkConsistency before runPlan.recover only for "
                "system-recoverable workflow state."
            )
        return f"Ticket is {ticket.status.value}; reopen the ticket if work should continue."


__all__ = [
    "TrackerQueryMixin",
]
