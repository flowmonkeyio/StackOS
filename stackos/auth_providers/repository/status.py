"""Global Account inventory and project Connection shaping."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as ModelValidationError
from sqlalchemy import func
from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    Credential,
    CredentialAccount,
    CredentialScope,
    CredentialUsageEvent,
    Project,
    ProjectCredential,
)
from stackos.repositories.project_access import project_owner_filter

from .access import account_owner_filter, require_account_owner
from .schema import AccountOut, AuthStatusOut, AuthTestOut


class CredentialStatusMixin:
    """Shape global Accounts without exposing encrypted or plaintext secrets."""

    def status(
        self,
        *,
        project_id: int | None,
        provider_key: str | None = None,
        summary_only: bool = False,
    ) -> AuthStatusOut:
        """List all Accounts or only Accounts attached to one project."""
        providers = [] if summary_only else self.list_providers(provider_key=provider_key)
        if project_id is not None:
            rows = self.attached_credentials(
                project_id=project_id,
                provider_key=provider_key,
            )
        else:
            stmt = select(Credential).where(
                account_owner_filter(self._s), col(Credential.status) != "revoked"
            )
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
            accounts=self._accounts_out(list(rows)),
            summary_only=summary_only,
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
                account_owner_filter(self._s),
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
        credential = require_account_owner(self._s, credential)
        return self._account_out(credential)

    def _accounts_out(self, credentials: list[Credential]) -> list[AccountOut]:
        if not credentials:
            return []
        if len(credentials) == 1:
            return [self._account_out(credentials[0])]
        credential_ids = [row.id for row in credentials if row.id is not None]
        accounts_by_id: dict[int, dict[str, Any]] = {}
        for account in self._s.exec(
            select(CredentialAccount).where(
                col(CredentialAccount.credential_id).in_(credential_ids)
            )
        ).all():
            if account.credential_id not in accounts_by_id:
                accounts_by_id[account.credential_id] = {
                    "provider_account_id": account.provider_account_id,
                    "display_name": account.display_name,
                    "metadata_json": redact_secrets(account.metadata_json),
                }
        scopes_by_id: dict[int, list[str]] = {}
        for scope_row in self._s.exec(
            select(CredentialScope)
            .where(col(CredentialScope.credential_id).in_(credential_ids))
            .order_by(col(CredentialScope.scope).asc())
        ).all():
            scopes_by_id.setdefault(scope_row.credential_id, []).append(scope_row.scope)
        project_ids_by_id: dict[int, list[int]] = {}
        for link_row in self._s.exec(
            select(ProjectCredential)
            .join(Project, col(Project.id) == col(ProjectCredential.project_id))
            .where(
                col(ProjectCredential.credential_id).in_(credential_ids),
                project_owner_filter(self._s),
            )
            .order_by(col(ProjectCredential.project_id).asc())
        ).all():
            project_ids_by_id.setdefault(link_row.credential_id, []).append(link_row.project_id)
        latest_test_ids = [
            row[1]
            for row in self._s.exec(
                select(
                    CredentialUsageEvent.credential_id,
                    func.max(CredentialUsageEvent.id),
                )
                .where(
                    col(CredentialUsageEvent.credential_id).in_(credential_ids),
                    CredentialUsageEvent.operation == "account.test",
                )
                .group_by(col(CredentialUsageEvent.credential_id))
            ).all()
            if row[1] is not None
        ]
        test_events_by_id: dict[int, CredentialUsageEvent] = {}
        if latest_test_ids:
            for ev in self._s.exec(
                select(CredentialUsageEvent).where(
                    col(CredentialUsageEvent.id).in_(latest_test_ids)
                )
            ).all():
                if ev.credential_id is not None:
                    test_events_by_id[ev.credential_id] = ev
        telegram_ids = [
            row.id
            for row in credentials
            if row.id is not None and row.provider_key == "telegram" and row.id in test_events_by_id
        ]
        telegram_auth_max_id: dict[int, int] = {}
        if telegram_ids:
            for cid, max_id in self._s.exec(
                select(
                    CredentialUsageEvent.credential_id,
                    func.max(CredentialUsageEvent.id),
                )
                .where(
                    col(CredentialUsageEvent.credential_id).in_(telegram_ids),
                    col(CredentialUsageEvent.operation).in_(
                        ("account.authorization.ready", "account.authorization.changed")
                    ),
                )
                .group_by(col(CredentialUsageEvent.credential_id))
            ).all():
                if cid is not None and max_id is not None:
                    telegram_auth_max_id[cid] = max_id
        return [
            self._build_account_out(
                credential,
                account=accounts_by_id.get(credential.id),
                scopes=scopes_by_id.get(credential.id, []),
                project_ids=project_ids_by_id.get(credential.id, []),
                last_test=self._format_last_test(
                    credential,
                    test_events_by_id.get(credential.id),
                    telegram_auth_max_id.get(credential.id),
                ),
            )
            for credential in credentials
            if credential.id is not None
        ]

    def _build_account_out(
        self,
        credential: Credential,
        *,
        account: dict[str, Any] | None,
        scopes: list[str],
        project_ids: list[int],
        last_test: AuthTestOut | None,
    ) -> AccountOut:
        require_account_owner(self._s, credential)
        assert credential.id is not None
        has_backing = credential.integration_credential_id is not None
        status = (
            "revoked" if credential.revoked_at is not None or not has_backing else credential.status
        )
        saved_telegram_sign_in = bool(
            credential.provider_key == "telegram"
            and status == "disconnected"
            and account is not None
            and account.get("provider_account_id")
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
            last_tested_at=credential.last_tested_at if last_test is not None else None,
            last_test=last_test,
            revoked_at=credential.revoked_at,
            scopes=scopes,
            account=account,
            project_ids=project_ids,
            setup_required=(
                not has_backing or (status != "connected" and not saved_telegram_sign_in)
            ),
        )

    def _format_last_test(
        self,
        credential: Credential,
        event: CredentialUsageEvent | None,
        newer_telegram_auth_id: int | None,
    ) -> AuthTestOut | None:
        if credential.provider_key == "cfobooth" and credential.last_tested_at is None:
            return None
        if event is None:
            return None
        if (
            credential.provider_key == "telegram"
            and newer_telegram_auth_id is not None
            and event.id is not None
            and newer_telegram_auth_id > event.id
        ):
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

    def _account_out(self, credential: Credential) -> AccountOut:
        assert credential.id is not None
        return self._build_account_out(
            credential,
            account=self._provider_account_for_credential(credential),
            scopes=self._scopes_for_credential(credential),
            project_ids=self._project_ids_for_credential(credential.id),
            last_test=self._last_test_for_credential(credential),
        )

    def _last_test_for_credential(self, credential: Credential) -> AuthTestOut | None:
        """Project the existing test audit, never use it as an execution grant."""
        if credential.provider_key == "cfobooth" and credential.last_tested_at is None:
            return None
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
        newer_telegram_auth_id: int | None = None
        if credential.provider_key == "telegram":
            newer_authorization = self._s.exec(
                select(CredentialUsageEvent)
                .where(
                    CredentialUsageEvent.credential_id == credential.id,
                    col(CredentialUsageEvent.operation).in_(
                        ("account.authorization.ready", "account.authorization.changed")
                    ),
                )
                .order_by(col(CredentialUsageEvent.id).desc())
                .limit(1)
            ).first()
            if newer_authorization is not None:
                newer_telegram_auth_id = newer_authorization.id
        return self._format_last_test(credential, event, newer_telegram_auth_id)

    def _project_ids_for_credential(self, credential_id: int) -> list[int]:
        rows = self._s.exec(
            select(ProjectCredential)
            .join(Project, col(Project.id) == col(ProjectCredential.project_id))
            .where(
                col(ProjectCredential.credential_id) == credential_id,
                project_owner_filter(self._s),
            )
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
