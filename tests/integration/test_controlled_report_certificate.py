"""Canonical certificate/effect readback for finite selections; no native roles."""

from copy import deepcopy

import pytest
from sqlmodel import select

from stackos import controlled_process_host as host
from stackos.actions import ActionRepository
from stackos.actions import controlled_command as fixed
from stackos.db.models import Run, WorkItemOwnership
from stackos.repositories.base import ConflictError
from stackos.repositories.runs import RunRepository
from tests.integration.test_controlled_command_admission import case_factory as case_factory
from tests.integration.test_controlled_process_host import prepare
from tests.integration.test_controlled_process_terminal import (
    certify_original,
    database_snapshot,
    effect_arguments,
    known_call,
    make_original,
    second_controller,
)
from tests.integration.test_controlled_process_terminal import (
    isolated as isolated,
)
from tests.support.controlled_report import select_report


def select_command(case, key):
    if key == "synthetic-status-report-v1":
        return select_report(case)
    command = fixed._resolve_controlled_command()
    case.args["input_json"].update(command_key=key, command_digest=command.digest)

    def update(value):
        value["controlled_command"].update(
            command_digest=command.digest, command=command.descriptor()
        )
        value.pop("controlled_host", None)

    case.approval(change=update)
    return command


@pytest.fixture
def report_original(case_factory, tmp_path):
    case = case_factory()
    prepare(case)
    select_report(case)
    owner = make_original(case, tmp_path)
    try:
        yield case, owner
    finally:
        owner.custody.wait_accounted()


def test_report_effect_and_certificate_readback_uses_original_selection(report_original):
    case, owner = report_original
    call = known_call(case, owner)
    assert call.request_json["command_key"] == "synthetic-status-report-v1"
    audit = ActionRepository(case.session)
    effect = audit._controlled_effect_snapshot(**effect_arguments(case, owner))
    assert effect.status == "known_no_effect" and not effect.issues
    certified = certify_original(owner)
    assert certified.quiescence_revision == certified.revision
    stored = case.session.get(WorkItemOwnership, certified.id, populate_existing=True)
    before = database_snapshot(case.session)
    case.settings.controlled_host_enabled = False
    RunRepository(case.session)._assert_native_certificate_current(stored)
    case.session.rollback()
    assert database_snapshot(case.session) == before
    assert not (case.settings.state_dir / "controlled-reports").exists()


@pytest.mark.parametrize(
    "first_key,second_key",
    [
        ("noop-v1", "noop-v1"),
        ("noop-v1", "synthetic-status-report-v1"),
        ("synthetic-status-report-v1", "noop-v1"),
        ("synthetic-status-report-v1", "synthetic-status-report-v1"),
    ],
)
def test_certificate_covers_each_original_command_in_mixed_attempts(
    case_factory, tmp_path, first_key, second_key
):
    case = case_factory()
    prepare(case)
    select_command(case, first_key)
    first = make_original(case, tmp_path)
    second = None
    try:
        next_case = second_controller(case)
        select_command(next_case, second_key)
        second = make_original(next_case, tmp_path)
        repo = RunRepository(case.session)
        row = repo.owner_get(project_id=case.project, canonical_item_id=f"task:{case.task}")
        snapshot = repo._native_quiescence_snapshot(
            project_id=case.project,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
        )
        assert [item.manifest["command"]["command_key"] for item in snapshot.attempts] == [
            first_key,
            second_key,
        ]
        assert snapshot.effect.status == "known_no_effect"
        assert snapshot.effect.issues == ()
        verified = host._verify_native_quiescence(
            case.session, snapshot=snapshot, owners=(first, second)
        )
        assert type(verified) is host._VerifiedQuiescence
        result = repo._certify_owner_quiescence(
            project_id=case.project,
            canonical_item_id=row.canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
            evidence_ref="fixture:mixed-fixed-commands",
            verified=verified,
        )
        assert result.quiescence_revision == result.revision
        stored = case.session.get(WorkItemOwnership, row.id, populate_existing=True)
        repo._assert_native_certificate_current(stored)
        case.session.rollback()
        assert stored.continuity_json["attempts_used"] == 2
    finally:
        if second is not None:
            second.custody.wait_accounted()
        first.custody.wait_accounted()


@pytest.mark.parametrize(
    "drift", ["script", "input", "executable", "state", "selected-path", "unknown-key", "runtime"]
)
def test_report_certificate_holds_current_source_storage_or_manifest_drift(
    report_original, tmp_path, monkeypatch, drift
):
    case, owner = report_original
    certified = certify_original(owner)
    if drift in {"script", "input"}:
        name = fixed._REPORT_SCRIPT if drift == "script" else fixed._REPORT_INPUT
        path = case.source / name
        path.write_bytes(path.read_bytes() + b"\n")
    elif drift == "executable":
        changed = tmp_path / "different-interpreter"
        changed.write_bytes(b"different bytes; never executed")
        monkeypatch.setattr(fixed.sys, "executable", str(changed))
    elif drift == "state":
        case.settings.state_dir = tmp_path / "other-state"
    else:
        attempt = case.session.get(Run, owner.original.binding.attempt_run_id)
        metadata = deepcopy(attempt.metadata_json)
        command = metadata["native_recovery_private"]["manifest"]["command"]
        if drift == "selected-path":
            command["report"]["directory"] = str(tmp_path / "caller-selected")
        elif drift == "unknown-key":
            command["command_key"] = "arbitrary"
        else:
            metadata["native_recovery_private"]["manifest"]["watchdog_entry_sha256"] = "0" * 64
        attempt.metadata_json = metadata
        case.session.add(attempt)
        case.session.commit()
    stored = case.session.get(WorkItemOwnership, certified.id, populate_existing=True)
    before = database_snapshot(case.session)
    with pytest.raises(ConflictError):
        RunRepository(case.session)._assert_native_certificate_current(stored)
    assert database_snapshot(case.session) == before


def test_precomputed_runtime_cannot_cover_new_or_swapped_attempt(report_original):
    case, owner = report_original
    arguments = effect_arguments(case, owner)
    noop = host._native_manifest(fixed._resolve_controlled_command())
    for mapping in ({}, {owner.original.binding.attempt_run_id: noop}):
        result = ActionRepository(case.session)._controlled_effect_snapshot(
            **arguments, _resolved_runtimes=mapping
        )
        assert result.status == "unknown" and "effect_command_unknown" in result.issues
    assert case.session.exec(select(WorkItemOwnership)).one().quiescence_revision is None
