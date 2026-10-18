"""Trusted private W/A/T entry roles. No DB, daemon seed or provider access.

Invoked with a fixed role and explicit inherited descriptor integers. This is
not an action/operation or arbitrary-shell interface. Same-UID hostile callers
are outside the cooperative IPC/custody threat model.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, NoReturn

if __package__ in (None, ""):
    # -I -S deliberately ignores PYTHONPATH and site. Only this pinned root is
    # inserted; the parent verifies the exact runtime/shim bytes before spawn.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stackos import controlled_process_clock as clock
from stackos import controlled_process_identity as identity
from stackos import controlled_process_receipt as receipt
from stackos.controlled_process_host import (
    _account_owned,
    _Channel,
    _close_key_copy,
    _ControlError,
    _digest,
    _fd_observation,
    _GroupObservation,
    _json,
    _key_handoff_history,
    _known_process,
    _matches,
    _NativePolicy,
    _now,
    _observe_owned_group,
    _OwnedFD,
    _park_owned,
    _remaining,
    _signal_owned_group,
    _timer_signal,
    _worker_fd_observation,
)


def _key(fd: int) -> tuple[bytes, dict[str, Any]]:
    owned = _OwnedFD(fd)
    try:
        original = _fd_observation("key_pipe", {"key": fd})["handles"]["key"]
        value = os.read(fd, 33)
        if len(value) != 32:
            raise _ControlError("native_key_pipe_invalid")
        return value, original
    finally:
        if not owned.close():
            raise _ControlError("native_key_close_unknown")


def _bootstrap(fds: tuple[int, ...]) -> tuple[_Channel, dict[str, Any], _NativePolicy]:
    readfd, writefd, keyfd = fds[:3]
    key, original_key = _key(keyfd)
    inherited = _fd_observation(
        "bootstrap", {"control_read": readfd, "control_write": writefd, "key": keyfd}
    )
    inherited["closed_key_object"] = original_key
    if inherited["handles"]["key"]["open"] is not False:
        raise _ControlError("native_key_close_unknown")
    data = bytearray()
    while b"\n" not in data:
        chunk = os.read(readfd, 4096)
        if not chunk or len(data) + len(chunk) > 16_384:
            raise _ControlError("native_bootstrap_incomplete")
        data.extend(chunk)
    # Digests select no authority; their keyed authentication is checked below.
    try:
        body = json.loads(data)["body"]
        channel = _Channel(readfd, writefd, key, body["binding_digest"], body["manifest_digest"])
        channel.bootstrap_fds = inherited
        channel.buffer.extend(data)
        message = channel.receive()
        if message is None or message[0] != "bootstrap":
            raise _ControlError("native_bootstrap_invalid")
        config = message[1]
        if config.keys() != {
            "binding",
            "policy",
            "manifest",
            "slot",
            "barrier_nonce",
            "cancel_digest",
            "parent_key_closes",
            "terminal_slot",
        }:
            raise _ControlError("native_bootstrap_invalid")
        inherited["parent_key_closes"] = config["parent_key_closes"]
        _key_handoff_history(inherited)
        binding = receipt._Binding(**config["binding"])
        if not receipt._valid_binding(binding) or receipt._name(binding)[:-8] != channel.binding:
            raise _ControlError("native_bootstrap_binding")
        manifest = config["manifest"]
        if _digest(manifest) != channel.manifest:
            raise _ControlError("native_bootstrap_manifest")
        root = Path(__file__).resolve().parents[1]
        if (
            manifest["source_root"] != str(root)
            or manifest["shim"] != str(Path(__file__).resolve())
            or manifest["executable"] != str(Path(sys.executable).resolve())
            or hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()
            != manifest["executable_sha256"]
            or any(
                hashlib.sha256((root / name).read_bytes()).hexdigest() != digest
                for name, digest in manifest["source_files"].items()
            )
            or hashlib.sha256(Path(manifest["watchdog_entry"]).read_bytes()).hexdigest()
            != manifest["watchdog_entry_sha256"]
            or (len(fds) == 6 and str(Path(sys.argv[0]).resolve()) != manifest["watchdog_entry"])
        ):
            raise _ControlError("native_bootstrap_source_drift")
        policy = _NativePolicy.from_decision(
            config["policy"],
            command_digest=_digest(manifest["command"]),
            runtime_digest=channel.manifest,
        )
        policy.before_release(clock._sample_clock())
        if not receipt._hex(config["barrier_nonce"], nonzero=True) or not receipt._hex(
            config["cancel_digest"]
        ):
            raise _ControlError("native_bootstrap_invalid")
        command = manifest["command"]
        executable = str(Path(sys.executable).resolve())
        if (
            command["command_key"] != "noop-v1"
            or command["executable"] != executable
            or command["argv"] != [executable, "-I", "-c", "pass"]
            or command["environment"]
            != {
                "LANG": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        ):
            raise _ControlError("native_worker_command_invalid")
        return channel, config, policy
    except (KeyError, TypeError, ValueError) as exc:
        raise _ControlError("native_bootstrap_invalid") from exc


def _spawn(
    role: str,
    fds: tuple[int, ...],
    config: dict[str, Any],
    *,
    custody: _WatchdogState,
    **kwargs: Any,
) -> subprocess.Popen[bytes]:
    manifest = config["manifest"]
    # The existing W owner retains the handle before this helper can return.
    return custody.spawn(
        "worker" if role == "trampoline" else role,
        [manifest["executable"], "-I", "-S", manifest["shim"], role, *map(str, fds)],
        cwd=manifest["source_root"],
        env=config["manifest"]["command"]["environment"],
        close_fds=True,
        pass_fds=fds,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


@dataclass
class _PendingExitHold:
    anchor: subprocess.Popen[bytes]
    worker: subprocess.Popen[bytes]
    anchor_identity: identity._ProcessIdentity
    worker_identity: identity._ProcessIdentity
    consumed: bool = False
    resolved: bool = False
    worker_returncode: int | None = None


class _WatchdogState:
    """One W's local custody and release decision, never execution authority."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.children: dict[str, subprocess.Popen[bytes]] = {}
        self.identities: dict[str, identity._ProcessIdentity] = {}
        self.inflight: set[str] = set()
        self.held_children: list[tuple[str, subprocess.Popen[bytes]]] = []
        self.pending_exit: _PendingExitHold | None = None
        self.signal_attempts: set[int] = set()
        self.policy: _NativePolicy | None = None
        self.timer: threading.Thread | None = None
        self.flags: dict[str, Any] = {
            "cancel": False,
            "reason": "controller_eof",
            "unknown": False,
            "safety_blocked": False,
            "released": False,
            "timer_failed": False,
        }

    def block_safety(self) -> None:
        with self.lock:
            self.flags["unknown"] = True
            self.flags["safety_blocked"] = True

    def dispatch_signal(
        self,
        anchor: identity._ProcessIdentity,
        expected: tuple[identity._ProcessIdentity, ...],
        signum: int,
        snapshot: tuple[tuple[str, subprocess.Popen[bytes]], ...],
        registered: dict[str, identity._ProcessIdentity],
        policy: _NativePolicy | None,
        timer: threading.Thread | None,
    ) -> None:
        # Group corroboration holds no W lock. The final short state/clock/
        # effect is serialized. OS scheduling/query-to-signal races remain.
        try:
            acquired = self.lock.acquire(blocking=False)
        except BaseException:
            raise _ControlError("native_signal_admission_denied") from None
        if not acquired:
            raise _ControlError("native_signal_admission_denied")
        attempted = False
        try:
            if (
                policy is None
                or self.policy is not policy
                or timer is None
                or self.timer is not timer
                or not timer.is_alive()
                or not self.flags["cancel"]
                or self.flags["safety_blocked"]
                or self.flags["timer_failed"]
                or self.inflight
                or signum in self.signal_attempts
                or self.children.keys() != {r for r, _ in snapshot}
                or any(self.children.get(r) is not p for r, p in snapshot)
                or self.identities != registered
                or registered.get("anchor") != anchor
                or not expected
                or anchor not in expected
                or any(
                    self.identities.get(r) not in expected
                    for r, p in snapshot
                    if p.returncode is None
                )
                or {x.pid for x in expected} != {p.pid for _, p in snapshot if p.returncode is None}
                or (
                    self.flags["unknown"]
                    and (
                        self.pending_exit is None
                        or not self.pending_exit.consumed
                        or self.pending_exit.worker_returncode is None
                        or self.pending_exit.anchor is not self.children.get("anchor")
                        or self.pending_exit.worker is not self.children.get("worker")
                        or expected != (self.pending_exit.anchor_identity,)
                        or self.children["worker"].returncode != self.pending_exit.worker_returncode
                    )
                )
            ):
                raise _ControlError("native_signal_admission_denied")
            if _now() >= policy.cleanup_deadline_continuous_ns:
                raise _ControlError("native_signal_admission_denied")
            self.signal_attempts.add(signum)  # before an attempted syscall, including denial
            attempted = True
            os.killpg(anchor.pgid, signum)
        except BaseException:
            if not attempted:
                raise _ControlError("native_signal_admission_denied") from None
            raise
        finally:
            self.lock.release()

    def defer_identity_hold(
        self,
        observed: _GroupObservation,
        statuses: tuple[tuple[str, int, int | None], ...],
    ) -> bool:
        with self.lock:
            expected = {x.pid for x in self.identities.values()}
            if (
                self.flags["unknown"]
                or self.flags["safety_blocked"]
                or self.pending_exit is not None
                or self.inflight
                or self.children.keys() != {"anchor", "worker"}
                or self.identities.keys() != self.children.keys()
                or observed.status != "unknown"
                or observed.stage != "member_identity"
                or observed.issues != ("native_identity_unknown",)
                or len(observed.expected_pids) != 2
                or len(observed.returned_pids) != 2
                or set(observed.expected_pids) != expected
                or set(observed.returned_pids) != expected
                or len(statuses) != 2
                or {r for r, _, _ in statuses} != {"anchor", "worker"}
                or any(
                    rc is not None or pid != self.children[r].pid or pid != self.identities[r].pid
                    for r, pid, rc in statuses
                )
            ):
                self.block_safety()
                return False
            self.pending_exit = _PendingExitHold(
                self.children["anchor"],
                self.children["worker"],
                self.identities["anchor"],
                self.identities["worker"],
            )
            self.flags["unknown"] = True  # sticky forward/execution uncertainty
            return True

    def safety_ready(self, statuses: tuple[tuple[str, int, int | None], ...]) -> bool:
        """A new exact owned reap, never another tick, consumes recovery once."""
        with self.lock:
            if self.flags["safety_blocked"] or self.flags["timer_failed"]:
                return False
            if not self.flags["unknown"]:
                return True
            pending = self.pending_exit
            if pending is None or self.inflight:
                return False
            if (
                self.children.keys() != {"anchor", "worker"}
                or self.children["anchor"] is not pending.anchor
                or self.children["worker"] is not pending.worker
                or self.identities
                != {"anchor": pending.anchor_identity, "worker": pending.worker_identity}
                or len(self.held_children) != 2
                or any(self.children.get(r) is not p for r, p in self.held_children)
                or len(statuses) != 2
                or {r for r, _, _ in statuses} != {"anchor", "worker"}
                or any(pid != self.children[r].pid for r, pid, _ in statuses)
            ):
                self.block_safety()
                return False
            codes = {r: rc for r, _, rc in statuses}
            if codes["anchor"] is not None:
                self.block_safety()
                return False
            if pending.resolved:
                if codes["worker"] != pending.worker_returncode or type(codes["worker"]) is not int:
                    self.block_safety()
                    return False
                return True
            if pending.consumed or codes["worker"] is None:
                return False
            if type(codes["worker"]) is not int:
                self.block_safety()
                return False
            # statuses comes only from this timer's retained Popen.poll calls.
            pending.consumed = True
            pending.worker_returncode = codes["worker"]
            return True

    def record_signal(self, observed: _GroupObservation, signum: int) -> None:
        with self.lock:
            if observed.status == "known" or observed.stage == "signal":
                self.signal_attempts.add(signum)
            if observed.status != "known":
                self.block_safety()
                return
            pending = self.pending_exit
            if pending is not None and pending.consumed and not pending.resolved:
                if (
                    observed.members != (pending.anchor_identity,)
                    or observed.expected_pids != (pending.anchor.pid,)
                    or observed.returned_pids != (pending.anchor.pid,)
                ):
                    self.block_safety()
                    return
                pending.resolved = True

    def cleanup_resolved(self) -> bool:
        with self.lock:
            return not self.flags["safety_blocked"] and (
                not self.flags["unknown"]
                or (self.pending_exit is not None and self.pending_exit.resolved)
            )

    def begin(self, role: str) -> None:
        with self.lock:
            if (
                role in self.children
                or role in self.inflight
                or self.flags["cancel"]
                or self.flags["unknown"]
            ):
                raise _ControlError("native_spawn_not_admitted")
            self.inflight.add(role)

    def attach(self, role: str, child: subprocess.Popen[bytes]) -> None:
        with self.lock:
            # Retain the successful handle even when cancellation raced Popen.
            if role in self.children or role not in self.inflight:
                raise _ControlError("native_child_custody_conflict")
            self.children[role] = child

    def _retain_spawn(self, role: str, child: subprocess.Popen[bytes]) -> None:
        """Retain every locally obtained handle even if ordinary attach failed."""
        with self.lock:
            if not any(owned is child for _, owned in self.held_children):
                self.held_children.append((role, child))
            current = self.children.get(role)
            if current is None:
                self.children[role] = child
                self.block_safety()
                self.flags["cancel"] = True
            elif current is not child:
                self.block_safety()
                self.flags["cancel"] = True

    def spawn(self, role: str, *args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        with self.lock:
            if role not in self.inflight or role in self.children:
                raise _ControlError("native_spawn_not_admitted")
        child: subprocess.Popen[bytes] | None = None
        try:
            child = subprocess.Popen(*args, **kwargs)
            self.attach(role, child)
            return child
        except BaseException:
            with self.lock:
                self.block_safety()
                self.flags["cancel"] = True
            raise
        finally:
            if child is not None:
                self._retain_spawn(role, child)

    def owned_children(self) -> tuple[tuple[str, subprocess.Popen[bytes]], ...]:
        with self.lock:
            return tuple(self.held_children)

    def publish(self, role: str, observed: identity._ProcessIdentity) -> None:
        with self.lock:
            if role not in self.children or self.children[role].pid != observed.pid:
                self.block_safety()
                raise _ControlError("native_child_identity_conflict")
            self.identities[role] = observed
            self.inflight.remove(role)

    def snapshot(self):
        with self.lock:
            return tuple(self.children.items()), dict(self.identities), frozenset(self.inflight)

    def require_forward(self, timer: threading.Thread, deadline: int) -> None:
        with self.lock:
            if (
                self.flags["unknown"]
                or self.flags["cancel"]
                or self.flags["timer_failed"]
                or self.inflight
                or not timer.is_alive()
                or _remaining(deadline, 1.0) == 0
            ):
                raise _ControlError("native_watchdog_not_enforcing")

    def release(self, timer: threading.Thread, deadline: int, fd: int, nonce: bytes) -> None:
        with self.lock:
            self.require_forward(timer, deadline)
            if (
                self.children.keys() != {"anchor", "worker"}
                or self.identities.keys() != self.children.keys()
                or any(child.poll() is not None for child in self.children.values())
            ):
                raise _ControlError("native_release_custody_incomplete")
            # A small nonblocking pipe write is the only effect under this
            # lock. Receipt, channel and child-start waits never hold it.
            os.set_blocking(fd, False)
            if os.write(fd, nonce) != len(nonce):
                self.block_safety()
                raise _ControlError("native_barrier_write_unknown")
            self.flags["released"] = True


def _child_channel(
    role: str,
    config: dict[str, Any],
    key: bytes,
    extra: tuple[int, ...],
    *,
    custody: _WatchdogState,
    **kwargs: Any,
) -> tuple[subprocess.Popen[bytes], _Channel]:
    child_read, parent_write = os.pipe()
    parent_read, child_write = os.pipe()
    key_read, key_write = os.pipe()
    owned = [
        _OwnedFD(x)
        for x in (child_read, parent_write, parent_read, child_write, key_read, key_write)
    ]
    child: subprocess.Popen[bytes] | None = None
    try:
        os.write(key_write, key)
        parent_key_closes = {"write": _close_key_copy(owned[5])}
        child = _spawn(
            role, (child_read, child_write, key_read, *extra), config, custody=custody, **kwargs
        )
        for fd in (owned[0], owned[3], owned[4]):
            if fd is owned[4]:
                parent_key_closes["read"] = _close_key_copy(fd)
            elif not fd.close():
                raise _ControlError("native_child_handoff_close_unknown")
        channel = _Channel(
            parent_read,
            parent_write,
            key,
            receipt._name(receipt._Binding(**config["binding"]))[:-8],
            _digest(config["manifest"]),
        )
        channel.send(
            "bootstrap",
            {**config, "parent_key_closes": parent_key_closes},
            config["policy"]["cleanup_deadline_continuous_ns"],
        )
        return child, channel
    except BaseException:
        for fd in owned:
            fd.close()
        raise
    finally:
        if child is not None:
            custody._retain_spawn(role, child)


def _wait_message(channel: _Channel, deadline: int) -> tuple[str, dict[str, Any]]:
    while _now() < deadline:
        value = channel.receive(wait=min(0.02, max(0, (deadline - _now()) / 1e9)))
        if value is not None:
            return value
    raise _ControlError("native_ack_deadline")


def _slot_vectors(value: object) -> tuple[receipt.D6, receipt.F9, int, int]:
    if type(value) is not dict or value.keys() != {"dalloc", "fempty", "status_flags", "offset"}:
        raise _ControlError("native_slot_shape_invalid")
    d, f = value["dalloc"], value["fempty"]
    if (
        type(d) is not list
        or len(d) != 6
        or type(f) is not list
        or len(f) != 9
        or any(type(x) is not int or x < 0 for x in d + f)
        or type(value["status_flags"]) is not int
        or type(value["offset"]) is not int
    ):
        raise _ControlError("native_slot_shape_invalid")
    return (
        (d[0], d[1], d[2], d[3], d[4], d[5]),
        (f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8]),
        value["status_flags"],
        value["offset"],
    )


def _anchor(fds: tuple[int, ...]) -> int:
    channel, config, policy = _bootstrap(fds)
    writer, directory = fds[3:]
    slot_data = config["slot"]
    dalloc, fempty, status_flags, offset = _slot_vectors(slot_data)
    binding = receipt._Binding(**config["binding"])
    slot = receipt._AllocatedSlot(
        writer,
        binding,
        channel.binding,
        receipt._name(binding),
        dalloc,
        fempty,
        status_flags,
        offset,
    )
    directory_owner = _OwnedFD(directory)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        # pass_fds must expose these copies to exec. A now owns them: restore
        # close-on-exec before applying the unchanged S2 FD/D6/F9 guards.
        try:
            for owned_fd in (writer, directory):
                os.set_inheritable(owned_fd, False)
                if os.get_inheritable(owned_fd):
                    raise _ControlError("native_receipt_fd_seal_unknown")
        except (OSError, ValueError):
            raise _ControlError("native_receipt_fd_seal_unknown") from None
        me = _known_process(os.getpid())
        checked = receipt._check_allocated_receipt_slot(directory, slot)
        if checked is not None:
            raise _ControlError("native_slot_handoff_changed")
        channel.send(
            "received",
            {
                "identity": asdict(me),
                "bootstrap_fds": channel.bootstrap_fds,
                "fds": _fd_observation("anchor", {"writer": writer, "directory": directory}),
            },
            policy.cleanup_deadline_continuous_ns,
        )
        while _now() < policy.cleanup_deadline_continuous_ns:
            message = channel.receive(wait=_remaining(policy.cleanup_deadline_continuous_ns, 0.01))
            if message is None:
                continue
            kind, payload = message
            if kind == "stop":
                return 0
            if (
                kind != "fill"
                or payload.keys() != {"raw", "d_writer_closed", "w_writer_closed"}
                or payload["d_writer_closed"] is not True
                or payload["w_writer_closed"] is not True
            ):
                raise _ControlError("native_writer_chain_unconfirmed")
            policy.before_release(clock._sample_clock())
            raw = bytes.fromhex(payload["raw"])
            authenticated = receipt._authenticate_receipt(raw, binding, channel.key)
            if isinstance(authenticated, receipt._ReceiptHold):
                raise _ControlError("native_arm_authentication")
            fact = authenticated.fact
            if (
                fact.shim.identity is None
                or fact.leader.identity is None
                or fact.leader.identity.pid != me.pid
                or not _matches(fact.shim.identity)
                or not _matches(fact.leader.identity)
            ):
                raise _ControlError("native_arm_identity_changed")
            result = receipt._fill_allocated_receipt_slot(
                directory, slot, binding, channel.key, raw
            )
            closed = slot._close_once()
            if isinstance(result, receipt._ReceiptHold) or closed is not None:
                raise _ControlError("native_arm_fill_unknown")
            channel.send(
                "filled",
                {
                    "arm_digest": result.receipt.digest,
                    "fds": _fd_observation("anchor_after_fill", {"writer": writer}),
                    "writer_closed": slot.close_state == "CLOSED",
                },
                policy.cleanup_deadline_continuous_ns,
            )
        return 1
    finally:
        slot._close_once()
        directory_owner.close()


_STARTUP_PHASES = frozenset(
    {
        "config_read",
        "config_decode",
        "config_validate",
        "command_validate",
        "fd_observe",
        "fd_absence",
        "config_close",
        "identity_observe",
        "inert_emit",
    }
)
_STARTUP_CATEGORIES = frozenset(
    {"control_rejection", "json_invalid", "os_error", "unexpected_failure"}
)


def _startup_fd_facts(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if (
        type(value) is not dict
        or value.keys() != {"role", "handles", "protected_slots", "protected_handles_absent"}
        or value["role"] != "worker"
        or type(value["protected_handles_absent"]) is not bool
    ):
        raise _ControlError("native_worker_startup_diagnostic_invalid")
    handles, slots = value["handles"], value["protected_slots"]
    if (
        type(handles) is not dict
        or handles.keys() != {"config", "barrier", "evidence"}
        or type(slots) is not dict
        or len(slots) > 32
        or slots.keys() != {f"slot_{i}" for i in range(len(slots))}
    ):
        raise _ControlError("native_worker_startup_diagnostic_invalid")
    projected = []
    for table in (handles, slots):
        clean = {}
        for name, item in table.items():
            if (
                type(item) is not dict
                or type(item.get("open")) is not bool
                or type(item.get("fd")) is not int
                or item["fd"] < 0
            ):
                raise _ControlError("native_worker_startup_diagnostic_invalid")
            keys = (
                {"open", "fd", "device", "inode", "close_on_exec"}
                if item["open"]
                else {"open", "fd"}
            )
            if item.keys() != keys or (
                item["open"]
                and (
                    any(type(item[k]) is not int or item[k] < 0 for k in ("device", "inode"))
                    or type(item["close_on_exec"]) is not bool
                )
            ):
                raise _ControlError("native_worker_startup_diagnostic_invalid")
            clean[name] = {key: item[key] for key in keys}
        projected.append(clean)
    return {
        "role": "worker",
        "handles": projected[0],
        "protected_slots": projected[1],
        "protected_handles_absent": value["protected_handles_absent"],
    }


def _startup_diagnostic(value: object) -> dict[str, Any]:
    if (
        type(value) is not dict
        or value.keys() != {"event", "phase", "category", "errno", "fds"}
        or value["event"] != "worker_startup_hold"
        or type(value["phase"]) is not str
        or value["phase"] not in _STARTUP_PHASES
        or type(value["category"]) is not str
        or value["category"] not in _STARTUP_CATEGORIES
        or (value["errno"] is not None and (type(value["errno"]) is not int or value["errno"] < 0))
    ):
        raise _ControlError("native_worker_startup_diagnostic_invalid")
    return {
        "phase": value["phase"],
        "category": value["category"],
        "errno": value["errno"],
        "fds": _startup_fd_facts(value["fds"]),
    }


def _emit_startup_hold(fd: int, phase: str, failure: BaseException, observed: object) -> None:
    # Diagnostic failure must never replace the original exception or cleanup.
    with suppress(BaseException):
        if isinstance(failure, json.JSONDecodeError):
            category = "json_invalid"
        elif type(failure) is _ControlError:
            category = "control_rejection"
        elif isinstance(failure, OSError):
            category = "os_error"
        else:
            category = "unexpected_failure"
        number = failure.errno if isinstance(failure, OSError) else None
        if number is not None and (type(number) is not int or number < 0):
            number = None
        frame = {
            "event": "worker_startup_hold",
            "phase": phase,
            "category": category,
            "errno": number,
            "fds": _startup_fd_facts(observed),
        }
        _startup_diagnostic(frame)
        raw = _json(frame) + b"\n"
        if len(raw) > 8192:
            frame["fds"] = None
            raw = _json(frame) + b"\n"
        os.set_blocking(fd, False)
        os.write(fd, raw)  # exactly one best-effort write; partial stays unknown


def _reject_startup_hold(value: object, record: Callable[..., None]) -> NoReturn:
    try:
        safe = _startup_diagnostic(value)
    except _ControlError:
        raise _ControlError("native_worker_inert_unknown") from None
    record("worker_startup_hold", **safe)
    raise _ControlError("native_worker_inert_unknown")


def _trampoline(fds: tuple[int, ...]) -> int:
    config_fd, barrier_fd, evidence_fd = fds
    config_owner, barrier_owner, evidence_owner = map(_OwnedFD, fds)
    phase = "config_read"
    observed: dict[str, Any] | None = None
    inert_sent = False
    try:
        raw = bytearray()
        while True:
            chunk = os.read(config_fd, 4096)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > 8192:
                raise _ControlError("native_worker_config_invalid")
        phase = "config_decode"
        config = json.loads(raw)
        phase = "config_validate"
        if config.keys() != {
            "command",
            "nonce",
            "protected_objects",
            "closed_key_history",
            "fd_handoff",
            "deadline_continuous_ns",
        } or not receipt._integer(config["deadline_continuous_ns"]):
            raise _ControlError("native_worker_config_invalid")
        phase = "command_validate"
        command = config["command"]
        executable = str(Path(sys.executable).resolve())
        if (
            command["argv"] != [executable, "-I", "-c", "pass"]
            or command["executable"] != executable
        ):
            raise _ControlError("native_worker_command_invalid")
        phase = "fd_observe"
        observed = _worker_fd_observation(
            {"config": config_fd, "barrier": barrier_fd, "evidence": evidence_fd},
            config["protected_objects"],
            config["closed_key_history"],
            config["fd_handoff"],
        )
        phase = "fd_absence"
        if not observed["protected_handles_absent"]:
            raise _ControlError("native_worker_inherited_authority")
        phase = "config_close"
        if not config_owner.close():
            raise _ControlError("native_worker_config_close_unknown")
        phase = "identity_observe"
        me = _known_process(os.getpid())
        phase = "inert_emit"
        os.write(
            evidence_fd,
            _json(
                {
                    "event": "worker_inert",
                    "identity": asdict(me),
                    "fds": observed,
                }
            )
            + b"\n",
        )
        inert_sent = True
        import select

        remaining = _remaining(config["deadline_continuous_ns"], float("inf"))
        if remaining == 0 or not select.select([barrier_fd], [], [], remaining)[0]:
            return 3
        token = os.read(barrier_fd, 65)
        if token != config["nonce"].encode("ascii"):
            return 2
        if not barrier_owner.close():
            raise _ControlError("native_barrier_close_unknown")
        os.write(
            evidence_fd,
            _json({"event": "worker_consumed_barrier", "control_fds_closed": True}) + b"\n",
        )
        if not evidence_owner.close():
            raise _ControlError("native_worker_evidence_close_unknown")
        os.execve(executable, command["argv"], command["environment"])
        return 1
    except BaseException as failure:
        if not inert_sent:
            _emit_startup_hold(evidence_fd, phase, failure, observed)
        raise
    finally:
        config_owner.close()
        barrier_owner.close()
        evidence_owner.close()


def _watchdog_timer(
    custody: _WatchdogState,
    policy: _NativePolicy,
    finished: threading.Event,
    event: Callable[..., None],
) -> None:
    """The production timer body, also driven by finite deterministic fixtures."""
    state, lock = custody.flags, custody.lock

    def cancel(reason: str) -> None:
        with lock:
            state["cancel"] = True
            state["reason"] = reason

    termed_at: int | None = None
    killed = False
    while not finished.is_set():
        try:
            now = _now()
            if now >= policy.deadline_continuous_ns:
                cancel("deadline")
            snapshot, known, inflight = custody.snapshot()
            with lock:
                active = state["cancel"]
                signal_admitted = not state["safety_blocked"]
            anchor = known.get("anchor")
            statuses = tuple((k, p.pid, p.poll()) for k, p in snapshot)
            live = tuple(k for k, _, returncode in statuses if returncode is None)
            relied = tuple(known[k] for k in live if k in known)
            # An explicitly in-flight spawn is neither a failed timer nor
            # a registered signal target. Keep the timer running; release
            # is excluded until handle+identity publication completes.
            complete = not inflight and all(k in known for k in live)
            if (
                active
                and signal_admitted
                and complete
                and now < policy.cleanup_deadline_continuous_ns
                and anchor is not None
                and (anchor_returncode := dict(snapshot)["anchor"].poll()) is None
                and custody.safety_ready(statuses)
            ):
                signum = _timer_signal(policy, now, active, termed_at, killed)
                if signum is not None and signum not in custody.signal_attempts:
                    observed = _signal_owned_group(
                        anchor,
                        relied,
                        signum,
                        owner=custody,
                        custody_snapshot=snapshot,
                        registered=known,
                    )
                    successful_term_time = (
                        _now() if observed.status == "known" and signum == signal.SIGTERM else None
                    )
                    event(
                        "signal"
                        if observed.status == "known" or observed.stage == "signal"
                        else "signal_observation",
                        signal=int(signum),
                        known=observed.status == "known",
                        members=[asdict(x) for x in observed.members],
                        observation_stage=observed.stage,
                        issues=list(observed.issues),
                        expected_pids=list(observed.expected_pids),
                        returned_pids=list(observed.returned_pids),
                        decision_children=[
                            {"role": role, "pid": pid, "returncode": returncode}
                            for role, pid, returncode in statuses
                        ],
                        anchor_returncode=anchor_returncode,
                        successful_term_continuous_ns=successful_term_time,
                    )
                    if observed.status != "known":
                        if not custody.defer_identity_hold(observed, statuses):
                            custody.record_signal(observed, signum)
                        # Uncertainty forbids release, never a stale signal
                        # fallback. Keep timing/owned-handle observation.
                        finished.wait(_remaining(policy.cleanup_deadline_continuous_ns, 0.01))
                        continue
                    custody.record_signal(observed, signum)
                    if termed_at is None:
                        termed_at = successful_term_time
                    else:
                        killed = True
            if now >= policy.cleanup_deadline_continuous_ns:
                if inflight or any(p.poll() is None for _, p in snapshot):
                    with lock:
                        custody.block_safety()
                return
            finished.wait(_remaining(policy.cleanup_deadline_continuous_ns, 0.01))
        except BaseException:
            with lock:
                custody.block_safety()
                state["cancel"] = True
                state["timer_failed"] = True
            return


def _watchdog(fds: tuple[int, ...]) -> int:
    channel, config, policy = _bootstrap(fds)
    writer, directory, report_fd, terminal_writer, terminal_directory = fds[3:]
    writer_owner, directory_owner, report_owner = map(_OwnedFD, (writer, directory, report_fd))
    terminal_directory_owner = _OwnedFD(terminal_directory)
    terminal_values = config["terminal_slot"]
    binding = receipt._Binding(**config["binding"])
    terminal_dalloc, terminal_fempty, terminal_flags, terminal_offset = _slot_vectors(
        {name: terminal_values[name] for name in ("dalloc", "fempty", "status_flags", "offset")}
    )
    terminal_slot = receipt._AllocatedSlot(
        terminal_writer,
        binding,
        receipt._name(binding)[:-8],
        receipt._name(binding),
        terminal_dalloc,
        terminal_fempty,
        terminal_flags,
        terminal_offset,
        "terminal",
    )
    # Python deliberately marks passed FDs inheritable for exec. Reseal only
    # these two exact owned capabilities, then run unchanged S2 validators.
    for fd in (terminal_writer, terminal_directory):
        os.set_inheritable(fd, False)
        if os.get_inheritable(fd):
            raise _ControlError("terminal_custody_unknown")
    if receipt._check_allocated_receipt_slot(terminal_directory, terminal_slot) is not None:
        raise _ControlError("terminal_file_unknown")
    registration_digest: str | None = None
    control_revision = 0
    me = _known_process(os.getpid())
    events: list[dict[str, Any]] = []
    custody = _WatchdogState()
    children, identities, state, lock = (
        custody.children,
        custody.identities,
        custody.flags,
        custody.lock,
    )
    finished = threading.Event()
    barrier_owner: _OwnedFD | None = None
    anchor_channel: _Channel | None = None
    anchor_control_owners: tuple[_OwnedFD, ...] = ()
    control_owners = (_OwnedFD(channel.read_fd), _OwnedFD(channel.write_fd))
    local_fds: dict[int, _OwnedFD] = {}

    def event(name: str, **facts: Any) -> None:
        try:
            observed_time = _now()
        except BaseException:
            observed_time = None
            with lock:
                custody.block_safety()
        with lock:
            if len(events) >= 128:
                custody.block_safety()
                return
            events.append(
                {
                    "ordinal": len(events),
                    "role": "watchdog",
                    "event": name,
                    "continuous_ns": observed_time,
                    "clock_known": observed_time is not None,
                    **facts,
                }
            )

    def cancel(reason: str) -> None:
        with lock:
            state["cancel"] = True
            state["reason"] = reason

    def hold_accounting(operation: str, pid: int | None, failure: BaseException) -> NoReturn:
        custody.block_safety()
        event("custody_accounting_held", operation=operation, pid=pid)
        diagnostic = {
            "schema_version": "stackos.native-custody-held.v1",
            "owner": "watchdog",
            "operation": operation,
            "pid": pid,
            "code": "native_wait_accounting_held",
            "errno": failure.errno if isinstance(failure, OSError) else None,
            "product_success": False,
            "handle_retained": True,
        }

        def publish(safe: dict[str, Any]) -> None:
            raw = _json(
                {
                    "schema_version": "stackos.native-observation.v1",
                    "binding_digest": channel.binding,
                    "manifest_digest": channel.manifest,
                    "released": state["released"],
                    "execution_unknown": True,
                    "cleanup_known": False,
                    "certified": False,
                    "events": events,
                    "custody_held": safe,
                }
            )
            if len(raw) > policy.max_output_bytes or os.write(report_fd, raw) != len(raw):
                raise _ControlError("native_held_report_write_unknown")
            os.fsync(report_fd)

        _park_owned(diagnostic, publish)

    custody.policy = policy
    thread = threading.Thread(
        target=_watchdog_timer,
        args=(custody, policy, finished, event),
        name="controlled-watchdog",
        daemon=True,
    )
    custody.timer = thread
    thread.start()
    try:
        event(
            "watchdog_armed",
            identity=asdict(me),
            bootstrap_fds=channel.bootstrap_fds,
            fds=_fd_observation(
                "watchdog",
                {
                    "writer": writer,
                    "directory": directory,
                    "report": report_fd,
                    "control_read": channel.read_fd,
                    "control_write": channel.write_fd,
                    "terminal_writer": terminal_writer,
                    "terminal_directory": terminal_directory,
                },
            ),
        )
        channel.send(
            "received",
            {"identity": asdict(me), "fds": events[-1]["fds"]},
            policy.cleanup_deadline_continuous_ns,
        )
        kind, payload = _wait_message(channel, policy.deadline_continuous_ns)
        if kind != "writer_closed" or payload != {"closed": True, "terminal_closed": True}:
            raise _ControlError("native_d_writer_close_unconfirmed")
        event("d_writer_close_ack")
        policy.before_release(clock._sample_clock())
        custody.require_forward(thread, policy.deadline_continuous_ns)
        custody.begin("anchor")
        a, anchor_channel = _child_channel(
            "anchor",
            config,
            channel.key,
            (writer, directory),
            custody=custody,
            process_group=0,
        )
        anchor_control_owners = (
            _OwnedFD(anchor_channel.read_fd),
            _OwnedFD(anchor_channel.write_fd),
        )
        custody.publish("anchor", _known_process(a.pid))
        kind, payload = _wait_message(anchor_channel, policy.deadline_continuous_ns)
        if kind != "received" or payload.get("identity") != asdict(identities["anchor"]):
            raise _ControlError("native_a_handoff_unknown")
        event("a_received", fds=payload["fds"], bootstrap_fds=payload["bootstrap_fds"])
        if not writer_owner.close():
            raise _ControlError("native_w_writer_close_unknown")
        event("w_writer_closed", fds=_fd_observation("watchdog_after_transfer", {"writer": writer}))
        # T receives no key, writer, directory or any existing control-write FD.
        cfg_read, cfg_write = os.pipe()
        barrier_read, barrier_write = os.pipe()
        evidence_read, evidence_write = os.pipe()
        local_fds = {
            fd: _OwnedFD(fd)
            for fd in (
                cfg_read,
                cfg_write,
                barrier_read,
                barrier_write,
                evidence_read,
                evidence_write,
            )
        }
        barrier_owner = local_fds[barrier_write]
        protected = [x for x in payload["fds"]["handles"].values() if x["open"]]
        protected.extend(x for x in events[0]["fds"]["handles"].values() if x["open"])
        retained = terminal_values["retained"]
        if type(retained) is not dict or retained.keys() != {"read", "directory"}:
            raise _ControlError("terminal_custody_unknown")
        if [
            terminal_writer,
            terminal_directory,
            retained["read"]["fd"],
            retained["directory"]["fd"],
        ] != terminal_values["known_slots"]:
            raise _ControlError("terminal_custody_unknown")
        if any(retained[name]["open"] is not True for name in retained):
            raise _ControlError("terminal_custody_unknown")
        protected.extend(retained.values())
        closed_keys = []
        for bootstrap in (payload["bootstrap_fds"], channel.bootstrap_fds):
            if type(bootstrap) is not dict:
                raise _ControlError("native_bootstrap_fd_unknown")
            handles = bootstrap.get("handles")
            if type(handles) is not dict:
                raise _ControlError("native_bootstrap_fd_unknown")
            closed_keys.extend(_key_handoff_history(bootstrap))
            protected.extend(x for x in handles.values() if x["open"])
        worker_fds = {"config": cfg_read, "barrier": barrier_read, "evidence": evidence_write}
        fd_handoff = {
            "handles": _fd_observation("watchdog_worker_handoff", worker_fds)["handles"],
            "pass_fds": list(worker_fds.values()),
            "close_fds": True,
        }
        worker_config = {
            "command": config["manifest"]["command"],
            "nonce": config["barrier_nonce"],
            "protected_objects": protected,
            "closed_key_history": closed_keys,
            "fd_handoff": fd_handoff,
            "deadline_continuous_ns": policy.deadline_continuous_ns,
        }
        os.write(cfg_write, _json(worker_config))
        if not local_fds[cfg_write].close():
            raise _ControlError("native_worker_config_close_unknown")
        custody.require_forward(thread, policy.deadline_continuous_ns)
        custody.begin("worker")
        event("worker_fd_handoff", binding=fd_handoff, closed_key_history=closed_keys)
        t = _spawn(
            "trampoline",
            tuple(worker_fds.values()),
            config,
            custody=custody,
            process_group=a.pid,
        )
        for fd in (cfg_read, barrier_read, evidence_write):
            if not local_fds[fd].close():
                raise _ControlError("native_worker_handoff_close_unknown")
        custody.publish("worker", _known_process(t.pid))
        worker_event = bytearray()
        while b"\n" not in worker_event and _now() < policy.deadline_continuous_ns:
            import select

            if select.select(
                [evidence_read], [], [], _remaining(policy.deadline_continuous_ns, 0.01)
            )[0]:
                chunk = os.read(evidence_read, 4096)
                if not chunk:
                    raise _ControlError("native_worker_inert_unknown")
                worker_event.extend(chunk)
                if len(worker_event) > 8192:
                    raise _ControlError("native_worker_evidence_oversized")
        inert = json.loads(worker_event)
        if type(inert) is dict and inert.get("event") == "worker_startup_hold":
            _reject_startup_hold(inert, event)
        if (
            inert.get("event") != "worker_inert"
            or inert.get("identity") != asdict(identities["worker"])
            or inert.get("fds", {}).get("protected_handles_absent") is not True
        ):
            raise _ControlError("native_worker_inert_unknown")
        event("worker_inert", fds=inert["fds"], identity=inert["identity"])
        channel.send(
            "ready",
            {
                "identities": {
                    "watchdog": asdict(me),
                    **{k: asdict(v) for k, v in identities.items()},
                },
                "events": list(events),
            },
            policy.cleanup_deadline_continuous_ns,
        )
        kind, payload = _wait_message(channel, policy.deadline_continuous_ns)
        if (
            kind != "fill"
            or payload.keys() != {"raw", "registration_digest", "control_revision"}
            or not receipt._hex(payload["registration_digest"])
            or not receipt._integer(payload["control_revision"])
        ):
            raise _ControlError("native_fill_missing")
        registration_digest, control_revision = (
            payload["registration_digest"],
            payload["control_revision"],
        )
        custody.require_forward(thread, policy.deadline_continuous_ns)
        anchor_channel.send(
            "fill",
            {"raw": payload["raw"], "d_writer_closed": True, "w_writer_closed": True},
            policy.cleanup_deadline_continuous_ns,
        )
        kind, payload = _wait_message(anchor_channel, policy.deadline_continuous_ns)
        if kind != "filled" or payload.get("writer_closed") is not True:
            raise _ControlError("native_arm_close_unknown")
        event("anchor_filled", **payload)
        channel.send("filled", payload, policy.cleanup_deadline_continuous_ns)
        kind, payload = _wait_message(channel, policy.deadline_continuous_ns)
        if kind == "release" and payload == {
            "nonce_digest": _digest(config["barrier_nonce"]),
            "registration_digest": registration_digest,
            "control_revision": control_revision + 2,
        }:
            control_revision = payload["control_revision"]
            policy.before_release(clock._sample_clock())
            if not all(_matches(x) for x in identities.values()):
                raise _ControlError("native_release_identity_changed")
            group = _observe_owned_group(
                anchor=identities["anchor"], expected=(identities["anchor"], identities["worker"])
            )
            if group.status != "known":
                raise _ControlError("native_release_group_unknown")
            event("release_group_observed", members=[asdict(x) for x in group.members])
            custody.release(
                thread,
                policy.deadline_continuous_ns,
                barrier_write,
                config["barrier_nonce"].encode("ascii"),
            )
            if not barrier_owner.close():
                raise _ControlError("native_barrier_close_unknown")
            event("release_sent")
            channel.send("released", {}, policy.cleanup_deadline_continuous_ns)
        else:
            cancel("controller_eof")
        while _now() < policy.cleanup_deadline_continuous_ns:
            if not state["cancel"]:
                try:
                    message = channel.receive()
                    if message is not None:
                        if message[0] != "cancel":
                            raise _ControlError("native_control_sequence")
                        cancel("controller_eof")
                except _ControlError:
                    cancel("controller_eof")
            if t.poll() is not None and not state["cancel"]:
                # Graceful normal drain avoids a signal after anchor exit.
                anchor_channel.send("stop", {}, policy.cleanup_deadline_continuous_ns)
                while a.poll() is None and _now() < policy.cleanup_deadline_continuous_ns:
                    finished.wait(_remaining(policy.cleanup_deadline_continuous_ns, 0.005))
                break
            if all(p.poll() is not None for p in children.values()):
                break
            finished.wait(_remaining(policy.cleanup_deadline_continuous_ns, 0.005))
        # Role-local final barrier-consumption observation, not an assertion
        # based only on the parent having written a token.
        import select

        if select.select([evidence_read], [], [], 0)[0]:
            tail = os.read(evidence_read, 8192)
            for line in tail.splitlines():
                item = json.loads(line)
                if item.keys() == {"event", "control_fds_closed"}:
                    event(item["event"], control_fds_closed=item["control_fds_closed"])
        local_fds[evidence_read].close()
    except BaseException as exc:
        cancel("controller_eof")
        event("hold", code=str(exc) if type(exc) is _ControlError else "native_role_failure")
        # A protocol Hold does not itself erase independently observed cleanup.
        # Unknown cleanup is latched separately by failed OS observations.
    finally:
        for owned_fd in local_fds.values():
            if not owned_fd.close():
                custody.block_safety()
        if barrier_owner is not None and not barrier_owner.close():
            custody.block_safety()
        if anchor_channel is not None and "anchor" not in identities:
            # EOF makes an incompletely bootstrapped anchor exit itself. Do not
            # invent signal authority when identity corroboration failed.
            for owner in anchor_control_owners:
                if not owner.close():
                    custody.block_safety()
        try:
            if any(p.poll() is None for p in children.values()):
                cancel("controller_eof")
                while _now() < policy.cleanup_deadline_continuous_ns and any(
                    p.poll() is None for p in children.values()
                ):
                    finished.wait(_remaining(policy.cleanup_deadline_continuous_ns, 0.005))
        except BaseException:
            custody.block_safety()
        finished.set()
        try:
            thread.join(timeout=_remaining(policy.cleanup_deadline_continuous_ns, float("inf")))
        except BaseException:
            custody.block_safety()
        # Product cleanup uncertainty does not discard the W owner. No further
        # signal/release is admitted here; keep exact thread/child handles alive
        # until they are accounted, without calling this timely cleanup.
        if thread.is_alive():
            custody.block_safety()
            if (
                _account_owned(
                    thread.join, lambda failure: hold_accounting("timer_join", None, failure)
                )
                is not None
            ):
                custody.block_safety()
        for role, child in custody.owned_children():
            if child.poll() is None:
                custody.block_safety()
                event("custody_retained", child_role=role, pid=child.pid)

            def hold_child(failure: BaseException, pid: int = child.pid) -> NoReturn:
                hold_accounting("waitpid", pid, failure)

            if _account_owned(child.wait, hold_child) is not None:
                custody.block_safety()
        if barrier_owner is not None:
            barrier_owner.close()
        writer_closed = writer_owner.close()
        directory_closed = directory_owner.close()
        if not writer_closed or not directory_closed:
            custody.block_safety()
        reaped = {k: p.poll() is not None for k, p in children.items()}
        try:
            cleanup_completed: int | None = _now()
            if (
                cleanup_completed is None
                or cleanup_completed >= policy.cleanup_deadline_continuous_ns
            ):
                custody.block_safety()
        except BaseException:
            cleanup_completed = None
            custody.block_safety()
        event(
            "cleanup",
            reaped=reaped,
            writer_closed=writer_closed,
            directory_closed=directory_closed,
            returncodes={k: p.returncode for k, p in children.items()},
            completed_continuous_ns=cleanup_completed,
        )
        report = {
            "schema_version": "stackos.native-observation.v1",
            "binding_digest": channel.binding,
            "manifest_digest": channel.manifest,
            "released": state["released"],
            "execution_unknown": state["unknown"],
            "cleanup_known": all(reaped.values())
            and writer_closed
            and directory_closed
            and custody.cleanup_resolved()
            and not custody.inflight
            and not thread.is_alive(),
            "cleanup_completed_continuous_ns": cleanup_completed,
            "events": events,
            "certified": False,
        }
        raw = _json(report)
        if len(raw) > policy.max_output_bytes:
            custody.block_safety()
            report["cleanup_known"] = False
            raw = _json(
                {
                    "schema_version": "stackos.native-observation.v1",
                    "cleanup_known": False,
                    "execution_unknown": True,
                    "events": [],
                    "certified": False,
                }
            )
        report_written = False
        try:
            remaining = raw
            for _ in range(256):
                if not remaining:
                    break
                try:
                    count = os.write(report_fd, remaining)
                except InterruptedError:
                    continue
                if count <= 0:
                    raise _ControlError("native_report_write_unknown")
                remaining = remaining[count:]
            if remaining:
                raise _ControlError("native_report_write_unknown")
            os.fsync(report_fd)
            report_written = True
            channel.send(
                "done", {"report_digest": _digest(report)}, policy.cleanup_deadline_continuous_ns
            )
        except (OSError, ValueError):
            pass
        report_closed = report_owner.close()
        if not report_written or not report_closed:
            custody.block_safety()
        try:
            report_timely = _now() < policy.cleanup_deadline_continuous_ns
        except BaseException:
            report_timely = False
        if not report_timely:
            custody.block_safety()
        # Terminal evidence cannot attest W's later close/exit. D independently
        # accounts those exact capabilities before committing/certifying.
        control_closed = True
        for control_owner in (*anchor_control_owners, *control_owners):
            if not control_owner.close():
                control_closed = False
                custody.block_safety()
        terminal_written = False
        try:
            observation = clock._sample_clock()
            fact = receipt._TerminalFact(
                "terminal",
                "host-watchdog",
                1,
                registration_digest,
                control_revision,
                observation,
                {
                    "watchdog": me,
                    "anchor": identities.get("anchor"),
                    "worker": identities.get("worker"),
                },
                {
                    "anchor": children["anchor"].returncode if "anchor" in children else None,
                    "worker": children["worker"].returncode if "worker" in children else None,
                },
                bool(report["cleanup_known"]),
                bool(state["unknown"]),
                bool(state["safety_blocked"]),
                {"writer": writer_closed, "directory": directory_closed, "control": control_closed},
                _digest(report) if report_written and report_closed else None,
                cleanup_completed,
                tuple(
                    sorted(
                        {
                            code
                            for code, occurred in (
                                ("terminal_cleanup_unknown", not report["cleanup_known"]),
                                ("terminal_clock_unknown", not clock._valid_clock(observation)),
                                (
                                    "terminal_control_unknown",
                                    not control_closed or registration_digest is None,
                                ),
                                (
                                    "terminal_report_unknown",
                                    not report_written or not report_closed,
                                ),
                            )
                            if occurred
                        }
                    )
                ),
            )
            terminal_raw = receipt._seal_terminal_receipt(binding, fact, channel.key)
            if not isinstance(terminal_raw, receipt._ReceiptHold):
                filled = receipt._fill_terminal_slot(
                    terminal_directory, terminal_slot, binding, channel.key, terminal_raw
                )
                terminal_written = not isinstance(filled, receipt._ReceiptHold)
        except (OSError, ValueError, TypeError):
            terminal_written = False
        terminal_closed = terminal_slot._close_once() is None and terminal_directory_owner.close()
        try:
            terminal_timely = _now() < policy.cleanup_deadline_continuous_ns
        except BaseException:
            terminal_timely = False
    return (
        0
        if report["cleanup_known"]
        and report_written
        and report_closed
        and report_timely
        and terminal_written
        and terminal_closed
        and terminal_timely
        else 1
    )


def _entry(role: str, inherited_fds: tuple[int, ...]) -> int:
    """Validate the raw CLI role before dispatch to the exact role allowlist."""
    counts = {"watchdog": 8, "anchor": 5, "trampoline": 3}
    if (
        role not in counts
        or len(inherited_fds) != counts[role]
        or any(type(x) is not int or x < 3 for x in inherited_fds)
        or len(set(inherited_fds)) != len(inherited_fds)
    ):
        return 64
    try:
        return {"watchdog": _watchdog, "anchor": _anchor, "trampoline": _trampoline}[role](
            inherited_fds
        )
    except BaseException:
        # No raw exception, argv, frame, MAC or key reaches stderr/audit.
        return 70


if __name__ == "__main__":
    try:
        raise SystemExit(_entry(sys.argv[1], tuple(int(x) for x in sys.argv[2:])))
    except (IndexError, ValueError):
        raise SystemExit(64) from None
