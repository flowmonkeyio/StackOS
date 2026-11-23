"""Shared executable discovery for MCP host CLIs."""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from threading import Lock

SHELL_DISCOVERY_TIMEOUT_SECONDS = 3
SUPPORTED_LOGIN_SHELLS = {"bash", "sh", "zsh"}
_LOGIN_SHELL_PATH_TTL_SECONDS = 30.0
_login_shell_path_lock = Lock()
_login_shell_path_cache: dict[tuple[str, str, str], tuple[float, str | None]] = {}


def resolve_cli_bin(
    command_name: str,
    *,
    env_var: str | None = None,
    explicit: str | None = None,
    common_candidates: Iterable[str] = (),
    app_bundle_candidates: Iterable[str] = (),
) -> str | None:
    """Find a host CLI from the current PATH, known installs, or a login shell."""

    return next(
        iter_cli_bins(
            command_name,
            env_var=env_var,
            explicit=explicit,
            common_candidates=common_candidates,
            app_bundle_candidates=app_bundle_candidates,
        ),
        None,
    )


def iter_cli_bins(
    command_name: str,
    *,
    env_var: str | None = None,
    explicit: str | None = None,
    preferred_candidates: Iterable[str] = (),
    common_candidates: Iterable[str] = (),
    app_bundle_candidates: Iterable[str] = (),
) -> Iterator[str]:
    """Yield ordered, deduplicated executable candidates lazily.

    Login-shell discovery only runs if the caller exhausts earlier PATH,
    known-install, and app-bundle candidates.
    """

    if explicit is not None:
        resolved = executable_path_or_none(explicit)
        if resolved:
            yield resolved
        return

    if env_var:
        override = os.environ.get(env_var)
        if override is not None:
            resolved = executable_path_or_none(override)
            if resolved:
                yield resolved
            return

    seen: set[str] = set()

    def maybe(candidate: str | None) -> str | None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            return candidate
        return None

    for candidate in expand_candidate_paths(preferred_candidates):
        found = maybe(executable_path_or_none(candidate))
        if found:
            yield found
    found = maybe(shutil.which(command_name))
    if found:
        yield found

    for candidate in expand_candidate_paths(common_candidates):
        found = maybe(executable_path_or_none(candidate))
        if found:
            yield found

    for candidate in expand_candidate_paths(app_bundle_candidates):
        found = maybe(executable_path_or_none(candidate))
        if found:
            yield found

    found = maybe(discover_with_login_shell(command_name))
    if found:
        yield found


def resolve_cli_bins(
    command_name: str,
    *,
    env_var: str | None = None,
    explicit: str | None = None,
    preferred_candidates: Iterable[str] = (),
    common_candidates: Iterable[str] = (),
    app_bundle_candidates: Iterable[str] = (),
) -> list[str]:
    """Return ordered, deduplicated executable candidates for a host CLI."""

    return list(
        iter_cli_bins(
            command_name,
            env_var=env_var,
            explicit=explicit,
            preferred_candidates=preferred_candidates,
            common_candidates=common_candidates,
            app_bundle_candidates=app_bundle_candidates,
        )
    )


def subprocess_env_for_cli(cli_bin: str) -> dict[str, str]:
    env = os.environ.copy()
    parent = str(Path(cli_bin).expanduser().parent)
    path = env.get("PATH", "")
    parts = [parent, *[part for part in path.split(os.pathsep) if part]]
    env["PATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env


def executable_identity(cli_bin: str) -> tuple[int, ...] | None:
    """File identity for one observation, without executing another native read."""
    try:
        info = Path(cli_bin).stat()
    except OSError:
        return None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def probe_cli_version(cli_bin: str) -> tuple[str, str | None]:
    """Bounded, noninteractive runnable evidence; unknown version is not absence."""
    try:
        result = subprocess.run(
            [cli_bin, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=3,
            env=subprocess_env_for_cli(cli_bin),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable", None
    if result.returncode != 0:
        return "unavailable", None
    # Never publish arbitrary stdout, paths, account identity or diagnostics.
    version = re.search(
        r"(?<![\w.])\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?(?![\w.])", result.stdout[:4096]
    )
    return "available", version[0] if version else None


def _login_shell_path(shell: str) -> str | None:
    key = (shell, os.environ.get("PATH", ""), os.environ.get("HOME", ""))
    now = time.monotonic()
    with _login_shell_path_lock:
        cached = _login_shell_path_cache.get(key)
        if cached is not None and now - cached[0] < _LOGIN_SHELL_PATH_TTL_SECONDS:
            return cached[1]
    try:
        result = subprocess.run(
            [shell, "-lc", 'printf "%s" "$PATH"'],
            capture_output=True,
            text=True,
            check=False,
            timeout=SHELL_DISCOVERY_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    resolved = result.stdout.strip() if result.returncode == 0 else ""
    value = resolved or None
    with _login_shell_path_lock:
        _login_shell_path_cache[key] = (time.monotonic(), value)
    return value


def discover_with_login_shell(command_name: str) -> str | None:
    shell = os.environ.get("SHELL")
    if not shell:
        return None
    shell_name = Path(shell).name
    if shell_name not in SUPPORTED_LOGIN_SHELLS:
        return None
    login_path = _login_shell_path(shell)
    if login_path:
        found = shutil.which(command_name, path=login_path)
        if found:
            return executable_path_or_none(found)
    return None


def expand_candidate_paths(candidates: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for candidate in candidates:
        expanded = str(Path(candidate).expanduser())
        matches = glob.glob(expanded) if glob.has_magic(expanded) else [expanded]
        paths.extend(Path(match) for match in sorted(matches, key=_natural_path_key, reverse=True))
    return paths


def _natural_path_key(value: str) -> str:
    return re.sub(r"\d+", lambda match: f"{int(match.group()):020d}", value.lower())


def executable_path_or_none(candidate: str | Path) -> str | None:
    raw = str(candidate).strip()
    if not raw:
        return None
    if os.path.sep not in raw:
        return shutil.which(raw)
    path = Path(raw).expanduser()
    try:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    except OSError:
        return None
    return None
