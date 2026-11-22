"""Structured native observation. Invocation lifetime belongs to the host service."""

from __future__ import annotations

import codecs
import json
import os
import sqlite3
import subprocess
from collections.abc import Callable, Mapping
from contextlib import closing, suppress
from datetime import datetime
from pathlib import Path
from threading import Thread
from time import monotonic
from typing import Any, BinaryIO
from uuid import UUID

from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.result import ModelObservation, normalize_model_observation, usage_snapshot


def _non_context_record(prefix: bytes) -> bool:
    """Recognize only the native envelope header, never types inside its payload."""
    try:
        # The bounded read can end inside a UTF-8 character in a large payload.
        text = codecs.utf_8_decode(prefix, "strict", False)[0].lstrip()
        if not text.startswith("{"):
            return False
        text = text[1:].lstrip()
        decoder = json.JSONDecoder()
        # Native records put type after optional timestamp/ordinal fields. An unknown
        # layout stays uncertain rather than searching arbitrary transcript data.
        for _ in range(3):
            key, end = decoder.raw_decode(text)
            text = text[end:].lstrip()
            if not text.startswith(":"):
                return False
            text = text[1:].lstrip()
            if key not in ("timestamp", "ordinal", "type"):
                return False
            value, end = decoder.raw_decode(text)
            text = text[end:].lstrip()
            if not text.startswith(","):
                return False
            if key == "type":
                return value in ("event_msg", "response_item")
            if (key == "timestamp" and not isinstance(value, str)) or (
                key == "ordinal" and type(value) is not int
            ):
                return False
            text = text[1:].lstrip()
    except (ValueError, RecursionError):
        pass
    return False


class CodexCounterReader:
    """Resolve one UUIDv7 rollout, then consume appended bytes, never transcript data.

    Codex's native thread index gives the exact rollout path by ID. File naming
    time can differ from UUID creation time. No historical session scan is used.
    Unproven layouts/identities simply have no live telemetry.
    """

    def __init__(
        self, session_id: str, project_dir: Path, *, home: Path | None = None, resumed: bool = False
    ):
        self.file: BinaryIO | None = None
        self.path: Path | None = None
        self.session_id = session_id
        self.project_dir = project_dir.resolve()
        self.resumed = resumed
        self.index: Path | None = None
        self.cursor = 0
        self.discard_line = False
        self.pending = b""
        self.model_observation: ModelObservation | None = None
        self.unattributed_usage = False
        try:
            identity = UUID(session_id)
            if identity.version != 7:
                return
            root = home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            self.index = root / "state_5.sqlite"
            self._open()
        except (OSError, ValueError, TypeError, OverflowError):
            self.close()

    def _open(self) -> None:
        if self.file is not None:
            return
        if self.path is None and self.index is not None:
            try:
                with closing(
                    sqlite3.connect(
                        self.index.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1
                    )
                ) as database:
                    row = database.execute(
                        "SELECT rollout_path FROM threads WHERE id = ?", (self.session_id,)
                    ).fetchone()
                if row:
                    self.path = Path(row[0])
                    self.index = None
            except (OSError, sqlite3.Error, TypeError):
                return
        if self.path is None:
            return
        try:
            handle = self.path.open("rb")
            self.file = handle
            line = handle.readline(1024 * 1024)
            if not line.endswith(b"\n"):
                handle.close()
                self.file = None
                return
            metadata = json.loads(line)
            payload = metadata.get("payload", {})
            if (
                metadata.get("type") != "session_meta"
                or payload.get("id") != self.session_id
                or Path(payload.get("cwd", "")).resolve() != self.project_dir
            ):
                self.close()
                return
            self.cursor = handle.seek(0, os.SEEK_END) if self.resumed else handle.tell()
        except FileNotFoundError:
            # thread.started can precede native rollout creation. The exact
            # resolved path is retained; no rediscovery or historical scan.
            return
        except (OSError, ValueError, TypeError):
            self.close()

    def read(self) -> list[tuple[str, dict[str, Any]]]:
        self._open()
        if self.file is None:
            return []
        # Preserve source checkpoints across a delayed collector read. Trimming
        # keeps a baseline, never moves the collapsed prefix into the last point.
        samples: list[tuple[str, dict[str, Any]]] = []
        # Bounded working memory even when a native transcript line is huge.
        # Only counter/context metadata events are decoded; transcript payloads
        # never supply counters or model identity.
        while chunk := self.file.readline(64 * 1024):
            self.cursor = self.file.tell()
            if self.discard_line:
                self.discard_line = not chunk.endswith(b"\n")
                continue
            line = self.pending + chunk
            self.pending = b""
            if len(line) > 64 * 1024 or (len(line) == 64 * 1024 and not chunk.endswith(b"\n")):
                # Large tool output is unrelated to model context. Unreadable
                # context or an unrecognized envelope still leaves a real gap.
                if self.model_observation is None or not _non_context_record(line):
                    # Before the first context even an ordinary event could
                    # hide earlier usage; preserve that attribution gap.
                    self._observe_model({})
                self.discard_line = not chunk.endswith(b"\n")
                continue
            if not chunk.endswith(b"\n"):
                self.pending = line
                break
            if b'"token_count"' not in line and b'"turn_context"' not in line:
                continue
            try:
                event = json.loads(line)
                payload = event.get("payload", {})
                if event.get("type") == "turn_context":
                    self._observe_model(payload)
                    continue
                if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                counters = payload.get("info", {}).get("total_token_usage", {})
                stamp = event.get("timestamp")
                measured_at = datetime.fromisoformat(stamp)
                if measured_at.utcoffset() is None:
                    continue
                usage = usage_snapshot(
                    "codex.turn",
                    "session",
                    counters.get("input_tokens"),
                    counters.get("output_tokens"),
                    counters.get("total_tokens"),
                    cache_read=counters.get("cached_input_tokens"),
                    reasoning=counters.get("reasoning_output_tokens"),
                )
                if usage:
                    if self.model_observation is None and usage["snapshot"]["total"] > 0:
                        self.unattributed_usage = True
                    if any(
                        datetime.fromisoformat(at) == measured_at and previous == usage
                        for at, previous in samples
                    ):
                        continue
                    samples.append((stamp, usage))
                    samples.sort(key=lambda point: datetime.fromisoformat(point[0]))
                    del samples[:-120]
            except (ValueError, TypeError, AttributeError):
                if b'"turn_context"' in line:
                    # A complete but unreadable context record can hide a
                    # model change. Later valid contexts cannot fill that gap.
                    self._observe_model({})
                continue
        return samples

    def _observe_model(self, payload: Any) -> None:
        payload = payload if isinstance(payload, dict) else {}
        observation: ModelObservation = {
            "status": "observed",
            "model": payload.get("model"),
            "source": "codex.turn_context",
            "session_id": self.session_id,
        }
        valid = normalize_model_observation(
            observation, session_id=self.session_id, host_key="codex"
        )
        cwd = payload.get("cwd")
        try:
            same_directory = (
                isinstance(cwd, str) and bool(cwd) and Path(cwd).resolve() == self.project_dir
            )
        except (OSError, ValueError, RuntimeError):
            same_directory = False
        if (
            valid is None
            or not same_directory
            or self.unattributed_usage
            or (self.model_observation is not None and self.model_observation != observation)
        ):
            observation["status"] = "ambiguous"
            observation["model"] = None
        self.model_observation = observation

    def close(self) -> None:
        if self.pending:
            # Partial live records are allowed to finish. Once observation ends,
            # an unfinished record is a permanent gap in the context evidence.
            self._observe_model({})
        if self.file is not None:
            self.file.close()
        self.file = None
        self.path = None
        self.index = None
        self.pending = b""


class NativeStreamError(Exception):
    """Post-launch stream failure with the reaped process's available output."""

    def __init__(
        self, process: subprocess.CompletedProcess[str], cause: Exception, host: str
    ) -> None:
        self.process = process
        self.cause = cause
        super().__init__(f"{host} stream failed ({type(cause).__name__}): {cause}")


class CodexStreamError(NativeStreamError):
    def __init__(self, process: subprocess.CompletedProcess[str], cause: Exception) -> None:
        super().__init__(process, cause, "Codex")


def run_codex_process(
    command: list[str],
    *,
    input: str,
    cwd: Path,
    env: dict[str, str],
    on_session: Callable[[str], None],
    process_control: ManagedProcessControl | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Preserve native stdout/stderr/result behavior while exposing thread.started early."""

    def observe(line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        if (
            isinstance(event, dict)
            and event.get("type") == "thread.started"
            and isinstance(event.get("thread_id"), str)
        ):
            on_session(event["thread_id"])

    try:
        return run_stream_process(
            command,
            input=input,
            cwd=cwd,
            env=env,
            on_output_line=observe,
            process_control=process_control,
            pass_fds=pass_fds,
        )
    except NativeStreamError as exc:
        raise CodexStreamError(exc.process, exc.cause) from exc.cause


def run_stream_process(
    command: list[str],
    *,
    input: str,
    cwd: Path,
    env: Mapping[str, str],
    on_output_line: Callable[[str], None],
    process_control: ManagedProcessControl | None = None,
    pass_fds: tuple[int, ...] = (),
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Observe native lines within the existing owned process lifetime."""
    deadline = monotonic() + timeout if timeout is not None else None
    process = (process_control.spawn if process_control else subprocess.Popen)(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
        pass_fds=pass_fds,
    )
    output: list[str] = []
    errors: list[str] = []
    read_failures: list[Exception] = []

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.append(line)
            on_output_line(line)

    def read_errors() -> None:
        assert process.stderr is not None
        errors.append(process.stderr.read())

    def read_safely(read: Callable[[], None]) -> None:
        try:
            read()
        except Exception as exc:
            read_failures.append(exc)
            # Preserve native decoding/I/O failures rather than accepting a
            # partial transcript, and do not leave a writer blocked on the pipe.
            if process.poll() is None:
                if process_control is not None:
                    process_control.stop()
                else:
                    with suppress(OSError):
                        process.terminate()

    stdout = Thread(target=lambda: read_safely(read_output), daemon=True)
    stderr = Thread(target=lambda: read_safely(read_errors), daemon=True)
    stdout.start()
    stderr.start()
    try:
        assert process.stdin is not None
        try:
            process.stdin.write(input)
        except BrokenPipeError:
            pass
        finally:
            with suppress(BrokenPipeError):
                process.stdin.close()
        process.wait(timeout=max(0, deadline - monotonic()) if deadline is not None else None)
        for reader in (stdout, stderr):
            reader.join(max(0, deadline - monotonic()) if deadline is not None else None)
        if stdout.is_alive() or stderr.is_alive():
            assert timeout is not None
            raise subprocess.TimeoutExpired(command, timeout)
    except subprocess.TimeoutExpired:
        retirement = process_control.stop() if process_control else "unconfirmed"
        read_failures.append(RuntimeError(f"external deadline; retirement {retirement}"))
    except (OSError, UnicodeError) as exc:
        read_failures.append(exc)
    finally:
        if process.poll() is None:
            if process_control is not None:
                process_control.stop()
            else:
                process.terminate()
                process.wait()
        stdout.join(2)
        stderr.join(2)
        if process.stdout and not stdout.is_alive():
            process.stdout.close()
        if process.stderr and not stderr.is_alive():
            process.stderr.close()
    completed = subprocess.CompletedProcess(
        command, process.returncode, "".join(output), "".join(errors)
    )
    if read_failures:
        raise NativeStreamError(completed, read_failures[0], "Native") from read_failures[0]
    return completed
