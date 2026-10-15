"""Validated, generic local backup configuration (never recovery key material)."""

from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import limits


class BackupError(ValueError):
    """An operator-safe failure, suitable for CLI/status without exception internals."""

    def __init__(self, code: str, message: str, next_action: str = "Check backup configuration."):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_action = next_action

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "next_action": self.next_action}


def parse_public_key(value: str) -> X25519PublicKey:
    try:
        key = serialization.load_pem_public_key(value.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BackupError("invalid_public_key", "Use an X25519 recovery public key PEM.") from exc
    if not isinstance(key, X25519PublicKey):
        raise BackupError("invalid_public_key", "Use an X25519 recovery public key PEM.")
    return key


class BackupTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=200)
    path: Path
    lock_file: Path | None = None

    @field_validator("path", "lock_file")
    @classmethod
    def absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Backup paths must be absolute.")
        return value


class BackupPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=168)
    keep_last: int = Field(default=30, ge=1, le=limits.MAX_RETAINED_ARCHIVES)
    destination: Path | None = None
    recipient_public_key: str | None = Field(default=None, max_length=4096)
    targets: list[BackupTarget] = Field(default_factory=list, max_length=100)

    @field_validator("destination")
    @classmethod
    def absolute_destination(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Backup destination must be absolute.")
        return value

    @field_validator("recipient_public_key")
    @classmethod
    def public_key(cls, value: str | None) -> str | None:
        if value is not None:
            key = parse_public_key(value)
            return key.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode("ascii")
        return None
