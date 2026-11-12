"""Repository for the generic StackOS agent request inbox."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.db.models import (
    AGENT_REQUEST_STATUS_TRANSITIONS,
    AgentRequest,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    ResourceRecord,
    RunPlan,
)
from stackos.host_mcp.agent_execution import managed_launch_lock
from stackos.host_mcp.result import AgentRunResult
from stackos.host_mcp.service import (
    ActiveInvocation,
    active_request_execution,
    owns_request_invocation,
)
from stackos.repositories.base import (
    ConflictError,
    Envelope,
    NotFoundError,
    Page,
    ValidationError,
    cursor_paginate,
    validate_transition,
)
from stackos.repositories.run_plans import RunPlanOut, RunPlanRepository


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run_plan_json_with_metadata(
    run_plan_json: dict[str, Any],
    metadata_json: dict[str, Any],
) -> dict[str, Any]:
    plan = dict(run_plan_json)
    existing = plan.get("metadata_json", plan.get("metadata"))
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(metadata_json)
    plan["metadata_json"] = merged
    plan.pop("metadata", None)
    return plan


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    return redact_secret_text(str(value))


class AgentRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    request_key: str
    title: str
    body_preview: str
    source_provider: str | None
    source_kind: str | None
    source_resource_key: str | None
    source_resource_record_id: int | None
    source_message_ref: str | None
    priority: int
    status: AgentRequestStatus
    attention_status: AgentRequestAttentionStatus
    claimed_by: str | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    run_plan_id: int | None
    completed_at: datetime | None
    ignored_at: datetime | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class AgentRequestClaimOut(AgentRequestOut):
    claim_token: str = Field(min_length=1)


class AgentRequestPrepareRunPlanOut(BaseModel):
    request: AgentRequestOut
    run_plan: RunPlanOut
    claim_token: str = Field(min_length=1)


@dataclass(frozen=True)
class AgentRequestExecution:
    """Daemon-held claim and session factory for one request's native handoff.

    No runner or process ownership lives here. The host registry calls these
    repository writes before launch, on its existing collector, and at exit.
    """

    project_id: int
    request_id: int
    claim_token: str = field(repr=False)
    session_factory: Callable[[], Session] = field(repr=False)
    snapshot: dict[str, Any]

    def admit(self, call: ActiveInvocation, launch_snapshot: dict[str, Any]) -> None:
        with managed_launch_lock, self.session_factory() as session:
            AgentRequestRepository(session)._admit_execution(self, call, launch_snapshot)

    def renew(self, call: ActiveInvocation) -> None:
        with managed_launch_lock, self.session_factory() as session:
            AgentRequestRepository(session)._renew_execution(self, call)

    def finish(self, call: ActiveInvocation, result: AgentRunResult | None) -> None:
        with managed_launch_lock, self.session_factory() as session:
            AgentRequestRepository(session)._finish_execution(self, call, result)


class AgentRequestRepository:
    """Generic claimable inbox for agents.

    Provider plugins may feed this queue, but the repository intentionally has
    no Telegram, SMTP, IMAP, or webhook-specific behavior.
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        *,
        project_id: int,
        request_key: str,
        title: str,
        body_preview: str = "",
        source_provider: str | None = None,
        source_kind: str | None = None,
        source_resource_key: str | None = None,
        source_resource_record_id: int | None = None,
        source_message_ref: str | None = None,
        priority: int = 0,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[AgentRequestOut]:
        self._require_project(project_id)
        if metadata_json is not None and "native_execution" in metadata_json:
            raise ValidationError("native_execution is owned by the native request handoff")
        self._validate_source_record(project_id, source_resource_record_id)
        if not request_key.strip():
            raise ValidationError("request_key is required")
        existing = self._s.exec(
            select(AgentRequest).where(
                AgentRequest.project_id == project_id,
                AgentRequest.request_key == request_key,
            )
        ).first()
        if existing is not None:
            return Envelope(data=self._out(existing), project_id=project_id)

        now = _utcnow()
        row = AgentRequest(
            project_id=project_id,
            request_key=request_key,
            title=_clean_text(title),
            body_preview=_clean_text(body_preview),
            source_provider=source_provider,
            source_kind=source_kind,
            source_resource_key=source_resource_key,
            source_resource_record_id=source_resource_record_id,
            source_message_ref=_clean_text(source_message_ref) if source_message_ref else None,
            priority=priority,
            status=AgentRequestStatus.NEW,
            attention_status=AgentRequestAttentionStatus.UNREAD,
            metadata_json=redact_secrets(metadata_json) if metadata_json is not None else None,
            created_at=now,
            updated_at=now,
        )
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=project_id)

    def find_by_key(self, *, project_id: int, request_key: str) -> AgentRequestOut | None:
        """Return one request by stable provider key without changing lifecycle state."""
        self._require_project(project_id)
        if not request_key.strip():
            raise ValidationError("request_key is required")
        row = self._s.exec(
            select(AgentRequest).where(
                AgentRequest.project_id == project_id,
                AgentRequest.request_key == request_key,
            )
        ).first()
        return self._out(row) if row is not None else None

    def get(self, *, project_id: int, request_id: int) -> AgentRequestOut:
        return self._out(self._fetch(project_id=project_id, request_id=request_id))

    def execution_evidence(self, *, project_id: int, request_id: int) -> dict[str, Any]:
        """Request history projection: committed final evidence wins over a bounded copy."""
        observed = active_request_execution(project_id, request_id, include_handoff=True)
        row = self._fetch(project_id=project_id, request_id=request_id)
        evidence = deepcopy((row.metadata_json or {}).get("native_execution"))
        active = None
        if (
            observed is not None
            and evidence is not None
            and observed["invocation_id"] == evidence["execution"]["invocation_id"]
        ):
            finished = observed.pop("finished")
            usage = observed.pop("usage")
            if not finished and not evidence.get("finished_at"):
                active = observed
            if not evidence.get("finished_at"):
                evidence.update(execution=observed, usage=usage)
        return {"native_execution": redact_secrets(evidence), "active_execution": active}

    def list(
        self,
        *,
        project_id: int,
        statuses: list[AgentRequestStatus] | None = None,
        attention_status: AgentRequestAttentionStatus | None = None,
        claimed_by: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[AgentRequestOut]:
        self._require_project(project_id)
        stmt = select(AgentRequest).where(AgentRequest.project_id == project_id)
        if statuses:
            stmt = stmt.where(col(AgentRequest.status).in_(statuses))
        if attention_status is not None:
            stmt = stmt.where(AgentRequest.attention_status == attention_status)
        if claimed_by is not None:
            stmt = stmt.where(AgentRequest.claimed_by == claimed_by)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=AgentRequest.id,
            limit=limit,
            after_id=after_id,
            converter=self._out,
        )

    def list_claimable(
        self,
        *,
        project_id: int,
        attention_status: AgentRequestAttentionStatus | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> Page[AgentRequestOut]:
        """List requests an agent can claim now, including expired leases."""
        self._require_project(project_id)
        now = _utcnow()
        stmt = select(AgentRequest).where(
            col(AgentRequest.project_id) == project_id,
            or_(
                col(AgentRequest.status) == AgentRequestStatus.NEW,
                (
                    (col(AgentRequest.status) == AgentRequestStatus.CLAIMED)
                    & col(AgentRequest.claim_expires_at).is_not(None)
                    & (col(AgentRequest.claim_expires_at) <= now)
                    & func.json_type(col(AgentRequest.metadata_json), "$.native_execution").is_(
                        None
                    )
                ),
            ),
        )
        if attention_status is not None:
            stmt = stmt.where(col(AgentRequest.attention_status) == attention_status)
        return cursor_paginate(
            self._s,
            stmt,
            id_col=AgentRequest.id,
            limit=limit,
            after_id=after_id,
            converter=self._out,
        )

    def claim(
        self,
        *,
        project_id: int,
        request_id: int,
        claimed_by: str,
        lease_seconds: int = 600,
    ) -> Envelope[AgentRequestClaimOut]:
        if not claimed_by.strip():
            raise ValidationError("claimed_by is required")
        if lease_seconds < 1 or lease_seconds > 86_400:
            raise ValidationError(
                "lease_seconds must be between 1 and 86400",
                data={"lease_seconds": lease_seconds},
            )
        row = self._fetch(project_id=project_id, request_id=request_id)
        now = _utcnow()
        if (row.metadata_json or {}).get("native_execution") is not None:
            raise ConflictError(
                "native request execution requires explicit resolution, not reclaim"
            )
        if row.status == AgentRequestStatus.CLAIMED:
            if row.claim_expires_at is None or row.claim_expires_at > now:
                raise ConflictError(
                    "agent request is already claimed",
                    data={
                        "request_id": request_id,
                        "claimed_by": row.claimed_by,
                        "claim_expires_at": row.claim_expires_at.isoformat()
                        if row.claim_expires_at
                        else None,
                    },
                )
        elif row.status != AgentRequestStatus.NEW:
            raise ConflictError(
                "agent request is not claimable",
                data={"request_id": request_id, "status": row.status.value},
            )

        if row.status != AgentRequestStatus.CLAIMED:
            validate_transition(
                row.status,
                AgentRequestStatus.CLAIMED,
                AGENT_REQUEST_STATUS_TRANSITIONS,
                label="agent_request.status",
            )
        claim_token = _token()
        row.status = AgentRequestStatus.CLAIMED
        row.attention_status = AgentRequestAttentionStatus.READ
        row.claimed_by = claimed_by
        row.claim_token_hash = _hash_token(claim_token)
        row.claimed_at = now
        row.claim_expires_at = now + timedelta(seconds=lease_seconds)
        row.updated_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(
            data=AgentRequestClaimOut(**self._out(row).model_dump(), claim_token=claim_token),
            project_id=project_id,
        )

    def release(
        self,
        *,
        project_id: int,
        request_id: int,
        claim_token: str,
    ) -> Envelope[AgentRequestOut]:
        row = self._fetch(project_id=project_id, request_id=request_id)
        if (row.metadata_json or {}).get("native_execution") is not None:
            raise ConflictError("an admitted native request cannot be released for relaunch")
        self._verify_claim(row, claim_token)
        validate_transition(
            row.status,
            AgentRequestStatus.NEW,
            AGENT_REQUEST_STATUS_TRANSITIONS,
            label="agent_request.status",
        )
        row.status = AgentRequestStatus.NEW
        row.claimed_by = None
        row.claim_token_hash = None
        row.claimed_at = None
        row.claim_expires_at = None
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=project_id)

    def link_run_plan(
        self,
        *,
        project_id: int,
        request_id: int,
        run_plan_id: int,
        claim_token: str,
    ) -> Envelope[AgentRequestOut]:
        row = self._fetch(project_id=project_id, request_id=request_id)
        # Native results can link after a long invocation's final handoff. The
        # original owner remains fenced against reclaim even beyond its lease.
        evidence = (row.metadata_json or {}).get("native_execution")
        self._verify_claim(
            row, claim_token, allow_expired=bool(evidence and evidence.get("finished_at"))
        )
        plan = self._s.get(RunPlan, run_plan_id)
        if plan is None:
            raise NotFoundError(f"run plan {run_plan_id} not found")
        if plan.project_id != project_id:
            raise ConflictError(
                "run plan project does not match agent request project",
                data={
                    "request_project_id": project_id,
                    "run_plan_project_id": plan.project_id,
                },
            )
        validate_transition(
            row.status,
            AgentRequestStatus.RUN_CREATED,
            AGENT_REQUEST_STATUS_TRANSITIONS,
            label="agent_request.status",
        )
        row.run_plan_id = run_plan_id
        row.status = AgentRequestStatus.RUN_CREATED
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=project_id)

    def prepare_run_plan(
        self,
        *,
        project_id: int,
        request_id: int,
        claimed_by: str,
        lease_seconds: int = 86_400,
        run_plan_json: dict[str, Any] | None = None,
        template_key: str | None = None,
        repo_root: str | None = None,
        plugin_slug: str | None = None,
        source: str | None = None,
        key: str | None = None,
        title: str | None = None,
        inputs_json: dict[str, Any] | None = None,
        context_snapshot_id: int | None = None,
        selected_context_json: dict[str, Any] | None = None,
        created_by: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[AgentRequestPrepareRunPlanOut]:
        """Atomically claim a request, create its run plan, and link both.

        The caller supplies the template or plan. This method never infers
        intent from provider payloads, never starts execution, and never calls
        tools or models.
        """
        if not claimed_by.strip():
            raise ValidationError("claimed_by is required")
        if lease_seconds < 1 or lease_seconds > 86_400:
            raise ValidationError(
                "lease_seconds must be between 1 and 86400",
                data={"lease_seconds": lease_seconds},
            )
        if run_plan_json is None and template_key is None:
            raise ValidationError("run_plan_json or template_key is required")
        row = self._fetch(project_id=project_id, request_id=request_id)
        if (row.metadata_json or {}).get("native_execution") is not None:
            raise ConflictError(
                "native request execution requires explicit resolution, not reclaim"
            )
        now = _utcnow()
        if row.status == AgentRequestStatus.CLAIMED:
            if row.claim_expires_at is None or row.claim_expires_at > now:
                raise ConflictError(
                    "agent request is already claimed",
                    data={
                        "request_id": request_id,
                        "claimed_by": row.claimed_by,
                        "claim_expires_at": row.claim_expires_at.isoformat()
                        if row.claim_expires_at
                        else None,
                    },
                )
        elif row.status != AgentRequestStatus.NEW:
            raise ConflictError(
                "agent request is not claimable",
                data={"request_id": request_id, "status": row.status.value},
            )

        claim_token = _token()
        try:
            if row.status != AgentRequestStatus.CLAIMED:
                validate_transition(
                    row.status,
                    AgentRequestStatus.CLAIMED,
                    AGENT_REQUEST_STATUS_TRANSITIONS,
                    label="agent_request.status",
                )
            row.status = AgentRequestStatus.CLAIMED
            row.attention_status = AgentRequestAttentionStatus.READ
            row.claimed_by = claimed_by
            row.claim_token_hash = _hash_token(claim_token)
            row.claimed_at = now
            row.claim_expires_at = now + timedelta(seconds=lease_seconds)
            row.updated_at = now
            self._s.add(row)

            linked_metadata = {
                **(metadata_json or {}),
                "agent_request_id": request_id,
                "agent_request_key": row.request_key,
            }
            plan_env = RunPlanRepository(self._s).create(
                project_id=project_id,
                run_plan_json=(
                    _run_plan_json_with_metadata(run_plan_json, linked_metadata)
                    if run_plan_json is not None
                    else None
                ),
                template_key=template_key,
                repo_root=repo_root,
                plugin_slug=plugin_slug,
                source=source,
                key=key,
                title=title,
                inputs_json=inputs_json,
                context_snapshot_id=context_snapshot_id,
                selected_context_json=selected_context_json,
                created_by=created_by or claimed_by,
                metadata_json=linked_metadata,
                _commit=False,
            )
            validate_transition(
                row.status,
                AgentRequestStatus.RUN_CREATED,
                AGENT_REQUEST_STATUS_TRANSITIONS,
                label="agent_request.status",
            )
            row.status = AgentRequestStatus.RUN_CREATED
            row.run_plan_id = plan_env.data.id
            row.updated_at = _utcnow()
            self._s.add(row)
            self._s.commit()
            self._s.refresh(row)
        except PydanticValidationError as exc:
            self._s.rollback()
            raise ValidationError(
                "run plan is invalid",
                data={"errors": exc.errors()},
            ) from exc
        except Exception:
            self._s.rollback()
            raise

        return Envelope(
            data=AgentRequestPrepareRunPlanOut(
                request=self._out(row),
                run_plan=RunPlanRepository(self._s).get(plan_env.data.id),
                claim_token=claim_token,
            ),
            project_id=project_id,
        )

    def complete(
        self,
        *,
        project_id: int,
        request_id: int,
        claim_token: str,
        status: AgentRequestStatus = AgentRequestStatus.RESOLVED,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[AgentRequestOut]:
        if status not in {AgentRequestStatus.RESOLVED, AgentRequestStatus.FAILED}:
            raise ValidationError(
                "completion status must be resolved or failed",
                data={"status": status.value},
            )
        row = self._fetch(project_id=project_id, request_id=request_id)
        evidence = (row.metadata_json or {}).get("native_execution")
        self._verify_claim(row, claim_token, allow_expired=evidence is not None)
        if metadata_json is not None and "native_execution" in metadata_json:
            raise ValidationError("native_execution is owned by the native request handoff")
        if evidence is not None:
            if not evidence.get("finished_at"):
                raise ConflictError("native request is still running or its exit is unobserved")
            if status == AgentRequestStatus.RESOLVED and (
                evidence.get("cancelled")
                or evidence.get("exit_code") != 0
                or evidence.get("error")
                or evidence.get("result") is None
                or not evidence["execution"].get("thread_id")
            ):
                raise ConflictError("native request has no successful terminal evidence")
        validate_transition(
            row.status,
            status,
            AGENT_REQUEST_STATUS_TRANSITIONS,
            label="agent_request.status",
        )
        now = _utcnow()
        row.status = status
        row.completed_at = now
        row.claim_token_hash = None
        row.claim_expires_at = None
        row.updated_at = now
        if metadata_json is not None:
            current = dict(row.metadata_json or {})
            current.update(redact_secrets(metadata_json))
            row.metadata_json = current
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=project_id)

    def ignore(
        self,
        *,
        project_id: int,
        request_id: int,
        ignored_by: str,
        claim_token: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> Envelope[AgentRequestOut]:
        _ = ignored_by
        row = self._fetch(project_id=project_id, request_id=request_id)
        if (row.metadata_json or {}).get("native_execution") is not None:
            raise ConflictError("an admitted native request requires terminal resolution")
        if metadata_json is not None and "native_execution" in metadata_json:
            raise ValidationError("native_execution is owned by the native request handoff")
        if row.status == AgentRequestStatus.CLAIMED:
            if claim_token is None:
                raise ConflictError("claim_token is required for claimed agent requests")
            self._verify_claim(row, claim_token)
        elif row.status != AgentRequestStatus.NEW:
            raise ConflictError(
                "agent request cannot be ignored from current status",
                data={"request_id": request_id, "status": row.status.value},
            )
        validate_transition(
            row.status,
            AgentRequestStatus.IGNORED,
            AGENT_REQUEST_STATUS_TRANSITIONS,
            label="agent_request.status",
        )
        now = _utcnow()
        row.status = AgentRequestStatus.IGNORED
        row.attention_status = AgentRequestAttentionStatus.ARCHIVED
        row.ignored_at = now
        row.claim_token_hash = None
        row.claim_expires_at = None
        row.updated_at = now
        if metadata_json is not None:
            current = dict(row.metadata_json or {})
            current.update(redact_secrets(metadata_json))
            row.metadata_json = current
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._out(row), project_id=project_id)

    def _fetch(self, *, project_id: int, request_id: int) -> AgentRequest:
        row = self._s.get(AgentRequest, request_id, populate_existing=True)
        if row is None or row.project_id != project_id:
            raise NotFoundError(f"agent request {request_id} not found")
        return row

    def _out(self, row: AgentRequest) -> AgentRequestOut:
        assert row.id is not None
        return AgentRequestOut(
            id=row.id,
            project_id=row.project_id,
            request_key=row.request_key,
            title=redact_secret_text(row.title),
            body_preview=redact_secret_text(row.body_preview),
            source_provider=row.source_provider,
            source_kind=row.source_kind,
            source_resource_key=row.source_resource_key,
            source_resource_record_id=row.source_resource_record_id,
            source_message_ref=redact_secret_text(row.source_message_ref)
            if row.source_message_ref is not None
            else None,
            priority=row.priority,
            status=row.status,
            attention_status=row.attention_status,
            claimed_by=row.claimed_by,
            claimed_at=row.claimed_at,
            claim_expires_at=row.claim_expires_at,
            run_plan_id=row.run_plan_id,
            completed_at=row.completed_at,
            ignored_at=row.ignored_at,
            metadata_json=redact_secrets(row.metadata_json)
            if row.metadata_json is not None
            else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _require_project(self, project_id: int) -> None:
        from stackos.repositories.projects import ProjectRepository

        ProjectRepository(self._s).require_available(project_id)

    def _validate_source_record(
        self,
        project_id: int,
        source_resource_record_id: int | None,
    ) -> None:
        if source_resource_record_id is None:
            return
        row = self._s.get(ResourceRecord, source_resource_record_id)
        if row is None:
            raise NotFoundError(f"resource record {source_resource_record_id} not found")
        if row.project_id != project_id:
            raise ConflictError(
                "source resource record project does not match agent request project",
                data={
                    "project_id": project_id,
                    "source_resource_record_project_id": row.project_id,
                },
            )

    def _verify_claim(
        self, row: AgentRequest, claim_token: str, *, allow_expired: bool = False
    ) -> None:
        if row.status not in {
            AgentRequestStatus.CLAIMED,
            AgentRequestStatus.RUN_CREATED,
            AgentRequestStatus.RUN_STARTED,
            AgentRequestStatus.RESPONDED,
        }:
            raise ConflictError(
                "agent request is not actively claimed",
                data={"request_id": row.id, "status": row.status.value},
            )
        if (
            not allow_expired
            and row.claim_expires_at is not None
            and row.claim_expires_at <= _utcnow()
        ):
            raise ConflictError(
                "agent request claim has expired",
                data={"request_id": row.id, "claim_expires_at": row.claim_expires_at.isoformat()},
            )
        if row.claim_token_hash is None or not hmac.compare_digest(
            row.claim_token_hash,
            _hash_token(claim_token),
        ):
            raise ConflictError("claim_token does not match agent request claim")

    def _execution_row(
        self,
        owner: AgentRequestExecution,
        call: ActiveInvocation,
        *,
        finished: bool = False,
    ) -> AgentRequest:
        if (
            call.scope.project_id != owner.project_id
            or call.scope.request_id != owner.request_id
            or not owns_request_invocation(call, finished=finished)
        ):
            raise ConflictError("native invocation does not own this request")
        row = self._fetch(project_id=owner.project_id, request_id=owner.request_id)
        self._verify_claim(row, owner.claim_token, allow_expired=True)
        return row

    def _admit_execution(
        self,
        owner: AgentRequestExecution,
        call: ActiveInvocation,
        launch_snapshot: dict[str, Any],
    ) -> None:
        from stackos.repositories.projects import ProjectRepository

        ProjectRepository(self._s).require_available(owner.project_id, reserve=True)
        row = self._execution_row(owner, call)
        self._verify_claim(row, owner.claim_token)
        if row.status != AgentRequestStatus.CLAIMED or (row.metadata_json or {}).get(
            "native_execution"
        ):
            raise ConflictError("request already has native execution evidence")
        row.metadata_json = {
            **(row.metadata_json or {}),
            "native_execution": redact_secrets(
                {
                    "snapshot": {**launch_snapshot, "context": owner.snapshot},
                    "execution": call.projection(),
                    "finished_at": None,
                }
            ),
        }
        self._s.add(row)
        self._s.commit()

    def _renew_execution(self, owner: AgentRequestExecution, call: ActiveInvocation) -> None:
        row = self._execution_row(owner, call)
        evidence = (row.metadata_json or {}).get("native_execution", {})
        if (
            call.cancelled.is_set()
            or call.process_control.cancelled.is_set()
            or evidence.get("finished_at")
            or evidence.get("execution", {}).get("invocation_id") != call.invocation_id
        ):
            raise ConflictError("native request renewal is fenced")
        now = _utcnow()
        changed = False
        if call.thread_id is not None and evidence["execution"].get("thread_id") != call.thread_id:
            row.metadata_json = {
                **(row.metadata_json or {}),
                "native_execution": {
                    **evidence,
                    "execution": call.projection(),
                },
            }
            changed = True
        # Collector ticks remain transient; write only near the existing lease boundary.
        if row.claim_expires_at is None or row.claim_expires_at <= now + timedelta(seconds=300):
            row.claim_expires_at = now + timedelta(seconds=600)
            changed = True
        if changed:
            row.updated_at = now
            self._s.add(row)
            self._s.commit()

    def _finish_execution(
        self,
        owner: AgentRequestExecution,
        call: ActiveInvocation,
        result: AgentRunResult | None,
    ) -> None:
        row = self._execution_row(owner, call, finished=True)
        evidence = dict((row.metadata_json or {}).get("native_execution", {}))
        if (
            evidence.get("finished_at")
            or evidence.get("execution", {}).get("invocation_id") != call.invocation_id
        ):
            raise ConflictError("native request exit handoff is already finished or superseded")
        evidence.update(
            redact_secrets(
                {
                    "execution": call.projection(),
                    "finished_at": _utcnow().isoformat(),
                    "usage": call.usage,
                    "exit_code": result.exit_code if result else None,
                    "result": result.result if result else None,
                    "error": result.error
                    if result
                    else "Native exit was not successfully observed",
                    "cancelled": call.cancelled.is_set() or call.process_control.cancelled.is_set(),
                    "stop_error": call.process_control.stop_error,
                }
            )
        )
        row.metadata_json = {**(row.metadata_json or {}), "native_execution": evidence}
        row.updated_at = _utcnow()
        self._s.add(row)
        self._s.commit()


__all__ = [
    "AgentRequestClaimOut",
    "AgentRequestExecution",
    "AgentRequestOut",
    "AgentRequestPrepareRunPlanOut",
    "AgentRequestRepository",
]
