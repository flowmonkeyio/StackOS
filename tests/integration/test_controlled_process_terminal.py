"""Original terminal reconciliation on real disposable SQLite and held files.

Portable OS observations are synthetic; grants, transactions, receipts and
effect-ledger reads are the actual canonical owners. No process is launched.
"""

import asyncio
import json
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from stackos import controlled_process_clock as clock
from stackos import controlled_process_host as host
from stackos import controlled_process_receipt as receipt
from stackos.actions import ActionRepository
from stackos.crypto import aes_gcm
from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
    Credential,
    DurableActionAttempt,
    DurableActionAttemptStatus,
    DurableActionItem,
    DurableActionItemStatus,
    DurableActionJob,
    DurableActionJobStatus,
    ProjectEvent,
    Run,
    RunStatus,
    TrackerTask,
    WorkItemOwnership,
)
from stackos.mcp.context import bind_context, build_context
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.runs import OwnerGetInput, _owner_get
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.runs import RunRepository
from tests.integration.test_controlled_command_admission import case_factory as case_factory
from tests.integration.test_controlled_process_host import BOOT, admitted, prepare, reserve


def observation():
    return clock._ClockObservation("known", BOOT, 1, 1, runtime_abi=clock._SUPPORTED_ABI)


def certify_empty(repo, row):
    """Real zero-attempt verifier and owner CAS; clock is a portable fixture."""
    with patch.object(clock, "_sample_clock", observation):
        snapshot = repo._native_quiescence_snapshot(
            project_id=row.project_id,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
        )
        verified = host._verify_native_quiescence(repo._s, snapshot=snapshot, owners=())
        assert type(verified) is host._VerifiedQuiescence
        return repo._certify_owner_quiescence(
            project_id=row.project_id,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
            evidence_ref="fixture:empty-canonical-set",
            verified=verified,
        )


def make_original(case, tmp_path, *, commit=True):
    """Actual granted reservation; never invokes Popen, never invents a run."""
    prepare(case)
    with admitted(case) as admission:
        custody = host._NativeCustody(admission)
        custody.__enter__()
        custody.begin(admission)
        repo, guard, _cursor, binding = reserve(case, admission)
        state = tmp_path / ("state-" + str(binding.attempt_run_id))
        state.mkdir(mode=0o700)
        arm, attempt_path = host._open_arm_directory(
            state, binding.project_id, binding.attempt_run_id
        )
        custody.owned_fds.append(host._OwnedFD(arm))
        manifest = host._native_manifest(admission.original.command)
        key = b"T" * 32
        box, nonce = aes_gcm.encrypt(
            key,
            project_id=binding.project_id,
            kind="controlled-host-key:" + receipt._name(binding)[:-8],
        )
        owner = host._allocate_terminal_owner(
            case.session,
            binding=binding,
            policy=admission.policy,
            manifest=manifest,
            wrapped_key={"ciphertext": box.hex(), "nonce": nonce.hex()},
            key=key,
            custody=custody,
            attempt_path=attempt_path,
        )
        reserved = repo._reserve_terminal_receipt_slot(
            binding=binding,
            expected_revision=guard.ownership.revision,
            evidence_ref="fixture:terminal-slot",
        )
        guard.advance(reserved)
        slot = owner.slot
        allocated = repo._record_terminal_slot_allocated(
            binding=binding,
            expected_revision=guard.ownership.revision,
            expected_slot_revision=1,
            dalloc=slot.dalloc,
            fempty=slot.fempty,
            status_flags=slot.status_flags,
            offset=slot.offset,
            evidence_ref="fixture:terminal-slot",
        )
        guard.advance(allocated)
        guard.retain_terminal_original(owner.original, custody)
        claimed = repo._claim_terminal_receipt_slot(
            binding=binding,
            expected_revision=guard.ownership.revision,
            expected_slot_revision=2,
            producer_digest=owner.original.producer_digest,
            evidence_ref="fixture:terminal-slot",
        )
        guard.advance(claimed)
    if commit:
        result = host._record_not_spawned_terminal(owner)
        assert result.state == "recorded"
    return owner


def certify_original(owner):
    repo = RunRepository(owner.session)
    binding = owner.original.binding
    row = repo.owner_get(project_id=binding.project_id, canonical_item_id=binding.canonical_item_id)
    snapshot = repo._native_quiescence_snapshot(
        project_id=row.project_id,
        canonical_item_id=row.canonical_item_id,
        owner_ref=row.owner_ref,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
    )
    verified = host._verify_native_quiescence(owner.session, snapshot=snapshot, owners=(owner,))
    assert type(verified) is host._VerifiedQuiescence
    return repo._certify_owner_quiescence(
        project_id=row.project_id,
        canonical_item_id=row.canonical_item_id,
        owner_ref=row.owner_ref,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
        evidence_ref="fixture:original-set",
        verified=verified,
    )


def database_snapshot(session):
    session.expire_all()
    return tuple(
        (
            model.__name__,
            tuple(
                row.model_dump(mode="json")
                for row in session.exec(select(model).order_by(model.id)).all()
            ),
        )
        for model in (
            Run,
            WorkItemOwnership,
            ActionCall,
            DurableActionJob,
            DurableActionItem,
            DurableActionAttempt,
            ProjectEvent,
        )
    )


def test_public_owner_status_after_terminal_certificate_and_consumption(original):
    case, owner = original
    binding = owner.original.binding
    repo = RunRepository(case.session)
    certified = certify_original(owner)
    stored = case.session.get(WorkItemOwnership, certified.id, populate_existing=True)
    descriptor = deepcopy(stored.continuity_json["native_quiescence_certificate"])
    attempt = case.session.get(Run, binding.attempt_run_id, populate_existing=True)
    original_attempt = attempt.model_dump(mode="json")
    assert attempt.metadata_json["native_terminal_slot"]["phase"] == "COMMITTED"
    assert attempt.metadata_json["native_terminal_facts"]
    # This genuine not-spawned terminal has no committed ARM and leaves Run running.
    assert "receipt_slot" not in attempt.metadata_json
    assert attempt.status == RunStatus.RUNNING
    assert certified.quiescence_revision == certified.revision
    assert descriptor["certified_owner_revision"] == certified.revision

    def inspect(expected):
        before = database_snapshot(case.session)
        task_before = case.session.get(TrackerTask, case.task).model_dump(mode="json")
        with Session(case.engine) as request_session:
            arguments = {
                "project_id": case.project,
                "canonical_item_id": binding.canonical_item_id,
                "run_token": case.token,
            }
            with bind_context(build_context(arguments, request_session)) as ctx:
                public = asyncio.run(
                    _owner_get(
                        OwnerGetInput.model_validate(arguments), ctx, ProgressEmitter(None, None)
                    )
                ).model_dump(mode="json")
        assert database_snapshot(case.session) == before
        assert case.session.get(TrackerTask, case.task).model_dump(mode="json") == task_before
        assert public["authority_valid"] is True
        assert public["authority_issues"] == []
        assert public["ownership"] == expected.model_dump(mode="json")
        status = public["status"]
        assert status["state"] == "running"
        assert status["freshness"] == "current_at_snapshot"
        assert status["captured_at"] == public["captured_at"]
        assert status["owner"] == {
            "owner_ref": expected.owner_ref,
            "epoch": expected.epoch,
            "revision": expected.revision,
        }
        assert status["original_binding"] == {
            "owner_ref": binding.owner_ref,
            "epoch": binding.epoch,
            "scope_digest": binding.scope_digest,
            "manifest_digest": binding.manifest_digest,
            "controller_run_id": binding.controller_run_id,
            "attempt_run_id": binding.attempt_run_id,
            "reservation_ref": binding.intent_ref,
        }
        assert status["attempt"]["id"] == binding.attempt_run_id
        assert status["attempt"]["status"] == original_attempt["status"]
        assert status["control"] is None
        assert status["acceptance_state"] == "not_evaluated"
        wire = json.dumps(public, sort_keys=True)
        for private in (
            "native_quiescence_certificate",
            "native_terminal_slot",
            "native_terminal_facts",
            "native_recovery_private",
            "wrapped_key",
            "ciphertext",
            "nonce",
            "envelope",
        ):
            assert private not in wire
        assert public["ownership"]["quiescence_ref"] == certified.quiescence_ref

    inspect(certified)
    consumed = repo.owner_consume(
        project_id=case.project,
        canonical_item_id=binding.canonical_item_id,
        owner_ref=binding.owner_ref,
        scope_digest=binding.scope_digest,
        expected_epoch=certified.epoch,
        expected_revision=certified.revision,
        attempts_delta=0,
        failures_delta=1,
        evidence_ref="fixture:combined-consumption",
    )
    assert consumed.revision == certified.revision + 1
    assert consumed.epoch == certified.epoch and consumed.owner_ref == certified.owner_ref
    assert consumed.quiescence_revision is None
    for field in ("attempts_used", "stopped", "denied", "stop_scope_ref", "budget_ref"):
        assert consumed.continuity_json[field] == certified.continuity_json[field]
    assert (
        consumed.continuity_json["failures_used"] == certified.continuity_json["failures_used"] + 1
    )
    stored = case.session.get(WorkItemOwnership, certified.id, populate_existing=True)
    assert stored.continuity_json["native_quiescence_certificate"] == descriptor
    current_attempt = case.session.get(Run, binding.attempt_run_id, populate_existing=True)
    assert current_attempt.model_dump(mode="json") == original_attempt
    inspect(consumed)
    with Session(case.engine) as verifier:
        stale = verifier.get(WorkItemOwnership, certified.id)
        with pytest.raises(ConflictError, match="current private certificate descriptor required"):
            RunRepository(verifier)._assert_native_certificate_current(stale)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("process/network/native operation forbidden")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(os, "killpg", forbidden)
    monkeypatch.setattr(clock, "_sample_clock", observation)
    monkeypatch.setattr(host, "_now", lambda: 1)
    seed = tmp_path / "synthetic-seed"
    seed.write_bytes(b"S" * 32)
    seed.chmod(0o600)
    monkeypatch.setattr(aes_gcm, "_default_seed_path", seed)
    monkeypatch.setattr(aes_gcm, "_cache", {})


@pytest.fixture
def original(case_factory, tmp_path):
    case = case_factory()
    owner = make_original(case, tmp_path)
    try:
        yield case, owner
    finally:
        owner.custody.wait_accounted()


@pytest.mark.parametrize(
    "case_name",
    [
        "running",
        "success",
        "failed",
        "aborted",
        "superseded",
        "duplicate",
        "contradiction",
        "sequence-gap",
        "foreign-binding",
    ],
)
def test_original_terminal_fact_is_append_only_and_idempotent(original, case_name):
    case, owner = original
    binding = owner.original.binding
    repo = RunRepository(case.session)
    attempt = case.session.get(Run, binding.attempt_run_id)
    if case_name in {"success", "failed", "aborted"}:
        repo.finish(attempt.id, status=RunStatus(case_name))
    if case_name == "superseded":
        certified = certify_original(owner)
        repo.owner_reassign(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=binding.owner_ref,
            new_owner_ref="fixture:replacement",
            scope_digest=binding.scope_digest,
            expected_epoch=certified.epoch,
            expected_revision=certified.revision,
        )
    before = database_snapshot(case.session)
    verified = host._terminal_verification(owner)
    if case_name == "contradiction":
        verified = replace(
            verified,
            final_f9=(*verified.final_f9[:6], verified.final_f9[6] + 1, *verified.final_f9[7:]),
        )
    elif case_name == "foreign-binding":
        verified = replace(
            verified,
            authenticated=replace(
                verified.authenticated, binding=replace(binding, epoch=binding.epoch + 1)
            ),
        )
    elif case_name == "sequence-gap":
        verified = replace(
            verified,
            authenticated=replace(
                verified.authenticated, fact=replace(verified.authenticated.fact, sequence=2)
            ),
        )
    if case_name in {"contradiction", "sequence-gap", "foreign-binding"}:
        with pytest.raises((host._ControlError, ConflictError)):
            repo._commit_terminal_receipt_slot(
                verified=verified, expected_slot_revision=4, evidence_ref="native:not-spawned"
            )
    else:
        result = repo._commit_terminal_receipt_slot(
            verified=verified, expected_slot_revision=4, evidence_ref="native:not-spawned"
        )
        assert result.applied is False and result.fact_index == result.facts_revision == 1
    assert database_snapshot(case.session) == before


class InjectedTerminalFault(RuntimeError):
    pass


@pytest.fixture
def uncommitted_original(case_factory, tmp_path):
    """Real receipt, closed custody and authentication; fault before DB write."""
    case = case_factory()
    owner = make_original(case, tmp_path, commit=False)
    try:
        with patch.object(
            RunRepository,
            "_commit_terminal_receipt_slot",
            side_effect=InjectedTerminalFault("before terminal writer"),
        ) as writer:
            assert host._record_not_spawned_terminal(owner).state == "held"
            writer.assert_called_once()
        attempt = case.session.get(Run, owner.original.binding.attempt_run_id)
        assert attempt.metadata_json["native_terminal_slot"]["phase"] == "CLAIMED"
        assert "native_terminal_facts" not in attempt.metadata_json
        verified = host._terminal_verification(owner)
        verified.validate()
        yield case, owner, verified
    finally:
        owner.custody.wait_accounted()


@pytest.mark.parametrize(
    "boundary", ["event-before", "event-after", "commit-before", "commit-after"]
)
def test_terminal_fact_and_event_commit_atomically_at_fault_boundaries(
    uncommitted_original, boundary
):
    case, owner, verified = uncommitted_original
    repo = RunRepository(case.session)
    before = database_snapshot(case.session)
    event_writer = repo._owner_event
    commit = case.session.commit

    def fail_event(*args, **kwargs):
        if boundary == "event-after":
            event_writer(*args, **kwargs)
        raise InjectedTerminalFault(boundary)

    def fail_commit():
        if boundary == "commit-after":
            commit()
        raise InjectedTerminalFault(boundary)

    target, method, fault = (
        (repo, "_owner_event", fail_event)
        if boundary.startswith("event")
        else (case.session, "commit", fail_commit)
    )
    with patch.object(target, method, side_effect=fault) as injected:
        with pytest.raises(InjectedTerminalFault, match=boundary):
            repo._commit_terminal_receipt_slot(
                verified=verified, expected_slot_revision=3, evidence_ref="native:not-spawned"
            )
        injected.assert_called_once()
    after = database_snapshot(case.session)
    if boundary != "commit-after":
        assert after == before
        return
    # A real successful commit followed by lost acknowledgement is durable.
    assert after != before
    attempt = case.session.get(Run, owner.original.binding.attempt_run_id)
    assert attempt.metadata_json["native_terminal_slot"]["phase"] == "COMMITTED"
    assert len(attempt.metadata_json["native_terminal_facts"]["entries"]) == 1
    before_events = dict(before)["ProjectEvent"]
    after_events = dict(after)["ProjectEvent"]
    assert len(after_events) == len(before_events) + 1
    assert dict(after)["WorkItemOwnership"] == dict(before)["WorkItemOwnership"]
    result = repo._commit_terminal_receipt_slot(
        verified=verified, expected_slot_revision=4, evidence_ref="native:not-spawned"
    )
    assert not result.applied
    assert database_snapshot(case.session) == after


def test_concurrent_terminal_writers_commit_one_fact_and_event(uncommitted_original):
    case, owner, verified = uncommitted_original
    before = database_snapshot(case.session)
    barrier = Barrier(2)
    begin = RunRepository._terminal_writer_begin

    def synchronized_begin(repo):
        barrier.wait(timeout=10)
        return begin(repo)

    def write():
        with Session(case.engine) as session:
            local_owner = replace(owner, session=session)
            local_verification = replace(verified, owner=local_owner)
            try:
                result = RunRepository(session)._commit_terminal_receipt_slot(
                    verified=local_verification,
                    expected_slot_revision=3,
                    evidence_ref="native:not-spawned",
                )
                assert result.applied
                return "committed"
            except ConflictError as exc:
                assert "stale terminal commit fence" in str(exc)
                return "stale"

    with (
        patch.object(RunRepository, "_terminal_writer_begin", synchronized_begin),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first, second = pool.submit(write), pool.submit(write)
        assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
            "committed",
            "stale",
        ]
    after = database_snapshot(case.session)
    assert dict(after)["WorkItemOwnership"] == dict(before)["WorkItemOwnership"]
    assert len(dict(after)["ProjectEvent"]) == len(dict(before)["ProjectEvent"]) + 1
    attempt = case.session.get(Run, owner.original.binding.attempt_run_id)
    assert attempt.metadata_json["native_terminal_slot"]["revision"] == 4
    assert attempt.metadata_json["native_terminal_facts"]["revision"] == 1
    assert len(attempt.metadata_json["native_terminal_facts"]["entries"]) == 1
    replay = RunRepository(case.session)._commit_terminal_receipt_slot(
        verified=verified, expected_slot_revision=4, evidence_ref="native:not-spawned"
    )
    assert not replay.applied
    assert database_snapshot(case.session) == after


@pytest.mark.parametrize(
    "case_name",
    [
        "complete",
        "unregistered-reservation",
        "late-registration",
        "control-revision-drift",
        "fact-drift",
        "terminal-slot-uncommitted",
        "missing-run",
        "owner-revision",
        "owner-epoch",
        "scope-drift",
        "untrusted-proof",
        "lost-exit-witness",
        "zero-launch",
    ],
)
def test_certificate_requires_complete_exact_registered_set(case_factory, original, case_name):
    case, owner = original
    repo = RunRepository(case.session)
    binding = owner.original.binding
    row = repo.owner_get(project_id=case.project, canonical_item_id=binding.canonical_item_id)
    if case_name == "zero-launch":
        empty = case_factory()
        prepare(empty)
        empty_repo = RunRepository(empty.session)
        empty_row = empty_repo.owner_get(
            project_id=empty.project, canonical_item_id=f"task:{empty.task}"
        )
        result = certify_empty(empty_repo, empty_row)
        assert (
            result.quiescence_revision == result.revision
            and "native_quiescence_certificate" not in result.continuity_json
        )
        return
    snapshot = repo._native_quiescence_snapshot(
        project_id=case.project,
        canonical_item_id=binding.canonical_item_id,
        owner_ref=row.owner_ref,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
    )
    verified = host._verify_native_quiescence(case.session, snapshot=snapshot, owners=(owner,))
    assert type(verified) is host._VerifiedQuiescence
    if case_name == "complete":
        result = repo._certify_owner_quiescence(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
            evidence_ref="fixture:certificate",
            verified=verified,
        )
        assert result.quiescence_revision == result.revision
        raw_owner = case.session.get(WorkItemOwnership, row.id)
        assert raw_owner.continuity_json["native_quiescence_certificate"]["owner_id"] == row.id
        assert "native_quiescence_certificate" not in result.continuity_json
        return
    raw_owner = case.session.get(WorkItemOwnership, row.id)
    attempt = case.session.get(Run, binding.attempt_run_id)
    if case_name in {"owner-revision", "owner-epoch", "scope-drift"}:
        setattr(
            raw_owner,
            {"owner-revision": "revision", "owner-epoch": "epoch", "scope-drift": "scope_digest"}[
                case_name
            ],
            "f" * 64
            if case_name == "scope-drift"
            else getattr(raw_owner, "revision" if case_name == "owner-revision" else "epoch") + 1,
        )
        case.session.add(raw_owner)
    elif case_name == "missing-run":
        case.session.delete(attempt)
    elif case_name == "unregistered-reservation":
        value = deepcopy(raw_owner.continuity_json)
        value["launch_reservations"]["fixture:extra"] = {"attempt_run_id": 999999}
        raw_owner.continuity_json = value
        case.session.add(raw_owner)
    elif case_name == "lost-exit-witness":
        owner.custody.ever_spawn_invoked = True
    elif case_name == "untrusted-proof":
        verified = object()
    else:
        metadata = deepcopy(attempt.metadata_json)
        if case_name == "terminal-slot-uncommitted":
            metadata["native_terminal_slot"]["phase"] = "CLAIMED"
            for name in ("receipt_digest", "final_f9", "fact_sequence"):
                del metadata["native_terminal_slot"][name]
        elif case_name == "fact-drift":
            metadata["native_terminal_facts"]["revision"] += 1
        elif case_name == "control-revision-drift":
            metadata["native_control"] = {"control_revision": 99}
        else:
            metadata["native_recovery_private"]["manifest"]["late"] = True
        attempt.metadata_json = metadata
        case.session.add(attempt)
    case.session.commit()
    before = database_snapshot(case.session)
    with pytest.raises((ConflictError, ValidationError, host._ControlError)):
        repo._certify_owner_quiescence(
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
            evidence_ref="fixture:certificate",
            verified=verified,
        )
    assert database_snapshot(case.session) == before
    owner.custody.ever_spawn_invoked = False


def carry_current_accounting(case, current):
    """Model the canonical task writer preserving already charged floors."""
    task = case.session.get(TrackerTask, case.task)
    metadata = deepcopy(task.metadata_json)
    for counter in ("attempts_used", "failures_used"):
        metadata["ownership_continuity"][counter] = max(
            metadata["ownership_continuity"][counter], current.continuity_json[counter]
        )
    task.metadata_json = metadata
    case.session.add(task)
    case.session.commit()


def second_controller(case):
    """A second granted controller reserving the same canonical task."""
    ref = "engineering.controlled-proof.v1"
    plans = RunPlanRepository(case.session)
    plan = plans.create(
        project_id=case.project,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "fixture.second",
            "title": "Second",
            "approvals": [{"key": "command", "title": "Exact command"}],
            "grants": {
                "mcp_tool_grants": [
                    {"step_id": "admit", "tool": "action.execute", "action_refs": [ref]}
                ],
                "action_contracts": [
                    {"key": "controlled", "action": ref, "approval_ref": "command"}
                ],
            },
            "steps": [{"id": "admit", "title": "Admit", "action_refs": [ref]}],
        },
    ).data
    started = plans.start(plan.id, project_id=case.project).data
    step = plans.get_step(plan.id, "admit")
    task = case.session.get(TrackerTask, case.task)
    task.source_json = {"run_plan_id": plan.id, "run_plan_step_id": step.id}
    case.session.add(task)
    case.session.commit()
    current = RunRepository(case.session).owner_get(
        project_id=case.project, canonical_item_id=f"task:{case.task}"
    )
    # A new controller consumes cumulative source accounting, never an older
    # task snapshot that would regress the persisted owner's charged floor.
    carry_current_accounting(case, current)
    args = deepcopy(case.args)
    args["run_token"] = started.run_token
    args["input_json"].update(intent_ref="fixture:second", expected_revision=current.revision)
    approval = case.session.exec(
        select(ApprovalRequest).where(ApprovalRequest.run_plan_id == case.plan)
    ).one()
    decision = deepcopy(approval.decision_json)
    decision["controlled_command"].update(
        controller_run_id=started.run_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        intent_ref="fixture:second",
    )
    # The second host policy is derived by prepare from this command decision.
    decision.pop("controlled_host", None)
    plans.update(
        run_plan_id=plan.id,
        approval_key="command",
        approval_status=ApprovalRequestStatus.APPROVED,
        decided_by="fixture:operator",
        decision_json=decision,
    )
    plans.claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="admit", claimed_by="fixture:lead"
    )
    return replace(
        case, plan=plan.id, step=step.id, run=started.run_id, token=started.run_token, args=args
    )


def test_certificate_covers_retained_attempts_from_both_controllers(original, tmp_path):
    case, first = original
    second = make_original(second_controller(case), tmp_path)
    try:
        assert first.original.binding.controller_run_id != second.original.binding.controller_run_id
        repo = RunRepository(case.session)
        row = repo.owner_get(project_id=case.project, canonical_item_id=f"task:{case.task}")
        snapshot = repo._native_quiescence_snapshot(
            project_id=case.project,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
        )
        assert {item.binding.attempt_run_id for item in snapshot.attempts} == {
            first.original.binding.attempt_run_id,
            second.original.binding.attempt_run_id,
        }
        assert len(row.continuity_json["launch_reservations"]) == 2
        assert row.continuity_json["attempts_used"] == 2
        before = database_snapshot(case.session)
        incomplete = host._verify_native_quiescence(
            case.session, snapshot=snapshot, owners=(second,)
        )
        assert type(incomplete) is host._TerminalHold
        assert database_snapshot(case.session) == before
        complete = host._verify_native_quiescence(
            case.session, snapshot=snapshot, owners=(first, second)
        )
        assert type(complete) is host._VerifiedQuiescence
        result = repo._certify_owner_quiescence(
            project_id=case.project,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
            evidence_ref="fixture:two-controllers",
            verified=complete,
        )
        assert result.quiescence_revision == result.revision == row.revision + 1
        assert result.continuity_json["attempts_used"] == 2
    finally:
        second.custody.wait_accounted()


def known_call(case, owner):
    args = deepcopy(case.args)
    current = RunRepository(case.session).owner_get(
        project_id=case.project, canonical_item_id=owner.original.binding.canonical_item_id
    )
    carry_current_accounting(case, current)
    args["input_json"]["expected_revision"] = current.revision
    args["dry_run"] = True
    asyncio.run(case.dispatch(args))
    row = case.session.exec(select(ActionCall)).one()
    assert row.status == ActionCallStatus.DRY_RUN
    return row


def effect_arguments(case, owner):
    return dict(
        project_id=case.project,
        bindings=(owner.original.binding,),
        effect_identity="effect:fixture:no-effect",
        command_manifests=(owner.original.manifest,),
    )


def durable_rows(case, call):
    credential = Credential(
        credential_ref="fixture:synthetic",
        provider_key="fixture",
        display_name="Synthetic",
        display_name_key="synthetic",
    )
    case.session.add(credential)
    case.session.commit()
    job = DurableActionJob(
        project_id=case.project,
        action_call_id=call.id,
        run_id=case.run,
        run_plan_id=case.plan,
        run_plan_step_id=case.step,
        credential_id=credential.id,
        credential_ref=credential.credential_ref,
        action_ref="fixture.synthetic",
        input_digest="a" * 64,
        state=DurableActionJobStatus.UNKNOWN_HOLD,
    )
    case.session.add(job)
    case.session.commit()
    item = DurableActionItem(
        job_id=job.id,
        ordinal=1,
        destination_ref="fixture:destination",
        correlation_ref="fixture:correlation",
        input_digest="b" * 64,
        state=DurableActionItemStatus.UNKNOWN_HOLD,
    )
    case.session.add(item)
    case.session.commit()
    attempt = DurableActionAttempt(
        item_id=item.id,
        ordinal=1,
        attempt_ref="fixture:attempt",
        state=DurableActionAttemptStatus.UNKNOWN_HOLD,
    )
    case.session.add(attempt)
    case.session.commit()
    return job, item, attempt


@pytest.mark.parametrize("changed", ["job", "item", "effect_attempt"])
def test_actual_durable_lineage_holds_and_refreshes_cached_rows(original, changed):
    case, owner = original
    call = known_call(case, owner)
    audit = ActionRepository(case.session)
    arguments = effect_arguments(case, owner)
    assert audit._controlled_effect_snapshot(**arguments).status == "known_no_effect"
    rows = durable_rows(case, call)
    initial = audit._controlled_effect_snapshot(**arguments)
    frozen = deepcopy(initial)
    assert initial.status == "unknown"
    assert {entry["kind"] for entry in initial.rows} >= {"call", "job", "item", "effect_attempt"}
    index = {"job": 0, "item": 1, "effect_attempt": 2}[changed]
    cached = rows[index]
    cached_id = cached.id
    with Session(case.engine) as second:
        actual = second.get(type(cached), cached_id)
        field = {"job": "metadata_json", "item": "result_json", "effect_attempt": "receipt_json"}[
            changed
        ]
        setattr(actual, field, {"second_session": True})
        second.add(actual)
        second.commit()
    before = database_snapshot_in_other_session(case)
    refreshed = audit._controlled_effect_snapshot(**arguments)
    assert (
        refreshed.status == "unknown" and refreshed.descriptor_digest != initial.descriptor_digest
    )
    assert initial == frozen
    assert database_snapshot_in_other_session(case) == before


def database_snapshot_in_other_session(case):
    with Session(case.engine) as independent:
        return database_snapshot(independent)


@pytest.mark.parametrize("boundary", ["certify", "consume"])
def test_effect_drift_from_second_session_prevents_certificate_use(original, boundary):
    case, owner = original
    cached_call = known_call(case, owner)
    repo = RunRepository(case.session)
    row = repo.owner_get(project_id=case.project, canonical_item_id=f"task:{case.task}")
    snapshot = repo._native_quiescence_snapshot(
        project_id=case.project,
        canonical_item_id=row.canonical_item_id,
        owner_ref=row.owner_ref,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
    )
    verified = host._verify_native_quiescence(case.session, snapshot=snapshot, owners=(owner,))
    assert type(verified) is host._VerifiedQuiescence
    if boundary == "consume":
        row = repo._certify_owner_quiescence(
            project_id=case.project,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
            evidence_ref="fixture:pre-drift",
            verified=verified,
        )
        # Retain a loaded identity-map row across the external writer.
        case.session.refresh(cached_call)
    with Session(case.engine) as second:
        changed = second.get(ActionCall, cached_call.id)
        changed.status = ActionCallStatus.RUNNING
        second.add(changed)
        second.commit()
    assert cached_call.status == ActionCallStatus.DRY_RUN
    before = database_snapshot_in_other_session(case)
    with pytest.raises((ConflictError, ValidationError, host._ControlError)):
        if boundary == "certify":
            repo._certify_owner_quiescence(
                project_id=case.project,
                canonical_item_id=row.canonical_item_id,
                owner_ref=row.owner_ref,
                expected_epoch=row.epoch,
                expected_revision=row.revision,
                evidence_ref="fixture:post-drift",
                verified=verified,
            )
        else:
            repo.owner_reassign(
                project_id=case.project,
                canonical_item_id=row.canonical_item_id,
                owner_ref=row.owner_ref,
                new_owner_ref="fixture:replacement",
                scope_digest=row.scope_digest,
                expected_epoch=row.epoch,
                expected_revision=row.revision,
            )
    assert database_snapshot_in_other_session(case) == before


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted", "physical-transaction"])
@pytest.mark.parametrize("boundary", ["commit", "certify", "consume", "recover"])
def test_native_boundaries_preserve_caller_session_work(original, pending, boundary):
    case, owner = original
    repo = RunRepository(case.session)
    binding = owner.original.binding
    row = repo.owner_get(project_id=case.project, canonical_item_id=binding.canonical_item_id)
    snapshot = repo._native_quiescence_snapshot(
        project_id=case.project,
        canonical_item_id=row.canonical_item_id,
        owner_ref=row.owner_ref,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
    )
    verified = host._verify_native_quiescence(case.session, snapshot=snapshot, owners=(owner,))
    assert type(verified) is host._VerifiedQuiescence
    if boundary == "consume":
        row = repo._certify_owner_quiescence(
            project_id=case.project,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
            evidence_ref="fixture:caller-work",
            verified=verified,
        )
    task = case.session.get(TrackerTask, case.task)
    before = database_snapshot_in_other_session(case)
    if pending == "new":
        case.session.add(
            ActionCall(
                project_id=case.project,
                plugin_slug="fixture",
                action_key="pending",
                operation="pending",
                status=ActionCallStatus.RUNNING,
            )
        )
    elif pending == "dirty":
        task.title = "Caller unsaved title"
    elif pending == "deleted":
        case.session.delete(task)
    else:
        case.session.connection().exec_driver_sql("BEGIN")
    pending_before = (
        tuple(case.session.new),
        tuple(case.session.dirty),
        tuple(case.session.deleted),
    )
    driver = case.session.connection().connection.driver_connection
    transaction_before = driver.in_transaction
    writes = []

    def touched(*args, **kwargs):
        writes.append(True)

    event.listen(case.session, "before_flush", touched)
    try:
        with (
            patch.object(case.session, "commit", wraps=case.session.commit) as commit,
            patch.object(case.session, "rollback", wraps=case.session.rollback) as rollback,
        ):
            with pytest.raises(ConflictError):
                if boundary == "commit":
                    repo._commit_terminal_receipt_slot(
                        verified=host._terminal_verification(owner),
                        expected_slot_revision=4,
                        evidence_ref="native:not-spawned",
                    )
                elif boundary == "certify":
                    repo._certify_owner_quiescence(
                        project_id=case.project,
                        canonical_item_id=row.canonical_item_id,
                        owner_ref=row.owner_ref,
                        expected_epoch=row.epoch,
                        expected_revision=row.revision,
                        evidence_ref="fixture:caller-work",
                        verified=verified,
                    )
                elif boundary == "consume":
                    repo.owner_reassign(
                        project_id=case.project,
                        canonical_item_id=row.canonical_item_id,
                        owner_ref=row.owner_ref,
                        new_owner_ref="fixture:replacement",
                        scope_digest=row.scope_digest,
                        expected_epoch=row.epoch,
                        expected_revision=row.revision,
                    )
                else:
                    host._recover_native_terminal(
                        case.session,
                        project_id=case.project,
                        canonical_item_id=row.canonical_item_id,
                        attempt_run_id=binding.attempt_run_id,
                        evidence_ref="fixture:caller-work",
                    )
            commit.assert_not_called()
            rollback.assert_not_called()
        assert not writes
        assert (
            tuple(case.session.new),
            tuple(case.session.dirty),
            tuple(case.session.deleted),
        ) == pending_before
        assert driver.in_transaction == transaction_before
        if pending == "dirty":
            assert task.title == "Caller unsaved title"
        assert database_snapshot_in_other_session(case) == before
    finally:
        event.remove(case.session, "before_flush", touched)
        # Caller cleanup happens only after proving the operation did not own it.
        case.session.rollback()


@pytest.mark.parametrize(
    "case_name",
    [
        "empty-fixed-noop",
        "running-call",
        "restart-orphan",
        "incompatible-action",
        "wrong-intent",
        "command-drift",
        "rowset-changed",
    ],
)
def test_effect_readback_is_authoritative_and_unknown_holds(original, case_name):
    case, owner = original
    binding = owner.original.binding
    audit = ActionRepository(case.session)
    arguments = dict(
        project_id=case.project,
        bindings=(binding,),
        effect_identity="effect:fixture:no-effect",
        command_manifests=(owner.original.manifest,),
    )
    initial = audit._controlled_effect_snapshot(**arguments)
    assert initial.status == "known_no_effect"
    if case_name == "empty-fixed-noop":
        return
    if case_name == "command-drift":
        arguments["command_manifests"] = (owner.original.manifest | {"changed": True},)
    else:
        row = ActionCall(
            project_id=case.project,
            run_id=case.run,
            run_plan_id=case.plan,
            run_plan_step_id=case.step,
            plugin_slug="engineering",
            action_key="controlled-proof.v1",
            operation="controlled-proof.v1",
            status=ActionCallStatus.RUNNING,
            idempotency_key="wrong" if case_name == "wrong-intent" else None,
        )
        if case_name == "restart-orphan":
            row.status = ActionCallStatus.FAILED
            row.error = "daemon-restart-orphan"
            row.response_json = dict(outcome_unknown=True, retry_safe=False)
        if case_name == "incompatible-action":
            row.plugin_slug = "external"
        case.session.add(row)
        case.session.commit()
    actual = audit._controlled_effect_snapshot(**arguments)
    assert actual.status == "unknown" and actual.descriptor_digest != initial.descriptor_digest


@pytest.mark.parametrize(
    "case_name", ["exact", "new-terminal-fact", "changed-action-call", "stale-certificate"]
)
def test_reassignment_consumes_only_current_certificate_snapshot(original, case_name):
    case, owner = original
    repo = RunRepository(case.session)
    certified = certify_original(owner)
    if case_name == "new-terminal-fact":
        attempt = case.session.get(Run, owner.original.binding.attempt_run_id)
        value = deepcopy(attempt.metadata_json)
        value["native_terminal_facts"]["revision"] += 1
        attempt.metadata_json = value
        case.session.add(attempt)
        case.session.commit()
    elif case_name == "changed-action-call":
        case.session.add(
            ActionCall(
                project_id=case.project,
                run_id=case.run,
                run_plan_id=case.plan,
                run_plan_step_id=case.step,
                plugin_slug="external",
                action_key="write",
                operation="write",
                status=ActionCallStatus.RUNNING,
            )
        )
        case.session.commit()
    args = dict(
        project_id=case.project,
        canonical_item_id=certified.canonical_item_id,
        owner_ref=certified.owner_ref,
        new_owner_ref="fixture:replacement",
        scope_digest=certified.scope_digest,
        expected_epoch=certified.epoch,
        expected_revision=certified.revision - (case_name == "stale-certificate"),
    )
    before = database_snapshot(case.session)
    if case_name == "exact":
        result = repo.owner_reassign(**args)
        assert (
            result.epoch == certified.epoch + 1
            and result.continuity_json["attempts_used"]
            == certified.continuity_json["attempts_used"]
        )
    else:
        with pytest.raises((ConflictError, ValidationError)):
            repo.owner_reassign(**args)
        assert database_snapshot(case.session) == before


@pytest.mark.parametrize(
    "case_name",
    [
        "missing-original-witness",
        "terminal-late",
    ],
)
def test_recovery_preserves_original_authority_and_never_relaunches(original, case_name):
    case, owner = original
    binding = owner.original.binding
    if case_name == "terminal-late":
        RunRepository(case.session).finish(binding.attempt_run_id, status=RunStatus.ABORTED)
    before = database_snapshot(case.session)
    if case_name == "terminal-late":
        result = host._reconcile_native_terminal(
            case.session,
            original=owner.original,
            custody=owner.custody,
            exit_witness=None,
            evidence_ref="native:not-spawned",
            owner=owner,
        )
        assert result.state == "replayed"
    else:
        # Restart has no original exit/writer capability even when bytes are
        # present. No replacement key, grant, writer, monitor or spawn occurs.
        result = host._recover_native_terminal(
            case.session,
            project_id=case.project,
            canonical_item_id=binding.canonical_item_id,
            attempt_run_id=binding.attempt_run_id,
            evidence_ref="fixture:recovery",
        )
        assert result.state == "held" and result.certificate is None
    assert database_snapshot(case.session) == before
