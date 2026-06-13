"""SQLModel table declarations for StackOS browser automation state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _utcnow


class BrowserProfile(SQLModel, table=True):
    """Project-scoped persistent browser profile metadata.

    The actual user-data directory stays daemon-side. Agents receive stable refs
    and policy metadata, never filesystem paths, cookies, or storage state.
    """

    __tablename__ = "browser_profiles"
    __table_args__ = (
        UniqueConstraint("project_id", "profile_key", name="uq_browser_profiles_project_key"),
        Index("ix_browser_profiles_project_status", "project_id", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    )
    profile_key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    provider: str = Field(default="camoufox", max_length=80)
    status: str = Field(default="ready", max_length=40)
    profile_ref: str = Field(max_length=220)
    allowed_origins_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    launch_options_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class BrowserSession(SQLModel, table=True):
    """One live or historical daemon-owned browser session."""

    __tablename__ = "browser_sessions"
    __table_args__ = (
        UniqueConstraint("project_id", "session_ref", name="uq_browser_sessions_project_ref"),
        Index("ix_browser_sessions_project_status", "project_id", "status"),
        Index("ix_browser_sessions_profile", "profile_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    )
    profile_id: int = Field(
        sa_column=Column(ForeignKey("browser_profiles.id", ondelete="CASCADE"), nullable=False)
    )
    session_ref: str = Field(max_length=220)
    provider: str = Field(default="camoufox", max_length=80)
    status: str = Field(default="starting", max_length=40)
    headless: bool = Field(default=False, nullable=False)
    page_refs_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    current_url: str | None = Field(default=None, max_length=2048)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    started_at: datetime = Field(default_factory=_utcnow, nullable=False)
    ended_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class BrowserActionReceipt(SQLModel, table=True):
    """Redacted audit receipt for one browser operation."""

    __tablename__ = "browser_action_receipts"
    __table_args__ = (
        Index("ix_browser_action_receipts_project", "project_id", "created_at"),
        Index("ix_browser_action_receipts_session", "session_id"),
        Index("ix_browser_action_receipts_profile", "profile_id"),
        Index("ix_browser_action_receipts_artifact", "artifact_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    )
    profile_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("browser_profiles.id", ondelete="SET NULL"), nullable=True),
    )
    session_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("browser_sessions.id", ondelete="SET NULL"), nullable=True),
    )
    artifact_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
    )
    session_ref: str | None = Field(default=None, max_length=220)
    page_ref: str | None = Field(default=None, max_length=220)
    operation: str = Field(max_length=120)
    method: str = Field(max_length=120)
    side_effect_class: str = Field(max_length=80)
    target_url: str | None = Field(default=None, max_length=2048)
    target_origin: str | None = Field(default=None, max_length=300)
    status: str = Field(max_length=40)
    input_summary_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    result_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    completed_at: datetime | None = Field(default=None)


__all__ = ["BrowserActionReceipt", "BrowserProfile", "BrowserSession"]
