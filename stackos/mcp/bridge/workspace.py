"""Workspace and project scoping policy for bridge tool calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .catalog import _bridge_tool_accepts_project_id
from .constants import _AGENT_GLOBAL_DISCOVERY_TOOL_NAMES
from .protocol import _bridge_as_int

_PROJECT_SCOPE_INJECTION_EXCLUDED_TOOL_NAMES = {
    "workspace.bootstrap",
    "workspace.connect",
}
_PROJECT_SCOPE_IDENTITY_ARGUMENT_NAMES = {
    "project_id",
    "project_slug",
    "project_name",
    "workspace_alias",
}


def _bridge_scoped_arguments(
    *,
    catalog: dict[str, dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    scoped_project_id: int | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    has_explicit_project_or_workspace_identity = (
        tool_name in _PROJECT_SCOPE_INJECTION_EXCLUDED_TOOL_NAMES
        and any(arguments.get(name) is not None for name in _PROJECT_SCOPE_IDENTITY_ARGUMENT_NAMES)
    )
    if (
        scoped_project_id is None
        or has_explicit_project_or_workspace_identity
        or not _bridge_tool_accepts_project_id(catalog, tool_name)
    ):
        return dict(arguments), None
    current = arguments.get("project_id")
    if current is None:
        merged = dict(arguments)
        merged["project_id"] = scoped_project_id
        return merged, None
    current_id = _bridge_as_int(current)
    if current_id == scoped_project_id:
        return dict(arguments), None
    return None, {
        "tool": tool_name,
        "scoped_project_id": scoped_project_id,
        "requested_project_id": current,
    }


def _bridge_normalized_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except OSError:
        return str(Path(value).expanduser().absolute())


def _bridge_path_is_same_or_child(path: str, root: str) -> bool:
    normalized_path = _bridge_normalized_path(path)
    normalized_root = _bridge_normalized_path(root)
    return normalized_path == normalized_root or normalized_path.startswith(
        normalized_root.rstrip("/") + "/"
    )


def _bridge_exact_path_match(path: str, expected: str) -> bool:
    return _bridge_normalized_path(path) == _bridge_normalized_path(expected)


def _bridge_apply_expected_argument(
    *,
    out: dict[str, Any],
    tool_name: str,
    field: str,
    expected: str | None,
    path_policy: str | None = None,
) -> dict[str, Any] | None:
    if expected is None:
        return None
    requested = out.get(field)
    if requested is None:
        out[field] = expected
        return None
    if not isinstance(requested, str):
        return {"tool": tool_name, "field": field, "expected": expected, "requested": requested}
    if path_policy == "same-or-child":
        matches = _bridge_path_is_same_or_child(requested, expected)
    elif path_policy == "exact":
        matches = _bridge_exact_path_match(requested, expected)
    else:
        matches = requested == expected
    if matches:
        return None
    return {"tool": tool_name, "field": field, "expected": expected, "requested": requested}


def _bridge_workspace_scoped_arguments(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    runtime: str,
    cwd: str | None,
    repo_fingerprint: str | None,
    git_remote_url: str | None,
    client_session_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if tool_name not in {
        "workspace.resolve",
        "workspace.startSession",
        "workspace.bootstrap",
        "workspace.connect",
    }:
        return dict(arguments), None

    out = dict(arguments)
    has_host_workspace_hints = any((cwd, repo_fingerprint, git_remote_url))
    if not has_host_workspace_hints and tool_name in {
        "workspace.resolve",
        "workspace.startSession",
        "workspace.bootstrap",
        "workspace.connect",
    }:
        synthetic_anchor_fields = {
            "cwd",
            "last_known_root",
            "repo_fingerprint",
            "git_remote_url",
            "normalized_repo_name",
        }
        supplied = sorted(
            field for field in synthetic_anchor_fields if arguments.get(field) is not None
        )
        if supplied:
            allowed_no_hint_fields = (
                ["workspace_alias"]
                if tool_name in {"workspace.resolve", "workspace.startSession"}
                else ["workspace_alias", "project_id", "project_slug", "project_name"]
            )
            return None, {
                "tool": tool_name,
                "reason": "workspace_anchor_missing_from_host",
                "detail": (
                    "The agent host did not provide a workspace directory, repo "
                    "fingerprint, or git remote. Do not invent directory/repo anchors "
                    "from a desktop/global session; bind by workspace_alias or an "
                    "explicit project identifier instead."
                ),
                "supplied_fields": supplied,
                "allowed_no_hint_fields": allowed_no_hint_fields,
                "repair": {
                    "existing_project": (
                        "Use workspace.connect with project_id, project_slug, "
                        "project_name, or an existing workspace_alias."
                    ),
                    "new_project": (
                        "Use workspace.bootstrap with project_name, project_slug, "
                        "or workspace_alias."
                    ),
                },
            }
    checks: list[tuple[str, str | None, str | None]] = []
    if tool_name in {"workspace.resolve", "workspace.startSession", "workspace.bootstrap"}:
        checks.extend(
            [
                ("cwd", cwd, "same-or-child"),
                ("repo_fingerprint", repo_fingerprint, None),
                ("git_remote_url", git_remote_url, None),
            ]
        )
    if tool_name == "workspace.startSession":
        out.setdefault("runtime", runtime)
        checks.append(("client_session_id", client_session_id, None))
    if tool_name in {"workspace.connect", "workspace.bootstrap"}:
        checks.extend(
            [
                ("repo_fingerprint", repo_fingerprint, None),
                ("git_remote_url", git_remote_url, None),
                ("last_known_root", cwd, "exact"),
            ]
        )

    for field, expected, path_policy in checks:
        error = _bridge_apply_expected_argument(
            out=out,
            tool_name=tool_name,
            field=field,
            expected=expected,
            path_policy=path_policy,
        )
        if error is not None:
            return None, error
    return out, None


def _bridge_scope_visibility_error(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    has_workspace_hints: bool,
    scoped_project_id: int | None,
    workspace_scope_error: str | None,
) -> dict[str, Any] | None:
    if not has_workspace_hints:
        return None
    if workspace_scope_error is not None and tool_name not in {
        "workspace.bootstrap",
        "workspace.connect",
        "workspace.resolve",
        "workspace.startSession",
    }:
        return {
            "tool": tool_name,
            "reason": "workspace_scope_failed",
            "detail": workspace_scope_error,
        }
    if scoped_project_id is not None:
        return None
    if tool_name in {"workspace.bootstrap", "workspace.connect"}:
        return None
    if tool_name in _AGENT_GLOBAL_DISCOVERY_TOOL_NAMES and arguments.get("project_id") is None:
        return None
    return {
        "tool": tool_name,
        "reason": "workspace_not_connected",
        "hint": "Bind this repository with workspace.connect before using project-scoped tools.",
    }
