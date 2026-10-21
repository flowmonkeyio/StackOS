"""Finite Main-operated continuation of the retained disposable MYB-103 case.

These selectors are never run as synthetic acceptance. Main supplies the actual
completed independent report and adjudication, then separately approves the
exact later-use proposal emitted by the first selector. Both reuse the accepted
daemon child owner; no alternate launcher, automatic retry or live-grant edit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import stat
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, create_engine, select
from stackos.actions.controlled_command import _canonical_digest
from stackos.actions.controlled_publication import (
    DESTINATION,
    PUBLICATION_REF,
    TARGET,
    _candidate_ref,
    _PublicationInput,
    _verify_terminal,
)
from stackos.operations.actions.controlled import _native_storage
from tests.integration.test_controlled_publication import git
from tests.support.controlled_report import REPORT_SHA256
from tests.support.supervision_loaded import require_ok, sdk

from stackos.actions import ActionRepository
from stackos.config import Settings
from stackos.db.models import ActionCall, AgentRequest, Learning, RunPlanStep, TrackerTicket
from tests.integration import test_controlled_command_loaded as loaded
from tests.integration import test_feedback_resolution_loaded as mechanics

pytestmark = loaded.pytestmark


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_private(path):
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.is_file() and not path.is_symlink()
    return json.loads(path.read_text())


def _save_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")


def _review(custody, retained, path):
    value = json.loads(path.read_text())
    assert value["schema"] == "stackos.feedback-actual-review-main.v1"
    assert value["kind"] == "actual_independent_review_and_main_decision"
    assert value["occurrence"] == custody["occurrence"]
    assert value["canonical_item_id"] == custody["item"]
    assert value["candidate_sha256"] == custody["candidate_sha256"]
    assert len(value["candidate_sha256"]) == 64
    assert value["capture_sha256"] == _sha(retained / "receipt.json")
    reviewer, main = value["reviewer"], value["main"]
    assert reviewer["completed"] is True and reviewer["disposition"] == "accepted"
    assert reviewer["actor"] not in {reviewer["author_actor"], main["actor"]}
    assert main["disposition"] == "accepted" and main["decision"] and main["decision_ref"]
    assert not reviewer["actor"].startswith("synthetic:")
    assert not main["actor"].startswith("synthetic:")
    assert not reviewer["actor"].startswith("REQUIRED_")
    assert not main["actor"].startswith("REQUIRED_")
    assert reviewer["report_sha256"] == _sha(Path(reviewer["report_path"]))
    assert value["lesson_id"] == custody["learning_id"]
    transfer = value["claim_custody"]
    assert transfer["explicit_transfer"] is True and transfer["custody_ref"]
    assert transfer["source_claimed_by"] == custody["claimed_by"]
    assert transfer["accepted_by"] == main["actor"]
    assert transfer["request_id"] == custody["request_id"]
    # This checks bindings and explicit Main custody, not human identity or the
    # semantic merits of an independent review. Main owns that adjudication.
    return value


def _run_retained(stage, exercise):
    assert sys.platform == "darwin"
    retained = Path(os.environ["STACKOS_FEEDBACK_RETAINED_WORK"]).resolve()
    original_receipt = json.loads((retained / "receipt.json").read_text())
    assert original_receipt["status"] == "mechanics_passed_actual_review_pending"
    assert original_receipt["owned_daemon_reaped"] is True
    assert original_receipt["cleanup_errors"] == []
    custody = _read_private(retained / "continuation-private.json")
    assert mechanics._source_manifest(Path(custody["source_root"])) == custody["source_manifest"]
    assert Path(custody["data_dir"]).resolve() == retained / "data"
    assert Path(custody["state_dir"]).resolve() == retained / "state"
    review_path = Path(os.environ["STACKOS_FEEDBACK_ACTUAL_REVIEW_FILE"]).resolve()
    assert _sha(review_path) == os.environ["STACKOS_FEEDBACK_ACTUAL_REVIEW_SHA256"]
    review = _review(custody, retained, review_path)
    stage_work = retained / stage
    stage_work.mkdir(
        mode=0o700, exist_ok=False
    )  # failed attempts retained; never rerun automatically
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    settings = Settings(
        _env_file=None,
        data_dir=Path(custody["data_dir"]),
        state_dir=Path(custody["state_dir"]),
        host="127.0.0.1",
        port=port,
        controlled_host_enabled=True,
    )
    env = deepcopy(custody["env"])
    env["STACKOS_PORT"] = str(port)
    assert set(env) == set(custody["env"]), "No ambient host/provider environment inherited"
    audit = dict(
        status="started",
        stage=stage,
        daemon_launches=0,
        candidate_sha256=custody["candidate_sha256"],
        actual_review_sha256=_sha(review_path),
    )
    daemon = loaded._LoadedDaemonOwner(settings, env, stage_work, audit)
    engine = None
    try:
        daemon.start()
        engine = create_engine(f"sqlite:///{settings.db_path}")
        asyncio.run(
            exercise(
                settings,
                engine,
                f"http://127.0.0.1:{port}",
                env,
                stage_work,
                audit,
                custody,
                review,
                retained,
            )
        )
    finally:
        primary = sys.exception()
        errors = []
        try:
            if engine is not None:
                engine.dispose()
        except BaseException as error:
            errors.append(error)
        try:
            daemon.close()
        except BaseException as error:
            errors.append(error)
        audit["cleanup_errors"] = [type(error).__name__ for error in errors]
        (stage_work / "receipt.json").write_text(json.dumps(audit, sort_keys=True, indent=2) + "\n")
        if errors:
            if primary is None:
                raise errors[0]
            for error in errors:
                primary.add_note("Retained daemon cleanup also failed: " + type(error).__name__)
    assert audit["owned_daemon_reaped"] is True and not audit["cleanup_errors"]


def _case(custody):
    return SimpleNamespace(
        project=custody["project"],
        item=custody["item"],
        task=custody["ticket_id"],
        ticket_key=custody["ticket_key"],
        plan=custody["plan"],
        step=custody["step"],
        run=custody["run"],
        args={"run_token": custody["run_token"]},
    )


async def _record(call, custody, step, result):
    return require_ok(
        await call(
            "runPlan.recordStep",
            dict(
                project_id=custody["project"],
                run_plan_id=custody["plan"],
                step_id=step,
                status="success",
                result_json=result,
                run_token=custody["run_token"],
                response_mode="raw",
            ),
        )
    )


async def _claim(call, custody, step, actor):
    return require_ok(
        await call(
            "runPlan.claimStep",
            dict(
                project_id=custody["project"],
                run_plan_id=custody["plan"],
                step_id=step,
                claimed_by=actor,
                run_token=custody["run_token"],
                response_mode="raw",
            ),
        )
    )


async def _complete_child(call, engine, custody, key, actor, evidence):
    with Session(engine) as session:
        child = session.exec(select(TrackerTicket).where(TrackerTicket.key == key)).one()
        preserved = deepcopy(child.completion_evidence_json or {})
    preserved.update(evidence)
    require_ok(
        await call(
            "tracker.updateTicket",
            dict(
                project_id=custody["project"],
                ticket_key=key,
                actor=actor,
                patch_json=dict(
                    status="complete", blocker_reason=None, completion_evidence_json=preserved
                ),
                response_mode="raw",
            ),
        )
    )


async def _prepare_later_use(call, settings, engine, custody, review, work):
    """Prepare exact local candidate; Main reviews the emitted payload before use."""
    with Session(engine) as session:
        child = session.exec(
            select(TrackerTicket).where(TrackerTicket.key == "loaded-feedback-later-use")
        ).one()
        reuse = session.exec(
            select(RunPlanStep).where(
                RunPlanStep.run_plan_id == custody["plan"], RunPlanStep.step_id == "reuse"
            )
        ).one()
        item, ticket_id, step_id = f"ticket:{child.id}", child.id, reuse.id
    require_ok(
        await call(
            "tracker.updateTicket",
            dict(
                project_id=custody["project"],
                ticket_key="loaded-feedback-later-use",
                actor=review["main"]["actor"],
                patch_json=dict(status="in-progress", assignee=review["main"]["actor"]),
                response_mode="raw",
            ),
        )
    )
    acquired = require_ok(
        await call(
            "run.ownerAcquire",
            dict(
                project_id=custody["project"],
                canonical_item_id=item,
                owner_ref=review["main"]["actor"],
                scope_digest=loaded.SCOPE,
                run_token=custody["run_token"],
                response_mode="raw",
            ),
        )
    )["data"]["ownership"]
    assert acquired["continuity_json"]["attempts_used"] == 0
    assert acquired["continuity_json"]["attempt_limit"] == 1
    root = settings.state_dir / "engineering-publication" / TARGET
    observed = git(root, "rev-parse", DESTINATION)
    assert observed == custody["accepted_oid"]
    report = (Path(custody["report_directory"]) / "report.json").read_bytes()
    assert len(report) == 227 and hashlib.sha256(report).hexdigest() == REPORT_SHA256
    manifest = (
        json.dumps(
            dict(
                schema_version="stackos.synthetic-report-publication.v1",
                canonical_item_id=item,
                controller_run_id=custody["run"],
                run_plan_id=custody["plan"],
                run_plan_step_id=step_id,
                owner_ref=acquired["owner_ref"],
                epoch=acquired["epoch"],
                report_sha256=REPORT_SHA256,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    blobs = [git(root, "hash-object", "-w", "--stdin", data=data) for data in (manifest, report)]
    tree = git(
        root,
        "mktree",
        data=(
            f"100644 blob {blobs[0]}\tmanifest.json\n100644 blob {blobs[1]}\treport.json\n"
        ).encode(),
    )
    intent = "synthetic:separately-admitted-lesson-use-1"
    commit = git(root, "commit-tree", tree, data=(intent + "\n").encode())
    facts = dict(
        candidate_commit_oid=commit,
        candidate_report_sha256=REPORT_SHA256,
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        canonical_item_id=item,
        scope_digest=loaded.SCOPE,
    )
    now = datetime.now(UTC).isoformat()
    review_data = dict(
        schema_version="stackos.engineering-evidence.v2",
        title="Exact later-use candidate and report inspection",
        evidence_type="automated-test",
        evidence_mode="executed",
        summary="Actual 227-byte report and exact candidate blobs verified",
        repository_bound=False,
        status="passed",
        lifecycle_state="current",
        scope=dict(
            covered_paths=["report.json", "manifest.json"],
            flow_refs=["synthetic:feedback-later-use"],
            ticket_keys=["MYB-103"],
        ),
        execution=dict(
            status="executed",
            command_or_run_ref=f"run:{custody['run']}",
            pass_count=1,
            fail_count=0,
            skipped_count=0,
            output_checksum="sha256:" + REPORT_SHA256,
            executed_at=now,
        ),
        checked_at=now,
        publication=facts,
    )
    assert (git(root, "show", commit + ":report.json") + "\n").encode() == report
    assert (git(root, "show", commit + ":manifest.json") + "\n").encode() == manifest
    evidence = require_ok(
        await call(
            "resource.upsert",
            dict(
                project_id=custody["project"],
                plugin_slug="engineering",
                resource_key="engineering-evidence",
                external_id="feedback-use-review:" + custody["occurrence"],
                data_json=review_data,
                run_token=custody["run_token"],
                response_mode="raw",
            ),
        )
    )["data"]
    request = _PublicationInput.model_validate(
        dict(
            canonical_item_id=item,
            owner_ref=acquired["owner_ref"],
            expected_epoch=acquired["epoch"],
            expected_revision=acquired["revision"],
            scope_digest=loaded.SCOPE,
            intent_ref=intent,
            target_ref=TARGET,
            candidate_commit_oid=commit,
            candidate_report_sha256=REPORT_SHA256,
            expected_destination_oid=observed,
            review_record_id=evidence["id"],
            review_content_digest=_canonical_digest(review_data),
        )
    )
    git(root, "update-ref", _candidate_ref(custody["project"], request), commit)
    with Session(engine) as session:
        described = ActionRepository(session).describe(
            project_id=custody["project"], action_ref=PUBLICATION_REF
        )
        reviewed_lesson = session.get(Learning, custody["learning_id"])
        assert reviewed_lesson.status == "active" and reviewed_lesson.review_state == "accepted"
        selected_lesson_digest = _canonical_digest(reviewed_lesson.model_dump(mode="json"))
    approval = dict(
        publication=dict(
            schema_version="stackos.publication-approval.v1",
            action_ref=PUBLICATION_REF,
            project_id=custody["project"],
            controller_run_id=custody["run"],
            run_plan_id=custody["plan"],
            run_plan_step_id=step_id,
            request=request.model_dump(),
            review=facts,
            manifest_digest=_canonical_digest(described.manifest.model_dump(mode="json")),
        )
    )
    proposal = dict(
        schema="stackos.feedback-later-use-proposal.v1",
        candidate_sha256=custody["candidate_sha256"],
        occurrence=custody["occurrence"],
        lesson_id=custody["learning_id"],
        lesson_digest=selected_lesson_digest,
        expected_destination_oid=observed,
        intended_outcome="one local publication with reconciled observed destination",
        scope="owned synthetic fixed target only",
        attempts=1,
        publication_approval=approval,
    )
    (work / "later-use-proposal.json").write_text(
        json.dumps(proposal, sort_keys=True, indent=2) + "\n"
    )
    (work / "later-use-admission.template.json").write_text(
        json.dumps(
            dict(
                schema="stackos.feedback-actual-later-use-admission.v1",
                main_actor=review["main"]["actor"],
                proposal_sha256=_sha(work / "later-use-proposal.json"),
                candidate_sha256=custody["candidate_sha256"],
                disposition="pending",
                admission_ref="REQUIRED_MAIN_EXPLICIT_USE_ADMISSION_REF",
                publication_approval=approval,
            ),
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    _save_private(
        work / "later-use-private.json",
        dict(
            owner=acquired,
            ticket_id=ticket_id,
            item=item,
            step=step_id,
            payload=request.model_dump(),
            proposal_sha256=_sha(work / "later-use-proposal.json"),
        ),
    )


async def _closeout(settings, engine, base, env, work, audit, custody, review, retained):
    case = _case(custody)
    with Session(engine) as session:
        request = session.get(AgentRequest, custody["request_id"])
        assert request.status.value == "run-created" and request.claimed_by == custody["claimed_by"]
        expiry = request.claim_expires_at.replace(tzinfo=UTC)
        assert expiry > datetime.now(UTC), (
            "Original legitimate claim expired; no replacement or reset"
        )
        lesson = session.get(Learning, custody["learning_id"])
        assert lesson.review_state == "proposed"
        assert request.project_id == lesson.project_id == custody["project"]
    async with sdk(settings, base) as call:
        original_owner = await mechanics._owner(call, case)
        assert original_owner == custody["original_owner"]
        actual_evidence = dict(
            actual_independent_review=review["reviewer"],
            main_adjudication=review["main"],
            candidate_sha256=review["candidate_sha256"],
            capture_sha256=review["capture_sha256"],
            resolution_disposition="accepted_by_Main",
            claim_custody=review["claim_custody"],
        )
        await _complete_child(
            call, engine, custody, custody["ticket_key"], review["main"]["actor"], actual_evidence
        )
        await _record(call, custody, "report", actual_evidence)
        await _claim(call, custody, "closeout", review["main"]["actor"])
        decision = require_ok(
            await call(
                "decision.record",
                dict(
                    project_id=custody["project"],
                    run_id=custody["run"],
                    decision=review["main"]["decision"],
                    decided_by=review["main"]["actor"],
                    evidence_json=actual_evidence,
                    tags=["MYB-103", custody["occurrence"]],
                    run_token=custody["run_token"],
                    response_mode="raw",
                ),
            )
        )["data"]
        chain = deepcopy(custody["chain"])
        chain.update(actual_evidence, actual_decision_id=decision["id"])
        promoted = require_ok(
            await call(
                "learning.update",
                dict(
                    project_id=custody["project"],
                    learning_id=custody["learning_id"],
                    review_state="accepted",
                    confidence="independently_reviewed_bounded_synthetic_case",
                    evidence_json=chain,
                    metadata_json=dict(
                        actual_decision_id=decision["id"],
                        review_kind="actual_independent_review_and_Main_adjudication",
                    ),
                    run_token=custody["run_token"],
                    response_mode="raw",
                ),
            )
        )["data"]
        assert promoted["id"] == custody["learning_id"] and promoted["review_state"] == "accepted"
        resolved = require_ok(
            await call(
                "agentRequest.complete",
                dict(
                    project_id=custody["project"],
                    request_id=custody["request_id"],
                    claim_token=custody["claim_token"],
                    status="resolved",
                    metadata_json=dict(
                        actual_decision_id=decision["id"], resolution_evidence=chain
                    ),
                    response_mode="raw",
                ),
            )
        )["data"]
        assert resolved["id"] == custody["request_id"] and resolved["status"] == "resolved"
        await _record(
            call,
            custody,
            "closeout",
            dict(actual_decision_id=decision["id"], accepted_learning_id=custody["learning_id"]),
        )
        await _claim(call, custody, "reuse", review["main"]["actor"])
        await _prepare_later_use(call, settings, engine, custody, review, work)
        assert await mechanics._owner(call, case) == original_owner
        audit.update(
            status="actual_review_recorded_same_request_resolved_same_lesson_promoted_use_pending",
            actual_decision_id=decision["id"],
            request_id=custody["request_id"],
            learning_id=custody["learning_id"],
            original_owner_budget_unchanged=True,
            actual_independent_review_ref=review["reviewer"]["report_sha256"],
            later_use_proposal_sha256=_sha(work / "later-use-proposal.json"),
        )


async def _later_use(settings, engine, base, env, work, audit, custody, review, retained):
    prior = retained / "actual-review-closeout"
    prior_receipt = json.loads((prior / "receipt.json").read_text())
    assert (
        prior_receipt["status"]
        == "actual_review_recorded_same_request_resolved_same_lesson_promoted_use_pending"
    )
    assert prior_receipt["owned_daemon_reaped"] is True and not prior_receipt["cleanup_errors"]
    proposal = json.loads((prior / "later-use-proposal.json").read_text())
    use = _read_private(prior / "later-use-private.json")
    assert use["proposal_sha256"] == _sha(prior / "later-use-proposal.json")
    admission_path = Path(os.environ["STACKOS_FEEDBACK_LATER_USE_ADMISSION_FILE"])
    assert _sha(admission_path) == os.environ["STACKOS_FEEDBACK_LATER_USE_ADMISSION_SHA256"]
    admission = json.loads(admission_path.read_text())
    assert admission["schema"] == "stackos.feedback-actual-later-use-admission.v1"
    assert admission["main_actor"] == review["main"]["actor"]
    assert admission["proposal_sha256"] == use["proposal_sha256"]
    assert admission["candidate_sha256"] == custody["candidate_sha256"]
    assert admission["disposition"] == "approved" and admission["admission_ref"]
    assert admission["publication_approval"] == proposal["publication_approval"]
    case = _case(custody)
    root = settings.state_dir / "engineering-publication" / TARGET
    with Session(engine) as session:
        lesson = session.get(Learning, custody["learning_id"])
        assert lesson.status == "active" and lesson.review_state == "accepted"
        lesson_digest = _canonical_digest(lesson.model_dump(mode="json"))
        assert lesson_digest == proposal["lesson_digest"]
    async with sdk(settings, base) as call:
        original_owner = await mechanics._owner(call, case)
        assert original_owner == custody["original_owner"]
        selected = require_ok(
            await call(
                "context.query",
                dict(
                    project_id=custody["project"],
                    sources=["learnings"],
                    fields=[
                        "statement",
                        "status",
                        "review_state",
                        "applies_to_json",
                        "evidence_json",
                    ],
                    statuses=["accepted"],
                    tags=["synthetic-feedback"],
                    limit=5,
                    run_token=custody["run_token"],
                    response_mode="raw",
                ),
            )
        )
        eligible = [
            row
            for row in selected["items"]
            if row["id"] == custody["learning_id"]
            and row["fields"]["status"] == "active"
            and row["fields"]["review_state"] == "accepted"
        ]
        assert len(eligible) == 1
        assert eligible[0]["fields"]["applies_to_json"]["settled_CAS_conflict"] is True
        assert eligible[0]["fields"]["applies_to_json"]["action_ref"] == PUBLICATION_REF
        # Actual application: reconcile original stale expected ZERO with current
        # state before constructing the separately admitted fixed publication.
        observed = git(root, "rev-parse", DESTINATION)
        assert (
            observed
            == proposal["expected_destination_oid"]
            == use["payload"]["expected_destination_oid"]
        )
        assert observed != "0" * 40
        require_ok(
            await call(
                "runPlan.update",
                dict(
                    project_id=custody["project"],
                    run_plan_id=custody["plan"],
                    approval_key="review",
                    approval_status="approved",
                    decided_by=admission["main_actor"],
                    decision_json=admission["publication_approval"],
                    response_mode="raw",
                ),
            )
        )
        before_calls = mechanics._rows(engine, ActionCall)
        before_log = (root / ".git/logs" / DESTINATION).read_bytes()
        args = dict(
            project_id=custody["project"],
            action_ref=PUBLICATION_REF,
            input_json=use["payload"],
            run_token=custody["run_token"],
            response_mode="raw",
        )
        result = require_ok(await call("action.execute", args))
        assert result["data"]["output_json"]["outcome"] == "published"
        after_calls = mechanics._rows(engine, ActionCall)
        assert len(after_calls) == len(before_calls) + 1
        _, seed, _ = _native_storage(settings)
        with Session(engine) as session:
            row = session.get(ActionCall, result["data"]["action_call"]["id"])
            assert _verify_terminal(row, seed) == result["data"]["output_json"]
            assert (row.run_id, row.run_plan_id, row.run_plan_step_id) == (
                custody["run"],
                custody["plan"],
                use["step"],
            )
            assert row.request_json["canonical_item_id"] == use["item"]
        after_log = (root / ".git/logs" / DESTINATION).read_bytes()
        assert len(after_log.splitlines()) == len(before_log.splitlines()) + 1
        assert git(root, "rev-parse", DESTINATION) == use["payload"]["candidate_commit_oid"]
        replay = require_ok(await call("action.execute", deepcopy(args)))
        assert replay == {**result, "idempotency_replay": True}
        assert mechanics._rows(engine, ActionCall) == after_calls
        assert (root / ".git/logs" / DESTINATION).read_bytes() == after_log
        reuse_case = SimpleNamespace(
            project=custody["project"], item=use["item"], args={"run_token": custody["run_token"]}
        )
        consumed = await mechanics._account(call, reuse_case, use["owner"], result, failed=False)
        assert (
            consumed["continuity_json"]["attempts_used"]
            == consumed["continuity_json"]["attempt_limit"]
            == 1
        )
        outcome = dict(
            selected_learning_id=custody["learning_id"],
            selected_learning_digest=lesson_digest,
            actual_review_decision_id=prior_receipt["actual_decision_id"],
            actual_use_admission_ref=admission["admission_ref"],
            prior_stale_expected_destination_oid="0" * 40,
            selected_expected_destination_oid=observed,
            action_call_id=result["data"]["action_call"]["id"],
            published_commit_oid=use["payload"]["candidate_commit_oid"],
            actual_publication_transitions=1,
            replay_without_new_effect=True,
            original_budget_unchanged=True,
        )
        evidence = await mechanics._evidence(
            call,
            SimpleNamespace(
                project=custody["project"],
                item=use["item"],
                ticket_key="loaded-feedback-later-use",
                plan=custody["plan"],
                step=use["step"],
                run=custody["run"],
                args={"run_token": custody["run_token"]},
            ),
            "later-use:" + custody["occurrence"],
            facts=outcome,
            status="passed",
            actual_review=review["reviewer"],
        )
        await _complete_child(
            call,
            engine,
            custody,
            "loaded-feedback-later-use",
            admission["main_actor"],
            dict(later_use_evidence_record_id=evidence["id"], **outcome),
        )
        # Compare public stakeholder reads while the controller is still live.
        await mechanics._stakeholder_reads(
            call,
            settings,
            engine,
            base,
            env,
            work,
            case,
            [
                ("agentRequest.get", dict(request_id=custody["request_id"])),
                (
                    "learning.query",
                    dict(
                        domain="engineering",
                        status="active",
                        review_state="accepted",
                        tags=["synthetic-feedback"],
                        fields=["statement", "status", "review_state"],
                    ),
                ),
                ("run.ownerGet", dict(canonical_item_id=custody["item"])),
            ],
        )
        assert await mechanics._owner(call, case) == original_owner
        await _record(
            call, custody, "reuse", dict(later_use_evidence_record_id=evidence["id"], **outcome)
        )
        audit.update(
            status="actual_review_closeout_and_separately_admitted_lesson_use_passed",
            **outcome,
            later_use_evidence_record_id=evidence["id"],
            synthetic_fixture_review_used_as_actual=False,
        )


@pytest.mark.skipif(
    os.environ.get("STACKOS_FEEDBACK_CONTINUATION_PHASE") != "closeout",
    reason="Main supplies actual independent review and closeout custody explicitly",
)
def test_retained_actual_review_closeout_and_prepare_one_later_use():
    _run_retained("actual-review-closeout", _closeout)


@pytest.mark.skipif(
    os.environ.get("STACKOS_FEEDBACK_CONTINUATION_PHASE") != "later-use",
    reason="Main separately admits the exact emitted later-use proposal",
)
def test_retained_separately_admitted_accepted_lesson_publication_use():
    _run_retained("actual-later-use", _later_use)
