"""Trackbooth Agent API integration wrapper and shared URL/auth helpers."""

from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from stackos.artifacts import redact_secret_text
from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError

TRACKBOOTH_DEFAULT_API_BASE_URL = "https://apis.trackbooth.com"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class TrackboothConfigError(ValueError):
    """Raised when a Trackbooth safe credential config is unsafe or invalid."""


def normalize_trackbooth_base_url(raw_url: str | None) -> str:
    """Validate and normalize a Trackbooth API base URL.

    Remote custom URLs must use HTTPS. Plain HTTP is allowed only for local
    development targets on localhost/127.0.0.1/[::1].
    """
    raw = (raw_url or TRACKBOOTH_DEFAULT_API_BASE_URL).strip()
    if not raw:
        raw = TRACKBOOTH_DEFAULT_API_BASE_URL
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TrackboothConfigError("Trackbooth API URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise TrackboothConfigError("Trackbooth API URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise TrackboothConfigError("Trackbooth API URL must not include query or fragment")
    host = parsed.hostname
    if not host:
        raise TrackboothConfigError("Trackbooth API URL must include a host")

    host_lower = host.lower()
    is_localhost = host_lower in _LOCAL_HOSTS
    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        ip = None
    if ip is not None:
        is_localhost = ip.is_loopback
        if not ip.is_loopback and (
            ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise TrackboothConfigError(
                "Trackbooth API URL must not target private, link-local, reserved, or metadata IPs"
            )

    if parsed.scheme == "http" and not is_localhost:
        raise TrackboothConfigError(
            "Trackbooth API URL may use http only for localhost or 127.0.0.1"
        )
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def parse_trackbooth_api_key(payload: bytes) -> str:
    """Read the daemon-held Trackbooth API key from raw or JSON credential bytes."""
    text = payload.decode("utf-8").strip()
    if not text:
        raise TrackboothConfigError("Trackbooth credential payload is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        key = text
    else:
        if not isinstance(parsed, dict):
            raise TrackboothConfigError("Trackbooth credential JSON must be an object")
        key = str(parsed.get("api_key") or parsed.get("token") or parsed.get("value") or "")
    if not key.strip():
        raise TrackboothConfigError("Trackbooth credential missing api_key")
    return key.strip()


def trackbooth_headers(api_key: str, *, acting_as_account: str | None = None) -> dict[str, str]:
    """Build supported Trackbooth auth headers without exposing secondary auth modes."""
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    if acting_as_account is not None and acting_as_account.strip():
        headers["X-Acting-As-Account"] = acting_as_account.strip()
    return headers


class TrackboothIntegration(BaseIntegration):
    """Credential health check wrapper for the Trackbooth Agent API."""

    kind = "trackbooth"
    vendor = "trackbooth"
    default_qps = 2.0

    def __init__(self, *, api_base_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            self._api_base_url = normalize_trackbooth_base_url(api_base_url)
            self._api_key = parse_trackbooth_api_key(self.payload)
        except TrackboothConfigError as exc:
            raise IntegrationDownError(
                redact_secret_text(str(exc)),
                data={"vendor": "trackbooth"},
            ) from exc

    async def test_credentials(self) -> dict[str, Any]:
        """Probe the permission-filtered live catalog with X-API-Key auth."""
        url = f"{self._api_base_url}/api/agent-api/catalog"
        try:
            response = await self._http.get(
                url,
                headers=trackbooth_headers(self._api_key),
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise IntegrationDownError(
                redact_secret_text(f"Trackbooth credential test failed: {exc}"),
                data={"vendor": "trackbooth", "api_base_url": self._api_base_url},
            ) from exc

        if response.status_code in {401, 403}:
            return {
                "ok": False,
                "vendor": "trackbooth",
                "status": "forbidden" if response.status_code == 403 else "unauthorized",
                "status_code": response.status_code,
                "summary": redact_secret_text(response.text[:300]),
                "api_base_url": self._api_base_url,
            }
        if response.status_code >= 400:
            raise IntegrationDownError(
                redact_secret_text(
                    f"Trackbooth credential test returned {response.status_code}: "
                    f"{response.text[:300]}"
                ),
                data={
                    "vendor": "trackbooth",
                    "status": response.status_code,
                    "api_base_url": self._api_base_url,
                },
            )

        try:
            body: Any = response.json()
        except ValueError:
            body = {}
        endpoint_count = _catalog_endpoint_count(body)
        return {
            "ok": True,
            "vendor": "trackbooth",
            "status": "ok",
            "endpoint_count": endpoint_count,
            "api_base_url": self._api_base_url,
        }


def _catalog_endpoint_count(body: Any) -> int | None:
    if isinstance(body, list):
        return len(body)
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        endpoints = data.get("endpoints") or data.get("tools")
        if isinstance(endpoints, list):
            return len(endpoints)
    endpoints = body.get("endpoints") or body.get("tools")
    if isinstance(endpoints, list):
        return len(endpoints)
    return None


__all__ = [
    "TRACKBOOTH_DEFAULT_API_BASE_URL",
    "TrackboothConfigError",
    "TrackboothIntegration",
    "normalize_trackbooth_base_url",
    "parse_trackbooth_api_key",
    "trackbooth_headers",
]
