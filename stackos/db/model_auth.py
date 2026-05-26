"""SQLModel table declarations for auth providers, credentials, and integration budgets."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    UniqueConstraint,
)
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _utcnow


class AuthProvider(SQLModel, table=True):
    """Auth provider metadata derived from plugin provider declarations."""

    __tablename__ = "auth_providers"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_auth_providers_plugin_key"),
        Index("ix_auth_providers_plugin", "plugin_id"),
        Index("ix_auth_providers_key", "key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    auth_type: str = Field(default="none", max_length=80)
    scopes_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Credential(SQLModel, table=True):
    """Opaque credential reference over encrypted integration credential rows."""

    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("credential_ref", name="uq_credentials_ref"),
        UniqueConstraint(
            "integration_credential_id",
            name="uq_credentials_integration_credential",
        ),
        Index("ix_credentials_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    auth_provider_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("auth_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    integration_credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("integration_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    credential_ref: str = Field(max_length=120)
    provider_key: str = Field(max_length=160)
    auth_type: str = Field(default="none", max_length=80)
    auth_method_key: str = Field(default="default", max_length=160)
    profile_key: str = Field(default="default", max_length=160)
    status: str = Field(default="connected", max_length=40)
    expires_at: datetime | None = Field(default=None)
    last_tested_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialScope(SQLModel, table=True):
    """Scope granted to an opaque credential reference."""

    __tablename__ = "credential_scopes"
    __table_args__ = (
        UniqueConstraint("credential_id", "scope", name="uq_credential_scopes_scope"),
        Index("ix_credential_scopes_credential", "credential_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int = Field(
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    scope: str = Field(max_length=200)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialAccount(SQLModel, table=True):
    """Provider account metadata linked to a credential reference."""

    __tablename__ = "credential_accounts"
    __table_args__ = (
        Index("ix_credential_accounts_credential", "credential_id"),
        Index("ix_credential_accounts_provider_account", "provider_account_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int = Field(
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_account_id: str | None = Field(default=None, max_length=300)
    display_name: str | None = Field(default=None, max_length=300)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class OAuthState(SQLModel, table=True):
    """OAuth state nonce for local human setup flows."""

    __tablename__ = "oauth_states"
    __table_args__ = (
        UniqueConstraint("state", name="uq_oauth_states_state"),
        Index("ix_oauth_states_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_key: str = Field(max_length=160)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    integration_credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("integration_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    state: str = Field(max_length=200)
    redirect_uri: str | None = Field(default=None, max_length=2048)
    expires_at: datetime | None = Field(default=None)
    consumed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialUsageEvent(SQLModel, table=True):
    """Redacted audit event for credential use or health probes."""

    __tablename__ = "credential_usage_events"
    __table_args__ = (
        Index("ix_credential_usage_events_credential", "credential_id"),
        Index("ix_credential_usage_events_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: int | None = Field(default=None)
    provider_key: str = Field(max_length=160)
    operation: str = Field(max_length=120)
    status: str = Field(max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialRefreshEvent(SQLModel, table=True):
    """Redacted audit event for credential refresh attempts."""

    __tablename__ = "credential_refresh_events"
    __table_args__ = (
        Index("ix_credential_refresh_events_credential", "credential_id"),
        Index("ix_credential_refresh_events_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: int | None = Field(default=None)
    provider_key: str = Field(max_length=160)
    status: str = Field(max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class IntegrationCredential(SQLModel, table=True):
    """Encrypted provider credential profile backing daemon-side execution.

    ``project_id`` is nullable for global credentials. ``encrypted_payload`` +
    ``nonce`` are AES-256-GCM ciphertext; AAD is composed at the repository
    layer (M5).
    """

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "kind",
            "profile_key",
            name="uq_integration_credentials_project_kind_profile",
        ),
        Index("ix_integration_credentials_project", "project_id"),
        Index(
            "ix_integration_credentials_project_kind_profile",
            "project_id",
            "kind",
            "profile_key",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    kind: str = Field(max_length=120)
    profile_key: str = Field(default="default", max_length=160)
    encrypted_payload: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    nonce: bytes = Field(sa_column=Column(LargeBinary(12), nullable=False))
    expires_at: datetime | None = Field(default=None)
    last_refreshed_at: datetime | None = Field(default=None)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class IntegrationBudget(SQLModel, table=True):
    """Pre-emptive cost cap + rate limit (PLAN.md L368)."""

    __tablename__ = "integration_budgets"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "kind",
            name="uq_integration_budgets_project_kind",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: str = Field(max_length=120)
    monthly_budget_usd: float = Field(default=50.0)
    alert_threshold_pct: int = Field(default=80)
    current_month_spend: float = Field(default=0.0)
    current_month_calls: int = Field(default=0)
    qps: float = Field(default=1.0)
    last_reset: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


__all__ = [
    "AuthProvider",
    "Credential",
    "CredentialAccount",
    "CredentialRefreshEvent",
    "CredentialScope",
    "CredentialUsageEvent",
    "IntegrationBudget",
    "IntegrationCredential",
    "OAuthState",
]
