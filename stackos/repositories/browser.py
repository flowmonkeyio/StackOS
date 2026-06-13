"""Repository helpers and public models for StackOS browser automation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from stackos.db.models import (
    Artifact,
    BrowserActionReceipt,
    BrowserProfile,
    BrowserSession,
    Project,
)
from stackos.repositories.base import Envelope, NotFoundError, Page


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


class BrowserRuntimeStatusOut(BaseModel):
    provider: str
    package_installed: bool
    package_version: str | None
    browser_downloaded: bool
    browser_path_present: bool = False
    executable_path: str | None
    live_session_refs: list[str]
    repair: str | None = None
    method_manifest: list[dict[str, Any]]


class BrowserProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    profile_key: str
    name: str
    provider: str
    status: str
    profile_ref: str
    allowed_origins_json: list[str] | None
    launch_options_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class BrowserSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    profile_id: int
    profile_ref: str
    session_ref: str
    provider: str
    status: str
    headless: bool
    page_refs_json: list[str] | None
    current_url: str | None
    metadata_json: dict[str, Any] | None
    started_at: datetime
    ended_at: datetime | None
    updated_at: datetime


class BrowserActionReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    profile_id: int | None
    session_id: int | None
    artifact_id: int | None
    session_ref: str | None
    page_ref: str | None
    operation: str
    method: str
    side_effect_class: str
    target_url: str | None
    target_origin: str | None
    status: str
    input_summary_json: dict[str, Any] | None
    result_json: dict[str, Any] | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class BrowserCallOut(BaseModel):
    receipt: BrowserActionReceiptOut
    session: BrowserSessionOut | None = None
    artifact: dict[str, Any] | None = None
    result: dict[str, Any]


class BrowserRepository:
    """Read and write browser profiles, sessions, and receipts."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found", data={"project_id": project_id})

    def profile_ref(self, *, project_id: int, profile_key: str) -> str:
        return f"browser-profile:project-{project_id}:{profile_key}"

    def session_ref(self, *, project_id: int, profile_key: str, session_key: str) -> str:
        return f"browser-session:project-{project_id}:{profile_key}:{session_key}"

    def create_profile(
        self,
        *,
        project_id: int,
        profile_key: str,
        name: str,
        allowed_origins_json: list[str] | None,
        launch_options_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None,
    ) -> Envelope[BrowserProfileOut]:
        self.require_project(project_id)
        ref = self.profile_ref(project_id=project_id, profile_key=profile_key)
        existing = self._s.exec(
            select(BrowserProfile).where(
                col(BrowserProfile.project_id) == project_id,
                col(BrowserProfile.profile_key) == profile_key,
            )
        ).first()
        now = _utcnow()
        if existing is None:
            row = BrowserProfile(
                project_id=project_id,
                profile_key=profile_key,
                name=name,
                profile_ref=ref,
                allowed_origins_json=allowed_origins_json,
                launch_options_json=launch_options_json,
                metadata_json=metadata_json,
            )
        else:
            row = existing
            row.name = name
            row.status = "ready"
            row.profile_ref = ref
            if allowed_origins_json is not None:
                row.allowed_origins_json = allowed_origins_json
            if launch_options_json is not None:
                row.launch_options_json = launch_options_json
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
            items=[BrowserProfileOut.model_validate(row) for row in rows],
            total_estimate=len(rows),
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
        headless: bool,
        page_refs: list[str],
        current_url: str | None,
        metadata_json: dict[str, Any] | None,
    ) -> Envelope[BrowserSessionOut]:
        row = self._s.exec(
            select(BrowserSession).where(
                col(BrowserSession.project_id) == project_id,
                col(BrowserSession.session_ref) == session_ref,
            )
        ).first()
        now = _utcnow()
        if row is None:
            row = BrowserSession(
                project_id=project_id,
                profile_id=_required_id(profile.id),
                session_ref=session_ref,
                status="running",
                headless=headless,
                page_refs_json=page_refs,
                current_url=current_url,
                metadata_json=metadata_json,
            )
        else:
            row.profile_id = _required_id(profile.id)
            row.status = "running"
            row.headless = headless
            row.page_refs_json = page_refs
            row.current_url = current_url
            row.metadata_json = metadata_json
            row.ended_at = None
            row.updated_at = now
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

    def get_session(
        self,
        *,
        project_id: int,
        session_ref: str,
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

    def stop_session(self, *, project_id: int, session_ref: str) -> Envelope[BrowserSessionOut]:
        session_row, profile = self.get_session(project_id=project_id, session_ref=session_ref)
        now = _utcnow()
        session_row.status = "stopped"
        session_row.ended_at = now
        session_row.updated_at = now
        self._s.add(session_row)
        self._s.commit()
        self._s.refresh(session_row)
        return Envelope(data=self._session_out(session_row, profile), project_id=project_id)

    def update_session_url(
        self,
        *,
        project_id: int,
        session_ref: str,
        current_url: str | None,
        page_refs: list[str] | None = None,
    ) -> BrowserSessionOut:
        session_row, profile = self.get_session(project_id=project_id, session_ref=session_ref)
        session_row.current_url = current_url
        if page_refs is not None:
            session_row.page_refs_json = page_refs
        session_row.updated_at = _utcnow()
        self._s.add(session_row)
        self._s.commit()
        self._s.refresh(session_row)
        return self._session_out(session_row, profile)

    def create_artifact(
        self,
        *,
        project_id: int,
        uri: str,
        name: str,
        mime_type: str,
        size_bytes: int | None,
        metadata_json: dict[str, Any] | None,
        provenance_json: dict[str, Any] | None,
    ) -> Artifact:
        row = Artifact(
            project_id=project_id,
            kind="browser-screenshot",
            uri=uri,
            name=name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            metadata_json=metadata_json,
            provenance_json=provenance_json,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return row

    def record_receipt(
        self,
        *,
        project_id: int,
        profile_id: int | None,
        session_id: int | None,
        artifact_id: int | None,
        session_ref: str | None,
        page_ref: str | None,
        operation: str,
        method: str,
        side_effect_class: str,
        target_url: str | None,
        target_origin: str | None,
        status: str,
        input_summary_json: dict[str, Any] | None,
        result_json: dict[str, Any] | None,
        error: str | None = None,
    ) -> BrowserActionReceiptOut:
        row = BrowserActionReceipt(
            project_id=project_id,
            profile_id=profile_id,
            session_id=session_id,
            artifact_id=artifact_id,
            session_ref=session_ref,
            page_ref=page_ref,
            operation=operation,
            method=method,
            side_effect_class=side_effect_class,
            target_url=target_url,
            target_origin=target_origin,
            status=status,
            input_summary_json=input_summary_json,
            result_json=result_json,
            error=error,
            completed_at=_utcnow(),
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return BrowserActionReceiptOut.model_validate(row)

    def session_out(
        self,
        session_row: BrowserSession,
        profile: BrowserProfile,
    ) -> BrowserSessionOut:
        """Return the public output model for a session/profile pair."""
        return self._session_out(session_row, profile)

    def _session_out(
        self,
        session_row: BrowserSession,
        profile: BrowserProfile,
    ) -> BrowserSessionOut:
        data = session_row.model_dump()
        data["profile_ref"] = profile.profile_ref
        return BrowserSessionOut.model_validate(data)


__all__ = [
    "BrowserActionReceiptOut",
    "BrowserCallOut",
    "BrowserProfileOut",
    "BrowserRepository",
    "BrowserRuntimeStatusOut",
    "BrowserSessionOut",
]
