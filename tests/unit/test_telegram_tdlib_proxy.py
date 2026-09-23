from __future__ import annotations

import pytest

from stackos.integrations.telegram_tdlib.proxy import (
    TelegramProxyConfig,
    TelegramProxyController,
    TelegramProxyValidationError,
)


class _Client:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.proxies: list[dict[str, object]] = []

    async def _request(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        request_type = request["@type"]
        if request_type == "getProxies":
            return {"@type": "addedProxies", "proxies": self.proxies}
        if request_type in {"addProxy", "editProxy"}:
            response = {
                "@type": "addedProxy",
                "id": 41,
                "is_enabled": request["enable"],
                "last_used_date": 0,
                "comment": request["comment"],
                "proxy": request["proxy"],
            }
            self.proxies = [response]
            return response
        if request_type == "pingProxy":
            return {"@type": "seconds", "seconds": 0.25}
        if request_type == "disableProxy":
            for proxy in self.proxies:
                proxy["is_enabled"] = False
            return {"@type": "ok"}
        if request_type == "removeProxy":
            self.proxies = [proxy for proxy in self.proxies if proxy["id"] != request["proxy_id"]]
            return {"@type": "ok"}
        return {"@type": "ok"}


def test_proxy_config_serializes_each_official_tdlib_type_without_secret_repr() -> None:
    socks = TelegramProxyConfig(
        kind="socks5", host="127.0.0.1", port=1080, username="alice", password="secret"
    )
    http = TelegramProxyConfig(kind="http", host="proxy.example", port=8080, http_only=True)
    mtproto = TelegramProxyConfig(
        kind="mtproto", host="proxy.example", port=443, mtproto_secret="a1" * 16
    )

    assert socks.to_tdjson() == {
        "@type": "proxy",
        "server": "127.0.0.1",
        "port": 1080,
        "type": {"@type": "proxyTypeSocks5", "username": "alice", "password": "secret"},
    }
    assert http.to_tdjson()["type"] == {
        "@type": "proxyTypeHttp",
        "username": "",
        "password": "",
        "http_only": True,
    }
    assert mtproto.to_tdjson()["type"] == {"@type": "proxyTypeMtproto", "secret": "a1" * 16}
    assert "secret" not in repr(socks)
    assert "a1" * 16 not in repr(mtproto)
    assert socks.to_safe_dict() == {"kind": "socks5", "host": "127.0.0.1", "port": 1080}


def test_proxy_config_rejects_values_tdlib_cannot_use() -> None:
    with pytest.raises(TelegramProxyValidationError, match="port"):
        TelegramProxyConfig(kind="socks5", host="proxy.example", port=0)
    with pytest.raises(TelegramProxyValidationError, match="hexadecimal"):
        TelegramProxyConfig(kind="mtproto", host="proxy.example", port=443, mtproto_secret="wat?")
    with pytest.raises(TelegramProxyValidationError, match="hexadecimal"):
        TelegramProxyConfig(
            kind="mtproto", host="proxy.example", port=443, mtproto_secret="a1 " * 16
        )
    with pytest.raises(TelegramProxyValidationError, match="TDLib-supported"):
        TelegramProxyConfig(kind="mtproto", host="proxy.example", port=443, mtproto_secret="a1b2")
    TelegramProxyConfig(
        kind="mtproto", host="proxy.example", port=443, mtproto_secret="dd" + "00" * 16
    )
    TelegramProxyConfig(
        kind="mtproto", host="proxy.example", port=443, mtproto_secret="ee" + "00" * 16 + "61"
    )
    with pytest.raises(TelegramProxyValidationError, match="http_only"):
        TelegramProxyConfig(kind="socks5", host="proxy.example", port=1080, http_only=True)


@pytest.mark.parametrize("host", ["http:", ":", "[broken]", "proxy:1080", "bad..host"])
def test_proxy_host_rejects_malformed_endpoints_before_native_start(host: str) -> None:
    with pytest.raises(TelegramProxyValidationError, match="host"):
        TelegramProxyConfig(kind="socks5", host=host, port=1080)


def test_proxy_host_accepts_bare_ipv6() -> None:
    config = TelegramProxyConfig(kind="socks5", host="2001:db8::1", port=1080)
    assert config.to_tdjson()["server"] == "2001:db8::1"


@pytest.mark.asyncio
async def test_proxy_reconciliation_is_typed_and_prepares_one_enabled_proxy() -> None:
    client = _Client()
    controller = TelegramProxyController(client)
    config = TelegramProxyConfig(kind="http", host="proxy.example", port=8080)

    receipt = await controller.reconcile(config, persisted_proxy_id=None)

    assert receipt.proxy_id == 41
    assert receipt.enabled is True
    assert [request["@type"] for request in client.requests] == [
        "getProxies",
        "addProxy",
        "getProxies",
    ]
    assert client.requests[1]["enable"] is True


@pytest.mark.asyncio
async def test_direct_reconciliation_disables_an_unexpected_active_native_proxy() -> None:
    client = _Client()
    client.proxies = [
        {
            "@type": "addedProxy",
            "id": 64,
            "is_enabled": True,
            "last_used_date": 0,
            "proxy": {
                "@type": "proxy",
                "server": "stale.example",
                "port": 443,
                "type": {"@type": "proxyTypeMtproto", "secret": "never-returned"},
            },
        }
    ]

    receipt = await TelegramProxyController(client).reconcile(None, persisted_proxy_id=None)

    assert receipt.proxy_id is None
    assert receipt.enabled is False
    assert [request["@type"] for request in client.requests] == [
        "getProxies",
        "disableProxy",
        "getProxies",
    ]
    assert client.proxies[0]["is_enabled"] is False
