"""Private repository proof on disposable SQLite; no worker or host is launched."""

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass, replace
from tempfile import gettempdir
from threading import Barrier, local

import pytest
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, SQLModel, create_engine, select

from stackos import controlled_process_receipt as controlled_receipt
from stackos.db.models import Project, ProjectEvent, Run, RunKind, RunStatus, TrackerTask
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_controlled_command_admission import case_factory as case_factory
from tests.integration.test_controlled_process_host import admitted as native_admitted
from tests.integration.test_controlled_process_host import prepare as native_prepare
from tests.integration.test_controlled_process_host import record as native_record
from tests.integration.test_controlled_process_host import registration as native_registration
from tests.integration.test_controlled_process_host import reserve as native_reserve
from tests.integration.test_controlled_process_host import snapshot as native_snapshot

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
    target_device = os.stat(gettempdir()).st_dev
    assert tmp_path.stat().st_dev == target_device
    engine = create_engine(
        f"sqlite:///{tmp_path / 'launch.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    SQLModel.metadata.create_all(engine)
    try:
        assert (tmp_path / "launch.db").stat().st_dev == target_device
    except BaseException:
        engine.dispose()
        raise
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


def _never_spawned_precondition(case_factory, tmp_path, monkeypatch):
    from stackos import controlled_process_clock as clock
    from stackos.crypto import aes_gcm
    from tests.integration.test_controlled_process_terminal import (
        certify_original,
        make_original,
        observation,
    )

    monkeypatch.setattr(clock, "_sample_clock", observation)
    seed = tmp_path / "original-test-seed"
    seed.write_bytes(b"X" * 32)
    seed.chmod(0o600)
    monkeypatch.setattr(aes_gcm, "_default_seed_path", seed)
    monkeypatch.setattr(aes_gcm, "_cache", {})
    case = case_factory()
    owner = make_original(case, tmp_path)
    certified = certify_original(owner)
    return case, owner, certified


@pytest.mark.parametrize(
    "corruption",
    [
        "revision-bool",
        "index-bool",
        "index-float",
        "sequence-bool",
        "kind",
        "origin",
        "extra-key",
        "payload-sequence-bool",
        "payload-clock-bool",
        "payload-extra-binding",
        "payload-missing-key",
        "material-payload",
    ],
)
def test_stored_terminal_fact_corruption_denies_replay_certification_and_consumption(
    case_factory, tmp_path, monkeypatch, corruption
):
    from stackos import controlled_process_host as host
    from tests.integration.test_controlled_process_terminal import (
        certify_original,
        database_snapshot,
    )

    case, owner, certified = _never_spawned_precondition(case_factory, tmp_path, monkeypatch)
    binding = owner.original.binding
    repo = RunRepository(case.session)
    row = case.session.get(Run, binding.attempt_run_id, populate_existing=True)
    metadata = deepcopy(row.metadata_json)
    history = metadata["native_terminal_facts"]
    entry = history["entries"][0]
    payload = entry["fact_payload"]
    if corruption == "revision-bool":
        history["revision"] = True
    elif corruption in {"index-bool", "index-float"}:
        entry["index"] = True if corruption == "index-bool" else 1.0
    elif corruption == "sequence-bool":
        entry["sequence"] = True
    elif corruption == "kind":
        entry["kind"] = "supplemental"
    elif corruption == "origin":
        entry["origin"] = "host-watchdog"
    elif corruption == "extra-key":
        entry["unknown"] = None
    elif corruption == "payload-sequence-bool":
        payload["sequence"] = True
    elif corruption == "payload-clock-bool":
        payload["clock"]["continuous_ns"] = True
    elif corruption == "payload-extra-binding":
        payload["binding"] = {"project_id": case.project}
    elif corruption == "payload-missing-key":
        del payload["policy_digest"]
    else:
        payload["policy_digest"] = "f" * 64
    row.metadata_json = metadata
    flag_modified(row, "metadata_json")  # bool/int equality must not mask the injected corruption
    case.session.add(row)
    case.session.commit()
    before = database_snapshot(case.session)
    with pytest.raises(ConflictError):
        repo._commit_terminal_receipt_slot(
            verified=host._terminal_verification(owner),
            expected_slot_revision=4,
            evidence_ref="native:not-spawned",
        )
    assert database_snapshot(case.session) == before
    if corruption == "material-payload":
        current = repo.owner_get(
            project_id=case.project, canonical_item_id=binding.canonical_item_id
        )
        changed = repo._native_quiescence_snapshot(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=current.owner_ref,
            expected_epoch=current.epoch,
            expected_revision=current.revision,
        )
        held = host._verify_native_quiescence(case.session, snapshot=changed, owners=(owner,))
        assert type(held) is host._TerminalHold
        assert held.issues == ("terminal_cleanup_unknown",)
        with pytest.raises(ConflictError, match="actual host quiescence verification required"):
            repo._certify_owner_quiescence(
                project_id=case.project,
                canonical_item_id=binding.canonical_item_id,
                owner_ref=current.owner_ref,
                expected_epoch=current.epoch,
                expected_revision=current.revision,
                evidence_ref="fixture:corrupt-fact",
                verified=held,
            )
    else:
        with pytest.raises(ConflictError):
            certify_original(owner)
    assert database_snapshot(case.session) == before
    with pytest.raises(ConflictError):
        repo.owner_reassign(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=certified.owner_ref,
            new_owner_ref="fixture:replacement",
            scope_digest=certified.scope_digest,
            expected_epoch=certified.epoch,
            expected_revision=certified.revision,
        )
    assert database_snapshot(case.session) == before
    assert owner.custody.close_accounted()


@pytest.mark.parametrize("fault", ["different-session", "stale-original-record"])
def test_terminal_commit_rejects_foreign_session_and_changed_original(
    case_factory, tmp_path, monkeypatch, fault
):
    from stackos import controlled_process_host as host
    from tests.integration.test_controlled_process_terminal import database_snapshot

    case, owner, _ = _never_spawned_precondition(case_factory, tmp_path, monkeypatch)
    binding = owner.original.binding
    if fault == "stale-original-record":
        row = case.session.get(Run, binding.attempt_run_id, populate_existing=True)
        metadata = deepcopy(row.metadata_json)
        metadata["native_recovery_private"]["policy"]["deadline_continuous_ns"] += 1
        row.metadata_json = metadata
        case.session.add(row)
        case.session.commit()
    before = database_snapshot(case.session)
    verified = host._terminal_verification(owner)
    if fault == "different-session":

        def forbidden_read(*args, **kwargs):
            raise AssertionError("verification read occurred before session rejection")

        with Session(case.session.get_bind()) as other, monkeypatch.context() as local:
            local.setattr(case.session, "get", forbidden_read)
            with pytest.raises(ConflictError, match="another session"):
                RunRepository(other)._commit_terminal_receipt_slot(
                    verified=verified, expected_slot_revision=4, evidence_ref="native:not-spawned"
                )
    else:
        with pytest.raises(host._ControlError, match="terminal_control_unknown"):
            RunRepository(case.session)._commit_terminal_receipt_slot(
                verified=verified, expected_slot_revision=4, evidence_ref="native:not-spawned"
            )
    assert database_snapshot(case.session) == before
    assert owner.custody.close_accounted()


def test_launch_accounting_preserves_private_certificate_while_invalidating_freshness(case):
    from stackos.db.models import WorkItemOwnership
    from tests.integration.test_controlled_process_terminal import certify_empty

    with Session(case.engine) as session:
        repo = case.repo(session)
        row = repo._owner_row(project_id=case.project_id, canonical_item_id=case.item)
        row.continuity_json = deepcopy(row.continuity_json) | {"effect_identity": "fixture:stable"}
        task = session.get(TrackerTask, int(case.item.split(":")[1]))
        task.metadata_json = deepcopy(task.metadata_json)
        task.metadata_json["ownership_continuity"]["effect_identity"] = "fixture:stable"
        session.add_all([row, task])
        session.commit()
        certified = certify_empty(
            repo, repo.owner_get(project_id=case.project_id, canonical_item_id=case.item)
        )
        raw = session.get(WorkItemOwnership, certified.id, populate_existing=True)
        certificate = deepcopy(raw.continuity_json["native_quiescence_certificate"])
        reserved = repo._reserve_owner_launch(**case.args(repo))
        raw = session.get(WorkItemOwnership, certified.id, populate_existing=True)
        assert raw.continuity_json["native_quiescence_certificate"] == certificate
        assert raw.quiescence_revision is None and raw.revision == certified.revision + 1
        assert "native_quiescence_certificate" not in reserved.ownership.continuity_json
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            repo.owner_reassign(
                project_id=case.project_id,
                canonical_item_id=case.item,
                owner_ref=reserved.ownership.owner_ref,
                new_owner_ref="fixture:replacement",
                scope_digest=SCOPE,
                expected_epoch=reserved.ownership.epoch,
                expected_revision=reserved.ownership.revision,
            )
        assert snapshot(case, session) == before


def test_stale_epoch_receipt_and_stable_intent_cannot_relaunch_after_reassignment(
    case_factory, tmp_path, monkeypatch
):
    from tests.integration.test_controlled_process_terminal import database_snapshot

    case, original, fence = _never_spawned_precondition(case_factory, tmp_path, monkeypatch)
    binding = original.original.binding
    repo = RunRepository(case.session, audit_run_id=case.run, owner_commit_guard=lambda: None)
    try:
        replacement = repo.owner_reassign(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=binding.owner_ref,
            new_owner_ref="fixture:replacement",
            scope_digest=SCOPE,
            expected_epoch=fence.epoch,
            expected_revision=fence.revision,
        )
        args = dict(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=replacement.owner_ref,
            scope_digest=SCOPE,
            expected_epoch=replacement.epoch,
            expected_revision=replacement.revision,
            controller_run_id=case.run,
            intent_ref=binding.intent_ref,
            manifest_digest=binding.manifest_digest,
            evidence_ref="fixture:forbidden-relaunch",
        )
        before = database_snapshot(case.session)
        with pytest.raises(ConflictError):
            repo._record_owner_launch_receipt(
                **args, attempt_run_id=binding.attempt_run_id, receipt_digest=RECEIPT
            )
        with pytest.raises(ConflictError):
            repo._reserve_owner_launch(**args)
        assert database_snapshot(case.session) == before
    finally:
        original.custody.wait_accounted()


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


@pytest.mark.parametrize("key", ["launch_reservation", "launch_receipt", "receipt_slot"])
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


def test_revoked_owner_cannot_obtain_a_new_permit_or_charge(case_factory, tmp_path, monkeypatch):
    from tests.integration.test_controlled_process_terminal import database_snapshot

    case, original, fence = _never_spawned_precondition(case_factory, tmp_path, monkeypatch)
    binding = original.original.binding
    repo = RunRepository(case.session, audit_run_id=case.run, owner_commit_guard=lambda: None)
    try:
        assert fence.continuity_json["attempts_used"] == 1
        revoked = repo.owner_reassign(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=binding.owner_ref,
            new_owner_ref=None,
            scope_digest=SCOPE,
            expected_epoch=fence.epoch,
            expected_revision=fence.revision,
        )
        assert revoked.owner_ref is None
        before = database_snapshot(case.session)
        with pytest.raises((ValidationError, ConflictError), match="owner"):
            repo._reserve_owner_launch(
                project_id=case.project,
                canonical_item_id=binding.canonical_item_id,
                owner_ref=revoked.owner_ref,
                scope_digest=SCOPE,
                expected_epoch=revoked.epoch,
                expected_revision=revoked.revision,
                controller_run_id=case.run,
                intent_ref="fixture:after-revocation",
                manifest_digest=binding.manifest_digest,
                evidence_ref="fixture:forbidden",
            )
        assert database_snapshot(case.session) == before
    finally:
        original.custody.wait_accounted()


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


# These component guards record/reject state transitions on disposable SQLite.
# They are deliberately NOT current-step grants or a production host adapter.
class SlotGuard:
    def __init__(self):
        self.calls = 0
        self.deny_at = None

    def __call__(self):
        self.calls += 1
        if self.deny_at is not None and self.calls >= self.deny_at:
            raise ConflictError("fixture:slot-authority-rejected")


def slot_setup(case, session, phase="ALLOCATION_PENDING"):
    guard = SlotGuard()
    repo = case.repo(session, guard)
    launch = repo._reserve_owner_launch(**case.args(repo))
    binding = controlled_receipt._Binding(
        project_id=case.project_id,
        canonical_item_id=case.item,
        owner_ref="fixture:lead",
        epoch=launch.ownership.epoch,
        reservation_revision=launch.ownership.revision,
        scope_digest=SCOPE,
        controller_run_id=case.controller,
        run_plan_id=1,
        run_plan_step_id=1,
        attempt_run_id=launch.attempt.id,
        intent_ref="fixture:one-launch",
        manifest_digest=MANIFEST,
        host_ref="fixture:synthetic-host",
        boot_uuid="00000000-0000-0000-0000-000000000001",
    )
    out = repo._reserve_receipt_slot(
        **slot_args(case, repo, launch.attempt.id, 0, "fixture:pending"), binding=binding
    )
    if phase != "ALLOCATION_PENDING":
        out = repo._record_receipt_slot_allocated(
            **slot_args(case, repo, out.attempt.id, out.slot["slot_revision"], "fixture:allocated"),
            dalloc=(1, 2, 123, 123, stat.S_IFDIR | 0o700, 2),
            fempty=(1, 3, 123, 123, stat.S_IFREG | 0o600, 1, 0, 100, 200),
            status_flags=os.O_WRONLY,
            offset=0,
        )
    if phase == "CLAIMED":
        out = repo._claim_receipt_slot(
            **slot_args(case, repo, out.attempt.id, out.slot["slot_revision"], "fixture:claimed"),
            sealed_digest=RECEIPT,
        )
    return repo, binding, out, guard


def slot_args(case, repo, attempt_id, slot_revision, evidence, **changes):
    return (
        case.args(repo, evidence_ref=evidence)
        | {"attempt_run_id": attempt_id, "expected_slot_revision": slot_revision}
        | changes
    )


def test_slot_one_shot_claim_and_atomic_commit(case):
    from stackos.repositories.runs import RunOut, WorkItemOwnershipOut

    with Session(case.engine) as session:
        repo, binding, pending, guard = slot_setup(case, session)
        before = snapshot(case, session)
        duplicate = repo._reserve_receipt_slot(
            **slot_args(case, repo, pending.attempt.id, 0, "fixture:pending"), binding=binding
        )
        assert not duplicate.applied and snapshot(case, session) == before
        assert type(duplicate.ownership) is WorkItemOwnershipOut
        assert type(duplicate.attempt) is RunOut
        with pytest.raises(FrozenInstanceError):
            duplicate.applied = True
        allocated = repo._record_receipt_slot_allocated(
            **slot_args(case, repo, pending.attempt.id, 1, "fixture:allocated"),
            dalloc=(1, 2, 123, 123, stat.S_IFDIR | 0o700, 2),
            fempty=(1, 3, 123, 123, stat.S_IFREG | 0o600, 1, 0, 100, 200),
            status_flags=os.O_WRONLY,
            offset=0,
        )
        before = snapshot(case, session)
        again = repo._record_receipt_slot_allocated(
            **slot_args(case, repo, pending.attempt.id, 2, "fixture:allocated"),
            dalloc=tuple(allocated.slot["dalloc"]),
            fempty=tuple(allocated.slot["fempty"]),
            status_flags=os.O_WRONLY,
            offset=0,
        )
        assert not again.applied and snapshot(case, session) == before
        claimed = repo._claim_receipt_slot(
            **slot_args(case, repo, pending.attempt.id, 2, "fixture:claimed"),
            sealed_digest=RECEIPT,
        )
        before = snapshot(case, session)
        again = repo._claim_receipt_slot(
            **slot_args(case, repo, pending.attempt.id, 3, "fixture:claimed"),
            sealed_digest=RECEIPT,
        )
        assert not again.applied and snapshot(case, session) == before
        with pytest.raises(ConflictError):
            repo._reserve_receipt_slot(
                **slot_args(case, repo, pending.attempt.id, 0, "fixture:pending"), binding=binding
            )
        committed = repo._commit_receipt_slot(
            **slot_args(case, repo, pending.attempt.id, 3, "fixture:committed"),
            receipt_digest=RECEIPT,
        )
        assert committed.applied and committed.slot["phase"] == "COMMITTED"
        assert committed.slot["sealed_digest"] == claimed.slot["sealed_digest"] == RECEIPT
        assert committed.attempt.metadata_json["launch_receipt"] == {
            "digest": RECEIPT,
            "evidence_ref": "fixture:committed",
        }
        assert committed.ownership.continuity_json["attempts_used"] == 1
        before = snapshot(case, session)
        again = repo._commit_receipt_slot(
            **slot_args(case, repo, pending.attempt.id, 4, "fixture:committed"),
            receipt_digest=RECEIPT,
        )
        assert not again.applied and snapshot(case, session) == before
        with pytest.raises(ConflictError):
            repo._record_owner_launch_receipt(
                **case.args(repo), attempt_run_id=pending.attempt.id, receipt_digest=RECEIPT
            )
        assert snapshot(case, session) == before and guard.calls > 0


def test_slot_concurrent_claim_has_at_most_one_transition(case, monkeypatch):
    with Session(case.engine) as session:
        repo, _binding, allocated, _guard = slot_setup(case, session, "ALLOCATED")
        args = slot_args(case, repo, allocated.attempt.id, 2, "fixture:claim")
    original = RunRepository._slot_context
    barrier = Barrier(2)

    def aligned(repo, fence):
        result = original(repo, fence)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(RunRepository, "_slot_context", aligned)

    def contender():
        with Session(case.engine) as session:
            try:
                repo = case.repo(session, SlotGuard())
                return repo._claim_receipt_slot(**args, sealed_digest=RECEIPT).applied
            except ConflictError:
                return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: contender(), range(2)))
    assert results.count(True) == 1
    with Session(case.engine) as session:
        attempt = session.get(Run, allocated.attempt.id)
        assert attempt.metadata_json["receipt_slot"]["phase"] == "CLAIMED"
        assert attempt.metadata_json["receipt_slot"]["slot_revision"] == 3
        events = session.exec(select(ProjectEvent)).all()
        assert sum(e.event_type == "WORK_ITEM_OWNER_RECEIPT_SLOT_TRANSITIONED" for e in events) == 3


@pytest.mark.parametrize(
    "change",
    [
        {"project_id": 999},
        {"canonical_item_id": "task:999"},
        {"owner_ref": "fixture:other"},
        {"scope_digest": "d" * 64},
        {"expected_epoch": 2},
        {"expected_revision": 1},
        {"controller_run_id": 999},
        {"intent_ref": "fixture:other"},
        {"manifest_digest": "d" * 64},
        {"attempt_run_id": 999},
        {"expected_slot_revision": 1},
    ],
)
def test_slot_fences_and_protected_metadata_reject_forgery(case, change):
    with Session(case.engine) as session:
        repo, _binding, allocated, _guard = slot_setup(case, session, "ALLOCATED")
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            repo._claim_receipt_slot(
                **slot_args(case, repo, allocated.attempt.id, 2, "fixture:claim", **change),
                sealed_digest=RECEIPT,
            )
        assert snapshot(case, session) == before
        with pytest.raises(ValidationError):
            repo.finish(
                allocated.attempt.id,
                status=RunStatus.SUCCESS,
                metadata_json={"receipt_slot": {"forged": True}},
            )
        with pytest.raises(ConflictError):
            repo._record_owner_launch_receipt(
                **case.args(repo), attempt_run_id=allocated.attempt.id, receipt_digest=RECEIPT
            )
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", 999),
        ("canonical_item_id", "task:999"),
        ("owner_ref", "fixture:other"),
        ("epoch", 2),
        ("reservation_revision", 99),
        ("scope_digest", "d" * 64),
        ("controller_run_id", 999),
        ("run_plan_id", 99),
        ("run_plan_step_id", 99),
        ("attempt_run_id", 999),
        ("intent_ref", "fixture:other"),
        ("manifest_digest", "d" * 64),
        ("host_ref", "fixture:other"),
        ("boot_uuid", "00000000-0000-0000-0000-000000000002"),
    ],
)
def test_slot_reserve_readback_does_not_rebind_any_identity(case, field, value):
    with Session(case.engine) as session:
        repo, binding, pending, _guard = slot_setup(case, session)
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            repo._reserve_receipt_slot(
                **slot_args(case, repo, pending.attempt.id, 0, "fixture:pending"),
                binding=replace(binding, **{field: value}),
            )
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "window,phase,reason",
    [
        ("before-claim", "ALLOCATED", "context_lost"),
        ("after-claim", "CLAIMED", "context_lost"),
        ("partial-fill", "CLAIMED", "io_outcome_unconfirmed"),
        ("after-sync-before-close", "CLAIMED", "close_unconfirmed"),
        ("after-close-before-ack", "CLAIMED", "canonical_ack_unconfirmed"),
    ],
)
def test_slot_failure_windows_never_reissue_claim(case, window, phase, reason):
    with Session(case.engine) as session:
        repo, binding, out, _guard = slot_setup(case, session, phase)
        held = repo._hold_receipt_slot(
            **slot_args(case, repo, out.attempt.id, out.slot["slot_revision"], "fixture:" + window),
            unknown=True,
            fixed_reason=reason,
        )
        assert held.slot["phase"] == "UNKNOWN_HOLD" and held.slot["hold_from"] == phase
        assert held.slot["fempty"] == out.slot["fempty"]
        assert "launch_receipt" not in held.attempt.metadata_json
        attempt_id = out.attempt.id
    with Session(case.engine) as session:
        repo = case.repo(session, SlotGuard())
        before = snapshot(case, session)
        again = repo._hold_receipt_slot(
            **slot_args(case, repo, attempt_id, held.slot["slot_revision"], "fixture:" + window),
            unknown=True,
            fixed_reason=reason,
        )
        assert not again.applied and snapshot(case, session) == before
        with pytest.raises(ConflictError):
            repo._claim_receipt_slot(
                **slot_args(case, repo, attempt_id, held.slot["slot_revision"], "fixture:new"),
                sealed_digest=RECEIPT,
            )
        with pytest.raises(ConflictError):
            repo._reserve_receipt_slot(
                **slot_args(case, repo, attempt_id, 0, "fixture:pending"), binding=binding
            )
        assert snapshot(case, session) == before


@pytest.mark.parametrize("operation", ["reserve", "allocated", "claim", "commit", "hold"])
@pytest.mark.parametrize("fault", ["guard", "audit"])
def test_slot_commit_guard_or_audit_failure_rolls_back_atomically(
    case, monkeypatch, operation, fault
):
    from stackos.repositories.project_events import StackOSEventEmitter

    with Session(case.engine) as session:
        phase = (
            "CLAIMED"
            if operation == "commit"
            else ("ALLOCATED" if operation in {"claim", "hold"} else "ALLOCATION_PENDING")
        )
        repo, binding, out, guard = slot_setup(case, session, phase)
        if operation == "reserve":
            # Use a second stable intent; the old claim/history is untouched.
            launch = repo._reserve_owner_launch(**case.args(repo, intent_ref="fixture:second"))
            binding = replace(
                binding,
                attempt_run_id=launch.attempt.id,
                intent_ref="fixture:second",
                reservation_revision=launch.ownership.revision,
            )
            attempt_id, revision = launch.attempt.id, 0
        else:
            attempt_id, revision = out.attempt.id, out.slot["slot_revision"]
        before = snapshot(case, session)
        args = slot_args(case, repo, attempt_id, revision, "fixture:failed-transition")
        if operation == "reserve":
            args["intent_ref"] = "fixture:second"
            # Existing two-attempt budget must leave room for this receipt scope.
            # Set a larger fixture limit in BOTH canonical and owner state first.
            task = session.get(TrackerTask, int(case.item.split(":")[1]))
            owner = repo._owner_row(project_id=case.project_id, canonical_item_id=case.item)
            task.metadata_json = deepcopy(task.metadata_json)
            task.metadata_json["ownership_continuity"]["attempt_limit"] = 3
            owner.continuity_json = deepcopy(owner.continuity_json) | {"attempt_limit": 3}
            session.add(task)
            session.add(owner)
            session.commit()
            before = snapshot(case, session)
        if fault == "guard":
            guard.deny_at = guard.calls + 2
        else:

            def fail(*args, **kwargs):
                raise RuntimeError("fixture:slot-audit-unavailable")

            monkeypatch.setattr(StackOSEventEmitter, "emit", fail)
        expected = ConflictError if fault == "guard" else RuntimeError
        with pytest.raises(expected):
            if operation == "reserve":
                repo._reserve_receipt_slot(**args, binding=binding)
            elif operation == "allocated":
                repo._record_receipt_slot_allocated(
                    **args,
                    dalloc=(1, 2, 123, 123, stat.S_IFDIR | 0o700, 2),
                    fempty=(1, 3, 123, 123, stat.S_IFREG | 0o600, 1, 0, 100, 200),
                    status_flags=os.O_WRONLY,
                    offset=0,
                )
            elif operation == "claim":
                repo._claim_receipt_slot(**args, sealed_digest=RECEIPT)
            elif operation == "commit":
                repo._commit_receipt_slot(**args, receipt_digest=RECEIPT)
            else:
                repo._hold_receipt_slot(**args, unknown=False, fixed_reason="io_failure")
        assert snapshot(case, session) == before


@pytest.mark.parametrize("mode", ["missing", "denying", "recording"])
def test_slot_component_guard_is_not_live_admission(case, mode):
    with Session(case.engine) as session:
        _repo, binding, pending, _guard = slot_setup(case, session)
        guard = SlotGuard()
        if mode == "denying":
            guard.deny_at = 1
        selected = (
            RunRepository(session, audit_run_id=case.controller)
            if mode == "missing"
            else case.repo(session, guard)
        )
        before = snapshot(case, session)
        if mode == "recording":
            out = selected._reserve_receipt_slot(
                **slot_args(case, selected, pending.attempt.id, 0, "fixture:pending"),
                binding=binding,
            )
            assert not out.applied and guard.calls == 1
        else:
            with pytest.raises(ConflictError):
                selected._reserve_receipt_slot(
                    **slot_args(case, selected, pending.attempt.id, 0, "fixture:pending"),
                    binding=binding,
                )
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"slot_revision": None},
        {"extra": 1},
        {"phase": None},
        {"name": None},
        {"binding_digest": "bad"},
        {"allocation_reservation_revision": True},
        {"evidence_ref": " "},
        {"dalloc": [1, 2]},
        {"fempty": [1] * 9},
        {"status_flags": True},
        {"offset": True},
        {"hold_from": "ALLOCATED"},
        {"sealed_digest": "d" * 64},
    ],
)
def test_slot_exact_record_schema_rejects_corruption(case, change):
    with Session(case.engine) as session:
        repo, _binding, out, _guard = slot_setup(case, session, "ALLOCATED")
        attempt = session.get(Run, out.attempt.id)
        altered = deepcopy(attempt.metadata_json)
        altered["receipt_slot"].update(change)
        attempt.metadata_json = altered
        flag_modified(attempt, "metadata_json")
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        for field, expected in change.items():
            actual = attempt.metadata_json["receipt_slot"][field]
            assert type(actual) is type(expected)
            assert actual == expected
        before = snapshot(case, session)
        with pytest.raises(ValidationError):
            repo._claim_receipt_slot(
                **slot_args(case, repo, out.attempt.id, 2, "fixture:claim"),
                sealed_digest=RECEIPT,
            )
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "state",
    [
        "ALLOCATION_PENDING",
        "ALLOCATED",
        "CLAIMED",
        "COMMITTED",
        "FAILED_HOLD:ALLOCATION_PENDING",
        "FAILED_HOLD:ALLOCATED",
        "FAILED_HOLD:CLAIMED",
        "UNKNOWN_HOLD:ALLOCATION_PENDING",
        "UNKNOWN_HOLD:ALLOCATED",
        "UNKNOWN_HOLD:CLAIMED",
    ],
)
@pytest.mark.parametrize("corruption", ["missing", "extra", "null"])
def test_slot_all_phase_keysets_are_exact(state, corruption):
    from stackos.repositories.runs import _parse_receipt_slot

    binding = controlled_receipt._Binding(
        1,
        "task:1",
        "fixture:lead",
        1,
        2,
        SCOPE,
        1,
        1,
        1,
        2,
        "fixture:one",
        MANIFEST,
        "fixture:synthetic-host",
        "00000000-0000-0000-0000-000000000001",
    )
    phase, _, origin = state.partition(":")
    record = {
        "schema_version": 1,
        "phase": phase,
        "slot_revision": 1,
        "binding": controlled_receipt._table(binding),
        "binding_digest": controlled_receipt._name(binding)[:-8],
        "name": controlled_receipt._name(binding),
        "allocation_reservation_revision": 2,
        "evidence_ref": "fixture:record",
    }
    if (origin or phase) in {"ALLOCATED", "CLAIMED", "COMMITTED"}:
        record.update(
            dalloc=[1, 2, 123, 123, stat.S_IFDIR | 0o700, 2],
            fempty=[1, 3, 123, 123, stat.S_IFREG | 0o600, 1, 0, 100, 200],
            status_flags=os.O_WRONLY,
            offset=0,
        )
    if (origin or phase) in {"CLAIMED", "COMMITTED"}:
        record["sealed_digest"] = RECEIPT
    if phase == "COMMITTED":
        record["receipt_digest"] = RECEIPT
    if origin:
        record.update(
            hold_from=origin,
            hold_reason="context_lost" if phase == "UNKNOWN_HOLD" else "io_failure",
        )
    assert _parse_receipt_slot(record) == record
    if corruption == "extra":
        with pytest.raises(ValidationError):
            _parse_receipt_slot(record | {"not_allowed": 0})
    else:
        for key in record:
            bad = deepcopy(record)
            if corruption == "missing":
                del bad[key]
            else:
                bad[key] = None
            with pytest.raises(ValidationError):
                _parse_receipt_slot(bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("stopped", True),
        ("denied", True),
        ("attempts_used", 2),
        ("failures_used", 2),
    ],
)
def test_slot_current_authority_blocks_claim_without_rewriting(case, field, value):
    with Session(case.engine) as session:
        repo, _binding, out, _guard = slot_setup(case, session, "ALLOCATED")
        task = session.get(TrackerTask, int(case.item.split(":")[1]))
        changed = deepcopy(task.metadata_json)
        changed["ownership_continuity"][field] = value
        task.metadata_json = changed
        session.add(task)
        session.commit()
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            repo._claim_receipt_slot(
                **slot_args(case, repo, out.attempt.id, 2, "fixture:blocked"),
                sealed_digest=RECEIPT,
            )
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("stopped", True),
        ("denied", True),
        ("attempts_used", 2),
        ("failures_used", 2),
    ],
)
def test_slot_canonical_stop_race_rolls_back(case, monkeypatch, field, value):
    with Session(case.engine) as session:
        repo, _binding, out, _guard = slot_setup(case, session, "ALLOCATED")
        args = slot_args(case, repo, out.attempt.id, 2, "fixture:race")
        original = RunRepository._slot_context

        def race(current_repo, fence):
            result = original(current_repo, fence)
            with Session(case.engine) as other:
                task = other.get(TrackerTask, int(case.item.split(":")[1]))
                changed = deepcopy(task.metadata_json)
                changed["ownership_continuity"][field] = value
                task.metadata_json = changed
                other.add(task)
                other.commit()
            return result

        monkeypatch.setattr(RunRepository, "_slot_context", race)
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            repo._claim_receipt_slot(**args, sealed_digest=RECEIPT)
        after = snapshot(case, session)
        assert after == before


@pytest.mark.parametrize("operation", ["reserve", "allocated", "claim", "commit", "hold"])
def test_slot_stale_duplicate_fences_are_never_relaxed(case, operation):
    with Session(case.engine) as session:
        repo, binding, out, _guard = slot_setup(case, session)
        if operation == "reserve":
            args = slot_args(case, repo, out.attempt.id, 0, "fixture:pending")
            args["expected_revision"] -= 1

            def call():
                return repo._reserve_receipt_slot(**args, binding=binding)
        elif operation == "allocated":
            args = slot_args(case, repo, out.attempt.id, 1, "fixture:allocated")
            repo._record_receipt_slot_allocated(
                **args,
                dalloc=(1, 2, 123, 123, stat.S_IFDIR | 0o700, 2),
                fempty=(1, 3, 123, 123, stat.S_IFREG | 0o600, 1, 0, 100, 200),
                status_flags=os.O_WRONLY,
                offset=0,
            )

            def call():
                return repo._record_receipt_slot_allocated(
                    **args,
                    dalloc=(1, 2, 123, 123, stat.S_IFDIR | 0o700, 2),
                    fempty=(1, 3, 123, 123, stat.S_IFREG | 0o600, 1, 0, 100, 200),
                    status_flags=os.O_WRONLY,
                    offset=0,
                )
        else:
            # Reuse the same canonical owner/attempt and advance it finitely.
            allocated = repo._record_receipt_slot_allocated(
                **slot_args(case, repo, out.attempt.id, 1, "fixture:allocated"),
                dalloc=(1, 2, 123, 123, stat.S_IFDIR | 0o700, 2),
                fempty=(1, 3, 123, 123, stat.S_IFREG | 0o600, 1, 0, 100, 200),
                status_flags=os.O_WRONLY,
                offset=0,
            )
            args = slot_args(case, repo, out.attempt.id, 2, "fixture:claimed")
            repo._claim_receipt_slot(**args, sealed_digest=RECEIPT)
            if operation == "claim":

                def call():
                    return repo._claim_receipt_slot(**args, sealed_digest=RECEIPT)
            else:
                args = slot_args(case, repo, out.attempt.id, 3, "fixture:terminal")
                if operation == "commit":
                    repo._commit_receipt_slot(**args, receipt_digest=RECEIPT)

                    def call():
                        return repo._commit_receipt_slot(**args, receipt_digest=RECEIPT)
                else:
                    repo._hold_receipt_slot(**args, unknown=False, fixed_reason="io_failure")

                    def call():
                        return repo._hold_receipt_slot(
                            **args, unknown=False, fixed_reason="io_failure"
                        )

            assert allocated.slot["slot_revision"] == 2
        before = snapshot(case, session)
        with pytest.raises(ConflictError):
            call()
        assert snapshot(case, session) == before


@pytest.mark.parametrize(
    "field,kind",
    [
        ("status_flags", "bool"),
        ("status_flags", "int-subclass"),
        ("offset", "bool"),
        ("offset", "int-subclass"),
        ("dalloc", "bool"),
        ("dalloc", "int-subclass"),
        ("dalloc", "tuple-subclass"),
        ("fempty", "bool"),
        ("fempty", "int-subclass"),
        ("fempty", "tuple-subclass"),
    ],
)
def test_slot_duplicate_allocation_rejects_nonexact_facts(case, field, kind):
    class IntegerSubclass(int):
        pass

    class TupleSubclass(tuple):
        pass

    with Session(case.engine) as session:
        repo, _binding, allocated, guard = slot_setup(case, session, "ALLOCATED")
        facts = {
            "dalloc": tuple(allocated.slot["dalloc"]),
            "fempty": tuple(allocated.slot["fempty"]),
            "status_flags": allocated.slot["status_flags"],
            "offset": allocated.slot["offset"],
        }
        if field in {"dalloc", "fempty"}:
            value = facts[field]
            if kind == "tuple-subclass":
                facts[field] = TupleSubclass(value)
            else:
                head = True if kind == "bool" else IntegerSubclass(value[0])
                facts[field] = (head, *value[1:])
        else:
            value = facts[field]
            facts[field] = bool(value) if kind == "bool" else IntegerSubclass(value)
        # Every supplied value compares equal to its valid stored counterpart.
        assert facts[field] == (
            tuple(allocated.slot[field]) if field in {"dalloc", "fempty"} else allocated.slot[field]
        )
        before = snapshot(case, session)
        calls_before = guard.calls
        with pytest.raises(ValidationError):
            repo._record_receipt_slot_allocated(
                **slot_args(case, repo, allocated.attempt.id, 2, "fixture:allocated"), **facts
            )
        assert guard.calls == calls_before + 1  # Fresh guard still precedes classification.
        assert snapshot(case, session) == before


@pytest.mark.parametrize("fault", ["stale-owner", "stale-control", "changed-manifest"])
def test_native_record_fences_reject_without_effect(case_factory, fault):
    candidate = case_factory()
    native_prepare(candidate)
    with native_admitted(candidate) as authority:
        repo, guard, cursor, binding = native_reserve(candidate, authority)
        cursor = native_record(
            repo, guard, cursor, "register", native_registration(authority, binding)
        ).cursor
        if fault == "stale-owner":
            cursor = replace(
                cursor,
                ownership=cursor.ownership.model_copy(
                    update={"revision": cursor.ownership.revision - 1}
                ),
            )
        elif fault == "stale-control":
            cursor = replace(cursor, control_revision=cursor.control_revision - 1)
        else:
            cursor = replace(cursor, manifest_digest="f" * 64)
        before = native_snapshot(candidate)
        with pytest.raises(ConflictError):
            native_record(repo, guard, cursor, "register", native_registration(authority, binding))
        assert native_snapshot(candidate) == before


def test_native_duplicate_does_not_advance_revision_or_audit(case_factory):
    candidate = case_factory()
    native_prepare(candidate)
    with native_admitted(candidate) as authority:
        repo, guard, cursor, binding = native_reserve(candidate, authority)
        facts = native_registration(authority, binding)
        first = native_record(repo, guard, cursor, "register", facts)
        before = native_snapshot(candidate)
        second = native_record(repo, guard, first.cursor, "register", facts)
        assert second.applied is False and second.cursor == first.cursor
        assert native_snapshot(candidate) == before


@pytest.mark.parametrize("method", ["start", "finish"])
def test_generic_metadata_cannot_write_native_authority(case, method):
    with Session(case.engine) as session:
        repo = RunRepository(session)
        for key in ("native_control", "native_control_private"):
            before = snapshot(case, session)
            with pytest.raises(ValidationError):
                if method == "start":
                    repo.start(
                        project_id=case.project_id,
                        kind=RunKind.SKILL_RUN,
                        metadata_json={key: {"forged": True}},
                    )
                else:
                    repo.finish(
                        case.controller,
                        status=RunStatus.SUCCESS,
                        metadata_json={key: {"forged": True}},
                    )
            assert snapshot(case, session) == before


def test_native_restart_projection_cannot_recover_a_release_frame(case_factory):
    candidate = case_factory()
    native_prepare(candidate)
    with native_admitted(candidate) as authority:
        repo, guard, cursor, _binding = native_reserve(candidate, authority)
        held = native_record(
            repo, guard, cursor, "hold", {"reason": "ack_unknown", "outcome_unknown": True}
        )
        candidate.session.expire_all()
        # Re-read is durable uncertainty, never a synthesized release capability.
        row = candidate.session.get(Run, held.cursor.attempt.id)
        assert row.metadata_json["native_control"]["phase"] == "UNKNOWN_HOLD"
        assert "frame" not in row.metadata_json["native_control"]
        assert row.status == RunStatus.RUNNING


def test_native_cleanup_observation_is_not_certification(case_factory):
    candidate = case_factory()
    native_prepare(candidate)
    with native_admitted(candidate) as authority:
        repo, guard, cursor, binding = native_reserve(candidate, authority)
        cursor = native_record(
            repo, guard, cursor, "register", native_registration(authority, binding)
        ).cursor
        before = repo.owner_get(
            project_id=candidate.project, canonical_item_id=binding.canonical_item_id
        )
        result = native_record(
            repo, guard, cursor, "observe_cleanup", {"observation_digest": "a" * 64}
        )
        assert result.cursor.attempt.status == RunStatus.RUNNING
        assert "certificate" not in result.cursor.attempt.metadata_json
        assert (
            repo.owner_get(
                project_id=candidate.project, canonical_item_id=binding.canonical_item_id
            )
            == before
        )
