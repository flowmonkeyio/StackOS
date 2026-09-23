from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from stackos.integrations.telegram_tdlib.service import (
    TelegramTdlibService,
    TelegramTdlibServiceError,
)
from stackos.integrations.telegram_tdlib.sessions import (
    TelegramApplicationCredentials,
    TelegramTdlibSessionConfig,
    TelegramTdlibSessionReceipt,
    TelegramTdlibSessionRegistration,
)


class _Session:
    def __init__(self) -> None:
        self.updates: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.requests: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.closed = False

    async def next_update(self) -> dict[str, Any]:
        return await self.updates.get()

    async def _request(self, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.requests.append((request, kwargs))
        return {"@type": "ok"}

    async def close(self, *, timeout_seconds: float = 10.0) -> None:
        self.closed = True


class _Registry:
    def __init__(self, sessions: deque[_Session]) -> None:
        self._sessions = sessions
        self.by_account: dict[str, _Session] = {}
        self.closed: list[str] = []

    async def configure(
        self,
        *,
        account_ref: str,
        config: TelegramTdlibSessionConfig,
        startup_timeout_seconds: float,
    ) -> TelegramTdlibSessionRegistration:
        session = self._sessions.popleft()
        self.by_account[account_ref] = session
        return TelegramTdlibSessionRegistration(
            account_ref=account_ref,
            session=session,  # type: ignore[arg-type]
            receipt=TelegramTdlibSessionReceipt(
                account_kind=config.account_kind, proxy_id=None, proxy_enabled=False
            ),
        )

    async def get(self, *, account_ref: str) -> _Session:
        return self.by_account[account_ref]

    async def close(self, *, account_ref: str, timeout_seconds: float = 10.0) -> None:
        self.closed.append(account_ref)
        session = self.by_account.pop(account_ref, None)
        if session is not None:
            await session.close(timeout_seconds=timeout_seconds)

    async def close_all(self, *, timeout_seconds: float = 10.0) -> None:
        for account_ref in tuple(self.by_account):
            await self.close(account_ref=account_ref, timeout_seconds=timeout_seconds)


def _config(tmp_path: Path) -> TelegramTdlibSessionConfig:
    return TelegramTdlibSessionConfig(
        account_kind="user",
        application=TelegramApplicationCredentials(api_id=12345, api_hash="application-secret"),
        database_directory=tmp_path / "database",
        files_directory=tmp_path / "files",
        database_encryption_key="storage-secret",
    )


@pytest.mark.asyncio
async def test_service_has_one_receiver_and_routes_auth_receipts_and_ingress(
    tmp_path: Path,
) -> None:
    session = _Session()
    authorization_events: list[tuple[str, int, str]] = []
    message_events: list[tuple[str, int, int]] = []
    ingress_events: list[tuple[str, int, str]] = []

    async def on_authorization(account_ref: str, generation: int, state: dict[str, Any]) -> None:
        authorization_events.append((account_ref, generation, state["@type"]))

    async def on_message(account_ref: str, generation: int, update: dict[str, Any]) -> None:
        message_events.append((account_ref, generation, update["old_message_id"]))

    async def on_ingress(account_ref: str, generation: int, update: dict[str, Any]) -> None:
        ingress_events.append((account_ref, generation, update["@type"]))

    service = TelegramTdlibService(
        session_registry=_Registry(deque([session])),
        authorization_sink=on_authorization,
        message_receipt_sink=on_message,
        ingress_sink=on_ingress,
    )
    configured = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=4, config=_config(tmp_path))
    )
    await asyncio.sleep(0)
    await session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
        }
    )

    state = await configured
    await session.updates.put(
        {
            "@type": "updateMessageSendSucceeded",
            "old_message_id": 19,
            "message": {"id": 20, "chat_id": 44},
        }
    )
    await session.updates.put({"@type": "updateNewMessage", "message": {"id": 3}})
    await asyncio.sleep(0)

    assert state["@type"] == "authorizationStateWaitPhoneNumber"
    assert authorization_events == [("cred_telegram", 4, "authorizationStateWaitPhoneNumber")]
    assert message_events == [("cred_telegram", 4, 19)]
    assert ingress_events == [("cred_telegram", 4, "updateNewMessage")]


@pytest.mark.asyncio
async def test_concurrent_same_generation_configure_joins_one_native_session(
    tmp_path: Path,
) -> None:
    session = _Session()
    registry = _Registry(deque([session]))
    service = TelegramTdlibService(session_registry=registry)
    first = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=1, config=_config(tmp_path))
    )
    second = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=1, config=_config(tmp_path))
    )
    await asyncio.sleep(0)
    await session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )

    states = await asyncio.gather(first, second)
    assert states == [{"@type": "authorizationStateReady"}] * 2
    assert registry.by_account == {"cred_telegram": session}
    assert registry.closed == []
    await service.close_all()


@pytest.mark.asyncio
async def test_account_transition_lease_serializes_native_configure_and_close(
    tmp_path: Path,
) -> None:
    session = _Session()
    registry = _Registry(deque([session]))
    service = TelegramTdlibService(session_registry=registry)

    async with service.account_transition("cred_telegram"):
        configuring = asyncio.create_task(
            service.configure(account_ref="cred_telegram", generation=1, config=_config(tmp_path))
        )
        await asyncio.sleep(0)
        assert service.active_generation(account_ref="cred_telegram") is None
        assert registry.by_account == {}
    await asyncio.sleep(0)
    await session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )
    assert (await configuring)["@type"] == "authorizationStateReady"

    async with (
        service.account_transition("cred_telegram"),
        service.account_transition("cred_telegram"),
    ):
        closing = asyncio.create_task(service.close(account_ref="cred_telegram", generation=1))
        await asyncio.sleep(0)
        assert service.active_generation(account_ref="cred_telegram") == 1
        assert not closing.done()
    await closing
    assert session.closed
    assert service.active_generation(account_ref="cred_telegram") is None


@pytest.mark.asyncio
async def test_close_while_configure_awaits_auth_state_retires_receiver(
    tmp_path: Path,
) -> None:
    session = _Session()
    registry = _Registry(deque([session]))
    service = TelegramTdlibService(session_registry=registry)
    configuring = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=1, config=_config(tmp_path))
    )
    await asyncio.sleep(0)

    await service.close(account_ref="cred_telegram", generation=1)

    with pytest.raises(TelegramTdlibServiceError, match="retired"):
        await configuring
    assert session.closed
    assert registry.by_account == {}


@pytest.mark.asyncio
async def test_service_wait_message_handles_live_and_previously_received_final_receipts(
    tmp_path: Path,
) -> None:
    session = _Session()
    service = TelegramTdlibService(session_registry=_Registry(deque([session])))
    configured = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=4, config=_config(tmp_path))
    )
    await asyncio.sleep(0)
    await session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )
    await configured

    waiting = asyncio.create_task(
        service.wait_message(
            account_ref="cred_telegram",
            generation=4,
            chat_id=44,
            temporary_message_id=-19,
        )
    )
    await asyncio.sleep(0)
    update = {
        "@type": "updateMessageSendSucceeded",
        "old_message_id": -19,
        "message": {"id": 20, "chat_id": 44},
    }
    await session.updates.put(update)
    assert await waiting == update

    cached = {
        "@type": "updateMessageSendFailed",
        "old_message_id": -20,
        "message": {"id": -20, "chat_id": 44},
        "error": {"code": 400},
    }
    await session.updates.put(cached)
    await asyncio.sleep(0)
    assert (
        await service.wait_message(
            account_ref="cred_telegram",
            generation=4,
            chat_id=44,
            temporary_message_id=-20,
        )
        == cached
    )


@pytest.mark.asyncio
async def test_service_forwards_correlated_requests_and_awaits_next_auth_state(
    tmp_path: Path,
) -> None:
    session = _Session()
    service = TelegramTdlibService(session_registry=_Registry(deque([session])))
    configured = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=7, config=_config(tmp_path))
    )
    await asyncio.sleep(0)
    await session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
        }
    )
    await configured

    requested = asyncio.create_task(
        service.request_authorization(
            account_ref="cred_telegram",
            generation=7,
            request={"@type": "setAuthenticationPhoneNumber", "phone_number": "+15551234567"},
            correlation_id="auth-generation-7",
        )
    )
    await asyncio.sleep(0)
    await session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitCode"},
        }
    )

    state = await requested
    response = await service.request(
        account_ref="cred_telegram",
        generation=7,
        request={"@type": "getAuthorizationState"},
        correlation_id="delivery-attempt-11",
    )

    assert state["@type"] == "authorizationStateWaitCode"
    assert response == {"@type": "ok"}
    assert session.requests == [
        (
            {"@type": "setAuthenticationPhoneNumber", "phone_number": "+15551234567"},
            {"correlation_id": "auth-generation-7"},
        ),
        (
            {"@type": "getAuthorizationState"},
            {"correlation_id": "delivery-attempt-11"},
        ),
    ]


@pytest.mark.asyncio
async def test_service_drops_old_generation_after_reconfigure_and_close(tmp_path: Path) -> None:
    old_session, current_session = _Session(), _Session()
    authorization_events: list[tuple[int, str]] = []

    async def on_authorization(_account_ref: str, generation: int, state: dict[str, Any]) -> None:
        authorization_events.append((generation, state["@type"]))

    registry = _Registry(deque([old_session, current_session]))
    service = TelegramTdlibService(
        session_registry=registry,
        authorization_sink=on_authorization,
    )
    first = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=1, config=_config(tmp_path))
    )
    await asyncio.sleep(0)
    await old_session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitPhoneNumber"},
        }
    )
    await first
    second = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=2, config=_config(tmp_path))
    )
    await asyncio.sleep(0)
    await current_session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateWaitCode"},
        }
    )
    await second
    await old_session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )
    await service.close(account_ref="cred_telegram", generation=2)

    assert authorization_events == [
        (1, "authorizationStateWaitPhoneNumber"),
        (2, "authorizationStateWaitCode"),
    ]
    assert old_session.closed is True
    assert current_session.closed is True
    assert registry.closed == ["cred_telegram", "cred_telegram"]


@pytest.mark.asyncio
async def test_authorization_sink_can_retire_its_own_receiver_without_self_await(
    tmp_path: Path,
) -> None:
    session = _Session()
    registry = _Registry(deque([session]))
    service: TelegramTdlibService

    async def reject_ready(_account_ref: str, generation: int, _state: dict[str, Any]) -> None:
        await service.close(account_ref="cred_telegram", generation=generation)

    service = TelegramTdlibService(session_registry=registry, authorization_sink=reject_ready)
    configured = asyncio.create_task(
        service.configure(account_ref="cred_telegram", generation=1, config=_config(tmp_path))
    )
    await asyncio.sleep(0)
    await session.updates.put(
        {
            "@type": "updateAuthorizationState",
            "authorization_state": {"@type": "authorizationStateReady"},
        }
    )

    with pytest.raises(TelegramTdlibServiceError, match="retired"):
        await configured
    await asyncio.sleep(0)
    assert registry.closed == ["cred_telegram"]
    assert session.closed is True


@pytest.mark.asyncio
async def test_service_shutdown_closes_all_managed_receivers_before_engine_teardown(
    tmp_path: Path,
) -> None:
    first_session, second_session = _Session(), _Session()
    registry = _Registry(deque([first_session, second_session]))
    service = TelegramTdlibService(session_registry=registry)

    for account_ref, session in (
        ("cred_telegram_one", first_session),
        ("cred_telegram_two", second_session),
    ):
        configured = asyncio.create_task(
            service.configure(account_ref=account_ref, generation=1, config=_config(tmp_path))
        )
        await asyncio.sleep(0)
        await session.updates.put(
            {
                "@type": "updateAuthorizationState",
                "authorization_state": {"@type": "authorizationStateReady"},
            }
        )
        await configured

    await service.close_all()

    assert registry.closed == ["cred_telegram_one", "cred_telegram_two"]
    assert first_session.closed is True
    assert second_session.closed is True
