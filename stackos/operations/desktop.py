"""Native-only availability transport contract; no JSON presence setter."""

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ValidationError

AVAILABILITY_PATH = "/api/v1/desktop/availability"


async def stream_only(_inp: MCPInput, _ctx: MCPContext, _emit: ProgressEmitter) -> None:
    raise ValidationError(
        "Native desktop availability requires the authenticated streaming transport.",
        data={"method": "POST", "path": AVAILABILITY_PATH},
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="desktop.availability",
            summary="Hold native desktop availability only for the lifetime of this connection.",
            input_model=MCPInput,
            output_model=dict,
            handler=stream_only,
            surfaces=OperationSurfaces(
                rest=OperationSurface(
                    enabled=True,
                    path=AVAILABILITY_PATH,
                    notes="POST stream; daemon-admin token only. Not a JSON operation call.",
                )
            ),
            purpose=(
                "Electron main holds the stream while awake; closing it removes "
                "future workflow timers."
            ),
            prerequisites=(
                "Native local daemon authentication; browser console tokens are refused.",
            ),
            returns=("Transient ready event; no persisted presence, replay, heartbeat or lease.",),
            examples=(OperationExample(title="Native main-process stream", arguments={}),),
            mutating=True,
            grant_policy="local-admin-write",
            category="system",
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("raw",),
                raw_only_reason=(
                    "Connection lifetime is the transport; JSON compact/ack cannot represent it."
                ),
            ),
        )
    ]
