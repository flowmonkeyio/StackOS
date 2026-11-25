"""Execution context operation contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, Field

from stackos.artifacts.json_path import select_json_path
from stackos.config import Settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.execution_contexts import (
    ExecutionContextArtifactOut,
    ExecutionContextDiscoveryOut,
    ExecutionContextOut,
    ExecutionContextRepository,
    ExecutionContextResolveOut,
)


class ExecutionContextCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "context_ref": "ctx_provider_analysis",
                "name": "Provider analysis context",
                "action_ref": "example-plugin.analytics.query",
                "credential_ref": "cred_example",
                "provider_context_json": {"account_ref": "acct_example"},
                "output_policy_json": {"mode": "file_if_large", "max_inline_bytes": 16000},
                "request_budget_json": {"max_parallel": 3},
                "task_key": "workflow-123",
            }
        },
    )

    project_id: int | None = None
    context_ref: str | None = None
    name: str
    description: str = ""
    plugin_slug: str | None = None
    provider_key: str | None = None
    action_ref: str | None = None
    credential_ref: str | None = None
    credential_locked: bool = False
    provider_context_json: dict[str, Any] | None = None
    provider_context_locked_fields_json: list[str] | None = None
    output_policy_json: dict[str, Any] | None = None
    request_budget_json: dict[str, Any] | None = None
    artifact_namespace: str | None = None
    metadata_json: dict[str, Any] | None = None
    task_key: str | None = None
    ticket_key: str | None = None
    run_plan_id: int | None = None
    run_id: int | None = None
    links_json: list[dict[str, Any]] | None = None
    created_by: str | None = None


class ExecutionContextGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "context_ref": "ctx_provider_analysis"}},
    )

    project_id: int | None = None
    context_ref: str


class ExecutionContextListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "task_key": "workflow-33"}},
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    provider_key: str | None = None
    action_ref: str | None = None
    status: str | None = "active"
    task_key: str | None = None
    ticket_key: str | None = None
    run_plan_id: int | None = None
    run_id: int | None = None
    limit: int | None = None
    after_id: int | None = None


class ExecutionContextDiscoverInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "task_key": "workflow-33"}},
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    provider_key: str | None = None
    action_ref: str | None = None
    status: str | None = "active"
    task_key: str | None = None
    ticket_key: str | None = None
    run_plan_id: int | None = None
    run_id: int | None = None
    limit: int | None = None
    after_id: int | None = None


class ExecutionContextResolveInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "context_ref": "ctx_provider_analysis",
                "action_ref": "example-plugin.analytics.query",
            }
        },
    )

    project_id: int | None = None
    context_ref: str
    action_ref: str | None = None


class ExecutionContextUpdateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "context_ref": "ctx_provider_analysis",
                "patch_json": {"output_policy_json": {"mode": "always_file"}},
            }
        },
    )

    project_id: int | None = None
    context_ref: str
    patch_json: dict[str, Any]


class ExecutionContextLinkInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "context_ref": "ctx_provider_analysis",
                "task_key": "workflow-123",
            }
        },
    )

    project_id: int | None = None
    context_ref: str
    link_type: str | None = None
    link_ref: str | None = None
    role: str = "default"
    task_key: str | None = None
    ticket_key: str | None = None
    run_plan_id: int | None = None
    run_id: int | None = None
    metadata_json: dict[str, Any] | None = None


class ExecutionContextUnlinkInput(ExecutionContextLinkInput):
    pass


class ExecutionContextArtifactListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "context_ref": "ctx_provider_analysis"}},
    )

    project_id: int | None = None
    context_ref: str
    action_ref: str | None = None
    limit: int | None = None
    after_id: int | None = None


class ExecutionContextArtifactReadInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "context_ref": "ctx_provider_analysis",
                "artifact_id": 1,
                "json_path": "$.data.items[0]",
            }
        },
    )

    project_id: int | None = None
    context_ref: str
    artifact_id: int
    json_path: str | None = None
    max_bytes: int = Field(default=16000, ge=1, le=200000)


async def execution_context_create(
    inp: ExecutionContextCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ExecutionContextOut]:
    project_id = _project_id(inp.project_id, ctx)
    links = list(inp.links_json or [])
    links.extend(_convenience_links(inp))
    env = ExecutionContextRepository(ctx.session).create(
        project_id=project_id,
        context_ref=inp.context_ref,
        name=inp.name,
        description=inp.description,
        plugin_slug=inp.plugin_slug,
        provider_key=inp.provider_key,
        action_ref=inp.action_ref,
        credential_ref=inp.credential_ref,
        credential_locked=inp.credential_locked,
        provider_context_json=inp.provider_context_json,
        provider_context_locked_fields_json=inp.provider_context_locked_fields_json,
        output_policy_json=inp.output_policy_json,
        request_budget_json=inp.request_budget_json,
        artifact_namespace=inp.artifact_namespace,
        metadata_json=inp.metadata_json,
        links_json=links,
        created_by=inp.created_by,
    )
    return WriteEnvelope[ExecutionContextOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def execution_context_get(
    inp: ExecutionContextGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ExecutionContextOut:
    return ExecutionContextRepository(ctx.session).get(
        project_id=_project_id(inp.project_id, ctx),
        context_ref=inp.context_ref,
    )


async def execution_context_list(
    inp: ExecutionContextListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[ExecutionContextOut]:
    return ExecutionContextRepository(ctx.session).list(
        project_id=_project_id(inp.project_id, ctx),
        plugin_slug=inp.plugin_slug,
        provider_key=inp.provider_key,
        action_ref=inp.action_ref,
        status=inp.status,
        task_key=inp.task_key,
        ticket_key=inp.ticket_key,
        run_plan_id=inp.run_plan_id,
        run_id=inp.run_id,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def execution_context_discover(
    inp: ExecutionContextDiscoverInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ExecutionContextDiscoveryOut:
    return ExecutionContextRepository(ctx.session).discover(
        project_id=_project_id(inp.project_id, ctx),
        plugin_slug=inp.plugin_slug,
        provider_key=inp.provider_key,
        action_ref=inp.action_ref,
        status=inp.status,
        task_key=inp.task_key,
        ticket_key=inp.ticket_key,
        run_plan_id=inp.run_plan_id,
        run_id=inp.run_id,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def execution_context_resolve(
    inp: ExecutionContextResolveInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ExecutionContextResolveOut:
    return ExecutionContextRepository(ctx.session).resolve(
        project_id=_project_id(inp.project_id, ctx),
        context_ref=inp.context_ref,
        action_ref=inp.action_ref,
    )


async def execution_context_update(
    inp: ExecutionContextUpdateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ExecutionContextOut]:
    env = ExecutionContextRepository(ctx.session).update(
        project_id=_project_id(inp.project_id, ctx),
        context_ref=inp.context_ref,
        patch_json=inp.patch_json,
    )
    return WriteEnvelope[ExecutionContextOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def execution_context_link(
    inp: ExecutionContextLinkInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ExecutionContextOut]:
    link_type, link_ref = _one_link(inp)
    env = ExecutionContextRepository(ctx.session).link(
        project_id=_project_id(inp.project_id, ctx),
        context_ref=inp.context_ref,
        link_type=link_type,
        link_ref=link_ref,
        role=inp.role,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[ExecutionContextOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def execution_context_unlink(
    inp: ExecutionContextUnlinkInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ExecutionContextOut]:
    link_type, link_ref = _one_link(inp)
    env = ExecutionContextRepository(ctx.session).unlink(
        project_id=_project_id(inp.project_id, ctx),
        context_ref=inp.context_ref,
        link_type=link_type,
        link_ref=link_ref,
        role=inp.role,
    )
    return WriteEnvelope[ExecutionContextOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def execution_context_artifact_list(
    inp: ExecutionContextArtifactListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[ExecutionContextArtifactOut]:
    return ExecutionContextRepository(ctx.session).list_artifacts(
        project_id=_project_id(inp.project_id, ctx),
        context_ref=inp.context_ref,
        action_ref=inp.action_ref,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def execution_context_artifact_read(
    inp: ExecutionContextArtifactReadInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> dict[str, Any]:
    item = ExecutionContextRepository(ctx.session).get_artifact(
        project_id=_project_id(inp.project_id, ctx),
        context_ref=inp.context_ref,
        artifact_id=inp.artifact_id,
    )
    artifact = item.artifact
    metadata_raw = artifact.get("metadata_json")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    provenance_raw = artifact.get("provenance_json")
    provenance: dict[str, Any] = provenance_raw if isinstance(provenance_raw, dict) else {}
    uri = artifact.get("uri")
    if (
        not metadata.get("file_backed_action_output")
        or item.action_call_id is None
        or provenance.get("action_call_id") != item.action_call_id
        or provenance.get("context_ref") != inp.context_ref
        or not isinstance(uri, str)
        or not uri.startswith("/generated-assets/")
    ):
        return {
            "context_ref": inp.context_ref,
            "artifact_id": inp.artifact_id,
            "artifact": artifact,
            "json_path": inp.json_path,
            "max_bytes": inp.max_bytes,
            "content_available": False,
            "read_instructions": (
                "This artifact pointer is registered but does not reference a "
                "StackOS file-backed action output."
            ),
        }
    settings = ctx.extras.get("settings")
    asset_root = Path(
        getattr(settings, "generated_assets_dir", Settings().generated_assets_dir)
    ).resolve()
    relative = uri.removeprefix("/generated-assets/")
    path = (asset_root / relative).resolve()
    try:
        path.relative_to(asset_root)
    except ValueError as exc:
        raise ValidationError("artifact path escaped generated assets") from exc
    metadata_path = metadata.get("absolute_path")
    if isinstance(metadata_path, str) and Path(metadata_path).resolve() != path:
        return {
            "context_ref": inp.context_ref,
            "artifact_id": inp.artifact_id,
            "artifact": artifact,
            "json_path": inp.json_path,
            "max_bytes": inp.max_bytes,
            "content_available": False,
            "error": "artifact metadata path does not match generated asset URI",
        }
    if not path.exists() or not path.is_file():
        return {
            "context_ref": inp.context_ref,
            "artifact_id": inp.artifact_id,
            "artifact": artifact,
            "json_path": inp.json_path,
            "max_bytes": inp.max_bytes,
            "content_available": False,
            "error": "artifact file is missing",
        }
    if str(artifact.get("mime_type") or "") != "application/json":
        raise ValidationError("file-backed action output artifacts must be application/json")
    raw = path.read_bytes()
    value: Any
    selected_json_path = inp.json_path or "$"
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("artifact file is not valid JSON") from exc
    value = select_json_path(value, selected_json_path)
    content = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    encoded = content.encode("utf-8")
    truncated = len(encoded) > inp.max_bytes
    if truncated:
        content = encoded[: inp.max_bytes].decode("utf-8", errors="ignore")
    return {
        "context_ref": inp.context_ref,
        "artifact_id": inp.artifact_id,
        "artifact": artifact,
        "json_path": selected_json_path,
        "max_bytes": inp.max_bytes,
        "content_available": True,
        "content_type": artifact.get("mime_type"),
        "bytes": len(raw),
        "sha256": metadata.get("sha256"),
        "content_truncated": truncated,
        "content": content,
    }


def _project_id(value: int | None, ctx: MCPContext) -> int:
    project_id = value if value is not None else ctx.project_id
    if project_id is None:
        raise ValidationError("project_id is required")
    return project_id


def _convenience_links(inp: Any) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    if inp.task_key:
        links.append({"link_type": "task", "link_ref": inp.task_key})
    if inp.ticket_key:
        links.append({"link_type": "ticket", "link_ref": inp.ticket_key})
    if inp.run_plan_id is not None:
        links.append({"link_type": "run_plan", "link_ref": str(inp.run_plan_id)})
    if inp.run_id is not None:
        links.append({"link_type": "run", "link_ref": str(inp.run_id)})
    return links


def _one_link(inp: ExecutionContextLinkInput) -> tuple[str, str]:
    explicit = (inp.link_type, inp.link_ref) if inp.link_type and inp.link_ref else None
    candidates = _convenience_links(inp)
    if explicit is not None:
        candidates.append({"link_type": explicit[0], "link_ref": explicit[1]})
    if len(candidates) != 1:
        raise ValidationError(
            "provide exactly one execution context link target",
            data={
                "accepted": [
                    "link_type+link_ref",
                    "task_key",
                    "ticket_key",
                    "run_plan_id",
                    "run_id",
                ]
            },
        )
    return str(candidates[0]["link_type"]), str(candidates[0]["link_ref"])


def _returns() -> tuple[str, ...]:
    return (
        "Safe context refs and redacted execution defaults; never provider secrets.",
        "Task/run links are organization and discovery metadata only, not grant state.",
    )


def operation_specs():
    return [
        operation_spec(
            name="executionContext.create",
            summary="Create one reusable provider action execution context.",
            input_model=ExecutionContextCreateInput,
            output_model=WriteEnvelope[ExecutionContextOut],
            handler=execution_context_create,
            purpose=(
                "Use this when an agent needs to bind provider/action defaults such as "
                "credential_ref, typed provider context, output policy, budget, and "
                "artifact namespace once instead of repeating them in every action call."
            ),
            returns=_returns(),
            examples=(
                OperationExample(
                    title="Create reporting context",
                    arguments={
                        "project_id": 1,
                        "context_ref": "ctx_provider_analysis",
                        "name": "Provider analysis context",
                        "action_ref": "example-plugin.analytics.query",
                        "credential_ref": "cred_example",
                        "provider_context_json": {"account_ref": "acct_example"},
                        "task_key": "workflow-123",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-tracker-write",
            category="actions",
        ),
        operation_spec(
            name="executionContext.get",
            summary="Fetch one execution context by ref.",
            input_model=ExecutionContextGetInput,
            output_model=ExecutionContextOut,
            handler=execution_context_get,
            purpose="Use this to inspect one safe execution context before action planning.",
            returns=_returns(),
            mutating=False,
            grant_policy="direct-read",
            category="actions",
        ),
        operation_spec(
            name="executionContext.list",
            summary="List execution contexts by project, task, provider, action, or run.",
            input_model=ExecutionContextListInput,
            output_model=Page[ExecutionContextOut],
            handler=execution_context_list,
            purpose=(
                "Use this at the start of agent work to discover available context_ref "
                "values for the project/task instead of scanning credentials."
            ),
            returns=_returns(),
            mutating=False,
            grant_policy="direct-read",
            category="actions",
        ),
        operation_spec(
            name="executionContext.discover",
            summary="Discover context refs and safe next calls for a project, task, or run.",
            input_model=ExecutionContextDiscoverInput,
            output_model=ExecutionContextDiscoveryOut,
            handler=execution_context_discover,
            purpose=(
                "Use this at the start of agent work to find context_ref values linked "
                "to a task, ticket, run plan, run, provider, or action. It returns "
                "safe next-call shapes for link and resolve without granting execution."
            ),
            returns=_returns(),
            mutating=False,
            grant_policy="direct-read",
            category="actions",
        ),
        operation_spec(
            name="executionContext.resolve",
            summary="Resolve and compatibility-check one execution context for an action.",
            input_model=ExecutionContextResolveInput,
            output_model=ExecutionContextResolveOut,
            handler=execution_context_resolve,
            purpose=(
                "Use this before action.validate/run/execute when the agent needs the "
                "resolved credential/provider scope, output policy, and next-call hints."
            ),
            returns=_returns(),
            mutating=False,
            grant_policy="direct-read",
            category="actions",
        ),
        operation_spec(
            name="executionContext.update",
            summary="Patch safe fields on one execution context.",
            input_model=ExecutionContextUpdateInput,
            output_model=WriteEnvelope[ExecutionContextOut],
            handler=execution_context_update,
            purpose="Use this to adjust context output policy, status, metadata, or safe defaults.",
            returns=_returns(),
            mutating=True,
            grant_policy="direct-tracker-write",
            category="actions",
        ),
        operation_spec(
            name="executionContext.link",
            summary="Link an execution context to a task, ticket, run plan, or run.",
            input_model=ExecutionContextLinkInput,
            output_model=WriteEnvelope[ExecutionContextOut],
            handler=execution_context_link,
            purpose=(
                "Use this to organize context refs under workflow/task scope so fresh "
                "agents can discover them later."
            ),
            returns=_returns(),
            mutating=True,
            grant_policy="direct-tracker-write",
            category="actions",
        ),
        operation_spec(
            name="executionContext.unlink",
            summary="Remove one execution context relationship link.",
            input_model=ExecutionContextUnlinkInput,
            output_model=WriteEnvelope[ExecutionContextOut],
            handler=execution_context_unlink,
            purpose="Use this to detach a context from task/run organization without deleting it.",
            returns=_returns(),
            mutating=True,
            grant_policy="direct-tracker-write",
            category="actions",
        ),
        operation_spec(
            name="executionContext.artifact.list",
            summary="List artifacts registered under one execution context.",
            input_model=ExecutionContextArtifactListInput,
            output_model=Page[ExecutionContextArtifactOut],
            handler=execution_context_artifact_list,
            purpose="Use this to discover prior provider action outputs for a context.",
            returns=_returns(),
            mutating=False,
            grant_policy="direct-read",
            category="actions",
        ),
        operation_spec(
            name="executionContext.artifact.read",
            summary="Read a bounded artifact pointer registered under one execution context.",
            input_model=ExecutionContextArtifactReadInput,
            output_model=dict[str, Any],
            handler=execution_context_artifact_read,
            purpose=(
                "Use this to inspect previous file-backed action outputs without rerunning actions."
            ),
            returns=_returns(),
            mutating=False,
            grant_policy="direct-read",
            category="actions",
        ),
    ]


__all__ = ["operation_specs"]
