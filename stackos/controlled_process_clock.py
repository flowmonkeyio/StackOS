"""Private Darwin observations; no timer, process control or execution authority."""

from __future__ import annotations

import ctypes
import platform
import sys
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

_API_VERSION = "darwin-mach-clock-v1"
_SUPPORTED_ABI = "Darwin/arm64/64"


@dataclass(frozen=True)
class _Unknown:
    issues: tuple[str, ...]


@dataclass(frozen=True)
class _ClockObservation:
    status: Literal["known", "unknown"]
    boot_uuid: str | None = None
    continuous_ns: int | None = None
    awake_ns: int | None = None
    api_version: str = _API_VERSION
    runtime_abi: str | None = None
    issues: tuple[str, ...] = ()


class _ObservationError(ValueError):
    """A bounded diagnostic code, never native payload or environment text."""


class _MachTimebaseInfo(ctypes.Structure):
    numer: int
    denom: int
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


def _runtime_abi() -> str:
    system = "Darwin" if sys.platform == "darwin" else sys.platform
    return f"{system}/{platform.machine()}/{ctypes.sizeof(ctypes.c_void_p) * 8}"


def _load_native() -> ctypes.CDLL:
    # Loading and probing are deliberately deferred until an explicit observation.
    if _runtime_abi() != _SUPPORTED_ABI:
        raise _ObservationError("unsupported_runtime_abi")
    native = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    native.mach_timebase_info.argtypes = [ctypes.POINTER(_MachTimebaseInfo)]
    native.mach_timebase_info.restype = ctypes.c_int
    native.mach_continuous_time.argtypes = []
    native.mach_continuous_time.restype = ctypes.c_uint64
    native.mach_absolute_time.argtypes = []
    native.mach_absolute_time.restype = ctypes.c_uint64
    native.sysctlbyname.argtypes = [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    native.sysctlbyname.restype = ctypes.c_int
    return native


def _read_boot_uuid(native: ctypes.CDLL) -> str:
    size = ctypes.c_size_t()
    name = b"kern.bootsessionuuid"
    if native.sysctlbyname(name, None, ctypes.byref(size), None, 0) != 0 or size.value != 37:
        raise _ObservationError("boot_uuid_unavailable")
    value = ctypes.create_string_buffer(size.value)
    if native.sysctlbyname(name, value, ctypes.byref(size), None, 0) != 0 or size.value != 37:
        raise _ObservationError("boot_uuid_unavailable")
    if value.raw[-1:] != b"\0" or len(value.value) != 36:
        raise _ObservationError("boot_uuid_malformed")
    parsed = UUID(value.value.decode("ascii"))
    if not parsed.int:
        raise _ObservationError("boot_uuid_malformed")
    return str(parsed)


def _ticks_to_ns(ticks: int, numer: int, denom: int) -> int:
    if (
        type(ticks) is not int
        or not 0 <= ticks < 2**64
        or type(numer) is not int
        or not 0 < numer < 2**32
        or type(denom) is not int
        or not 0 < denom < 2**32
    ):
        raise _ObservationError("invalid_timebase_or_ticks")
    return ticks * numer // denom


def _issue(error: Exception) -> str:
    return (
        str(error)
        if isinstance(error, _ObservationError)
        else f"native_read:{type(error).__name__}"
    )


def _sample_clock() -> _ClockObservation:
    abi = None
    try:
        abi = _runtime_abi()
        native = _load_native()
        boot = _read_boot_uuid(native)
        ratio = _MachTimebaseInfo()
        if native.mach_timebase_info(ctypes.byref(ratio)) != 0:
            raise _ObservationError("timebase_unavailable")
        continuous = _ticks_to_ns(
            int(native.mach_continuous_time()), int(ratio.numer), int(ratio.denom)
        )
        awake = _ticks_to_ns(int(native.mach_absolute_time()), int(ratio.numer), int(ratio.denom))
        if _read_boot_uuid(native) != boot or _runtime_abi() != abi:
            raise _ObservationError("clock_basis_changed")
        return _ClockObservation("known", boot, continuous, awake, _API_VERSION, abi)
    except (OSError, AttributeError, ValueError, TypeError, OverflowError) as error:
        return _ClockObservation("unknown", runtime_abi=abi, issues=(_issue(error),))


def _valid_boot(value: str | None) -> bool:
    try:
        return value is not None and bool(UUID(value).int) and str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _valid_clock(observation: _ClockObservation) -> bool:
    return (
        observation.status == "known"
        and not observation.issues
        and _valid_boot(observation.boot_uuid)
        and observation.api_version == _API_VERSION
        and observation.runtime_abi == _SUPPORTED_ABI
        and isinstance(observation.continuous_ns, int)
        and type(observation.continuous_ns) is int
        and observation.continuous_ns >= 0
        and isinstance(observation.awake_ns, int)
        and type(observation.awake_ns) is int
        and observation.awake_ns >= 0
    )


def _elapsed_total_ns(before: _ClockObservation, after: _ClockObservation) -> int | _Unknown:
    if not _valid_clock(before) or not _valid_clock(after):
        return _Unknown(("clock_unknown_or_unsupported",))
    if before.boot_uuid != after.boot_uuid:
        return _Unknown(("boot_changed",))
    # Each counter is sampled separately. Neither absolute nor delta ordering
    # between the two counters is evidence of suspension or clock correctness.
    assert before.continuous_ns is not None and after.continuous_ns is not None
    assert before.awake_ns is not None and after.awake_ns is not None
    total = after.continuous_ns - before.continuous_ns
    awake = after.awake_ns - before.awake_ns
    if total < 0 or awake < 0:
        return _Unknown(("clock_rollback",))
    return total


def _deadline_status(
    before: _ClockObservation, after: _ClockObservation, ceiling_ns: int
) -> Literal["within", "expired", "clock_uncertain", "boot_changed"]:
    if type(ceiling_ns) is not int or ceiling_ns < 0:
        return "clock_uncertain"
    elapsed = _elapsed_total_ns(before, after)
    if isinstance(elapsed, _Unknown):
        return "boot_changed" if elapsed.issues == ("boot_changed",) else "clock_uncertain"
    return "expired" if elapsed >= ceiling_ns else "within"
