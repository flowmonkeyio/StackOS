from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

from stackos.integrations.telegram_tdlib.native import (
    TelegramTdlibClient,
    TelegramTdlibNativeError,
    TelegramTdlibRequestError,
    _raise_if_error,
)


class _FakeAbi:
    def __init__(self) -> None:
        self.created = 0
        self.destroyed: list[int] = []
        self.sent: list[tuple[int, bytes]] = []
        self.received: deque[bytes | None] = deque()

    def create_client(self) -> int:
        self.created += 1
        return self.created

    def send(self, handle: int, request: bytes) -> None:
        self.sent.append((handle, request))

    def receive(self, _handle: int, _timeout_seconds: float) -> bytes | None:
        return self.received.popleft() if self.received else None

    def destroy_client(self, handle: int) -> None:
        self.destroyed.append(handle)


@pytest.mark.asyncio
async def test_client_routes_responses_by_extra_and_preserves_update_order() -> None:
    native = _FakeAbi()
    client = TelegramTdlibClient(native, receive_timeout_seconds=0.001)
    await client.start()
    request = asyncio.create_task(
        client._request({"@type": "getProxies"}, correlation_id="persisted-attempt-1")
    )
    await asyncio.sleep(0)
    extra = json.loads(native.sent[-1][1])["@extra"]
    assert extra == "persisted-attempt-1"
    with pytest.raises(TelegramTdlibNativeError, match="correlation is already in use"):
        await client._request({"@type": "getProxies"}, correlation_id="persisted-attempt-1")
    native.received.extend(
        [
            json.dumps({"@type": "updateOption", "name": "one"}).encode(),
            json.dumps({"@type": "ok", "@extra": extra}).encode(),
            json.dumps({"@type": "updateOption", "name": "two"}).encode(),
        ]
    )

    assert await request == {"@type": "ok", "@extra": extra}
    assert [await client.next_update(), await client.next_update()] == [
        {"@type": "updateOption", "name": "one"},
        {"@type": "updateOption", "name": "two"},
    ]

    close = asyncio.create_task(client.close(timeout_seconds=0.1))
    await asyncio.sleep(0)
    close_extra = json.loads(native.sent[-1][1])["@extra"]
    native.received.extend(
        [
            json.dumps({"@type": "ok", "@extra": close_extra}).encode(),
            json.dumps(
                {
                    "@type": "updateAuthorizationState",
                    "authorization_state": {"@type": "authorizationStateClosed"},
                }
            ).encode(),
        ]
    )
    await close
    assert native.destroyed == [1]


@pytest.mark.asyncio
async def test_close_waits_for_closed_authorization_update_before_destroying() -> None:
    native = _FakeAbi()
    client = TelegramTdlibClient(native, receive_timeout_seconds=0.001)
    await client.start()
    close = asyncio.create_task(client.close(timeout_seconds=0.1))
    await asyncio.sleep(0)
    extra = json.loads(native.sent[-1][1])["@extra"]
    native.received.extend(
        [
            json.dumps({"@type": "ok", "@extra": extra}).encode(),
            json.dumps(
                {
                    "@type": "updateAuthorizationState",
                    "authorization_state": {"@type": "authorizationStateClosed"},
                }
            ).encode(),
        ]
    )

    await close

    assert native.destroyed == [1]
    assert (await client.next_update())["authorization_state"][
        "@type"
    ] == "authorizationStateClosed"


@pytest.mark.parametrize(
    ("message", "expected_name", "expected_retry"),
    [
        ("FLOOD_WAIT_42", "FLOOD_WAIT", 42),
        ("SLOWMODE_WAIT_9", "SLOWMODE_WAIT", 9),
        ("Too Many Requests: retry after 17", "FLOOD_WAIT", 17),
        ("PEER_FLOOD", "PEER_FLOOD", None),
        ("PHONE_CODE_INVALID", "PHONE_CODE_INVALID", None),
    ],
)
def test_request_error_keeps_only_whitelisted_safe_retry_metadata(
    message: str, expected_name: str, expected_retry: int | None
) -> None:
    with pytest.raises(TelegramTdlibRequestError) as caught:
        _raise_if_error({"@type": "error", "code": 429, "message": message}, phase="request")

    error = caught.value
    assert error.error_name == expected_name
    assert error.retry_after_seconds == expected_retry
    assert "secret-value" not in str(error)
    assert "secret-value" not in repr(error)
    assert "secret-value" not in error.__dict__.values()


def test_request_error_drops_unrecognized_provider_text() -> None:
    secret = "unexpected provider text carrying secret-value"
    with pytest.raises(TelegramTdlibRequestError) as caught:
        _raise_if_error({"@type": "error", "code": 500, "message": secret}, phase="request")

    error = caught.value
    assert error.error_name is None
    assert error.retry_after_seconds is None
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in error.__dict__.values()
