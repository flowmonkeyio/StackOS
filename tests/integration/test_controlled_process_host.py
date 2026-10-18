"""Real disposable authority/CAS integration; no process or native read."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from stackos import controlled_process_clock as clock
from stackos import controlled_process_host as host
from stackos import controlled_process_identity as identity
from stackos import controlled_process_receipt as receipt
from stackos.actions import controlled_command as fixed
from stackos.config import Settings
from stackos.crypto import aes_gcm
from stackos.db.models import ProjectEvent, Run, RunPlan, TrackerTask, WorkItemOwnership
from stackos.mcp.context import bind_context, build_context
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.actions.controlled import _issue_native_admission, _NativeCommitGuard
from stackos.operations.actions.schemas import ActionExecuteInput
from stackos.repositories.base import ConflictError, RepositoryError, ValidationError
from stackos.repositories.runs import (
    OwnershipAttemptOut,
    RunOut,
    RunRepository,
    _ControlCursor,
    _NativeTransition,
)
from tests.integration.test_controlled_command_admission import case_factory as case_factory

BOOT = "11111111-1111-4111-8111-111111111111"


def prepare(case, *, boot=BOOT, deadline=10_000_000_000, grace=1_000_000_000, attempt_limit=2):
    if case.settings is None:
        case.settings = Settings(
            _env_file=None,
            state_dir=case.source.parent / "native-state",
            data_dir=case.source.parent / "native-data",
            controlled_host_enabled=True,
        )
    case.session.info["operation_services"] = {
        "action_connectors": case.connectors,
        "settings": case.settings,
    }
    # Each disposable case owns its explicit seed; ambient crypto configuration
    # is intentionally independent and is never adjusted to make admission pass.
    case.settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not case.settings.seed_path.exists():
        case.settings.seed_path.write_bytes(b"C" * 32)
        case.settings.seed_path.chmod(0o600)
    command = fixed._resolve_controlled_command()
    runtime = host._native_manifest(command)
    decision = dict(
        schema_version="stackos.controlled-host-approval.v1",
        command_digest=command.digest,
        runtime_digest=host._digest(runtime),
        boot_uuid=boot,
        deadline_continuous_ns=deadline,
        term_grace_ns=grace,
        cleanup_deadline_continuous_ns=deadline + grace + 2_000_000_000,
        max_output_bytes=65536,
        member_limit=8,
        threat_model="cooperative-fixed-command",
    )
    policy = host._NativePolicy.from_decision(
        decision, command_digest=command.digest, runtime_digest=host._digest(runtime)
    )
    case.approval(change=lambda value: value.update(controlled_host=decision))
    task = case.session.get(TrackerTask, case.task)
    owner = case.session.exec(select(WorkItemOwnership)).one()
    value = deepcopy(task.metadata_json)
    value["ownership_continuity"].update(
        deadline_ref=policy.deadline_ref,
        attempt_limit=attempt_limit,
        effect_identity="effect:fixture:no-effect",
    )
    task.metadata_json = value
    owner.continuity_json = deepcopy(owner.continuity_json) | {
        "deadline_ref": policy.deadline_ref,
        "attempt_limit": attempt_limit,
        "effect_identity": "effect:fixture:no-effect",
    }
    case.session.add_all([task, owner])
    case.session.commit()
    return policy


@contextmanager
def admitted(case):
    case.session.info["operation_services"] = {
        "action_connectors": case.connectors,
        "settings": case.settings,
    }
    with bind_context(build_context(case.args, case.session)) as ctx:
        yield _issue_native_admission(ActionExecuteInput.model_validate(case.args), ctx)


def reserve(case, admission):
    guard = _NativeCommitGuard(admission)
    repo = RunRepository(case.session, audit_run_id=case.run, owner_commit_guard=guard)
    request = admission.original.request
    result = repo._reserve_owner_launch(
        project_id=case.project,
        canonical_item_id=request.canonical_item_id,
        owner_ref=request.owner_ref,
        scope_digest=request.scope_digest,
        expected_epoch=request.expected_epoch,
        expected_revision=guard.ownership.revision,
        controller_run_id=case.run,
        intent_ref=request.intent_ref,
        manifest_digest=admission.runtime_digest,
        evidence_ref="fixture:native-reserve",
    )
    guard.advance(result)
    binding = receipt._Binding(
        case.project,
        request.canonical_item_id,
        request.owner_ref,
        request.expected_epoch,
        result.ownership.revision,
        request.scope_digest,
        case.run,
        case.plan,
        case.step,
        result.attempt.id,
        request.intent_ref,
        admission.runtime_digest,
        "fixture:host",
        admission.policy.boot_uuid,
    )
    cursor = _ControlCursor(
        result.ownership, result.attempt, 0, receipt._name(binding)[:-8], admission.runtime_digest
    )
    return repo, guard, cursor, binding


def registration(admission, binding):
    boot = binding.boot_uuid
    processes = {
        "watchdog": identity._ProcessIdentity(boot, 100, 1, 100, 100, 1000, 1),
        "anchor": identity._ProcessIdentity(boot, 101, 100, 101, 100, 1000, 2),
        "worker": identity._ProcessIdentity(boot, 102, 100, 101, 100, 1000, 3),
    }
    return {
        "binding": asdict(binding),
        "manifest": host._native_manifest(admission.original.command),
        "wrapped_key": {"ciphertext": "ab" * 48, "nonce": "cd" * 12},
        "identities": {k: asdict(v) for k, v in processes.items()},
    }


def record(repo, guard, cursor, kind, facts):
    result = repo._record_native_control(
        cursor=cursor,
        expected_control_revision=cursor.control_revision,
        transition=_NativeTransition(kind, facts),
        evidence_ref="fixture:" + kind,
    )
    if kind in {"register", "arm", "release"} and result.applied:
        guard.advance(result)
    return result


def commit_slot(repo, guard, cursor, binding):
    def fence(revision):
        return dict(
            project_id=binding.project_id,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=binding.owner_ref,
            scope_digest=binding.scope_digest,
            expected_epoch=binding.epoch,
            expected_revision=guard.ownership.revision,
            controller_run_id=binding.controller_run_id,
            intent_ref=binding.intent_ref,
            manifest_digest=binding.manifest_digest,
            attempt_run_id=binding.attempt_run_id,
            expected_slot_revision=revision,
            evidence_ref="fixture:slot",
        )

    operations = (
        lambda: repo._reserve_receipt_slot(**fence(0), binding=binding),
        lambda: repo._record_receipt_slot_allocated(
            **fence(1),
            dalloc=(1, 10, os.geteuid(), os.getegid(), 0o40700, 2),
            fempty=(1, 11, os.geteuid(), os.getegid(), 0o100600, 1, 0, 1, 1),
            status_flags=os.O_WRONLY,
            offset=0,
        ),
        lambda: repo._claim_receipt_slot(**fence(2), sealed_digest="b" * 64),
        lambda: repo._commit_receipt_slot(**fence(3), receipt_digest="b" * 64),
    )
    for operation in operations:
        result = operation()
        guard.advance(result)
        cursor = replace(cursor, ownership=result.ownership, attempt=result.attempt)
    return cursor


def snapshot(case):
    case.session.expire_all()
    return {
        model.__name__: [
            row.model_dump(mode="json")
            for row in case.session.exec(select(model).order_by(model.id))
        ]
        for model in (Run, WorkItemOwnership, ProjectEvent)
    }


def lose_grant(case):
    plan = case.session.get(RunPlan, case.plan)
    value = deepcopy(plan.grant_snapshot_json)
    value["mcp_tool_grants"] = []
    plan.grant_snapshot_json = value
    case.session.add(plan)
    case.session.commit()


@pytest.fixture(autouse=True)
def no_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("portable case must not launch or probe native")

    monkeypatch.setattr(host.subprocess, "Popen", forbidden)
    monkeypatch.setattr(identity, "_load_native", forbidden)
    monkeypatch.setattr(host.clock, "_load_native", forbidden)


@pytest.mark.parametrize("fault", ["wrong-token", "grant-drift", "approval-drift", "owner-stop"])
def test_native_admission_requires_actual_current_authority(case_factory, fault):
    case = case_factory()
    prepare(case)
    if fault == "wrong-token":
        case.args["run_token"] = "not-a-token"
    elif fault == "grant-drift":
        lose_grant(case)
    elif fault == "approval-drift":
        case.approval(change=lambda value: value["controlled_host"].update(runtime_digest="0" * 64))
    else:
        case.stop()
    before = snapshot(case)
    with pytest.raises((RepositoryError, ToolNotGrantedError, host._ControlError)), admitted(case):
        pytest.fail("invalid native authority admitted")
    assert snapshot(case) == before


@pytest.mark.parametrize(
    "fault", ["legitimate-reserve", "stale-owner-revision", "foreign-transition"]
)
def test_native_cursor_only_tracks_own_acknowledged_cas(case_factory, fault):
    case = case_factory()
    prepare(case, attempt_limit=1)
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        assert guard.ownership.continuity_json["attempts_used"] == 1
        if fault == "legitimate-reserve":
            # Already charged exactly once is permitted to fill its own slot,
            # despite attempt_limit=1. No unreserved new attempt is permitted.
            cursor = commit_slot(repo, guard, cursor, binding)
            assert cursor.attempt.metadata_json["receipt_slot"]["phase"] == "COMMITTED"
            task = case.session.get(TrackerTask, case.task)
            value = deepcopy(task.metadata_json)
            value["ownership_continuity"]["attempts_used"] = 1
            task.metadata_json = value
            case.session.add(task)
            case.session.commit()
            with pytest.raises(ConflictError), admitted(case):
                pytest.fail("exhausted unreserved admission accepted")
        elif fault == "stale-owner-revision":
            old = replace(
                cursor,
                ownership=cursor.ownership.model_copy(
                    update={"revision": cursor.ownership.revision - 1}
                ),
            )
            with pytest.raises(ConflictError):
                record(repo, guard, old, "register", registration(authority, binding))
        else:
            owner = case.session.get(WorkItemOwnership, cursor.ownership.id)
            owner.revision += 1
            case.session.add(owner)
            case.session.commit()
            with pytest.raises(ConflictError):
                guard()


@pytest.mark.parametrize("fault", ["duplicate", "concurrent", "slot-not-committed"])
def test_release_is_applied_once_and_requires_committed_arm(case_factory, fault):
    case = case_factory()
    prepare(case)
    if fault == "concurrent":
        # Independent real callers contend for the one reservation. A losing
        # caller cannot adopt the winner's acknowledgments to become a second
        # release producer. The actual release CAS/readback is exercised below.
        barrier = Barrier(2)

        def contender(_):
            with Session(case.engine) as session:
                session.info["operation_services"] = case.session.info["operation_services"]
                context = build_context(case.args, session)
                assert context.extras["settings"] is case.settings
                with bind_context(context):
                    authority = _issue_native_admission(
                        ActionExecuteInput.model_validate(case.args), context
                    )
                    guard = _NativeCommitGuard(authority)
                    repo = RunRepository(session, audit_run_id=case.run, owner_commit_guard=guard)
                    request = authority.original.request
                    barrier.wait(timeout=5)
                    try:
                        result = repo._reserve_owner_launch(
                            project_id=case.project,
                            canonical_item_id=request.canonical_item_id,
                            owner_ref=request.owner_ref,
                            scope_digest=request.scope_digest,
                            expected_epoch=request.expected_epoch,
                            expected_revision=guard.ownership.revision,
                            controller_run_id=case.run,
                            intent_ref=request.intent_ref,
                            manifest_digest=authority.runtime_digest,
                            evidence_ref="fixture:concurrent",
                        )
                        if result.launch_permit:
                            guard.advance(result)
                        return result.launch_permit
                    except (ConflictError, OperationalError):
                        session.rollback()
                        return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(contender, range(2)))
        assert outcomes.count(True) == 1
        case.session.expire_all()
        owner = case.session.exec(select(WorkItemOwnership)).one()
        assert owner.continuity_json["attempts_used"] == 1
        assert len(case.session.exec(select(Run).where(Run.parent_run_id == case.run)).all()) == 1
        return
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        cursor = record(repo, guard, cursor, "register", registration(authority, binding)).cursor
        if fault == "slot-not-committed":
            before = snapshot(case)
            with pytest.raises(ValidationError, match=r"^invalid private receipt slot record$"):
                record(repo, guard, cursor, "arm", {"arm_digest": "b" * 64})
            assert snapshot(case) == before
            reserved = repo._reserve_receipt_slot(
                project_id=binding.project_id,
                canonical_item_id=binding.canonical_item_id,
                owner_ref=binding.owner_ref,
                scope_digest=binding.scope_digest,
                expected_epoch=binding.epoch,
                expected_revision=guard.ownership.revision,
                controller_run_id=binding.controller_run_id,
                intent_ref=binding.intent_ref,
                manifest_digest=binding.manifest_digest,
                attempt_run_id=binding.attempt_run_id,
                expected_slot_revision=0,
                evidence_ref="fixture:slot",
                binding=binding,
            )
            assert reserved.applied is True and reserved.slot["phase"] == "ALLOCATION_PENDING"
            guard.advance(reserved)
            cursor = replace(cursor, ownership=reserved.ownership, attempt=reserved.attempt)
            before = snapshot(case)
            with pytest.raises(
                ConflictError, match=r"^native release requires its committed ARM slot$"
            ):
                record(repo, guard, cursor, "arm", {"arm_digest": "b" * 64})
            assert snapshot(case) == before
            return
        cursor = commit_slot(repo, guard, cursor, binding)
        cursor = record(repo, guard, cursor, "arm", {"arm_digest": "b" * 64}).cursor
        original = cursor
        facts = {"arm_digest": "b" * 64, "frame_digest": "c" * 64}
        first = record(repo, guard, cursor, "release", facts)
        assert first.applied is True
        before = snapshot(case)
        assert record(repo, guard, first.cursor, "release", facts).applied is False
        with pytest.raises(ConflictError):
            record(repo, guard, original, "release", facts)
        with pytest.raises(ConflictError):
            guard.advance(first)  # consumed own acknowledgment cannot replay
        assert snapshot(case) == before


@pytest.mark.parametrize("fault", ["lost-ack", "key-unavailable"])
def test_uncertainty_is_a_sticky_hold_without_relaunch(case_factory, fault):
    case = case_factory()
    prepare(case)
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        result = record(
            repo,
            guard,
            cursor,
            "hold",
            {
                "reason": "ack_unknown" if fault == "lost-ack" else "key_unavailable",
                "outcome_unknown": True,
            },
        )
        assert (
            result.applied
            and result.cursor.attempt.metadata_json["native_control"]["phase"] == "UNKNOWN_HOLD"
        )
        before = snapshot(case)
        with pytest.raises(ConflictError):
            record(repo, guard, result.cursor, "register", registration(authority, binding))
        assert snapshot(case) == before


def test_private_registration_and_key_are_backend_only(case_factory):
    case = case_factory()
    prepare(case)
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        result = record(repo, guard, cursor, "register", registration(authority, binding))
        row = case.session.get(Run, cursor.attempt.id)
        assert "native_control_private" in row.metadata_json
        public = RunOut.model_validate(row).metadata_json
        assert "native_control_private" not in public
        assert "registration" not in public["native_control"]
        assert result.cursor.attempt.metadata_json == public


def test_public_dispatch_remains_host_unavailable(case_factory):
    case = case_factory()
    prepare(case)
    # Exercise the public default-off contract, not prepare's private opt-in.
    case.settings.controlled_host_enabled = False
    output = asyncio.run(case.dispatch())
    assert output["data"]["output_json"]["code"] == "controlled_host_unavailable"
    assert output["data"]["output_json"]["host_dispatched"] is False


def test_native_guard_cannot_commit_its_callers_staged_cas(case_factory, monkeypatch):
    from stackos.actions import ActionRepository

    case = case_factory()
    prepare(case)
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        describe = ActionRepository.describe

        def committing_catalog(catalog, **kwargs):
            catalog._s.commit()
            return describe(catalog, **kwargs)

        monkeypatch.setattr(ActionRepository, "describe", committing_catalog)
        before = snapshot(case)
        with pytest.raises(ConflictError, match="cannot commit"):
            record(repo, guard, cursor, "register", registration(authority, binding))
        case.session.rollback()
        assert snapshot(case) == before


@pytest.mark.parametrize("foreign", ["checkpoint", "zero-consumption"])
def test_native_cursor_rejects_generic_fresh_reads_after_foreign_cas(case_factory, foreign):
    case = case_factory()
    prepare(case)
    with admitted(case) as authority:
        _repo, guard, cursor, binding = reserve(case, authority)
        other = RunRepository(case.session)
        args = dict(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=binding.owner_ref,
            scope_digest=binding.scope_digest,
            expected_epoch=binding.epoch,
            expected_revision=cursor.ownership.revision,
        )
        if foreign == "checkpoint":
            other.owner_checkpoint(**args, checkpoint_ref="fixture:foreign-checkpoint")
        else:
            other.owner_consume(
                **args,
                attempts_delta=0,
                failures_delta=0,
                evidence_ref="fixture:foreign-accounting",
            )
        fresh = other.owner_get(
            project_id=case.project, canonical_item_id=binding.canonical_item_id
        )
        attempt = RunOut.model_validate(case.session.get(Run, cursor.attempt.id))
        assert fresh.revision == cursor.ownership.revision + 1
        before = snapshot(case)
        with pytest.raises(ConflictError, match="exact own committed"):
            guard.advance(OwnershipAttemptOut(ownership=fresh, attempt=attempt))
        with pytest.raises(TypeError):
            guard.advance(fresh, attempt)  # retired generic-read signature
        assert snapshot(case) == before


def test_native_lost_own_ack_is_not_regenerated_by_readback(case_factory):
    case = case_factory()
    prepare(case)
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        actual = repo._record_native_control(
            cursor=cursor,
            expected_control_revision=0,
            transition=_NativeTransition("register", registration(authority, binding)),
            evidence_ref="fixture:register",
        )
        # A separately assembled object can contain byte-identical current
        # models, but is not the exact result retained by this guard.
        copied = replace(actual)
        before = snapshot(case)
        with pytest.raises(ConflictError):
            guard.advance(copied)
        assert snapshot(case) == before
        guard.advance(actual)
        with pytest.raises(ConflictError):
            guard.advance(actual)
        assert guard.ownership.revision == actual.cursor.ownership.revision


@pytest.mark.parametrize(
    "fault", ["register-grant-loss", "arm-approval-drift", "release-owner-stop"]
)
def test_forward_transition_rechecks_live_authority(case_factory, fault):
    case = case_factory()
    prepare(case)
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        if fault == "register-grant-loss":
            lose_grant(case)
            kind, facts = "register", registration(authority, binding)
        else:
            cursor = record(
                repo, guard, cursor, "register", registration(authority, binding)
            ).cursor
            cursor = commit_slot(repo, guard, cursor, binding)
            if fault == "arm-approval-drift":
                case.approval(change=lambda value: value.update(review_revision=2))
                kind, facts = "arm", {"arm_digest": "b" * 64}
            else:
                cursor = record(repo, guard, cursor, "arm", {"arm_digest": "b" * 64}).cursor
                case.stop()
                kind, facts = "release", {"arm_digest": "b" * 64, "frame_digest": "c" * 64}
        before = snapshot(case)
        with pytest.raises((RepositoryError, ToolNotGrantedError)):
            record(repo, guard, cursor, kind, facts)
        assert snapshot(case) == before


@pytest.mark.parametrize("loss", ["owner-stop-after-release", "grant-loss-after-release"])
def test_original_safety_lane_survives_authority_loss(case_factory, loss):
    case = case_factory()
    prepare(case)
    with admitted(case) as authority:
        repo, guard, cursor, binding = reserve(case, authority)
        cursor = record(repo, guard, cursor, "register", registration(authority, binding)).cursor
        cursor = commit_slot(repo, guard, cursor, binding)
        cursor = record(repo, guard, cursor, "arm", {"arm_digest": "b" * 64}).cursor
        cursor = record(
            repo, guard, cursor, "release", {"arm_digest": "b" * 64, "frame_digest": "c" * 64}
        ).cursor
        case.stop() if loss.startswith("owner") else lose_grant(case)
        owner_before = repo.owner_get(
            project_id=case.project, canonical_item_id=binding.canonical_item_id
        )
        cancelled = record(repo, guard, cursor, "cancel", {"reason": "owner_stop"})
        cleaned = record(
            repo, guard, cancelled.cursor, "observe_cleanup", {"observation_digest": "d" * 64}
        )
        assert cleaned.applied and cleaned.cursor.control_revision == cursor.control_revision + 2
        assert (
            repo.owner_get(project_id=case.project, canonical_item_id=binding.canonical_item_id)
            == owner_before
        )
        assert "certificate" not in cleaned.cursor.attempt.metadata_json


@pytest.mark.parametrize("fault", ["attach-error", "attach-interrupt", "helper-return-interrupt"])
def test_host_spawn_attachment_failure_retains_exact_child(
    case_factory, tmp_path, monkeypatch, fault
):
    case = case_factory()
    policy = prepare(case)
    expired = False
    child = SimpleNamespace(pid=900001, returncode=None)
    child.poll = lambda: child.returncode
    waits = []

    def wait():
        waits.append(child)
        child.returncode = 0
        return 0

    child.wait = wait

    def popen(*args, **kwargs):
        nonlocal expired
        expired = True
        return child

    def sample():
        now = policy.cleanup_deadline_continuous_ns if expired else 1
        return clock._ClockObservation("known", BOOT, now, now, runtime_abi=clock._SUPPORTED_ABI)

    state = tmp_path / "native-owned-state"
    state.mkdir(mode=0o700)
    monkeypatch.setattr(
        "stackos.config.get_settings",
        lambda: Settings(state_dir=state, data_dir=tmp_path / "native-data"),
    )
    # Supplied synthetic envelope only; all real owner/grant/approval checks run.
    monkeypatch.setattr(aes_gcm, "encrypt", lambda *args, **kwargs: (b"c" * 48, b"n" * 12))
    monkeypatch.setattr(clock, "_sample_clock", sample)
    monkeypatch.setattr(host.subprocess, "Popen", popen)
    failure = (
        ValueError("synthetic attach failure") if fault == "attach-error" else KeyboardInterrupt()
    )
    original_spawn = host._NativeCustody.spawn
    if fault == "helper-return-interrupt":

        def interrupted_return(owner, *args, **kwargs):
            returned = original_spawn(owner, *args, **kwargs)
            assert returned is child and owner.process is child
            raise failure

        monkeypatch.setattr(host._NativeCustody, "spawn", interrupted_return)
    else:

        def failed_attach(owner, process):
            assert process is child
            raise failure

        monkeypatch.setattr(host._NativeCustody, "attach", failed_attach)
    with admitted(case) as authority:
        owned = host._NativeCustody(authority)
        owned.__enter__()
        try:
            if fault == "attach-error":
                result = asyncio.run(host._run_native_control(authority, custody=owned))
                assert result.state == "unknown" and not result.release_sent
                assert not result.cleanup_known
            else:
                with pytest.raises(KeyboardInterrupt) as caught:
                    asyncio.run(host._run_native_control(authority, custody=owned))
                assert caught.value is failure
            assert owned.process is child and not owned.accounted
            assert child.returncode is None and waits == []
            case.session.expire_all()
            attempt = case.session.exec(select(Run).where(Run.parent_run_id == case.run)).one()
            assert attempt.metadata_json.get("native_control", {}).get("phase") != "RELEASED"
        finally:
            owned.__exit__(None, None, None)
        assert waits == [child] and owned.accounted and not owned.active


@pytest.mark.parametrize("fault", ["held-execution", "missing-execution-flag", "late-cleanup"])
def test_host_reads_known_cleanup_without_reviving_unknown_execution(
    case_factory, tmp_path, monkeypatch, fault
):
    case = case_factory()
    target = prepare(case)
    state_dir = tmp_path / "owned-state"
    state_dir.mkdir(mode=0o700)
    monkeypatch.setattr(
        "stackos.config.get_settings",
        lambda: Settings(state_dir=state_dir, data_dir=tmp_path / "owned-data"),
    )
    monkeypatch.setattr(aes_gcm, "encrypt", lambda *args, **kwargs: (b"c" * 48, b"n" * 12))
    monkeypatch.setattr(
        clock,
        "_sample_clock",
        lambda: clock._ClockObservation("known", BOOT, 1, 1, runtime_abi=clock._SUPPORTED_ABI),
    )
    child = SimpleNamespace(pid=900002, returncode=0, poll=lambda: 0, wait=lambda: 0)
    with admitted(case) as authority:
        owned = host._NativeCustody(authority)

        def popen(*args, **kwargs):
            assert kwargs["close_fds"] and len(kwargs["pass_fds"]) == 8
            # Synthetic role report through the actual allocated report FD;
            # this tests readback, not a native/process capability claim.
            report = {
                "binding_digest": owned.slot.binding_digest,
                "manifest_digest": authority.runtime_digest,
                "certified": False,
                "cleanup_known": True,
                "execution_unknown": True,
                "cleanup_completed_continuous_ns": (
                    target.cleanup_deadline_continuous_ns if fault == "late-cleanup" else 1
                ),
                "events": [],
            }
            if fault == "missing-execution-flag":
                del report["execution_unknown"]
            os.write(kwargs["pass_fds"][5], json.dumps(report).encode())
            return child

        monkeypatch.setattr(host.subprocess, "Popen", popen)
        with owned:
            result = asyncio.run(host._run_native_control(authority, custody=owned))
            assert owned.accounted and owned.process is child
            assert not result.certified and not result.release_sent
            if fault == "held-execution":
                assert result.cleanup_known and result.state == "held"
                assert "native_execution_unknown" in result.issues
            else:
                assert not result.cleanup_known and result.state == "unknown"
                expected = (
                    "native_cleanup_report_unknown"
                    if fault == "missing-execution-flag"
                    else "native_cleanup_timing_unknown"
                )
                assert expected in result.issues
        case.session.expire_all()
        attempt = case.session.exec(select(Run).where(Run.parent_run_id == case.run)).one()
        assert "certificate" not in attempt.metadata_json
