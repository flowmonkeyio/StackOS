"""Credential storage, revocation, and integration-row synchronization."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import Credential, CredentialScope, IntegrationCredential
from stackos.repositories.base import ConflictError, Envelope, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .schema import (
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthFieldOut,
    AuthMethodOut,
    AuthRevokeOut,
)
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
        self._require_provider_enabled_for_project(project_id=project_id, provider=provider)
        method = self._get_auth_method(provider, auth_method_key)
        assert method is not None
        profile_key = self._normalize_profile_key(profile_key)
        existing = self._s.exec(
            select(IntegrationCredential).where(
                IntegrationCredential.project_id == project_id,
                IntegrationCredential.kind == provider.key,
                IntegrationCredential.profile_key == profile_key,
            )
        ).first()
        existing_credential = None
        existing_secret_payload: bytes | None = None
        if existing is not None and existing.id is not None:
            existing_credential = self._s.exec(
                select(Credential).where(col(Credential.integration_credential_id) == existing.id)
            ).first()
            existing_secret_payload = IntegrationCredentialRepository(self._s).get_decrypted(
                existing.id
            )
        fields = self._with_provider_field_defaults(provider=provider, method=method, fields=fields)
        secret_values, safe_config = self._split_credential_fields(method=method, fields=fields)
        if provider.key == "ftp":
            from stackos.integrations.ftp import validate_ftp_credential_config

            try:
                validate_ftp_credential_config(safe_config)
            except ValueError as exc:
                raise ValidationError(str(exc), data={"provider_key": "ftp"}) from exc
        existing_config = dict(existing.config_json or {}) if existing is not None else {}
        previous_auth_method_key = existing_config.get("auth_method_key")
        existing_config.update(safe_config)
        safe_config = existing_config
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
        scoped_noninteractive_method = not method.interactive and method.auth_type in {
            "oauth",
            "oauth-client-credentials",
        }
        declared_material_matches = False
        if (
            scoped_noninteractive_method
            and existing is not None
            and existing_secret_payload is not None
            and previous_auth_method_key == method.key
        ):
            try:
                existing_declared_payload = self._serialize_secret_payload(
                    method=method,
                    values=self._deserialize_secret_payload(method=method, row=existing),
                )
            except ValidationError:
                pass
            else:
                declared_material_matches = existing_declared_payload == secret_payload
                if declared_material_matches:
                    secret_payload = existing_secret_payload
        scope_state_reset = False
        if not method.interactive:
            scope_state_reset = bool(
                existing_credential is not None
                and (
                    previous_auth_method_key != method.key
                    or (scoped_noninteractive_method and not declared_material_matches)
                )
            )
            if scoped_noninteractive_method and (existing is None or scope_state_reset):
                safe_config["scope_status"] = "unknown"
            elif scope_state_reset:
                safe_config.pop("scope_status", None)
        resolved_status = "connected"
        if method.interactive:
            application_values = json.loads(secret_payload.decode("utf-8"))
            if not isinstance(application_values, dict):
                raise ValidationError("interactive OAuth application fields must use JSON")
            existing_payload: dict[str, Any] = {}
            if existing is not None and existing.id is not None:
                try:
                    assert existing_secret_payload is not None
                    decoded = json.loads(existing_secret_payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValidationError(
                        "existing OAuth credential payload must be JSON before reconnect"
                    ) from exc
                if not isinstance(decoded, dict):
                    raise ValidationError(
                        "existing OAuth credential payload must be an object before reconnect"
                    )
                existing_payload = decoded
            existing_payload["_oauth_application_pending"] = application_values
            existing_payload.pop("_oauth_pending", None)
            secret_payload = json.dumps(existing_payload, separators=(",", ":")).encode()
            has_active_credential = bool(
                existing_credential is not None
                and existing_credential.status == "connected"
                and any(
                    existing_payload.get(key) for key in ("access_token", "refresh_token", "value")
                )
            )
            resolved_status = "connected" if has_active_credential else "pending"
            safe_config["oauth_connection_status"] = resolved_status
            safe_config["oauth_pending"] = True
            if has_active_credential and expires_at is None and existing is not None:
                expires_at = existing.expires_at
        env = IntegrationCredentialRepository(self._s).set(
            project_id=project_id,
            kind=provider.key,
            secret_payload=secret_payload,
            profile_key=profile_key,
            config_json=safe_config,
            expires_at=expires_at,
            commit=False,
        )
        row = self._s.get(IntegrationCredential, env.data.id)
        if row is None:
            raise NotFoundError("stored credential row not found")
        credential = self._ensure_credential(row, status=resolved_status, method=method)
        if scope_state_reset and credential.id is not None:
            self._s.exec(
                delete(CredentialScope).where(col(CredentialScope.credential_id) == credential.id)
            )
        out = self._connection_out(credential, row)
        self.record_usage_event(
            credential=credential,
            provider_key=provider.key,
            operation="auth.credential.set",
            status=resolved_status,
            metadata_json={
                "source": "local-admin",
                "auth_method_key": method.key,
                "profile_key": profile_key,
            },
        )
        self._s.commit()
        return Envelope(data=AuthCredentialSetOut(**out.model_dump()), project_id=project_id)

    def get_credential_edit_state(
        self,
        *,
        project_id: int,
        credential_ref: str,
    ) -> AuthCredentialEditOut:
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        provider = self._get_provider(row.kind)
        assert provider is not None
        method = self._get_auth_method(
            provider,
            (row.config_json or {}).get("auth_method_key"),
        )
        assert method is not None
        secret_values = self._deserialize_secret_payload(method=method, row=row)
        config = row.config_json or {}
        values = {
            field.key: config[field.key]
            for field in method.fields
            if not field.secret and field.key in config
        }
        return AuthCredentialEditOut(
            connection=self._connection_out(credential, row),
            values=values,
            secret_present={
                field.key: field.key in secret_values for field in method.fields if field.secret
            },
        )

    def update_credential(
        self,
        *,
        project_id: int,
        credential_ref: str,
        fields: dict[str, Any],
        label: str | None,
    ) -> Envelope[AuthCredentialSetOut]:
        _, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        provider = self._get_provider(row.kind)
        assert provider is not None
        method = self._get_auth_method(
            provider,
            (row.config_json or {}).get("auth_method_key"),
        )
        assert method is not None
        declared = {field.key: field for field in method.fields}
        unknown = sorted(set(fields) - set(declared))
        if unknown:
            raise ValidationError(
                "credential fields include keys not declared by the provider auth method",
                data={"unknown_fields": unknown, "auth_method_key": method.key},
            )
        merged: dict[str, Any] = {
            field.key: (row.config_json or {})[field.key]
            for field in method.fields
            if not field.secret and field.key in (row.config_json or {})
        }
        merged.update(self._deserialize_secret_payload(method=method, row=row))
        merged.update(fields)
        return self.store_credential(
            project_id=project_id,
            provider_key=row.kind,
            auth_method_key=method.key,
            profile_key=row.profile_key,
            label=label,
            fields=merged,
            expires_at=row.expires_at,
        )

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
            IntegrationCredentialRepository(self._s).remove(int(row.id), commit=False)
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
            is_blank = raw is None or (
                isinstance(raw, str) and (raw == "" if field.secret else not raw.strip())
            )
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
        if not isinstance(raw, str) or raw == "":
            raise ValidationError(
                f"secret credential field {field.key} must be a non-empty string",
                data={"field": field.key},
            )
        return raw

    def _safe_field_value(self, *, field: AuthFieldOut, raw: Any) -> Any:
        if field.type in {"multi-select", "multiselect"}:
            if isinstance(raw, str):
                values = [item.strip() for item in raw.split(",") if item.strip()]
            elif isinstance(raw, list):
                values = [str(item).strip() for item in raw if str(item).strip()]
            else:
                raise ValidationError(
                    f"credential field {field.key} must be a list or comma-separated string",
                    data={"field": field.key},
                )
            values = list(dict.fromkeys(values))
            allowed = {
                str(option.get("value") or "").strip()
                for option in field.options or []
                if str(option.get("value") or "").strip()
            }
            unknown = sorted(set(values) - allowed) if allowed else []
            if unknown:
                raise ValidationError(
                    f"OAuth scope bundle field {field.key} includes unknown selections",
                    data={"field": field.key, "unknown": unknown},
                )
            return values
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

    def _deserialize_secret_payload(
        self,
        *,
        method: AuthMethodOut,
        row: IntegrationCredential,
    ) -> dict[str, str]:
        assert row.id is not None
        payload = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        if method.payload_format == "none":
            return {}
        if method.payload_format == "raw":
            field_key = method.payload_field
            if field_key is None:
                secret_fields = [field.key for field in method.fields if field.secret]
                if len(secret_fields) != 1:
                    raise ValidationError(
                        "raw credential contract must declare one secret payload field",
                        data={"auth_method_key": method.key},
                    )
                field_key = secret_fields[0]
            return {field_key: payload.decode("utf-8")}
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                "stored credential payload is invalid",
                data={"auth_method_key": method.key},
            ) from exc
        if not isinstance(decoded, Mapping):
            raise ValidationError(
                "stored credential payload does not match its auth method",
                data={"auth_method_key": method.key},
            )
        pending_application = decoded.get("_oauth_application_pending")
        sources = (
            (pending_application, decoded)
            if isinstance(pending_application, Mapping)
            else (decoded,)
        )
        values: dict[str, str] = {}
        for field in method.fields:
            if not field.secret:
                continue
            for source in sources:
                if field.key not in source:
                    continue
                value = source[field.key]
                if not isinstance(value, str):
                    raise ValidationError(
                        "stored credential payload does not match its auth method",
                        data={"auth_method_key": method.key, "field": field.key},
                    )
                values[field.key] = value
                break
        return values

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
