# mypy: disable-error-code=attr-defined
"""Tracker graph projection and graph advisory warnings."""

from __future__ import annotations

from stackos.db.models import (
    TrackerItemStatus,
    TrackerLinkKind,
    TrackerTicketKind,
)
from stackos.repositories.tracker.schema import (
    TrackerDependencyOut,
    TrackerGraphEdgeOut,
    TrackerGraphNodeOut,
    TrackerGraphOut,
    TrackerLinkOut,
    TrackerTaskOut,
    TrackerTicketOut,
)


class TrackerGraphMixin:
    """Tracker graph projection and graph advisory warnings."""

    def _graph_out(
        self,
        tasks: list[TrackerTaskOut],
        tickets: list[TrackerTicketOut],
        dependencies: list[TrackerDependencyOut],
        links: list[TrackerLinkOut],
    ) -> TrackerGraphOut:
        nodes: list[TrackerGraphNodeOut] = []
        edges: list[TrackerGraphEdgeOut] = []
        task_node_ids: set[str] = set()
        ticket_node_ids: set[str] = set()
        for task in tasks:
            node_id = f"task:{task.key}"
            task_node_ids.add(node_id)
            nodes.append(
                TrackerGraphNodeOut(
                    id=node_id,
                    type="task",
                    label=task.title,
                    status=task.status.value,
                    lane_key=task.lane_key,
                    priority_key=task.priority_key,
                    data=task.model_dump(mode="json"),
                )
            )
        for ticket in tickets:
            node_id = f"ticket:{ticket.key}"
            ticket_node_ids.add(node_id)
            task_node = f"task:{ticket.task_key}"
            nodes.append(
                TrackerGraphNodeOut(
                    id=node_id,
                    type="group" if ticket.kind == TrackerTicketKind.GROUP else "ticket",
                    parent_id=task_node if task_node in task_node_ids else None,
                    label=ticket.title,
                    status=ticket.status.value,
                    lane_key=ticket.lane_key,
                    priority_key=ticket.priority_key,
                    data=ticket.model_dump(mode="json"),
                )
            )
            if task_node in task_node_ids:
                edges.append(
                    TrackerGraphEdgeOut(
                        id=f"contains:{ticket.task_key}:{ticket.key}",
                        type="contains",
                        source=task_node,
                        target=node_id,
                    )
                )
        for dependency in dependencies:
            source = f"ticket:{dependency.depends_on_ticket_key}"
            target = f"ticket:{dependency.ticket_key}"
            if source in ticket_node_ids and target in ticket_node_ids:
                edges.append(
                    TrackerGraphEdgeOut(
                        id=f"dependency:{dependency.depends_on_ticket_key}:{dependency.ticket_key}",
                        type="dependency",
                        source=source,
                        target=target,
                        label=dependency.dependency_type,
                        data={"dependency_id": dependency.id},
                    )
                )
        for link in links:
            if link.ticket_id is None and link.task_id is None:
                continue
            if link.link_kind in {TrackerLinkKind.RUN_PLAN, TrackerLinkKind.RUN_PLAN_STEP}:
                continue
            link_target: str | None = None
            if link.ticket_id is not None:
                linked_ticket = next((item for item in tickets if item.id == link.ticket_id), None)
                link_target = f"ticket:{linked_ticket.key}" if linked_ticket is not None else None
            if link_target is None and link.task_id is not None:
                linked_task = next((item for item in tasks if item.id == link.task_id), None)
                link_target = f"task:{linked_task.key}" if linked_task is not None else None
            if link_target is not None:
                source = f"link:{link.id}"
                nodes.append(
                    TrackerGraphNodeOut(
                        id=source,
                        type="ticket",
                        label=link.title or link.ref or link.link_kind.value,
                        status="link",
                        lane_key="external",
                        priority_key="p3",
                        data=link.model_dump(mode="json"),
                    )
                )
                edges.append(
                    TrackerGraphEdgeOut(
                        id=f"link:{link.id}:{link_target}",
                        type="link",
                        source=source,
                        target=link_target,
                        label=link.link_kind.value,
                    )
                )
        return TrackerGraphOut(
            nodes=nodes,
            edges=edges,
            warnings=self._graph_advisory_warnings(tasks, tickets, dependencies),
            layout_hints={"direction": "LR", "group_by": "task"},
        )

    def _graph_advisory_warnings(
        self,
        tasks: list[TrackerTaskOut],
        tickets: list[TrackerTicketOut],
        dependencies: list[TrackerDependencyOut],
    ) -> list[str]:
        warnings: list[str] = []
        tickets_by_task: dict[str, list[TrackerTicketOut]] = {}
        for ticket in tickets:
            tickets_by_task.setdefault(ticket.task_key, []).append(ticket)
        dependency_keys_by_task: dict[str, set[tuple[str, str]]] = {}
        for task_key, task_tickets in tickets_by_task.items():
            task_ticket_keys = {ticket.key for ticket in task_tickets}
            dependency_keys_by_task[task_key] = {
                (dependency.ticket_key, dependency.depends_on_ticket_key)
                for dependency in dependencies
                if dependency.ticket_key in task_ticket_keys
                and dependency.depends_on_ticket_key in task_ticket_keys
            }

        task_title_by_key = {task.key: task.title for task in tasks}
        terminal_statuses = {
            TrackerItemStatus.COMPLETE.value,
            TrackerItemStatus.DEFERRED.value,
        }
        for task_key, task_tickets in tickets_by_task.items():
            active_tickets = [
                ticket for ticket in task_tickets if ticket.status.value not in terminal_statuses
            ]
            if len(active_tickets) < 5:
                continue
            edges = dependency_keys_by_task.get(task_key, set())
            minimum_edges = max(2, len(active_tickets) // 2)
            if len(edges) < minimum_edges:
                warnings.append(
                    "Task "
                    f"{task_key} has {len(active_tickets)} nonterminal tickets but only "
                    f"{len(edges)} dependency relations; review whether the plan is missing "
                    "blocking edges."
                )
            linked_ticket_keys = {ticket_key for edge in edges for ticket_key in edge}
            isolated = [
                ticket.key for ticket in active_tickets if ticket.key not in linked_ticket_keys
            ]
            if len(isolated) >= 3 and len(isolated) >= (len(active_tickets) + 1) // 2:
                sample = ", ".join(isolated[:3])
                suffix = "..." if len(isolated) > 3 else ""
                warnings.append(
                    "Task "
                    f"{task_key} has {len(isolated)} nonterminal tickets without dependency "
                    f"links ({sample}{suffix}); review isolated work before implementation."
                )

        dependencies_by_ticket = {
            ticket_key: [
                dependency for dependency in dependencies if dependency.ticket_key == ticket_key
            ]
            for ticket_key in {ticket.key for ticket in tickets}
        }
        for ticket in tickets:
            if ticket.status.value in terminal_statuses:
                continue
            text = " ".join(
                [
                    ticket.key,
                    ticket.title,
                    ticket.goal or "",
                    task_title_by_key.get(ticket.task_key, ""),
                ]
            ).lower()
            looks_like_pre_gate = ("gate" in text or "review" in text) and (
                "before" in text or "pre-" in text or "pre " in text or ticket.lane_key == "review"
            )
            dependency_count = len(dependencies_by_ticket.get(ticket.key, []))
            if looks_like_pre_gate and dependency_count >= 2:
                warnings.append(
                    "Review/gate ticket "
                    f"{ticket.key} depends on {dependency_count} tickets; confirm dependency "
                    "direction if this gate should unblock implementation work."
                )
        return warnings


__all__ = [
    "TrackerGraphMixin",
]
