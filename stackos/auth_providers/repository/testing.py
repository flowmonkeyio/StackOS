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

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.db.models import Credential, CredentialAccount, CredentialScope
from stackos.repositories.base import Envelope, RepositoryError, ValidationError

from .schema import AuthMethodOut, AuthMethodProbeContext, AuthProbeEvidence, AuthTestOut
from .utils import utcnow


def _integration_class_for(kind: str) -> type[Any] | None:
    """Resolve through package attr so legacy monkeypatch paths keep working."""

    from stackos.auth_providers import repository as repository_package

    return repository_package.integration_class_for(kind)


class CredentialTestingMixin:
    """Run provider auth tests and persist only redacted metadata."""

    async def test(
        self,
        *,
        project_id: int | None,
        credential_ref: str,
    ) -> Envelope[AuthTestOut]:
        if project_id is None:
            credential, row = self._global_credential(credential_ref)
            assert row.id is not None
            from stackos.repositories.projects import IntegrationCredentialRepository

            secret_payload = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        else:
            credential, row = self._resolve_credential(
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
            row = resolved.integration
            secret_payload = resolved.secret_payload
        integration_cls = _integration_class_for(credential.provider_key)
        if integration_cls is None:
            raise ValidationError(
                f"auth provider {credential.provider_key!r} has no test wrapper",
                data={
                    "provider_key": credential.provider_key,
                    "credential_ref": credential.credential_ref,
                },
            )
        provider = self._get_provider(
            credential.provider_key,
            required=False,
            sync=False,
        )
        method: AuthMethodOut | None = None
        probe_context: AuthMethodProbeContext | None = None
        if provider is not None:
            method = self._get_auth_method(
                provider,
                credential.auth_method_key,
            )
            assert method is not None
            probe_context = AuthMethodProbeContext(
                auth_method_key=method.key,
                permission_verification=method.permission_verification,
            )
        extra = self._integration_extra(credential)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                integration = integration_cls(
                    payload=secret_payload,
                    project_id=project_id or 0,
                    http=client,
                    probe_context=probe_context,
                    **extra,
                )
                raw_result = await integration.test_credentials()
        except RepositoryError as exc:
            safe = redact_secrets(exc.data)
            stage = str(safe.get("stage") or "test")[:80]
            reason_code = str(safe.get("reason_code") or type(exc).__name__)[:120]
            metadata = {
                "stage": stage,
                "reason_code": reason_code,
            }
            if safe.get("reply_code") is not None:
                metadata["reply_code"] = str(safe["reply_code"])[:3]
            raw_result = {
                "ok": False,
                "status": "failed",
                "summary": f"{credential.provider_key} credential test failed at {stage}",
                "retryable": bool(exc.retryable),
                "metadata": metadata,
            }
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
            metadata_json={"ok": out.ok, "metadata": out.metadata},
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
            return
        if provider_key != "telegram-bot":
            return
        bot_id = metadata.get("bot_id")
        bot_account_id = str(bot_id) if bot_id is not None else ""
        username = str(metadata.get("username") or "").strip()
        first_name = str(metadata.get("first_name") or "").strip()
        if bot_id is None and not username and not first_name:
            return
        if bot_account_id:
            self._assert_telegram_bot_account_available(
                bot_id=bot_account_id,
                current_credential_id=credential.id,
            )
        account = self._s.exec(
            select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
        ).first()
        now = utcnow()
        if account is None:
            account = CredentialAccount(credential_id=credential.id)
        account.provider_account_id = bot_account_id or username or None
        account.display_name = f"@{username}" if username else first_name or None
        account.metadata_json = {
            "bot_id": bot_id,
            "username": username or None,
            "first_name": first_name or None,
            "is_bot": metadata.get("is_bot"),
        }
        account.updated_at = now
        self._s.add(account)

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

    def _integration_extra(self, credential: Credential) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        config = credential.config_json or {}
        if credential.provider_key == "dataforseo":
            login = config.get("login")
            if not login:
                raise ValidationError(
                    "dataforseo credential missing config_json.login",
                    data={"credential_id": credential.id},
                )
            extra["login"] = login
        elif credential.provider_key == "wordpress":
            site_url = config.get("wp_url") or config.get("site_url") or config.get("base_url")
            if not site_url:
                raise ValidationError(
                    "wordpress credential missing config_json.wp_url",
                    data={"credential_id": credential.id},
                )
            extra["site_url"] = str(site_url)
        elif credential.provider_key == "ghost":
            site_url = config.get("ghost_url") or config.get("site_url") or config.get("base_url")
            if not site_url:
                raise ValidationError(
                    "ghost credential missing config_json.ghost_url",
                    data={"credential_id": credential.id},
                )
            extra["site_url"] = str(site_url)
            if config.get("api_version"):
                extra["api_version"] = str(config["api_version"])
        elif credential.provider_key == "openrouter":
            for key in ("http_referer", "app_title"):
                value = config.get(key)
                if isinstance(value, str) and value.strip():
                    extra[key] = value.strip()
        elif credential.provider_key == "pipedrive":
            api_domain = (
                config.get("api_domain") or config.get("base_url") or config.get("company_domain")
            )
            if not isinstance(api_domain, str) or not api_domain.strip():
                raise ValidationError(
                    "pipedrive credential missing a trusted API domain",
                    data={"credential_id": credential.id},
                )
            # The provider wrapper owns strict normalization and the
            # ``.pipedrive.com`` host allowlist before it performs HTTP.
            extra["api_domain"] = api_domain.strip()
        elif credential.provider_key in {
            "telegram-bot",
            "slack-bot",
            "trackbooth",
        } and config.get("api_base_url"):
            extra["api_base_url"] = str(config["api_base_url"])
        elif credential.provider_key == "shopify":
            store_domain = (
                config.get("store_domain") or config.get("shop_domain") or config.get("shop")
            )
            if not store_domain:
                raise ValidationError(
                    "shopify credential missing config_json.store_domain",
                    data={"credential_id": credential.id},
                )
            extra["store_domain"] = str(store_domain)
            if config.get("api_version"):
                extra["api_version"] = str(config["api_version"])
        elif credential.provider_key == "ftp":
            from stackos.integrations.ftp import validate_ftp_credential_config

            try:
                validate_ftp_credential_config(config)
            except ValueError as exc:
                raise ValidationError(
                    str(exc),
                    data={"credential_id": credential.id},
                ) from exc
            passive_value = config.get("passive_mode", True)
            extra.update(
                {
                    "host": str(config["host"]),
                    "port": int(config.get("port") or 21),
                    "tls_mode": str(config.get("tls_mode") or "explicit"),
                    "username": str(config["username"]),
                    "passive_mode": (
                        passive_value
                        if isinstance(passive_value, bool)
                        else str(passive_value).lower() in {"true", "1", "yes", "on"}
                    ),
                    "timeout_s": float(config.get("timeout_s") or 30),
                    "encoding": str(config.get("encoding") or "utf-8"),
                }
            )
        elif credential.provider_key == "aws-s3":
            from stackos.integrations.s3 import validate_s3_credential_config

            try:
                validate_s3_credential_config(config)
            except ValueError as exc:
                raise ValidationError(
                    str(exc),
                    data={"credential_id": credential.id},
                ) from exc
            extra.update(
                {
                    "bucket": str(config["bucket"]),
                    "region": str(config["region"]),
                }
            )
        elif credential.provider_key in {"smtp", "imap"}:
            for key in ("host", "port", "tls_mode", "username", "timeout_s"):
                if key in config and config[key] is not None:
                    extra[key] = config[key]
            if credential.provider_key == "imap":
                extra["default_mailbox"] = str(config.get("default_mailbox") or "INBOX")
            missing = [key for key in ("host", "port", "tls_mode", "username") if key not in extra]
            if missing:
                raise ValidationError(
                    f"{credential.provider_key} credential missing config_json fields",
                    data={"credential_id": credential.id, "missing": missing},
                )
            extra["port"] = int(extra["port"])
            extra["timeout_s"] = float(extra.get("timeout_s") or 30)
        return extra

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
