"""Typed event envelopes for durable StackOS project events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StackOSEventSource(BaseModel):
    """Origin of an emitted StackOS event."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=80)
    id: int | None = None
    ref: str | None = Field(default=None, max_length=300)

    @field_validator("type")
    @classmethod
    def _clean_type(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source type is required")
        return value

    @field_validator("ref")
    @classmethod
    def _clean_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class StackOSEvent(BaseModel):
    """Durable event envelope accepted by StackOS event emitters."""

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0)
    event_type: str = Field(min_length=1, max_length=120)
    source: StackOSEventSource
    run_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: str | None = Field(default=None, max_length=160)
    correlation_id: str | None = Field(default=None, max_length=160)
    schema_version: int | None = Field(default=1, gt=0)
    occurred_at: datetime | None = None

    @field_validator("event_type")
    @classmethod
    def _clean_event_type(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("event type is required")
        return value

    @field_validator("title", "summary", "actor", "correlation_id")
    @classmethod
    def _clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = str(raw).strip()
            if tag and tag not in seen:
                cleaned.append(tag)
                seen.add(tag)
        return cleaned
