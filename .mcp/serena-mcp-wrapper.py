#!/usr/bin/env python3
"""Lifecycle-managed stdio bridge for a repo-local Serena HTTP MCP server."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        if default is None:
            raise SystemExit(f"{name} is required")
        return default
    return value.strip()


DEFAULT_PROJECT = Path(__file__).resolve().parents[1]
PROJECT = Path(_env("SERENA_PROJECT", str(DEFAULT_PROJECT))).resolve()
PROJECT_NAME = _env("SERENA_PROJECT_NAME", PROJECT.name)
HOST = _env("SERENA_HOST", "127.0.0.1")
PORT = int(_env("SERENA_PORT", "9133"))
CONTEXT = _env("SERENA_CONTEXT", "codex")
SERENA_BIN = _env("SERENA_BIN", "serena")
IDLE_TIMEOUT_SECONDS = int(_env("SERENA_IDLE_TIMEOUT_SECONDS", "3600"))
STARTUP_TIMEOUT_SECONDS = int(_env("SERENA_STARTUP_TIMEOUT_SECONDS", "60"))
HTTP_TIMEOUT_SECONDS = int(_env("SERENA_HTTP_TIMEOUT_SECONDS", "300"))
CHECK_INTERVAL_SECONDS = max(10, min(60, IDLE_TIMEOUT_SECONDS // 4 or 10))

STATE_ROOT = Path(
    os.environ.get("SERENA_MCP_STATE_ROOT", Path(tempfile.gettempdir()) / "serena-mcp")
)
STATE_DIR = STATE_ROOT / f"{PROJECT_NAME}-{PORT}"
LOCK_FILE = STATE_DIR / "lock"
ACTIVITY_LOCK_FILE = STATE_DIR / "activity.lock"
PID_FILE = STATE_DIR / "serena.pid"
WATCHDOG_PID_FILE = STATE_DIR / "watchdog.pid"
LAST_ACTIVITY_FILE = STATE_DIR / "last-activity"
ACTIVE_DIR = STATE_DIR / "active"
PROXY_LOG_FILE = STATE_DIR / "proxy.log"
SERENA_LOG_FILE = STATE_DIR / "serena.log"
ENDPOINT = f"http://{HOST}:{PORT}/mcp"


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)


def _log(message: str) -> None:
    _ensure_state_dir()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with PROXY_LOG_FILE.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{timestamp} {message}\n")


@contextlib.contextmanager
def _file_lock(path: Path):
    _ensure_state_dir()
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_pid(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1.0):
            return True
    except OSError:
        return False


def _touch_activity() -> None:
    _ensure_state_dir()
    with _file_lock(ACTIVITY_LOCK_FILE):
        LAST_ACTIVITY_FILE.write_text(str(time.time()), encoding="utf-8")


def _last_activity() -> float:
    try:
        return float(LAST_ACTIVITY_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return time.time()


def _clean_active_markers() -> list[Path]:
    _ensure_state_dir()
    active_markers: list[Path] = []
    for marker in ACTIVE_DIR.glob("*.active"):
        try:
            pid = int(marker.name.split(".", 1)[0])
        except ValueError:
            marker.unlink(missing_ok=True)
            continue
        if _pid_alive(pid):
            active_markers.append(marker)
        else:
            marker.unlink(missing_ok=True)
    return active_markers


def _begin_request() -> Path:
    _ensure_state_dir()
    marker = ACTIVE_DIR / f"{os.getpid()}.{time.monotonic_ns()}.active"
    with _file_lock(ACTIVITY_LOCK_FILE):
        marker.write_text(str(time.time()), encoding="utf-8")
        LAST_ACTIVITY_FILE.write_text(str(time.time()), encoding="utf-8")
    return marker


def _end_request(marker: Path) -> None:
    with _file_lock(ACTIVITY_LOCK_FILE):
        marker.unlink(missing_ok=True)
        LAST_ACTIVITY_FILE.write_text(str(time.time()), encoding="utf-8")


def _start_watchdog() -> None:
    existing_pid = _read_pid(WATCHDOG_PID_FILE)
    if _pid_alive(existing_pid):
        return

    env = os.environ.copy()
    env.update(
        {
            "SERENA_PROJECT": str(PROJECT),
            "SERENA_PROJECT_NAME": PROJECT_NAME,
            "SERENA_HOST": HOST,
            "SERENA_PORT": str(PORT),
            "SERENA_CONTEXT": CONTEXT,
            "SERENA_BIN": SERENA_BIN,
            "SERENA_IDLE_TIMEOUT_SECONDS": str(IDLE_TIMEOUT_SECONDS),
            "SERENA_STARTUP_TIMEOUT_SECONDS": str(STARTUP_TIMEOUT_SECONDS),
            "SERENA_HTTP_TIMEOUT_SECONDS": str(HTTP_TIMEOUT_SECONDS),
            "SERENA_MCP_STATE_ROOT": str(STATE_ROOT),
        }
    )
    with PROXY_LOG_FILE.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--watchdog"],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            env=env,
            start_new_session=True,
        )
    WATCHDOG_PID_FILE.write_text(str(proc.pid), encoding="utf-8")


def _start_serena() -> None:
    _ensure_state_dir()
    log_handle = SERENA_LOG_FILE.open("a", encoding="utf-8")
    env = os.environ.copy()
    env.setdefault("SERENA_USAGE_REPORTING", "false")
    proc = subprocess.Popen(
        [
            SERENA_BIN,
            "start-mcp-server",
            "--transport",
            "streamable-http",
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--project",
            str(PROJECT),
            "--context",
            CONTEXT,
            "--enable-web-dashboard",
            "false",
            "--open-web-dashboard",
            "false",
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        env=env,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    _log(f"started Serena pid={proc.pid} endpoint={ENDPOINT} project={PROJECT}")


def ensure_server() -> bool:
    """Ensure Serena is listening. Returns True when a new process was started."""
    _ensure_state_dir()
    with _file_lock(LOCK_FILE):
        if _port_open():
            _touch_activity()
            _start_watchdog()
            return False

        pid = _read_pid(PID_FILE)
        if not _pid_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            _start_serena()

        deadline = time.time() + STARTUP_TIMEOUT_SECONDS
        while time.time() < deadline:
            if _port_open():
                _touch_activity()
                _start_watchdog()
                return True
            if not _pid_alive(_read_pid(PID_FILE)):
                break
            time.sleep(0.25)

        raise RuntimeError(f"Serena did not become ready at {ENDPOINT}; see {SERENA_LOG_FILE}")


def _terminate_pid(pid: int | None, name: str) -> bool:
    if not _pid_alive(pid):
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 10
    while time.time() < deadline:
        if not _pid_alive(pid):
            _log(f"stopped {name} pid={pid}")
            return True
        time.sleep(0.25)
    os.kill(pid, signal.SIGKILL)
    _log(f"force-stopped {name} pid={pid}")
    return True


def stop_server(stop_watchdog: bool = True) -> None:
    _ensure_state_dir()
    with _file_lock(LOCK_FILE):
        _terminate_pid(_read_pid(PID_FILE), "Serena")
        PID_FILE.unlink(missing_ok=True)
        if stop_watchdog:
            watchdog_pid = _read_pid(WATCHDOG_PID_FILE)
            if watchdog_pid != os.getpid():
                _terminate_pid(watchdog_pid, "watchdog")
            WATCHDOG_PID_FILE.unlink(missing_ok=True)


def watchdog_loop() -> None:
    _ensure_state_dir()
    _log(f"watchdog started idle_timeout={IDLE_TIMEOUT_SECONDS}s endpoint={ENDPOINT}")
    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        if not _pid_alive(_read_pid(PID_FILE)) and not _port_open():
            _log("watchdog exiting because Serena is not running")
            WATCHDOG_PID_FILE.unlink(missing_ok=True)
            return
        active = _clean_active_markers()
        if active:
            continue
        idle_for = time.time() - _last_activity()
        if idle_for >= IDLE_TIMEOUT_SECONDS:
            _log(f"idle timeout reached after {int(idle_for)}s")
            stop_server(stop_watchdog=False)
            WATCHDOG_PID_FILE.unlink(missing_ok=True)
            return


def _message_id(payload: Any) -> Any | None:
    if isinstance(payload, dict):
        return payload.get("id")
    return None


def _message_method(payload: Any) -> str | None:
    if isinstance(payload, dict):
        method = payload.get("method")
        if isinstance(method, str):
            return method
    return None


def _protocol_version(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    value = params.get("protocolVersion")
    return value if isinstance(value, str) else None


def _json_error_response(message_id: Any, message: str) -> bytes | None:
    if message_id is None:
        return None
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": -32000, "message": message},
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _parse_sse_messages(body: bytes) -> list[bytes]:
    messages: list[bytes] = []
    data_lines: list[str] = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        if line == "":
            if data_lines:
                data = "\n".join(data_lines)
                if data != "[DONE]":
                    messages.append(data.encode("utf-8"))
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
    if data_lines:
        data = "\n".join(data_lines)
        if data != "[DONE]":
            messages.append(data.encode("utf-8"))
    return messages


class SerenaHttpProxy:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.initialize_request: bytes | None = None
        self.initialized_notification: bytes | None = None
        self.upstream_initialized = False

    def _headers(self, include_session: bool = True) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if include_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        return headers

    def _post(self, data: bytes, include_session: bool = True) -> tuple[int, Any, bytes]:
        ensure_server()
        request = urllib.request.Request(
            ENDPOINT,
            data=data,
            headers=self._headers(include_session=include_session),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()
        except urllib.error.URLError:
            ensure_server()
            request = urllib.request.Request(
                ENDPOINT,
                data=data,
                headers=self._headers(include_session=include_session),
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.status, response.headers, response.read()

    def _response_messages(self, headers: Any, body: bytes) -> list[bytes]:
        session_id = headers.get("Mcp-Session-Id")
        if session_id:
            self.session_id = session_id
        if not body.strip():
            return []
        content_type = headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return _parse_sse_messages(body)
        return [body.strip()]

    def _establish_upstream_session(self) -> None:
        if self.initialize_request is None:
            return
        status, headers, body = self._post(self.initialize_request, include_session=False)
        if status < 200 or status >= 300:
            raise RuntimeError(
                f"failed to initialize restarted Serena upstream: HTTP {status} {body[:500]!r}"
            )
        self._response_messages(headers, body)
        self.upstream_initialized = True
        if self.initialized_notification is not None:
            status, _headers, body = self._post(self.initialized_notification)
            if status < 200 or status >= 300:
                raise RuntimeError(
                    f"failed to notify restarted Serena upstream: HTTP {status} {body[:500]!r}"
                )

    def forward(self, data: bytes, payload: Any) -> list[bytes]:
        method = _message_method(payload)
        if method == "initialize":
            self.initialize_request = data
            self.protocol_version = _protocol_version(payload)
            self.session_id = None
            status, headers, body = self._post(data, include_session=False)
            if status < 200 or status >= 300:
                raise RuntimeError(f"Serena initialize failed: HTTP {status} {body[:500]!r}")
            self.upstream_initialized = True
            return self._response_messages(headers, body)

        if method == "notifications/initialized":
            self.initialized_notification = data

        if self.initialize_request is not None and not self.upstream_initialized:
            self._establish_upstream_session()

        status, headers, body = self._post(data)
        if status in {400, 404} and self.initialize_request is not None and method != "initialize":
            self.session_id = None
            self.upstream_initialized = False
            self._establish_upstream_session()
            status, headers, body = self._post(data)
        if status < 200 or status >= 300:
            raise RuntimeError(f"Serena request failed: HTTP {status} {body[:500]!r}")
        return self._response_messages(headers, body)


def stdio_loop() -> None:
    proxy = SerenaHttpProxy()
    for raw_line in sys.stdin.buffer:
        data = raw_line.strip()
        if not data:
            continue
        marker = _begin_request()
        message_id: Any | None = None
        try:
            payload = json.loads(data.decode("utf-8"))
            message_id = _message_id(payload)
            responses = proxy.forward(data, payload)
            for response in responses:
                sys.stdout.buffer.write(response + b"\n")
                sys.stdout.buffer.flush()
        except Exception as exc:
            _log(f"proxy error: {exc}")
            error_response = _json_error_response(message_id, f"Serena proxy error: {exc}")
            if error_response is not None:
                sys.stdout.buffer.write(error_response + b"\n")
                sys.stdout.buffer.flush()
        finally:
            _end_request(marker)


def print_status() -> None:
    _ensure_state_dir()
    serena_pid = _read_pid(PID_FILE)
    watchdog_pid = _read_pid(WATCHDOG_PID_FILE)
    active_count = len(_clean_active_markers())
    idle_for = int(time.time() - _last_activity())
    print(f"project={PROJECT}")
    print(f"endpoint={ENDPOINT}")
    print(f"serena_pid={serena_pid or ''} alive={_pid_alive(serena_pid)} port_open={_port_open()}")
    print(f"watchdog_pid={watchdog_pid or ''} alive={_pid_alive(watchdog_pid)}")
    print(
        f"active_requests={active_count} "
        f"idle_for_seconds={idle_for} "
        f"idle_timeout_seconds={IDLE_TIMEOUT_SECONDS}"
    )
    print(f"state_dir={STATE_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repo-local Serena MCP lifecycle wrapper")
    parser.add_argument("--ensure", action="store_true", help="start Serena if needed and exit")
    parser.add_argument(
        "--status",
        action="store_true",
        help="print Serena wrapper status and exit",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="stop the managed Serena process and exit",
    )
    parser.add_argument("--watchdog", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.watchdog:
        watchdog_loop()
        return
    if args.ensure:
        ensure_server()
        print(ENDPOINT)
        return
    if args.status:
        print_status()
        return
    if args.stop:
        stop_server()
        return
    stdio_loop()


if __name__ == "__main__":
    main()
