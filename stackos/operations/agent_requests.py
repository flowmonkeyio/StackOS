"""StackOS agent request operation registrations."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.db.models import AgentRequestAttentionStatus, AgentRequestStatus
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.agent_requests import (
    AgentRequestClaimOut,
    AgentRequestOut,
    AgentRequestPrepareRunPlanOut,
    AgentRequestRepository,
)
from stackos.repositories.base import Page, ValidationError


class AgentRequestListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "claimable": True, "limit": 10}},
    )

    project_id: int
    statuses: list[AgentRequestStatus] | None = None
    attention_status: AgentRequestAttentionStatus | None = None
    claimed_by: str | None = None
    claimable: bool = False
    limit: int | None = None
    after_id: int | None = None


class AgentRequestGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "request_id": 42}},
    )

    project_id: int
    request_id: int


class AgentRequestCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_key": "telegram:update:123",
                "title": "Inbound Telegram message",
                "body_preview": "Please run the launch check",
                "source_provider": "telegram-bot",
                "source_kind": "telegram-message",
            }
        },
    )

    project_id: int
    request_key: str
    title: str
    body_preview: str = ""
    source_provider: str | None = None
    source_kind: str | None = None
    source_resource_key: str | None = None
    source_resource_record_id: int | None = None
    source_message_ref: str | None = None
    priority: int = 0
    metadata_json: dict[str, Any] | None = None


class AgentRequestClaimInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_id": 42,
                "claimed_by": "codex",
                "idempotency_key": "claim-agent-request-42-codex",
            }
        },
    )

    project_id: int
    request_id: int
    claimed_by: str
    lease_seconds: int = 600


class AgentRequestClaimTokenInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_id": 42,
                "claim_token": "claim-token-from-agentRequest.claim",
            }
        },
    )

    project_id: int
    request_id: int
    claim_token: str


class AgentRequestLinkRunPlanInput(AgentRequestClaimTokenInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_id": 42,
                "run_plan_id": 9,
                "claim_token": "claim-token-from-agentRequest.claim",
            }
        },
    )

    run_plan_id: int


class AgentRequestPrepareRunPlanInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_id": 42,
                "claimed_by": "codex",
                "idempotency_key": "prepare-request-42",
                "run_plan_json": {
                    "schema_version": "stackos.run-plan.v1",
                    "key": "handle.telegram.request.run",
                    "title": "Handle Telegram Request",
                    "steps": [{"id": "handle", "title": "Handle request"}],
                },
            }
        },
    )

    project_id: int
    request_id: int
    claimed_by: str
    lease_seconds: int = 86_400
    run_plan_json: dict[str, Any] | None = None
    template_key: str | None = None
    repo_root: str | None = None
    plugin_slug: str | None = None
    source: str | None = None
    key: str | None = None
    title: str | None = None
    inputs_json: dict[str, Any] | None = None
    context_snapshot_id: int | None = None
    selected_context_json: dict[str, Any] | None = None
    created_by: str | None = None
    metadata_json: dict[str, Any] | None = None


class AgentRequestCompleteInput(AgentRequestClaimTokenInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_id": 42,
                "claim_token": "claim-token-from-agentRequest.claim",
                "status": "resolved",
                "metadata_json": {"summary": "Handled by run plan 9"},
            }
        },
    )

    status: AgentRequestStatus = AgentRequestStatus.RESOLVED
    metadata_json: dict[str, Any] | None = None


class AgentRequestIgnoreInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_id": 42,
                "ignored_by": "codex",
                "metadata_json": {"reason": "duplicate"},
            }
        },
    )

    project_id: int
    request_id: int
    ignored_by: str
    claim_token: str | None = None
    metadata_json: dict[str, Any] | None = None


async def agent_request_list(
    inp: AgentRequestListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[AgentRequestOut]:
    repo = AgentRequestRepository(ctx.session)
    if inp.claimable:
        if inp.statuses:
            raise ValidationError("statuses cannot be combined with claimable=true")
        if inp.claimed_by is not None:
            raise ValidationError("claimed_by cannot be combined with claimable=true")
        return repo.list_claimable(
            project_id=inp.project_id,
            attention_status=inp.attention_status,
            limit=inp.limit,
            after_id=inp.after_id,
        )
    return repo.list(
        project_id=inp.project_id,
        statuses=inp.statuses,
        attention_status=inp.attention_status,
        claimed_by=inp.claimed_by,
        limit=inp.limit,
        after_id=inp.after_id,
    )


async def agent_request_get(
    inp: AgentRequestGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> AgentRequestOut:
    return AgentRequestRepository(ctx.session).get(
        project_id=inp.project_id,
        request_id=inp.request_id,
    )


async def agent_request_create(
    inp: AgentRequestCreateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AgentRequestOut]:
    env = AgentRequestRepository(ctx.session).create(
        project_id=inp.project_id,
        request_key=inp.request_key,
        title=inp.title,
        body_preview=inp.body_preview,
        source_provider=inp.source_provider,
        source_kind=inp.source_kind,
        source_resource_key=inp.source_resource_key,
        source_resource_record_id=inp.source_resource_record_id,
        source_message_ref=inp.source_message_ref,
        priority=inp.priority,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[AgentRequestOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def agent_request_claim(
    inp: AgentRequestClaimInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AgentRequestClaimOut]:
    if not inp.idempotency_key:
        raise ValidationError("idempotency_key is required for agentRequest.claim")
    env = AgentRequestRepository(ctx.session).claim(
        project_id=inp.project_id,
        request_id=inp.request_id,
        claimed_by=inp.claimed_by,
        lease_seconds=inp.lease_seconds,
    )
    return WriteEnvelope[AgentRequestClaimOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def agent_request_release(
    inp: AgentRequestClaimTokenInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AgentRequestOut]:
    env = AgentRequestRepository(ctx.session).release(
        project_id=inp.project_id,
        request_id=inp.request_id,
        claim_token=inp.claim_token,
    )
    return WriteEnvelope[AgentRequestOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def agent_request_link_run_plan(
    inp: AgentRequestLinkRunPlanInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AgentRequestOut]:
    env = AgentRequestRepository(ctx.session).link_run_plan(
        project_id=inp.project_id,
        request_id=inp.request_id,
        run_plan_id=inp.run_plan_id,
        claim_token=inp.claim_token,
    )
    return WriteEnvelope[AgentRequestOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def agent_request_prepare_run_plan(
    inp: AgentRequestPrepareRunPlanInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AgentRequestPrepareRunPlanOut]:
    if not inp.idempotency_key:
        raise ValidationError("idempotency_key is required for agentRequest.prepareRunPlan")
    env = AgentRequestRepository(ctx.session).prepare_run_plan(
        project_id=inp.project_id,
        request_id=inp.request_id,
        claimed_by=inp.claimed_by,
        lease_seconds=inp.lease_seconds,
        run_plan_json=inp.run_plan_json,
        template_key=inp.template_key,
        repo_root=inp.repo_root,
        plugin_slug=inp.plugin_slug,
        source=inp.source,
        key=inp.key,
        title=inp.title,
        inputs_json=inp.inputs_json,
        context_snapshot_id=inp.context_snapshot_id,
        selected_context_json=inp.selected_context_json,
        created_by=inp.created_by,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[AgentRequestPrepareRunPlanOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def agent_request_complete(
    inp: AgentRequestCompleteInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AgentRequestOut]:
    env = AgentRequestRepository(ctx.session).complete(
        project_id=inp.project_id,
        request_id=inp.request_id,
        claim_token=inp.claim_token,
        status=inp.status,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[AgentRequestOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


async def agent_request_ignore(
    inp: AgentRequestIgnoreInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[AgentRequestOut]:
    env = AgentRequestRepository(ctx.session).ignore(
        project_id=inp.project_id,
        request_id=inp.request_id,
        ignored_by=inp.ignored_by,
        claim_token=inp.claim_token,
        metadata_json=inp.metadata_json,
    )
    return WriteEnvelope[AgentRequestOut](
        data=env.data, run_id=ctx.run_id, project_id=env.project_id
    )


def _surfaces(name: str, command: str) -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=f"/api/v1/operations/{name}/call"),
        cli=OperationSurface(enabled=True, command=command),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="agentRequest.list",
            summary="List project agent requests, optionally only currently claimable items.",
            input_model=AgentRequestListInput,
            output_model=Page[AgentRequestOut],
            handler=agent_request_list,
            surfaces=_surfaces("agentRequest.list", "agent-requests list"),
            purpose=(
                "Use this as the generic inbox polling operation for Telegram, SMTP/IMAP, "
                "webhooks, schedules, or future trigger sources. It reads StackOS queue "
                "state only and never calls providers."
            ),
            when_to_use=(
                "An agent wants to find inbound work needing attention.",
                "A script wants claimable queue items without provider-specific polling logic.",
            ),
            prerequisites=(
                "Pass project_id.",
                "Use claimable=true to include new requests and expired leases only.",
                "Use limit and after_id to keep the response bounded.",
            ),
            returns=("A cursor-paginated Page of sanitized AgentRequestOut records.",),
            examples=(
                OperationExample(
                    title="List claimable requests",
                    arguments={"project_id": 1, "claimable": True, "limit": 10},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="agentRequest.get",
            summary="Fetch one sanitized agent request by id.",
            input_model=AgentRequestGetInput,
            output_model=AgentRequestOut,
            handler=agent_request_get,
            surfaces=_surfaces("agentRequest.get", "agent-requests get"),
            purpose="Use this to inspect one queue item before claiming or linking it.",
            when_to_use=(
                "An agent has a request_id from list/search/UI and needs the sanitized "
                "payload before deciding whether to claim it.",
                "A script needs to inspect queue state without changing claim ownership.",
            ),
            prerequisites=("Pass project_id and request_id.",),
            returns=("One sanitized AgentRequestOut record.",),
            examples=(
                OperationExample(
                    title="Get one request", arguments={"project_id": 1, "request_id": 42}
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="agentRequest.create",
            summary="Create a generic agent request from an already-ingested trigger source.",
            input_model=AgentRequestCreateInput,
            output_model=WriteEnvelope[AgentRequestOut],
            handler=agent_request_create,
            surfaces=_surfaces("agentRequest.create", "agent-requests create"),
            purpose=(
                "Use this only from trusted ingestion or a granted run-plan step after a "
                "provider event has already been stored or allowlisted. The operation stores "
                "queue state; it does not poll Telegram, read email, or call webhooks."
            ),
            when_to_use=(
                "A communications/scheduler/webhook ingestion step has a concrete event "
                "that should become agent work.",
                "A granted workflow step transforms a stored resource into a follow-up request.",
            ),
            prerequisites=(
                "Normal callers must pass a run_token whose active step grants "
                "agentRequest.create.",
                "Direct bootstrap/system calls are intentionally denied.",
                "Pass a stable request_key so creation is idempotent per project.",
                "Do not pass secrets; title, preview, source ref, and metadata are "
                "redacted server-side.",
            ),
            returns=("A WriteEnvelope containing the created or existing AgentRequestOut.",),
            examples=(
                OperationExample(
                    title="Create from a stored Telegram event",
                    arguments={
                        "project_id": 1,
                        "request_key": "telegram:update:123",
                        "title": "Telegram message needs agent action",
                        "source_provider": "telegram-bot",
                        "source_kind": "telegram-message",
                        "source_resource_key": "communication-message",
                        "source_resource_record_id": 77,
                        "run_token": "run-plan-token",
                    },
                ),
            ),
            mutating=True,
            grant_policy="run-plan-step-grant",
        ),
        OperationSpec(
            name="agentRequest.claim",
            summary="Claim one agent request with a leased one-time claim token.",
            input_model=AgentRequestClaimInput,
            output_model=WriteEnvelope[AgentRequestClaimOut],
            handler=agent_request_claim,
            surfaces=_surfaces("agentRequest.claim", "agent-requests claim"),
            purpose=(
                "Use this before creating or linking a run plan for an inbound request. "
                "The returned claim_token is the only raw claim token StackOS returns."
            ),
            when_to_use=(
                "An agent is ready to take ownership of a request and needs a leased claim token.",
                "A retry-safe queue consumer needs to prevent multiple agents handling "
                "the same request.",
            ),
            prerequisites=(
                "Pass project_id, request_id, claimed_by, and idempotency_key.",
                "Use the same idempotency_key when retrying a network-lost claim call.",
                "Store the returned claim_token only in the agent's active execution context.",
                "Expired claims are reclaimable.",
            ),
            returns=("A WriteEnvelope containing AgentRequestClaimOut with claim_token.",),
            examples=(
                OperationExample(
                    title="Claim one request",
                    arguments={
                        "project_id": 1,
                        "request_id": 42,
                        "claimed_by": "codex",
                        "idempotency_key": "claim-agent-request-42-codex",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-work-queue-write",
        ),
        OperationSpec(
            name="agentRequest.release",
            summary="Release an active claim back to new queue state.",
            input_model=AgentRequestClaimTokenInput,
            output_model=WriteEnvelope[AgentRequestOut],
            handler=agent_request_release,
            surfaces=_surfaces("agentRequest.release", "agent-requests release"),
            purpose="Use this when an agent decides not to handle a claimed request yet.",
            when_to_use=(
                "The claiming agent cannot continue and wants another agent to see the "
                "request as claimable.",
                "A request was claimed during planning but no run plan should be linked yet.",
            ),
            prerequisites=("Pass project_id, request_id, and the active claim_token.",),
            returns=("A WriteEnvelope containing the released AgentRequestOut.",),
            examples=(
                OperationExample(
                    title="Release a claim",
                    arguments={"project_id": 1, "request_id": 42, "claim_token": "claim-token"},
                ),
            ),
            mutating=True,
            grant_policy="direct-claim-token-write",
        ),
        OperationSpec(
            name="agentRequest.linkRunPlan",
            summary="Attach a claimed request to the run plan created to handle it.",
            input_model=AgentRequestLinkRunPlanInput,
            output_model=WriteEnvelope[AgentRequestOut],
            handler=agent_request_link_run_plan,
            surfaces=_surfaces("agentRequest.linkRunPlan", "agent-requests link-run-plan"),
            purpose=(
                "Use this after agentRequest.claim and runPlan.create so the trigger, plan, "
                "and later audit trail stay connected."
            ),
            when_to_use=(
                "The agent created a run plan in separate calls and must attach it to "
                "the claimed request.",
                "The request should remain traceable from trigger to run-plan execution.",
            ),
            prerequisites=(
                "Pass project_id, request_id, run_plan_id, and the active claim_token.",
                "The run plan must belong to the same project.",
            ),
            returns=("A WriteEnvelope containing the linked AgentRequestOut.",),
            examples=(
                OperationExample(
                    title="Link request to a run plan",
                    arguments={
                        "project_id": 1,
                        "request_id": 42,
                        "run_plan_id": 9,
                        "claim_token": "claim-token",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-claim-token-write",
        ),
        OperationSpec(
            name="agentRequest.prepareRunPlan",
            summary="Claim a request, create a caller-supplied run plan, and link both.",
            input_model=AgentRequestPrepareRunPlanInput,
            output_model=WriteEnvelope[AgentRequestPrepareRunPlanOut],
            handler=agent_request_prepare_run_plan,
            surfaces=_surfaces("agentRequest.prepareRunPlan", "agent-requests prepare-run-plan"),
            purpose=(
                "Use this as the deterministic queue-to-plan handoff for trigger-driven "
                "work. The caller supplies run_plan_json or template_key; StackOS only "
                "claims the request, stores the plan, links the audit trail, and returns "
                "the claim token."
            ),
            when_to_use=(
                "An agent or script selected a workflow template or explicit plan for an "
                "inbound request.",
                "The caller wants one retry-safe operation instead of separate claim, "
                "runPlan.create, and linkRunPlan calls.",
            ),
            prerequisites=(
                "Pass project_id, request_id, claimed_by, and idempotency_key.",
                "Pass either run_plan_json or template_key.",
                "The operation does not infer intent, start a model, execute actions, "
                "or send replies.",
                "Use runPlan.start and runPlan.claimStep after this operation when the "
                "agent is ready to execute the created plan.",
            ),
            returns=(
                "A WriteEnvelope containing the linked AgentRequestOut, RunPlanOut, and "
                "the one-time claim_token.",
            ),
            examples=(
                OperationExample(
                    title="Prepare a request run plan",
                    arguments={
                        "project_id": 1,
                        "request_id": 42,
                        "claimed_by": "codex",
                        "idempotency_key": "prepare-agent-request-42-codex",
                        "run_plan_json": {
                            "schema_version": "stackos.run-plan.v1",
                            "key": "handle.request.run",
                            "title": "Handle request",
                            "steps": [{"id": "handle", "title": "Handle request"}],
                        },
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-work-queue-write",
        ),
        OperationSpec(
            name="agentRequest.complete",
            summary="Resolve or fail a claimed agent request.",
            input_model=AgentRequestCompleteInput,
            output_model=WriteEnvelope[AgentRequestOut],
            handler=agent_request_complete,
            surfaces=_surfaces("agentRequest.complete", "agent-requests complete"),
            purpose="Use this after the linked work has reached a terminal outcome.",
            when_to_use=(
                "The agent finished or failed the requested work and needs to close the "
                "queue item.",
                "The linked run plan has a terminal outcome and the request status should "
                "match that result.",
            ),
            prerequisites=(
                "Pass project_id, request_id, and the active claim_token.",
                "status must be resolved or failed.",
                "Keep metadata_json concise and secret-free; StackOS redacts defensively.",
            ),
            returns=("A WriteEnvelope containing the terminal AgentRequestOut.",),
            examples=(
                OperationExample(
                    title="Resolve a request",
                    arguments={
                        "project_id": 1,
                        "request_id": 42,
                        "claim_token": "claim-token",
                        "status": "resolved",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-claim-token-write",
        ),
        OperationSpec(
            name="agentRequest.ignore",
            summary="Archive a new or actively claimed agent request without running it.",
            input_model=AgentRequestIgnoreInput,
            output_model=WriteEnvelope[AgentRequestOut],
            handler=agent_request_ignore,
            surfaces=_surfaces("agentRequest.ignore", "agent-requests ignore"),
            purpose="Use this for duplicates, noise, or explicit operator decisions to skip work.",
            when_to_use=(
                "A request is duplicate/noise and should not create or continue a run plan.",
                "An operator or policy explicitly decided the request should be archived.",
            ),
            prerequisites=(
                "Pass project_id, request_id, and ignored_by.",
                "Pass claim_token when the request is already claimed.",
                "Keep metadata_json concise and secret-free; StackOS redacts defensively.",
            ),
            returns=("A WriteEnvelope containing the ignored AgentRequestOut.",),
            examples=(
                OperationExample(
                    title="Ignore a duplicate request",
                    arguments={
                        "project_id": 1,
                        "request_id": 42,
                        "ignored_by": "codex",
                        "metadata_json": {"reason": "duplicate"},
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-claim-token-write",
        ),
    ]


__all__ = [
    "AgentRequestClaimInput",
    "AgentRequestClaimTokenInput",
    "AgentRequestCompleteInput",
    "AgentRequestCreateInput",
    "AgentRequestGetInput",
    "AgentRequestIgnoreInput",
    "AgentRequestLinkRunPlanInput",
    "AgentRequestListInput",
    "AgentRequestPrepareRunPlanInput",
    "agent_request_claim",
    "agent_request_complete",
    "agent_request_create",
    "agent_request_get",
    "agent_request_ignore",
    "agent_request_link_run_plan",
    "agent_request_list",
    "agent_request_prepare_run_plan",
    "agent_request_release",
    "operation_specs",
]
