"""Repositories for StackOS projects, credentials, budgets, and schedules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import and_, func, or_, update
from sqlmodel import Session, col, select

from stackos.db.models import (
    AgentRequest,
    Credential,
    IntegrationBudget,
    IntegrationCredential,
    Project,
    Run,
    RunPlan,
    ScheduledJob,
    TrackerItemStatus,
    WorkspaceBinding,
)
from stackos.operations.tracker.schemas import (
    AgentRole,
    TaskDeliveryAgentOverride,
    TaskMcpConfigRef,
)
from stackos.repositories.base import (
    BudgetExceededError,
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
    _normalise_limit,
    cursor_paginate_desc,
)
from stackos.repositories.tracker.schema import (
    ActiveExecutionOut,
    AgentStatsMetricsOut,
    WorkDurationOut,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_project_name = TypeAdapter(ProjectName)


def validate_project_name(value: Any) -> str:
    try:
        return _project_name.validate_python(value)
    except SchemaValidationError as exc:
        raise ValidationError(
            "project name must be a nonblank string of at most 200 characters"
        ) from exc


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    order_index: int
    domain: str
    niche: str | None
    locale: str
    is_active: bool
    cleanup_json: dict[str, Any] | None = None
    schedule_json: dict[str, Any] | None
    primary_workspace_binding_id: int | None = None
    project_dir: str | None = None
    project_folder_issue: str | None = None
    created_at: datetime
    updated_at: datetime


class PortfolioTaskOut(BaseModel):
    id: int
    key: str
    title: str
    status: str
    updated_at: datetime


class ProjectPortfolioItemOut(BaseModel):
    id: int
    slug: str
    name: str
    domain: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime | None = None
    latest_task: PortfolioTaskOut | None
    ticket_count: int
    ticket_counts: dict[str, int]


class IntegrationCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
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


class ScheduleExecution(BaseModel):
    """Existing handoff settings, without occurrence-specific lifecycle arguments."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_dir: str = Field(min_length=1)
    host_key: str | None = Field(default=None, min_length=1)
    delivery_agent: TaskDeliveryAgentOverride | None = None
    review_agent: TaskDeliveryAgentOverride | None = None
    agents: dict[AgentRole, TaskDeliveryAgentOverride] | None = None
    step_agent_assignments: dict[str, AgentRole] | None = None
    mcp_config_ref: TaskMcpConfigRef | None = None


class ScheduleConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(min_length=1, max_length=120)
    cron_expr: str = Field(min_length=1, max_length=120)
    enabled: bool = False
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    template_key: str | None = Field(default=None, min_length=1, max_length=160)
    inputs_json: dict[str, Any] | None = None
    execution_json: ScheduleExecution | None = None


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
    name: str | None
    timezone: str | None
    template_key: str | None
    inputs_json: dict[str, Any] | None
    execution_json: ScheduleExecution | None
    last_request_id: int | None
    setup_issue: str | None = None


class ScheduleRemovalOut(BaseModel):
    project_id: int
    job_id: int
    removed: bool = True


class ScheduledRunOut(BaseModel):
    project_id: int
    request_id: int
    schedule_id: int
    schedule_name: str
    template_key: str | None
    workflow_title: str | None
    run_plan_id: int
    run_id: int | None
    task_key: str | None
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    work_duration: WorkDurationOut | None = None
    agent_stats: AgentStatsMetricsOut | None
    active_execution: ActiveExecutionOut | None


class ProjectRepository:
    """CRUD for the project container."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def require_available(self, project_id: int, *, reserve: bool = False) -> Project:
        """Fence admissions against cleanup using the existing SQLite write transaction.

        Writers reserve immediately before persisting admission, without an await
        or commit between this check and that write. Reads use a fresh SELECT so
        an old Session identity map cannot authorize work after cleanup starts.
        """
        if reserve:
            self._s.exec(update(Project).where(col(Project.id) == project_id).values(id=Project.id))
        row = self._s.exec(
            select(Project)
            .where(Project.id == project_id)
            .execution_options(populate_existing=True)
        ).first()
        if row is None:
            raise NotFoundError(f"project {project_id} not found", data={"project_id": project_id})
        if row.cleanup_json is not None:
            raise ConflictError(
                "project cleanup is incomplete; retry the confirmed deletion",
                data={"project_id": project_id, "cleanup": row.cleanup_json},
            )
        return row

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
        name = validate_project_name(name)
        self._lock_order()
        now = _utcnow()
        if self._s.exec(select(Project.id).where(Project.slug == slug)).first() is not None:
            raise ConflictError(f"project slug {slug!r} already exists")
        row = Project(
            slug=slug,
            name=name,
            order_index=(self._s.exec(select(func.max(Project.order_index))).one() or 0) + 1,
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
        return Envelope(data=self._out(row), project_id=row.id)

    def get(self, id_or_slug: int | str) -> ProjectOut:
        row = self._fetch_row(id_or_slug)
        return self._out(row)

    def _out(self, row: Project) -> ProjectOut:
        out = ProjectOut.model_validate(row)
        setup = (
            "Ask your setup agent to select this project's folder once from its existing "
            "workspace bindings in project setup. "
            "Projects without a local folder can continue using other features."
        )
        binding = (
            self._s.get(WorkspaceBinding, row.primary_workspace_binding_id)
            if row.primary_workspace_binding_id
            else None
        )
        if binding is None or binding.project_id != row.id or not binding.last_known_root:
            return out.model_copy(update={"project_folder_issue": setup})
        try:
            root = Path(binding.last_known_root).resolve(strict=True)
            if not root.is_dir() or not root.is_absolute() or root == Path(root.anchor):
                raise ValueError("unavailable folder")
            # Match the exact project/path association enforced by projectAgent.*.
            owners = set()
            for candidate in self._s.exec(select(WorkspaceBinding)).all():
                try:
                    if (
                        candidate.last_known_root
                        and Path(candidate.last_known_root).resolve() == root
                    ):
                        owners.add(candidate.project_id)
                except (OSError, RuntimeError, ValueError):
                    continue
            if owners != {row.id}:
                raise ValueError("conflicting folder association")
        except (OSError, RuntimeError, ValueError):
            return out.model_copy(
                update={
                    "project_folder_issue": "The configured project folder is unavailable "
                    "or its binding needs repair. " + setup
                }
            )
        return out.model_copy(update={"project_dir": str(root)})

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
            return self._out(rows[0])
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
        size = _normalise_limit(limit)
        total = self._s.exec(select(func.count()).select_from(stmt.subquery())).one()
        if after_id is not None:
            anchor = self._s.exec(stmt.where(Project.id == after_id)).first()
            if anchor is None:
                raise ValidationError(
                    "project cursor no longer matches the list; refresh the first page"
                )
            stmt = stmt.where(
                or_(
                    col(Project.order_index) > anchor.order_index,
                    and_(
                        col(Project.order_index) == anchor.order_index, col(Project.id) > after_id
                    ),
                )
            )
        rows = self._s.exec(
            stmt.order_by(col(Project.order_index), col(Project.id)).limit(size + 1)
        ).all()
        items = [self._out(row) for row in rows[:size]]
        return Page(
            items=items,
            next_cursor=items[-1].id if len(rows) > size else None,
            total_estimate=total,
        )

    def portfolio(
        self,
        *,
        project_id: int | None = None,
        is_active: bool | None = True,
        query: str | None = None,
        ticket_status: TrackerItemStatus | None = None,
        sort: Literal["recent", "name"] = "recent",
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ProjectPortfolioItemOut]:
        from .project_portfolio import query_portfolio

        if project_id is not None:
            self._fetch_row(project_id)
        return query_portfolio(
            self._s,
            project_id=project_id,
            is_active=is_active,
            query=query,
            ticket_status=ticket_status,
            sort=sort,
            limit=limit,
            after_id=after_id,
        )

    def update(self, project_id: int, **patch: Any) -> Envelope[ProjectOut]:
        self.require_available(project_id, reserve=True)
        if "name" in patch:
            patch["name"] = validate_project_name(patch["name"])
        row = self._fetch_row(project_id)
        if "slug" in patch and patch["slug"] != row.slug:
            raise ValidationError("project slug is immutable")
        if "primary_workspace_binding_id" in patch:
            binding_id = patch["primary_workspace_binding_id"]
            if binding_id is not None:
                if type(binding_id) is not int or binding_id <= 0:
                    raise ValidationError(
                        "primary_workspace_binding_id must be a positive integer or null"
                    )
                binding = self._s.get(WorkspaceBinding, binding_id)
                if (
                    binding is None
                    or binding.project_id != project_id
                    or not binding.last_known_root
                ):
                    raise ValidationError(
                        "Select an existing directory workspace binding belonging to this project"
                    )
        allowed = {
            "name",
            "domain",
            "niche",
            "locale",
            "schedule_json",
            "is_active",
            "primary_workspace_binding_id",
        }
        if all(getattr(row, key) == value for key, value in patch.items() if key in allowed):
            return Envelope(data=self._out(row), project_id=row.id)
        for key, value in patch.items():
            if key in allowed:
                setattr(row, key, value)
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=row.id)

    def move(
        self,
        project_id: int,
        *,
        placement: Literal["top", "before", "after"],
        anchor_id: int | None = None,
    ) -> Envelope[ProjectOut]:
        """Move one visible project, retaining every unseen/inactive catalog row."""
        if placement not in {"top", "before", "after"} or (
            (placement == "top") != (anchor_id is None)
        ):
            raise ValidationError("top requires no anchor; before/after require an anchor_id")
        self._lock_order()
        rows = list(
            self._s.exec(select(Project).order_by(col(Project.order_index), col(Project.id))).all()
        )
        target = next((row for row in rows if row.id == project_id and row.is_active), None)
        if target is None:
            raise NotFoundError("visible project not found")
        anchor = next((row for row in rows if row.id == anchor_id and row.is_active), None)
        if anchor_id is not None and anchor is None:
            raise NotFoundError("visible anchor project not found; refresh the project list")
        original = [row.id for row in rows]
        if anchor_id != project_id:
            rows.remove(target)
            position = 0 if anchor is None else rows.index(anchor) + (placement == "after")
            rows.insert(position, target)
        if original != [row.id for row in rows]:
            for position, row in enumerate(rows, start=1):
                if row.order_index != position:
                    row.order_index = position
                    self._s.add(row)
            self._s.commit()
        return Envelope(data=self._out(target), project_id=project_id)

    def _lock_order(self) -> None:
        # Serialize read/modify/write with moves and appends, including callers
        # whose dispatcher already owns a write transaction for idempotency.
        connection = self._s.connection()
        driver = connection.connection.driver_connection
        assert driver is not None
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        self._s.expire_all()

    def _fetch_row(self, id_or_slug: int | str) -> Project:
        if isinstance(id_or_slug, int):
            row = self._s.get(Project, id_or_slug)
        else:
            row = self._s.exec(select(Project).where(Project.slug == id_or_slug)).first()
        if row is None:
            raise NotFoundError(f"project {id_or_slug!r} not found")
        return row


class IntegrationCredentialRepository:
    """Encrypted backing storage for global Accounts."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self) -> list[IntegrationCredentialOut]:
        stmt = select(IntegrationCredential)
        rows = self._s.exec(stmt.order_by(col(IntegrationCredential.id).asc())).all()
        return [IntegrationCredentialOut.model_validate(row) for row in rows]

    def set(
        self,
        *,
        credential_ref: str,
        provider_key: str,
        secret_payload: bytes,
        integration_credential_id: int | None = None,
        commit: bool = True,
    ) -> Envelope[IntegrationCredentialOut]:
        from stackos.crypto.aes_gcm import encrypt_account

        account = self._s.exec(
            select(Credential).where(col(Credential.credential_ref) == credential_ref)
        ).first()
        if integration_credential_id is not None:
            row = self._s.get(IntegrationCredential, integration_credential_id)
            if row is None:
                raise NotFoundError(f"credential backing {integration_credential_id} not found")
            if (
                account is None
                or account.integration_credential_id != integration_credential_id
                or account.provider_key != provider_key
            ):
                raise ValidationError(
                    "credential backing does not match the supplied Account identity"
                )
        else:
            if account is not None:
                raise ConflictError(
                    "Account already has encrypted credential backing",
                    data={"credential_ref": credential_ref},
                )
            row = None
        ciphertext, nonce = encrypt_account(
            secret_payload,
            credential_ref=credential_ref,
            provider_key=provider_key,
        )
        now = _utcnow()
        if row is None:
            row = IntegrationCredential(
                encrypted_payload=ciphertext,
                nonce=nonce,
                created_at=now,
                updated_at=now,
            )
        else:
            row.encrypted_payload = ciphertext
            row.nonce = nonce
            row.updated_at = now
        self._s.add(row)
        if commit:
            self._s.commit()
            self._s.refresh(row)
        else:
            self._s.flush()
        return Envelope(data=IntegrationCredentialOut.model_validate(row))

    def get_decrypted(self, credential_id: int) -> bytes:
        from stackos.crypto.aes_gcm import decrypt_account

        row = self._s.get(IntegrationCredential, credential_id)
        if row is None:
            raise NotFoundError(f"credential {credential_id} not found")
        account = self._s.exec(
            select(Credential).where(col(Credential.integration_credential_id) == credential_id)
        ).first()
        if account is None:
            raise NotFoundError(
                "Account identity for encrypted credential backing not found",
                data={"integration_credential_id": credential_id},
            )
        return decrypt_account(
            row.encrypted_payload,
            nonce=row.nonce,
            credential_ref=account.credential_ref,
            provider_key=account.provider_key,
        )

    def mark_refreshed(self, credential_id: int) -> None:
        row = self._s.get(IntegrationCredential, credential_id)
        if row is None:
            raise NotFoundError(f"credential {credential_id} not found")
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
        return Envelope(data=out)

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
    """Canonical project schedule configuration and linked execution projection."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self, project_id: int) -> list[ScheduledJobOut]:
        rows = self._s.exec(
            select(ScheduledJob)
            .where(ScheduledJob.project_id == project_id)
            .order_by(ScheduledJob.id.asc())  # type: ignore[union-attr]
        ).all()
        return [self._out(row) for row in rows]

    def set(
        self,
        *,
        project_id: int,
        kind: str,
        cron_expr: str,
        enabled: bool = False,
        name: str | None = None,
        timezone: str | None = None,
        template_key: str | None = None,
        inputs_json: dict[str, Any] | None = None,
        execution_json: dict[str, Any] | None = None,
    ) -> Envelope[ScheduledJobOut]:
        ProjectRepository(self._s).require_available(project_id, reserve=True)
        try:
            config = ScheduleConfiguration.model_validate(
                {
                    "kind": kind,
                    "cron_expr": cron_expr,
                    "enabled": enabled,
                    "name": name,
                    "timezone": timezone,
                    "template_key": template_key,
                    "inputs_json": inputs_json,
                    "execution_json": execution_json,
                }
            ).model_dump(mode="json")
            if execution_json is not None:
                # Preserve omitted versus explicit-null role overrides through storage.
                config["execution_json"] = ScheduleExecution.model_validate(
                    execution_json
                ).model_dump(mode="json", exclude_unset=True)
        except SchemaValidationError as exc:
            raise ValidationError(
                "Invalid schedule configuration",
                data={
                    "errors": exc.errors(include_input=False, include_url=False),
                },
            ) from None
        self.validate_configuration(project_id, config)
        row = self._s.exec(
            select(ScheduledJob).where(
                ScheduledJob.project_id == project_id,
                ScheduledJob.kind == config["kind"],
            )
        ).first()
        if row is None:
            row = ScheduledJob(
                project_id=project_id,
                **config,
            )
        else:
            for key, value in config.items():
                setattr(row, key, value)
        row.next_run_at = self._next_run(config) if enabled else None
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        self._reconcile(row.project_id, row.id)
        return Envelope(data=self._out(row), project_id=project_id)

    def toggle(
        self,
        job_id: int,
        *,
        enabled: bool,
        project_id: int,
    ) -> Envelope[ScheduledJobOut]:
        ProjectRepository(self._s).require_available(project_id, reserve=True)
        row = self._get(project_id, job_id)
        if enabled:
            self.validate_configuration(
                project_id,
                {
                    **{key: getattr(row, key) for key in ScheduleConfiguration.model_fields},
                    "enabled": True,
                },
            )
        row.enabled = enabled
        row.next_run_at = (
            self._next_run(
                {
                    "cron_expr": row.cron_expr,
                    "timezone": row.timezone,
                }
            )
            if enabled
            else None
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        self._reconcile(project_id, job_id)
        return Envelope(data=self._out(row), project_id=project_id)

    def _get(self, project_id: int, job_id: int) -> ScheduledJob:
        row = self._s.get(ScheduledJob, job_id)
        if row is None or row.project_id != project_id:
            raise NotFoundError(
                f"scheduled job {job_id} not found in project {project_id}",
                data={"project_id": project_id, "job_id": job_id},
            )
        return row

    def remove(self, job_id: int, *, project_id: int) -> Envelope[ScheduleRemovalOut]:
        row = self._get(project_id, job_id)
        self._s.delete(row)
        self._s.commit()
        self._reconcile(project_id, job_id)
        return Envelope(
            data=ScheduleRemovalOut(project_id=project_id, job_id=job_id), project_id=project_id
        )

    def _reconcile(self, project_id: int, job_id: int | None) -> None:
        # Daemon-owned seam; every adapter also reaches it via this repository.
        reconcile = self._s.info.get("operation_services", {}).get("schedule_reconcile")
        if reconcile is not None:
            reconcile(project_id=project_id, job_id=job_id)

    @staticmethod
    def _next_run(config: dict[str, Any]) -> datetime | None:
        trigger = CronTrigger.from_crontab(
            config["cron_expr"], timezone=ZoneInfo(config["timezone"])
        )
        next_fire = trigger.get_next_fire_time(None, datetime.now(UTC))
        return next_fire.astimezone(UTC).replace(tzinfo=None) if next_fire else None

    def validate_configuration(
        self, project_id: int, config: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Read-only setup/admission preflight; return validated execution settings."""
        from stackos.workflows.run_plan_schema import ensure_run_plan_has_no_secrets

        try:
            ensure_run_plan_has_no_secrets(config)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        timezone = config.get("timezone")
        try:
            zone = ZoneInfo(timezone) if timezone else ZoneInfo("UTC")
            CronTrigger.from_crontab(config["cron_expr"], timezone=zone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValidationError(f"Invalid schedule cron/timezone: {exc}") from None
        from stackos.repositories.tracker import TrackerRepository
        from stackos.task_delivery import resolve_delivery_settings, workflow_agent_assignments
        from stackos.workflows.template_loader import WorkflowTemplateLoader

        execution = (
            ScheduleExecution.model_validate(config["execution_json"]).model_dump(
                exclude_unset=True
            )
            if config.get("execution_json")
            else {}
        )
        explicit = execution.pop("step_agent_assignments", None)
        sources = {
            **(execution.get("agents") or {}),
            **{
                role: execution[role]
                for role in ("delivery_agent", "review_agent")
                if execution.get(role)
            },
        }
        missing = [
            key
            for key in ("name", "timezone", "template_key", "execution_json")
            if not config.get(key)
        ]
        incomplete_draft = not config.get("enabled") and (
            missing or (not execution.get("agents") and explicit is None)
        )
        # Legacy role arguments replace matching mapped choices at execution, but
        # both forms remain stored. Validate the shadowed choices before merging.
        shadowed = {
            role: choice
            for role, choice in (execution.get("agents") or {}).items()
            if role in {"delivery_agent", "review_agent"}
            and execution.get(role) is not None
            and (not incomplete_draft or choice.get("source_ref") or choice.get("instructions"))
        }
        if shadowed:
            resolve_delivery_settings(
                self._s,
                project_id=project_id,
                project_dir=execution["project_dir"],
                host_key=execution.get("host_key"),
                agents=shadowed,
                role_keys=set(shadowed),
            )
        if incomplete_draft:
            # Incomplete drafts stay inert, but supplied source identities must be current.
            source_roles = {
                role
                for role, choice in sources.items()
                if choice.get("source_ref") or choice.get("instructions")
            }
            if source_roles:
                resolve_delivery_settings(
                    self._s, project_id=project_id, role_keys=source_roles, **execution
                )
            return None
        if missing:
            raise ValidationError(
                "Schedule needs setup: " + ", ".join(missing), data={"missing_fields": missing}
            )
        from stackos.repositories.run_plans import RunPlanRepository

        validation = RunPlanRepository(self._s).validate_plan(
            template_key=config["template_key"],
            project_id=project_id,
            repo_root=execution["project_dir"],
            inputs_json=config.get("inputs_json"),
            enforce_required_inputs=bool(config.get("enabled")),
        )
        if not validation.valid:
            raise ValidationError(
                "Workflow setup is invalid",
                data={
                    "errors": [issue.model_dump() for issue in validation.errors],
                },
            )
        assert validation.plan is not None
        template = WorkflowTemplateLoader(self._s).describe_template(
            key=config["template_key"], project_id=project_id, repo_root=execution["project_dir"]
        )
        requirements = [req.model_dump() for req in template.spec.agent_requirements]
        declared = {req["role"] for req in requirements}
        if declared and set(execution.get("agents") or {}) - declared - {
            "delivery_agent",
            "review_agent",
        }:
            raise ValidationError("Selected agents must bind declared workflow roles")
        defaults = TrackerRepository(self._s).delivery_defaults(project_id=project_id)
        available = set(defaults.get("agents") or {}) | set(execution.get("agents") or {})
        available.update(
            role
            for role in ("delivery_agent", "review_agent")
            if defaults.get(role) or execution.get(role)
        )
        assignments = workflow_agent_assignments(
            requirements=requirements,
            steps={
                step.id: (step.metadata_json or {}).get("agent_assignment")
                for step in validation.plan.steps
            },
            selected_roles=available,
            assignments=explicit,
        )
        if declared and set(assignments.values()) - declared - {"delivery_agent", "review_agent"}:
            raise ValidationError("Selected agents must bind declared workflow roles")
        # Validate every supplied choice before persistence, even an unassigned role.
        # Unrelated project defaults remain outside this selection.
        resolved = resolve_delivery_settings(
            self._s,
            project_id=project_id,
            role_keys=set(assignments.values()) | sources.keys(),
            **execution,
        )
        return (
            {
                **execution,
                "step_agent_assignments": assignments,
                "_resolved_settings": {"project_id": project_id, **resolved},
            }
            if config.get("enabled")
            else None
        )

    def _out(self, row: ScheduledJob) -> ScheduledJobOut:
        out = ScheduledJobOut.model_validate(row)
        missing = [
            key
            for key in ("name", "timezone", "template_key", "execution_json")
            if not getattr(row, key)
        ]
        out.setup_issue = "Schedule needs setup: " + ", ".join(missing) if missing else None
        if not missing:
            try:
                self.validate_configuration(
                    row.project_id,
                    {
                        **{key: getattr(row, key) for key in ScheduleConfiguration.model_fields},
                        "enabled": True,
                    },
                )
            except ValidationError as exc:
                errors = exc.data.get("errors", [])
                messages = [issue.get("message", "") for issue in errors if isinstance(issue, dict)]
                source_ref = exc.data.get("source_ref")
                if isinstance(source_ref, dict):
                    messages.append(
                        f"{source_ref.get('native_name')} ({source_ref.get('source_path')})"
                    )
                    messages.append(exc.data.get("repair", "Repair the selected project source."))
                out.setup_issue = ": ".join([exc.detail, *filter(None, messages)])
        return out

    def runs(
        self,
        *,
        project_id: int,
        schedule_id: int | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[ScheduledRunOut]:
        ProjectRepository(self._s).get(project_id)
        statement = (
            select(AgentRequest)
            .join(RunPlan, col(AgentRequest.run_plan_id) == col(RunPlan.id))
            .where(
                AgentRequest.project_id == project_id,
                RunPlan.project_id == project_id,
                AgentRequest.source_kind == "schedule",
                AgentRequest.run_plan_id.is_not(None),  # type: ignore[union-attr]
                AgentRequest.metadata_json["schedule_id"].as_integer().is_not(None),  # type: ignore[index]
            )
        )
        if schedule_id is not None:
            statement = statement.where(
                AgentRequest.metadata_json["schedule_id"].as_integer() == schedule_id,  # type: ignore[index]
            )
        return cursor_paginate_desc(
            self._s,
            statement,
            id_col=col(AgentRequest.id),
            limit=limit,
            after_id=after_id,
            converter=self._run_out,
        )

    def _run_out(self, request: AgentRequest) -> ScheduledRunOut:
        from stackos.repositories.tracker import TrackerRepository

        plan = self._s.get(RunPlan, request.run_plan_id)
        if (
            plan is None
            or plan.id is None
            or request.id is None
            or plan.project_id != request.project_id
        ):
            raise NotFoundError("Scheduled request has no project-scoped run plan")
        run = self._s.get(Run, plan.run_id) if plan.run_id else None
        tasks = (
            TrackerRepository(self._s)
            .get(
                project_id=request.project_id,
                run_plan_id=plan.id,
                include_graph=False,
            )
            .tasks
        )
        task = next(
            (task for task in tasks if (task.source_json or {}).get("run_plan_id") == plan.id), None
        )
        metadata = request.metadata_json or {}
        started = (task.started_at if task else None) or plan.started_at
        ended = (task.completed_at if task else None) or plan.completed_at
        return ScheduledRunOut(
            project_id=request.project_id,
            request_id=request.id,
            schedule_id=metadata["schedule_id"],
            schedule_name=metadata.get("schedule_name", request.title),
            template_key=plan.template_key,
            workflow_title=plan.title,
            run_plan_id=plan.id,
            run_id=run.id if run and run.project_id == request.project_id else None,
            task_key=task.key if task else None,
            status=task.status.value if task else plan.status.value,
            started_at=started,
            ended_at=ended,
            duration_seconds=task.work_duration.total_seconds
            if task and task.work_duration
            else None,
            work_duration=task.work_duration if task else None,
            agent_stats=task.agent_stats if task else None,
            active_execution=task.active_execution if task else None,
        )


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
