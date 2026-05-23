"""Generic auth provider repository over encrypted credential storage."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import Session, col, select

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.config import Settings
from stackos.db.models import (
    AuthProvider,
    Credential,
    CredentialAccount,
    CredentialRefreshEvent,
    CredentialScope,
    CredentialUsageEvent,
    IntegrationCredential,
    OAuthState,
    Plugin,
    Project,
    Provider,
)
from stackos.integrations import integration_class_for
from stackos.repositories.base import ConflictError, Envelope, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

_PROFILE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,119}$")
_TELEGRAM_BOT_TOKEN_RE = re.compile(r"^(?P<bot_id>\d+):\S+$")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _credential_ref() -> str:
    return f"cred_{secrets.token_urlsafe(18)}"


class AuthFieldOut(BaseModel):
    key: str
    label: str
    type: str = "text"
    secret: bool = False
    required: bool = False
    placeholder: str | None = None
    description: str | None = None
    options: list[dict[str, str]] | None = None


class AuthMethodOut(BaseModel):
    key: str
    label: str
    auth_type: str
    description: str = ""
    interactive: bool = False
    payload_format: str = "json"
    payload_field: str | None = None
    fields: list[AuthFieldOut] = Field(default_factory=list)
    config: dict[str, Any] | None = None


class AuthProviderOut(BaseModel):
    id: int
    plugin_id: int | None
    plugin_slug: str | None
    key: str
    name: str
    description: str
    auth_type: str
    auth_methods: list[AuthMethodOut] = Field(default_factory=list)
    scopes: list[str]
    config_json: dict[str, Any] | None


class CredentialConnectionOut(BaseModel):
    credential_ref: str
    project_id: int | None
    provider_key: str
    auth_type: str
    auth_method_key: str
    profile_key: str
    label: str | None = None
    status: str
    expires_at: datetime | None
    last_tested_at: datetime | None
    revoked_at: datetime | None
    scopes: list[str]
    account: dict[str, Any] | None = None
    setup_required: bool = False


class AuthStatusOut(BaseModel):
    project_id: int | None
    provider_key: str | None
    providers: list[AuthProviderOut]
    connections: list[CredentialConnectionOut]


class AuthStartOut(BaseModel):
    project_id: int
    provider_key: str
    auth_type: str
    auth_method_key: str
    status: str
    setup_url: str | None = None
    authorization_url: str | None = None
    redirect_uri: str | None = None
    state: str | None = None
    credential_ref: str | None = None
    expires_at: datetime | None = None


class AuthTestOut(BaseModel):
    credential_ref: str
    provider_key: str
    ok: bool
    status: str
    summary: str
    checked_at: str
    retryable: bool = False
    next_action: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthRevokeOut(BaseModel):
    credential_ref: str
    provider_key: str
    project_id: int | None
    revoked_at: datetime
    status: str = "revoked"


class AuthCredentialSetOut(CredentialConnectionOut):
    """Sanitized result for a local-admin credential profile write."""


@dataclass(frozen=True)
class ResolvedCredential:
    """Daemon-internal credential bundle for connector execution.

    The secret payload is intentionally not a Pydantic model field and is hidden
    from repr; callers must never serialize this object into MCP/REST
    responses or audit JSON.
    """

    credential: Credential
    integration: IntegrationCredential
    secret_payload: bytes = dataclass_field(repr=False)
    config_json: dict[str, Any] | None = dataclass_field(default=None, repr=False)

    @property
    def credential_ref(self) -> str:
        return self.credential.credential_ref

    @property
    def credential_id(self) -> int | None:
        return self.credential.id

    @property
    def provider_key(self) -> str:
        return self.credential.provider_key


class AuthRepository:
    """Auth-provider facade that never returns encrypted payloads or plaintext secrets."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def sync_providers(self) -> None:
        """Mirror plugin provider auth metadata into auth_providers."""
        from stackos.repositories.plugins import PluginRepository

        PluginRepository(self._s).sync_builtin_plugins()
        rows = self._s.exec(
            select(Provider, Plugin)
            .join(Plugin, col(Provider.plugin_id) == col(Plugin.id))
            .order_by(col(Plugin.slug).asc(), col(Provider.key).asc())
        ).all()
        now = _utcnow()
        for provider, plugin in rows:
            row = self._s.exec(
                select(AuthProvider).where(
                    AuthProvider.plugin_id == plugin.id,
                    AuthProvider.key == provider.key,
                )
            ).first()
            scopes = None
            if provider.config_json and isinstance(provider.config_json.get("scopes"), list):
                scopes = [str(scope) for scope in provider.config_json["scopes"]]
            if row is None:
                row = AuthProvider(
                    plugin_id=plugin.id,
                    key=provider.key,
                    name=provider.name,
                    description=provider.description,
                    auth_type=provider.auth_type,
                    scopes_json=scopes,
                    config_json=self._safe_provider_config(provider.config_json),
                )
            else:
                row.name = provider.name
                row.description = provider.description
                row.auth_type = provider.auth_type
                row.scopes_json = scopes
                row.config_json = self._safe_provider_config(provider.config_json)
                row.updated_at = now
            self._s.add(row)
        self._s.commit()

    def list_providers(self, *, provider_key: str | None = None) -> list[AuthProviderOut]:
        self.sync_providers()
        stmt = select(AuthProvider, Plugin).outerjoin(
            Plugin, col(AuthProvider.plugin_id) == col(Plugin.id)
        )
        if provider_key is not None:
            stmt = stmt.where(col(AuthProvider.key) == provider_key)
        rows = self._s.exec(stmt.order_by(col(AuthProvider.key).asc())).all()
        return [self._provider_out(provider, plugin) for provider, plugin in rows]

    def status(
        self,
        *,
        project_id: int | None,
        provider_key: str | None = None,
    ) -> AuthStatusOut:
        providers = self.list_providers(provider_key=provider_key)
        integration_rows = self._integration_rows(project_id=project_id, provider_key=provider_key)
        seen_credential_ids: set[int] = set()
        connections: list[CredentialConnectionOut] = []
        touched = False
        for row in integration_rows:
            credential = self._ensure_credential(row)
            touched = True
            if credential.id is not None:
                seen_credential_ids.add(credential.id)
            connections.append(self._connection_out(credential, row))
        for credential in self._credential_rows(project_id=project_id, provider_key=provider_key):
            if credential.id in seen_credential_ids:
                continue
            connections.append(self._connection_out(credential, None))
        if touched:
            self._s.commit()
        return AuthStatusOut(
            project_id=project_id,
            provider_key=provider_key,
            providers=providers,
            connections=connections,
        )

    def start(
        self,
        *,
        project_id: int,
        provider_key: str,
        settings: Settings,
        auth_method_key: str | None = None,
        redirect_uri: str | None = None,
    ) -> Envelope[AuthStartOut]:
        self._require_project(project_id)
        provider = self._get_provider(provider_key)
        assert provider is not None
        method = self._get_auth_method(provider, auth_method_key)
        assert method is not None
        _ = redirect_uri
        setup_url = self._local_setup_url(
            settings=settings,
            project_id=project_id,
            provider_key=provider_key,
        )
        setup_required = provider.auth_type not in {"none", "local"}
        status = "not-required"
        if setup_required and method.interactive:
            status = "requires-oauth"
        elif setup_required:
            status = "requires-local-credential"
        return Envelope(
            data=AuthStartOut(
                project_id=project_id,
                provider_key=provider.key,
                auth_type=method.auth_type,
                auth_method_key=method.key,
                status=status,
                setup_url=setup_url if setup_required else None,
                authorization_url=self._authorization_url(method),
            ),
            project_id=project_id,
        )

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
            bot_id = self._telegram_bot_id_from_token(secret_values.get("bot_token"))
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
        provider: AuthProvider,
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
        integration_cls = integration_class_for(row.kind)
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
        now = _utcnow()
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
        if not ok or provider_key != "telegram-bot" or credential.id is None:
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
        now = _utcnow()
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

    def _telegram_bot_id_from_token(self, token: str | None) -> str | None:
        if token is None:
            return None
        match = _TELEGRAM_BOT_TOKEN_RE.match(token.strip())
        return match.group("bot_id") if match else None

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
        return self._telegram_bot_id_from_token(str(token)) if token is not None else None

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
            select(IntegrationCredential).where(col(IntegrationCredential.kind) == "telegram-bot")
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

    def resolve_for_execution(
        self,
        *,
        project_id: int,
        provider_key: str | None,
        credential_ref: str | None,
        operation: str,
    ) -> ResolvedCredential:
        """Resolve a credential ref for an in-process connector call.

        This is not an agent-facing read. It decrypts the backing payload only
        inside the daemon, records a redacted usage event, and returns an
        internal dataclass whose secret fields must not be serialized.
        """
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
        )
        if credential.status != "connected":
            raise ConflictError(
                "credential is not connected",
                data={
                    "credential_ref": credential.credential_ref,
                    "status": credential.status,
                },
            )
        if provider_key is not None and credential.provider_key != provider_key:
            raise ValidationError(
                "credential provider does not match action provider",
                data={
                    "credential_ref": credential.credential_ref,
                    "credential_provider": credential.provider_key,
                    "action_provider": provider_key,
                },
            )
        assert row.id is not None
        secret_payload = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        self.record_usage_event(
            credential=credential,
            provider_key=credential.provider_key,
            operation=operation,
            status="used",
            metadata_json={"credential_ref": credential.credential_ref},
        )
        return ResolvedCredential(
            credential=credential,
            integration=row,
            secret_payload=secret_payload,
            config_json=row.config_json,
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
        now = _utcnow()
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

    def record_usage_event(
        self,
        *,
        credential: Credential | None,
        provider_key: str,
        operation: str,
        status: str,
        metadata_json: dict[str, Any] | None,
    ) -> None:
        self._s.add(
            CredentialUsageEvent(
                credential_id=credential.id if credential is not None else None,
                project_id=credential.project_id if credential is not None else None,
                provider_key=provider_key,
                operation=operation,
                status=status,
                metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            )
        )

    def record_refresh_event(
        self,
        *,
        credential: Credential | None,
        provider_key: str,
        status: str,
        metadata_json: dict[str, Any] | None,
    ) -> None:
        self._s.add(
            CredentialRefreshEvent(
                credential_id=credential.id if credential is not None else None,
                project_id=credential.project_id if credential is not None else None,
                provider_key=provider_key,
                status=status,
                metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            )
        )

    def consume_oauth_state(self, *, state: str, provider_key: str) -> OAuthState | None:
        row = self._s.exec(
            select(OAuthState).where(
                OAuthState.provider_key == provider_key,
                OAuthState.state == state,
                col(OAuthState.consumed_at).is_(None),
                or_(col(OAuthState.expires_at).is_(None), col(OAuthState.expires_at) > _utcnow()),
            )
        ).first()
        if row is None:
            return None
        row.consumed_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return row

    def sync_credential_for_integration(self, integration_credential_id: int) -> Credential:
        row = self._s.get(IntegrationCredential, integration_credential_id)
        if row is None:
            raise NotFoundError(f"credential {integration_credential_id} not found")
        credential = self._ensure_credential(row)
        self._s.commit()
        return credential

    def _local_setup_url(self, *, settings: Settings, project_id: int, provider_key: str) -> str:
        return (
            f"http://{settings.host}:{settings.port}"
            f"/projects/{project_id}/connections?provider_key={provider_key}"
        )

    def _integration_rows(
        self,
        *,
        project_id: int | None,
        provider_key: str | None,
    ) -> list[IntegrationCredential]:
        if provider_key == "telegram-bot" and project_id is None:
            return []
        stmt = select(IntegrationCredential)
        if provider_key is not None:
            stmt = stmt.where(col(IntegrationCredential.kind) == provider_key)
        if project_id is None:
            stmt = stmt.where(col(IntegrationCredential.project_id).is_(None))
            if provider_key is None:
                stmt = stmt.where(col(IntegrationCredential.kind) != "telegram-bot")
        elif provider_key == "telegram-bot":
            stmt = stmt.where(col(IntegrationCredential.project_id) == project_id)
        else:
            stmt = stmt.where(
                or_(
                    col(IntegrationCredential.project_id) == project_id,
                    col(IntegrationCredential.project_id).is_(None),
                )
            )
            if provider_key is None:
                stmt = stmt.where(
                    or_(
                        col(IntegrationCredential.project_id).is_not(None),
                        col(IntegrationCredential.kind) != "telegram-bot",
                    )
                )
        return list(
            self._s.exec(
                stmt.order_by(
                    col(IntegrationCredential.kind).asc(),
                    col(IntegrationCredential.project_id).desc(),
                    col(IntegrationCredential.profile_key).asc(),
                )
            ).all()
        )

    def _credential_rows(
        self,
        *,
        project_id: int | None,
        provider_key: str | None,
    ) -> list[Credential]:
        if provider_key == "telegram-bot" and project_id is None:
            return []
        stmt = select(Credential)
        if provider_key is not None:
            stmt = stmt.where(col(Credential.provider_key) == provider_key)
        if project_id is None:
            stmt = stmt.where(col(Credential.project_id).is_(None))
            if provider_key is None:
                stmt = stmt.where(col(Credential.provider_key) != "telegram-bot")
        elif provider_key == "telegram-bot":
            stmt = stmt.where(col(Credential.project_id) == project_id)
        else:
            stmt = stmt.where(
                or_(
                    col(Credential.project_id) == project_id,
                    col(Credential.project_id).is_(None),
                )
            )
            if provider_key is None:
                stmt = stmt.where(
                    or_(
                        col(Credential.project_id).is_not(None),
                        col(Credential.provider_key) != "telegram-bot",
                    )
                )
        return list(self._s.exec(stmt.order_by(col(Credential.id).asc())).all())

    def _ensure_credential(
        self,
        row: IntegrationCredential,
        *,
        status: str | None = None,
        method: AuthMethodOut | None = None,
    ) -> Credential:
        assert row.id is not None
        provider = self._get_provider(row.kind, required=False)
        if method is None and provider is not None:
            method = self._get_auth_method(
                provider,
                (row.config_json or {}).get("auth_method_key"),
                required=False,
            )
        credential = self._s.exec(
            select(Credential).where(Credential.integration_credential_id == row.id)
        ).first()
        now = _utcnow()
        row_status = self._status_for_integration(row)
        resolved_status = status or row_status
        auth_type = (
            method.auth_type
            if method is not None
            else provider.auth_type
            if provider is not None
            else "unknown"
        )
        auth_method_key = method.key if method is not None else "default"
        if credential is None:
            credential = Credential(
                project_id=row.project_id,
                auth_provider_id=provider.id if provider is not None else None,
                integration_credential_id=row.id,
                credential_ref=_credential_ref(),
                provider_key=row.kind,
                auth_type=auth_type,
                auth_method_key=auth_method_key,
                profile_key=row.profile_key,
                status=resolved_status,
                expires_at=row.expires_at,
                config_json=self._safe_config(row.config_json),
            )
        else:
            credential.project_id = row.project_id
            credential.auth_provider_id = (
                provider.id if provider is not None else credential.auth_provider_id
            )
            credential.provider_key = row.kind
            credential.auth_type = auth_type
            credential.auth_method_key = auth_method_key
            credential.profile_key = row.profile_key
            if credential.revoked_at is not None:
                credential.status = "revoked"
            elif status is None and credential.status == "failed" and row_status == "connected":
                credential.status = "failed"
            else:
                credential.status = resolved_status
            credential.expires_at = row.expires_at
            credential.config_json = self._safe_config(row.config_json)
            credential.updated_at = now
        self._s.add(credential)
        self._s.flush()
        return credential

    def _status_for_integration(self, row: IntegrationCredential) -> str:
        if row.config_json and row.config_json.get("oauth_state"):
            return "pending"
        if row.expires_at is not None and row.expires_at < _utcnow():
            return "expired"
        return "connected"

    def _connection_out(
        self,
        credential: Credential,
        row: IntegrationCredential | None,
    ) -> CredentialConnectionOut:
        scopes = self._scopes_for_credential(credential)
        return CredentialConnectionOut(
            credential_ref=credential.credential_ref,
            project_id=credential.project_id,
            provider_key=credential.provider_key,
            auth_type=credential.auth_type,
            auth_method_key=credential.auth_method_key,
            profile_key=credential.profile_key,
            label=self._credential_label(credential),
            status=credential.status if row is not None else "revoked",
            expires_at=credential.expires_at,
            last_tested_at=credential.last_tested_at,
            revoked_at=credential.revoked_at,
            scopes=scopes,
            account=self._account_for_credential(credential),
            setup_required=row is None or credential.status != "connected",
        )

    def _credential_label(self, credential: Credential) -> str | None:
        config = credential.config_json or {}
        label = config.get("label")
        return str(label) if label is not None and str(label).strip() else None

    def _account_for_credential(self, credential: Credential) -> dict[str, Any] | None:
        if credential.id is None:
            return None
        account = self._s.exec(
            select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
        ).first()
        if account is None:
            return None
        return {
            "provider_account_id": account.provider_account_id,
            "display_name": account.display_name,
            "metadata_json": redact_secrets(account.metadata_json),
        }

    def _scopes_for_credential(self, credential: Credential) -> list[str]:
        if credential.id is None:
            return []
        rows = self._s.exec(
            select(CredentialScope)
            .where(col(CredentialScope.credential_id) == credential.id)
            .order_by(col(CredentialScope.scope).asc())
        ).all()
        return [row.scope for row in rows]

    def _resolve_credential(
        self,
        *,
        project_id: int,
        credential_ref: str | None,
    ) -> tuple[Credential, IntegrationCredential]:
        if credential_ref is None:
            raise ValidationError("credential_ref is required")
        credential = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            raise NotFoundError(f"credential ref {credential_ref!r} not found")

        if credential.revoked_at is not None or credential.integration_credential_id is None:
            raise ConflictError(
                "credential is revoked",
                data={"credential_ref": credential.credential_ref},
            )
        row = self._s.get(IntegrationCredential, credential.integration_credential_id)
        if row is None:
            raise NotFoundError(
                "backing credential not found",
                data={"credential_ref": credential.credential_ref},
            )
        if row.kind == "telegram-bot" and row.project_id is None:
            raise NotFoundError(
                f"credential {credential.credential_ref!r} not in project {project_id}",
                data={"project_id": project_id, "credential_ref": credential.credential_ref},
            )
        if row.project_id is not None and row.project_id != project_id:
            raise NotFoundError(
                f"credential {credential.credential_ref!r} not in project {project_id}",
                data={"project_id": project_id, "credential_ref": credential.credential_ref},
            )
        return credential, row

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
        elif row.kind == "telegram-bot" and config.get("api_base_url"):
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

    def _get_provider(self, provider_key: str, *, required: bool = True) -> AuthProvider | None:
        self.sync_providers()
        row = self._s.exec(
            select(AuthProvider).where(col(AuthProvider.key) == provider_key)
        ).first()
        if row is None and required:
            raise NotFoundError(f"auth provider {provider_key!r} not found")
        return row

    def _normalize_profile_key(self, profile_key: str) -> str:
        normalized = profile_key.strip().lower().replace(" ", "-")
        if not _PROFILE_KEY_RE.match(normalized):
            raise ValidationError(
                "profile_key must start with a letter and contain only lowercase letters, "
                "numbers, underscores, or hyphens",
                data={"profile_key": profile_key},
            )
        return normalized

    def _auth_methods(self, provider: AuthProvider) -> list[AuthMethodOut]:
        raw_methods = (provider.config_json or {}).get("auth_methods")
        methods: list[AuthMethodOut] = []
        if isinstance(raw_methods, list):
            for raw_method in raw_methods:
                if not isinstance(raw_method, dict):
                    continue
                fields = [
                    AuthFieldOut(**raw_field)
                    for raw_field in raw_method.get("fields", [])
                    if isinstance(raw_field, dict)
                ]
                methods.append(
                    AuthMethodOut(
                        key=str(raw_method.get("key") or provider.auth_type or "default"),
                        label=str(raw_method.get("label") or provider.name),
                        auth_type=str(raw_method.get("auth_type") or provider.auth_type),
                        description=str(raw_method.get("description") or ""),
                        interactive=bool(raw_method.get("interactive")),
                        payload_format=str(raw_method.get("payload_format") or "json"),
                        payload_field=(
                            str(raw_method["payload_field"])
                            if raw_method.get("payload_field") is not None
                            else None
                        ),
                        fields=fields,
                        config=(
                            dict(raw_method["config"])
                            if isinstance(raw_method.get("config"), dict)
                            else None
                        ),
                    )
                )
        if methods:
            return methods
        if provider.auth_type in {"none", "local"}:
            return [
                AuthMethodOut(
                    key=provider.auth_type,
                    label=provider.auth_type.replace("-", " ").title(),
                    auth_type=provider.auth_type,
                    payload_format="none",
                )
            ]
        raise ValidationError(
            f"auth provider {provider.key!r} does not declare auth_methods",
            data={"provider_key": provider.key},
        )

    def _get_auth_method(
        self,
        provider: AuthProvider,
        auth_method_key: str | Any | None,
        *,
        required: bool = True,
    ) -> AuthMethodOut | None:
        methods = self._auth_methods(provider)
        if auth_method_key is None:
            return methods[0] if methods else None
        auth_method_key = str(auth_method_key)
        for method in methods:
            if method.key == auth_method_key:
                return method
        if required:
            raise ValidationError(
                f"auth method {auth_method_key!r} not found for provider {provider.key!r}",
                data={"provider_key": provider.key, "auth_method_key": auth_method_key},
            )
        return None

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

    def _authorization_url(self, method: AuthMethodOut) -> str | None:
        config = method.config or {}
        value = config.get("authorization_url") or config.get("authorize_url")
        return str(value) if value is not None and str(value).strip() else None

    def _provider_out(self, row: AuthProvider, plugin: Plugin | None) -> AuthProviderOut:
        assert row.id is not None
        return AuthProviderOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug if plugin is not None else None,
            key=row.key,
            name=row.name,
            description=row.description,
            auth_type=row.auth_type,
            auth_methods=self._auth_methods(row),
            scopes=list(row.scopes_json or []),
            config_json=self._provider_public_config(row.config_json),
        )

    def _safe_config(self, config_json: dict[str, Any] | None) -> dict[str, Any] | None:
        if config_json is None:
            return None
        safe = redact_secrets(config_json)
        safe.pop("oauth_state", None)
        return safe

    def _safe_provider_config(self, config_json: dict[str, Any] | None) -> dict[str, Any] | None:
        if config_json is None:
            return None
        visible_config = {key: value for key, value in config_json.items() if key != "auth_methods"}
        safe = redact_secrets(visible_config)
        if "auth_methods" in config_json:
            safe["auth_methods"] = config_json["auth_methods"]
        return safe or None

    def _provider_public_config(self, config_json: dict[str, Any] | None) -> dict[str, Any] | None:
        if config_json is None:
            return None
        public = dict(config_json)
        public.pop("auth_methods", None)
        return public or None

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")


__all__ = [
    "AuthCredentialSetOut",
    "AuthFieldOut",
    "AuthMethodOut",
    "AuthProviderOut",
    "AuthRepository",
    "AuthRevokeOut",
    "AuthStartOut",
    "AuthStatusOut",
    "AuthTestOut",
    "CredentialConnectionOut",
    "ResolvedCredential",
]
