"""Shared native process and structured-result boundary; adapters own CLI syntax."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable
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
        self._process: subprocess.Popen[str] | None = None
        self._launch_allowed = launch_allowed
        self._stop_lock = RLock()
        self.stop_error: str | None = None

    def spawn(self, command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        with managed_launch_lock:
            if self.cancelled.is_set() or (
                self._launch_allowed is not None and not self._launch_allowed()
            ):
                self.cancelled.set()
                raise InvocationCancelled("Invocation cancelled before launch")
            if self._process is not None:
                raise RuntimeError("Managed invocation already launched")
            self._process = subprocess.Popen(
                command, start_new_session=os.name == "posix", **kwargs
            )
            return self._process

    def stop(self, timeout: float = 1.5) -> str:
        """TERM then KILL only our launch group, with bounded confirmation."""
        self.cancelled.set()
        with self._stop_lock:
            with managed_launch_lock:
                process = self._process
            if process is None:
                return "prevented"
            if os.name != "posix":
                self.stop_error = "Owned process-group termination is unsupported on this platform"
                return "unconfirmed"

            def exited() -> bool:
                process.poll()
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    return process.returncode is not None
                return False

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
                return "unconfirmed"
            except OSError as error:
                self.stop_error = f"{type(error).__name__} (errno {error.errno})"
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
    *,
    process_control: ManagedProcessControl | None = None,
) -> AgentRunResult:
    try:
        if process_control is not None:
            child = process_control.spawn(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_dir,
                env=subprocess_env_for_cli(command[0]),
                text=True,
            )
            try:
                stdout, stderr = child.communicate(prompt)
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
                env=subprocess_env_for_cli(command[0]),
                text=True,
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
    result = AgentRunResult(
        process.returncode,
        thread_id=native.thread_id,
        result=native.result,
        usage=native.usage,
        error=native.error
        or (
            process.stderr.strip() or process.stdout.strip() or f"{host} exited unsuccessfully."
            if process.returncode
            else None
        ),
    )
    return validate_agent_result(result, schema, host)
