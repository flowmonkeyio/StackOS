"""Communication surfaces, contacts, memberships, targets, and routes."""

from __future__ import annotations

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.resources import ResourceRepository

from .schemas import (
    CommunicationContactListInput,
    CommunicationContactOut,
    CommunicationContactUpsertInput,
    CommunicationMembershipListInput,
    CommunicationMembershipOut,
    CommunicationMembershipUpsertInput,
    CommunicationRouteListInput,
    CommunicationRouteOut,
    CommunicationRouteUpsertInput,
    CommunicationSurfaceListInput,
    CommunicationSurfaceOut,
    CommunicationSurfaceUpsertInput,
    CommunicationTargetListInput,
    CommunicationTargetOut,
    CommunicationTargetResolveInput,
    CommunicationTargetResolveOut,
    CommunicationTargetUpsertInput,
)
from .utils import (
    _communication_contact_out,
    _communication_contact_ref,
    _communication_membership_out,
    _communication_route_out,
    _communication_route_ref,
    _communication_surface_out,
    _communication_target_out,
    _communication_target_ref,
    _default_action_ref,
    _membership_ref,
    _record_by_resource_external_id,
    _require_project,
    _string_list,
    _surface_external_id,
    _target_action_defaults,
    _target_policy_allowed,
    _target_policy_profile_ref,
    _validate_no_setup_secrets,
    _validate_profile_key,
    _validate_provider_key,
    _validate_ref,
)


async def communication_surface_upsert(
    inp: CommunicationSurfaceUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationSurfaceOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_ref(inp.surface_ref, "surface_ref")
    _validate_provider_key(inp.provider_key)
    _validate_no_setup_secrets(
        "communicationSurface.upsert",
        {
            "metadata_json": inp.metadata_json,
        },
    )
    data_json = {
        "surface_ref": inp.surface_ref.strip(),
        "channel_ref": inp.surface_ref.strip(),
        "provider_key": inp.provider_key.strip(),
        "kind": inp.kind.strip(),
        "display_name": inp.display_name,
        "credential_ref": inp.credential_ref,
        "safe_external_ref": inp.safe_external_ref,
        "ingest_enabled": inp.ingest_enabled,
        "send_enabled": inp.send_enabled,
        "capabilities": inp.capabilities,
        "audience": inp.audience,
        "intent": inp.intent,
        "agent_guidance": inp.agent_guidance,
        "data_scope": inp.data_scope,
        "external_context": inp.external_context,
        "metadata_json": inp.metadata_json,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-channel",
        external_id=_surface_external_id(inp.surface_ref),
        title=inp.display_name or inp.surface_ref.strip(),
        data_json=data_json,
        provenance_json={"source": "communicationSurface.upsert"},
    )
    return WriteEnvelope(
        data=_communication_surface_out(env.data.id, env.data.project_id, env.data.data_json),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def communication_surface_list(
    inp: CommunicationSurfaceListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[CommunicationSurfaceOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-channel",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    items = []
    for record in records.items:
        data = record.data_json or {}
        if inp.provider_key is not None and data.get("provider_key") != inp.provider_key:
            continue
        if inp.kind is not None and data.get("kind") != inp.kind:
            continue
        items.append(_communication_surface_out(record.id, record.project_id, data))
    return Page(items=items, next_cursor=records.next_cursor, total_estimate=len(items))


async def communication_contact_upsert(
    inp: CommunicationContactUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationContactOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    data_json = {
        "contact_ref": _communication_contact_ref(inp.key),
        "key": inp.key.strip(),
        "display_name": inp.display_name.strip(),
        "kind": inp.kind,
        "status": inp.status,
        "provider_refs": inp.provider_refs,
        "safe_external_refs": inp.safe_external_refs,
        "metadata_json": inp.metadata_json,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-contact",
        external_id=_communication_contact_ref(inp.key),
        title=inp.display_name.strip(),
        data_json=data_json,
        provenance_json={"source": "communicationContact.upsert"},
    )
    return WriteEnvelope(
        data=_communication_contact_out(env.data.id, env.data.project_id, env.data.data_json),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def communication_contact_list(
    inp: CommunicationContactListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[CommunicationContactOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-contact",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    items = []
    for record in records.items:
        data = record.data_json or {}
        if inp.kind is not None and data.get("kind") != inp.kind:
            continue
        if inp.status is not None and data.get("status") != inp.status:
            continue
        items.append(_communication_contact_out(record.id, record.project_id, data))
    return Page(items=items, next_cursor=records.next_cursor, total_estimate=len(items))


async def communication_membership_upsert(
    inp: CommunicationMembershipUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationMembershipOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_ref(inp.surface_ref, "surface_ref")
    _validate_ref(inp.member_ref, "member_ref")
    _validate_provider_key(inp.provider_key)
    data_json = {
        "membership_ref": _membership_ref(inp.surface_ref, inp.member_ref),
        "surface_ref": inp.surface_ref.strip(),
        "member_ref": inp.member_ref.strip(),
        "provider_key": inp.provider_key.strip(),
        "membership_kind": inp.membership_kind,
        "status": inp.status,
        "roles": inp.roles,
        "permissions": inp.permissions,
        "scope_status": inp.scope_status,
        "metadata_json": inp.metadata_json,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-membership",
        external_id=_membership_ref(inp.surface_ref, inp.member_ref),
        title=f"{inp.member_ref.strip()} in {inp.surface_ref.strip()}",
        data_json=data_json,
        provenance_json={"source": "communicationMembership.upsert"},
    )
    return WriteEnvelope(
        data=_communication_membership_out(env.data.id, env.data.project_id, env.data.data_json),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def communication_membership_list(
    inp: CommunicationMembershipListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[CommunicationMembershipOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-membership",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    items = []
    for record in records.items:
        data = record.data_json or {}
        if inp.surface_ref is not None and data.get("surface_ref") != inp.surface_ref:
            continue
        if inp.member_ref is not None and data.get("member_ref") != inp.member_ref:
            continue
        if inp.provider_key is not None and data.get("provider_key") != inp.provider_key:
            continue
        if inp.status is not None and data.get("status") != inp.status:
            continue
        items.append(_communication_membership_out(record.id, record.project_id, data))
    return Page(items=items, next_cursor=records.next_cursor, total_estimate=len(items))


async def communication_target_upsert(
    inp: CommunicationTargetUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationTargetOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    _validate_provider_key(inp.provider_key)
    _validate_ref(inp.surface_ref, "surface_ref")
    _validate_no_setup_secrets(
        "communicationTarget.upsert",
        {
            "action_input_defaults": inp.action_input_defaults,
            "metadata_json": inp.metadata_json,
        },
    )
    data_json = {
        "target_ref": _communication_target_ref(inp.key),
        "key": inp.key.strip(),
        "display_name": inp.display_name,
        "provider_key": inp.provider_key.strip(),
        "surface_ref": inp.surface_ref.strip(),
        "profile_ref": inp.profile_ref,
        "thread_ref": inp.thread_ref,
        "enabled": inp.enabled,
        "action_ref": inp.action_ref or _default_action_ref(inp.provider_key),
        "action_input_defaults": inp.action_input_defaults,
        "send_policy": inp.send_policy,
        "metadata_json": inp.metadata_json,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-target",
        external_id=_communication_target_ref(inp.key),
        title=inp.display_name or inp.key.strip(),
        data_json=data_json,
        provenance_json={"source": "communicationTarget.upsert"},
    )
    return WriteEnvelope(
        data=_communication_target_out(env.data.id, env.data.project_id, env.data.data_json),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def communication_target_list(
    inp: CommunicationTargetListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[CommunicationTargetOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-target",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    items = []
    for record in records.items:
        data = record.data_json or {}
        if inp.provider_key is not None and data.get("provider_key") != inp.provider_key:
            continue
        if inp.profile_ref is not None and data.get("profile_ref") != inp.profile_ref:
            continue
        items.append(_communication_target_out(record.id, record.project_id, data))
    return Page(items=items, next_cursor=records.next_cursor, total_estimate=len(items))


async def communication_target_resolve(
    inp: CommunicationTargetResolveInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CommunicationTargetResolveOut:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    row = _record_by_resource_external_id(
        ctx.session,
        project_id=inp.project_id,
        resource_key="communication-target",
        external_id=_communication_target_ref(inp.key),
    )
    if row is None:
        raise ValidationError("communication target was not found")
    target = _communication_target_out(row.id, row.project_id, row.data_json or {})
    policy_profile_ref = _target_policy_profile_ref(target, inp.profile_ref)
    allowed, reason = _target_policy_allowed(
        target.send_policy,
        target_ref=target.target_ref,
        profile_ref=policy_profile_ref,
        source_surface_ref=inp.source_surface_ref,
        invoker_ref=inp.invoker_ref,
    )
    if not target.enabled:
        allowed = False
        reason = "target_disabled"
    notes = [
        "This resolver does not send messages. It returns provider action refs and "
        "safe defaults for planning/debugging; normal agent delivery should use "
        "communication.send or communication.reply."
    ]
    if policy_profile_ref is not None:
        notes.append(
            "Target policy was evaluated with the same target/default profile selection "
            "communication.send uses when from is omitted."
        )
    elif reason == "profile_not_allowed":
        notes.append(
            "Pass profile_ref to debug a specific actor, or call communication.send with "
            "from set to a configured communication profile."
        )
    if target.provider_key in {"slack-bot", "smtp", "imap"}:
        notes.append("Provider connector execution depends on the installed provider action.")
    return CommunicationTargetResolveOut(
        project_id=inp.project_id,
        target=target,
        allowed=allowed,
        denial_reason=reason,
        action_ref=target.action_ref,
        provider_key=target.provider_key,
        surface_ref=target.surface_ref,
        thread_ref=target.thread_ref,
        policy_profile_ref=policy_profile_ref,
        action_input_defaults=_target_action_defaults(ctx.session, target),
        notes=notes,
    )


async def communication_route_upsert(
    inp: CommunicationRouteUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationRouteOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    _validate_no_setup_secrets(
        "communicationRoute.upsert",
        {
            "metadata_json": inp.metadata_json,
        },
    )
    data_json = {
        "route_ref": _communication_route_ref(inp.key),
        "key": inp.key.strip(),
        "enabled": inp.enabled,
        "source_surface_refs": inp.source_surface_refs,
        "target_refs": inp.target_refs,
        "allowed_profile_refs": inp.allowed_profile_refs,
        "requires_approval": inp.requires_approval,
        "field_policy": inp.field_policy,
        "metadata_json": inp.metadata_json,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-route",
        external_id=_communication_route_ref(inp.key),
        title=inp.key.strip(),
        data_json=data_json,
        provenance_json={"source": "communicationRoute.upsert"},
    )
    return WriteEnvelope(
        data=_communication_route_out(env.data.id, env.data.project_id, env.data.data_json),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def communication_route_list(
    inp: CommunicationRouteListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[CommunicationRouteOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-route",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    items = []
    for record in records.items:
        data = record.data_json or {}
        if inp.source_surface_ref is not None and inp.source_surface_ref not in _string_list(
            data.get("source_surface_refs")
        ):
            continue
        if inp.target_ref is not None and inp.target_ref not in _string_list(
            data.get("target_refs")
        ):
            continue
        if inp.profile_ref is not None and inp.profile_ref not in _string_list(
            data.get("allowed_profile_refs")
        ):
            continue
        items.append(_communication_route_out(record.id, record.project_id, data))
    return Page(items=items, next_cursor=records.next_cursor, total_estimate=len(items))
