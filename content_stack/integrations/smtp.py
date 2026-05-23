"""SMTP auth wrapper for credential tests.

Official docs:
- SMTP core protocol: https://www.rfc-editor.org/rfc/rfc5321.html
- SMTP AUTH extension: https://www.rfc-editor.org/rfc/rfc4954
"""

from __future__ import annotations

import asyncio
import json
import smtplib
from contextlib import suppress
from typing import Any

from content_stack.integrations._base import BaseIntegration
from content_stack.mcp.errors import IntegrationDownError


class SmtpIntegration(BaseIntegration):
    """Wrapper for SMTP credential health checks."""

    kind = "smtp"
    vendor = "smtp"
    default_qps = 1.0

    def __init__(
        self,
        *,
        host: str,
        port: int,
        tls_mode: str,
        username: str,
        timeout_s: float = 30.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._host = host
        self._port = port
        self._tls_mode = tls_mode
        self._username = username
        self._timeout_s = timeout_s
        self._password = self._parse_payload(self.payload)

    @staticmethod
    def _parse_payload(payload: bytes) -> str:
        text = payload.decode("utf-8").strip()
        if not text:
            raise IntegrationDownError("SMTP credential payload is empty", data={"vendor": "smtp"})
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            password = text
        else:
            if not isinstance(parsed, dict):
                raise IntegrationDownError(
                    "SMTP credential JSON must be an object",
                    data={"vendor": "smtp"},
                )
            password = str(parsed.get("password") or parsed.get("secret") or "")
        if not password:
            raise IntegrationDownError("SMTP credential missing password", data={"vendor": "smtp"})
        return password

    async def test_credentials(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._test_sync)

    def _test_sync(self) -> dict[str, Any]:
        client: Any | None = None
        try:
            klass = smtplib.SMTP_SSL if self._tls_mode == "ssl" else smtplib.SMTP
            client = klass(self._host, self._port, timeout=self._timeout_s)
            if self._tls_mode == "starttls":
                client.ehlo()
                client.starttls()
                client.ehlo()
            client.login(self._username, self._password)
            return {
                "ok": True,
                "vendor": "smtp",
                "status": "ok",
                "host": self._host,
                "port": self._port,
                "tls_mode": self._tls_mode,
            }
        except Exception as exc:
            raise IntegrationDownError(
                "SMTP credential test failed",
                data={
                    "vendor": "smtp",
                    "host": self._host,
                    "port": self._port,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        finally:
            if client is not None:
                with suppress(Exception):
                    client.quit()
                with suppress(Exception):
                    client.close()


__all__ = ["SmtpIntegration"]
