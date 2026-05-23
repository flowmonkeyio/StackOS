"""WordPress REST API integration wrapper.

Authentication: WordPress Application Passwords over HTTPS using Basic
Auth. The encrypted payload is either JSON
``{"username": "...", "application_password": "..."}`` or the compact
``username:application-password`` form. ``site_url`` comes from the
credential/target config and points at the WordPress site root.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError


class WordPressIntegration(BaseIntegration):
    """Wrapper for core ``/wp-json/wp/v2`` endpoints."""

    kind = "wordpress"
    vendor = "wordpress"
    default_qps = 2.0

    # Official refs:
    # https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/
    # https://developer.wordpress.org/rest-api/reference/application-passwords/
    # https://developer.wordpress.org/rest-api/reference/posts/

    def __init__(self, *, site_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._site_url = (site_url or "").rstrip("/")
        if not self._site_url:
            raise IntegrationDownError(
                "WordPress credential missing config_json.wp_url",
                data={"vendor": "wordpress", "required_config": "wp_url"},
            )
        self._username, self._application_password = self._parse_payload(self.payload)

    @staticmethod
    def _parse_payload(payload: bytes) -> tuple[str, str]:
        text = payload.decode("utf-8").strip()
        if not text:
            raise IntegrationDownError(
                "WordPress credential payload is empty",
                data={"vendor": "wordpress"},
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if ":" not in text:
                raise IntegrationDownError(
                    "WordPress credential payload must be JSON or username:application_password",
                    data={"vendor": "wordpress"},
                ) from None
            username, password = text.split(":", 1)
        else:
            if not isinstance(parsed, dict):
                raise IntegrationDownError(
                    "WordPress credential JSON must be an object",
                    data={"vendor": "wordpress"},
                )
            username = str(parsed.get("username") or parsed.get("user") or "")
            password = str(
                parsed.get("application_password")
                or parsed.get("app_password")
                or parsed.get("password")
                or ""
            )
        if not username or not password:
            raise IntegrationDownError(
                "WordPress credential missing username/application_password",
                data={"vendor": "wordpress"},
            )
        return username, password

    def _url(self, path: str) -> str:
        return f"{self._site_url}/wp-json/wp/v2/{path.lstrip('/')}"

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self._username, self._application_password)

    async def current_user(self) -> IntegrationCallResult:
        """Return the authenticated REST user."""
        return await self.call(
            op="current_user",
            method="GET",
            url=self._url("users/me"),
            params={"context": "edit"},
            auth=self._auth(),
        )

    async def create_post(self, *, post: dict[str, Any]) -> IntegrationCallResult:
        """Create a post from an explicit WordPress REST payload."""
        # TODO: surface create_posts capability in setup checks before templates
        # promise publish flows beyond this single create-post action.
        return await self.call(
            op="post.create",
            method="POST",
            url=self._url("posts"),
            json_body=post,
            auth=self._auth(),
        )

    async def test_credentials(self) -> dict[str, Any]:
        """Probe auth and return the REST user role shape."""
        result = await self.current_user()
        data = result.data if isinstance(result.data, dict) else {}
        return {
            "ok": True,
            "vendor": "wordpress",
            "user_id": data.get("id"),
            "name": data.get("name"),
            "roles": data.get("roles", []),
        }


__all__ = ["WordPressIntegration"]
