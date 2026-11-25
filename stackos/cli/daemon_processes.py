"""Daemon process discovery, readiness, spawning, and termination services."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from stackos.config import Settings

from .constants import _LOOPBACK_HOSTS


def _tcp_can_connect(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True iff a TCP connect to (host, port) succeeds within ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_loopback_host(host: str) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _wait_for_daemon(host: str, port: int, *, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _daemon_health_ok(host, port, timeout=0.25):
            return True
        time.sleep(0.2)
    return _daemon_health_ok(host, port, timeout=0.25)


def _daemon_health_ok(host: str, port: int, *, timeout: float = 0.5) -> bool:
    """Return True iff the daemon's unauthenticated health endpoint is ready."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    request = Request(f"http://{host}:{port}/api/v1/health", method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (HTTPError, OSError, URLError, TimeoutError):
        return False


def _daemon_args(host: str, port: int, log_level: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "stackos",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        log_level,
    ]


def _spawn_detached_daemon(
    settings: Settings,
    host: str,
    port: int,
    *,
    log_level: str,
    log_path: Path,
    cwd: Path,
    ready_timeout: float = 20.0,
) -> tuple[bool, str]:
    if _tcp_can_connect(host, port, timeout=0.25):
        return True, "daemon already running"
    if not _is_loopback_host(host):
        return False, f"refusing to start non-loopback daemon host {host!r}"

    settings.ensure_dirs()
    env = os.environ.copy()
    env["STACKOS_HOST"] = host
    env["STACKOS_PORT"] = str(port)
    env["STACKOS_LOG_LEVEL"] = log_level
    args = _daemon_args(host, port, log_level)
    try:
        with log_path.open("ab", buffering=0) as log:
            process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(cwd),
                env=env,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        return False, f"failed to spawn daemon: {exc}; log={log_path}"

    if _wait_for_daemon(host, port, timeout=ready_timeout):
        return True, f"started daemon pid={process.pid}; url=http://{host}:{port}; log={log_path}"
    exit_code = process.poll()
    if exit_code is None:
        return False, f"daemon did not become ready on {host}:{port}; log={log_path}"
    return False, f"daemon exited with code {exit_code}; log={log_path}"


def _write_pid_file(path: Path, pid: int) -> None:
    path.write_text(f"{pid}\n", encoding="utf-8")


def _remove_pid_file(path: Path, pid: int) -> None:
    try:
        current = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return
    if current == pid:
        path.unlink(missing_ok=True)


def _read_pid_file(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _pid_is_zombie(pid)


def _pid_is_zombie(pid: int) -> bool:
    ps = shutil.which("ps")
    if not ps:
        return False
    try:
        result = subprocess.run(
            [ps, "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip().startswith("Z")


def _pid_command(pid: int) -> str | None:
    ps = shutil.which("ps")
    if not ps:
        return None
    try:
        result = subprocess.run(
            [ps, "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    command = result.stdout.strip()
    return command or None


def _command_looks_like_daemon(command: str | None) -> bool:
    if not command:
        return False
    normalized = command.replace("-", "_")
    return "stackos" in normalized and " serve" in f" {normalized} "


def _listener_pids(port: int) -> list[int]:
    lsof = shutil.which("lsof")
    if not lsof:
        return []
    try:
        result = subprocess.run(
            [lsof, f"-tiTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode not in (0, 1):
        return []

    pids: list[int] = []
    seen: set[int] = set()
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid > 0 and pid not in seen:
            seen.add(pid)
            pids.append(pid)
    return pids


def _discover_daemon_processes(settings: Settings, port: int) -> tuple[list[int], list[int]]:
    """Return ``(daemon_pids, blocker_pids)`` for the configured daemon port."""
    pid_file_pid = _read_pid_file(settings.pid_path)
    listener_pids = _listener_pids(port)
    daemons: list[int] = []
    blockers: list[int] = []
    seen_daemons: set[int] = set()

    for pid in listener_pids:
        if pid == os.getpid():
            continue
        command = _pid_command(pid)
        if _command_looks_like_daemon(command) or (command is None and pid == pid_file_pid):
            daemons.append(pid)
            seen_daemons.add(pid)
        else:
            blockers.append(pid)

    if pid_file_pid and pid_file_pid != os.getpid() and pid_file_pid not in seen_daemons:
        if not _pid_is_running(pid_file_pid):
            _remove_pid_file(settings.pid_path, pid_file_pid)
        elif _command_looks_like_daemon(_pid_command(pid_file_pid)):
            daemons.append(pid_file_pid)

    return daemons, blockers


def _wait_for_pids_to_exit(pids: list[int], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not _pid_is_running(pid) for pid in pids):
            return True
        time.sleep(0.2)
    return all(not _pid_is_running(pid) for pid in pids)


def _terminate_daemon_processes(
    pids: list[int],
    *,
    timeout: float,
    force: bool,
) -> tuple[bool, str]:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            return False, f"permission denied stopping daemon pid={pid}: {exc}"

    if _wait_for_pids_to_exit(pids, timeout=timeout):
        return True, f"stopped daemon pid(s): {', '.join(str(pid) for pid in pids)}"

    if force:
        for pid in pids:
            if not _pid_is_running(pid):
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except PermissionError as exc:
                return False, f"permission denied force-stopping daemon pid={pid}: {exc}"
        if _wait_for_pids_to_exit(pids, timeout=timeout):
            return True, f"force-stopped daemon pid(s): {', '.join(str(pid) for pid in pids)}"

    return False, (
        "daemon did not stop before timeout; re-run with `stackos restart --force` "
        "if the process is wedged."
    )


__all__ = [
    "_command_looks_like_daemon",
    "_daemon_args",
    "_daemon_health_ok",
    "_discover_daemon_processes",
    "_is_loopback_host",
    "_listener_pids",
    "_pid_command",
    "_pid_is_running",
    "_pid_is_zombie",
    "_read_pid_file",
    "_remove_pid_file",
    "_spawn_detached_daemon",
    "_tcp_can_connect",
    "_terminate_daemon_processes",
    "_wait_for_daemon",
    "_wait_for_pids_to_exit",
    "_write_pid_file",
]
