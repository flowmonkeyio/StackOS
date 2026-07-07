"""Daemon, autostart, and MCP bridge CLI commands."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from stackos.config import Settings, get_settings
from stackos.host_mcp.bridge import WORKSPACE_ROOT_ENV
from stackos.mcp.bridge import AgentBridgeProxy, bridge_error
from stackos.workspace_identity import (
    is_usable_workspace_root,
    normalize_path,
    path_fingerprint,
)

from .app import app, autostart_app
from .constants import _LAUNCHD_LABEL, _LOOPBACK_HOSTS
from .paths import _doctor_home


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option("--host", help="Loopback address to bind"),
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="TCP port")] = 5180,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Log level (DEBUG/INFO/WARNING/ERROR)"),
    ] = "INFO",
) -> None:
    """Run the daemon foreground.

    Refuses non-loopback hosts at parse time so `0.0.0.0` exits with code 1
    and a one-line explanation rather than uvicorn binding publicly.
    """
    if host not in _LOOPBACK_HOSTS:
        # Try one more parse for edge cases like 0:0:0:0:0:0:0:1.
        import ipaddress

        ok = False
        try:
            addr = ipaddress.ip_address(host)
            ok = addr.is_loopback
        except ValueError:
            ok = False
        if not ok:
            # Use exit code 1 for unsafe host misuse, distinct from
            # typer.BadParameter's default 2.
            typer.echo(
                f"error: --host {host!r} is not a loopback address; refusing to bind. "
                "Use 127.0.0.1, ::1, or localhost.",
                err=True,
            )
            raise typer.Exit(code=1)

    # Override settings with CLI flags by stuffing into env *before* importing
    # uvicorn — pydantic-settings will read these.
    env_overrides = ("STACKOS_HOST", "STACKOS_PORT", "STACKOS_LOG_LEVEL")
    previous_env = {key: os.environ.get(key) for key in env_overrides}
    os.environ["STACKOS_HOST"] = host
    os.environ["STACKOS_PORT"] = str(port)
    os.environ["STACKOS_LOG_LEVEL"] = log_level.upper()

    settings = get_settings()
    settings.ensure_dirs()
    _write_pid_file(settings.pid_path, os.getpid())

    try:
        # Late-import uvicorn so `--help` is fast and the heavy import only
        # happens on actual serve.
        import uvicorn

        uvicorn.run(
            "stackos.server:create_app",
            host=host,
            port=port,
            factory=True,
            log_level=log_level.lower(),
            reload=False,
        )
    finally:
        _remove_pid_file(settings.pid_path, os.getpid())
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _tcp_can_connect(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True iff a TCP connect to (host, port) succeeds within `timeout`."""
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


def _autostart_bridge_daemon(settings: Settings, host: str, port: int) -> tuple[bool, str]:
    """Start the singleton daemon for plugin clients when it is not running."""
    log_path = settings.state_dir / "mcp-bridge-autostart.log"
    ok, message = _spawn_detached_daemon(
        settings,
        host,
        port,
        log_level=settings.log_level,
        log_path=log_path,
        cwd=Path.home(),
    )
    if ok and message.startswith("started daemon"):
        message = "auto-" + message
    return ok, message


def _launchd_plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _launchd_domain() -> str:
    if not hasattr(os, "getuid"):
        raise RuntimeError("launchd autostart requires a Unix-like platform")
    return f"gui/{os.getuid()}"


def _launchd_service() -> str:
    return f"{_launchd_domain()}/{_LAUNCHD_LABEL}"


def _launchctl(args: list[str]) -> tuple[bool, str]:
    launchctl = shutil.which("launchctl")
    if launchctl is None:
        return False, "launchctl is not on PATH; launchd autostart requires macOS."
    try:
        result = subprocess.run(
            [launchctl, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (result.stderr or result.stdout).strip()
    return result.returncode == 0, output


def _launchd_loaded() -> tuple[bool, str]:
    try:
        service = _launchd_service()
    except RuntimeError as exc:
        return False, str(exc)
    return _launchctl(["print", service])


def _launchd_bootout(plist_path: Path) -> tuple[bool, str]:
    loaded, _ = _launchd_loaded()
    if loaded:
        try:
            service = _launchd_service()
        except RuntimeError as exc:
            return False, str(exc)
        ok, message = _launchctl(["bootout", service])
        if not ok:
            return ok, message
        if not _wait_for_launchd_unloaded(timeout=5.0):
            return False, "launchd job did not unload before timeout"
        return ok, message
    # Older launchctl versions accept unload and ignore unloaded jobs.
    ok, message = _launchctl(["unload", str(plist_path)])
    if not ok and _launchd_unload_failure_is_benign(message):
        return True, message
    return ok, message


def _launchd_unload_failure_is_benign(message: str) -> bool:
    normalized = message.lower()
    benign_markers = (
        "not loaded",
        "not found",
        "no such process",
        "could not find specified service",
        "service is not loaded",
    )
    return any(marker in normalized for marker in benign_markers)


def _wait_for_launchd_unloaded(*, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() <= deadline:
        loaded, _ = _launchd_loaded()
        if not loaded:
            return True
        time.sleep(0.1)
    return False


def _launchd_bootstrap(plist_path: Path) -> tuple[bool, str]:
    try:
        domain = _launchd_domain()
    except RuntimeError as exc:
        return False, str(exc)
    loaded, _ = _launchd_loaded()
    if loaded:
        return True, "launchd job already loaded"
    ok, message = _launchctl(["bootstrap", domain, str(plist_path)])
    if ok:
        return True, "launchd job loaded"
    legacy_ok, legacy_message = _launchctl(["load", "-w", str(plist_path)])
    if legacy_ok:
        return True, "launchd job loaded"
    return False, legacy_message or message


def _loaded_launchd_plist(home: Path) -> Path | None:
    """Return the launchd plist path when the StackOS job is currently loaded."""
    plist_path = _launchd_plist_path(home)
    if not plist_path.exists():
        return None
    loaded, _message = _launchd_loaded()
    return plist_path if loaded else None


def _installed_launchd_plist(home: Path) -> Path | None:
    """Return the launchd plist path when launchd is the configured owner."""
    plist_path = _launchd_plist_path(home)
    return plist_path if plist_path.exists() else None


def _launchd_plist_content(
    settings: Settings,
    *,
    home: Path,
    host: str,
    port: int,
    log_level: str,
) -> bytes:
    import plistlib

    settings.ensure_dirs()
    environment = {
        "HOME": str(home),
        "STACKOS_DATA_DIR": str(settings.data_dir),
        "STACKOS_STATE_DIR": str(settings.state_dir),
        "STACKOS_HOST": host,
        "STACKOS_PORT": str(port),
        "STACKOS_LOG_LEVEL": log_level,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    for key in ("STACKOS_PACKAGED_CLI", "PYTHONHOME", "PLAYWRIGHT_BROWSERS_PATH"):
        value = os.environ.get(key)
        if value:
            environment[key] = value

    payload = {
        "Label": _LAUNCHD_LABEL,
        "ProgramArguments": _daemon_args(host, port, log_level),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "WorkingDirectory": str(home),
        "StandardOutPath": str(settings.log_path),
        "StandardErrorPath": str(settings.log_path),
        "EnvironmentVariables": environment,
    }
    return plistlib.dumps(payload, sort_keys=False)


def _install_launchd_autostart(
    settings: Settings,
    *,
    home: Path,
    force: bool,
    host: str,
    port: int,
    log_level: str,
) -> tuple[bool, str]:
    if shutil.which("launchctl") is None:
        return False, "launchctl is not on PATH; launchd autostart requires macOS."
    try:
        _launchd_domain()
    except RuntimeError as exc:
        return False, str(exc)

    plist_path = _launchd_plist_path(home)
    content = _launchd_plist_content(
        settings,
        home=home,
        host=host,
        port=port,
        log_level=log_level,
    )

    if plist_path.exists() and plist_path.read_bytes() == content:
        ok, message = _launchd_bootstrap(plist_path)
        if not ok:
            return (
                False,
                f"launchd plist already current at {plist_path}, but load failed: {message}",
            )
        return True, f"launchd plist already current at {plist_path}; {message}"

    if plist_path.exists() and not force:
        return (
            False,
            f"launchd plist at {plist_path} differs; rerun with --force to overwrite.",
        )

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.exists():
        backup = plist_path.with_suffix(plist_path.suffix + ".bak")
        backup.write_bytes(plist_path.read_bytes())
        _launchd_bootout(plist_path)

    tmp_path = plist_path.with_name(f".{plist_path.name}.tmp")
    tmp_path.write_bytes(content)
    os.replace(tmp_path, plist_path)

    ok, message = _launchd_bootstrap(plist_path)
    if not ok:
        return False, f"wrote launchd plist at {plist_path}, but load failed: {message}"
    return True, f"installed launchd plist at {plist_path}; {message}"


def _uninstall_launchd_autostart(*, home: Path) -> tuple[bool, str]:
    plist_path = _launchd_plist_path(home)
    if not plist_path.exists():
        return True, f"no launchd plist at {plist_path}; nothing to do"
    ok, message = _launchd_bootout(plist_path)
    if not ok:
        return False, f"failed to unload launchd job for {plist_path}: {message}"
    plist_path.unlink(missing_ok=True)
    return True, f"removed launchd plist {plist_path}"


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


def _git_output(cwd: Path, args: list[str]) -> str | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _usable_workspace_root(path: Path) -> Path | None:
    resolved = normalize_path(path)
    if not resolved or not is_usable_workspace_root(resolved):
        return None
    return Path(resolved)


def _mcp_bridge_workspace_hints(
    cwd: Path,
    *,
    workspace_root: Path | None = None,
) -> dict[str, str]:
    # Claude Code supplies the real project root for stdio MCP servers. Claude
    # Desktop does not, so process cwd is only a fallback hint. The selected
    # directory is the workspace identity; Git remote is optional metadata.
    env_workspace_root = os.environ.get(WORKSPACE_ROOT_ENV)
    candidate = (
        workspace_root
        or (Path(env_workspace_root) if env_workspace_root else None)
        or Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    )
    resolved_root = _usable_workspace_root(candidate)
    if resolved_root is None:
        return {}
    remote = _git_output(resolved_root, ["config", "--get", "remote.origin.url"])
    fingerprint = path_fingerprint(resolved_root)
    if fingerprint is None:
        return {}
    hints = {
        "cwd": str(resolved_root),
        "repo_fingerprint": fingerprint,
    }
    if remote:
        hints["git_remote_url"] = remote
    return hints


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


def _abort_restart_after_launchd_bootout(launchd_plist: Path, message: str) -> None:
    restore_ok, restore_message = _launchd_bootstrap(launchd_plist)
    typer.echo(message, err=True)
    if restore_ok:
        typer.echo(
            f"restart: restored launchd job after failed restart; {restore_message}",
            err=True,
        )
    else:
        typer.echo(
            f"restart: failed to restore launchd job after failed restart; {restore_message}",
            err=True,
        )
    raise typer.Exit(code=1)


@app.command()
def start(
    host: Annotated[
        str | None,
        typer.Option("--host", help="Daemon host; defaults to configured loopback host."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Daemon port; defaults to configured daemon port."),
    ] = None,
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", help="Log level for the daemon."),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for readiness."),
    ] = 20.0,
) -> None:
    """Start the local singleton daemon in the background if needed."""
    settings = get_settings()
    settings.ensure_dirs()
    daemon_host = host or settings.host
    daemon_port = port or settings.port
    daemon_log_level = (log_level or settings.log_level).upper()

    if not _is_loopback_host(daemon_host):
        typer.echo(
            f"error: --host {daemon_host!r} is not a loopback address; refusing to start.",
            err=True,
        )
        raise typer.Exit(code=1)

    daemon_pids, blocker_pids = _discover_daemon_processes(settings, daemon_port)
    if blocker_pids:
        typer.echo(
            "error: port "
            f"{daemon_port} is held by non-StackOS process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}",
            err=True,
        )
        raise typer.Exit(code=1)

    if _daemon_health_ok(daemon_host, daemon_port, timeout=0.25):
        typer.echo(f"start: daemon already running; url=http://{daemon_host}:{daemon_port}")
        return

    if daemon_pids or _tcp_can_connect(daemon_host, daemon_port, timeout=0.25):
        typer.echo(
            "error: daemon process or port is present but health is not ready; "
            "run `stackos restart`.",
            err=True,
        )
        raise typer.Exit(code=1)

    ok, message = _spawn_detached_daemon(
        settings,
        daemon_host,
        daemon_port,
        log_level=daemon_log_level,
        log_path=settings.log_path,
        cwd=Path.cwd(),
        ready_timeout=timeout,
    )
    if not ok:
        typer.echo(f"start: {message}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"start: {message}")


@app.command()
def stop(
    host: Annotated[
        str | None,
        typer.Option("--host", help="Daemon host; defaults to configured loopback host."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Daemon port; defaults to configured daemon port."),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for stop readiness."),
    ] = 20.0,
    force: Annotated[
        bool,
        typer.Option("--force", help="SIGKILL the daemon if SIGTERM does not stop it."),
    ] = False,
) -> None:
    """Stop the local singleton daemon if it is running."""
    settings = get_settings()
    settings.ensure_dirs()
    daemon_host = host or settings.host
    daemon_port = port or settings.port

    if not _is_loopback_host(daemon_host):
        typer.echo(
            f"error: --host {daemon_host!r} is not a loopback address; refusing to stop.",
            err=True,
        )
        raise typer.Exit(code=1)

    launchd_plist = _loaded_launchd_plist(_doctor_home())
    if launchd_plist is not None:
        ok, message = _launchd_bootout(launchd_plist)
        if not ok:
            typer.echo(f"stop: launchd bootout failed: {message}", err=True)
            raise typer.Exit(code=1)
        detail = f"; {message}" if message else ""
        typer.echo(f"stop: unloaded launchd job{detail}")

    daemon_pids, blocker_pids = _discover_daemon_processes(settings, daemon_port)
    if blocker_pids:
        typer.echo(
            "error: port "
            f"{daemon_port} is held by non-StackOS process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}",
            err=True,
        )
        raise typer.Exit(code=1)

    if not daemon_pids:
        if _tcp_can_connect(daemon_host, daemon_port, timeout=0.25):
            typer.echo(
                "error: daemon port is reachable, but no StackOS daemon PID could be identified.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo("stop: no running daemon found")
        return

    ok, message = _terminate_daemon_processes(
        daemon_pids,
        timeout=timeout,
        force=force,
    )
    if not ok:
        typer.echo(f"stop: {message}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"stop: {message}")


@app.command()
def restart(
    host: Annotated[
        str | None,
        typer.Option("--host", help="Daemon host; defaults to configured loopback host."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Daemon port; defaults to configured daemon port."),
    ] = None,
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", help="Log level for the restarted daemon."),
    ] = None,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Seconds to wait for stop/start readiness."),
    ] = 20.0,
    force: Annotated[
        bool,
        typer.Option("--force", help="SIGKILL the old daemon if SIGTERM does not stop it."),
    ] = False,
) -> None:
    """Restart the local singleton daemon in the background."""
    settings = get_settings()
    settings.ensure_dirs()
    daemon_host = host or settings.host
    daemon_port = port or settings.port
    daemon_log_level = (log_level or settings.log_level).upper()

    if not _is_loopback_host(daemon_host):
        typer.echo(
            f"error: --host {daemon_host!r} is not a loopback address; refusing to start.",
            err=True,
        )
        raise typer.Exit(code=1)

    launchd_plist = _installed_launchd_plist(_doctor_home())
    restart_via_launchd = launchd_plist is not None
    launchd_loaded, _launchd_message = _launchd_loaded() if restart_via_launchd else (False, "")

    daemon_pids, blocker_pids = _discover_daemon_processes(settings, daemon_port)
    if blocker_pids:
        typer.echo(
            "error: port "
            f"{daemon_port} is held by non-StackOS process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}",
            err=True,
        )
        raise typer.Exit(code=1)

    if launchd_plist is not None and launchd_loaded:
        ok, message = _launchd_bootout(launchd_plist)
        if not ok:
            typer.echo(f"restart: launchd bootout failed: {message}", err=True)
            raise typer.Exit(code=1)
        detail = f"; {message}" if message else ""
        typer.echo(f"restart: unloaded launchd job{detail}")
        daemon_pids, blocker_pids = _discover_daemon_processes(settings, daemon_port)

    if blocker_pids:
        message = (
            "error: port "
            f"{daemon_port} is held by non-StackOS process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}"
        )
        if restart_via_launchd:
            assert launchd_plist is not None
            _abort_restart_after_launchd_bootout(launchd_plist, message)
        typer.echo(message, err=True)
        raise typer.Exit(code=1)

    if daemon_pids:
        ok, message = _terminate_daemon_processes(
            daemon_pids,
            timeout=timeout,
            force=force,
        )
        if not ok:
            error_message = f"restart: {message}"
            if restart_via_launchd:
                assert launchd_plist is not None
                _abort_restart_after_launchd_bootout(launchd_plist, error_message)
            typer.echo(error_message, err=True)
            raise typer.Exit(code=1)
        typer.echo(f"restart: {message}")
    elif _tcp_can_connect(daemon_host, daemon_port, timeout=0.25):
        message = "error: daemon port is reachable, but no StackOS daemon PID could be identified."
        if restart_via_launchd:
            assert launchd_plist is not None
            _abort_restart_after_launchd_bootout(launchd_plist, message)
        typer.echo(message, err=True)
        raise typer.Exit(code=1)
    else:
        if not restart_via_launchd:
            typer.echo("restart: no running daemon found")

    if restart_via_launchd:
        assert launchd_plist is not None
        ok, message = _launchd_bootstrap(launchd_plist)
        if not ok:
            typer.echo(f"restart: launchd bootstrap failed: {message}", err=True)
            raise typer.Exit(code=1)
        if not _wait_for_daemon(daemon_host, daemon_port, timeout=timeout):
            typer.echo(
                f"restart: launchd job loaded but daemon did not become ready; {message}",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"restart: {message}; url=http://{daemon_host}:{daemon_port}")
        return

    ok, message = _spawn_detached_daemon(
        settings,
        daemon_host,
        daemon_port,
        log_level=daemon_log_level,
        log_path=settings.log_path,
        cwd=Path.cwd(),
        ready_timeout=timeout,
    )
    if not ok:
        typer.echo(f"restart: {message}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"restart: {message}")


@autostart_app.command(name="install")
def autostart_install(
    host: Annotated[
        str | None,
        typer.Option("--host", help="Daemon host for the launchd job."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Daemon port for the launchd job."),
    ] = None,
    log_level: Annotated[
        str | None,
        typer.Option("--log-level", help="Daemon log level for the launchd job."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an existing differing plist."),
    ] = False,
) -> None:
    """Install or refresh the macOS launchd autostart job."""
    settings = get_settings()
    settings.ensure_dirs()
    daemon_host = host or settings.host
    daemon_port = port or settings.port
    daemon_log_level = (log_level or settings.log_level).upper()
    home = _doctor_home()
    if not _is_loopback_host(daemon_host):
        typer.echo(
            f"autostart: refusing non-loopback daemon host {daemon_host!r}.",
            err=True,
        )
        raise typer.Exit(code=1)
    ok, message = _install_launchd_autostart(
        settings,
        home=home,
        force=force,
        host=daemon_host,
        port=daemon_port,
        log_level=daemon_log_level,
    )
    if not ok:
        typer.echo(f"autostart: {message}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"autostart: {message}")


@autostart_app.command(name="uninstall")
def autostart_uninstall() -> None:
    """Remove the launchd autostart job and plist."""
    ok, message = _uninstall_launchd_autostart(home=_doctor_home())
    if not ok:
        typer.echo(f"autostart: {message}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"autostart: {message}")


@autostart_app.command(name="status")
def autostart_status(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON")] = False,
) -> None:
    """Inspect launchd autostart plist and load state."""
    home = _doctor_home()
    plist_path = _launchd_plist_path(home)
    loaded, message = _launchd_loaded()
    payload = {
        "plist_path": str(plist_path),
        "plist_present": plist_path.exists(),
        "launchd_loaded": loaded,
        "launchctl_message": message,
    }
    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return
    typer.echo(f"autostart: plist_present={payload['plist_present']} path={plist_path}")
    typer.echo(f"autostart: launchd_loaded={loaded}")
    if message:
        typer.echo(f"autostart: launchctl={message}")


@app.command(name="mcp-bridge")
def mcp_bridge(
    host: Annotated[
        str | None,
        typer.Option("--host", help="Daemon host; defaults to configured loopback host."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Daemon port; defaults to configured daemon port."),
    ] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="StackOS data dir for this bridge session."),
    ] = None,
    state_dir: Annotated[
        Path | None,
        typer.Option("--state-dir", help="StackOS state dir for this bridge session."),
    ] = None,
    runtime: Annotated[
        str,
        typer.Option("--runtime", help="Agent host runtime label for workspace sessions."),
    ] = "codex",
    workspace_root: Annotated[
        Path | None,
        typer.Option(
            "--workspace-root",
            help="Explicit directory to use as this bridge session's StackOS workspace.",
        ),
    ] = None,
) -> None:
    """Bridge plugin stdio MCP traffic to the singleton HTTP daemon.

    The plugin runs this command from the website repo, but all state and
    credentials stay in the daemon. The bridge reads the daemon token from
    the user's StackOS state dir rather than from project files.
    """
    import httpx

    if data_dir is not None:
        os.environ["STACKOS_DATA_DIR"] = str(data_dir)
    if state_dir is not None:
        os.environ["STACKOS_STATE_DIR"] = str(state_dir)
    settings = get_settings()
    settings.ensure_dirs()
    bridge_host = host or settings.host
    bridge_port = port or settings.port
    url = f"http://{bridge_host}:{bridge_port}/mcp"

    try:
        token = settings.token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        typer.echo(
            f"mcp-bridge: auth token missing at {settings.token_path}; run `stackos init`.",
            err=True,
        )
        raise typer.Exit(code=7) from None

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    workspace_hints = _mcp_bridge_workspace_hints(Path.cwd(), workspace_root=workspace_root)
    proxy = AgentBridgeProxy(
        url=url,
        headers=headers,
        runtime=runtime or "codex",
        client_session_id=f"stackos-bridge:{os.getpid()}",
        **workspace_hints,
    )
    autostart_attempted = False

    with httpx.Client(timeout=None) as client:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue

            request_id: object = None
            try:
                payload = json.loads(line)
                if isinstance(payload, dict):
                    request_id = payload.get("id")
            except json.JSONDecodeError as exc:
                sys.stdout.write(bridge_error(None, -32700, f"Parse error: {exc.msg}") + "\n")
                sys.stdout.flush()
                continue

            try:
                out = proxy.handle(client, payload=payload, line=line, request_id=request_id)
            except Exception as original_exc:
                failure_message = str(original_exc)
                if not autostart_attempted and not _tcp_can_connect(
                    bridge_host,
                    bridge_port,
                    timeout=0.25,
                ):
                    autostart_attempted = True
                    ok, msg = _autostart_bridge_daemon(settings, bridge_host, bridge_port)
                    typer.echo(f"mcp-bridge: {msg}", err=True)
                    if ok:
                        try:
                            out = proxy.handle(
                                client,
                                payload=payload,
                                line=line,
                                request_id=request_id,
                            )
                        except Exception as retry_exc:
                            failure_message = str(retry_exc)
                        else:
                            if out:
                                sys.stdout.write(out)
                                if not out.endswith("\n"):
                                    sys.stdout.write("\n")
                                sys.stdout.flush()
                            continue
                if request_id is None:
                    typer.echo(f"mcp-bridge: daemon request failed: {failure_message}", err=True)
                    continue
                out = bridge_error(request_id, -32000, f"Daemon request failed: {failure_message}")

            if out:
                sys.stdout.write(out)
                if not out.endswith("\n"):
                    sys.stdout.write("\n")
                sys.stdout.flush()
