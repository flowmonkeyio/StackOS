"""Counter-only native observation. Invocation lifetime belongs to the host service."""

from __future__ import annotations

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
from stackos.host_mcp.result import usage_snapshot


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

    def read(self) -> tuple[str, dict[str, Any]] | None:
        self._open()
        if self.file is None:
            return None
        latest = None
        # Bounded working memory even when a native transcript line is huge.
        # Only counter event lines are decoded; all other bytes are discarded.
        while chunk := self.file.readline(64 * 1024):
            self.cursor = self.file.tell()
            if self.discard_line:
                self.discard_line = not chunk.endswith(b"\n")
                continue
            line = self.pending + chunk
            self.pending = b""
            if not chunk.endswith(b"\n"):
                if len(line) < 64 * 1024:
                    self.pending = line
                else:
                    self.discard_line = True
                break
            if b'"token_count"' not in line:
                continue
            try:
                event = json.loads(line)
                payload = event.get("payload", {})
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
                    latest = (stamp, usage)
            except (ValueError, TypeError, AttributeError):
                continue
        return latest

    def close(self) -> None:
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
                    if event.get("type") == "thread.started" and isinstance(
                        event.get("thread_id"), str
                    ):
                        on_session(event["thread_id"])
                except Exception:
                    # Optional telemetry cannot affect execution or final handling.
                    pass

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
