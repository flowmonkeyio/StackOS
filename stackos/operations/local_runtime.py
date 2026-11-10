"""Finite computer authorization and identified host invocation exchange."""

from pydantic import Field

from stackos.local_runtime.exchange import InvocationReport
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

SETUP_OPERATIONS = frozenset(
    {"localRuntime.authorize", "localRuntime.list", "localRuntime.status", "localRuntime.revoke"}
)
EXCHANGE_OPERATIONS = frozenset({"localRuntime.poll", "localRuntime.report"})
CONNECTION_OPERATIONS = SETUP_OPERATIONS | EXCHANGE_OPERATIONS
RUNTIME_OPERATIONS = frozenset({"localRuntime.status", "localRuntime.revoke"}) | EXCHANGE_OPERATIONS


class ConnectionInput(MCPInput):
    project_id: int = Field(gt=0)


class ConnectionRefInput(ConnectionInput):
    connection_id: str = Field(min_length=36, max_length=36)


class PollInput(ConnectionRefInput):
    invocation_id: str | None = Field(default=None, min_length=1, max_length=64)


class ReportInput(ConnectionRefInput):
    report: InvocationReport


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


def _exchange_connection(inp, ctx):
    if ctx.extras.get("auth_scope") != "local-runtime":
        raise ValidationError("invocation exchange requires the bound computer credential")
    row = LocalRuntimeConnectionRepository(ctx.session).owned(inp.project_id, inp.connection_id)
    if (
        row.server_origin != ctx.extras["settings"].server_url
        or LocalRuntimeConnectionRepository.public(row)["status"] != "authorized"
    ):
        raise ValidationError("computer authorization is unavailable")
    return row


async def poll(inp, ctx, _emit):
    return ctx.extras["local_invocations"].poll(_exchange_connection(inp, ctx), inp.invocation_id)


async def report(inp, ctx, _emit):
    return ctx.extras["local_invocations"].report(_exchange_connection(inp, ctx), inp.report)


def operation_specs() -> list[OperationSpec]:
    specs = []
    for name, handler, model, mutating in (
        ("authorize", authorize, ConnectionInput, True),
        ("list", list_connections, ConnectionInput, False),
        ("status", status, ConnectionRefInput, False),
        ("revoke", revoke, ConnectionRefInput, True),
        ("poll", poll, PollInput, True),
        ("report", report, ReportInput, True),
    ):
        full_name = f"localRuntime.{name}"
        specs.append(
            OperationSpec(
                name=full_name,
                summary=f"{name.capitalize()} one bound computer's invocation."
                if full_name in EXCHANGE_OPERATIONS
                else f"{name.capitalize()} one owned project's computer authorization.",
                input_model=model,
                output_model=dict,
                handler=handler,
                surfaces=OperationSurfaces(
                    rest=OperationSurface(
                        enabled=True,
                        browser_safe=full_name not in EXCHANGE_OPERATIONS,
                        path=f"/api/v1/operations/{full_name}/call",
                    )
                ),
                purpose="Exchange an identified invocation without arbitrary tool or file RPC."
                if full_name in EXCHANGE_OPERATIONS
                else "Authorize a local runtime without granting server filesystem access.",
                prerequisites=("Bound computer credential; existing controller-owned invocation.",)
                if full_name in EXCHANGE_OPERATIONS
                else ("Verified server owner session; existing private project.",),
                returns=(
                    "Identity, state and needs_attention; poll returns a launch once.",
                    "One terminal result; exact duplicate acknowledges replay; conflict refuses.",
                )
                if full_name in EXCHANGE_OPERATIONS
                else (
                    "Safe owner/environment/project/connection identity and status.",
                    "Authorize returns a scoped credential once; store only on this computer.",
                ),
                examples=(
                    OperationExample(
                        title="Owned project",
                        arguments={"project_id": 1}
                        | (
                            {"connection_id": "00000000-0000-0000-0000-000000000001"}
                            if issubclass(model, ConnectionRefInput)
                            else {}
                        )
                        | (
                            {
                                "report": {
                                    "invocation_id": "existing-host-invocation",
                                    "attempt": 1,
                                    "state": "unknown",
                                }
                            }
                            if name == "report"
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
                    raw_only_reason="Keep invocation identity, replay and uncertainty visible."
                    if full_name in EXCHANGE_OPERATIONS
                    else "Return connection identity; never replay credentials.",
                ),
            )
        )
    return specs
