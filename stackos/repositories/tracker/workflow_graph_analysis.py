"""Pure workflow-backed tracker graph facts."""

from __future__ import annotations

from collections.abc import Hashable, Iterable

_WORKFLOW_GATE_LANES = {"verification", "review", "release", "docs", "qa"}
_WORKFLOW_GATE_KEYWORDS = (
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


def is_workflow_gate_child(
    *,
    key: str,
    title: str,
    goal: str,
    lane_key: str,
) -> bool:
    """Return whether a workflow child represents a downstream delivery gate."""

    if lane_key in _WORKFLOW_GATE_LANES:
        return True
    haystack = " ".join((key, title, goal, lane_key)).lower()
    return any(keyword in haystack for keyword in _WORKFLOW_GATE_KEYWORDS)


def dependency_path_exists[Node: Hashable](
    source: Node,
    target: Node,
    dependency_edges: set[tuple[Node, Node]],
) -> bool:
    """Return whether ``target`` is downstream of ``source``."""

    queue = [source]
    seen: set[Node] = set()
    while queue:
        current = queue.pop(0)
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        for dependency_node, dependent_node in dependency_edges:
            if dependency_node == current and dependent_node not in seen:
                queue.append(dependent_node)
    return False


def terminal_workflow_children[Node: Hashable](
    child_nodes: Iterable[Node],
    dependency_edges: set[tuple[Node, Node]],
) -> list[Node]:
    """Return child nodes with no dependent sibling, preserving child order."""

    ordered_children = list(child_nodes)
    child_set = set(ordered_children)
    depended_on_by_sibling = {
        dependency_node
        for dependency_node, dependent_node in dependency_edges
        if dependency_node in child_set and dependent_node in child_set
    }
    return [child for child in ordered_children if child not in depended_on_by_sibling]


def bypassing_workflow_gate_children[Node: Hashable](
    child_nodes: Iterable[Node],
    gate_child_nodes: set[Node],
    dependency_edges: set[tuple[Node, Node]],
) -> list[Node]:
    """Return gate children that are not downstream of every delivery child."""

    ordered_children = list(child_nodes)
    gate_children = [child for child in ordered_children if child in gate_child_nodes]
    delivery_children = [child for child in ordered_children if child not in gate_child_nodes]
    if not gate_children or not delivery_children:
        return []
    child_set = set(ordered_children)
    scoped_edges = {
        edge for edge in dependency_edges if edge[0] in child_set and edge[1] in child_set
    }
    return [
        gate_child
        for gate_child in gate_children
        if not all(
            dependency_path_exists(delivery_child, gate_child, scoped_edges)
            for delivery_child in delivery_children
        )
    ]


__all__ = [
    "bypassing_workflow_gate_children",
    "dependency_path_exists",
    "is_workflow_gate_child",
    "terminal_workflow_children",
]
