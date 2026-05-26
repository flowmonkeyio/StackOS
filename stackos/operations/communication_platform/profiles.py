"""Communication profile setup operations."""

from __future__ import annotations

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.resources import ResourceRepository

from .schemas import (
    CommunicationProfileGetInput,
    CommunicationProfileListInput,
    CommunicationProfileOut,
    CommunicationProfileUpsertInput,
)
from .utils import (
    _communication_profile_out,
    _communication_profile_ref,
    _record_by_resource_external_id,
    _require_project,
    _validate_identity,
    _validate_no_setup_secrets,
    _validate_profile_key,
)


async def communication_profile_upsert(
    inp: CommunicationProfileUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationProfileOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    _validate_identity(inp.identity)
    _validate_no_setup_secrets(
        "communicationProfile.upsert",
        {
            "provider_facets": inp.provider_facets,
            "metadata_json": inp.metadata_json,
        },
    )
    data_json = {
        "key": inp.key.strip(),
        "profile_ref": _communication_profile_ref(inp.key),
        "enabled": inp.enabled,
        "identity": inp.identity,
        "agent_guidance": inp.agent_guidance,
        "provider_facets": inp.provider_facets,
        "access_policy": inp.access_policy,
        "visibility_policy": inp.visibility_policy,
        "trigger_policy": inp.trigger_policy,
        "context_policy": inp.context_policy,
        "response_policy": inp.response_policy,
        "send_policy": inp.send_policy,
        "handoff_policy": inp.handoff_policy,
        "approval_policy": inp.approval_policy,
        "metadata_json": inp.metadata_json,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id=_communication_profile_ref(inp.key),
        title=str(inp.identity.get("display_name") or inp.key.strip()),
        data_json=data_json,
        provenance_json={"source": "communicationProfile.upsert"},
    )
    return WriteEnvelope(
        data=_communication_profile_out(env.data.id, env.data.project_id, env.data.data_json),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def communication_profile_get(
    inp: CommunicationProfileGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CommunicationProfileOut:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    row = _record_by_resource_external_id(
        ctx.session,
        project_id=inp.project_id,
        resource_key="communication-profile",
        external_id=_communication_profile_ref(inp.key),
    )
    if row is None:
        raise ValidationError("communication profile was not found")
    return _communication_profile_out(row.id, row.project_id, row.data_json or {})


async def communication_profile_list(
    inp: CommunicationProfileListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[CommunicationProfileOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    return Page(
        items=[
            _communication_profile_out(record.id, record.project_id, record.data_json or {})
            for record in records.items
        ],
        next_cursor=records.next_cursor,
        total_estimate=records.total_estimate,
    )
