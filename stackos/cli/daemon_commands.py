"""Daemon, autostart, and MCP bridge CLI commands."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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

from . import daemon_processes, launchd
from .app import app, autostart_app
from .constants import _LOOPBACK_HOSTS
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
    daemon_processes._write_pid_file(settings.pid_path, os.getpid())

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
            # OAuth callbacks carry one-time values in the query string.
            # RequestTimingMiddleware provides path-only request diagnostics.
            access_log=False,
        )
    finally:
        daemon_processes._remove_pid_file(settings.pid_path, os.getpid())
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _autostart_bridge_daemon(settings: Settings, host: str, port: int) -> tuple[bool, str]:
    """Start the singleton daemon for plugin clients when it is not running."""
    log_path = settings.state_dir / "mcp-bridge-autostart.log"
    ok, message = daemon_processes._spawn_detached_daemon(
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


def _abort_restart_after_launchd_bootout(launchd_plist: Path, message: str) -> None:
    restore_ok, restore_message = launchd._launchd_bootstrap(launchd_plist)
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

    if not daemon_processes._is_loopback_host(daemon_host):
        typer.echo(
            f"error: --host {daemon_host!r} is not a loopback address; refusing to start.",
            err=True,
        )
        raise typer.Exit(code=1)

    daemon_pids, blocker_pids = daemon_processes._discover_daemon_processes(settings, daemon_port)
    if blocker_pids:
        typer.echo(
            "error: port "
            f"{daemon_port} is held by non-StackOS process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}",
            err=True,
        )
        raise typer.Exit(code=1)

    if daemon_processes._daemon_health_ok(daemon_host, daemon_port, timeout=0.25):
        typer.echo(f"start: daemon already running; url=http://{daemon_host}:{daemon_port}")
        return

    if daemon_pids or daemon_processes._tcp_can_connect(daemon_host, daemon_port, timeout=0.25):
        typer.echo(
            "error: daemon process or port is present but health is not ready; "
            "run `stackos restart`.",
            err=True,
        )
        raise typer.Exit(code=1)

    ok, message = daemon_processes._spawn_detached_daemon(
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

    if not daemon_processes._is_loopback_host(daemon_host):
        typer.echo(
            f"error: --host {daemon_host!r} is not a loopback address; refusing to stop.",
            err=True,
        )
        raise typer.Exit(code=1)

    launchd_plist = launchd._loaded_launchd_plist(_doctor_home())
    if launchd_plist is not None:
        ok, message = launchd._launchd_bootout(launchd_plist, wait_timeout=timeout)
        if not ok:
            typer.echo(f"stop: launchd bootout failed: {message}", err=True)
            raise typer.Exit(code=1)
        detail = f"; {message}" if message else ""
        typer.echo(f"stop: unloaded launchd job{detail}")

    daemon_pids, blocker_pids = daemon_processes._discover_daemon_processes(settings, daemon_port)
    if blocker_pids:
        typer.echo(
            "error: port "
            f"{daemon_port} is held by non-StackOS process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}",
            err=True,
        )
        raise typer.Exit(code=1)

    if not daemon_pids:
        if daemon_processes._tcp_can_connect(daemon_host, daemon_port, timeout=0.25):
            typer.echo(
                "error: daemon port is reachable, but no StackOS daemon PID could be identified.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo("stop: no running daemon found")
        return

    ok, message = daemon_processes._terminate_daemon_processes(
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

    if not daemon_processes._is_loopback_host(daemon_host):
        typer.echo(
            f"error: --host {daemon_host!r} is not a loopback address; refusing to start.",
            err=True,
        )
        raise typer.Exit(code=1)

    launchd_plist = launchd._installed_launchd_plist(_doctor_home())
    restart_via_launchd = launchd_plist is not None
    launchd_loaded, _launchd_message = (
        launchd._launchd_loaded() if restart_via_launchd else (False, "")
    )

    daemon_pids, blocker_pids = daemon_processes._discover_daemon_processes(settings, daemon_port)
    if blocker_pids:
        typer.echo(
            "error: port "
            f"{daemon_port} is held by non-StackOS process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}",
            err=True,
        )
        raise typer.Exit(code=1)

    if launchd_plist is not None and launchd_loaded:
        ok, message = launchd._launchd_bootout(launchd_plist, wait_timeout=timeout)
        if not ok:
            typer.echo(f"restart: launchd bootout failed: {message}", err=True)
            raise typer.Exit(code=1)
        detail = f"; {message}" if message else ""
        typer.echo(f"restart: unloaded launchd job{detail}")
        daemon_pids, blocker_pids = daemon_processes._discover_daemon_processes(
            settings, daemon_port
        )

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
        ok, message = daemon_processes._terminate_daemon_processes(
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
    elif daemon_processes._tcp_can_connect(daemon_host, daemon_port, timeout=0.25):
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
        ok, message = launchd._launchd_bootstrap(launchd_plist)
        if not ok:
            typer.echo(f"restart: launchd bootstrap failed: {message}", err=True)
            raise typer.Exit(code=1)
        if not daemon_processes._wait_for_daemon(daemon_host, daemon_port, timeout=timeout):
            typer.echo(
                f"restart: launchd job loaded but daemon did not become ready; {message}",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"restart: {message}; url=http://{daemon_host}:{daemon_port}")
        return

    ok, message = daemon_processes._spawn_detached_daemon(
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
    if not daemon_processes._is_loopback_host(daemon_host):
        typer.echo(
            f"autostart: refusing non-loopback daemon host {daemon_host!r}.",
            err=True,
        )
        raise typer.Exit(code=1)
    ok, message = launchd._install_launchd_autostart(
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
    ok, message = launchd._uninstall_launchd_autostart(home=_doctor_home())
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
    plist_path = launchd._launchd_plist_path(home)
    loaded, message = launchd._launchd_loaded()
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
                if not autostart_attempted and not daemon_processes._tcp_can_connect(
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
