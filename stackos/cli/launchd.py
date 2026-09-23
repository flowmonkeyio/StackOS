"""macOS launchd lifecycle services for the StackOS daemon."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

from stackos.config import Settings

from . import daemon_processes
from .constants import _DESKTOP_BUNDLE_IDENTIFIER, _LAUNCHD_LABEL


@dataclass(frozen=True)
class _LaunchdDaemonContext:
    data_dir: Path
    state_dir: Path
    host: str
    port: int


@dataclass(frozen=True)
class _LaunchdContextOwnership:
    """The installed launchd service and the context it declares, if parseable."""

    plist_path: Path | None
    persisted: _LaunchdDaemonContext | None
    matches: bool


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


def _launchd_bootout(
    plist_path: Path,
    *,
    wait_timeout: float = 5.0,
) -> tuple[bool, str]:
    loaded, _ = _launchd_loaded()
    if loaded:
        try:
            service = _launchd_service()
        except RuntimeError as exc:
            return False, str(exc)
        ok, message = _launchctl(["bootout", service])
        if not ok:
            return ok, message
        if not _wait_for_launchd_unloaded(timeout=wait_timeout):
            return False, "launchd job did not unload before timeout"
        return ok, message
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


def _absolute_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return None
        return path.resolve(strict=False)
    except (OSError, ValueError):
        return None


def _program_option(arguments: list[str], option: str) -> str | None:
    values: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == option:
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
                return None
            values.append(arguments[index + 1])
            index += 2
            continue
        if argument.startswith(f"{option}="):
            value = argument.removeprefix(f"{option}=")
            if not value:
                return None
            values.append(value)
        index += 1
    return values[0] if len(values) == 1 else None


def _port(value: object) -> int | None:
    if not isinstance(value, str) or not value.isdecimal():
        return None
    try:
        port = int(value)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def _read_launchd_context(plist_path: Path) -> _LaunchdDaemonContext | None:
    try:
        payload: Any = plistlib.loads(plist_path.read_bytes())
    except (ExpatError, OSError, ValueError, plistlib.InvalidFileException):
        return None
    if not isinstance(payload, dict) or payload.get("Label") != _LAUNCHD_LABEL:
        return None

    environment = payload.get("EnvironmentVariables")
    arguments = payload.get("ProgramArguments")
    if (
        not isinstance(environment, dict)
        or not isinstance(arguments, list)
        or not all(isinstance(argument, str) for argument in arguments)
        or len(arguments) < 4
        or arguments[1:4] != ["-m", "stackos", "serve"]
    ):
        return None

    argument_host = _program_option(arguments, "--host")
    argument_port = _port(_program_option(arguments, "--port"))
    environment_host = environment.get("STACKOS_HOST")
    environment_port = _port(environment.get("STACKOS_PORT"))
    data_dir = _absolute_path(environment.get("STACKOS_DATA_DIR"))
    state_dir = _absolute_path(environment.get("STACKOS_STATE_DIR"))
    if (
        not isinstance(environment_host, str)
        or not argument_host
        or not daemon_processes._is_loopback_host(argument_host)
        or environment_host != argument_host
        or argument_port is None
        or environment_port != argument_port
        or data_dir is None
        or state_dir is None
    ):
        return None
    return _LaunchdDaemonContext(
        data_dir=data_dir,
        state_dir=state_dir,
        host=argument_host,
        port=argument_port,
    )


def _launchd_context_ownership(
    home: Path,
    *,
    settings: Settings,
    host: str,
    port: int,
) -> _LaunchdContextOwnership:
    """Return launchd ownership only when its persisted context is unambiguous."""
    plist_path = _installed_launchd_plist(home)
    if plist_path is None:
        return _LaunchdContextOwnership(None, None, False)

    persisted = _read_launchd_context(plist_path)
    data_dir = _absolute_path(str(settings.data_dir))
    state_dir = _absolute_path(str(settings.state_dir))
    matches = (
        persisted is not None
        and data_dir is not None
        and state_dir is not None
        and persisted.data_dir == data_dir
        and persisted.state_dir == state_dir
        and persisted.host == host
        and persisted.port == port
    )
    return _LaunchdContextOwnership(plist_path, persisted, matches)


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
        "ProgramArguments": daemon_processes._daemon_args(host, port, log_level),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "WorkingDirectory": str(home),
        "StandardOutPath": str(settings.log_path),
        "StandardErrorPath": str(settings.log_path),
        "EnvironmentVariables": environment,
    }
    if "STACKOS_PACKAGED_CLI" in environment:
        payload["AssociatedBundleIdentifiers"] = [_DESKTOP_BUNDLE_IDENTIFIER]
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


__all__ = [
    "_install_launchd_autostart",
    "_installed_launchd_plist",
    "_launchctl",
    "_launchd_bootout",
    "_launchd_bootstrap",
    "_launchd_context_ownership",
    "_launchd_domain",
    "_launchd_loaded",
    "_launchd_plist_content",
    "_launchd_plist_path",
    "_launchd_service",
    "_loaded_launchd_plist",
    "_uninstall_launchd_autostart",
]
