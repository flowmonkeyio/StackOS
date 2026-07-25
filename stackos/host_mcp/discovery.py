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

    candidates = resolve_cli_bins(
        command_name,
        env_var=env_var,
        explicit=explicit,
        common_candidates=common_candidates,
        app_bundle_candidates=app_bundle_candidates,
    )
    return candidates[0] if candidates else None


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

    if explicit is not None:
        resolved = executable_path_or_none(explicit)
        return [resolved] if resolved else []

    if env_var:
        override = os.environ.get(env_var)
        if override is not None:
            resolved = executable_path_or_none(override)
            return [resolved] if resolved else []

    ordered: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)

    for candidate in expand_candidate_paths(preferred_candidates):
        add(executable_path_or_none(candidate))
    from_path = shutil.which(command_name)
    add(from_path)

    for candidate in expand_candidate_paths(common_candidates):
        add(executable_path_or_none(candidate))

    for candidate in expand_candidate_paths(app_bundle_candidates):
        add(executable_path_or_none(candidate))

    from_login_shell = discover_with_login_shell(command_name)
    add(from_login_shell)
    return ordered


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
