"""One shared catalog and optimistic write boundary for every adapter."""

from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, false, func, or_, update
from sqlalchemy import select as sa_select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlmodel import Session, col, select

from stackos.db.model_base import _utcnow
from stackos.db.models import Pattern
from stackos.repositories.base import (
    ConflictError,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
)
from stackos.validation_errors import safe_validation_errors

PatternSource = Literal["all", "stackos", "customized", "custom"]


class PatternContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(max_length=1000)
    body: str = Field(min_length=1, max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("key", "title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("body")
    @classmethod
    def markdown(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value.strip()) > 100 for value in values):
            raise ValueError("tags must contain 1 to 100 characters")
        return sorted({value.strip() for value in values})


class PatternSummary(BaseModel):
    id: int
    pattern_ref: str
    key: str
    title: str
    summary: str
    tags: list[str]
    revision: int
    etag: str
    created_at: datetime
    updated_at: datetime
    source: Literal["custom", "stackos", "customized"] = "custom"
    source_label: str = "Custom"


class PatternDetail(PatternSummary):
    body: str


class PatternPage(Page[PatternSummary]):
    catalog_total: int


def _summary(row) -> PatternSummary:
    return PatternSummary(
        id=row.id,
        pattern_ref=f"pattern:{row.id}",
        key=row.key,
        title=row.title,
        summary=row.summary,
        tags=row.tags_json,
        revision=row.revision,
        etag=str(row.revision),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PatternRepository:
    def __init__(self, session: Session):
        self._s = session

    def get(self, *, pattern_id: int | None = None, key: str | None = None) -> PatternDetail:
        if (pattern_id is None) == (key is None):
            raise ValidationError("Supply exactly one of pattern_id or key")
        row = self._s.exec(
            select(Pattern).where(
                Pattern.id == pattern_id if pattern_id is not None else Pattern.key == key
            )
        ).first()
        if row is None:
            raise NotFoundError("Pattern not found", data={"pattern_id": pattern_id, "key": key})
        return PatternDetail(**_summary(row).model_dump(), body=row.body)

    def list(
        self,
        *,
        query: str | None = None,
        tags: list[str] | None = None,
        source: PatternSource = "all",
        limit: int = 25,
        cursor: int | None = None,
    ) -> PatternPage:
        if source not in {"all", "custom", "stackos", "customized"}:
            raise ValidationError("Unknown pattern source filter")
        if not 1 <= limit <= 100 or (cursor is not None and cursor < 1):
            raise ValidationError("limit must be 1..100; cursor must be positive")
        # Select only summary columns; article bodies never enter a list result.
        statement = sa_select(
            col(Pattern.id),
            col(Pattern.key),
            col(Pattern.title),
            col(Pattern.summary),
            col(Pattern.tags_json),
            col(Pattern.revision),
            col(Pattern.created_at),
            col(Pattern.updated_at),
        )
        text_query = (query or "").strip()
        tag_values = func.json_each(Pattern.tags_json).table_valued("value")
        if text_query:
            statement = statement.where(
                or_(
                    col(Pattern.title).icontains(text_query, autoescape=True),
                    col(Pattern.summary).icontains(text_query, autoescape=True),
                    col(Pattern.body).icontains(text_query, autoescape=True),
                    select(tag_values.c.value)
                    .where(tag_values.c.value.icontains(text_query, autoescape=True))
                    .exists(),
                )
            )
        if tags:
            statement = statement.where(
                select(tag_values.c.value).where(tag_values.c.value.in_(tags)).exists()
            )
        # This owner currently contains only Custom records. The bundled-update
        # ticket extends this derived boundary, never the editable API payload.
        if source in {"stackos", "customized"}:
            statement = statement.where(false())
        page = cursor_paginate(
            self._s,
            statement,
            id_col=Pattern.id,
            limit=limit,
            after_id=cursor,
            converter=_summary,
        )
        total = self._s.exec(select(func.count()).select_from(Pattern)).one()
        return PatternPage(**page.model_dump(), catalog_total=total)

    def upsert(self, *, expected_etag: str | None = None, **values) -> PatternDetail:
        try:
            content = PatternContent.model_validate(values)
        except PydanticValidationError as exc:
            raise ValidationError(
                "Invalid pattern", data={"errors": safe_validation_errors(exc.errors())}
            ) from exc
        payload = content.model_dump(exclude={"tags"}) | {"tags_json": content.tags}
        # ON CONFLICT allows stable-key create retries without a duplicate or a
        # transaction-wide rollback. A differing existing payload still needs CAS.
        if expected_etag is None:
            now = _utcnow()
            self._s.execute(
                insert(Pattern)
                .values(
                    **payload,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )
        current = self.get(key=content.key)
        if expected_etag is None:
            if all(getattr(current, k) == v for k, v in content.model_dump().items()):
                self._s.commit()
                return current
            self._conflict(current)
        if expected_etag != current.etag:
            self._conflict(current)
        result = self._s.execute(
            update(Pattern)
            .where(
                col(Pattern.id) == current.id,
                col(Pattern.revision) == current.revision,
            )
            .values(**payload, revision=current.revision + 1, updated_at=_utcnow())
        )
        if cast(CursorResult, result).rowcount != 1:
            self._s.expire_all()
            self._conflict(self.get(key=content.key))
        self._s.commit()
        return self.get(key=content.key)

    def delete(self, pattern_id: int, *, expected_etag: str) -> PatternSummary:
        current = self.get(pattern_id=pattern_id)
        if expected_etag != current.etag:
            self._conflict(current)
        result = self._s.execute(
            delete(Pattern).where(
                col(Pattern.id) == pattern_id,
                col(Pattern.revision) == current.revision,
            )
        )
        if cast(CursorResult, result).rowcount != 1:
            self._s.expire_all()
            self._conflict(self.get(pattern_id=pattern_id))
        self._s.commit()
        return PatternSummary(**current.model_dump(exclude={"body"}))

    @staticmethod
    def _conflict(current: PatternDetail):
        raise ConflictError(
            "Pattern changed; read the current entry before saving or deleting",
            data={
                "pattern_id": current.id,
                "pattern_ref": current.pattern_ref,
                "current_etag": current.etag,
                "current_revision": current.revision,
                "next_action": "Call pattern.get and reconcile the draft before retrying.",
            },
        )
