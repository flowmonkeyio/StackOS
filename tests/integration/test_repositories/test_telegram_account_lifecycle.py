"""Explicit Telegram session control fences delivery and Account edits."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.telegram import remove_telegram_local_data
from stackos.auth_providers.repository.utils import utcnow
from stackos.config import Settings
from stackos.integrations.telegram_tdlib.daemon import restore_telegram_accounts
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.auth_handlers import (
    AccountAuthorizationCancelInput,
    AccountRevokeInput,
    AccountSessionInput,
    AccountStartInput,
    AccountUpdateInput,
    account_authorization_cancel,
    account_revoke,
    account_session_connect,
    account_session_disconnect,
    account_start,
    account_update,
)
from stackos.repositories.base import ConflictError, ValidationError
from tests.integration.test_repositories.test_telegram_native_authorization import (
    _bot_account,
    _credential,
    _Runtime,
    _user_account,
)


@pytest.mark.asyncio
async def test_proxy_update_requires_explicit_disconnect_and_reconnect(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _user_account(session)
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}] * 2)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=runtime, settings=settings
    )
    ctx = MCPContext(
        session=session,
        request_id="native-proxy-change",
        project_id=project_id,
        extras={
            "telegram_runtime": runtime,
            "settings": settings,
            "surface": "rest",
            "trusted_local_admin": True,
        },
    )
    inp = AccountUpdateInput(
        credential_ref=account_ref,
        fields={
            "proxy_enabled": True,
            "proxy_type": "socks5",
            "proxy_host": "proxy.example.test",
            "proxy_port": 1080,
            "proxy_username": "private-user",
            "proxy_password": "private-password",
        },
    )
    with pytest.raises(ConflictError, match="Disconnect") as error:
        await account_update(inp, ctx, ProgressEmitter(None, None))
    assert "account.session.disconnect" in error.value.data["next_action"]
    assert _credential(session, account_ref).config_json["proxy_enabled"] is False
    assert runtime.closed == []
    await account_session_disconnect(
        AccountSessionInput(project_id=project_id, credential_ref=account_ref),
        ctx,
        ProgressEmitter(None, None),
    )
    result = await account_update(inp, ctx, ProgressEmitter(None, None))
    assert runtime.closed == [(account_ref, 1)]
    assert len(runtime.configurations) == 1
    assert "private-password" not in str(result.model_dump())
    assert (
        repo.get_telegram_session_status(credential_ref=account_ref)["desired_connected"] is False
    )
    connected = await account_session_connect(
        AccountSessionInput(project_id=project_id, credential_ref=account_ref),
        ctx,
        ProgressEmitter(None, None),
    )
    assert connected.data.connected is True
    assert runtime.configurations[-1]["config"].proxy.host == "proxy.example.test"


@pytest.mark.asyncio
async def test_failed_proxy_validation_keeps_disconnected_account_config(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _user_account(session)
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=runtime, settings=settings
    )
    ctx = MCPContext(
        session=session,
        request_id="invalid-native-proxy-change",
        project_id=project_id,
        extras={
            "telegram_runtime": runtime,
            "settings": settings,
            "surface": "rest",
            "trusted_local_admin": True,
        },
    )

    await account_session_disconnect(
        AccountSessionInput(project_id=project_id, credential_ref=account_ref),
        ctx,
        ProgressEmitter(None, None),
    )
    with pytest.raises(ValidationError, match="proxy"):
        await account_update(
            AccountUpdateInput(
                credential_ref=account_ref,
                fields={
                    "proxy_enabled": True,
                    "proxy_type": "socks5",
                    "proxy_host": "proxy.example.test",
                },
            ),
            ctx,
            ProgressEmitter(None, None),
        )

    assert _credential(session, account_ref).config_json["proxy_enabled"] is False
    assert (
        repo.get_telegram_session_status(credential_ref=account_ref)["desired_connected"] is False
    )
    assert len(runtime.configurations) == 1


@pytest.mark.asyncio
async def test_cancel_or_revoke_failure_does_not_leave_connected_account_quiesced(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _user_account(session)
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=runtime, settings=settings
    )
    ctx = MCPContext(
        session=session,
        request_id="native-cancel-fencing",
        extras={
            "telegram_runtime": runtime,
            "settings": settings,
            "surface": "rest",
            "trusted_local_admin": True,
        },
    )
    delivery = ActionRepository(session)

    with pytest.raises(ConflictError, match="generation is stale"):
        await account_authorization_cancel(
            AccountAuthorizationCancelInput(credential_ref=account_ref, generation=999),
            ctx,
            ProgressEmitter(None, None),
        )
    first = delivery.admit_delivery(
        project_id=project_id,
        credential_ref=account_ref,
        destination_ref="telegram-chat:cancel-recovery",
    )
    assert first.admitted
    delivery.release_delivery_admission(
        credential_ref=account_ref,
        destination_ref="telegram-chat:cancel-recovery",
        lease_ref=first.lease_ref,
    )

    unavailable_ctx = MCPContext(
        session=session,
        request_id="native-revoke-runtime-unavailable",
        extras={"surface": "rest", "trusted_local_admin": True},
    )
    with pytest.raises(ValueError, match="native runtime is unavailable"):
        await account_revoke(
            AccountRevokeInput(credential_ref=account_ref),
            unavailable_ctx,
            ProgressEmitter(None, None),
        )
    second = delivery.admit_delivery(
        project_id=project_id,
        credential_ref=account_ref,
        destination_ref="telegram-chat:revoke-recovery",
        now=utcnow() + timedelta(seconds=2),
    )
    assert second.admitted

    with pytest.raises(ConflictError, match="Disconnect"):
        await account_start(
            AccountStartInput(provider_key="telegram", credential_ref=account_ref),
            unavailable_ctx,
            ProgressEmitter(None, None),
        )
    delivery.release_delivery_admission(
        credential_ref=account_ref,
        destination_ref="telegram-chat:revoke-recovery",
        lease_ref=second.lease_ref,
    )
    assert delivery.admit_delivery(
        project_id=project_id,
        credential_ref=account_ref,
        destination_ref="telegram-chat:start-recovery",
        now=utcnow() + timedelta(seconds=4),
    ).admitted


@pytest.mark.asyncio
async def test_native_account_lifecycle_never_releases_an_external_delivery_hold(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _user_account(session)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    initial_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    repo = AuthRepository(session)
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    await repo.connect_telegram_session(
        credential_ref=account_ref,
        runtime=initial_runtime,
        settings=settings,
    )
    delivery = ActionRepository(session)
    delivery.quiesce_account_delivery(
        credential_ref=account_ref,
        reason="operator-peer-flood-hold",
    )
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    ctx = MCPContext(
        session=session,
        request_id="native-lifecycle-external-hold",
        extras={
            "telegram_runtime": runtime,
            "settings": settings,
            "surface": "rest",
            "trusted_local_admin": True,
        },
    )

    with pytest.raises(ConflictError, match="Disconnect"):
        await account_start(
            AccountStartInput(provider_key="telegram", credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    with pytest.raises(ConflictError, match="Disconnect"):
        await account_update(
            AccountUpdateInput(
                credential_ref=account_ref,
                fields={"proxy_enabled": True, "proxy_type": "socks5", "proxy_host": "bad"},
            ),
            ctx,
            ProgressEmitter(None, None),
        )

    assert not delivery.admit_delivery(
        project_id=project_id,
        credential_ref=account_ref,
        destination_ref="telegram-chat:operator-hold",
    ).admitted
    delivery.release_account_delivery_quiesce(credential_ref=account_ref)
    assert delivery.admit_delivery(
        project_id=project_id,
        credential_ref=account_ref,
        destination_ref="telegram-chat:operator-release",
    ).admitted


@pytest.mark.asyncio
async def test_daemon_restore_resumes_only_previously_active_native_accounts(
    session: Session,
    tmp_path,
) -> None:
    active_ref = _user_account(session)
    inactive_ref = _bot_account(session, display_name="Inactive native bot")
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    initial_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    await AuthRepository(session).connect_telegram_session(
        credential_ref=active_ref,
        runtime=initial_runtime,
        settings=settings,
    )
    restoring_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    engine = cast(Engine, session.get_bind())

    await restore_telegram_accounts(engine, settings, restoring_runtime)

    assert [item["account_ref"] for item in restoring_runtime.configurations] == [active_ref]
    assert (
        AuthRepository(session).telegram_authorization_status(credential_ref=active_ref).status
        == "connected"
    )
    assert (
        AuthRepository(session).telegram_authorization_status(credential_ref=inactive_ref).status
        == "pending"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("account_kind", ["bot", "user"])
async def test_explicit_session_connect_disconnect_controls_restart_restore(
    session: Session,
    tmp_path: Path,
    account_kind: str,
) -> None:
    account_ref = (
        _bot_account(session, display_name="Session bot")
        if account_kind == "bot"
        else _user_account(session)
    )
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    assert (
        repo.get_telegram_session_status(credential_ref=account_ref)["desired_connected"] is False
    )

    initial_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    first = await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=initial_runtime, settings=settings
    )
    assert first.data.status == "connected"
    assert (
        repo.get_telegram_session_status(credential_ref=account_ref, runtime=initial_runtime)[
            "connected"
        ]
        is True
    )
    await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=initial_runtime, settings=settings
    )
    assert len(initial_runtime.configurations) == 1

    stopped = await repo.disconnect_telegram_session(
        credential_ref=account_ref, runtime=initial_runtime
    )
    assert stopped.data.status == "disconnected"
    assert initial_runtime.closed == [(account_ref, 1)]
    assert repo.get_telegram_session_status(
        credential_ref=account_ref, runtime=initial_runtime
    ) == {
        "desired_connected": False,
        "connected": False,
        "status": "disconnected",
        "next_action": "Call account.session.connect when this Account is needed.",
    }
    await repo.disconnect_telegram_session(credential_ref=account_ref, runtime=initial_runtime)
    assert initial_runtime.closed == [(account_ref, 1)]

    engine = cast(Engine, session.get_bind())
    restore_while_stopped = _Runtime(states=[])
    await restore_telegram_accounts(engine, settings, restore_while_stopped)
    assert restore_while_stopped.configurations == []

    reconnect_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=reconnect_runtime, settings=settings
    )
    restore_after_connect = _Runtime(states=[{"@type": "authorizationStateReady"}])
    await restore_telegram_accounts(engine, settings, restore_after_connect)
    assert [item["account_ref"] for item in restore_after_connect.configurations] == [account_ref]


@pytest.mark.asyncio
async def test_user_agent_connect_opens_tdlib_but_leaves_login_in_local_accounts(
    session: Session,
    tmp_path: Path,
) -> None:
    account_ref = _user_account(session)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    runtime = _Runtime(states=[{"@type": "authorizationStateWaitPhoneNumber"}])
    repo = AuthRepository(session)

    await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=runtime, settings=settings
    )

    safe = repo.get_telegram_session_status(credential_ref=account_ref, runtime=runtime)
    assert safe["desired_connected"] is True
    assert safe["connected"] is False
    assert safe["status"] == "authorization_required"
    assert "phone" not in str(safe).lower()
    assert runtime.requests == []


@pytest.mark.asyncio
async def test_repeated_connect_during_pending_bootstrap_does_not_restart_tdlib(
    session: Session,
    tmp_path: Path,
) -> None:
    class _SlowRuntime(_Runtime):
        def __init__(self) -> None:
            super().__init__(states=[{"@type": "authorizationStateReady"}])
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def configure(self, **kwargs):
            self.started.set()
            await self.release.wait()
            return await super().configure(**kwargs)

        def is_connecting(self, *, account_ref: str, generation: int) -> bool:
            return self.started.is_set() and not self.release.is_set()

    account_ref = _user_account(session)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    runtime = _SlowRuntime()
    first = asyncio.create_task(
        repo.connect_telegram_session(
            credential_ref=account_ref, runtime=runtime, settings=settings
        )
    )
    await runtime.started.wait()

    pending = await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=runtime, settings=settings
    )
    assert pending.data.status == "pending"
    assert runtime.configurations == []
    runtime.release.set()
    assert (await first).data.status == "connected"
    assert len(runtime.configurations) == 1


@pytest.mark.asyncio
async def test_disconnect_during_connect_fences_late_ready_state(
    session: Session,
    tmp_path: Path,
) -> None:
    class _SlowRuntime(_Runtime):
        def __init__(self) -> None:
            super().__init__(states=[{"@type": "authorizationStateReady"}])
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def configure(self, **kwargs):
            self.started.set()
            await self.release.wait()
            return await super().configure(**kwargs)

    account_ref = _user_account(session)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    runtime = _SlowRuntime()
    connecting = asyncio.create_task(
        repo.connect_telegram_session(
            credential_ref=account_ref, runtime=runtime, settings=settings
        )
    )
    await runtime.started.wait()

    stopped = await repo.disconnect_telegram_session(credential_ref=account_ref, runtime=runtime)
    assert stopped.data.status == "disconnected"
    runtime.release.set()
    assert (await connecting).data.status == "disconnected"
    assert (
        repo.get_telegram_session_status(credential_ref=account_ref, runtime=runtime)[
            "desired_connected"
        ]
        is False
    )
    assert _credential(session, account_ref).status == "disconnected"
    assert (
        repo.record_telegram_authorization_state(
            credential_ref=account_ref,
            generation=1,
            state={"@type": "authorizationStateReady"},
        ).status
        == "disconnected"
    )


@pytest.mark.asyncio
async def test_failed_bootstrap_can_be_retried_explicitly(
    session: Session,
    tmp_path: Path,
) -> None:
    account_ref = _user_account(session)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    failed_runtime = _Runtime(states=[])
    with pytest.raises(IndexError):
        await repo.connect_telegram_session(
            credential_ref=account_ref, runtime=failed_runtime, settings=settings
        )
    assert repo.telegram_authorization_status(credential_ref=account_ref).status == (
        "repair-required"
    )

    retry_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    connected = await repo.connect_telegram_session(
        credential_ref=account_ref, runtime=retry_runtime, settings=settings
    )
    assert connected.data.status == "connected"
    assert len(retry_runtime.configurations) == 1


@pytest.mark.parametrize("account_kind", ["bot", "user"])
def test_revoke_cleanup_removes_only_account_native_files(
    session: Session,
    tmp_path: Path,
    account_kind: str,
) -> None:
    account_ref = (
        _bot_account(session, display_name="Cleanup bot")
        if account_kind == "bot"
        else _user_account(session)
    )
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    native = settings.data_dir / "telegram-tdlib" / account_ref
    (native / "database").mkdir(parents=True)
    (native / "files").mkdir()
    (native / "database" / "db.bin").write_bytes(b"private")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"keep")
    (native / "files" / "outside-link").symlink_to(outside)

    remove_telegram_local_data(settings=settings, credential_ref=account_ref)
    remove_telegram_local_data(settings=settings, credential_ref=account_ref)

    assert not native.exists()
    assert outside.read_bytes() == b"keep"


def test_revoke_cleanup_rejects_symlinked_account_root(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    native_root = settings.data_dir / "telegram-tdlib"
    native_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.bin").write_bytes(b"keep")
    (native_root / "cred_safe").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ConflictError, match="symbolic link"):
        remove_telegram_local_data(settings=settings, credential_ref="cred_safe")
    with pytest.raises(ValidationError, match="reference"):
        remove_telegram_local_data(settings=settings, credential_ref="../outside")
    assert (outside / "private.bin").read_bytes() == b"keep"
