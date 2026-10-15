"""Private read-only process identity; a match never authorizes a signal."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Literal

from stackos import controlled_process_clock as clock

_API_VERSION = "darwin-proc-bsdinfo-v1"
_PROC_PIDTBSDINFO = 3
_BSDINFO_SIZE = 136


class _ProcBSDInfo(ctypes.Structure):
    pbi_pid: int
    pbi_ppid: int
    pbi_pgid: int
    pbi_start_tvsec: int
    pbi_start_tvusec: int
    # Exact local SDK sys/proc_info.h layout, including unused fields/padding.
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


@dataclass(frozen=True)
class _ProcessIdentity:
    boot_uuid: str
    pid: int
    parent_pid: int
    pgid: int
    sid: int
    start_seconds: int
    start_microseconds: int
    api_version: str = _API_VERSION
    runtime_abi: str = clock._SUPPORTED_ABI


@dataclass(frozen=True)
class _ProcessObservation:
    status: Literal["known", "unknown"]
    identity: _ProcessIdentity | None = None
    issues: tuple[str, ...] = ()


def _load_native() -> ctypes.CDLL:
    if clock._runtime_abi() != clock._SUPPORTED_ABI:
        raise clock._ObservationError("unsupported_runtime_abi")
    if (
        ctypes.sizeof(_ProcBSDInfo) != _BSDINFO_SIZE
        or vars(_ProcBSDInfo)["pbi_start_tvsec"].offset != 120
        or vars(_ProcBSDInfo)["pbi_start_tvusec"].offset != 128
        or vars(_ProcBSDInfo)["pbi_pgid"].offset != 100
    ):
        raise clock._ObservationError("unsupported_bsdinfo_layout")
    native = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    native.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    native.proc_pidinfo.restype = ctypes.c_int
    return native


def _read_bsdinfo(native: ctypes.CDLL, pid: int) -> tuple[int, int, int, int, int]:
    info = _ProcBSDInfo()
    size = native.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, ctypes.byref(info), _BSDINFO_SIZE)
    if size != _BSDINFO_SIZE:
        raise clock._ObservationError("proc_pidinfo_incomplete")
    fields = (
        int(info.pbi_pid),
        int(info.pbi_ppid),
        int(info.pbi_pgid),
        int(info.pbi_start_tvsec),
        int(info.pbi_start_tvusec),
    )
    actual_pid, parent, pgid, seconds, micros = fields
    if (
        actual_pid != pid
        or not 0 <= parent < 2**31
        or not 0 < pgid < 2**31
        or seconds <= 0
        or not 0 <= micros < 1_000_000
    ):
        raise clock._ObservationError("proc_pidinfo_invalid")
    return fields


def _inspect_process(pid: int) -> _ProcessObservation:
    if type(pid) is not int or not 0 < pid < 2**31:
        return _ProcessObservation("unknown", issues=("invalid_pid",))
    try:
        abi = clock._runtime_abi()
        system = clock._load_native()
        native = _load_native()
        boot = clock._read_boot_uuid(system)
        first = _read_bsdinfo(native, pid)
        group, session = os.getpgid(pid), os.getsid(pid)
        group_after, session_after = os.getpgid(pid), os.getsid(pid)
        second = _read_bsdinfo(native, pid)
        boot_after = clock._read_boot_uuid(system)
        if (
            first != second
            or group != group_after
            or session != session_after
            or first[2] != group
            or boot != boot_after
            or clock._runtime_abi() != abi
        ):
            raise clock._ObservationError("process_identity_changed")
        identity = _ProcessIdentity(
            boot, pid, first[1], group, session, first[3], first[4], _API_VERSION, abi
        )
        if not _valid_identity(identity):
            raise clock._ObservationError("process_identity_invalid")
        return _ProcessObservation("known", identity)
    except (OSError, AttributeError, ValueError, TypeError, OverflowError) as error:
        return _ProcessObservation("unknown", issues=(clock._issue(error),))


def _valid_identity(identity: _ProcessIdentity) -> bool:
    return (
        clock._valid_boot(identity.boot_uuid)
        and identity.api_version == _API_VERSION
        and identity.runtime_abi == clock._SUPPORTED_ABI
        and all(
            type(value) is int
            for value in (
                identity.pid,
                identity.parent_pid,
                identity.pgid,
                identity.sid,
                identity.start_seconds,
                identity.start_microseconds,
            )
        )
        and 0 < identity.pid < 2**31
        and 0 <= identity.parent_pid < 2**31
        and 0 < identity.pgid < 2**31
        and 0 < identity.sid < 2**31
        and identity.start_seconds > 0
        and 0 <= identity.start_microseconds < 1_000_000
    )


def _same_process(
    expected: _ProcessObservation, observed: _ProcessObservation
) -> Literal["match", "mismatch", "unknown"]:
    if (
        expected.status != "known"
        or observed.status != "known"
        or expected.issues
        or observed.issues
        or expected.identity is None
        or observed.identity is None
        or not _valid_identity(expected.identity)
        or not _valid_identity(observed.identity)
    ):
        return "unknown"
    before, after = expected.identity, observed.identity
    # Reparenting alone does not change process identity. Never compare a name.
    return (
        "match"
        if (
            before.boot_uuid,
            before.pid,
            before.start_seconds,
            before.start_microseconds,
            before.pgid,
            before.sid,
        )
        == (
            after.boot_uuid,
            after.pid,
            after.start_seconds,
            after.start_microseconds,
            after.pgid,
            after.sid,
        )
        else "mismatch"
    )
