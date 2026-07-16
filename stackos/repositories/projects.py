"""Repositories for StackOS projects, credentials, budgets, and schedules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_
from sqlmodel import Session, col, select

from stackos.artifacts import redact_secrets
from stackos.db.models import (
    IntegrationBudget,
    IntegrationCredential,
    Project,
    ScheduledJob,
)
from stackos.repositories.base import (
    BudgetExceededError,
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    domain: str
    niche: str | None
    locale: str
    is_active: bool
    schedule_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class IntegrationCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    kind: str
    profile_key: str
    expires_at: datetime | None
    last_refreshed_at: datetime | None
    config_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class IntegrationBudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    kind: str
    monthly_budget_usd: float
    alert_threshold_pct: int
    current_month_spend: float
    current_month_calls: int
    qps: float
    last_reset: datetime
    created_at: datetime
    updated_at: datetime


class ScheduledJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    kind: str
    cron_expr: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_status: str | None
    enabled: bool


class ProjectRepository:
    """CRUD for the project container."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        *,
        slug: str,
        name: str,
        domain: str,
        niche: str | None = None,
        locale: str = "en-US",
        schedule_json: dict[str, Any] | None = None,
    ) -> Envelope[ProjectOut]:
        now = _utcnow()
        if self._s.exec(select(Project.id).where(Project.slug == slug)).first() is not None:
            raise ConflictError(f"project slug {slug!r} already exists")
        row = Project(
            slug=slug,
            name=name,
            domain=domain,
            niche=niche,
            locale=locale,
            schedule_json=schedule_json,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ProjectOut.model_validate(row), project_id=row.id)

    def get(self, id_or_slug: int | str) -> ProjectOut:
        row = self._fetch_row(id_or_slug)
        return ProjectOut.model_validate(row)

    def resolve_identifier(
        self,
        *,
        project_id: int | None = None,
        project_slug: str | None = None,
        project_name: str | None = None,
    ) -> ProjectOut:
        """Resolve one project by an agent-discoverable identifier."""
        if project_id is not None:
            return self.get(project_id)
        if project_slug is not None:
            return self.get(project_slug)
        if project_name is None:
            raise ValidationError("one of project_id, project_slug, or project_name is required")
        rows = self._s.exec(select(Project).where(Project.name == project_name)).all()
        if len(rows) == 1:
            return ProjectOut.model_validate(rows[0])
        if len(rows) > 1:
            raise ValidationError(
                "project_name matches multiple projects; pass project_id or project_slug"
            )
        raise NotFoundError(f"project named {project_name!r} not found")

    def list(
        self,
        *,
        active_only: bool = False,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ProjectOut]:
        stmt = select(Project)
        if active_only:
            stmt = stmt.where(col(Project.is_active).is_(True))
        return cursor_paginate(
            self._s,
            stmt,
            id_col=Project.id,
            limit=limit,
            after_id=after_id,
            converter=lambda row: ProjectOut.model_validate(row),
        )

    def update(self, project_id: int, **patch: Any) -> Envelope[ProjectOut]:
        row = self._fetch_row(project_id)
        if "slug" in patch and patch["slug"] != row.slug:
            raise ValidationError("project slug is immutable")
        allowed = {"name", "domain", "niche", "locale", "schedule_json", "is_active"}
        for key, value in patch.items():
            if key in allowed:
                setattr(row, key, value)
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ProjectOut.model_validate(row), project_id=row.id)

    def delete(self, project_id: int, *, hard: bool = False) -> Envelope[ProjectOut]:
        row = self._fetch_row(project_id)
        if hard:
            out = ProjectOut.model_validate(row)
            self._s.delete(row)
            self._s.commit()
            return Envelope(data=out, project_id=project_id)
        row.is_active = False
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ProjectOut.model_validate(row), project_id=row.id)

    def _fetch_row(self, id_or_slug: int | str) -> Project:
        if isinstance(id_or_slug, int):
            row = self._s.get(Project, id_or_slug)
        else:
            row = self._s.exec(select(Project).where(Project.slug == id_or_slug)).first()
        if row is None:
            raise NotFoundError(f"project {id_or_slug!r} not found")
        return row


class IntegrationCredentialRepository:
    """Encrypted credential storage for daemon-side tool execution."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self, project_id: int | None) -> list[IntegrationCredentialOut]:
        stmt = select(IntegrationCredential)
        if project_id is None:
            stmt = stmt.where(col(IntegrationCredential.project_id).is_(None))
        else:
            stmt = stmt.where(
                or_(
                    col(IntegrationCredential.project_id) == project_id,
                    col(IntegrationCredential.project_id).is_(None),
                )
            )
        rows = self._s.exec(stmt.order_by(col(IntegrationCredential.id).asc())).all()
        return [IntegrationCredentialOut.model_validate(row) for row in rows]

    def set(
        self,
        *,
        project_id: int | None,
        kind: str,
        secret_payload: bytes,
        profile_key: str = "default",
        config_json: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
        commit: bool = True,
    ) -> Envelope[IntegrationCredentialOut]:
        from stackos.crypto.aes_gcm import encrypt as _crypto_encrypt

        if config_json is not None and redact_secrets(config_json) != config_json:
            raise ValidationError(
                "credential config_json must not contain secret-like keys; "
                "put secrets in the encrypted credential payload"
            )
        existing_stmt = select(IntegrationCredential).where(
            IntegrationCredential.kind == kind,
            IntegrationCredential.profile_key == profile_key,
        )
        if project_id is None:
            existing_stmt = existing_stmt.where(IntegrationCredential.project_id.is_(None))  # type: ignore[union-attr]
        else:
            existing_stmt = existing_stmt.where(IntegrationCredential.project_id == project_id)
        row = self._s.exec(existing_stmt).first()
        ciphertext, nonce = _crypto_encrypt(secret_payload, project_id=project_id, kind=kind)
        now = _utcnow()
        if row is None:
            row = IntegrationCredential(
                project_id=project_id,
                kind=kind,
                profile_key=profile_key,
                encrypted_payload=ciphertext,
                nonce=nonce,
                expires_at=expires_at,
                config_json=config_json,
                last_refreshed_at=now,
                created_at=now,
                updated_at=now,
            )
        else:
            row.encrypted_payload = ciphertext
            row.nonce = nonce
            row.expires_at = expires_at
            row.config_json = config_json
            row.last_refreshed_at = now
            row.updated_at = now
        self._s.add(row)
        if commit:
            self._s.commit()
            self._s.refresh(row)
        else:
            self._s.flush()
        return Envelope(data=IntegrationCredentialOut.model_validate(row), project_id=project_id)

    def get_decrypted(self, credential_id: int) -> bytes:
        from stackos.crypto.aes_gcm import decrypt as _crypto_decrypt

        row = self._s.get(IntegrationCredential, credential_id)
        if row is None:
            raise NotFoundError(f"credential {credential_id} not found")
        return _crypto_decrypt(
            row.encrypted_payload,
            nonce=row.nonce,
            project_id=row.project_id,
            kind=row.kind,
        )

    def mark_refreshed(self, credential_id: int) -> None:
        row = self._s.get(IntegrationCredential, credential_id)
        if row is None:
            raise NotFoundError(f"credential {credential_id} not found")
        row.last_refreshed_at = _utcnow()
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()

    def remove(
        self,
        credential_id: int,
        *,
        commit: bool = True,
    ) -> Envelope[IntegrationCredentialOut]:
        row = self._s.get(IntegrationCredential, credential_id)
        if row is None:
            raise NotFoundError(f"credential {credential_id} not found")
        out = IntegrationCredentialOut.model_validate(row)
        self._s.delete(row)
        if commit:
            self._s.commit()
        else:
            self._s.flush()
        return Envelope(data=out, project_id=out.project_id)

    def fetch_row(self, credential_id: int) -> IntegrationCredential:
        row = self._s.get(IntegrationCredential, credential_id)
        if row is None:
            raise NotFoundError(f"credential {credential_id} not found")
        return row


class IntegrationBudgetRepository:
    """Project-scoped cost and rate budget rows for tool connectors."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self, project_id: int) -> list[IntegrationBudgetOut]:
        rows = self._s.exec(
            select(IntegrationBudget)
            .where(col(IntegrationBudget.project_id) == project_id)
            .order_by(col(IntegrationBudget.kind).asc())
        ).all()
        return [IntegrationBudgetOut.model_validate(row) for row in rows]

    def get(self, project_id: int, kind: str) -> IntegrationBudgetOut:
        row = self._row(project_id, kind)
        return IntegrationBudgetOut.model_validate(row)

    def set(
        self,
        *,
        project_id: int,
        kind: str,
        monthly_budget_usd: float,
        alert_threshold_pct: int = 80,
        qps: float = 1.0,
    ) -> Envelope[IntegrationBudgetOut]:
        now = _utcnow()
        row = self._s.exec(
            select(IntegrationBudget).where(
                IntegrationBudget.project_id == project_id,
                IntegrationBudget.kind == kind,
            )
        ).first()
        if row is None:
            row = IntegrationBudget(
                project_id=project_id,
                kind=kind,
                monthly_budget_usd=monthly_budget_usd,
                alert_threshold_pct=alert_threshold_pct,
                current_month_spend=0.0,
                current_month_calls=0,
                qps=qps,
                last_reset=now,
                created_at=now,
                updated_at=now,
            )
        else:
            row.monthly_budget_usd = monthly_budget_usd
            row.alert_threshold_pct = alert_threshold_pct
            row.qps = qps
            row.updated_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=IntegrationBudgetOut.model_validate(row), project_id=project_id)

    def record_call(
        self,
        *,
        project_id: int,
        kind: str,
        cost_usd: float = 0.0,
        now: datetime | None = None,
    ) -> Envelope[IntegrationBudgetOut]:
        ts = now or _utcnow()
        row = self._row(project_id, kind)
        if (row.last_reset.year, row.last_reset.month) != (ts.year, ts.month):
            row.current_month_spend = 0.0
            row.current_month_calls = 0
            row.last_reset = ts
        if row.current_month_spend + cost_usd > row.monthly_budget_usd:
            raise BudgetExceededError(
                "integration budget exceeded",
                data={
                    "project_id": project_id,
                    "kind": kind,
                    "monthly_budget_usd": row.monthly_budget_usd,
                    "current_month_spend": row.current_month_spend,
                    "attempted_cost_usd": cost_usd,
                },
            )
        row.current_month_spend += cost_usd
        row.current_month_calls += 1
        row.updated_at = ts
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=IntegrationBudgetOut.model_validate(row), project_id=project_id)

    def _row(self, project_id: int, kind: str) -> IntegrationBudget:
        row = self._s.exec(
            select(IntegrationBudget).where(
                IntegrationBudget.project_id == project_id,
                IntegrationBudget.kind == kind,
            )
        ).first()
        if row is None:
            raise NotFoundError(
                f"budget not found for project={project_id} kind={kind!r}",
                data={"project_id": project_id, "kind": kind},
            )
        return row


class ScheduledJobRepository:
    """Operator-facing schedule metadata."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self, project_id: int) -> list[ScheduledJobOut]:
        rows = self._s.exec(
            select(ScheduledJob)
            .where(ScheduledJob.project_id == project_id)
            .order_by(ScheduledJob.id.asc())  # type: ignore[union-attr]
        ).all()
        return [ScheduledJobOut.model_validate(row) for row in rows]

    def set(
        self,
        *,
        project_id: int,
        kind: str,
        cron_expr: str,
        enabled: bool = True,
    ) -> Envelope[ScheduledJobOut]:
        row = self._s.exec(
            select(ScheduledJob).where(
                ScheduledJob.project_id == project_id,
                ScheduledJob.kind == kind,
            )
        ).first()
        if row is None:
            row = ScheduledJob(
                project_id=project_id,
                kind=kind,
                cron_expr=cron_expr,
                enabled=enabled,
            )
        else:
            row.cron_expr = cron_expr
            row.enabled = enabled
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ScheduledJobOut.model_validate(row), project_id=project_id)

    def toggle(
        self,
        job_id: int,
        *,
        enabled: bool,
        project_id: int | None = None,
    ) -> Envelope[ScheduledJobOut]:
        row = self._s.get(ScheduledJob, job_id)
        if row is None:
            raise NotFoundError(f"scheduled job {job_id} not found")
        if project_id is not None and row.project_id != project_id:
            raise NotFoundError(
                f"scheduled job {job_id} not found in project {project_id}",
                data={"project_id": project_id, "job_id": job_id},
            )
        row.enabled = enabled
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=ScheduledJobOut.model_validate(row), project_id=row.project_id)


__all__ = [
    "IntegrationBudgetOut",
    "IntegrationBudgetRepository",
    "IntegrationCredentialOut",
    "IntegrationCredentialRepository",
    "ProjectOut",
    "ProjectRepository",
    "ScheduledJobOut",
    "ScheduledJobRepository",
]
