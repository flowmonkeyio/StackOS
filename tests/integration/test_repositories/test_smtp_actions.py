"""SMTP connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import pytest
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository


class _FakeSMTP:
    instances: ClassVar[list[_FakeSMTP]] = []
    refused: ClassVar[dict[str, tuple[int, bytes]]] = {}

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_count = 0
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None
        self.send_args: dict[str, Any] | None = None
        self.quit_called = False
        self.close_called = False
        self.__class__.instances.append(self)

    def ehlo(self) -> None:
        self.ehlo_count += 1

    def starttls(self) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(
        self,
        message: Any,
        *,
        from_addr: str,
        to_addrs: list[str],
    ) -> dict[str, tuple[int, bytes]]:
        self.send_args = {
            "message": message,
            "from_addr": from_addr,
            "to_addrs": to_addrs,
        }
        return dict(self.__class__.refused)

    def quit(self) -> None:
        self.quit_called = True

    def close(self) -> None:
        self.close_called = True


class _FakeSMTPSSL(_FakeSMTP):
    instances: ClassVar[list[_FakeSMTPSSL]] = []
    refused: ClassVar[dict[str, tuple[int, bytes]]] = {}


def _credential_ref(
    session: Session,
    project_id: int,
    *,
    tls_mode: str = "starttls",
) -> str:
    ActionRepository(session).describe(action_ref="communications.smtp.email.send")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="smtp",
        secret_payload=json.dumps({"password": "smtp-secret"}).encode("utf-8"),
        config_json={
            "host": "smtp.example.test",
            "port": 587 if tls_mode != "ssl" else 465,
            "tls_mode": tls_mode,
            "username": "mailer@example.test",
            "from_email": "mailer@example.test",
            "from_name": "StackOS",
            "reply_to": "reply@example.test",
        },
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="smtp")
    return status.connections[0].credential_ref


def test_smtp_action_is_executable_and_sends_without_secret_leak(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.smtp as smtp_module

    _FakeSMTP.instances.clear()
    _FakeSMTP.refused = {"bad@example.test": (550, b"mailbox unavailable")}
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", _FakeSMTP)
    credential_ref = _credential_ref(session, project_id)

    described = ActionRepository(session).describe(
        project_id=project_id,
        action_ref="communications.smtp.email.send",
    )
    assert described.connector_registered is True
    assert described.manifest.connector_key == "smtp"
    assert described.manifest.operation == "email.send"

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="communications.smtp.email.send",
            input_json={
                "recipients": ["ok@example.test", "bad@example.test"],
                "cc": ["copy@example.test"],
                "subject": "Status",
                "text": "Plain body",
                "html": "<p>HTML body</p>",
                "reply_to": "reply@example.test",
                "headers": {"X-StackOS-Run": "test"},
            },
            credential_ref=credential_ref,
        )
    ).data

    instance = _FakeSMTP.instances[0]
    assert instance.starttls_called is True
    assert instance.login_args == ("mailer@example.test", "smtp-secret")
    assert instance.send_args is not None
    assert instance.send_args["from_addr"] == "mailer@example.test"
    assert instance.send_args["to_addrs"] == [
        "ok@example.test",
        "bad@example.test",
        "copy@example.test",
    ]
    message = instance.send_args["message"]
    assert message["From"] == "StackOS <mailer@example.test>"
    assert message["Reply-To"] == "reply@example.test"
    assert message["X-StackOS-Run"] == "test"
    assert out.output_json["status"] == "partial"
    assert out.output_json["accepted_recipient_count"] == 2
    assert out.output_json["rejected_recipient_count"] == 1
    assert out.action_call.connector_key == "smtp"
    assert out.action_call.credential_ref == credential_ref
    rendered = json.dumps(out.model_dump(mode="json"))
    assert "smtp-secret" not in rendered

    records = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    assert records.items[0].data_json["provider_key"] == "smtp"
    assert records.items[0].data_json["direction"] == "outbound"


def test_smtp_validate_rejects_unsafe_payload(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id)

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="communications.smtp.email.send",
        input_json={
            "recipients": ["not-an-email"],
            "subject": "Missing body",
            "headers": {"Bcc": "hidden@example.test", "X-Bad": "one\ntwo"},
        },
        credential_ref=credential_ref,
    )

    codes = {item.code for item in validation.issues}
    assert {"format", "forbidden", "required"} <= codes


def test_smtp_ssl_path_uses_smtp_ssl(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.smtp as smtp_module

    _FakeSMTPSSL.instances.clear()
    _FakeSMTPSSL.refused = {}
    monkeypatch.setattr(smtp_module.smtplib, "SMTP_SSL", _FakeSMTPSSL)
    credential_ref = _credential_ref(session, project_id, tls_mode="ssl")

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="communications.smtp.email.send",
            input_json={
                "recipients": ["ok@example.test"],
                "subject": "SSL",
                "text": "Body",
            },
            credential_ref=credential_ref,
        )
    ).data

    assert len(_FakeSMTPSSL.instances) == 1
    assert _FakeSMTPSSL.instances[0].starttls_called is False
    assert out.output_json["status"] == "accepted"
