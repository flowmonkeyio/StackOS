"""Salesloft credential probes.

Official docs: https://developers.salesloft.com/docs/api/me-index/
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from stackos.artifacts import redact_secret_text
from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError

_ME_URL = "https://api.salesloft.com/v2/me"


class SalesloftCredentialConfigurationError(ValueError):
    """Raised when a daemon-resolved Salesloft credential is unusable."""


def _payload_object(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8").strip()
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SalesloftCredentialConfigurationError(
            "Salesloft credential payload must be a JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise SalesloftCredentialConfigurationError(
            "Salesloft credential payload must be a JSON object"
        )
    return value


def _required_token(payload: Mapping[str, Any], key: str) -> str:
    token = payload.get(key)
    if not isinstance(token, str) or not token.strip():
        raise SalesloftCredentialConfigurationError(f"Salesloft credential missing {key}")
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


class SalesloftIntegration(BaseIntegration):
    """Read-only current-user probe selected from saved Salesloft auth method."""

    kind = "salesloft"
    vendor = "salesloft"
    default_qps = 1.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            self._payload = _payload_object(self.payload)
        except SalesloftCredentialConfigurationError as exc:
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
                        "Salesloft manual OAuth tokens have no verified scope evidence; reconnect "
                        "with OAuth."
                    ),
                }
            return self._unsupported_posture()
        if method == "api_key":
            if not _posture_matches(
                self,
                evidence_source="unavailable",
                enforcement="provider_enforced",
            ):
                return self._unsupported_posture()
            token = _required_token(self._payload, "api_key")
        elif method == "oauth2_authorization_code":
            if not _posture_matches(
                self,
                evidence_source="oauth_response",
                enforcement="local_required",
            ):
                return self._unsupported_posture()
            token = _required_token(self._payload, "access_token")
        else:
            return {
                "ok": False,
                "vendor": self.vendor,
                "status": "unsupported_auth_method",
                "summary": "Salesloft credential test requires a recognized saved auth method.",
            }
        result = await self.call(
            op="auth.test",
            method="GET",
            url=_ME_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        body = result.data if isinstance(result.data, Mapping) else {}
        if not body:
            return {
                "ok": False,
                "vendor": self.vendor,
                "status": "invalid_response",
                "summary": "Salesloft auth probe did not return the current user.",
            }
        output: dict[str, Any] = {"ok": True, "vendor": self.vendor, "status": "ok"}
        for body_key, output_key in (
            ("id", "user_id"),
            ("guid", "user_guid"),
            ("name", "user_name"),
        ):
            value = body.get(body_key)
            if value is not None:
                output[output_key] = str(value)
        account_id = body.get("guid") or body.get("id")
        if account_id is not None:
            output["metadata"] = {
                "evidence": {
                    "account": {
                        "provider_account_id": str(account_id),
                        "display_name": (
                            str(body.get("name")) if body.get("name") is not None else None
                        ),
                        "metadata": {
                            "user_id": body.get("id"),
                            "user_guid": body.get("guid"),
                            "user_name": body.get("name"),
                        },
                    }
                }
            }
        # This endpoint is a credential-health probe, never grant evidence.
        return output

    def _unsupported_posture(self) -> dict[str, Any]:
        return {
            "ok": False,
            "vendor": self.vendor,
            "status": "unsupported_permission_verification",
            "summary": "Salesloft credential test requires the saved method's reviewed posture.",
        }


__all__ = ["SalesloftIntegration"]
