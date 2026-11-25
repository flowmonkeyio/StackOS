"""Explicit downloads of files owned by existing Artifact and ActionCall rows."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from stackos.artifacts.files import resolve_file
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationResponsePolicy
from stackos.repositories.base import ValidationError


class ArtifactDownloadInput(MCPInput):
    project_id: int
    artifact_id: int = Field(gt=0)
    destination_path: str | None = Field(
        default=None,
        description=(
            "Optional absolute NEW local file path. The native MCP bridge writes it; "
            "the server never does. Omit for an authenticated download URI."
        ),
    )


class ActionOutputDownloadInput(MCPInput):
    project_id: int
    action_call_id: int = Field(gt=0)
    destination_path: str | None = Field(
        default=None,
        description="Optional absolute NEW local file path, written only by the native MCP bridge.",
    )


async def download(
    inp: ArtifactDownloadInput | ActionOutputDownloadInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> dict[str, Any]:
    if inp.destination_path is not None:
        raise ValidationError(
            "destination_path requires the local native MCP bridge; "
            "omit it for an authenticated download URI"
        )
    return resolve_file(
        ctx.session,
        ctx.extras["settings"],
        project_id=inp.project_id,
        source="artifact" if isinstance(inp, ArtifactDownloadInput) else "action-output",
        file_id=inp.artifact_id if isinstance(inp, ArtifactDownloadInput) else inp.action_call_id,
    ).descriptor()


def operation_specs():
    return [
        operation_spec(
            name=name,
            summary="Download one authorized managed file explicitly.",
            input_model=model,
            output_model=dict[str, Any],
            handler=download,
            purpose=(
                "Use the native MCP bridge with destination_path to get a real local file, "
                "or omit it for an authenticated server download URI. "
                "Never treat a server file path as local."
            ),
            grant_policy="direct-read",
            mutating=False,
            browser_safe=True,
            category="resources",
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("raw",),
                raw_only_reason=(
                    "Download identity, checksum and materialization outcome must remain visible."
                ),
            ),
            examples=(
                OperationExample(
                    title="Explicit local download",
                    arguments={
                        "project_id": 1,
                        field: 1,
                        "destination_path": "/absolute/new-result.bin",
                    },
                ),
            ),
            returns=(
                "Authenticated relative download URI, exact byte size and SHA-256. "
                "The native bridge adds local_path only after verifying the downloaded bytes. "
                "Existing local files are never overwritten.",
            ),
        )
        for name, model, field in (
            ("artifact.download", ArtifactDownloadInput, "artifact_id"),
            ("actionCall.download", ActionOutputDownloadInput, "action_call_id"),
        )
    ]
