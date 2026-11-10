"""Finite computer authorization operations. No filesystem or invocation RPC."""

from pydantic import Field

from stackos.mcp.contract import MCPInput
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.workspaces import LocalRuntimeConnectionRepository

CONNECTION_OPERATIONS = frozenset(
    {"localRuntime.authorize", "localRuntime.list", "localRuntime.status", "localRuntime.revoke"}
)
RUNTIME_OPERATIONS = frozenset({"localRuntime.status", "localRuntime.revoke"})


class ConnectionInput(MCPInput):
    project_id: int = Field(gt=0)


class ConnectionRefInput(ConnectionInput):
    connection_id: str = Field(min_length=36, max_length=36)


async def authorize(inp, ctx, _emit):
    return LocalRuntimeConnectionRepository(ctx.session).authorize(
        inp.project_id, ctx.extras["settings"].server_url
    )


async def list_connections(inp, ctx, _emit):
    return {"items": LocalRuntimeConnectionRepository(ctx.session).list(inp.project_id)}


async def status(inp, ctx, _emit):
    repo = LocalRuntimeConnectionRepository(ctx.session)
    row = repo.owned(inp.project_id, inp.connection_id)
    if row.server_origin != ctx.extras["settings"].server_url:
        raise ValidationError("connection belongs to another server")
    return repo.public(row)


async def revoke(inp, ctx, _emit):
    return LocalRuntimeConnectionRepository(ctx.session).revoke(inp.project_id, inp.connection_id)


def operation_specs() -> list[OperationSpec]:
    specs = []
    for name, handler, model, mutating in (
        ("authorize", authorize, ConnectionInput, True),
        ("list", list_connections, ConnectionInput, False),
        ("status", status, ConnectionRefInput, False),
        ("revoke", revoke, ConnectionRefInput, True),
    ):
        full_name = f"localRuntime.{name}"
        specs.append(
            OperationSpec(
                name=full_name,
                summary=f"{name.capitalize()} one owned project's computer authorization.",
                input_model=model,
                output_model=dict,
                handler=handler,
                surfaces=OperationSurfaces(
                    rest=OperationSurface(
                        enabled=True,
                        browser_safe=True,
                        path=f"/api/v1/operations/{full_name}/call",
                    )
                ),
                purpose="Authorize a local runtime without granting server filesystem access.",
                prerequisites=("Verified server owner session; existing private project.",),
                returns=(
                    "Safe owner/environment/project/connection identity and status.",
                    "Authorize returns a scoped credential once; store only on this computer.",
                ),
                examples=(
                    OperationExample(
                        title="Owned project",
                        arguments={"project_id": 1}
                        | (
                            {"connection_id": "00000000-0000-0000-0000-000000000001"}
                            if model is ConnectionRefInput
                            else {}
                        ),
                    ),
                ),
                mutating=mutating,
                grant_policy="verified-owner-connection",
                secret_policy="scoped-runtime-credential"
                if name == "authorize"
                else "no-secret-output",
                idempotency_policy="handler",
                response_policy=OperationResponsePolicy(
                    default_mode="raw",
                    allowed_modes=("raw",),
                    raw_only_reason="Return connection identity; never replay credentials.",
                ),
            )
        )
    return specs
