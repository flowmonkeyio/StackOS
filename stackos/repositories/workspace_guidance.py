"""Pure response guidance for workspace binding and setup flows."""

from __future__ import annotations

from typing import Any, Protocol


class _WorkspaceBindingGuidance(Protocol):
    id: int
    project_id: int
    framework: str | None
    content_model_json: dict[str, Any] | None


def _ui_paths(project_id: int | None) -> dict[str, str]:
    if project_id is None:
        return {"projects": "/"}
    return {
        "setup": f"/projects/{project_id}/setup",
        "connections": f"/projects/{project_id}/connections",
        "tasks": f"/projects/{project_id}/tasks",
        "workflow_templates": f"/projects/{project_id}/workflow-templates",
    }


def _profile_update_suggestion(
    *,
    binding: _WorkspaceBindingGuidance,
    profile_missing: list[str],
) -> dict[str, Any]:
    recommended_arguments: dict[str, Any] = {
        "binding_id": binding.id,
        "project_id": binding.project_id,
    }
    if "framework" in profile_missing:
        recommended_arguments["framework"] = "<detected-framework-or-stack>"
    if "content_model_json" in profile_missing:
        recommended_arguments["content_model_json"] = {
            "project_type": "saas|content|commerce|internal-tool|other",
            "primary_objects": ["customer", "account", "project"],
            "primary_workflows": [
                "Name the durable product/workflow concepts agents should understand."
            ],
            "content_heavy": False,
            "notes": "Replace this with project-specific model hints after reading repo docs.",
        }
    return {
        "tool": "workspace.updateProfile",
        "call_via": "toolbox.call",
        "recommended_arguments": recommended_arguments,
        "guidance": [
            "This is an adaptation hint, not a setup blocker.",
            (
                "Populate framework from local repo evidence such as README, AGENTS, "
                "package files, or build config."
            ),
            (
                "For non-content SaaS/internal tools, describe domain objects and workflows "
                "instead of forcing a content taxonomy."
            ),
        ],
    }


def _setup_state(
    *,
    binding: _WorkspaceBindingGuidance | None,
    needs_connect: bool,
    auto_bootstrap: bool | None = None,
) -> dict[str, Any]:
    if needs_connect or binding is None:
        return {
            "state": "needs_workspace_binding",
            "workspace_bound": False,
            "project_scoped_tools_usable": False,
            "profile_complete": False,
            "profile_missing": ["workspace_binding"],
            "meaning": (
                "No project-scoped StackOS operations are available until this workspace is bound."
            ),
        }
    profile_missing: list[str] = []
    if not binding.framework:
        profile_missing.append("framework")
    if binding.content_model_json is None:
        profile_missing.append("content_model_json")
    out: dict[str, Any] = {
        "state": "bound_profile_incomplete" if profile_missing else "bound_profile_configured",
        "workspace_bound": True,
        "project_scoped_tools_usable": True,
        "profile_complete": not profile_missing,
        "profile_missing": profile_missing,
        "meaning": (
            "The workspace is bound and project-scoped tools are usable. "
            "Profile fields are adaptation hints for agents and workflows, not a blocker."
        ),
        "auto_bootstrap": auto_bootstrap,
    }
    if profile_missing:
        out["profile_update_suggestion"] = _profile_update_suggestion(
            binding=binding,
            profile_missing=profile_missing,
        )
    return out


def _repo_hints(
    *,
    repo_fingerprint: str | None,
    git_remote_url: str | None,
    cwd: str | None,
    normalized_cwd: str | None,
    workspace_root_usable: bool,
) -> dict[str, Any]:
    hints = {
        "repo_fingerprint": repo_fingerprint,
        "git_remote_url": git_remote_url,
        "cwd": cwd,
        "normalized_cwd": normalized_cwd,
        "fingerprint_format": (
            "The StackOS bridge sends path:<sha256(workspace_root)[:24]> by default; "
            "git:<stable-repo-id> is also accepted when a host supplies it."
        ),
    }
    if normalized_cwd and not workspace_root_usable:
        hints["workspace_hint_warning"] = (
            "The MCP host did not provide a usable project directory; filesystem root "
            "and app bundle paths are not treated as StackOS workspaces."
        )
    return hints


def _connect_required_next_step(
    *,
    repo_fingerprint: str | None,
    git_remote_url: str | None,
    cwd: str | None,
    project_identity_required: bool,
    has_workspace_identity: bool,
) -> dict[str, Any]:
    bootstrap_args: dict[str, Any] = {}
    if repo_fingerprint:
        bootstrap_args["repo_fingerprint"] = repo_fingerprint
    if git_remote_url:
        bootstrap_args["git_remote_url"] = git_remote_url
    if cwd:
        bootstrap_args["cwd"] = cwd
    if project_identity_required:
        bootstrap_args["project_name"] = "<operator-provided project name>"
    if project_identity_required and not has_workspace_identity:
        return {
            "status": "project_selection_required",
            "call_via": "toolbox.call",
            "project_identity_required": True,
            "project_identity_guidance": (
                "The host did not provide a reliable directory, repo fingerprint, or git "
                "remote. Choose an existing named workspace/project from user intent, or "
                "ask for the business project name before creating one."
            ),
            "recommended_arguments": {
                "workspace_alias": "<business-project-alias>",
                "project_name": "<operator-provided project name>",
            },
            "why": (
                "Desktop/global hosts can start without filesystem identity. StackOS will "
                "not use last-used/global fallback; an agent must explicitly connect or "
                "bootstrap the intended project."
            ),
            "options": [
                {
                    "tool": "workspace.connect",
                    "when": (
                        "Reuse a known named workspace or intentionally selected existing project."
                    ),
                    "arguments": {
                        "workspace_alias": "<known alias>",
                        "project_id": "<selected existing project id>",
                    },
                },
                {
                    "tool": "workspace.bootstrap",
                    "when": (
                        "Create a new named desktop workspace from an "
                        "operator-provided project name."
                    ),
                    "arguments": {
                        "project_name": "<business project name>",
                        "workspace_alias": "<business-project-alias>",
                    },
                },
                {
                    "tool": "project.list",
                    "when": "Inspect projects when user intent is ambiguous.",
                },
            ],
        }
    return {
        "status": "bootstrap_required",
        "recommended_tool": "workspace.bootstrap",
        "call_via": "toolbox.call",
        "recommended_arguments": bootstrap_args,
        "project_identity_required": project_identity_required,
        "project_identity_guidance": (
            "Pass project_name or project_slug when StackOS cannot derive a reliable "
            "business project name from the host-supplied workspace directory."
            if project_identity_required
            else "StackOS can derive a project name from the current workspace metadata."
        ),
        "why": (
            "This workspace has no daemon-owned project binding yet. "
            "workspace.bootstrap explicitly creates or reuses one project for this "
            "workspace root and stores the binding."
        ),
        "alternatives": [
            {
                "tool": "project.list",
                "call_via": "toolbox.call",
                "when": "Choose an existing project intentionally before binding.",
            },
            {
                "tool": "project.create",
                "call_via": "toolbox.call",
                "when": "Create a project with explicit operator-provided metadata.",
            },
            {
                "tool": "workspace.connect",
                "call_via": "toolbox.call",
                "when": "Bind to a known project_id, project_slug, or project_name.",
            },
        ],
    }


def _connected_next_step(project_id: int) -> dict[str, Any]:
    return {
        "status": "ready",
        "call_via": "toolbox.call",
        "recommended_tools": [
            "operation.list",
            "workflowTemplate.list",
            "readiness.check",
            "agentPreset.list",
            "tracker.status",
            "auth.status",
        ],
        "ui_paths": _ui_paths(project_id),
    }
