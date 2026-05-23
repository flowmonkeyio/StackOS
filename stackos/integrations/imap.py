"""IMAP auth wrapper for credential tests.

Official docs:
- IMAP4rev2 protocol: https://www.rfc-editor.org/rfc/rfc9051.html
"""

from __future__ import annotations

import asyncio
import imaplib
import json
from contextlib import suppress
from typing import Any

from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError


class ImapIntegration(BaseIntegration):
    """Wrapper for IMAP credential health checks."""

    kind = "imap"
    vendor = "imap"
    default_qps = 1.0

    def __init__(
        self,
        *,
        host: str,
        port: int,
        tls_mode: str,
        username: str,
        default_mailbox: str = "INBOX",
        timeout_s: float = 30.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._host = host
        self._port = port
        self._tls_mode = tls_mode
        self._username = username
        self._default_mailbox = default_mailbox
        self._timeout_s = timeout_s
        self._password = self._parse_payload(self.payload)

    @staticmethod
    def _parse_payload(payload: bytes) -> str:
        text = payload.decode("utf-8").strip()
        if not text:
            raise IntegrationDownError("IMAP credential payload is empty", data={"vendor": "imap"})
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            password = text
        else:
            if not isinstance(parsed, dict):
                raise IntegrationDownError(
                    "IMAP credential JSON must be an object",
                    data={"vendor": "imap"},
                )
            password = str(parsed.get("password") or parsed.get("secret") or "")
        if not password:
            raise IntegrationDownError("IMAP credential missing password", data={"vendor": "imap"})
        return password

    async def test_credentials(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._test_sync)

    def _test_sync(self) -> dict[str, Any]:
        client: Any | None = None
        try:
            klass = imaplib.IMAP4_SSL if self._tls_mode == "ssl" else imaplib.IMAP4
            try:
                client = klass(self._host, self._port, timeout=self._timeout_s)
            except TypeError:
                client = klass(self._host, self._port)
            if self._tls_mode == "starttls":
                client.starttls()
            client.login(self._username, self._password)
            status, _data = client.select(self._default_mailbox, readonly=True)
            if str(status).upper() != "OK":
                raise IntegrationDownError(
                    f"IMAP select failed for mailbox {self._default_mailbox!r}",
                    data={"vendor": "imap", "mailbox": self._default_mailbox},
                )
            return {
                "ok": True,
                "vendor": "imap",
                "status": "ok",
                "host": self._host,
                "port": self._port,
                "tls_mode": self._tls_mode,
                "default_mailbox": self._default_mailbox,
            }
        except IntegrationDownError:
            raise
        except Exception as exc:
            raise IntegrationDownError(
                "IMAP credential test failed",
                data={
                    "vendor": "imap",
                    "host": self._host,
                    "port": self._port,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        finally:
            if client is not None:
                with suppress(Exception):
                    client.close()
                with suppress(Exception):
                    client.logout()


__all__ = ["ImapIntegration"]
