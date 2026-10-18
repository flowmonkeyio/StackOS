"""Main-dispatched Darwin proof: only fresh directly owned synthetic roles.

Every case preserves sanitized actual role observations beneath pytest's
basetemp. Native positives must be known; unsupported/denied never skips.
"""

import asyncio
import errno
import hashlib
import json
import multiprocessing
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

import pytest

from stackos import controlled_process_clock as clock
from stackos import controlled_process_host as host
from stackos import controlled_process_identity as identity
from stackos.config import Settings
from stackos.controlled_process_host import _DARWIN_EV_RECEIPT as _DARWIN_EV_RECEIPT
from stackos.controlled_process_host import _ExactExitWatch as _ExactExitWatch
from stackos.crypto import aes_gcm
from tests.integration.test_controlled_command_admission import case_factory as case_factory
from tests.integration.test_controlled_process_host import admitted, lose_grant, prepare


class _FixtureOwner:
    """Retain exact live contexts even when assertions/cancellation fail."""

    def __init__(self, state, observed, output):
        self.state, self.observed, self.output = state, observed, output
        self.contexts = []

    def __iter__(self):
        return iter((self.state, self.observed))

    def custody(self, admission):
        owned = host._NativeCustody(admission)
        owned.__enter__()
        self.contexts.append(owned)
        return owned

    def finalize(self):
        errors = []
        for ordinal, owned in enumerate(self.contexts):
            before = self.output / f"custody-{ordinal}-before-teardown.json"
            try:
                before.write_text(json.dumps(owned.snapshot(), sort_keys=True, indent=2) + "\n")
            except BaseException as error:
                errors.append(type(error).__name__)
            finally:
                # No validation cutoff or PID lookup. Keep this owner alive on
                # the exact handle until waitpid accounts it, even after the
                # product deadline. Do not claim timely product cleanup.
                try:
                    owned.__exit__(None, None, None)
                except BaseException as error:
                    errors.append(type(error).__name__)
            try:
                (self.output / f"custody-{ordinal}-after-teardown.json").write_text(
                    json.dumps(owned.snapshot(), sort_keys=True, indent=2) + "\n"
                )
            except BaseException as error:
                errors.append(type(error).__name__)
            if not owned.accounted:
                errors.append("owned_fd_disposition_unknown")
        assert not errors, errors


def _join_owned_controller(controller, held_output):
    def hold(failure):
        host._park_owned(
            {
                "schema_version": "stackos.native-custody-held.v1",
                "owner": "fixture-controller",
                "operation": "join",
                "pid": controller.pid,
                "code": "native_controller_join_held",
                "errno": failure.errno if isinstance(failure, OSError) else None,
                "product_success": False,
                "handle_retained": True,
            },
            lambda safe: held_output.write_text(json.dumps(safe, sort_keys=True) + "\n"),
        )

    interrupted = host._account_owned(controller.join, hold)
    if interrupted is not None:
        raise interrupted


@pytest.fixture
def native_storage(tmp_path, monkeypatch):
    observed = clock._sample_clock()
    assert clock._runtime_abi() == clock._SUPPORTED_ABI
    assert observed.status == "known" and observed.boot_uuid and observed.continuous_ns is not None
    root = Path(__file__).resolve().parents[2]
    assert Path(host.__file__).resolve() == root / "stackos/controlled_process_host.py"
    assert identity._inspect_process(os.getpid()).status == "known"
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    seed = state / "seed.bin"
    seed.write_bytes(b"s" * 32)
    seed.chmod(0o600)
    settings = Settings(state_dir=state, data_dir=tmp_path / "data", controlled_host_enabled=True)
    monkeypatch.setattr("stackos.config.get_settings", lambda: settings)
    monkeypatch.setattr(aes_gcm, "_default_seed_path", seed)
    monkeypatch.setattr(aes_gcm, "_cache", {})
    owner = _FixtureOwner(state, observed, tmp_path)
    try:
        yield owner
    finally:
        owner.finalize()


def setup(case, native_storage):
    from stackos.config import get_settings

    case.settings = get_settings()
    _, observed = native_storage
    return prepare(case, boot=observed.boot_uuid, deadline=observed.continuous_ns + 5_000_000_000)


def save(tmp_path, name, result):
    payload = {
        "schema_version": "stackos.native-case-evidence.v1",
        "case": name,
        "state": result.state,
        "released": result.release_sent,
        "certified": result.certified,
        "cleanup_known": result.cleanup_known,
        "issues": list(result.issues),
        "events": list(result.evidence),
        "attempt_id": result.cursor.attempt.id,
        "control_revision": result.cursor.control_revision,
    }
    (tmp_path / (name + ".json")).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return payload


def events(result, name):
    return [item for item in result.evidence if item.get("event") == name]


def test_native_barrier_receipt_and_cleanup(case_factory, native_storage, tmp_path):
    case = case_factory()
    setup(case, native_storage)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "normal", result)
    assert result.state == "cleaned" and result.release_sent and result.arm_receipt_digest
    assert result.certified is False
    ordered = [item["event"] for item in result.evidence]
    assert (
        ordered.index("d_writer_close_ack")
        < ordered.index("a_received")
        < ordered.index("w_writer_closed")
        < ordered.index("anchor_filled")
        < ordered.index("release_sent")
    )
    assert events(result, "writer_closed")[0]["fds"]["handles"]["writer"]["open"] is False
    assert events(result, "w_writer_closed")[0]["fds"]["handles"]["writer"]["open"] is False
    assert events(result, "anchor_filled")[0]["writer_closed"] is True
    assert events(result, "worker_inert")[0]["fds"]["protected_handles_absent"] is True
    assert events(result, "worker_consumed_barrier")[0]["control_fds_closed"] is True
    assert all(events(result, "cleanup")[0]["reaped"].values())


def _certify_native_result(case, result):
    from stackos.repositories.runs import RunRepository

    owner = result.terminal_owner
    assert owner is not None and owner.authenticated is not None
    assert owner.read.state == owner.directory.state == "closed"
    assert owner.slot.close_state == "CLOSED" and owner.transfer_directory.state == "closed"
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
    certificate = repo._certify_owner_quiescence(
        project_id=case.project,
        canonical_item_id=row.canonical_item_id,
        owner_ref=row.owner_ref,
        expected_epoch=row.epoch,
        expected_revision=row.revision,
        evidence_ref="native:certificate",
        verified=verified,
    )
    assert certificate.quiescence_revision == certificate.revision
    return certificate


def test_native_terminal_file_is_authenticated_before_certification(
    case_factory, native_storage, tmp_path
):
    case = case_factory()
    setup(case, native_storage)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "terminal-authenticated", result)
    assert result.cleanup_known and result.release_sent
    certificate = _certify_native_result(case, result)
    (tmp_path / "terminal-certificate.json").write_text(
        json.dumps(
            {
                "quiescence_ref": certificate.quiescence_ref,
                "revision": certificate.revision,
                "terminal_digest": result.terminal_owner.authenticated.digest,
                "worker_acceptance": False,
                "public_activation": False,
            },
            sort_keys=True,
        )
        + "\n"
    )


def test_native_cancelled_terminal_preserves_held_execution_and_known_quiescence(
    case_factory, native_storage, tmp_path, monkeypatch
):
    from copy import deepcopy

    from stackos.db.models import Run, TrackerTask
    from stackos.repositories.base import ConflictError
    from stackos.repositories.runs import RunRepository

    case = case_factory()
    setup(case, native_storage)
    send = host._Channel.send

    def stop_release(channel, kind, payload, deadline):
        if kind == "release":
            case.stop()
            raise host._ControlError("native_fixture_stop")
        return send(channel, kind, payload, deadline)

    monkeypatch.setattr(host._Channel, "send", stop_release)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "terminal-cancelled", result)
    assert result.state == "held" and "native_fixture_stop" in result.issues
    assert not result.release_sent and result.cleanup_known and result.certified is False
    assert events(result, "worker_inert")
    assert not events(result, "release_sent") and not events(result, "worker_consumed_barrier")
    assert all(events(result, "cleanup")[0]["reaped"].values())
    owner = result.terminal_owner
    assert owner is not None and owner.authenticated is not None
    state, _ = native_storage
    report_path = (
        state
        / "controlled-process"
        / f"{case.project}-{owner.original.binding.attempt_run_id}"
        / "watchdog-observation.json"
    )
    watchdog = json.loads(report_path.read_bytes())
    fact = owner.authenticated.fact
    # A held, known-inert cancellation is distinct from observation uncertainty.
    # Any already latched unknown stays sticky; this path never asserted one.
    assert watchdog["released"] is False and watchdog["cleanup_known"] is True
    assert watchdog["execution_unknown"] is False
    assert fact.execution_unknown is watchdog["execution_unknown"]
    assert ("native_execution_unknown" in result.issues) is fact.execution_unknown
    assert host._digest(watchdog) == fact.report_digest
    repo = RunRepository(case.session)

    def stopped_forward_authority():
        task = case.session.get(TrackerTask, case.task, populate_existing=True)
        assert task.metadata_json["ownership_continuity"]["stopped"] is True
        current = repo.owner_get(project_id=case.project, canonical_item_id=f"task:{case.task}")
        with pytest.raises(
            ConflictError, match=r"^original/current stop or denial remains binding$"
        ):
            repo.owner_authority(
                project_id=case.project,
                canonical_item_id=current.canonical_item_id,
                ownership=current,
                require_budget=True,
            )
        return current, deepcopy(task.metadata_json)

    before_owner, before_task = stopped_forward_authority()
    before_run = deepcopy(
        case.session.get(
            Run, owner.original.binding.attempt_run_id, populate_existing=True
        ).model_dump()
    )
    certificate = _certify_native_result(case, result)
    after_owner, after_task = stopped_forward_authority()
    assert after_task == before_task
    assert after_owner == certificate
    assert (
        certificate.owner_ref == before_owner.owner_ref and certificate.epoch == before_owner.epoch
    )
    assert certificate.continuity_json == before_owner.continuity_json
    assert certificate.revision == before_owner.revision + 1
    assert (
        case.session.get(
            Run, owner.original.binding.attempt_run_id, populate_existing=True
        ).model_dump()
        == before_run
    )
    (tmp_path / "terminal-cancelled-certificate.json").write_text(
        json.dumps(
            {
                "state": result.state,
                "released": False,
                "cleanup_known": True,
                "execution_unknown": fact.execution_unknown,
                "canonical_task_stopped": True,
                "forward_denied_before_and_after_certificate": True,
                "quiescence_ref": certificate.quiescence_ref,
                "production_completion": False,
                "worker_acceptance": False,
                "public_activation": False,
            },
            sort_keys=True,
        )
        + "\n"
    )


def test_native_controller_loss_terminal_replays_without_forward_authority(
    case_factory, native_storage, tmp_path, monkeypatch
):
    _controller_exit_case(case_factory, native_storage, tmp_path, monkeypatch, late=True)
    evidence = json.loads((tmp_path / "controller-exit.json").read_text())
    assert evidence["independent_W_exit"]["note_exit"] is True
    assert evidence["released"] is True and evidence["certified"] is False
    assert evidence["terminal_recovery"]["state"] == "recorded"
    assert evidence["terminal_recovery"]["facts_revision"] == 1
    assert evidence["terminal_recovery"]["replay_zero_writes"] is True


def test_native_cancel_before_release(case_factory, native_storage, tmp_path, monkeypatch):
    case = case_factory()
    setup(case, native_storage)
    original = host._Channel.send

    def stop_before_fill(channel, kind, payload, deadline):
        if kind == "fill":
            case.stop()
            raise host._ControlError("native_fixture_stop")
        return original(channel, kind, payload, deadline)

    monkeypatch.setattr(host._Channel, "send", stop_before_fill)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "cancel-before-release", result)
    assert not result.release_sent and not events(result, "worker_consumed_barrier")
    assert events(result, "cleanup") and all(events(result, "cleanup")[0]["reaped"].values())


def test_native_watchdog_deadline_escalates_ignoring_anchor(
    case_factory, native_storage, tmp_path, monkeypatch
):
    case = case_factory()
    setup(case, native_storage)
    original = host._Channel.send

    def withhold_release(channel, kind, payload, deadline):
        if kind == "release":
            return None  # Transport-loss fixture, not a different command/guard.
        return original(channel, kind, payload, deadline)

    monkeypatch.setattr(host._Channel, "send", withhold_release)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "deadline", result)
    actual = events(result, "signal")
    assert [x["signal"] for x in actual] == [int(signal.SIGTERM), int(signal.SIGKILL)]
    assert all(x["known"] for x in actual)
    assert not events(result, "worker_consumed_barrier")
    assert all(events(result, "cleanup")[0]["reaped"].values())


def test_native_watchdog_survives_fixture_controller_exit(
    case_factory, native_storage, tmp_path, monkeypatch
):
    _controller_exit_case(case_factory, native_storage, tmp_path, monkeypatch)


def _controller_exit_case(case_factory, native_storage, tmp_path, monkeypatch, *, late=False):
    case = case_factory()
    policy = setup(case, native_storage)
    state, _ = native_storage
    read_end, write_end = multiprocessing.get_context("fork").Pipe(duplex=False)
    ack_read, ack_write = multiprocessing.get_context("fork").Pipe(duplex=False)
    original_receive = host._Channel.receive

    def child_controller():
        read_end.close()
        ack_write.close()

        def lose_controller(channel, **kwargs):
            value = original_receive(channel, **kwargs)
            if value is not None and value[0] == "ready":
                write_end.send({"watchdog": value[1]["identities"]["watchdog"]})
                if (
                    not ack_read.poll(host._remaining(policy.deadline_continuous_ns, float("inf")))
                    or ack_read.recv() is not True
                ):
                    raise host._ControlError("native_fixture_monitor_not_admitted")
                if not late:
                    os._exit(0)
            if value is not None and value[0] == "released" and late:
                os._exit(0)
            return value

        host._Channel.receive = lose_controller
        with admitted(case) as authority, host._NativeCustody(authority) as owned:
            asyncio.run(host._run_native_control(authority, custody=owned))
        (tmp_path / "controller-ack-denial-accounted.json").write_text(
            json.dumps(
                {
                    **owned.snapshot(),
                    "watchdog_returncode": owned.process.returncode
                    if owned.process is not None
                    else None,
                    "product_success": False,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        os._exit(2)

    controller = multiprocessing.get_context("fork").Process(target=child_controller)
    monitor = None
    acknowledged = False
    try:
        controller.start()
        write_end.close()
        ack_read.close()
        assert read_end.poll(host._remaining(policy.deadline_continuous_ns, float("inf")))
        received = read_end.recv()
        expected = identity._ProcessIdentity(**received["watchdog"])
        monitor = _ExactExitWatch(expected, tmp_path / "W-monitor-held-failure.json")
        # D remains held through both birth checks and kernel registration.
        ack_write.send(True)
        acknowledged = True
        controller.join(
            timeout=host._remaining(policy.cleanup_deadline_continuous_ns, float("inf"))
        )
        assert controller.exitcode == 0
        from sqlmodel import select as sql_select

        from stackos.db.models import Run

        case.session.expire_all()
        attempt = case.session.exec(sql_select(Run).where(Run.parent_run_id == case.run)).one()
        report_path = (
            state
            / "controlled-process"
            / f"{case.project}-{attempt.id}"
            / "watchdog-observation.json"
        )
        report = None
        while host._remaining(policy.cleanup_deadline_continuous_ns, 0.01) > 0:
            monitor.observe(host._remaining(policy.cleanup_deadline_continuous_ns, 0.01))
            if report_path.exists():
                with suppress(json.JSONDecodeError):
                    report = json.loads(report_path.read_bytes())
            if report is not None and monitor.exited:
                break
        assert report is not None and report["cleanup_known"] is True
        assert monitor.exited is True
        assert report["released"] is late and report["certified"] is False
        report["independent_W_exit"] = {
            "registered_identity": received["watchdog"],
            "note_exit": True,
            "parent_test_reaped_nonchild": False,
        }
        case.session.expire_all()
        recovered = host._recover_native_terminal(
            case.session,
            project_id=case.project,
            canonical_item_id=f"task:{case.task}",
            attempt_run_id=attempt.id,
            evidence_ref="native:controller-loss-read",
            exit_witness=host._OriginalExitWitness(expected, None, monitor=monitor)
            if late
            else None,
        )
        assert recovered.state == ("recorded" if late else "held")
        report["terminal_recovery"] = {
            "state": recovered.state,
            "facts_revision": recovered.facts_revision,
            "issues": list(recovered.issues),
            "forward_revived": False,
        }
        if late:
            from copy import deepcopy

            from tests.integration.test_controlled_process_terminal import database_snapshot

            witness = host._OriginalExitWitness(expected, None, monitor=monitor)
            before_replay = database_snapshot(case.session)
            replay = host._recover_native_terminal(
                case.session,
                project_id=case.project,
                canonical_item_id=f"task:{case.task}",
                attempt_run_id=attempt.id,
                evidence_ref="native:controller-loss-read",
                exit_witness=witness,
            )
            assert replay.state == "replayed" and replay.facts_revision == 1
            assert database_snapshot(case.session) == before_replay
            report["terminal_recovery"]["replay_zero_writes"] = True
            report["terminal_recovery"]["certificate_ref"] = (
                recovered.certificate.quiescence_ref if recovered.certificate else None
            )
            from stackos import controlled_process_receipt as receipt

            terminal_path = (
                report_path.parent
                / "terminal"
                / receipt._name(
                    receipt._Binding(**attempt.metadata_json["native_recovery_private"]["binding"])
                )
            )
            close_evidence = []
            # These are NEW read capabilities under the actual registered exit
            # witness; no original writer/slot is reconstructed by the fixture.
            for role, path in (
                ("directory", terminal_path.parent),
                ("read", terminal_path),
                ("report", report_path),
            ):
                target = path.stat()
                for close_fault in ("error", "interrupt"):
                    closes = []
                    real_close = os.close
                    failure = (
                        OSError(errno.EIO, "synthetic read close")
                        if close_fault == "error"
                        else KeyboardInterrupt("synthetic read close")
                    )

                    def fail_close(
                        fd, *, target=target, closes=closes, failure=failure, real_close=real_close
                    ):
                        value = os.fstat(fd)
                        real_close(fd)
                        if (value.st_dev, value.st_ino) == (target.st_dev, target.st_ino):
                            closes.append(fd)
                            raise failure

                    before_close = database_snapshot(case.session)
                    with monkeypatch.context() as local:
                        local.setattr(os, "close", fail_close)
                        held = host._recover_native_terminal(
                            case.session,
                            project_id=case.project,
                            canonical_item_id=f"task:{case.task}",
                            attempt_run_id=attempt.id,
                            evidence_ref="native:controller-loss-read",
                            exit_witness=witness,
                        )
                        assert held.state == "held" and "terminal_custody_unknown" in held.issues
                        unknown = [cap for cap in held.held_reads if cap.state == "unknown"]
                        assert len(unknown) == len(closes) == 1 and unknown[0].fd == closes[0]
                        assert unknown[0].close() is False and len(closes) == 1
                    assert database_snapshot(case.session) == before_close
                    close_evidence.append(
                        {
                            "role": role,
                            "fault": close_fault,
                            "held": True,
                            "close_attempts": 1,
                            "owner_retained": True,
                        }
                    )
            interruption = KeyboardInterrupt("synthetic retained read interruption")

            def interrupt_read(*args, **kwargs):
                raise interruption

            before_interrupt = database_snapshot(case.session)
            with monkeypatch.context() as local:
                local.setattr(receipt, "_read_opened", interrupt_read)
                with pytest.raises(KeyboardInterrupt) as caught:
                    host._recover_native_terminal(
                        case.session,
                        project_id=case.project,
                        canonical_item_id=f"task:{case.task}",
                        attempt_run_id=attempt.id,
                        evidence_ref="native:controller-loss-read",
                        exit_witness=witness,
                    )
            assert caught.value is interruption
            retained = interruption.__dict__["_stackos_terminal_read_custody"]
            assert len(retained) == 2 and all(cap.state == "closed" for cap in retained)
            assert database_snapshot(case.session) == before_interrupt
            report["terminal_recovery"]["read_close_faults"] = close_evidence
            report["terminal_recovery"]["interrupted_read_owners_accounted"] = True
            original_metadata = deepcopy(
                case.session.get(Run, attempt.id, populate_existing=True).metadata_json
            )
            fault_evidence = []
            for fault, expected_issue in (
                ("missing-key", "terminal_key_unknown"),
                ("foreign-key", "terminal_key_unknown"),
                ("boot-mismatch", "terminal_clock_unknown"),
                ("policy-drift", "terminal_control_unknown"),
                ("report-fifo", "terminal_report_unknown"),
                ("partial-file", "terminal_file_unknown"),
                ("terminal-fifo", "terminal_file_unknown"),
            ):
                row = case.session.get(Run, attempt.id, populate_existing=True)
                altered = deepcopy(original_metadata)
                if fault == "missing-key":
                    del altered["native_recovery_private"]["wrapped_key"]
                elif fault == "foreign-key":
                    box, nonce = aes_gcm.encrypt(
                        b"F" * 32, project_id=case.project, kind="controlled-host-key:" + "0" * 64
                    )
                    altered["native_recovery_private"]["wrapped_key"] = {
                        "ciphertext": box.hex(),
                        "nonce": nonce.hex(),
                    }
                elif fault == "boot-mismatch":
                    altered["native_recovery_private"]["policy"]["boot_uuid"] = (
                        "22222222-2222-4222-8222-222222222222"
                    )
                elif fault == "policy-drift":
                    altered["native_recovery_private"]["policy"]["deadline_continuous_ns"] += 1
                elif fault == "report-fifo":
                    report_backup = report_path.with_name("watchdog-fixture-backup.json")
                    report_path.rename(report_backup)
                    os.mkfifo(report_path, mode=0o600)
                elif fault == "terminal-fifo":
                    terminal_path.unlink()
                    os.mkfifo(terminal_path, mode=0o600)
                else:
                    binding = altered["native_recovery_private"]["binding"]
                    terminal_path = (
                        report_path.parent / "terminal" / receipt._name(receipt._Binding(**binding))
                    )
                    terminal_path.write_bytes(
                        b"{"
                    )  # disposable hostile-corruption negative, last only
                row.metadata_json = altered
                case.session.add(row)
                case.session.commit()
                before = database_snapshot(case.session)
                held = host._recover_native_terminal(
                    case.session,
                    project_id=case.project,
                    canonical_item_id=f"task:{case.task}",
                    attempt_run_id=attempt.id,
                    evidence_ref="native:controller-loss-read",
                    exit_witness=witness,
                )
                assert held.state == "held" and expected_issue in held.issues
                assert database_snapshot(case.session) == before
                if fault == "report-fifo":
                    report_path.unlink()
                    report_backup.rename(report_path)
                fault_evidence.append(
                    {
                        "fault": fault,
                        "state": held.state,
                        "issues": list(held.issues),
                        "no_mutation": True,
                    }
                )
                # Fixture-only DB reset between isolated corruption observations;
                # the product recovery never repairs or rewrites original bytes.
                row = case.session.get(Run, attempt.id, populate_existing=True)
                row.metadata_json = deepcopy(original_metadata)
                case.session.add(row)
                case.session.commit()
            report["terminal_recovery"]["fault_observations"] = fault_evidence
        (tmp_path / "controller-exit.json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n"
        )
    finally:
        try:
            if not acknowledged:
                with suppress(BrokenPipeError, EOFError):
                    ack_write.send(False)
            ack_write.close()
            read_end.close()
            (tmp_path / "controller-teardown.json").write_text(
                json.dumps(
                    {
                        "pid": controller.pid,
                        "exitcode_before_join": controller.exitcode,
                        "product_success": False,
                    }
                )
                + "\n"
            )
        finally:
            try:
                if controller.pid is not None:
                    _join_owned_controller(controller, tmp_path / "controller-join-held.json")
                    assert controller.exitcode is not None
                    (tmp_path / "controller-postjoin.json").write_text(
                        json.dumps(
                            {
                                "pid": controller.pid,
                                "exitcode_after_join": controller.exitcode,
                                "acknowledged": acknowledged,
                                "product_success": False,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
            finally:
                if monitor is not None:
                    try:
                        (tmp_path / "W-monitor-held.json").write_text(
                            json.dumps(
                                {
                                    "pid": monitor.expected.pid,
                                    "note_exit_seen": monitor.exited,
                                    "product_success": False,
                                }
                            )
                            + "\n"
                        )
                    finally:
                        monitor.finalize()


def test_native_lost_anchor_never_signals_stale_group(
    case_factory, native_storage, tmp_path, monkeypatch
):
    case = case_factory()
    setup(case, native_storage)
    receive = host._Channel.receive
    removed = []

    def terminate_owned_anchor(channel, **kwargs):
        value = receive(channel, **kwargs)
        if value is not None and value[0] == "ready" and not removed:
            anchor = identity._ProcessIdentity(**value[1]["identities"]["anchor"])
            assert host._matches(anchor)
            os.kill(anchor.pid, signal.SIGKILL)  # Only this fresh exact registered fixture.
            removed.append(anchor)
        return value

    monkeypatch.setattr(host._Channel, "receive", terminate_owned_anchor)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "anchor-loss", result)
    assert removed and not result.release_sent
    assert result.cleanup_known is True
    assert all(events(result, "cleanup")[0]["reaped"].values())
    # W's sampled identity guard refuses a stale PGID after losing its anchor.
    assert not any(x.get("known") for x in events(result, "signal"))
    assert not events(result, "worker_consumed_barrier")


def test_native_invalid_release_mac_keeps_barrier_closed(
    case_factory, native_storage, tmp_path, monkeypatch
):
    case = case_factory()
    setup(case, native_storage)
    encode = host._frame

    def corrupt(kind, payload, key, **kwargs):
        raw = encode(kind, payload, key, **kwargs)
        if kind == "release":
            value = json.loads(raw)
            value["mac"] = "0" * 64
            return host._json(value) + b"\n"
        return raw

    monkeypatch.setattr(host, "_frame", corrupt)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "invalid-mac", result)
    assert not events(result, "release_sent") and not events(result, "worker_consumed_barrier")
    assert all(events(result, "cleanup")[0]["reaped"].values())


@pytest.mark.parametrize("hop", ["D-to-W", "W-to-A"])
def test_native_handoff_ack_loss_holds_fill(
    case_factory, native_storage, tmp_path, monkeypatch, hop
):
    case = case_factory()
    fixture_record = None
    if hop == "W-to-A":
        wrapper = tmp_path / "watchdog-ack-loss-fixture.py"
        observation = tmp_path / "actual-anchor-received.json"
        root = Path(host.__file__).resolve().parents[1]
        wrapper.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            + "sys.path.insert(0,"
            + repr(str(root))
            + ")\n"
            + "from stackos import controlled_process_shim as shim\n"
            + "original=shim._wait_message\n"
            + "def lose_ack(channel,deadline):\n"
            + "    value=original(channel,deadline)\n"
            + "    if value[0]=='received' and 'bootstrap_fds' in value[1]:\n"
            + (
                "        safe={'event':'actual_A_to_W_ack_withheld',"
                "'identity':value[1]['identity'],'fds':value[1]['fds'],"
                "'bootstrap_fds':value[1]['bootstrap_fds']}\n"
            )
            + "        Path("
            + repr(str(observation))
            + ").write_text(json.dumps(safe,sort_keys=True))\n"
            + "        raise shim._ControlError('native_fixture_a_ack_lost')\n"
            + "    return value\n"
            + "shim._wait_message=lose_ack\n"
            + "raise SystemExit(shim._entry(sys.argv[1],tuple(int(x) for x in sys.argv[2:])))\n"
        )
        original_manifest = host._native_manifest

        def negative_manifest(command):
            manifest = original_manifest(command)
            manifest["watchdog_entry"] = str(wrapper.resolve())
            manifest["watchdog_entry_sha256"] = hashlib.sha256(wrapper.read_bytes()).hexdigest()
            return manifest

        # This deliberately modified runtime is bound by the genuine approval
        # BEFORE admission. Production A/T/shim/body/source remain unchanged.
        monkeypatch.setattr(host, "_native_manifest", negative_manifest)
        fixture_record = {
            "path": str(wrapper.resolve()),
            "sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            "argv_prefix": [
                str(Path(sys.executable).resolve()),
                "-I",
                "-S",
                str(wrapper.resolve()),
                "watchdog",
            ],
        }
    setup(case, native_storage)
    receive = host._Channel.receive
    lost = []

    def lose_ack(channel, **kwargs):
        value = receive(channel, **kwargs)
        expected = "received"
        if hop == "D-to-W" and value is not None and value[0] == expected and not lost:
            lost.append(expected)
            raise host._ControlError("native_fixture_ack_lost")
        return value

    monkeypatch.setattr(host._Channel, "receive", lose_ack)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, "ack-" + hop, result)
    if fixture_record is not None:
        observed = json.loads(observation.read_text())
        assert observed["event"] == "actual_A_to_W_ack_withheld"
        assert observed["fds"]["handles"]["writer"]["open"] is True
        assert observed["bootstrap_fds"]["handles"]["key"]["open"] is False
        fixture_record["actual_observation"] = observed
        fixture_record["runtime_digest"] = result.cursor.manifest_digest
        (tmp_path / "negative-runtime-binding.json").write_text(
            json.dumps(fixture_record, sort_keys=True, indent=2) + "\n"
        )
    else:
        assert lost
    assert not result.release_sent and not events(result, "anchor_filled")
    assert not events(result, "worker_consumed_barrier")
    # D-to-W loss may happen before any A/T exists; even then the W receipt
    # must report its exact owned-child cleanup rather than invent registered IDs.
    assert result.cleanup_known is True
    assert all(events(result, "cleanup")[0]["reaped"].values())


@pytest.mark.parametrize("loss", ["owner-stop", "grant-loss"])
def test_native_safety_cleanup_after_authority_loss(
    case_factory, native_storage, tmp_path, monkeypatch, loss
):
    case = case_factory()
    setup(case, native_storage)
    receive = host._Channel.receive
    changed = []

    def change_after_release(channel, **kwargs):
        value = receive(channel, **kwargs)
        if value is not None and value[0] == "released" and not changed:
            case.stop() if loss == "owner-stop" else lose_grant(case)
            changed.append(loss)
        return value

    monkeypatch.setattr(host._Channel, "receive", change_after_release)
    with admitted(case) as authority:
        result = asyncio.run(
            host._run_native_control(authority, custody=native_storage.custody(authority))
        )
    save(tmp_path, loss, result)
    assert changed and result.release_sent and result.certified is False
    assert all(events(result, "cleanup")[0]["reaped"].values())
