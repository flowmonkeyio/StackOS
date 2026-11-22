"""The native status-line read sends no input and retains only bounded plan data."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic

import pytest

from stackos.host_mcp import subscription_antigravity as antigravity


@pytest.mark.parametrize(
    "plan", ["Antigravity Starter Quota", "Pro", None, 1, {}, "", "x" * 121, "Pro\nsecret"]
)
def test_status_callback_retains_only_valid_plan(tmp_path, plan):
    target = tmp_path / "plan.json"
    result = subprocess.run(
        [sys.executable, "-c", antigravity._PLAN_CALLBACK, str(target)],
        input=json.dumps(
            {"plan_tier": plan, "email": "private-identity-canary", "session_id": "private-session"}
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout == result.stderr == ""
    if isinstance(plan, str) and 0 < len(plan) <= 120 and plan.isprintable():
        assert json.loads(target.read_text()) == plan
    else:
        assert not target.exists()


@pytest.mark.skipif(os.name != "posix", reason="Native plan capture uses a POSIX terminal")
@pytest.mark.parametrize("mode", ["success", "absent", "malformed", "flood", "exit"])
def test_native_plan_capture_bounds_and_retires_its_process(monkeypatch, tmp_path, mode):
    executable = tmp_path / "agy"
    marker = tmp_path / "invocation.json"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, select, shlex, subprocess, sys, threading\n"
        "assert sys.argv[1:4] == ['--mode', 'plan', '--settings']\n"
        "assert all(os.isatty(fd) for fd in (0, 1, 2))\n"
        "assert not select.select([0], [], [], 0.05)[0], 'Unexpected terminal input'\n"
        "settings = json.loads(sys.argv[4])\n"
        "assert list(settings) == ['statusLine']\n"
        "command = settings['statusLine']['command']\n"
        "target = pathlib.Path(shlex.split(command)[-1])\n"
        f"pathlib.Path({str(marker)!r}).write_text("
        "json.dumps({'cwd': os.getcwd(), 'target': str(target)}))\n"
        + (
            "subprocess.run(command, shell=True, check=True, input=json.dumps({"
            "'plan_tier': 'Antigravity Starter Quota', "
            "'email': 'private-identity-canary'}), text=True)\n"
            if mode == "success"
            else "target.write_text('{')\n"
            if mode == "malformed"
            else "os.write(1, b'x' * 1048577)\n"
            if mode == "flood"
            else "sys.exit(1)\n"
            if mode == "exit"
            else ""
        )
        + "threading.Event().wait()\n"
    )
    executable.chmod(0o700)
    processes = []
    spawn = antigravity.ManagedProcessControl.spawn

    def observed_spawn(control, *args, **kwargs):
        process = spawn(control, *args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(antigravity.ManagedProcessControl, "spawn", observed_spawn)
    start = monotonic()
    plan = antigravity._read_plan(str(executable), deadline=start + 2)
    assert plan == ("Antigravity Starter Quota" if mode == "success" else None)
    assert monotonic() - start < 4
    assert len(processes) == 1 and processes[0].poll() is not None
    invocation = json.loads(marker.read_text())
    assert not Path(invocation["cwd"]).exists()
    assert not Path(invocation["target"]).exists()


def test_expired_budget_never_launches_plan_read(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Plan subprocess launched after quota read exhausted the budget")

    monkeypatch.setattr(antigravity.ManagedProcessControl, "spawn", unexpected)
    assert antigravity._read_plan("unused", deadline=monotonic() - 1) is None
