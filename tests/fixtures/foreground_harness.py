"""Isolated native harness used by the foreground CLI's actual PTY tests."""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import signal
import subprocess
import sys
import termios
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from stackos.cli import app
from stackos.host_mcp import service
from stackos.host_mcp.accounts import HostAccounts, NativeAccountProfile, NativeIdentity
from stackos.host_mcp.foreground import ForegroundLaunch, ForegroundObservation, ForegroundRequest

ROOT = Path(os.environ["STACKOS_DATA_DIR"])
SESSION = "00000000-0000-4000-8000-000000000880"
ENROLLMENT = "00000000-0000-4000-8000-000000000881"


class FixtureAccounts:
    def read_identity(self, profile: NativeAccountProfile) -> NativeIdentity:
        principal = "b" if profile.directory else "a"
        return NativeIdentity(principal=principal, email=f"{principal}@fixture.invalid")

    def start_login(self, profile: NativeAccountProfile) -> None:
        pass

    def discard_login(self, profile: NativeAccountProfile) -> bool:
        return True


class FixtureForeground:
    def prepare(self, request: ForegroundRequest) -> ForegroundLaunch:
        session = request.session_id or SESSION
        transcript = ROOT / f"{session}.history"
        if request.session_id is not None and not transcript.is_file():
            raise ValueError("unknown exact session")
        principal = request.selection.principal_key or ("a", None)
        launch_id = uuid4()
        observation = ROOT / f"observation-{launch_id}"
        input_decision = ROOT / f"admission-{launch_id}"
        os.mkfifo(input_decision, 0o600)
        env = dict(
            os.environ,
            FIXTURE_PRINCIPAL=principal[0],
            FIXTURE_OBSERVATION=str(observation),
            FIXTURE_INPUT_DECISION=str(input_decision),
        )

        def observe(pid: int, returncode: int | None) -> ForegroundObservation:
            if returncode is None and (ROOT / "observe-only-on-exit").exists():
                return ForegroundObservation()
            if (ROOT / f"hold-{principal[0]}-observation").exists():
                return ForegroundObservation()
            if not observation.exists():
                return ForegroundObservation()
            row = json.loads(observation.read_text())
            assert row["pid"] == pid
            return ForegroundObservation(
                session_id=row["session"],
                principal_key=(row["principal"], None),
                settled=(returncode == row.get("settled_exit") and returncode is not None),
            )

        def decide_input(accepted: bool) -> None:
            if accepted and (ROOT / "fail-admission").exists():
                with (ROOT / "host-accounts" / ".lock").open("rb") as account_lock:
                    try:
                        fcntl.flock(account_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        (ROOT / "selection-fenced").touch()
                raise OSError("synthetic admission channel failure")
            if not accepted:
                (ROOT / "retire-requested").touch()
                if (ROOT / "fail-retirement").exists():
                    raise OSError("synthetic retirement channel failure")
            fd = os.open(input_decision, os.O_WRONLY | os.O_NONBLOCK)
            with os.fdopen(fd, "wb", buffering=0) as gate:
                gate.write(b"1" if accepted else b"0")

        return ForegroundLaunch(
            (request.executable, "-E", __file__, "native", session, *request.native_args),
            env,
            observe,
            decide_input,
        )


def native(session: str, *args: str) -> int:
    transcript = ROOT / f"{session}.history"
    principal = os.environ["FIXTURE_PRINCIPAL"]
    if principal == "b" and (ROOT / "refuse-b").exists():
        print("native rejected the exact resume", flush=True)
        return 4
    if principal == "b" and (ROOT / "cached-a").exists():
        principal = "a"
    transcript.touch()
    draft_file = ROOT / "draft"
    observation = Path(os.environ["FIXTURE_OBSERVATION"])
    row = {"pid": os.getpid(), "session": session, "principal": principal}

    def publish() -> None:
        temporary = observation.with_suffix(".tmp")
        temporary.write_text(json.dumps(row))
        temporary.replace(observation)

    def interrupt(signum, _frame):
        print("native received interrupt", flush=True)
        sys.exit(128 + signum)

    signal.signal(signal.SIGINT, interrupt)
    if (ROOT / "fail-retirement").exists():
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if (ROOT / "disable-echo-during-startup").exists():
        original_modes = termios.tcgetattr(0)
        startup_modes = termios.tcgetattr(0)
        startup_modes[3] &= ~termios.ECHO
        termios.tcsetattr(0, termios.TCSANOW, startup_modes)
        atexit.register(termios.tcsetattr, 0, termios.TCSANOW, original_modes)
    admission_fd = os.open(os.environ["FIXTURE_INPUT_DECISION"], os.O_RDWR)
    publish()
    print(
        "native "
        + json.dumps(
            {
                **row,
                "tty": os.ttyname(0),
                "foreground": os.getpgrp() == os.tcgetpgrp(0),
                "echo": bool(termios.tcgetattr(0)[3] & termios.ECHO),
                "cwd": os.getcwd(),
                "args": args,
                "mcp": os.environ.get("FIXTURE_MCP"),
                "history": transcript.read_text(),
                "draft": draft_file.read_text() if draft_file.exists() else None,
            }
        ),
        flush=True,
    )
    with os.fdopen(admission_fd, "rb", buffering=0) as gate:
        if gate.read(1) != b"1":
            return 1
    tool = None
    for line in sys.stdin:
        match line.strip():
            case "commit":
                with transcript.open("a") as handle:
                    handle.write("initial-marker\n")
                with (ROOT / "effects").open("a") as handle:
                    handle.write("committed\n")
                print("effect committed", flush=True)
            case "quota":
                tool = subprocess.Popen(
                    [
                        sys.executable,
                        "-E",
                        "-c",
                        "import signal, sys\n"
                        "signal.signal(signal.SIGINT, signal.SIG_DFL)\n"
                        "print('ready', flush=True)\n"
                        "sys.stdin.read()\n",
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
                assert tool.stdout is not None and tool.stdout.readline() == b"ready\n"
                draft_file.write_text("unsent draft")
                print('native_error {"type":"rate_limit_exceeded","status":429}', flush=True)
                print(f"tool and approval still pending {tool.pid}", flush=True)
            case "unsettled":
                return 75
            case "finish":
                result = 75 if tool is not None else 0
                if tool is not None:
                    tool.communicate(timeout=3)
                    assert tool.returncode == 0
                row["settled_exit"] = result
                publish()
                return result
            case "continue":
                with transcript.open("a") as handle:
                    handle.write(f"continued-as-{principal}\n")
                print(f"continued as {principal}", flush=True)
    return 1


def main() -> None:
    adapters = {"codex": FixtureAccounts()}
    accounts = HostAccounts(ROOT, adapters)
    mode, *args = sys.argv[1:]
    if mode == "native":
        sys.exit(native(*args))
    if mode == "shell":
        original_modes = termios.tcgetattr(0)
        result = subprocess.run([sys.executable, "-E", __file__, "cli", *args], check=False)
        print(f"shell modes restored {termios.tcgetattr(0) == original_modes}", flush=True)
        print(f"shell returned {result.returncode}", flush=True)
        print("shell received " + sys.stdin.readline(), end="", flush=True)
        sys.exit(result.returncode)
    if mode == "setup":
        a = accounts.adopt("codex")
        accounts.add("codex", ENROLLMENT)
        b = accounts.complete("codex", ENROLLMENT)
        print(json.dumps({"a": a.account_ref, "b": b.account_ref}))
        return
    if mode == "select":
        print(accounts.select("codex", args[0], int(args[1])).revision)
        return

    definitions = service._definitions()
    service._definitions = lambda: tuple(
        replace(
            item,
            account_adapter=adapters["codex"],
            foreground_binary=lambda: sys.executable,
            foreground_adapter=FixtureForeground(),
        )
        if item.policy.host_key == "codex"
        else item
        for item in definitions
    )
    app(args=args)


if __name__ == "__main__":
    main()
