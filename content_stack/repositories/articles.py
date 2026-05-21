"""Articles + versions + assets + publishes + sources + schema-emits.

This is the heaviest M1.B module. It implements the audit B-07
optimistic concurrency model on the ``articles`` fat row:

- Every mutating ``set_*`` / ``mark_*`` method takes ``expected_etag``.
- On mismatch we raise ``ConflictError`` (-32008 / HTTP 409) so the
  REST + MCP transport adapters surface it consistently.
- On success we regenerate ``step_etag`` (UUID4) and bump
  ``last_completed_step`` so the next caller picks up a fresh token.

State-machine: PLAN.md L388 lists ``briefing → outlined → drafted →
edited → eeat_passed → published`` with ``refresh_due`` and
``aborted-publish`` as side branches. The transition map lives in
``content_stack.db.models.ARTICLE_STATUS_TRANSITIONS``.

**Transition contract** (locked here, mirrored to skill docstrings in M7):

- ``set_brief(expected_etag)`` — writes ``brief_json`` and advances
  ``briefing → outlined``. Per PLAN.md L928, the ``content-brief``
  skill (#4) call is the *first* mutating call after ``article.create``
  and is what graduates the article from "row exists with no brief" to
  "ready for outline". M7 skill #4 calls this; the article enters
  ``outlined`` waiting for skill #6.
- ``set_outline(expected_etag)`` — writes ``outline_md``; status stays
  ``outlined`` (no transition). Status only advances on the *next*
  set_draft call.
- ``set_draft(expected_etag, append=False/True)`` — writes
  ``draft_md`` (replace OR append). Status advance to ``drafted``
  happens on ``mark_drafted``, NOT auto-advanced — skills 7/8/9 each
  call ``set_draft`` then the procedure runner calls ``mark_drafted``
  once at the end of the draft phase.
- ``set_edited(expected_etag)`` — writes ``edited_md`` and advances
  ``drafted → edited``. The editor skill (#10) is the only writer.
- ``mark_eeat_passed(expected_etag, run_id, eeat_criteria_version)`` —
  ``edited → eeat_passed``. Records the frozen rubric version for
  audit reproducibility.
- ``mark_published(expected_etag, run_id)`` — ``eeat_passed → published``.
  Slug becomes immutable from this point. The actual publish-target
  rows live in ``article_publishes`` (separate table); this method
  only mutates the article fat row.
- ``mark_refresh_due(reason)`` — ``published → refresh_due``. Two
  callers: skill #23 (refresh-detector) AND humans via the UI escape
  hatch (per audit P-I6). No etag required because this is a
  one-shot status flip — the brief/outline/draft/edited columns are
  not touched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, update
from sqlmodel import Session, select

from content_stack.db.models import (
    ARTICLE_STATUS_TRANSITIONS,
    Article,
    ArticleAsset,
    ArticleAssetKind,
    ArticlePublish,
    ArticlePublishStatus,
    ArticleStatus,
    ArticleVersion,
    Artifact,
    ResearchSource,
    SchemaEmit,
)
from content_stack.repositories.base import (
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
    validate_transition,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _new_etag() -> str:
    return str(uuid.uuid4())


# Refresh-detector window per PLAN.md L508-L520.
_REFRESH_WINDOW_DAYS = 90
_REFRESH_EVAL_WINDOW_DAYS = 7


# ---------------------------------------------------------------------------
# Output models.
# ---------------------------------------------------------------------------


class ArticleOut(BaseModel):
    """Public shape for ``articles`` rows (full fat-row hydrate)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    topic_id: int | None
    author_id: int | None
    reviewer_author_id: int | None
    canonical_target_id: int | None
    owner_run_id: int | None
    slug: str
    title: str
    status: ArticleStatus
    brief_json: dict[str, Any] | None
    outline_md: str | None
    draft_md: str | None
    edited_md: str | None
    voice_id_used: int | None
    eeat_criteria_version_used: int | None
    last_refreshed_at: datetime | None
    last_evaluated_for_refresh_at: datetime | None
    last_link_audit_at: datetime | None
    version: int
    current_step: str | None
    last_completed_step: str | None
    step_started_at: datetime | None
    step_etag: str | None
    lock_token: str | None
    created_at: datetime
    updated_at: datetime


class ArticleVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    version: int
    brief_json: dict[str, Any] | None
    outline_md: str | None
    draft_md: str | None
    edited_md: str | None
    frontmatter_json: dict[str, Any] | None
    published_url: str | None
    published_at: datetime | None
    voice_id_used: int | None
    eeat_criteria_version_used: int | None
    created_at: datetime
    refreshed_at: datetime | None
    refresh_reason: str | None


class ArticleAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    kind: ArticleAssetKind
    prompt: str | None
    url: str
    alt_text: str | None
    width: int | None
    height: int | None
    position: int | None
    created_at: datetime


class ArticlePublishOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    target_id: int | None
    version_published: int
    published_url: str | None
    frontmatter_json: dict[str, Any] | None
    published_at: datetime | None
    status: ArticlePublishStatus
    error: str | None


class ResearchSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int
    url: str
    title: str | None
    snippet: str | None
    fetched_at: datetime
    used: bool


class SchemaEmitOut(BaseModel):
    """Public shape for ``schema_emits`` rows.

    The ``schema_json`` column name shadows ``BaseModel.schema_json`` in
    pydantic v2 (which is a classmethod for OpenAPI emission). The same
    pattern is in ``content_stack.db.models.SchemaEmit`` and the warning
    is suppressed in ``pyproject.toml``'s pytest filter; we mirror the
    type-ignore here so mypy accepts the column-named attribute.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    article_id: int | None
    type: str
    schema_json: dict[str, Any] | None  # type: ignore[assignment]
    position: int | None
    is_primary: bool
    version_published: int | None
    validated_at: datetime | None


# ---------------------------------------------------------------------------
# ArticleRepository.
# ---------------------------------------------------------------------------


class ArticleRepository:
    """The article fat row + optimistic concurrency on every set_*."""

    def __init__(self, session: Session) -> None:
        self._s = session

    # -------- Create / read / list --------

    def create(
        self,
        *,
        project_id: int,
        topic_id: int | None,
        title: str,
        slug: str,
        voice_id: int | None = None,
        eeat_criteria_version: int | None = None,
        author_id: int | None = None,
        reviewer_author_id: int | None = None,
    ) -> Envelope[ArticleOut]:
        """Insert a fresh article in ``status='briefing'`` with a fresh ``step_etag``."""
        if not slug or len(slug) > 80:
            raise ValidationError("slug must be 1..80 chars", data={"slug": slug})
        existing = self._s.exec(
            select(Article).where(
                Article.project_id == project_id,
                Article.slug == slug,
            )
        ).first()
        if existing is not None:
            raise ConflictError(
                f"slug {slug!r} already in use for project {project_id}",
                data={"slug": slug, "existing_id": existing.id},
            )
        row = Article(
            project_id=project_id,
            topic_id=topic_id,
            author_id=author_id,
            reviewer_author_id=reviewer_author_id,
            slug=slug,
            title=title,
            status=ArticleStatus.BRIEFING,
            voice_id_used=voice_id,
            eeat_criteria_version_used=eeat_criteria_version,
            version=1,
            step_etag=_new_etag(),
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ArticleOut.model_validate(row), project_id=project_id)

    def get(self, article_id: int) -> ArticleOut:
        """Full row with all JSON columns hydrated."""
        return ArticleOut.model_validate(self._fetch(article_id))

    def list(
        self,
        project_id: int,
        *,
        status: ArticleStatus | None = None,
        topic_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ArticleOut]:
        """Cursor-paginated list."""
        stmt = select(Article).where(Article.project_id == project_id)
        if status is not None:
            stmt = stmt.where(Article.status == status)
        if topic_id is not None:
            stmt = stmt.where(Article.topic_id == topic_id)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=Article.id,
            limit=limit,
            after_id=after_id,
            converter=ArticleOut.model_validate,
        )

    def list_due_for_refresh(
        self,
        project_id: int,
        *,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ArticleOut]:
        """Implement the canonical refresh-detector query (PLAN.md L508-L520).

        Surface ``status='published'`` articles whose ``last_refreshed_at`` is
        90+ days old (or null) AND whose ``last_evaluated_for_refresh_at`` is
        7+ days old (or null), so the detector skill (#23) doesn't thrash.
        Ordering: ``published_at ASC`` per PLAN.md spec; we use
        ``COALESCE(last_refreshed_at, created_at) ASC`` because ``published_at``
        lives in ``article_publishes``, not on the fat row.
        """
        now = _utcnow()
        ninety_ago = now - timedelta(days=_REFRESH_WINDOW_DAYS)
        seven_ago = now - timedelta(days=_REFRESH_EVAL_WINDOW_DAYS)
        from sqlalchemy import func, or_

        stmt = (
            select(Article)
            .where(
                Article.project_id == project_id,
                Article.status == ArticleStatus.PUBLISHED,
                or_(
                    Article.last_refreshed_at.is_(None),  # type: ignore[union-attr,attr-defined]
                    Article.last_refreshed_at < ninety_ago,  # type: ignore[operator,arg-type]
                ),
                or_(
                    Article.last_evaluated_for_refresh_at.is_(None),  # type: ignore[union-attr,attr-defined]
                    Article.last_evaluated_for_refresh_at < seven_ago,  # type: ignore[operator,arg-type]
                ),
            )
            .order_by(
                func.coalesce(Article.last_refreshed_at, Article.created_at).asc(),
                Article.id.asc(),  # type: ignore[union-attr,attr-defined]
            )
        )
        # Cursor pagination on id ASC for stability across the multi-column sort.
        return cursor_paginate(
            self._s,
            stmt,
            id_col=Article.id,
            limit=limit,
            after_id=after_id,
            converter=ArticleOut.model_validate,
        )

    # -------- Mutating set_* / mark_* methods --------

    def set_brief(
        self,
        article_id: int,
        brief_json: dict[str, Any],
        *,
        expected_etag: str,
    ) -> Envelope[ArticleOut]:
        """Write ``brief_json`` and advance ``briefing → outlined``."""
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=ArticleStatus.BRIEFING,
            to_status=ArticleStatus.OUTLINED,
            patch={"brief_json": brief_json},
            step="set_brief",
        )

    def set_outline(
        self,
        article_id: int,
        outline_md: str,
        *,
        expected_etag: str,
    ) -> Envelope[ArticleOut]:
        """Write ``outline_md``. No status transition (status stays ``outlined``).

        The article must already be in ``outlined`` (came from set_brief).
        Calling set_outline a second time replaces; the step_etag rotates
        each call.
        """
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=ArticleStatus.OUTLINED,
            to_status=ArticleStatus.OUTLINED,
            patch={"outline_md": outline_md},
            step="set_outline",
        )

    def set_draft(
        self,
        article_id: int,
        draft_md: str,
        *,
        expected_etag: str,
        append: bool = False,
    ) -> Envelope[ArticleOut]:
        """Write or append ``draft_md``.

        Skills 7/8/9 (intro/body/conclusion) each call set_draft once;
        the procedure runner stitches them with ``append=True`` for
        skills 8 and 9. ``append=False`` is the first call (intro).

        Status: must be ``outlined`` OR ``drafted``. After this method
        the article is in ``drafted`` only via ``mark_drafted`` — set_draft
        does NOT auto-advance. This split lets skills 7/8/9 each take a
        run at the body without prematurely exiting the draft phase.
        """
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=(ArticleStatus.OUTLINED, ArticleStatus.DRAFTED),
            to_status=None,  # no transition
            patch={"draft_md": draft_md},
            step="set_draft",
            append_field="draft_md" if append else None,
        )

    def mark_drafted(
        self,
        article_id: int,
        *,
        expected_etag: str,
    ) -> Envelope[ArticleOut]:
        """Advance ``outlined → drafted`` once the procedure runner ends the draft phase."""
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=ArticleStatus.OUTLINED,
            to_status=ArticleStatus.DRAFTED,
            patch={},
            step="mark_drafted",
        )

    def set_edited(
        self,
        article_id: int,
        edited_md: str,
        *,
        expected_etag: str,
    ) -> Envelope[ArticleOut]:
        """Write ``edited_md`` and advance/remain at ``edited``.

        The editor advances ``drafted → edited``; the humanizer may then
        replace the edited body while the article is already ``edited``.
        Both writes still rotate the etag for audit/concurrency.
        """
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=(ArticleStatus.DRAFTED, ArticleStatus.EDITED),
            to_status=ArticleStatus.EDITED,
            patch={"edited_md": edited_md},
            step="set_edited",
        )

    def mark_eeat_passed(
        self,
        article_id: int,
        *,
        expected_etag: str,
        run_id: int,
        eeat_criteria_version: int,
    ) -> Envelope[ArticleOut]:
        """Move ``edited → eeat_passed``; freeze rubric version + run id.

        The frozen ``eeat_criteria_version_used`` makes audits
        reproducible — a subsequent rubric edit (M7+) does not retro-
        actively invalidate already-published content.
        """
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=ArticleStatus.EDITED,
            to_status=ArticleStatus.EEAT_PASSED,
            patch={
                "eeat_criteria_version_used": eeat_criteria_version,
                "owner_run_id": run_id,
            },
            step="mark_eeat_passed",
        )

    def mark_published(
        self,
        article_id: int,
        *,
        expected_etag: str,
        run_id: int,
    ) -> Envelope[ArticleOut]:
        """Move ``eeat_passed → published``. Slug becomes immutable.

        The actual ``article_publishes`` rows are written by
        ``ArticlePublishRepository.record_publish`` (separate method,
        same module). This method only mutates the article fat row.
        """
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=ArticleStatus.EEAT_PASSED,
            to_status=ArticleStatus.PUBLISHED,
            patch={"owner_run_id": run_id},
            step="mark_published",
        )

    def mark_aborted_publish(
        self,
        article_id: int,
        *,
        expected_etag: str,
        run_id: int,
    ) -> Envelope[ArticleOut]:
        """Move a pre-publish article into the terminal aborted-publish state."""
        return self._mutate(
            article_id,
            expected_etag=expected_etag,
            from_status=(
                ArticleStatus.BRIEFING,
                ArticleStatus.OUTLINED,
                ArticleStatus.DRAFTED,
                ArticleStatus.EDITED,
                ArticleStatus.EEAT_PASSED,
            ),
            to_status=ArticleStatus.ABORTED_PUBLISH,
            patch={"owner_run_id": run_id},
            step="mark_aborted_publish",
        )

    def mark_refresh_due(
        self,
        article_id: int,
        *,
        reason: str,
    ) -> Envelope[ArticleOut]:
        """Move ``published → refresh_due``.

        No etag required (per the docstring at module top — this is a
        one-shot status flip, not a content edit).  Two callers:
        - Skill #23 (refresh-detector) on its weekly schedule.
        - Human via the UI escape hatch (audit P-I6).
        """
        if not reason:
            raise ValidationError("reason is required for mark_refresh_due")
        row = self._fetch(article_id)
        validate_transition(
            row.status,
            ArticleStatus.REFRESH_DUE,
            ARTICLE_STATUS_TRANSITIONS,
            label="article.status",
        )
        row.status = ArticleStatus.REFRESH_DUE
        row.last_evaluated_for_refresh_at = _utcnow()
        row.step_etag = _new_etag()
        row.last_completed_step = "mark_refresh_due"
        row.updated_at = _utcnow()
        # Stash reason in the brief_json metadata so callers can audit
        # without a separate column. Non-destructive merge.
        meta = dict(row.brief_json or {})
        meta.setdefault("refresh_history", []).append(
            {"at": _utcnow().isoformat(), "reason": reason}
        )
        row.brief_json = meta
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ArticleOut.model_validate(row), project_id=row.project_id)

    def update_slug(
        self,
        article_id: int,
        new_slug: str,
    ) -> Envelope[ArticleOut]:
        """Pre-publish slug edit; refuses post-publish.

        PLAN.md L354 / D6: slug is immutable post-``status='published'``.
        Pre-publish slug changes write a ``redirects`` row only on
        publish (procedure 7 / refresh path); this method just updates
        the column.
        """
        row = self._fetch(article_id)
        if row.status in (ArticleStatus.PUBLISHED, ArticleStatus.REFRESH_DUE):
            raise ConflictError(
                "slug is immutable post-publish",
                data={"current_status": row.status.value, "article_id": article_id},
            )
        if not new_slug or len(new_slug) > 80:
            raise ValidationError("slug must be 1..80 chars")
        existing = self._s.exec(
            select(Article).where(
                Article.project_id == row.project_id,
                Article.slug == new_slug,
                Article.id != article_id,  # type: ignore[arg-type]
            )
        ).first()
        if existing is not None:
            raise ConflictError(
                f"slug {new_slug!r} already in use",
                data={"slug": new_slug, "existing_id": existing.id},
            )
        row.slug = new_slug
        row.step_etag = _new_etag()
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ArticleOut.model_validate(row), project_id=row.project_id)

    # -------- Versioning --------

    def create_version(self, article_id: int) -> Envelope[ArticleVersionOut]:
        """Snapshot the live row into ``article_versions``.

        Used by procedure 7 (humanize-pass) and content-refresher (M7).
        Bumps the *new* article version number on the source row and
        updates ``last_refreshed_at`` + ``article_versions.refreshed_at``.
        """
        row = self._fetch(article_id)
        snapshot = ArticleVersion(
            article_id=row.id,  # type: ignore[arg-type]
            version=row.version,
            brief_json=row.brief_json,
            outline_md=row.outline_md,
            draft_md=row.draft_md,
            edited_md=row.edited_md,
            voice_id_used=row.voice_id_used,
            eeat_criteria_version_used=row.eeat_criteria_version_used,
            refreshed_at=_utcnow(),
        )
        # Bump the source row's version + last_refreshed_at.
        row.version += 1
        row.last_refreshed_at = _utcnow()
        row.step_etag = _new_etag()
        row.updated_at = _utcnow()
        self._s.add(snapshot)
        self._s.add(row)
        self._s.commit()
        self._s.refresh(snapshot)
        return Envelope(
            data=ArticleVersionOut.model_validate(snapshot),
            project_id=row.project_id,
        )

    def list_versions(
        self,
        article_id: int,
        *,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ArticleVersionOut]:
        """Cursor-paginated version history."""
        stmt = select(ArticleVersion).where(ArticleVersion.article_id == article_id)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=ArticleVersion.id,
            limit=limit,
            after_id=after_id,
            converter=ArticleVersionOut.model_validate,
        )

    # -------- Internal --------

    def _fetch(self, article_id: int) -> Article:
        row = self._s.get(Article, article_id)
        if row is None:
            raise NotFoundError(f"article {article_id} not found")
        return row

    def _mutate(
        self,
        article_id: int,
        *,
        expected_etag: str,
        from_status: ArticleStatus | tuple[ArticleStatus, ...],
        to_status: ArticleStatus | None,
        patch: dict[str, Any],
        step: str,
        append_field: str | None = None,
    ) -> Envelope[ArticleOut]:
        """Common path for every set_* / mark_* method.

        - Verify current status is in ``from_status``.
        - Verify ``expected_etag`` matches.
        - Apply ``patch`` (with optional append-mode for set_draft).
        - If ``to_status`` is set, validate the transition + apply.
        - Rotate ``step_etag``, advance ``last_completed_step``, bump
          ``updated_at``.
        """
        allowed = (from_status,) if isinstance(from_status, ArticleStatus) else from_status
        if to_status is not None:
            for status in allowed:
                if to_status != status:
                    validate_transition(
                        status, to_status, ARTICLE_STATUS_TRANSITIONS, label="article.status"
                    )

        values: dict[str, Any] = {}
        for k, v in patch.items():
            if append_field == k and v is not None:
                values[k] = func.coalesce(getattr(Article, k), "") + v
            else:
                values[k] = v
        if to_status is not None:
            values["status"] = to_status
        values["step_etag"] = _new_etag()
        values["last_completed_step"] = step
        values["updated_at"] = _utcnow()

        stmt = (
            update(Article)
            .where(
                cast(Any, Article.id) == article_id,
                cast(Any, Article.step_etag) == expected_etag,
                cast(Any, Article.status).in_(allowed),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        result = self._s.execute(stmt)
        if cast(Any, result).rowcount != 1:
            self._s.rollback()
            self._s.expire_all()
            row = self._fetch(article_id)
            if expected_etag != row.step_etag:
                raise ConflictError(
                    "expected_etag mismatch — article was modified concurrently",
                    data={
                        "expected_etag": expected_etag,
                        "current_etag": row.step_etag,
                        "article_id": article_id,
                        "step": step,
                    },
                )
            raise ConflictError(
                f"{step} requires status in {[s.value for s in allowed]}, "
                f"current is {row.status.value!r}",
                data={
                    "current_status": row.status.value,
                    "allowed": [s.value for s in allowed],
                    "step": step,
                    "article_id": article_id,
                },
            )
        self._s.commit()
        self._s.expire_all()
        row = self._fetch(article_id)
        return Envelope(data=ArticleOut.model_validate(row), project_id=row.project_id)


def _publish_target_clause(target_id: int | None) -> Any:
    """Build the upsert key predicate for target-backed vs targetless publishes."""
    if target_id is None:
        return ArticlePublish.target_id.is_(None)  # type: ignore[union-attr,attr-defined]
    return ArticlePublish.target_id == target_id


# ---------------------------------------------------------------------------
# ArticleAssetRepository.
# ---------------------------------------------------------------------------


class ArticleAssetRepository:
    """Hero / inline / OG / etc. images for an article."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        *,
        article_id: int,
        kind: ArticleAssetKind,
        url: str,
        prompt: str | None = None,
        alt_text: str | None = None,
        width: int | None = None,
        height: int | None = None,
        position: int | None = None,
    ) -> Envelope[ArticleAssetOut]:
        """Insert a new asset row."""
        row = ArticleAsset(
            article_id=article_id,
            kind=kind,
            url=url,
            prompt=prompt,
            alt_text=alt_text,
            width=width,
            height=height,
            position=position,
        )
        self._s.add(row)
        self._s.flush()
        article = self._s.get(Article, article_id)
        if article is not None and row.id is not None:
            asset_kind = kind.value if isinstance(kind, ArticleAssetKind) else str(kind)
            self._s.add(
                Artifact(
                    project_id=article.project_id,
                    kind="article-asset",
                    uri=url,
                    name=alt_text[:300] if alt_text else None,
                    mime_type=None,
                    metadata_json={
                        "asset_kind": asset_kind,
                        "alt_text": alt_text,
                        "width": width,
                        "height": height,
                        "position": position,
                    },
                    provenance_json={
                        "source_table": "article_assets",
                        "source_id": row.id,
                        "article_id": article_id,
                        "prompt": prompt,
                    },
                )
            )
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ArticleAssetOut.model_validate(row))

    def list(self, article_id: int) -> list[ArticleAssetOut]:
        """All assets for an article in id order."""
        rows = self._s.exec(
            select(ArticleAsset)
            .where(ArticleAsset.article_id == article_id)
            .order_by(ArticleAsset.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [ArticleAssetOut.model_validate(r) for r in rows]

    def update(self, asset_id: int, **patch: Any) -> Envelope[ArticleAssetOut]:
        """Partial update; commonly used by alt-text-auditor (#14)."""
        row = self._s.get(ArticleAsset, asset_id)
        if row is None:
            raise NotFoundError(f"asset {asset_id} not found")
        for k, v in patch.items():
            if k in {"id", "article_id", "created_at"}:
                continue
            if not hasattr(row, k):
                raise ValidationError(f"unknown field {k!r}")
            setattr(row, k, v)
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ArticleAssetOut.model_validate(row))

    def remove(self, asset_id: int) -> Envelope[ArticleAssetOut]:
        """Hard-delete an asset row."""
        row = self._s.get(ArticleAsset, asset_id)
        if row is None:
            raise NotFoundError(f"asset {asset_id} not found")
        out = ArticleAssetOut.model_validate(row)
        self._s.delete(row)
        self._s.commit()
        return Envelope(data=out)


# ---------------------------------------------------------------------------
# ArticlePublishRepository.
# ---------------------------------------------------------------------------


class ArticlePublishRepository:
    """Publish records for target-backed and external/manual publishes.

    Target-backed rows upsert on ``(article_id, target_id,
    version_published)``. Targetless rows are the operator-agent/manual
    publish escape hatch and upsert on ``(article_id, version_published)``
    where ``target_id IS NULL``.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def record_publish(
        self,
        *,
        article_id: int,
        target_id: int | None,
        version_published: int,
        published_url: str | None = None,
        frontmatter_json: dict[str, Any] | None = None,
        status: ArticlePublishStatus = ArticlePublishStatus.PUBLISHED,
        error: str | None = None,
        published_at: datetime | None = None,
    ) -> Envelope[ArticlePublishOut]:
        """Upsert a publish record on the target-backed or targetless key."""
        existing = self._s.exec(
            select(ArticlePublish).where(
                ArticlePublish.article_id == article_id,
                _publish_target_clause(target_id),
                ArticlePublish.version_published == version_published,
            )
        ).first()
        if existing is None:
            row = ArticlePublish(
                article_id=article_id,
                target_id=target_id,
                version_published=version_published,
                published_url=published_url,
                frontmatter_json=frontmatter_json,
                published_at=published_at or _utcnow(),
                status=status,
                error=error,
            )
        else:
            row = existing
            row.published_url = published_url
            row.frontmatter_json = frontmatter_json
            row.published_at = published_at or _utcnow()
            row.status = status
            row.error = error
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ArticlePublishOut.model_validate(row))

    def record_external(
        self,
        *,
        article_id: int,
        version_published: int,
        published_url: str,
        frontmatter_json: dict[str, Any] | None = None,
        external_ref: str | None = None,
        status: ArticlePublishStatus = ArticlePublishStatus.PUBLISHED,
        error: str | None = None,
        expected_etag: str | None = None,
        run_id: int | None = None,
        mark_article_published: bool = True,
        published_at: datetime | None = None,
    ) -> Envelope[ArticlePublishOut]:
        """Record a targetless publish and optionally advance the article.

        This is the durable handoff for the default ``agent-publish`` skill:
        Codex may publish through a website repo, DB MCP, or another external
        tool, then records the result without requiring a ``publish_targets``
        row for the project.
        """
        article = self._s.get(Article, article_id)
        if article is None:
            raise NotFoundError(f"article {article_id} not found")
        should_mark_published = (
            mark_article_published
            and status == ArticlePublishStatus.PUBLISHED
            and article.status != ArticleStatus.PUBLISHED
        )
        if should_mark_published and article.status != ArticleStatus.EEAT_PASSED:
            raise ValidationError(
                "mark_article_published requires article status 'eeat_passed'",
                data={"article_id": article_id, "current_status": article.status.value},
            )
        if should_mark_published and (expected_etag is None or run_id is None):
            raise ValidationError("expected_etag and run_id are required to mark article published")
        metadata = dict(frontmatter_json or {})
        metadata.setdefault("publisher", "agent")
        if external_ref is not None:
            metadata["external_ref"] = external_ref
        env = self.record_publish(
            article_id=article_id,
            target_id=None,
            version_published=version_published,
            published_url=published_url,
            frontmatter_json=metadata,
            status=status,
            error=error,
            published_at=published_at,
        )
        if should_mark_published:
            assert expected_etag is not None
            assert run_id is not None
            ArticleRepository(self._s).mark_published(
                article_id,
                expected_etag=expected_etag,
                run_id=run_id,
            )
        return Envelope(data=env.data, project_id=article.project_id)

    def list_for_article(self, article_id: int) -> list[ArticlePublishOut]:
        """All publish rows for an article in id order."""
        rows = self._s.exec(
            select(ArticlePublish)
            .where(ArticlePublish.article_id == article_id)
            .order_by(ArticlePublish.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [ArticlePublishOut.model_validate(r) for r in rows]

    def set_canonical(self, *, article_id: int, target_id: int) -> Envelope[ArticleOut]:
        """Set ``articles.canonical_target_id`` for SEO canonical-URL emission.

        Returns the updated article row so callers can re-render the
        article detail page without a separate read.
        """
        article = self._s.get(Article, article_id)
        if article is None:
            raise NotFoundError(f"article {article_id} not found")
        article.canonical_target_id = target_id
        article.updated_at = _utcnow()
        self._s.add(article)
        self._s.commit()
        self._s.refresh(article)
        return Envelope(data=ArticleOut.model_validate(article), project_id=article.project_id)


# ---------------------------------------------------------------------------
# ResearchSourceRepository.
# ---------------------------------------------------------------------------


class ResearchSourceRepository:
    """Citations / research sources per article."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def add(
        self,
        *,
        article_id: int,
        url: str,
        title: str | None = None,
        snippet: str | None = None,
        used: bool = False,
    ) -> Envelope[ResearchSourceOut]:
        """Insert a research-source row."""
        row = ResearchSource(
            article_id=article_id,
            url=url,
            title=title,
            snippet=snippet,
            used=used,
            fetched_at=_utcnow(),
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ResearchSourceOut.model_validate(row))

    def list(self, article_id: int, *, used: bool | None = None) -> list[ResearchSourceOut]:
        """All sources for an article, optionally filtered by citation use."""
        stmt = select(ResearchSource).where(ResearchSource.article_id == article_id)
        if used is not None:
            stmt = stmt.where(ResearchSource.used == used)
        rows = self._s.exec(
            stmt.order_by(ResearchSource.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [ResearchSourceOut.model_validate(r) for r in rows]

    def update_used(self, source_id: int, *, used: bool) -> Envelope[ResearchSourceOut]:
        """Patch the ``used`` flag on a research-source row."""
        row = self._s.get(ResearchSource, source_id)
        if row is None:
            raise NotFoundError(f"research source {source_id} not found")
        row.used = used
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ResearchSourceOut.model_validate(row))


# ---------------------------------------------------------------------------
# SchemaEmitRepository.
# ---------------------------------------------------------------------------


class SchemaEmitRepository:
    """JSON-LD blobs per article + the ``is_primary`` exactly-one invariant."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def set(
        self,
        *,
        article_id: int,
        type: str,
        schema_json: dict[str, Any],
        is_primary: bool = False,
        position: int | None = None,
        version_published: int | None = None,
    ) -> Envelope[SchemaEmitOut]:
        """Insert or update a schema-emit row.

        ``is_primary`` invariant: exactly one row per ``article_id`` may
        be primary. When ``is_primary=True``, this method transactionally
        sets ``is_primary=False`` on every other row of the same article.
        """
        if is_primary:
            others = self._s.exec(
                select(SchemaEmit).where(
                    SchemaEmit.article_id == article_id,
                    SchemaEmit.is_primary.is_(True),  # type: ignore[union-attr,attr-defined]
                )
            ).all()
            for o in others:
                o.is_primary = False
                self._s.add(o)
        # Upsert on (article_id, type) — there's no DB unique constraint
        # because templates with article_id=NULL share types; we enforce
        # uniqueness at the article-grain only here.
        existing = self._s.exec(
            select(SchemaEmit).where(
                SchemaEmit.article_id == article_id,
                SchemaEmit.type == type,
            )
        ).first()
        if existing is None:
            row = SchemaEmit(
                article_id=article_id,
                type=type,
                schema_json=schema_json,
                is_primary=is_primary,
                position=position,
                version_published=version_published,
            )
        else:
            row = existing
            row.schema_json = schema_json
            row.is_primary = is_primary
            row.position = position
            row.version_published = version_published
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=SchemaEmitOut.model_validate(row))

    def get(self, schema_id: int) -> SchemaEmitOut:
        """Fetch one schema-emit row."""
        row = self._s.get(SchemaEmit, schema_id)
        if row is None:
            raise NotFoundError(f"schema emit {schema_id} not found")
        return SchemaEmitOut.model_validate(row)

    def validate(self, schema_id: int) -> Envelope[SchemaEmitOut]:
        """Mark a schema-emit row as validated.

        The actual JSON-LD validation logic lives in skill #16 (M7) +
        Google's structured-data testing API integration (M5). M1
        only flips ``validated_at`` so the UI can show "validated"
        without a real check; M5 will replace the body of this method.
        """
        row = self._s.get(SchemaEmit, schema_id)
        if row is None:
            raise NotFoundError(f"schema emit {schema_id} not found")
        row.validated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=SchemaEmitOut.model_validate(row))

    def list_for_article(self, article_id: int) -> list[SchemaEmitOut]:
        """All schema-emit rows for an article ordered by position then id."""
        rows = self._s.exec(
            select(SchemaEmit)
            .where(SchemaEmit.article_id == article_id)
            .order_by(SchemaEmit.position.asc().nullslast(), SchemaEmit.id.asc())  # type: ignore[union-attr,attr-defined]
        ).all()
        return [SchemaEmitOut.model_validate(r) for r in rows]


__all__ = [
    "ArticleAssetOut",
    "ArticleAssetRepository",
    "ArticleOut",
    "ArticlePublishOut",
    "ArticlePublishRepository",
    "ArticleRepository",
    "ArticleVersionOut",
    "ResearchSourceOut",
    "ResearchSourceRepository",
    "SchemaEmitOut",
    "SchemaEmitRepository",
]
