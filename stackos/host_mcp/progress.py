"""Structured native observation. Invocation lifetime belongs to the host service."""

from __future__ import annotations

import codecs
import json
import os
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import closing, suppress
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, BinaryIO
from uuid import UUID

from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.result import ModelObservation, normalize_model_observation, usage_snapshot


def _envelope_type(prefix: bytes, *, require_payload: bool = True) -> str | None:
    """Read only the native envelope's top-level type, never nested payload keys."""
    try:
        # The bounded read can end inside a UTF-8 character in a large payload.
        text = codecs.utf_8_decode(prefix, "strict", False)[0].lstrip()
        if not text.startswith("{"):
            return None
        text = text[1:].lstrip()
        decoder = json.JSONDecoder()
        seen: set[str] = set()
        record_type: str | None = None
        # Native records put unique timestamp/ordinal/type headers before payload.
        # Continue through payload so duplicate top-level headers after type (such
        # as a second "type") stay ambiguous rather than returning early.
        for _ in range(4):
            key, end = decoder.raw_decode(text)
            text = text[end:].lstrip()
            if not text.startswith(":"):
                return None
            text = text[1:].lstrip()
            if not isinstance(key, str) or key in seen:
                return None
            seen.add(key)
            if key == "payload":
                return record_type if (not require_payload or text.startswith("{")) else None
            if key not in ("timestamp", "ordinal", "type"):
                return None
            value, end = decoder.raw_decode(text)
            text = text[end:].lstrip()
            if key == "type":
                if not isinstance(value, str):
                    return None
                record_type = value
                if not require_payload and not text.startswith(","):
                    return record_type
            elif (key == "timestamp" and not isinstance(value, str)) or (
                key == "ordinal" and type(value) is not int
            ):
                return None
            if not text.startswith(","):
                return None
            text = text[1:].lstrip()
    except (ValueError, RecursionError):
        pass
    return None


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
                record_type = _envelope_type(line)
                # response_item carries neither turn_context nor token_count, even
                # before the first context (for example initial tool definitions).
                # event_msg and compacted are safe to discard only after this
                # invocation observed context: before context, an event_msg can hide
                # token_count usage and pre-turn compaction can run on a previous
                # model rather than the following turn_context.
                safe_oversized = record_type == "response_item" or (
                    self.model_observation is not None and record_type in ("event_msg", "compacted")
                )
                if not safe_oversized:
                    self._observe_model({})
                self.discard_line = not chunk.endswith(b"\n")
                continue
            if not chunk.endswith(b"\n"):
                self.pending = line
                break
            if (
                self.model_observation is not None
                and b'"token_count"' not in line
                and b'"turn_context"' not in line
                and b"\\u" not in line
            ):
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise TypeError("expected rollout object")
                record_type = event.get("type")
                if record_type == "turn_context":
                    self._observe_model(event.get("payload", {}))
                    continue
                if record_type == "compacted":
                    if self.model_observation is None:
                        self._observe_model({})
                    continue
                payload = event.get("payload", {})
                if record_type != "event_msg" or payload.get("type") != "token_count":
                    continue
                counters = payload.get("info", {}).get("total_token_usage", {})
                stamp = event.get("timestamp")
                if not isinstance(stamp, str):
                    continue
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
                header_type = _envelope_type(line, require_payload=False)
                if (
                    b'"turn_context"' in line
                    or header_type == "turn_context"
                    or (
                        self.model_observation is None
                        and (b'"compacted"' in line or header_type == "compacted")
                    )
                ):
                    # A complete but unreadable context or pre-context compaction
                    # record can hide a model change. Later valid contexts cannot
                    # fill that gap.
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


class CodexStreamError(Exception):
    """Post-launch stream failure with the reaped process's available output."""

    def __init__(self, process: subprocess.CompletedProcess[str], cause: Exception) -> None:
        self.process = process
        super().__init__(f"Codex stream failed ({type(cause).__name__}): {cause}")


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
            if '"thread.started"' in line:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(event, dict)
                    and event.get("type") == "thread.started"
                    and isinstance(event.get("thread_id"), str)
                ):
                    # Session attachment can commit required recovery identity.
                    # Its failures must reach read_safely and stop the process.
                    on_session(event["thread_id"])

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
        process.wait()
    except (OSError, UnicodeError) as exc:
        read_failures.append(exc)
    finally:
        if process.poll() is None:
            if process_control is not None:
                process_control.stop()
            else:
                process.terminate()
                process.wait()
        stdout.join()
        stderr.join()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    completed = subprocess.CompletedProcess(
        command, process.returncode, "".join(output), "".join(errors)
    )
    if read_failures:
        raise CodexStreamError(completed, read_failures[0]) from read_failures[0]
    return completed
