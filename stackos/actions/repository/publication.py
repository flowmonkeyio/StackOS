"""One existing ActionCall reservation, writer-fenced Git CAS and final audit."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from sqlmodel import Session

from stackos.actions.controlled_publication import (
    _admission_payload,
    _audit_payload,
    _intent_key,
    _LocalGit,
    _seal,
)
from stackos.db.models import ActionCall, ActionCallStatus
from stackos.mcp.context import bind_context
from stackos.repositories.base import ConflictError, Envelope

from .schema import ActionExecutionOut

if TYPE_CHECKING:
    from stackos.operations.actions.publication import _PublicationAuthority


class PublicationActionMixin:
    def _publication_out(self, row: ActionCall) -> ActionExecutionOut:
        return ActionExecutionOut(
            action_call=self._call_audit_out(row),
            output_json=self._public_action_result(row),
            metadata_json={},
            credential_ref=None,
            cost_cents=0,
            dry_run=False,
        )

    async def _execute_publication(
        self, *, authority: _PublicationAuthority, manifest: Any
    ) -> Envelope[ActionExecutionOut]:
        from stackos.actions import ActionRepository
        from stackos.operations.actions.publication import (
            _require_publication_session,
            _resolve_publication_readback,
        )

        original = authority.ctx
        _require_publication_session(self._s)
        # Logical autobegin has no physical SQLite snapshot to discard. Keep
        # that caller Session intact; only the fresh private Session owns writes.
        engine = self._s.get_bind()
        request = authority.request
        metadata: dict[str, Any] = {"publication_private": authority.admission()}
        metadata["publication_admission_seal"] = _seal(
            authority.seed,
            _admission_payload(
                authority.fence.project_id, request.model_dump(), metadata["publication_private"]
            ),
        )
        key = _intent_key(authority.fence.project_id, request)
        with Session(engine) as session:
            session.info.update(self._s.info)
            ctx = replace(original, session=session)
            repo = ActionRepository(session)
            with bind_context(ctx):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")

                def guard() -> None:
                    authority.revalidate(ctx)

                def replay_guard(row: ActionCall) -> None:
                    _resolve_publication_readback(authority.inp, ctx, cached_action_call_id=row.id)

                row, replayed = repo._reserve_background_call(
                    project_id=authority.fence.project_id,
                    manifest=manifest,
                    credential_ref=None,
                    run_id=authority.fence.controller_run_id,
                    run_plan_id=authority.fence.run_plan_id,
                    run_plan_step_id=authority.fence.run_plan_step_id,
                    idempotency_key=key,
                    request_json=request.model_dump(),
                    provider_context_json=None,
                    metadata_json=metadata,
                    estimated_cost_cents=0,
                    commit_guard=guard,
                    replay_guard=replay_guard,
                )
                if replayed:
                    return Envelope(
                        data=repo._publication_out(row),
                        project_id=row.project_id,
                        run_id=row.run_id,
                    )
                call_id = row.id
                assert call_id is not None
                project = row.project_id
                # The original intent is durable now. No Git effect has occurred.
                session.rollback()
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                dispatched = False
                try:
                    current = authority.revalidate(ctx)
                    settings = ctx.extras["settings"]
                    git = _LocalGit(settings.state_dir, project)
                    if git.executable_digest != current.git_digest:
                        raise ConflictError("publication Git changed")
                    git.validate(request, current.review_manifest_sha256)
                    # Hold the canonical writer until child reaping, readback and final audit.
                    # Stops/reassignment use this same SQLite writer boundary.
                    authority.revalidate(ctx)
                    dispatched = True
                    outcome, observed = git.promote(request)
                    authority.revalidate(ctx)
                    result = self._publication_result(call_id, request, outcome, observed)
                    row = repo._finalize_running_call(
                        project_id=project,
                        action_call_id=call_id,
                        credential=None,
                        response_json=result,
                        metadata_json=metadata,
                        status=ActionCallStatus.SUCCESS
                        if outcome == "published"
                        else ActionCallStatus.FAILED,
                        cost_cents=0,
                        duration_ms=None,
                        error=None if outcome == "published" else "publication_conflict",
                        commit=False,
                    )
                    session.refresh(row)
                    row.metadata_json = {
                        **metadata,
                        "publication_seal": _seal(authority.seed, _audit_payload(row)),
                    }
                    session.add(row)
                    session.commit()
                    session.refresh(row)
                    return Envelope(
                        data=repo._publication_out(row), project_id=project, run_id=row.run_id
                    )
                except BaseException as exc:
                    session.rollback()
                    # Neither current ref equality nor an exception can settle a released CAS.
                    # Even pre-dispatch failures retain the original reservation: no hidden retry.
                    raise ConflictError(
                        "publication original held",
                        data={
                            "action_call_id": call_id,
                            "outcome_unknown": True,
                            "retry_safe": False,
                            "dispatch_attempted": dispatched,
                        },
                    ) from exc

    @staticmethod
    def _publication_result(
        call_id: int, request: Any, outcome: str, observed: str
    ) -> dict[str, Any]:
        return dict(
            schema_version="stackos.publication-result.v1",
            action_call_id=call_id,
            intent_ref=request.intent_ref,
            outcome=outcome,
            outcome_unknown=False,
            retry_safe=False,
            work_accepted=False,
            target_ref=request.target_ref,
            candidate_commit_oid=request.candidate_commit_oid,
            expected_destination_oid=request.expected_destination_oid,
            observed_destination_oid=observed,
            review_content_digest=request.review_content_digest,
        )
