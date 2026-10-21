"""Portable control protocol and fail-closed logic; no child/native observations."""

import errno
import json
import os
import signal
import threading
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from stackos import controlled_process_clock as clock
from stackos import controlled_process_host as host
from stackos import controlled_process_identity as identity
from stackos import controlled_process_receipt as receipt
from stackos import controlled_process_shim as shim
from stackos.actions.controlled_command import _resolve_controlled_command
from stackos.repositories.runs import RunOut

BOOT = "11111111-1111-4111-8111-111111111111"
KEY = b"k" * 32
DIGEST = "a" * 64


@pytest.mark.parametrize(
    "fault", ["read-open", "validation", "close-error", "read-interrupt", "close-interrupt", "fifo"]
)
def test_terminal_allocation_retains_actual_close_owner(tmp_path, monkeypatch, fault):
    # This is actual local FD custody only, not a substitute admission witness.
    custody = host._NativeCustody(None)
    binding = receipt._Binding(
        1,
        "task:1",
        "fixture:owner",
        1,
        2,
        DIGEST,
        1,
        1,
        1,
        2,
        "fixture:intent",
        DIGEST,
        "fixture:host",
        BOOT,
    )
    allocate, real_open, real_close, safe = (
        receipt._allocate_terminal_slot,
        os.open,
        os.close,
        receipt._safe_fd,
    )
    held, close_calls = [], []
    fifo_reads = []
    interruption = KeyboardInterrupt("synthetic allocation interruption")

    def retain(*args):
        slot = allocate(*args)
        assert type(slot) is receipt._AllocatedSlot
        held.append(slot)
        # Preserve S2's real supports_dir_fd discovery before injecting the
        # subsequent host-owned read-open boundary fault.
        monkeypatch.setattr(os, "open", open_read)
        return slot

    def open_read(path, flags, *args, **kwargs):
        if held and path == held[0].name and flags & os.O_ACCMODE == os.O_RDONLY:
            if fault == "read-open":
                raise OSError(errno.EIO, "synthetic read-open failure")
            if fault == "read-interrupt":
                raise interruption
            if fault == "fifo":
                os.unlink(path, dir_fd=kwargs["dir_fd"])
                os.mkfifo(path, mode=0o600, dir_fd=kwargs["dir_fd"])
                assert flags & os.O_NONBLOCK
                fd = real_open(path, flags, *args, **kwargs)
                fifo_reads.append(os.fstat(fd).st_mode)
                return fd
        return real_open(path, flags, *args, **kwargs)

    def validate(fd, mode):
        if held and fd != held[0].fd and mode == os.O_RDONLY:
            return False
        return safe(fd, mode)

    def close(fd):
        if held and fd == held[0].fd:
            close_calls.append(fd)
            real_close(fd)
            if fault == "close-error":
                raise OSError(errno.EIO, "synthetic close failure")
            if fault == "close-interrupt":
                raise interruption
            return
        real_close(fd)

    monkeypatch.setattr(receipt, "_allocate_terminal_slot", retain)
    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(receipt, "_safe_fd", validate)
    expected = (
        KeyboardInterrupt
        if fault == "read-interrupt"
        else (OSError if fault == "read-open" else host._ControlError)
    )
    with pytest.raises(expected) as caught:
        host._allocate_terminal_owner(
            None,
            binding=binding,
            policy=policy(),
            manifest={},
            wrapped_key={},
            key=KEY,
            custody=custody,
            attempt_path=tmp_path,
        )
    if fault == "read-interrupt":
        assert caught.value is interruption
    elif fault != "read-open":
        assert str(caught.value) == "terminal_custody_unknown"
    assert len(held) == 1 and custody.terminal_slot is held[0]
    assert custody.terminal_owner is None
    state = {"close-error": "FAILED", "close-interrupt": "UNKNOWN"}.get(fault, "CLOSED")
    assert held[0].close_state == state and close_calls == [held[0].fd]
    assert custody.close_accounted() is (state == "CLOSED")
    assert close_calls == [held[0].fd]  # no recycled-integer close retry
    assert all(fd.state == "closed" for fd in custody.owned_fds)
    assert custody.snapshot()["terminal_close_state"] == state
    if fault == "fifo":
        assert len(fifo_reads) == 1 and host.stat.S_ISFIFO(fifo_reads[0])


def policy(**changes):
    value = dict(
        schema_version="stackos.controlled-host-approval.v1",
        command_digest=DIGEST,
        runtime_digest=DIGEST,
        boot_uuid=BOOT,
        deadline_continuous_ns=100,
        term_grace_ns=10,
        cleanup_deadline_continuous_ns=200,
        max_output_bytes=65536,
        member_limit=8,
        threat_model="cooperative-fixed-command",
    )
    value.update(changes)
    return host._NativePolicy.from_decision(value, command_digest=DIGEST, runtime_digest=DIGEST)


def closed_key_copies():
    # Actual local close observations, never fabricated successful OS custody.
    descriptors = [os.pipe() for _ in range(2)]
    records = []
    for readfd, writefd in descriptors:
        read = host._close_key_copy(host._OwnedFD(readfd))
        write = host._close_key_copy(host._OwnedFD(writefd))
        # Simulated receiver shares the inherited read number and pipe object;
        # both parent records above came from actual local closes.
        records.extend((read, write, read))
    return records


def worker_handoff(allowed):
    return {
        "handles": host._fd_observation("fixture_handoff", allowed)["handles"],
        "pass_fds": list(allowed.values()),
        "close_fds": True,
    }


def synthetic_bootstrap_history():
    # The spawn-boundary tests below substitute Popen and the role channel.
    before = {"open": True, "fd": 999, "device": 1, "inode": 2, "close_on_exec": False}
    read = {"before": before, "after": {"open": False, "fd": 999}}
    write = {
        "before": {**before, "fd": 998},
        "after": {"open": False, "fd": 998},
    }
    return {
        "closed_key_object": before,
        "handles": {"key": read["after"]},
        "parent_key_closes": {"read": read, "write": write},
    }


@pytest.fixture(autouse=True)
def no_native(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unit native/process effect forbidden")

    monkeypatch.setattr(identity, "_load_native", forbidden)
    monkeypatch.setattr(clock, "_load_native", forbidden)
    monkeypatch.setattr(shim.subprocess, "Popen", forbidden)


@pytest.mark.parametrize("fault", ["extra-key", "bad-mac", "replayed-sequence", "wrong-binding"])
def test_frames_reject_untrusted_input(fault):
    raw = host._frame("cancel", {}, KEY, sequence=0, binding_digest=DIGEST, manifest_digest=DIGEST)
    sequence, binding = 0, DIGEST
    if fault in {"extra-key", "bad-mac"}:
        value = json.loads(raw)
        if fault == "extra-key":
            value["extra"] = True
        else:
            value["mac"] = "0" * 64
        raw = host._json(value) + b"\n"
    elif fault == "replayed-sequence":
        sequence = 1
    else:
        binding = "b" * 64
    with pytest.raises(host._ControlError, match="native_frame_invalid"):
        host._decode_frame(
            raw, KEY, sequence=sequence, binding_digest=binding, manifest_digest=DIGEST
        )


@pytest.mark.parametrize(
    "fault", ["wrong-boot", "expired-equal", "negative-grace", "cleanup-before-deadline"]
)
def test_product_policy_is_fail_closed(fault):
    if fault in {"negative-grace", "cleanup-before-deadline"}:
        with pytest.raises(host._ControlError):
            policy(
                **(
                    {"term_grace_ns": -1}
                    if fault == "negative-grace"
                    else {"cleanup_deadline_continuous_ns": 99}
                )
            )
        return
    observed = clock._ClockObservation("known", BOOT, 100, 99, runtime_abi=clock._SUPPORTED_ABI)
    if fault == "wrong-boot":
        observed = replace(observed, boot_uuid="22222222-2222-4222-8222-222222222222")
    with pytest.raises(host._ControlError):
        policy().before_release(observed)


@pytest.mark.parametrize("fault", ["unknown", "birth-mismatch", "pgid-mismatch", "already-reaped"])
def test_signal_requires_fresh_original_identity(monkeypatch, fault):
    original = identity._ProcessIdentity(BOOT, 100, 10, 100, 10, 1000, 1)
    observed = identity._ProcessObservation("unknown", issues=("unavailable",))
    if fault == "birth-mismatch":
        observed = identity._ProcessObservation("known", replace(original, start_microseconds=2))
    elif fault == "pgid-mismatch":
        observed = identity._ProcessObservation("known", replace(original, pgid=101))
    monkeypatch.setattr(identity, "_inspect_process", lambda pid: observed)
    signals = []
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))
    result = host._signal_owned_group(original, (original,), signal.SIGTERM)
    assert result.status == "unknown" and signals == []


@pytest.mark.parametrize(
    ("fault", "stage", "issue"),
    [
        ("known", "complete", None),
        ("anchor", "anchor_identity", "native_anchor_unknown"),
        ("native-api", "native_api", "native_group_unavailable"),
        ("member-list", "member_list", "native_group_unavailable"),
        ("incomplete", "member_list", "native_group_incomplete"),
        ("member-set", "member_set", "native_group_changed"),
        ("member-identity", "member_identity", "native_identity_unknown"),
        ("unsafe-identity-diagnostic", "member_identity", "native_group_unavailable"),
        ("identity-recheck", "identity_recheck", "native_group_changed"),
        ("member-scope", "member_scope", "native_group_changed"),
        ("signal-denied", "signal", "native_signal_denied"),
        ("missing-owner", "signal_admission", "native_signal_admission_denied"),
    ],
)
def test_group_signal_diagnostics_use_existing_observations_and_fixed_codes(
    monkeypatch, fault, stage, issue
):
    anchor = identity._ProcessIdentity(BOOT, 100, 10, 100, 10, 1000, 1)
    worker = replace(anchor, pid=101, parent_pid=100)
    queries, signals, matches = [], [], []

    def match(original):
        matches.append(original.pid)
        return fault != "anchor" and not (fault == "identity-recheck" and len(matches) == 2)

    def listing(kind, pgid, array, capacity):
        queries.append((kind, pgid, capacity))
        if fault == "member-list":
            raise OSError(errno.EPERM, "synthetic-private-text")
        if fault == "incomplete":
            return 0
        pids = (100,) if fault == "member-set" else (100, 101)
        for i, pid in enumerate(pids):
            array[i] = pid
        return len(pids) * host.ctypes.sizeof(host.ctypes.c_int)

    def native():
        if fault == "native-api":
            raise OSError(errno.EPERM, "synthetic-private-text")
        return SimpleNamespace(proc_listpids=listing)

    def known(pid):
        if fault in {"member-identity", "unsafe-identity-diagnostic"}:
            raise host._ControlError(
                "native_identity_unknown"
                if fault == "member-identity"
                else "synthetic-private-text"
            )
        value = anchor if pid == anchor.pid else worker
        return replace(value, pgid=102) if fault == "member-scope" else value

    def killpg(pgid, signum):
        signals.append((pgid, signum))
        if fault == "signal-denied":
            raise PermissionError(errno.EPERM, "synthetic-private-text")

    monkeypatch.setattr(host, "_matches", match)
    monkeypatch.setattr(identity, "_load_native", native)
    monkeypatch.setattr(host, "_known_process", known)
    monkeypatch.setattr(os, "killpg", killpg)
    owner = watchdog_pair()
    owner.identities = {"anchor": anchor, "worker": worker}
    owner.policy = policy()
    owner.timer = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(shim, "_now", lambda: 1)
    snapshot, registered, _ = owner.snapshot()
    result = host._signal_owned_group(
        anchor,
        (anchor, worker),
        signal.SIGTERM,
        owner=None if fault == "missing-owner" else owner,
        custody_snapshot=snapshot,
        registered=registered,
    )
    assert result.stage == stage
    assert result.status == ("known" if fault == "known" else "unknown")
    assert result.issues == (() if issue is None else (issue,))
    assert result.expected_pids == (() if fault == "anchor" else (100, 101))
    assert result.returned_pids == (
        ()
        if fault in {"anchor", "native-api", "member-list", "incomplete"}
        else (100,)
        if fault == "member-set"
        else (100, 101)
    )
    assert len(queries) == (0 if fault in {"anchor", "native-api"} else 1)
    assert signals == ([(100, signal.SIGTERM)] if fault in {"known", "signal-denied"} else [])
    assert result.members == ((anchor, worker) if fault == "known" else ())
    assert "synthetic-private-text" not in json.dumps(asdict(result))


def watchdog_pair():
    state = shim._WatchdogState()
    for role, pid in (("anchor", 100), ("worker", 101)):
        child = SimpleNamespace(pid=pid, returncode=None)
        child.poll = lambda p=child: p.returncode
        state.begin(role)
        state.attach(role, child)
        state._retain_spawn(role, child)
        state.publish(role, identity._ProcessIdentity(BOOT, pid, 1, 100, 1, 1000, pid))
    state.flags["cancel"] = True
    return state


def watchdog_statuses(state):
    return tuple((role, child.pid, child.poll()) for role, child in state.children.items())


def eligible_identity_hold():
    return host._GroupObservation(
        "unknown",
        issues=("native_identity_unknown",),
        stage="member_identity",
        expected_pids=(100, 101),
        returned_pids=(100, 101),
    )


def test_new_exact_reap_allows_one_original_anchor_check_without_forward_revival():
    state = watchdog_pair()
    assert state.defer_identity_hold(eligible_identity_hold(), watchdog_statuses(state))
    for _ in range(4):
        assert not state.safety_ready(watchdog_statuses(state))
    assert not state.pending_exit.consumed and not state.signal_attempts
    # Synthetic Popen exit/reaping model; real role evidence is native-only.
    state.children["worker"].returncode = 3
    assert state.safety_ready(watchdog_statuses(state))
    assert state.pending_exit.consumed and state.pending_exit.worker_returncode == 3
    assert not state.safety_ready(watchdog_statuses(state))  # no repeat of the consumed check
    assert not state.cleanup_resolved()
    anchor = state.identities["anchor"]
    known = host._GroupObservation(
        "known", (anchor,), stage="complete", expected_pids=(100,), returned_pids=(100,)
    )
    state.record_signal(known, signal.SIGTERM)
    assert state.safety_ready(watchdog_statuses(state))
    assert state.signal_attempts == {signal.SIGTERM}
    assert state.flags["unknown"] and not state.flags["safety_blocked"]
    assert state.cleanup_resolved()
    with pytest.raises(host._ControlError, match="native_spawn_not_admitted"):
        state.begin("new-worker")
    with pytest.raises(host._ControlError, match="native_watchdog_not_enforcing"):
        state.require_forward(SimpleNamespace(is_alive=lambda: True), 200)
    # Original grace starts at known TERM success, not the earlier failed observation.
    assert host._timer_signal(policy(), 114, True, 105, False) is None
    assert host._timer_signal(policy(), 115, True, 105, False) == signal.SIGKILL
    state.record_signal(known, signal.SIGKILL)
    assert state.signal_attempts == {signal.SIGTERM, signal.SIGKILL}
    state.block_safety()
    assert not state.cleanup_resolved() and not state.safety_ready(watchdog_statuses(state))


@pytest.mark.parametrize(
    "fault",
    [
        "no-exit",
        "same-pid-new-handle",
        "stale-identity",
        "foreign-member",
        "anchor-exited",
        "other-unknown",
        "signal-denied",
        "unknown-again",
    ],
)
def test_reaping_recovery_rejects_nonoriginal_or_unresolved_evidence(fault):
    state = watchdog_pair()
    assert state.defer_identity_hold(eligible_identity_hold(), watchdog_statuses(state))
    state.children["worker"].returncode = 3
    if fault == "no-exit":
        state.children["worker"].returncode = None
    elif fault == "same-pid-new-handle":
        old = state.children["worker"]
        state.children["worker"] = SimpleNamespace(pid=old.pid, returncode=3, poll=lambda: 3)
    elif fault == "stale-identity":
        state.identities["anchor"] = replace(state.identities["anchor"], start_microseconds=999)
    elif fault == "foreign-member":
        state.children["extra"] = SimpleNamespace(pid=102, returncode=None, poll=lambda: None)
    elif fault == "anchor-exited":
        state.children["anchor"].returncode = 0
    elif fault == "other-unknown":
        state.block_safety()
    if fault in {"signal-denied", "unknown-again"}:
        assert state.safety_ready(watchdog_statuses(state))
        observed = host._GroupObservation(
            "unknown",
            issues=("native_signal_denied",)
            if fault == "signal-denied"
            else ("native_identity_unknown",),
            stage="signal" if fault == "signal-denied" else "member_identity",
            expected_pids=(100,),
            returned_pids=(100,),
        )
        assert not state.defer_identity_hold(observed, watchdog_statuses(state))
        state.record_signal(observed, signal.SIGTERM)
        assert not state.safety_ready(watchdog_statuses(state))
    else:
        assert not state.safety_ready(watchdog_statuses(state))
    assert state.flags["unknown"] and not state.cleanup_resolved()
    assert state.signal_attempts == ({signal.SIGTERM} if fault == "signal-denied" else set())


@pytest.mark.parametrize("fault", ["wrong-stage", "foreign-pid", "other-unknown", "not-live"])
def test_only_the_exact_original_live_pair_can_defer_identity_uncertainty(fault):
    state = watchdog_pair()
    observed = eligible_identity_hold()
    if fault == "wrong-stage":
        observed = replace(observed, stage="member_set")
    elif fault == "foreign-pid":
        observed = replace(observed, returned_pids=(100, 102))
    elif fault == "other-unknown":
        state.block_safety()
    else:
        state.children["worker"].returncode = 3
    assert not state.defer_identity_hold(observed, watchdog_statuses(state))
    assert state.pending_exit is None and state.flags["safety_blocked"]


@pytest.mark.parametrize(
    "fault",
    [
        "recover",
        "no-new-evidence",
        "expiry",
        "unrelated-block",
        "fresh-unknown",
        "syscall-denied",
        "handle-drift",
        "identity-drift",
        "clock-error",
        "timer-failed",
    ],
)
def test_production_timer_wiring_rechecks_signal_admission_after_corroboration(monkeypatch, fault):
    state = watchdog_pair()
    target = policy()
    state.policy = target
    state.timer = SimpleNamespace(is_alive=lambda: True)
    originals = dict(state.identities)
    clock_state = SimpleNamespace(now=100, fail=False)
    queries, signals, events = [], [], []
    injected = False

    class Finished:
        def __init__(self):
            self.done = False
            self.waits = 0

        def is_set(self):
            return self.done

        def wait(self, timeout):
            self.waits += 1
            assert self.waits <= 12, "finite timer fixture exceeded its scripted observations"
            assert 0 <= timeout <= 0.01
            if state.flags["safety_blocked"] or state.children["anchor"].returncode is not None:
                self.done = True
                return True
            if fault == "no-new-evidence":
                clock_state.now = 200 if self.waits == 5 else 100 + self.waits
            else:
                clock_state.now = [101, 102, 103, 115, 121, 122, 123][self.waits - 1]
                if self.waits == 3:
                    state.children["worker"].returncode = 3
            return False

    def now():
        if clock_state.fail:
            raise host._ControlError("native_clock_unknown")
        return clock_state.now

    def listing(kind, pgid, array, capacity):
        assert kind == 2 and pgid == 100
        assert capacity == 9 * host.ctypes.sizeof(host.ctypes.c_int)
        pids = (100, 101) if not queries else (100,)
        queries.append(pids)
        for index, pid in enumerate(pids):
            array[index] = pid
        return len(pids) * host.ctypes.sizeof(host.ctypes.c_int)

    def known(pid):
        nonlocal injected
        if len(queries) == 1 and pid == 101:
            raise host._ControlError("native_identity_unknown")
        if len(queries) == 2 and not injected:
            injected = True
            if fault == "expiry":
                clock_state.now = target.cleanup_deadline_continuous_ns
            elif fault == "unrelated-block":
                state.block_safety()
            elif fault == "fresh-unknown":
                raise host._ControlError("native_identity_unknown")
            elif fault == "handle-drift":
                state.children["anchor"] = SimpleNamespace(
                    pid=100, returncode=None, poll=lambda: None
                )
            elif fault == "identity-drift":
                state.identities["anchor"] = replace(originals["anchor"], start_microseconds=999)
            elif fault == "clock-error":
                clock_state.fail = True
            elif fault == "timer-failed":
                state.flags["timer_failed"] = True
            else:
                clock_state.now = 110  # corroboration took time, before actual TERM
        return originals["anchor"] if pid == 100 else originals["worker"]

    def killpg(pgid, signum):
        assert pgid == 100
        signals.append((signum, clock_state.now))
        if fault == "syscall-denied":
            raise PermissionError(errno.EPERM, "synthetic syscall denial")
        if signum == signal.SIGTERM:
            clock_state.now = 112  # known successful return; grace must start here
        elif signum == signal.SIGKILL:
            state.children["anchor"].returncode = -9

    monkeypatch.setattr(host, "_matches", lambda original: original in originals.values())
    monkeypatch.setattr(host, "_known_process", known)
    monkeypatch.setattr(identity, "_load_native", lambda: SimpleNamespace(proc_listpids=listing))
    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(host, "_now", now)
    monkeypatch.setattr(shim, "_now", now)
    finished = Finished()
    # This is the same timer entry used by production's Thread, with finite
    # simulated OS/clock/wait dependencies, never evidence of native capability.
    shim._watchdog_timer(
        state, target, finished, lambda name, **facts: events.append({"event": name, **facts})
    )
    observations = [e for e in events if e["event"] == "signal_observation"]
    attempted = [e for e in events if e["event"] == "signal"]
    assert observations[0]["observation_stage"] == "member_identity"
    assert observations[0]["issues"] == ["native_identity_unknown"]
    assert state.flags["unknown"]  # never cleared by safety-only recovery
    if fault == "recover":
        assert queries == [(100, 101), (100,), (100,)]
        assert signals == [(signal.SIGTERM, 110), (signal.SIGKILL, 122)]
        assert [e["signal"] for e in attempted] == [signal.SIGTERM, signal.SIGKILL]
        assert all(e["known"] for e in attempted) and len(observations) == 1
        assert attempted[0]["successful_term_continuous_ns"] == 112
        assert signals[1][1] - attempted[0]["successful_term_continuous_ns"] == target.term_grace_ns
        assert state.pending_exit.resolved and state.cleanup_resolved()
        assert state.signal_attempts == {signal.SIGTERM, signal.SIGKILL}
        assert not state.flags["safety_blocked"]
    elif fault == "no-new-evidence":
        assert queries == [(100, 101)] and not signals and not attempted
        assert not state.pending_exit.consumed and len(observations) == 1
    elif fault == "syscall-denied":
        assert queries == [(100, 101), (100,)]
        assert signals == [(signal.SIGTERM, 110)]
        assert len(attempted) == 1 and not attempted[0]["known"]
        assert attempted[0]["observation_stage"] == "signal"
        assert state.signal_attempts == {signal.SIGTERM} and state.flags["safety_blocked"]
    else:
        assert queries == [(100, 101), (100,)] and not signals and not attempted
        assert state.flags["safety_blocked"] and not state.cleanup_resolved()
        assert observations[-1]["observation_stage"] == (
            "member_identity" if fault == "fresh-unknown" else "signal_admission"
        )
        assert state.pending_exit.consumed and not state.pending_exit.resolved
    assert not state.flags["timer_failed"] or fault in {"clock-error", "timer-failed"}


@pytest.mark.parametrize(
    "fault",
    ["unexpected-fd", "borrowed-fd", "d-close-error", "w-close-error", "d-ack-lost", "w-ack-lost"],
)
def test_fd_custody_cannot_be_inferred(monkeypatch, fault):
    if fault in {"unexpected-fd", "borrowed-fd"}:
        # An integer/duplicate descriptor list is no role admission. No role is
        # invoked and a borrowed caller descriptor is not closed on rejection.
        readfd, writefd = os.pipe()
        try:
            assert shim._entry("anchor", (readfd, readfd, writefd, writefd, readfd)) == 64
            os.fstat(readfd)
        finally:
            os.close(readfd)
            os.close(writefd)
        return
    if fault in {"d-ack-lost", "w-ack-lost"}:
        child = object()
        monkeypatch.setattr(shim, "_spawn", lambda *args, **kwargs: child)
        monkeypatch.setattr(
            host._Channel,
            "send",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                host._ControlError("native_fixture_ack_lost")
            ),
        )
        # Binding construction is deliberately stopped after successful Popen
        # and child-copy closes. The handle must already be owned at this point.
        binding = receipt._Binding(
            1,
            "task:1",
            "fixture:owner",
            1,
            2,
            DIGEST,
            1,
            1,
            1,
            2,
            "fixture:intent",
            DIGEST,
            "fixture:host",
            BOOT,
        )
        config = {
            "binding": asdict(binding),
            "manifest": {},
            "policy": {"cleanup_deadline_continuous_ns": 1},
        }
        custody = shim._WatchdogState()
        custody.begin("anchor")
        with pytest.raises(host._ControlError, match="native_fixture_ack_lost"):
            shim._child_channel("anchor", config, KEY, (), custody=custody)
        assert custody.children == {"anchor": child}
        return
    calls = []

    def failed_close(fd):
        calls.append(fd)
        raise OSError("synthetic close failure")

    monkeypatch.setattr(os, "close", failed_close)
    owned = host._OwnedFD(123)
    assert owned.close() is False and owned.state == "unknown"
    assert owned.close() is False and calls == [123]
    # A missing acknowledgement cannot turn an unknown local close into known.


def test_timer_decisions_do_not_wait_for_control_io():
    target = policy()
    assert host._timer_signal(target, 99, False, None, False) is None
    assert host._timer_signal(target, 100, False, None, False) == signal.SIGTERM
    assert host._timer_signal(target, 109, True, 100, False) is None
    assert host._timer_signal(target, 110, True, 100, False) == signal.SIGKILL
    assert host._timer_signal(target, 111, True, 100, True) is None


def test_private_key_and_provenance_are_not_serialized():
    value = {
        "native_control_private": {"ciphertext": "private", "nonce": "private"},
        "native_control": {
            "schema_version": "stackos.native-control.v1",
            "phase": "ARMED",
            "control_revision": 2,
            "binding_digest": DIGEST,
            "manifest_digest": DIGEST,
            "registration": {"source_root": "private", "identities": {"pid": 42}},
        },
    }
    projected = RunOut._private_control_projection(value)
    assert "private" not in json.dumps(projected)
    assert set(projected["native_control"]) == {
        "schema_version",
        "phase",
        "control_revision",
        "binding_digest",
        "manifest_digest",
    }
    assert value["native_control_private"]["ciphertext"] == "private"


def test_worker_command_is_fixed_before_any_spawn(monkeypatch):
    # Actual bootstrap verification fails before a role has a usable channel.
    raw = host._frame(
        "bootstrap",
        {"command": ["sh", "-c", "anything"]},
        KEY,
        sequence=0,
        binding_digest=DIGEST,
        manifest_digest=DIGEST,
    )
    readfd, writefd = os.pipe()
    keyread, keywrite = os.pipe()
    replyread, replywrite = os.pipe()
    os.write(keywrite, KEY)
    os.write(writefd, raw)
    try:
        with pytest.raises(host._ControlError, match="native_bootstrap_invalid"):
            shim._bootstrap((readfd, replywrite, keyread, writefd, replyread))
    finally:
        for fd in (readfd, writefd, keywrite, replyread, replywrite):
            host._OwnedFD(fd).close()


@pytest.mark.parametrize(
    "fault",
    ["handle-before-identity", "cancel-during-publish", "dead-timer", "timer-failed", "unknown"],
)
def test_real_watchdog_release_decision_denies_races_and_dead_timer(monkeypatch, fault):
    monkeypatch.setattr(host, "_now", lambda: 0)
    state = shim._WatchdogState()
    finish = threading.Event()
    running = threading.Event()

    def timer():
        running.set()
        finish.wait()

    thread = threading.Thread(target=timer)
    thread.start()
    running.wait()
    readfd, writefd = os.pipe()
    os.set_blocking(readfd, False)
    try:
        for role, pid in (("anchor", 101), ("worker", 102)):
            state.begin(role)
            state.attach(role, SimpleNamespace(pid=pid, poll=lambda: None))
            if fault != "handle-before-identity" or role != "worker":
                state.publish(role, identity._ProcessIdentity(BOOT, pid, 100, 101, 100, 1000, pid))
        if fault == "cancel-during-publish":
            with state.lock:
                state.flags["cancel"] = True
        elif fault == "dead-timer":
            finish.set()
            thread.join()
        elif fault == "timer-failed":
            with state.lock:
                state.flags["timer_failed"] = True
        elif fault == "unknown":
            with state.lock:
                state.flags["unknown"] = True
        with pytest.raises(host._ControlError):
            state.release(thread, 100, writefd, b"x" * 64)
        with pytest.raises(BlockingIOError):
            os.read(readfd, 65)
        if fault == "handle-before-identity":
            assert state.snapshot()[2] == frozenset({"worker"})
            assert state.flags["unknown"] is False and thread.is_alive()
    finally:
        finish.set()
        thread.join()
        os.close(readfd)
        os.close(writefd)


@pytest.mark.parametrize("case", ["positive", "equal", "expired", "clock-failure"])
def test_remaining_product_wait_has_no_positive_floor(monkeypatch, case):
    if case == "clock-failure":

        def failed():
            raise host._ControlError("native_clock_unknown")

        monkeypatch.setattr(host, "_now", failed)
        with pytest.raises(host._ControlError):
            host._remaining(500, 0.02)
    else:
        now = {"positive": 499, "equal": 500, "expired": 501}[case]
        monkeypatch.setattr(host, "_now", lambda: now)
        assert host._remaining(500, 0.02) == (1e-9 if case == "positive" else 0)


@pytest.mark.parametrize("case", ["extra-protected-descriptor", "reused-number"])
def test_worker_observes_known_protected_slots_not_only_allowlist(tmp_path, case):
    path = tmp_path / "protected"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    duplicate = os.dup(fd)
    descriptors = [endpoint for _ in range(3) for endpoint in os.pipe()]
    allowed = dict(zip(("config", "barrier", "evidence"), descriptors[::2], strict=True))
    protected = host._fd_observation("fixture", {"protected": duplicate})["handles"]["protected"]
    try:
        if case == "reused-number":
            os.close(duplicate)
            os.dup2(allowed["config"], duplicate)
            allowed["config"] = duplicate  # exactly bound fresh channel at the old number
        observed = host._worker_fd_observation(
            allowed, [protected], closed_key_copies(), worker_handoff(allowed)
        )
        assert observed["protected_handles_absent"] is (case == "reused-number")
        assert observed["protected_slots"]
    finally:
        for owned in (fd, duplicate, *descriptors):
            os.close(owned)


@pytest.mark.parametrize(
    "case",
    [
        "closed-key-reused-by-config",
        "live-object-also-in-history",
        "live-duplicate-at-known-slot",
        "unbound-open-known-slot",
        "reused-slot-object-drift",
    ],
)
def test_worker_separates_closed_lifetimes_without_exempting_live_objects(monkeypatch, case):
    # Retained c12 tuples: A's closed key became T's config; W's closed key
    # became A's still-live control_read. No filesystem reuse is assumed here.
    def opened(fd, inode):
        return {"open": True, "fd": fd, "device": 0, "inode": inode, "close_on_exec": False}

    config = opened(3, 8216191602929979657)
    live = opened(22, 3938026793018061459)
    objects = {3: config, 7: opened(7, 6622191951588903513), 10: opened(10, 6469289385114467811)}
    history = []
    for fd, inode in (
        (21, live["inode"]),
        (20, live["inode"]),
        (21, live["inode"]),
        (7, config["inode"]),
        (6, config["inode"]),
        (7, config["inode"]),
    ):
        history.append({"before": opened(fd, inode), "after": {"open": False, "fd": fd}})
    if case == "live-object-also-in-history":
        objects[7] = {**live, "fd": 7}
    elif case == "live-duplicate-at-known-slot":
        objects[22] = live
    elif case == "unbound-open-known-slot":
        objects[22] = opened(22, 123456)

    def observe(role, named):
        current = dict(objects)
        if role == "worker_known_protected_slots" and case == "reused-slot-object-drift":
            current[7] = opened(7, 123456)
        return {
            "role": role,
            "handles": {
                name: dict(current.get(fd, {"open": False, "fd": fd})) for name, fd in named.items()
            },
        }

    monkeypatch.setattr(host, "_fd_observation", observe)
    allowed = {"config": 3, "barrier": 7, "evidence": 10}
    observed = host._worker_fd_observation(allowed, [live], history, worker_handoff(allowed))
    assert observed["protected_handles_absent"] is (case == "closed-key-reused-by-config")
    assert {value["fd"] for value in observed["protected_slots"].values()} == {6, 7, 20, 21, 22}


@pytest.mark.parametrize("fault", ["object", "fd", "extra-pass-fd", "close-fds", "bool-fd"])
def test_worker_rejects_inexact_fresh_channel_binding(monkeypatch, fault):
    endpoints = [fd for _ in range(3) for fd in os.pipe()]
    allowed = dict(zip(("config", "barrier", "evidence"), endpoints[::2], strict=True))
    protected = {"open": True, "fd": 999, "device": 1, "inode": 2, "close_on_exec": False}
    try:
        handoff = worker_handoff(allowed)
        if fault == "object":
            handoff["handles"]["config"]["inode"] += 1
        elif fault == "fd":
            handoff["handles"]["config"]["fd"] = endpoints[1]
        elif fault == "extra-pass-fd":
            handoff["pass_fds"].append(endpoints[1])
        elif fault == "close-fds":
            handoff["close_fds"] = False
        else:
            handoff["handles"]["config"]["fd"] = True
        with pytest.raises(host._ControlError, match="native_worker_fd_binding_unknown"):
            host._worker_fd_observation(allowed, [protected], closed_key_copies(), handoff)
    finally:
        for fd in endpoints:
            os.close(fd)


@pytest.mark.parametrize(
    "fault",
    ["none", "missing-read", "missing-write", "receiver-live", "parent-live", "wrong-object"],
)
def test_key_history_requires_every_matching_copy_close(fault):
    value = synthetic_bootstrap_history()
    if fault == "missing-read":
        del value["parent_key_closes"]["read"]
    elif fault == "missing-write":
        del value["parent_key_closes"]["write"]
    elif fault == "receiver-live":
        value["handles"]["key"] = value["closed_key_object"]
    elif fault == "parent-live":
        value["parent_key_closes"]["write"]["after"] = value["parent_key_closes"]["write"]["before"]
    elif fault == "wrong-object":
        value["closed_key_object"] = {**value["closed_key_object"], "inode": 3}
    if fault == "none":
        assert len(host._key_handoff_history(value)) == 3
    else:
        with pytest.raises(host._ControlError, match="native_key_close_unknown"):
            host._key_handoff_history(value)


@pytest.mark.parametrize("fault", ["close-error", "false-close-ack", "observation-error"])
def test_parent_key_close_evidence_requires_actual_closed_descriptor(monkeypatch, fault):
    readfd, writefd = os.pipe()
    owned = host._OwnedFD(readfd)
    real_close, real_observe = os.close, host._fd_observation
    attempts = []

    def close(fd):
        attempts.append(fd)
        if fd == readfd and fault == "close-error":
            raise OSError(errno.EIO, "synthetic close failure")
        return real_close(fd)

    def observe(role, named):
        if role == "key_copy_closed" and fault == "observation-error":
            raise host._ControlError("native_fd_observation_unknown")
        return real_observe(role, named)

    monkeypatch.setattr(os, "close", close)
    monkeypatch.setattr(host, "_fd_observation", observe)
    if fault == "false-close-ack":
        monkeypatch.setattr(owned, "close", lambda: True)
    try:
        expected = (
            "native_fd_observation_unknown"
            if fault == "observation-error"
            else "native_key_close_unknown"
        )
        with pytest.raises(host._ControlError, match=expected):
            host._close_key_copy(owned)
        assert attempts == ([] if fault == "false-close-ack" else [readfd])
    finally:
        if fault != "observation-error":
            real_close(readfd)
        real_close(writefd)


def test_receiver_key_close_failure_never_returns_key(monkeypatch):
    readfd, writefd = os.pipe()
    os.write(writefd, KEY)
    real_close = os.close
    attempts = []

    def close(fd):
        attempts.append(fd)
        if fd == readfd:
            raise OSError(errno.EIO, "synthetic close failure")
        return real_close(fd)

    monkeypatch.setattr(os, "close", close)
    try:
        with pytest.raises(host._ControlError, match="native_key_close_unknown"):
            shim._key(readfd)
        assert attempts == [readfd]
    finally:
        real_close(readfd)
        real_close(writefd)


def test_unresolved_custody_retains_exact_handle_until_accounted():
    admission = object()  # custody data itself grants no action/native authority
    context = host._NativeCustody(admission)
    context.__enter__()
    context.begin(admission)
    child = SimpleNamespace(pid=100, poll=lambda: None, returncode=None)
    context.attach(child)
    assert context.close_accounted() is False
    assert context.process is child and context.accounted is False
    waited = []

    def wait():
        waited.append(True)
        child.poll = lambda: 0
        child.returncode = 0
        return 0

    child.wait = wait
    context.__exit__(None, None, None)
    assert waited == [True] and context.accounted is True and context.process is child


@pytest.mark.parametrize("body_error", [False, True])
def test_interrupted_teardown_keeps_owning_handle_until_waitpid(body_error):
    admission = object()
    context = host._NativeCustody(admission)
    context.__enter__()
    context.begin(admission)
    child = SimpleNamespace(pid=100, returncode=None, poll=lambda: None)
    calls = []

    def wait():
        calls.append(context.active)
        if len(calls) == 1:
            raise KeyboardInterrupt()
        child.returncode = 0
        child.poll = lambda: 0
        return 0

    child.wait = wait
    context.attach(child)
    if body_error:
        context.__exit__(ValueError, ValueError("synthetic body failure"), None)
    else:
        with pytest.raises(KeyboardInterrupt):
            context.__exit__(None, None, None)
    assert calls == [True, True] and context.accounted and not context.active
    assert context.process is child and "native_teardown_interrupted" in context.unresolved


@pytest.mark.parametrize("role", ["anchor", "trampoline"])
@pytest.mark.parametrize("fault", ["attach-error", "attach-interrupt", "helper-return-interrupt"])
def test_watchdog_spawn_boundaries_retain_and_reap_exact_children(
    tmp_path, monkeypatch, role, fault
):
    """Drive the actual W A/T call paths; every Popen is a no-effect fake."""
    expired = False
    processes = []
    contexts = []
    channels = []
    original_state = shim._WatchdogState
    original_spawn = shim._spawn
    failure = (
        ValueError("synthetic attach failure") if fault == "attach-error" else KeyboardInterrupt()
    )
    owner_role = "worker" if role == "trampoline" else role

    class State(original_state):
        def __init__(self):
            super().__init__()
            contexts.append(self)

        def attach(self, child_role, child):
            nonlocal expired
            if child_role == owner_role and fault != "helper-return-interrupt":
                expired = True
                raise failure
            super().attach(child_role, child)

    class Channel:
        def __init__(self, readfd, writefd, *args):
            self.read_fd, self.write_fd = readfd, writefd
            self.key = KEY
            self.binding = self.manifest = DIGEST
            self.bootstrap_fds = synthetic_bootstrap_history()
            channels.append(self)

        def send(self, *args, **kwargs):
            pass

    def popen(argv, **kwargs):
        child = SimpleNamespace(pid=101 + len(processes), returncode=None, role=argv[4], waits=0)
        child.poll = lambda: child.returncode

        def wait():
            child.waits += 1
            child.returncode = 0
            return 0

        child.wait = wait
        processes.append(child)
        return child

    def spawn(child_role, *args, **kwargs):
        nonlocal expired
        child = original_spawn(child_role, *args, **kwargs)
        if child_role == role and fault == "helper-return-interrupt":
            expired = True
            assert any(held is child for _, held in contexts[0].owned_children())
            raise failure
        return child

    binding = receipt._Binding(
        1,
        "task:1",
        "fixture:owner",
        1,
        2,
        DIGEST,
        1,
        1,
        1,
        2,
        "fixture:intent",
        DIGEST,
        "fixture:host",
        BOOT,
    )
    config = {
        "binding": asdict(binding),
        "manifest": {
            "executable": "/synthetic/python",
            "source_root": str(tmp_path),
            "shim": "/synthetic/shim.py",
            "command": {"environment": {}},
        },
        "policy": policy()._decision,
        "barrier_nonce": "n" * 64,
    }
    writer = os.open(tmp_path / "writer", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    report_path = tmp_path / "report"
    report_fd = os.open(report_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    terminal_path = tmp_path / "terminal"
    terminal_path.mkdir(mode=0o700)
    terminal_directory = os.open(terminal_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    terminal = receipt._allocate_terminal_slot(terminal_directory, binding)
    assert type(terminal) is receipt._AllocatedSlot
    terminal_read = os.open(terminal.name, os.O_RDONLY | os.O_CLOEXEC, dir_fd=terminal_directory)
    terminal_verify_directory = os.open(
        ".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=terminal_directory
    )
    config["terminal_slot"] = {
        "dalloc": list(terminal.dalloc),
        "fempty": list(terminal.fempty),
        "status_flags": terminal.status_flags,
        "offset": terminal.offset,
        "known_slots": [terminal.fd, terminal_directory, terminal_read, terminal_verify_directory],
        "retained": host._fd_observation(
            "fixture_retained", {"read": terminal_read, "directory": terminal_verify_directory}
        )["handles"],
    }
    control_read, control_write = os.pipe()
    controller = Channel(control_read, control_write)

    def known(pid):
        return identity._ProcessIdentity(BOOT, pid, 1, 101, 1, 1000, pid)

    def message(channel, deadline):
        if channel is controller:
            return "writer_closed", {"closed": True, "terminal_closed": True}
        return "received", {
            "identity": asdict(known(processes[0].pid)),
            "fds": {"handles": {}},
            "bootstrap_fds": channel.bootstrap_fds,
        }

    monkeypatch.setattr(shim, "_WatchdogState", State)
    monkeypatch.setattr(shim, "_Channel", Channel)
    monkeypatch.setattr(shim, "_spawn", spawn)
    monkeypatch.setattr(shim.subprocess, "Popen", popen)
    monkeypatch.setattr(shim, "_bootstrap", lambda fds: (controller, config, policy()))
    monkeypatch.setattr(shim, "_known_process", known)
    monkeypatch.setattr(shim, "_wait_message", message)
    monkeypatch.setattr(shim, "_now", lambda: 200 if expired else 1)
    monkeypatch.setattr(
        shim, "_remaining", lambda deadline, ceiling: 0 if expired else min(0.001, ceiling)
    )
    monkeypatch.setattr(
        clock,
        "_sample_clock",
        lambda: clock._ClockObservation("known", BOOT, 1, 1, runtime_abi=clock._SUPPORTED_ABI),
    )
    try:
        assert (
            shim._watchdog(
                (
                    control_read,
                    control_write,
                    999,
                    writer,
                    directory,
                    report_fd,
                    terminal.fd,
                    terminal_directory,
                )
            )
            == 1
        )
        assert [child.role for child in processes] == (
            ["anchor"] if role == "anchor" else ["anchor", "trampoline"]
        )
        assert all(child.waits == 1 and child.returncode == 0 for child in processes)
        assert {id(child) for _, child in contexts[0].owned_children()} == {
            id(child) for child in processes
        }
        assert contexts[0].flags["unknown"] and not contexts[0].flags["released"]
        report = json.loads(report_path.read_bytes())
        assert not report["released"] and not report["cleanup_known"]
        cleanup = [event for event in report["events"] if event["event"] == "cleanup"]
        assert len(cleanup) == 1 and all(cleanup[0]["reaped"].values())
    finally:
        for channel in channels:
            for fd in (channel.read_fd, channel.write_fd):
                try:
                    os.close(fd)
                except OSError as error:
                    assert error.errno == errno.EBADF
        for fd in (
            writer,
            directory,
            report_fd,
            terminal.fd,
            terminal_directory,
            terminal_read,
            terminal_verify_directory,
        ):
            try:
                os.close(fd)
            except OSError as error:
                assert error.errno == errno.EBADF


@pytest.mark.parametrize("kind", ["os-error", "assertion-error"])
def test_permanent_owned_wait_error_parks_once_without_discarding_context(monkeypatch, kind):
    class Parked(BaseException):
        pass

    admission = object()
    owned = host._NativeCustody(admission)
    owned.__enter__()
    owned.begin(admission)
    child = SimpleNamespace(pid=100, returncode=None, poll=lambda: None)
    calls = []

    def wait():
        calls.append(True)
        raise (
            OSError(errno.ECHILD, "synthetic accounting failure")
            if kind == "os-error"
            else AssertionError()
        )

    child.wait = wait
    owned.attach(child)
    seen = []

    def park(diagnostic, publish=None):
        seen.append(diagnostic)
        raise Parked()

    monkeypatch.setattr(host, "_park_owned", park)
    with pytest.raises(Parked):
        owned.__exit__(None, None, None)
    assert calls == [True] and len(seen) == 1
    assert seen[0]["code"] == "native_wait_accounting_held" and not seen[0]["product_success"]
    assert owned.active and owned.process is child and not owned.accounted

    # Only the no-effect fake is made accounted for bounded test finalization.
    def accounted():
        child.returncode = 0
        child.poll = lambda: 0

    child.wait = accounted
    owned.__exit__(None, None, None)
    assert owned.accounted and not owned.active


@pytest.mark.parametrize(
    "fault",
    [
        "valid",
        "empty-receipt",
        "missing-error-flag",
        "nonzero-error",
        "wrong-pid",
        "malformed-receipt",
        "post-birth-mismatch",
        "registration-error",
    ],
)
def test_exit_monitor_constructor_uses_sdk_receipt_and_accounts_failure_close(
    tmp_path, monkeypatch, fault
):
    import select

    from tests.integration.test_controlled_process_native import _DARWIN_EV_RECEIPT, _ExactExitWatch

    constants = {
        "KQ_FILTER_PROC": -5,
        "KQ_EV_ADD": 0x0001,
        "KQ_EV_ENABLE": 0x0004,
        "KQ_EV_ONESHOT": 0x0010,
        "KQ_EV_ERROR": 0x4000,
        "KQ_NOTE_EXIT": 0x80000000,
    }
    for name, value in constants.items():
        monkeypatch.setattr(select, name, value, raising=False)
    monkeypatch.delattr(select, "KQ_EV_RECEIPT", raising=False)
    expected = identity._ProcessIdentity(BOOT, 100, 10, 100, 10, 1000, 1)
    births, changes, controls, closed = [], [], [], []
    failure = OSError(errno.EPERM, "synthetic registration failure")

    def matches(value):
        assert value is expected
        births.append(value)
        return not (fault == "post-birth-mismatch" and len(births) == 2)

    def kevent(pid, **kwargs):
        assert pid == expected.pid
        changes.append(SimpleNamespace(ident=pid, **kwargs))
        return changes[-1]

    def control(changelist, max_events, timeout):
        controls.append((changelist, max_events, timeout))
        assert changelist == [changes[0]] and max_events == 1 and timeout == 0
        if fault == "registration-error":
            raise failure
        if fault == "empty-receipt":
            return []
        if fault == "malformed-receipt":
            return [SimpleNamespace(ident=100)]
        return [
            SimpleNamespace(
                ident=101 if fault == "wrong-pid" else 100,
                flags=0 if fault == "missing-error-flag" else constants["KQ_EV_ERROR"],
                data=errno.EPERM if fault == "nonzero-error" else 0,
            )
        ]

    queue = SimpleNamespace(control=control, close=lambda: closed.append(True))
    monkeypatch.setattr(host, "_matches", matches)
    monkeypatch.setattr(select, "kevent", kevent, raising=False)
    monkeypatch.setattr(select, "kqueue", lambda: queue, raising=False)
    if fault == "valid":
        watch = _ExactExitWatch(expected, tmp_path / "held.json")
        assert watch.queue is queue and not watch.exited and watch.held is None
        assert len(births) == 2 and closed == []
        watch.queue.close()
    else:
        error = (
            OSError
            if fault == "registration-error"
            else AttributeError
            if fault == "malformed-receipt"
            else AssertionError
        )
        with pytest.raises(error) as caught:
            _ExactExitWatch(expected, tmp_path / "held.json")
        if fault == "registration-error":
            assert caught.value is failure
        assert len(births) == (2 if fault == "post-birth-mismatch" else 1)
    assert _DARWIN_EV_RECEIPT == 0x0040
    assert len(changes) == 1 and len(controls) == 1 and closed == [True]
    assert changes[0].filter == constants["KQ_FILTER_PROC"]
    assert changes[0].flags == 0x0055  # ADD|ENABLE|ONESHOT|Darwin EV_RECEIPT
    assert changes[0].fflags == constants["KQ_NOTE_EXIT"]
    assert not (tmp_path / "held.json").exists()


@pytest.mark.parametrize("fault", ["permanent-os-error", "invalid-event", "eintr-then-exit"])
def test_exact_exit_monitor_never_retries_permanent_or_consumed_event(tmp_path, monkeypatch, fault):
    import select

    from tests.integration.test_controlled_process_native import _ExactExitWatch

    for name, value in {"KQ_FILTER_PROC": 1, "KQ_EV_ERROR": 2, "KQ_NOTE_EXIT": 4}.items():
        monkeypatch.setattr(select, name, value, raising=False)

    class Parked(BaseException):
        pass

    calls = []
    closed = []

    def control(*args):
        calls.append(True)
        if fault == "permanent-os-error":
            raise OSError(errno.EBADF, "synthetic monitor failure")
        if fault == "eintr-then-exit" and len(calls) == 1:
            raise InterruptedError(errno.EINTR, "synthetic interruption")
        return [
            SimpleNamespace(
                ident=999 if fault == "invalid-event" else 100, filter=1, flags=0, fflags=4
            )
        ]

    watch = _ExactExitWatch.__new__(_ExactExitWatch)
    watch.expected = SimpleNamespace(pid=100)
    watch.queue = SimpleNamespace(control=control, close=lambda: closed.append(True))
    watch.exited = False
    watch.held = watch.failure = None
    watch.held_output = tmp_path / "held.json"
    if fault == "eintr-then-exit":
        with pytest.raises(InterruptedError):
            watch.finalize()
        assert len(calls) == 2 and watch.exited and closed == [True]
        return
    with pytest.raises((OSError, AssertionError)):
        watch.observe(0)
    original_queue = watch.queue
    seen = []

    def park(diagnostic, publish=None):
        seen.append(diagnostic)
        publish(diagnostic)
        raise Parked()

    monkeypatch.setattr(host, "_park_owned", park)
    with pytest.raises(Parked):
        watch.finalize()
    assert len(calls) == 1 and len(seen) == 1 and closed == []
    assert watch.queue is original_queue and not watch.exited and watch.failure is not None
    assert json.loads(watch.held_output.read_text()) == watch.held
    assert watch.held["monitor_retained"] and not watch.held["product_success"]


def test_fixture_controller_permanent_join_error_retains_owner_and_diagnostic(
    tmp_path, monkeypatch
):
    from tests.integration.test_controlled_process_native import _join_owned_controller

    class Parked(BaseException):
        pass

    calls = []

    def join():
        calls.append(True)
        raise OSError(errno.ECHILD, "synthetic accounting failure")

    controller = SimpleNamespace(pid=100, join=join)
    seen = []

    def park(diagnostic, publish=None):
        seen.append(diagnostic)
        publish(diagnostic)
        raise Parked()

    monkeypatch.setattr(host, "_park_owned", park)
    path = tmp_path / "controller-held.json"
    with pytest.raises(Parked):
        _join_owned_controller(controller, path)
    assert calls == [True] and len(seen) == 1 and json.loads(path.read_text()) == seen[0]
    assert seen[0]["handle_retained"] and not seen[0]["product_success"]


@pytest.mark.parametrize(
    "fault",
    [
        "inherited",
        "writer-set-error",
        "directory-set-error",
        "writer-read-error",
        "directory-read-error",
        "writer-still-inheritable",
        "directory-still-inheritable",
    ],
)
def test_anchor_reseals_owned_inherited_receipt_fds_before_s2(tmp_path, monkeypatch, fault):
    folder = tmp_path / "arm"
    folder.mkdir(mode=0o700)
    original_directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    binding = receipt._Binding(
        1,
        "task:1",
        "fixture:owner",
        1,
        2,
        DIGEST,
        1,
        1,
        1,
        2,
        "fixture:intent",
        DIGEST,
        "fixture:host",
        BOOT,
    )
    original = receipt._allocate_receipt_slot(original_directory, binding)
    assert isinstance(original, receipt._AllocatedSlot)
    writer = os.dup(original.fd)
    directory = os.dup(original_directory)
    assert original._close_once() is None  # explicit transfer of owned copies
    os.set_inheritable(writer, True)
    os.set_inheritable(directory, True)
    assert os.get_inheritable(writer) and os.get_inheritable(directory)
    target = writer if fault.startswith("writer") else directory
    set_calls = []
    real_set, real_get = os.set_inheritable, os.get_inheritable

    def set_flag(fd, inheritable):
        set_calls.append((fd, inheritable))
        if fd == target and fault.endswith("set-error"):
            raise OSError(errno.EPERM, "synthetic flag write failure")
        real_set(fd, inheritable)

    def get_flag(fd):
        if fd == target and fault.endswith("read-error"):
            raise OSError(errno.EBADF, "synthetic flag read failure")
        if fd == target and fault.endswith("still-inheritable"):
            return True
        return real_get(fd)

    config = {
        "binding": asdict(binding),
        "slot": {
            "dalloc": list(original.dalloc),
            "fempty": list(original.fempty),
            "status_flags": original.status_flags,
            "offset": original.offset,
        },
    }
    sent = []
    channel = SimpleNamespace(
        binding=original.binding_digest,
        bootstrap_fds={},
        send=lambda *args: sent.append(args),
        receive=lambda **kwargs: ("stop", {}),
    )
    checks = []
    real_check = receipt._check_allocated_receipt_slot

    def check(fd, slot):
        checks.append((fd, slot.fd, real_get(fd), real_get(slot.fd)))
        assert slot.dalloc == original.dalloc and slot.fempty == original.fempty
        return real_check(fd, slot)

    monkeypatch.setattr(os, "set_inheritable", set_flag)
    monkeypatch.setattr(os, "get_inheritable", get_flag)
    monkeypatch.setattr(shim, "_bootstrap", lambda fds: (channel, config, policy()))
    monkeypatch.setattr(
        shim, "_known_process", lambda pid: identity._ProcessIdentity(BOOT, pid, 1, pid, 1, 1000, 1)
    )
    monkeypatch.setattr(shim.signal, "signal", lambda *args: None)
    monkeypatch.setattr(shim, "_now", lambda: 1)
    monkeypatch.setattr(host, "_now", lambda: 1)
    monkeypatch.setattr(receipt, "_check_allocated_receipt_slot", check)
    try:
        if fault == "inherited":
            assert shim._anchor((999, 998, 997, writer, directory)) == 0
            assert checks == [(directory, writer, False, False)]
            assert set_calls == [(writer, False), (directory, False)]
            assert len(sent) == 1 and sent[0][0] == "received"
            handles = sent[0][1]["fds"]["handles"]
            assert handles["writer"]["close_on_exec"] and handles["directory"]["close_on_exec"]
        else:
            with pytest.raises(host._ControlError, match="native_receipt_fd_seal_unknown"):
                shim._anchor((999, 998, 997, writer, directory))
            assert checks == [] and sent == []
        for owned in (writer, directory):
            with pytest.raises(OSError) as closed:
                os.fstat(owned)
            assert closed.value.errno == errno.EBADF
    finally:
        os.close(original_directory)


def startup_frame():
    return {
        "event": "worker_startup_hold",
        "phase": "fd_absence",
        "category": "control_rejection",
        "errno": None,
        "fds": {
            "role": "worker",
            "handles": {
                name: {"open": True, "fd": i, "device": 1, "inode": i + 10, "close_on_exec": True}
                for i, name in enumerate(("config", "barrier", "evidence"), 3)
            },
            "protected_slots": {"slot_0": {"open": False, "fd": 6}},
            "protected_handles_absent": False,
        },
    }


@pytest.mark.parametrize(
    "fault", ["valid", "extra-key", "phase", "category", "errno", "fd-extra-key"]
)
def test_worker_startup_diagnostic_projects_only_fixed_safe_facts(fault):
    frame = startup_frame()
    if fault == "extra-key":
        frame["unallowed_payload"] = "synthetic-unallowed"
    elif fault == "phase":
        frame["phase"] = "unallowed"
    elif fault == "category":
        frame["category"] = "unallowed"
    elif fault == "errno":
        frame["errno"] = True
    elif fault == "fd-extra-key":
        frame["fds"]["handles"]["config"]["unallowed_payload"] = "synthetic-unallowed"
    before = host._json(frame)
    if fault == "valid":
        safe = shim._startup_diagnostic(frame)
        assert safe == {key: frame[key] for key in ("phase", "category", "errno", "fds")}
        assert safe["fds"] is not frame["fds"]
    else:
        with pytest.raises(host._ControlError, match="native_worker_startup_diagnostic_invalid"):
            shim._startup_diagnostic(frame)
    assert host._json(frame) == before


@pytest.mark.parametrize(
    "fault",
    [
        "protected-present",
        "observer-failure",
        "diagnostic-write-failure",
        "diagnostic-partial-write",
    ],
)
def test_trampoline_startup_diagnostic_preserves_original_failure_and_cleanup(monkeypatch, fault):
    command = _resolve_controlled_command("noop-v1").descriptor()
    config_read, config_write = os.pipe()
    barrier_read, barrier_write = os.pipe()
    evidence_read, evidence_write = os.pipe()
    protected = host._fd_observation("fixture", {"config": config_read})["handles"]["config"]
    config = {
        "command": command,
        "nonce": "n" * 64,
        "protected_objects": [protected],
        "closed_key_history": closed_key_copies(),
        "fd_handoff": worker_handoff(
            {"config": config_read, "barrier": barrier_read, "evidence": evidence_write}
        ),
        "deadline_continuous_ns": 100,
    }
    os.write(config_write, host._json(config))
    os.close(config_write)
    failure = host._ControlError("native_fd_observation_unknown")
    if fault in {"observer-failure", "diagnostic-partial-write"}:

        def failed_observation(*args):
            raise failure

        monkeypatch.setattr(shim, "_worker_fd_observation", failed_observation)
    write_attempts = []
    if fault in {"diagnostic-write-failure", "diagnostic-partial-write"}:
        real_write = os.write

        def failed_write(fd, raw):
            if fd == evidence_write:
                write_attempts.append(raw)
                if fault == "diagnostic-write-failure":
                    raise OSError(errno.EIO, "synthetic diagnostic write failure")
                return real_write(fd, raw[:7])
            return real_write(fd, raw)

        monkeypatch.setattr(os, "write", failed_write)
    executed = []

    def forbidden_exec(*args):
        executed.append(True)
        raise AssertionError("body must remain blocked")

    monkeypatch.setattr(os, "execve", forbidden_exec)
    try:
        expected = (
            "native_fd_observation_unknown"
            if fault in {"observer-failure", "diagnostic-partial-write"}
            else "native_worker_inherited_authority"
        )
        with pytest.raises(host._ControlError, match=expected) as caught:
            shim._trampoline((config_read, barrier_read, evidence_write))
        if fault in {"observer-failure", "diagnostic-partial-write"}:
            assert caught.value is failure
        assert executed == []
        raw = os.read(evidence_read, 8192)
        if fault == "diagnostic-write-failure":
            assert raw == b"" and len(write_attempts) == 1
        elif fault == "diagnostic-partial-write":
            assert len(write_attempts) == 1 and raw == write_attempts[0][:7]
            assert len(raw) == 7 and not raw.endswith(b"\n")
            with pytest.raises(json.JSONDecodeError):
                json.loads(raw)
        else:
            frame = json.loads(raw)
            assert frame["event"] == "worker_startup_hold"
            assert frame["phase"] == ("fd_observe" if fault == "observer-failure" else "fd_absence")
            assert frame["category"] == "control_rejection" and frame["errno"] is None
            safe = shim._startup_diagnostic(frame)
            if fault == "observer-failure":
                assert safe["fds"] is None
            else:
                assert safe["fds"]["protected_handles_absent"] is False
        for fd in (config_read, barrier_read, evidence_write):
            with pytest.raises(OSError) as closed:
                os.fstat(fd)
            assert closed.value.errno == errno.EBADF
    finally:
        os.close(evidence_read)
        os.close(barrier_write)


@pytest.mark.parametrize("fault", ["valid", "unknown-phase", "extra-field"])
def test_watchdog_diagnostic_can_never_return_ready_or_release(fault):
    frame = startup_frame()
    if fault == "unknown-phase":
        frame["phase"] = "unallowed"
    elif fault == "extra-field":
        frame["unallowed_payload"] = "synthetic-unallowed"
    records = []
    released = []
    with pytest.raises(host._ControlError, match="native_worker_inert_unknown"):
        shim._reject_startup_hold(frame, lambda name, **facts: records.append((name, facts)))
        released.append(True)
    assert released == []
    if fault == "valid":
        assert records == [("worker_startup_hold", shim._startup_diagnostic(frame))]
    else:
        assert records == []
