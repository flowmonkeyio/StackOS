"""Encrypted project-scoped action payload values."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, ForeignKey, Index, LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _utcnow


class PayloadSecret(SQLModel, table=True):
    """One immutable string value referenced symbolically from action input."""

    __tablename__ = "payload_secrets"
    __table_args__ = (
        UniqueConstraint("secret_ref", name="uq_payload_secrets_ref"),
        Index("ix_payload_secrets_project", "project_id"),
        Index("ix_payload_secrets_project_ref", "project_id", "secret_ref"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    secret_ref: str = Field(max_length=120, nullable=False)
    value_type: str = Field(default="string", max_length=40, nullable=False)
    encrypted_payload: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    nonce: bytes = Field(sa_column=Column(LargeBinary(12), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


__all__ = ["PayloadSecret"]
