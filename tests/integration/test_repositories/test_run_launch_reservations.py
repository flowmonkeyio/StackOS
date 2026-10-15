"""Private repository proof on disposable SQLite; no worker or host is launched."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import Barrier, local

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from stackos.db.models import Project, ProjectEvent, Run, RunKind, RunStatus, TrackerTask
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository

SCOPE = "a" * 64
MANIFEST = "b" * 64
RECEIPT = "c" * 64


@dataclass
class Case:
    engine: object
    project_id: int
    item: str
    controller: int

    def repo(self, session, guard=lambda: None):
        return RunRepository(session, audit_run_id=self.controller, owner_commit_guard=guard)

    def args(self, repo, **changes):
        owner = repo.owner_get(project_id=self.project_id, canonical_item_id=self.item)
        return (
            dict(
                project_id=self.project_id,
                canonical_item_id=self.item,
                owner_ref=owner.owner_ref,
                scope_digest=SCOPE,
                expected_epoch=owner.epoch,
                expected_revision=owner.revision,
                controller_run_id=self.controller,
                intent_ref="fixture:one-launch",
                manifest_digest=MANIFEST,
                evidence_ref="fixture:admission",
            )
            | changes
        )


@pytest.fixture
def case(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'launch.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(slug="fixture", name="Fixture", domain="example.test", locale="en-US")
        session.add(project)
        session.commit()
        project_id = project.id
        assert project_id is not None
        TrackerRepository(session).create_task(project_id=project_id, key="work", title="Fixture")
        task = session.exec(select(TrackerTask)).one()
        task.metadata_json = {
            "ownership_continuity": {
                "scope_digest": SCOPE,
                "budget_ref": "fixture:budget",
                "stop_scope_ref": "fixture:stop",
                "history_ref": "fixture:history",
                "stopped": False,
                "denied": False,
                "attempts_used": 0,
                "failures_used": 0,
                "attempt_limit": 2,
                "failure_limit": 2,
            }
        }
        session.add(task)
        session.commit()
        item = f"task:{task.id}"
        repo = RunRepository(session)
        repo.owner_acquire(
            project_id=project_id,
            canonical_item_id=item,
            owner_ref="fixture:lead",
            scope_digest=SCOPE,
        )
        controller = repo.start(project_id=project_id, kind=RunKind.RUN_PLAN).data.id
    yield Case(engine, project_id, item, controller)
    engine.dispose()


def reserve(case, session, **changes):
    repo = case.repo(session)
    return repo._reserve_owner_launch(**case.args(repo, **changes))


def receipt(case, session, attempt_id, **changes):
    repo = case.repo(session)
    args = case.args(repo, evidence_ref="fixture:receipt", **changes)
    return repo._record_owner_launch_receipt(
        **args, attempt_run_id=attempt_id, receipt_digest=RECEIPT
    )


def snapshot(case, session):
    session.expire_all()
    return {
        "owner": case.repo(session)
        .owner_get(project_id=case.project_id, canonical_item_id=case.item)
        .model_dump(),
        "runs": [r.model_dump() for r in session.exec(select(Run).order_by(Run.id)).all()],
        "events": [
            e.model_dump()
            for e in session.exec(select(ProjectEvent).order_by(ProjectEvent.id)).all()
        ],
    }


def test_one_reservation_charge_and_child_survive_reopen(case):
    with Session(case.engine) as session:
        admitted = reserve(case, session)
        assert admitted.launch_permit
        assert admitted.ownership.continuity_json["attempts_used"] == 1
        assert admitted.ownership.quiescence_revision is None
        assert admitted.attempt.parent_run_id == case.controller
        assert admitted.attempt.metadata_json["ownership_binding"]["canonical_item_id"] == case.item
        before = snapshot(case, session)
    with Session(case.engine) as session:
        duplicate = reserve(case, session)
        assert not duplicate.launch_permit and duplicate.attempt.id == admitted.attempt.id
        assert snapshot(case, session) == before


def test_concurrent_duplicate_admission_has_at_most_one_permit_and_charge(case, monkeypatch):
    barrier = Barrier(2)
    thread_state = local()
    calculate = RunRepository._owner_consumption_values

    def synchronized(repo, **kwargs):
        value = calculate(repo, **kwargs)
        if not getattr(thread_state, "seen", False):
            thread_state.seen = True
            # Both callers calculate from the same pre-CAS revision before writing.
            barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(RunRepository, "_owner_consumption_values", synchronized)
    with Session(case.engine) as session:
        args = case.args(case.repo(session))

    def contender():
        with Session(case.engine) as session:
            try:
                return case.repo(session)._reserve_owner_launch(**args).launch_permit
            except ConflictError:
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: contender(), range(2)))
    assert results.count(True) == 1
    with Session(case.engine) as session:
        state = snapshot(case, session)
        assert state["owner"]["continuity_json"]["attempts_used"] == 1
        assert len(state["runs"]) == 2
        assert (
            sum(e["event_type"] == "WORK_ITEM_OWNER_LAUNCH_RESERVED" for e in state["events"]) == 1
        )


@pytest.mark.parametrize(
    "change",
    [
        {"manifest_digest": "d" * 64},
        {"controller_run_id": 999},
        {"scope_digest": "d" * 64},
        {"owner_ref": "fixture:other"},
        {"expected_epoch": 2},
        {"expected_revision": 1},
    ],
)
def test_conflicting_reservation_never_adds_permit_charge_or_run(case, change):
    with Session(case.engine) as session:
        reserve(case, session)
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            reserve(case, session, **change)
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "field,value", [("stopped", True), ("denied", True), ("attempts_used", 2), ("failures_used", 2)]
)
def test_current_canonical_stop_or_exhaustion_blocks_reservation(case, field, value):
    with Session(case.engine) as session:
        task = session.get(TrackerTask, int(case.item.split(":")[1]))
        metadata = deepcopy(task.metadata_json)
        metadata["ownership_continuity"][field] = value
        task.metadata_json = metadata
        session.add(task)
        session.commit()
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            reserve(case, session)
        assert snapshot(case, session) == before


def test_valid_receipt_replay_is_idempotent_and_never_certifies(case):
    with Session(case.engine) as session:
        admitted = reserve(case, session)
        recorded = receipt(case, session, admitted.attempt.id)
        assert recorded.recorded
        assert recorded.attempt.status == RunStatus.RUNNING
        assert recorded.attempt.ended_at is None
        assert recorded.ownership.quiescence_revision is None
        before = snapshot(case, session)
        assert not receipt(case, session, admitted.attempt.id).recorded
        assert snapshot(case, session) == before


@pytest.mark.parametrize("status", [RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.ABORTED])
def test_terminal_receipt_rejected_even_after_valid_receipt_without_rewriting_history(case, status):
    with Session(case.engine) as session:
        admitted = reserve(case, session)
        receipt(case, session, admitted.attempt.id)
        RunRepository(session).finish(
            admitted.attempt.id, status=status, error="fixture:original-terminal-reason"
        )
        before = snapshot(case, session)
        with pytest.raises(ConflictError, match="terminal"):
            receipt(case, session, admitted.attempt.id)
        assert snapshot(case, session) == before


def test_stale_epoch_receipt_and_stable_intent_cannot_relaunch_after_reassignment(case):
    with Session(case.engine) as session:
        admitted = reserve(case, session)
        repo = case.repo(session)
        fence = repo._certify_owner_quiescence(
            project_id=case.project_id,
            canonical_item_id=case.item,
            owner_ref="fixture:lead",
            expected_epoch=admitted.ownership.epoch,
            expected_revision=admitted.ownership.revision,
            evidence_ref="fixture:synthetic-only-certificate",
        )
        repo.owner_reassign(
            project_id=case.project_id,
            canonical_item_id=case.item,
            owner_ref="fixture:lead",
            new_owner_ref="fixture:replacement",
            scope_digest=SCOPE,
            expected_epoch=fence.epoch,
            expected_revision=fence.revision,
        )
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            receipt(case, session, admitted.attempt.id)
        with pytest.raises(ConflictError):
            reserve(case, session)
        assert snapshot(case, session) == before


@pytest.mark.parametrize("operation", ["reservation", "receipt"])
def test_commit_guard_failure_rolls_back_every_staged_write(case, operation):
    with Session(case.engine) as session:
        attempt_id = reserve(case, session).attempt.id if operation == "receipt" else None
        before = snapshot(case, session)

        def deny():
            raise ConflictError("fixture:authority-revoked-at-commit")

        repo = case.repo(session, deny)
        with pytest.raises(ConflictError, match="authority-revoked"):
            if operation == "reservation":
                repo._reserve_owner_launch(**case.args(repo))
            else:
                repo._record_owner_launch_receipt(
                    **case.args(repo), attempt_run_id=attempt_id, receipt_digest=RECEIPT
                )
        assert snapshot(case, session) == before


@pytest.mark.parametrize("key", ["launch_reservation", "launch_receipt"])
def test_generic_run_metadata_cannot_forge_private_launch_state(case, key):
    with Session(case.engine) as session:
        repo = RunRepository(session)
        with pytest.raises(ValidationError):
            repo.start(
                project_id=case.project_id,
                kind=RunKind.SKILL_RUN,
                metadata_json={key: {"forged": True}},
            )
        with pytest.raises(ValidationError):
            repo.finish(
                case.controller, status=RunStatus.SUCCESS, metadata_json={key: {"forged": True}}
            )


def test_canonical_metadata_cannot_seed_the_private_reservation_ledger(case):
    with Session(case.engine) as session:
        task = session.get(TrackerTask, int(case.item.split(":")[1]))
        metadata = deepcopy(task.metadata_json)
        metadata["ownership_continuity"]["launch_reservations"] = {"forged": {"attempt_run_id": 1}}
        task.metadata_json = metadata
        session.add(task)
        session.commit()
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            reserve(case, session)
        assert snapshot(case, session) == before


def test_private_admission_requires_trusted_guard_and_current_controller(case):
    with Session(case.engine) as session:
        repo = RunRepository(session, audit_run_id=case.controller)
        with pytest.raises(ConflictError):
            repo._reserve_owner_launch(**case.args(repo))
        RunRepository(session).finish(case.controller, status=RunStatus.SUCCESS)
        with pytest.raises(ConflictError):
            reserve(case, session)


def test_revoked_owner_cannot_obtain_a_new_permit_or_charge(case):
    with Session(case.engine) as session:
        admitted = reserve(case, session)
        assert admitted.launch_permit
        assert admitted.ownership.continuity_json["attempts_used"] == 1
        repo = case.repo(session)
        fence = repo._certify_owner_quiescence(
            project_id=case.project_id,
            canonical_item_id=case.item,
            owner_ref="fixture:lead",
            expected_epoch=admitted.ownership.epoch,
            expected_revision=admitted.ownership.revision,
            evidence_ref="fixture:synthetic-revocation-precondition",
        )
        revoked = repo.owner_reassign(
            project_id=case.project_id,
            canonical_item_id=case.item,
            owner_ref="fixture:lead",
            new_owner_ref=None,
            scope_digest=SCOPE,
            expected_epoch=fence.epoch,
            expected_revision=fence.revision,
        )
        assert revoked.owner_ref is None
        before = snapshot(case, session)
        with pytest.raises((ValidationError, ConflictError), match="owner"):
            reserve(case, session, intent_ref="fixture:after-revocation")
        assert snapshot(case, session) == before


@pytest.mark.parametrize("operation", ["reservation", "receipt"])
def test_audit_failure_rolls_back_child_accounting_and_receipt(case, monkeypatch, operation):
    from stackos.repositories.project_events import StackOSEventEmitter

    with Session(case.engine) as session:
        attempt_id = reserve(case, session).attempt.id if operation == "receipt" else None
        before = snapshot(case, session)

        def fail(*args, **kwargs):
            raise RuntimeError("fixture:audit-unavailable")

        monkeypatch.setattr(StackOSEventEmitter, "emit", fail)
        with pytest.raises(RuntimeError, match="audit-unavailable"):
            if operation == "reservation":
                reserve(case, session)
            else:
                receipt(case, session, attempt_id)
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "field,value", [("stopped", True), ("attempts_used", 1), ("failures_used", 2)]
)
def test_canonical_authority_race_rolls_back_reservation(case, monkeypatch, field, value):
    calculate = RunRepository._owner_consumption_values
    changed = False

    def advance(repo, **kwargs):
        nonlocal changed
        result = calculate(repo, **kwargs)
        if not changed:
            changed = True
            with Session(case.engine) as other:
                task = other.get(TrackerTask, int(case.item.split(":")[1]))
                metadata = deepcopy(task.metadata_json)
                metadata["ownership_continuity"][field] = value
                task.metadata_json = metadata
                other.add(task)
                other.commit()
        return result

    monkeypatch.setattr(RunRepository, "_owner_consumption_values", advance)
    with Session(case.engine) as session:
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            reserve(case, session)
        assert snapshot(case, session) == before
        assert (
            session.get(TrackerTask, int(case.item.split(":")[1])).metadata_json[
                "ownership_continuity"
            ][field]
            == value
        )


def test_new_owner_cannot_copy_forged_reservations_from_canonical_metadata(case):
    with Session(case.engine) as session:
        tracker = TrackerRepository(session)
        tracker.create_task(project_id=case.project_id, key="forged", title="Fixture")
        source = session.get(TrackerTask, int(case.item.split(":")[1]))
        target = session.exec(select(TrackerTask).where(TrackerTask.key == "forged")).one()
        target.metadata_json = deepcopy(source.metadata_json)
        target.metadata_json["ownership_continuity"]["launch_reservations"] = {}
        session.add(target)
        session.commit()
        with pytest.raises(ValidationError, match="private run owner"):
            RunRepository(session).owner_acquire(
                project_id=case.project_id,
                canonical_item_id=f"task:{target.id}",
                owner_ref="fixture:lead",
                scope_digest=SCOPE,
            )
