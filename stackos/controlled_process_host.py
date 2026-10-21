"""Private cooperative native control. Importing this module has no native effects.

The public controlled connector stays unavailable. This component is reached
only by its current authenticated private caller, never by registry dispatch.
"""

from __future__ import annotations

import asyncio
import ctypes
import errno
import hashlib
import hmac
import json
import os
import select
import signal
import stat
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from stackos import controlled_process_clock as clock
from stackos import controlled_process_identity as identity
from stackos import controlled_process_receipt as receipt

if TYPE_CHECKING:
    from sqlmodel import Session

    from stackos.actions.controlled_command import _ResolvedControlledCommand
    from stackos.controlled_process_shim import _WatchdogState
    from stackos.operations.actions.controlled import _NativeAdmission
    from stackos.repositories.runs import (
        RunOut,
        WorkItemOwnershipOut,
        _ControlCursor,
        _NativeSetSnapshot,
        _NativeTransition,
    )

_MAX_FRAME = 16_384
_DOMAIN = b"stackos-native-control-v1\0"
_KINDS = frozenset(
    {
        "bootstrap",
        "received",
        "writer_closed",
        "ready",
        "fill",
        "filled",
        "release",
        "released",
        "cancel",
        "stop",
        "done",
        "hold",
    }
)
_RUNTIME_FILES = (
    "stackos/controlled_process_host.py",
    "stackos/controlled_process_shim.py",
    "stackos/controlled_process_clock.py",
    "stackos/controlled_process_identity.py",
    "stackos/controlled_process_receipt.py",
    "stackos/operations/actions/controlled.py",
    "stackos/repositories/runs.py",
    "stackos/mcp/permissions.py",
    "stackos/mcp/context.py",
    "stackos/repositories/tracker/relations.py",
)


class _ControlError(ValueError):
    """Fixed nonsecret diagnostic, never raw OS/IPC/payload text."""


def _park_owned(
    diagnostic: dict[str, Any], publish: Callable[[dict[str, Any]], None] | None = None
) -> NoReturn:
    """Keep this exact owning frame alive after permanent accounting failure."""
    try:
        if publish is None:
            sys.stderr.write(json.dumps(diagnostic, sort_keys=True) + "\n")
            sys.stderr.flush()
        else:
            publish(diagnostic)
    except BaseException:
        diagnostic["diagnostic_write_failed"] = True
    held = threading.Event()
    while True:
        try:
            held.wait()  # no polling, retry, new signal or forward authority
            held.clear()
        except BaseException:
            diagnostic["park_interrupted"] = True


def _account_owned(
    wait: Callable[[], object], hold: Callable[[BaseException], NoReturn]
) -> BaseException | None:
    """Retry interruption only; permanent failure retains the owning frame."""
    interrupted: BaseException | None = None
    while True:
        try:
            wait()
            return interrupted
        except BaseException as failure:
            if isinstance(
                failure, (InterruptedError, KeyboardInterrupt, SystemExit, asyncio.CancelledError)
            ) or (isinstance(failure, OSError) and failure.errno == errno.EINTR):
                if interrupted is None:
                    interrupted = failure
            else:
                hold(failure)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _native_manifest(command: _ResolvedControlledCommand) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    executable = Path(sys.executable).resolve(strict=True)
    return {
        "schema_version": "stackos.native-runtime.v1",
        "command": command.descriptor(),
        "source_root": str(root),
        "source_files": {
            p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in _RUNTIME_FILES
        },
        "executable": str(executable),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "shim": str(root / "stackos/controlled_process_shim.py"),
        "watchdog_entry": str(root / "stackos/controlled_process_shim.py"),
        "watchdog_entry_sha256": hashlib.sha256(
            (root / "stackos/controlled_process_shim.py").read_bytes()
        ).hexdigest(),
        "python_flags": ["-I", "-S"],
        "roles": ["watchdog", "anchor", "trampoline"],
    }


@dataclass(frozen=True)
class _NativePolicy:
    boot_uuid: str
    deadline_continuous_ns: int
    term_grace_ns: int
    cleanup_deadline_continuous_ns: int
    max_output_bytes: Literal[65536] = 65536
    member_limit: Literal[8] = 8
    _decision: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def deadline_ref(self) -> str:
        return "native:" + _digest(self._decision)

    @classmethod
    def from_decision(
        cls, value: object, *, command_digest: str, runtime_digest: str
    ) -> _NativePolicy:
        keys = {
            "schema_version",
            "command_digest",
            "runtime_digest",
            "boot_uuid",
            "deadline_continuous_ns",
            "term_grace_ns",
            "cleanup_deadline_continuous_ns",
            "max_output_bytes",
            "member_limit",
            "threat_model",
        }
        if (
            type(value) is not dict
            or value.keys() != keys
            or value.get("schema_version") != "stackos.controlled-host-approval.v1"
            or value.get("command_digest") != command_digest
            or value.get("runtime_digest") != runtime_digest
            or not receipt._hex(command_digest)
            or not receipt._hex(runtime_digest)
            or value.get("threat_model") != "cooperative-fixed-command"
            or not receipt._uuid(value.get("boot_uuid"))
            or not receipt._integer(value.get("deadline_continuous_ns"))
            or not receipt._integer(value.get("cleanup_deadline_continuous_ns"))
            or not receipt._integer(value.get("term_grace_ns"), 0)
            or type(value.get("max_output_bytes")) is not int
            or value["max_output_bytes"] != 65536
            or type(value.get("member_limit")) is not int
            or value["member_limit"] != 8
            or value["cleanup_deadline_continuous_ns"] < value["deadline_continuous_ns"]
        ):
            raise _ControlError("native_policy_invalid")
        return cls(
            value["boot_uuid"],
            value["deadline_continuous_ns"],
            value["term_grace_ns"],
            value["cleanup_deadline_continuous_ns"],
            _decision=dict(value),
        )

    def check(self, observed: clock._ClockObservation) -> int:
        if (
            not clock._valid_clock(observed)
            or observed.status != "known"
            or observed.boot_uuid != self.boot_uuid
            or observed.continuous_ns is None
        ):
            raise _ControlError("native_clock_unknown")
        return observed.continuous_ns

    def before_release(self, observed: clock._ClockObservation) -> None:
        if self.check(observed) >= self.deadline_continuous_ns:
            raise _ControlError("native_deadline_expired")


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise _ControlError("native_frame_invalid")
        result[name] = value
    return result


def _frame(
    kind: str,
    payload: dict[str, Any],
    key: bytes,
    *,
    sequence: int,
    binding_digest: str,
    manifest_digest: str,
) -> bytes:
    if (
        kind not in _KINDS
        or type(key) is not bytes
        or len(key) != 32
        or not receipt._integer(sequence, 0)
        or not receipt._hex(binding_digest)
        or not receipt._hex(manifest_digest)
        or type(payload) is not dict
    ):
        raise _ControlError("native_frame_invalid")
    body = {
        "kind": kind,
        "payload": payload,
        "sequence": sequence,
        "binding_digest": binding_digest,
        "manifest_digest": manifest_digest,
    }
    raw = _json(body)
    envelope = (
        _json({"body": body, "mac": hmac.new(key, _DOMAIN + raw, hashlib.sha256).hexdigest()})
        + b"\n"
    )
    if len(envelope) > _MAX_FRAME:
        raise _ControlError("native_frame_oversized")
    return envelope


def _decode_frame(
    raw: bytes, key: bytes, *, sequence: int, binding_digest: str, manifest_digest: str
) -> tuple[str, dict[str, Any]]:
    try:
        if type(raw) is not bytes or not raw.endswith(b"\n") or len(raw) > _MAX_FRAME:
            raise _ControlError("native_frame_invalid")
        value = json.loads(raw, object_pairs_hook=_strict_pairs)
        if type(value) is not dict or value.keys() != {"body", "mac"}:
            raise _ControlError("native_frame_invalid")
        body = value["body"]
        if (
            type(body) is not dict
            or body.keys() != {"kind", "payload", "sequence", "binding_digest", "manifest_digest"}
            or body["kind"] not in _KINDS
            or type(body["payload"]) is not dict
            or type(body["sequence"]) is not int
            or body["sequence"] != sequence
            or body["binding_digest"] != binding_digest
            or body["manifest_digest"] != manifest_digest
            or not receipt._hex(value["mac"])
            or not hmac.compare_digest(
                value["mac"], hmac.new(key, _DOMAIN + _json(body), hashlib.sha256).hexdigest()
            )
            or _json(value) + b"\n" != raw
        ):
            raise _ControlError("native_frame_invalid")
        return body["kind"], body["payload"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise _ControlError("native_frame_invalid") from exc


class _Channel:
    """Bounded framed pipes; a direction is never replayed or resynchronized."""

    def __init__(self, read_fd: int, write_fd: int, key: bytes, binding: str, manifest: str):
        self.read_fd, self.write_fd, self.key = read_fd, write_fd, key
        self.binding, self.manifest = binding, manifest
        self.received = self.sent = 0
        self.buffer = bytearray()
        self.eof = False
        self.bootstrap_fds: dict[str, Any] | None = None

    def send(self, kind: str, payload: dict[str, Any], deadline: int) -> None:
        raw = _frame(
            kind,
            payload,
            self.key,
            sequence=self.sent,
            binding_digest=self.binding,
            manifest_digest=self.manifest,
        )
        os.set_blocking(self.write_fd, False)
        while raw:
            now = _now()
            if now >= deadline:
                raise _ControlError("native_write_deadline")
            _, writable, _ = select.select(
                [], [self.write_fd], [], min(0.02, (deadline - now) / 1e9)
            )
            if writable:
                try:
                    count = os.write(self.write_fd, raw)
                except (BlockingIOError, InterruptedError):
                    continue
                if count <= 0:
                    raise _ControlError("native_write_unknown")
                raw = raw[count:]
        self.sent += 1

    def receive(self, *, wait: float = 0) -> tuple[str, dict[str, Any]] | None:
        if b"\n" not in self.buffer:
            readable, _, _ = select.select([self.read_fd], [], [], wait)
            if not readable:
                return None
            data = os.read(self.read_fd, 4096)
            if not data:
                self.eof = True
                raise _ControlError("native_channel_eof")
            self.buffer.extend(data)
            if len(self.buffer) > _MAX_FRAME:
                raise _ControlError("native_frame_oversized")
        if b"\n" not in self.buffer:
            return None
        line, rest = self.buffer.split(b"\n", 1)
        self.buffer = bytearray(rest)
        decoded = _decode_frame(
            bytes(line) + b"\n",
            self.key,
            sequence=self.received,
            binding_digest=self.binding,
            manifest_digest=self.manifest,
        )
        self.received += 1
        return decoded


def _now() -> int:
    observed = clock._sample_clock()
    if not clock._valid_clock(observed) or observed.continuous_ns is None:
        raise _ControlError("native_clock_unknown")
    return observed.continuous_ns


def _remaining(deadline: int, ceiling: float) -> float:
    """Fresh continuous remaining time; zero at/after expiry, never a floor."""
    return min(ceiling, max(0.0, (deadline - _now()) / 1e9))


class _OwnedFD:
    def __init__(self, fd: int):
        if type(fd) is not int or fd < 0:
            raise _ControlError("native_fd_invalid")
        self.fd = fd
        self.state: Literal["open", "attempted", "closed", "unknown"] = "open"
        self.failure: BaseException | None = None

    def close(self) -> bool:
        if self.state == "closed":
            return True
        if self.state != "open":
            return False
        self.state = "attempted"
        try:
            os.close(self.fd)
        except BaseException as failure:
            self.failure = failure
            self.state = "unknown"
            return False
        self.state = "closed"
        return True


class _ReportFDs:
    """Close-once owners retained by native custody or the original exception."""

    def __init__(self, custody: list[_OwnedFD] | None = None):
        self.owners: list[_OwnedFD] = []
        self.custody = custody
        self.transferred: _OwnedFD | None = None

    def __enter__(self) -> _ReportFDs:
        return self

    def open(self, path: str, flags: int, *, directory: _OwnedFD | None = None) -> _OwnedFD:
        owner = _OwnedFD(os.open(path, flags, dir_fd=directory.fd if directory else None))
        self.owners.append(owner)
        if self.custody is not None:
            self.custody.append(owner)
        return owner

    def close(self, owner: _OwnedFD) -> None:
        if not owner.close():
            raise owner.failure or _ControlError("native_report_fd_close_unknown")

    def transfer(self, owner: _OwnedFD) -> _OwnedFD:
        self.transferred = owner
        return owner

    def __exit__(self, exc_type: object, primary: BaseException | None, tb: object) -> None:
        # A transfer is valid only on a successful return. Every partial-failure
        # capability is accounted here or held in its actual close-once owner.
        for owner in reversed(self.owners):
            if owner is not self.transferred or primary is not None:
                owner.close()
        unknown = [owner for owner in self.owners if owner.state == "unknown"]
        if unknown:
            failure = (
                primary or unknown[0].failure or _ControlError("native_report_fd_close_unknown")
            )
            # Preserve a validation/open/close/interruption exception verbatim.
            # The retained owners prohibit a retry against a recycled integer.
            object.__setattr__(failure, "_report_fd_custody", self)
            if primary is None:
                if self.transferred is not None:
                    self.transferred.close()
                raise failure


def _fd_observation(role: str, named: dict[str, int]) -> dict[str, Any]:
    import fcntl

    result: dict[str, Any] = {"role": role, "handles": {}}
    for name, fd in named.items():
        try:
            flags = fcntl.fcntl(fd, fcntl.F_GETFD)
            info = os.fstat(fd)
            result["handles"][name] = {
                "open": True,
                "fd": fd,
                "device": info.st_dev,
                "inode": info.st_ino,
                "close_on_exec": bool(flags & fcntl.FD_CLOEXEC),
            }
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise _ControlError("native_fd_observation_unknown") from exc
            result["handles"][name] = {"open": False, "fd": fd}
    return result


def _close_key_copy(owned: _OwnedFD) -> dict[str, Any]:
    """Record this owner's actual key-copy close before its number can be reused."""
    before = _fd_observation("key_copy", {"key": owned.fd})["handles"]["key"]
    if before["open"] is not True or not owned.close():
        raise _ControlError("native_key_close_unknown")
    after = _fd_observation("key_copy_closed", {"key": owned.fd})["handles"]["key"]
    result = {"before": before, "after": after}
    _closed_key_copy(result)
    return result


def _closed_key_copy(value: object) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != {"before", "after"}:
        raise _ControlError("native_key_close_unknown")
    before, after = value["before"], value["after"]
    if (
        type(before) is not dict
        or before.keys() != {"open", "fd", "device", "inode", "close_on_exec"}
        or before["open"] is not True
        or type(before["close_on_exec"]) is not bool
        or any(type(before[k]) is not int or before[k] < 0 for k in ("fd", "device", "inode"))
        or type(after) is not dict
        or after.keys() != {"open", "fd"}
        or after["open"] is not False
        or type(after["fd"]) is not int
        or after["fd"] != before["fd"]
    ):
        raise _ControlError("native_key_close_unknown")
    return before


def _key_handoff_history(bootstrap: object) -> list[dict[str, Any]]:
    if type(bootstrap) is not dict:
        raise _ControlError("native_key_close_unknown")
    handles, parent = bootstrap.get("handles"), bootstrap.get("parent_key_closes")
    if type(handles) is not dict or type(parent) is not dict or parent.keys() != {"read", "write"}:
        raise _ControlError("native_key_close_unknown")
    receiver = {"before": bootstrap.get("closed_key_object"), "after": handles.get("key")}
    records = [parent["read"], parent["write"], receiver]
    originals = [_closed_key_copy(x) for x in records]
    if (
        originals[0]["fd"] != originals[2]["fd"]
        or originals[0]["fd"] == originals[1]["fd"]
        or any(originals[0][k] != originals[2][k] for k in ("device", "inode"))
    ):
        raise _ControlError("native_key_close_unknown")
    return records


def _worker_fd_observation(
    allowed: dict[str, int], protected: object, closed_keys: object, handoff: object
) -> dict[str, Any]:
    """Inspect finite known inherited slots, comparing objects not FD numbers.

    This deliberately makes no claim about arbitrary unknown descriptors or
    malicious same-process/same-UID interference.
    """
    if (
        allowed.keys() != {"config", "barrier", "evidence"}
        or any(type(fd) is not int or fd < 0 for fd in allowed.values())
        or len(set(allowed.values())) != 3
        or type(protected) is not list
        or not 1 <= len(protected) <= 32
        or type(closed_keys) is not list
        or len(closed_keys) != 6
    ):
        raise _ControlError("native_protected_fd_set_invalid")
    for item in protected:
        if (
            type(item) is not dict
            or item.keys() != {"open", "fd", "device", "inode", "close_on_exec"}
            or item["open"] is not True
            or type(item["close_on_exec"]) is not bool
            or any(type(item[k]) is not int or item[k] < 0 for k in ("fd", "device", "inode"))
        ):
            raise _ControlError("native_protected_fd_set_invalid")
    # Historical pipe objects can be reused after their last copy closes.
    # They contribute known slots, never erase an independently live object.
    closed_originals = [_closed_key_copy(x) for x in closed_keys]
    for group in (closed_originals[:3], closed_originals[3:]):
        if (
            group[0]["fd"] != group[2]["fd"]
            or group[0]["fd"] == group[1]["fd"]
            or any(group[0][k] != group[2][k] for k in ("device", "inode"))
        ):
            raise _ControlError("native_key_close_unknown")
    original_objects = {(x["device"], x["inode"]) for x in protected}
    slots = sorted({x["fd"] for x in [*protected, *closed_originals]})
    if len(slots) > 32:
        raise _ControlError("native_protected_fd_set_invalid")
    observed = _fd_observation("worker", allowed)
    if (
        type(handoff) is not dict
        or handoff.keys() != {"handles", "pass_fds", "close_fds"}
        or handoff["close_fds"] is not True
        or type(handoff["pass_fds"]) is not list
        or any(type(fd) is not int for fd in handoff["pass_fds"])
        or handoff["pass_fds"] != list(allowed.values())
        or type(handoff["handles"]) is not dict
        or handoff["handles"].keys() != allowed.keys()
    ):
        raise _ControlError("native_worker_fd_binding_unknown")
    for name, actual in observed["handles"].items():
        expected = handoff["handles"][name]
        if (
            type(expected) is not dict
            or expected.keys() != {"open", "fd", "device", "inode", "close_on_exec"}
            or expected["open"] is not True
            or type(expected["close_on_exec"]) is not bool
            or actual["open"] is not True
            or any(
                type(expected[k]) is not int or expected[k] < 0 or expected[k] != actual[k]
                for k in ("fd", "device", "inode")
            )
        ):
            raise _ControlError("native_worker_fd_binding_unknown")
    extra = _fd_observation(
        "worker_known_protected_slots", {f"slot_{i}": fd for i, fd in enumerate(slots)}
    )
    present = {
        (x["device"], x["inode"])
        for table in (observed, extra)
        for x in table["handles"].values()
        if x["open"]
    }
    observed["protected_slots"] = extra["handles"]
    bound_objects = {x["fd"]: (x["device"], x["inode"]) for x in observed["handles"].values()}
    observed["protected_handles_absent"] = not bool(present & original_objects) and all(
        not x["open"] or bound_objects.get(x["fd"]) == (x["device"], x["inode"])
        for x in extra["handles"].values()
    )
    return observed


def _known_process(pid: int) -> identity._ProcessIdentity:
    observed = identity._inspect_process(pid)
    if observed.status != "known" or observed.identity is None or observed.issues:
        raise _ControlError("native_identity_unknown")
    return observed.identity


def _matches(expected: identity._ProcessIdentity) -> bool:
    return (
        identity._same_process(
            identity._ProcessObservation("known", expected), identity._inspect_process(expected.pid)
        )
        == "match"
    )


@dataclass(frozen=True)
class _GroupObservation:
    status: Literal["known", "unknown"]
    members: tuple[identity._ProcessIdentity, ...] = ()
    issues: tuple[str, ...] = ()
    stage: Literal[
        "anchor_identity",
        "native_api",
        "member_list",
        "member_set",
        "member_identity",
        "identity_recheck",
        "member_scope",
        "complete",
        "signal",
        "signal_admission",
    ] = "anchor_identity"
    expected_pids: tuple[int, ...] = ()
    returned_pids: tuple[int, ...] = ()


def _observe_owned_group(
    *, anchor: identity._ProcessIdentity, expected: tuple[identity._ProcessIdentity, ...]
) -> _GroupObservation:
    # Local SDK libproc.h proc_listpids(uint32_t,uint32_t,void*,int) returns
    # bytes; sys/proc_info.h PROC_PGRP_ONLY=2. Query only this fresh owned PGID.
    stage: Literal[
        "anchor_identity",
        "native_api",
        "member_list",
        "member_set",
        "member_identity",
        "identity_recheck",
        "member_scope",
        "complete",
        "signal",
        "signal_admission",
    ] = "anchor_identity"
    expected_pids: tuple[int, ...] = ()
    pids: tuple[int, ...] = ()
    try:
        if not expected or len(expected) > 8 or anchor not in expected or not _matches(anchor):
            raise _ControlError("native_anchor_unknown")
        expected_pids = tuple(x.pid for x in expected)
        stage = "native_api"
        native = identity._load_native()
        native.proc_listpids.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        native.proc_listpids.restype = ctypes.c_int
        array = (ctypes.c_int * 9)()
        stage = "member_list"
        count = native.proc_listpids(2, anchor.pgid, array, ctypes.sizeof(array))
        if count <= 0 or count % ctypes.sizeof(ctypes.c_int) or count >= ctypes.sizeof(array):
            raise _ControlError("native_group_incomplete")
        pids = tuple(int(array[i]) for i in range(count // ctypes.sizeof(ctypes.c_int)))
        stage = "member_set"
        if len(set(pids)) != len(pids) or set(pids) != {x.pid for x in expected}:
            raise _ControlError("native_group_changed")
        stage = "member_identity"
        members = tuple(_known_process(pid) for pid in sorted(pids))
        stage = "identity_recheck"
        if any(not _matches(x) for x in expected) or not _matches(anchor):
            raise _ControlError("native_group_changed")
        stage = "member_scope"
        if any(
            x.pgid != anchor.pgid or x.sid != anchor.sid or x.boot_uuid != anchor.boot_uuid
            for x in members
        ):
            raise _ControlError("native_group_changed")
        return _GroupObservation(
            "known", members, stage="complete", expected_pids=expected_pids, returned_pids=pids
        )
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        code = str(exc) if type(exc) is _ControlError else "native_group_unavailable"
        if code not in {
            "native_anchor_unknown",
            "native_group_incomplete",
            "native_group_changed",
            "native_identity_unknown",
            "native_group_unavailable",
        }:
            code = "native_group_unavailable"
        return _GroupObservation(
            "unknown",
            issues=(code,),
            stage=stage,
            expected_pids=expected_pids,
            returned_pids=pids,
        )


def _signal_owned_group(
    anchor: identity._ProcessIdentity,
    expected: tuple[identity._ProcessIdentity, ...],
    signum: int,
    *,
    owner: _WatchdogState | None = None,
    custody_snapshot: tuple[tuple[str, subprocess.Popen[bytes]], ...] = (),
    registered: dict[str, identity._ProcessIdentity] | None = None,
) -> _GroupObservation:
    if signum not in (signal.SIGTERM, signal.SIGKILL):
        raise _ControlError("native_signal_invalid")
    policy = owner.policy if owner is not None else None
    timer = owner.timer if owner is not None else None
    observed = _observe_owned_group(anchor=anchor, expected=expected)
    if observed.status != "known":
        return observed
    try:
        if owner is None or registered is None:
            raise _ControlError("native_signal_admission_denied")
        owner.dispatch_signal(anchor, expected, signum, custody_snapshot, registered, policy, timer)
    except _ControlError:
        return _GroupObservation(
            "unknown",
            issues=("native_signal_admission_denied",),
            stage="signal_admission",
            expected_pids=observed.expected_pids,
            returned_pids=observed.returned_pids,
        )
    except OSError:
        return _GroupObservation(
            "unknown",
            issues=("native_signal_denied",),
            stage="signal",
            expected_pids=observed.expected_pids,
            returned_pids=observed.returned_pids,
        )
    return observed


def _timer_signal(
    policy: _NativePolicy, now: int, cancelled: bool, termed_at: int | None, killed: bool
) -> int | None:
    """Product timer decisions require no channel or controller availability."""
    if not cancelled and now < policy.deadline_continuous_ns:
        return None
    if termed_at is None:
        return signal.SIGTERM
    if not killed and now - termed_at >= policy.term_grace_ns:
        return signal.SIGKILL
    return None


def _validate_native_transition(transition: _NativeTransition) -> None:
    from stackos.repositories.base import ValidationError
    from stackos.repositories.runs import _NativeTransition

    keys = {
        "register": {"binding", "manifest", "wrapped_key", "identities"},
        "arm": {"arm_digest"},
        "release": {"arm_digest", "frame_digest"},
        "cancel": {"reason"},
        "observe_cleanup": {"observation_digest"},
        "hold": {"reason", "outcome_unknown"},
    }
    if (
        type(transition) is not _NativeTransition
        or type(transition.kind) is not str
        or transition.kind not in keys
        or type(transition.facts) is not dict
        or transition.facts.keys() != keys[transition.kind]
    ):
        raise ValidationError("invalid native transition")
    facts = transition.facts
    for name in ("arm_digest", "frame_digest", "observation_digest"):
        if name in facts and not receipt._hex(facts[name]):
            raise ValidationError("invalid native digest")
    if "reason" in facts and facts["reason"] not in {
        "owner_stop",
        "grant_lost",
        "controller_eof",
        "deadline",
        "custody_unknown",
        "key_unavailable",
        "setup_failed",
        "identity_unknown",
        "ack_unknown",
    }:
        raise ValidationError("invalid native safety reason")
    if "outcome_unknown" in facts and type(facts["outcome_unknown"]) is not bool:
        raise ValidationError("invalid native uncertainty")
    if transition.kind == "register":
        box = facts["wrapped_key"]
        identities = facts["identities"]
        try:
            binding = receipt._Binding(**facts["binding"])
            if not receipt._valid_binding(binding):
                raise ValueError
            if type(box) is not dict or box.keys() != {"ciphertext", "nonce"}:
                raise ValueError
            if (
                any(type(box[k]) is not str for k in box)
                or len(bytes.fromhex(box["ciphertext"])) != 48
                or len(bytes.fromhex(box["nonce"])) != 12
            ):
                raise ValueError
            if type(identities) is not dict or identities.keys() != {
                "watchdog",
                "anchor",
                "worker",
            }:
                raise ValueError
            processes = {k: identity._ProcessIdentity(**v) for k, v in identities.items()}
            if not all(identity._valid_identity(x) for x in processes.values()):
                raise ValueError
            w, a, t = (processes[k] for k in ("watchdog", "anchor", "worker"))
            if not (
                w.pid == w.pgid == w.sid == a.sid == t.sid
                and a.pid == a.pgid == t.pgid
                and len({w.pid, a.pid, t.pid}) == 3
                and a.parent_pid == w.pid == t.parent_pid
            ):
                raise ValueError
            if any(x.boot_uuid != binding.boot_uuid for x in processes.values()):
                raise ValueError
            if type(facts["manifest"]) is not dict:
                raise ValueError
        except (TypeError, ValueError, KeyError):
            raise ValidationError("invalid native registration") from None


@dataclass(frozen=True)
class _NativeControlOut:
    state: Literal["cleaned", "held", "unknown"]
    cursor: _ControlCursor
    arm_receipt_digest: str | None
    release_sent: bool
    cleanup_observations: tuple[identity._ProcessObservation, ...] = ()
    issues: tuple[str, ...] = ()
    certified: Literal[False] = False
    evidence: tuple[dict[str, Any], ...] = ()
    cleanup_known: bool = False
    custody: _NativeCustody | None = field(default=None, repr=False, compare=False)
    terminal_owner: _TerminalOwner | None = field(default=None, repr=False, compare=False)


class _NativeCustody:
    """One caller's exact child/FD ownership; no signal/forward authority.

    Keep this context alive until exact waitpid accounting. Owning teardown
    may wait after product expiry; that never claims timely product cleanup.
    """

    def __init__(self, admission: _NativeAdmission):
        self.admission = admission
        self.process: subprocess.Popen[bytes] | None = None
        self.identity: identity._ProcessIdentity | None = None
        self.owned_fds: list[_OwnedFD] = []
        self.slot: receipt._AllocatedSlot | None = None
        self.binding_digest: str | None = None
        self.active = False
        self.started = False
        self.accounted = False
        self.unresolved: list[str] = []
        self._teardown_interrupt: BaseException | None = None
        self.ever_spawn_invoked = False
        self.terminal_owner: _TerminalOwner | None = None
        self.terminal_slot: receipt._AllocatedSlot | None = None

    def __enter__(self) -> _NativeCustody:
        if self.active or self.started or self.accounted:
            raise _ControlError("native_custody_cannot_be_reused")
        self.active = True
        return self

    def begin(self, admission: _NativeAdmission) -> None:
        if not self.active or self.started or self.admission is not admission:
            raise _ControlError("native_custody_not_owned")
        self.started = True

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        if self.process is not None or not self.started:
            raise _ControlError("native_process_already_owned")
        self.process = process

    def _retain_spawn(self, process: subprocess.Popen[bytes]) -> None:
        """Fallback publication, independent of the fallible ordinary attach."""
        if self.process is None:
            self.process = process
            self.unresolved.append("native_spawn_attachment_unknown")
        elif self.process is not process:
            # Only one spawn is allowed by this context. Keep the new local
            # owner alive rather than discard it on a contradictory attachment.
            _account_owned(process.wait, lambda failure: self._hold_accounting(process, failure))
            raise _ControlError("native_spawn_ownership_conflict")

    def _hold_accounting(
        self, process: subprocess.Popen[bytes], failure: BaseException
    ) -> NoReturn:
        self.unresolved.append("native_wait_accounting_held")
        _park_owned(
            {
                "schema_version": "stackos.native-custody-held.v1",
                "owner": "controller",
                "operation": "waitpid",
                "pid": process.pid,
                "code": "native_wait_accounting_held",
                "errno": failure.errno if isinstance(failure, OSError) else None,
                "product_success": False,
                "handle_retained": True,
            }
        )

    def spawn(self, *args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        if self.process is not None or not self.started or not self.active:
            raise _ControlError("native_process_already_owned")
        process: subprocess.Popen[bytes] | None = None
        try:
            self.ever_spawn_invoked = True  # unknown even if Popen raises before returning
            process = subprocess.Popen(*args, **kwargs)
            self.attach(process)
            return process
        finally:
            # Cover attachment failure and the local successful return boundary.
            # No subsequent helper/caller must be the sole holder of this child.
            if process is not None:
                self._retain_spawn(process)

    def snapshot(self) -> dict[str, Any]:
        return {
            "binding_digest": self.binding_digest,
            "pid": self.process.pid if self.process is not None else None,
            "identity": asdict(self.identity) if self.identity is not None else None,
            "direct_child_reaped": self.process is None or self.process.poll() is not None,
            "fd_states": [{"fd": x.fd, "state": x.state} for x in self.owned_fds],
            "unresolved": list(self.unresolved),
            "accounted": self.accounted,
            "terminal_close_state": self.terminal_slot.close_state
            if self.terminal_slot is not None
            else None,
        }

    def close_accounted(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return False
        okay = True
        if self.slot is not None and self.slot._close_once() is not None:
            okay = False
        if self.terminal_slot is not None and self.terminal_slot._close_once() is not None:
            okay = False
        if self.terminal_owner is not None:
            if self.terminal_owner.slot._close_once() is not None:
                okay = False
            self.terminal_owner.close_verification()
        for fd in self.owned_fds:
            if not fd.close():
                okay = False
        self.accounted = okay
        return okay

    def wait_accounted(self, *, propagate_interrupt: bool = True) -> bool:
        if self.process is not None:
            process = self.process
            interrupted = _account_owned(
                process.wait, lambda failure: self._hold_accounting(process, failure)
            )
            if interrupted is not None:
                if self._teardown_interrupt is None:
                    self._teardown_interrupt = interrupted
                if "native_teardown_interrupted" not in self.unresolved:
                    self.unresolved.append("native_teardown_interrupted")
        okay = self.close_accounted()
        if propagate_interrupt and self._teardown_interrupt is not None:
            raise self._teardown_interrupt
        return okay

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.wait_accounted(propagate_interrupt=exc is None)
        finally:
            if self.process is None or self.process.returncode is not None:
                self.active = False


def _open_arm_directory(state_dir: Path, project: int, attempt: int) -> tuple[int, Path]:
    """Create owned private storage; never adopt an existing attempt/slot."""
    parent = state_dir / "controlled-process"
    for folder in (state_dir, parent):
        with suppress(FileExistsError):
            folder.mkdir(mode=0o700)
        info = folder.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise _ControlError("native_storage_unsafe")
    attempt_dir = parent / f"{project}-{attempt}"
    attempt_dir.mkdir(mode=0o700)
    leaf = attempt_dir / "arm"
    leaf.mkdir(mode=0o700)
    fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    return fd, attempt_dir


def _prepare_report_directory(
    state: Path, directory: Path, *, custody: list[_OwnedFD] | None = None
) -> _OwnedFD:
    """Exclusive intent workspace; caller retains its returned FD in custody."""
    if directory.parent.parent != state or directory.parent.name != "controlled-reports":
        raise _ControlError("native_report_storage_invalid")
    with _ReportFDs(custody) as owned:
        parent = owned.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for part in state.parts[1:]:
            child = owned.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, directory=parent)
            owned.close(parent)
            parent = child
        with suppress(FileExistsError):
            os.mkdir("controlled-reports", mode=0o700, dir_fd=parent.fd)
        child = owned.open(
            "controlled-reports", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, directory=parent
        )
        owned.close(parent)
        parent = child
        info = os.fstat(parent.fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise _ControlError("native_report_storage_unsafe")
        os.mkdir(directory.name, mode=0o700, dir_fd=parent.fd)
        child = owned.open(
            directory.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            directory=parent,
        )
        owned.close(parent)
        return owned.transfer(child)


@dataclass(frozen=True)
class _TerminalOriginal:
    binding: receipt._Binding
    policy: _NativePolicy
    manifest: dict[str, Any] = field(repr=False)
    wrapped_key: dict[str, str] = field(repr=False)
    admission: dict[str, Any] = field(repr=False)
    transfer_close: dict[str, Any] | None = None

    @property
    def producer_digest(self) -> str:
        return _digest(
            {
                "binding": asdict(self.binding),
                "schema": receipt._TERMINAL_SCHEMA,
                "policy": self.policy._decision,
                "manifest": self.manifest,
            }
        )

    def record(self) -> dict[str, Any]:
        return {
            "schema_version": "stackos.native-recovery.v1",
            "binding": asdict(self.binding),
            "policy": self.policy._decision,
            "manifest": self.manifest,
            "wrapped_key": self.wrapped_key,
            "admission": self.admission,
            "producer_digest": self.producer_digest,
            "transfer_close": self.transfer_close,
        }


# Darwin SDK sys/event.h EV_RECEIPT. CPython 3.12 omits KQ_EV_RECEIPT.
_DARWIN_EV_RECEIPT = 0x0040


class _ExactExitWatch:
    """One pre-registered original W instance; NOTE_EXIT never means waitpid."""

    def __init__(self, expected: identity._ProcessIdentity, held_output: Path):
        assert _matches(expected)
        self.expected = expected
        self.queue = select.kqueue()
        self.exited = False
        self.registered = False
        self.held_output = held_output
        self.held: dict[str, Any] | None = None
        self.failure: BaseException | None = None
        try:
            change = select.kevent(
                expected.pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD
                | select.KQ_EV_ENABLE
                | select.KQ_EV_ONESHOT
                | _DARWIN_EV_RECEIPT,
                fflags=select.KQ_NOTE_EXIT,
            )
            result = self.queue.control([change], 1, 0)
            assert len(result) == 1 and result[0].ident == expected.pid
            assert result[0].flags & select.KQ_EV_ERROR and result[0].data == 0
            assert _matches(expected)
            self.registered = True
        except BaseException:
            self.queue.close()
            raise

    def observe(self, timeout: float | None) -> bool:
        if self.held is not None:
            raise _ControlError("native_exit_monitor_held")
        if self.exited:
            return True
        try:
            values = self.queue.control([], 1, timeout)
            if not values:
                return False
            event = values[0]
            assert event.ident == self.expected.pid and event.filter == select.KQ_FILTER_PROC
            assert not (event.flags & select.KQ_EV_ERROR)
            assert event.fflags & select.KQ_NOTE_EXIT
            self.exited = True
            return True
        except OSError as failure:
            if failure.errno != errno.EINTR:
                self._latch_hold("native_exit_monitor_os_error", failure)
            raise
        except BaseException as failure:
            self._latch_hold("native_exit_monitor_event_unknown", failure)
            raise

    def _latch_hold(self, code: str, failure: BaseException) -> None:
        if self.held is None:
            self.failure = failure
            self.held = {
                "code": code,
                "pid": self.expected.pid,
                "errno": failure.errno if isinstance(failure, OSError) else None,
                "note_exit_seen": self.exited,
                "product_success": False,
                "monitor_retained": True,
            }

    def _park_held(self) -> NoReturn:
        if self.held is None:
            raise _ControlError("native_exit_monitor_held")

        def publish(safe: dict[str, Any]) -> None:
            self.held_output.write_text(json.dumps(safe, sort_keys=True, indent=2) + "\n")

        _park_owned(self.held, publish)

    def finalize(self) -> None:
        interrupted: BaseException | None = None
        while not self.exited:
            if self.held is not None:
                self._park_held()
            try:
                self.observe(None)
            except OSError as failure:
                if failure.errno != errno.EINTR:
                    continue  # next iteration parks the latched permanent hold
                if interrupted is None:
                    interrupted = failure
            except BaseException as failure:
                self._latch_hold("native_exit_monitor_event_unknown", failure)
        self.queue.close()
        if interrupted is not None:
            raise interrupted


@dataclass(frozen=True)
class _OriginalExitWitness:
    identity: identity._ProcessIdentity
    returncode: int | None
    custody: _NativeCustody | None = field(default=None, repr=False, compare=False)
    monitor: _ExactExitWatch | None = field(default=None, repr=False, compare=False)

    def validate(self, original: _TerminalOriginal) -> None:
        owned = self.custody
        if self.monitor is not None:
            monitor = self.monitor
            if (
                owned is not None
                or self.returncode is not None
                or type(monitor) is not _ExactExitWatch
                or not monitor.registered
                or not monitor.exited
                or monitor.held is not None
                or monitor.expected != self.identity
                or self.identity.boot_uuid != original.binding.boot_uuid
            ):
                raise _ControlError("terminal_custody_unknown")
            return
        if (
            type(owned) is not _NativeCustody
            or owned is None
            or owned.process is None
            or owned.binding_digest != receipt._name(original.binding)[:-8]
            or owned.identity != self.identity
            or owned.process.pid != self.identity.pid
            or owned.process.returncode is None
            or owned.process.returncode != self.returncode
        ):
            raise _ControlError("terminal_custody_unknown")


@dataclass
class _TerminalOwner:
    session: Session = field(repr=False)
    original: _TerminalOriginal
    custody: _NativeCustody = field(repr=False)
    slot: receipt._AllocatedSlot = field(repr=False)
    transfer_directory: _OwnedFD = field(repr=False)
    directory: _OwnedFD = field(repr=False)
    read: _OwnedFD = field(repr=False)
    key: bytes = field(repr=False)
    authenticated: receipt._KeyAuthenticatedTerminalReceipt | None = field(default=None, repr=False)
    final_f9: receipt.F9 | None = None
    read_complete_ns: int | None = None
    not_spawned: bool = False
    report_digest: str | None = None

    def finish_read(self) -> None:
        if self.authenticated is not None:
            return
        if self.directory.state != "open" or self.read.state != "open":
            raise _ControlError("terminal_custody_unknown")
        binding = self.original.binding
        if self.custody.ever_spawn_invoked:
            if (
                self.custody.process is None
                or self.custody.process.returncode is None
                or self.custody.identity is None
            ):
                raise _ControlError("terminal_custody_unknown")
            _OriginalExitWitness(
                self.custody.identity, self.custody.process.returncode, self.custody
            ).validate(self.original)
        elif not self.not_spawned:
            raise _ControlError("terminal_custody_unknown")
        result = receipt._read_opened(
            self.read.fd,
            self.directory.fd,
            self.slot.name,
            self.slot.dalloc,
            receipt._entry(self.directory.fd, self.slot.name),
            binding,
            self.key,
            receipt_kind="terminal",
            retained=True,
        )
        if isinstance(result, receipt._ReceiptHold) or not isinstance(
            result[0], receipt._KeyAuthenticatedTerminalReceipt
        ):
            raise _ControlError("terminal_file_unknown")
        authenticated, vector = result
        if not isinstance(authenticated, receipt._KeyAuthenticatedTerminalReceipt):
            raise _ControlError("terminal_file_unknown")
        if len(vector) != 9:
            raise _ControlError("terminal_file_unknown")
        self.authenticated = authenticated
        self.final_f9 = (
            vector[0],
            vector[1],
            vector[2],
            vector[3],
            vector[4],
            vector[5],
            vector[6],
            vector[7],
            vector[8],
        )
        self.read_complete_ns = self.original.policy.check(clock._sample_clock())

    def close_verification(self) -> None:
        # Each exact owned integer is spent before its close attempt. Failed
        # closes cannot be repaired by a new/recycled descriptor.
        self.read.close()
        self.directory.close()


@dataclass
class _RecoveredTerminalRead:
    """New read-only custody; never an allocated/transferred writer context."""

    session: Session = field(repr=False)
    original: _TerminalOriginal
    key: bytes = field(repr=False)
    exit_witness: _OriginalExitWitness = field(repr=False)
    directory: _OwnedFD = field(repr=False)
    read: _OwnedFD = field(repr=False)
    report: _OwnedFD = field(repr=False)
    dalloc: receipt.D6
    fempty: receipt.F9
    authenticated: receipt._KeyAuthenticatedTerminalReceipt = field(repr=False)
    final_f9: receipt.F9
    read_complete_ns: int
    report_digest: str | None
    report_known: bool

    def validate(self) -> None:
        from stackos.db.models import Run

        self.exit_witness.validate(self.original)
        row = self.session.get(Run, self.original.binding.attempt_run_id, populate_existing=True)
        metadata = row.metadata_json if row is not None else {}
        control = (metadata or {}).get("native_control", {})
        registered = control.get("registration", {}).get("identities", {}).get("watchdog")
        close = self.original.transfer_close
        if (
            self.directory.state != "closed"
            or self.read.state != "closed"
            or self.report.state != "closed"
            or (metadata or {}).get("native_recovery_private") != self.original.record()
            or registered != asdict(self.exit_witness.identity)
            or close
            != {
                "schema_version": "stackos.native-terminal-transfer.v1",
                "binding_digest": receipt._name(self.original.binding)[:-8],
                "dalloc": list(self.dalloc),
                "fempty": list(self.fempty),
                "d_writer_closed": True,
                "d_directory_closed": True,
            }
            or receipt._authenticate_terminal_receipt(
                self.authenticated.raw, self.original.binding, self.key
            )
            != self.authenticated
        ):
            raise _ControlError("terminal_custody_unknown")


@dataclass(frozen=True)
class _TerminalReadVerification:
    owner: _TerminalOwner | _RecoveredTerminalRead = field(repr=False)
    authenticated: receipt._KeyAuthenticatedTerminalReceipt = field(repr=False)
    dalloc: receipt.D6
    fempty: receipt.F9
    final_f9: receipt.F9
    producer_digest: str

    def validate(self) -> None:
        owner = self.owner
        if type(owner) is _RecoveredTerminalRead:
            owner.validate()
            if (
                self.authenticated is not owner.authenticated
                or self.dalloc != owner.dalloc
                or self.fempty != owner.fempty
                or self.final_f9 != owner.final_f9
                or self.producer_digest != owner.original.producer_digest
            ):
                raise _ControlError("terminal_file_unknown")
            return
        if (
            type(owner) is not _TerminalOwner
            or self.authenticated is not owner.authenticated
            or self.dalloc != owner.slot.dalloc
            or self.fempty != owner.slot.fempty
            or self.final_f9 != owner.final_f9
            or self.producer_digest != owner.original.producer_digest
            or owner.read.state != "closed"
            or owner.directory.state != "closed"
            or owner.transfer_directory.state != "closed"
            or owner.slot.close_state != "CLOSED"
            or not owner.custody.accounted
        ):
            raise _ControlError("terminal_custody_unknown")
        from stackos.db.models import Run

        row = owner.session.get(Run, owner.original.binding.attempt_run_id, populate_existing=True)
        if (
            row is None
            or (row.metadata_json or {}).get("native_recovery_private") != owner.original.record()
        ):
            raise _ControlError("terminal_control_unknown")
        if (
            receipt._authenticate_terminal_receipt(
                self.authenticated.raw, owner.original.binding, owner.key
            )
            != self.authenticated
        ):
            raise _ControlError("terminal_key_unknown")
        if owner.custody.ever_spawn_invoked:
            if (
                owner.custody.process is None
                or owner.custody.identity is None
                or owner.custody.process.returncode is None
            ):
                raise _ControlError("terminal_custody_unknown")
            _OriginalExitWitness(
                owner.custody.identity, owner.custody.process.returncode, owner.custody
            ).validate(owner.original)
        elif not owner.not_spawned or self.authenticated.fact.kind != "not_spawned":
            raise _ControlError("terminal_custody_unknown")


@dataclass(frozen=True)
class _TerminalHold:
    issues: tuple[str, ...]


@dataclass(frozen=True)
class _TerminalReconcileOut:
    state: Literal["recorded", "replayed", "held"]
    attempt: RunOut
    facts_revision: int
    certificate: WorkItemOwnershipOut | None
    issues: tuple[str, ...]
    # Caller must retain/inspect these actual close-once owners on a hold.
    # This is new read custody, never original writer/exit authority.
    held_reads: tuple[_OwnedFD, ...] = field(default=(), repr=False, compare=False)


@dataclass
class _VerifiedQuiescence:
    snapshot: _NativeSetSnapshot
    owners: tuple[_TerminalOwner | _RecoveredTerminalRead, ...] = field(repr=False)
    observation: clock._ClockObservation
    _committed_descriptor: dict[str, Any] | None = field(default=None, repr=False)

    def validate(self) -> None:
        from stackos.db.models import Run
        from stackos.repositories.runs import _parse_terminal_facts, _parse_terminal_slot

        if not clock._valid_clock(self.observation) or self.snapshot.effect.status not in {
            "known_no_effect",
            "known_settled",
        }:
            raise _ControlError("terminal_effect_unknown")
        if tuple(sorted(owner.original.binding.attempt_run_id for owner in self.owners)) != tuple(
            item.id for item in self.snapshot.attempts
        ):
            raise _ControlError("terminal_registered_set_unknown")
        for owner in self.owners:
            verified = _terminal_verification(owner)
            verified.validate()
            fact = verified.authenticated.fact
            row = owner.session.get(
                Run, owner.original.binding.attempt_run_id, populate_existing=True
            )
            metadata = row.metadata_json if row is not None else {}
            terminal_slot = _parse_terminal_slot((metadata or {}).get("native_terminal_slot"))
            facts = _parse_terminal_facts(
                (metadata or {}).get("native_terminal_facts"), owner.original.binding
            )
            if (
                terminal_slot["phase"] != "COMMITTED"
                or terminal_slot["receipt_digest"] != verified.authenticated.digest
                or terminal_slot["fact_sequence"] != 1
                or facts["revision"] != 1
                or len(facts["entries"]) != 1
                or facts["entries"][0]["receipt_digest"] != verified.authenticated.digest
                or facts["entries"][0]["fact_payload"]
                != receipt._terminal_payload(owner.original.binding, fact)["fact"]
            ):
                raise _ControlError("terminal_control_unknown")
            if isinstance(fact, receipt._TerminalFact):
                if (
                    not fact.cleanup_candidate
                    or fact.safety_blocked
                    or not all(fact.arm_closed.values())
                    or fact.registration_digest is None
                    or fact.control_revision == 0
                    or fact.completed_continuous_ns is None
                    or fact.issues
                    or owner.read_complete_ns is None
                    or owner.read_complete_ns
                    >= owner.original.policy.cleanup_deadline_continuous_ns
                    or (
                        isinstance(owner, _TerminalOwner)
                        and (owner.custody.process is None or owner.custody.process.returncode != 0)
                    )
                    or (isinstance(owner, _RecoveredTerminalRead) and not owner.report_known)
                    or not receipt._terminal_clock(fact.clock, owner.original.binding, known=True)
                ):
                    raise _ControlError("terminal_cleanup_unknown")
                control = (metadata or {}).get("native_control", {})
                registration = control.get("registration", {})
                if (
                    fact.registered
                    != {
                        name: identity._ProcessIdentity(**value)
                        for name, value in registration.get("identities", {}).items()
                    }
                    or fact.registration_digest != _digest(registration)
                    or fact.control_revision > control.get("control_revision", 0)
                    or fact.report_digest != owner.report_digest
                    or (metadata or {}).get("receipt_slot", {}).get("phase") != "COMMITTED"
                ):
                    raise _ControlError("terminal_control_unknown")
            if self.observation.boot_uuid != owner.original.binding.boot_uuid:
                raise _ControlError("terminal_clock_unknown")

    def descriptor(self, evidence_ref: str) -> dict[str, Any]:
        snapshot = self.snapshot
        descriptor = dict(
            schema_version="stackos.native-quiescence.v1",
            owner_id=snapshot.owner_basis["id"],
            project_id=snapshot.project_id,
            canonical_item_id=snapshot.canonical_item_id,
            owner_ref=snapshot.owner_ref,
            epoch=snapshot.epoch,
            scope_digest=snapshot.owner_basis["scope_digest"],
            verified_owner_revision=snapshot.revision,
            certified_owner_revision=snapshot.revision + 1,
            set_digest=snapshot.digest,
            attempt_snapshots=[asdict(item) for item in snapshot.attempts],
            effect_snapshot=asdict(snapshot.effect),
            receipt_digests=[
                owner.authenticated.digest
                for owner in self.owners
                if owner.authenticated is not None
            ],
            manifest_digests=[item.binding.manifest_digest for item in snapshot.attempts],
            verification_clock=asdict(self.observation),
            evidence_ref=evidence_ref,
        )
        if self._committed_descriptor is not None and self._committed_descriptor != descriptor:
            raise _ControlError("terminal_certificate_context_spent")
        return descriptor

    def record_commit(self, descriptor: dict[str, Any]) -> None:
        self._committed_descriptor = descriptor


def _terminal_verification(
    owner: _TerminalOwner | _RecoveredTerminalRead,
) -> _TerminalReadVerification:
    if owner.authenticated is None or owner.final_f9 is None:
        raise _ControlError("terminal_file_unknown")
    dalloc, fempty = (
        (owner.dalloc, owner.fempty)
        if isinstance(owner, _RecoveredTerminalRead)
        else (owner.slot.dalloc, owner.slot.fempty)
    )
    return _TerminalReadVerification(
        owner,
        owner.authenticated,
        dalloc,
        fempty,
        owner.final_f9,
        owner.original.producer_digest,
    )


def _allocate_terminal_owner(
    session: Session,
    *,
    binding: receipt._Binding,
    policy: _NativePolicy,
    manifest: dict[str, Any],
    wrapped_key: dict[str, str],
    key: bytes,
    custody: _NativeCustody,
    attempt_path: Path,
) -> _TerminalOwner:
    leaf = attempt_path / "terminal"
    leaf.mkdir(mode=0o700)
    owned = custody.owned_fds
    directory = _OwnedFD(os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC))
    owned.append(directory)
    transfer = _OwnedFD(
        os.open(
            ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory.fd
        )
    )
    owned.append(transfer)
    if receipt._directory(directory.fd) != receipt._directory(transfer.fd):
        raise _ControlError("terminal_custody_unknown")
    slot = receipt._allocate_terminal_slot(transfer.fd, binding)
    if isinstance(slot, receipt._ReceiptHold):
        raise _ControlError("terminal_file_unknown")
    # Retain the newly held writer immediately, including later open failure.
    custody.terminal_slot = slot
    try:
        read = _OwnedFD(
            os.open(
                slot.name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory.fd,
            )
        )
        owned.append(read)
        if (
            len({directory.fd, transfer.fd, slot.fd, read.fd}) != 4
            or receipt._file_fingerprint(os.fstat(read.fd)) != slot.fempty
            or not receipt._safe_fd(read.fd, os.O_RDONLY)
            or not receipt._same_directory(directory.fd, slot.dalloc)
        ):
            raise _ControlError("terminal_custody_unknown")
        fence = custody.admission.original.fence
        original = _TerminalOriginal(
            binding,
            policy,
            manifest,
            wrapped_key,
            {
                name: value.isoformat() if isinstance(value, datetime) else value
                for name, value in asdict(fence).items()
            }
            | {
                "storage_context": {
                    "state_dir": custody.admission.storage[0],
                    "seed_path": custody.admission.storage[1],
                    "seed_digest": _digest(custody.admission.seed.hex()),
                }
            },
        )
        owner = _TerminalOwner(session, original, custody, slot, transfer, directory, read, key)
        custody.terminal_owner = owner
        return owner
    except BaseException:
        # The actual close-once owner remains in custody even if this attempt
        # fails/interrupts. A secondary close failure cannot mask the original
        # exception or authorize retry against a recycled descriptor number.
        with suppress(BaseException):
            slot._close_once()
        raise


def _reconcile_native_terminal(
    session: Session,
    *,
    original: _TerminalOriginal,
    custody: _NativeCustody | None,
    exit_witness: _OriginalExitWitness | None,
    evidence_ref: str,
    owner: _TerminalOwner | None = None,
) -> _TerminalReconcileOut:
    from stackos.repositories.runs import RunOut, RunRepository

    repo = RunRepository(session)
    attempt = repo._terminal_attempt(binding=original.binding)
    try:
        if (
            owner is None
            or owner.original != original
            or owner.session is not session
            or owner.custody is not custody
            or (attempt.metadata_json or {}).get("native_recovery_private") != original.record()
        ):
            raise _ControlError("terminal_custody_unknown")
        if exit_witness is not None:
            exit_witness.validate(original)
        slot = (attempt.metadata_json or {}).get("native_terminal_slot", {})
        result = repo._commit_terminal_receipt_slot(
            verified=_terminal_verification(owner),
            expected_slot_revision=slot["revision"],
            evidence_ref=evidence_ref,
        )
        return _TerminalReconcileOut(
            "recorded" if result.applied else "replayed",
            result.attempt,
            result.facts_revision,
            None,
            (),
        )
    except Exception:
        return _TerminalReconcileOut(
            "held", RunOut.model_validate(attempt), 0, None, ("terminal_custody_unknown",)
        )


def _verify_native_quiescence(
    session: Session,
    *,
    snapshot: _NativeSetSnapshot,
    owners: tuple[_TerminalOwner | _RecoveredTerminalRead, ...],
) -> _VerifiedQuiescence | _TerminalHold:
    from stackos.repositories.runs import RunRepository

    try:
        if any(owner.session is not session for owner in owners):
            raise _ControlError("terminal_custody_unknown")
        current = RunRepository(session)._native_quiescence_snapshot(
            project_id=snapshot.project_id,
            canonical_item_id=snapshot.canonical_item_id,
            owner_ref=snapshot.owner_ref,
            expected_epoch=snapshot.epoch,
            expected_revision=snapshot.revision,
        )
        if current != snapshot:
            raise _ControlError("terminal_registered_set_unknown")
        result = _VerifiedQuiescence(snapshot, owners, clock._sample_clock())
        result.validate()
        return result
    except Exception:
        return _TerminalHold(("terminal_cleanup_unknown",))


def _record_not_spawned_terminal(owner: _TerminalOwner) -> _TerminalReconcileOut:
    custody = owner.custody
    if (
        not custody.started
        or custody.ever_spawn_invoked
        or custody.process is not None
        or custody.admission.policy != owner.original.policy
    ):
        raise _ControlError("terminal_custody_unknown")
    fact = receipt._NotSpawnedTerminalFact(
        "not_spawned",
        "host-controller",
        1,
        clock._sample_clock(),
        _digest(owner.original.policy._decision),
        False,
    )
    raw = receipt._seal_terminal_receipt(owner.original.binding, fact, owner.key)
    if isinstance(raw, receipt._ReceiptHold):
        raise _ControlError("terminal_clock_unknown")
    filled = receipt._fill_terminal_slot(
        owner.transfer_directory.fd, owner.slot, owner.original.binding, owner.key, raw
    )
    if isinstance(filled, receipt._ReceiptHold):
        raise _ControlError("terminal_file_unknown")
    if owner.slot._close_once() is not None or not owner.transfer_directory.close():
        raise _ControlError("terminal_custody_unknown")
    owner.not_spawned = True
    owner.finish_read()
    owner.close_verification()
    custody.close_accounted()
    return _reconcile_native_terminal(
        owner.session,
        original=owner.original,
        custody=custody,
        exit_witness=None,
        evidence_ref="native:not-spawned",
        owner=owner,
    )


def _recover_native_terminal(
    session: Session,
    *,
    project_id: int,
    canonical_item_id: str,
    attempt_run_id: int,
    evidence_ref: str,
    exit_witness: _OriginalExitWitness | None = None,
) -> _TerminalReconcileOut:
    from stackos.actions.controlled_command import _controlled_host_settings
    from stackos.crypto.aes_gcm import decrypt
    from stackos.db.models import Run
    from stackos.operations.actions.controlled import _native_storage
    from stackos.repositories.base import ConflictError
    from stackos.repositories.runs import RunOut, RunRepository, _parse_terminal_slot

    repo = RunRepository(session)
    repo._require_native_clean_session()
    attempt = session.get(Run, attempt_run_id, populate_existing=True)
    if attempt is None or attempt.project_id != project_id:
        raise ConflictError("original terminal recovery snapshot unavailable")
    metadata = attempt.metadata_json or {}
    recovery = metadata.get("native_recovery_private")
    if (
        type(recovery) is not dict
        or recovery.get("binding", {}).get("canonical_item_id") != canonical_item_id
    ):
        return _TerminalReconcileOut(
            "held", RunOut.model_validate(attempt), 0, None, ("terminal_key_unknown",)
        )
    if type(exit_witness) is not _OriginalExitWitness:
        return _TerminalReconcileOut(
            "held", RunOut.model_validate(attempt), 0, None, ("terminal_custody_unknown",)
        )
    directory: _OwnedFD | None = None
    read_owner: _OwnedFD | None = None
    opened: list[_OwnedFD] = []
    reader: _RecoveredTerminalRead | None = None
    issues: tuple[str, ...] = ("terminal_custody_unknown",)
    try:
        binding = receipt._Binding(**recovery["binding"])
        repo._terminal_attempt(binding=binding)
        if type(recovery.get("wrapped_key")) is not dict or recovery["wrapped_key"].keys() != {
            "ciphertext",
            "nonce",
        }:
            raise _ControlError("terminal_key_unknown")
        policy = _NativePolicy.from_decision(
            recovery["policy"],
            command_digest=_digest(recovery["manifest"]["command"]),
            runtime_digest=binding.manifest_digest,
        )
        if policy.boot_uuid != binding.boot_uuid:
            raise _ControlError("terminal_clock_unknown")
        original = _TerminalOriginal(
            binding,
            policy,
            recovery["manifest"],
            recovery["wrapped_key"],
            recovery["admission"],
            recovery.get("transfer_close"),
        )
        if original.record() != recovery or _digest(original.manifest) != binding.manifest_digest:
            raise _ControlError("terminal_control_unknown")
        exit_witness.validate(original)
        settings = _controlled_host_settings(session)
        if settings is None:
            raise _ControlError("terminal_key_unknown")
        try:
            storage, seed, _storage_digest = _native_storage(settings)
        except Exception:
            raise _ControlError("terminal_key_unknown") from None
        if original.admission.get("storage_context") != {
            "state_dir": storage[0],
            "seed_path": storage[1],
            "seed_digest": _digest(seed.hex()),
        }:
            raise _ControlError("terminal_key_unknown")
        registered = (
            metadata.get("native_control", {})
            .get("registration", {})
            .get("identities", {})
            .get("watchdog")
        )
        if registered != asdict(exit_witness.identity):
            raise _ControlError("terminal_custody_unknown")
        slot = _parse_terminal_slot(metadata.get("native_terminal_slot"))
        if (
            slot["phase"] not in {"CLAIMED", "COMMITTED"}
            or slot["producer_digest"] != original.producer_digest
        ):
            raise _ControlError("terminal_control_unknown")
        d, f = slot["dalloc"], slot["fempty"]
        dalloc: receipt.D6 = (d[0], d[1], d[2], d[3], d[4], d[5])
        fempty: receipt.F9 = (f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8])
        if original.transfer_close != {
            "schema_version": "stackos.native-terminal-transfer.v1",
            "binding_digest": receipt._name(binding)[:-8],
            "dalloc": d,
            "fempty": f,
            "d_writer_closed": True,
            "d_directory_closed": True,
        }:
            raise _ControlError("terminal_custody_unknown")
        # Validate literal bools independently of Python's True == 1 equality.
        if (
            type(original.transfer_close["d_writer_closed"]) is not bool
            or type(original.transfer_close["d_directory_closed"]) is not bool
        ):
            raise _ControlError("terminal_custody_unknown")
        try:
            policy.check(clock._sample_clock())
        except _ControlError:
            raise _ControlError("terminal_clock_unknown") from None
        box = original.wrapped_key
        try:
            key = decrypt(
                bytes.fromhex(box["ciphertext"]),
                nonce=bytes.fromhex(box["nonce"]),
                project_id=project_id,
                kind="controlled-host-key:" + receipt._name(binding)[:-8],
                seed=seed,
            )
        except Exception:
            raise _ControlError("terminal_key_unknown") from None
        # This is NEW read custody, never a reclaimed slot or original D FD.
        attempt_path = settings.state_dir / "controlled-process" / f"{project_id}-{attempt_run_id}"
        leaf = attempt_path / "terminal"
        directory = _OwnedFD(
            os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        )
        opened.append(directory)
        if receipt._directory(directory.fd) != dalloc:
            raise _ControlError("terminal_file_unknown")
        read_owner = _OwnedFD(
            os.open(
                receipt._name(binding),
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory.fd,
            )
        )
        opened.append(read_owner)
        observed = receipt._read_opened(
            read_owner.fd,
            directory.fd,
            receipt._name(binding),
            dalloc,
            receipt._entry(directory.fd, receipt._name(binding)),
            binding,
            key,
            receipt_kind="terminal",
            retained=True,
        )
        if isinstance(observed, receipt._ReceiptHold) or not isinstance(
            observed[0], receipt._KeyAuthenticatedTerminalReceipt
        ):
            raise _ControlError("terminal_file_unknown")
        authenticated, vector = observed
        if (
            not isinstance(authenticated, receipt._KeyAuthenticatedTerminalReceipt)
            or len(vector) != 9
        ):
            raise _ControlError("terminal_file_unknown")
        final: receipt.F9 = (
            vector[0],
            vector[1],
            vector[2],
            vector[3],
            vector[4],
            vector[5],
            vector[6],
            vector[7],
            vector[8],
        )
        report_digest: str | None = None
        report_known = False
        report_fd = _OwnedFD(
            os.open(
                attempt_path / "watchdog-observation.json",
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        )
        opened.append(report_fd)
        try:
            before = os.fstat(report_fd.fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or not receipt._safe_fd(report_fd.fd, os.O_RDONLY)
            ):
                raise _ControlError("terminal_report_unknown")
            raw_report = os.pread(report_fd.fd, policy.max_output_bytes + 1, 0)
            if (
                len(raw_report) != before.st_size
                or len(raw_report) > policy.max_output_bytes
                or receipt._file_fingerprint(before)
                != receipt._file_fingerprint(os.fstat(report_fd.fd))
            ):
                raise _ControlError("terminal_report_unknown")
            report = json.loads(raw_report)
            report_digest = _digest(report)
            fact = authenticated.fact
            cleanup = [item for item in report.get("events", []) if item.get("event") == "cleanup"]
            report_known = (
                isinstance(fact, receipt._TerminalFact)
                and report.get("cleanup_known") is True
                and report.get("binding_digest") == receipt._name(binding)[:-8]
                and report.get("manifest_digest") == binding.manifest_digest
                and report.get("execution_unknown") is fact.execution_unknown
                and report.get("certified") is False
                and len(cleanup) == 1
                and cleanup[0].get("returncodes") == fact.child_returns
                and cleanup[0].get("reaped") == {"anchor": True, "worker": True}
                and report_digest == fact.report_digest
            )
        finally:
            report_close_known = report_fd.close()
        if not report_close_known:
            raise _ControlError("terminal_custody_unknown")
        completed = policy.check(clock._sample_clock())
        reader = _RecoveredTerminalRead(
            session,
            original,
            key,
            exit_witness,
            directory,
            read_owner,
            report_fd,
            dalloc,
            fempty,
            authenticated,
            final,
            completed,
            report_digest,
            report_known,
        )
    except _ControlError as failure:
        issues = (str(failure),)
    except Exception:
        issues = ("terminal_file_unknown",)
    except BaseException as failure:
        # Preserve the original interruption and retain every actually opened
        # read capability on that same exception; no close retry is authorized.
        failure.__dict__["_stackos_terminal_read_custody"] = tuple(opened)
        raise
    finally:
        for capability in reversed(opened):
            if not capability.close():
                reader = None
                issues = tuple(sorted({*issues, "terminal_custody_unknown"}))
    if reader is None:
        return _TerminalReconcileOut(
            "held", RunOut.model_validate(attempt), 0, None, issues, tuple(opened)
        )
    result = repo._commit_terminal_receipt_slot(
        verified=_terminal_verification(reader),
        expected_slot_revision=slot["revision"],
        evidence_ref=evidence_ref,
    )
    # A complete single-original scope may now certify. Additional originals
    # require their own retained witnesses; never infer them from row absence.
    row = repo.owner_get(project_id=project_id, canonical_item_id=canonical_item_id)
    certificate = None
    try:
        if not result.applied and row.quiescence_revision == row.revision and row.quiescence_ref:
            raw_owner = repo._owner_row(project_id=project_id, canonical_item_id=canonical_item_id)
            repo._assert_native_certificate_current(raw_owner)
            # Exact fresh readback only; no owner/event/revision write and no
            # new verifier/forward cursor is issued from the lost ack.
            from stackos.repositories.runs import WorkItemOwnershipOut

            certificate = WorkItemOwnershipOut.model_validate(raw_owner)
            session.commit()
            return _TerminalReconcileOut(
                "replayed", result.attempt, result.facts_revision, certificate, ()
            )
        snapshot = repo._native_quiescence_snapshot(
            project_id=project_id,
            canonical_item_id=canonical_item_id,
            owner_ref=row.owner_ref,
            expected_epoch=row.epoch,
            expected_revision=row.revision,
        )
        verified = _verify_native_quiescence(session, snapshot=snapshot, owners=(reader,))
        if isinstance(verified, _VerifiedQuiescence):
            certificate = repo._certify_owner_quiescence(
                project_id=project_id,
                canonical_item_id=canonical_item_id,
                owner_ref=row.owner_ref,
                expected_epoch=row.epoch,
                expected_revision=row.revision,
                evidence_ref=evidence_ref,
                verified=verified,
            )
    except Exception:
        pass  # Fact settlement does not imply whole-owner certification.
    return _TerminalReconcileOut(
        "recorded" if result.applied else "replayed",
        result.attempt,
        result.facts_revision,
        certificate,
        (),
    )


async def _run_native_control(
    admission: _NativeAdmission, *, custody: _NativeCustody
) -> _NativeControlOut:
    """Private native component, deliberately not registered as an action.

    Forward mutations repeat real current admission. Original-bound safety
    cleanup survives stop/grant loss. This function never certifies effects or
    finishes the canonical owner/attempt (the later terminal slice owns that).
    """
    from stackos.crypto.aes_gcm import encrypt
    from stackos.operations.actions.controlled import _NativeCommitGuard
    from stackos.repositories.runs import RunRepository, _ControlCursor, _NativeTransition

    if type(custody) is not _NativeCustody:
        raise _ControlError("native_custody_not_owned")
    custody.begin(admission)
    guard = _NativeCommitGuard(admission)
    original = admission.original
    policy = admission.policy
    policy.before_release(clock._sample_clock())
    manifest = _native_manifest(original.command)
    if _digest(manifest) != admission.runtime_digest:
        raise _ControlError("native_manifest_drift")
    session = original._ctx.session
    repository = RunRepository(
        session, audit_run_id=original.fence.controller_run_id, owner_commit_guard=guard
    )
    request = original.request
    launched = repository._reserve_owner_launch(
        project_id=original.fence.project_id,
        canonical_item_id=request.canonical_item_id,
        owner_ref=request.owner_ref,
        scope_digest=request.scope_digest,
        expected_epoch=request.expected_epoch,
        expected_revision=guard.ownership.revision,
        controller_run_id=original.fence.controller_run_id,
        intent_ref=request.intent_ref,
        manifest_digest=admission.runtime_digest,
        evidence_ref="native:reservation",
    )
    if launched.launch_permit:
        guard.advance(launched)
    binding = receipt._Binding(
        original.fence.project_id,
        request.canonical_item_id,
        request.owner_ref,
        request.expected_epoch,
        launched.ownership.revision,
        request.scope_digest,
        original.fence.controller_run_id,
        original.fence.run_plan_id,
        original.fence.run_plan_step_id,
        launched.attempt.id,
        request.intent_ref,
        admission.runtime_digest,
        "host:" + admission.runtime_digest[:24],
        policy.boot_uuid,
    )
    binding_digest = receipt._name(binding)[:-8]
    custody.binding_digest = binding_digest
    cursor = _ControlCursor(
        launched.ownership, launched.attempt, 0, binding_digest, admission.runtime_digest
    )
    if not launched.launch_permit:
        return _NativeControlOut(
            "held", cursor, None, False, issues=("native_duplicate_no_launch",), custody=custody
        )
    owned = custody.owned_fds
    process: subprocess.Popen[bytes] | None = None
    channel: _Channel | None = None
    controller_write: _OwnedFD | None = None
    slot: receipt._AllocatedSlot | None = None
    directory = -1
    report_path: Path | None = None
    report_read_fd: int | None = None
    arm_digest: str | None = None
    released = False
    report: dict[str, Any] | None = None
    terminal_owner: _TerminalOwner | None = None
    issues: list[str] = []
    evidence: list[dict[str, Any]] = []

    def transition(
        kind: Literal["register", "arm", "release", "cancel", "observe_cleanup", "hold"],
        facts: dict[str, Any],
    ) -> bool:
        nonlocal cursor
        result = repository._record_native_control(
            cursor=cursor,
            expected_control_revision=cursor.control_revision,
            transition=_NativeTransition(kind, facts),
            evidence_ref="native:" + kind,
        )
        cursor = result.cursor
        if kind in {"register", "arm", "release"} and result.applied:
            guard.advance(result)
        return result.applied

    def slot_fence(revision: int) -> dict[str, Any]:
        return {
            "project_id": binding.project_id,
            "canonical_item_id": binding.canonical_item_id,
            "owner_ref": binding.owner_ref,
            "scope_digest": binding.scope_digest,
            "expected_epoch": binding.epoch,
            "expected_revision": guard.ownership.revision,
            "controller_run_id": binding.controller_run_id,
            "intent_ref": binding.intent_ref,
            "manifest_digest": binding.manifest_digest,
            "attempt_run_id": binding.attempt_run_id,
            "expected_slot_revision": revision,
            "evidence_ref": "native:arm-slot",
        }

    def slot_ack(result: Any) -> None:
        nonlocal cursor
        guard.advance(result)
        cursor = _ControlCursor(
            result.ownership,
            result.attempt,
            cursor.control_revision,
            binding_digest,
            admission.runtime_digest,
        )

    async def message(expected: str) -> dict[str, Any]:
        assert channel is not None
        while _now() < policy.cleanup_deadline_continuous_ns:
            value = channel.receive()
            if value is not None:
                if value[0] != expected:
                    raise _ControlError("native_ack_sequence")
                return value[1]
            if process is not None and process.poll() is not None:
                raise _ControlError("native_watchdog_exited")
            guard()
            await asyncio.sleep(_remaining(policy.cleanup_deadline_continuous_ns, 0.005))
        raise _ControlError("native_ack_deadline")

    try:
        key = os.urandom(32)
        encrypted, nonce = encrypt(
            key,
            project_id=binding.project_id,
            kind="controlled-host-key:" + binding_digest,
            seed=admission.seed,
        )
        wrapped = {"ciphertext": encrypted.hex(), "nonce": nonce.hex()}
        directory, attempt_path = _open_arm_directory(
            admission.state_dir, binding.project_id, binding.attempt_run_id
        )
        owned.append(_OwnedFD(directory))
        if original.command.report_directory is not None:
            guard()
            _prepare_report_directory(
                admission.state_dir, original.command.report_directory, custody=owned
            )
        slot_ack(repository._reserve_receipt_slot(**slot_fence(0), binding=binding))
        allocated = receipt._allocate_receipt_slot(directory, binding)
        if isinstance(allocated, receipt._ReceiptHold):
            raise _ControlError("native_allocation_hold")
        slot = allocated
        custody.slot = slot
        slot_ack(
            repository._record_receipt_slot_allocated(
                **slot_fence(1),
                dalloc=slot.dalloc,
                fempty=slot.fempty,
                status_flags=slot.status_flags,
                offset=slot.offset,
            )
        )
        # A sibling leaf is allocated before its own Dalloc freeze. The ARM
        # protected leaf remains untouched. Four distinct retained/transferred
        # capabilities are established before claim or any spawn invocation.
        terminal_owner = _allocate_terminal_owner(
            session,
            binding=binding,
            policy=policy,
            manifest=manifest,
            wrapped_key=wrapped,
            key=key,
            custody=custody,
            attempt_path=attempt_path,
        )
        original_terminal = terminal_owner.original
        slot_ack(
            repository._reserve_terminal_receipt_slot(
                binding=binding,
                expected_revision=guard.ownership.revision,
                evidence_ref="native:terminal-slot",
            )
        )
        terminal_slot = terminal_owner.slot
        slot_ack(
            repository._record_terminal_slot_allocated(
                binding=binding,
                expected_revision=guard.ownership.revision,
                expected_slot_revision=1,
                dalloc=terminal_slot.dalloc,
                fempty=terminal_slot.fempty,
                status_flags=terminal_slot.status_flags,
                offset=terminal_slot.offset,
                evidence_ref="native:terminal-slot",
            )
        )
        guard.retain_terminal_original(original_terminal, custody)
        slot_ack(
            repository._claim_terminal_receipt_slot(
                binding=binding,
                expected_revision=guard.ownership.revision,
                expected_slot_revision=2,
                producer_digest=original_terminal.producer_digest,
                evidence_ref="native:terminal-slot",
            )
        )
        child_read, parent_write = os.pipe()
        parent_read, child_write = os.pipe()
        key_read, key_write = os.pipe()
        fd_owners = {
            fd: _OwnedFD(fd)
            for fd in (child_read, parent_write, parent_read, child_write, key_read, key_write)
        }
        controller_write = fd_owners[parent_write]
        owned.extend(fd_owners.values())
        os.write(key_write, key)
        parent_key_closes = {"write": _close_key_copy(fd_owners[key_write])}
        report_path = attempt_path / "watchdog-observation.json"
        report_fd = os.open(
            report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
        )
        report_owner = _OwnedFD(report_fd)
        owned.append(report_owner)
        report_read_fd = os.open(report_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        owned.append(_OwnedFD(report_read_fd))
        if (os.fstat(report_fd).st_dev, os.fstat(report_fd).st_ino) != (
            os.fstat(report_read_fd).st_dev,
            os.fstat(report_read_fd).st_ino,
        ):
            raise _ControlError("native_report_identity_changed")
        guard()
        policy.before_release(clock._sample_clock())
        process = custody.spawn(
            [
                manifest["executable"],
                "-I",
                "-S",
                manifest["watchdog_entry"],
                "watchdog",
                *map(
                    str,
                    (
                        child_read,
                        child_write,
                        key_read,
                        slot.fd,
                        directory,
                        report_fd,
                        terminal_slot.fd,
                        terminal_owner.transfer_directory.fd,
                    ),
                ),
            ],
            cwd=manifest["source_root"],
            env=original.command.descriptor()["environment"],
            start_new_session=True,
            close_fds=True,
            pass_fds=(
                child_read,
                child_write,
                key_read,
                slot.fd,
                directory,
                report_fd,
                terminal_slot.fd,
                terminal_owner.transfer_directory.fd,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # The spawn owner's finally retains W before this helper returns.
        for fd in (child_read, child_write, key_read, report_fd):
            if fd == key_read:
                parent_key_closes["read"] = _close_key_copy(fd_owners[fd])
            elif not (report_owner if fd == report_fd else fd_owners[fd]).close():
                raise _ControlError("native_d_handoff_close_unknown")
        channel = _Channel(parent_read, parent_write, key, binding_digest, admission.runtime_digest)
        barrier_nonce = os.urandom(32).hex()
        cancel_digest = _digest({"binding": binding_digest, "intent": "cancel"})
        config = {
            "binding": asdict(binding),
            "policy": policy._decision,
            "manifest": manifest,
            "slot": {
                "dalloc": list(slot.dalloc),
                "fempty": list(slot.fempty),
                "status_flags": slot.status_flags,
                "offset": slot.offset,
            },
            "barrier_nonce": barrier_nonce,
            "cancel_digest": cancel_digest,
            "parent_key_closes": parent_key_closes,
            "terminal_slot": {
                "dalloc": list(terminal_slot.dalloc),
                "fempty": list(terminal_slot.fempty),
                "status_flags": terminal_slot.status_flags,
                "offset": terminal_slot.offset,
                "producer_digest": original_terminal.producer_digest,
                "known_slots": [
                    terminal_slot.fd,
                    terminal_owner.transfer_directory.fd,
                    terminal_owner.read.fd,
                    terminal_owner.directory.fd,
                ],
                "retained": _fd_observation(
                    "controller_terminal_verification",
                    {"read": terminal_owner.read.fd, "directory": terminal_owner.directory.fd},
                )["handles"],
            },
        }
        evidence.append(
            {
                "role": "controller",
                "event": "handoff",
                "fds": _fd_observation("controller", {"writer": slot.fd, "directory": directory}),
            }
        )
        channel.send("bootstrap", config, policy.cleanup_deadline_continuous_ns)
        received = await message("received")
        watchdog = identity._ProcessIdentity(**received["identity"])
        custody.identity = watchdog
        if (
            watchdog.pid != process.pid
            or not _matches(watchdog)
            or watchdog.pid != watchdog.pgid
            or watchdog.pid != watchdog.sid
        ):
            raise _ControlError("native_watchdog_identity_changed")
        if slot._close_once() is not None:
            raise _ControlError("native_d_writer_close_unknown")
        if terminal_slot._close_once() is not None or not terminal_owner.transfer_directory.close():
            raise _ControlError("native_terminal_transfer_close_unknown")
        guard.retain_terminal_transfer(terminal_owner)
        evidence.append(
            {
                "role": "controller",
                "event": "writer_closed",
                "fds": _fd_observation("controller_after_transfer", {"writer": slot.fd}),
            }
        )
        channel.send(
            "writer_closed",
            {"closed": True, "terminal_closed": True},
            policy.cleanup_deadline_continuous_ns,
        )
        ready = await message("ready")
        registered = {k: identity._ProcessIdentity(**v) for k, v in ready["identities"].items()}
        if (
            not all(_matches(x) for x in registered.values())
            or registered.get("watchdog") != watchdog
        ):
            raise _ControlError("native_registration_unknown")
        transition(
            "register",
            {
                "binding": asdict(binding),
                "manifest": manifest,
                "wrapped_key": wrapped,
                "identities": ready["identities"],
            },
        )
        observed_clock = clock._sample_clock()
        policy.before_release(observed_clock)
        fact = receipt._ArmFact(
            "armed",
            "host-shim",
            0,
            observed_clock,
            identity._ProcessObservation("known", registered["watchdog"]),
            identity._ProcessObservation("known", registered["anchor"]),
            policy.deadline_continuous_ns,
            cancel_digest,
            barrier_nonce,
        )
        raw = receipt._seal_receipt(binding, fact, key)
        if isinstance(raw, receipt._ReceiptHold):
            raise _ControlError("native_arm_seal_hold")
        arm_digest = hashlib.sha256(raw).hexdigest()
        slot_ack(repository._claim_receipt_slot(**slot_fence(2), sealed_digest=arm_digest))
        guard()
        registration_digest = _digest(
            {"binding": asdict(binding), "manifest": manifest, "identities": ready["identities"]}
        )
        channel.send(
            "fill",
            {
                "raw": raw.hex(),
                "registration_digest": registration_digest,
                "control_revision": cursor.control_revision,
            },
            policy.cleanup_deadline_continuous_ns,
        )
        filled = await message("filled")
        readback = receipt._read_receipt(directory, binding, key)
        if (
            isinstance(readback, receipt._ReceiptHold)
            or readback.digest != arm_digest
            or filled.get("arm_digest") != arm_digest
            or filled.get("writer_closed") is not True
        ):
            raise _ControlError("native_arm_readback_unknown")
        slot_ack(repository._commit_receipt_slot(**slot_fence(3), receipt_digest=arm_digest))
        transition("arm", {"arm_digest": arm_digest})
        guard()
        policy.before_release(clock._sample_clock())
        group = _observe_owned_group(
            anchor=registered["anchor"], expected=(registered["anchor"], registered["worker"])
        )
        if group.status != "known":
            raise _ControlError("native_release_group_unknown")
        payload = {
            "nonce_digest": _digest(barrier_nonce),
            "registration_digest": registration_digest,
            "control_revision": cursor.control_revision + 1,
        }
        if not transition("release", {"arm_digest": arm_digest, "frame_digest": _digest(payload)}):
            raise _ControlError("native_duplicate_release_no_frame")
        guard()
        policy.before_release(clock._sample_clock())
        channel.send("release", payload, policy.cleanup_deadline_continuous_ns)
        released = True
        await message("released")
        while process.poll() is None and _now() < policy.cleanup_deadline_continuous_ns:
            try:
                guard()
            except Exception:
                transition("cancel", {"reason": "grant_lost"})
                channel.send("cancel", {}, policy.cleanup_deadline_continuous_ns)
                break
            await asyncio.sleep(_remaining(policy.cleanup_deadline_continuous_ns, 0.005))
    except Exception as exc:
        issues.append(str(exc) if type(exc) is _ControlError else "native_forward_or_setup_held")
        try:
            transition(
                "hold",
                {"reason": "custody_unknown", "outcome_unknown": custody.process is not None},
            )
        except Exception:
            issues.append("native_hold_ack_unknown")
    finally:
        if process is None:
            # A failed/interrupted spawn helper may not return, while its
            # finally has already adopted the exact child into this owner.
            process = custody.process
        if process is not None:
            custody._retain_spawn(process)
        # Closing the D->W pipe activates W's original-bound safety lane even
        # when DB admission or the live controller no longer exists.
        if controller_write is not None and not controller_write.close():
            issues.append("native_control_close_unknown")
        if slot is not None and slot._close_once() is not None:
            issues.append("native_writer_close_unknown")
        if process is not None:
            while process.poll() is None:
                try:
                    if _now() >= policy.cleanup_deadline_continuous_ns:
                        break
                except _ControlError:
                    break
                await asyncio.sleep(_remaining(policy.cleanup_deadline_continuous_ns, 0.005))
            if process.poll() is None:
                issues.append("native_watchdog_cleanup_unknown")
        if report_read_fd is not None:
            try:
                # Independently held original FD, never a reopened replacement
                # path. Observe post-write file stability around bounded read.
                before = os.fstat(report_read_fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_nlink != 1
                ):
                    raise _ControlError("native_report_identity_changed")
                raw_report = os.pread(report_read_fd, policy.max_output_bytes + 1, 0)
                if receipt._file_fingerprint(before) != receipt._file_fingerprint(
                    os.fstat(report_read_fd)
                ):
                    raise _ControlError("native_report_identity_changed")
                decoded = json.loads(raw_report)
                if (
                    len(raw_report) > policy.max_output_bytes
                    or type(decoded) is not dict
                    or decoded.get("binding_digest") != binding_digest
                    or decoded.get("manifest_digest") != admission.runtime_digest
                    or decoded.get("certified") is not False
                    or type(decoded.get("execution_unknown")) is not bool
                ):
                    raise _ControlError("native_cleanup_report_unknown")
                report = decoded
                if terminal_owner is not None:
                    terminal_owner.report_digest = _digest(decoded)
                evidence.extend(decoded["events"])
                if decoded["execution_unknown"]:
                    issues.append("native_execution_unknown")
            except (OSError, ValueError, KeyError, TypeError):
                issues.append("native_cleanup_report_unknown")
        if terminal_owner is not None and (process is None or process.poll() is not None):
            try:
                terminal_owner.finish_read()
            except Exception:
                issues.append("native_terminal_read_unknown")
            finally:
                terminal_owner.close_verification()
        if process is None or process.poll() is not None:
            if not custody.close_accounted():
                issues.append("native_fd_close_unknown")
        else:
            issues.append("native_custody_retained")
        custody.unresolved = list(issues)
    known = (
        process is not None
        and process.returncode == 0
        and report is not None
        and report.get("cleanup_known") is True
        and custody.accounted
    )
    if known and report is not None:
        completed = report.get("cleanup_completed_continuous_ns")
        try:
            known = (
                type(completed) is int
                and 0 <= completed < policy.cleanup_deadline_continuous_ns
                and _now() < policy.cleanup_deadline_continuous_ns
            )
        except _ControlError:
            known = False
        if not known:
            issues.append("native_cleanup_timing_unknown")
    if known:
        try:
            transition("observe_cleanup", {"observation_digest": _digest(report)})
        except Exception:
            issues.append("native_cleanup_ack_unknown")
    if terminal_owner is not None:
        reconciled = _reconcile_native_terminal(
            session,
            original=terminal_owner.original,
            custody=custody,
            exit_witness=None,
            evidence_ref="native:terminal",
            owner=terminal_owner,
        )
        if reconciled.state == "held":
            issues.extend(reconciled.issues)
    return _NativeControlOut(
        "cleaned" if known and not issues else "held" if known else "unknown",
        cursor,
        arm_digest,
        released,
        issues=tuple(issues),
        evidence=tuple(evidence),
        cleanup_known=known,
        custody=custody,
        terminal_owner=terminal_owner,
    )
