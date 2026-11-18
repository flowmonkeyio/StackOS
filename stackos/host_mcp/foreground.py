"""Keep a managed native harness on the operator's foreground terminal."""

from __future__ import annotations

import fcntl
import os
import re
import signal
import subprocess
import termios
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from stackos.host_mcp.accounts import (
    HostAccountError,
    HostAccounts,
    ResolvedAccountSelection,
    _private_directory,
    _private_fd,
)

_NATIVE_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_OBSERVATION_INTERVAL_SECONDS = 0.25
Phase = Literal["closed", "requested", "pending", "confirmed", "failed"]


class ForegroundError(Exception):
    """A safe local operator diagnostic."""


@dataclass(frozen=True)
class ForegroundRequest:
    executable: str
    cwd: Path
    selection: ResolvedAccountSelection
    session_id: str | None
    native_args: tuple[str, ...] = field(repr=False)
    reopening: bool = False


@dataclass(frozen=True)
class ForegroundObservation:
    session_id: str | None = None
    principal_key: tuple[str, str | None] | None = field(default=None, repr=False)
    settled: bool = False


@dataclass(frozen=True)
class ForegroundLaunch:
    argv: tuple[str, ...] = field(repr=False)
    env: Mapping[str, str] | None = field(repr=False)
    observe: Callable[[int, int | None], ForegroundObservation] = field(repr=False)
    decide_input: Callable[[bool], None] = field(repr=False)


class ForegroundAdapter(Protocol):
    def prepare(self, request: ForegroundRequest) -> ForegroundLaunch:
        """Build a bounded launch for the selected profile and exact native session.

        Verify exact existing sessions and exclude active writers before adoption.
        Reject native arguments that override the pinned identity, session or cwd.
        On reopening, retain configuration and omit initial prompts or work inputs.
        Observe only evidence attributable to the supplied child PID. An observation
        is a bounded local read. Mark settled only after native retirement, with
        tool, approval and subagent work resolved and resumable input preserved.
        A terminal native error can qualify; process exit alone cannot.

        Keep input and turn dispatch closed from process start until decide_input(True).
        A failed admission keeps the gate closed. A negative decision retires the
        gated startup without starting any work.
        Refuse managed launch during preparation if the stock host cannot enforce
        this boundary on the original terminal.
        """
        ...


class ForegroundStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Phase = "closed"
    session_id: str | None = None
    requested_revision: int = 0
    confirmed_revision: int | None = None


class ForegroundSlot:
    """A user-local lifetime lock and safe status for one managed native host."""

    def __init__(self, data_dir: Path, host_key: str) -> None:
        self.root = data_dir / "host-foreground"
        self.lock_path = self.root / f"{host_key}.lock"
        self.status_path = self.root / f"{host_key}.json"

    @contextmanager
    def acquire(self) -> Iterator[int]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private_directory(self.root, "unsafe_foreground_state")
        fd = _private_fd(self.lock_path, os.O_RDWR | os.O_CREAT)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ForegroundError(
                    "A foreground harness is already running for this host."
                ) from None
            yield fd
        finally:
            os.close(fd)

    def _active(self) -> bool:
        try:
            fd = _private_fd(self.lock_path, os.O_RDONLY)
        except FileNotFoundError:
            return False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
        finally:
            os.close(fd)

    def status(self, requested_revision: int) -> ForegroundStatus:
        try:
            _private_directory(self.root, "unsafe_foreground_state")
            active = self._active()
            with os.fdopen(_private_fd(self.status_path, os.O_RDONLY), "rb") as file:
                status = ForegroundStatus.model_validate_json(file.read(4096))
        except FileNotFoundError:
            return ForegroundStatus(requested_revision=requested_revision)
        except ValidationError:
            raise ForegroundError("Foreground status is unreadable.") from None
        if status.phase in {"requested", "pending", "confirmed"}:
            if not active:
                status.phase = "failed"
            elif status.requested_revision != requested_revision:
                status.phase = "pending"
        status.requested_revision = requested_revision
        return status

    def publish(self, status: ForegroundStatus) -> None:
        with NamedTemporaryFile(dir=self.root, delete=False) as file:
            try:
                file.write(status.model_dump_json().encode())
                file.close()
                os.replace(file.name, self.status_path)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(file.name)


def require_foreground_terminal() -> None:
    try:
        if (
            os.isatty(0)
            and os.isatty(1)
            and os.ttyname(0) == os.ttyname(1)
            and os.tcgetpgrp(0) == os.getpgrp()
        ):
            return
    except OSError:
        pass
    raise ForegroundError("Run the harness from the foreground of an interactive terminal.")


def run_foreground(
    *,
    host_key: str,
    executable: str,
    adapter: ForegroundAdapter,
    accounts: HostAccounts,
    slot: ForegroundSlot,
    native_args: tuple[str, ...],
    session_id: str | None,
    notice: Callable[[str], None],
) -> int:
    if session_id is not None and not _NATIVE_ID.fullmatch(session_id):
        raise ForegroundError("Supply an exact native conversation ID with --session.")
    cwd = Path.cwd()
    with slot.acquire() as lock_fd:
        status = ForegroundStatus(session_id=session_id)
        reopening = False
        try:
            selection = accounts.resolve(host_key)
            while True:
                status.phase = "requested"
                status.requested_revision = selection.desired.revision
                slot.publish(status)
                request = ForegroundRequest(
                    executable, cwd, selection, status.session_id, native_args, reopening
                )
                try:
                    launch = adapter.prepare(request)
                except ForegroundError:
                    raise
                except Exception:
                    raise ForegroundError("Native launch preparation failed.") from None
                if not launch.argv or launch.argv[0] != executable:
                    raise ForegroundError("The native launch did not use the selected executable.")
                if accounts.resolve(host_key).desired != selection.desired:
                    raise ForegroundError(
                        "Selection changed during preparation. Retry with the current selection."
                    )

                require_foreground_terminal()
                code, settled = _run_native(launch, request, lock_fd, status, slot, notice)
                if code < 0 or not settled:
                    status.phase = "failed"
                    slot.publish(status)
                    _show_recovery(notice, host_key, status.session_id)
                    return 128 - code if code < 0 else code or 1

                desired = accounts.resolve(host_key)
                if desired.desired == selection.desired:
                    status.phase = "failed" if code else "closed"
                    slot.publish(status)
                    if code:
                        _show_recovery(notice, host_key, status.session_id)
                    return code
                notice(
                    f"Selection requested at revision {desired.desired.revision}. "
                    "Reopening the exact conversation."
                )
                selection = desired
                reopening = True
        except (ForegroundError, HostAccountError, OSError):
            status.phase = "failed"
            try:
                slot.publish(status)
            finally:
                _show_recovery(notice, host_key, status.session_id)
            raise


def _show_recovery(notice: Callable[[str], None], host_key: str, session_id: str | None) -> None:
    notice("Foreground harness failed or retired without a verified switch boundary.")
    if session_id is not None:
        notice(
            "Select the intended account, then recover the exact conversation with "
            f"`stackos harness run {host_key} --session {session_id}`. "
            "Pass your original native options after `--`."
        )


def _run_native(
    launch: ForegroundLaunch,
    request: ForegroundRequest,
    lock_fd: int,
    status: ForegroundStatus,
    slot: ForegroundSlot,
    notice: Callable[[str], None],
) -> tuple[int, bool]:
    child: subprocess.Popen[bytes] | None = None
    input_admitted = False
    stopping: int | None = None

    def on_signal(signum: int, _frame: object) -> None:
        nonlocal stopping
        # The foreground process group already delivers terminal interrupts to the child.
        if signum in (signal.SIGTERM, signal.SIGHUP):
            stopping = signum
            if child is not None:
                child.send_signal(signum)

    signals = (signal.SIGINT, signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP)
    original = {sig: signal.signal(sig, on_signal) for sig in signals}
    try:
        # Inherit the real TTY and process group. The child also retains the writer
        # fence if the supervisor unexpectedly disappears while it is still alive.
        child = subprocess.Popen(launch.argv, env=launch.env, pass_fds=(lock_fd,))
        if stopping is not None:
            child.send_signal(stopping)
        status.phase = "pending"
        slot.publish(status)
        while True:
            try:
                code = child.wait(timeout=_OBSERVATION_INTERVAL_SECONDS)
            except subprocess.TimeoutExpired:
                if status.phase == "pending" and stopping is None:
                    previous_session = status.session_id
                    try:
                        observed = _read_native_observation(launch, child.pid, None, status)
                        if _matches_selection(observed, request) and stopping is None:
                            launch.decide_input(True)
                            input_admitted = True
                            status.confirmed_revision = status.requested_revision
                            status.phase = "confirmed"
                    except ForegroundError as exc:
                        status.phase = "failed"
                        try:
                            notice(str(exc))
                        finally:
                            launch.decide_input(False)
                    if status.phase != "pending" or status.session_id != previous_session:
                        slot.publish(status)
                continue
            if stopping is not None or code < 0 or status.phase == "failed":
                # A signal can arrive before the first live observation. Capture a
                # published ID for recovery without confirming or starting another child.
                with suppress(ForegroundError):
                    _read_native_observation(launch, child.pid, code, status)
                return (-stopping if stopping is not None else code or 1), False
            observed = _read_native_observation(launch, child.pid, code, status)
            settled = input_admitted and _matches_selection(observed, request) and observed.settled
            return (-stopping, False) if stopping is not None else (code, settled)
    finally:
        try:
            if child is not None:
                child.wait()
                if not input_admitted:
                    # Bytes typed into an unadmitted startup belong to that launch.
                    # Discard them before returning the terminal to the shell.
                    with suppress(OSError, termios.error):
                        if os.tcgetpgrp(0) == os.getpgrp():
                            termios.tcflush(0, termios.TCIFLUSH)
        finally:
            for sig, handler in original.items():
                signal.signal(sig, handler)


def _read_native_observation(
    launch: ForegroundLaunch,
    pid: int,
    code: int | None,
    status: ForegroundStatus,
) -> ForegroundObservation:
    try:
        observed = launch.observe(pid, code)
    except ForegroundError:
        raise
    except Exception:
        raise ForegroundError("Native conversation observation failed.") from None
    if observed.session_id is None:
        return observed
    if not _NATIVE_ID.fullmatch(observed.session_id) or status.session_id not in (
        None,
        observed.session_id,
    ):
        raise ForegroundError("Native launch did not retain the exact conversation.")
    status.session_id = observed.session_id
    return observed


def _matches_selection(observed: ForegroundObservation, request: ForegroundRequest) -> bool:
    if observed.session_id is None:
        return False
    principal = request.selection.principal_key
    if principal is None:
        return True
    if observed.principal_key is None:
        return False
    if observed.principal_key != principal:
        raise ForegroundError("Native launch did not confirm the selected account.")
    return True
