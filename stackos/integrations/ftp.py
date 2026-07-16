"""FTP/explicit-FTPS wrapper and credential health probe.

Official references:
- Explicit FTP over TLS: https://www.rfc-editor.org/rfc/rfc4217
- Python ``ftplib`` adapter: https://docs.python.org/3/library/ftplib.html
"""

from __future__ import annotations

import asyncio
import codecs
import ftplib
import json
import re
import ssl
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError

_TLS_MODES = {"explicit", "none"}


def validate_ftp_credential_config(config: Mapping[str, Any]) -> None:
    """Validate FTP connection fields without opening a network connection."""

    host = config.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("FTP host is required")
    host = host.strip()
    if "://" in host or "/" in host or "@" in host or any(char.isspace() for char in host):
        raise ValueError("FTP host must be a hostname or IP address without a scheme or path")

    username = config.get("username")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("FTP username is required")

    port = config.get("port", 21)
    if isinstance(port, bool) or not isinstance(port, int | float) or int(port) != port:
        raise ValueError("FTP port must be an integer")
    if not 1 <= int(port) <= 65_535:
        raise ValueError("FTP port must be between 1 and 65535")

    tls_mode = str(config.get("tls_mode") or "explicit").lower()
    if tls_mode not in _TLS_MODES:
        raise ValueError("FTP transport security must be explicit or none")

    timeout_s = config.get("timeout_s", 30)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int | float):
        raise ValueError("FTP timeout must be a number")
    if not 1 <= float(timeout_s) <= 600:
        raise ValueError("FTP timeout must be between 1 and 600 seconds")

    passive_mode = config.get("passive_mode", True)
    if not isinstance(passive_mode, bool) and str(passive_mode).lower() not in {
        "true",
        "false",
        "1",
        "0",
        "yes",
        "no",
        "on",
        "off",
    }:
        raise ValueError("FTP passive mode must be enabled or disabled")

    encoding = config.get("encoding", "utf-8")
    if not isinstance(encoding, str) or not encoding:
        raise ValueError("FTP filename encoding is required")
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        raise ValueError("FTP filename encoding is not recognized") from exc


class FtpIntegration(BaseIntegration):
    """Wrapper for read-only FTP credential health checks."""

    kind = "ftp"
    vendor = "ftp"
    default_qps = 1.0

    def __init__(
        self,
        *,
        host: str,
        port: int = 21,
        tls_mode: str = "explicit",
        username: str,
        passive_mode: bool = True,
        timeout_s: float = 30.0,
        encoding: str = "utf-8",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        config = {
            "host": host,
            "port": port,
            "tls_mode": tls_mode,
            "username": username,
            "passive_mode": passive_mode,
            "timeout_s": timeout_s,
            "encoding": encoding,
        }
        try:
            validate_ftp_credential_config(config)
        except ValueError as exc:
            raise IntegrationDownError(str(exc), data={"vendor": "ftp"}) from exc
        self._host = host
        self._port = int(port)
        self._tls_mode = str(tls_mode).lower()
        self._username = username
        self._passive_mode = passive_mode
        self._timeout_s = float(timeout_s)
        self._encoding = encoding
        self._password = parse_ftp_password(self.payload)
        if self._tls_mode not in _TLS_MODES:
            raise IntegrationDownError(
                "FTP tls_mode must be explicit or none",
                data={"vendor": "ftp", "tls_mode": self._tls_mode},
            )

    async def test_credentials(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._test_sync)

    def _test_sync(self) -> dict[str, Any]:
        client: Any | None = None
        try:
            client = open_ftp_client(
                host=self._host,
                port=self._port,
                tls_mode=self._tls_mode,
                username=self._username,
                password=self._password,
                passive_mode=self._passive_mode,
                timeout_s=self._timeout_s,
                encoding=self._encoding,
                ftp_module=ftplib,
            )
            raw_remote_directory = str(client.pwd())
            remote_directory = _redact_text(raw_remote_directory, secret=self._password)
            return {
                "ok": True,
                "vendor": "ftp",
                "status": "ok",
                "host": _redact_text(self._host, secret=self._password),
                "port": self._port,
                "tls_mode": self._tls_mode,
                "passive_mode": self._passive_mode,
                "remote_directory": remote_directory,
                "remote_directory_redacted": self._password in raw_remote_directory,
            }
        except IntegrationDownError:
            raise
        except Exception as exc:
            raise IntegrationDownError(
                "FTP credential test failed",
                data={
                    "vendor": "ftp",
                    "host": self._host,
                    "port": self._port,
                    "tls_mode": self._tls_mode,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        finally:
            close_ftp_client(client)


def _redact_text(value: str, *, secret: str) -> str:
    message = value.replace("\r", " ").replace("\n", " ")
    return message.replace(secret, "[REDACTED]") if secret else message


def parse_ftp_password(payload: bytes) -> str:
    text = payload.decode("utf-8").strip()
    if not text:
        raise IntegrationDownError(
            "FTP credential payload is empty",
            data={"vendor": "ftp"},
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        password = text
    else:
        if not isinstance(parsed, dict):
            raise IntegrationDownError(
                "FTP credential JSON must be an object",
                data={"vendor": "ftp"},
            )
        password = str(parsed.get("password") or parsed.get("secret") or "")
    if not password:
        raise IntegrationDownError(
            "FTP credential is missing password",
            data={"vendor": "ftp"},
        )
    return password


def open_ftp_client(
    *,
    host: str,
    port: int,
    tls_mode: str,
    username: str,
    password: str,
    passive_mode: bool,
    timeout_s: float,
    encoding: str,
    ftp_module: Any = ftplib,
) -> Any:
    """Open a fresh authenticated FTP session for one explicit action."""

    if tls_mode == "explicit":
        client = ftp_module.FTP_TLS(
            timeout=timeout_s,
            encoding=encoding,
            context=ssl.create_default_context(),
        )
    elif tls_mode == "none":
        client = ftp_module.FTP(timeout=timeout_s, encoding=encoding)
    else:
        raise IntegrationDownError(
            "FTP tls_mode must be explicit or none",
            data={"vendor": "ftp", "tls_mode": tls_mode},
        )
    client.connect(host, port, timeout=timeout_s)
    if tls_mode == "explicit":
        client.auth()
    try:
        client.login(username, password)
    except ftplib.error_perm as exc:
        reply_code = _ftp_reply_code(exc)
        data: dict[str, Any] = {
            "vendor": "ftp",
            "stage": "authenticate",
            "reason_code": "login_rejected",
        }
        if reply_code is not None:
            data["reply_code"] = reply_code
        raise IntegrationDownError("FTP server rejected the login", data=data) from exc
    if tls_mode == "explicit":
        client.prot_p()
    client.set_pasv(passive_mode)
    return client


def _ftp_reply_code(exc: BaseException) -> str | None:
    match = re.match(r"\s*(\d{3})\b", str(exc))
    return match.group(1) if match is not None else None


def close_ftp_client(client: Any | None) -> None:
    if client is None:
        return
    with suppress(Exception):
        client.quit()
    with suppress(Exception):
        client.close()


__all__ = [
    "FtpIntegration",
    "close_ftp_client",
    "open_ftp_client",
    "parse_ftp_password",
    "validate_ftp_credential_config",
]
