"""Credential storage, revocation, and integration-row synchronization."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    Credential,
    CredentialScope,
    ExecutionContext,
    IntegrationCredential,
    ProjectCredential,
)
from stackos.repositories.base import ConflictError, Envelope, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .schema import (
    AccountOut,
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthFieldOut,
    AuthMethodOut,
    AuthRevokeOut,
)
from .utils import (
    credential_ref as new_credential_ref,
)
from .utils import (
    telegram_bot_id_from_token,
    utcnow,
)


class CredentialStorageMixin:
    """Store and revoke credentials while keeping secret payloads daemon-only."""

    def store_credential(
        self,
        *,
        provider_key: str,
        display_name: str,
        fields: dict[str, Any],
        auth_method_key: str | None = None,
        expires_at: datetime | None = None,
        attach_project_id: int | None = None,
    ) -> Envelope[AuthCredentialSetOut]:
        """Create one global Account and optionally attach it to a project."""
        provider = self._get_provider(provider_key)
        assert provider is not None
        if attach_project_id is not None:
            self._require_project(attach_project_id)
            self._require_provider_enabled_for_project(
                project_id=attach_project_id,
                provider=provider,
            )
        method = self._get_auth_method(provider, auth_method_key)
        assert method is not None
        name, name_key = self._account_name(display_name)
        existing = self._s.exec(
            select(Credential).where(
                col(Credential.provider_key) == provider.key,
                col(Credential.display_name_key) == name_key,
            )
        ).first()
        if existing is not None:
            raise ConflictError(
                "An Account with this name already exists for the provider",
                data={
                    "provider_key": provider.key,
                    "display_name": name,
                    "existing_credential_ref": existing.credential_ref,
                    "next_action": "Use the existing Account or choose a different name.",
                },
            )
        fields = self._with_provider_field_defaults(provider=provider, method=method, fields=fields)
        secret_values, safe_config = self._split_credential_fields(method=method, fields=fields)
        if provider.key == "ftp":
            from stackos.integrations.ftp import validate_ftp_credential_config

            try:
                validate_ftp_credential_config(safe_config)
            except ValueError as exc:
                raise ValidationError(str(exc), data={"provider_key": "ftp"}) from exc
        elif provider.key == "aws-s3":
            from stackos.integrations.s3 import (
                normalize_s3_prefix,
                validate_s3_credential_config,
            )

            try:
                if "prefix" in safe_config:
                    safe_config["prefix"] = normalize_s3_prefix(safe_config["prefix"])
                validate_s3_credential_config(safe_config)
            except ValueError as exc:
                raise ValidationError(
                    str(exc),
                    data={"provider_key": "aws-s3"},
                ) from exc
        safe_config["auth_method_key"] = method.key
        if provider.key == "telegram-bot" and method.key == "bot-token":
            bot_id = telegram_bot_id_from_token(secret_values.get("bot_token"))
            if bot_id is None:
                raise ValidationError(
                    "Telegram bot token must start with the numeric bot id",
                    data={"provider_key": provider.key, "auth_method_key": method.key},
                )
            self._assert_telegram_bot_account_available(
                bot_id=bot_id,
            )
            safe_config["provider_account_id"] = bot_id
        secret_payload = self._serialize_secret_payload(method=method, values=secret_values)
        if not method.interactive and self._method_requires_local_scope_gate(method):
            safe_config["scope_status"] = "unknown"
        resolved_status = "connected"
        if method.interactive:
            application_values = json.loads(secret_payload.decode("utf-8"))
            if not isinstance(application_values, dict):
                raise ValidationError("interactive OAuth application fields must use JSON")
            existing_payload: dict[str, Any] = {}
            existing_payload["_oauth_application_pending"] = application_values
            secret_payload = json.dumps(existing_payload, separators=(",", ":")).encode()
            resolved_status = "pending"
            safe_config["oauth_connection_status"] = resolved_status
            safe_config["oauth_pending"] = True
        account_ref = new_credential_ref()
        env = IntegrationCredentialRepository(self._s).set(
            credential_ref=account_ref,
            provider_key=provider.key,
            secret_payload=secret_payload,
            commit=False,
        )
        row = self._s.get(IntegrationCredential, env.data.id)
        if row is None:
            raise NotFoundError("stored credential row not found")
        credential = Credential(
            auth_provider_id=provider.id,
            integration_credential_id=row.id,
            credential_ref=account_ref,
            provider_key=provider.key,
            display_name=name,
            display_name_key=name_key,
            auth_type=method.auth_type,
            auth_method_key=method.key,
            status=resolved_status,
            expires_at=expires_at,
            config_json=self._safe_config(safe_config),
        )
        self._s.add(credential)
        try:
            self._s.flush()
        except IntegrityError as exc:
            self._s.rollback()
            raise ConflictError(
                "An Account with this name already exists for the provider",
                data={
                    "provider_key": provider.key,
                    "display_name": name,
                    "next_action": "Use the existing Account or choose a different name.",
                },
            ) from exc
        assert credential.id is not None
        if attach_project_id is not None:
            self._s.add(
                ProjectCredential(
                    project_id=attach_project_id,
                    credential_id=credential.id,
                    attached_by="local-admin",
                )
            )
        self.record_usage_event(
            credential=credential,
            provider_key=provider.key,
            operation="account.create",
            status=resolved_status,
            metadata_json={
                "source": "local-admin",
                "auth_method_key": method.key,
            },
            project_id=attach_project_id,
        )
        self._s.commit()
        out = self._account_out(credential)
        return Envelope(
            data=AuthCredentialSetOut(**out.model_dump()),
            project_id=attach_project_id,
        )

    def get_credential_edit_state(
        self,
        *,
        credential_ref: str,
    ) -> AuthCredentialEditOut:
        credential, row = self._global_credential(credential_ref)
        provider = self._get_provider(credential.provider_key)
        assert provider is not None
        method = self._get_auth_method(provider, credential.auth_method_key)
        assert method is not None
        secret_values = self._deserialize_secret_payload(method=method, row=row)
        config = credential.config_json or {}
        values = {
            field.key: config[field.key]
            for field in method.fields
            if not field.secret and field.key in config
        }
        return AuthCredentialEditOut(
            account=self._account_out(credential),
            values=values,
            secret_present={
                field.key: field.key in secret_values for field in method.fields if field.secret
            },
        )

    def update_credential(
        self,
        *,
        credential_ref: str,
        fields: dict[str, Any],
        display_name: str | None,
    ) -> Envelope[AuthCredentialSetOut]:
        credential, row = self._global_credential(credential_ref)
        provider = self._get_provider(credential.provider_key)
        assert provider is not None
        method = self._get_auth_method(provider, credential.auth_method_key)
        assert method is not None
        declared = {field.key: field for field in method.fields}
        unknown = sorted(set(fields) - set(declared))
        if unknown:
            raise ValidationError(
                "credential fields include keys not declared by the provider auth method",
                data={"unknown_fields": unknown, "auth_method_key": method.key},
            )
        existing_secret_values = self._deserialize_secret_payload(method=method, row=row)
        assert row.id is not None
        existing_secret_payload = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        merged: dict[str, Any] = {
            field.key: (credential.config_json or {})[field.key]
            for field in method.fields
            if not field.secret and field.key in (credential.config_json or {})
        }
        merged.update(existing_secret_values)
        merged.update(fields)
        merged = self._with_provider_field_defaults(
            provider=provider,
            method=method,
            fields=merged,
        )
        secret_values, safe_config = self._split_credential_fields(
            method=method,
            fields=merged,
        )
        if provider.key == "ftp":
            from stackos.integrations.ftp import validate_ftp_credential_config

            try:
                validate_ftp_credential_config(safe_config)
            except ValueError as exc:
                raise ValidationError(str(exc), data={"provider_key": "ftp"}) from exc
        elif provider.key == "aws-s3":
            from stackos.integrations.s3 import (
                normalize_s3_prefix,
                validate_s3_credential_config,
            )

            try:
                if "prefix" in safe_config:
                    safe_config["prefix"] = normalize_s3_prefix(safe_config["prefix"])
                validate_s3_credential_config(safe_config)
            except ValueError as exc:
                raise ValidationError(
                    str(exc),
                    data={"provider_key": "aws-s3"},
                ) from exc
        existing_config = dict(credential.config_json or {})
        existing_config.update(safe_config)
        safe_config = existing_config
        safe_config["auth_method_key"] = method.key
        if provider.key == "telegram-bot" and method.key == "bot-token":
            bot_id = telegram_bot_id_from_token(secret_values.get("bot_token"))
            if bot_id is None:
                raise ValidationError(
                    "Telegram bot token must start with the numeric bot id",
                    data={"provider_key": provider.key, "auth_method_key": method.key},
                )
            self._assert_telegram_bot_account_available(
                bot_id=bot_id,
                current_credential_id=credential.id,
            )
            safe_config["provider_account_id"] = bot_id
        if display_name is not None:
            name, name_key = self._account_name(display_name)
            duplicate = self._s.exec(
                select(Credential).where(
                    col(Credential.provider_key) == credential.provider_key,
                    col(Credential.display_name_key) == name_key,
                    col(Credential.id) != credential.id,
                )
            ).first()
            if duplicate is not None:
                raise ConflictError(
                    "An Account with this name already exists for the provider",
                    data={"existing_credential_ref": duplicate.credential_ref},
                )
            credential.display_name = name
            credential.display_name_key = name_key
        declared_secret_payload = self._serialize_secret_payload(
            method=method,
            values=secret_values,
        )
        changed_secret_fields = {
            field.key
            for field in method.fields
            if field.secret
            and field.key in fields
            and fields[field.key] != existing_secret_values.get(field.key)
        }
        scope_state_reset = bool(
            changed_secret_fields and self._method_requires_local_scope_gate(method)
        )
        resolved_status = credential.status
        secret_payload = declared_secret_payload
        if method.payload_format == "json" and not changed_secret_fields:
            # Safe-field and display-name edits must not discard acquired OAuth
            # tokens, pending application state, refresh material, or other
            # daemon-owned fields that are not part of the setup form.
            secret_payload = existing_secret_payload
        elif method.interactive:
            try:
                decoded = json.loads(existing_secret_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError(
                    "existing OAuth Account payload must be JSON before reconnect"
                ) from exc
            if not isinstance(decoded, dict):
                raise ValidationError(
                    "existing OAuth Account payload must be an object before reconnect"
                )
            application_values = json.loads(declared_secret_payload.decode("utf-8"))
            if not isinstance(application_values, dict):
                raise ValidationError("interactive OAuth application fields must use JSON")
            decoded["_oauth_application_pending"] = application_values
            decoded.pop("_oauth_pending", None)
            secret_payload = json.dumps(decoded, separators=(",", ":")).encode()
            has_active_credential = bool(
                credential.status == "connected"
                and any(decoded.get(key) for key in ("access_token", "refresh_token", "value"))
            )
            resolved_status = "connected" if has_active_credential else "pending"
            safe_config["oauth_connection_status"] = resolved_status
            safe_config["oauth_pending"] = True
        elif scope_state_reset:
            safe_config["scope_status"] = "unknown"
        IntegrationCredentialRepository(self._s).set(
            credential_ref=credential.credential_ref,
            provider_key=credential.provider_key,
            secret_payload=secret_payload,
            integration_credential_id=row.id,
            commit=False,
        )
        credential.config_json = self._safe_config(safe_config)
        credential.status = resolved_status
        credential.revoked_at = None
        credential.updated_at = utcnow()
        self._s.add(credential)
        if scope_state_reset and credential.id is not None:
            self._s.exec(
                delete(CredentialScope).where(col(CredentialScope.credential_id) == credential.id)
            )
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation="account.update",
            status=resolved_status,
            metadata_json={"source": "local-admin"},
        )
        self._s.commit()
        return Envelope(data=AuthCredentialSetOut(**self._account_out(credential).model_dump()))

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

    def _telegram_bot_id_for_credential(self, credential: Credential) -> str | None:
        config = credential.config_json or {}
        configured = config.get("provider_account_id") or config.get("telegram_bot_id")
        if configured is not None and str(configured).strip():
            return str(configured).strip()
        if credential.integration_credential_id is None:
            return None
        try:
            raw = IntegrationCredentialRepository(self._s).get_decrypted(
                credential.integration_credential_id
            )
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
        current_credential_id: int | None = None,
    ) -> None:
        rows = self._s.exec(
            select(Credential).where(
                col(Credential.provider_key) == "telegram-bot",
                col(Credential.revoked_at).is_(None),
                col(Credential.integration_credential_id).is_not(None),
            )
        ).all()
        for credential in rows:
            if credential.id == current_credential_id:
                continue
            if self._telegram_bot_id_for_credential(credential) != bot_id:
                continue
            raise ConflictError(
                "Telegram bot token is already claimed by another active Account",
                data={
                    "provider_key": "telegram-bot",
                    "provider_account_id": bot_id,
                    "existing_credential_ref": credential.credential_ref,
                },
            )

    def attach_account(
        self,
        *,
        project_id: int,
        credential_ref: str,
        attached_by: str | None = "local-admin",
    ) -> Envelope[AccountOut]:
        self._require_project(project_id)
        credential, _ = self._global_credential(credential_ref)
        provider = self._get_provider(credential.provider_key)
        assert provider is not None
        self._require_provider_enabled_for_project(project_id=project_id, provider=provider)
        assert credential.id is not None
        existing = self._s.exec(
            select(ProjectCredential).where(
                col(ProjectCredential.project_id) == project_id,
                col(ProjectCredential.credential_id) == credential.id,
            )
        ).first()
        if existing is None:
            self._s.add(
                ProjectCredential(
                    project_id=project_id,
                    credential_id=credential.id,
                    attached_by=attached_by,
                )
            )
            try:
                self._s.commit()
            except IntegrityError as exc:
                self._s.rollback()
                existing = self._s.exec(
                    select(ProjectCredential).where(
                        col(ProjectCredential.project_id) == project_id,
                        col(ProjectCredential.credential_id) == credential.id,
                    )
                ).first()
                if existing is None:
                    raise ConflictError(
                        "Account changed while it was being attached",
                        data={
                            "project_id": project_id,
                            "credential_ref": credential_ref,
                            "retryable": True,
                            "next_action": (
                                "Refresh Accounts and retry only if the Account still exists."
                            ),
                        },
                    ) from exc
                credential = self._s.exec(
                    select(Credential).where(col(Credential.credential_ref) == credential_ref)
                ).one()
        return Envelope(data=self._account_out(credential), project_id=project_id)

    def detach_account(
        self,
        *,
        project_id: int,
        credential_ref: str,
    ) -> Envelope[AccountOut]:
        self._require_project(project_id)
        credential, _ = self._global_credential(credential_ref)
        assert credential.id is not None
        attachment = self._s.exec(
            select(ProjectCredential).where(
                col(ProjectCredential.project_id) == project_id,
                col(ProjectCredential.credential_id) == credential.id,
            )
        ).first()
        if attachment is None:
            raise NotFoundError(
                f"Account {credential_ref!r} is not attached to project {project_id}"
            )
        active_context = self._s.exec(
            select(ExecutionContext).where(
                col(ExecutionContext.project_id) == project_id,
                col(ExecutionContext.credential_ref) == credential_ref,
                col(ExecutionContext.status) == "active",
            )
        ).first()
        if active_context is not None:
            raise ConflictError(
                "Account is used by an active project execution context",
                data={
                    "credential_ref": credential_ref,
                    "context_ref": active_context.context_ref,
                    "next_action": "Rebind or disable the active context before detaching.",
                },
            )
        from stackos.communications import communication_profile_account_uses

        for use in communication_profile_account_uses(self._s, project_id=project_id):
            if not use["profile_enabled"] or use["credential_ref"] != credential_ref:
                continue
            raise ConflictError(
                "Account is used by an active project communication profile",
                data={
                    "credential_ref": credential_ref,
                    "profile_ref": use["profile_ref"],
                    "provider_key": use["provider_key"],
                    "next_action": (
                        "Rebind or disable the communication profile before detaching."
                    ),
                },
            )
        self._s.delete(attachment)
        self._s.commit()
        return Envelope(data=self._account_out(credential), project_id=project_id)

    def revoke(
        self,
        *,
        credential_ref: str,
    ) -> Envelope[AuthRevokeOut]:
        credential, row = self._global_credential(credential_ref)
        assert credential.id is not None
        project_ids = self._project_ids_for_credential(credential.id)
        if project_ids:
            raise ConflictError(
                "Account is still attached to projects",
                data={
                    "credential_ref": credential_ref,
                    "project_ids": project_ids,
                    "next_action": "Detach the Account from every project before revoking it.",
                },
            )
        now = utcnow()
        account_ref = credential.credential_ref
        provider_key = credential.provider_key
        self.record_usage_event(
            credential=credential,
            provider_key=provider_key,
            operation="account.revoke",
            status="revoked",
            metadata_json={"credential_ref": account_ref},
        )
        credential.status = "revoked"
        credential.revoked_at = now
        credential.expires_at = None
        credential.integration_credential_id = None
        credential.display_name_key = f"revoked:{account_ref}"
        credential.config_json = self._safe_config(
            {
                **dict(credential.config_json or {}),
                "audit_tombstone": True,
                "secret_material_removed": True,
            }
        )
        self._s.flush()
        if row.id is not None:
            IntegrationCredentialRepository(self._s).remove(int(row.id), commit=False)
        try:
            self._s.commit()
        except IntegrityError as exc:
            self._s.rollback()
            current = self._s.exec(
                select(Credential).where(col(Credential.credential_ref) == credential_ref)
            ).first()
            if current is not None and current.id is not None:
                project_ids = self._project_ids_for_credential(current.id)
                if project_ids:
                    raise ConflictError(
                        "Account was attached while revocation was in progress",
                        data={
                            "credential_ref": credential_ref,
                            "project_ids": project_ids,
                            "next_action": (
                                "Detach the Account from every project before revoking it."
                            ),
                        },
                    ) from exc
            raise ConflictError(
                "Account changed while it was being revoked",
                data={
                    "credential_ref": credential_ref,
                    "retryable": True,
                    "next_action": "Refresh Accounts before retrying revocation.",
                },
            ) from exc
        return Envelope(
            data=AuthRevokeOut(
                credential_ref=account_ref,
                provider_key=provider_key,
                revoked_at=now,
            ),
        )

    def _global_credential(
        self,
        credential_ref: str,
    ) -> tuple[Credential, IntegrationCredential]:
        credential = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            raise NotFoundError(f"Account {credential_ref!r} not found")
        if credential.revoked_at is not None or credential.integration_credential_id is None:
            raise ConflictError(
                "Account is revoked",
                data={"credential_ref": credential_ref},
            )
        row = self._s.get(IntegrationCredential, credential.integration_credential_id)
        if row is None:
            raise NotFoundError(
                "backing credential not found",
                data={"credential_ref": credential_ref},
            )
        return credential, row

    def _account_name(self, display_name: str) -> tuple[str, str]:
        name = " ".join(display_name.split())
        if not name:
            raise ValidationError("Account name is required")
        if len(name) > 200:
            raise ValidationError("Account name must be at most 200 characters")
        return name, name.casefold()

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
        if field.type == "select":
            if isinstance(raw, bool):
                value = "true" if raw else "false"
                normalized: Any = raw
            elif isinstance(raw, str):
                value = raw.strip()
                normalized = value
            elif isinstance(raw, int | float):
                value = str(raw)
                normalized = raw
            else:
                value = ""
                normalized = raw
            if not value:
                raise ValidationError(
                    f"credential field {field.key} must be one declared selection",
                    data={"field": field.key},
                )
            allowed = {
                str(option.get("value") or "").strip()
                for option in field.options or []
                if str(option.get("value") or "").strip()
            }
            if allowed and value not in allowed:
                raise ValidationError(
                    f"credential field {field.key} includes an unknown selection",
                    data={"field": field.key, "unknown": [value]},
                )
            return normalized
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
