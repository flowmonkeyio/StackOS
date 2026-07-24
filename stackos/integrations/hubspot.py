"""HubSpot credential probes.

Official docs:
- Private-app token information: https://developers.hubspot.com/docs/apps/legacy-apps/private-apps/overview
- Authenticated account details: https://developers.hubspot.com/docs/api-reference/legacy/account/account-information/v1/get-integrations-v1-me
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError
from stackos.secret_refs import redact_secret_values

_API_BASE_URL = "https://api.hubapi.com"
_PRIVATE_APP_TOKEN_INFO_URL = f"{_API_BASE_URL}/oauth/v2/private-apps/get/access-token-info"
_OAUTH_ACCOUNT_INFO_URL = f"{_API_BASE_URL}/integrations/v1/me"
_TOKEN_KEY_TEXT_RE = re.compile(r"(?i)([\"']?tokenkey[\"']?\s*[:=]\s*[\"']?)[^\"'\s,;}&]+")


class HubSpotCredentialConfigurationError(ValueError):
    """Raised when a daemon-resolved HubSpot credential is unusable."""


def _payload_object(payload: bytes) -> dict[str, Any]:
    try:
        decoded = payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise HubSpotCredentialConfigurationError(
            "HubSpot credential payload is not UTF-8"
        ) from exc
    if not decoded:
        raise HubSpotCredentialConfigurationError("HubSpot credential payload is empty")
    try:
        value = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise HubSpotCredentialConfigurationError(
            "HubSpot credential payload must be a JSON object"
        ) from exc
    if not isinstance(value, dict):
        raise HubSpotCredentialConfigurationError(
            "HubSpot credential payload must be a JSON object"
        )
    return value


def _access_token(payload: bytes) -> str:
    value = _payload_object(payload).get("access_token")
    if not isinstance(value, str) or not value.strip():
        raise HubSpotCredentialConfigurationError("HubSpot credential missing access_token")
    return value.strip()


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


def _safe_scopes(body: Any) -> list[str]:
    if not isinstance(body, Mapping):
        return []
    scopes = body.get("scopes")
    if not isinstance(scopes, list):
        return []
    return sorted({scope.strip() for scope in scopes if isinstance(scope, str) and scope.strip()})


def _redact_probe_text(value: str, token: str) -> str:
    redacted = redact_secret_text(value).replace(token, "[redacted]")
    return _TOKEN_KEY_TEXT_RE.sub(lambda match: f"{match.group(1)}[redacted]", redacted)


def _redact_probe_value(value: Any, token: str) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if str(key).lower() == "tokenkey"
            else _redact_probe_value(item, token)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_probe_value(item, token) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_probe_value(item, token) for item in value)
    if isinstance(value, str):
        return _redact_probe_text(value, token)
    return value


class HubSpotIntegration(BaseIntegration):
    """Read-only auth probes selected by the saved HubSpot auth method."""

    kind = "hubspot"
    vendor = "hubspot"
    default_qps = 1.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            self._token = _access_token(self.payload)
        except HubSpotCredentialConfigurationError as exc:
            raise IntegrationDownError(
                redact_secret_text(str(exc)),
                data={"vendor": self.vendor},
            ) from exc

    @classmethod
    def _sanitize_request(cls, request_json: Any) -> Any:
        sanitized = super()._sanitize_request(request_json)
        if isinstance(sanitized, dict) and "tokenKey" in sanitized:
            sanitized["tokenKey"] = "[redacted]"
        return sanitized

    def _redacted_error(self, exc: IntegrationDownError) -> IntegrationDownError:
        safe_data = _redact_probe_value(
            redact_secrets(redact_secret_values(exc.data, (self._token,))), self._token
        )
        return IntegrationDownError(
            _redact_probe_text(str(exc), self._token),
            data=safe_data,
        )

    def _record_call(
        self,
        *,
        op: str,
        request: Any,
        response: Any,
        duration_ms: int,
        error: str | None,
        cost_cents: int,
    ) -> None:
        """Keep provider-required ``tokenKey`` out of the inherited run-step audit."""
        super()._record_call(
            op=op,
            request=_redact_probe_value(
                redact_secrets(redact_secret_values(request, (self._token,))), self._token
            ),
            response=_redact_probe_value(
                redact_secrets(redact_secret_values(response, (self._token,))), self._token
            ),
            duration_ms=duration_ms,
            error=_redact_probe_text(error, self._token) if error is not None else None,
            cost_cents=cost_cents,
        )

    async def test_credentials(self) -> dict[str, Any]:
        method = self.probe_context.auth_method_key if self.probe_context else ""
        if method == "private_app_token":
            if not _posture_matches(
                self,
                evidence_source="provider_probe",
                enforcement="local_required",
            ):
                return {
                    "ok": False,
                    "vendor": self.vendor,
                    "status": "unsupported_permission_verification",
                    "summary": (
                        "HubSpot private-app scope evidence requires the reviewed provider probe "
                        "posture."
                    ),
                }
            return await self._test_private_app_token()
        if method == "oauth2_authorization_code":
            if not _posture_matches(
                self,
                evidence_source="oauth_response",
                enforcement="local_required",
            ):
                return {
                    "ok": False,
                    "vendor": self.vendor,
                    "status": "unsupported_permission_verification",
                    "summary": "HubSpot OAuth scope evidence must come from the OAuth response.",
                }
            return await self._test_oauth_account()
        return {
            "ok": False,
            "vendor": self.vendor,
            "status": "unsupported_auth_method",
            "summary": "HubSpot credential test requires a recognized saved auth method.",
        }

    async def _test_private_app_token(self) -> dict[str, Any]:
        try:
            result = await self.call(
                op="auth.test.private_app_token",
                method="POST",
                url=_PRIVATE_APP_TOKEN_INFO_URL,
                json_body={"tokenKey": self._token},
                headers={"Content-Type": "application/json"},
                # ``tokenKey`` is a provider-required request value, never audit material.
                request_log_body={"tokenKey": "[redacted]"},
            )
        except IntegrationDownError as exc:
            raise self._redacted_error(exc) from exc
        body = result.data if isinstance(result.data, Mapping) else {}
        account_id = body.get("hubId")
        user_id = body.get("userId")
        app_id = body.get("appId")
        account: dict[str, Any] | None = None
        if account_id is not None:
            metadata: dict[str, Any] = {"hub_id": account_id}
            if app_id is not None:
                metadata["app_id"] = app_id
            if user_id is not None:
                metadata["user_id"] = user_id
            account = {
                "provider_account_id": str(account_id),
                "display_name": None,
                "metadata": metadata,
            }
        evidence: dict[str, Any] = {"grants": _safe_scopes(body)}
        if account is not None:
            evidence["account"] = account
        return {
            "ok": True,
            "vendor": self.vendor,
            "status": "ok",
            "metadata": {"evidence": evidence},
        }

    async def _test_oauth_account(self) -> dict[str, Any]:
        try:
            result = await self.call(
                op="auth.test.oauth_account",
                method="GET",
                url=_OAUTH_ACCOUNT_INFO_URL,
                headers={"Authorization": f"Bearer {self._token}"},
            )
        except IntegrationDownError as exc:
            raise self._redacted_error(exc) from exc
        body = result.data if isinstance(result.data, Mapping) else {}
        portal_id = body.get("portalId")
        output: dict[str, Any] = {
            "ok": True,
            "vendor": self.vendor,
            "status": "ok",
        }
        if portal_id is not None:
            output["portal_id"] = str(portal_id)
        timezone = body.get("timeZone")
        if isinstance(timezone, str) and timezone:
            output["timezone"] = timezone
        currency = body.get("currency")
        if isinstance(currency, str) and currency:
            output["currency"] = currency
        if portal_id is not None:
            output["metadata"] = {
                "evidence": {
                    "account": {
                        "provider_account_id": str(portal_id),
                        "display_name": None,
                        "metadata": {
                            "portal_id": portal_id,
                            "timezone": timezone if isinstance(timezone, str) else None,
                            "currency": currency if isinstance(currency, str) else None,
                        },
                    }
                }
            }
        # OAuth grants remain the authoritative OAuth lifecycle evidence.
        return output


__all__ = ["HubSpotIntegration"]
