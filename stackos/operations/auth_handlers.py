"""Protocol-neutral Account and Connection operation contracts and handlers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from stackos.auth_providers import (
    AccountOut,
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthRepository,
    AuthRevokeOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
    OAuthCallbackOut,
)
from stackos.config import Settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter


class AccountListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"provider_key": "firecrawl"}},
    )

    provider_key: str | None = None


class ConnectionListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "provider_key": "firecrawl"}},
    )

    project_id: int
    provider_key: str | None = None


class AccountStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "provider_key": "google-analytics",
                "credential_ref": "cred_...",
                "attach_project_id": 1,
                "return_surface": "project-connections",
            }
        },
    )

    provider_key: str
    auth_method_key: str | None = None
    credential_ref: str | None = None
    attach_project_id: int | None = None
    return_surface: str = Field(
        default="accounts",
        pattern="^(accounts|project-connections)$",
    )


class AccountCreateInput(MCPInput):
    """Local-admin Account creation input; credential fields are write-only."""

    model_config = ConfigDict(extra="forbid")

    provider_key: str
    auth_method_key: str | None = None
    display_name: str = Field(min_length=1, max_length=200)
    fields: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"writeOnly": True},
    )
    expires_at: datetime | None = None
    attach_project_id: int | None = None


class AccountGetInput(MCPInput):
    model_config = ConfigDict(extra="forbid")

    credential_ref: str


class AccountUpdateInput(MCPInput):
    """Local-admin Account edit input; supplied credential fields are write-only."""

    model_config = ConfigDict(extra="forbid")

    credential_ref: str
    fields: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"writeOnly": True},
    )
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


class AccountTestInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"credential_ref": "cred_..."}},
    )

    credential_ref: str


class AuthCallbackInput(MCPInput):
    """Transport-bound provider callback input; never exposed through MCP."""

    model_config = ConfigDict(extra="forbid")

    state: str
    code: str | None = None
    provider_error: str | None = None


class AccountRevokeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"credential_ref": "cred_..."}},
    )

    credential_ref: str


class ConnectionAccountInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "credential_ref": "cred_..."}},
    )

    project_id: int
    credential_ref: str


def _settings_from_context(ctx: MCPContext) -> Settings:
    settings = ctx.extras.get("settings")
    if isinstance(settings, Settings):
        return settings
    return Settings()


async def account_list(
    inp: AccountListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AuthStatusOut:
    return AuthRepository(ctx.session).status(
        project_id=None,
        provider_key=inp.provider_key,
    )


async def connection_list(
    inp: ConnectionListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AuthStatusOut:
    return AuthRepository(ctx.session).status(
        project_id=inp.project_id,
        provider_key=inp.provider_key,
    )


async def account_start(
    inp: AccountStartInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthStartOut]:
    env = AuthRepository(ctx.session).start(
        provider_key=inp.provider_key,
        settings=_settings_from_context(ctx),
        auth_method_key=inp.auth_method_key,
        credential_ref=inp.credential_ref,
        attach_project_id=inp.attach_project_id,
        return_surface=inp.return_surface,
    )
    return WriteEnvelope[AuthStartOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def account_create(
    inp: AccountCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthCredentialSetOut]:
    env = AuthRepository(ctx.session).store_credential(
        provider_key=inp.provider_key,
        auth_method_key=inp.auth_method_key,
        display_name=inp.display_name,
        fields=inp.fields,
        expires_at=inp.expires_at,
        attach_project_id=inp.attach_project_id,
    )
    return WriteEnvelope[AuthCredentialSetOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def account_get(
    inp: AccountGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AuthCredentialEditOut:
    return AuthRepository(ctx.session).get_credential_edit_state(
        credential_ref=inp.credential_ref,
    )


async def account_update(
    inp: AccountUpdateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthCredentialSetOut]:
    env = AuthRepository(ctx.session).update_credential(
        credential_ref=inp.credential_ref,
        fields=inp.fields,
        display_name=inp.display_name,
    )
    return WriteEnvelope[AuthCredentialSetOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def auth_callback(
    inp: AuthCallbackInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> OAuthCallbackOut:
    return await AuthRepository(ctx.session).complete_oauth_callback(
        state=inp.state,
        code=inp.code,
        provider_error=inp.provider_error,
        settings=_settings_from_context(ctx),
    )


async def account_test(
    inp: AccountTestInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthTestOut]:
    env = await AuthRepository(ctx.session).test(
        project_id=None,
        credential_ref=inp.credential_ref,
    )
    return WriteEnvelope[AuthTestOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def account_revoke(
    inp: AccountRevokeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthRevokeOut]:
    env = AuthRepository(ctx.session).revoke(credential_ref=inp.credential_ref)
    return WriteEnvelope[AuthRevokeOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def connection_attach(
    inp: ConnectionAccountInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AccountOut]:
    env = AuthRepository(ctx.session).attach_account(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        attached_by="local-admin" if ctx.extras.get("surface") == "rest" else "agent",
    )
    return WriteEnvelope[AccountOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def connection_detach(
    inp: ConnectionAccountInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AccountOut]:
    env = AuthRepository(ctx.session).detach_account(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
    )
    return WriteEnvelope[AccountOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


__all__ = [
    "AccountCreateInput",
    "AccountGetInput",
    "AccountListInput",
    "AccountRevokeInput",
    "AccountStartInput",
    "AccountTestInput",
    "AccountUpdateInput",
    "AuthCallbackInput",
    "ConnectionAccountInput",
    "ConnectionListInput",
    "account_create",
    "account_get",
    "account_list",
    "account_revoke",
    "account_start",
    "account_test",
    "account_update",
    "auth_callback",
    "connection_attach",
    "connection_detach",
    "connection_list",
]
