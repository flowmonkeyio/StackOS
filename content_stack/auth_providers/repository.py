"""Generic auth provider repository over encrypted credential storage."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import Session, select

from content_stack.artifacts import redact_secret_text, redact_secrets
from content_stack.config import Settings
from content_stack.db.models import (
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
from content_stack.integrations import integration_class_for
from content_stack.mcp.errors import IntegrationDownError
from content_stack.repositories.base import ConflictError, Envelope, NotFoundError, ValidationError
from content_stack.repositories.projects import IntegrationCredentialRepository

_OAUTH_STATE_TTL_MINUTES = 30


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _credential_ref() -> str:
    return f"cred_{secrets.token_urlsafe(18)}"


class AuthProviderOut(BaseModel):
    id: int
    plugin_id: int | None
    plugin_slug: str | None
    key: str
    name: str
    description: str
    auth_type: str
    scopes: list[str]
    config_json: dict[str, Any] | None


class CredentialConnectionOut(BaseModel):
    credential_ref: str
    project_id: int | None
    provider_key: str
    auth_type: str
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


@dataclass(frozen=True)
class ResolvedCredential:
    """Daemon-internal credential bundle for connector execution.

    The plaintext payload is intentionally not a Pydantic model field and is
    hidden from repr; callers must never serialize this object into MCP/REST
    responses or audit JSON.
    """

    credential: Credential
    integration: IntegrationCredential
    plaintext_payload: bytes = dataclass_field(repr=False)
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
        from content_stack.repositories.plugins import PluginRepository

        PluginRepository(self._s).sync_builtin_plugins()
        rows = self._s.exec(
            select(Provider, Plugin)
            .join(Plugin, Provider.plugin_id == Plugin.id)
            .order_by(Plugin.slug.asc(), Provider.key.asc())
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
                    config_json=redact_secrets(provider.config_json)
                    if provider.config_json is not None
                    else None,
                )
            else:
                row.name = provider.name
                row.description = provider.description
                row.auth_type = provider.auth_type
                row.scopes_json = scopes
                row.config_json = (
                    redact_secrets(provider.config_json)
                    if provider.config_json is not None
                    else None
                )
                row.updated_at = now
            self._s.add(row)
        self._s.commit()

    def list_providers(self, *, provider_key: str | None = None) -> list[AuthProviderOut]:
        self.sync_providers()
        stmt = select(AuthProvider, Plugin).outerjoin(Plugin, AuthProvider.plugin_id == Plugin.id)
        if provider_key is not None:
            stmt = stmt.where(AuthProvider.key == provider_key)
        rows = self._s.exec(stmt.order_by(AuthProvider.key.asc())).all()
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
        redirect_uri: str | None = None,
    ) -> Envelope[AuthStartOut]:
        self._require_project(project_id)
        provider = self._get_provider(provider_key)
        if provider.auth_type == "oauth" and provider.key == "gsc":
            return self._start_gsc(
                project_id=project_id,
                provider=provider,
                settings=settings,
                redirect_uri=redirect_uri,
            )
        setup_url = self._local_setup_url(
            settings=settings,
            project_id=project_id,
            provider_key=provider_key,
        )
        return Envelope(
            data=AuthStartOut(
                project_id=project_id,
                provider_key=provider.key,
                auth_type=provider.auth_type,
                status="requires-local-secret" if provider.auth_type != "none" else "not-required",
                setup_url=setup_url if provider.auth_type != "none" else None,
            ),
            project_id=project_id,
        )

    async def test(
        self,
        *,
        project_id: int,
        credential_ref: str | None = None,
        provider_key: str | None = None,
    ) -> Envelope[AuthTestOut]:
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
            provider_key=provider_key,
        )
        integration_cls = integration_class_for(row.kind)
        if integration_cls is None:
            raise ValidationError(
                f"auth provider {row.kind!r} has no test wrapper",
                data={"provider_key": row.kind, "credential_ref": credential.credential_ref},
            )
        plaintext = IntegrationCredentialRepository(self._s).get_decrypted(int(row.id))
        extra = self._integration_extra(row)
        async with httpx.AsyncClient(timeout=30.0) as client:
            integration = integration_cls(
                payload=plaintext,
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
        self.record_usage_event(
            credential=credential,
            provider_key=row.kind,
            operation="auth.test",
            status=out.status,
            metadata_json={"ok": out.ok, "metadata": out.metadata},
        )
        self._s.commit()
        return Envelope(data=out, project_id=project_id)

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
            provider_key=provider_key,
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
        plaintext = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
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
            plaintext_payload=plaintext,
            config_json=row.config_json,
        )

    def revoke(
        self,
        *,
        project_id: int,
        credential_ref: str | None = None,
        provider_key: str | None = None,
    ) -> Envelope[AuthRevokeOut]:
        credential, row = self._resolve_credential(
            project_id=project_id,
            credential_ref=credential_ref,
            provider_key=provider_key,
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
                OAuthState.consumed_at.is_(None),  # type: ignore[union-attr]
                or_(OAuthState.expires_at.is_(None), OAuthState.expires_at > _utcnow()),  # type: ignore[union-attr]
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

    def _start_gsc(
        self,
        *,
        project_id: int,
        provider: AuthProvider,
        settings: Settings,
        redirect_uri: str | None,
    ) -> Envelope[AuthStartOut]:
        from content_stack.integrations.gsc import build_authorize_url

        missing = self._missing_gsc_oauth_env_vars()
        if missing:
            raise ValidationError(
                "GSC OAuth client not configured",
                data={
                    "provider_key": "gsc",
                    "missing": missing,
                    "hint": self._gsc_oauth_setup_hint(),
                },
            )
        state = secrets.token_urlsafe(32)
        resolved_redirect_uri = redirect_uri or self.default_gsc_redirect_uri(settings)
        try:
            authorization_url = build_authorize_url(
                state=state,
                redirect_uri=resolved_redirect_uri,
            )
        except IntegrationDownError as exc:
            raise ValidationError(
                exc.detail,
                data=redact_secrets(exc.data),
            ) from exc
        env = IntegrationCredentialRepository(self._s).set(
            project_id=project_id,
            kind="gsc",
            plaintext_payload=b"{}",
            config_json={"oauth_state": state, "redirect_uri": resolved_redirect_uri},
        )
        row = self._s.get(IntegrationCredential, env.data.id)
        assert row is not None and row.id is not None
        credential = self._ensure_credential(row, status="pending")
        oauth_state = OAuthState(
            project_id=project_id,
            provider_key="gsc",
            credential_id=credential.id,
            integration_credential_id=row.id,
            state=state,
            redirect_uri=resolved_redirect_uri,
            expires_at=_utcnow() + timedelta(minutes=_OAUTH_STATE_TTL_MINUTES),
        )
        self._s.add(oauth_state)
        self.record_usage_event(
            credential=credential,
            provider_key="gsc",
            operation="auth.start",
            status="pending",
            metadata_json={"redirect_uri": resolved_redirect_uri},
        )
        self._s.commit()
        return Envelope(
            data=AuthStartOut(
                project_id=project_id,
                provider_key="gsc",
                auth_type=provider.auth_type,
                status="pending",
                authorization_url=authorization_url,
                redirect_uri=resolved_redirect_uri,
                state=state,
                credential_ref=credential.credential_ref,
                expires_at=oauth_state.expires_at,
            ),
            project_id=project_id,
        )

    @staticmethod
    def default_gsc_redirect_uri(settings: Settings) -> str:
        return f"http://{settings.host}:{settings.port}/api/v1/integrations/gsc/oauth/callback"

    @staticmethod
    def _missing_gsc_oauth_env_vars() -> list[str]:
        import os

        return [
            name
            for name in ("GSC_OAUTH_CLIENT_ID", "GSC_OAUTH_CLIENT_SECRET")
            if not os.environ.get(name)
        ]

    @staticmethod
    def _gsc_oauth_setup_hint() -> str:
        return "Set GSC_OAUTH_CLIENT_ID and GSC_OAUTH_CLIENT_SECRET, then restart the daemon."

    def _local_setup_url(self, *, settings: Settings, project_id: int, provider_key: str) -> str:
        return (
            f"http://{settings.host}:{settings.port}"
            f"/api/v1/projects/{project_id}/integrations?kind={provider_key}"
        )

    def _integration_rows(
        self,
        *,
        project_id: int | None,
        provider_key: str | None,
    ) -> list[IntegrationCredential]:
        stmt = select(IntegrationCredential)
        if provider_key is not None:
            stmt = stmt.where(IntegrationCredential.kind == provider_key)
        if project_id is None:
            stmt = stmt.where(IntegrationCredential.project_id.is_(None))  # type: ignore[union-attr]
        else:
            stmt = stmt.where(
                or_(
                    IntegrationCredential.project_id == project_id,
                    IntegrationCredential.project_id.is_(None),  # type: ignore[union-attr]
                )
            )
        return list(self._s.exec(stmt.order_by(IntegrationCredential.id.asc())).all())  # type: ignore[union-attr]

    def _credential_rows(
        self,
        *,
        project_id: int | None,
        provider_key: str | None,
    ) -> list[Credential]:
        stmt = select(Credential)
        if provider_key is not None:
            stmt = stmt.where(Credential.provider_key == provider_key)
        if project_id is None:
            stmt = stmt.where(Credential.project_id.is_(None))  # type: ignore[union-attr]
        else:
            stmt = stmt.where(
                or_(
                    Credential.project_id == project_id,
                    Credential.project_id.is_(None),  # type: ignore[union-attr]
                )
            )
        return list(self._s.exec(stmt.order_by(Credential.id.asc())).all())  # type: ignore[union-attr]

    def _ensure_credential(
        self,
        row: IntegrationCredential,
        *,
        status: str | None = None,
    ) -> Credential:
        assert row.id is not None
        provider = self._get_provider(row.kind, required=False)
        credential = self._s.exec(
            select(Credential).where(Credential.integration_credential_id == row.id)
        ).first()
        now = _utcnow()
        resolved_status = status or self._status_for_integration(row)
        if credential is None:
            credential = Credential(
                project_id=row.project_id,
                auth_provider_id=provider.id if provider is not None else None,
                integration_credential_id=row.id,
                credential_ref=_credential_ref(),
                provider_key=row.kind,
                auth_type=provider.auth_type if provider is not None else "unknown",
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
            credential.auth_type = (
                provider.auth_type if provider is not None else credential.auth_type
            )
            credential.status = resolved_status if credential.revoked_at is None else "revoked"
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
            status=credential.status if row is not None else "revoked",
            expires_at=credential.expires_at,
            last_tested_at=credential.last_tested_at,
            revoked_at=credential.revoked_at,
            scopes=scopes,
            account=self._account_for_credential(credential),
            setup_required=row is None or credential.status in {"pending", "expired", "revoked"},
        )

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
            .where(CredentialScope.credential_id == credential.id)
            .order_by(CredentialScope.scope.asc())
        ).all()
        return [row.scope for row in rows]

    def _resolve_credential(
        self,
        *,
        project_id: int,
        credential_ref: str | None,
        provider_key: str | None,
    ) -> tuple[Credential, IntegrationCredential]:
        if credential_ref is not None:
            credential = self._s.exec(
                select(Credential).where(Credential.credential_ref == credential_ref)
            ).first()
            if credential is None:
                raise NotFoundError(f"credential ref {credential_ref!r} not found")
        elif provider_key is not None:
            row = self._s.exec(
                select(IntegrationCredential).where(
                    IntegrationCredential.kind == provider_key,
                    IntegrationCredential.project_id == project_id,
                )
            ).first()
            if row is None:
                row = self._s.exec(
                    select(IntegrationCredential).where(
                        IntegrationCredential.kind == provider_key,
                        IntegrationCredential.project_id.is_(None),  # type: ignore[union-attr]
                    )
                ).first()
            if row is None:
                raise NotFoundError(
                    f"credential not found for project={project_id} provider={provider_key!r}",
                    data={"project_id": project_id, "provider_key": provider_key},
                )
            credential = self._ensure_credential(row)
        else:
            raise ValidationError("credential_ref or provider_key is required")

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
                f"{vendor} credentials are reachable"
                if ok
                else f"{vendor} credential test failed"
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
        row = self._s.exec(select(AuthProvider).where(AuthProvider.key == provider_key)).first()
        if row is None and required:
            raise NotFoundError(f"auth provider {provider_key!r} not found")
        return row

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
            scopes=list(row.scopes_json or []),
            config_json=row.config_json,
        )

    def _safe_config(self, config_json: dict[str, Any] | None) -> dict[str, Any] | None:
        if config_json is None:
            return None
        safe = redact_secrets(config_json)
        safe.pop("oauth_state", None)
        return safe

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")


__all__ = [
    "AuthProviderOut",
    "AuthRepository",
    "AuthRevokeOut",
    "AuthStartOut",
    "AuthStatusOut",
    "AuthTestOut",
    "CredentialConnectionOut",
    "ResolvedCredential",
]
