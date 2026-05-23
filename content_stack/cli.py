"""Typer CLI surface for the daemon.

Subcommand inventory matches PLAN.md's CLI reference. M0 implements `serve`
and a minimal `doctor`; later subcommands deliberately raise `NotImplementedError`
or print the milestone tag and exit, so the CLI shape is fixed from day one.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from content_stack import __milestone__, __version__
from content_stack.config import Settings, get_settings
from content_stack.mcp.bridge import AgentBridgeProxy, bridge_error

app = typer.Typer(
    name="content-stack",
    help=(
        "content-stack daemon CLI — `serve` runs the daemon, `doctor` diagnoses "
        "an install. Other subcommands stub to milestone tags until implemented."
    ),
    no_args_is_help=True,
    add_completion=False,
)

ops_app = typer.Typer(
    name="ops",
    help="Inspect and call registered StackOS operations through the daemon REST adapter.",
    no_args_is_help=True,
)
app.add_typer(ops_app, name="ops")


_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def _exit(code: int) -> None:
    """Exit via typer.Exit so context managers (e.g. uvicorn) unwind cleanly."""
    raise typer.Exit(code=code)


def _print_version(value: bool) -> None:
    """Eager `--version` callback — short-circuits before subcommand resolution."""
    if value:
        typer.echo(f"content-stack {__version__} ({__milestone__})")
        raise typer.Exit(code=0)


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Print version and exit",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Top-level options. `--version` short-circuits the help-on-no-args flow."""
    _ = ctx
    _ = version


# ---- operations -----------------------------------------------------------


def _read_daemon_token(settings: Settings) -> str:
    try:
        return settings.token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        typer.echo(
            f"auth token missing at {settings.token_path}; run `content-stack init`.",
            err=True,
        )
        raise typer.Exit(code=7) from None


def _api_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> Any:
    """Call the local daemon REST API and return parsed JSON."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    settings = settings or get_settings()
    token = _read_daemon_token(settings)
    url = f"http://{settings.host}:{settings.port}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            detail = payload.get("detail") if isinstance(payload, dict) else raw
        except json.JSONDecodeError:
            detail = raw
        typer.echo(f"daemon API error {exc.code}: {detail}", err=True)
        raise typer.Exit(code=1) from None
    except (OSError, TimeoutError, URLError) as exc:
        typer.echo(
            f"daemon API request failed: {exc}; is content-stack running on "
            f"{settings.host}:{settings.port}?",
            err=True,
        )
        raise typer.Exit(code=1) from None
    if not raw:
        return None
    return json.loads(raw)


def _load_operation_arguments(input_path: str | None) -> dict[str, Any]:
    if input_path is None:
        return {}
    raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        typer.echo(f"invalid JSON input: {exc.msg}", err=True)
        raise typer.Exit(code=2) from None
    if not isinstance(payload, dict):
        typer.echo("operation input must be a JSON object", err=True)
        raise typer.Exit(code=2)
    if set(payload) == {"arguments"} and isinstance(payload["arguments"], dict):
        return dict(payload["arguments"])
    return dict(payload)


def _echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@ops_app.command(name="list")
def ops_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the full machine-readable operation list."),
    ] = False,
) -> None:
    """List daemon-registered operations."""
    payload = _api_request("GET", "/api/v1/operations")
    if json_output:
        _echo_json(payload)
        return
    for item in payload.get("items", []):
        surfaces = [
            name
            for name, surface in item.get("surfaces", {}).items()
            if isinstance(surface, dict) and surface.get("enabled")
        ]
        typer.echo(f"{item['name']}\t{','.join(surfaces)}\t{item['summary']}")


@ops_app.command(name="describe")
def ops_describe(
    operation_name: Annotated[str, typer.Argument(help="Operation name, e.g. action.describe")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the full schema and agent guidance."),
    ] = False,
) -> None:
    """Describe one operation with schemas, examples, and agent guidance."""
    payload = _api_request("GET", f"/api/v1/operations/{operation_name}")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"{payload['name']}: {payload['summary']}")
    typer.echo(f"purpose: {payload['purpose']}")
    if payload.get("prerequisites"):
        typer.echo("prerequisites:")
        for item in payload["prerequisites"]:
            typer.echo(f"  - {item}")
    if payload.get("examples"):
        typer.echo("examples:")
        for item in payload["examples"]:
            typer.echo(f"  - {item['title']}")
            typer.echo(f"    {json.dumps(item['arguments'], sort_keys=True)}")


@ops_app.command(name="call")
def ops_call(
    operation_name: Annotated[str, typer.Argument(help="Operation name, e.g. action.validate")],
    input_path: Annotated[
        str | None,
        typer.Option(
            "--input",
            "-i",
            help="JSON file containing operation arguments, or '-' for stdin.",
        ),
    ] = None,
    project_id: Annotated[
        int | None,
        typer.Option("--project", help="Merge project_id into operation arguments."),
    ] = None,
    run_token: Annotated[
        str | None,
        typer.Option("--run-token", help="Merge run_token into operation arguments."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="Merge idempotency_key into operation arguments."),
    ] = None,
) -> None:
    """Call one operation through the daemon's generic REST adapter."""
    arguments = _load_operation_arguments(input_path)
    if project_id is not None:
        arguments["project_id"] = project_id
    if run_token is not None:
        arguments["run_token"] = run_token
    if idempotency_key is not None:
        arguments["idempotency_key"] = idempotency_key
    payload = _api_request(
        "POST",
        f"/api/v1/operations/{operation_name}/call",
        body={"arguments": arguments},
    )
    _echo_json(payload)


# ---- serve ----------------------------------------------------------------


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

    Refuses non-loopback hosts at parse time per PLAN.md M-37 — `0.0.0.0`
    exits with code 1 and a one-line explanation rather than uvicorn binding
    publicly and the host-header middleware paper-clipping the leak.
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
            # PLAN.md L1267 mandates exit code 1 for misuse, distinct from
            # typer.BadParameter's default 2. We emit a one-line stderr
            # message and exit 1 directly.
            typer.echo(
                f"error: --host {host!r} is not a loopback address; refusing to bind. "
                "Use 127.0.0.1, ::1, or localhost.",
                err=True,
            )
            raise typer.Exit(code=1)

    # Override settings with CLI flags by stuffing into env *before* importing
    # uvicorn — pydantic-settings will read these.
    os.environ["CONTENT_STACK_HOST"] = host
    os.environ["CONTENT_STACK_PORT"] = str(port)
    os.environ["CONTENT_STACK_LOG_LEVEL"] = log_level.upper()

    settings = get_settings()
    settings.ensure_dirs()
    _write_pid_file(settings.pid_path, os.getpid())

    try:
        # Late-import uvicorn so `--help` is fast and the heavy import only
        # happens on actual serve.
        import uvicorn

        uvicorn.run(
            "content_stack.server:create_app",
            host=host,
            port=port,
            factory=True,
            log_level=log_level.lower(),
            reload=False,
        )
    finally:
        _remove_pid_file(settings.pid_path, os.getpid())


# ---- plugin bridge --------------------------------------------------------


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
        "content_stack",
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
    env["CONTENT_STACK_HOST"] = host
    env["CONTENT_STACK_PORT"] = str(port)
    env["CONTENT_STACK_LOG_LEVEL"] = log_level
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
    return True


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
    return "content_stack" in normalized and " serve" in f" {normalized} "


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

    if (
        pid_file_pid
        and pid_file_pid != os.getpid()
        and pid_file_pid not in seen_daemons
        and _pid_is_running(pid_file_pid)
        and _command_looks_like_daemon(_pid_command(pid_file_pid))
    ):
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
        "daemon did not stop before timeout; re-run with `content-stack restart --force` "
        "if the process is wedged."
    )


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

    daemon_pids, blocker_pids = _discover_daemon_processes(settings, daemon_port)
    if blocker_pids:
        typer.echo(
            "error: port "
            f"{daemon_port} is held by non-content-stack process pid(s): "
            f"{', '.join(str(pid) for pid in blocker_pids)}",
            err=True,
        )
        raise typer.Exit(code=1)

    if daemon_pids:
        ok, message = _terminate_daemon_processes(
            daemon_pids,
            timeout=timeout,
            force=force,
        )
        if not ok:
            typer.echo(f"restart: {message}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"restart: {message}")
    elif _tcp_can_connect(daemon_host, daemon_port, timeout=0.25):
        typer.echo(
            "error: daemon port is reachable, but no content-stack daemon PID could be identified.",
            err=True,
        )
        raise typer.Exit(code=1)
    else:
        typer.echo("restart: no running daemon found")

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
) -> None:
    """Bridge plugin stdio MCP traffic to the singleton HTTP daemon.

    The plugin runs this command from the website repo, but all state and
    credentials stay in the daemon. The bridge reads the daemon token from
    the user's content-stack state dir rather than from project files.
    """
    import httpx

    settings = get_settings()
    settings.ensure_dirs()
    bridge_host = host or settings.host
    bridge_port = port or settings.port
    url = f"http://{bridge_host}:{bridge_port}/mcp"

    try:
        token = settings.token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        typer.echo(
            f"mcp-bridge: auth token missing at {settings.token_path}; run `content-stack init`.",
            err=True,
        )
        raise typer.Exit(code=7) from None

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    proxy = AgentBridgeProxy(url=url, headers=headers)
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


# ---- doctor ---------------------------------------------------------------


def _tcp_can_connect(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True iff a TCP connect to (host, port) succeeds within `timeout`."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _file_mode_or_none(path: Path) -> int | None:
    """Return the file's permission bits or None if it does not exist."""
    if not path.exists():
        return None
    return stat.S_IMODE(path.stat().st_mode)


def _check_credentials_decrypt(
    settings: Settings,
    db_present: bool,
) -> tuple[bool, list[dict[str, object]]]:
    """Return ``(all_ok, [issues])`` for every integration_credentials row.

    Each issue is ``{credential_id, kind, project_id, error}``. Returns
    ``(True, [])`` when there are zero rows or when the DB does not
    exist yet — those are not failures, just a fresh install.
    """
    if not db_present:
        return True, []
    try:
        from sqlmodel import Session, select

        from content_stack.crypto.aes_gcm import (
            CryptoError,
            configure_seed_path,
        )
        from content_stack.db.connection import make_engine
        from content_stack.db.models import IntegrationCredential
        from content_stack.repositories.projects import IntegrationCredentialRepository

        configure_seed_path(settings.seed_path)
        engine = make_engine(settings.db_path)
        issues: list[dict[str, object]] = []
        try:
            with Session(engine) as session:
                rows = session.exec(select(IntegrationCredential)).all()
                repo = IntegrationCredentialRepository(session)
                for row in rows:
                    if row.id is None:
                        continue
                    try:
                        repo.get_decrypted(row.id)
                    except CryptoError as exc:
                        issues.append(
                            {
                                "credential_id": row.id,
                                "kind": row.kind,
                                "project_id": row.project_id,
                                "error": str(exc.detail),
                            }
                        )
        finally:
            engine.dispose()
    except Exception as exc:  # pragma: no cover — defensive
        return False, [{"error": f"doctor probe failed: {exc}"}]
    return len(issues) == 0, issues


def _check_alembic_at_head(settings: Settings, db_present: bool) -> tuple[bool, str | None]:
    """Confirm ``alembic_version`` row matches the expected head.

    Returns ``(ok, version_or_None)``. A missing DB (fresh install) is
    treated as a non-failure — ``code=4`` only fires when the DB exists
    AND the version table is missing or stale.
    """
    if not db_present:
        return True, None
    try:
        from alembic.script import ScriptDirectory

        from content_stack.db.migrate import alembic_config, current_alembic_version

        current = current_alembic_version(settings)
        script = ScriptDirectory.from_config(alembic_config(settings))
        head = script.get_current_head()
        return current == head, current
    except Exception:  # pragma: no cover — defensive
        return False, None


def _check_scheduler_jobs(settings: Settings) -> tuple[bool, int]:
    """Lightweight liveness check for APScheduler.

    M8 doesn't expose an out-of-band query for scheduled jobs (the
    daemon owns the scheduler instance), so for the doctor we fall back
    to checking that the daemon is up — when it is, we trust the
    lifespan registered the four ops jobs.
    """
    daemon_up = _tcp_can_connect(settings.host, settings.port)
    return daemon_up, 4 if daemon_up else 0


def _doctor_home() -> Path:
    """Return the install home used by scripts and pipx install helpers."""
    return Path(os.environ.get("CONTENT_STACK_HOME") or Path.home()).expanduser()


def _count_traversable_named(
    root: object, filename: str, *, exclude_dirs: frozenset[str] = frozenset()
) -> int:
    """Count files named ``filename`` under an importlib.resources Traversable."""
    count = 0
    try:
        children = list(root.iterdir())  # type: ignore[attr-defined]
    except Exception:
        return 0
    for child in children:
        name = getattr(child, "name", "")
        if name in {".DS_Store", "__pycache__"}:
            continue
        try:
            if child.is_dir():  # type: ignore[attr-defined]
                if name in exclude_dirs:
                    continue
                count += _count_traversable_named(child, filename, exclude_dirs=exclude_dirs)
            elif name == filename:
                count += 1
        except Exception:
            continue
    return count


def _count_expected_plugins(source: object) -> int:
    if isinstance(source, Path):
        return sum(1 for p in source.rglob("plugin.json") if p.parent.name == ".codex-plugin")

    count = 0

    def walk(node: object, *, in_codex_plugin: bool = False) -> None:
        nonlocal count
        try:
            children = list(node.iterdir())  # type: ignore[attr-defined]
        except Exception:
            return
        for child in children:
            name = getattr(child, "name", "")
            try:
                if child.is_dir():  # type: ignore[attr-defined]
                    walk(child, in_codex_plugin=name == ".codex-plugin")
                elif name == "plugin.json" and in_codex_plugin:
                    count += 1
            except Exception:
                continue

    walk(source)
    return count


def _expected_asset_count(kind: Literal["skills", "plugins"]) -> int | None:
    """Return source asset count for doctor install checks."""
    try:
        from content_stack import install as installer

        source = installer._resolve_source(kind)  # type: ignore[attr-defined]
    except Exception:
        return None
    if kind == "plugins":
        return _count_expected_plugins(source)
    filename = "SKILL.md"
    if isinstance(source, Path):
        return sum(1 for _ in source.rglob(filename))
    return _count_traversable_named(source, filename, exclude_dirs=frozenset())


def _installed_asset_count(home: Path, runtime: Literal["codex", "claude"], kind: str) -> int:
    """Count installed skills for one runtime target."""
    filename = "SKILL.md"
    target = home / f".{runtime}" / kind / "content-stack"
    if not target.is_dir():
        return 0
    return sum(1 for _ in target.rglob(filename))


def _installed_plugin_count(home: Path) -> int:
    target = home / ".codex" / "plugins"
    if not target.is_dir():
        return 0
    return sum(1 for p in target.rglob("plugin.json") if p.parent.name == ".codex-plugin")


def _plugin_marketplace_has_content_stack(home: Path) -> bool:
    target = home / ".agents" / "plugins" / "marketplace.json"
    if not target.exists():
        return False
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return False
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    if not isinstance(plugins, list):
        return False
    return any(
        isinstance(p, dict)
        and p.get("name") == "content-stack"
        and (p.get("source") or {}).get("path") == "./.codex/plugins/content-stack"
        for p in plugins
    )


def _check_installed_assets(home: Path) -> tuple[dict[str, bool], dict[str, object]]:
    """Return install mirror checks for plugin-first assets."""
    expected_skills = _expected_asset_count("skills")
    expected_plugins = _expected_asset_count("plugins")
    checks: dict[str, bool] = {}
    details: dict[str, object] = {
        "expected_skills": expected_skills,
        "expected_plugins": expected_plugins,
    }
    for runtime in ("codex", "claude"):
        skills_count = _installed_asset_count(home, runtime, "skills")
        details[f"{runtime}_skills_count"] = skills_count
        details[f"{runtime}_skills_installed"] = (
            expected_skills is not None and skills_count == expected_skills
        )
    plugins_count = _installed_plugin_count(home)
    checks["plugins_installed"] = expected_plugins is not None and plugins_count == expected_plugins
    checks["plugin_marketplace_registered"] = _plugin_marketplace_has_content_stack(home)
    details["plugins_count"] = plugins_count
    return checks, details


def _codex_mcp_line_is_bridge(line: str) -> bool:
    normalized = line.strip()
    if not normalized.startswith("content-stack"):
        return False
    lowered = normalized.lower()
    forbidden = (
        "/mcp",
        "--url",
        "--bearer-token-env-var",
        "authorization",
        "bearer",
    )
    if any(token in lowered for token in forbidden):
        return False
    return "stdio" in lowered or "mcp-bridge" in lowered


def _check_codex_mcp_registered() -> tuple[bool, dict[str, object]]:
    """Best-effort read-only check for Codex MCP registration."""
    codex = shutil.which("codex")
    if codex is None:
        return False, {"available": False}
    try:
        result = subprocess.run(
            [codex, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception as exc:
        return False, {"available": True, "error": str(exc)}
    content_stack_lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("content-stack")
    ]
    bridge_lines = [line for line in content_stack_lines if _codex_mcp_line_is_bridge(line)]
    ok = bool(bridge_lines)
    return ok, {
        "available": True,
        "returncode": result.returncode,
        "expected_transport": "stdio",
        "expected_command": "python -m content_stack mcp-bridge",
        "entries": content_stack_lines,
        "bridge_entries": bridge_lines,
    }


def _check_claude_mcp_registered(home: Path) -> tuple[bool, dict[str, object]]:
    """Read the Claude MCP JSON target and look for the content-stack entry."""
    target = Path(os.environ.get("CONTENT_STACK_MCP_TARGET") or home / ".claude" / "mcp.json")
    if not target.exists():
        return False, {"target": str(target), "exists": False}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        servers = payload.get("mcpServers", {})
        row = servers.get("content-stack") if isinstance(servers, dict) else None
    except Exception as exc:
        return False, {"target": str(target), "exists": True, "error": str(exc)}
    ok = (
        isinstance(row, dict)
        and row.get("transport") == "stdio"
        and isinstance(row.get("command"), str)
        and row.get("args") == ["-m", "content_stack", "mcp-bridge"]
        and "url" not in row
        and "headers" not in row
    )
    return ok, {
        "target": str(target),
        "exists": True,
        "expected_transport": "stdio",
        "expected_args": ["-m", "content_stack", "mcp-bridge"],
        "has_url": isinstance(row, dict) and "url" in row,
        "has_headers": isinstance(row, dict) and "headers" in row,
    }


def _check_launchd_plist(home: Path) -> tuple[bool, dict[str, object]]:
    """Optional launchd plist presence check; launchd itself is not required."""
    target = home / "Library" / "LaunchAgents" / "com.content-stack.daemon.plist"
    return target.exists(), {"target": str(target), "exists": target.exists()}


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON")] = False,
) -> None:
    """Diagnose the install — M8 expanded.

    Exit codes (subset of PLAN.md L1271):
      0 all green
      1 daemon down
      4 alembic head mismatch
      7 auth token missing or wrong mode
      8 seed file missing or wrong mode

    The DB-not-yet-created case is a *warning*, not a failure — first install
    has no DB until `make migrate` runs.
    """
    settings = get_settings()

    daemon_up = _tcp_can_connect(settings.host, settings.port)

    seed_mode = _file_mode_or_none(settings.seed_path)
    seed_ok = seed_mode == 0o600

    token_mode = _file_mode_or_none(settings.token_path)
    token_ok = token_mode == 0o600

    db_present = settings.db_path.exists()

    # M4: walk every integration_credentials row and confirm it
    # decrypts cleanly. A failure here usually means the seed file was
    # rotated outside the CLI or restored from a backup that doesn't
    # match the DB's credentials. We surface it as an issue list rather
    # than crashing doctor — operators want a full report, not a
    # half-finished one.
    credentials_ok, credential_issues = _check_credentials_decrypt(settings, db_present)

    # M8: alembic-head + scheduler-jobs probes.
    alembic_ok, alembic_version = _check_alembic_at_head(settings, db_present)
    scheduler_ok, scheduler_job_count = _check_scheduler_jobs(settings)
    home = _doctor_home()
    install_checks, install_info = _check_installed_assets(home)
    codex_mcp_ok, codex_mcp_info = _check_codex_mcp_registered()
    claude_mcp_ok, claude_mcp_info = _check_claude_mcp_registered(home)
    launchd_ok, launchd_info = _check_launchd_plist(home)

    checks = {
        "daemon_up": daemon_up,
        "seed_file_present": seed_mode is not None,
        "seed_file_mode_0600": seed_ok,
        "auth_token_present": token_mode is not None,
        "auth_token_mode_0600": token_ok,
        "db_file_present": db_present,
        "credentials_decrypt": credentials_ok,
        "alembic_at_head": alembic_ok,
        "scheduler_jobs_healthy": scheduler_ok,
        "codex_mcp_registered": codex_mcp_ok,
        "claude_mcp_registered": claude_mcp_ok,
        "launchd_plist_present": launchd_ok,
        **install_checks,
    }
    info = {
        "host": settings.host,
        "port": settings.port,
        "data_dir": str(settings.data_dir),
        "state_dir": str(settings.state_dir),
        "seed_mode": oct(seed_mode) if seed_mode is not None else None,
        "token_mode": oct(token_mode) if token_mode is not None else None,
        "version": __version__,
        "milestone": __milestone__,
        "credential_issues": credential_issues,
        "alembic_version": alembic_version,
        "scheduler_job_count": scheduler_job_count,
        "home_dir": str(home),
        "install_checks": install_info,
        "codex_mcp": codex_mcp_info,
        "claude_mcp": claude_mcp_info,
        "launchd": launchd_info,
    }

    # Compute exit code with the highest-priority failure winning so a single
    # `doctor` invocation gives the operator the most actionable signal.
    # Credential decrypt failures imply seed mismatch (operator restored a
    # DB without the seed, or seed was overwritten); we surface that as
    # the same exit-code 8 as a missing seed since the remediation is
    # identical (find the right seed or rotate).
    if seed_mode is None or not seed_ok or not credentials_ok:
        code = 8
    elif token_mode is None or not token_ok:
        code = 7
    elif not alembic_ok:
        code = 4
    elif not daemon_up:
        code = 1
    else:
        code = 0

    if json_output:
        typer.echo(json.dumps({"ok": code == 0, "code": code, "checks": checks, "info": info}))
    else:
        status = "OK" if code == 0 else "ISSUES"
        typer.echo(f"content-stack doctor: {status} (exit code {code})")
        for name, val in checks.items():
            mark = "  ok " if val else " FAIL"
            typer.echo(f"  [{mark}] {name}: {val}")
        if not db_present:
            typer.echo("  note: DB not yet created — run `make migrate` after first install.")
        if credential_issues:
            typer.echo(
                f"  note: {len(credential_issues)} credential row(s) failed to decrypt — "
                "see --json output for details."
            )

    _exit(code)


# ---- stubbed subcommands --------------------------------------------------


def _stub(milestone: str, name: str) -> None:
    """Print a milestone tag and exit 0 — uniform format for stubbed CLIs."""
    typer.echo(f"`content-stack {name}` not yet implemented ({milestone}).")


@app.command()
def init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing config")] = False,
) -> None:
    """Initialize XDG dirs, seed, and bearer token.

    Idempotent: re-running on a populated state dir is a no-op (the
    seed and token are read but not regenerated). ``--force`` is
    accepted for symmetry with PLAN.md L1268; it is rejected here as
    too dangerous to wire blindly — operators wanting to rotate seed
    should call ``content-stack rotate-seed`` and ``rotate-token``
    explicitly so the side-effects (re-encryption, MCP re-registration)
    cannot be skipped accidentally.
    """
    if force:
        typer.echo(
            "error: --force on `init` is intentionally not implemented. "
            "Use `content-stack rotate-seed --reencrypt` or `rotate-token --yes`.",
            err=True,
        )
        raise typer.Exit(code=2)

    from content_stack.auth import ensure_token
    from content_stack.crypto.seed import ensure_seed_file

    settings = get_settings()
    settings.ensure_dirs()
    ensure_seed_file(settings.seed_path)
    ensure_token(settings.token_path)
    typer.echo(f"init: state dir at {settings.state_dir}; seed + auth.token present (mode 0600).")


@app.command()
def migrate() -> None:
    """Run alembic migrations forward to head."""
    from content_stack.db.migrate import upgrade_to_head

    settings = get_settings()
    result = upgrade_to_head(settings)
    if result.stamped_existing_schema:
        typer.echo("migrate: stamped existing create_all schema at alembic head.")
    typer.echo(f"migrate: alembic upgraded to head ({settings.db_path}).")


@app.command()
def install(
    skills_only: Annotated[
        bool,
        typer.Option("--skills-only", help="Only mirror skills/ into runtimes."),
    ] = False,
    mcp_only: Annotated[
        bool,
        typer.Option("--mcp-only", help="Only register the MCP server."),
    ] = False,
    plugins_only: Annotated[
        bool,
        typer.Option("--plugins-only", help="Only mirror plugins and register marketplace."),
    ] = False,
    launchd: Annotated[
        bool,
        typer.Option("--launchd", help="Also install the launchd plist (macOS)."),
    ] = False,
    skip_doctor: Annotated[
        bool,
        typer.Option("--skip-doctor", help="Skip the post-install doctor check."),
    ] = False,
) -> None:
    """End-user one-liner install — clone-mode or pipx-mode.

    Auto-detects whether the package was installed from a checked-out
    repo (uses the bash scripts under ``scripts/``) or via pipx (resolves
    bundled assets via ``importlib.resources``). The two paths land at
    the same end state.

    Re-running is idempotent: plugins are the default runtime surface, MCP
    registration upserts existing entries, and loose skills remain available
    through the explicit ``--skills-only`` flag.
    """
    from content_stack import install as installer

    selectors = [skills_only, mcp_only, plugins_only]
    if sum(1 for s in selectors if s) > 1:
        typer.echo(
            "error: --skills-only, --mcp-only, and --plugins-only are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)
    do_skills = skills_only
    do_mcp = mcp_only or not (skills_only or plugins_only)
    do_plugins = plugins_only or not (skills_only or mcp_only)

    mode = installer.detect_mode()
    typer.echo(f"==> Install mode: {mode}")

    settings = get_settings()
    settings.ensure_dirs()
    from content_stack.auth import ensure_token
    from content_stack.crypto.seed import ensure_seed_file

    ensure_seed_file(settings.seed_path)
    ensure_token(settings.token_path)
    typer.echo(f"==> Bootstrap state ready: {settings.state_dir}")
    home = Path.home()

    runtimes: tuple[Literal["codex", "claude"], ...] = ("codex", "claude")
    if do_skills:
        for runtime in runtimes:
            target, count = installer.copy_skills(runtime, home=home)
            typer.echo(f"==> Installed {count} skills -> {target}")

    if do_plugins:
        target, count = installer.copy_plugins(home=home)
        typer.echo(f"==> Installed {count} plugins -> {target}")
        msg = installer.register_plugin_marketplace(home=home)
        typer.echo(f"==> {msg}")

    if do_mcp:
        # Codex first; it prints its own skip-line if not on PATH.
        msg = installer.register_mcp_codex(home=home, port=settings.port)
        typer.echo(f"==> {msg}")
        msg = installer.register_mcp_claude(home=home, port=settings.port)
        typer.echo(f"==> {msg}")

    if launchd:
        # Defer to the bash script; pipx-mode users still get launchd
        # via the script if they have a clone of the repo. Without a
        # repo we cannot generate a sensible plist (the daemon needs a
        # working directory) — surface that explicitly.
        scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
        plist_script = scripts_dir / "install-launchd.sh"
        if plist_script.is_file():
            import subprocess

            subprocess.run(["bash", str(plist_script)], check=True)
        else:
            typer.echo(
                "warning: --launchd requires a checked-out repo (script not in wheel).",
                err=True,
            )

    if not skip_doctor:
        typer.echo("==> Running doctor")
        # Re-enter the doctor command in-process so the exit code
        # propagates. We delegate to the underlying typer command via
        # the same module to avoid duplicating the logic.
        try:
            doctor(json_output=False)
        except typer.Exit as exc:
            # Surface the doctor exit code as the install exit code so
            # `make install` fails loudly when the daemon is not yet up.
            if exc.exit_code not in (0, None):
                raise
    typer.echo("==> install complete")


@app.command(name="rotate-seed")
def rotate_seed(
    reencrypt: Annotated[
        bool,
        typer.Option(
            "--reencrypt",
            help="Required — re-encrypt every integration_credentials row under a fresh seed.",
        ),
    ] = False,
) -> None:
    """Rotate the integration-credentials seed.

    Per PLAN.md L1136-L1142: writes a fresh 32-byte seed, re-encrypts
    every credential row in a single SQLite transaction, and keeps the
    old seed at ``seed.bin.bak`` for one daemon boot. ``--reencrypt`` is
    mandatory because rotating without re-encrypting would orphan every
    existing credential.
    """
    if not reencrypt:
        typer.echo(
            "error: rotate-seed requires --reencrypt (rotating without re-encryption "
            "would orphan every credential row).",
            err=True,
        )
        raise typer.Exit(code=2)

    from sqlmodel import Session

    from content_stack.crypto.aes_gcm import configure_seed_path
    from content_stack.crypto.seed import (
        abort_staged_seed_rotation,
        commit_staged_seed_rotation,
        reencrypt_rows_for_seed_rotation,
        stage_seed_rotation,
    )
    from content_stack.db.connection import make_engine
    from content_stack.db.models import IntegrationCredential

    settings = get_settings()
    settings.ensure_dirs()
    configure_seed_path(settings.seed_path)
    engine = make_engine(settings.db_path)
    db_committed = False
    try:
        with Session(engine) as session:
            from sqlmodel import select

            rows = list(session.exec(select(IntegrationCredential)).all())
            row_dicts = [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "kind": r.kind,
                    "encrypted_payload": r.encrypted_payload,
                    "nonce": r.nonce,
                }
                for r in rows
            ]
            new_seed, rotated = reencrypt_rows_for_seed_rotation(settings.seed_path, rows=row_dicts)
            stage_seed_rotation(settings.seed_path, new_seed)
            id_to_row = {r.id: r for r in rows}
            for rotated_row in rotated:
                row = id_to_row[rotated_row["id"]]
                row.encrypted_payload = rotated_row["encrypted_payload"]
                row.nonce = rotated_row["nonce"]
                session.add(row)
            session.commit()
            db_committed = True
        commit_staged_seed_rotation(settings.seed_path)
        # Drop any cached key from the old seed so subsequent calls in
        # this process re-derive from the fresh seed file.
        configure_seed_path(settings.seed_path)
        typer.echo(f"rotate-seed: rotated {len(rows)} row(s); old seed → seed.bin.bak")
    except Exception:
        if not db_committed:
            abort_staged_seed_rotation(settings.seed_path)
        raise
    finally:
        engine.dispose()


@app.command(name="rotate-token")
def rotate_token(
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Required — rotating without confirmation changes the daemon bearer token.",
        ),
    ] = False,
) -> None:
    """Rotate the daemon bearer token and refresh bridge MCP configs.

    Writes a fresh 32 bytes to ``auth.token`` (mode 0600), then re-registers
    Codex + Claude Code so both clients keep using the local stdio bridge. The
    token itself is not stored in agent MCP configs. A daemon that is already
    running keeps accepting the token it loaded at startup until it is restarted.
    """
    if not yes:
        typer.echo(
            "error: rotate-token requires --yes (rotating changes the daemon bearer token).",
            err=True,
        )
        raise typer.Exit(code=2)

    import secrets

    from content_stack import install as installer

    settings = get_settings()
    settings.ensure_dirs()
    new_token = secrets.token_hex(32)
    token_path = settings.token_path
    # Write under temp + rename for atomicity, mode 0600 enforced.
    fd = os.open(
        str(token_path) + ".new",
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new_token)
        f.write("\n")
    os.replace(str(token_path) + ".new", token_path)

    # Re-register to keep agent MCP clients on the stdio bridge path.
    msg = installer.register_mcp_codex(port=settings.port, force=True)
    typer.echo(msg)
    msg = installer.register_mcp_claude(port=settings.port)
    typer.echo(msg)
    typer.echo("rotate-token: token rotated; MCP configs updated.")
    typer.echo("rotate-token: run `content-stack restart` so the daemon loads the new token.")


@app.command()
def backup() -> None:
    """Atomic SQLite .backup + copy seed/auth-token (M9)."""
    _stub("M9: jobs/scheduling", "backup")


@app.command()
def restore(
    file: Annotated[Path, typer.Argument(help="Path to a .db backup")],
) -> None:
    """Halt daemon, restore DB from backup, restart (M9)."""
    _ = file
    _stub("M9: jobs/scheduling", "restore")


# Re-export Settings on the module so tests can `from content_stack.cli import Settings`
# (handy for end-to-end smoke tests that want to assert on env-derived paths).
__all__ = ["Settings", "app"]
