"""Account-local IMAP trust persists without changing daemon-wide trust."""

from __future__ import annotations

import asyncio
import json
import ssl
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

import stackos.actions.imap as imap_actions
import stackos.integrations.imap as imap_integration
from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository
from tests.helpers.imap_tls import certificates


def _fields(**overrides: object) -> dict:
    return {
        "host": "mail.example.test",
        "port": 993,
        "tls_mode": "ssl",
        "username": "synthetic-user",
        "password": "synthetic-password",
        **overrides,
    }


def _account(session: Session, **overrides: object):
    return (
        AuthRepository(session)
        .store_credential(
            provider_key="imap",
            display_name="Synthetic IMAP Account",
            fields=_fields(**overrides),
        )
        .data
    )


def _row(session: Session, ref: str) -> Credential:
    return session.exec(select(Credential).where(Credential.credential_ref == ref)).one()


def test_imap_account_ca_persists_across_repository_sessions_and_edits(session: Session) -> None:
    ca, _leaf, _key = certificates()
    account = _account(session, tls_ca_pem=ca)
    integration_id = _row(session, account.credential_ref).integration_credential_id
    assert integration_id is not None
    payload_before = IntegrationCredentialRepository(session).get_decrypted(integration_id)
    with Session(session.get_bind()) as reopened:
        repo = AuthRepository(reopened)
        edit = repo.get_credential_edit_state(credential_ref=account.credential_ref)
        assert edit.values["tls_ca_pem"].strip() == ca.strip()
        assert "password" not in edit.values
        assert edit.secret_present == {"password": True}
        repo.update_credential(
            credential_ref=account.credential_ref, fields={}, display_name="Renamed IMAP"
        )
        assert (
            _row(reopened, account.credential_ref).config_json["tls_ca_pem"].strip() == ca.strip()
        )
        replacement, _leaf, _key = certificates()
        repo.update_credential(
            credential_ref=account.credential_ref,
            fields={"tls_ca_pem": replacement},
            display_name=None,
        )
        assert (
            _row(reopened, account.credential_ref).config_json["tls_ca_pem"].strip()
            == replacement.strip()
        )
        payload = IntegrationCredentialRepository(reopened).get_decrypted(integration_id)
        assert json.loads(payload)["password"] == "synthetic-password"
        assert account.credential_ref == _row(reopened, account.credential_ref).credential_ref
        # CA configuration is public Account data, never appended to the secret payload.
        assert "CERTIFICATE" not in payload.decode()
        assert payload == payload_before


@pytest.mark.parametrize("cleared", ["", " \n ", None])
def test_imap_account_ca_can_be_explicitly_cleared(session: Session, cleared: object) -> None:
    ca, _leaf, _key = certificates()
    account = _account(session, tls_ca_pem=ca)
    AuthRepository(session).update_credential(
        credential_ref=account.credential_ref,
        fields={"tls_ca_pem": cleared},
        display_name=None,
    )
    assert "tls_ca_pem" not in _row(session, account.credential_ref).config_json


@pytest.mark.parametrize(
    "invalid_kind", ["junk", "private_key", "trailing", "leaf", "oversized", "number", "malformed"]
)
def test_imap_account_rejects_invalid_trust_without_changing_saved_ca(
    session: Session, invalid_kind: str
) -> None:
    ca, leaf, key = certificates()
    invalid = {
        "junk": "NOT A CERTIFICATE",
        "private_key": ca + key.decode(),
        "trailing": ca + "untrusted trailing data",
        "leaf": leaf,
        "oversized": ca * 200,
        "number": 42,
        "malformed": "-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----",
    }[invalid_kind]
    with pytest.raises(ValidationError) as creation:
        _account(session, tls_ca_pem=invalid)
    assert creation.value.data["field"] == "tls_ca_pem"
    assert "BEGIN" not in str(creation.value)
    assert session.exec(select(Credential).where(Credential.provider_key == "imap")).first() is None
    account = _account(session, tls_ca_pem=ca)
    with pytest.raises(ValidationError) as update:
        AuthRepository(session).update_credential(
            credential_ref=account.credential_ref,
            fields={"tls_ca_pem": invalid},
            display_name=None,
        )
    assert update.value.data["field"] == "tls_ca_pem"
    assert _row(session, account.credential_ref).config_json["tls_ca_pem"].strip() == ca.strip()


def test_imap_context_adds_only_account_ca_to_default_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    ca, _leaf, _key = certificates()
    other_ca, _leaf, _key = certificates()
    default = ssl.create_default_context()
    custom = imap_integration.imap_ssl_context(ca + other_ca)
    plain = imap_integration.imap_ssl_context(None)
    original = set(default.get_ca_certs(binary_form=True))
    assert set(custom.get_ca_certs(binary_form=True)) == original | {
        ssl.PEM_cert_to_DER_cert(ca),
        ssl.PEM_cert_to_DER_cert(other_ca),
    }
    assert set(plain.get_ca_certs(binary_form=True)) == original
    assert custom.verify_mode == ssl.CERT_REQUIRED
    assert custom.check_hostname is True
    assert custom.verify_flags == default.verify_flags


def test_invalid_stored_ca_fails_safely_before_imap_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_connect(*_args, **_kwargs):
        raise AssertionError("Invalid trust configuration must fail before network access")

    monkeypatch.setattr(imap_integration.imaplib, "IMAP4_SSL", unexpected_connect)
    invalid = "invalid-certificate-fixture-content"

    async def probe():
        async with httpx.AsyncClient() as http:
            return await imap_integration.ImapIntegration(
                host="mail.example.test",
                port=993,
                tls_mode="ssl",
                username="synthetic-user",
                payload=b"synthetic-password",
                http=http,
                project_id=0,
                tls_ca_pem=invalid,
            ).test_credentials()

    result = asyncio.run(probe())
    assert result["metadata"] == {"stage": "tls", "reason_code": "tls_configuration_error"}
    assert invalid not in json.dumps(result)
    with pytest.raises(ValidationError) as error:
        imap_actions._login({**_fields(tls_ca_pem=invalid), "timeout_s": 5})
    assert error.value.data["field"] == "tls_ca_pem"
    assert invalid not in str(error.value)


@pytest.mark.parametrize("tls_mode", ["ssl", "starttls"])
def test_account_probe_and_actions_use_saved_ca(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch, tls_mode: str
) -> None:
    ca, _leaf, _key = certificates()
    contexts: list[ssl.SSLContext] = []

    class Probe:
        def __init__(self, _host, _port, *, ssl_context=None, timeout=None):
            if ssl_context is not None:
                contexts.append(ssl_context)

        def starttls(self, *, ssl_context):
            contexts.append(ssl_context)

        def login(self, username, password):
            assert (username, password) == ("synthetic-user", "synthetic-password")

        def select(self, mailbox, *, readonly):
            assert readonly is True
            return "OK", []

        def list(self):
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

        def logout(self):
            return "BYE", []

    monkeypatch.setattr(imap_integration.imaplib, "IMAP4_SSL", Probe)
    monkeypatch.setattr(imap_integration.imaplib, "IMAP4", Probe)
    account = (
        AuthRepository(session)
        .store_credential(
            provider_key="imap",
            display_name="Synthetic IMAP Account",
            attach_project_id=project_id,
            fields=_fields(tls_ca_pem=ca, tls_mode=tls_mode),
        )
        .data
    )
    tested = asyncio.run(
        AuthRepository(session).test(project_id=project_id, credential_ref=account.credential_ref)
    )
    assert tested.data.ok is True
    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="communications.imap.mailbox.list",
            input_json={},
            credential_ref=account.credential_ref,
        )
    )
    assert len(contexts) == 2
    for context in contexts:
        assert context.check_hostname is True
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert ssl.PEM_cert_to_DER_cert(ca) in context.get_ca_certs(binary_form=True)


def _handshake(
    client_context: ssl.SSLContext, server_context: ssl.SSLContext, hostname: str
) -> None:
    client_in, client_out, server_in, server_out = [ssl.MemoryBIO() for _ in range(4)]
    client = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    done: set[int] = set()
    for _ in range(20):
        for index, peer in enumerate((client, server)):
            if index not in done:
                try:
                    peer.do_handshake()
                    done.add(index)
                except ssl.SSLWantReadError:
                    pass
        server_in.write(client_out.read())
        client_in.write(server_out.read())
        if len(done) == 2:
            return
    raise AssertionError("TLS handshake did not finish")


@pytest.mark.parametrize("failure", [None, "unknown_ca", "hostname", "expired"])
def test_custom_ca_keeps_chain_hostname_and_expiry_verification(
    tmp_path: Path, failure: str | None
) -> None:
    ca, leaf, key = certificates(expired=failure == "expired")
    cert_path, key_path = tmp_path / "synthetic-server.pem", tmp_path / "synthetic-server.key"
    cert_path.write_text(leaf + ca)
    key_path.write_bytes(key)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = imap_integration.imap_ssl_context(None if failure == "unknown_ca" else ca)
    hostname = "wrong.example.test" if failure == "hostname" else "mail.example.test"
    if failure is None:
        _handshake(client, server, hostname)
    else:
        with pytest.raises(ssl.SSLCertVerificationError):
            _handshake(client, server, hostname)
