"""Protocol-neutral Account and Connection operation contracts and handlers."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.actions import ActionRepository
from stackos.auth_providers import (
    AccountAuthStatusOut,
    AccountOut,
    AccountSessionOut,
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthRepository,
    AuthRevokeOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
    OAuthCallbackOut,
)
from stackos.auth_providers.repository.telegram_application import TelegramApplicationRepository
from stackos.config import Settings
from stackos.db.models import Credential
from stackos.integrations.telegram_tdlib import (
    TelegramTdlibNativeError,
    TelegramTdlibRuntimeError,
    TelegramTdlibServiceError,
    TelegramTdlibSessionError,
)
from stackos.integrations.telegram_tdlib.holds import telegram_native_auth_hold_reason
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import ConflictError


class AccountListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"provider_key": "firecrawl"}},
    )

    provider_key: str | None = None


class TelegramApplicationStatusInput(MCPInput):
    model_config = ConfigDict(extra="forbid")


class TelegramApplicationStatusOut(BaseModel):
    configured: bool


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
    authorization_mode: Literal["phone", "qr"] = "phone"


class AccountAuthorizationStatusInput(MCPInput):
    """Read one safe local-admin native authorization projection."""

    model_config = ConfigDict(extra="forbid")

    credential_ref: str


class AccountAuthorizationSubmitInput(MCPInput):
    """One write-only answer for a current native Account challenge."""

    model_config = ConfigDict(extra="forbid")

    credential_ref: str
    generation: int = Field(gt=0)
    answer: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"writeOnly": True},
    )


class AccountAuthorizationCancelInput(MCPInput):
    """Cancel one exact native authorization generation."""

    model_config = ConfigDict(extra="forbid")

    credential_ref: str
    generation: int = Field(gt=0)


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
    project_id: int | None = Field(default=None, gt=0)


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


class AccountSessionInput(MCPInput):
    """Control the one shared TDLib session through an attached project."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "credential_ref": "cred_..."}},
    )

    project_id: int = Field(gt=0)
    credential_ref: str


def _settings_from_context(ctx: MCPContext) -> Settings:
    settings = ctx.extras.get("settings")
    if isinstance(settings, Settings):
        return settings
    return Settings()


def _telegram_runtime_from_context(ctx: MCPContext) -> Any:
    """Get the daemon-owned native service without constructing a fallback client."""
    runtime = ctx.extras.get("telegram_runtime")
    if runtime is None:
        raise ValueError(
            "Telegram native runtime is unavailable. Install or repair the managed TDLib runtime."
        )
    return runtime


def _quiesce_native_delivery(ctx: MCPContext, credential_ref: str) -> None:
    result = ActionRepository(ctx.session).quiesce_account_delivery(
        credential_ref=credential_ref,
        reason=telegram_native_auth_hold_reason(credential_ref),
    )
    if result.active_lease_count:
        raise ConflictError(
            "Account delivery is draining. Retry this Account change after active attempts finish.",
            data={
                "credential_ref": credential_ref,
                "state": "draining",
                "active_lease_count": result.active_lease_count,
                "retry_safe": True,
            },
        )


def _resume_native_delivery(ctx: MCPContext, credential_ref: str) -> None:
    if AuthRepository(ctx.session).get_telegram_session_status(
        credential_ref=credential_ref,
        runtime=_telegram_runtime_from_context(ctx),
    )["connected"]:
        ActionRepository(ctx.session).release_account_delivery_quiesce(
            credential_ref=credential_ref,
            expected_reason=telegram_native_auth_hold_reason(credential_ref),
        )


def _restore_native_delivery_on_error(ctx: MCPContext, credential_ref: str) -> None:
    """Restore admission only when a failed transition left the Account connected."""
    ctx.session.rollback()
    _resume_native_delivery(ctx, credential_ref)


def _require_local_native_authorization(ctx: MCPContext) -> None:
    if ctx.extras.get("surface") != "rest" or not ctx.extras.get("trusted_local_admin"):
        raise ValueError("Telegram native authorization is available only to local-admin REST")


def _telegram_session_out(
    repo: AuthRepository,
    *,
    project_id: int,
    credential_ref: str,
    runtime: Any,
) -> AccountSessionOut:
    state = repo.get_telegram_session_status(
        credential_ref=credential_ref,
        runtime=runtime,
    )
    project_ids = repo.get_account(credential_ref=credential_ref).project_ids
    return AccountSessionOut(
        credential_ref=credential_ref,
        desired_connected=state["desired_connected"],
        connected=state["connected"],
        status=state["status"],
        project_ids=project_ids,
        affects_other_projects=any(item != project_id for item in project_ids),
        next_action=state.get("next_action"),
    )


def _record_telegram_session_command(
    repo: AuthRepository,
    ctx: MCPContext,
    *,
    inp: AccountSessionInput,
    operation: str,
    result: AccountSessionOut,
) -> None:
    credential = repo.require_attached_account(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        provider_key="telegram",
        require_connected=False,
    )
    repo.record_usage_event(
        credential=credential,
        provider_key="telegram",
        operation=operation,
        status=result.status,
        project_id=inp.project_id,
        metadata_json={
            "invoking_project_id": inp.project_id,
            "affected_project_ids": result.project_ids,
            "desired_connected": result.desired_connected,
            "connected": result.connected,
        },
    )
    ctx.session.commit()


async def account_session_status(
    inp: AccountSessionInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AccountSessionOut:
    repo = AuthRepository(ctx.session)
    repo.require_attached_account(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        provider_key="telegram",
        require_connected=False,
    )
    return _telegram_session_out(
        repo,
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        runtime=_telegram_runtime_from_context(ctx),
    )


async def account_session_connect(
    inp: AccountSessionInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AccountSessionOut]:
    repo = AuthRepository(ctx.session)
    repo.require_attached_account(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        provider_key="telegram",
        require_connected=False,
    )
    runtime = _telegram_runtime_from_context(ctx)
    current = _telegram_session_out(
        repo,
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        runtime=runtime,
    )
    if not (current.desired_connected and current.connected):
        _quiesce_native_delivery(ctx, inp.credential_ref)
        try:
            await repo.connect_telegram_session(
                credential_ref=inp.credential_ref,
                runtime=runtime,
                settings=_settings_from_context(ctx),
            )
        except Exception:
            _restore_native_delivery_on_error(ctx, inp.credential_ref)
            raise
        _resume_native_delivery(ctx, inp.credential_ref)
    result = _telegram_session_out(
        repo,
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        runtime=runtime,
    )
    _record_telegram_session_command(
        repo, ctx, inp=inp, operation="account.session.connect", result=result
    )
    return WriteEnvelope[AccountSessionOut](
        data=result,
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def account_session_disconnect(
    inp: AccountSessionInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AccountSessionOut]:
    repo = AuthRepository(ctx.session)
    repo.require_attached_account(
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        provider_key="telegram",
        require_connected=False,
    )
    runtime = _telegram_runtime_from_context(ctx)
    _quiesce_native_delivery(ctx, inp.credential_ref)
    try:
        await repo.disconnect_telegram_session(
            credential_ref=inp.credential_ref,
            runtime=runtime,
        )
    except Exception:
        _restore_native_delivery_on_error(ctx, inp.credential_ref)
        raise
    result = _telegram_session_out(
        repo,
        project_id=inp.project_id,
        credential_ref=inp.credential_ref,
        runtime=runtime,
    )
    _record_telegram_session_command(
        repo, ctx, inp=inp, operation="account.session.disconnect", result=result
    )
    return WriteEnvelope[AccountSessionOut](
        data=result,
        run_id=ctx.run_id,
        project_id=inp.project_id,
    )


async def account_list(
    inp: AccountListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AuthStatusOut:
    return AuthRepository(ctx.session).status(
        project_id=None,
        provider_key=inp.provider_key,
    )


async def telegram_application_status(
    _inp: TelegramApplicationStatusInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TelegramApplicationStatusOut:
    return TelegramApplicationStatusOut(
        configured=TelegramApplicationRepository(ctx.session).configured()
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
    repo = AuthRepository(ctx.session)
    if inp.provider_key == "telegram":
        _require_local_native_authorization(ctx)
        if inp.credential_ref is None:
            raise ValueError("credential_ref is required to start Telegram authorization")
        account = repo.get_account(credential_ref=inp.credential_ref)
        if account.provider_key != "telegram":
            raise ConflictError(
                "Account provider does not match Telegram",
                data={"credential_ref": inp.credential_ref},
            )
        if repo.get_telegram_session_status(credential_ref=inp.credential_ref)["desired_connected"]:
            raise ConflictError(
                "Disconnect the Telegram Account before starting local sign-in",
                data={
                    "credential_ref": inp.credential_ref,
                    "next_action": (
                        "Call account.session.disconnect from an attached project, then "
                        "start local Telegram sign-in."
                    ),
                },
            )
        runtime = _telegram_runtime_from_context(ctx)
        _quiesce_native_delivery(ctx, inp.credential_ref)
        try:
            env = await repo.start_telegram_authorization(
                credential_ref=inp.credential_ref,
                runtime=runtime,
                settings=_settings_from_context(ctx),
                authorization_mode=inp.authorization_mode,
                sign_in_only=True,
            )
        except Exception:
            _restore_native_delivery_on_error(ctx, inp.credential_ref)
            raise
        _resume_native_delivery(ctx, inp.credential_ref)
    else:
        env = repo.start(
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


async def account_authorization_status(
    inp: AccountAuthorizationStatusInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AccountAuthStatusOut:
    _require_local_native_authorization(ctx)
    return AuthRepository(ctx.session).telegram_authorization_status(
        credential_ref=inp.credential_ref
    )


async def account_authorization_submit(
    inp: AccountAuthorizationSubmitInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AccountAuthStatusOut]:
    _require_local_native_authorization(ctx)
    env = await AuthRepository(ctx.session).submit_telegram_authorization(
        credential_ref=inp.credential_ref,
        generation=inp.generation,
        answer=inp.answer,
        runtime=_telegram_runtime_from_context(ctx),
    )
    _resume_native_delivery(ctx, inp.credential_ref)
    return WriteEnvelope[AccountAuthStatusOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def account_authorization_cancel(
    inp: AccountAuthorizationCancelInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AccountAuthStatusOut]:
    _require_local_native_authorization(ctx)
    runtime = _telegram_runtime_from_context(ctx)
    state = AuthRepository(ctx.session).telegram_authorization_status(
        credential_ref=inp.credential_ref
    )
    native_bootstrap_pending = (
        state.status == "pending"
        and state.generation == inp.generation
        and (
            (
                callable(getattr(runtime, "is_connecting", None))
                and runtime.is_connecting(account_ref=inp.credential_ref, generation=inp.generation)
            )
            or (
                callable(getattr(runtime, "active_generation", None))
                and runtime.active_generation(account_ref=inp.credential_ref) == inp.generation
            )
        )
    )
    if (
        state.generation is not None
        and state.generation == inp.generation
        and not (
            state.status in {"challenge", "verifying", "repair-required"}
            or native_bootstrap_pending
        )
    ):
        raise ConflictError(
            "Authorization is complete; use account.session.disconnect to stop this Account",
            data={
                "credential_ref": inp.credential_ref,
                "status": state.status,
                "next_action": "Use account.session.disconnect from an attached project.",
            },
        )
    _quiesce_native_delivery(ctx, inp.credential_ref)
    try:
        env = await AuthRepository(ctx.session).cancel_telegram_authorization(
            credential_ref=inp.credential_ref,
            generation=inp.generation,
            runtime=runtime,
        )
    except Exception:
        _restore_native_delivery_on_error(ctx, inp.credential_ref)
        raise
    return WriteEnvelope[AccountAuthStatusOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def account_create(
    inp: AccountCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AuthCredentialSetOut]:
    # A missing daemon native service is a setup failure, so detect it before
    # creating a row that the UI cannot authorize in this request.
    telegram_runtime = (
        _telegram_runtime_from_context(ctx) if inp.provider_key == "telegram" else None
    )
    env = AuthRepository(ctx.session).store_credential(
        provider_key=inp.provider_key,
        auth_method_key=inp.auth_method_key,
        display_name=inp.display_name,
        fields=inp.fields,
        expires_at=inp.expires_at,
        attach_project_id=inp.attach_project_id,
    )
    if inp.provider_key == "telegram" and env.data.auth_method_key == "tdlib-bot-token":
        assert telegram_runtime is not None
        # Bot credentials can finish their first local TDLib login at creation.
        # The Account row is already committed, so a native error leaves a
        # repairable Account instead of making a retry collide on its name.
        repo = AuthRepository(ctx.session)
        # Native authentication failures are persisted as repair-required by
        # the repository. Programmer errors still surface to the caller.
        with suppress(
            ConflictError,
            OSError,
            TelegramTdlibNativeError,
            TelegramTdlibRuntimeError,
            TelegramTdlibServiceError,
            TelegramTdlibSessionError,
        ):
            await repo.start_telegram_authorization(
                credential_ref=env.data.credential_ref,
                runtime=telegram_runtime,
                settings=_settings_from_context(ctx),
                sign_in_only=True,
            )
        env.data = AuthCredentialSetOut.model_validate(
            repo.get_account(credential_ref=env.data.credential_ref).model_dump()
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
    repo = AuthRepository(ctx.session)
    account = repo.get_account(credential_ref=inp.credential_ref)
    if account.provider_key == "telegram" and inp.fields:
        runtime = _telegram_runtime_from_context(ctx)
        async with runtime.account_transition(inp.credential_ref):
            ctx.session.expire_all()
            state = repo.get_telegram_session_status(credential_ref=inp.credential_ref)
            if state["desired_connected"]:
                raise ConflictError(
                    "Disconnect the Telegram Account before changing its connection fields",
                    data={
                        "credential_ref": inp.credential_ref,
                        "desired_connected": True,
                        "next_action": (
                            "Call account.session.disconnect from an attached project, save "
                            "the Account changes, then call account.session.connect."
                        ),
                    },
                )
            authorization = repo.telegram_authorization_status(credential_ref=inp.credential_ref)
            native_bootstrap_pending = (
                authorization.status == "pending"
                and authorization.generation is not None
                and callable(getattr(runtime, "is_connecting", None))
                and runtime.is_connecting(
                    account_ref=inp.credential_ref, generation=authorization.generation
                )
            )
            if authorization.status in {"challenge", "verifying"} or native_bootstrap_pending:
                raise ConflictError(
                    "Cancel the temporary Telegram sign-in before changing connection fields",
                    data={
                        "credential_ref": inp.credential_ref,
                        "authorization_status": authorization.status,
                        "next_action": (
                            "Cancel the current local Account authorization, save the Account "
                            "changes, then start sign-in again."
                        ),
                    },
                )
            if runtime.active_generation(account_ref=inp.credential_ref) is not None:
                raise ConflictError(
                    "Finish or cancel Telegram sign-in before changing connection fields",
                    data={
                        "credential_ref": inp.credential_ref,
                        "next_action": (
                            "Finish or cancel local Telegram sign-in, then save the "
                            "Account changes."
                        ),
                    },
                )
            env = repo.update_credential(
                credential_ref=inp.credential_ref,
                fields=inp.fields,
                display_name=inp.display_name,
            )
            if env.data.status == "pending" and not (
                env.data.account and env.data.account.get("provider_account_id")
            ):
                # The repository fenced an app/token identity change before
                # commit. Retire its old database under this same Account
                # transition; a retry of this edit can finish cleanup.
                from stackos.auth_providers.repository.telegram import remove_telegram_local_data

                remove_telegram_local_data(
                    settings=_settings_from_context(ctx), credential_ref=inp.credential_ref
                )
    else:
        env = repo.update_credential(
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
    repo = AuthRepository(ctx.session)
    account = repo.get_account(credential_ref=inp.credential_ref)
    if account.provider_key == "telegram":
        local_admin_rest = (
            ctx.extras.get("surface") == "rest" and ctx.extras.get("trusted_local_admin") is True
        )
        test_project_id: int | None = None
        if not local_admin_rest:
            if ctx.project_id is None:
                raise ConflictError(
                    "Telegram Account test requires an attached project",
                    data={"next_action": "Select an attached project and retry account.test."},
                )
            repo.require_attached_account(
                project_id=ctx.project_id,
                credential_ref=inp.credential_ref,
                provider_key="telegram",
                require_connected=False,
            )
            test_project_id = ctx.project_id
        env = await repo.test_telegram_authorization(
            project_id=test_project_id,
            credential_ref=inp.credential_ref,
            runtime=_telegram_runtime_from_context(ctx),
            settings=_settings_from_context(ctx),
        )
    else:
        env = await repo.test(
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
    repo = AuthRepository(ctx.session)
    account = repo.get_account(credential_ref=inp.credential_ref)
    if account.provider_key == "telegram":
        from stackos.auth_providers.repository.telegram import remove_telegram_local_data

        remote_logout_status: Literal["requested", "unconfirmed"] = "unconfirmed"
        if account.revoked_at is None:
            runtime = _telegram_runtime_from_context(ctx)
            _quiesce_native_delivery(ctx, inp.credential_ref)
            async with runtime.account_transition(inp.credential_ref):
                ctx.session.expire_all()
                account = repo.get_account(credential_ref=inp.credential_ref)
                if account.revoked_at is None:
                    try:
                        state = repo.telegram_authorization_status(
                            credential_ref=inp.credential_ref
                        )
                        if state.generation is not None:
                            if state.status == "connected":
                                # TDLib close retires the local client only. A bounded
                                # logOut may also invalidate the remote session.
                                with suppress(Exception):
                                    logout = await runtime.request(
                                        inp.credential_ref,
                                        {"@type": "logOut"},
                                        generation=state.generation,
                                        timeout_seconds=5.0,
                                    )
                                    if logout.get("@type") == "ok":
                                        remote_logout_status = "requested"
                            await repo.cancel_telegram_authorization(
                                credential_ref=inp.credential_ref,
                                generation=state.generation,
                                runtime=runtime,
                            )
                        env = repo.revoke(credential_ref=inp.credential_ref)
                    except Exception:
                        _restore_native_delivery_on_error(ctx, inp.credential_ref)
                        raise
                else:
                    env = None
                remove_telegram_local_data(
                    settings=_settings_from_context(ctx),
                    credential_ref=inp.credential_ref,
                )
        else:
            env = None
            # Cleanup is idempotent after an interrupted prior revoke.
            remove_telegram_local_data(
                settings=_settings_from_context(ctx),
                credential_ref=inp.credential_ref,
            )
        if env is None:
            assert account.revoked_at is not None
            revoked_at = account.revoked_at
        else:
            revoked_at = env.data.revoked_at
        receipt = AuthRevokeOut(
            credential_ref=inp.credential_ref,
            provider_key="telegram",
            revoked_at=revoked_at,
            remote_logout_status=remote_logout_status,
            local_data_removed=True,
        )
        repo.record_usage_event(
            credential=ctx.session.get(Credential, account.credential_id),
            provider_key="telegram",
            operation="account.revoke.local_cleanup",
            status="complete",
            metadata_json={
                "credential_ref": inp.credential_ref,
                "remote_logout_status": remote_logout_status,
                "local_data_removed": True,
            },
        )
        ctx.session.commit()
    else:
        env = repo.revoke(credential_ref=inp.credential_ref)
        receipt = env.data
    return WriteEnvelope[AuthRevokeOut](
        data=receipt,
        run_id=ctx.run_id,
        project_id=env.project_id if env is not None else None,
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
    "AccountAuthorizationCancelInput",
    "AccountAuthorizationStatusInput",
    "AccountAuthorizationSubmitInput",
    "AccountCreateInput",
    "AccountGetInput",
    "AccountListInput",
    "AccountRevokeInput",
    "AccountSessionInput",
    "AccountStartInput",
    "AccountTestInput",
    "AccountUpdateInput",
    "AuthCallbackInput",
    "ConnectionAccountInput",
    "ConnectionListInput",
    "TelegramApplicationStatusInput",
    "TelegramApplicationStatusOut",
    "account_authorization_cancel",
    "account_authorization_status",
    "account_authorization_submit",
    "account_create",
    "account_get",
    "account_list",
    "account_revoke",
    "account_session_connect",
    "account_session_disconnect",
    "account_session_status",
    "account_start",
    "account_test",
    "account_update",
    "auth_callback",
    "connection_attach",
    "connection_detach",
    "connection_list",
    "telegram_application_status",
]
