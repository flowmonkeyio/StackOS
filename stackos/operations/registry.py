"""Central StackOS operation registry."""

from __future__ import annotations

from collections.abc import Iterable

from stackos.operations.spec import OperationGroupOut, OperationListOut, OperationSpec
from stackos.repositories.base import NotFoundError

_CATEGORY_ALIASES: dict[str, str] = {
    "action": "actions",
    "actions": "actions",
    "agent": "agents",
    "agents": "agents",
    "auth": "auth",
    "authentication": "auth",
    "communication": "communications",
    "communications": "communications",
    "catalog": "catalog",
    "catalogs": "catalog",
    "operation": "operations",
    "operations": "operations",
    "resource": "resources",
    "resources": "resources",
    "setup": "setup",
    "system": "system",
    "tracker": "tracker",
    "tracking": "tracker",
    "workflow": "workflow",
    "workflows": "workflow",
    "runplan": "workflow",
    "run-plan": "workflow",
    "runplans": "workflow",
    "run-plans": "workflow",
}


def _normalize_category(category: str | None) -> str | None:
    if category is None:
        return None
    normalized = category.strip().lower()
    if not normalized:
        return None
    return _CATEGORY_ALIASES.get(normalized, normalized)


class OperationRegistry:
    """In-memory registry of protocol-neutral StackOS operations."""

    def __init__(self) -> None:
        self._operations: dict[str, OperationSpec] = {}

    def register(self, spec: OperationSpec) -> None:
        if spec.name in self._operations:
            raise RuntimeError(f"duplicate operation registration: {spec.name!r}")
        self._operations[spec.name] = spec

    def get(self, name: str, *, surface: str | None = None) -> OperationSpec:
        try:
            spec = self._operations[name]
        except KeyError as exc:
            raise NotFoundError(
                f"operation {name!r} is not registered",
                data={"operation": name},
            ) from exc
        if surface is not None and not spec.surfaces.is_enabled(surface):
            raise NotFoundError(
                f"operation {name!r} is not available on {surface}",
                data={"operation": name, "surface": surface},
            )
        return spec

    def all(self) -> list[OperationSpec]:
        return [self._operations[key] for key in sorted(self._operations)]

    def by_surface(self, surface: str) -> list[OperationSpec]:
        return [spec for spec in self.all() if spec.surfaces.is_enabled(surface)]

    def list_out(
        self,
        *,
        surface: str | None = None,
        category: str | None = None,
        query: str | None = None,
        mode: str = "standard",
    ) -> OperationListOut:
        rows: Iterable[OperationSpec] = self.all() if surface is None else self.by_surface(surface)
        filtered = list(rows)
        normalized_category = _normalize_category(category)
        if normalized_category is not None:
            filtered = [row for row in filtered if row.category_name == normalized_category]
        normalized_query = (query or "").strip().lower()
        if normalized_query:
            filtered = [
                row
                for row in filtered
                if normalized_query in row.name.lower()
                or normalized_query in row.summary.lower()
                or normalized_query in row.category_name.lower()
                or normalized_query in row.grant_policy.lower()
            ]
        groups = _operation_groups(filtered)
        if mode == "grouped":
            return OperationListOut(items=[], groups=groups)
        return OperationListOut(items=[row.summary_out() for row in filtered], groups=groups)


def _operation_groups(rows: list[OperationSpec]) -> list[OperationGroupOut]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row.category_name, []).append(row.name)
    return [
        OperationGroupOut(
            category=category,
            count=len(operation_names),
            operation_names=operation_names,
        )
        for category, operation_names in sorted(grouped.items())
    ]


def build_operation_registry() -> OperationRegistry:
    from stackos.operations import (
        actions,
        agent_presets,
        agent_requests,
        auth,
        catalog,
        communications,
        cost,
        discovery,
        integrations,
        project_bootstrap,
        project_setup,
        readiness,
        resources,
        run_plans,
        runs,
        system,
        tool_profiles,
        workflow_templates,
    )
    from stackos.operations import tracker as tracker_operations

    registry = OperationRegistry()
    for spec in discovery.operation_specs():
        registry.register(spec)
    for spec in project_bootstrap.operation_specs():
        registry.register(spec)
    for spec in project_setup.operation_specs():
        registry.register(spec)
    for spec in readiness.operation_specs():
        registry.register(spec)
    for spec in agent_requests.operation_specs():
        registry.register(spec)
    for spec in auth.operation_specs():
        registry.register(spec)
    for spec in catalog.operation_specs():
        registry.register(spec)
    for spec in communications.operation_specs():
        registry.register(spec)
    for spec in tool_profiles.operation_specs():
        registry.register(spec)
    for spec in integrations.operation_specs():
        registry.register(spec)
    for spec in actions.operation_specs():
        registry.register(spec)
    for spec in cost.operation_specs():
        registry.register(spec)
    for spec in agent_presets.operation_specs():
        registry.register(spec)
    for spec in workflow_templates.operation_specs():
        registry.register(spec)
    for spec in run_plans.operation_specs():
        registry.register(spec)
    for spec in runs.operation_specs():
        registry.register(spec)
    for spec in system.operation_specs():
        registry.register(spec)
    for spec in resources.operation_specs():
        registry.register(spec)
    for spec in tracker_operations.operation_specs():
        registry.register(spec)
    return registry


__all__ = ["OperationRegistry", "build_operation_registry"]
