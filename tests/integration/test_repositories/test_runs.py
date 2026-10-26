"""Tests for RunRepository — start, finish, abort cascade, reap_stale."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, SQLModel, create_engine, select

from stackos.db.models import (
    ProjectEvent,
    Run,
    RunKind,
    RunPlan,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
    TrackerLinkKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketLink,
    TrackerTicketReference,
    WorkItemOwnership,
)
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository


def _now() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _ownership_ticket(session: Session, project_id: int, key: str = "work"):
    tracker = TrackerRepository(session)
    tracker.create_task(project_id=project_id, key=key, title="Canonical task")
    result = tracker.create_ticket(
        project_id=project_id,
        task_key=key,
        key=key,
        title="Canonical ticket",
        references_json=[
            {"ref_type": "external", "ref": "https://example.test/issues/one"},
            {"ref_type": "external", "ref": "https://example.test/issues/one"},
        ],
    ).data
    assert result.ticket is not None
    return result.ticket


def test_owner_item_aliases_converge_on_native_ticket(session: Session, project_id: int) -> None:
    ticket = _ownership_ticket(session, project_id)
    session.add(
        TrackerTicketLink(
            tracker_id=ticket.tracker_id,
            project_id=project_id,
            task_id=ticket.task_id,
            ticket_id=ticket.id,
            link_kind=TrackerLinkKind.EXTERNAL,
            ref="external-system:stable-uuid",
        )
    )
    session.commit()
    repo = TrackerRepository(session)
    native = repo.resolve_owner_item(project_id=project_id, canonical_item_id=f"ticket:{ticket.id}")
    by_url = repo.resolve_owner_item(
        project_id=project_id, alias_ref="https://example.test/issues/one"
    )
    by_uuid = repo.resolve_owner_item(
        project_id=project_id, alias_ref="external-system:stable-uuid"
    )
    assert native == by_url == by_uuid
    assert native.project_id == project_id
    assert native.canonical_item_id == f"ticket:{ticket.id}"
    assert native.item_kind == "ticket"


def test_owner_item_task_and_ticket_ids_are_distinct(session: Session, project_id: int) -> None:
    ticket = _ownership_ticket(session, project_id)
    # SQLite tables allocate independently: raw integer IDs overlap.
    assert ticket.id == ticket.task_id
    repo = TrackerRepository(session)
    task = repo.resolve_owner_item(
        project_id=project_id, canonical_item_id=f"task:{ticket.task_id}"
    )
    child = repo.resolve_owner_item(project_id=project_id, canonical_item_id=f"ticket:{ticket.id}")
    assert task.item_id == child.item_id
    assert task.canonical_item_id != child.canonical_item_id


def test_owner_item_ambiguous_alias_is_rejected(session: Session, project_id: int) -> None:
    first = _ownership_ticket(session, project_id)
    second = _ownership_ticket(session, project_id, key="other-work")
    assert first.id != second.id
    with pytest.raises(ConflictError, match="ambiguous"):
        TrackerRepository(session).resolve_owner_item(
            project_id=project_id, alias_ref="https://example.test/issues/one"
        )


def test_owner_item_project_scope_rejects_foreign_and_corrupt_alias_targets(
    session: Session, project_id: int
) -> None:
    from stackos.repositories.projects import ProjectRepository

    other_project_id = (
        ProjectRepository(session)
        .create(slug="other-owner-project", name="Other project", domain="example.test")
        .data.id
    )
    ticket = _ownership_ticket(session, project_id)
    foreign = _ownership_ticket(session, other_project_id, key="foreign-work")
    repo = TrackerRepository(session)
    assert (
        repo.resolve_owner_item(
            project_id=project_id, alias_ref="https://example.test/issues/one"
        ).item_id
        == ticket.id
    )
    with pytest.raises(NotFoundError):
        repo.resolve_owner_item(project_id=project_id, canonical_item_id=f"ticket:{foreign.id}")
    session.add(
        TrackerTicketReference(
            tracker_id=ticket.tracker_id,
            project_id=project_id,
            ticket_id=foreign.id,
            ref_type="external",
            ref="external-system:corrupt-target",
        )
    )
    session.commit()
    with pytest.raises(NotFoundError):
        repo.resolve_owner_item(project_id=project_id, alias_ref="external-system:corrupt-target")


@pytest.mark.parametrize(
    "canonical_item_id",
    ["ticket:0", "ticket:-1", "ticket:01", "task:bad", "1", "run:1", "ticket:9223372036854775808"],
)
def test_owner_item_rejects_malformed_native_identity(
    session: Session, project_id: int, canonical_item_id: str
) -> None:
    with pytest.raises(ValidationError):
        TrackerRepository(session).resolve_owner_item(
            project_id=project_id, canonical_item_id=canonical_item_id
        )


def test_owner_item_requires_one_selector_and_known_alias(
    session: Session, project_id: int
) -> None:
    repo = TrackerRepository(session)
    with pytest.raises(ValidationError):
        repo.resolve_owner_item(project_id=project_id)
    with pytest.raises(ValidationError):
        repo.resolve_owner_item(
            project_id=project_id, canonical_item_id="ticket:1", alias_ref="external:one"
        )
    with pytest.raises(ValidationError):
        repo.resolve_owner_item(project_id=project_id, alias_ref="")
    with pytest.raises(NotFoundError):
        repo.resolve_owner_item(project_id=project_id, alias_ref="external:missing")


OWNER_SCOPE = "a" * 64


def _continuity_ticket(session: Session, project_id: int):
    ticket = _ownership_ticket(session, project_id)
    native = session.get(TrackerTicket, ticket.id)
    assert native is not None
    native.metadata_json = {
        "ownership_continuity": {
            "budget_ref": "budget:original",
            "stop_scope_ref": "stop-scope:original",
            "history_ref": "history:original",
            "scope_digest": OWNER_SCOPE,
            "stopped": False,
            "denied": False,
            "attempts_used": 2,
            "failures_used": 1,
            "attempt_limit": 4,
            "failure_limit": 3,
            "effect_identity": "effect:stable",
        }
    }
    session.add(native)
    session.commit()
    return f"ticket:{ticket.id}"


def _acquire(repo: RunRepository, project_id: int, item: str, owner: str = "session:first"):
    return repo.owner_acquire(
        project_id=project_id,
        canonical_item_id=item,
        owner_ref=owner,
        scope_digest=OWNER_SCOPE,
    ).ownership


@pytest.mark.parametrize("binding", ["valid", "missing", "boolean", "overflow"])
def test_owner_restart_context_task_uses_exact_bounded_source_binding(
    session: Session, project_id: int, binding: str
) -> None:
    item = _continuity_ticket(session, project_id)
    ticket = session.get(TrackerTicket, int(item.split(":")[1]))
    task = session.get(TrackerTask, ticket.task_id)
    plan = RunPlan(
        project_id=project_id, key="restart", title="Restart", status=RunPlanStatus.DRAFT
    )
    session.add(plan)
    session.flush()
    step = RunPlanStep(
        run_plan_id=plan.id,
        step_id="work",
        title="Work",
        position=0,
        status=RunPlanStepStatus.PENDING,
    )
    session.add(step)
    session.flush()
    task.metadata_json = ticket.metadata_json
    task.source_json = {
        "valid": {"run_plan_id": plan.id, "run_plan_step_id": step.id},
        "missing": {},
        "boolean": {"run_plan_id": True, "run_plan_step_id": True},
        "overflow": {"run_plan_id": 2**100, "run_plan_step_id": 2**100},
    }[binding]
    session.add(task)
    session.commit()
    repo = RunRepository(session)
    owner = _acquire(repo, project_id, f"task:{task.id}")
    context = repo.owner_restart_context(owner)
    assert context["context_only"] is True
    assert context["stored_continuity"]["attempts_used"] == 2
    assert context["current_continuity"]["failures_used"] == 1
    if binding == "valid":
        assert context["workflow"]["run_plan_id"] == plan.id
        assert context["workflow"]["run_plan_step_id"] == step.id
        assert context["workflow"]["run_id"] is None
    else:
        assert context["workflow"] is None
        assert context["binding_issues"]


def _checkpoint(repo: RunRepository, row, **overrides):
    args = {
        "project_id": row.project_id,
        "canonical_item_id": row.canonical_item_id,
        "owner_ref": row.owner_ref,
        "scope_digest": row.scope_digest,
        "expected_epoch": row.epoch,
        "expected_revision": row.revision,
        "checkpoint_ref": "evidence:checkpoint",
    }
    return repo.owner_checkpoint(**(args | overrides))


def _certify(repo: RunRepository, row):
    # Fixture for the private producer seam, NOT evidence of real host control.
    return repo._certify_owner_quiescence(
        project_id=row.project_id,
        canonical_item_id=row.canonical_item_id,
        owner_ref=row.owner_ref,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
        evidence_ref="fixture:quiescence-and-effects-disposed",
    )


def _reassign(repo: RunRepository, row, new_owner="session:replacement"):
    return repo.owner_reassign(
        project_id=row.project_id,
        canonical_item_id=row.canonical_item_id,
        owner_ref=row.owner_ref,
        new_owner_ref=new_owner,
        scope_digest=row.scope_digest,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
    )


def test_owner_duplicate_alias_trigger_has_one_epoch_and_one_event(
    session: Session, project_id: int
) -> None:
    item = _continuity_ticket(session, project_id)
    repo = RunRepository(session)
    row = _acquire(repo, project_id, item)
    repeat = repo.owner_acquire(
        project_id=project_id,
        alias_ref="https://example.test/issues/one",
        owner_ref=row.owner_ref,
        scope_digest=OWNER_SCOPE,
    )
    assert repeat.acquired is False
    assert repeat.ownership == row
    assert row.epoch == row.revision == 1
    assert len(session.exec(select(WorkItemOwnership)).all()) == 1
    events = session.exec(
        select(ProjectEvent).where(ProjectEvent.event_type == "WORK_ITEM_OWNER_ACQUIRED")
    ).all()
    assert len(events) == 1
    assert events[0].source_type == "tracker_ticket"
    assert events[0].source_id == int(item.split(":")[1])
    with pytest.raises(ConflictError, match="already has an owner"):
        _acquire(repo, project_id, item, "session:duplicate")


def test_owner_checkpoint_cas_and_replacement_preserve_lineage(
    session: Session, project_id: int
) -> None:
    repo = RunRepository(session)
    first = _acquire(repo, project_id, _continuity_ticket(session, project_id))
    checked = _checkpoint(repo, first)
    assert checked.revision == 2
    assert checked.checkpoint_revision == 1
    with pytest.raises(ConflictError, match="stale ownership fence"):
        _checkpoint(repo, first)
    with pytest.raises(ConflictError, match="quiescence"):
        _reassign(repo, checked)
    certified = _certify(repo, checked)
    assert certified.revision == certified.quiescence_revision == 3
    replacement = _reassign(repo, certified)
    assert replacement.epoch == 2
    assert replacement.revision == 4
    assert replacement.previous_owner_ref == first.owner_ref
    assert replacement.checkpoint_ref == checked.checkpoint_ref
    assert replacement.checkpoint_revision == checked.checkpoint_revision
    assert replacement.continuity_json == first.continuity_json
    assert replacement.quiescence_ref == certified.quiescence_ref
    assert replacement.quiescence_revision is None
    with pytest.raises(ConflictError, match="stale ownership fence"):
        _checkpoint(repo, replacement, owner_ref=first.owner_ref, expected_epoch=first.epoch)
    assert (
        repo.owner_get(project_id=project_id, canonical_item_id=first.canonical_item_id)
        == replacement
    )


def test_owner_new_checkpoint_invalidates_old_quiescence(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    first = _acquire(repo, project_id, _continuity_ticket(session, project_id))
    certified = _certify(repo, first)
    checked = _checkpoint(repo, certified)
    with pytest.raises(ConflictError, match="quiescence"):
        _reassign(repo, checked)
    with pytest.raises(ConflictError, match="stale ownership fence"):
        _certify(repo, first)


def test_owner_explicit_revocation_fences_and_reacquires_without_reset(
    session: Session, project_id: int
) -> None:
    repo = RunRepository(session)
    item = _continuity_ticket(session, project_id)
    original = _acquire(repo, project_id, item)
    revoked = _reassign(repo, _certify(repo, original), None)
    assert revoked.owner_ref is None
    assert revoked.epoch == 2
    with pytest.raises(ConflictError, match="stale ownership fence"):
        _checkpoint(repo, original)
    next_owner = _acquire(repo, project_id, item, "session:next")
    assert next_owner.epoch == 3
    assert next_owner.previous_owner_ref == original.owner_ref
    assert next_owner.continuity_json == original.continuity_json


def test_owner_stop_survives_restart_and_revocation(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    item = _continuity_ticket(session, project_id)
    original = _acquire(repo, project_id, item)
    stopped = repo.owner_stop(
        project_id=project_id,
        canonical_item_id=item,
        owner_ref=original.owner_ref,
        expected_epoch=original.epoch,
        expected_revision=original.revision,
        reason_ref="STOP2:original",
    )
    with Session(session.get_bind()) as restarted:
        repo = RunRepository(restarted)
        loaded = repo.owner_get(project_id=project_id, canonical_item_id=item)
        assert loaded == stopped
        assert loaded.continuity_json["attempts_used"] == 2
        assert loaded.continuity_json["failures_used"] == 1
        assert loaded.continuity_json["effect_identity"] == "effect:stable"
        with pytest.raises(ConflictError, match="stop/denial"):
            _checkpoint(repo, loaded)
        certified = _certify(repo, loaded)
        with pytest.raises(ConflictError, match="stop/denial"):
            _reassign(repo, certified)
        revoked = _reassign(repo, certified, None)
        assert revoked.continuity_json == loaded.continuity_json
        with pytest.raises(ConflictError, match="stop/denial"):
            _acquire(repo, project_id, item, "session:renamed")


@pytest.mark.parametrize(
    "changes",
    [
        {"scope_digest": "b" * 64},
        {"stopped": None},
        {"denied": None},
        {"attempts_used": -1},
        {"failures_used": True},
        {"history_ref": None},
    ],
)
def test_owner_unknown_or_changed_continuity_is_not_fresh_budget(
    session: Session, project_id: int, changes: dict
) -> None:
    item = _continuity_ticket(session, project_id)
    native = session.get(TrackerTicket, int(item.split(":")[1]))
    native.metadata_json = {
        "ownership_continuity": native.metadata_json["ownership_continuity"] | changes
    }
    flag_modified(native, "metadata_json")
    session.add(native)
    session.commit()
    with pytest.raises(ValidationError):
        _acquire(RunRepository(session), project_id, item)
    assert session.exec(select(WorkItemOwnership)).all() == []


@pytest.mark.parametrize("flag", ["stopped", "denied"])
def test_owner_original_stop_or_denial_blocks_first_admission(
    session: Session, project_id: int, flag: str
) -> None:
    item = _continuity_ticket(session, project_id)
    native = session.get(TrackerTicket, int(item.split(":")[1]))
    native.metadata_json = {
        "ownership_continuity": native.metadata_json["ownership_continuity"] | {flag: True}
    }
    session.add(native)
    session.commit()
    with pytest.raises(ConflictError, match="stop/denial"):
        _acquire(RunRepository(session), project_id, item)
    assert session.exec(select(WorkItemOwnership)).all() == []


def test_owner_scope_and_project_changes_are_rejected(session: Session, project_id: int) -> None:
    from stackos.repositories.projects import ProjectRepository

    repo = RunRepository(session)
    item = _continuity_ticket(session, project_id)
    row = _acquire(repo, project_id, item)
    with pytest.raises(ConflictError, match="scope changed"):
        _checkpoint(repo, row, scope_digest="b" * 64)
    other = (
        ProjectRepository(session).create(slug="other", name="Other", domain="other.test").data.id
    )
    with pytest.raises(NotFoundError):
        repo.owner_get(project_id=other, canonical_item_id=item)
    with pytest.raises(NotFoundError):
        _acquire(repo, other, item)


def test_owner_acquisition_race_through_aliases_has_one_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackos.repositories.projects import ProjectRepository

    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"timeout": 10})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as setup:
        pid = ProjectRepository(setup).create(slug="race", name="Race", domain="race.test").data.id
        item = _continuity_ticket(setup, pid)
    barrier = Barrier(2, timeout=5)
    admissible = RunRepository._owner_admissible

    def synchronized(row, scope_digest):
        admissible(row, scope_digest)
        if row.id is None:
            barrier.wait()

    monkeypatch.setattr(RunRepository, "_owner_admissible", staticmethod(synchronized))

    def contender(number):
        with Session(engine) as active:
            try:
                return (
                    RunRepository(active)
                    .owner_acquire(
                        project_id=pid,
                        owner_ref=f"session:{number}",
                        scope_digest=OWNER_SCOPE,
                        **(
                            {"canonical_item_id": item}
                            if number == 0
                            else {"alias_ref": "https://example.test/issues/one"}
                        ),
                    )
                    .acquired
                )
            except ConflictError:
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(contender, [0, 1])) == [False, True]
    with Session(engine) as readback:
        rows = readback.exec(select(WorkItemOwnership)).all()
        assert len(rows) == 1
        assert rows[0].epoch == rows[0].revision == 1
        assert (
            len(
                readback.exec(
                    select(ProjectEvent).where(
                        ProjectEvent.event_type == "WORK_ITEM_OWNER_ACQUIRED"
                    )
                ).all()
            )
            == 1
        )
    engine.dispose()


@pytest.mark.parametrize("existing", [False, True])
def test_owner_mutation_and_audit_are_one_transaction(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    from stackos.repositories.project_events import StackOSEventEmitter

    item = _continuity_ticket(session, project_id)
    repo = RunRepository(session)
    row = _acquire(repo, project_id, item) if existing else None

    def failed_emit(*args, **kwargs):
        raise RuntimeError("fixture: audit unavailable")

    monkeypatch.setattr(StackOSEventEmitter, "emit", failed_emit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        if row:
            _checkpoint(repo, row)
        else:
            _acquire(repo, project_id, item)
    session.commit()
    if row:
        assert repo.owner_get(project_id=project_id, canonical_item_id=item) == row
    else:
        assert session.exec(select(WorkItemOwnership)).all() == []


@pytest.mark.parametrize("operation", ["checkpoint", "reassign", "acquire"])
def test_owner_retained_session_cannot_bypass_concurrent_stop(
    session: Session, project_id: int, operation: str
) -> None:
    repo = RunRepository(session)
    item = _continuity_ticket(session, project_id)
    original = _acquire(repo, project_id, item)
    cached = session.get(WorkItemOwnership, original.id)
    assert cached is not None
    with Session(session.get_bind()) as other:
        current = RunRepository(other).owner_stop(
            project_id=project_id,
            canonical_item_id=item,
            owner_ref=original.owner_ref,
            expected_epoch=original.epoch,
            expected_revision=original.revision,
            reason_ref="STOP2:concurrent",
        )
        if operation == "reassign":
            current = _certify(RunRepository(other), current)
    assert cached.revision == 1
    assert cached.continuity_json["stopped"] is False
    with pytest.raises(ConflictError, match="stop/denial"):
        if operation == "checkpoint":
            _checkpoint(repo, current)
        elif operation == "reassign":
            _reassign(repo, current)
        else:
            _acquire(repo, project_id, item)
    readback = repo.owner_get(project_id=project_id, canonical_item_id=item)
    assert readback == current
    assert readback.continuity_json["attempts_used"] == 2
    assert readback.continuity_json["failures_used"] == 1


def test_owner_stop_racing_admission_preflight_rejects_cas(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = RunRepository(session)
    item = _continuity_ticket(session, project_id)
    original = _acquire(repo, project_id, item)
    admissible = RunRepository._owner_admissible

    def stop_after_preflight(row, scope):
        admissible(row, scope)
        with Session(session.get_bind()) as other:
            RunRepository(other).owner_stop(
                project_id=project_id,
                canonical_item_id=item,
                owner_ref=row.owner_ref,
                expected_epoch=row.epoch,
                expected_revision=row.revision,
                reason_ref="STOP2:after-preflight",
            )

    monkeypatch.setattr(RunRepository, "_owner_admissible", staticmethod(stop_after_preflight))
    with pytest.raises(ConflictError, match="stale ownership fence"):
        _checkpoint(repo, original)
    readback = repo.owner_get(project_id=project_id, canonical_item_id=item)
    assert readback.revision == 2
    assert readback.continuity_json["stopped"] is True
    assert readback.checkpoint_ref is None


@pytest.mark.parametrize("mutation", ["acquire-flush", "checkpoint-refresh"])
def test_owner_pre_audit_failure_cannot_later_commit_mutation(
    session: Session, project_id: int, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    repo = RunRepository(session)
    item = _continuity_ticket(session, project_id)
    original = _acquire(repo, project_id, item) if mutation == "checkpoint-refresh" else None
    if original:

        def failed_refresh(row, *args, **kwargs):
            raise RuntimeError("fixture: pre-audit refresh unavailable")

        monkeypatch.setattr(session, "refresh", failed_refresh)
    else:
        flush = session.flush

        def failed_flush(*args, **kwargs):
            ownership_insert = any(isinstance(row, WorkItemOwnership) for row in session.new)
            flush(*args, **kwargs)
            if ownership_insert:
                raise RuntimeError("fixture: pre-audit flush unavailable")

        monkeypatch.setattr(session, "flush", failed_flush)
    with pytest.raises(RuntimeError, match="pre-audit"):
        if original:
            _checkpoint(repo, original)
        else:
            _acquire(repo, project_id, item)
    monkeypatch.undo()
    session.commit()
    rows = session.exec(select(WorkItemOwnership)).all()
    if original:
        assert repo.owner_get(project_id=project_id, canonical_item_id=item) == original
        assert (
            session.exec(
                select(ProjectEvent).where(
                    ProjectEvent.event_type == "WORK_ITEM_OWNER_CHECKPOINTED"
                )
            ).all()
            == []
        )
    else:
        assert rows == []
        assert (
            session.exec(
                select(ProjectEvent).where(ProjectEvent.event_type == "WORK_ITEM_OWNER_ACQUIRED")
            ).all()
            == []
        )


def test_start_finish_happy_path(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    assert env.data.status == RunStatus.RUNNING
    out = repo.finish(env.data.id, status="success")
    assert out.data.status == RunStatus.SUCCESS
    assert out.data.ended_at is not None


def test_finish_terminal_run_raises(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    repo.finish(env.data.id, status="success")
    with pytest.raises(ConflictError):
        repo.finish(env.data.id, status="failed")


def test_heartbeat_updates_timestamp(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    env = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN)
    out = repo.heartbeat(env.data.id)
    assert out.data.heartbeat_at is not None


def test_heartbeat_missing_run_is_idempotent(session: Session) -> None:
    repo = RunRepository(session)
    out = repo.heartbeat(99999)
    assert out.run_id is None


def test_abort_cascade(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    parent = repo.start(project_id=project_id, kind=RunKind.RUN_PLAN).data
    child1 = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=parent.id).data
    child2 = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=parent.id).data
    grandchild = repo.start(
        project_id=project_id, kind=RunKind.SKILL_RUN, parent_run_id=child1.id
    ).data
    repo.abort(parent.id, cascade=True)
    assert repo.get(parent.id).status == RunStatus.ABORTED
    assert repo.get(child1.id).status == RunStatus.ABORTED
    assert repo.get(child2.id).status == RunStatus.ABORTED
    assert repo.get(grandchild.id).status == RunStatus.ABORTED


def test_reap_stale_orphans(session: Session, project_id: int) -> None:
    repo = RunRepository(session)
    fresh = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    stale = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    # Manually backdate the stale heartbeat past the 5-minute threshold.
    from stackos.db.models import Run

    s_row = session.get(Run, stale.id)
    assert s_row is not None
    s_row.heartbeat_at = _now() - timedelta(minutes=10)
    session.add(s_row)
    session.commit()

    n = repo.reap_stale(stale_after_seconds=300)
    assert n == 1
    assert repo.get(stale.id).status == RunStatus.ABORTED
    assert repo.get(stale.id).error == "daemon-restart-orphan"
    assert repo.get(fresh.id).status == RunStatus.RUNNING


def test_reap_stale_preserves_started_run_plan(session: Session, project_id: int) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "stale.live.workflow.run",
                "title": "Stale Live Workflow",
                "steps": [
                    {"id": "prepare", "title": "Prepare"},
                    {"id": "deliver", "title": "Deliver"},
                ],
            },
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data

    run = session.get(Run, started.run_id)
    plan_row = session.get(RunPlan, plan.id)
    assert run is not None and plan_row is not None
    stale_at = _now() - timedelta(minutes=10)
    run.heartbeat_at = stale_at
    plan_row.updated_at = stale_at
    session.add(run)
    session.add(plan_row)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    assert reaped == 0
    run = session.get(Run, started.run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING
    assert run.error is None
    assert run.heartbeat_at == stale_at
    assert RunRepository(session).reap_stale(stale_after_seconds=300) == 0
    session.refresh(run)
    assert run.heartbeat_at == stale_at
    plan_row = session.get(RunPlan, plan.id)
    assert plan_row is not None
    assert plan_row.status == "started"
    steps = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).all()
    assert {step.status for step in steps} == {RunPlanStepStatus.PENDING}
    tracker_snapshot = TrackerRepository(session).get(project_id=project_id, run_plan_id=plan.id)
    assert tracker_snapshot.tasks[0].status == "in-progress"
    assert {ticket.status for ticket in tracker_snapshot.tickets} == {"not-started"}


def test_reap_stale_preserves_started_run_plan_with_recent_activity(
    session: Session,
    project_id: int,
) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "recent-plan.workflow.run",
                "title": "Recent Plan Workflow",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data
    run = session.get(Run, started.run_id)
    plan_row = session.get(RunPlan, plan.id)
    assert run is not None and plan_row is not None
    stale_at = _now() - timedelta(minutes=10)
    run.heartbeat_at = stale_at
    plan_row.updated_at = _now()
    session.add(run)
    session.add(plan_row)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    assert reaped == 0
    run_out = RunRepository(session).get(started.run_id)
    assert run_out.status == RunStatus.RUNNING
    assert run_out.heartbeat_at == stale_at


def test_reap_stale_preserves_started_run_plan_with_blocked_step(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "blocked-plan.workflow.run",
            "title": "Blocked Plan Workflow",
            "steps": [{"id": "graph-check", "title": "Graph Check"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="graph-check")
    repo.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="graph-check",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"blocking_issue": "tracker graph warnings"},
        error="tracker-graph-warning",
    )

    run = session.get(Run, started.run_id)
    assert run is not None
    stale_at = _now() - timedelta(minutes=10)
    run.heartbeat_at = stale_at
    session.add(run)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    assert reaped == 0
    run = session.get(Run, started.run_id)
    assert run is not None
    assert run.status == RunStatus.RUNNING
    assert run.error is None
    assert run.heartbeat_at == stale_at
    assert RunRepository(session).reap_stale(stale_after_seconds=300) == 0
    session.refresh(run)
    assert run.heartbeat_at == stale_at
    recovered_plan = repo.get(plan.id)
    assert recovered_plan.status == "started"
    assert recovered_plan.steps[0].status == RunPlanStepStatus.BLOCKED
    tracker_snapshot = TrackerRepository(session).get(project_id=project_id, run_plan_id=plan.id)
    assert tracker_snapshot.tasks[0].status == "in-progress"
    assert tracker_snapshot.tickets[0].status == "in-progress"


@pytest.mark.parametrize("operation", ["claim", "record", "recover"])
def test_workflow_activity_refreshes_heartbeat_after_stale_sweep(
    session: Session, project_id: int, operation: str
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": f"heartbeat.{operation}.after-sweep",
            "title": "Workflow activity after maintenance",
            "steps": [{"id": "work", "title": "Work"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    if operation != "claim":
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="work")
    if operation == "recover":
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="work",
            status=RunPlanStepStatus.BLOCKED,
            result_json={"blocking_issue": "operator input needed"},
            error="operator input needed",
        )

    run = session.get(Run, started.run_id)
    assert run is not None
    stale_at = _now() - timedelta(minutes=10)
    run.heartbeat_at = stale_at
    session.add(run)
    session.commit()
    assert RunRepository(session).reap_stale(stale_after_seconds=300) == 0
    session.refresh(run)
    assert run.heartbeat_at == stale_at

    if operation == "claim":
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="work")
    elif operation == "record":
        repo.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="work",
            status=RunPlanStepStatus.SUCCESS,
            result_json={"summary": "completed"},
        )
    else:
        repo.recover(
            run_plan_id=plan.id,
            project_id=project_id,
            step_id="work",
            step_status=RunPlanStepStatus.PENDING,
            reason="operator input supplied",
        )
    session.refresh(run)
    assert run.heartbeat_at is not None
    assert run.heartbeat_at > stale_at


def test_reap_stale_parent_reconciles_child_run_plan(
    session: Session,
    project_id: int,
) -> None:
    parent = RunRepository(session).start(project_id=project_id, kind=RunKind.RUN_PLAN).data
    child_plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "child.workflow.run",
                "title": "Child Workflow",
                "steps": [{"id": "child-step", "title": "Child Step"}],
            },
        )
        .data
    )
    child_started = RunPlanRepository(session).start(child_plan.id, project_id=project_id).data
    parent_run = session.get(Run, parent.id)
    child_run = session.get(Run, child_started.run_id)
    assert parent_run is not None and child_run is not None
    parent_run.heartbeat_at = _now() - timedelta(minutes=10)
    child_run.parent_run_id = parent.id
    session.add(parent_run)
    session.add(child_run)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    assert reaped == 1
    child_plan_row = session.get(RunPlan, child_plan.id)
    child_run_row = session.get(Run, child_started.run_id)
    assert child_plan_row is not None and child_run_row is not None
    assert child_run_row.status == RunStatus.ABORTED
    assert child_plan_row.status == "aborted"
    assert child_plan_row.metadata_json is not None
    assert child_plan_row.metadata_json["abort_reason"] == "parent-aborted"


def test_reap_stale_historical_aborted_parent_reconciles_live_child_run_plan(
    session: Session,
    project_id: int,
) -> None:
    parent = RunRepository(session).start(project_id=project_id, kind=RunKind.RUN_PLAN).data
    child_plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "historical-child.workflow.run",
                "title": "Historical Child Workflow",
                "steps": [{"id": "child-step", "title": "Child Step"}],
            },
        )
        .data
    )
    child_started = RunPlanRepository(session).start(child_plan.id, project_id=project_id).data
    parent_run = session.get(Run, parent.id)
    child_run = session.get(Run, child_started.run_id)
    assert parent_run is not None and child_run is not None
    parent_run.status = RunStatus.ABORTED
    parent_run.error = "daemon-restart-orphan"
    parent_run.ended_at = _now() - timedelta(minutes=5)
    child_run.parent_run_id = parent.id
    child_run.heartbeat_at = _now()
    session.add(parent_run)
    session.add(child_run)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    child_plan_row = session.get(RunPlan, child_plan.id)
    child_run_row = session.get(Run, child_started.run_id)
    assert reaped == 0
    assert child_plan_row is not None and child_run_row is not None
    assert child_run_row.status == RunStatus.ABORTED
    assert child_run_row.error == "parent-aborted"
    assert child_plan_row.status == "aborted"
    assert child_plan_row.metadata_json is not None
    assert child_plan_row.metadata_json["abort_reason"] == "parent-aborted"


def test_reap_stale_ignores_historical_run_metadata_when_plan_has_new_run(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    plan = repo.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "recovered.workflow.run",
            "title": "Recovered Workflow",
            "steps": [{"id": "repair", "title": "Repair"}],
        },
    ).data
    started = repo.start(plan.id, project_id=project_id).data
    historical = (
        RunRepository(session)
        .start(
            project_id=project_id,
            kind=RunKind.RUN_PLAN,
            metadata_json={
                "stackos_type": "run-plan",
                "run_plan_id": plan.id,
                "template_key": "legacy",
            },
        )
        .data
    )
    historical_row = session.get(Run, historical.id)
    live_run = session.get(Run, started.run_id)
    assert historical_row is not None and live_run is not None
    historical_row.status = RunStatus.ABORTED
    historical_row.error = "daemon-restart-orphan"
    historical_row.ended_at = _now()
    live_run.heartbeat_at = _now()
    session.add(historical_row)
    session.add(live_run)
    session.commit()

    reaped = RunRepository(session).reap_stale(stale_after_seconds=300)

    plan_row = session.get(RunPlan, plan.id)
    live_run_row = session.get(Run, started.run_id)
    assert reaped == 0
    assert plan_row is not None and live_run_row is not None
    assert plan_row.run_id == started.run_id
    assert plan_row.status == RunPlanStatus.STARTED
    assert live_run_row.status == RunStatus.RUNNING
    assert live_run_row.error is None


def test_finish_rejects_live_run_plan_audit_run(session: Session, project_id: int) -> None:
    plan = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "finish-live.workflow.run",
                "title": "Finish Live Workflow",
                "steps": [{"id": "write", "title": "Write"}],
            },
        )
        .data
    )
    started = RunPlanRepository(session).start(plan.id, project_id=project_id).data

    with pytest.raises(ConflictError) as exc_info:
        RunRepository(session).finish(started.run_id, status="success")

    assert exc_info.value.data["run_plan_id"] == plan.id
    assert exc_info.value.data["run_plan_status"] == "started"
    assert "runPlan.recordStep" in exc_info.value.data["next_operations"]
    assert session.get(Run, started.run_id).status == RunStatus.RUNNING
    assert session.get(RunPlan, plan.id).status == "started"


def test_cost_aggregation(session: Session, project_id: int) -> None:
    """Cost rollup sums run_steps.cost_cents per kind for a month."""
    from stackos.db.models import RunStep, RunStepStatus

    repo = RunRepository(session)
    run_a = repo.start(project_id=project_id, kind=RunKind.SKILL_RUN).data
    run_b = repo.start(project_id=project_id, kind=RunKind.ACTION).data

    # Insert run_steps with cost.
    step1 = RunStep(
        run_id=run_a.id,
        step_index=0,
        skill_name="outline",
        status=RunStepStatus.SUCCESS,
        cost_cents=120,
    )
    step2 = RunStep(
        run_id=run_b.id,
        step_index=0,
        skill_name="action",
        status=RunStepStatus.SUCCESS,
        cost_cents=45,
    )
    session.add(step1)
    session.add(step2)
    session.commit()

    month = _now().strftime("%Y-%m")
    cost = repo.cost(project_id, month=month)
    assert cost["total_cents"] == 165
    assert cost["by_kind_cents"][RunKind.SKILL_RUN.value] == 120
    assert cost["by_kind_cents"][RunKind.ACTION.value] == 45
