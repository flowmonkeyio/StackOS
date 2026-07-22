"""Auth credential audit and OAuth state helpers."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    Credential,
    CredentialRefreshEvent,
    CredentialUsageEvent,
)


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
