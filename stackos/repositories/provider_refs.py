"""Internal account-scoped provider object reference mappings."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import Credential, CredentialAccount, ProviderObjectReference
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError


@dataclass(frozen=True)
class ResolvedProviderObject:
    """Daemon-internal resolved identity; provider id must never be serialized."""

    safe_ref: str
    provider_key: str
    provider_account_id: str
    object_type: str
    provider_object_id: str = field(repr=False)
    display_name: str | None = None
    metadata_json: dict[str, Any] | None = None


class ProviderObjectReferenceRepository:
    """Issue and resolve opaque provider refs inside connector execution only."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(
        self,
        *,
        credential: Credential,
        object_type: str,
        provider_object_id: str | int,
        display_name: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> str:
        project_id, account_id = self._binding(credential)
        normalized_type = self._object_type(object_type)
        external_id = str(provider_object_id).strip()
        if not external_id:
            raise ValidationError("provider object id is required")
        row = self._s.exec(
            select(ProviderObjectReference).where(
                ProviderObjectReference.project_id == project_id,
                ProviderObjectReference.provider_key == credential.provider_key,
                ProviderObjectReference.provider_account_id == account_id,
                ProviderObjectReference.object_type == normalized_type,
                ProviderObjectReference.provider_object_id == external_id,
            )
        ).first()
        now = datetime.now(tz=UTC).replace(tzinfo=None)
        if row is None:
            assert credential.id is not None
            row = ProviderObjectReference(
                project_id=project_id,
                credential_id=credential.id,
                provider_key=credential.provider_key,
                provider_account_id=account_id,
                object_type=normalized_type,
                provider_object_id=external_id,
                safe_ref=f"provider-object:{secrets.token_urlsafe(24)}",
            )
        else:
            assert credential.id is not None
            row.credential_id = credential.id
            row.stale_at = None
            row.updated_at = now
        if display_name is not None:
            row.display_name = display_name.strip()[:500] or None
        if metadata_json is not None:
            row.metadata_json = redact_secrets(metadata_json)
        self._s.add(row)
        self._s.flush()
        return row.safe_ref

    def resolve(
        self,
        *,
        credential: Credential,
        safe_ref: str,
        expected_object_type: str,
    ) -> ResolvedProviderObject:
        project_id, account_id = self._binding(credential)
        normalized_ref = self._safe_ref(safe_ref)
        normalized_type = self._object_type(expected_object_type)
        row = self._s.exec(
            select(ProviderObjectReference).where(
                col(ProviderObjectReference.safe_ref) == normalized_ref
            )
        ).first()
        if (
            row is None
            or row.project_id != project_id
            or row.provider_key != credential.provider_key
            or row.provider_account_id != account_id
            or row.object_type != normalized_type
        ):
            raise NotFoundError(
                "provider object reference is not valid for this connection and object type",
                data={
                    "safe_ref": normalized_ref,
                    "provider_key": credential.provider_key,
                    "expected_object_type": normalized_type,
                },
            )
        if row.stale_at is not None:
            raise ConflictError(
                "provider object reference is stale",
                data={
                    "safe_ref": normalized_ref,
                    "provider_key": credential.provider_key,
                    "expected_object_type": normalized_type,
                    "next_action": "Refresh provider metadata or search for the record again.",
                },
            )
        return ResolvedProviderObject(
            safe_ref=row.safe_ref,
            provider_key=row.provider_key,
            provider_account_id=row.provider_account_id,
            object_type=row.object_type,
            display_name=row.display_name,
            metadata_json=dict(row.metadata_json) if row.metadata_json is not None else None,
            provider_object_id=row.provider_object_id,
        )

    def resolve_one_of(
        self,
        *,
        credential: Credential,
        safe_ref: str,
        expected_object_types: set[str] | tuple[str, ...] | list[str],
    ) -> ResolvedProviderObject:
        project_id, account_id = self._binding(credential)
        normalized_ref = self._safe_ref(safe_ref)
        normalized_types = {self._object_type(item) for item in expected_object_types}
        if not normalized_types:
            raise ValidationError("at least one provider object type is required")
        row = self._s.exec(
            select(ProviderObjectReference).where(
                col(ProviderObjectReference.safe_ref) == normalized_ref
            )
        ).first()
        if (
            row is None
            or row.project_id != project_id
            or row.provider_key != credential.provider_key
            or row.provider_account_id != account_id
            or row.object_type not in normalized_types
        ):
            raise NotFoundError(
                "provider object reference is not valid for this connection and allowed types",
                data={
                    "safe_ref": normalized_ref,
                    "provider_key": credential.provider_key,
                    "expected_object_types": sorted(normalized_types),
                },
            )
        if row.stale_at is not None:
            raise ConflictError(
                "provider object reference is stale",
                data={
                    "safe_ref": normalized_ref,
                    "provider_key": credential.provider_key,
                    "expected_object_types": sorted(normalized_types),
                    "next_action": "Refresh or search for the provider record again.",
                },
            )
        return ResolvedProviderObject(
            safe_ref=row.safe_ref,
            provider_key=row.provider_key,
            provider_account_id=row.provider_account_id,
            object_type=row.object_type,
            display_name=row.display_name,
            metadata_json=dict(row.metadata_json) if row.metadata_json is not None else None,
            provider_object_id=row.provider_object_id,
        )

    def mark_stale(
        self,
        *,
        credential: Credential,
        safe_ref: str,
        expected_object_type: str,
    ) -> None:
        resolved = self.resolve(
            credential=credential,
            safe_ref=safe_ref,
            expected_object_type=expected_object_type,
        )
        row = self._s.exec(
            select(ProviderObjectReference).where(
                col(ProviderObjectReference.safe_ref) == resolved.safe_ref
            )
        ).one()
        row.stale_at = datetime.now(tz=UTC).replace(tzinfo=None)
        row.updated_at = row.stale_at
        self._s.add(row)
        self._s.flush()

    def _binding(self, credential: Credential) -> tuple[int, str]:
        if credential.id is None or credential.project_id is None:
            raise ConflictError("provider object refs require a project credential")
        accounts = self._s.exec(
            select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
        ).all()
        account_ids = {
            str(account.provider_account_id).strip()
            for account in accounts
            if account.provider_account_id is not None and str(account.provider_account_id).strip()
        }
        if len(account_ids) != 1:
            raise ConflictError(
                "provider object refs require one verified provider account",
                data={
                    "credential_ref": credential.credential_ref,
                    "provider_key": credential.provider_key,
                    "account_count": len(account_ids),
                    "next_action": (
                        "Reconnect and verify the provider account before using object refs."
                    ),
                },
            )
        return credential.project_id, next(iter(account_ids))

    @staticmethod
    def _safe_ref(value: Any) -> str:
        if not isinstance(value, str) or not value.startswith("provider-object:"):
            raise ValidationError("provider object ref must use provider-object:<opaque> format")
        suffix = value.removeprefix("provider-object:")
        if not suffix or len(value) > 120:
            raise ValidationError("provider object ref is invalid")
        return value

    @staticmethod
    def _object_type(value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or len(normalized) > 200:
            raise ValidationError("provider object type is invalid")
        return normalized


__all__ = ["ProviderObjectReferenceRepository", "ResolvedProviderObject"]
