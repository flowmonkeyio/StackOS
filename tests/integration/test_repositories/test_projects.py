"""Integration tests for StackOS project setup repositories."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from stackos.db.connection import make_engine
from stackos.db.models import Credential, Project, ProjectCredential
from stackos.repositories.base import (
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from stackos.repositories.projects import (
    IntegrationBudgetRepository,
    IntegrationCredentialRepository,
    ProjectRepository,
    ScheduledJobRepository,
)
from tests.integration.account_test_support import seed_test_account


def test_project_crud_live_projects_and_pagination(session: Session) -> None:
    repo = ProjectRepository(session)
    first = repo.create(slug="acme", name="Acme", domain="acme.example", locale="en-US")
    second = repo.create(slug="beta", name="Beta", domain="beta.example", locale="en-US")

    assert repo.get(first.data.id).is_active is True
    assert repo.get("beta").is_active is True
    assert len(repo.list(limit=1).items) == 1
    assert [project.id for project in repo.list(active_only=True).items] == [
        first.data.id,
        second.data.id,
    ]


def test_project_validation_and_inactive_metadata(session: Session) -> None:
    repo = ProjectRepository(session)
    env = repo.create(slug="immut", name="Immutable", domain="example.com", locale="en-US")

    with pytest.raises(ConflictError):
        repo.create(slug="immut", name="Duplicate", domain="dup.example", locale="en-US")
    with pytest.raises(ValidationError):
        repo.update(env.data.id, slug="changed")
    with pytest.raises(NotFoundError):
        repo.get("missing")

    repo.update(env.data.id, is_active=False)
    assert repo.get(env.data.id).is_active is False


@pytest.mark.parametrize("replacement_slug", ["saved-target", "other-target"])
def test_deleted_highest_project_id_is_not_reused(session: Session, replacement_slug: str) -> None:
    repo = ProjectRepository(session)
    saved_id = repo.create(
        slug="saved-target", name="Saved target", domain="saved.example", locale="en-US"
    ).data.id
    # Simulate historical row removal; cleanup adapters have real-service proof.
    session.delete(session.get(Project, saved_id))
    session.commit()
    replacement = repo.create(
        slug=replacement_slug, name="Replacement", domain="new.example", locale="en-US"
    ).data.id
    assert replacement > saved_id
    with pytest.raises(NotFoundError):
        repo.get(saved_id)


def test_project_id_nonreuse_after_reopen_and_concurrent_allocations(tmp_path: Path) -> None:
    db = tmp_path / "projects.db"
    engine = make_engine(db)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        repo = ProjectRepository(session)
        saved_id = repo.create(
            slug="saved", name="Saved", domain="saved.local", locale="en-US"
        ).data.id
        session.delete(session.get(Project, saved_id))
        session.commit()
    engine.dispose()
    engine = make_engine(db)

    def create(slug: str) -> int:
        with Session(engine) as session:
            return (
                ProjectRepository(session)
                .create(slug=slug, name=slug, domain=f"{slug}.local", locale="en-US")
                .data.id
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, ["saved", "replacement"]))
    assert len(set(ids)) == 2
    assert min(ids) > saved_id
    with Session(engine) as session, pytest.raises(NotFoundError):
        ProjectRepository(session).get(saved_id)
    engine.dispose()


def test_integration_credential_set_round_trip_and_remove(
    session: Session,
    project_id: int,
) -> None:
    repo = IntegrationCredentialRepository(session)
    out = seed_test_account(
        session,
        project_id=project_id,
        provider_key="dataforseo",
        display_name="DataForSEO - Default",
        secret_payload=b"API_KEY",
    )

    assert repo.get_decrypted(out.data.id) == b"API_KEY"
    row = repo.fetch_row(out.data.id)
    assert row.encrypted_payload
    assert len(row.nonce) == 12
    assert repo.list()[0].id == out.data.id

    account = session.exec(
        select(Credential).where(Credential.integration_credential_id == out.data.id)
    ).one()
    attachment = session.exec(
        select(ProjectCredential).where(ProjectCredential.credential_id == account.id)
    ).one()
    session.delete(attachment)
    session.flush()
    session.delete(account)
    session.commit()
    repo.remove(out.data.id)
    assert repo.list() == []


def test_integration_credential_aad_tamper_fails(session: Session, project_id: int) -> None:
    from stackos.crypto.aes_gcm import CryptoError, decrypt_account

    repo = IntegrationCredentialRepository(session)
    env = seed_test_account(
        session,
        project_id=project_id,
        provider_key="firecrawl",
        display_name="Firecrawl - Default",
        secret_payload=b"secret",
    )
    row = repo.fetch_row(env.data.id)

    with pytest.raises(CryptoError):
        decrypt_account(
            row.encrypted_payload,
            nonce=row.nonce,
            credential_ref="cred_wrong",
            provider_key="firecrawl",
        )


def test_budget_record_call_preemption_and_rollover(session: Session, project_id: int) -> None:
    repo = IntegrationBudgetRepository(session)
    repo.set(project_id=project_id, kind="dataforseo", monthly_budget_usd=1.0)
    repo.record_call(project_id=project_id, kind="dataforseo", cost_usd=0.5)
    repo.record_call(project_id=project_id, kind="dataforseo", cost_usd=0.4)

    with pytest.raises(BudgetExceededError):
        repo.record_call(project_id=project_id, kind="dataforseo", cost_usd=0.2)

    feb = datetime(2026, 2, 1, 0, 0, 0)
    out = repo.record_call(
        project_id=project_id,
        kind="dataforseo",
        cost_usd=0.8,
        now=feb,
    )
    assert out.data.current_month_calls == 1
    assert out.data.current_month_spend == pytest.approx(0.8)


def test_budget_preemption_uses_atomic_increment_with_stale_session(tmp_path) -> None:
    engine = make_engine(tmp_path / "budget-race.sqlite")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as setup:
        project_id = (
            ProjectRepository(setup)
            .create(
                slug="budget-race",
                name="Budget Race",
                domain="example.com",
                locale="en-US",
            )
            .data.id
        )
        assert project_id is not None
        IntegrationBudgetRepository(setup).set(
            project_id=project_id,
            kind="dataforseo",
            monthly_budget_usd=1.0,
        )

    with Session(engine) as s1, Session(engine) as s2:
        repo1 = IntegrationBudgetRepository(s1)
        repo2 = IntegrationBudgetRepository(s2)
        repo2.get(project_id, "dataforseo")

        repo1.record_call(project_id=project_id, kind="dataforseo", cost_usd=0.6)
        with pytest.raises(BudgetExceededError):
            repo2.record_call(project_id=project_id, kind="dataforseo", cost_usd=0.6)

    with Session(engine) as check:
        budget = IntegrationBudgetRepository(check).get(project_id, "dataforseo")
        assert budget.current_month_spend == pytest.approx(0.6)
        assert budget.current_month_calls == 1


def test_scheduled_jobs(session: Session, project_id: int) -> None:
    repo = ScheduledJobRepository(session)
    env = repo.set(project_id=project_id, kind="weekly-review", cron_expr="0 3 * * *")

    rows = repo.list(project_id)
    assert len(rows) == 1
    assert rows[0].cron_expr == "0 3 * * *"

    repo.toggle(env.data.id, project_id=project_id, enabled=False)
    assert repo.list(project_id)[0].enabled is False


@pytest.mark.parametrize(
    "patch",
    [
        {"cron_expr": "not cron"},
        {"timezone": "Mars/Olympus"},
        {"execution_json": {"project_dir": "/tmp", "task_key": "forbidden"}},
        {"inputs_json": {"api_key": "do-not-store"}},
    ],
)
def test_schedule_rejects_invalid_drafts_without_writes(
    session: Session, project_id: int, patch: dict
) -> None:
    repo = ScheduledJobRepository(session)
    with pytest.raises(ValidationError):
        repo.set(**{"project_id": project_id, "kind": "invalid", "cron_expr": "0 9 * * 1", **patch})
    assert repo.list(project_id) == []


def test_schedule_reconcile_and_history_reuse_canonical_task(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackos.db.models import AgentRequest, ScheduledJob
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.run_plans import RunPlanRepository
    from stackos.repositories.tracker import TrackerRepository
    from tests.integration.test_repositories.test_agent_statistics import _receipt

    reconciled = []
    session.info["operation_services"] = {
        "schedule_reconcile": lambda **scope: reconciled.append(scope)
    }
    repo = ScheduledJobRepository(session)
    schedule = repo.set(
        project_id=project_id, kind="history", name="Original name", cron_expr="0 9 * * 1"
    ).data
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        template_key="core.project-memory-review",
        inputs_json={"goal": "Synthetic history"},
    ).data
    plans.start(plan.id, project_id=project_id)
    request = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key="schedule:history:one",
            title="Original name",
            source_kind="schedule",
            metadata_json={"schedule_id": schedule.id, "schedule_name": "Original name"},
        )
        .data
    )
    row = session.get(AgentRequest, request.id)
    row.run_plan_id = plan.id
    session.add(row)
    session.commit()
    tracker = TrackerRepository(session)
    ticket_key = f"workflow-{plan.id}-clarify-goal"
    tracker.update_ticket(
        project_id=project_id,
        ticket_key=ticket_key,
        patch_json={
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 1,
                    "delivery": _receipt(total=10),
                    "review": _receipt("review_agent", total=20),
                }
            },
        },
    )
    # One request is linked to a real plan/run; a skipped tick is never a row.
    AgentRequestRepository(session).create(
        project_id=project_id, request_key="not-a-run", title="Skipped", source_kind="schedule"
    )
    expected_task = tracker.get(
        project_id=project_id, task_key=f"workflow-{plan.id}", include_graph=False
    ).tasks[0]
    history = repo.runs(project_id=project_id, schedule_id=schedule.id, limit=1)
    assert history.total_estimate == 1
    item = history.items[0]
    assert item.schedule_name == "Original name"
    assert item.run_id == plans.get(plan.id, project_id=project_id).run_id
    assert item.task_key == expected_task.key
    assert item.status == expected_task.status
    assert item.agent_stats == expected_task.agent_stats
    assert item.agent_stats.reported_usage.input == 30
    assert item.active_execution == expected_task.active_execution
    repo.set(project_id=project_id, kind="history", name="Renamed", cron_expr="0 10 * * 1")
    repo.remove(schedule.id, project_id=project_id)
    assert session.get(ScheduledJob, schedule.id) is None
    assert repo.runs(project_id=project_id).items[0] == item
    assert plans.get(plan.id, project_id=project_id).run_id == item.run_id
    assert reconciled == [{"project_id": project_id, "job_id": schedule.id}] * 3
    other = (
        ProjectRepository(session)
        .create(slug="other-history", name="Other", domain="other.test")
        .data.id
    )
    assert repo.runs(project_id=other, schedule_id=schedule.id).items == []


def test_scheduled_run_pagination_and_nullable_run_link(session: Session, project_id: int) -> None:
    from stackos.db.models import AgentRequest
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.run_plans import RunPlanRepository

    repo = ScheduledJobRepository(session)
    ids = []
    for number in range(2):
        plan = (
            RunPlanRepository(session)
            .create(
                project_id=project_id,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": f"history-{number}.run",
                    "title": "Unstarted occurrence",
                    "steps": [{"id": "work", "title": "Work"}],
                },
            )
            .data
        )
        request = (
            AgentRequestRepository(session)
            .create(
                project_id=project_id,
                request_key=f"history-{number}",
                title="Snapshot",
                source_kind="schedule",
                metadata_json={"schedule_id": 17, "schedule_name": "Snapshot"},
            )
            .data
        )
        row = session.get(AgentRequest, request.id)
        row.run_plan_id = plan.id
        session.add(row)
        session.commit()
        ids.append(request.id)
    first = repo.runs(project_id=project_id, schedule_id=17, limit=1)
    assert first.total_estimate == 2 and first.next_cursor == ids[1]
    assert first.items[0].request_id == ids[1]
    assert first.items[0].run_id is None and first.items[0].started_at is None
    assert first.items[0].agent_stats.coverage.usage_status == "empty"
    assert first.items[0].agent_stats.reported_usage.input == 0
    second = repo.runs(project_id=project_id, schedule_id=17, limit=1, after_id=first.next_cursor)
    assert [item.request_id for item in second.items] == [ids[0]]
    assert second.next_cursor is None
    with pytest.raises(ValidationError):
        repo.runs(project_id=project_id, limit=201)


def test_removed_schedule_id_cannot_alias_replacement_or_its_history(
    session: Session, project_id: int
) -> None:
    from stackos.db.models import AgentRequest
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.run_plans import RunPlanRepository

    schedules = ScheduledJobRepository(session)
    old = schedules.set(
        project_id=project_id, kind="removed", name="Removed", cron_expr="0 9 * * mon"
    ).data
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "removed.history",
                "title": "Retained history",
                "steps": [{"id": "work", "title": "Work"}],
            },
        )
        .data
    )
    request = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key=f"schedule:{old.id}:old-due",
            title=old.name,
            source_kind="schedule",
            metadata_json={"schedule_id": old.id, "schedule_name": old.name},
        )
        .data
    )
    row = session.get(AgentRequest, request.id)
    row.run_plan_id = plan.id
    session.add(row)
    session.commit()
    schedules.remove(old.id, project_id=project_id)
    replacement = schedules.set(
        project_id=project_id, kind="replacement", name="Replacement", cron_expr="0 10 * * mon"
    ).data
    assert replacement.id > old.id
    assert schedules.runs(project_id=project_id, schedule_id=replacement.id).items == []
    assert [
        item.request_id for item in schedules.runs(project_id=project_id, schedule_id=old.id).items
    ] == [request.id]
    for mutate in (
        lambda: schedules.toggle(old.id, project_id=project_id, enabled=False),
        lambda: schedules.remove(old.id, project_id=project_id),
    ):
        with pytest.raises(NotFoundError):
            mutate()
    assert schedules.list(project_id) == [replacement]
