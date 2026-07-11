"""Credential status and sanitized connection shaping."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    Credential,
    CredentialAccount,
    CredentialScope,
    IntegrationCredential,
)

from .schema import AuthMethodOut, AuthStatusOut, CredentialConnectionOut
from .utils import _PROJECT_SCOPED_PROVIDER_KEYS, credential_ref, utcnow


class CredentialStatusMixin:
    """Shape auth status without exposing encrypted or plaintext secrets."""

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

    def _integration_rows(
        self,
        *,
        project_id: int | None,
        provider_key: str | None,
    ) -> list[IntegrationCredential]:
        if provider_key in _PROJECT_SCOPED_PROVIDER_KEYS and project_id is None:
            return []
        stmt = select(IntegrationCredential)
        if provider_key is not None:
            stmt = stmt.where(col(IntegrationCredential.kind) == provider_key)
        if project_id is None:
            stmt = stmt.where(col(IntegrationCredential.project_id).is_(None))
            if provider_key is None:
                stmt = stmt.where(
                    col(IntegrationCredential.kind).not_in(_PROJECT_SCOPED_PROVIDER_KEYS)
                )
        elif provider_key in _PROJECT_SCOPED_PROVIDER_KEYS:
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
                        col(IntegrationCredential.kind).not_in(_PROJECT_SCOPED_PROVIDER_KEYS),
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
        if provider_key in _PROJECT_SCOPED_PROVIDER_KEYS and project_id is None:
            return []
        stmt = select(Credential)
        if provider_key is not None:
            stmt = stmt.where(col(Credential.provider_key) == provider_key)
        if project_id is None:
            stmt = stmt.where(col(Credential.project_id).is_(None))
            if provider_key is None:
                stmt = stmt.where(
                    col(Credential.provider_key).not_in(_PROJECT_SCOPED_PROVIDER_KEYS)
                )
        elif provider_key in _PROJECT_SCOPED_PROVIDER_KEYS:
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
                        col(Credential.provider_key).not_in(_PROJECT_SCOPED_PROVIDER_KEYS),
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
        # status() has already synchronized the provider catalog once through
        # list_providers(). Repeating that catalog sync for every connection was
        # the dominant cost of the read-only auth status endpoint.
        provider = self._get_provider(row.kind, required=False, sync=False)
        if method is None and provider is not None:
            method = self._get_auth_method(
                provider,
                (row.config_json or {}).get("auth_method_key"),
                required=False,
            )
        credential = self._s.exec(
            select(Credential).where(Credential.integration_credential_id == row.id)
        ).first()
        now = utcnow()
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
                credential_ref=credential_ref(),
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
        if row.expires_at is not None and row.expires_at < utcnow():
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

    def _safe_config(self, config_json: dict[str, Any] | None) -> dict[str, Any] | None:
        if config_json is None:
            return None
        safe = redact_secrets(config_json)
        safe.pop("oauth_state", None)
        return safe
