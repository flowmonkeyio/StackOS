"""IMAP connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import json
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.resources import ResourceRepository


def _raw_message() -> bytes:
    message = EmailMessage()
    message["Subject"] = "Need help"
    message["From"] = "customer@example.test"
    message["To"] = "support@example.test"
    message["Date"] = "Sat, 23 May 2026 12:00:00 +0000"
    message["Message-ID"] = "<m1@example.test>"
    message.set_content("Hello from IMAP")
    return message.as_bytes()


class _FakeIMAP:
    instances: ClassVar[list[_FakeIMAP]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args: tuple[str, str] | None = None
        self.selected: list[tuple[str, bool]] = []
        self.uid_calls: list[tuple[Any, ...]] = []
        self.starttls_called = False
        self.logout_called = False
        self.__class__.instances.append(self)

    def starttls(self) -> None:
        self.starttls_called = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def list(self) -> tuple[str, list[bytes]]:
        return (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Support"',
            ],
        )

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected.append((mailbox, readonly))
        return ("OK", [b"2"])

    def response(self, key: str) -> tuple[str, list[bytes]]:
        assert key == "UIDVALIDITY"
        return ("UIDVALIDITY", [b"777"])

    def uid(self, *args: Any) -> tuple[str, list[Any]]:
        self.uid_calls.append(args)
        if args[0] == "SEARCH":
            return ("OK", [b"3 5"])
        if args[0] == "FETCH":
            assert args[1] == "3"
            assert "BODY.PEEK" in args[2]
            return (
                "OK",
                [
                    (
                        b"3 (UID 3 FLAGS (\\Seen) RFC822.SIZE 123 BODY[] {120}",
                        _raw_message(),
                    )
                ],
            )
        if args[0] == "STORE":
            return ("OK", [b"3 (FLAGS (\\Seen))"])
        raise AssertionError(f"unexpected uid call {args!r}")

    def close(self) -> None:
        return None

    def logout(self) -> None:
        self.logout_called = True


class _FakeIMAPSSL(_FakeIMAP):
    instances: ClassVar[list[_FakeIMAPSSL]] = []


def _credential_ref(
    session: Session,
    project_id: int,
    *,
    tls_mode: str = "ssl",
) -> str:
    ActionRepository(session).describe(action_ref="communications.imap.mailbox.list")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="imap",
        secret_payload=json.dumps({"password": "imap-secret"}).encode("utf-8"),
        config_json={
            "host": "imap.example.test",
            "port": 993 if tls_mode == "ssl" else 143,
            "tls_mode": tls_mode,
            "username": "support@example.test",
            "default_mailbox": "INBOX",
            "mailbox_refs": {"support": "Support"},
            "search_limit": 50,
        },
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="imap")
    return status.connections[0].credential_ref


def test_imap_actions_are_registered(session: Session) -> None:
    repo = ActionRepository(session)

    for action_ref, operation in {
        "communications.imap.mailbox.list": "mailbox.list",
        "communications.imap.messages.search": "messages.search",
        "communications.imap.message.fetch": "message.fetch",
        "communications.imap.message.mark_seen": "message.mark_seen",
        "communications.imap.message.mark_unseen": "message.mark_unseen",
    }.items():
        described = repo.describe(action_ref=action_ref)
        assert described.connector_registered is True
        assert described.manifest.connector_key == "imap"
        assert described.manifest.operation == operation


def test_imap_list_search_fetch_and_mark_without_secret_leak(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session)

    mailboxes = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.mailbox.list",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data
    search = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.messages.search",
            input_json={
                "mailbox_ref": "support",
                "criteria": {"unseen": True, "since": "01-May-2026"},
                "limit": 10,
            },
            credential_ref=credential_ref,
        )
    ).data
    fetched = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.fetch",
            input_json={
                "mailbox_ref": "support",
                "uid": 3,
                "fields": ["subject", "from", "to", "text_preview", "flags"],
                "max_body_bytes": 4096,
            },
            credential_ref=credential_ref,
        )
    ).data
    marked = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.mark_unseen",
            input_json={"mailbox_ref": "support", "uid": 3},
            credential_ref=credential_ref,
        )
    ).data
    marked_seen = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.mark_seen",
            input_json={"mailbox_ref": "support", "uid": 5},
            credential_ref=credential_ref,
        )
    ).data

    assert mailboxes.output_json["mailbox_count"] == 2
    assert search.output_json["uidvalidity"] == "777"
    assert search.output_json["uids"] == [3, 5]
    assert fetched.output_json["subject"] == "Need help"
    assert fetched.output_json["from"] == ["customer@example.test"]
    assert fetched.output_json["text_preview"] == "Hello from IMAP\n"
    assert fetched.output_json["flags"] == ["\\Seen"]
    assert marked.output_json["attention_status"] == "unread"
    assert marked_seen.output_json["attention_status"] == "read"
    rendered = json.dumps(
        {
            "mailboxes": mailboxes.model_dump(mode="json"),
            "search": search.model_dump(mode="json"),
            "fetched": fetched.model_dump(mode="json"),
            "marked": marked.model_dump(mode="json"),
            "marked_seen": marked_seen.model_dump(mode="json"),
        }
    )
    assert "imap-secret" not in rendered
    all_uid_calls = [call for instance in _FakeIMAPSSL.instances for call in instance.uid_calls]
    assert ("SEARCH", None, "UNSEEN", "SINCE", "01-May-2026") in all_uid_calls
    assert any(call[0] == "FETCH" and call[1] == "3" for call in all_uid_calls)
    assert any(
        call[0] == "STORE" and call[1] == "3" and call[2] == "-FLAGS" for call in all_uid_calls
    )
    assert any(
        call[0] == "STORE" and call[1] == "5" and call[2] == "+FLAGS" for call in all_uid_calls
    )

    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
    )
    assert messages.items[0].data_json["provider_key"] == "imap"
    assert messages.items[0].data_json["attention_status"] == "read"


def test_imap_validate_rejects_open_criteria_and_fields(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id)

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="communications.imap.messages.search",
        input_json={
            "mailbox_ref": "support",
            "criteria": {"unknown": "x", "from": "bad\nvalue"},
            "limit": 999,
        },
        credential_ref=credential_ref,
    )

    codes = {item.code for item in validation.issues}
    assert {"additional_property", "range", "forbidden", "validation_error"} & codes
