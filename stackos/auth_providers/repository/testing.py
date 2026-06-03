"""Provider credential testing and safe account synchronization."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlmodel import select

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.db.models import Credential, CredentialAccount, IntegrationCredential
from stackos.repositories.base import Envelope, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .schema import AuthTestOut
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
        project_id: int,
        credential_ref: str,
    ) -> Envelope[AuthTestOut]:
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        integration_cls = _integration_class_for(row.kind)
        if integration_cls is None:
            raise ValidationError(
                f"auth provider {row.kind!r} has no test wrapper",
                data={"provider_key": row.kind, "credential_ref": credential.credential_ref},
            )
        assert row.id is not None
        secret_payload = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        extra = self._integration_extra(row)
        async with httpx.AsyncClient(timeout=30.0) as client:
            integration = integration_cls(
                payload=secret_payload,
                project_id=project_id,
                http=client,
                **extra,
            )
            raw_result = await integration.test_credentials()
        out = self._normalize_test_result(
            credential=credential,
            provider_key=row.kind,
            raw=raw_result,
        )
        now = utcnow()
        credential.last_tested_at = now
        credential.status = "connected" if out.ok else "failed"
        credential.updated_at = now
        self._s.add(credential)
        self._sync_account_from_test_result(
            credential=credential,
            provider_key=row.kind,
            ok=out.ok,
            metadata=out.metadata,
        )
        self.record_usage_event(
            credential=credential,
            provider_key=row.kind,
            operation="auth.test",
            status=out.status,
            metadata_json={"ok": out.ok, "metadata": out.metadata},
        )
        self._s.commit()
        return Envelope(data=out, project_id=project_id)

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
                project_id=credential.project_id,
                profile_key=credential.profile_key,
                current_credential_id=credential.id,
                current_integration_credential_id=credential.integration_credential_id,
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

    def _integration_extra(self, row: IntegrationCredential) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        config = row.config_json or {}
        if row.kind == "dataforseo":
            login = config.get("login")
            if not login:
                raise ValidationError(
                    "dataforseo credential missing config_json.login",
                    data={"credential_id": row.id},
                )
            extra["login"] = login
        elif row.kind == "wordpress":
            site_url = config.get("wp_url") or config.get("site_url") or config.get("base_url")
            if not site_url:
                raise ValidationError(
                    "wordpress credential missing config_json.wp_url",
                    data={"credential_id": row.id},
                )
            extra["site_url"] = str(site_url)
        elif row.kind == "ghost":
            site_url = config.get("ghost_url") or config.get("site_url") or config.get("base_url")
            if not site_url:
                raise ValidationError(
                    "ghost credential missing config_json.ghost_url",
                    data={"credential_id": row.id},
                )
            extra["site_url"] = str(site_url)
            if config.get("api_version"):
                extra["api_version"] = str(config["api_version"])
        elif row.kind == "openrouter":
            for key in ("http_referer", "app_title"):
                value = config.get(key)
                if isinstance(value, str) and value.strip():
                    extra[key] = value.strip()
        elif row.kind in {"telegram-bot", "slack-bot", "trackbooth"} and config.get(
            "api_base_url"
        ):
            extra["api_base_url"] = str(config["api_base_url"])
        elif row.kind in {"smtp", "imap"}:
            for key in ("host", "port", "tls_mode", "username", "timeout_s"):
                if key in config and config[key] is not None:
                    extra[key] = config[key]
            if row.kind == "imap":
                extra["default_mailbox"] = str(config.get("default_mailbox") or "INBOX")
            missing = [key for key in ("host", "port", "tls_mode", "username") if key not in extra]
            if missing:
                raise ValidationError(
                    f"{row.kind} credential missing config_json fields",
                    data={"credential_id": row.id, "missing": missing},
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
