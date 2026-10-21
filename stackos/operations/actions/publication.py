"""Authenticated publication issuance and immutable original-intent readback."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import ValidationError as ModelValidationError
from sqlmodel import Session, col, select

from stackos.actions.controlled_command import _canonical_digest, _controlled_host_settings
from stackos.actions.controlled_publication import (
    PUBLICATION_REF,
    _check_manifest,
    _intent_key,
    _LocalGit,
    _PublicationInput,
    _verify_terminal,
)
from stackos.db.models import (
    ActionCall,
    ApprovalRequest,
    ApprovalRequestStatus,
    Plugin,
    Resource,
    ResourceRecord,
)
from stackos.mcp.context import MCPContext, current_context
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.permissions import RUN_PLAN_CONTROLLER_SKILL, check_call_grant, resolve_run_token
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.runs import RunRepository

from .controlled import _action_ref, _ControlledFence, _current_owner_authority, _native_storage
from .schemas import ActionExecuteInput


def _require_publication_session(session: Session) -> None:
    """Admit logical autobegin, never consume pending work or a real caller transaction."""
    RunRepository(session)._require_native_clean_session()
    if session.connection().dialect.name != "sqlite":
        raise ConflictError("publication requires an unowned SQLite transaction")


def _request(inp: ActionExecuteInput) -> _PublicationInput:
    if _action_ref(inp) != PUBLICATION_REF or inp.dry_run:
        raise ValidationError("publication requires explicit workflow execution")
    for name in (
        "context_ref",
        "credential_ref",
        "provider_context_json",
        "output_policy_json",
        "metadata_json",
        "due_at",
        "expires_at",
        "account_interval_seconds",
        "destination_interval_seconds",
    ):
        if getattr(inp, name) is not None:
            raise ValidationError("publication option forbidden", data={"field": name})
    try:
        return _PublicationInput.model_validate(inp.input_json)
    except ModelValidationError as exc:
        raise ValidationError("invalid publication binding") from exc


def _storage(ctx: MCPContext) -> tuple[Any, bytes, str]:
    settings = _controlled_host_settings(ctx.session)
    if settings is None:
        raise ConflictError("publication storage unavailable")
    canonical_settings = settings
    if settings is not ctx.extras.get("settings"):
        raise ConflictError("publication storage unavailable")
    _, seed, digest = _native_storage(canonical_settings)
    return canonical_settings, seed, digest


@dataclass(frozen=True)
class _PublicationAuthority:
    ctx: MCPContext = field(repr=False)
    inp: ActionExecuteInput = field(repr=False)
    request: _PublicationInput
    fence: _ControlledFence
    review_manifest_sha256: str
    storage_digest: str
    git_digest: str
    seed: bytes = field(repr=False)

    def revalidate(self, ctx: MCPContext) -> _PublicationAuthority:
        # Only the repository's private fresh writer reaches this revalidation.
        # External issuance/readback must first admit the caller Session.
        with ctx.session.no_autoflush:
            current = _current_publication_authority(self.inp, ctx)
        if (
            current.fence,
            current.request,
            current.review_manifest_sha256,
            current.storage_digest,
            current.git_digest,
        ) != (
            self.fence,
            self.request,
            self.review_manifest_sha256,
            self.storage_digest,
            self.git_digest,
        ):
            raise ConflictError("publication authority changed")
        return current

    def admission(self) -> dict[str, Any]:
        return {
            **asdict(self.fence),
            "approval_updated_at": self.fence.approval_updated_at.isoformat()
            if self.fence.approval_updated_at
            else None,
            "request_digest": _canonical_digest(self.request.model_dump()),
            "storage_digest": self.storage_digest,
            "git_digest": self.git_digest,
            "manifest_sha256": self.review_manifest_sha256,
        }


def _issue_publication_authority(inp: ActionExecuteInput, ctx: MCPContext) -> _PublicationAuthority:
    _require_publication_session(ctx.session)
    with ctx.session.no_autoflush:
        return _current_publication_authority(inp, ctx)


def _current_publication_authority(
    inp: ActionExecuteInput, ctx: MCPContext
) -> _PublicationAuthority:
    request = _request(inp)
    run, plan, step, owner, continuity = _current_owner_authority(inp, ctx, request)
    from stackos.actions import ActionRepository

    from .execution import _approval_ref_for_action

    described = ActionRepository(ctx.session).describe(
        project_id=inp.project_id, action_ref=PUBLICATION_REF
    )
    _check_manifest(described.manifest)
    if not described.availability.executable:
        raise ConflictError("publication manifest unavailable")
    review = ctx.session.exec(
        select(ResourceRecord)
        .join(Resource, col(Resource.id) == col(ResourceRecord.resource_id))
        .join(Plugin, col(Plugin.id) == col(Resource.plugin_id))
        .where(
            ResourceRecord.id == request.review_record_id,
            ResourceRecord.project_id == inp.project_id,
            Resource.key == "engineering-evidence",
            Plugin.slug == "engineering",
        )
        .execution_options(populate_existing=True)
    ).first()
    if review is None or _canonical_digest(review.data_json) != request.review_content_digest:
        raise ConflictError("publication review changed")
    data = review.data_json
    facts = data.get("publication")
    if (
        data.get("status") != "passed"
        or data.get("lifecycle_state") != "current"
        or type(facts) is not dict
        or set(facts)
        != {
            "candidate_commit_oid",
            "candidate_report_sha256",
            "manifest_sha256",
            "canonical_item_id",
            "scope_digest",
        }
    ):
        raise ConflictError("publication review binding missing")
    if (
        any(
            facts[k] != getattr(request, k)
            for k in (
                "candidate_commit_oid",
                "candidate_report_sha256",
                "canonical_item_id",
                "scope_digest",
            )
        )
        or not isinstance(facts["manifest_sha256"], str)
        or len(facts["manifest_sha256"]) != 64
    ):
        raise ConflictError("publication review binding changed")
    key = _approval_ref_for_action(plan.grant_snapshot_json or {}, PUBLICATION_REF)
    approval = (
        ctx.session.exec(
            select(ApprovalRequest)
            .where(ApprovalRequest.run_plan_id == plan.id, ApprovalRequest.approval_key == key)
            .execution_options(populate_existing=True)
        ).first()
        if key
        else None
    )
    expected = dict(
        schema_version="stackos.publication-approval.v1",
        action_ref=PUBLICATION_REF,
        project_id=inp.project_id,
        controller_run_id=run.id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        request=request.model_dump(),
        review=facts,
        manifest_digest=_canonical_digest(described.manifest.model_dump(mode="json")),
    )
    if (
        approval is None
        or approval.id is None
        or approval.status != ApprovalRequestStatus.APPROVED
        or not approval.decided_by
        or _canonical_digest((approval.decision_json or {}).get("publication"))
        != _canonical_digest(expected)
    ):
        raise ConflictError("publication requires current exact approved review decision")
    settings, seed, storage_digest = _storage(ctx)
    git = _LocalGit(settings.state_dir, inp.project_id)
    fence = _ControlledFence(
        inp.project_id,
        run.id,
        plan.id,
        step.id,
        request.canonical_item_id,
        owner.owner_ref,
        owner.epoch,
        owner.revision,
        owner.scope_digest,
        _canonical_digest(continuity),
        approval.id,
        approval.updated_at,
        _canonical_digest(approval.decision_json),
        _canonical_digest(plan.grant_snapshot_json),
        _canonical_digest(step.action_refs_json),
    )
    return _PublicationAuthority(
        ctx,
        inp.model_copy(deep=True),
        request,
        fence,
        facts["manifest_sha256"],
        storage_digest,
        git.executable_digest,
        seed,
    )


def _resolve_publication_readback(
    inp: ActionExecuteInput, ctx: MCPContext, *, cached_action_call_id: int | None = None
) -> Any:
    if _action_ref(inp) != PUBLICATION_REF:
        return None
    _require_publication_session(ctx.session)
    with ctx.session.no_autoflush:
        return _read_publication_original(inp, ctx, cached_action_call_id=cached_action_call_id)


def _read_publication_original(
    inp: ActionExecuteInput, ctx: MCPContext, *, cached_action_call_id: int | None
) -> Any:
    request = _request(inp)
    if (
        current_context() is not ctx
        or ctx.extras.get("trusted_local_admin")
        or ctx.skill_name != RUN_PLAN_CONTROLLER_SKILL
        or not ctx.run_token
    ):
        raise ToolNotGrantedError("publication requires authenticated original controller")
    run, skill = resolve_run_token(ctx.run_token, ctx.session)
    if (
        run is None
        or skill != RUN_PLAN_CONTROLLER_SKILL
        or run.id != ctx.run_id
        or run.project_id != inp.project_id
        or ctx.project_id != inp.project_id
    ):
        raise ToolNotGrantedError("publication original controller mismatch")
    check_call_grant("action.execute", ctx, inp)
    key = _intent_key(inp.project_id, request)
    if inp.idempotency_key is not None and inp.idempotency_key != key:
        raise ConflictError("publication intent key mismatch")
    rows = ctx.session.exec(
        select(ActionCall)
        .where(ActionCall.project_id == inp.project_id, ActionCall.idempotency_key == key)
        .execution_options(populate_existing=True)
    ).all()
    if not rows:
        if cached_action_call_id is not None:
            raise ConflictError("publication cached original missing")
        return None
    if len(rows) != 1:
        raise ConflictError("publication original ambiguous")
    row = rows[0]
    if (
        row.run_id != ctx.run_id
        or row.request_json != request.model_dump()
        or _canonical_digest(row.request_json) != _canonical_digest(request.model_dump())
        or (cached_action_call_id is not None and row.id != cached_action_call_id)
    ):
        raise ConflictError("publication original binding changed")
    _, seed, storage_digest = _storage(ctx)
    if (row.metadata_json or {}).get("publication_private", {}).get(
        "storage_digest"
    ) != storage_digest:
        raise ConflictError("publication original storage changed")
    _verify_terminal(row, seed)
    from stackos.actions import ActionRepository

    return ActionRepository(ctx.session)._publication_out(row)


def _validate_publication_cached_replay(
    inp: ActionExecuteInput, ctx: MCPContext, cached: dict[str, Any]
) -> dict[str, Any]:
    if _action_ref(inp) == PUBLICATION_REF:
        _require_publication_session(ctx.session)
    data = cached.get("data")
    call = data.get("action_call") if type(data) is dict else None
    call_id = call.get("id") if type(call) is dict else None
    with ctx.session.no_autoflush:
        row = (
            ctx.session.get(ActionCall, call_id, populate_existing=True)
            if type(call_id) is int
            else None
        )
    requested = _action_ref(inp) == PUBLICATION_REF
    canonical = row is not None and (row.plugin_slug, row.action_key) == (
        "engineering",
        "publish-candidate.v1",
    )
    if not requested and not canonical:
        return cached
    if (
        not requested
        or not canonical
        or type(cached.get("project_id")) is not int
        or cached["project_id"] != ctx.project_id
        or type(cached.get("run_id")) is not int
        or cached["run_id"] != ctx.run_id
    ):
        raise ConflictError("publication cached identity unknown")
    out = _resolve_publication_readback(inp, ctx, cached_action_call_id=call_id)
    from stackos.mcp.contract import WriteEnvelope

    return WriteEnvelope(data=out, project_id=ctx.project_id, run_id=ctx.run_id).model_dump(
        mode="json"
    )
