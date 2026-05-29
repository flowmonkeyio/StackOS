"""Delivery execution through the canonical action repository path."""

from __future__ import annotations

from typing import Any

from stackos.actions import ActionRepository
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope

from .errors import _reject
from .schemas import CommunicationFallbackInput, CommunicationSendOut
from .utils import _first_str


async def _execute_delivery(
    ctx: MCPContext,
    *,
    project_id: int,
    operation: str,
    action_ref: str,
    input_json: dict[str, Any],
    credential_ref: str,
    idempotency_key: str,
    dry_run: bool,
    metadata_json: dict[str, Any],
    resolved: dict[str, Any],
    target_ref: str | None,
    actor_ref: str | None,
    surface_ref: str | None,
    fallback: CommunicationFallbackInput,
) -> WriteEnvelope[CommunicationSendOut]:
    if fallback.mode != "reject":
        _reject(
            code="COMM_FALLBACK_UNSUPPORTED",
            category="input",
            message="Only fallback.mode=reject is currently supported.",
            resolved={**resolved, "fallback_mode": fallback.mode},
            failed_paths=[{"path": "/fallback/mode", "requested": fallback.mode}],
        )
    settings = ctx.extras.get("settings")
    asset_dir = getattr(settings, "generated_assets_dir", None)
    env = await ActionRepository(ctx.session, asset_dir=asset_dir).execute(
        project_id=project_id,
        action_ref=action_ref,
        input_json=input_json,
        credential_ref=credential_ref,
        run_id=ctx.run_id,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
        metadata_json={
            **metadata_json,
            "dedupe_source": "communication-operation",
        },
    )
    output = env.data.output_json or {}
    out = CommunicationSendOut(
        status="validated" if dry_run else str(output.get("status") or "sent"),
        action_call_id=env.data.action_call.id,
        action_ref=action_ref,
        provider_key=str(env.data.action_call.provider_key or resolved.get("provider_key") or ""),
        target_ref=target_ref,
        actor_ref=actor_ref,
        surface_ref=surface_ref or _first_str(output, "channel_ref", "chat_ref", "surface_ref"),
        thread_ref=_first_str(output, "thread_ref"),
        message_ref=_first_str(output, "message_ref"),
        message_refs=_string_list(output.get("message_refs")),
        file_ref=_first_str(output, "file_ref"),
        file_refs=_string_list(output.get("file_refs")),
        attachment_refs=_string_list(output.get("attachment_refs")),
        local_artifact_deleted=(
            output.get("local_artifact_deleted")
            if isinstance(output.get("local_artifact_deleted"), bool)
            else None
        ),
        dry_run=dry_run,
        effects=(
            [
                "validated provider payload",
                "created dry-run action_call audit row",
                "did not call provider connector",
            ]
            if dry_run
            else ["called provider connector", "created action_call audit row"]
        ),
        resolved=resolved,
    )
    return WriteEnvelope(data=out, run_id=env.run_id, project_id=env.project_id)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]
