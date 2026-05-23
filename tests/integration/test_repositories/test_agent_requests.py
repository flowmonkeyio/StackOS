"""Repository tests for the generic agent request inbox."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session

from content_stack.db.models import AgentRequest, AgentRequestStatus
from content_stack.repositories.agent_requests import AgentRequestRepository
from content_stack.repositories.base import ConflictError, NotFoundError, ValidationError
from content_stack.repositories.projects import ProjectRepository
from content_stack.repositories.resources import ResourceRepository
from content_stack.repositories.run_plans import RunPlanRepository


def _run_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "agent.request.run",
        "title": "Handle Agent Request",
        "steps": [{"id": "handle", "title": "Handle request"}],
    }


def test_agent_request_create_list_and_redaction(session: Session, project_id: int) -> None:
    source = (
        ResourceRepository(session)
        .upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-message",
            external_id="telegram-update-1",
            title="Telegram update",
            data_json={"body_preview": "Need help"},
        )
        .data
    )
    repo = AgentRequestRepository(session)

    created = repo.create(
        project_id=project_id,
        request_key="telegram:update:1",
        title="Message with api_key=secret",
        body_preview="Authorization: Bearer nope",
        source_provider="telegram-bot",
        source_kind="telegram-message",
        source_resource_key="communication-message",
        source_resource_record_id=source.id,
        source_message_ref="msg-1",
        priority=5,
        metadata_json={"source": "telegram", "access_token": "hidden"},
    ).data

    assert created.status == AgentRequestStatus.NEW
    assert created.attention_status == "unread"
    assert created.title == "Message with api_key=[redacted]"
    assert created.body_preview == "Authorization: Bearer [redacted]"
    assert created.metadata_json == {"source": "telegram", "access_token": "[redacted]"}
    assert created.source_resource_record_id == source.id

    duplicate = repo.create(
        project_id=project_id,
        request_key="telegram:update:1",
        title="Different title",
    ).data
    assert duplicate.id == created.id
    assert duplicate.title == created.title

    page = repo.list(project_id=project_id, statuses=[AgentRequestStatus.NEW])
    assert [item.id for item in page.items] == [created.id]


def test_agent_request_claim_release_and_expired_lease(
    session: Session,
    project_id: int,
) -> None:
    repo = AgentRequestRepository(session)
    created = repo.create(
        project_id=project_id,
        request_key="email:uid:1",
        title="Inbox message",
    ).data

    claimed = repo.claim(
        project_id=project_id,
        request_id=created.id,
        claimed_by="agent-a",
        lease_seconds=60,
    ).data
    assert claimed.claim_token
    assert claimed.status == AgentRequestStatus.CLAIMED
    assert claimed.attention_status == "read"
    assert claimed.claimed_by == "agent-a"

    with pytest.raises(ConflictError):
        repo.claim(
            project_id=project_id,
            request_id=created.id,
            claimed_by="agent-b",
        )
    with pytest.raises(ConflictError):
        repo.release(project_id=project_id, request_id=created.id, claim_token="wrong")

    released = repo.release(
        project_id=project_id,
        request_id=created.id,
        claim_token=claimed.claim_token,
    ).data
    assert released.status == AgentRequestStatus.NEW
    assert released.claimed_by is None

    second = repo.claim(
        project_id=project_id,
        request_id=created.id,
        claimed_by="agent-a",
        lease_seconds=60,
    ).data
    row = session.get(AgentRequest, created.id)
    assert row is not None and row.claim_expires_at is not None
    row.claim_expires_at = row.claim_expires_at - timedelta(seconds=120)
    session.add(row)
    session.commit()

    reclaimed = repo.claim(
        project_id=project_id,
        request_id=created.id,
        claimed_by="agent-b",
        lease_seconds=60,
    ).data
    assert reclaimed.claim_token != second.claim_token
    assert reclaimed.claimed_by == "agent-b"


def test_agent_request_claimable_list_includes_expired_leases(
    session: Session,
    project_id: int,
) -> None:
    repo = AgentRequestRepository(session)
    open_request = repo.create(
        project_id=project_id,
        request_key="claimable-open",
        title="Open",
    ).data
    fresh_request = repo.create(
        project_id=project_id,
        request_key="claimable-fresh-lease",
        title="Fresh lease",
    ).data
    expired_request = repo.create(
        project_id=project_id,
        request_key="claimable-expired-lease",
        title="Expired lease",
    ).data

    repo.claim(
        project_id=project_id,
        request_id=fresh_request.id,
        claimed_by="agent-a",
        lease_seconds=600,
    )
    expired_claim = repo.claim(
        project_id=project_id,
        request_id=expired_request.id,
        claimed_by="agent-a",
        lease_seconds=60,
    ).data
    row = session.get(AgentRequest, expired_request.id)
    assert row is not None and row.claim_expires_at is not None
    row.claim_expires_at = row.claim_expires_at - timedelta(seconds=120)
    session.add(row)
    session.commit()

    claimable = repo.list_claimable(project_id=project_id).items

    assert [item.id for item in claimable] == [open_request.id, expired_request.id]
    assert expired_claim.id == expired_request.id


def test_agent_request_link_complete_and_project_invariants(
    session: Session,
    project_id: int,
) -> None:
    repo = AgentRequestRepository(session)
    request = repo.create(
        project_id=project_id,
        request_key="telegram:update:2",
        title="Run this",
    ).data
    claimed = repo.claim(
        project_id=project_id,
        request_id=request.id,
        claimed_by="agent-a",
    ).data
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json=_run_plan_json(),
        )
        .data
    )

    linked = repo.link_run_plan(
        project_id=project_id,
        request_id=request.id,
        run_plan_id=plan.id,
        claim_token=claimed.claim_token,
    ).data
    assert linked.status == AgentRequestStatus.RUN_CREATED
    assert linked.run_plan_id == plan.id

    completed = repo.complete(
        project_id=project_id,
        request_id=request.id,
        claim_token=claimed.claim_token,
        metadata_json={"result": "ok", "api_key": "hidden"},
    ).data
    assert completed.status == AgentRequestStatus.RESOLVED
    assert completed.completed_at is not None
    assert completed.metadata_json == {"result": "ok", "api_key": "[redacted]"}
    completed_row = session.get(AgentRequest, request.id)
    assert completed_row is not None
    assert completed_row.claim_token_hash is None
    assert completed_row.claim_expires_at is None

    invalid_request = repo.create(
        project_id=project_id,
        request_key="telegram:update:invalid-complete",
        title="Invalid complete status",
    ).data
    invalid_claim = repo.claim(
        project_id=project_id,
        request_id=invalid_request.id,
        claimed_by="agent-a",
    ).data
    with pytest.raises(ValidationError):
        repo.complete(
            project_id=project_id,
            request_id=invalid_request.id,
            claim_token=invalid_claim.claim_token,
            status=AgentRequestStatus.RESPONDED,
        )

    other_project = (
        ProjectRepository(session)
        .create(
            slug="other",
            name="Other",
            domain="other.example",
        )
        .data
    )
    other_plan = (
        RunPlanRepository(session)
        .create(
            project_id=other_project.id,
            run_plan_json=_run_plan_json(),
        )
        .data
    )
    other_request = repo.create(
        project_id=project_id,
        request_key="telegram:update:3",
        title="Wrong project",
    ).data
    other_claim = repo.claim(
        project_id=project_id,
        request_id=other_request.id,
        claimed_by="agent-a",
    ).data
    with pytest.raises(ConflictError):
        repo.link_run_plan(
            project_id=project_id,
            request_id=other_request.id,
            run_plan_id=other_plan.id,
            claim_token=other_claim.claim_token,
        )


def test_agent_request_prepare_run_plan_is_atomic_and_links_request(
    session: Session,
    project_id: int,
) -> None:
    repo = AgentRequestRepository(session)
    request = repo.create(
        project_id=project_id,
        request_key="telegram:update:prepare",
        title="Prepare this",
    ).data

    prepared = repo.prepare_run_plan(
        project_id=project_id,
        request_id=request.id,
        claimed_by="agent-a",
        run_plan_json=_run_plan_json(),
        metadata_json={"source": "telegram"},
    ).data

    assert prepared.claim_token
    assert prepared.request.status == AgentRequestStatus.RUN_CREATED
    assert prepared.request.claimed_by == "agent-a"
    assert prepared.request.run_plan_id == prepared.run_plan.id
    assert prepared.run_plan.status == "draft"
    assert prepared.run_plan.metadata_json == {
        "source": "telegram",
        "agent_request_id": request.id,
        "agent_request_key": "telegram:update:prepare",
    }

    completed = repo.complete(
        project_id=project_id,
        request_id=request.id,
        claim_token=prepared.claim_token,
    ).data
    assert completed.status == AgentRequestStatus.RESOLVED


def test_agent_request_prepare_run_plan_rolls_back_invalid_plan(
    session: Session,
    project_id: int,
) -> None:
    repo = AgentRequestRepository(session)
    request = repo.create(
        project_id=project_id,
        request_key="telegram:update:bad-plan",
        title="Bad plan",
    ).data

    with pytest.raises(ValidationError):
        repo.prepare_run_plan(
            project_id=project_id,
            request_id=request.id,
            claimed_by="agent-a",
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "bad.run",
                "title": "Bad",
                "steps": [],
            },
        )

    after = repo.get(project_id=project_id, request_id=request.id)
    assert after.status == AgentRequestStatus.NEW
    assert after.claimed_by is None
    assert RunPlanRepository(session).list(project_id=project_id).items == []


def test_agent_request_ignore_and_validation(session: Session, project_id: int) -> None:
    repo = AgentRequestRepository(session)
    created = repo.create(project_id=project_id, request_key="ignore-me", title="Ignore").data

    ignored = repo.ignore(
        project_id=project_id,
        request_id=created.id,
        ignored_by="agent-a",
    ).data
    assert ignored.status == AgentRequestStatus.IGNORED
    assert ignored.attention_status == "archived"
    assert ignored.ignored_at is not None

    with pytest.raises(ConflictError):
        repo.claim(project_id=project_id, request_id=created.id, claimed_by="agent-a")
    with pytest.raises(NotFoundError):
        repo.get(project_id=project_id + 10, request_id=created.id)
    with pytest.raises(ValidationError):
        repo.create(project_id=project_id, request_key="", title="No key")

    claimed = repo.create(
        project_id=project_id,
        request_key="claimed-ignore",
        title="Ignore after claim",
    ).data
    claim = repo.claim(
        project_id=project_id,
        request_id=claimed.id,
        claimed_by="agent-a",
    ).data
    ignored_claim = repo.ignore(
        project_id=project_id,
        request_id=claimed.id,
        ignored_by="agent-a",
        claim_token=claim.claim_token,
    ).data
    assert ignored_claim.status == AgentRequestStatus.IGNORED
    ignored_row = session.get(AgentRequest, claimed.id)
    assert ignored_row is not None
    assert ignored_row.claim_token_hash is None
    assert ignored_row.claim_expires_at is None
