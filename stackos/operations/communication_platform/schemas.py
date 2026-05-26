"""Input and output schemas for communication platform operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.mcp.contract import MCPInput

from .constants import _DEFAULT_INGRESS_KEY, _DEFAULT_LOCAL_BASE_URL


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
                    "telegram-bot": {"auth_profile_key": "support-bot"},
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
    profile_key: str | None = None
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
