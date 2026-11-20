"""One shared catalog and optimistic write boundary for every adapter."""

import hashlib
import json
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import JSON, delete, func, literal, or_, update
from sqlalchemy import select as sa_select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlmodel import Session, col, select

from stackos.db.model_base import _utcnow
from stackos.db.model_patterns import PatternDeletion
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
    etag: str = Field(description="Opaque record identity/revision token; copy it unchanged.")
    created_at: datetime
    updated_at: datetime
    source: Literal["custom", "stackos", "customized"] = "custom"
    source_label: str = "Custom"


class PatternDetail(PatternSummary):
    body: str


class PatternPage(Page[PatternSummary]):
    catalog_total: int


class BundledPattern(PatternContent):
    bundled_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


class PatternBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=120, pattern=r"^\S+$")
    entries: list[BundledPattern] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique_identity(self):
        for field in ("bundled_id", "key"):
            values = [getattr(entry, field) for entry in self.entries]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {field}")
        return self


def _content_hash(*, title: str, summary: str, tags: list[str], body: str) -> str:
    payload = dict(title=title, summary=summary, tags=tags, body=body)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source(row) -> Literal["custom", "stackos", "customized"]:
    if row.bundled_id is None:
        return "custom"
    return "stackos" if row.content_hash == row.bundled_base_hash else "customized"


def _summary(row) -> PatternSummary:
    source = _source(row)
    return PatternSummary(
        id=row.id,
        pattern_ref=f"pattern:{row.id}",
        key=row.key,
        title=row.title,
        summary=row.summary,
        tags=row.tags_json,
        revision=row.revision,
        # Keys can be reused after deletion; bind the precondition to the
        # non-reused row ID too so an old draft cannot target its replacement.
        etag=f"{row.id}:{row.revision}",
        created_at=row.created_at,
        updated_at=row.updated_at,
        source=source,
        source_label={
            "custom": "Custom",
            "stackos": "StackOS",
            "customized": "StackOS · Customized",
        }[source],
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
            col(Pattern.bundled_id),
            col(Pattern.bundled_base_hash),
            col(Pattern.content_hash),
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
        if source == "custom":
            statement = statement.where(col(Pattern.bundled_id).is_(None))
        elif source in {"stackos", "customized"}:
            statement = statement.where(col(Pattern.bundled_id).is_not(None))
            if source == "customized":
                statement = statement.where(
                    col(Pattern.content_hash) != col(Pattern.bundled_base_hash)
                )
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
        payload["content_hash"] = _content_hash(**content.model_dump(exclude={"key"}))
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
        bundled_id = self._s.exec(select(Pattern.bundled_id).where(Pattern.id == pattern_id)).one()
        result = self._s.execute(
            delete(Pattern).where(
                col(Pattern.id) == pattern_id,
                col(Pattern.revision) == current.revision,
            )
        )
        if cast(CursorResult, result).rowcount != 1:
            self._s.expire_all()
            self._conflict(self.get(pattern_id=pattern_id))
        if bundled_id is not None:
            self._s.execute(
                insert(PatternDeletion).values(bundled_id=bundled_id).on_conflict_do_nothing()
            )
        self._s.commit()
        return PatternSummary(**current.model_dump(exclude={"body"}))

    def refresh_bundle(self, raw: str) -> dict[str, int]:
        """Startup-only trusted input. Validate everything before any writes.

        Revision CAS shares the normal edit/delete boundary. A committed edit
        after the read wins; failed comparisons never advance its shipped base.
        """
        try:
            bundle = PatternBundle.model_validate_json(raw)
        except PydanticValidationError as exc:
            # Do not log article content or unbounded validation input at startup.
            raise ValidationError("Invalid packaged pattern bundle") from exc
        result = dict(inserted=0, updated=0, customized=0, deleted=0, collisions=0, concurrent=0)
        try:
            for entry in bundle.entries:
                self._refresh_entry(entry, bundle.version, result)
            self._s.commit()
        except Exception:
            self._s.rollback()
            raise
        return result

    def _refresh_entry(self, entry: BundledPattern, version: str, result: dict[str, int]) -> None:
        if self._s.get(PatternDeletion, entry.bundled_id) is not None:
            result["deleted"] += 1
            return
        row = self._s.exec(select(Pattern).where(Pattern.bundled_id == entry.bundled_id)).first()
        # Never infer ownership from the public key or title. Preserve collisions.
        collision = self._s.exec(
            select(Pattern.id).where(
                or_(col(Pattern.key) == entry.key, col(Pattern.title) == entry.title),
                col(Pattern.id) != row.id if row is not None else True,
            )
        ).first()
        if collision is not None:
            result["collisions"] += 1
            return
        content = entry.model_dump(exclude={"bundled_id", "key"})
        digest = _content_hash(**content)
        payload = {k: v for k, v in content.items() if k != "tags"} | {
            "tags_json": entry.tags,
            "content_hash": digest,
            "bundled_version": version,
            "bundled_base_hash": digest,
        }
        if row is None:
            now = _utcnow()
            values = payload | dict(
                key=entry.key,
                bundled_id=entry.bundled_id,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            # Guard in the insert itself: another refresh may insert and an
            # operator may delete after our initial read. Never resurrect it.
            allowed = (
                ~select(PatternDeletion)
                .where(PatternDeletion.bundled_id == entry.bundled_id)
                .exists()
            )
            written = self._s.execute(
                insert(Pattern)
                .from_select(
                    list(values),
                    sa_select(
                        *(
                            literal(value, type_=JSON) if name == "tags_json" else literal(value)
                            for name, value in values.items()
                        )
                    ).where(allowed),
                )
                .on_conflict_do_nothing()
            )
            if cast(CursorResult, written).rowcount == 1:
                result["inserted"] += 1
            elif self._s.get(PatternDeletion, entry.bundled_id) is not None:
                result["deleted"] += 1
            else:
                result["collisions"] += 1
            return
        current_hash = _content_hash(
            title=row.title, summary=row.summary, tags=row.tags_json, body=row.body
        )
        if current_hash != row.bundled_base_hash:
            result["customized"] += 1
            return
        if digest == row.bundled_base_hash and version == row.bundled_version:
            return
        written = self._s.execute(
            update(Pattern)
            .where(
                col(Pattern.id) == row.id,
                col(Pattern.revision) == row.revision,
            )
            .values(**payload, revision=row.revision + 1, updated_at=_utcnow())
        )
        result["updated" if cast(CursorResult, written).rowcount == 1 else "concurrent"] += 1

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
