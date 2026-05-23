"""Telegram Bot API auth wrapper for credential tests.

Official docs:
- Bot API overview: https://core.telegram.org/bots/api
- getMe: https://core.telegram.org/bots/api#getme
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError


class TelegramBotIntegration(BaseIntegration):
    """Wrapper for Telegram Bot API credential health checks."""

    kind = "telegram-bot"
    vendor = "telegram-bot"
    default_qps = 1.0

    def __init__(self, *, api_base_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_base_url = (api_base_url or "https://api.telegram.org").rstrip("/")
        self._bot_token = self._parse_payload(self.payload)

    @staticmethod
    def _parse_payload(payload: bytes) -> str:
        text = payload.decode("utf-8").strip()
        if not text:
            raise IntegrationDownError(
                "Telegram credential payload is empty",
                data={"vendor": "telegram-bot"},
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            token = text
        else:
            if not isinstance(parsed, dict):
                raise IntegrationDownError(
                    "Telegram credential JSON must be an object",
                    data={"vendor": "telegram-bot"},
                )
            token = str(parsed.get("bot_token") or parsed.get("token") or "")
        if not token:
            raise IntegrationDownError(
                "Telegram credential missing bot_token",
                data={"vendor": "telegram-bot"},
            )
        return token

    def _method_url(self, method: str) -> str:
        return f"{self._api_base_url}/bot{quote(self._bot_token, safe=':-_')}/{method}"

    async def get_me(self) -> IntegrationCallResult:
        return await self.call(
            op="getMe",
            method="POST",
            url=self._method_url("getMe"),
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.get_me()
        body: dict[str, Any] = result.data if isinstance(result.data, dict) else {}
        raw_user = body.get("result")
        user: dict[str, Any] = raw_user if isinstance(raw_user, dict) else {}
        return {
            "ok": bool(body.get("ok")),
            "vendor": "telegram-bot",
            "bot_id": user.get("id"),
            "username": user.get("username"),
            "first_name": user.get("first_name"),
            "is_bot": user.get("is_bot"),
        }


__all__ = ["TelegramBotIntegration"]
