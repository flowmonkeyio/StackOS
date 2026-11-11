"""Local-admin REST adapter for actual project-native Codex files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import ConfigDict, Field
from sqlmodel import Session, select

from stackos.db.models import ScheduledJob, TaskTracker
from stackos.host_mcp.adapters.codex_definition import AgentFields, AgentPatch
from stackos.host_mcp.project_agents import (
    AgentDefinition,
    AgentList,
    AgentSourceRef,
    ProjectAgentError,
    ProjectAgentService,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.workspaces import WorkspaceRepository


class ProjectAgentScope(MCPInput):
    model_config = ConfigDict(extra="forbid")
    project_id: int = Field(ge=1)
    project_dir: str = Field(min_length=1, max_length=1000)
    host_key: Literal["codex"] = "codex"
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"


class ProjectAgentGetInput(ProjectAgentScope):
    source_ref: AgentSourceRef


class ProjectAgentCreateInput(ProjectAgentScope):
    fields: AgentFields
    response_mode: Literal["raw", "standard", "verbose"] = "raw"


class ProjectAgentUpdateInput(ProjectAgentGetInput):
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_registration_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    patch: AgentPatch
    response_mode: Literal["raw", "standard", "verbose"] = "raw"


def saved_agent_references(
    session: Session,
    project_id: int,
    project_dir: str,
    source_ref: AgentSourceRef,
) -> list[dict[str, Any]]:
    """Inspect editable assignments only; immutable task/run snapshots are history."""

    def matches(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if value.get("source_ref") == source_ref.model_dump():
            return True
        # Existing saved two-role defaults also reference native names during adoption.
        for key in ("delivery_agent", "review_agent"):
            agent = value.get(key)
            if (
                isinstance(agent, dict)
                and agent.get("name") == source_ref.native_name
                and (agent.get("host_key") or value.get("host_key") or "codex") == "codex"
            ):
                return True
        return any(matches(child) for child in value.values())

    references = []
    for tracker in session.exec(
        select(TaskTracker)
        .where(TaskTracker.project_id == project_id)
        .execution_options(populate_existing=True)
    ).all():
        if matches((tracker.config_json or {}).get("delivery")):
            references.append({"kind": "project_assignment", "tracker_id": tracker.id})
    for job in session.exec(
        select(ScheduledJob)
        .where(ScheduledJob.project_id == project_id)
        .execution_options(populate_existing=True)
    ).all():
        execution = job.execution_json or {}
        saved_dir = execution.get("project_dir")
        if saved_dir is not None:
            try:
                if str(Path(saved_dir).resolve()) != project_dir:
                    continue
            except (OSError, RuntimeError, TypeError, ValueError):
                # Unknown scope cannot establish that a name reference is unrelated.
                pass
        if matches(execution):
            references.append({"kind": "schedule", "id": job.id})
    return references


def _service(inp: ProjectAgentScope, ctx: MCPContext) -> ProjectAgentService:
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("trusted_local_admin") is not True
        or ctx.extras.get("client_surface") not in {None, "rest"}
        or inp.run_token is not None
    ):
        raise ToolNotGrantedError(
            "Project agent files require an explicit local-admin REST session."
        )
    service = ProjectAgentService(inp.project_dir)
    # Exact known workspace only; binding is association, local-admin is file authority.
    bindings = WorkspaceRepository(ctx.session).list_bindings()
    matching = []
    for binding in bindings:
        try:
            if binding.last_known_root and Path(binding.last_known_root).resolve() == service.root:
                matching.append(binding.project_id)
        except (OSError, RuntimeError, ValueError):
            continue
    if set(matching) != {inp.project_id}:
        raise ProjectAgentError("missing_scope")
    service.references = lambda ref: saved_agent_references(
        ctx.session, inp.project_id, str(service.root), ref
    )
    return service


def _error(exc: ProjectAgentError) -> NoReturn:
    error = (
        ConflictError
        if exc.data["code"] in {"stale_source", "ambiguous_identity", "referenced_identity"}
        else ValidationError
    )
    raise error("Project agent source requires repair.", data=exc.data) from exc


async def list_agents(inp: ProjectAgentScope, ctx: MCPContext, _p: ProgressEmitter) -> AgentList:
    try:
        result = _service(inp, ctx).list()
        result.project_id = inp.project_id
        for row in result.items:
            row.project_id = inp.project_id
        return result
    except ProjectAgentError as exc:
        _error(exc)


async def get_agent(
    inp: ProjectAgentGetInput, ctx: MCPContext, _p: ProgressEmitter
) -> AgentDefinition:
    try:
        result = _service(inp, ctx).get(inp.source_ref)
        result.project_id = inp.project_id
        return result
    except ProjectAgentError as exc:
        _error(exc)


async def create_agent(
    inp: ProjectAgentCreateInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[AgentDefinition]:
    try:
        result = _service(inp, ctx).create(inp.fields.model_dump(exclude_none=True))
        result.project_id = inp.project_id
        return WriteEnvelope(data=result, project_id=inp.project_id)
    except ProjectAgentError as exc:
        _error(exc)


async def update_agent(
    inp: ProjectAgentUpdateInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[AgentDefinition]:
    try:
        result = _service(inp, ctx).update(
            inp.source_ref,
            inp.expected_digest,
            inp.patch.model_dump(exclude_unset=True),
            expected_registration_digest=inp.expected_registration_digest,
        )
        result.project_id = inp.project_id
        return WriteEnvelope(data=result, project_id=inp.project_id)
    except ProjectAgentError as exc:
        _error(exc)


def operation_specs() -> list[OperationSpec]:
    specs = []
    for verb, model, output, handler, write in (
        ("list", ProjectAgentScope, AgentList, list_agents, False),
        ("get", ProjectAgentGetInput, AgentDefinition, get_agent, False),
        ("create", ProjectAgentCreateInput, WriteEnvelope[AgentDefinition], create_agent, True),
        ("update", ProjectAgentUpdateInput, WriteEnvelope[AgentDefinition], update_agent, True),
    ):
        name = f"projectAgent.{verb}"
        specs.append(
            OperationSpec(
                name=name,
                summary=f"{verb.capitalize()} actual Codex definitions in the selected tree.",
                input_model=model,
                output_model=output,
                handler=handler,
                mutating=write,
                # Every retry rechecks console authority, root, source and digest.
                # Exclusive create/stale update reject safely; never replay prompt bytes
                # from generic DB idempotency storage before those checks.
                idempotency_policy="handler",
                category="agents",
                grant_policy="local-admin-console-write" if write else "local-admin-console-read",
                purpose=(
                    "Manage native source files without an editable DB prompt copy "
                    "or execution on save."
                ),
                prerequisites=(
                    "Local-admin REST authority and an exact existing workspace association.",
                ),
                returns=(
                    "Source identity/digest, native settings/inheritance, unsupported fields "
                    "and structured repair context.",
                ),
                examples=(
                    OperationExample(
                        title="Explicit workspace scope",
                        arguments={
                            "project_id": 1,
                            "project_dir": "/chosen/project",
                            "host_key": "codex",
                            **(
                                {
                                    "source_ref": {
                                        "host_key": "codex",
                                        "native_name": "research",
                                        "source_path": ".codex/agents/research.toml",
                                    }
                                }
                                if verb in {"get", "update"}
                                else {}
                            ),
                            **(
                                {
                                    "fields": {
                                        "name": "research",
                                        "description": "Research",
                                        "developer_instructions": "Project-adapted instructions.",
                                    }
                                }
                                if verb == "create"
                                else {}
                            ),
                            **(
                                {
                                    "expected_digest": "0" * 64,
                                    "patch": {"description": "Updated description"},
                                }
                                if verb == "update"
                                else {}
                            ),
                        },
                    ),
                ),
                surfaces=OperationSurfaces(
                    rest=OperationSurface(
                        enabled=True, browser_safe=True, path=f"/api/v1/operations/{name}/call"
                    )
                ),
                response_policy=OperationResponsePolicy(
                    default_mode="raw" if write else "compact",
                    allowed_modes=("raw",) if write else ("compact", "raw"),
                    ack_safe=False,
                    raw_only_reason="Native writes retain exact readback and repair context."
                    if write
                    else None,
                    compact_notes=(
                        "Keep all refs, digests, scope, inheritance, capabilities and diagnostics; "
                        "request raw for full instructions.",
                    ),
                ),
            )
        )
    return specs
