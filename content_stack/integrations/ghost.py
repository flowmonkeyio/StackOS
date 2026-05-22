"""Ghost Admin API integration wrapper.

Authentication: Ghost Admin API key (``id:hex_secret``) signed into a
short-lived HS256 JWT and sent as ``Authorization: Ghost <token>``.
``site_url`` comes from credential/target config and points at the
Ghost Admin domain root.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from content_stack.integrations._base import BaseIntegration, IntegrationCallResult
from content_stack.mcp.errors import IntegrationDownError


class GhostIntegration(BaseIntegration):
    """Wrapper for stable Ghost Admin API endpoints."""

    kind = "ghost"
    vendor = "ghost"
    default_qps = 2.0

    def __init__(
        self,
        *,
        site_url: str | None = None,
        api_version: str = "v5.0",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._site_url = (site_url or "").rstrip("/")
        if not self._site_url:
            raise IntegrationDownError(
                "Ghost credential missing config_json.ghost_url",
                data={"vendor": "ghost", "required_config": "ghost_url"},
            )
        self._api_version = api_version
        self._key_id, self._secret = self._parse_payload(self.payload)

    @staticmethod
    def _parse_payload(payload: bytes) -> tuple[str, bytes]:
        text = payload.decode("utf-8").strip()
        if not text:
            raise IntegrationDownError(
                "Ghost credential payload is empty",
                data={"vendor": "ghost"},
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            key = text
        else:
            if not isinstance(parsed, dict):
                raise IntegrationDownError(
                    "Ghost credential JSON must be an object",
                    data={"vendor": "ghost"},
                )
            key = str(parsed.get("admin_api_key") or parsed.get("key") or "")
        if ":" not in key:
            raise IntegrationDownError(
                "Ghost Admin API key must be in id:secret form",
                data={"vendor": "ghost"},
            )
        key_id, hex_secret = key.split(":", 1)
        try:
            secret = bytes.fromhex(hex_secret)
        except ValueError as exc:
            raise IntegrationDownError(
                "Ghost Admin API key secret must be hexadecimal",
                data={"vendor": "ghost"},
            ) from exc
        return key_id, secret

    @staticmethod
    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _jwt(self) -> str:
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT", "kid": self._key_id}
        payload = {"iat": now, "exp": now + 300, "aud": "/admin/"}
        header_payload = ".".join(
            [
                self._b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
                self._b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
            ]
        )
        signature = hmac.new(
            self._secret,
            header_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{header_payload}.{self._b64url(signature)}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Ghost {self._jwt()}",
            "Accept-Version": self._api_version,
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self._site_url}/ghost/api/admin/{path.lstrip('/')}"

    async def users(self, *, limit: int = 1) -> IntegrationCallResult:
        """Browse users as a cheap permission/auth probe."""
        return await self.call(
            op="users",
            method="GET",
            url=self._url("users/"),
            params={"limit": str(limit), "include": "roles"},
            headers=self._headers(),
        )

    async def create_post(
        self,
        *,
        post: dict[str, Any],
        source: str = "html",
    ) -> IntegrationCallResult:
        """Create a Ghost post from an explicit Admin API payload."""
        return await self.call(
            op="post.create",
            method="POST",
            url=self._url("posts/"),
            params={"source": source},
            json_body={"posts": [post]},
            headers=self._headers(),
        )

    async def test_credentials(self) -> dict[str, Any]:
        """Probe auth and return a small user/role sample."""
        result = await self.users(limit=1)
        users = result.data.get("users", []) if isinstance(result.data, dict) else []
        first = users[0] if users else {}
        roles = first.get("roles", []) if isinstance(first, dict) else []
        return {
            "ok": True,
            "vendor": "ghost",
            "user_id": first.get("id") if isinstance(first, dict) else None,
            "name": first.get("name") if isinstance(first, dict) else None,
            "roles": roles,
        }


__all__ = ["GhostIntegration"]
