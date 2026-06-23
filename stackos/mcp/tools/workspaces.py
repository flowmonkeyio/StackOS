"""workspace.* MCP tools for plugin-first repo binding.

These tools are intentionally daemon-owned. A plugin-provided MCP bridge sends
cwd/git/framework hints from the current website repository; the daemon stores
the binding and session metadata in the singleton DB.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import ConfigDict

from stackos.config import get_settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.workspaces import (
    AgentSessionOut,
    WorkspaceBindingOut,
    WorkspaceBootstrapOut,
    WorkspaceProjectCandidateOut,
    WorkspaceRepository,
    WorkspaceResolutionOut,
)


def _loopback_base_url() -> str:
    settings = get_settings()
    host = settings.host
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{display_host}:{settings.port}"


def _ui_urls(ui_paths: dict[str, str]) -> dict[str, str]:
    base = _loopback_base_url()
    return {
        key: f"{base}{path if path.startswith('/') else '/' + path}"
        for key, path in ui_paths.items()
    }


def _ui_health() -> dict[str, Any]:
    base = _loopback_base_url()
    return {
        "base_url": base,
        "daemon_reached": True,
        "check_source": "mcp_daemon_response",
        "meaning": (
            "This MCP response came from the StackOS daemon. UI routes use the same "
            "loopback host and port unless the operator configured a different local port."
        ),
    }


def _with_candidate_urls(
    candidates: list[WorkspaceProjectCandidateOut],
) -> list[WorkspaceProjectCandidateOut]:
    return [
        candidate.model_copy(update={"ui_urls": _ui_urls(candidate.ui_paths)})
        for candidate in candidates
    ]


def _with_ui_context[
    T: AgentSessionOut | WorkspaceBootstrapOut | WorkspaceResolutionOut,
](payload: T) -> T:
    update: dict[str, Any] = {
        "ui_urls": _ui_urls(payload.ui_paths),
        "ui_health": _ui_health(),
    }
    if isinstance(payload, (AgentSessionOut, WorkspaceResolutionOut)):
        update["candidate_projects"] = _with_candidate_urls(payload.candidate_projects)
    return cast(T, payload.model_copy(update=update))


class WorkspaceResolveInput(MCPInput):
    """Resolve current repo hints to a StackOS project binding."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"repo_fingerprint": "git:abc123"}},
    )

    repo_fingerprint: str | None = None
    git_remote_url: str | None = None
    cwd: str | None = None


class WorkspaceConnectInput(MCPInput):
    """Create/update a daemon-owned repo binding without writing repo files."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_slug": "acme",
                "repo_fingerprint": "git:abc123",
                "git_remote_url": "git@github.com:org/site.git",
                "framework": "nuxt",
                "rebind_existing": False,
            }
        },
    )

    project_id: int | None = None
    project_slug: str | None = None
    project_name: str | None = None
    repo_fingerprint: str
    git_remote_url: str | None = None
    normalized_repo_name: str | None = None
    last_known_root: str | None = None
    framework: str | None = None
    content_model_json: dict[str, Any] | None = None
    rebind_existing: bool = False


class WorkspaceBootstrapInput(MCPInput):
    """Ensure one StackOS project and binding for the current workspace root."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "cwd": "/Users/me/Sites/example",
                "repo_fingerprint": "path:abc123",
                "git_remote_url": "git@github.com:org/site.git",
            }
        },
    )

    repo_fingerprint: str | None = None
    git_remote_url: str | None = None
    normalized_repo_name: str | None = None
    cwd: str | None = None
    last_known_root: str | None = None
    framework: str | None = None
    content_model_json: dict[str, Any] | None = None
    project_id: int | None = None
    project_slug: str | None = None
    project_name: str | None = None
    domain: str | None = None
    niche: str | None = None
    locale: str = "en-US"
    rebind_existing: bool = False


class WorkspaceListBindingsInput(MCPInput):
    """List daemon-owned workspace bindings."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None


class WorkspaceUpdateProfileInput(MCPInput):
    """Patch detected content-model hints for a binding."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "binding_id": 1,
                "framework": "nuxt",
                "content_model_json": {"primary_resource": "content-piece"},
            }
        },
    )

    binding_id: int
    project_id: int | None = None
    framework: str | None = None
    content_model_json: dict[str, Any] | None = None


class WorkspaceStartSessionInput(MCPInput):
    """Register one plugin MCP bridge session and ensure project binding by default."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "runtime": "codex",
                "cwd": "/Users/me/Sites/example",
                "repo_fingerprint": "git:abc123",
            }
        },
    )

    runtime: str
    cwd: str | None = None
    repo_fingerprint: str | None = None
    git_remote_url: str | None = None
    thread_id: str | None = None
    client_session_id: str | None = None
    auto_bootstrap: bool = True


async def _workspace_resolve(
    inp: WorkspaceResolveInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WorkspaceResolutionOut:
    resolved = WorkspaceRepository(ctx.session).resolve(
        repo_fingerprint=inp.repo_fingerprint,
        git_remote_url=inp.git_remote_url,
        cwd=inp.cwd,
    )
    return _with_ui_context(resolved)


async def _workspace_connect(
    inp: WorkspaceConnectInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[WorkspaceBindingOut]:
    project = ProjectRepository(ctx.session).resolve_identifier(
        project_id=inp.project_id,
        project_slug=inp.project_slug,
        project_name=inp.project_name,
    )
    env = WorkspaceRepository(ctx.session).connect(
        project_id=project.id,
        repo_fingerprint=inp.repo_fingerprint,
        git_remote_url=inp.git_remote_url,
        normalized_repo_name=inp.normalized_repo_name,
        last_known_root=inp.last_known_root,
        framework=inp.framework,
        content_model_json=inp.content_model_json,
        rebind_existing=inp.rebind_existing,
    )
    return WriteEnvelope[WorkspaceBindingOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _workspace_bootstrap(
    inp: WorkspaceBootstrapInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[WorkspaceBootstrapOut]:
    env = WorkspaceRepository(ctx.session).bootstrap(
        repo_fingerprint=inp.repo_fingerprint,
        git_remote_url=inp.git_remote_url,
        normalized_repo_name=inp.normalized_repo_name,
        cwd=inp.cwd,
        last_known_root=inp.last_known_root,
        framework=inp.framework,
        content_model_json=inp.content_model_json,
        project_id=inp.project_id,
        project_slug=inp.project_slug,
        project_name=inp.project_name,
        domain=inp.domain,
        niche=inp.niche,
        locale=inp.locale,
        rebind_existing=inp.rebind_existing,
    )
    return WriteEnvelope[WorkspaceBootstrapOut](
        data=_with_ui_context(env.data),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _workspace_list_bindings(
    inp: WorkspaceListBindingsInput, ctx: MCPContext, _emit: ProgressEmitter
) -> list[WorkspaceBindingOut]:
    return WorkspaceRepository(ctx.session).list_bindings(project_id=inp.project_id)


async def _workspace_update_profile(
    inp: WorkspaceUpdateProfileInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[WorkspaceBindingOut]:
    env = WorkspaceRepository(ctx.session).update_profile(
        inp.binding_id,
        project_id=inp.project_id,
        framework=inp.framework,
        content_model_json=inp.content_model_json,
    )
    return WriteEnvelope[WorkspaceBindingOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def _workspace_start_session(
    inp: WorkspaceStartSessionInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[AgentSessionOut]:
    env = WorkspaceRepository(ctx.session).start_session(
        runtime=inp.runtime,
        cwd=inp.cwd,
        repo_fingerprint=inp.repo_fingerprint,
        git_remote_url=inp.git_remote_url,
        thread_id=inp.thread_id,
        client_session_id=inp.client_session_id,
        auto_bootstrap=inp.auto_bootstrap,
    )
    return WriteEnvelope[AgentSessionOut](
        data=_with_ui_context(env.data),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


def register(registry: ToolRegistry) -> None:
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        (
            "workspace.resolve",
            "workspace.connect",
            "workspace.bootstrap",
            "workspace.listBindings",
            "workspace.updateProfile",
            "workspace.startSession",
        ),
    )


__all__ = ["register"]
