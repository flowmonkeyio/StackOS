"""Provider-neutral communication platform setup and query operations."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from stackos.actions import ActionRepository
from stackos.db.models import Credential, Plugin, Resource, ResourceRecord
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
_DEFAULT_INGRESS_KEY = "default"
_DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:5180"
_LOCAL_TUNNEL_PROVIDERS: dict[str, dict[str, Any]] = {
    "ngrok": {
        "discovery_url": "http://127.0.0.1:4040/api/endpoints",
        "response_url_fields": ("url", "public_url"),
    },
}
_DEFAULT_DRIVER_CONFIG: dict[str, dict[str, Any]] = {
    "local-tunnel": {"provider": "ngrok"},
    "public-url": {},
}


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


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


class IngressEndpointConfigureInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "driver": "public-url",
                "public_base_url": "https://stackos.example.com",
            }
        },
    )

    project_id: int
    key: str = _DEFAULT_INGRESS_KEY
    driver: Literal["public-url", "local-tunnel"] = "public-url"
    enabled: bool = True
    public_base_url: str | None = None
    local_base_url: str = _DEFAULT_LOCAL_BASE_URL
    driver_config: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class IngressEndpointRefreshInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    key: str = _DEFAULT_INGRESS_KEY
    public_base_url: str | None = None
    driver_config: dict[str, Any] = Field(default_factory=dict)
    sync_profiles: bool = True
    apply_provider_webhooks: bool = False
    dry_run_provider_webhooks: bool = True


class IngressEndpointRoutesInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    key: str = _DEFAULT_INGRESS_KEY


class IngressEndpointSyncInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    key: str = _DEFAULT_INGRESS_KEY
    apply_provider_webhooks: bool = False
    dry_run_provider_webhooks: bool = True


class IngressEndpointStatusInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    key: str = _DEFAULT_INGRESS_KEY


class IngressRouteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: str
    profile_key: str
    profile_ref: str
    profile_resource_key: str
    ingress_path: str
    ingress_url: str | None = None
    local_url: str | None = None
    remote_status: str = "not_checked"
    notes: list[str] = Field(default_factory=list)


class IngressEndpointOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    endpoint_ref: str
    key: str
    driver: str
    enabled: bool
    status: str
    public_base_url: str | None = None
    local_base_url: str
    driver_config: dict[str, Any] = Field(default_factory=dict)
    last_refreshed_at: str | None = None
    last_synced_at: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class IngressEndpointRoutesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: IngressEndpointOut
    routes: list[IngressRouteOut]


class IngressEndpointSyncOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: IngressEndpointOut
    routes: list[IngressRouteOut]
    provider_results: list[dict[str, Any]]
    updated_profile_refs: list[str]


class IngressEndpointStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: IngressEndpointOut | None = None
    routes: list[IngressRouteOut] = Field(default_factory=list)
    configured: bool = False
    ready: bool = False
    notes: list[str] = Field(default_factory=list)


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
                "audience": "customer",
                "intent": {
                    "category": "customer-support",
                    "summary": "Customer-facing issue room for Acme billing escalations.",
                },
                "agent_guidance": {
                    "default_instructions": (
                        "Treat replies as customer-visible unless the target is explicitly "
                        "internal."
                    ),
                    "restricted_topics": ["other customers", "secrets", "internal financials"],
                },
                "data_scope": {
                    "classification": "customer-confidential",
                    "allowed_share_refs": ["communication-target:internal-support"],
                    "requires_customer_context": True,
                },
                "external_context": {
                    "customer": {
                        "safe_ref": "customer:acme",
                        "crm_account_id": "crm-account-123",
                        "primary_email": "ops@acme.example",
                    }
                },
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
    audience: Literal["internal", "customer", "partner", "vendor", "public", "mixed", "unknown"] = (
        "unknown"
    )
    intent: dict[str, Any] = Field(default_factory=dict)
    agent_guidance: dict[str, Any] = Field(default_factory=dict)
    data_scope: dict[str, Any] = Field(default_factory=dict)
    external_context: dict[str, Any] = Field(default_factory=dict)
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
    audience: str
    intent: dict[str, Any]
    agent_guidance: dict[str, Any]
    data_scope: dict[str, Any]
    external_context: dict[str, Any]
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
                    "allowed_invoker_refs": ["telegram-user:555"],
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
                "invoker_ref": "telegram-user:555",
            }
        },
    )

    project_id: int
    key: str
    profile_ref: str | None = None
    source_surface_ref: str | None = None
    invoker_ref: str | None = None


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


async def ingress_endpoint_configure(
    inp: IngressEndpointConfigureInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[IngressEndpointOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    public_base_url = _normalize_public_base_url(inp.public_base_url)
    local_base_url = _normalize_base_url(inp.local_base_url, require_https=False)
    driver_config = _normalize_driver_config(inp.driver, inp.driver_config)
    data_json = {
        "key": inp.key.strip(),
        "endpoint_ref": _ingress_endpoint_ref(inp.key),
        "driver": inp.driver,
        "enabled": inp.enabled,
        "status": "running" if public_base_url and inp.enabled else "configured",
        "public_base_url": public_base_url,
        "local_base_url": local_base_url,
        "driver_config": driver_config,
        "metadata_json": inp.metadata_json,
        "updated_at": _utcnow_iso(),
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(inp.key),
        title=f"Ingress endpoint {inp.key.strip()}",
        data_json=data_json,
        provenance_json={"source": "ingressEndpoint.configure"},
    )
    return WriteEnvelope(
        data=_ingress_endpoint_out(env.data.id, env.data.project_id, env.data.data_json or {}),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def ingress_endpoint_refresh(
    inp: IngressEndpointRefreshInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[IngressEndpointSyncOut]:
    _require_project(ctx.session, inp.project_id)
    endpoint = _require_ingress_endpoint(ctx.session, project_id=inp.project_id, key=inp.key)
    data = dict(endpoint.data_json or {})
    driver = str(data.get("driver") or "public-url")
    driver_config = _normalize_driver_config(
        driver,
        {
            **dict(data.get("driver_config") or {}),
            **inp.driver_config,
        },
    )
    public_base_url = _normalize_public_base_url(inp.public_base_url)
    diagnostics: dict[str, Any] = {}
    if public_base_url is None:
        public_base_url, diagnostics = await _discover_public_base_url(
            driver=driver,
            driver_config=driver_config,
        )
    data["public_base_url"] = public_base_url
    data["driver_config"] = driver_config
    data["status"] = "running" if public_base_url else "failed"
    data["last_refreshed_at"] = _utcnow_iso()
    data["metadata_json"] = {
        **dict(data.get("metadata_json") or {}),
        "last_refresh": diagnostics,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(inp.key),
        title=f"Ingress endpoint {inp.key.strip()}",
        data_json=data,
        provenance_json={"source": "ingressEndpoint.refresh"},
    )
    endpoint_out = _ingress_endpoint_out(env.data.id, env.data.project_id, env.data.data_json or {})
    if inp.sync_profiles:
        sync_out = await _sync_ingress_endpoint(
            ctx,
            endpoint_out,
            apply_provider_webhooks=inp.apply_provider_webhooks,
            dry_run_provider_webhooks=inp.dry_run_provider_webhooks,
        )
    else:
        sync_out = IngressEndpointSyncOut(
            endpoint=endpoint_out,
            routes=_ingress_routes(ctx.session, endpoint=endpoint_out),
            provider_results=[],
            updated_profile_refs=[],
        )
    return WriteEnvelope(data=sync_out, run_id=ctx.run_id, project_id=env.project_id)


async def ingress_endpoint_routes(
    inp: IngressEndpointRoutesInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> IngressEndpointRoutesOut:
    _require_project(ctx.session, inp.project_id)
    endpoint = _require_ingress_endpoint(ctx.session, project_id=inp.project_id, key=inp.key)
    endpoint_out = _ingress_endpoint_out(endpoint.id, endpoint.project_id, endpoint.data_json or {})
    return IngressEndpointRoutesOut(
        endpoint=endpoint_out,
        routes=_ingress_routes(ctx.session, endpoint=endpoint_out),
    )


async def ingress_endpoint_sync(
    inp: IngressEndpointSyncInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[IngressEndpointSyncOut]:
    _require_project(ctx.session, inp.project_id)
    endpoint = _require_ingress_endpoint(ctx.session, project_id=inp.project_id, key=inp.key)
    endpoint_out = _ingress_endpoint_out(endpoint.id, endpoint.project_id, endpoint.data_json or {})
    sync_out = await _sync_ingress_endpoint(
        ctx,
        endpoint_out,
        apply_provider_webhooks=inp.apply_provider_webhooks,
        dry_run_provider_webhooks=inp.dry_run_provider_webhooks,
    )
    return WriteEnvelope(data=sync_out, run_id=ctx.run_id, project_id=inp.project_id)


async def ingress_endpoint_status(
    inp: IngressEndpointStatusInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> IngressEndpointStatusOut:
    _require_project(ctx.session, inp.project_id)
    endpoint = _record_by_resource_external_id(
        ctx.session,
        project_id=inp.project_id,
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(inp.key),
    )
    if endpoint is None:
        return IngressEndpointStatusOut(
            configured=False,
            ready=False,
            notes=["No ingress endpoint is configured for this project."],
        )
    endpoint_out = _ingress_endpoint_out(endpoint.id, endpoint.project_id, endpoint.data_json or {})
    routes = _ingress_routes(ctx.session, endpoint=endpoint_out)
    ready = bool(endpoint_out.enabled and endpoint_out.public_base_url and routes)
    notes = []
    if not endpoint_out.public_base_url:
        notes.append("Set or refresh public_base_url before syncing provider webhooks.")
    if not routes:
        notes.append("No Slack or Telegram communication profiles currently expose ingress routes.")
    return IngressEndpointStatusOut(
        endpoint=endpoint_out,
        routes=routes,
        configured=True,
        ready=ready,
        notes=notes,
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
        target_ref=target.target_ref,
        profile_ref=inp.profile_ref,
        source_surface_ref=inp.source_surface_ref,
        invoker_ref=inp.invoker_ref,
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
        raise ValidationError("communication keys must be 1-80 chars of letters, numbers, _, or -")


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


def _normalize_public_base_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _normalize_base_url(value, require_https=True)


def _normalize_base_url(value: str, *, require_https: bool) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("base URL must include http(s) scheme and host")
    if require_https and parsed.scheme != "https":
        raise ValidationError("public ingress base URL must use https")
    if parsed.query or parsed.fragment:
        raise ValidationError("base URL must not include query or fragment")
    return raw


def _normalize_driver_config(driver: str, value: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        **_DEFAULT_DRIVER_CONFIG.get(driver, {}),
        **dict(value or {}),
    }
    if driver == "local-tunnel":
        provider = str(normalized.get("provider") or "").strip().lower()
        if provider not in _LOCAL_TUNNEL_PROVIDERS:
            raise ValidationError(f"unsupported local tunnel provider {provider!r}")
        provider_defaults = _LOCAL_TUNNEL_PROVIDERS[provider]
        normalized = {
            **provider_defaults,
            **normalized,
            "provider": provider,
        }
        discovery_url = normalized.get("discovery_url")
        if isinstance(discovery_url, str) and discovery_url.strip():
            normalized["discovery_url"] = _normalize_base_url(
                discovery_url,
                require_https=False,
            )
        static_host = normalized.get("static_host")
        if isinstance(static_host, str) and static_host.strip():
            normalized["static_host"] = static_host.strip().lower()
        fields = normalized.get("response_url_fields")
        if isinstance(fields, tuple):
            normalized["response_url_fields"] = list(fields)
    elif driver == "public-url":
        normalized = {}
    else:
        raise ValidationError(f"unsupported ingress driver {driver!r}")
    return normalized


async def _discover_public_base_url(
    *,
    driver: str,
    driver_config: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    if driver == "public-url":
        return None, {
            "driver": driver,
            "status": "failed",
            "error": "public_base_url is required for public-url ingress endpoints",
        }
    if driver != "local-tunnel":
        return None, {"driver": driver, "status": "failed", "error": "unsupported ingress driver"}
    provider = str(driver_config.get("provider") or "").strip().lower()
    if provider not in _LOCAL_TUNNEL_PROVIDERS:
        return None, {
            "driver": driver,
            "provider": provider,
            "status": "failed",
            "error": "unsupported local tunnel provider",
        }
    api_url = str(driver_config.get("discovery_url") or "")
    static_host = str(driver_config.get("static_host") or "") or None
    diagnostics: dict[str, Any] = {
        "driver": driver,
        "provider": provider,
        "source": "driver_discovery",
        "discovery_url": api_url,
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(api_url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        diagnostics.update({"status": "failed", "error": str(exc)})
        return None, diagnostics
    items = []
    if isinstance(payload, dict):
        if isinstance(payload.get("endpoints"), list):
            items = payload["endpoints"]
            diagnostics["resource"] = "endpoints"
        elif isinstance(payload.get("tunnels"), list):
            items = payload["tunnels"]
            diagnostics["resource"] = "tunnels"
    if not isinstance(items, list) or not items:
        diagnostics.update({"status": "failed", "error": "local tunnel API returned no endpoints"})
        return None, diagnostics
    candidates: list[str] = []
    response_url_fields = [
        str(item)
        for item in driver_config.get("response_url_fields", [])
        if isinstance(item, str) and item
    ]
    if not response_url_fields:
        response_url_fields = ["url", "public_url"]
    for item in items:
        if not isinstance(item, dict):
            continue
        public_url = next(
            (item.get(field) for field in response_url_fields if isinstance(item.get(field), str)),
            None,
        )
        if isinstance(public_url, str) and public_url.startswith("https://"):
            candidates.append(public_url.rstrip("/"))
    diagnostics["candidate_count"] = len(candidates)
    if static_host:
        for candidate in candidates:
            if (urlparse(candidate).hostname or "").lower() == static_host.lower():
                diagnostics.update(
                    {
                        "status": "ok",
                        "selected": candidate,
                        "matched_static_host": True,
                    }
                )
                return candidate, diagnostics
    if candidates:
        diagnostics.update({"status": "ok", "selected": candidates[0]})
        return candidates[0], diagnostics
    diagnostics.update({"status": "failed", "error": "no https local tunnel endpoints are active"})
    return None, diagnostics


def _require_ingress_endpoint(
    session: Session,
    *,
    project_id: int,
    key: str,
) -> ResourceRecord:
    _validate_profile_key(key)
    record = _record_by_resource_external_id(
        session,
        project_id=project_id,
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(key),
    )
    if record is None:
        raise ValidationError("ingress endpoint is not configured")
    return record


def _ingress_endpoint_ref(key: str) -> str:
    return f"ingress-endpoint:{key.strip()}"


def _ingress_endpoint_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> IngressEndpointOut:
    key = str(data.get("key") or _DEFAULT_INGRESS_KEY)
    return IngressEndpointOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        endpoint_ref=str(data.get("endpoint_ref") or _ingress_endpoint_ref(key)),
        key=key,
        driver=str(data.get("driver") or "public-url"),
        enabled=bool(data.get("enabled", True)),
        status=str(data.get("status") or "configured"),
        public_base_url=(
            data.get("public_base_url") if isinstance(data.get("public_base_url"), str) else None
        ),
        local_base_url=str(data.get("local_base_url") or _DEFAULT_LOCAL_BASE_URL),
        driver_config=dict(data.get("driver_config") or {}),
        last_refreshed_at=(
            data.get("last_refreshed_at")
            if isinstance(data.get("last_refreshed_at"), str)
            else None
        ),
        last_synced_at=(
            data.get("last_synced_at") if isinstance(data.get("last_synced_at"), str) else None
        ),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _ingress_routes(session: Session, *, endpoint: IngressEndpointOut) -> list[IngressRouteOut]:
    routes: list[IngressRouteOut] = []
    seen: set[tuple[str, str]] = set()
    for record in _resource_records(
        session,
        project_id=endpoint.project_id,
        resource_key="communication-profile",
    ):
        data = dict(record.data_json or {})
        profile_key = str(data.get("key") or "")
        if not profile_key:
            continue
        profile_ref = str(data.get("profile_ref") or _communication_profile_ref(profile_key))
        facets = data.get("provider_facets")
        if not isinstance(facets, dict):
            continue
        if isinstance(facets.get("slack-bot"), dict):
            routes.append(
                _route_out(
                    endpoint=endpoint,
                    provider_key="slack-bot",
                    profile_key=profile_key,
                    profile_ref=profile_ref,
                    profile_resource_key="communication-profile",
                    remote_status="manual_provider_update_required",
                )
            )
            seen.add(("slack-bot", profile_key))
    for record in _resource_records(
        session,
        project_id=endpoint.project_id,
        resource_key="communication-bot-profile",
    ):
        data = dict(record.data_json or {})
        profile_key = str(data.get("key") or "")
        if not profile_key:
            continue
        key = ("telegram-bot", profile_key)
        if key in seen:
            continue
        routes.append(
            _route_out(
                endpoint=endpoint,
                provider_key="telegram-bot",
                profile_key=profile_key,
                profile_ref=f"telegram-bot-profile:{profile_key}",
                profile_resource_key="communication-bot-profile",
                remote_status="provider_webhook_not_checked",
            )
        )
        seen.add(key)
    return routes


def _route_out(
    *,
    endpoint: IngressEndpointOut,
    provider_key: str,
    profile_key: str,
    profile_ref: str,
    profile_resource_key: str,
    remote_status: str,
) -> IngressRouteOut:
    path = _provider_ingress_path(
        project_id=endpoint.project_id,
        provider_key=provider_key,
        profile_key=profile_key,
    )
    return IngressRouteOut(
        provider_key=provider_key,
        profile_key=profile_key,
        profile_ref=profile_ref,
        profile_resource_key=profile_resource_key,
        ingress_path=path,
        ingress_url=_join_base_path(endpoint.public_base_url, path),
        local_url=_join_base_path(endpoint.local_base_url, path),
        remote_status=remote_status,
        notes=_route_notes(endpoint=endpoint, provider_key=provider_key),
    )


def _route_notes(*, endpoint: IngressEndpointOut, provider_key: str) -> list[str]:
    notes: list[str] = []
    if not endpoint.public_base_url:
        notes.append("public_base_url is not configured")
    if provider_key == "slack-bot":
        notes.append("Slack Events API and Interactivity URLs must be set in the Slack app.")
    if provider_key == "telegram-bot":
        notes.append("Telegram setWebhook can be applied by ingressEndpoint.sync.")
    return notes


def _provider_ingress_path(*, project_id: int, provider_key: str, profile_key: str) -> str:
    encoded = quote(profile_key, safe="")
    if provider_key == "slack-bot":
        return f"/api/v1/ingress/slack/{project_id}/{encoded}"
    if provider_key == "telegram-bot":
        return f"/api/v1/ingress/telegram/{project_id}/{encoded}"
    raise ValidationError(f"provider {provider_key!r} does not support webhook ingress")


def _join_base_path(base_url: str | None, path: str) -> str | None:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}{path}"


async def _sync_ingress_endpoint(
    ctx: MCPContext,
    endpoint: IngressEndpointOut,
    *,
    apply_provider_webhooks: bool,
    dry_run_provider_webhooks: bool,
) -> IngressEndpointSyncOut:
    routes = _ingress_routes(ctx.session, endpoint=endpoint)
    updated_profile_refs: list[str] = []
    provider_results: list[dict[str, Any]] = []
    resources = ResourceRepository(ctx.session)
    for route in routes:
        if not route.ingress_url:
            provider_results.append(
                {
                    "provider_key": route.provider_key,
                    "profile_key": route.profile_key,
                    "status": "skipped",
                    "reason": "public_base_url_missing",
                }
            )
            continue
        if route.profile_resource_key == "communication-profile":
            updated = _sync_communication_profile_route(
                ctx.session,
                resources=resources,
                endpoint=endpoint,
                route=route,
            )
            if updated:
                updated_profile_refs.append(route.profile_ref)
        if (
            route.provider_key == "telegram-bot"
            and route.profile_resource_key == "communication-bot-profile"
        ):
            updated = _sync_telegram_bot_profile_route(
                ctx.session,
                resources=resources,
                endpoint=endpoint,
                route=route,
            )
            if updated:
                updated_profile_refs.append(route.profile_ref)
            provider_results.append(
                await _maybe_apply_telegram_webhook(
                    ctx,
                    project_id=endpoint.project_id,
                    route=route,
                    apply_provider_webhooks=apply_provider_webhooks,
                    dry_run_provider_webhooks=dry_run_provider_webhooks,
                )
            )
        elif route.provider_key == "slack-bot":
            provider_results.append(
                {
                    "provider_key": "slack-bot",
                    "profile_key": route.profile_key,
                    "status": "manual_provider_update_required",
                    "request_url": route.ingress_url,
                    "notes": [
                        "Set this URL as the Slack Events API Request URL and "
                        "Interactivity Request URL."
                    ],
                }
            )
    _mark_ingress_endpoint_synced(ctx.session, endpoint=endpoint, route_count=len(routes))
    updated_endpoint = _require_ingress_endpoint(
        ctx.session,
        project_id=endpoint.project_id,
        key=endpoint.key,
    )
    updated_endpoint_out = _ingress_endpoint_out(
        updated_endpoint.id,
        updated_endpoint.project_id,
        updated_endpoint.data_json or {},
    )
    return IngressEndpointSyncOut(
        endpoint=updated_endpoint_out,
        routes=_ingress_routes(ctx.session, endpoint=updated_endpoint_out),
        provider_results=provider_results,
        updated_profile_refs=sorted(set(updated_profile_refs)),
    )


def _sync_communication_profile_route(
    session: Session,
    *,
    resources: ResourceRepository,
    endpoint: IngressEndpointOut,
    route: IngressRouteOut,
) -> bool:
    record = _record_by_resource_external_id(
        session,
        project_id=endpoint.project_id,
        resource_key="communication-profile",
        external_id=route.profile_ref,
    )
    if record is None:
        return False
    data = dict(record.data_json or {})
    facets = dict(data.get("provider_facets") or {})
    facet = dict(facets.get(route.provider_key) or {})
    facet.update(
        {
            "ingress_path": route.ingress_path,
            "ingress_url": route.ingress_url,
            "ingress_public_base_url": endpoint.public_base_url,
            "ingress_driver": endpoint.driver,
            "ingress_endpoint_ref": endpoint.endpoint_ref,
        }
    )
    facets[route.provider_key] = facet
    data["provider_facets"] = facets
    data["metadata_json"] = {
        **dict(data.get("metadata_json") or {}),
        "last_ingress_sync_at": _utcnow_iso(),
    }
    resources.upsert_record(
        project_id=endpoint.project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id=route.profile_ref,
        title=str(
            dict(data.get("identity") or {}).get("display_name")
            or data.get("key")
            or route.profile_key
        ),
        data_json=data,
        provenance_json={"source": "ingressEndpoint.sync"},
    )
    return True


def _sync_telegram_bot_profile_route(
    session: Session,
    *,
    resources: ResourceRepository,
    endpoint: IngressEndpointOut,
    route: IngressRouteOut,
) -> bool:
    record = _record_by_resource_external_id(
        session,
        project_id=endpoint.project_id,
        resource_key="communication-bot-profile",
        external_id=route.profile_ref,
    )
    if record is None:
        return False
    data = dict(record.data_json or {})
    host = urlparse(route.ingress_url or "").hostname
    allowed_hosts = {str(item) for item in data.get("allowed_webhook_hosts") or []}
    if host:
        allowed_hosts.add(host.lower())
    refs = dict(data.get("refs") or {})
    refs["ingress_url"] = str(route.ingress_url)
    refs["ingress_endpoint_ref"] = endpoint.endpoint_ref
    data.update(
        {
            "ingress_mode": "webhook",
            "webhook_base_url": endpoint.public_base_url,
            "allowed_webhook_hosts": sorted(allowed_hosts),
            "refs": refs,
            "webhook_policy": {
                **dict(data.get("webhook_policy") or {}),
                "driver": endpoint.driver,
                "endpoint_ref": endpoint.endpoint_ref,
                "allowed_hosts": sorted(allowed_hosts),
            },
        }
    )
    resources.upsert_record(
        project_id=endpoint.project_id,
        plugin_slug="communications",
        resource_key="communication-bot-profile",
        external_id=route.profile_ref,
        title=str(data.get("key") or route.profile_key),
        data_json=data,
        provenance_json={"source": "ingressEndpoint.sync"},
    )
    return True


async def _maybe_apply_telegram_webhook(
    ctx: MCPContext,
    *,
    project_id: int,
    route: IngressRouteOut,
    apply_provider_webhooks: bool,
    dry_run_provider_webhooks: bool,
) -> dict[str, Any]:
    if not apply_provider_webhooks:
        return {
            "provider_key": "telegram-bot",
            "profile_key": route.profile_key,
            "status": "profile_updated",
            "remote_status": "not_applied",
            "webhook_url": route.ingress_url,
        }
    credential_ref = _credential_ref_for_profile(
        ctx.session,
        project_id=project_id,
        provider_key="telegram-bot",
        profile_key=_telegram_auth_profile_key(
            ctx.session,
            project_id=project_id,
            profile_key=route.profile_key,
        ),
    )
    if credential_ref is None:
        return {
            "provider_key": "telegram-bot",
            "profile_key": route.profile_key,
            "status": "missing_credential",
            "webhook_url": route.ingress_url,
        }
    try:
        env = await ActionRepository(ctx.session).execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.webhook.set",
            input_json={
                "bot_profile_key": route.profile_key,
                "webhook_url": route.ingress_url,
            },
            credential_ref=credential_ref,
            dry_run=dry_run_provider_webhooks,
            metadata_json={"source": "ingressEndpoint.sync"},
        )
    except Exception as exc:
        return {
            "provider_key": "telegram-bot",
            "profile_key": route.profile_key,
            "status": "failed",
            "webhook_url": route.ingress_url,
            "error": str(exc),
        }
    return {
        "provider_key": "telegram-bot",
        "profile_key": route.profile_key,
        "status": (
            "remote_webhook_dry_run" if dry_run_provider_webhooks else "remote_webhook_updated"
        ),
        "webhook_url": route.ingress_url,
        "action_call_id": env.data.action_call.id,
    }


def _telegram_auth_profile_key(session: Session, *, project_id: int, profile_key: str) -> str:
    record = _record_by_resource_external_id(
        session,
        project_id=project_id,
        resource_key="communication-bot-profile",
        external_id=f"telegram-bot-profile:{profile_key}",
    )
    data = dict(record.data_json or {}) if record is not None else {}
    return str(data.get("auth_profile_key") or "default")


def _credential_ref_for_profile(
    session: Session,
    *,
    project_id: int,
    provider_key: str,
    profile_key: str,
) -> str | None:
    row = session.exec(
        select(Credential).where(
            col(Credential.project_id) == project_id,
            col(Credential.provider_key) == provider_key,
            col(Credential.profile_key) == profile_key,
            col(Credential.revoked_at).is_(None),
        )
    ).first()
    return row.credential_ref if row is not None else None


def _mark_ingress_endpoint_synced(
    session: Session,
    *,
    endpoint: IngressEndpointOut,
    route_count: int,
) -> None:
    record = _record_by_resource_external_id(
        session,
        project_id=endpoint.project_id,
        resource_key="ingress-endpoint",
        external_id=endpoint.endpoint_ref,
    )
    if record is None:
        return
    data = dict(record.data_json or {})
    data["status"] = "running" if endpoint.public_base_url else data.get("status") or "configured"
    data["last_synced_at"] = _utcnow_iso()
    data["metadata_json"] = {
        **dict(data.get("metadata_json") or {}),
        "last_sync": {"route_count": route_count},
    }
    ResourceRepository(session).upsert_record(
        project_id=endpoint.project_id,
        plugin_slug="communications",
        resource_key="ingress-endpoint",
        external_id=endpoint.endpoint_ref,
        title=f"Ingress endpoint {endpoint.key}",
        data_json=data,
        provenance_json={"source": "ingressEndpoint.sync"},
    )


def _resource_records(
    session: Session,
    *,
    project_id: int,
    resource_key: str,
) -> list[ResourceRecord]:
    ResourceRepository(session).list_resources(plugin_slug="communications", project_id=project_id)
    return list(
        session.exec(
            select(ResourceRecord)
            .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
            .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
            .where(
                col(ResourceRecord.project_id) == project_id,
                col(Resource.key) == resource_key,
                col(Plugin.slug) == "communications",
            )
            .order_by(col(ResourceRecord.id).asc())
        ).all()
    )


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
        audience=str(data.get("audience") or "unknown"),
        intent=dict(data.get("intent") or {}),
        agent_guidance=dict(data.get("agent_guidance") or {}),
        data_scope=dict(data.get("data_scope") or {}),
        external_context=dict(data.get("external_context") or {}),
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


def _target_action_defaults(
    session: Session,
    target: CommunicationTargetOut,
) -> dict[str, Any]:
    defaults = dict(target.action_input_defaults or {})
    if target.provider_key == "slack-bot":
        defaults.setdefault("surface_ref", target.surface_ref)
        if target.profile_ref:
            defaults.setdefault("profile_ref", target.profile_ref)
        if target.thread_ref:
            defaults.setdefault("thread_ref", target.thread_ref)
    elif target.provider_key == "telegram-bot":
        defaults.setdefault("chat_ref", target.surface_ref)
        bot_profile_key = _telegram_bot_profile_key(session, target)
        if bot_profile_key:
            defaults.setdefault("bot_profile_key", bot_profile_key)
        if target.thread_ref:
            defaults.setdefault("thread_ref", target.thread_ref)
    return defaults


def _telegram_bot_profile_key(
    session: Session,
    target: CommunicationTargetOut,
) -> str | None:
    explicit = target.action_input_defaults.get("bot_profile_key")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if isinstance(target.profile_ref, str):
        if target.profile_ref.startswith("telegram-bot-profile:"):
            return target.profile_ref.split(":", 1)[1].strip() or None
        if target.profile_ref.startswith("communication-profile:"):
            row = _record_by_resource_external_id(
                session,
                project_id=target.project_id,
                resource_key="communication-profile",
                external_id=target.profile_ref,
            )
            if row is not None:
                facets = dict((row.data_json or {}).get("provider_facets") or {})
                telegram_facet = facets.get("telegram-bot")
                if isinstance(telegram_facet, dict):
                    bot_profile_key = telegram_facet.get("bot_profile_key")
                    if isinstance(bot_profile_key, str) and bot_profile_key.strip():
                        return bot_profile_key.strip()
    return None


def _target_policy_allowed(
    policy: dict[str, Any],
    *,
    target_ref: str,
    profile_ref: str | None,
    source_surface_ref: str | None,
    invoker_ref: str | None,
) -> tuple[bool, str | None]:
    mode = str(policy.get("mode") or "explicit-target")
    if mode in {"disabled", "deny"}:
        return False, "send_policy_disabled"
    denied_invokers = set(_string_list(policy.get("denied_invoker_refs")))
    if invoker_ref is not None and invoker_ref in denied_invokers:
        return False, "invoker_denied"
    allowed_profiles = set(_string_list(policy.get("allowed_profile_refs")))
    allowed_sources = set(_string_list(policy.get("allowed_source_surface_refs")))
    allowed_targets = set(_string_list(policy.get("allowed_target_refs")))
    allowed_invokers = set(_string_list(policy.get("allowed_invoker_refs")))
    if mode == "denylist" and not (
        allowed_profiles or allowed_sources or allowed_targets or allowed_invokers
    ):
        if policy.get("requires_approval") is True:
            return False, "approval_required"
        return True, None
    if (
        not allowed_profiles
        and not allowed_sources
        and not allowed_targets
        and not allowed_invokers
    ):
        return False, "send_policy_missing_allowlist"
    if allowed_profiles and profile_ref not in allowed_profiles:
        return False, "profile_not_allowed"
    if allowed_sources and source_surface_ref not in allowed_sources:
        return False, "source_surface_not_allowed"
    if allowed_targets and target_ref not in allowed_targets:
        return False, "target_not_allowed"
    if allowed_invokers and invoker_ref not in allowed_invokers:
        return False, "invoker_not_allowed"
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
            name="ingressEndpoint.configure",
            summary="Configure one project-level public ingress endpoint.",
            input_model=IngressEndpointConfigureInput,
            output_model=WriteEnvelope[IngressEndpointOut],
            handler=ingress_endpoint_configure,
            surfaces=_surfaces(
                "ingressEndpoint.configure",
                "ops call ingressEndpoint.configure",
            ),
            purpose=(
                "Use this setup operation to define the project public ingress base URL. "
                "The endpoint is provider-neutral; driver-specific details live only in "
                "driver_config and routes are derived from project communication profiles."
            ),
            prerequisites=(
                "Use driver=public-url for a deployed/reverse-proxied HTTPS host.",
                "Use driver=local-tunnel only for local development and keep provider "
                "settings in driver_config.",
                "Never store provider secrets in this resource.",
            ),
            returns=("A WriteEnvelope with the safe IngressEndpointOut record.",),
            examples=(
                OperationExample(
                    title="Configure deployed HTTPS ingress",
                    arguments={
                        "project_id": 1,
                        "driver": "public-url",
                        "public_base_url": "https://stackos.example.com",
                    },
                ),
                OperationExample(
                    title="Configure local tunnel discovery",
                    arguments={
                        "project_id": 1,
                        "driver": "local-tunnel",
                        "driver_config": {
                            "provider": "ngrok",
                            "discovery_url": "http://127.0.0.1:4040/api/endpoints",
                        },
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="ingressEndpoint.refresh",
            summary="Refresh the endpoint public URL from explicit input or driver discovery.",
            input_model=IngressEndpointRefreshInput,
            output_model=WriteEnvelope[IngressEndpointSyncOut],
            handler=ingress_endpoint_refresh,
            surfaces=_surfaces("ingressEndpoint.refresh", "ops call ingressEndpoint.refresh"),
            purpose=(
                "Use this after a local tunnel or deployment URL changes. It stores one "
                "project public_base_url and can sync derived provider routes."
            ),
            prerequisites=("Configure ingressEndpoint first.",),
            returns=("A WriteEnvelope with endpoint, route, and provider sync status.",),
            examples=(
                OperationExample(
                    title="Refresh from local tunnel provider API",
                    arguments={"project_id": 1, "key": "default"},
                ),
            ),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="ingressEndpoint.routes",
            summary="List provider webhook URLs derived from the current public ingress endpoint.",
            input_model=IngressEndpointRoutesInput,
            output_model=IngressEndpointRoutesOut,
            handler=ingress_endpoint_routes,
            surfaces=_surfaces("ingressEndpoint.routes", "ops call ingressEndpoint.routes"),
            purpose="Use this to get exact Slack and Telegram webhook URLs without guessing.",
            prerequisites=("Configure ingressEndpoint first.",),
            returns=("Endpoint metadata plus provider route URLs.",),
            examples=(OperationExample(title="List routes", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="ingressEndpoint.sync",
            summary="Sync the current ingress endpoint into provider profile route metadata.",
            input_model=IngressEndpointSyncInput,
            output_model=WriteEnvelope[IngressEndpointSyncOut],
            handler=ingress_endpoint_sync,
            surfaces=_surfaces("ingressEndpoint.sync", "ops call ingressEndpoint.sync"),
            purpose=(
                "Use this after configuring profiles or public_base_url. It updates safe route "
                "metadata and can apply Telegram setWebhook through daemon-held credentials."
            ),
            prerequisites=("Configure ingressEndpoint and communication profiles first.",),
            returns=("Updated routes and per-provider sync results.",),
            examples=(OperationExample(title="Sync routes", arguments={"project_id": 1}),),
            grant_policy="direct-setup-write",
        ),
        OperationSpec(
            name="ingressEndpoint.status",
            summary="Inspect ingress endpoint readiness and provider route state.",
            input_model=IngressEndpointStatusInput,
            output_model=IngressEndpointStatusOut,
            handler=ingress_endpoint_status,
            surfaces=_surfaces("ingressEndpoint.status", "ops call ingressEndpoint.status"),
            purpose="Use this before telling an operator to ping Slack or Telegram.",
            prerequisites=("Pass project_id.",),
            returns=("Configured/ready booleans, endpoint metadata, routes, and notes.",),
            examples=(OperationExample(title="Ingress status", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
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
