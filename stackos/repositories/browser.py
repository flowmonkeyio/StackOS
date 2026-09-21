"""Durable profile/session identity for the native gstack browser lifecycle."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from stackos.browser.runtime import BROWSER_PROVIDER, NativeSessionState
from stackos.db.models import BrowserProfile, BrowserSession, Project
from stackos.repositories.base import Envelope, NotFoundError, Page, ValidationError


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


_CANONICAL_BROWSER_KEY = r"[a-z0-9](?:[a-z0-9_.-]{0,158}[a-z0-9])?"
_SESSION_REF_RE = re.compile(
    rf"^browser-session:project-([1-9][0-9]*):({_CANONICAL_BROWSER_KEY}):({_CANONICAL_BROWSER_KEY})$"
)


class BrowserRuntimeStatusOut(BaseModel):
    provider: str
    package_installed: bool
    package_version: str | None
    browser_downloaded: bool
    browser_path_present: bool = False
    live_session_refs: list[str]
    repair: str | None = None


class BrowserProfileOut(BaseModel):
    """Public profile identity; legacy database columns are not active controls."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    profile_key: str
    name: str
    provider: str
    status: str
    profile_ref: str
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class BrowserSessionOut(BaseModel):
    """Safe selected-session detail or bulk session summary."""

    id: int
    project_id: int
    profile_id: int
    profile_ref: str
    session_ref: str
    provider: str
    status: str
    healthy: bool | None = None
    repair: str | None = None
    cli_argv: list[str]
    native_cli: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None
    started_at: datetime
    ended_at: datetime | None
    updated_at: datetime


class BrowserRepository:
    """The single durable project/profile/session owner.

    BrowserActionReceipt and the historic Playwright-only columns remain in the
    database for retention, but this repository intentionally has no active
    receipt, artifact, page, URL, or launch-option writer.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found", data={"project_id": project_id})

    def profile_ref(self, *, project_id: int, profile_key: str) -> str:
        return f"browser-profile:project-{project_id}:{profile_key}"

    def session_ref(self, *, project_id: int, profile_key: str, session_key: str) -> str:
        return f"browser-session:project-{project_id}:{profile_key}:{session_key}"

    @staticmethod
    def project_id_from_session_ref(session_ref: str) -> int:
        """Read project scope from one exact, canonical session reference."""
        match = _SESSION_REF_RE.fullmatch(session_ref)
        if match is None:
            raise ValidationError("browser session_ref must be a canonical full session reference")
        return int(match.group(1))

    def create_profile(
        self,
        *,
        project_id: int,
        profile_key: str,
        name: str,
        metadata_json: dict[str, Any] | None,
    ) -> Envelope[BrowserProfileOut]:
        self.require_project(project_id)
        ref = self.profile_ref(project_id=project_id, profile_key=profile_key)
        row = self._s.exec(
            select(BrowserProfile).where(
                col(BrowserProfile.project_id) == project_id,
                col(BrowserProfile.profile_key) == profile_key,
            )
        ).first()
        now = _utcnow()
        if row is None:
            row = BrowserProfile(
                project_id=project_id,
                profile_key=profile_key,
                name=name,
                provider=BROWSER_PROVIDER,
                profile_ref=ref,
                metadata_json=metadata_json,
            )
        else:
            row.name = name
            row.provider = BROWSER_PROVIDER
            row.status = "ready"
            row.profile_ref = ref
            row.metadata_json = metadata_json
            row.updated_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=BrowserProfileOut.model_validate(row), project_id=project_id)

    def list_profiles(self, *, project_id: int) -> Page[BrowserProfileOut]:
        self.require_project(project_id)
        rows = list(
            self._s.exec(
                select(BrowserProfile)
                .where(col(BrowserProfile.project_id) == project_id)
                .order_by(col(BrowserProfile.id).asc())
            ).all()
        )
        return Page(
            items=[BrowserProfileOut.model_validate(row) for row in rows], total_estimate=len(rows)
        )

    def get_profile(self, *, project_id: int, profile_ref: str) -> BrowserProfile:
        row = self._s.exec(
            select(BrowserProfile).where(
                col(BrowserProfile.project_id) == project_id,
                col(BrowserProfile.profile_ref) == profile_ref,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                "browser profile not found",
                data={"project_id": project_id, "profile_ref": profile_ref},
            )
        return row

    def create_or_update_session(
        self,
        *,
        project_id: int,
        profile: BrowserProfile,
        session_ref: str,
        metadata_json: dict[str, Any] | None,
    ) -> Envelope[BrowserSessionOut]:
        row = self._s.exec(
            select(BrowserSession).where(
                col(BrowserSession.project_id) == project_id,
                col(BrowserSession.session_ref) == session_ref,
            )
        ).first()
        now = _utcnow()
        profile.provider = BROWSER_PROVIDER
        if row is None:
            row = BrowserSession(
                project_id=project_id,
                profile_id=_required_id(profile.id),
                session_ref=session_ref,
                provider=BROWSER_PROVIDER,
                status="running",
                headless=False,
                metadata_json=metadata_json,
            )
        else:
            row.profile_id = _required_id(profile.id)
            row.provider = BROWSER_PROVIDER
            row.status = "running"
            row.metadata_json = metadata_json
            row.ended_at = None
            row.updated_at = now
        self._s.add(profile)
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._session_out(row, profile), project_id=project_id)

    def list_sessions(self, *, project_id: int) -> Page[BrowserSessionOut]:
        self.require_project(project_id)
        rows = list(
            self._s.exec(
                select(BrowserSession, BrowserProfile)
                .join(BrowserProfile, col(BrowserSession.profile_id) == col(BrowserProfile.id))
                .where(col(BrowserSession.project_id) == project_id)
                .order_by(col(BrowserSession.id).asc())
            ).all()
        )
        return Page(
            items=[self._session_out(session, profile) for session, profile in rows],
            total_estimate=len(rows),
        )

    def reconcile_sessions(
        self, *, project_id: int, states: Mapping[str, NativeSessionState]
    ) -> list[str]:
        """Persist observed lifecycle state without treating health failure as retirement."""
        self.require_project(project_id)
        rows = list(
            self._s.exec(
                select(BrowserSession).where(col(BrowserSession.project_id) == project_id)
            ).all()
        )
        changed: list[str] = []
        now = _utcnow()
        for row in rows:
            state = states.get(row.session_ref)
            if state is None:
                continue
            next_status: str | None = None
            ended_at: datetime | None | object = row.ended_at
            if state.owned:
                next_status = state.status
                ended_at = None
            elif state.status == "stopped":
                next_status = "stopped"
                ended_at = now
            elif row.status == "running":
                next_status = "stale"
                ended_at = now
            if next_status is None:
                continue
            if row.status != next_status or row.ended_at != ended_at:
                row.status = next_status
                row.ended_at = ended_at if isinstance(ended_at, datetime) else None
                row.updated_at = now
                self._s.add(row)
                changed.append(row.session_ref)
        if changed:
            self._s.commit()
        return changed

    def get_session(
        self, *, project_id: int, session_ref: str
    ) -> tuple[BrowserSession, BrowserProfile]:
        row = self._s.exec(
            select(BrowserSession, BrowserProfile)
            .join(BrowserProfile, col(BrowserSession.profile_id) == col(BrowserProfile.id))
            .where(
                col(BrowserSession.project_id) == project_id,
                col(BrowserSession.session_ref) == session_ref,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                "browser session not found",
                data={"project_id": project_id, "session_ref": session_ref},
            )
        return row

    def session_out(
        self,
        session_row: BrowserSession,
        profile: BrowserProfile | None = None,
        *,
        state: NativeSessionState | None = None,
        include_native_cli: bool = False,
        native_cli: dict[str, Any] | None = None,
    ) -> BrowserSessionOut:
        if profile is None:
            profile = self._s.get(BrowserProfile, session_row.profile_id)
            if profile is None:
                raise NotFoundError(
                    "browser profile not found", data={"profile_id": session_row.profile_id}
                )
        return self._session_out(
            session_row,
            profile,
            state=state,
            include_native_cli=include_native_cli,
            native_cli=native_cli,
        )

    @staticmethod
    def _session_out(
        session_row: BrowserSession,
        profile: BrowserProfile,
        *,
        state: NativeSessionState | None = None,
        include_native_cli: bool = False,
        native_cli: dict[str, Any] | None = None,
    ) -> BrowserSessionOut:
        selected_native_cli: dict[str, Any] | None = None
        healthy: bool | None = None
        repair: str | None = None
        if state is not None:
            healthy = state.healthy if state.owned else None
            repair = state.repair
        if include_native_cli and native_cli is not None:
            selected_native_cli = dict(native_cli)
        return BrowserSessionOut(
            id=_required_id(session_row.id),
            project_id=session_row.project_id,
            profile_id=session_row.profile_id,
            profile_ref=profile.profile_ref,
            session_ref=session_row.session_ref,
            provider=session_row.provider,
            status=session_row.status,
            healthy=healthy,
            repair=repair,
            cli_argv=["stackos.browser", "--session", session_row.session_ref],
            native_cli=selected_native_cli,
            metadata_json=session_row.metadata_json,
            started_at=session_row.started_at,
            ended_at=session_row.ended_at,
            updated_at=session_row.updated_at,
        )


__all__ = [
    "BrowserProfileOut",
    "BrowserRepository",
    "BrowserRuntimeStatusOut",
    "BrowserSessionOut",
]
