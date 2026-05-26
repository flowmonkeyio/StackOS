"""Auth credential audit and OAuth state helpers."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    Credential,
    CredentialRefreshEvent,
    CredentialUsageEvent,
    OAuthState,
)

from .utils import utcnow


class CredentialEventMixin:
    """Record auth events with secret redaction before persistence."""

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
                or_(col(OAuthState.expires_at).is_(None), col(OAuthState.expires_at) > utcnow()),
            )
        ).first()
        if row is None:
            return None
        row.consumed_at = utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return row
