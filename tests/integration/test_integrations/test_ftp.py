"""FTP credential wrapper tests."""

from __future__ import annotations

import asyncio
import ftplib
import json
from typing import Any, ClassVar

import httpx
import pytest

from stackos.integrations.ftp import FtpIntegration
from stackos.mcp.errors import IntegrationDownError


class _AuthFTP:
    instances: ClassVar[list[_AuthFTP]] = []
    pwd_value: ClassVar[str] = "/"

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.cwd = self.__class__.pwd_value
        self.__class__.instances.append(self)

    def connect(self, host: str, port: int, timeout: float | None = None) -> str:
        self.calls.append(("connect", host, port, timeout))
        return "220 ready"

    def auth(self) -> str:
        self.calls.append(("auth",))
        return "234 secure"

    def login(self, username: str, password: str) -> str:
        self.calls.append(("login", username, password))
        return "230 logged in"

    def prot_p(self) -> str:
        self.calls.append(("prot_p",))
        return "200 protected"

    def set_pasv(self, value: bool) -> None:
        self.calls.append(("set_pasv", value))

    def pwd(self) -> str:
        self.calls.append(("pwd",))
        return self.cwd

    def quit(self) -> str:
        self.calls.append(("quit",))
        return "221 bye"

    def close(self) -> None:
        self.calls.append(("close",))


class _RejectingAuthFTP(_AuthFTP):
    instances: ClassVar[list[_RejectingAuthFTP]] = []

    def login(self, username: str, password: str) -> str:
        self.calls.append(("login", username, password))
        raise ftplib.error_perm("530 Login incorrect; password=ftp-secret")


def test_ftp_auth_probe_rejects_url_in_host(project_id: int) -> None:
    async def go() -> None:
        async with httpx.AsyncClient() as client:
            with pytest.raises(IntegrationDownError, match="hostname or IP address"):
                FtpIntegration(
                    payload=json.dumps({"password": "ftp-secret"}).encode(),
                    project_id=project_id,
                    http=client,
                    host="ftp://ftp.example.test/public_html",
                    username="deploy",
                )

    asyncio.run(go())


def test_ftp_auth_probe_protects_ftps_data_and_returns_safe_metadata(
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.integrations.ftp as ftp_module

    _AuthFTP.instances.clear()
    _AuthFTP.pwd_value = "/"
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _AuthFTP)

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = FtpIntegration(
                payload=json.dumps({"password": "ftp-secret"}).encode(),
                project_id=project_id,
                http=client,
                host="ftp.example.test",
                port=21,
                tls_mode="explicit",
                username="deploy",
                passive_mode=True,
                timeout_s=30,
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    calls = _AuthFTP.instances[0].calls
    assert calls.index(("prot_p",)) < calls.index(("pwd",))
    assert result == {
        "ok": True,
        "vendor": "ftp",
        "status": "ok",
        "host": "ftp.example.test",
        "port": 21,
        "tls_mode": "explicit",
        "passive_mode": True,
        "remote_directory": "/",
        "remote_directory_redacted": False,
    }
    assert "ftp-secret" not in json.dumps(result)


def test_ftp_auth_probe_exact_redacts_password_echoed_by_pwd(
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.integrations.ftp as ftp_module

    _AuthFTP.instances.clear()
    _AuthFTP.pwd_value = "/server/ftp-secret\r\n"
    monkeypatch.setattr(ftp_module.ftplib, "FTP_TLS", _AuthFTP)

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = FtpIntegration(
                payload=json.dumps({"password": "ftp-secret"}).encode(),
                project_id=project_id,
                http=client,
                host="ftp.example.test",
                username="deploy",
            )
            return await integration.test_credentials()

    result = asyncio.run(go())
    assert result["remote_directory"] == "/server/[REDACTED]  "
    assert result["remote_directory_redacted"] is True
    assert "ftp-secret" not in json.dumps(result)


def test_ftp_auth_probe_reports_sanitized_login_rejection(
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.integrations.ftp as ftp_module

    _RejectingAuthFTP.instances.clear()
    monkeypatch.setattr(ftp_module.ftplib, "FTP", _RejectingAuthFTP)

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integration = FtpIntegration(
                payload=json.dumps({"password": "ftp-secret"}).encode(),
                project_id=project_id,
                http=client,
                host="ftp.example.test",
                tls_mode="none",
                username="deploy",
            )
            with pytest.raises(IntegrationDownError) as excinfo:
                await integration.test_credentials()
            assert excinfo.value.detail == "FTP server rejected the login"
            assert excinfo.value.data == {
                "vendor": "ftp",
                "stage": "authenticate",
                "reason_code": "login_rejected",
                "reply_code": "530",
            }
            assert "ftp-secret" not in json.dumps(excinfo.value.to_dict())

    asyncio.run(go())
