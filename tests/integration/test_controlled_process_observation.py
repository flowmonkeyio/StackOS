"""Read-only native capability or explicit unsupported-ABI contract, on self."""

from __future__ import annotations

import builtins
import ctypes
import io
import json
import os
import platform
import socket
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from stackos import controlled_process_clock as clock
from stackos import controlled_process_identity as identity

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def forbid_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("native observation attempted an effect")

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


def test_known_native_clock_and_own_identity(request, monkeypatch):
    assert Path(clock.__file__).resolve() == ROOT / "stackos/controlled_process_clock.py"
    assert Path(identity.__file__).resolve() == ROOT / "stackos/controlled_process_identity.py"
    supported = clock._runtime_abi() == clock._SUPPORTED_ABI
    queried = []
    original = identity._read_bsdinfo

    def self_only(native, pid):
        queried.append(pid)
        return original(native, pid)

    monkeypatch.setattr(identity, "_read_bsdinfo", self_only)
    baseline_modules = set(sys.modules)
    requested_imports = []
    original_import = builtins.__import__

    def observe_import(name, globals=None, locals=None, fromlist=(), level=0):
        requested_imports.append(name)
        requested_imports.extend(f"{name}.{item}" for item in (fromlist or ()))
        return original_import(name, globals, locals, fromlist, level)

    with monkeypatch.context() as operation:
        operation.setattr(builtins, "__import__", observe_import)
        before, after = clock._sample_clock(), clock._sample_clock()
        pid_before = os.getpid()
        first = identity._inspect_process(pid_before)
        pid_after = os.getpid()
        second = identity._inspect_process(pid_after)
    operation_imports = set(requested_imports) | (set(sys.modules) - baseline_modules)
    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in operation_imports
        for prefix in ("stackos.config", "stackos.crypto")
    )
    reporter = request.config.pluginmanager.get_plugin("terminalreporter")
    assert reporter is not None
    reporter.write_line(
        "NATIVE_OBSERVATION "
        + json.dumps(
            {
                "supported": supported,
                "python": sys.version.split()[0],
                "system": platform.system(),
                "machine": platform.machine(),
                "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
                "modules": [clock.__file__, identity.__file__],
                "clock_before": asdict(before),
                "clock_after": asdict(after),
                "identity_before": asdict(first),
                "identity_after": asdict(second),
                "proc_bsdinfo_bytes": ctypes.sizeof(identity._ProcBSDInfo),
                "queried_pids": queried,
                "self_pids": [pid_before, pid_after],
            },
            sort_keys=True,
        )
    )
    if not supported:
        assert before.status == after.status == "unknown"
        assert first.status == second.status == "unknown"
        assert before.issues and after.issues and first.issues and second.issues
        assert identity._same_process(first, second) == "unknown"
        assert queried == []
        return

    assert ctypes.sizeof(identity._ProcBSDInfo) == 136
    assert before.status == "known", before
    assert after.status == "known", after
    assert before.boot_uuid == after.boot_uuid
    assert before.api_version == after.api_version == clock._API_VERSION
    assert before.runtime_abi == after.runtime_abi == clock._SUPPORTED_ABI
    assert isinstance(clock._elapsed_total_ns(before, after), int)
    assert after.awake_ns >= before.awake_ns
    assert first.status == "known", first
    assert second.status == "known", second
    assert queried == [pid_before, pid_before, pid_after, pid_after]
    assert identity._same_process(first, second) == "match"
    assert first.identity.pid == second.identity.pid == pid_before == pid_after
    assert first.identity.parent_pid == os.getppid()
    assert first.identity.pgid == os.getpgid(pid_before)
    assert first.identity.sid == os.getsid(pid_before)
    assert first.identity.boot_uuid == before.boot_uuid
