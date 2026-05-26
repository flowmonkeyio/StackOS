"""Stored communication history query operation."""

from __future__ import annotations

from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository

from .constants import _CONTEXT_ALLOWED_FIELDS
from .schemas import (
    CommunicationContextMessageOut,
    CommunicationContextQueryInput,
    CommunicationContextQueryOut,
)
from .utils import _require_project, _select_context_fields


async def communication_context_query(
    inp: CommunicationContextQueryInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CommunicationContextQueryOut:
    _require_project(ctx.session, inp.project_id)
    if inp.limit < 1 or inp.limit > 100:
        raise ValidationError("limit must be between 1 and 100")
    unsupported = sorted(set(inp.fields) - _CONTEXT_ALLOWED_FIELDS)
    if unsupported:
        raise ValidationError(
            "communicationContext.query fields are not supported",
            data={
                "fields": unsupported,
                "allowed_fields": sorted(_CONTEXT_ALLOWED_FIELDS),
            },
        )
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        limit=200,
    )
    filtered = []
    for record in records.items:
        if inp.before_record_id is not None and record.id >= inp.before_record_id:
            continue
        data = record.data_json or {}
        if inp.provider_key is not None and data.get("provider_key") != inp.provider_key:
            continue
        if inp.profile_ref is not None and data.get("profile_ref") != inp.profile_ref:
            continue
        if inp.profile_key is not None and data.get("profile_key") != inp.profile_key:
            continue
        surface = inp.surface_ref or inp.channel_ref
        if surface is not None and surface not in {
            data.get("surface_ref"),
            data.get("channel_ref"),
        }:
            continue
        if inp.thread_ref is not None and data.get("thread_ref") != inp.thread_ref:
            continue
        if inp.direction is not None and data.get("direction") != inp.direction:
            continue
        filtered.append(record)
    selected = filtered[-inp.limit :]
    return CommunicationContextQueryOut(
        project_id=inp.project_id,
        history_source=inp.history_source,
        items=[
            CommunicationContextMessageOut(
                record_id=record.id,
                created_at=record.created_at.isoformat(),
                updated_at=record.updated_at.isoformat(),
                fields=_select_context_fields(record.data_json or {}, inp.fields),
            )
            for record in selected
        ],
        filters={
            "provider_key": inp.provider_key,
            "profile_ref": inp.profile_ref,
            "profile_key": inp.profile_key,
            "surface_ref": inp.surface_ref,
            "channel_ref": inp.channel_ref,
            "thread_ref": inp.thread_ref,
            "direction": inp.direction,
            "before_record_id": inp.before_record_id,
        },
        notes=[
            "Only stored StackOS communication history is returned. Live provider history "
            "fetching must be implemented as explicit provider actions with provider scopes, "
            "pagination, rate-limit handling, and audit."
        ],
    )
