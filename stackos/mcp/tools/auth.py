"""Generic StackOS auth provider MCP tools."""

from __future__ import annotations

from pydantic import ConfigDict

from stackos.auth_providers import (
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
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter


class AuthStatusInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "provider_key": "firecrawl"}},
    )

    project_id: int
    provider_key: str | None = None


class AuthStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "provider_key": "firecrawl"}},
    )

    project_id: int
    provider_key: str
    auth_method_key: str | None = None
    credential_ref: str | None = None


class AuthTestInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "credential_ref": "cred_..."}},
    )

    project_id: int
    credential_ref: str


class AuthCallbackInput(MCPInput):
    """Transport-bound provider callback input; never exposed through MCP."""

    model_config = ConfigDict(extra="forbid")

    state: str
    code: str | None = None
    provider_error: str | None = None


class AuthRevokeInput(MCPInput):
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


async def _auth_status(
    inp: AuthStatusInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AuthStatusOut:
    return AuthRepository(ctx.session).status(
        project_id=inp.project_id,
        provider_key=inp.provider_key,
    )


async def _auth_start(
    inp: AuthStartInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthStartOut]:
    env = AuthRepository(ctx.session).start(
        project_id=inp.project_id,
        provider_key=inp.provider_key,
        settings=_settings_from_context(ctx),
        auth_method_key=inp.auth_method_key,
        credential_ref=inp.credential_ref,
    )
    return WriteEnvelope[AuthStartOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _auth_callback(
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


async def _auth_test(
    inp: AuthTestInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthTestOut]:
    env = await AuthRepository(ctx.session).test(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
    )
    return WriteEnvelope[AuthTestOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _auth_revoke(
    inp: AuthRevokeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthRevokeOut]:
    env = AuthRepository(ctx.session).revoke(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
    )
    return WriteEnvelope[AuthRevokeOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


def register(registry: ToolRegistry) -> None:
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        ("auth.status", "auth.start", "auth.test", "auth.revoke"),
    )


__all__ = ["register"]
