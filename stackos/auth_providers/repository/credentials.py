"""Credential storage, revocation, and integration-row synchronization."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from sqlmodel import select

from stackos.artifacts import redact_secrets
from stackos.db.models import Credential, IntegrationCredential
from stackos.repositories.base import ConflictError, Envelope, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .schema import AuthCredentialSetOut, AuthFieldOut, AuthMethodOut, AuthRevokeOut
from .utils import (
    is_valid_profile_key,
    telegram_bot_id_from_token,
    utcnow,
)


class CredentialStorageMixin:
    """Store and revoke credentials while keeping secret payloads daemon-only."""

    def store_credential(
        self,
        *,
        project_id: int,
        provider_key: str,
        fields: dict[str, Any],
        auth_method_key: str | None = None,
        profile_key: str = "default",
        label: str | None = None,
        expires_at: datetime | None = None,
    ) -> Envelope[AuthCredentialSetOut]:
        self._require_project(project_id)
        provider = self._get_provider(provider_key)
        assert provider is not None
        method = self._get_auth_method(provider, auth_method_key)
        assert method is not None
        profile_key = self._normalize_profile_key(profile_key)
        fields = self._with_provider_field_defaults(provider=provider, method=method, fields=fields)
        secret_values, safe_config = self._split_credential_fields(method=method, fields=fields)
        safe_config["auth_method_key"] = method.key
        safe_config["profile_key"] = profile_key
        if provider.key == "telegram-bot" and method.key == "bot-token":
            bot_id = telegram_bot_id_from_token(secret_values.get("bot_token"))
            if bot_id is None:
                raise ValidationError(
                    "Telegram bot token must start with the numeric bot id",
                    data={"provider_key": provider.key, "auth_method_key": method.key},
                )
            self._assert_telegram_bot_account_available(
                bot_id=bot_id,
                project_id=project_id,
                profile_key=profile_key,
            )
            safe_config["provider_account_id"] = bot_id
        if label is not None and label.strip():
            safe_config["label"] = label.strip()
        secret_payload = self._serialize_secret_payload(method=method, values=secret_values)
        env = IntegrationCredentialRepository(self._s).set(
            project_id=project_id,
            kind=provider.key,
            secret_payload=secret_payload,
            profile_key=profile_key,
            config_json=safe_config,
            expires_at=expires_at,
        )
        row = self._s.get(IntegrationCredential, env.data.id)
        if row is None:
            raise NotFoundError("stored credential row not found")
        credential = self._ensure_credential(row, status="connected", method=method)
        out = self._connection_out(credential, row)
        self.record_usage_event(
            credential=credential,
            provider_key=provider.key,
            operation="auth.credential.set",
            status="connected",
            metadata_json={
                "source": "local-admin",
                "auth_method_key": method.key,
                "profile_key": profile_key,
            },
        )
        self._s.commit()
        return Envelope(data=AuthCredentialSetOut(**out.model_dump()), project_id=project_id)

    def _with_provider_field_defaults(
        self,
        *,
        provider: Any,
        method: AuthMethodOut,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Fill daemon-owned credential defaults that should not burden setup UI."""

        if (
            provider.key == "telegram-bot"
            and method.key == "bot-token"
            and not str(fields.get("webhook_secret_token") or "").strip()
        ):
            fields = dict(fields)
            fields["webhook_secret_token"] = secrets.token_urlsafe(32)
        return fields

    def _telegram_bot_id_for_integration(self, row: IntegrationCredential) -> str | None:
        config = row.config_json or {}
        configured = config.get("provider_account_id") or config.get("telegram_bot_id")
        if configured is not None and str(configured).strip():
            return str(configured).strip()
        if row.id is None:
            return None
        try:
            raw = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        token = payload.get("bot_token")
        return telegram_bot_id_from_token(str(token)) if token is not None else None

    def _assert_telegram_bot_account_available(
        self,
        *,
        bot_id: str,
        project_id: int | None,
        profile_key: str,
        current_credential_id: int | None = None,
        current_integration_credential_id: int | None = None,
    ) -> None:
        rows = self._s.exec(
            select(IntegrationCredential).where(IntegrationCredential.kind == "telegram-bot")
        ).all()
        for row in rows:
            if row.id is not None and row.id == current_integration_credential_id:
                continue
            if (
                current_integration_credential_id is None
                and row.project_id == project_id
                and row.profile_key == profile_key
            ):
                continue
            if self._telegram_bot_id_for_integration(row) != bot_id:
                continue
            credential = self._s.exec(
                select(Credential).where(Credential.integration_credential_id == row.id)
            ).first()
            if credential is not None and credential.id == current_credential_id:
                continue
            if credential is not None and (
                credential.revoked_at is not None or credential.integration_credential_id is None
            ):
                continue
            raise ConflictError(
                "Telegram bot token is already claimed by another active connection",
                data={
                    "provider_key": "telegram-bot",
                    "provider_account_id": bot_id,
                    "existing_project_id": row.project_id,
                    "existing_profile_key": row.profile_key,
                },
            )

    def revoke(
        self,
        *,
        project_id: int,
        credential_ref: str,
    ) -> Envelope[AuthRevokeOut]:
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        now = utcnow()
        if row.id is not None:
            IntegrationCredentialRepository(self._s).remove(int(row.id))
        credential.integration_credential_id = None
        credential.status = "revoked"
        credential.revoked_at = now
        credential.updated_at = now
        self._s.add(credential)
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation="auth.revoke",
            status="revoked",
            metadata_json={},
        )
        self._s.commit()
        return Envelope(
            data=AuthRevokeOut(
                credential_ref=credential.credential_ref,
                provider_key=credential.provider_key,
                project_id=credential.project_id,
                revoked_at=now,
            ),
            project_id=project_id,
        )

    def sync_credential_for_integration(self, integration_credential_id: int) -> Credential:
        row = self._s.get(IntegrationCredential, integration_credential_id)
        if row is None:
            raise NotFoundError(f"credential {integration_credential_id} not found")
        credential = self._ensure_credential(row)
        self._s.commit()
        return credential

    def _normalize_profile_key(self, profile_key: str) -> str:
        normalized = profile_key.strip().lower().replace(" ", "-")
        if not is_valid_profile_key(normalized):
            raise ValidationError(
                "profile_key must start with a letter and contain only lowercase letters, "
                "numbers, underscores, or hyphens",
                data={"profile_key": profile_key},
            )
        return normalized

    def _split_credential_fields(
        self,
        *,
        method: AuthMethodOut,
        fields: dict[str, Any],
    ) -> tuple[dict[str, str], dict[str, Any]]:
        declared = {field.key: field for field in method.fields}
        unknown = sorted(set(fields) - set(declared))
        if unknown:
            raise ValidationError(
                "credential fields include keys not declared by the provider auth method",
                data={"unknown_fields": unknown, "auth_method_key": method.key},
            )
        secret_values: dict[str, str] = {}
        safe_config: dict[str, Any] = {}
        for field in method.fields:
            raw = fields.get(field.key)
            is_blank = raw is None or (isinstance(raw, str) and not raw.strip())
            if field.required and is_blank:
                raise ValidationError(
                    f"{method.key} credential missing {field.key}",
                    data={"auth_method_key": method.key, "field": field.key},
                )
            if is_blank:
                continue
            if field.secret:
                secret_values[field.key] = self._secret_field_value(field=field, raw=raw)
            else:
                safe_config[field.key] = self._safe_field_value(field=field, raw=raw)
        if redact_secrets(safe_config) != safe_config:
            raise ValidationError(
                "non-secret credential fields include secret-like keys; mark them as secret "
                "in the provider auth method",
                data={"auth_method_key": method.key},
            )
        return secret_values, safe_config

    def _secret_field_value(self, *, field: AuthFieldOut, raw: Any) -> str:
        value = raw.strip() if isinstance(raw, str) else str(raw).strip()
        if not value:
            raise ValidationError(
                f"secret credential field {field.key} must be a non-empty string",
                data={"field": field.key},
            )
        return value

    def _safe_field_value(self, *, field: AuthFieldOut, raw: Any) -> Any:
        if field.type == "number":
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                text = raw.strip()
                try:
                    return int(text) if text.isdigit() else float(text)
                except ValueError as exc:
                    raise ValidationError(
                        f"credential field {field.key} must be numeric",
                        data={"field": field.key},
                    ) from exc
            raise ValidationError(
                f"credential field {field.key} must be numeric",
                data={"field": field.key},
            )
        if field.type == "boolean":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                text = raw.strip().lower()
                if text in {"true", "1", "yes", "on"}:
                    return True
                if text in {"false", "0", "no", "off"}:
                    return False
            raise ValidationError(
                f"credential field {field.key} must be boolean",
                data={"field": field.key},
            )
        return raw.strip() if isinstance(raw, str) else raw

    def _serialize_secret_payload(self, *, method: AuthMethodOut, values: dict[str, str]) -> bytes:
        if method.payload_format == "none":
            if values:
                raise ValidationError(
                    "auth method does not accept secret fields",
                    data={"auth_method_key": method.key},
                )
            return b""
        if method.payload_format == "raw":
            field_key = method.payload_field
            if field_key is None:
                if len(values) != 1:
                    raise ValidationError(
                        "raw credential payloads require one secret field or payload_field",
                        data={"auth_method_key": method.key},
                    )
                field_key = next(iter(values))
            value = values.get(field_key)
            if value is None:
                raise ValidationError(
                    f"raw credential payload missing {field_key}",
                    data={"auth_method_key": method.key, "payload_field": field_key},
                )
            return value.encode("utf-8")
        if method.payload_format != "json":
            raise ValidationError(
                f"unsupported auth method payload_format {method.payload_format!r}",
                data={"auth_method_key": method.key},
            )
        return json.dumps(values, separators=(",", ":"), sort_keys=True).encode("utf-8")
