"""workspace.* MCP tools for plugin-first repo binding.

These tools are intentionally daemon-owned. A plugin-provided MCP bridge sends
cwd/git/framework hints from the current website repository; the daemon stores
the binding and session metadata in the singleton DB.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry, ToolSpec
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.workspaces import (
    AgentSessionOut,
    WorkspaceBindingOut,
    WorkspaceRepository,
    WorkspaceResolutionOut,
)


class WorkspaceResolveInput(MCPInput):
    """Resolve current repo hints to a StackOS project binding."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"repo_fingerprint": "git:abc123"}},
    )

    repo_fingerprint: str | None = None
    git_remote_url: str | None = None


class WorkspaceConnectInput(MCPInput):
    """Create/update a daemon-owned repo binding without writing repo files."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "repo_fingerprint": "git:abc123",
                "git_remote_url": "git@github.com:org/site.git",
                "framework": "nuxt",
            }
        },
    )

    project_id: int
    repo_fingerprint: str
    git_remote_url: str | None = None
    normalized_repo_name: str | None = None
    last_known_root: str | None = None
    framework: str | None = None
    content_model_json: dict[str, Any] | None = None


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
    framework: str | None = None
    content_model_json: dict[str, Any] | None = None


class WorkspaceStartSessionInput(MCPInput):
    """Register one plugin MCP bridge session."""

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


async def _workspace_resolve(
    inp: WorkspaceResolveInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WorkspaceResolutionOut:
    return WorkspaceRepository(ctx.session).resolve(
        repo_fingerprint=inp.repo_fingerprint,
        git_remote_url=inp.git_remote_url,
    )


async def _workspace_connect(
    inp: WorkspaceConnectInput, ctx: MCPContext, _emit: ProgressEmitter
) -> WriteEnvelope[WorkspaceBindingOut]:
    env = WorkspaceRepository(ctx.session).connect(
        project_id=inp.project_id,
        repo_fingerprint=inp.repo_fingerprint,
        git_remote_url=inp.git_remote_url,
        normalized_repo_name=inp.normalized_repo_name,
        last_known_root=inp.last_known_root,
        framework=inp.framework,
        content_model_json=inp.content_model_json,
    )
    return WriteEnvelope[WorkspaceBindingOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
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
    )
    return WriteEnvelope[AgentSessionOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "workspace.resolve",
            "Resolve current repo hints to a StackOS project binding.",
            WorkspaceResolveInput,
            WorkspaceResolutionOut,
            _workspace_resolve,
        )
    )
    registry.register(
        ToolSpec(
            "workspace.connect",
            "Create/update a daemon-owned repo binding.",
            WorkspaceConnectInput,
            WriteEnvelope[WorkspaceBindingOut],
            _workspace_connect,
        )
    )
    registry.register(
        ToolSpec(
            "workspace.listBindings",
            "List daemon-owned repo bindings.",
            WorkspaceListBindingsInput,
            list[WorkspaceBindingOut],
            _workspace_list_bindings,
        )
    )
    registry.register(
        ToolSpec(
            "workspace.updateProfile",
            "Patch detected content-model hints for a binding.",
            WorkspaceUpdateProfileInput,
            WriteEnvelope[WorkspaceBindingOut],
            _workspace_update_profile,
        )
    )
    registry.register(
        ToolSpec(
            "workspace.startSession",
            "Register a plugin MCP bridge session.",
            WorkspaceStartSessionInput,
            WriteEnvelope[AgentSessionOut],
            _workspace_start_session,
        )
    )


__all__ = ["register"]
