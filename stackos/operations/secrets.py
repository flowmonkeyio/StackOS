"""Write-only MCP ingress for action payload string values."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.secrets import PayloadSecretRepository


class SecretSetInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int | None = None
    value: SecretStr = Field(
        description=(
            "Write-only string value. StackOS returns an opaque reference and never echoes "
            "this value."
        ),
        json_schema_extra={"writeOnly": True},
    )


class SecretSetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_ref: str = Field(description="Opaque project-scoped action payload reference.")


async def secret_set(
    inp: SecretSetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[SecretSetOut]:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    if project_id is None:
        raise ValidationError(
            "project_id is required unless the agent bridge resolved the workspace project"
        )
    row = PayloadSecretRepository(ctx.session).set(
        project_id=project_id,
        value=inp.value.get_secret_value(),
    )
    return WriteEnvelope[SecretSetOut](
        data=SecretSetOut(secret_ref=row.secret_ref),
        project_id=project_id,
        run_id=ctx.run_id,
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="secret.set",
            summary="Store one write-only action payload string and return an opaque reference.",
            input_model=SecretSetInput,
            output_model=WriteEnvelope[SecretSetOut],
            handler=secret_set,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(enabled=False),
                cli=OperationSurface(enabled=False),
            ),
            purpose=(
                "Use this immediately before an action needs a tenant password, token, or "
                "other sensitive string in input_json. Put the returned ref in the exact "
                "payload marker; do not create a Connection for payload data."
            ),
            when_to_use=(
                "An action payload requires a sensitive string that is not provider "
                "authentication.",
            ),
            prerequisites=(
                "The MCP bridge must be bound to the target project.",
                "Pass the value once through this write-only field, then retain only the ref.",
            ),
            returns=("One opaque project-scoped ref and no plaintext value.",),
            examples=(
                OperationExample(
                    title="Store one write-only action payload value",
                    arguments={"value": "<value-from-authorized-input>"},
                ),
            ),
            mutating=True,
            grant_policy="direct-secret-write",
            secret_policy="write-only-input-no-secret-output",
            category="actions",
            response_policy=OperationResponsePolicy(
                default_mode="compact",
                allowed_modes=("compact", "raw"),
                ack_safe=False,
            ),
        )
    ]


__all__ = ["SecretSetInput", "SecretSetOut", "operation_specs", "secret_set"]
