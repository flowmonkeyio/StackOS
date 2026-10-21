"""Fake process identity reads; no process is created or signalled."""

from __future__ import annotations

import builtins
import ctypes
import importlib
import io
import os
import socket
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from stackos import controlled_process_clock as clock
from stackos import controlled_process_identity as identity

BOOT = "583c1631-0f6b-494f-82d9-94d7633a7c5a"
OTHER_BOOT = "583c1631-0f6b-494f-82d9-94d7633a7c5b"


@pytest.fixture(autouse=True)
def forbid_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("observation attempted an effect")

    original_open = builtins.open

    def read_only(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            forbidden()
        return original_open(file, mode, *args, **kwargs)

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("kill", "killpg", "fork", "posix_spawn", "posix_spawnp", "system"):
        monkeypatch.setattr(os, name, forbidden, raising=False)
    for name in ("write_text", "write_bytes", "mkdir", "touch"):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(builtins, "open", read_only)
    monkeypatch.setattr(io, "open", read_only)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def known(**changes):
    value = replace(
        identity._ProcessIdentity(BOOT, 123, 12, 100, 90, 1_700_000_000, 123), **changes
    )
    return identity._ProcessObservation("known", value)


def fake_reads(monkeypatch, *, records=None, boots=None, groups=None, sessions=None):
    records = iter(records or [(123, 12, 100, 1_700_000_000, 123)] * 2)
    boots = iter(boots or [BOOT, BOOT])
    groups = iter(groups or [100, 100])
    sessions = iter(sessions or [90, 90])
    monkeypatch.setattr(clock, "_runtime_abi", lambda: clock._SUPPORTED_ABI)
    monkeypatch.setattr(clock, "_load_native", lambda: None)
    monkeypatch.setattr(clock, "_read_boot_uuid", lambda native: next(boots))
    monkeypatch.setattr(identity, "_load_native", lambda: None)
    monkeypatch.setattr(identity, "_read_bsdinfo", lambda native, pid: next(records))
    monkeypatch.setattr(os, "getpgid", lambda pid: next(groups))
    monkeypatch.setattr(os, "getsid", lambda pid: next(sessions))


def test_lazy_import(monkeypatch):
    loader = Mock(side_effect=AssertionError("import loaded native library"))
    monkeypatch.setattr(ctypes, "CDLL", loader)
    importlib.reload(identity)
    loader.assert_not_called()


def test_stable_bracketed_identity(monkeypatch):
    fake_reads(monkeypatch)
    result = identity._inspect_process(123)
    assert result == known()
    assert identity._same_process(known(), result) == "match"


@pytest.mark.parametrize(
    "changes",
    [
        {"pid": 124},
        {"boot_uuid": OTHER_BOOT},
        {"start_seconds": 1_700_000_001},
        {"start_microseconds": 124},
        {"pgid": 101},
        {"sid": 91},
    ],
)
def test_pid_alone_never_matches(changes):
    assert identity._same_process(known(), known(**changes)) == "mismatch"


def test_reparenting_is_not_a_new_process():
    assert identity._same_process(known(), known(parent_pid=13)) == "match"


@pytest.mark.parametrize(
    "changes",
    [
        {"api_version": "other"},
        {"runtime_abi": "other"},
        {"start_microseconds": 1_000_000},
        {"start_microseconds": -1},
        {"start_seconds": 0},
        {"pgid": 0},
        {"sid": 0},
        {"pid": True},
        {"boot_uuid": ""},
    ],
)
def test_invalid_or_unsupported_basis_is_unknown(changes):
    assert identity._same_process(known(), known(**changes)) == "unknown"


def test_unknown_or_missing_observation():
    for other in (
        identity._ProcessObservation("unknown", issues=("denied",)),
        identity._ProcessObservation("known"),
    ):
        assert identity._same_process(known(), other) == "unknown"
        assert identity._same_process(other, known()) == "unknown"


@pytest.mark.parametrize(
    "changes",
    [
        {"records": [(123, 12, 100, 1_700_000_000, 123), (123, 12, 100, 1_700_000_001, 0)]},
        {"boots": [BOOT, OTHER_BOOT]},
        {"groups": [100, 101]},
        {"groups": [101, 101]},
        {"sessions": [90, 91]},
        {"sessions": [0, 0]},
    ],
)
def test_interleaved_reuse_or_group_session_boot_change(monkeypatch, changes):
    fake_reads(monkeypatch, **changes)
    result = identity._inspect_process(123)
    assert result.status == "unknown" and result.identity is None and result.issues


@pytest.mark.parametrize("failure", [ProcessLookupError, PermissionError, OSError, AttributeError])
def test_missing_denied_or_unavailable_read(monkeypatch, failure):
    fake_reads(monkeypatch)
    monkeypatch.setattr(identity, "_read_bsdinfo", Mock(side_effect=failure("fake denial")))
    assert identity._inspect_process(123).status == "unknown"


@pytest.mark.parametrize("pid", [0, -1, True, 2**31, "123"])
def test_invalid_pid_is_not_queried(monkeypatch, pid):
    read = Mock(side_effect=AssertionError("invalid PID queried"))
    monkeypatch.setattr(identity, "_load_native", read)
    assert identity._inspect_process(pid).status == "unknown"
    read.assert_not_called()


def test_exact_sdk_layout_and_prototype(monkeypatch):
    function = Mock(return_value=identity._BSDINFO_SIZE)
    native = Mock(proc_pidinfo=function)
    monkeypatch.setattr(clock, "_runtime_abi", lambda: clock._SUPPORTED_ABI)
    monkeypatch.setattr(ctypes, "CDLL", Mock(return_value=native))
    assert identity._load_native() is native
    assert ctypes.sizeof(identity._ProcBSDInfo) == 136
    assert identity._ProcBSDInfo.pbi_pid.offset == 12
    assert identity._ProcBSDInfo.pbi_ppid.offset == 16
    assert identity._ProcBSDInfo.pbi_pgid.offset == 100
    assert identity._ProcBSDInfo.pbi_start_tvsec.offset == 120
    assert identity._ProcBSDInfo.pbi_start_tvusec.offset == 128
    assert function.argtypes == [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    assert function.restype is ctypes.c_int


@pytest.mark.parametrize("returned", [-1, 0, 135, 137])
def test_exact_returned_byte_count_required(returned):
    native = Mock(proc_pidinfo=Mock(return_value=returned))
    with pytest.raises(clock._ObservationError, match="proc_pidinfo_incomplete"):
        identity._read_bsdinfo(native, 123)


@pytest.mark.parametrize(
    "changes",
    [{"pbi_pid": 124}, {"pbi_start_tvusec": 1_000_000}, {"pbi_start_tvsec": 0}, {"pbi_pgid": 0}],
)
def test_malformed_native_struct(changes):
    def fill(pid, flavor, arg, buffer, size):
        assert (pid, flavor, arg, size) == (123, 3, 0, 136)
        data = buffer._obj
        data.pbi_pid, data.pbi_ppid, data.pbi_pgid = 123, 12, 100
        data.pbi_start_tvsec, data.pbi_start_tvusec = 1_700_000_000, 123
        for name, value in changes.items():
            setattr(data, name, value)
        return 136

    with pytest.raises(clock._ObservationError, match="proc_pidinfo_invalid"):
        identity._read_bsdinfo(Mock(proc_pidinfo=fill), 123)


def test_unsupported_abi_never_loads(monkeypatch):
    monkeypatch.setattr(clock, "_runtime_abi", lambda: "Darwin/x86_64/64")
    loader = Mock(side_effect=AssertionError("unsupported ABI loaded"))
    monkeypatch.setattr(ctypes, "CDLL", loader)
    assert identity._inspect_process(123).status == "unknown"
    loader.assert_not_called()
