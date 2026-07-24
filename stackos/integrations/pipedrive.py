"""Pipedrive credential probes.

Official docs: https://developers.pipedrive.com/docs/api/v1/Users
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from stackos.artifacts import redact_secret_text
from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError

_PIPEDRIVE_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.pipedrive\.com$", re.IGNORECASE)


class PipedriveCredentialConfigurationError(ValueError):
    """Raised when saved Pipedrive credential configuration is unsafe."""


def normalize_pipedrive_api_domain(raw_domain: str | None) -> str:
    """Return the one allowed HTTPS Pipedrive API origin from safe config."""
    raw = (raw_domain or "").strip()
    if not raw:
        raise PipedriveCredentialConfigurationError("Pipedrive credential missing api_domain")
    if "://" not in raw:
        raw = f"https://{raw if '.' in raw else f'{raw}.pipedrive.com'}"
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise PipedriveCredentialConfigurationError(
            "Pipedrive api_domain must be an HTTPS origin"
        ) from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise PipedriveCredentialConfigurationError("Pipedrive api_domain must be an HTTPS origin")
    hostname = parsed.hostname.lower().rstrip(".")
    if not _PIPEDRIVE_HOST_RE.fullmatch(hostname):
        raise PipedriveCredentialConfigurationError(
            "Pipedrive api_domain must be a tenant .pipedrive.com host"
        )
    return f"https://{hostname}"


def _payload_object(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8").strip()
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipedriveCredentialConfigurationError(
            "Pipedrive credential payload must be a JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise PipedriveCredentialConfigurationError(
            "Pipedrive credential payload must be a JSON object"
        )
    return value


def _required_token(payload: Mapping[str, Any], key: str) -> str:
    token = payload.get(key)
    if not isinstance(token, str) or not token.strip():
        raise PipedriveCredentialConfigurationError(f"Pipedrive credential missing {key}")
    return token.strip()


def _posture_matches(
    integration: BaseIntegration,
    *,
    evidence_source: str,
    enforcement: str,
) -> bool:
    posture = (
        integration.probe_context.permission_verification if integration.probe_context else None
    )
    return bool(
        posture
        and posture.evidence_source == evidence_source
        and posture.enforcement == enforcement
    )


class PipedriveIntegration(BaseIntegration):
    """Read-only current-user probe selected from a saved Pipedrive auth method."""

    kind = "pipedrive"
    vendor = "pipedrive"
    default_qps = 1.0

    def __init__(self, *, api_domain: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            self.api_domain = normalize_pipedrive_api_domain(api_domain)
            self._payload = _payload_object(self.payload)
        except PipedriveCredentialConfigurationError as exc:
            raise IntegrationDownError(
                redact_secret_text(str(exc)),
                data={"vendor": self.vendor},
            ) from exc

    async def test_credentials(self) -> dict[str, Any]:
        method = self.probe_context.auth_method_key if self.probe_context else ""
        if method == "oauth2_token":
            if _posture_matches(
                self,
                evidence_source="unavailable",
                enforcement="local_required",
            ):
                return {
                    "ok": False,
                    "vendor": self.vendor,
                    "status": "permission_evidence_unavailable",
                    "summary": (
                        "Pipedrive manual OAuth tokens have no verified scope evidence; reconnect "
                        "with OAuth."
                    ),
                }
            return self._unsupported_posture()
        if method == "api_token":
            if not _posture_matches(
                self,
                evidence_source="unavailable",
                enforcement="provider_enforced",
            ):
                return self._unsupported_posture()
            token = _required_token(self._payload, "api_token")
            headers = {"x-api-token": token}
        elif method == "oauth2_authorization_code":
            if not _posture_matches(
                self,
                evidence_source="oauth_response",
                enforcement="local_required",
            ):
                return self._unsupported_posture()
            token = _required_token(self._payload, "access_token")
            headers = {"Authorization": f"Bearer {token}"}
        else:
            return {
                "ok": False,
                "vendor": self.vendor,
                "status": "unsupported_auth_method",
                "summary": "Pipedrive credential test requires a recognized saved auth method.",
            }
        result = await self.call(
            op="auth.test",
            method="GET",
            url=f"{self.api_domain}/api/v1/users/me",
            headers=headers,
        )
        body = result.data if isinstance(result.data, Mapping) else {}
        raw_user = body.get("data") if isinstance(body.get("data"), Mapping) else None
        if body.get("success") is False or raw_user is None:
            return {
                "ok": False,
                "vendor": self.vendor,
                "status": "invalid_response",
                "summary": "Pipedrive auth probe did not return the current user.",
            }
        output: dict[str, Any] = {"ok": True, "vendor": self.vendor, "status": "ok"}
        for body_key, output_key in (
            ("id", "user_id"),
            ("name", "user_name"),
            ("company_id", "company_id"),
            ("company_name", "company_name"),
        ):
            value = raw_user.get(body_key)
            if value is not None:
                output[output_key] = str(value)
        account_id = raw_user.get("company_id") or raw_user.get("id")
        if account_id is not None:
            output["metadata"] = {
                "evidence": {
                    "account": {
                        "provider_account_id": str(account_id),
                        "display_name": (
                            str(raw_user.get("company_name") or raw_user.get("name") or account_id)
                        ),
                        "metadata": {
                            "user_id": raw_user.get("id"),
                            "user_name": raw_user.get("name"),
                            "company_id": raw_user.get("company_id"),
                            "company_name": raw_user.get("company_name"),
                        },
                    }
                }
            }
        # Grant records remain provider-enforced for API tokens and OAuth-response-owned for OAuth.
        return output

    def _unsupported_posture(self) -> dict[str, Any]:
        return {
            "ok": False,
            "vendor": self.vendor,
            "status": "unsupported_permission_verification",
            "summary": "Pipedrive credential test requires the saved method's reviewed posture.",
        }


__all__ = ["PipedriveIntegration", "normalize_pipedrive_api_domain"]
