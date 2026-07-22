"""Sanitized auth-provider repository response models."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from stackos.db.models import Credential, IntegrationCredential


class AuthFieldOut(BaseModel):
    key: str
    label: str
    type: str = "text"
    secret: bool = False
    required: bool = False
    placeholder: str | None = None
    description: str | None = None
    options: list[dict[str, str]] | None = None


class AuthMethodOut(BaseModel):
    key: str
    label: str
    auth_type: str
    description: str = ""
    interactive: bool = False
    payload_format: str = "json"
    payload_field: str | None = None
    fields: list[AuthFieldOut] = Field(default_factory=list)
    config: dict[str, Any] | None = None


class AuthProviderOut(BaseModel):
    id: int
    plugin_id: int | None
    plugin_slug: str | None
    key: str
    name: str
    description: str
    auth_type: str
    auth_methods: list[AuthMethodOut] = Field(default_factory=list)
    scopes: list[str]
    config_json: dict[str, Any] | None


class CredentialConnectionOut(BaseModel):
    credential_ref: str
    project_id: int | None
    provider_key: str
    auth_type: str
    auth_method_key: str
    profile_key: str
    label: str | None = None
    status: str
    expires_at: datetime | None
    last_tested_at: datetime | None
    revoked_at: datetime | None
    scopes: list[str]
    account: dict[str, Any] | None = None
    setup_required: bool = False


class AuthStatusOut(BaseModel):
    project_id: int | None
    provider_key: str | None
    providers: list[AuthProviderOut]
    connections: list[CredentialConnectionOut]


class AuthStartOut(BaseModel):
    project_id: int
    provider_key: str
    auth_type: str
    auth_method_key: str
    status: str
    setup_url: str | None = None
    authorization_url: str | None = None
    redirect_uri: str | None = None
    credential_ref: str | None = None
    expires_at: datetime | None = None


class OAuthCallbackOut(BaseModel):
    """Sanitized result used only to choose the local callback redirect."""

    project_id: int | None = None
    provider_key: str | None = None
    credential_ref: str | None = None
    status: str


class AuthTestOut(BaseModel):
    credential_ref: str
    provider_key: str
    ok: bool
    status: str
    summary: str
    checked_at: str
    retryable: bool = False
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthRevokeOut(BaseModel):
    credential_ref: str
    provider_key: str
    project_id: int | None
    revoked_at: datetime
    status: str = "revoked"


class AuthCredentialSetOut(CredentialConnectionOut):
    """Sanitized result for a local-admin credential profile write."""


class AuthCredentialEditOut(BaseModel):
    """Safe stored values for the existing provider-declared credential schema."""

    connection: CredentialConnectionOut
    values: dict[str, Any]
    secret_present: dict[str, bool]


@dataclass(frozen=True)
class ResolvedCredential:
    """Daemon-internal credential bundle for connector execution.

    The secret payload is intentionally hidden from repr. Callers must never
    serialize this object into MCP/REST responses or audit JSON.
    """

    credential: Credential
    integration: IntegrationCredential
    secret_payload: bytes = dataclass_field(repr=False)
    config_json: dict[str, Any] | None = dataclass_field(default=None, repr=False)

    @property
    def credential_ref(self) -> str:
        return self.credential.credential_ref

    @property
    def credential_id(self) -> int | None:
        return self.credential.id

    @property
    def provider_key(self) -> str:
        return self.credential.provider_key
