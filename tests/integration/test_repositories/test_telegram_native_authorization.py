"""Native TDLib Account authorization owns state without retaining answers."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.telegram_application import TelegramApplicationRepository
from stackos.auth_providers.repository.utils import utcnow
from stackos.config import Settings
from stackos.db.models import Credential, IntegrationCredential
from stackos.integrations.telegram_tdlib.daemon import restore_telegram_accounts
from stackos.integrations.telegram_tdlib.service import TelegramTdlibService
from stackos.integrations.telegram_tdlib.sessions import TelegramTdlibSessionReceipt
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.auth_handlers import (
    AccountRevokeInput,
    AccountUpdateInput,
    account_revoke,
    account_update,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository
from tests.unit.test_telegram_tdlib_service import _Registry, _Session


class _Runtime:
    def __init__(self, *, states: list[dict[str, Any]]) -> None:
        self.states = list(states)
        self.configurations: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.native_requests: list[dict[str, Any]] = []
        self.closed: list[tuple[str, int]] = []
        self.me: dict[str, Any] = {"@type": "user", "id": 123456, "first_name": "Native"}
        self.active_generation_value: int | None = None

    @asynccontextmanager
    async def account_transition(self, _account_ref: str):
        yield

    def active_generation(self, *, account_ref: str) -> int | None:
        assert account_ref
        return self.active_generation_value

    async def configure(self, **kwargs: Any) -> dict[str, Any]:
        self.configurations.append(kwargs)
        self.active_generation_value = kwargs["generation"]
        return self.states.pop(0)

    async def request_authorization(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(dict(kwargs["request"]))
        return self.states.pop(0)

    async def request(
        self,
        account_ref: str,
        request: Mapping[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert account_ref
        self.native_requests.append(dict(request))
        assert request == {"@type": "getMe"}
        return dict(self.me)

    def bootstrap_receipt(
        self, *, account_ref: str, generation: int
    ) -> TelegramTdlibSessionReceipt:
        return TelegramTdlibSessionReceipt(account_kind="user", proxy_id=42, proxy_enabled=True)

    async def close(self, *, account_ref: str, generation: int) -> None:
        self.closed.append((account_ref, generation))
        if self.active_generation_value == generation:
            self.active_generation_value = None


class _IdentitySession(_Session):
    def __init__(self) -> None:
        super().__init__()
        self.closed_event = asyncio.Event()
        self.me_id = 987654

    async def _request(self, request, **kwargs):
        if request.get("@type") == "getMe":
            return {"@type": "user", "id": self.me_id, "first_name": "Native user"}
        return await super()._request(request, **kwargs)

    async def close(self, *, timeout_seconds: float = 10.0) -> None:
        await super().close(timeout_seconds=timeout_seconds)
        self.closed_event.set()


class _BlockingIdentitySession(_IdentitySession):
    def __init__(self) -> None:
        super().__init__()
        self.get_me_started = asyncio.Event()
        self.release_get_me = asyncio.Event()

    async def _request(self, request, **kwargs):
        if request.get("@type") == "getMe":
            self.get_me_started.set()
            await self.release_get_me.wait()
        return await super()._request(request, **kwargs)


class _BlockingAuthorizationSession(_IdentitySession):
    def __init__(self) -> None:
        super().__init__()
        self.authorization_started = asyncio.Event()
        self.release_authorization = asyncio.Event()

    async def next_update(self) -> dict[str, Any]:
        update = await super().next_update()
        self.authorization_started.set()
        await self.release_authorization.wait()
        return update


def _queue_ready(native: _IdentitySession) -> None:
    native.updates.put_nowait(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )


def _native_authorization_service(
    engine: Engine, *natives: _IdentitySession
) -> TelegramTdlibService:
    async def authorization_sink(account_ref: str, generation: int, state: dict[str, Any]) -> None:
        with Session(engine) as callback_session:
            callback_repo = AuthRepository(callback_session)
            status = callback_repo.record_telegram_authorization_state(
                credential_ref=account_ref, generation=generation, state=state
            )
            if status.status == "verifying":
                await callback_repo.synchronize_telegram_ready(
                    credential_ref=account_ref, generation=generation, runtime=runtime
                )

    async def settled_sink(account_ref: str, generation: int, _state: dict[str, Any]) -> None:
        with Session(engine) as callback_session:
            await AuthRepository(callback_session).finish_telegram_sign_in_if_ready(
                credential_ref=account_ref, generation=generation, runtime=runtime
            )

    runtime = TelegramTdlibService(
        session_registry=_Registry(deque(natives)),
        authorization_sink=authorization_sink,
        authorization_settled_sink=settled_sink,
    )
    return runtime


async def _wait_for_native_request(native: _IdentitySession, count: int) -> None:
    for _ in range(100):
        if len(native.requests) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"TDLib did not receive request {count}")


def _credential(session: Session, credential_ref: str) -> Credential:
    return session.exec(select(Credential).where(Credential.credential_ref == credential_ref)).one()


def _decrypted_payload(session: Session, credential_ref: str) -> dict[str, object]:
    credential = _credential(session, credential_ref)
    assert credential.integration_credential_id is not None
    row = session.get(IntegrationCredential, credential.integration_credential_id)
    assert row is not None
    return json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id).decode())


def _user_account(session: Session) -> str:
    app_fields = (
        {}
        if TelegramApplicationRepository(session).configured()
        else {"api_id": 12345, "api_hash": "application-hash"}
    )
    return (
        AuthRepository(session)
        .store_credential(
            provider_key="telegram",
            auth_method_key="tdlib-user-session",
            display_name="Native user",
            fields={**app_fields, "proxy_enabled": False},
        )
        .data.credential_ref
    )


def _bot_account(session: Session, *, display_name: str) -> str:
    app_fields = (
        {}
        if TelegramApplicationRepository(session).configured()
        else {"api_id": 12345, "api_hash": "application-hash"}
    )
    return (
        AuthRepository(session)
        .store_credential(
            provider_key="telegram",
            auth_method_key="tdlib-bot-token",
            display_name=display_name,
            fields={
                **app_fields,
                "bot_token": "123456:bot-token",
                "proxy_enabled": False,
            },
        )
        .data.credential_ref
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["user", "bot"])
async def test_queued_disconnect_prevents_an_earlier_connect_from_opening_tdlib(
    session: Session, settings: Settings, kind: str
) -> None:
    repo = AuthRepository(session)
    account_ref = (
        _user_account(session)
        if kind == "user"
        else _bot_account(session, display_name="Queued bot")
    )
    native = _IdentitySession()
    native.updates.put_nowait(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
        }
    )
    runtime = _native_authorization_service(session.get_bind(), native)
    try:
        async with runtime.account_transition(account_ref):
            connecting = asyncio.create_task(
                repo.connect_telegram_session(
                    credential_ref=account_ref, runtime=runtime, settings=settings
                )
            )
            await asyncio.sleep(0)
            disconnecting = asyncio.create_task(
                repo.disconnect_telegram_session(credential_ref=account_ref, runtime=runtime)
            )
            await asyncio.sleep(0)
        results = await asyncio.wait_for(asyncio.gather(connecting, disconnecting), timeout=3)
        assert [result.data.status for result in results] == ["disconnected", "disconnected"]
        assert runtime.active_generation(account_ref=account_ref) is None
        assert native.requests == []
        assert not repo.get_telegram_session_status(credential_ref=account_ref)["desired_connected"]
    finally:
        await runtime.close_all()


@pytest.mark.asyncio
async def test_new_connect_after_queued_disconnect_does_not_revive_the_old_connect(
    session: Session, settings: Settings
) -> None:
    repo = AuthRepository(session)
    account_ref = _user_account(session)
    native = _IdentitySession()
    native.updates.put_nowait(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
        }
    )
    runtime = _native_authorization_service(session.get_bind(), native)
    try:
        async with runtime.account_transition(account_ref):
            old_connect = asyncio.create_task(
                repo.connect_telegram_session(
                    credential_ref=account_ref, runtime=runtime, settings=settings
                )
            )
            await asyncio.sleep(0)
            disconnect = asyncio.create_task(
                repo.disconnect_telegram_session(credential_ref=account_ref, runtime=runtime)
            )
            await asyncio.sleep(0)
            new_connect = asyncio.create_task(
                repo.connect_telegram_session(
                    credential_ref=account_ref, runtime=runtime, settings=settings
                )
            )
            await asyncio.sleep(0)
        old, stopped, new = await asyncio.wait_for(
            asyncio.gather(old_connect, disconnect, new_connect), timeout=3
        )
        assert old.data.status == "disconnected"
        assert stopped.data.status == "disconnected"
        assert new.data.status == "challenge"
        assert runtime.active_generation(account_ref=account_ref) == new.data.generation
        assert repo.get_telegram_session_status(credential_ref=account_ref)["desired_connected"]
    finally:
        await runtime.close_all()


@pytest.mark.asyncio
async def test_conflicting_legacy_application_is_visible_and_cannot_connect(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _bot_account(session, display_name="Conflicting old application")
    credential = _credential(session, credential_ref)
    config = dict(credential.config_json or {})
    config["telegram_application_conflict"] = True
    config["telegram_desired_connected"] = False
    credential.config_json = config
    credential.status = "repair-required"
    session.add(credential)
    session.commit()

    repo = AuthRepository(session)
    status = repo.get_telegram_session_status(credential_ref=credential_ref)
    assert status["status"] == "repair-required"
    assert status["connected"] is False
    assert status["desired_connected"] is False
    with pytest.raises(ConflictError, match="different application identity"):
        await repo.connect_telegram_session(
            credential_ref=credential_ref,
            runtime=_Runtime(states=[]),
            settings=settings,
        )
    assert repo.get_telegram_session_status(credential_ref=credential_ref) == status


def test_native_user_challenges_are_generation_fenced_and_never_persist_answers(
    session: Session,
    settings: Any,
) -> None:
    credential_ref = _user_account(session)
    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {
                "@type": "authorizationStateWaitCode",
                "code_info": {
                    "@type": "authenticationCodeInfo",
                    "timeout": 60,
                    "type": {"@type": "authenticationCodeTypeTelegramMessage"},
                },
            },
        ]
    )
    repo = AuthRepository(session)

    started = asyncio.run(
        repo.start_telegram_authorization(
            credential_ref=credential_ref,
            runtime=runtime,
            settings=settings,
        )
    ).data
    assert started.status == "challenge"
    assert started.challenge is not None
    assert started.challenge.kind == "phone_number"
    assert started.challenge.generation == 1

    submitted = asyncio.run(
        repo.submit_telegram_authorization(
            credential_ref=credential_ref,
            generation=1,
            answer={"phone_number": "+15551234567"},
            runtime=runtime,
        )
    ).data
    assert submitted.challenge is not None
    assert submitted.challenge.kind == "code"
    assert runtime.requests == [
        {
            "@type": "setAuthenticationPhoneNumber",
            "phone_number": "+15551234567",
            "settings": None,
        }
    ]
    serialized = json.dumps(_credential(session, credential_ref).config_json)
    assert "+15551234567" not in serialized
    assert "+15551234567" not in json.dumps(_decrypted_payload(session, credential_ref))
    assert "_tdlib_database_encryption_key" in _decrypted_payload(session, credential_ref)
    config = _credential(session, credential_ref).config_json
    assert config is not None
    assert "api_id" not in config
    assert TelegramApplicationRepository(session).get().api_id == 12345
    assert config["auth_method_key"] == "tdlib-user-session"
    assert config["proxy_enabled"] is False
    assert config["tdlib_proxy_id"] == 42
    auth = config["telegram_auth"]
    assert auth["challenge_kind"] == "code"
    assert auth["generation"] == 1
    assert auth["state"] == "challenge"
    assert isinstance(auth["updated_at"], str)
    assert isinstance(auth["challenge_expires_at"], str)
    assert auth["challenge_metadata"]["timeout_seconds"] == 60
    assert auth["challenge_metadata"]["delivery_type"] == "authenticationCodeTypeTelegramMessage"
    with pytest.raises(ConflictError, match="generation is stale"):
        asyncio.run(
            repo.submit_telegram_authorization(
                credential_ref=credential_ref,
                generation=0,
                answer={"code": "12345"},
                runtime=runtime,
            )
        )
    expired_credential = _credential(session, credential_ref)
    assert expired_credential.config_json is not None
    expired_config = dict(expired_credential.config_json)
    expired_auth = dict(expired_config["telegram_auth"])
    expired_auth["challenge_expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
    expired_config["telegram_auth"] = expired_auth
    expired_credential.config_json = expired_config
    session.add(expired_credential)
    session.commit()
    with pytest.raises(ConflictError, match="challenge expired"):
        asyncio.run(
            repo.submit_telegram_authorization(
                credential_ref=credential_ref,
                generation=1,
                answer={"code": "12345"},
                runtime=runtime,
            )
        )
    assert (
        repo.telegram_authorization_status(credential_ref=credential_ref).status
        == "repair-required"
    )


def test_native_qr_link_is_transient_and_stale_callback_cannot_replace_current_state(
    session: Session,
    settings: Any,
) -> None:
    credential_ref = _user_account(session)
    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {
                "@type": "authorizationStateWaitOtherDeviceConfirmation",
                "link": "tg://login?token=must-not-persist",
            },
        ]
    )
    repo = AuthRepository(session)

    started = asyncio.run(
        repo.start_telegram_authorization(
            credential_ref=credential_ref,
            authorization_mode="qr",
            runtime=runtime,
            settings=settings,
        )
    ).data
    assert started.challenge is not None
    assert started.challenge.kind == "qr"
    assert started.challenge.qr_link == "tg://login?token=must-not-persist"
    serialized = json.dumps(_credential(session, credential_ref).config_json)
    assert "must-not-persist" not in serialized

    recorded = repo.record_telegram_authorization_state(
        credential_ref=credential_ref,
        generation=0,
        state={"@type": "authorizationStateReady"},
    )
    assert recorded.generation == 1
    assert recorded.status == "challenge"
    canceled = asyncio.run(
        repo.cancel_telegram_authorization(
            credential_ref=credential_ref,
            generation=1,
            runtime=runtime,
        )
    ).data
    assert canceled.status == "pending"
    assert canceled.generation == 2
    assert runtime.closed == [(credential_ref, 1)]
    assert (
        repo.record_telegram_authorization_state(
            credential_ref=credential_ref,
            generation=1,
            state={"@type": "authorizationStateReady"},
        ).status
        == "pending"
    )


def test_native_bot_identity_refuses_a_duplicate_physical_account(
    session: Session,
    settings: Any,
) -> None:
    first_ref = _bot_account(session, display_name="Native bot one")
    first_runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateReady"},
        ]
    )
    first = asyncio.run(
        AuthRepository(session).start_telegram_authorization(
            credential_ref=first_ref,
            runtime=first_runtime,
            settings=settings,
        )
    ).data
    assert first.status == "connected"
    assert first_runtime.requests == [
        {"@type": "checkAuthenticationBotToken", "token": "123456:bot-token"}
    ]
    assert first_runtime.native_requests == [{"@type": "getMe"}]

    duplicate_ref = _bot_account(session, display_name="Native bot two")
    duplicate_runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateReady"},
        ]
    )
    with pytest.raises(ConflictError, match="already connected") as error:
        asyncio.run(
            AuthRepository(session).start_telegram_authorization(
                credential_ref=duplicate_ref,
                runtime=duplicate_runtime,
                settings=settings,
            )
        )
    assert error.value.data["existing_credential_ref"] == first_ref
    duplicate_status = AuthRepository(session).telegram_authorization_status(
        credential_ref=duplicate_ref
    )
    assert duplicate_status.status == "repair-required"
    assert _credential(session, duplicate_ref).status == "pending"


def test_resume_uses_the_restored_tdlib_state_without_replaying_authorization(
    session: Session,
    settings: Any,
) -> None:
    credential_ref = _bot_account(session, display_name="Restored native bot")
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])

    resumed = asyncio.run(
        AuthRepository(session).resume_telegram_session(
            credential_ref=credential_ref,
            runtime=runtime,
            settings=settings,
        )
    ).data
    assert resumed.status == "connected"
    assert resumed.generation == 1
    assert runtime.requests == []
    assert runtime.native_requests == [{"@type": "getMe"}]


def test_start_does_not_replay_a_bot_token_when_the_configured_session_is_ready(
    session: Session,
    settings: Any,
) -> None:
    credential_ref = _bot_account(session, display_name="Already ready bot")
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])

    started = asyncio.run(
        AuthRepository(session).start_telegram_authorization(
            credential_ref=credential_ref,
            runtime=runtime,
            settings=settings,
        )
    ).data
    assert started.status == "connected"
    assert runtime.requests == []


def test_restored_bot_ready_requires_the_configured_token_identity(
    session: Session,
    settings: Any,
) -> None:
    credential_ref = _bot_account(session, display_name="Rotated bot")
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    runtime.me["id"] = 987654

    with pytest.raises(ConflictError, match="does not match the authenticated identity"):
        asyncio.run(
            AuthRepository(session).resume_telegram_session(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
            )
        )

    status = AuthRepository(session).telegram_authorization_status(credential_ref=credential_ref)
    assert status.status == "repair-required"
    assert _credential(session, credential_ref).status == "pending"
    assert runtime.closed == [(credential_ref, 1)]


@pytest.mark.asyncio
async def test_local_user_sign_in_saves_auth_then_connect_resumes_without_login_replay(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateWaitCode"},
            {"@type": "authorizationStateWaitPassword"},
            {"@type": "authorizationStateReady"},
        ]
    )

    started = await repo.start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=runtime,
        settings=settings,
        sign_in_only=True,
    )
    assert started.data.challenge is not None
    assert started.data.challenge.kind == "phone_number"
    for answer, expected in (
        ({"phone_number": "+15551234567"}, "code"),
        ({"code": "12345"}, "password"),
    ):
        status = await repo.submit_telegram_authorization(
            credential_ref=credential_ref,
            generation=1,
            answer=answer,
            runtime=runtime,
        )
        assert status.data.challenge is not None
        assert status.data.challenge.kind == expected
    completed = await repo.submit_telegram_authorization(
        credential_ref=credential_ref,
        generation=1,
        answer={"password": "private-password"},
        runtime=runtime,
    )

    assert completed.data.status == "disconnected"
    assert runtime.closed == [(credential_ref, 1)]
    assert repo.get_account(credential_ref=credential_ref).setup_required is False
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref)["desired_connected"]
        is False
    )
    assert "private-password" not in json.dumps(_credential(session, credential_ref).config_json)
    restoring = _Runtime(states=[])
    await restore_telegram_accounts(cast(Engine, session.get_bind()), settings, restoring)
    assert restoring.configurations == []

    connected_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    connected = await repo.connect_telegram_session(
        credential_ref=credential_ref,
        runtime=connected_runtime,
        settings=settings,
    )
    assert connected.data.status == "connected"
    assert connected_runtime.requests == []
    assert connected_runtime.configurations[0]["generation"] > 1
    await repo.disconnect_telegram_session(credential_ref=credential_ref, runtime=connected_runtime)
    resumed_runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    resumed = await repo.connect_telegram_session(
        credential_ref=credential_ref,
        runtime=resumed_runtime,
        settings=settings,
    )
    assert resumed.data.status == "connected"
    assert resumed_runtime.requests == []


@pytest.mark.asyncio
async def test_agent_connect_waits_for_local_sign_in_to_finish_or_cancel(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    runtime = _Runtime(states=[{"@type": "authorizationStateWaitPhoneNumber"}])
    await repo.start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=runtime,
        settings=settings,
        sign_in_only=True,
    )

    with pytest.raises(ConflictError, match="Finish or cancel local Telegram sign-in"):
        await repo.connect_telegram_session(
            credential_ref=credential_ref, runtime=runtime, settings=settings
        )
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref)["desired_connected"]
        is False
    )
    assert len(runtime.configurations) == 1


@pytest.mark.asyncio
async def test_duplicate_local_sign_in_joins_pending_native_bootstrap(
    session: Session,
    settings: Settings,
) -> None:
    class SlowRuntime(_Runtime):
        def __init__(self) -> None:
            super().__init__(states=[{"@type": "authorizationStateReady"}])
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def configure(self, **kwargs: Any) -> dict[str, Any]:
            self.started.set()
            await self.release.wait()
            return await super().configure(**kwargs)

        def is_connecting(self, *, account_ref: str, generation: int) -> bool:
            return self.started.is_set() and not self.release.is_set()

    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    runtime = SlowRuntime()
    first = asyncio.create_task(
        repo.start_telegram_authorization(
            credential_ref=credential_ref,
            runtime=runtime,
            settings=settings,
            sign_in_only=True,
        )
    )
    await asyncio.wait_for(runtime.started.wait(), timeout=2)
    second = await repo.start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=runtime,
        settings=settings,
        sign_in_only=True,
    )
    assert second.data.status == "pending"
    assert runtime.configurations == []
    runtime.release.set()
    assert (await first).data.status == "disconnected"
    assert len(runtime.configurations) == 1
    assert runtime.closed == [(credential_ref, 1)]


@pytest.mark.asyncio
async def test_saved_offline_user_account_test_reopens_database_gets_identity_and_closes(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    incomplete = await repo.test_telegram_authorization(
        credential_ref=credential_ref,
        runtime=_Runtime(states=[]),
        settings=settings,
        project_id=None,
    )
    assert incomplete.data.ok is False
    assert repo.get_account(credential_ref=credential_ref).last_test is not None
    assert repo.get_account(credential_ref=credential_ref).last_tested_at is not None
    sign_in = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateWaitCode"},
            {"@type": "authorizationStateReady"},
        ]
    )
    await repo.start_telegram_authorization(
        credential_ref=credential_ref, runtime=sign_in, settings=settings, sign_in_only=True
    )
    await repo.submit_telegram_authorization(
        credential_ref=credential_ref,
        generation=1,
        answer={"phone_number": "+15551234567"},
        runtime=sign_in,
    )
    await repo.submit_telegram_authorization(
        credential_ref=credential_ref,
        generation=1,
        answer={"code": "12345"},
        runtime=sign_in,
    )
    assert (
        repo.telegram_authorization_status(credential_ref=credential_ref).status == "disconnected"
    )
    assert repo.get_account(credential_ref=credential_ref).last_test is None
    assert repo.get_account(credential_ref=credential_ref).last_tested_at is None

    probe = _Runtime(states=[{"@type": "authorizationStateReady"}])
    tested = await repo.test_telegram_authorization(
        credential_ref=credential_ref, runtime=probe, settings=settings, project_id=None
    )

    assert tested.data.ok is True
    assert tested.data.metadata["provider_account_id"] == "123456"
    assert probe.configurations[0]["config"].database_encryption_key == (
        sign_in.configurations[0]["config"].database_encryption_key
    )
    assert probe.configurations[0]["config"].database_directory == (
        sign_in.configurations[0]["config"].database_directory
    )
    assert probe.requests == []
    assert probe.native_requests == [{"@type": "getMe"}]
    probe_generation = probe.configurations[0]["generation"]
    assert 1 << 61 <= probe_generation < 1 << 62
    assert probe.closed == [(credential_ref, probe_generation)]
    assert (
        repo.telegram_authorization_status(credential_ref=credential_ref).status == "disconnected"
    )
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref)["desired_connected"]
        is False
    )
    account = repo.get_account(credential_ref=credential_ref)
    assert account.status == "disconnected"
    assert account.setup_required is False
    assert account.last_test is not None and account.last_test.ok is True

    with pytest.raises(ValidationError, match="not declared"):
        repo.update_credential(
            credential_ref=credential_ref,
            fields={"api_hash": "rotated-application-hash"},
            display_name=None,
        )
    updated = repo.get_account(credential_ref=credential_ref)
    assert updated.status == "disconnected"
    assert updated.last_test is not None and updated.last_test.ok is True


@pytest.mark.asyncio
async def test_saved_offline_account_test_reports_expired_authorization_and_closes(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    signed_in = _Runtime(states=[{"@type": "authorizationStateReady"}])
    await repo.start_telegram_authorization(
        credential_ref=credential_ref, runtime=signed_in, settings=settings, sign_in_only=True
    )
    expired = _Runtime(states=[{"@type": "authorizationStateWaitPhoneNumber"}])

    tested = await repo.test_telegram_authorization(
        credential_ref=credential_ref, runtime=expired, settings=settings, project_id=None
    )

    assert tested.data.ok is False
    assert "reauthorize" in (tested.data.next_action or "").lower()
    assert expired.requests == []
    assert expired.native_requests == []
    assert expired.closed == [(credential_ref, expired.configurations[0]["generation"])]
    assert repo.telegram_authorization_status(credential_ref=credential_ref).status == (
        "repair-required"
    )
    assert repo.get_account(credential_ref=credential_ref).setup_required is True
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref)["desired_connected"]
        is False
    )


@pytest.mark.asyncio
async def test_saved_offline_account_test_rejects_a_different_restored_identity(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    await repo.start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=_Runtime(states=[{"@type": "authorizationStateReady"}]),
        settings=settings,
        sign_in_only=True,
    )
    probe = _Runtime(states=[{"@type": "authorizationStateReady"}])
    probe.me["id"] = 999999

    tested = await repo.test_telegram_authorization(
        credential_ref=credential_ref, runtime=probe, settings=settings, project_id=None
    )

    assert tested.data.ok is False
    assert "reauthorize" in (tested.data.next_action or "").lower()
    assert probe.closed == [(credential_ref, probe.configurations[0]["generation"])]
    assert repo.telegram_authorization_status(credential_ref=credential_ref).status == (
        "repair-required"
    )
    account = repo.get_account(credential_ref=credential_ref)
    assert account.account is not None
    assert account.account["provider_account_id"] == "123456"


@pytest.mark.asyncio
async def test_saved_offline_account_test_keeps_session_on_transient_probe_failure(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    await repo.start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=_Runtime(states=[{"@type": "authorizationStateReady"}]),
        settings=settings,
        sign_in_only=True,
    )

    class UnavailableRuntime(_Runtime):
        async def configure(self, **kwargs: Any) -> dict[str, Any]:
            self.configurations.append(kwargs)
            raise RuntimeError("network unavailable")

    probe = UnavailableRuntime(states=[])
    tested = await repo.test_telegram_authorization(
        credential_ref=credential_ref, runtime=probe, settings=settings, project_id=None
    )

    assert tested.data.ok is False
    assert tested.data.retryable is True
    assert probe.closed == [(credential_ref, probe.configurations[0]["generation"])]
    assert repo.telegram_authorization_status(credential_ref=credential_ref).status == (
        "disconnected"
    )
    assert repo.get_account(credential_ref=credential_ref).setup_required is False


@pytest.mark.asyncio
async def test_connected_account_test_reuses_live_session_without_retiring_it(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    runtime = _Runtime(states=[{"@type": "authorizationStateReady"}])
    await repo.connect_telegram_session(
        credential_ref=credential_ref, runtime=runtime, settings=settings
    )

    tested = await repo.test_telegram_authorization(
        credential_ref=credential_ref, runtime=runtime, settings=settings, project_id=None
    )

    assert tested.data.ok is True
    assert len(runtime.configurations) == 1
    assert runtime.closed == []
    assert runtime.native_requests == [{"@type": "getMe"}, {"@type": "getMe"}]
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref)["desired_connected"] is True
    )


@pytest.mark.asyncio
async def test_two_offline_account_tests_share_one_account_transition_without_clobbering(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    signed_in = _Runtime(states=[{"@type": "authorizationStateReady"}])
    signed_in.me["id"] = 987654
    await AuthRepository(session).start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=signed_in,
        settings=settings,
        sign_in_only=True,
    )
    engine = cast(Engine, session.get_bind())
    first_native = _BlockingIdentitySession()
    second_native = _IdentitySession()
    _queue_ready(first_native)
    _queue_ready(second_native)
    runtime = _native_authorization_service(engine, first_native, second_native)

    async def probe() -> Any:
        with Session(engine) as probe_session:
            return await AuthRepository(probe_session).test_telegram_authorization(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
                project_id=None,
            )

    first = asyncio.create_task(probe())
    await asyncio.wait_for(first_native.get_me_started.wait(), timeout=2)
    second = asyncio.create_task(probe())
    await asyncio.sleep(0)
    assert not second.done()
    assert second_native.requests == []
    first_native.release_get_me.set()
    first_result, second_result = await asyncio.wait_for(asyncio.gather(first, second), timeout=3)

    assert first_result.data.ok is True
    assert second_result.data.ok is True
    assert first_native.closed and second_native.closed
    assert runtime.active_generation(account_ref=credential_ref) is None
    assert (
        AuthRepository(session).telegram_authorization_status(credential_ref=credential_ref).status
        == "disconnected"
    )


@pytest.mark.asyncio
async def test_offline_account_test_cannot_retire_a_racing_explicit_connect(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    signed_in = _Runtime(states=[{"@type": "authorizationStateReady"}])
    signed_in.me["id"] = 987654
    await AuthRepository(session).start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=signed_in,
        settings=settings,
        sign_in_only=True,
    )
    engine = cast(Engine, session.get_bind())
    probe_native = _BlockingIdentitySession()
    connected_native = _IdentitySession()
    _queue_ready(probe_native)
    _queue_ready(connected_native)
    runtime = _native_authorization_service(engine, probe_native, connected_native)

    async def probe() -> Any:
        with Session(engine) as probe_session:
            return await AuthRepository(probe_session).test_telegram_authorization(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
                project_id=None,
            )

    async def connect() -> Any:
        with Session(engine) as connect_session:
            return await AuthRepository(connect_session).connect_telegram_session(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
            )

    tested = asyncio.create_task(probe())
    await asyncio.wait_for(probe_native.get_me_started.wait(), timeout=2)
    connected = asyncio.create_task(connect())
    await asyncio.sleep(0)
    assert not connected.done()
    assert connected_native.requests == []
    probe_native.release_get_me.set()
    test_result, connected_result = await asyncio.wait_for(
        asyncio.gather(tested, connected), timeout=3
    )

    assert test_result.data.ok is True
    assert connected_result.data.status == "connected"
    assert probe_native.closed
    assert not connected_native.closed
    current = AuthRepository(session).telegram_authorization_status(credential_ref=credential_ref)
    assert runtime.active_generation(account_ref=credential_ref) == current.generation
    assert (
        AuthRepository(session).get_telegram_session_status(
            credential_ref=credential_ref, runtime=runtime
        )["connected"]
        is True
    )
    await runtime.close(account_ref=credential_ref, generation=current.generation)


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_failure", ["expired", "identity"])
async def test_failed_probe_cannot_overwrite_a_queued_explicit_connect(
    session: Session,
    settings: Settings,
    probe_failure: str,
) -> None:
    credential_ref = _user_account(session)
    signed_in = _Runtime(states=[{"@type": "authorizationStateReady"}])
    signed_in.me["id"] = 987654
    await AuthRepository(session).start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=signed_in,
        settings=settings,
        sign_in_only=True,
    )
    engine = cast(Engine, session.get_bind())
    if probe_failure == "expired":
        probe_native = _BlockingAuthorizationSession()
        probe_native.updates.put_nowait(
            {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
            }
        )
        probe_started = probe_native.authorization_started
        release_probe = probe_native.release_authorization
    else:
        probe_native = _BlockingIdentitySession()
        probe_native.me_id = 999999
        _queue_ready(probe_native)
        probe_started = probe_native.get_me_started
        release_probe = probe_native.release_get_me
    connected_native = _IdentitySession()
    _queue_ready(connected_native)
    runtime = _native_authorization_service(engine, probe_native, connected_native)

    async def probe() -> Any:
        with Session(engine) as probe_session:
            return await AuthRepository(probe_session).test_telegram_authorization(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
                project_id=None,
            )

    async def connect() -> Any:
        with Session(engine) as connect_session:
            return await AuthRepository(connect_session).connect_telegram_session(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
            )

    tested = asyncio.create_task(probe())
    await asyncio.wait_for(probe_started.wait(), timeout=2)
    connected = asyncio.create_task(connect())
    await asyncio.sleep(0)
    assert not connected.done()
    release_probe.set()
    test_result, connect_result = await asyncio.wait_for(
        asyncio.gather(tested, connected), timeout=3
    )

    assert test_result.data.ok is False
    assert "reauthorize" in (test_result.data.next_action or "").lower()
    assert connect_result.data.status == "connected"
    assert probe_native.closed
    assert not connected_native.closed
    with Session(engine) as verify_session:
        repo = AuthRepository(verify_session)
        current = repo.telegram_authorization_status(credential_ref=credential_ref)
        assert current.status == "connected"
        assert (
            repo.get_telegram_session_status(credential_ref=credential_ref, runtime=runtime)[
                "desired_connected"
            ]
            is True
        )
    assert runtime.active_generation(account_ref=credential_ref) == current.generation
    await runtime.close(account_ref=credential_ref, generation=current.generation)


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["update", "revoke"])
async def test_offline_test_finishes_before_account_change_or_revoke(
    session: Session,
    settings: Settings,
    transition: str,
) -> None:
    credential_ref = _user_account(session)
    signed_in = _Runtime(states=[{"@type": "authorizationStateReady"}])
    signed_in.me["id"] = 987654
    await AuthRepository(session).start_telegram_authorization(
        credential_ref=credential_ref,
        runtime=signed_in,
        settings=settings,
        sign_in_only=True,
    )
    engine = cast(Engine, session.get_bind())
    native = _BlockingIdentitySession()
    _queue_ready(native)
    runtime = _native_authorization_service(engine, native)

    async def probe() -> Any:
        with Session(engine) as probe_session:
            return await AuthRepository(probe_session).test_telegram_authorization(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
                project_id=None,
            )

    async def change_account() -> Any:
        with Session(engine) as change_session:
            ctx = MCPContext(
                session=change_session,
                request_id=f"telegram-test-vs-{transition}",
                extras={
                    "surface": "rest",
                    "trusted_local_admin": True,
                    "telegram_runtime": runtime,
                    "settings": settings,
                },
            )
            if transition == "update":
                return await account_update(
                    AccountUpdateInput(
                        credential_ref=credential_ref,
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
            return await account_revoke(
                AccountRevokeInput(credential_ref=credential_ref),
                ctx,
                ProgressEmitter(None, None),
            )

    testing = asyncio.create_task(probe())
    await asyncio.wait_for(native.get_me_started.wait(), timeout=2)
    changing = asyncio.create_task(change_account())
    await asyncio.sleep(0)
    assert not changing.done()
    native.release_get_me.set()
    tested, changed = await asyncio.wait_for(asyncio.gather(testing, changing), timeout=3)
    assert tested.data.ok is True
    assert native.closed
    assert runtime.active_generation(account_ref=credential_ref) is None
    with Session(engine) as verify_session:
        account = AuthRepository(verify_session).get_account(credential_ref=credential_ref)
        if transition == "update":
            assert changed.data.status == "disconnected"
            assert account.last_test is None
            assert account.last_tested_at is None
        else:
            assert changed.data.status == "revoked"
            assert account.status == "revoked"


@pytest.mark.asyncio
async def test_account_test_without_saved_sign_in_does_not_prompt_or_connect(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    runtime = _Runtime(states=[])
    tested = await AuthRepository(session).test_telegram_authorization(
        credential_ref=credential_ref, runtime=runtime, settings=settings, project_id=None
    )

    assert tested.data.ok is False
    assert tested.data.status == "pending"
    assert runtime.configurations == []
    assert runtime.requests == []


@pytest.mark.asyncio
async def test_first_user_connect_continues_local_challenge_and_stays_connected(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    repo = AuthRepository(session)
    runtime = _Runtime(
        states=[
            {"@type": "authorizationStateWaitPhoneNumber"},
            {"@type": "authorizationStateWaitCode"},
            {"@type": "authorizationStateWaitPassword"},
            {"@type": "authorizationStateReady"},
        ]
    )

    first_connect = await repo.connect_telegram_session(
        credential_ref=credential_ref,
        runtime=runtime,
        settings=settings,
    )
    assert first_connect.data.status == "challenge"
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref, runtime=runtime)["status"]
        == "authorization_required"
    )
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref)["desired_connected"] is True
    )
    local_challenge = repo.telegram_authorization_status(credential_ref=credential_ref)
    assert local_challenge.challenge is not None
    assert local_challenge.challenge.kind == "phone_number"

    for answer, next_kind in (
        ({"phone_number": "+15551234567"}, "code"),
        ({"code": "12345"}, "password"),
    ):
        response = await repo.submit_telegram_authorization(
            credential_ref=credential_ref,
            generation=1,
            answer=answer,
            runtime=runtime,
        )
        assert response.data.challenge is not None
        assert response.data.challenge.kind == next_kind
    completed = await repo.submit_telegram_authorization(
        credential_ref=credential_ref,
        generation=1,
        answer={"password": "private-password"},
        runtime=runtime,
    )

    assert completed.data.status == "connected"
    assert runtime.closed == []
    assert (
        repo.get_telegram_session_status(credential_ref=credential_ref, runtime=runtime)[
            "connected"
        ]
        is True
    )
    assert "private-password" not in json.dumps(_credential(session, credential_ref).config_json)


@pytest.mark.asyncio
async def test_qr_ready_after_start_retires_sign_in_session_without_restore(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    engine = cast(Engine, session.get_bind())
    native = _IdentitySession()
    runtime = _native_authorization_service(engine, native)
    started_task = asyncio.create_task(
        AuthRepository(session).start_telegram_authorization(
            credential_ref=credential_ref,
            runtime=runtime,
            settings=settings,
            authorization_mode="qr",
            sign_in_only=True,
        )
    )
    await asyncio.sleep(0)
    await native.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
        }
    )
    await _wait_for_native_request(native, 1)
    assert native.requests[0][0]["@type"] == "requestQrCodeAuthentication"
    await native.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {
                "@type": "authorizationStateWaitOtherDeviceConfirmation",
                "link": "tg://login?token=private",
            },
        }
    )
    started = await started_task
    assert started.data.challenge is not None
    assert started.data.challenge.kind == "qr"
    await native.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )
    await asyncio.wait_for(native.closed_event.wait(), timeout=2)

    assert (
        AuthRepository(session).telegram_authorization_status(credential_ref=credential_ref).status
        == "disconnected"
    )
    assert (
        AuthRepository(session).get_telegram_session_status(credential_ref=credential_ref)[
            "desired_connected"
        ]
        is False
    )
    restoring = _Runtime(states=[])
    await restore_telegram_accounts(engine, settings, restoring)
    assert restoring.configurations == []


@pytest.mark.asyncio
async def test_native_phone_code_password_ready_returns_saved_disconnected_success(
    session: Session,
    settings: Settings,
) -> None:
    credential_ref = _user_account(session)
    native = _IdentitySession()
    runtime = _native_authorization_service(cast(Engine, session.get_bind()), native)
    started_task = asyncio.create_task(
        AuthRepository(session).start_telegram_authorization(
            credential_ref=credential_ref,
            runtime=runtime,
            settings=settings,
            sign_in_only=True,
        )
    )
    await asyncio.sleep(0)
    await native.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
        }
    )
    started = await started_task
    assert started.data.challenge is not None
    assert started.data.challenge.kind == "phone_number"

    for index, (answer, next_state, next_kind) in enumerate(
        (
            ({"phone_number": "+15551234567"}, "authorizationStateWaitCode", "code"),
            ({"code": "12345"}, "authorizationStateWaitPassword", "password"),
        ),
        start=1,
    ):
        submission = asyncio.create_task(
            AuthRepository(session).submit_telegram_authorization(
                credential_ref=credential_ref,
                generation=1,
                answer=answer,
                runtime=runtime,
            )
        )
        await _wait_for_native_request(native, index)
        await native.updates.put(
            {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": next_state},
            }
        )
        result = await submission
        assert result.data.challenge is not None
        assert result.data.challenge.kind == next_kind

    final_submission = asyncio.create_task(
        AuthRepository(session).submit_telegram_authorization(
            credential_ref=credential_ref,
            generation=1,
            answer={"password": "private-password"},
            runtime=runtime,
        )
    )
    await _wait_for_native_request(native, 3)
    await native.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )
    completed = await final_submission
    await asyncio.wait_for(native.closed_event.wait(), timeout=2)

    assert completed.data.status == "disconnected"
    assert (
        AuthRepository(session).get_telegram_session_status(credential_ref=credential_ref)[
            "desired_connected"
        ]
        is False
    )
    assert "private-password" not in json.dumps(_credential(session, credential_ref).config_json)
