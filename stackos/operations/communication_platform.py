"""Provider-neutral communication platform setup and query operations."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRepository

_PROFILE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


class CommunicationProfileUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "support",
                "identity": {
                    "display_name": "Support Agent",
                    "purpose": "Coordinate customer support across chat and email.",
                    "voice": "Calm, explicit, and concise.",
                },
                "provider_facets": {
                    "telegram-bot": {"bot_profile_key": "support-bot"},
                    "slack-bot": {"bot_user_id": "U123"},
                },
                "send_policy": {
                    "mode": "explicit-targets",
                    "allowed_target_refs": ["communication-target:internal-support"],
                },
            }
        },
    )

    project_id: int
    key: str
    enabled: bool = True
    identity: dict[str, Any]
    agent_guidance: dict[str, Any] = Field(default_factory=dict)
    provider_facets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    access_policy: dict[str, Any] = Field(default_factory=dict)
    visibility_policy: dict[str, Any] = Field(default_factory=dict)
    trigger_policy: dict[str, Any] = Field(default_factory=dict)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    response_policy: dict[str, Any] = Field(default_factory=dict)
    send_policy: dict[str, Any] = Field(default_factory=lambda: {"mode": "explicit-targets"})
    handoff_policy: dict[str, Any] = Field(default_factory=lambda: {"mode": "explicit-targets"})
    approval_policy: dict[str, Any] = Field(default_factory=lambda: {"mode": "none"})
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CommunicationProfileGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    key: str


class CommunicationProfileListInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    limit: int | None = None
    after_id: int | None = None


class CommunicationProfileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    profile_ref: str
    key: str
    enabled: bool
    identity: dict[str, Any]
    agent_guidance: dict[str, Any]
    provider_facets: dict[str, dict[str, Any]]
    access_policy: dict[str, Any]
    visibility_policy: dict[str, Any]
    trigger_policy: dict[str, Any]
    context_policy: dict[str, Any]
    response_policy: dict[str, Any]
    send_policy: dict[str, Any]
    handoff_policy: dict[str, Any]
    approval_policy: dict[str, Any]
    metadata_json: dict[str, Any]


class CommunicationSurfaceUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "surface_ref": "slack-channel:C123",
                "provider_key": "slack-bot",
                "kind": "slack-channel",
                "display_name": "customer-issue-war-room",
                "capabilities": {"can_read": True, "can_write": True, "can_thread": True},
            }
        },
    )

    project_id: int
    surface_ref: str
    provider_key: str
    kind: str
    display_name: str | None = None
    credential_ref: str | None = None
    safe_external_ref: str | None = None
    ingest_enabled: bool = True
    send_enabled: bool = True
    capabilities: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CommunicationSurfaceListInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    provider_key: str | None = None
    kind: str | None = None
    limit: int | None = None
    after_id: int | None = None


class CommunicationSurfaceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    surface_ref: str
    channel_ref: str
    provider_key: str
    kind: str
    display_name: str | None = None
    credential_ref: str | None = None
    safe_external_ref: str | None = None
    ingest_enabled: bool
    send_enabled: bool
    capabilities: dict[str, Any]
    metadata_json: dict[str, Any]


class CommunicationContactUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "customer-acme",
                "display_name": "Acme Inc.",
                "kind": "organization",
                "provider_refs": {
                    "slack": ["slack-team:T123"],
                    "telegram": ["telegram-chat:-1001"],
                },
            }
        },
    )

    project_id: int
    key: str
    display_name: str
    kind: Literal["person", "customer", "team", "bot", "organization"] = "person"
    status: Literal["active", "inactive", "blocked", "unknown"] = "active"
    provider_refs: dict[str, list[str]] = Field(default_factory=dict)
    safe_external_refs: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CommunicationContactListInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    kind: str | None = None
    status: str | None = None
    limit: int | None = None
    after_id: int | None = None


class CommunicationContactOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    contact_ref: str
    key: str
    display_name: str
    kind: str
    status: str
    provider_refs: dict[str, list[str]]
    safe_external_refs: list[str]
    metadata_json: dict[str, Any]


class CommunicationMembershipUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "surface_ref": "slack-channel:C123",
                "member_ref": "communication-profile:support",
                "provider_key": "slack-bot",
                "membership_kind": "bot",
                "status": "joined",
                "roles": ["bot"],
                "permissions": {"can_read": True, "can_write": True},
            }
        },
    )

    project_id: int
    surface_ref: str
    member_ref: str
    provider_key: str
    membership_kind: Literal["profile", "contact", "bot", "user", "external"] = "profile"
    status: Literal["joined", "invited", "left", "removed", "unknown"] = "joined"
    roles: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    scope_status: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CommunicationMembershipListInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    surface_ref: str | None = None
    member_ref: str | None = None
    provider_key: str | None = None
    status: str | None = None
    limit: int | None = None
    after_id: int | None = None


class CommunicationMembershipOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    membership_ref: str
    surface_ref: str
    member_ref: str
    provider_key: str
    membership_kind: str
    status: str
    roles: list[str]
    permissions: dict[str, Any]
    scope_status: dict[str, Any]
    metadata_json: dict[str, Any]


class CommunicationTargetUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "internal-support",
                "display_name": "Internal support Slack channel",
                "provider_key": "slack-bot",
                "surface_ref": "slack-channel:C123",
                "action_ref": "communications.slack-bot.message.send",
                "send_policy": {
                    "mode": "explicit-target",
                    "allowed_profile_refs": ["communication-profile:support"],
                },
            }
        },
    )

    project_id: int
    key: str
    provider_key: str
    surface_ref: str
    display_name: str | None = None
    enabled: bool = True
    profile_ref: str | None = None
    thread_ref: str | None = None
    action_ref: str | None = None
    action_input_defaults: dict[str, Any] = Field(default_factory=dict)
    send_policy: dict[str, Any] = Field(default_factory=lambda: {"mode": "deny"})
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CommunicationTargetListInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    provider_key: str | None = None
    profile_ref: str | None = None
    limit: int | None = None
    after_id: int | None = None


class CommunicationTargetResolveInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "internal-support",
                "profile_ref": "communication-profile:support",
                "source_surface_ref": "telegram-chat:-1001",
            }
        },
    )

    project_id: int
    key: str
    profile_ref: str | None = None
    source_surface_ref: str | None = None


class CommunicationTargetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    target_ref: str
    key: str
    display_name: str | None = None
    provider_key: str
    surface_ref: str
    profile_ref: str | None = None
    thread_ref: str | None = None
    enabled: bool
    action_ref: str | None = None
    action_input_defaults: dict[str, Any]
    send_policy: dict[str, Any]
    metadata_json: dict[str, Any]


class CommunicationTargetResolveOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    target: CommunicationTargetOut
    allowed: bool
    denial_reason: str | None = None
    action_ref: str | None = None
    provider_key: str
    surface_ref: str
    thread_ref: str | None = None
    action_input_defaults: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


class CommunicationRouteUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "customer-issue-to-internal-support",
                "source_surface_refs": ["telegram-chat:-1001"],
                "target_refs": ["communication-target:internal-support"],
                "allowed_profile_refs": ["communication-profile:support"],
                "requires_approval": False,
            }
        },
    )

    project_id: int
    key: str
    enabled: bool = True
    source_surface_refs: list[str] = Field(default_factory=list)
    target_refs: list[str] = Field(default_factory=list)
    allowed_profile_refs: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    field_policy: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class CommunicationRouteListInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    source_surface_ref: str | None = None
    target_ref: str | None = None
    profile_ref: str | None = None
    limit: int | None = None
    after_id: int | None = None


class CommunicationRouteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    route_ref: str
    key: str
    enabled: bool
    source_surface_refs: list[str]
    target_refs: list[str]
    allowed_profile_refs: list[str]
    requires_approval: bool
    field_policy: dict[str, Any]
    metadata_json: dict[str, Any]


class CommunicationContextQueryInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "surface_ref": "slack-channel:C123",
                "thread_ref": "slack-thread:C123:1710000000.000100",
                "limit": 25,
                "fields": ["message_ref", "sender_ref", "text_preview"],
            }
        },
    )

    project_id: int
    provider_key: str | None = None
    profile_ref: str | None = None
    bot_profile_key: str | None = None
    surface_ref: str | None = None
    channel_ref: str | None = None
    thread_ref: str | None = None
    direction: Literal["inbound", "outbound"] | None = None
    before_record_id: int | None = None
    limit: int = 25
    fields: list[str] = Field(default_factory=lambda: ["message_ref", "text_preview"])
    history_source: Literal["stored"] = "stored"


class CommunicationContextMessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    created_at: str
    updated_at: str
    fields: dict[str, Any]


class CommunicationContextQueryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    history_source: str
    items: list[CommunicationContextMessageOut]
    filters: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


async def communication_profile_upsert(
    inp: CommunicationProfileUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationProfileOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    _validate_identity(inp.identity)
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


async def communication_surface_upsert(
    inp: CommunicationSurfaceUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationSurfaceOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_ref(inp.surface_ref, "surface_ref")
    _validate_provider_key(inp.provider_key)
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
    allowed, reason = _target_policy_allowed(
        target.send_policy,
        profile_ref=inp.profile_ref,
        source_surface_ref=inp.source_surface_ref,
    )
    if not target.enabled:
        allowed = False
        reason = "target_disabled"
    notes = [
        "This resolver does not send messages. It returns the explicit provider action "
        "reference and safe defaults an agent can validate and execute."
    ]
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
        action_input_defaults=target.action_input_defaults,
        notes=notes,
    )


async def communication_route_upsert(
    inp: CommunicationRouteUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationRouteOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
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
            data={"fields": unsupported},
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
        if inp.bot_profile_key is not None and data.get("bot_profile_key") != inp.bot_profile_key:
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
            "bot_profile_key": inp.bot_profile_key,
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




def _require_project(session: Session, project_id: int) -> None:
    ProjectRepository(session).get(project_id)


def _validate_profile_key(value: str) -> None:
    if not _PROFILE_KEY_RE.fullmatch(value.strip()):
        raise ValidationError(
            "communication keys must be 1-80 chars of letters, numbers, _, or -"
        )


def _validate_provider_key(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", value.strip()):
        raise ValidationError("provider_key must be a stable provider key")


def _validate_ref(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240:
        raise ValidationError(f"{label} must be a non-empty ref up to 240 chars")


def _validate_identity(value: dict[str, Any]) -> None:
    display_name = value.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValidationError("identity.display_name is required")


def _communication_profile_ref(key: str) -> str:
    return f"communication-profile:{key.strip()}"


def _communication_target_ref(key: str) -> str:
    return f"communication-target:{key.strip()}"


def _communication_contact_ref(key: str) -> str:
    return f"communication-contact:{key.strip()}"


def _communication_route_ref(key: str) -> str:
    return f"communication-route:{key.strip()}"


def _surface_external_id(surface_ref: str) -> str:
    return f"communication-surface:{surface_ref.strip()}"


def _membership_ref(surface_ref: str, member_ref: str) -> str:
    return f"communication-membership:{surface_ref.strip()}:{member_ref.strip()}"


def _record_by_resource_external_id(
    session: Session,
    *,
    project_id: int,
    resource_key: str,
    external_id: str,
) -> ResourceRecord | None:
    ResourceRepository(session).list_resources(
        plugin_slug="communications",
        project_id=project_id,
    )
    return session.exec(
        select(ResourceRecord)
        .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
        .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
        .where(
            col(ResourceRecord.project_id) == project_id,
            col(ResourceRecord.external_id) == external_id,
            col(Resource.key) == resource_key,
            col(Plugin.slug) == "communications",
        )
    ).first()


def _communication_profile_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationProfileOut:
    key = str(data.get("key") or "")
    return CommunicationProfileOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        profile_ref=str(data.get("profile_ref") or _communication_profile_ref(key)),
        key=key,
        enabled=bool(data.get("enabled", True)),
        identity=dict(data.get("identity") or {}),
        agent_guidance=dict(data.get("agent_guidance") or {}),
        provider_facets={
            str(k): dict(v)
            for k, v in dict(data.get("provider_facets") or {}).items()
            if isinstance(v, dict)
        },
        access_policy=dict(data.get("access_policy") or {}),
        visibility_policy=dict(data.get("visibility_policy") or {}),
        trigger_policy=dict(data.get("trigger_policy") or {}),
        context_policy=dict(data.get("context_policy") or {}),
        response_policy=dict(data.get("response_policy") or {}),
        send_policy=dict(data.get("send_policy") or {}),
        handoff_policy=dict(data.get("handoff_policy") or {}),
        approval_policy=dict(data.get("approval_policy") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_surface_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationSurfaceOut:
    surface_ref = str(data.get("surface_ref") or data.get("channel_ref") or "")
    return CommunicationSurfaceOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        surface_ref=surface_ref,
        channel_ref=str(data.get("channel_ref") or surface_ref),
        provider_key=str(data.get("provider_key") or ""),
        kind=str(data.get("kind") or data.get("channel_type") or ""),
        display_name=(
            data.get("display_name") if isinstance(data.get("display_name"), str) else None
        ),
        credential_ref=(
            data.get("credential_ref") if isinstance(data.get("credential_ref"), str) else None
        ),
        safe_external_ref=(
            data.get("safe_external_ref")
            if isinstance(data.get("safe_external_ref"), str)
            else None
        ),
        ingest_enabled=bool(data.get("ingest_enabled", True)),
        send_enabled=bool(data.get("send_enabled", True)),
        capabilities=dict(data.get("capabilities") or {}),
        metadata_json=dict(data.get("metadata_json") or data.get("metadata") or {}),
    )


def _communication_contact_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationContactOut:
    key = str(data.get("key") or "")
    provider_refs: dict[str, list[str]] = {}
    for provider, refs in dict(data.get("provider_refs") or {}).items():
        provider_refs[str(provider)] = _string_list(refs)
    return CommunicationContactOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        contact_ref=str(data.get("contact_ref") or _communication_contact_ref(key)),
        key=key,
        display_name=str(data.get("display_name") or key),
        kind=str(data.get("kind") or "person"),
        status=str(data.get("status") or "unknown"),
        provider_refs=provider_refs,
        safe_external_refs=_string_list(data.get("safe_external_refs")),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_membership_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationMembershipOut:
    surface_ref = str(data.get("surface_ref") or "")
    member_ref = str(data.get("member_ref") or "")
    return CommunicationMembershipOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        membership_ref=str(data.get("membership_ref") or _membership_ref(surface_ref, member_ref)),
        surface_ref=surface_ref,
        member_ref=member_ref,
        provider_key=str(data.get("provider_key") or ""),
        membership_kind=str(data.get("membership_kind") or "profile"),
        status=str(data.get("status") or "unknown"),
        roles=[str(item) for item in data.get("roles") or []],
        permissions=dict(data.get("permissions") or {}),
        scope_status=dict(data.get("scope_status") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_target_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationTargetOut:
    key = str(data.get("key") or "")
    return CommunicationTargetOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        target_ref=str(data.get("target_ref") or _communication_target_ref(key)),
        key=key,
        display_name=(
            data.get("display_name") if isinstance(data.get("display_name"), str) else None
        ),
        provider_key=str(data.get("provider_key") or ""),
        surface_ref=str(data.get("surface_ref") or ""),
        profile_ref=data.get("profile_ref") if isinstance(data.get("profile_ref"), str) else None,
        thread_ref=data.get("thread_ref") if isinstance(data.get("thread_ref"), str) else None,
        enabled=bool(data.get("enabled", True)),
        action_ref=data.get("action_ref") if isinstance(data.get("action_ref"), str) else None,
        action_input_defaults=dict(data.get("action_input_defaults") or {}),
        send_policy=dict(data.get("send_policy") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _communication_route_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> CommunicationRouteOut:
    key = str(data.get("key") or "")
    return CommunicationRouteOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        route_ref=str(data.get("route_ref") or _communication_route_ref(key)),
        key=key,
        enabled=bool(data.get("enabled", True)),
        source_surface_refs=_string_list(data.get("source_surface_refs")),
        target_refs=_string_list(data.get("target_refs")),
        allowed_profile_refs=_string_list(data.get("allowed_profile_refs")),
        requires_approval=bool(data.get("requires_approval", False)),
        field_policy=dict(data.get("field_policy") or {}),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _default_action_ref(provider_key: str) -> str | None:
    match provider_key.strip():
        case "telegram-bot":
            return "communications.telegram-bot.message.send"
        case "slack-bot":
            return "communications.slack-bot.message.send"
        case "smtp":
            return "communications.smtp.email.send"
        case "local-agent-chat":
            return "localAgentChat.createMessage"
        case _:
            return None


def _target_policy_allowed(
    policy: dict[str, Any],
    *,
    profile_ref: str | None,
    source_surface_ref: str | None,
) -> tuple[bool, str | None]:
    mode = str(policy.get("mode") or "explicit-target")
    if mode in {"disabled", "deny"}:
        return False, "send_policy_disabled"
    allowed_profiles = set(_string_list(policy.get("allowed_profile_refs")))
    allowed_sources = set(_string_list(policy.get("allowed_source_surface_refs")))
    allowed_targets = set(_string_list(policy.get("allowed_target_refs")))
    if not allowed_profiles and not allowed_sources and not allowed_targets:
        return False, "send_policy_missing_allowlist"
    if allowed_profiles and profile_ref not in allowed_profiles:
        return False, "profile_not_allowed"
    if allowed_sources and source_surface_ref not in allowed_sources:
        return False, "source_surface_not_allowed"
    if policy.get("requires_approval") is True:
        return False, "approval_required"
    return True, None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


_CONTEXT_ALLOWED_FIELDS = {
    "message_ref",
    "provider_key",
    "profile_ref",
    "bot_profile_key",
    "direction",
    "surface_ref",
    "channel_ref",
    "thread_ref",
    "content_type",
    "text_preview",
    "attention_status",
    "transport_status",
    "processing_status",
    "from_ref",
    "from_username",
    "sender_ref",
    "recipient_refs",
    "attachments",
    "date",
    "sent_at",
    "received_at",
    "source_agent_request_id",
}


def _select_context_fields(data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: data.get(field) for field in fields if field in _CONTEXT_ALLOWED_FIELDS}




def _surfaces(name: str, command: str) -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=f"/api/v1/operations/{name}/call"),
        cli=OperationSurface(enabled=True, command=command),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="communicationProfile.upsert",
            summary="Create or update a provider-neutral communication profile.",
            input_model=CommunicationProfileUpsertInput,
            output_model=WriteEnvelope[CommunicationProfileOut],
            handler=communication_profile_upsert,
            surfaces=_surfaces(
                "communicationProfile.upsert",
                "ops call communicationProfile.upsert",
            ),
            purpose=(
                "Use this setup operation for the agent-facing identity and policy bundle "
                "that can span Telegram, Slack, local chat, SMTP, IMAP, and future transports. "
                "Provider-specific credentials and sends remain explicit provider actions."
            ),
            prerequisites=(
                "Pass identity.display_name.",
                "Keep policy declarative; agents still decide work and provider calls.",
                "Use provider_facets only for safe provider refs, never tokens or secrets.",
            ),
            returns=("A WriteEnvelope with the safe CommunicationProfileOut record.",),
            examples=(
                OperationExample(
                    title="Create support communication profile",
                    arguments={
                        "project_id": 1,
                        "key": "support",
                        "identity": {"display_name": "Support Agent"},
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationProfile.get",
            summary="Get one provider-neutral communication profile.",
            input_model=CommunicationProfileGetInput,
            output_model=CommunicationProfileOut,
            handler=communication_profile_get,
            surfaces=_surfaces("communicationProfile.get", "ops call communicationProfile.get"),
            purpose="Use this to inspect safe profile identity, guidance, facets, and policy.",
            prerequisites=("Pass project_id and key.",),
            returns=("One safe CommunicationProfileOut record.",),
            examples=(
                OperationExample(
                    title="Get profile",
                    arguments={"project_id": 1, "key": "support"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationProfile.list",
            summary="List provider-neutral communication profiles.",
            input_model=CommunicationProfileListInput,
            output_model=Page[CommunicationProfileOut],
            handler=communication_profile_list,
            surfaces=_surfaces("communicationProfile.list", "ops call communicationProfile.list"),
            purpose=(
                "Use this during setup or routing diagnostics to discover communication profiles."
            ),
            prerequisites=("Pass project_id.",),
            returns=("A Page of safe CommunicationProfileOut records.",),
            examples=(OperationExample(title="List profiles", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationSurface.upsert",
            summary="Create or update safe communication surface metadata.",
            input_model=CommunicationSurfaceUpsertInput,
            output_model=WriteEnvelope[CommunicationSurfaceOut],
            handler=communication_surface_upsert,
            surfaces=_surfaces(
                "communicationSurface.upsert",
                "ops call communicationSurface.upsert",
            ),
            purpose=(
                "Use this to register a Telegram chat, Slack channel/DM, email mailbox, "
                "or local chat surface with safe capability metadata."
            ),
            prerequisites=("Pass provider_key, surface_ref, and kind.",),
            returns=("A WriteEnvelope with CommunicationSurfaceOut.",),
            examples=(
                OperationExample(
                    title="Register Slack surface",
                    arguments={
                        "project_id": 1,
                        "surface_ref": "slack-channel:C123",
                        "provider_key": "slack-bot",
                        "kind": "slack-channel",
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationSurface.list",
            summary="List safe communication surfaces.",
            input_model=CommunicationSurfaceListInput,
            output_model=Page[CommunicationSurfaceOut],
            handler=communication_surface_list,
            surfaces=_surfaces("communicationSurface.list", "ops call communicationSurface.list"),
            purpose="Use this to inspect known channels, DMs, mailboxes, and local chat surfaces.",
            prerequisites=("Pass project_id. Optional filters are provider_key and kind.",),
            returns=("A Page of CommunicationSurfaceOut records.",),
            examples=(
                OperationExample(
                    title="List Slack surfaces",
                    arguments={"project_id": 1, "provider_key": "slack-bot"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationContact.upsert",
            summary="Create or update a safe communication contact.",
            input_model=CommunicationContactUpsertInput,
            output_model=WriteEnvelope[CommunicationContactOut],
            handler=communication_contact_upsert,
            surfaces=_surfaces(
                "communicationContact.upsert",
                "ops call communicationContact.upsert",
            ),
            purpose=(
                "Use this to map people, customers, teams, bots, or organizations to safe "
                "provider refs without exposing provider tokens or credentials."
            ),
            prerequisites=("Pass key and display_name.",),
            returns=("A WriteEnvelope with CommunicationContactOut.",),
            examples=(
                OperationExample(
                    title="Create customer contact",
                    arguments={
                        "project_id": 1,
                        "key": "customer-acme",
                        "display_name": "Acme Inc.",
                        "kind": "organization",
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationContact.list",
            summary="List safe communication contacts.",
            input_model=CommunicationContactListInput,
            output_model=Page[CommunicationContactOut],
            handler=communication_contact_list,
            surfaces=_surfaces("communicationContact.list", "ops call communicationContact.list"),
            purpose="Use this to discover safe cross-provider person/customer/team refs.",
            prerequisites=("Pass project_id.",),
            returns=("A Page of CommunicationContactOut records.",),
            examples=(OperationExample(title="List contacts", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationMembership.upsert",
            summary="Create or update communication membership and permission state.",
            input_model=CommunicationMembershipUpsertInput,
            output_model=WriteEnvelope[CommunicationMembershipOut],
            handler=communication_membership_upsert,
            surfaces=_surfaces(
                "communicationMembership.upsert",
                "ops call communicationMembership.upsert",
            ),
            purpose=(
                "Use this to store whether a profile/contact/bot is joined, invited, removed, "
                "or unknown in a surface, with provider capability and scope diagnostics."
            ),
            prerequisites=("Pass surface_ref, member_ref, provider_key, and status.",),
            returns=("A WriteEnvelope with CommunicationMembershipOut.",),
            examples=(
                OperationExample(
                    title="Register bot channel membership",
                    arguments={
                        "project_id": 1,
                        "surface_ref": "slack-channel:C123",
                        "member_ref": "communication-profile:support",
                        "provider_key": "slack-bot",
                        "permissions": {"can_read": True, "can_write": True},
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationMembership.list",
            summary="List communication memberships and permission state.",
            input_model=CommunicationMembershipListInput,
            output_model=Page[CommunicationMembershipOut],
            handler=communication_membership_list,
            surfaces=_surfaces(
                "communicationMembership.list",
                "ops call communicationMembership.list",
            ),
            purpose="Use this to inspect where profiles, contacts, or bots can read/write.",
            prerequisites=("Pass project_id and optional surface/member/provider filters.",),
            returns=("A Page of CommunicationMembershipOut records.",),
            examples=(OperationExample(title="List memberships", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationTarget.upsert",
            summary="Create or update a named communication target.",
            input_model=CommunicationTargetUpsertInput,
            output_model=WriteEnvelope[CommunicationTargetOut],
            handler=communication_target_upsert,
            surfaces=_surfaces("communicationTarget.upsert", "ops call communicationTarget.upsert"),
            purpose=(
                "Use this setup operation to create named destinations such as internal-support "
                "or sergey-dm. Targets resolve to explicit provider action refs; they do not "
                "send messages or choose business behavior."
            ),
            prerequisites=("Pass provider_key and surface_ref.",),
            returns=("A WriteEnvelope with CommunicationTargetOut.",),
            examples=(
                OperationExample(
                    title="Register internal support target",
                    arguments={
                        "project_id": 1,
                        "key": "internal-support",
                        "provider_key": "slack-bot",
                        "surface_ref": "slack-channel:C123",
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationTarget.list",
            summary="List named communication targets.",
            input_model=CommunicationTargetListInput,
            output_model=Page[CommunicationTargetOut],
            handler=communication_target_list,
            surfaces=_surfaces("communicationTarget.list", "ops call communicationTarget.list"),
            purpose="Use this to discover configured safe send destinations.",
            prerequisites=("Pass project_id.",),
            returns=("A Page of CommunicationTargetOut records.",),
            examples=(OperationExample(title="List targets", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationTarget.resolve",
            summary="Resolve one named communication target to an explicit provider action.",
            input_model=CommunicationTargetResolveInput,
            output_model=CommunicationTargetResolveOut,
            handler=communication_target_resolve,
            surfaces=_surfaces(
                "communicationTarget.resolve",
                "ops call communicationTarget.resolve",
            ),
            purpose=(
                "Use this before sending across channels/providers. It applies static target "
                "policy and returns the provider action ref/defaults an agent can validate "
                "and execute with action.run or action.execute."
            ),
            prerequisites=("Create a communicationTarget first.",),
            returns=("Allowed/denied status plus explicit provider action ref and safe defaults.",),
            examples=(
                OperationExample(
                    title="Resolve internal support target",
                    arguments={
                        "project_id": 1,
                        "key": "internal-support",
                        "profile_ref": "communication-profile:support",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationRoute.upsert",
            summary="Create or update a static communication handoff route.",
            input_model=CommunicationRouteUpsertInput,
            output_model=WriteEnvelope[CommunicationRouteOut],
            handler=communication_route_upsert,
            surfaces=_surfaces("communicationRoute.upsert", "ops call communicationRoute.upsert"),
            purpose=(
                "Use this to declare allowed cross-surface handoffs such as a customer "
                "Telegram issue to an internal Slack target. It never sends messages."
            ),
            prerequisites=("Pass source surfaces and target refs explicitly.",),
            returns=("A WriteEnvelope with CommunicationRouteOut.",),
            examples=(
                OperationExample(
                    title="Create customer issue route",
                    arguments={
                        "project_id": 1,
                        "key": "customer-issue-to-internal-support",
                        "source_surface_refs": ["telegram-chat:-1001"],
                        "target_refs": ["communication-target:internal-support"],
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="communicationRoute.list",
            summary="List static communication handoff routes.",
            input_model=CommunicationRouteListInput,
            output_model=Page[CommunicationRouteOut],
            handler=communication_route_list,
            surfaces=_surfaces("communicationRoute.list", "ops call communicationRoute.list"),
            purpose="Use this to inspect configured cross-surface handoff routes.",
            prerequisites=("Pass project_id and optional source/target/profile filters.",),
            returns=("A Page of CommunicationRouteOut records.",),
            examples=(OperationExample(title="List routes", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationContext.query",
            summary="Query bounded stored communication history.",
            input_model=CommunicationContextQueryInput,
            output_model=CommunicationContextQueryOut,
            handler=communication_context_query,
            surfaces=_surfaces("communicationContext.query", "ops call communicationContext.query"),
            purpose=(
                "Use this when an agent needs recent stored conversation context before "
                "deciding a workflow. It never fetches live provider history."
            ),
            prerequisites=(
                "Pass bounded filters such as surface_ref, thread_ref, provider_key, or profile.",
                "Request only supported safe fields.",
            ),
            returns=("A compact list of selected message fields from stored StackOS records.",),
            examples=(
                OperationExample(
                    title="Read recent stored channel context",
                    arguments={
                        "project_id": 1,
                        "surface_ref": "slack-channel:C123",
                        "limit": 25,
                        "fields": ["message_ref", "sender_ref", "text_preview"],
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
    ]


__all__ = [
    "CommunicationContactListInput",
    "CommunicationContactOut",
    "CommunicationContactUpsertInput",
    "CommunicationContextQueryInput",
    "CommunicationContextQueryOut",
    "CommunicationMembershipListInput",
    "CommunicationMembershipOut",
    "CommunicationMembershipUpsertInput",
    "CommunicationProfileGetInput",
    "CommunicationProfileListInput",
    "CommunicationProfileOut",
    "CommunicationProfileUpsertInput",
    "CommunicationRouteListInput",
    "CommunicationRouteOut",
    "CommunicationRouteUpsertInput",
    "CommunicationSurfaceListInput",
    "CommunicationSurfaceOut",
    "CommunicationSurfaceUpsertInput",
    "CommunicationTargetListInput",
    "CommunicationTargetOut",
    "CommunicationTargetResolveInput",
    "CommunicationTargetResolveOut",
    "CommunicationTargetUpsertInput",
    "operation_specs",
]
