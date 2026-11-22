"""Shared native process and structured-result boundary; adapters own CLI syntax."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from threading import Event, RLock
from time import monotonic, sleep
from typing import Any

from stackos.host_mcp.discovery import subprocess_env_for_cli
from stackos.host_mcp.result import AgentRunResult, validate_agent_result

# Only short launch/abort/acceptance boundaries hold this lock, never native I/O.
managed_launch_lock = RLock()


class InvocationCancelled(Exception):
    """The recorded invocation was fenced before native launch."""


class ManagedProcessControl:
    """Private handle for one scoped launch; never reconstruct ownership from a PID."""

    def __init__(self, launch_allowed: Callable[[], bool] | None = None):
        self.cancelled = Event()
        self._closed = Event()
        self.account_switch = False
        self._process: subprocess.Popen[str] | None = None
        self._launch_allowed = launch_allowed
        self._stop_lock = RLock()
        self.stop_error: str | None = None

    def spawn(self, command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        with managed_launch_lock:
            if (
                self.cancelled.is_set()
                or self._closed.is_set()
                or (self._launch_allowed is not None and not self._launch_allowed())
            ):
                self.cancelled.set()
                raise InvocationCancelled("Invocation cancelled before launch")
            if self._process is not None:
                raise RuntimeError("Managed invocation already launched")
            self._process = subprocess.Popen(
                command, start_new_session=os.name == "posix", **kwargs
            )
            return self._process

    @staticmethod
    def _group_exit_observation(process: subprocess.Popen[str]) -> tuple[bool, str | None]:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return process.returncode is not None, None
        except PermissionError as error:
            return False, f"{type(error).__name__} (errno {error.errno})"
        return False, None

    def retirement_status(self) -> str:
        """Observe only our exact handle/group; a returned result is not exit proof."""
        with self._stop_lock:
            with managed_launch_lock:
                process = self._process
            if process is None:
                return "prevented"
            if os.name != "posix":
                return "unconfirmed"
            try:
                exited, _ = self._group_exit_observation(process)
            except OSError:
                return "unconfirmed"
            return "stopped" if exited else "unconfirmed"

    def stop(self, timeout: float = 1.5, *, cancel: bool = True) -> str:
        """TERM then KILL only our launch group, with bounded confirmation."""
        # A completed root may leave descendants to retire. Cleanup still fences
        # launch, but must not manufacture cancellation of its completed result.
        self._closed.set()
        if cancel:
            self.cancelled.set()
        with self._stop_lock:
            self.stop_error = None
            with managed_launch_lock:
                process = self._process
                # Trust our handle, not an adapter's returned exit code. A live
                # or failed root cannot become successful through cleanup.
                if not cancel and process is not None and process.poll() != 0:
                    self.cancelled.set()
            if process is None:
                return "prevented"
            if os.name != "posix":
                self.stop_error = "Owned process-group termination is unsupported on this platform"
                return "unconfirmed"

            last_probe_error: str | None = None

            def exited() -> bool:
                nonlocal last_probe_error
                confirmed, probe_error = self._group_exit_observation(process)
                if probe_error:
                    # Keep the deadline through transient permission failures;
                    # only ESRCH plus root exit confirms group retirement.
                    last_probe_error = probe_error
                return confirmed

            try:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    if exited():
                        return "stopped"
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, sig)
                    deadline = monotonic() + timeout
                    while monotonic() < deadline:
                        if exited():
                            return "stopped"
                        sleep(0.02)
                if exited():
                    return "stopped"
                self.stop_error = "Owned process-group exit was not observed before the deadline"
                if last_probe_error:
                    self.stop_error += f"; last group probe: {last_probe_error}"
                return "unconfirmed"
            except OSError as error:
                self.stop_error = (
                    f"Owned process-group {process.pid}: "
                    f"{type(error).__name__} (errno {error.errno})"
                )
                return "unconfirmed"


def json_object(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def structured_prompt(prompt: str, schema: dict[str, Any]) -> str:
    return (
        prompt
        + "\nReturn only a JSON object matching this schema: "
        + json.dumps(schema)
        + "\nIf native permissions deny required work, report that failure in the result and stop."
    )


def execute_json_agent(
    command: list[str],
    project_dir: Path,
    prompt: str,
    schema: dict[str, Any],
    host: str,
    decode: Callable[[str], AgentRunResult],
    env: Mapping[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
    /,
    *,
    process_control: ManagedProcessControl | None = None,
    timeout: float | None = None,
    stderr_error: Callable[[str], str | None] | None = None,
    on_output_line: Callable[[str], None] | None = None,
    usage_requires_complete_output: bool = False,
) -> AgentRunResult:
    native_env = subprocess_env_for_cli(command[0]) if env is None else env
    # A native print timeout need not bound authentication or child startup.
    # Reuse the same owned group boundary for callers without a managed scope.
    if timeout is not None and process_control is None:
        process_control = ManagedProcessControl()
    process_error = None
    try:
        if on_output_line is not None:
            from stackos.host_mcp.progress import NativeStreamError, run_stream_process

            try:
                process = run_stream_process(
                    command,
                    input=prompt,
                    cwd=project_dir,
                    env=native_env,
                    on_output_line=on_output_line,
                    process_control=process_control,
                    pass_fds=pass_fds,
                    timeout=timeout,
                )
            except NativeStreamError as exc:
                process = exc.process
                process_error = str(exc)
        elif process_control is not None:
            child = process_control.spawn(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_dir,
                env=native_env,
                text=True,
                pass_fds=pass_fds,
            )
            try:
                if timeout is None:
                    stdout, stderr = child.communicate(prompt)
                else:
                    try:
                        stdout, stderr = child.communicate(prompt, timeout=timeout)
                    except subprocess.TimeoutExpired as exc:
                        retirement = process_control.stop()
                        process_error = (
                            f"{host} exceeded its external deadline; retirement {retirement}."
                        )
                        try:
                            stdout, stderr = child.communicate(timeout=2)
                        except subprocess.TimeoutExpired as drain_exc:
                            # An unconfirmed descendant may still hold a pipe.
                            # Keep only observed bytes and return bounded failure.
                            raw_stdout = drain_exc.output or exc.output or ""
                            raw_stderr = drain_exc.stderr or exc.stderr or ""
                            stdout = (
                                raw_stdout.decode("utf-8", errors="replace")
                                if isinstance(raw_stdout, bytes)
                                else raw_stdout
                            )
                            stderr = (
                                raw_stderr.decode("utf-8", errors="replace")
                                if isinstance(raw_stderr, bytes)
                                else raw_stderr
                            )
                            for pipe in (child.stdin, child.stdout, child.stderr):
                                if pipe is not None:
                                    pipe.close()
            except BaseException:
                process_control.stop()
                raise
            process = subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
        else:
            process = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                check=False,
                cwd=project_dir,
                env=native_env,
                text=True,
                pass_fds=pass_fds,
            )
    except InvocationCancelled as exc:
        return AgentRunResult(None, error=str(exc))
    except OSError as exc:
        return AgentRunResult(None, error=f"Could not launch {host}: {exc}")
    try:
        native = decode(process.stdout)
    except (ValueError, KeyError, TypeError) as exc:
        detail = process.stderr.strip() or process.stdout.strip() or str(exc)
        return AgentRunResult(process.returncode, error=f"Invalid {host} result: {detail}")
    # A host may use this hook to revoke unsupported counters even when another
    # error already explains the result. Diagnostics must always be consumed.
    diagnostic_error = stderr_error(process.stderr) if stderr_error is not None else None
    result = AgentRunResult(
        process.returncode,
        thread_id=native.thread_id,
        result=native.result,
        # Some native formats signal incomplete counters only on stderr. A
        # failed output read cannot establish completeness for those formats.
        usage=None if usage_requires_complete_output and process_error else native.usage,
        model_observation=native.model_observation,
        error=process_error
        or native.error
        or diagnostic_error
        or (
            process.stderr.strip() or process.stdout.strip() or f"{host} exited unsuccessfully."
            if process.returncode
            else None
        ),
    )
    return validate_agent_result(result, schema, host)
