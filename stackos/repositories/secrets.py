"""Project-scoped encrypted values used only at action execution time."""

from __future__ import annotations

import secrets as stdlib_secrets

from sqlmodel import Session, select

from stackos.crypto.aes_gcm import decrypt, encrypt
from stackos.db.models import PayloadSecret, Project
from stackos.repositories.base import ValidationError

_VALUE_TYPE = "string"
_REF_PREFIX = "secret_"


def _aad_kind(secret_ref: str) -> str:
    return f"payload-secret:{_VALUE_TYPE}:{secret_ref}"


class PayloadSecretRepository:
    """Create opaque refs and resolve them only for the shared action runtime."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def set(self, *, project_id: int, value: str) -> PayloadSecret:
        if self._s.get(Project, project_id) is None:
            raise ValidationError(
                "project does not exist",
                data={"project_id": project_id},
            )
        if not isinstance(value, str):
            raise ValidationError("payload secret value must be a string")
        if not value:
            raise ValidationError("payload secret value must not be empty")

        secret_ref = self._new_ref()
        encrypted_payload, nonce = encrypt(
            value.encode("utf-8"),
            project_id=project_id,
            kind=_aad_kind(secret_ref),
        )
        row = PayloadSecret(
            project_id=project_id,
            secret_ref=secret_ref,
            value_type=_VALUE_TYPE,
            encrypted_payload=encrypted_payload,
            nonce=nonce,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return row

    def is_available(self, *, project_id: int, secret_ref: str) -> bool:
        return self._row(project_id=project_id, secret_ref=secret_ref) is not None

    def resolve(self, *, project_id: int, secret_ref: str) -> str:
        row = self._row(project_id=project_id, secret_ref=secret_ref)
        if row is None:
            raise ValidationError("payload secret reference is unavailable")
        plaintext = decrypt(
            row.encrypted_payload,
            nonce=row.nonce,
            project_id=project_id,
            kind=_aad_kind(row.secret_ref),
        )
        return plaintext.decode("utf-8")

    def _row(self, *, project_id: int, secret_ref: str) -> PayloadSecret | None:
        return self._s.exec(
            select(PayloadSecret).where(
                PayloadSecret.project_id == project_id,
                PayloadSecret.secret_ref == secret_ref,
                PayloadSecret.value_type == _VALUE_TYPE,
            )
        ).first()

    def _new_ref(self) -> str:
        for _ in range(8):
            candidate = _REF_PREFIX + stdlib_secrets.token_urlsafe(24)
            existing = self._s.exec(
                select(PayloadSecret.id).where(PayloadSecret.secret_ref == candidate)
            ).first()
            if existing is None:
                return candidate
        raise ValidationError("could not allocate an opaque payload secret reference")


__all__ = ["PayloadSecretRepository"]
