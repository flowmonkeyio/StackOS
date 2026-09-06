"""IMAP auth wrapper for credential tests.

Official docs:
- IMAP4rev2 protocol: https://www.rfc-editor.org/rfc/rfc9051.html
"""

from __future__ import annotations

import asyncio
import imaplib
import json
import re
import socket
import ssl
from contextlib import suppress
from imaplib import IMAP4
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ValidationError

_MAX_CA_PEM_BYTES = 65_536
_CA_CERTIFICATE_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----"
)


def normalize_imap_ca_pem(value: Any) -> str | None:
    """Validate public CA certificates before storing or using Account trust."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    error = ValidationError(
        "IMAP tls_ca_pem must contain only valid CA certificates in PEM format "
        "(maximum 64 KiB); private keys and other content are not accepted.",
        data={"provider_key": "imap", "field": "tls_ca_pem"},
    )
    if not isinstance(value, str) or len(value) > _MAX_CA_PEM_BYTES:
        raise error
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        raise error from None
    blocks = _CA_CERTIFICATE_BLOCK.findall(value)
    if not blocks or _CA_CERTIFICATE_BLOCK.sub("", value).strip():
        raise error
    if len(encoded) > _MAX_CA_PEM_BYTES:
        raise error
    normalized: list[str] = []
    try:
        for block in blocks:
            certificate = x509.load_pem_x509_certificate(block.encode("ascii"))
            constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
            if not constraints.value.ca:
                raise error
            try:
                usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
            except x509.ExtensionNotFound:
                pass
            else:
                if not usage.value.key_cert_sign:
                    raise error
            normalized.append(certificate.public_bytes(Encoding.PEM).decode("ascii"))
    except (ValueError, x509.ExtensionNotFound, x509.DuplicateExtension):
        raise error from None
    result = "".join(normalized)
    if len(result) > _MAX_CA_PEM_BYTES:
        raise error
    return result


def imap_ssl_context(tls_ca_pem: Any = None) -> ssl.SSLContext:
    """Add only this Account's approved CA bundle to Python's default trust."""
    ca_pem = normalize_imap_ca_pem(tls_ca_pem)
    context = ssl.create_default_context()
    if ca_pem:
        # Additive loading preserves default roots and hostname verification.
        # https://docs.python.org/3/library/ssl.html#ssl.SSLContext.load_verify_locations
        try:
            context.load_verify_locations(cadata=ca_pem)
        except ssl.SSLError:
            raise ValidationError(
                "IMAP additional CA certificates could not be loaded.",
                data={"provider_key": "imap", "field": "tls_ca_pem"},
            ) from None
    return context


_FAILURE_DIAGNOSTICS = {
    "tls_configuration_error": (
        "IMAP additional CA certificate configuration is invalid.",
        "Correct or clear the Account's additional trusted CA certificates, then test again. "
        "Supply public CA certificates only and keep TLS verification enabled.",
        False,
    ),
    "connection_refused": (
        "IMAP connection was refused.",
        "Check that the mailbox server is running and the configured host and port are correct; "
        "then test the Account again.",
        True,
    ),
    "dns_error": (
        "IMAP server hostname could not be resolved.",
        "Check the configured hostname and local DNS/network access, then test the Account again.",
        False,
    ),
    "timeout": (
        "IMAP credential probe timed out.",
        "Check mailbox-server availability and network access, then retry the read-only probe.",
        True,
    ),
    "network_error": (
        "IMAP connection failed or was interrupted.",
        "Check the mailbox server and network connection, then retry the read-only probe.",
        True,
    ),
    "tls_certificate_error": (
        "IMAP server certificate verification failed.",
        "Check certificate validity, hostname, and the CA trusted by the daemon process. "
        "Keep TLS verification enabled and test again after correcting the certificate setup.",
        False,
    ),
    "tls_negotiation_error": (
        "IMAP TLS negotiation failed.",
        "Check that the configured port and SSL/STARTTLS mode match the server. "
        "Keep TLS verification enabled and test again after correcting the setup.",
        False,
    ),
    "login_rejected": (
        "IMAP server rejected the login.",
        "Check the Account username, password or app password, and provider IMAP-login policy "
        "in local Account settings; then test again.",
        False,
    ),
    "mailbox_unavailable": (
        "IMAP default mailbox could not be selected read-only.",
        "Check that the configured default mailbox exists and this Account can read it; "
        "then test again.",
        False,
    ),
    "protocol_aborted": (
        "IMAP server aborted the credential probe.",
        "Check server availability, then retry with a new read-only probe connection.",
        True,
    ),
    "protocol_error": (
        "IMAP server returned an unsupported or invalid protocol response.",
        "Check that the host and port serve IMAP and review server configuration "
        "before testing again.",
        False,
    ),
    "probe_error": (
        "IMAP credential probe could not complete.",
        "Review the reported probe stage and local mailbox configuration before testing again.",
        False,
    ),
}


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
        tls_ca_pem: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._host = host
        self._port = port
        self._tls_mode = tls_mode
        self._username = username
        self._default_mailbox = default_mailbox
        self._timeout_s = timeout_s
        self._tls_ca_pem = tls_ca_pem
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
        stage = "tls"
        try:
            tls_context = imap_ssl_context(self._tls_ca_pem)
            stage = "connect"
            if self._tls_mode == "ssl":
                client = imaplib.IMAP4_SSL(
                    self._host,
                    self._port,
                    ssl_context=tls_context,
                    timeout=self._timeout_s,
                )
            else:
                client = imaplib.IMAP4(self._host, self._port, timeout=self._timeout_s)
            if self._tls_mode == "starttls":
                stage = "tls"
                client.starttls(ssl_context=tls_context)
            stage = "login"
            client.login(self._username, self._password)
            stage = "select"
            status, _data = client.select(self._default_mailbox, readonly=True)
            if str(status).upper() != "OK":
                raise IMAP4.error("Mailbox select rejected")
            return {
                "ok": True,
                "vendor": "imap",
                "status": "ok",
                "host": self._host,
                "port": self._port,
                "tls_mode": self._tls_mode,
                "default_mailbox": self._default_mailbox,
            }
        except Exception as exc:
            # Classify protocol facts, never provider-controlled exception text.
            # IMAP4.abort subclasses IMAP4.error; SSL and DNS errors subclass OSError.
            if isinstance(exc, ValidationError):
                reason_code, stage = "tls_configuration_error", "tls"
            elif isinstance(exc, ssl.SSLCertVerificationError):
                reason_code, stage = "tls_certificate_error", "tls"
            elif isinstance(exc, ssl.SSLError):
                if stage in {"login", "select"}:
                    # TLS is already established. EOF/close and other SSL I/O
                    # failures here are interrupted transport, not a bad port
                    # or failed handshake; retain the actual operation stage.
                    reason_code = "network_error"
                else:
                    reason_code, stage = "tls_negotiation_error", "tls"
            elif isinstance(exc, socket.gaierror):
                reason_code = "dns_error"
            elif isinstance(exc, TimeoutError):
                reason_code = "timeout"
            elif isinstance(exc, ConnectionRefusedError):
                reason_code = "connection_refused"
            elif isinstance(exc, OSError):
                reason_code = "network_error"
            elif isinstance(exc, IMAP4.abort):
                reason_code = "protocol_aborted"
            elif isinstance(exc, IMAP4.error):
                reason_code = {
                    "tls": "tls_negotiation_error",
                    "login": "login_rejected",
                    "select": "mailbox_unavailable",
                }.get(stage, "protocol_error")
            else:
                reason_code = "probe_error"
            summary, next_action, retryable = _FAILURE_DIAGNOSTICS[reason_code]
            if isinstance(exc, socket.gaierror):
                retryable = exc.errno == socket.EAI_AGAIN
            return {
                "ok": False,
                "vendor": "imap",
                "status": "failed",
                "summary": summary,
                "next_action": next_action,
                "retryable": retryable,
                "metadata": {"stage": stage, "reason_code": reason_code},
            }
        finally:
            if client is not None:
                # LOGOUT does not expunge; never use CLOSE as generic cleanup.
                with suppress(Exception):
                    client.logout()


__all__ = ["ImapIntegration", "imap_ssl_context", "normalize_imap_ca_pem"]
