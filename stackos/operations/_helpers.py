"""Small helpers for operation-spec modules."""

from __future__ import annotations

from typing import Any

from stackos.mcp.contract import MCPInput
from stackos.operations.spec import (
    OperationExample,
    OperationHandler,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)


def operation_spec(
    *,
    name: str,
    summary: str,
    input_model: type[MCPInput],
    output_model: Any,
    handler: OperationHandler,
    purpose: str,
    grant_policy: str,
    mutating: bool | None = None,
    rest_path: str | None = None,
    cli_command: str | None = None,
    when_to_use: tuple[str, ...] = (),
    prerequisites: tuple[str, ...] = (),
    returns: tuple[str, ...] = (),
    examples: tuple[OperationExample, ...] = (),
    secret_policy: str = "no-secret-output",
    category: str | None = None,
) -> OperationSpec:
    return OperationSpec(
        name=name,
        summary=summary,
        input_model=input_model,
        output_model=output_model,
        handler=handler,
        surfaces=OperationSurfaces(
            mcp=OperationSurface(enabled=True),
            rest=OperationSurface(
                enabled=True,
                path=rest_path or f"/api/v1/operations/{name}/call",
            ),
            cli=OperationSurface(enabled=True, command=cli_command or f"ops call {name}"),
        ),
        purpose=purpose,
        when_to_use=when_to_use,
        prerequisites=prerequisites,
        returns=returns,
        examples=examples,
        mutating=mutating,
        grant_policy=grant_policy,
        secret_policy=secret_policy,
        category=category,
    )


__all__ = ["operation_spec"]
