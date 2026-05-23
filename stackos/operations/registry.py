"""Central StackOS operation registry."""

from __future__ import annotations

from collections.abc import Iterable

from stackos.operations.spec import OperationListOut, OperationSpec
from stackos.repositories.base import NotFoundError


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

    def list_out(self, *, surface: str | None = None) -> OperationListOut:
        rows: Iterable[OperationSpec] = self.all() if surface is None else self.by_surface(surface)
        return OperationListOut(items=[row.summary_out() for row in rows])


def build_operation_registry() -> OperationRegistry:
    from stackos.operations import actions, agent_requests, communications, run_plans

    registry = OperationRegistry()
    for spec in agent_requests.operation_specs():
        registry.register(spec)
    for spec in communications.operation_specs():
        registry.register(spec)
    for spec in actions.operation_specs():
        registry.register(spec)
    for spec in run_plans.operation_specs():
        registry.register(spec)
    return registry


__all__ = ["OperationRegistry", "build_operation_registry"]
