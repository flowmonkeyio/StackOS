"""Global Account inventory and project Connection shaping."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as ModelValidationError
from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    Credential,
    CredentialAccount,
    CredentialScope,
    CredentialUsageEvent,
    ProjectCredential,
)
from stackos.repositories.base import NotFoundError

from .schema import AccountOut, AuthStatusOut, AuthTestOut


class CredentialStatusMixin:
    """Shape global Accounts without exposing encrypted or plaintext secrets."""

    def status(
        self,
        *,
        project_id: int | None,
        provider_key: str | None = None,
    ) -> AuthStatusOut:
        """List all Accounts or only Accounts attached to one project."""
        providers = self.list_providers(provider_key=provider_key)
        if project_id is not None:
            rows = self.attached_credentials(
                project_id=project_id,
                provider_key=provider_key,
            )
        else:
            stmt = select(Credential).where(col(Credential.status) != "revoked")
            if provider_key is not None:
                stmt = stmt.where(col(Credential.provider_key) == provider_key)
            rows = self._s.exec(
                stmt.order_by(
                    col(Credential.provider_key).asc(),
                    col(Credential.display_name_key).asc(),
                    col(Credential.id).asc(),
                )
            ).all()
        return AuthStatusOut(
            project_id=project_id,
            provider_key=provider_key,
            providers=providers,
            accounts=[self._account_out(row) for row in rows],
        )

    def attached_credentials(
        self,
        *,
        project_id: int,
        provider_key: str | None = None,
    ) -> list[Credential]:
        """Return safe Account identity rows through the sole attachment-query boundary."""

        self._require_project(project_id)
        stmt = (
            select(Credential)
            .join(
                ProjectCredential,
                col(ProjectCredential.credential_id) == col(Credential.id),
            )
            .where(
                col(Credential.status) != "revoked",
                col(ProjectCredential.project_id) == project_id,
            )
        )
        if provider_key is not None:
            stmt = stmt.where(col(Credential.provider_key) == provider_key)
        return list(
            self._s.exec(
                stmt.order_by(
                    col(Credential.provider_key).asc(),
                    col(Credential.display_name_key).asc(),
                    col(Credential.id).asc(),
                )
            ).all()
        )

    def get_account(self, *, credential_ref: str) -> AccountOut:
        """Return one sanitized global Account."""
        credential = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if credential is None:
            raise NotFoundError(f"Account {credential_ref!r} not found")
        return self._account_out(credential)

    def _account_out(self, credential: Credential) -> AccountOut:
        assert credential.id is not None
        has_backing = credential.integration_credential_id is not None
        status = (
            "revoked" if credential.revoked_at is not None or not has_backing else credential.status
        )
        return AccountOut(
            credential_id=credential.id,
            credential_ref=credential.credential_ref,
            display_name=credential.display_name,
            provider_key=credential.provider_key,
            auth_type=credential.auth_type,
            auth_method_key=credential.auth_method_key,
            status=status,
            expires_at=credential.expires_at,
            last_tested_at=credential.last_tested_at,
            last_test=self._last_test_for_credential(credential),
            revoked_at=credential.revoked_at,
            scopes=self._scopes_for_credential(credential),
            account=self._provider_account_for_credential(credential),
            project_ids=self._project_ids_for_credential(credential.id),
            setup_required=not has_backing or status != "connected",
        )

    def _last_test_for_credential(self, credential: Credential) -> AuthTestOut | None:
        """Project the existing test audit, never use it as an execution grant."""
        event = self._s.exec(
            select(CredentialUsageEvent)
            .where(
                CredentialUsageEvent.credential_id == credential.id,
                CredentialUsageEvent.operation == "account.test",
            )
            .order_by(col(CredentialUsageEvent.id).desc())
            .limit(1)
        ).first()
        if event is None:
            return None
        data = redact_secrets(event.metadata_json or {})
        result = data.get("result")
        if isinstance(result, dict):
            try:
                return AuthTestOut.model_validate(
                    {
                        **result,
                        "credential_ref": credential.credential_ref,
                        "provider_key": credential.provider_key,
                    }
                )
            except ModelValidationError:
                # Older audit rows did not retain the complete normalized result.
                # Preserve their known outcome without guessing a provider cause.
                pass
        ok = data.get("ok")
        if not isinstance(ok, bool):
            return None
        metadata = data.get("metadata")
        return AuthTestOut(
            credential_ref=credential.credential_ref,
            provider_key=credential.provider_key,
            ok=ok,
            status="ok" if ok else "failed",
            summary=(
                "Previous credential test passed." if ok else "Previous credential test failed."
            ),
            checked_at=event.created_at.isoformat(),
            next_action="Test the Account again for current diagnostics.",
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def _project_ids_for_credential(self, credential_id: int) -> list[int]:
        rows = self._s.exec(
            select(ProjectCredential)
            .where(col(ProjectCredential.credential_id) == credential_id)
            .order_by(col(ProjectCredential.project_id).asc())
        ).all()
        return [row.project_id for row in rows]

    def _provider_account_for_credential(
        self,
        credential: Credential,
    ) -> dict[str, Any] | None:
        assert credential.id is not None
        account = self._s.exec(
            select(CredentialAccount).where(col(CredentialAccount.credential_id) == credential.id)
        ).first()
        if account is None:
            return None
        return {
            "provider_account_id": account.provider_account_id,
            "display_name": account.display_name,
            "metadata_json": redact_secrets(account.metadata_json),
        }

    def _scopes_for_credential(self, credential: Credential) -> list[str]:
        assert credential.id is not None
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
        safe.pop("oauth_pending", None)
        safe.pop("oauth_connection_status", None)
        return safe
