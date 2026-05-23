"""Integration tests for StackOS project setup repositories."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel

from stackos.db.connection import make_engine
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


def test_project_crud_active_and_pagination(session: Session) -> None:
    repo = ProjectRepository(session)
    first = repo.create(slug="acme", name="Acme", domain="acme.example", locale="en-US")
    second = repo.create(slug="beta", name="Beta", domain="beta.example", locale="en-US")

    repo.set_active(second.data.id)

    assert repo.get(first.data.id).is_active is False
    assert repo.get("beta").is_active is True
    assert repo.get_active().id == second.data.id
    assert len(repo.list(limit=1).items) == 1
    assert repo.list(active_only=True).items[0].id == second.data.id


def test_project_validation_and_soft_delete(session: Session) -> None:
    repo = ProjectRepository(session)
    env = repo.create(slug="immut", name="Immutable", domain="example.com", locale="en-US")

    with pytest.raises(ConflictError):
        repo.create(slug="immut", name="Duplicate", domain="dup.example", locale="en-US")
    with pytest.raises(ValidationError):
        repo.update(env.data.id, slug="changed")
    with pytest.raises(NotFoundError):
        repo.get("missing")

    repo.set_active(env.data.id)
    repo.delete(env.data.id)
    assert repo.get(env.data.id).is_active is False


def test_integration_credential_set_round_trip_and_remove(
    session: Session,
    project_id: int,
) -> None:
    repo = IntegrationCredentialRepository(session)
    out = repo.set(
        project_id=project_id,
        kind="dataforseo",
        secret_payload=b"API_KEY",
        config_json={"login": "user@example.com"},
    )

    assert repo.get_decrypted(out.data.id) == b"API_KEY"
    row = repo.fetch_row(out.data.id)
    assert row.encrypted_payload
    assert len(row.nonce) == 12
    assert repo.list(project_id)[0].kind == "dataforseo"

    repo.remove(out.data.id)
    assert repo.list(project_id) == []


def test_integration_credential_aad_tamper_fails(session: Session, project_id: int) -> None:
    from stackos.crypto.aes_gcm import CryptoError, decrypt

    repo = IntegrationCredentialRepository(session)
    env = repo.set(project_id=project_id, kind="firecrawl", secret_payload=b"secret")
    row = repo.fetch_row(env.data.id)

    with pytest.raises(CryptoError):
        decrypt(
            row.encrypted_payload,
            nonce=row.nonce,
            project_id=project_id + 1,
            kind="firecrawl",
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

    repo.toggle(env.data.id, enabled=False)
    assert repo.list(project_id)[0].enabled is False
