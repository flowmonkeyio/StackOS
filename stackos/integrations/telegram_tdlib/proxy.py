"""Typed TDLib proxy setup for a single bot or user-account session.

Credentials in proxy configuration stay in the encrypted account record owned by
the daemon.  This module serializes them to TDLib only at the native boundary
and never includes them in reprs or safe receipts.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


class TelegramProxyValidationError(ValueError):
    """A proxy cannot be represented by TDLib's documented proxy types."""


@dataclass(frozen=True)
class TelegramProxyConfig:
    """An optional account proxy, with secret fields excluded from repr output."""

    kind: Literal["socks5", "http", "mtproto"]
    host: str
    port: int
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    http_only: bool = False
    mtproto_secret: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host or self.host != self.host.strip():
            raise TelegramProxyValidationError(
                "proxy host must be a non-empty host name or IP address"
            )
        _validate_host(self.host)
        if (
            not isinstance(self.port, int)
            or isinstance(self.port, bool)
            or not 1 <= self.port <= 65535
        ):
            raise TelegramProxyValidationError("proxy port must be between 1 and 65535")
        if self.kind not in {"socks5", "http", "mtproto"}:
            raise TelegramProxyValidationError("proxy kind must be socks5, http, or mtproto")
        if self.kind == "socks5" and self.http_only:
            raise TelegramProxyValidationError("http_only is only supported for HTTP proxies")
        if self.kind == "mtproto":
            if self.username or self.password or self.http_only:
                raise TelegramProxyValidationError(
                    "MTProto proxies don't accept username, password, or http_only"
                )
            _validate_mtproto_secret(self.mtproto_secret)
        elif self.mtproto_secret:
            raise TelegramProxyValidationError("MTProto proxy secret requires an MTProto proxy")

    def to_tdjson(self) -> dict[str, Any]:
        if self.kind == "socks5":
            proxy_type: dict[str, Any] = {
                "@type": "proxyTypeSocks5",
                "username": self.username,
                "password": self.password,
            }
        elif self.kind == "http":
            proxy_type = {
                "@type": "proxyTypeHttp",
                "username": self.username,
                "password": self.password,
                "http_only": self.http_only,
            }
        else:
            proxy_type = {"@type": "proxyTypeMtproto", "secret": self.mtproto_secret}
        return {"@type": "proxy", "server": self.host, "port": self.port, "type": proxy_type}

    def to_safe_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "host": self.host, "port": self.port}


@dataclass(frozen=True)
class TelegramProxyReceipt:
    """A safe local-admin proxy status without authentication values."""

    proxy_id: int
    enabled: bool
    last_used_date: int
    endpoint: dict[str, Any]

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "proxy_id": self.proxy_id,
            "enabled": self.enabled,
            "last_used_date": self.last_used_date,
            "proxy": dict(self.endpoint),
        }


@dataclass(frozen=True)
class TelegramProxyBootstrapReceipt:
    """The managed proxy state applied before a session initializes TDLib."""

    proxy_id: int | None
    enabled: bool


class _TdlibRequester(Protocol):
    async def _request(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float = 30.0,
        correlation_id: str | None = None,
    ) -> dict[str, Any]: ...

    def _begin_request(
        self,
        request: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> Awaitable[dict[str, Any]]: ...


def _safe_endpoint(proxy: Any) -> dict[str, Any]:
    if not isinstance(proxy, Mapping):
        return {}
    endpoint: dict[str, Any] = {}
    server = proxy.get("server")
    port = proxy.get("port")
    proxy_type = proxy.get("type")
    if isinstance(server, str):
        endpoint["host"] = server
    if isinstance(port, int) and not isinstance(port, bool):
        endpoint["port"] = port
    if isinstance(proxy_type, Mapping):
        raw_kind = proxy_type.get("@type")
        kinds = {
            "proxyTypeSocks5": "socks5",
            "proxyTypeHttp": "http",
            "proxyTypeMtproto": "mtproto",
        }
        if raw_kind in kinds:
            endpoint["kind"] = kinds[raw_kind]
    return endpoint


def _receipt(response: Mapping[str, Any]) -> TelegramProxyReceipt:
    proxy_id = response.get("id")
    enabled = response.get("is_enabled")
    last_used_date = response.get("last_used_date")
    if (
        not isinstance(proxy_id, int)
        or isinstance(proxy_id, bool)
        or not isinstance(enabled, bool)
        or not isinstance(last_used_date, int)
        or isinstance(last_used_date, bool)
    ):
        raise TelegramProxyValidationError("TDLib returned an invalid proxy response")
    return TelegramProxyReceipt(
        proxy_id=proxy_id,
        enabled=enabled,
        last_used_date=last_used_date,
        endpoint=_safe_endpoint(response.get("proxy")),
    )


class TelegramProxyController:
    """Explicit TDLib proxy methods; it makes no rotation or routing decisions."""

    def __init__(self, client: _TdlibRequester) -> None:
        self._client = client

    async def list(self) -> list[TelegramProxyReceipt]:
        response = await self._client._request({"@type": "getProxies"})
        proxies = response.get("proxies")
        if response.get("@type") != "addedProxies" or not isinstance(proxies, list):
            raise TelegramProxyValidationError("TDLib returned an invalid proxy list")
        return [_receipt(proxy) for proxy in proxies if isinstance(proxy, Mapping)]

    async def add(
        self, config: TelegramProxyConfig, *, enable: bool, comment: str = ""
    ) -> TelegramProxyReceipt:
        response = await self._client._request(
            {
                "@type": "addProxy",
                "proxy": config.to_tdjson(),
                "enable": enable,
                "comment": comment,
            }
        )
        if response.get("@type") != "addedProxy":
            raise TelegramProxyValidationError("TDLib returned an invalid addProxy response")
        return _receipt(response)

    async def edit(
        self, proxy_id: int, config: TelegramProxyConfig, *, enable: bool, comment: str = ""
    ) -> TelegramProxyReceipt:
        response = await self._client._request(
            {
                "@type": "editProxy",
                "proxy_id": _proxy_id(proxy_id),
                "proxy": config.to_tdjson(),
                "enable": enable,
                "comment": comment,
            }
        )
        if response.get("@type") != "addedProxy":
            raise TelegramProxyValidationError("TDLib returned an invalid editProxy response")
        return _receipt(response)

    async def enable(self, proxy_id: int) -> None:
        await self._ok({"@type": "enableProxy", "proxy_id": _proxy_id(proxy_id)})

    async def disable(self) -> None:
        await self._ok({"@type": "disableProxy"})

    async def remove(self, proxy_id: int) -> None:
        await self._ok({"@type": "removeProxy", "proxy_id": _proxy_id(proxy_id)})

    async def ping(self, config: TelegramProxyConfig) -> float:
        response = await self._client._request({"@type": "pingProxy", "proxy": config.to_tdjson()})
        seconds = response.get("seconds")
        if response.get("@type") != "seconds" or not isinstance(seconds, (int, float)):
            raise TelegramProxyValidationError("TDLib returned an invalid pingProxy response")
        return float(seconds)

    async def reconcile(
        self, config: TelegramProxyConfig | None, *, persisted_proxy_id: int | None
    ) -> TelegramProxyBootstrapReceipt:
        """Make the account configuration authoritative and verify the final state."""
        return await self.reconcile_from(
            await self.list(), config, persisted_proxy_id=persisted_proxy_id
        )

    async def reconcile_from(
        self,
        existing: Sequence[TelegramProxyReceipt],
        config: TelegramProxyConfig | None,
        *,
        persisted_proxy_id: int | None,
    ) -> TelegramProxyBootstrapReceipt:
        """Reconcile from a pre-parameter snapshot while the session network is paused."""
        known_ids = {receipt.proxy_id for receipt in existing}
        if config is None:
            if any(receipt.enabled for receipt in existing):
                await self.disable()
            if persisted_proxy_id is not None and persisted_proxy_id in known_ids:
                await self.remove(persisted_proxy_id)
            final = await self.list()
            if any(receipt.enabled for receipt in final):
                raise TelegramProxyValidationError(
                    "TDLib retained an enabled proxy for a direct account"
                )
            return TelegramProxyBootstrapReceipt(proxy_id=None, enabled=False)
        if persisted_proxy_id is not None and persisted_proxy_id in known_ids:
            receipt = await self.edit(persisted_proxy_id, config, enable=True)
        else:
            receipt = await self.add(config, enable=True)
        final = await self.list()
        enabled = [candidate for candidate in final if candidate.enabled]
        if len(enabled) != 1 or enabled[0].proxy_id != receipt.proxy_id:
            raise TelegramProxyValidationError(
                "TDLib proxy state doesn't match the account configuration"
            )
        return TelegramProxyBootstrapReceipt(proxy_id=receipt.proxy_id, enabled=True)

    def begin_snapshot(self) -> _PendingProxySnapshot:
        """Queue a safe TDLib proxy snapshot before TDLib parameters are applied.

        TDLib's pinned implementation queues these preauthorization calls until
        its ConnectionCreator exists.  This method deliberately avoids a list
        read, because awaiting it before ``setTdlibParameters`` would deadlock.
        """
        return _PendingProxySnapshot(self._client._begin_request({"@type": "getProxies"}))

    async def _ok(self, request: Mapping[str, Any]) -> None:
        response = await self._client._request(request)
        if response.get("@type") != "ok":
            raise TelegramProxyValidationError("TDLib rejected the proxy request")


def _proxy_id(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TelegramProxyValidationError("proxy id must be a positive integer")
    return value


_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")


def _validate_host(host: str) -> None:
    """Accept a DNS host or bare IP address, never a URL or host:port."""
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    labels = host.split(".")
    if host.endswith("."):
        labels = labels[:-1]
    if not labels or len(host) > 253 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise TelegramProxyValidationError(
            "proxy host must be a host name or bare IP address without a URL or port"
        )


def _validate_mtproto_secret(value: object) -> None:
    """Validate TDLib's documented hexadecimal MTProto secret encoding.

    TDLib 1.8.67 accepts exactly a 16-byte secret, a 17-byte ``dd`` secret,
    or an ``ee`` secret with its non-empty domain suffix.  Passing any other
    hexadecimal value to ``addProxy`` defers a predictable native rejection
    until startup, so reject it at the typed account boundary instead.
    """
    if not isinstance(value, str) or not value:
        raise TelegramProxyValidationError("MTProto proxy secret must be hexadecimal")
    if len(value) % 2 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise TelegramProxyValidationError("MTProto proxy secret must be hexadecimal")
    try:
        raw_secret = bytes.fromhex(value)
    except ValueError as exc:
        raise TelegramProxyValidationError("MTProto proxy secret must be hexadecimal") from exc
    size = len(raw_secret)
    valid = (
        size == 16
        or (size == 17 and raw_secret[0] == 0xDD)
        or (18 <= size <= 17 + 182 and raw_secret[0] == 0xEE)
    )
    if not valid:
        raise TelegramProxyValidationError(
            "MTProto proxy secret isn't a TDLib-supported 16-byte, dd, or ee secret"
        )


@dataclass(frozen=True)
class _PendingProxySnapshot:
    _response: Any

    async def resolve(self) -> list[TelegramProxyReceipt]:
        response = await self._response
        proxies = response.get("proxies")
        if response.get("@type") != "addedProxies" or not isinstance(proxies, list):
            raise TelegramProxyValidationError("TDLib returned an invalid proxy bootstrap snapshot")
        return [_receipt(proxy) for proxy in proxies if isinstance(proxy, Mapping)]


__all__ = [
    "TelegramProxyBootstrapReceipt",
    "TelegramProxyConfig",
    "TelegramProxyController",
    "TelegramProxyReceipt",
    "TelegramProxyValidationError",
]
