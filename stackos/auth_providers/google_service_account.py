"""Bounded Google JSON-key validation and signing; no credential discovery or I/O."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from stackos.repositories.base import ValidationError

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
GOOGLE_SERVICE_ACCOUNT_PROVIDERS = frozenset(
    {
        "google-search-console",
        "google-analytics",
        "google-tag-manager",
        "google-ads",
        "google-workspace",
    }
)
_EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\.[A-Za-z]{2,}"
)
_KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class ServiceAccountKey:
    client_email: str
    private_key: rsa.RSAPrivateKey = field(repr=False)
    private_key_id: str | None = None


def delegated_subject(provider_key: str, value: object) -> str | None:
    if value is None or value == "":
        return None
    if provider_key != "google-workspace":
        raise ValidationError("Delegation is supported only for Google Workspace")
    if not isinstance(value, str) or len(value) > 254 or not _EMAIL.fullmatch(value):
        raise ValidationError("Delegated subject must be a valid Workspace user email")
    return value


def validate_service_account(value: object) -> ServiceAccountKey:
    """Accept a Google RSA JSON key, never ADC, external accounts, URLs or file paths."""
    if not isinstance(value, str):
        raise ValidationError("Service-account JSON must be a string of at most 64 KiB")
    try:
        byte_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValidationError("Service-account JSON must contain valid UTF-8 text") from None
    if byte_size > 65536:
        raise ValidationError("Service-account JSON must be a string of at most 64 KiB")
    try:
        body = json.loads(value)
    except (ValueError, RecursionError):
        raise ValidationError("Service-account JSON is invalid") from None
    if not isinstance(body, dict) or body.get("type") != "service_account":
        raise ValidationError("Google credentials must have type service_account")
    if any(
        k in body
        for k in ("credential_source", "source_credentials", "service_account_impersonation_url")
    ):
        raise ValidationError("External credential sources are not supported")
    if (
        body.get("token_uri") != TOKEN_ENDPOINT
        or body.get("universe_domain", "googleapis.com") != "googleapis.com"
    ):
        raise ValidationError(
            "Service-account JSON must use the standard Google token endpoint and universe"
        )
    email = body.get("client_email")
    if (
        not isinstance(email, str)
        or len(email) > 254
        or not _EMAIL.fullmatch(email)
        or not email.endswith(".gserviceaccount.com")
    ):
        raise ValidationError("Service-account JSON must contain a valid service-account email")
    pem = body.get("private_key")
    if not isinstance(pem, str):
        raise ValidationError("Service-account JSON must contain an RSA private key")
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ValidationError("Service-account JSON contains an invalid private key") from None
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValidationError("Service-account private key must be RSA with at least 2048 bits")
    key_id = body.get("private_key_id")
    if key_id is not None and (not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id)):
        raise ValidationError("Service-account private key id is invalid")
    return ServiceAccountKey(email, key, key_id)


def sign_assertion(*, value: object, scopes: tuple[str, ...], subject: str | None = None) -> str:
    key = validate_service_account(value)
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    if key.private_key_id:
        header["kid"] = key.private_key_id
    claims: dict[str, object] = {
        "iss": key.client_email,
        "aud": TOKEN_ENDPOINT,
        "scope": " ".join(scopes),
        "iat": now,
        "exp": now + 3600,
    }
    if subject:
        claims["sub"] = delegated_subject("google-workspace", subject)

    def encode(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    signing_input = b".".join(
        encode(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
    )
    signature = key.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return (signing_input + b"." + encode(signature)).decode("ascii")
