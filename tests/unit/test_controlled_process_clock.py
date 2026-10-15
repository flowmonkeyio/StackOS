"""Fake native denials and arithmetic; no process or state fixtures."""

from __future__ import annotations

import builtins
import ctypes
import importlib
import io
import os
import socket
import subprocess
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from stackos import controlled_process_clock as clock

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


class Function:
    def __init__(self, implementation):
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.implementation(*args)


class Native:
    def __init__(self, *, numer=2, denom=3, continuous=30, awake=90, boots=None):
        self.numer, self.denom = numer, denom
        self.boots = iter(boots or [BOOT, BOOT])
        self.mach_timebase_info = Function(self.timebase)
        self.mach_continuous_time = Function(lambda: continuous)
        self.mach_absolute_time = Function(lambda: awake)
        self.sysctlbyname = Function(self.sysctl)

    def timebase(self, pointer):
        pointer._obj.numer, pointer._obj.denom = self.numer, self.denom
        return 0

    def sysctl(self, name, buffer, size, new, new_size):
        assert name == b"kern.bootsessionuuid" and new is None and new_size == 0
        size._obj.value = 37
        if buffer is not None:
            buffer.value = next(self.boots).encode("ascii")
        return 0


def sample(monkeypatch, native):
    monkeypatch.setattr(clock, "_runtime_abi", lambda: clock._SUPPORTED_ABI)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: native)
    return clock._sample_clock()


def observation(total=100, awake=200, **changes):
    return replace(
        clock._ClockObservation("known", BOOT, total, awake, runtime_abi=clock._SUPPORTED_ABI),
        **changes,
    )


def test_lazy_import(monkeypatch):
    loader = Mock(side_effect=AssertionError("import loaded a native library"))
    monkeypatch.setattr(ctypes, "CDLL", loader)
    importlib.reload(clock)
    loader.assert_not_called()


def test_exact_prototypes_and_nonatomic_sample(monkeypatch):
    native = Native()
    result = sample(monkeypatch, native)
    assert result.status == "known" and result.issues == ()
    assert (result.continuous_ns, result.awake_ns) == (20, 60)
    assert native.mach_timebase_info.argtypes == [ctypes.POINTER(clock._MachTimebaseInfo)]
    assert native.mach_timebase_info.restype is ctypes.c_int
    assert native.mach_continuous_time.argtypes == []
    assert native.mach_continuous_time.restype is ctypes.c_uint64
    assert native.mach_absolute_time.argtypes == []
    assert native.mach_absolute_time.restype is ctypes.c_uint64
    assert native.sysctlbyname.argtypes == [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    assert native.sysctlbyname.restype is ctypes.c_int


@pytest.mark.parametrize(
    "ticks,numer,denom,expected",
    [(0, 2, 3, 0), (5, 3, 2, 7), (2**64 - 1, 125, 3, (2**64 - 1) * 125 // 3)],
)
def test_integer_conversion(ticks, numer, denom, expected):
    assert clock._ticks_to_ns(ticks, numer, denom) == expected


@pytest.mark.parametrize(
    "values",
    [
        (-1, 1, 1),
        (2**64, 1, 1),
        (True, 1, 1),
        (1, 0, 1),
        (1, 1, 0),
        (1, -1, 1),
        (1, 1, -1),
        (1, 1, True),
    ],
)
def test_invalid_ratio_or_ticks(values):
    with pytest.raises(clock._ObservationError):
        clock._ticks_to_ns(*values)


@pytest.mark.parametrize(
    "native",
    [Native(numer=0), Native(denom=0), Native(boots=[BOOT, OTHER_BOOT]), Native(boots=["0" * 36])],
)
def test_invalid_native_result_is_unknown(monkeypatch, native):
    result = sample(monkeypatch, native)
    assert result.status == "unknown" and result.issues
    assert isinstance(clock._elapsed_total_ns(observation(), result), clock._Unknown)


@pytest.mark.parametrize("failure", [PermissionError, OSError, AttributeError])
def test_unavailable_or_denied_native_is_unknown(monkeypatch, failure):
    native = Native()
    native.mach_timebase_info = Function(Mock(side_effect=failure("bounded fake denial")))
    assert sample(monkeypatch, native).status == "unknown"


def test_short_or_denied_boot_read(monkeypatch):
    native = Native()

    def short(name, buffer, size, new, new_size):
        size._obj.value = 36
        return 0

    native.sysctlbyname = Function(short)
    assert sample(monkeypatch, native).status == "unknown"
    native.sysctlbyname = Function(lambda *args: -1)
    assert sample(monkeypatch, native).status == "unknown"


def test_unsupported_abi_never_loads(monkeypatch):
    monkeypatch.setattr(clock, "_runtime_abi", lambda: "Linux/x86_64/64")
    loader = Mock(side_effect=AssertionError("unsupported ABI loaded"))
    monkeypatch.setattr(ctypes, "CDLL", loader)
    assert clock._sample_clock().status == "unknown"
    loader.assert_not_called()


@pytest.mark.parametrize(
    "total,ceiling,expected",
    [
        (109, 10, "within"),
        (110, 10, "expired"),
        (111, 10, "expired"),
        (100, 0, "expired"),
        (100, 1, "within"),
    ],
)
def test_deadline_boundary(total, ceiling, expected):
    assert clock._deadline_status(observation(), observation(total, 200), ceiling) == expected


@pytest.mark.parametrize("total,awake", [(100, 200), (110, 200), (101, 900), (900, 201)])
def test_independent_nonnegative_deltas_and_zero(total, awake):
    before, after = observation(), observation(total, awake)
    assert clock._elapsed_total_ns(before, after) == total - 100
    assert clock._deadline_status(before, after, 10) in {"within", "expired"}


@pytest.mark.parametrize(
    "changes",
    [
        {"continuous_ns": 99},
        {"awake_ns": 199},
        {"continuous_ns": None},
        {"awake_ns": -1},
        {"continuous_ns": True},
        {"status": "unknown"},
        {"api_version": "other"},
        {"runtime_abi": "other"},
        {"boot_uuid": None},
        {"boot_uuid": ""},
        {"issues": ("uncertain",)},
    ],
)
def test_invalid_observation_cannot_establish_deadline(changes):
    after = observation(**changes)
    assert isinstance(clock._elapsed_total_ns(observation(), after), clock._Unknown)
    assert clock._deadline_status(observation(), after, 10) == "clock_uncertain"


def test_boot_change_is_not_elapsed_time():
    assert (
        clock._deadline_status(observation(), observation(boot_uuid=OTHER_BOOT), 10)
        == "boot_changed"
    )


@pytest.mark.parametrize("ceiling", [-1, True, 1.5])
def test_invalid_ceiling(ceiling):
    assert clock._deadline_status(observation(), observation(), ceiling) == "clock_uncertain"


def test_wall_jump_and_fake_suspend_do_not_renew_or_classify(monkeypatch):
    before, after = observation(), observation(1_000, 200)
    monkeypatch.setattr(time, "time", Mock(side_effect=AssertionError("wall clock consulted")))
    assert clock._deadline_status(before, after, 100) == "expired"
    assert clock._elapsed_total_ns(before, after) == 900
    with pytest.raises(FrozenInstanceError):
        before.awake_ns = 0
