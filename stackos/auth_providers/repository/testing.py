"""Provider credential testing and safe account synchronization."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete
from sqlmodel import col, select
from stackos_connectors.shared.google.probe import diagnostic_facts

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.db.models import Credential, CredentialAccount, CredentialScope
from stackos.integrations import integration_class_for
from stackos.repositories.base import Envelope, RepositoryError, ValidationError

from .schema import AuthMethodOut, AuthProbeEvidence, AuthTestOut
from .utils import utcnow

_ERROR_STAGES = frozenset(
    {
        "test",
        "connect",
        "authenticate",
        "login",
        "select",
        "tls",
        "credential",
        "config",
        "list_objects_v2",
        "auth.test",
    }
)
_ERROR_REASONS = frozenset(
    {
        "connection_refused",
        "dns_error",
        "timeout",
        "network_error",
        "transport_error",
        "transport_failure",
        "tls_certificate_error",
        "tls_configuration_error",
        "tls_negotiation_error",
        "authentication_failed",
        "authorization_failed",
        "permission_denied",
        "invalid_credentials",
        "expired_credentials",
        "access_denied",
        "provider_unavailable",
        "rate_limited",
        "protocol_error",
        "probe_error",
    }
)


def _known_error_value(value: Any, allowed: frozenset[str]) -> str | None:
    # Do not stringify arbitrary values or echo unreviewed text, even after redaction.
    return value if isinstance(value, str) and len(value) <= 64 and value in allowed else None


def _failed_test_result(provider_key: str, exc: RepositoryError) -> dict[str, Any]:
    """Project thrown provider failures onto reviewed Account Test diagnostics."""
    stage = _known_error_value(exc.data.get("stage"), _ERROR_STAGES) or "test"
    reason = _known_error_value(exc.data.get("reason_code"), _ERROR_REASONS) or "probe_error"
    metadata: dict[str, Any] = {"stage": stage, "reason_code": reason}
    summary = f"{provider_key} credential test failed at {stage}."
    next_action = "Review the Account configuration and provider availability, then test again."
    retryable = bool(exc.retryable)
    # RepositoryError.http_status describes our transport response, not the provider.
    status = exc.data.get("status")
    if type(status) is int and 400 <= status <= 599:
        metadata["provider_status_code"] = status
        retryable = status == 429 or status >= 500
        provider_facts = diagnostic_facts(provider_key, exc.data)
        provider_reason = provider_facts.get("provider_reason")
        if provider_reason is not None:
            metadata["provider_reason"] = provider_reason
        if status == 403 and provider_reason in {"SERVICE_DISABLED", "accessNotConfigured"}:
            metadata["reason_code"] = "api_disabled"
            api = provider_facts["provider_api"]
            summary = f"{api} is not enabled for this credential's Google Cloud project."
            next_action = (
                f"Enable the {api} in the credential's Google Cloud project, "
                "then test the Account again."
            )
        elif status == 401:
            metadata["reason_code"] = "authentication_failed"
            summary = "The provider rejected the Account's authentication (HTTP 401)."
            next_action = "Review or reconnect this Account in local Accounts, then test again."
        elif status == 403:
            metadata["reason_code"] = "permission_denied"
            summary = "The provider denied access to the account verification request (HTTP 403)."
            next_action = (
                "Review this Account's resource permissions and required API scopes, "
                "then test again."
            )
        elif status == 429:
            metadata["reason_code"] = "rate_limited"
            summary = "The provider rate-limited the account verification request (HTTP 429)."
            next_action = "Test again later after the provider's rate limit resets."
        elif status >= 500:
            metadata["reason_code"] = "provider_unavailable"
            summary = (
                f"The provider could not complete the account verification request (HTTP {status})."
            )
            next_action = (
                "Test again later; if the failure persists, check the provider's service status."
            )
        else:
            metadata["reason_code"] = "provider_http_error"
            summary = f"The provider rejected the account verification request (HTTP {status})."
    reply_code = exc.data.get("reply_code")
    if type(reply_code) is int and 400 <= reply_code <= 599:
        metadata["reply_code"] = str(reply_code)
    elif (
        isinstance(reply_code, str)
        and len(reply_code) == 3
        and reply_code.isascii()
        and reply_code.isdigit()
        and 400 <= int(reply_code) <= 599
    ):
        metadata["reply_code"] = reply_code
    return {
        "ok": False,
        "status": "failed",
        "summary": summary,
        "retryable": retryable,
        "next_action": next_action,
        "metadata": metadata,
    }


class CredentialTestingMixin:
    """Run provider auth tests and persist only redacted metadata."""

    async def test(
        self,
        *,
        project_id: int | None,
        credential_ref: str,
    ) -> Envelope[AuthTestOut]:
        if project_id is None:
            credential, _ = self._global_credential(credential_ref)
            resolved = await self._resolve_for_use(
                project_id=None,
                provider_key=credential.provider_key,
                credential_ref=credential_ref,
                operation="account.test.resolve",
                required_scopes=[],
                local_admin=True,
            )
            credential = resolved.credential
        else:
            credential, _ = self._resolve_credential(
                project_id=project_id,
                credential_ref=credential_ref,
            )
            resolved = await self.resolve_for_execution(
                project_id=project_id,
                provider_key=credential.provider_key,
                credential_ref=credential_ref,
                operation="account.test.resolve",
                required_scopes=[],
            )
            credential = resolved.credential
        integration_cls = integration_class_for(credential.provider_key)
        acquisition_only = (
            credential.auth_method_key == "service-account"
            and credential.provider_key in {"google-ads", "google-workspace"}
        )
        provider = self._get_provider(credential.provider_key, required=False, sync=False)
        method: AuthMethodOut | None = None
        if provider is not None:
            method = self._get_auth_method(provider, credential.auth_method_key)
            assert method is not None
        preflight = None
        if credential.provider_key in {"pipedrive", "salesloft", "hubspot"}:
            assert provider is not None and method is not None
            preflight = self._account_probe_preflight(provider=provider, method=method)
        try:
            if preflight is not None:
                raw_result = preflight
            elif acquisition_only:
                # The host resolved/acquired the token above; this existing diagnostic
                # intentionally makes no provider-resource or new-grant claim.
                raw_result = {
                    "ok": True,
                    "status": "connected",
                    "summary": (
                        "Google service-account token acquired; resource access is unverified."
                    ),
                    "metadata": {
                        "verification": "token_acquisition_only",
                        "resource_access": "unverified",
                    },
                }
            elif credential.provider_key == "google-paa":
                if integration_cls is None:
                    raise ValidationError("Google PAA test wrapper is unavailable")
                raw_result = await integration_cls().test_credentials()
            else:
                from stackos_connectors import CallOptions, ConnectorAuth, get_default_client
                from stackos_connectors import probe as native_probe
                from stackos_connectors.errors import IntegrationDownError as NativeProbeError
                from stackos_connectors.errors import ValidationError as NativeValidationError

                from stackos.actions.package_bridge import host_probe_error, resolved_auth
                from stackos.integrations._rate_limit import get_bucket

                assert method is not None
                catalog_key = (
                    "byteplus-seedream"
                    if credential.provider_key == "byteplus-ark"
                    else credential.provider_key
                )
                declarations = (
                    get_default_client()
                    .registry.connector_metadata.get(catalog_key, {})
                    .get("auth_methods", ())
                )
                declaration = next(
                    (item for item in declarations if item.get("key") == method.key), None
                )
                if declaration is None:
                    raise ValidationError("saved auth method has no native probe contract")
                try:
                    auth = resolved_auth(
                        resolved,
                        payload_format=declaration.get("payload_format", "json"),
                        payload_field=declaration.get("payload_field"),
                        declaration=declaration,
                    )
                    assert auth is not None
                    auth = ConnectorAuth(
                        method=auth.method,
                        fields=auth.fields,
                        config=native_probe.project_probe_config(
                            catalog_key, resolved.config_json or {}
                        ),
                    )
                    # Mailbox choice is a host preference; only the native mailbox
                    # crosses the package boundary, never aliases or policy maps.
                    provider_context = {}
                    if credential.provider_key == "imap":
                        provider_context["mailbox"] = str(
                            (resolved.config_json or {}).get("default_mailbox") or "INBOX"
                        )
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        raw_result = await native_probe.probe_credentials(
                            catalog_key,
                            auth=auth,
                            options=CallOptions(
                                http=client,
                                rate_limiter=get_bucket(
                                    project_id=project_id or 0,
                                    kind=credential.provider_key,
                                    qps=integration_cls.default_qps if integration_cls else 1.0,
                                ),
                                provider_context=provider_context,
                            ),
                            context=native_probe.AuthMethodProbeContext(auth_method_key=method.key),
                        )
                except NativeProbeError as exc:
                    raise host_probe_error(exc) from None
                except NativeValidationError as exc:
                    raise ValidationError(exc.detail, data=exc.metadata_json) from None
        except RepositoryError as exc:
            raw_result = _failed_test_result(credential.provider_key, exc)
        out = self._normalize_test_result(
            credential=credential,
            provider_key=credential.provider_key,
            raw=raw_result,
        )
        now = utcnow()
        credential.last_tested_at = now
        # A test result is diagnostic evidence, not an execution permission.
        # Keep the stored credential usable and return/audit the real result.
        credential.status = "connected"
        credential.updated_at = now
        self._s.add(credential)
        self._sync_account_from_test_result(
            credential=credential,
            provider_key=credential.provider_key,
            ok=out.ok,
            metadata=out.metadata,
        )
        if method is not None:
            self._sync_probe_evidence_from_test_result(
                credential=credential,
                method=method,
                ok=out.ok,
                metadata=out.metadata,
            )
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation="account.test",
            status=out.status,
            metadata_json={
                "ok": out.ok,
                "metadata": out.metadata,
                "result": out.model_dump(mode="json"),
            },
            project_id=project_id,
        )
        self._s.commit()
        return Envelope(data=out, project_id=project_id)

    def _sync_probe_evidence_from_test_result(
        self,
        *,
        credential: Credential,
        method: AuthMethodOut,
        ok: bool,
        metadata: Mapping[str, Any],
    ) -> None:
        """Persist normalized account and grant evidence under the saved method posture."""

        posture = method.permission_verification
        if not ok or posture is None or credential.id is None:
            return
        raw_evidence = metadata.get("evidence")
        try:
            evidence = AuthProbeEvidence.model_validate(raw_evidence)
        except PydanticValidationError:
            evidence = None
        if evidence is not None and evidence.account is not None:
            account = self._s.exec(
                select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
            ).first()
            if account is None:
                account = CredentialAccount(credential_id=credential.id)
            account.provider_account_id = evidence.account.provider_account_id
            account.display_name = evidence.account.display_name
            account.metadata_json = redact_secrets(evidence.account.metadata)
            account.updated_at = utcnow()
            self._s.add(account)
        if posture.evidence_source != "provider_probe" or posture.enforcement != "local_required":
            return
        safe_config = dict(credential.config_json or {})
        if evidence is None or evidence.grants is None:
            safe_config["scope_status"] = "unknown"
            self._s.exec(
                delete(CredentialScope).where(col(CredentialScope.credential_id) == credential.id)
            )
        else:
            grants = sorted({grant.strip() for grant in evidence.grants if grant.strip()})
            safe_config["scope_status"] = "known"
            self._s.exec(
                delete(CredentialScope).where(col(CredentialScope.credential_id) == credential.id)
            )
            for grant in grants:
                self._s.add(CredentialScope(credential_id=credential.id, scope=grant))
        credential.config_json = self._safe_config(safe_config)
        self._s.add(credential)

    def _sync_account_from_test_result(
        self,
        *,
        credential: Credential,
        provider_key: str,
        ok: bool,
        metadata: Mapping[str, Any],
    ) -> None:
        if not ok or credential.id is None:
            return
        if provider_key == "slack-bot":
            self._sync_slack_account_from_test_result(credential=credential, metadata=metadata)

    def _sync_slack_account_from_test_result(
        self,
        *,
        credential: Credential,
        metadata: Mapping[str, Any],
    ) -> None:
        assert credential.id is not None
        team_id = str(metadata.get("team_id") or "").strip()
        team = str(metadata.get("team") or "").strip()
        user_id = str(metadata.get("user_id") or "").strip()
        user = str(metadata.get("user") or "").strip()
        bot_id = str(metadata.get("bot_id") or "").strip()
        if not team_id and not user_id and not bot_id:
            return
        account = self._s.exec(
            select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
        ).first()
        now = utcnow()
        if account is None:
            account = CredentialAccount(credential_id=credential.id)
        account.provider_account_id = team_id or user_id or bot_id or None
        account.display_name = team or user or team_id or user_id or None
        account.metadata_json = {
            "team_id": team_id or None,
            "team": team or None,
            "user_id": user_id or None,
            "user": user or None,
            "bot_id": bot_id or None,
        }
        account.updated_at = now
        self._s.add(account)

    def _normalize_test_result(
        self,
        *,
        credential: Credential,
        provider_key: str,
        raw: Mapping[str, Any],
    ) -> AuthTestOut:
        vendor = str(raw.get("vendor") or provider_key)
        ok = bool(raw.get("ok", raw.get("status") == "ok"))
        status_value = raw.get("status")
        status_text = (
            redact_secret_text(str(status_value)) if status_value else ("ok" if ok else "failed")
        )
        summary_value = raw.get("summary") or raw.get("detail") or raw.get("message")
        summary = (
            redact_secret_text(str(summary_value))
            if summary_value
            else (
                f"{vendor} credentials are reachable" if ok else f"{vendor} credential test failed"
            )
        )
        passthrough = {
            "ok",
            "vendor",
            "status",
            "summary",
            "detail",
            "message",
            "details",
            "checked_at",
            "retryable",
            "next_action",
            "metadata",
        }
        metadata = {key: value for key, value in raw.items() if key not in passthrough}
        if isinstance(raw.get("metadata"), dict):
            metadata.update(raw["metadata"])
        checked_at = raw.get("checked_at")
        return AuthTestOut(
            credential_ref=credential.credential_ref,
            provider_key=provider_key,
            ok=ok,
            status=status_text,
            summary=summary,
            checked_at=str(checked_at) if checked_at else datetime.now(tz=UTC).isoformat(),
            retryable=(
                bool(raw.get("retryable")) if isinstance(raw.get("retryable"), bool) else False
            ),
            next_action=(
                redact_secret_text(str(raw["next_action"])) if raw.get("next_action") else None
            ),
            metadata=redact_secrets(metadata),
        )
