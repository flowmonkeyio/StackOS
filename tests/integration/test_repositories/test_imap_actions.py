"""IMAP connector tests through the generic action executor."""

from __future__ import annotations

import asyncio
import hashlib
import imaplib
import json
import socket
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.imap import ImapActionConnector
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.resources import ArtifactRepository, ResourceRepository
from tests.helpers.finance_workspace import (
    digest as finance_digest,
)
from tests.helpers.finance_workspace import (
    initialize as initialize_finance,
)
from tests.helpers.finance_workspace import (
    read_document,
    write_document,
)
from tests.helpers.finance_workspace import (
    record as finance_record,
)


def _raw_message() -> bytes:
    message = EmailMessage()
    message["Subject"] = "Need help"
    message["From"] = "customer@example.test"
    message["To"] = "support@example.test"
    message["Date"] = "Sat, 23 May 2026 12:00:00 +0000"
    message["Message-ID"] = "<m1@example.test>"
    message.set_content("Hello from IMAP")
    return message.as_bytes()


def _raw_multipart_message() -> bytes:
    message = EmailMessage()
    message["Subject"] = "Receipt detail that must not reach action audit"
    message["From"] = "receipt-sender@example.test"
    message["To"] = "receipts@example.test"
    message["Message-ID"] = "<receipt-private@example.test>"
    message.set_content("Private receipt body")
    message.add_attachment(
        b"%PDF-1.7 receipt attachment bytes",
        maintype="application",
        subtype="pdf",
        filename="../../untrusted-receipt.pdf",
    )
    return message.as_bytes()


def _raw_duplicate_filename_message() -> bytes:
    message = EmailMessage()
    message.set_content("duplicate names are untrusted")
    for payload in (b"first", b"second"):
        message.add_attachment(
            payload,
            maintype="application",
            subtype="octet-stream",
            filename="../../same-name.bin",
        )
    return message.as_bytes()


def _raw_message_with_attachments(count: int) -> bytes:
    message = EmailMessage()
    message.set_content("receipt batch")
    for ordinal in range(count):
        message.add_attachment(
            f"receipt-{ordinal}".encode(),
            maintype="application",
            subtype="octet-stream",
            filename=f"untrusted-{ordinal}.bin",
        )
    return message.as_bytes()


def _raw_binary_attachment(payload: bytes, *, encoding: str = "binary") -> bytes:
    return (
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Disposition: attachment; filename=hostile-name.bin\r\n"
        + f"Content-Transfer-Encoding: {encoding}\r\n\r\n".encode()
        + payload
    )


def _raw_binary_multipart(payloads: list[bytes]) -> bytes:
    root = EmailMessage()
    root.make_mixed()
    for ordinal, payload in enumerate(payloads, start=1):
        part = EmailMessage()
        part["Content-Type"] = "application/octet-stream"
        part["Content-Disposition"] = f"attachment; filename=hostile-{ordinal}.bin"
        part["Content-Transfer-Encoding"] = "binary"
        part.set_payload(payload)
        root.attach(part)
    return root.as_bytes()


def _raw_nested_multipart(depth: int) -> bytes:
    root = EmailMessage()
    root.make_mixed()
    current = root
    for _ in range(depth):
        child = EmailMessage()
        child.make_mixed()
        current.attach(child)
        current = child
    return root.as_bytes()


def _raw_many_mime_parts(count: int) -> bytes:
    root = EmailMessage()
    root.make_mixed()
    for index in range(count):
        child = EmailMessage()
        child.set_content(f"part-{index}")
        root.attach(child)
    return root.as_bytes()


class _FakeIMAP:
    instances: ClassVar[list[_FakeIMAP]] = []
    raw_message: ClassVar[bytes] = _raw_message()
    reported_size: ClassVar[int | None] = None
    uidvalidity: ClassVar[str] = "777"
    starttls_error: ClassVar[Exception | None] = None

    def __init__(
        self,
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ssl_context = ssl_context
        self.starttls_context: ssl.SSLContext | None = None
        self.login_args: tuple[str, str] | None = None
        self.selected: list[tuple[str, bool]] = []
        self.uid_calls: list[tuple[Any, ...]] = []
        self.starttls_called = False
        self.starttls_attempted = False
        self.logout_called = False
        self.close_called = False
        self.seen_by_uid: dict[str, bool] = {}
        self.__class__.instances.append(self)

    def starttls(self, *, ssl_context: ssl.SSLContext | None = None) -> None:
        self.starttls_attempted = True
        if self.starttls_error is not None:
            raise self.starttls_error
        self.starttls_context = ssl_context
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
        return ("UIDVALIDITY", [self.uidvalidity.encode()])

    def uid(self, *args: Any) -> tuple[str, list[Any]]:
        self.uid_calls.append(args)
        if args[0] == "SEARCH":
            return ("OK", [b"3 5"])
        if args[0] == "FETCH":
            if args[2] == "(UID FLAGS)":
                flag = "\\Seen" if self.seen_by_uid.get(args[1], True) else ""
                return ("OK", [f"3 (UID {args[1]} FLAGS ({flag}))".encode()])
            assert args[1] == "3"
            raw = self.raw_message
            reported_size = self.reported_size if self.reported_size is not None else len(raw)
            if "BODY.PEEK[]" not in args[2]:
                return (
                    "OK",
                    [f"3 (UID 3 RFC822.SIZE {reported_size})".encode()],
                )
            assert "BODY.PEEK" in args[2]
            return (
                "OK",
                [
                    (
                        (
                            f"3 (UID 3 FLAGS (\\Seen) RFC822.SIZE {reported_size} "
                            f"BODY[] {{{len(raw)}}}"
                        ).encode(),
                        raw,
                    )
                ],
            )
        if args[0] == "STORE":
            self.seen_by_uid[args[1]] = args[2] == "+FLAGS"
            return ("OK", [b"3 (FLAGS (\\Seen))"])
        raise AssertionError(f"unexpected uid call {args!r}")

    def close(self) -> None:
        self.close_called = True

    def logout(self) -> None:
        self.logout_called = True


class _FakeIMAPSSL(_FakeIMAP):
    instances: ClassVar[list[_FakeIMAPSSL]] = []


def _credential_ref(
    session: Session,
    project_id: int,
    *,
    tls_mode: str = "ssl",
    display_name: str = "IMAP - Default",
) -> str:
    ActionRepository(session).describe(action_ref="communications.imap.mailbox.list")
    return (
        AuthRepository(session)
        .store_credential(
            provider_key="imap",
            display_name=display_name,
            fields={
                "password": "imap-secret",
                "host": "imap.example.test",
                "port": 993 if tls_mode == "ssl" else 143,
                "tls_mode": tls_mode,
                "username": "support@example.test",
                "default_mailbox": "INBOX",
                "mailbox_refs": {"support": "Support"},
                "search_limit": 50,
            },
            attach_project_id=project_id,
        )
        .data.credential_ref
    )


def test_imap_actions_are_registered(session: Session) -> None:
    repo = ActionRepository(session)

    for action_ref, operation in {
        "communications.imap.mailbox.list": "mailbox.list",
        "communications.imap.messages.search": "messages.search",
        "communications.imap.message.fetch": "message.fetch",
        "communications.imap.message.export": "message.export",
        "communications.imap.message.export.cleanup": "message.export.cleanup",
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
    assert marked_seen.output_json["store_applied"] is True
    assert "store_response" not in marked_seen.output_json
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
    cursors = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-cursor",
    )
    assert cursors.items[0].data_json["last_observed_uid"] == 5
    assert "last_seen_uid" not in cursors.items[0].data_json
    search_call = session.get(ActionCall, search.action_call.id)
    assert search_call is not None
    assert search_call.metadata_json == {
        "vendor": "imap",
        "operation": "messages.search",
        "tls_mode": "ssl",
    }
    search_audit = json.dumps(
        {"response": search_call.response_json, "metadata": search_call.metadata_json},
        sort_keys=True,
    )
    assert "imap.example.test" not in search_audit
    assert "support@example.test" not in search_audit
    assert "imap-secret" not in search_audit
    mark_call = session.get(ActionCall, marked_seen.action_call.id)
    assert mark_call is not None
    assert mark_call.metadata_json == {
        "vendor": "imap",
        "operation": "message.mark_seen",
        "tls_mode": "ssl",
        "acknowledgement": True,
    }
    assert set(mark_call.response_json or {}) == {
        "provider",
        "operation",
        "status",
        "mailbox_ref",
        "uid",
        "uidvalidity",
        "attention_status",
        "store_applied",
    }


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


def test_imap_evidence_export_stages_exact_mime_without_audit_or_resource_leakage(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    raw = _raw_multipart_message()
    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "raw_message", raw)
    monkeypatch.setattr(_FakeIMAPSSL, "reported_size", None)
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session, asset_dir=tmp_path)

    exported = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.export",
            input_json={"mailbox_ref": "support", "uid": 3},
            credential_ref=credential_ref,
        )
    ).data
    output = exported.output_json

    assert output["transfer_kind"] == "imap-staged-evidence.v1"
    assert output["source_identity"] == {
        "provider_key": "imap",
        "account_ref": credential_ref,
        "mailbox_ref": "imap-mailbox:Support",
        "uidvalidity": "777",
        "uid": 3,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert output["raw_mime"] == {
        "path": "original.eml",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert output["attachment_count"] == 1
    assert output["attachments"][0]["path"] == "attachment-001"
    assert output["attachments"][0]["media_type"] == "application/pdf"
    assert "mailbox_name" not in output
    assert "host_handoff" not in output

    staged = tmp_path / output["staging_uri"].removeprefix("/generated-assets/")
    assert staged.is_dir()
    assert staged.parent.name == f"project-{project_id}"
    assert staged.name == output["transfer_id"]
    assert (staged / "original.eml").read_bytes() == raw
    assert (staged / "attachment-001").read_bytes() == b"%PDF-1.7 receipt attachment bytes"

    client = _FakeIMAPSSL.instances[-1]
    assert client.selected == [("Support", True)]
    assert client.uid_calls[:2] == [
        ("FETCH", "3", "(UID RFC822.SIZE)"),
        ("FETCH", "3", "(UID RFC822.SIZE BODY.PEEK[])"),
    ]
    assert all(call[0] != "STORE" for call in client.uid_calls)
    assert (
        ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        .items
        == []
    )
    assert ArtifactRepository(session).query(project_id=project_id).items == []

    call = session.get(ActionCall, exported.action_call.id)
    assert call is not None
    audit = json.dumps(
        {"response": call.response_json, "metadata": call.metadata_json, "error": call.error},
        sort_keys=True,
    )
    for private_value in (
        "Receipt detail that must not reach action audit",
        "receipt-sender@example.test",
        "receipt-private@example.test",
        "../../untrusted-receipt.pdf",
        "Private receipt body",
    ):
        assert private_value not in audit

    cleaned = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.export.cleanup",
            input_json={"transfer_id": output["transfer_id"]},
        )
    ).data
    assert cleaned.output_json["cleanup_status"] == "deleted"
    assert not staged.exists()


def test_imap_finance_host_handoff_stores_and_verifies_before_epoch_qualified_ack(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the documented host-owned local-json handoff with fake IMAP only."""
    import stackos.actions.imap as imap_module

    raw = _raw_multipart_message()
    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "raw_message", raw)
    credential_ref = _credential_ref(session, project_id)
    asset_root = tmp_path / "generated-assets"
    repo = ActionRepository(session, asset_dir=asset_root)

    exported = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.export",
            input_json={"mailbox_ref": "support", "uid": 3},
            credential_ref=credential_ref,
        )
    ).data
    manifest = exported.output_json
    staged = asset_root / manifest["staging_uri"].removeprefix("/generated-assets/")
    assert staged.is_dir()

    # The trusted host maps the safe URI to its own generated-assets mount. The
    # connector has not acknowledged the source or made an evidence record.
    export_client = _FakeIMAPSSL.instances[-1]
    assert export_client.uid_calls == [
        ("FETCH", "3", "(UID RFC822.SIZE)"),
        ("FETCH", "3", "(UID RFC822.SIZE BODY.PEEK[])"),
    ]
    assert all(call[0] != "STORE" for call in export_client.uid_calls)
    assert (
        ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
        )
        .items
        == []
    )
    assert ArtifactRepository(session).query(project_id=project_id).items == []

    # Emulate the host's contained, atomic writes to its external finance/
    # workspace. This intentionally does not give StackOS filesystem authority.
    finance_root = tmp_path / "finance"
    finance_index = initialize_finance(finance_root)
    prior_hash = finance_digest(finance_index)
    document = read_document(finance_index)
    attachment_dir = finance_root / "attachments" / "2026" / "09"
    attachment_dir.mkdir(parents=True)

    host_files = [
        ("original.eml", "rcpt_imap_777_3-original.eml", manifest["raw_mime"]),
        (
            "attachment-001",
            "rcpt_imap_777_3-receipt.pdf",
            manifest["attachments"][0],
        ),
    ]
    relative_paths: list[str] = []
    for staged_name, final_name, file_manifest in host_files:
        final_path = attachment_dir / final_name
        temporary_path = final_path.with_name(f".{final_name}.tmp")
        temporary_path.write_bytes((staged / staged_name).read_bytes())
        assert len(temporary_path.read_bytes()) == file_manifest["bytes"]
        assert hashlib.sha256(temporary_path.read_bytes()).hexdigest() == file_manifest["sha256"]
        temporary_path.replace(final_path)
        relative_paths.append(final_path.relative_to(finance_root).as_posix())

    source = manifest["source_identity"]
    source_identity = "/".join(
        str(source[key])
        for key in ("provider_key", "mailbox_ref", "uidvalidity", "uid", "content_sha256")
    )
    document["attachments"] = [
        finance_record(
            f"att_{ordinal}",
            "retained",
            source_ref="source_imap_777_3",
            role="original",
            relative_path=path,
            sha256=file_manifest["sha256"],
            bytes=file_manifest["bytes"],
            original_filename="untrusted fixture original",
            media_type="message/rfc822" if ordinal == 1 else file_manifest["media_type"],
        )
        for ordinal, (path, (_staged_name, _final_name, file_manifest)) in enumerate(
            zip(relative_paths, host_files, strict=True), start=1
        )
    ]
    document["sources"] = [
        finance_record(
            "source_imap_777_3",
            "retained",
            source_kind="imap-email",
            source_identity=source_identity,
            attachment_refs=["att_1", "att_2"],
            coverage_state="complete",
        )
    ]
    document["receipts"] = [
        finance_record(
            "rcpt_imap_777_3",
            "stored",
            source_ref="source_imap_777_3",
            attachment_refs=["att_1", "att_2"],
            gaps=[
                {
                    "field": "amount",
                    "reason": "Custody test does not infer financial facts.",
                    "source_refs": ["source_imap_777_3"],
                }
            ],
        )
    ]
    document["revision"] = 1
    write_document(finance_index, document, prior_hash)

    # Re-read the host index and final evidence before acknowledging the source.
    index_contents = read_document(finance_index)
    assert index_contents["receipts"][0]["record_id"] == "rcpt_imap_777_3"
    assert index_contents["receipts"][0]["status"] == "stored"
    for relative_path, (_staged_name, _final_name, file_manifest) in zip(
        relative_paths, host_files, strict=True
    ):
        final_path = finance_root / relative_path
        assert final_path.is_file()
        assert hashlib.sha256(final_path.read_bytes()).hexdigest() == file_manifest["sha256"]
        assert any(row["relative_path"] == relative_path for row in index_contents["attachments"])

    marked = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.mark_seen",
            input_json={
                "mailbox_ref": "support",
                "uid": source["uid"],
                "expected_uidvalidity": source["uidvalidity"],
            },
            credential_ref=credential_ref,
        )
    ).data
    assert marked.output_json["store_applied"] is True
    assert _FakeIMAPSSL.instances[-1].uid_calls == [
        ("STORE", "3", "+FLAGS", "(\\Seen)"),
        ("FETCH", "3", "(UID FLAGS)"),
    ]

    cleaned = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="communications.imap.message.export.cleanup",
            input_json={"transfer_id": manifest["transfer_id"]},
        )
    ).data
    assert cleaned.output_json["cleanup_status"] == "deleted"
    assert not staged.exists()

    # `mark_seen` can retain one safe lifecycle event, but the finance handoff
    # never creates an evidence resource, artifact, or raw-content audit entry.
    records = ResourceRepository(session).query(project_id=project_id).records
    assert [(record.plugin_slug, record.resource_key) for record in records] == [
        ("communications", "communication-event"),
    ]
    assert ArtifactRepository(session).query(project_id=project_id).items == []
    audits = [
        session.get(ActionCall, result.action_call.id) for result in (exported, marked, cleaned)
    ]
    assert all(audit is not None for audit in audits)
    serialized_audits = json.dumps(
        [
            {
                "response": audit.response_json,
                "metadata": audit.metadata_json,
                "error": audit.error,
            }
            for audit in audits
            if audit is not None
        ],
        sort_keys=True,
    )
    for private_value in (
        raw.decode("utf-8"),
        "Receipt detail that must not reach action audit",
        "receipt-sender@example.test",
        "receipt-private@example.test",
        "Private receipt body",
        "../../untrusted-receipt.pdf",
    ):
        assert private_value not in serialized_audits


def test_imap_evidence_export_uses_fixed_paths_for_hostile_duplicate_filenames(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    raw = _raw_duplicate_filename_message()
    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "raw_message", raw)
    credential_ref = _credential_ref(session, project_id)
    exported = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="communications.imap.message.export",
            input_json={"mailbox_ref": "support", "uid": 3},
            credential_ref=credential_ref,
        )
    ).data.output_json
    assert [attachment["path"] for attachment in exported["attachments"]] == [
        "attachment-001",
        "attachment-002",
    ]
    staged = tmp_path / exported["staging_uri"].removeprefix("/generated-assets/")
    assert (staged / "attachment-001").read_bytes() == b"first"
    assert (staged / "attachment-002").read_bytes() == b"second"
    assert "same-name.bin" not in json.dumps(exported)


def test_imap_evidence_export_rejects_tls_none_before_provider_access(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    credential_ref = _credential_ref(session, project_id, tls_mode="none")

    with pytest.raises(ConflictError) as denied:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.imap.message.export",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )

    call = session.get(ActionCall, denied.value.data["action_call_id"])
    assert call is not None
    assert call.response_json == {"status": "rejected", "category": "tls_required"}
    assert _FakeIMAPSSL.instances == []


def test_imap_evidence_export_uses_verified_ssl_and_starttls_contexts(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    ssl_credential = _credential_ref(session, project_id)
    asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="communications.imap.message.export",
            input_json={"mailbox_ref": "support", "uid": 3},
            credential_ref=ssl_credential,
        )
    )
    ssl_client = _FakeIMAPSSL.instances[-1]
    assert ssl_client.ssl_context is not None
    assert ssl_client.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert ssl_client.ssl_context.check_hostname is True

    _FakeIMAP.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4", _FakeIMAP)
    starttls_credential = _credential_ref(
        session,
        project_id,
        tls_mode="starttls",
        display_name="IMAP - STARTTLS",
    )
    asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="communications.imap.message.export",
            input_json={"mailbox_ref": "support", "uid": 3},
            credential_ref=starttls_credential,
        )
    )
    starttls_client = _FakeIMAP.instances[-1]
    assert starttls_client.starttls_called is True
    assert starttls_client.starttls_context is not None
    assert starttls_client.starttls_context.verify_mode == ssl.CERT_REQUIRED
    assert starttls_client.starttls_context.check_hostname is True


def test_imap_evidence_export_fails_when_verified_starttls_cannot_start(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAP.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4", _FakeIMAP)
    monkeypatch.setattr(_FakeIMAP, "starttls_error", ssl.SSLError("certificate rejected"))
    credential_ref = _credential_ref(
        session,
        project_id,
        tls_mode="starttls",
        display_name="IMAP - Broken STARTTLS",
    )

    with pytest.raises(ConflictError) as denied:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.imap.message.export",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )

    client = _FakeIMAP.instances[-1]
    assert client.starttls_attempted is True
    assert client.starttls_called is False
    assert client.selected == []
    call = session.get(ActionCall, denied.value.data["action_call_id"])
    assert call is not None
    assert call.response_json == {
        "status": "rejected",
        "category": "export_failed",
        "error_category": "SSLError",
    }


def test_imap_evidence_export_preflights_size_and_leaves_oversize_message_unseen(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "raw_message", b"small")
    monkeypatch.setattr(_FakeIMAPSSL, "reported_size", 10 * 1024 * 1024 + 1)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as denied:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.imap.message.export",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )

    client = _FakeIMAPSSL.instances[-1]
    assert client.uid_calls == [("FETCH", "3", "(UID RFC822.SIZE)")]
    assert all(call[0] != "STORE" for call in client.uid_calls)
    call = session.get(ActionCall, denied.value.data["action_call_id"])
    assert call is not None
    assert call.response_json == {
        "status": "rejected",
        "category": "oversize",
        "size_bytes": 10 * 1024 * 1024 + 1,
        "max_bytes": 10 * 1024 * 1024,
    }


def test_imap_export_requires_numeric_uidvalidity_and_unambiguous_fetch_tuples(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "uidvalidity", "0")
    credential_ref = _credential_ref(session, project_id)
    with pytest.raises(ConflictError) as invalid_epoch:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.imap.message.export",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )
    assert _FakeIMAPSSL.instances[-1].uid_calls == []
    invalid_call = session.get(ActionCall, invalid_epoch.value.data["action_call_id"])
    assert invalid_call is not None
    assert invalid_call.response_json == {"status": "rejected", "category": "uidvalidity_invalid"}

    full = (b"3 (UID 3 RFC822.SIZE 4 BODY[] {4}", b"data")
    assert imap_module._export_fetch_tuple(
        [b"3 (UID 3 RFC822.SIZE 4)"], uid=3, require_literal=False
    ) == (4, None)
    with pytest.raises(ActionConnectorError) as ambiguous:
        imap_module._export_fetch_tuple([full, full], uid=3, require_literal=True)
    assert ambiguous.value.output_json["category"] == "fetch_ambiguous"
    with pytest.raises(ActionConnectorError) as mismatched:
        imap_module._export_fetch_tuple(
            [(b"4 (UID 4 RFC822.SIZE 4 BODY[] {4}", b"data")],
            uid=3,
            require_literal=True,
        )
    assert mismatched.value.output_json["category"] == "fetch_mismatch"
    with pytest.raises(ActionConnectorError) as truncated:
        imap_module._export_fetch_tuple(
            [(b"3 (UID 3 RFC822.SIZE 4 BODY[] {4}", b"dat")],
            uid=3,
            require_literal=True,
        )
    assert truncated.value.output_json["category"] == "fetch_truncated"


def test_imap_evidence_export_rejects_malformed_mime_without_staging_or_acknowledgement(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    malformed = (
        b"Content-Type: application/pdf; name=private.pdf\r\n"
        b"Content-Disposition: attachment; filename=private.pdf\r\n"
        b"Content-Transfer-Encoding: base64\r\n\r\n%%%%"
    )
    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "raw_message", malformed)
    monkeypatch.setattr(_FakeIMAPSSL, "reported_size", None)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as denied:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.imap.message.export",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )

    assert all(call[0] != "STORE" for call in _FakeIMAPSSL.instances[-1].uid_calls)
    call = session.get(ActionCall, denied.value.data["action_call_id"])
    assert call is not None
    assert call.response_json == {"status": "rejected", "category": "malformed"}
    assert list(tmp_path.glob("imap-transfers/project-*/*")) == []


def test_imap_evidence_export_enforces_attachment_count_before_staging(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "raw_message", _raw_message_with_attachments(21))
    monkeypatch.setattr(_FakeIMAPSSL, "reported_size", None)
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError) as denied:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.imap.message.export",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )

    client = _FakeIMAPSSL.instances[-1]
    assert [call[0] for call in client.uid_calls] == ["FETCH", "FETCH"]
    assert all(call[0] != "STORE" for call in client.uid_calls)
    call = session.get(ActionCall, denied.value.data["action_call_id"])
    assert call is not None
    assert call.response_json == {
        "status": "rejected",
        "category": "attachment_count_exceeded",
        "max_attachments": 20,
    }
    assert list(tmp_path.glob("imap-transfers/project-*/*")) == []


def test_imap_evidence_mime_limits_and_nested_attachment_nodes_are_safe() -> None:
    import stackos.actions.imap as imap_module

    with pytest.raises(ActionConnectorError) as per_attachment:
        imap_module._export_attachments(_raw_binary_attachment(b"x" * (8 * 1024 * 1024 + 1)))
    assert per_attachment.value.output_json["category"] == "attachment_oversize"

    aggregate = _raw_binary_multipart([b"a" * (6 * 1024 * 1024), b"b" * (6 * 1024 * 1024)])
    with pytest.raises(ActionConnectorError) as aggregate_limit:
        imap_module._export_attachments(aggregate)
    assert aggregate_limit.value.output_json["category"] == "attachment_total_oversize"

    with pytest.raises(ActionConnectorError) as depth_limit:
        imap_module._export_attachments(_raw_nested_multipart(21))
    assert depth_limit.value.output_json["category"] == "mime_structure_exceeded"
    with pytest.raises(ActionConnectorError) as parts_limit:
        imap_module._export_attachments(_raw_many_mime_parts(200))
    assert parts_limit.value.output_json["category"] == "mime_structure_exceeded"
    with pytest.raises(ActionConnectorError) as encoding:
        imap_module._export_attachments(_raw_binary_attachment(b"data", encoding="x-rot13"))
    assert encoding.value.output_json["category"] == "unsupported_encoding"

    outer = EmailMessage()
    outer.make_mixed()
    attached_message = EmailMessage()
    attached_message.set_content("nested receipt")
    outer.add_attachment(attached_message, filename="one.eml")
    multipart_attachment = EmailMessage()
    multipart_attachment.make_mixed()
    multipart_attachment["Content-Disposition"] = "attachment; filename=two.eml"
    outer.attach(multipart_attachment)
    staged = imap_module._export_attachments(outer.as_bytes())
    assert [media_type for media_type, _payload in staged] == ["message/rfc822", "multipart/mixed"]
    assert all(payload for _media_type, payload in staged)
    assert imap_module._safe_media_type(f"{'a' * 100}/{'b' * 100}") == "application/octet-stream"


def test_imap_mailbox_ref_output_is_runtime_bounded() -> None:
    import stackos.actions.imap as imap_module

    assert imap_module._validated_mailbox_name("x" * 240) == "x" * 240
    with pytest.raises(ValidationError, match="invalid mailbox name"):
        imap_module._validated_mailbox_name("x" * 241)


def test_imap_evidence_cleanup_rejects_paths_and_cannot_cross_project_or_follow_symlinks(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    connector = ImapActionConnector()
    invalid = connector.validate(
        ActionConnectorRequest(
            project_id=project_id,
            plugin_slug="communications",
            action_key="imap.message.export.cleanup",
            action_ref="communications.imap.message.export.cleanup",
            provider_key="imap",
            operation="message.export.cleanup",
            input_json={"transfer_id": "../not-a-transfer"},
            config_json={},
            asset_dir=tmp_path,
        )
    )
    assert invalid[0].path == "$.transfer_id"

    transfer_id = "a" * 32
    transfer_dir = tmp_path / "imap-transfers" / f"project-{project_id}" / transfer_id
    transfer_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "keep.txt"
    protected.write_text("keep", encoding="utf-8")
    (transfer_dir / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ActionConnectorError) as unsafe:
        asyncio.run(
            connector.execute(
                ActionConnectorRequest(
                    project_id=project_id,
                    plugin_slug="communications",
                    action_key="imap.message.export.cleanup",
                    action_ref="communications.imap.message.export.cleanup",
                    provider_key="imap",
                    operation="message.export.cleanup",
                    input_json={"transfer_id": transfer_id},
                    config_json={},
                    asset_dir=tmp_path,
                )
            )
        )
    assert unsafe.value.output_json["category"] == "unsafe_transfer"
    assert protected.read_text(encoding="utf-8") == "keep"
    assert transfer_dir.exists()

    with pytest.raises(ActionConnectorError) as isolated:
        asyncio.run(
            connector.execute(
                ActionConnectorRequest(
                    project_id=project_id + 1,
                    plugin_slug="communications",
                    action_key="imap.message.export.cleanup",
                    action_ref="communications.imap.message.export.cleanup",
                    provider_key="imap",
                    operation="message.export.cleanup",
                    input_json={"transfer_id": transfer_id},
                    config_json={},
                    asset_dir=tmp_path,
                )
            )
        )
    assert isolated.value.output_json["category"] == "not_found"
    assert transfer_dir.exists()


def test_imap_evidence_cleanup_fails_closed_for_transfer_root_symlink_and_delete_error(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    transfer_id = "b" * 32
    outside = tmp_path / "outside-transfer-root"
    outside.mkdir()
    (tmp_path / "imap-transfers").symlink_to(outside, target_is_directory=True)
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="communications",
        action_key="imap.message.export.cleanup",
        action_ref="communications.imap.message.export.cleanup",
        provider_key="imap",
        operation="message.export.cleanup",
        input_json={"transfer_id": transfer_id},
        config_json={},
        asset_dir=tmp_path,
    )
    with pytest.raises(ActionConnectorError) as unsafe_root:
        asyncio.run(ImapActionConnector().execute(request))
    assert unsafe_root.value.output_json["category"] == "unsafe_staging"
    assert outside.exists()

    assets = tmp_path / "assets"
    transfer_dir = assets / "imap-transfers" / f"project-{project_id}" / transfer_id
    transfer_dir.mkdir(parents=True)

    def fail_rmtree(_path: Path) -> None:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(imap_module.shutil, "rmtree", fail_rmtree)
    with pytest.raises(ActionConnectorError) as delete_error:
        asyncio.run(
            ImapActionConnector().execute(
                ActionConnectorRequest(
                    project_id=project_id,
                    plugin_slug="communications",
                    action_key="imap.message.export.cleanup",
                    action_ref="communications.imap.message.export.cleanup",
                    provider_key="imap",
                    operation="message.export.cleanup",
                    input_json={"transfer_id": transfer_id},
                    config_json={},
                    asset_dir=assets,
                )
            )
        )
    assert delete_error.value.output_json["category"] == "cleanup_failed"
    assert delete_error.value.output_json["transfer_id"] == transfer_id
    assert transfer_dir.exists()

    dangling_transfer_id = "c" * 32
    dangling_transfer = assets / "imap-transfers" / f"project-{project_id}" / dangling_transfer_id
    dangling_transfer.symlink_to(assets / "missing-transfer")
    with pytest.raises(ActionConnectorError) as unsafe_transfer:
        asyncio.run(
            ImapActionConnector().execute(
                ActionConnectorRequest(
                    project_id=project_id,
                    plugin_slug="communications",
                    action_key="imap.message.export.cleanup",
                    action_ref="communications.imap.message.export.cleanup",
                    provider_key="imap",
                    operation="message.export.cleanup",
                    input_json={"transfer_id": dangling_transfer_id},
                    config_json={},
                    asset_dir=assets,
                )
            )
        )
    assert unsafe_transfer.value.output_json["category"] == "unsafe_transfer"


def test_imap_evidence_staging_write_failure_leaves_no_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected atomic replace failure")

    monkeypatch.setattr(imap_module.os, "replace", fail_replace)
    with pytest.raises(ActionConnectorError) as failed:
        imap_module._stage_export_file(staging_dir, "original.eml", b"raw-message")

    assert failed.value.output_json["category"] == "staging_unavailable"
    assert not (staging_dir / "original.eml").exists()
    assert list(staging_dir.iterdir()) == []


def test_imap_partial_export_cleanup_failure_returns_recoverable_transfer_id(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "raw_message", _raw_multipart_message())
    original_stage = imap_module._stage_export_file
    staged_files = 0

    def fail_second_stage(directory: Path, name: str, payload: bytes) -> dict[str, Any]:
        nonlocal staged_files
        staged_files += 1
        if staged_files == 2:
            raise OSError("injected attachment staging failure")
        return original_stage(directory, name, payload)

    monkeypatch.setattr(imap_module, "_stage_export_file", fail_second_stage)

    def fail_rmtree(_path: Path) -> None:
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(imap_module.shutil, "rmtree", fail_rmtree)
    credential_ref = _credential_ref(session, project_id)
    with pytest.raises(ConflictError) as failed:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="communications.imap.message.export",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )
    call = session.get(ActionCall, failed.value.data["action_call_id"])
    assert call is not None
    assert call.response_json is not None
    assert call.response_json["category"] == "partial_export_cleanup_failed"
    assert call.response_json["recovery_required"] is True
    transfer_id = call.response_json["transfer_id"]
    transfer_dir = tmp_path / "imap-transfers" / f"project-{project_id}" / transfer_id
    assert transfer_dir.exists()


def test_imap_mark_seen_requires_matching_uidvalidity(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.imap as imap_module

    _FakeIMAPSSL.instances.clear()
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    monkeypatch.setattr(_FakeIMAPSSL, "uidvalidity", "778")
    credential_ref = _credential_ref(session, project_id)

    with pytest.raises(ConflictError):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.imap.message.mark_seen",
                input_json={
                    "mailbox_ref": "support",
                    "uid": 3,
                    "expected_uidvalidity": "777",
                },
                credential_ref=credential_ref,
            )
        )

    assert all(call[0] != "STORE" for call in _FakeIMAPSSL.instances[-1].uid_calls)


@pytest.mark.parametrize("operation", ["mark_seen", "mark_unseen"])
def test_imap_protocol_cleanup_never_expunge_after_flag_write(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    import stackos.actions.imap as imap_module

    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    credential_ref = _credential_ref(session, project_id)
    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=f"communications.imap.message.{operation}",
            input_json={"mailbox_ref": "support", "uid": 3, "expected_uidvalidity": "777"},
            credential_ref=credential_ref,
        )
    )
    client = _FakeIMAPSSL.instances[-1]
    assert client.logout_called
    assert not client.close_called, "CLOSE can expunge unrelated pre-deleted messages"
    assert ("FETCH", "3", "(UID FLAGS)") in client.uid_calls


@pytest.mark.parametrize(
    "failure_stage,error,reason_code,retryable,tls_mode",
    [
        (
            "connect",
            ConnectionRefusedError("private-server-text"),
            "connection_refused",
            True,
            "ssl",
        ),
        ("connect", socket.gaierror(-2, "private-server-text"), "dns_error", False, "ssl"),
        ("connect", TimeoutError("private-server-text"), "timeout", True, "ssl"),
        ("connect", OSError("private-server-text"), "network_error", True, "ssl"),
        (
            "connect",
            ssl.SSLCertVerificationError("private-server-text"),
            "tls_certificate_error",
            False,
            "ssl",
        ),
        ("tls", ssl.SSLError("private-server-text"), "tls_negotiation_error", False, "starttls"),
        ("login", ssl.SSLEOFError("private-server-text"), "network_error", True, "ssl"),
        ("select", ssl.SSLEOFError("private-server-text"), "network_error", True, "ssl"),
        ("login", ssl.SSLZeroReturnError("private-server-text"), "network_error", True, "ssl"),
        ("select", ssl.SSLZeroReturnError("private-server-text"), "network_error", True, "ssl"),
        (
            "tls",
            imaplib.IMAP4.error("private-server-text"),
            "tls_negotiation_error",
            False,
            "starttls",
        ),
        ("login", imaplib.IMAP4.error("private-server-text"), "login_rejected", False, "ssl"),
        ("select", imaplib.IMAP4.error("private-server-text"), "mailbox_unavailable", False, "ssl"),
        ("select", None, "mailbox_unavailable", False, "ssl"),
        ("login", imaplib.IMAP4.abort("private-server-text"), "protocol_aborted", True, "ssl"),
        ("connect", imaplib.IMAP4.error("private-server-text"), "protocol_error", False, "ssl"),
        ("select", RuntimeError("private-server-text"), "probe_error", False, "ssl"),
    ],
    ids=[
        "refused",
        "dns",
        "timeout",
        "network",
        "certificate",
        "tls-handshake",
        "login-tls-eof",
        "select-tls-eof",
        "login-tls-closed",
        "select-tls-closed",
        "starttls-rejected",
        "login-rejected",
        "select-error",
        "select-rejected",
        "aborted",
        "protocol",
        "unexpected",
    ],
)
def test_imap_health_failure_returns_safe_actionable_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    error: Exception | None,
    reason_code: str,
    retryable: bool,
    tls_mode: str,
) -> None:
    from stackos.integrations.imap import ImapIntegration

    calls: list[str] = []

    class ProbeIMAP:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            calls.append("connect")
            if tls_mode == "ssl":
                context = kwargs["ssl_context"]
                assert context.check_hostname is True
                assert context.verify_mode == ssl.CERT_REQUIRED
            if failure_stage == "connect":
                assert error is not None
                raise error

        def starttls(self, *, ssl_context: ssl.SSLContext) -> None:
            calls.append("tls")
            assert ssl_context.check_hostname is True
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            if failure_stage == "tls":
                assert error is not None
                raise error

        def login(self, *_args: Any) -> None:
            calls.append("login")
            if failure_stage == "login":
                assert error is not None
                raise error

        def select(self, _mailbox: str, *, readonly: bool) -> tuple[str, list[bytes]]:
            calls.append("select")
            assert readonly is True
            if failure_stage == "select" and error is not None:
                raise error
            return "NO", [b"private-server-text"]

        def logout(self) -> None:
            calls.append("logout")

        def close(self) -> None:
            pytest.fail("Health probe must never CLOSE or expunge a mailbox")

        def uid(self, *_args: Any) -> None:
            pytest.fail("Health probe must not fetch messages or change flags")

    monkeypatch.setattr("stackos.integrations.imap.imaplib.IMAP4_SSL", ProbeIMAP)
    monkeypatch.setattr("stackos.integrations.imap.imaplib.IMAP4", ProbeIMAP)

    async def probe() -> dict[str, Any]:
        async with httpx.AsyncClient() as http:
            return await ImapIntegration(
                payload=b"private-password",
                project_id=1,
                http=http,
                host="imap.example.test",
                port=993 if tls_mode == "ssl" else 143,
                tls_mode=tls_mode,
                username="fixture",
            ).test_credentials()

    result = asyncio.run(probe())
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["retryable"] is retryable
    assert result["metadata"] == {
        "stage": "tls" if reason_code.startswith("tls_") else failure_stage,
        "reason_code": reason_code,
    }
    assert result["summary"]
    assert result["next_action"]
    assert "private-server-text" not in json.dumps(result)
    assert "private-password" not in json.dumps(result)
    if failure_stage != "connect":
        assert calls[-1] == "logout"


def test_imap_protocol_health_cleanup_never_close(monkeypatch: pytest.MonkeyPatch) -> None:
    import stackos.integrations.imap as imap_module
    from stackos.integrations.imap import ImapIntegration

    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)

    async def probe() -> dict[str, Any]:
        async with httpx.AsyncClient() as http:
            integration = ImapIntegration(
                payload=b"synthetic-password",
                project_id=1,
                http=http,
                host="imap.example.test",
                port=993,
                tls_mode="ssl",
                username="fixture",
            )
            return await integration.test_credentials()

    assert asyncio.run(probe())["ok"] is True
    assert _FakeIMAPSSL.instances[-1].logout_called
    assert not _FakeIMAPSSL.instances[-1].close_called


@pytest.mark.parametrize(
    "readback",
    [[], [b"3 (UID 99 FLAGS (\\Seen))"], [b"3 (UID 3 FLAGS ())"], None],
    ids=["missing", "wrong-uid", "wrong-flags", "aborted-store"],
)
def test_imap_protocol_flag_write_requires_exact_readback(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch, readback: list[bytes] | None
) -> None:
    import stackos.actions.imap as imap_module

    original_uid = _FakeIMAPSSL.uid

    def uid(client: _FakeIMAPSSL, *args: Any) -> tuple[str, list[Any]]:
        if args[0] == "STORE" and readback is None:
            client.uid_calls.append(args)
            raise ConnectionResetError("fixture connection lost after STORE dispatch")
        if args[0] == "FETCH" and args[2] == "(UID FLAGS)":
            client.uid_calls.append(args)
            assert readback is not None
            return ("OK", readback)
        return original_uid(client, *args)

    monkeypatch.setattr(_FakeIMAPSSL, "uid", uid)
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    credential_ref = _credential_ref(session, project_id)
    with pytest.raises(ConflictError) as failed:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.imap.message.mark_seen",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )
    assert failed.value.data["provider_error"]["outcome_unknown"] is True


@pytest.mark.parametrize("extra_meta", [b"4 (UID 99)", b"4 (UID 3)", b"4 (FLAGS ())"])
def test_imap_protocol_fetch_rejects_ambiguous_or_mismatched_literal(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch, extra_meta: bytes
) -> None:
    import stackos.actions.imap as imap_module

    original_uid = _FakeIMAPSSL.uid

    def uid(client: _FakeIMAPSSL, *args: Any) -> tuple[str, list[Any]]:
        status, data = original_uid(client, *args)
        if args[0] == "FETCH":
            data.append((extra_meta, b"Subject: WRONG MESSAGE\r\n\r\nprivate wrong body"))
        return status, data

    monkeypatch.setattr(_FakeIMAPSSL, "uid", uid)
    monkeypatch.setattr(imap_module.imaplib, "IMAP4_SSL", _FakeIMAPSSL)
    credential_ref = _credential_ref(session, project_id)
    with pytest.raises(ConflictError):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="communications.imap.message.fetch",
                input_json={"mailbox_ref": "support", "uid": 3},
                credential_ref=credential_ref,
            )
        )
