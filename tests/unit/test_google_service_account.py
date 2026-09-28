"""Cryptographic and input-boundary proof with ephemeral test-generated RSA keys."""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from stackos.artifacts.redaction import redact_secret_text, redact_secrets
from stackos.auth_providers.google_service_account import (
    TOKEN_ENDPOINT,
    delegated_subject,
    sign_assertion,
    validate_service_account,
)
from stackos.repositories.base import ValidationError


@pytest.fixture(scope="module")
def key_document():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "type": "service_account",
        "client_email": "reader@project.iam.gserviceaccount.com",
        "private_key_id": "abc123",
        "token_uri": TOKEN_ENDPOINT,
        "private_key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    }


@pytest.mark.parametrize("subject", [None, "reader@example.com"])
def test_assertion_signature_and_bounded_claims(key_document, subject):
    raw = json.dumps(key_document)
    assertion = sign_assertion(value=raw, scopes=("fixed-scope",), subject=subject)
    header, claims, signature = assertion.split(".")

    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    validate_service_account(raw).private_key.public_key().verify(
        decode(signature),
        f"{header}.{claims}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    assert json.loads(decode(header)) == {"alg": "RS256", "typ": "JWT", "kid": "abc123"}
    body = json.loads(decode(claims))
    assert body["iss"] == key_document["client_email"]
    assert body["aud"] == TOKEN_ENDPOINT
    assert body["scope"] == "fixed-scope"
    assert body["exp"] - body["iat"] == 3600
    assert abs(time.time() - body["iat"]) < 5
    assert body.get("sub") == subject
    assert "PRIVATE KEY" not in repr(validate_service_account(raw))


@pytest.mark.parametrize(
    "patch",
    [
        {"type": "external_account"},
        {"type": "authorized_user"},
        {"token_uri": "http://127.0.0.1/token"},
        {"token_uri": "https://evil.test/token"},
        {"token_uri": "https://oauth2.googleapis.com/token?forward=evil"},
        {"universe_domain": "evil.test"},
        {"credential_source": {"url": "http://localhost"}},
        {"source_credentials": {}},
        {"service_account_impersonation_url": "https://evil.test"},
        {"client_email": "reader@example.com"},
        {"client_email": "bad\n@x.gserviceaccount.com"},
        {"private_key": "SECRET INVALID PEM"},
        {"private_key": None},
        {"private_key_id": "bad\nkey"},
    ],
)
def test_untrusted_key_fields_rejected_without_echo(key_document, patch):
    with pytest.raises(ValidationError) as exc:
        validate_service_account(json.dumps({**key_document, **patch}))
    assert "SECRET INVALID PEM" not in str(exc.value)
    assert "http://localhost" not in str(exc.value)


@pytest.mark.parametrize("raw", [None, {}, "[]", "not-json", "[" * 2000, "x" * 65537])
def test_json_shape_and_size_bounded(raw):
    with pytest.raises(ValidationError):
        validate_service_account(raw)


def test_invalid_unicode_is_a_safe_validation_error():
    with pytest.raises(ValidationError) as exc:
        validate_service_account("SYNTHETIC PRIVATE INPUT\ud800")
    assert str(exc.value) == "Service-account JSON must contain valid UTF-8 text"
    assert exc.value.data == {}


@pytest.mark.parametrize("kind", ["small-rsa", "ec", "encrypted"])
def test_wrong_or_encrypted_private_keys_rejected(key_document, kind):
    key = (
        ec.generate_private_key(ec.SECP256R1())
        if kind == "ec"
        else rsa.generate_private_key(
            public_exponent=65537, key_size=1024 if kind == "small-rsa" else 2048
        )
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"synthetic-pass")
        if kind == "encrypted"
        else serialization.NoEncryption(),
    ).decode()
    with pytest.raises(ValidationError):
        validate_service_account(json.dumps({**key_document, "private_key": pem}))


@pytest.mark.parametrize(
    "provider,subject",
    [
        ("google-ads", "reader@example.com"),
        ("google-workspace", "reader\n@example.com"),
        ("google-workspace", "not-an-email"),
        ("google-workspace", 123),
    ],
)
def test_delegation_boundary(provider, subject):
    with pytest.raises(ValidationError):
        delegated_subject(provider, subject)


def test_service_material_redacted_in_nested_data_and_text(key_document):
    raw = json.dumps(key_document)
    result = redact_secrets(
        {
            "service_account_json": raw,
            "assertion": "signed-value",
            "note": key_document["private_key"],
            "credential_ref": "cred_safe",
        }
    )
    assert result == {
        "service_account_json": "[redacted]",
        "assertion": "[redacted]",
        "note": "[redacted private key]\n",
        "credential_ref": "cred_safe",
    }
    assert "signed-value" not in redact_secret_text("assertion=signed-value")
    assert "BEGIN PRIVATE KEY" not in redact_secret_text(raw)
