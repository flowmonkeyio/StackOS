"""One useful native report→reviewed local publication through the loaded SDK.

Fixture setup uses canonical repositories in the exclusively owned disposable DB.
The review is a schema-validated automated fixture decision, never human acceptance.
Native execution, publication, replay, owner inspection and stop use real MCP calls.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime

from jsonschema import Draft202012Validator
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.actions.controlled_command import _canonical_digest
from stackos.actions.controlled_publication import (
    DESTINATION,
    PUBLICATION_REF,
    TARGET,
    ZERO,
    _candidate_ref,
    _intent_key,
    _PublicationInput,
    _verify_terminal,
)
from stackos.db.models import (
    ActionCall,
    ApprovalRequest,
    ApprovalRequestStatus,
    IdempotencyKey,
    Plugin,
    Resource,
    ResourceRecord,
    Run,
    RunPlan,
    RunPlanStep,
    TrackerTask,
    WorkItemOwnership,
)
from stackos.operations.actions.controlled import _native_storage
from stackos.repositories.run_plans import RunPlanRepository
from tests.integration import test_controlled_command_loaded as loaded
from tests.integration.test_controlled_publication import git
from tests.support.controlled_report import REPORT_SHA256, verify_report
from tests.support.supervision_loaded import require_denied, require_ok, sdk

pytestmark = loaded.pytestmark


def _snapshot(engine):
    with Session(engine) as session:
        return {
            model.__name__: [
                row.model_dump(mode="json")
                for row in session.exec(select(model).order_by(model.id)).all()
            ]
            for model in (
                ActionCall,
                ApprovalRequest,
                IdempotencyKey,
                ResourceRecord,
                Run,
                RunPlan,
                RunPlanStep,
                TrackerTask,
                WorkItemOwnership,
            )
        }


def _owned_target(settings, project):
    root = settings.state_dir / "engineering-publication" / TARGET
    root.mkdir(parents=True, mode=0o700)
    git(root, "init", "--quiet")
    (root / ".stackos-owned-synthetic.json").write_text(
        json.dumps(dict(schema_version="stackos.owned-synthetic.v1", project_id=project))
    )
    sentinel = root / "unrelated-sentinel"
    sentinel.write_bytes(b"owned Git evidence unchanged\n")
    return root


def _reviewed_candidate(settings, engine, case, root, report, owner, *, intent, expected):
    """Inspect actual output, validate the full schema, approve only the review key."""
    assert len(report) == 227 and hashlib.sha256(report).hexdigest() == REPORT_SHA256
    manifest = (
        json.dumps(
            dict(
                schema_version="stackos.synthetic-report-publication.v1",
                canonical_item_id=case.item,
                controller_run_id=case.run,
                run_plan_id=case.plan,
                run_plan_step_id=case.step,
                owner_ref=owner["owner_ref"],
                epoch=owner["epoch"],
                report_sha256=REPORT_SHA256,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    blobs = [git(root, "hash-object", "-w", "--stdin", data=b) for b in (manifest, report)]
    tree = git(
        root,
        "mktree",
        data=(
            f"100644 blob {blobs[0]}\tmanifest.json\n100644 blob {blobs[1]}\treport.json\n"
        ).encode(),
    )
    commit = git(root, "commit-tree", tree, data=(intent + "\n").encode())
    # The frozen oracle already proves one exact newline and no surrounding whitespace.
    assert (git(root, "show", commit + ":report.json") + "\n").encode() == report
    assert (git(root, "show", commit + ":manifest.json") + "\n").encode() == manifest
    facts = dict(
        candidate_commit_oid=commit,
        candidate_report_sha256=REPORT_SHA256,
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        canonical_item_id=case.item,
        scope_digest=loaded.SCOPE,
    )
    checked_at = datetime.now(UTC).isoformat()
    review_data = dict(
        schema_version="stackos.engineering-evidence.v2",
        title="Automated synthetic report and exact candidate inspection",
        evidence_type="automated-test",
        evidence_mode="executed",
        summary="Frozen 227-byte report and exact local Git blobs inspected by fixture verifier",
        repository_bound=False,
        status="passed",
        lifecycle_state="current",
        scope=dict(
            covered_paths=["report.json", "manifest.json"],
            flow_refs=["synthetic:loaded-report-publication"],
            ticket_keys=["MYB-100"],
        ),
        execution=dict(
            status="executed",
            command_or_run_ref="synthetic:loaded-report-and-candidate-verifier",
            pass_count=1,
            fail_count=0,
            skipped_count=0,
            output_checksum="sha256:" + REPORT_SHA256,
            executed_at=checked_at,
        ),
        checked_at=checked_at,
        publication=facts,
    )
    with Session(engine) as session:
        resource = session.exec(
            select(Resource)
            .join(Plugin, Resource.plugin_id == Plugin.id)
            .where(Resource.key == "engineering-evidence", Plugin.slug == "engineering")
        ).one()
        Draft202012Validator(resource.schema_data).validate(review_data)
        review = ResourceRecord(
            project_id=case.project, resource_id=resource.id, data_json=review_data
        )
        session.add(review)
        session.commit()
        payload = dict(
            canonical_item_id=case.item,
            owner_ref=owner["owner_ref"],
            expected_epoch=owner["epoch"],
            expected_revision=owner["revision"],
            scope_digest=loaded.SCOPE,
            intent_ref=intent,
            target_ref=TARGET,
            candidate_commit_oid=commit,
            candidate_report_sha256=REPORT_SHA256,
            expected_destination_oid=expected,
            review_record_id=review.id,
            review_content_digest=_canonical_digest(review_data),
        )
        request = _PublicationInput.model_validate(payload)
        git(root, "update-ref", _candidate_ref(case.project, request), commit)
        described = ActionRepository(session).describe(
            project_id=case.project, action_ref=PUBLICATION_REF
        )
        approval = dict(
            schema_version="stackos.publication-approval.v1",
            action_ref=PUBLICATION_REF,
            project_id=case.project,
            controller_run_id=case.run,
            run_plan_id=case.plan,
            run_plan_step_id=case.step,
            request=request.model_dump(),
            review=facts,
            manifest_digest=_canonical_digest(described.manifest.model_dump(mode="json")),
        )
        # This plan also has command approval; selecting its sole approval is invalid.
        RunPlanRepository(session).update(
            run_plan_id=case.plan,
            approval_key="review",
            approval_status=ApprovalRequestStatus.APPROVED,
            decided_by="synthetic:automated-fixture-reviewer",
            decision_json={"publication": approval},
        )
        actual = session.exec(
            select(ApprovalRequest).where(
                ApprovalRequest.run_plan_id == case.plan,
                ApprovalRequest.approval_key == "review",
            )
        ).one()
        assert actual.decision_json == {"publication": approval}
        assert actual.status == ApprovalRequestStatus.APPROVED
    return dict(
        project_id=case.project,
        run_token=case.args["run_token"],
        action_ref=PUBLICATION_REF,
        input_json=payload,
        response_mode="raw",
    )


def _verify_one_publication(settings, engine, case, args, result, root, owner):
    """Use actual sealed terminal audit plus one observable destination transition."""
    request = _PublicationInput.model_validate(args["input_json"])
    _, seed, _ = _native_storage(settings)
    with Session(engine) as session:
        calls = session.exec(
            select(ActionCall).where(
                ActionCall.project_id == case.project,
                ActionCall.action_key == "publish-candidate.v1",
            )
        ).all()
        assert len(calls) == 1, "More than one publication ActionCall was admitted"
        row = calls[0]
        assert row.request_json == args["input_json"]
        assert (row.run_id, row.run_plan_id, row.run_plan_step_id) == (
            case.run,
            case.plan,
            case.step,
        )
        admission = row.metadata_json["publication_private"]
        assert (admission["canonical_item_id"], admission["owner_ref"], admission["epoch"]) == (
            case.item,
            owner["owner_ref"],
            owner["epoch"],
        )
        assert admission["revision"] == owner["revision"] == request.expected_revision
        assert request.expected_epoch == owner["epoch"]
        assert _verify_terminal(row, seed) == result["data"]["output_json"]
        reservations = session.exec(
            select(IdempotencyKey).where(
                IdempotencyKey.project_id == case.project,
                IdempotencyKey.tool_name == "action.background",
                IdempotencyKey.run_id == case.run,
            )
        ).all()
        reservations = [
            reservation
            for reservation in reservations
            if reservation.response_json.get("action_call_id") == row.id
        ]
        assert len(reservations) == 1
        expected_key = hashlib.sha256(
            f"{PUBLICATION_REF}\0{_intent_key(case.project, request)}".encode()
        ).hexdigest()
        assert reservations[0].idempotency_key == expected_key
        assert row.id == result["data"]["action_call"]["id"]
    output = result["data"]["output_json"]
    assert output["outcome"] == "published" and output["outcome_unknown"] is False
    assert output["work_accepted"] is False and output["retry_safe"] is False
    assert output["observed_destination_oid"] == request.candidate_commit_oid
    assert git(root, "rev-parse", DESTINATION) == request.candidate_commit_oid
    lines = (root / ".git/logs" / DESTINATION).read_bytes().splitlines()
    assert len(lines) == 1, "Destination reflog contains more than one CAS transition"
    assert lines[0].split()[:2] == [ZERO.encode(), request.candidate_commit_oid.encode()]


async def _scenario(settings, engine, base, env, work, audit, daemon_owner):
    async with sdk(settings, base) as call:
        # Match the original fixture: initialize transport before setting its finite deadline.
        case = loaded._setup_case(settings, engine, publication=True)
        input_path = (
            case.command.source_root
            / "plugins/engineering/fixtures/controlled-status-input-v1.json"
        )
        original_input = input_path.read_bytes()
        sentinel = work / "unrelated-sentinel"
        sentinel.write_bytes(b"unchanged sentinel\n")
        report_result = require_ok(await call("action.execute", case.args))
        report_output = report_result["data"]["output_json"]
        assert report_output["status"] == "succeeded" and report_output["work_accepted"] is False
        verify_report(case.command.report_directory, input_path, original_input, sentinel)
        report = (case.command.report_directory / "report.json").read_bytes()
        after_report = _snapshot(engine)
        current_owner = after_report["WorkItemOwnership"][0]
        assert current_owner["continuity_json"]["attempts_used"] == 1
        assert current_owner["continuity_json"]["attempt_limit"] == 2
        assert current_owner["quiescence_revision"] == current_owner["revision"]
        assert "native_quiescence_certificate" in current_owner["continuity_json"]
        root = _owned_target(settings, case.project)
        args = _reviewed_candidate(
            settings,
            engine,
            case,
            root,
            report,
            current_owner,
            intent="synthetic:loaded-publication-1",
            expected=ZERO,
        )
        before_publication = _snapshot(engine)
        result = require_ok(await call("action.execute", args))
        _verify_one_publication(settings, engine, case, args, result, root, current_owner)
        after_publication = _snapshot(engine)
        assert after_publication["WorkItemOwnership"] == before_publication["WorkItemOwnership"]
        assert len(after_publication["ActionCall"]) == len(before_publication["ActionCall"]) + 1
        accepted = args["input_json"]["candidate_commit_oid"]
        reflog = (root / ".git/logs" / DESTINATION).read_bytes()
        replay = require_ok(await call("action.execute", deepcopy(args)))
        assert replay == {**result, "idempotency_replay": True}
        assert _snapshot(engine) == after_publication
        assert (root / ".git/logs" / DESTINATION).read_bytes() == reflog
        _verify_one_publication(settings, engine, case, args, result, root, current_owner)

        # Stop the actual owner, then bind an otherwise valid new request to its
        # fresh revision: denial must come from stop, not a stale revision.
        stop = require_ok(
            await call(
                "run.ownerStop",
                dict(
                    project_id=case.project,
                    canonical_item_id=case.item,
                    owner_ref=current_owner["owner_ref"],
                    scope_digest=loaded.SCOPE,
                    expected_epoch=current_owner["epoch"],
                    expected_revision=current_owner["revision"],
                    reason_ref="synthetic:publication-proof-stop",
                    run_token=case.args["run_token"],
                    response_mode="raw",
                ),
            )
        )
        stopped = stop["data"]["ownership"]
        assert stopped["epoch"] == current_owner["epoch"]
        assert stopped["revision"] == current_owner["revision"] + 1
        assert stopped["continuity_json"]["stopped"] is True
        stopped_args = _reviewed_candidate(
            settings,
            engine,
            case,
            root,
            report,
            stopped,
            intent="synthetic:loaded-publication-stopped-2",
            expected=accepted,
        )
        assert stopped_args["input_json"]["candidate_commit_oid"] != accepted
        before_denied = _snapshot(engine)
        denied = await call("action.execute", stopped_args)
        require_denied(denied)
        assert "original/current stop or denial remains binding" in json.dumps(denied.value)
        assert _snapshot(engine) == before_denied
        assert git(root, "rev-parse", DESTINATION) == accepted
        assert (root / ".git/logs" / DESTINATION).read_bytes() == reflog
        _verify_one_publication(settings, engine, case, args, result, root, current_owner)
        inspection = require_ok(
            await call(
                "run.ownerGet",
                dict(project_id=case.project, canonical_item_id=case.item, response_mode="raw"),
            )
        )
        assert inspection["ownership"] == stopped
        assert inspection["status"]["acceptance_state"] == "not_evaluated"
        assert _snapshot(engine) == before_denied
        verify_report(case.command.report_directory, input_path, original_input, sentinel)
        assert (root / "unrelated-sentinel").read_bytes() == b"owned Git evidence unchanged\n"
        # No seed, bearer/controller token, private admission or local Git path is public.
        public = json.dumps([report_result, result, replay, denied.value, inspection])
        for secret in (settings.token_path.read_text().strip(), case.args["run_token"], str(root)):
            assert secret not in public, "Private fixture authority or path leaked"
        assert "publication_private" not in public and "publication_seal" not in public
        audit.update(
            status="passed",
            report_action_call_id=report_result["data"]["action_call"]["id"],
            publication_action_call_id=result["data"]["action_call"]["id"],
            canonical_item_id=case.item,
            controller_run_id=case.run,
            run_plan_id=case.plan,
            run_plan_step_id=case.step,
            publication_epoch=current_owner["epoch"],
            publication_revision=current_owner["revision"],
            stopped_revision=stopped["revision"],
            report_sha256=REPORT_SHA256,
            report_bytes=len(report),
            accepted_commit_oid=accepted,
            observable_destination_cas_transitions=1,
            original_publication_replay_without_new_call_or_ref_change=True,
            stopped_new_intent_denied_without_new_call_or_ref_change=True,
            actual_owner_status=inspection["status"],
            work_accepted=False,
            human_acceptance="not_evaluated",
            scope_limit=(
                "One local synthetic flow. Reflog observes successful destination writes; "
                "no process instrumentation counts failed/no-op Git calls. Stop advances "
                "revision in the same epoch; this scenario does not test reassignment."
            ),
        )


def test_loaded_report_reviewed_publication_replay_and_stopped_new_intent(tmp_path):
    loaded.run_loaded_scenario(tmp_path, _scenario)
