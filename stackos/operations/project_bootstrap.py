"""Project/workspace bootstrap operation contracts."""

from __future__ import annotations

from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.projects import (
    ProjectCreateInput,
    ProjectGetInput,
    ProjectListInput,
    _project_create,
    _project_get,
    _project_list,
)
from stackos.mcp.tools.workspaces import (
    WorkspaceBootstrapInput,
    WorkspaceConnectInput,
    WorkspaceListBindingsInput,
    WorkspaceResolveInput,
    WorkspaceStartSessionInput,
    WorkspaceUpdateProfileInput,
    _workspace_bootstrap,
    _workspace_connect,
    _workspace_list_bindings,
    _workspace_resolve,
    _workspace_start_session,
    _workspace_update_profile,
)
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page
from stackos.repositories.projects import ProjectOut
from stackos.repositories.workspaces import (
    AgentSessionOut,
    WorkspaceBindingOut,
    WorkspaceBootstrapOut,
    WorkspaceResolutionOut,
)


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="project.list",
            summary="List StackOS projects for setup and project selection.",
            input_model=ProjectListInput,
            output_model=Page[ProjectOut],
            handler=_project_list,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(enabled=True, path="/api/v1/projects"),
                cli=OperationSurface(enabled=True, command="ops call project.list"),
            ),
            purpose=(
                "Use this during setup when an unbound agent needs to inspect existing "
                "projects before choosing whether to bootstrap a new workspace project."
            ),
            when_to_use=(
                "workspace.resolve returned needs_connect=true and the agent wants to reuse "
                "an existing project.",
                "A human asked the agent to connect the current repo to a named existing project.",
            ),
            returns=(
                "Safe project metadata only: id, slug, name, domain, locale, active flag, "
                "and timestamps.",
            ),
            examples=(OperationExample(title="List projects", arguments={"limit": 20}),),
            mutating=False,
            grant_policy="direct-setup-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="project.get",
            summary="Look up one StackOS project by id or slug.",
            input_model=ProjectGetInput,
            output_model=ProjectOut,
            handler=_project_get,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(enabled=True, path="/api/v1/projects/{project_id}"),
                cli=OperationSurface(enabled=True, command="ops call project.get"),
            ),
            purpose="Use this to confirm the project identity before binding a workspace.",
            when_to_use=(
                "The agent has a candidate project id or slug and wants the exact metadata.",
            ),
            returns=("One safe project record.",),
            examples=(OperationExample(title="Get project", arguments={"id_or_slug": "acme"}),),
            mutating=False,
            grant_policy="direct-setup-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="project.create",
            summary="Create a StackOS project for setup or explicit operator intent.",
            input_model=ProjectCreateInput,
            output_model=WriteEnvelope[ProjectOut],
            handler=_project_create,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(enabled=True, path="/api/v1/projects"),
                cli=OperationSurface(enabled=True, command="ops call project.create"),
            ),
            purpose=(
                "Use this only when explicit project metadata is available. For the normal "
                "agent first-run path from a repo directory, prefer workspace.bootstrap."
            ),
            when_to_use=(
                "The user/operator provided project slug, name, domain, and locale.",
                "The agent intentionally wants to create a project before binding a workspace.",
            ),
            prerequisites=(
                "Use a stable slug; project slugs are immutable.",
                "Do not store repository files or secrets in project metadata.",
            ),
            returns=("A write envelope with the created safe project record.",),
            examples=(
                OperationExample(
                    title="Create project",
                    arguments={
                        "slug": "acme",
                        "name": "Acme",
                        "domain": "example.com",
                        "locale": "en-US",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="workspace.startSession",
            summary="Register one bridge session and ensure workspace project binding.",
            input_model=WorkspaceStartSessionInput,
            output_model=WriteEnvelope[AgentSessionOut],
            handler=_workspace_start_session,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/workspace.startSession/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call workspace.startSession"),
            ),
            purpose=(
                "Use this as the normal first call for an agent running from a repo or local "
                "directory. It registers the session and, by default, creates or reuses one "
                "daemon-owned project and binding for the current workspace when none exists."
            ),
            when_to_use=(
                "An agent starts work and needs the workspace-bound project_id.",
                "A bridge session needs compact setup guidance, UI paths, and next tools.",
            ),
            prerequisites=(
                "Let the bridge inject cwd, repo_fingerprint, git_remote_url, runtime, and "
                "client_session_id when possible.",
                "Set auto_bootstrap=false only for diagnostics when no state should be created.",
            ),
            returns=(
                "A write envelope with project_id, workspace binding status, repo hints, "
                "UI paths, and next recommended toolbox calls.",
                "If auto-bootstrap created state, project_was_created and binding_was_created "
                "describe the side effects.",
            ),
            examples=(
                OperationExample(
                    title="Start current workspace session",
                    arguments={"cwd": "/Users/me/Sites/example", "runtime": "codex"},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="workspace.resolve",
            summary="Resolve current repo hints to an existing StackOS project binding.",
            input_model=WorkspaceResolveInput,
            output_model=WorkspaceResolutionOut,
            handler=_workspace_resolve,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/workspace.resolve/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call workspace.resolve"),
            ),
            purpose=(
                "Use this as the read-only first check. It never creates projects or bindings; "
                "when unbound it returns model-readable bootstrap next steps."
            ),
            when_to_use=(
                "An agent starts in a repository and needs to know whether StackOS already "
                "knows it.",
                "A caller needs candidate projects and setup guidance without mutating state.",
            ),
            returns=(
                "Existing binding and project_id when found.",
                "When unbound: needs_connect=true, candidate projects, repo hints, UI paths, "
                "and workspace.bootstrap guidance.",
            ),
            examples=(
                OperationExample(
                    title="Resolve workspace",
                    arguments={"cwd": "/Users/me/Sites/example"},
                ),
            ),
            mutating=False,
            grant_policy="direct-setup-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="workspace.bootstrap",
            summary="Ensure one project and daemon-owned binding for the current workspace.",
            input_model=WorkspaceBootstrapInput,
            output_model=WriteEnvelope[WorkspaceBootstrapOut],
            handler=_workspace_bootstrap,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/workspace.bootstrap/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call workspace.bootstrap"),
            ),
            purpose=(
                "Use this as the normal first-run agent path after workspace.resolve reports "
                "needs_connect=true. It is explicit and idempotent: repeat calls for the same "
                "workspace root/fingerprint return the existing binding instead of creating "
                "duplicates."
            ),
            when_to_use=(
                "The current repo/directory is unbound and the agent should create or reuse "
                "one project for it.",
                "The user wants StackOS setup to happen from MCP without visiting the UI first.",
            ),
            prerequisites=(
                "The bridge normally injects cwd, repo_fingerprint, git_remote_url, and "
                "last_known_root.",
                "Pass project_id/project_slug/project_name only when intentionally binding "
                "to a known project.",
                "Pass rebind_existing=true only when deliberately moving an existing "
                "workspace binding.",
            ),
            returns=(
                "A write envelope with project, binding, project_was_created, "
                "binding_was_created, repo hints, UI paths, and next tools.",
            ),
            examples=(
                OperationExample(
                    title="Bootstrap current workspace",
                    arguments={"cwd": "/Users/me/Sites/example"},
                ),
                OperationExample(
                    title="Bootstrap into known project",
                    arguments={
                        "project_slug": "acme",
                        "cwd": "/Users/me/Sites/example",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="workspace.connect",
            summary="Create/update a daemon-owned repo binding to a known project.",
            input_model=WorkspaceConnectInput,
            output_model=WriteEnvelope[WorkspaceBindingOut],
            handler=_workspace_connect,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/workspace.connect/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call workspace.connect"),
            ),
            purpose=(
                "Use this when the agent already knows the intended project id, slug, or "
                "exact name. "
                "For unbound first-run setup, workspace.bootstrap is usually simpler."
            ),
            when_to_use=(
                "The user intentionally chose an existing project.",
                "The agent needs to refresh binding metadata such as framework or root path.",
            ),
            prerequisites=(
                "Pass one of project_id, project_slug, or project_name.",
                "Pass repo_fingerprint directly or let the bridge inject it.",
                "Pass rebind_existing=true only when intentionally moving an existing "
                "repo binding to a different project.",
            ),
            returns=("A write envelope with the workspace binding.",),
            examples=(
                OperationExample(
                    title="Connect by project slug",
                    arguments={"project_slug": "acme", "repo_fingerprint": "path:abc123"},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="workspace.listBindings",
            summary="List daemon-owned workspace bindings.",
            input_model=WorkspaceListBindingsInput,
            output_model=list[WorkspaceBindingOut],
            handler=_workspace_list_bindings,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/workspace.listBindings/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call workspace.listBindings"),
            ),
            purpose=(
                "Use this read-only setup diagnostic before or after binding to understand "
                "which workspace roots/fingerprints StackOS already knows."
            ),
            when_to_use=(
                "workspace.resolve is unbound and the agent wants to inspect existing bindings.",
                "A binding conflict needs diagnosis.",
            ),
            returns=(
                "Workspace binding rows with project id, repo fingerprint, remote, and root path.",
            ),
            examples=(OperationExample(title="List bindings", arguments={}),),
            mutating=False,
            grant_policy="direct-setup-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="workspace.updateProfile",
            summary="Patch durable framework and content-model hints for a workspace binding.",
            input_model=WorkspaceUpdateProfileInput,
            output_model=WriteEnvelope[WorkspaceBindingOut],
            handler=_workspace_update_profile,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/workspace.updateProfile/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call workspace.updateProfile"),
            ),
            purpose=(
                "Use this after an agent inspects the repository and wants future agents to "
                "receive durable project adaptation hints. Missing profile fields are not "
                "setup blockers; they tell agents what local stack/content context still "
                "needs to be recorded."
            ),
            when_to_use=(
                "workspace.startSession setup_state reports missing framework or "
                "content_model_json.",
                "A project-local audit identified stack, content model, or workflow hints "
                "that should persist across agent sessions.",
            ),
            prerequisites=(
                "Call workspace.startSession or workspace.listBindings to identify the binding_id.",
                "Only store non-secret project guidance such as framework names, data model "
                "shape, docs to read, or workflow notes.",
            ),
            returns=("A write envelope with the updated workspace binding.",),
            examples=(
                OperationExample(
                    title="Record repo profile hints",
                    arguments={
                        "binding_id": 1,
                        "project_id": 1,
                        "framework": "vue",
                        "content_model_json": {
                            "primary_runtime": "StackOS daemon",
                            "ui": "Vue/Vite",
                        },
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            secret_policy="no-secret-output",
        ),
    ]


__all__ = ["operation_specs"]
