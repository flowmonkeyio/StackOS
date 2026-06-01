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
from stackos.repositories.tracker.utils import (
    _is_closed_tracker_scope,
    _is_terminal_tracker_status,
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
            layout_hints={
                "direction": "LR",
                "group_by": "task",
                "edge_semantics": {
                    "contains": "Attached under task or workflow step; does not affect readiness.",
                    "dependency": "Blocks readiness and carries execution order.",
                    "link": "External reference only.",
                },
            },
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
        for task_key, task_tickets in tickets_by_task.items():
            active_tickets = [
                ticket for ticket in task_tickets if not _is_terminal_tracker_status(ticket.status)
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
            if _is_terminal_tracker_status(ticket.status):
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
                "before" in text or "pre-" in text or "pre " in text
            )
            dependency_count = len(dependencies_by_ticket.get(ticket.key, []))
            if looks_like_pre_gate and dependency_count >= 2:
                warnings.append(
                    "Review/gate ticket "
                    f"{ticket.key} depends on {dependency_count} tickets; confirm dependency "
                    "direction if this gate should unblock implementation work."
                )
        warnings.extend(
            self._workflow_graph_advisory_warnings(tickets=tickets, dependencies=dependencies)
        )
        return warnings

    def _workflow_graph_advisory_warnings(
        self,
        *,
        tickets: list[TrackerTicketOut],
        dependencies: list[TrackerDependencyOut],
    ) -> list[str]:
        warnings: list[str] = []
        tickets_by_key = {ticket.key: ticket for ticket in tickets}
        dependency_edges = {
            (dependency.depends_on_ticket_key, dependency.ticket_key) for dependency in dependencies
        }
        children_by_parent: dict[str, list[TrackerTicketOut]] = {}
        for ticket in tickets:
            if ticket.parent_ticket_key:
                children_by_parent.setdefault(ticket.parent_ticket_key, []).append(ticket)
        for parent_key, children in children_by_parent.items():
            parent = tickets_by_key.get(parent_key)
            if parent is None or parent.run_plan_id is None:
                continue
            open_children = [
                child.key for child in children if not _is_terminal_tracker_status(child.status)
            ]
            if open_children:
                sample = ", ".join(open_children[:3])
                suffix = "..." if len(open_children) > 3 else ""
                state = (
                    "is complete while"
                    if parent.status.value == TrackerItemStatus.COMPLETE.value
                    else "is not ready for closeout while"
                )
                warnings.append(
                    "Workflow step "
                    f"{parent.key} {state} attached child tickets remain open "
                    f"({sample}{suffix})."
                )
            if _is_closed_tracker_scope(parent.status, [child.status for child in children]):
                continue
            directly_bridged = [
                child.key for child in children if (parent.key, child.key) in dependency_edges
            ]
            if not directly_bridged:
                warnings.append(
                    "Workflow step "
                    f"{parent.key} has attached child tickets but no dependency bridge from "
                    "the step ticket. Attachment is containment only; add dependency edges "
                    "before execution."
                )
            for child in children:
                if not self._graph_dependency_path_exists(parent.key, child.key, dependency_edges):
                    warnings.append(
                        "Workflow child "
                        f"{child.key} is attached to {parent.key} but is not reachable from "
                        "that step through dependency edges."
                    )
            bypassing_gate_children = self._graph_bypassing_gate_child_keys(
                children=children,
                dependency_edges=dependency_edges,
            )
            if bypassing_gate_children:
                sample = ", ".join(bypassing_gate_children[:3])
                suffix = "..." if len(bypassing_gate_children) > 3 else ""
                warnings.append(
                    "Workflow step "
                    f"{parent.key} has verification/docs/signoff/release child tickets that "
                    f"can bypass delivery work ({sample}{suffix}). Make them depend on "
                    "terminal delivery child tickets."
                )
        step_tickets = [
            ticket
            for ticket in tickets
            if ticket.run_plan_id is not None
            and ticket.run_plan_step_id is not None
            and ticket.parent_ticket_key is None
        ]
        for step_ticket in step_tickets:
            if _is_terminal_tracker_status(step_ticket.status):
                continue
            prior_steps = [
                tickets_by_key[dependency_key]
                for dependency_key, ticket_key in dependency_edges
                if ticket_key == step_ticket.key
                and dependency_key in tickets_by_key
                and tickets_by_key[dependency_key].run_plan_id == step_ticket.run_plan_id
                and tickets_by_key[dependency_key].run_plan_step_id is not None
                and tickets_by_key[dependency_key].parent_ticket_key is None
            ]
            direct_dependencies = {
                dependency_key
                for dependency_key, ticket_key in dependency_edges
                if ticket_key == step_ticket.key
            }
            for prior_step in prior_steps:
                terminal_children = self._graph_terminal_workflow_children(
                    prior_step.key,
                    children_by_parent,
                    dependency_edges,
                )
                missing = [
                    child.key for child in terminal_children if child.key not in direct_dependencies
                ]
                if missing:
                    sample = ", ".join(missing[:3])
                    suffix = "..." if len(missing) > 3 else ""
                    warnings.append(
                        "Workflow step "
                        f"{step_ticket.key} depends on prior step {prior_step.key} but not "
                        f"its terminal child tickets ({sample}{suffix}). Add terminal-child "
                        "handoff dependencies so the next step cannot start early."
                    )
        return warnings

    def _graph_terminal_workflow_children(
        self,
        parent_key: str,
        children_by_parent: dict[str, list[TrackerTicketOut]],
        dependency_edges: set[tuple[str, str]],
    ) -> list[TrackerTicketOut]:
        children = children_by_parent.get(parent_key, [])
        child_keys = {child.key for child in children}
        depended_on_by_sibling = {
            dependency_key
            for dependency_key, ticket_key in dependency_edges
            if dependency_key in child_keys and ticket_key in child_keys
        }
        return [child for child in children if child.key not in depended_on_by_sibling]

    def _graph_bypassing_gate_child_keys(
        self,
        *,
        children: list[TrackerTicketOut],
        dependency_edges: set[tuple[str, str]],
    ) -> list[str]:
        gate_children = [child for child in children if self._graph_is_workflow_gate_child(child)]
        terminal_delivery_children = self._graph_terminal_workflow_delivery_children(
            children,
            dependency_edges,
        )
        if not gate_children or not terminal_delivery_children:
            return []
        child_keys = {child.key for child in children}
        scoped_edges = {
            edge for edge in dependency_edges if edge[0] in child_keys and edge[1] in child_keys
        }
        bypassing: list[str] = []
        for gate_child in gate_children:
            downstream_of_all_terminal_delivery = all(
                self._graph_dependency_path_exists(
                    delivery_child.key,
                    gate_child.key,
                    scoped_edges,
                )
                for delivery_child in terminal_delivery_children
            )
            if not downstream_of_all_terminal_delivery:
                bypassing.append(gate_child.key)
        return bypassing

    def _graph_terminal_workflow_delivery_children(
        self,
        children: list[TrackerTicketOut],
        dependency_edges: set[tuple[str, str]],
    ) -> list[TrackerTicketOut]:
        delivery_children = [
            child for child in children if not self._graph_is_workflow_gate_child(child)
        ]
        delivery_keys = {child.key for child in delivery_children}
        depended_on_by_delivery = {
            dependency_key
            for dependency_key, ticket_key in dependency_edges
            if dependency_key in delivery_keys and ticket_key in delivery_keys
        }
        return [child for child in delivery_children if child.key not in depended_on_by_delivery]

    def _graph_is_workflow_gate_child(self, ticket: TrackerTicketOut) -> bool:
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
                "doc",
                "documentation",
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

    def _graph_dependency_path_exists(
        self,
        source_key: str,
        target_key: str,
        dependency_edges: set[tuple[str, str]],
    ) -> bool:
        queue = [source_key]
        seen: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current == target_key:
                return True
            if current in seen:
                continue
            seen.add(current)
            for dependency_key, ticket_key in dependency_edges:
                if dependency_key == current and ticket_key not in seen:
                    queue.append(ticket_key)
        return False


__all__ = [
    "TrackerGraphMixin",
]
