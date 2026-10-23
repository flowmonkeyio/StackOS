"""Isolated source exercise: real stdio producer/consumer, no live StackOS state.

Run with the candidate's Python: scripts/diagnostics/bridge_lifecycle.py OUTPUT_DIR.
OUTPUT_DIR must not exist. JSONL receipts use the existing private, fsynced logger.
The supervisor owns both subprocesses; it never discovers or signals other PIDs.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from stackos.logging import append_json_log

SCENARIOS = (
    "success",
    "consumer_timeout",
    "cancellation",
    "consumer_exit",
    "bridge_failure",
    "bridge_exit",
    "bridge_timeout",
)
ATTEMPT_SECONDS = 20.0
CLEANUP_SECONDS = 1.0
CONSUMER_TIMEOUT_SECONDS = 0.2
ROOT = Path(__file__).resolve().parents[2]


def record(path: Path, attempt: str, actor: str, event: str, origin: float, **facts) -> None:
    append_json_log(
        path,
        {
            "schema_version": "stackos.bridge-lifecycle-exercise.v1",
            "attempt_id": attempt,
            "actor": actor,
            "event": event,
            "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "elapsed_ms": round((time.monotonic() - origin) * 1000),
            "timing_origin": "supervisor_before_spawn",
            **facts,
        },
    )


def consumer(path: Path, attempt: str, origin: float, control_fd: int) -> int:
    """One bounded synthetic initialize exchange; never persist response bodies."""

    def emit(event: str, **facts) -> None:
        record(path, attempt, "consumer", event, origin, **facts)

    emit("started")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "isolated-lifecycle-exercise", "version": "1"},
        },
    }
    sys.stdout.write(json.dumps(request) + "\n")
    sys.stdout.flush()
    emit("request_written")
    deadline = origin + ATTEMPT_SECONDS
    response = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(sys.stdin, selectors.EVENT_READ, "response")
        selector.register(control_fd, selectors.EVENT_READ, "control")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                emit("deadline_expired")
                return 21
            for key, _ in selector.select(remaining):
                if key.data == "control":
                    command = os.read(control_fd, 1)
                    if command == b"C":
                        emit("cancellation_received")
                        return 20
                    if command == b"T":
                        deadline = min(deadline, time.monotonic() + CONSUMER_TIMEOUT_SECONDS)
                        emit("deadline_armed", timeout_ms=round(CONSUMER_TIMEOUT_SECONDS * 1000))
                    elif not command:
                        selector.unregister(control_fd)
                    continue
                chunk = os.read(sys.stdin.fileno(), 4096)
                if not chunk:
                    emit("producer_eof", partial_response=bool(response))
                    return 22
                response.extend(chunk)
                if len(response) > 65536:
                    emit("response_limit_exceeded")
                    return 23
                if b"\n" in response:
                    try:
                        envelope = json.loads(response.split(b"\n", 1)[0])
                    except (ValueError, UnicodeError):
                        emit("invalid_response")
                        return 24
                    if isinstance(envelope, dict) and envelope.get("id") == 1:
                        if "result" in envelope:
                            emit("response_received", outcome="result")
                            return 0
                        data = envelope.get("error", {}).get("data", {})
                        # Correlation only; no arbitrary producer fields are copied.
                        emit(
                            "response_received",
                            outcome="error",
                            attempt_matches=data.get("attempt_id") == attempt,
                        )
                        return 25
                    emit("invalid_response")
                    return 24


def exercise(destination: Path, scenario: str) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError("unsupported exercise scenario")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    state = destination / "state"
    state.mkdir(mode=0o700)
    # A synthetic token, never a copy of local daemon credentials.
    if scenario != "bridge_exit":
        (state / "auth.token").write_text("isolated-fixture-only", encoding="utf-8")
        (state / "auth.token").chmod(0o600)
    attempt = uuid4().hex
    log = destination / "lifecycle.jsonl"
    origin = time.monotonic()
    deadline = origin + ATTEMPT_SECONDS
    received, release = threading.Event(), threading.Event()
    processes: dict[str, subprocess.Popen] = {}
    control_read = control_write = -1
    server = None
    server_thread = None
    cleanup_errors: list[str] = []
    completed = False

    def emit(actor: str, event: str, **facts) -> None:
        record(log, attempt, actor, event, origin, **facts)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(ATTEMPT_SECONDS)
            super().setup()

        def log_message(self, *_args):
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                self.send_error(400)
                return
            self.rfile.read(size)
            emit("fixture", "request_received")
            received.set()
            if scenario in {
                "consumer_timeout",
                "cancellation",
                "consumer_exit",
                "bridge_timeout",
            } and not release.wait(max(0, deadline - time.monotonic())):
                return
            body = (
                b"invalid-json"
                if scenario == "bridge_failure"
                else json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "isolated-fixture", "version": "1"},
                        },
                    }
                ).encode()
            )
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                emit("fixture", "response_written")
            except (BrokenPipeError, ConnectionResetError):
                emit("fixture", "peer_closed")

    # Allowlist environment, isolated cwd/home/data/state; no .env or host settings.
    env = {
        "PATH": os.defpath,
        "HOME": str(destination),
        "PYTHONPATH": str(ROOT),
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
        "STACKOS_DATA_DIR": str(destination / "data"),
        "STACKOS_STATE_DIR": str(state),
        "STACKOS_BRIDGE_ATTEMPT_ID": attempt,
        "STACKOS_BRIDGE_STARTUP_TIMEOUT_SECONDS": "0.2" if scenario == "bridge_timeout" else "5",
    }
    observed_exits: set[str] = set()

    def observe(actor: str) -> None:
        process = processes[actor]
        code = process.poll()
        if code is not None and actor not in observed_exits:
            observed_exits.add(actor)
            emit(
                "supervisor",
                "child_exited",
                child=actor,
                returncode=code,
                signal=-code if code < 0 else None,
            )

    def cleanup(operation, *args, **kwargs) -> None:
        # An unavailable receipt sink must never strand an owned child or FD.
        try:
            operation(*args, **kwargs)
        except Exception:
            cleanup_errors.append("cleanup_or_persistence_failed")

    def retire(actor: str, process: subprocess.Popen) -> None:
        if process.poll() is None:
            cleanup(emit, "supervisor", "cleanup_terminate_sent", child=actor)
            cleanup(process.terminate)
            try:
                process.wait(timeout=CLEANUP_SECONDS)
            except subprocess.TimeoutExpired:
                cleanup(emit, "supervisor", "cleanup_kill_sent", child=actor)
                cleanup(process.kill)
        cleanup(process.wait, timeout=CLEANUP_SECONDS)
        cleanup(observe, actor)

    try:
        control_read, control_write = os.pipe()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        emit(
            "supervisor",
            "attempt_started",
            scenario=scenario,
            deadline_ms=round(ATTEMPT_SECONDS * 1000),
            cleanup_grace_ms=round(CLEANUP_SECONDS * 1000),
            evidence_kind="source_exercise",
        )
        producer = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "stackos",
                "mcp-bridge",
                "--host",
                "127.0.0.1",
                "--port",
                str(server.server_port),
                "--data-dir",
                str(destination / "data"),
                "--state-dir",
                str(state),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=destination,
            env=env,
        )
        processes["producer"] = producer
        emit("supervisor", "child_spawned", child="producer", pid=producer.pid)
        reader = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--consumer",
                str(log),
                attempt,
                str(origin),
                str(control_read),
            ],
            stdin=producer.stdout,
            stdout=producer.stdin,
            stderr=subprocess.DEVNULL,
            pass_fds=(control_read,),
            cwd=destination,
            env=env,
        )
        processes["consumer"] = reader
        emit("supervisor", "child_spawned", child="consumer", pid=reader.pid)
        # Closing the parent's copies is essential: only the consumer owns the read end.
        producer.stdout.close()
        producer.stdin.close()
        os.close(control_read)
        control_read = -1
        triggered = False
        while time.monotonic() < deadline:
            observe("consumer")
            observe("producer")
            if len(observed_exits) == 2:
                completed = True
                break
            if received.is_set() and not triggered:
                triggered = True
                if scenario in {"consumer_timeout", "cancellation"}:
                    command = b"T" if scenario == "consumer_timeout" else b"C"
                    os.write(control_write, command)
                    emit(
                        "supervisor",
                        "deadline_arm_sent" if command == b"T" else "cancellation_sent",
                    )
                elif scenario == "consumer_exit":
                    reader.kill()
                    emit("supervisor", "consumer_kill_sent", signal=9)
            if "consumer" in observed_exits:
                release.set()
            time.sleep(0.01)
        else:
            emit("supervisor", "exercise_deadline_expired")
    finally:
        release.set()
        for actor, process in processes.items():
            cleanup(retire, actor, process)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    cleanup(stream.close)
        if control_write >= 0:
            cleanup(os.close, control_write)
        if control_read >= 0:
            cleanup(os.close, control_read)
        if server_thread is not None and server_thread.is_alive():
            cleanup(server.shutdown)
            cleanup(server_thread.join, timeout=CLEANUP_SECONDS)
        if server is not None:
            cleanup(server.server_close)
        cleanup(
            emit,
            "supervisor",
            "attempt_finished",
            reaped_children=len(observed_exits),
            completed=completed,
            cleanup_failed=bool(cleanup_errors),
        )
    if cleanup_errors:
        raise RuntimeError("Exercise cleanup or persistence failed; evidence is incomplete")
    return {
        "scenario": scenario,
        "attempt_id": attempt,
        "evidence_kind": "source_exercise",
        "completed": completed,
        "consumer_returncode": processes["consumer"].returncode,
        "producer_returncode": processes["producer"].returncode,
    }


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--consumer":
        return consumer(Path(sys.argv[2]), sys.argv[3], float(sys.argv[4]), int(sys.argv[5]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    results = [exercise(args.output / name, name) for name in SCENARIOS]
    append_json_log(args.output / "summary.jsonl", {"results": results})
    print(json.dumps(results))
    return 0 if all(result["completed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
