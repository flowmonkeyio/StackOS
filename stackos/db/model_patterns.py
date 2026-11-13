"""Installation-wide reusable guidance, independent of project lifecycle."""

from datetime import datetime

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

from stackos.db.model_base import _utcnow


class Pattern(SQLModel, table=True):
    __tablename__ = "patterns"
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(max_length=120, unique=True, index=True)
    title: str = Field(max_length=200)
    summary: str = Field(max_length=1000)
    body: str = Field(sa_column=Column(Text, nullable=False))
    tags_json: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    revision: int = Field(default=1)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
