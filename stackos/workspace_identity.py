"""Workspace identity helpers shared by MCP bridge and daemon repositories."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

LOW_CONFIDENCE_PROJECT_NAMES = frozenset({"contents", "macos", "project", "resources"})
FILESYSTEM_ROOT = str(Path("/").resolve())
FILESYSTEM_ROOT_FINGERPRINT = (
    f"path:{hashlib.sha256(FILESYSTEM_ROOT.encode('utf-8')).hexdigest()[:24]}"
)


def normalize_path(value: str | Path | None) -> str | None:
    if not value:
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except OSError:
        return str(Path(value).expanduser().absolute())


def is_usable_workspace_root(path: str | Path | None) -> bool:
    normalized = normalize_path(path)
    if not normalized:
        return False
    resolved = Path(normalized)
    if resolved.parent == resolved:
        return False
    return not any(part.lower().endswith(".app") for part in resolved.parts)


def path_fingerprint(path: str | Path | None) -> str | None:
    normalized = normalize_path(path)
    if not normalized or not is_usable_workspace_root(normalized):
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"path:{digest}"


def is_filesystem_root_fingerprint(value: str | None) -> bool:
    return value == FILESYSTEM_ROOT_FINGERPRINT


def repo_name_from_remote(remote: str | None) -> str | None:
    if not remote:
        return None
    trimmed = remote.rstrip("/")
    if ":" in trimmed and "://" not in trimmed:
        trimmed = trimmed.split(":", 1)[1]
    if "://" in trimmed:
        trimmed = trimmed.split("://", 1)[1]
        parts = trimmed.split("/", 1)
        trimmed = parts[1] if len(parts) > 1 else parts[0]
    if trimmed.endswith(".git"):
        trimmed = trimmed[:-4]
    if "/" in trimmed:
        trimmed = trimmed.rsplit("/", 1)[-1]
    return trimmed or None


def repo_name_from_root(root: str | Path | None) -> str | None:
    normalized = normalize_path(root)
    if not normalized:
        return None
    return Path(normalized).name or None


def is_low_confidence_project_name(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return (
        normalized in LOW_CONFIDENCE_PROJECT_NAMES
        or normalized.endswith(".app")
        or not slug
        or slug in LOW_CONFIDENCE_PROJECT_NAMES
    )


def project_name_candidate(value: str | None) -> str | None:
    if not value:
        return None
    candidate = re.split(r"[/\\]+", value.strip().rstrip("/\\"))[-1]
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    candidate = candidate.strip()
    return None if is_low_confidence_project_name(candidate) else candidate or None


def derive_project_name(
    *,
    normalized_repo_name: str | None,
    git_remote_url: str | None,
    workspace_root: str | Path | None,
) -> str | None:
    """Derive a safe project name from explicit directory metadata."""

    return (
        project_name_candidate(normalized_repo_name)
        or project_name_candidate(repo_name_from_remote(git_remote_url))
        or project_name_candidate(repo_name_from_root(workspace_root))
    )
