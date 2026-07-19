"""Shared operation spec builders for run-plan operations."""

from __future__ import annotations

from stackos.operations.spec import (
    OperationSurface,
    OperationSurfaces,
)


def _surfaces(name: str, command: str | None = None) -> OperationSurfaces:
    rest_path = f"/api/v1/operations/{name}/call"
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=rest_path),
        cli=OperationSurface(enabled=True, command=command or f"ops call {name}"),
    )


__all__ = ["_surfaces"]
