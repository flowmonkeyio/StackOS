"""Shared executable discovery for MCP host CLIs."""

from __future__ import annotations

import glob
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

SHELL_DISCOVERY_TIMEOUT_SECONDS = 3
SUPPORTED_LOGIN_SHELLS = {"bash", "sh", "zsh"}


def resolve_cli_bin(
    command_name: str,
    *,
    env_var: str | None = None,
    explicit: str | None = None,
    common_candidates: Iterable[str] = (),
    app_bundle_candidates: Iterable[str] = (),
) -> str | None:
    """Find a host CLI from the current PATH, known installs, or a login shell."""

    if explicit is not None:
        return executable_path_or_none(explicit)

    if env_var:
        override = os.environ.get(env_var)
        if override is not None:
            return executable_path_or_none(override)

    from_path = shutil.which(command_name)
    if from_path:
        return from_path

    for candidate in expand_candidate_paths(common_candidates):
        resolved = executable_path_or_none(candidate)
        if resolved:
            return resolved

    for candidate in expand_candidate_paths(app_bundle_candidates):
        resolved = executable_path_or_none(candidate)
        if resolved:
            return resolved

    from_login_shell = discover_with_login_shell(command_name)
    if from_login_shell:
        return from_login_shell
    return None


def subprocess_env_for_cli(cli_bin: str) -> dict[str, str]:
    env = os.environ.copy()
    parent = str(Path(cli_bin).expanduser().parent)
    path = env.get("PATH", "")
    parts = [parent, *[part for part in path.split(os.pathsep) if part]]
    env["PATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env


def discover_with_login_shell(command_name: str) -> str | None:
    shell = os.environ.get("SHELL")
    if not shell:
        return None
    shell_name = Path(shell).name
    if shell_name not in SUPPORTED_LOGIN_SHELLS:
        return None
    try:
        result = subprocess.run(
            [shell, "-lc", f"command -v {shlex.quote(command_name)}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=SHELL_DISCOVERY_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    return executable_path_or_none(lines[0])


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
