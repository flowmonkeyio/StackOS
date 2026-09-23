"""Attached project agents control one shared Telegram TDLib session explicitly."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.telegram_application import TelegramApplicationRepository
from stackos.config import Settings
from stackos.db.models import CredentialUsageEvent
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.auth_handlers import (
    AccountAuthorizationCancelInput,
    AccountAuthorizationSubmitInput,
    AccountRevokeInput,
    AccountSessionInput,
    AccountStartInput,
    AccountUpdateInput,
    account_authorization_cancel,
    account_authorization_submit,
    account_revoke,
    account_session_connect,
    account_session_disconnect,
    account_session_status,
    account_start,
    account_update,
)
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.projects import ProjectRepository
from tests.integration.test_repositories.test_telegram_native_authorization import (
    _bot_account,
    _credential,
    _decrypted_payload,
    _Runtime,
    _user_account,
)


def _project(session: Session, *, slug: str) -> int:
    created = ProjectRepository(session).create(
        slug=slug,
        name=slug.replace("-", " ").title(),
        domain=f"{slug}.example.test",
        locale="en-US",
    )
    assert created.data.id is not None
    return created.data.id


@pytest.mark.asyncio
async def test_any_attached_project_controls_one_shared_session_and_audit(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    project_b = _project(session, slug="telegram-shared-b")
    project_c = _project(session, slug="telegram-unattached-c")
    account_ref = _bot_account(session, display_name="Shared operator bot")
    repo = AuthRepository(session)
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    repo.attach_account(project_id=project_b, credential_ref=account_ref)
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    ctx = MCPContext(
        session=session,
        request_id="attached-project-operator",
        project_id=project_b,
        extras={
            "telegram_runtime": runtime,
            "settings": Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"),
        },
    )
    inp = AccountSessionInput(project_id=project_b, credential_ref=account_ref)
    emitter = ProgressEmitter(None, None)

    initial = await account_session_status(inp, ctx, emitter)
    assert (initial.desired_connected, initial.connected, initial.status) == (
        False,
        False,
        "disconnected",
    )
    assert initial.project_ids == sorted([project_id, project_b])
    assert initial.affects_other_projects is True

    connected = (await account_session_connect(inp, ctx, emitter)).data
    assert (connected.desired_connected, connected.connected, connected.status) == (
        True,
        True,
        "connected",
    )
    assert connected.project_ids == initial.project_ids
    assert connected.next_action is None

    delivery = ActionRepository(session)
    admission = delivery.admit_delivery(
        project_id=project_id,
        credential_ref=account_ref,
        destination_ref="telegram-chat:shared-lease",
    )
    assert admission.admitted
    with pytest.raises(ConflictError, match="draining"):
        await account_session_disconnect(inp, ctx, emitter)
    assert (
        repo.get_telegram_session_status(credential_ref=account_ref, runtime=runtime)[
            "desired_connected"
        ]
        is True
    )
    delivery.release_delivery_admission(
        credential_ref=account_ref,
        destination_ref="telegram-chat:shared-lease",
        lease_ref=admission.lease_ref,
    )

    disconnected = (await account_session_disconnect(inp, ctx, emitter)).data
    assert (disconnected.desired_connected, disconnected.connected, disconnected.status) == (
        False,
        False,
        "disconnected",
    )
    assert disconnected.project_ids == initial.project_ids
    assert runtime.closed == [(account_ref, 1)]

    events = session.exec(
        select(CredentialUsageEvent).where(
            CredentialUsageEvent.operation.in_(
                ["account.session.connect", "account.session.disconnect"]
            )
        )
    ).all()
    assert [event.operation for event in events] == [
        "account.session.connect",
        "account.session.disconnect",
    ]
    for event in events:
        assert event.project_id == project_b
        assert event.metadata_json["invoking_project_id"] == project_b
        assert event.metadata_json["affected_project_ids"] == initial.project_ids

    denied = AccountSessionInput(project_id=project_c, credential_ref=account_ref)
    with pytest.raises(NotFoundError, match="not attached"):
        await account_session_status(denied, ctx, emitter)
    with pytest.raises(NotFoundError, match="not attached"):
        await account_session_connect(denied, ctx, emitter)
    with pytest.raises(NotFoundError, match="not attached"):
        await account_session_disconnect(denied, ctx, emitter)


@pytest.mark.asyncio
async def test_user_connect_returns_only_local_authorization_repair(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _user_account(session)
    AuthRepository(session).attach_account(project_id=project_id, credential_ref=account_ref)
    runtime = _Runtime(states=[{"@type": "authorizationStateWaitPhoneNumber"}])
    ctx = MCPContext(
        session=session,
        request_id="user-connect-without-login",
        project_id=project_id,
        extras={
            "telegram_runtime": runtime,
            "settings": Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"),
        },
    )
    result = (
        await account_session_connect(
            AccountSessionInput(project_id=project_id, credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    ).data
    assert result.desired_connected is True
    assert result.connected is False
    assert result.status == "authorization_required"
    assert result.next_action is not None and "local Accounts" in result.next_action
    assert "challenge" not in result.model_dump()
    assert runtime.requests == []


@pytest.mark.asyncio
async def test_revoke_retires_local_database_without_claiming_remote_logout(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _bot_account(session, display_name="Revoke local data bot")
    repo = AuthRepository(session)
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    ctx = MCPContext(
        session=session,
        request_id="revoke-native-local-data",
        project_id=project_id,
        extras={"telegram_runtime": runtime, "settings": settings},
    )
    await account_session_connect(
        AccountSessionInput(project_id=project_id, credential_ref=account_ref),
        ctx,
        ProgressEmitter(None, None),
    )
    native_dir = settings.data_dir / "telegram-tdlib" / account_ref / "database"
    native_dir.mkdir(parents=True, exist_ok=True)
    (native_dir / "td.bin").write_bytes(b"encrypted-local-state")
    repo.detach_account(project_id=project_id, credential_ref=account_ref)

    first = (
        await account_revoke(
            AccountRevokeInput(credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    ).data
    assert first.status == "revoked"
    assert first.local_data_removed is True
    assert first.remote_logout_status == "unconfirmed"
    assert not native_dir.parent.exists()
    assert repo.get_account(credential_ref=account_ref).status == "revoked"
    assert TelegramApplicationRepository(session).configured() is False

    retried = (
        await account_revoke(
            AccountRevokeInput(credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    ).data
    assert retried.local_data_removed is True
    assert retried.remote_logout_status == "unconfirmed"


@pytest.mark.asyncio
async def test_revoke_cleanup_failure_can_be_retried_after_repair(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _bot_account(session, display_name="Revoke retry bot")
    repo = AuthRepository(session)
    repo.attach_account(project_id=project_id, credential_ref=account_ref)
    repo.detach_account(project_id=project_id, credential_ref=account_ref)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    native_root = settings.data_dir / "telegram-tdlib"
    native_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep")
    bad_link = native_root / account_ref
    bad_link.symlink_to(outside, target_is_directory=True)
    ctx = MCPContext(
        session=session,
        request_id="revoke-cleanup-retry",
        extras={
            "telegram_runtime": _Runtime(states=[]),
            "settings": settings,
        },
    )

    with pytest.raises(ConflictError, match="symbolic link") as error:
        await account_revoke(
            AccountRevokeInput(credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    assert error.value.data["next_action"]
    assert repo.get_account(credential_ref=account_ref).status == "revoked"
    assert sentinel.read_text() == "keep"

    bad_link.unlink()
    repaired = (
        await account_revoke(
            AccountRevokeInput(credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    ).data
    assert repaired.local_data_removed is True
    assert sentinel.read_text() == "keep"


@pytest.mark.asyncio
async def test_account_proxy_edit_while_disconnected_does_not_connect(
    session: Session,
    tmp_path,
) -> None:
    account_ref = _bot_account(session, display_name="Offline proxy edit bot")
    runtime = _Runtime(states=[])
    ctx = MCPContext(
        session=session,
        request_id="offline-proxy-edit",
        extras={
            "telegram_runtime": runtime,
            "settings": Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"),
        },
    )
    await account_update(
        AccountUpdateInput(
            credential_ref=account_ref,
            fields={
                "proxy_enabled": True,
                "proxy_type": "socks5",
                "proxy_host": "proxy.example.test",
                "proxy_port": 1080,
            },
        ),
        ctx,
        ProgressEmitter(None, None),
    )
    assert runtime.configurations == []
    assert (
        AuthRepository(session).get_telegram_session_status(
            credential_ref=account_ref, runtime=runtime
        )["desired_connected"]
        is False
    )


@pytest.mark.asyncio
async def test_identity_edit_invalidates_saved_sign_in_and_removes_native_database(
    session: Session,
    tmp_path,
) -> None:
    account_ref = _bot_account(session, display_name="Edited bot identity")
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    first = _Runtime(states=[{"@type": "authorizationStateReady"}])
    await repo.start_telegram_authorization(
        credential_ref=account_ref,
        runtime=first,
        settings=settings,
        sign_in_only=True,
    )
    tested_before_edit = await repo.test_telegram_authorization(
        credential_ref=account_ref,
        runtime=_Runtime(states=[{"@type": "authorizationStateReady"}]),
        settings=settings,
        project_id=None,
    )
    assert tested_before_edit.data.ok is True
    old_key = _decrypted_payload(session, account_ref)["_tdlib_database_encryption_key"]
    database = settings.data_dir / "telegram-tdlib" / account_ref / "database"
    sentinel = database / "old-session"
    sentinel.write_text("must be retired")
    assert repo.get_account(credential_ref=account_ref).account is not None
    ctx = MCPContext(
        session=session,
        request_id="edit-telegram-auth-identity",
        extras={
            "surface": "rest",
            "trusted_local_admin": True,
            "telegram_runtime": first,
            "settings": settings,
        },
    )

    edited = await account_update(
        AccountUpdateInput(
            credential_ref=account_ref, fields={"bot_token": "123456:new-bot-token"}
        ),
        ctx,
        ProgressEmitter(None, None),
    )

    assert edited.data.status == "pending"
    assert edited.data.setup_required is True
    assert edited.data.account is None
    assert edited.data.last_test is None
    assert edited.data.last_tested_at is None
    prior_tests = session.exec(
        select(CredentialUsageEvent).where(
            CredentialUsageEvent.credential_id == _credential(session, account_ref).id,
            CredentialUsageEvent.operation == "account.test",
        )
    ).all()
    assert len(prior_tests) == 1
    assert repo.telegram_authorization_status(credential_ref=account_ref).status == "pending"
    assert not sentinel.exists()
    assert "_tdlib_database_encryption_key" not in _decrypted_payload(session, account_ref)

    retry = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateReady"},
        ]
    )
    ctx.extras["telegram_runtime"] = retry
    restarted = await account_start(
        AccountStartInput(provider_key="telegram", credential_ref=account_ref),
        ctx,
        ProgressEmitter(None, None),
    )
    assert retry.configurations[0]["config"].database_encryption_key != old_key
    assert restarted.data.status == "disconnected"
    assert retry.requests[0]["token"] == "123456:new-bot-token"
    assert repo.get_account(credential_ref=account_ref).setup_required is False


@pytest.mark.asyncio
async def test_proxy_only_edit_keeps_saved_sign_in_and_tests_new_transport(
    session: Session,
    tmp_path,
) -> None:
    account_ref = _user_account(session)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    repo = AuthRepository(session)
    await repo.start_telegram_authorization(
        credential_ref=account_ref,
        runtime=_Runtime(states=[{"@type": "authorizationStateReady"}]),
        settings=settings,
        sign_in_only=True,
    )
    first_test = await repo.test_telegram_authorization(
        credential_ref=account_ref,
        runtime=_Runtime(states=[{"@type": "authorizationStateReady"}]),
        settings=settings,
        project_id=None,
    )
    assert first_test.data.ok is True
    old_key = _decrypted_payload(session, account_ref)["_tdlib_database_encryption_key"]
    database = settings.data_dir / "telegram-tdlib" / account_ref / "database"
    sentinel = database / "saved-session"
    sentinel.write_text("keep")
    ctx = MCPContext(
        session=session,
        request_id="edit-telegram-proxy",
        extras={"telegram_runtime": _Runtime(states=[]), "settings": settings},
    )

    edited = await account_update(
        AccountUpdateInput(
            credential_ref=account_ref,
            fields={
                "proxy_enabled": True,
                "proxy_type": "socks5",
                "proxy_host": "proxy.example.test",
                "proxy_port": 1080,
            },
        ),
        ctx,
        ProgressEmitter(None, None),
    )

    assert edited.data.status == "disconnected"
    assert edited.data.setup_required is False
    assert edited.data.account is not None
    assert edited.data.account["provider_account_id"] == "123456"
    assert edited.data.last_test is None
    assert edited.data.last_tested_at is None
    prior_tests = session.exec(
        select(CredentialUsageEvent).where(
            CredentialUsageEvent.credential_id == _credential(session, account_ref).id,
            CredentialUsageEvent.operation == "account.test",
        )
    ).all()
    assert len(prior_tests) == 1
    assert repo.telegram_authorization_status(credential_ref=account_ref).status == ("disconnected")
    assert repo.get_account(credential_ref=account_ref).last_test is None
    assert repo.get_account(credential_ref=account_ref).last_tested_at is None
    assert sentinel.read_text() == "keep"
    probe = _Runtime(states=[{"@type": "authorizationStateReady"}])
    tested = await repo.test_telegram_authorization(
        credential_ref=account_ref, runtime=probe, settings=settings, project_id=None
    )
    assert tested.data.ok is True
    assert probe.configurations[0]["config"].database_encryption_key == old_key
    assert probe.configurations[0]["config"].proxy.host == "proxy.example.test"
    assert probe.requests == []


@pytest.mark.asyncio
async def test_local_user_sign_in_is_temporary_through_password_then_explicit_connect(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _user_account(session)
    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateWaitCode"},
            {"@type": "authorizationStateWaitPassword"},
            {"@type": "authorizationStateReady"},
            {"@type": "authorizationStateReady"},
        ]
    )
    ctx = MCPContext(
        session=session,
        request_id="local-user-sign-in",
        extras={
            "surface": "rest",
            "trusted_local_admin": True,
            "telegram_runtime": runtime,
            "settings": Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"),
        },
    )
    started = (
        await account_start(
            AccountStartInput(provider_key="telegram", credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    ).data
    assert started.challenge is not None and started.challenge.kind == "phone_number"
    assert (
        AuthRepository(session).get_telegram_session_status(
            credential_ref=account_ref, runtime=runtime
        )["desired_connected"]
        is False
    )
    with pytest.raises(ConflictError, match="temporary Telegram sign-in"):
        await account_update(
            AccountUpdateInput(
                credential_ref=account_ref,
                fields={"proxy_enabled": False},
            ),
            ctx,
            ProgressEmitter(None, None),
        )

    for answer, expected in (
        ({"phone_number": "+15551234567"}, "code"),
        ({"code": "598743"}, "password"),
    ):
        submitted = (
            await account_authorization_submit(
                AccountAuthorizationSubmitInput(
                    credential_ref=account_ref,
                    generation=started.challenge.generation,
                    answer=answer,
                ),
                ctx,
                ProgressEmitter(None, None),
            )
        ).data
        assert submitted.challenge is not None
        assert submitted.challenge.kind == expected

    completed = (
        await account_authorization_submit(
            AccountAuthorizationSubmitInput(
                credential_ref=account_ref,
                generation=started.challenge.generation,
                answer={"password": "write-only-password"},
            ),
            ctx,
            ProgressEmitter(None, None),
        )
    ).data
    assert completed.status == "disconnected"
    assert runtime.closed == [(account_ref, started.challenge.generation)]
    assert (
        AuthRepository(session).get_telegram_session_status(
            credential_ref=account_ref, runtime=runtime
        )["desired_connected"]
        is False
    )
    durable = json.dumps(_credential(session, account_ref).config_json)
    backing = json.dumps(_decrypted_payload(session, account_ref))
    for secret in ("+15551234567", "598743", "write-only-password"):
        assert secret not in durable
        assert secret not in backing

    AuthRepository(session).attach_account(project_id=project_id, credential_ref=account_ref)
    ctx.project_id = project_id
    connected = (
        await account_session_connect(
            AccountSessionInput(project_id=project_id, credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    ).data
    assert connected.connected is True
    assert connected.desired_connected is True
    assert len(runtime.configurations) == 2


@pytest.mark.asyncio
async def test_local_bot_start_saves_sign_in_then_project_connect_reuses_it(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _bot_account(session, display_name="Local bot sign-in")
    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateReady"},
            {"@type": "authorizationStateReady"},
        ]
    )
    ctx = MCPContext(
        session=session,
        request_id="local-bot-sign-in",
        extras={
            "surface": "rest",
            "trusted_local_admin": True,
            "telegram_runtime": runtime,
            "settings": Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"),
        },
    )
    signed_in = await account_start(
        AccountStartInput(provider_key="telegram", credential_ref=account_ref),
        ctx,
        ProgressEmitter(None, None),
    )
    assert signed_in.data.status == "disconnected"
    assert runtime.requests == [
        {"@type": "checkAuthenticationBotToken", "token": "123456:bot-token"}
    ]
    assert runtime.closed == [(account_ref, 1)]
    assert (
        AuthRepository(session).get_telegram_session_status(
            credential_ref=account_ref, runtime=runtime
        )["desired_connected"]
        is False
    )

    AuthRepository(session).attach_account(project_id=project_id, credential_ref=account_ref)
    connected = await account_session_connect(
        AccountSessionInput(project_id=project_id, credential_ref=account_ref),
        MCPContext(
            session=session,
            request_id="project-bot-connect",
            project_id=project_id,
            extras=ctx.extras,
        ),
        ProgressEmitter(None, None),
    )
    assert connected.data.connected is True
    assert len(runtime.configurations) == 2
    assert len(runtime.requests) == 1


@pytest.mark.asyncio
async def test_pending_local_sign_in_blocks_edits_and_cancel_retires_late_native_client(
    session: Session,
    tmp_path,
) -> None:
    class SlowRuntime(_Runtime):
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
    runtime = SlowRuntime()
    ctx = MCPContext(
        session=session,
        request_id="cancel-pending-local-sign-in",
        extras={
            "surface": "rest",
            "trusted_local_admin": True,
            "telegram_runtime": runtime,
            "settings": Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"),
        },
    )
    signing_in = asyncio.create_task(
        account_start(
            AccountStartInput(provider_key="telegram", credential_ref=account_ref),
            ctx,
            ProgressEmitter(None, None),
        )
    )
    await asyncio.wait_for(runtime.started.wait(), timeout=2)
    generation = (
        AuthRepository(session).telegram_authorization_status(credential_ref=account_ref).generation
    )
    assert generation == 1

    with pytest.raises(ConflictError, match="temporary Telegram sign-in"):
        await account_update(
            AccountUpdateInput(
                credential_ref=account_ref,
                fields={
                    "proxy_enabled": True,
                    "proxy_type": "socks5",
                    "proxy_host": "proxy.example.test",
                    "proxy_port": 1080,
                },
            ),
            ctx,
            ProgressEmitter(None, None),
        )
    cancelled = await account_authorization_cancel(
        AccountAuthorizationCancelInput(credential_ref=account_ref, generation=generation),
        ctx,
        ProgressEmitter(None, None),
    )
    assert cancelled.data.status == "pending"
    runtime.release.set()
    finished = await asyncio.wait_for(signing_in, timeout=2)
    assert finished.data.status == "pending"
    assert runtime.active_generation_value is None
    assert runtime.closed == [(account_ref, generation), (account_ref, generation)]


@pytest.mark.asyncio
async def test_connected_account_can_be_renamed_without_session_change(
    session: Session,
    project_id: int,
    tmp_path,
) -> None:
    account_ref = _bot_account(session, display_name="Before rename")
    AuthRepository(session).attach_account(project_id=project_id, credential_ref=account_ref)
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    ctx = MCPContext(
        session=session,
        request_id="connected-account-rename",
        project_id=project_id,
        extras={
            "telegram_runtime": runtime,
            "settings": Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state"),
        },
    )
    await account_session_connect(
        AccountSessionInput(project_id=project_id, credential_ref=account_ref),
        ctx,
        ProgressEmitter(None, None),
    )
    changed = await account_update(
        AccountUpdateInput(credential_ref=account_ref, display_name="After rename"),
        ctx,
        ProgressEmitter(None, None),
    )
    assert changed.data.display_name == "After rename"
    assert len(runtime.configurations) == 1
    assert runtime.closed == []
    assert (
        AuthRepository(session).get_telegram_session_status(
            credential_ref=account_ref, runtime=runtime
        )["connected"]
        is True
    )
