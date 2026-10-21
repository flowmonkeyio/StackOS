"""Loaded MYB-103 mechanics; actual review, promotion and later use stay pending.

This uses synthetic publication approvals and a synthetic reviewer observation.
Neither is independent acceptance. The real incident request remains unresolved
and its learning proposed until actual independent review plus Main disposition.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlmodel import Session, select
from stackos.actions.controlled_command import _canonical_digest
from stackos.actions.controlled_publication import DESTINATION, ZERO, _verify_terminal
from stackos.operations.actions.controlled import _native_storage
from tests.integration.test_controlled_publication import git
from tests.support.controlled_report import REPORT_SHA256, verify_report
from tests.support.supervision_loaded import require_denied, require_ok, sdk

from stackos.db.models import ActionCall, AgentRequest, Learning, TrackerTicket
from tests.integration import test_controlled_command_loaded as loaded
from tests.integration import test_supervision_loaded_publication as publication

pytestmark = loaded.pytestmark


def _source_manifest(root):
    excluded = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    pins = {}
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in excluded)
        for name in sorted(files):
            if name in excluded or name.endswith(".pyc"):
                continue
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            pins[relative] = (
                ("symlink:" + os.readlink(path))
                if path.is_symlink()
                else ("sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
            )
    return pins


def _rows(engine, model):
    with Session(engine) as session:
        return [
            row.model_dump(mode="json")
            for row in session.exec(select(model).order_by(model.id)).all()
        ]


async def _owner(call, case):
    return require_ok(
        await call(
            "run.ownerGet",
            dict(project_id=case.project, canonical_item_id=case.item, response_mode="raw"),
        )
    )["ownership"]


async def _account(call, case, owner, result, *, failed):
    call_id = result["data"]["action_call"]["id"]
    return require_ok(
        await call(
            "run.ownerConsume",
            dict(
                project_id=case.project,
                canonical_item_id=case.item,
                owner_ref=owner["owner_ref"],
                scope_digest=loaded.SCOPE,
                expected_epoch=owner["epoch"],
                expected_revision=owner["revision"],
                attempts_delta=1,
                failures_delta=int(failed),
                evidence_ref=f"action-call:{call_id}",
                idempotency_key=f"feedback-account-call-{call_id}",
                run_token=case.args["run_token"],
                response_mode="raw",
            ),
        )
    )["data"]["ownership"]


async def _evidence(call, case, occurrence, *, facts, status="failed", actual_review="pending"):
    now = datetime.now(UTC).isoformat()
    return require_ok(
        await call(
            "resource.upsert",
            dict(
                project_id=case.project,
                plugin_slug="engineering",
                resource_key="engineering-evidence",
                external_id="feedback:" + occurrence,
                title="Synthetic bounded publication resolution evidence",
                data_json=dict(
                    schema_version="stackos.engineering-evidence.v2",
                    title="Synthetic bounded publication resolution evidence",
                    evidence_type="automated-test",
                    evidence_mode="executed",
                    summary="Observed CAS conflict and bounded routine repair outcome",
                    repository_bound=False,
                    status=status,
                    lifecycle_state="current",
                    scope=dict(
                        covered_paths=["report.json", "manifest.json"],
                        flow_refs=["synthetic:feedback-resolution"],
                        ticket_keys=[case.ticket_key],
                    ),
                    execution=dict(
                        status="executed",
                        command_or_run_ref=f"run:{case.run}",
                        pass_count=int(status == "passed"),
                        fail_count=int(status == "failed"),
                        skipped_count=0,
                        output_checksum="sha256:" + _canonical_digest(facts),
                        executed_at=now,
                    ),
                    checked_at=now,
                    resolution=dict(
                        occurrence_ref=occurrence,
                        canonical_item_id=case.item,
                        run_plan_id=case.plan,
                        run_plan_step_id=case.step,
                        controller_run_id=case.run,
                        facts=facts,
                        actual_independent_review=actual_review,
                    ),
                ),
                provenance_json=dict(source="synthetic:loaded-action-audit", occurrence=occurrence),
                run_token=case.args["run_token"],
                response_mode="raw",
            ),
        )
    )["data"]


async def _child(call, engine, case, *, evidence, blocker):
    with Session(engine) as session:
        row = session.get(TrackerTicket, case.task)
        metadata = deepcopy(row.metadata_json or {})
    metadata["feedback_resolution"] = dict(
        accountable_actor="synthetic:lead",
        next_action="independent_review",
        occurrence_ref=evidence["occurrence_ref"],
    )
    require_ok(
        await call(
            "tracker.updateTicket",
            dict(
                project_id=case.project,
                ticket_key=case.ticket_key,
                patch_json=dict(
                    status="in-progress",
                    assignee="synthetic:lead",
                    blocker_reason=blocker,
                    completion_evidence_json=evidence,
                    metadata_json=metadata,
                ),
                actor="synthetic:main-controller",
                response_mode="raw",
            ),
        )
    )


def _verify_publication_chain(settings, engine, case, results):
    _, seed, _ = _native_storage(settings)
    with Session(engine) as session:
        rows = session.exec(
            select(ActionCall)
            .where(
                ActionCall.project_id == case.project,
                ActionCall.action_key == "publish-candidate.v1",
            )
            .order_by(ActionCall.id)
        ).all()
        assert len(rows) == 3
        assert len({row.request_json["intent_ref"] for row in rows}) == 3
        assert [_verify_terminal(row, seed) for row in rows] == [
            result["data"]["output_json"] for result in results
        ]
        for row in rows:
            assert (row.run_id, row.run_plan_id, row.run_plan_step_id) == (
                case.run,
                case.plan,
                case.step,
            )
            assert row.request_json["canonical_item_id"] == case.item
            assert row.metadata_json["publication_private"]["epoch"] == 1
            assert row.request_json["expected_epoch"] == 1
    assert [r["data"]["output_json"]["outcome"] for r in results] == [
        "conflict",
        "conflict",
        "published",
    ]
    assert all(r["data"]["output_json"]["work_accepted"] is False for r in results)


async def _scenario(settings, engine, base, env, work, audit, daemon_owner):
    candidate = os.environ["STACKOS_FEEDBACK_CANDIDATE_SHA256"]
    assert len(candidate) == 64 and set(candidate) <= set("0123456789abcdef")
    async with sdk(settings, base) as call:
        case = loaded._setup_case(settings, engine, publication=True, feedback=True)
        assert case.item.startswith("ticket:") and case.ticket_key == "loaded-feedback-resolution"
        input_path = (
            case.command.source_root
            / "plugins/engineering/fixtures/controlled-status-input-v1.json"
        )
        original_input = input_path.read_bytes()
        sentinel = work / "unrelated-sentinel"
        sentinel.write_bytes(b"unchanged sentinel\n")
        report_result = require_ok(await call("action.execute", case.args))
        assert report_result["data"]["output_json"]["status"] == "succeeded"
        verify_report(case.command.report_directory, input_path, original_input, sentinel)
        report = (case.command.report_directory / "report.json").read_bytes()
        root = publication._owned_target(settings, case.project)
        owner = await _owner(call, case)
        assert owner["continuity_json"]["attempts_used"] == 1

        original_args = publication._reviewed_candidate(
            settings,
            engine,
            case,
            root,
            report,
            owner,
            intent="synthetic:feedback-original-1",
            expected=ZERO,
        )
        tree = git(
            root, "rev-parse", original_args["input_json"]["candidate_commit_oid"] + "^{tree}"
        )
        base_oid = git(root, "commit-tree", tree, data=b"Fixture prior destination\n")
        git(root, "update-ref", DESTINATION, base_oid, ZERO)
        original = require_ok(await call("action.execute", original_args))
        assert original["data"]["output_json"]["outcome"] == "conflict"
        assert original["data"]["output_json"]["observed_destination_oid"] == base_oid
        owner = await _account(call, case, owner, original, failed=True)
        assert _rows(engine, AgentRequest) == [], "Routine handling must precede rerouting"

        # Routine handling is diagnosis/eligibility, not an unconditional retry.
        # Changed CAS arguments need a fresh exact approval, so route this occurrence once.
        original_id = original["data"]["action_call"]["id"]
        occurrence = _canonical_digest(
            dict(
                project_id=case.project,
                action_call_id=original_id,
                candidate_commit_oid=original_args["input_json"]["candidate_commit_oid"],
                scope_digest=loaded.SCOPE,
            )
        )
        facts = dict(
            original_failure_call_id=original_id,
            cause_code="publication_expected_destination_changed",
            cause_confidence="corroborated_by_terminal_CAS_observations",
            original_observed_oid=base_oid,
            routine_handling=dict(
                classification="known_settled_CAS_conflict",
                repair_eligible=False,
                reason="changed_arguments_require_fresh_exact_approval",
            ),
            candidate_report_sha256=REPORT_SHA256,
            required_actor="synthetic:lead",
            required_action="bounded_diagnosis_and_review",
        )
        evidence_record = await _evidence(call, case, occurrence, facts=facts)
        request_args = dict(
            project_id=case.project,
            request_key="supervision-resolution:v1:" + occurrence,
            title="One unresolved synthetic publication occurrence",
            body_preview="Routine diagnosis found changed arguments need exact review approval",
            source_kind="bounded-publication-failure",
            source_resource_key="engineering-evidence",
            source_resource_record_id=evidence_record["id"],
            metadata_json=dict(
                canonical_item_id=case.item,
                run_plan_id=case.plan,
                run_plan_step_id=case.step,
                controller_run_id=case.run,
                source_occurrence_digest=occurrence,
                owner_ref=owner["owner_ref"],
                owner_epoch=owner["epoch"],
                required_actor="synthetic:lead",
                next_action="bounded_diagnosis_and_review",
            ),
            idempotency_key="feedback-intake:" + occurrence,
            run_token=case.args["run_token"],
            response_mode="raw",
        )
        request = require_ok(await call("agentRequest.create", request_args))["data"]
        duplicate_args = deepcopy(request_args)
        duplicate_args["idempotency_key"] += ":second-observation"
        duplicate = require_ok(await call("agentRequest.create", duplicate_args))["data"]
        assert duplicate["id"] == request["id"] and len(_rows(engine, AgentRequest)) == 1
        assert duplicate["metadata_json"]["source_occurrence_digest"] == occurrence
        claim = require_ok(
            await call(
                "agentRequest.claim",
                dict(
                    project_id=case.project,
                    request_id=request["id"],
                    claimed_by="synthetic:lead",
                    lease_seconds=86400,
                    idempotency_key="feedback-claim:" + occurrence,
                    response_mode="raw",
                ),
            )
        )["data"]
        claim_token = claim["claim_token"]
        linked = require_ok(
            await call(
                "agentRequest.linkRunPlan",
                dict(
                    project_id=case.project,
                    request_id=request["id"],
                    run_plan_id=case.plan,
                    claim_token=claim_token,
                    response_mode="raw",
                ),
            )
        )["data"]
        assert linked["status"] == "run-created" and linked["run_plan_id"] == case.plan
        chain = dict(
            occurrence_ref=occurrence,
            request_id=request["id"],
            evidence_record_id=evidence_record["id"],
            original_failure_call_id=original_id,
            actual_independent_review="pending",
            resolution_disposition="unresolved",
        )
        await _child(
            call, engine, case, evidence=chain, blocker="Actual independent review pending"
        )
        before_bad_claim = _rows(engine, AgentRequest)
        require_denied(
            await call(
                "agentRequest.complete",
                dict(
                    project_id=case.project,
                    request_id=request["id"],
                    claim_token="invalid-fixture-claim",
                    status="resolved",
                    response_mode="raw",
                ),
            )
        )
        assert _rows(engine, AgentRequest) == before_bad_claim

        # Named negative in feedback-resolution-spec: an ineffective admitted repair
        # must not resolve the incident. This intentionally retains the wrong OID;
        # no artificial competing writer or hidden production mutation is introduced.
        routine_args = publication._reviewed_candidate(
            settings,
            engine,
            case,
            root,
            report,
            owner,
            intent="synthetic:feedback-ineffective-repair-2",
            expected=ZERO,
        )
        routine = require_ok(await call("action.execute", routine_args))
        assert routine["data"]["output_json"]["outcome"] == "conflict"
        assert routine["data"]["output_json"]["observed_destination_oid"] == base_oid
        owner = await _account(call, case, owner, routine, failed=True)
        routine_id = routine["data"]["action_call"]["id"]
        assert owner["continuity_json"]["failures_used"] == 2
        assert len(_rows(engine, AgentRequest)) == 1
        assert _rows(engine, AgentRequest)[0]["status"] == "run-created"
        chain["ineffective_repair_call_id"] = routine_id
        facts["ineffective_repair_call_id"] = routine_id
        await _evidence(call, case, occurrence, facts=facts)
        await _child(
            call, engine, case, evidence=chain, blocker="Ineffective repair; review pending"
        )

        repaired_args = publication._reviewed_candidate(
            settings,
            engine,
            case,
            root,
            report,
            owner,
            intent="synthetic:feedback-reviewed-candidate-3",
            expected=base_oid,
        )
        before_success = (root / ".git/logs" / DESTINATION).read_bytes()
        repaired = require_ok(await call("action.execute", repaired_args))
        assert repaired["data"]["output_json"]["outcome"] == "published"
        owner = await _account(call, case, owner, repaired, failed=False)
        assert owner["continuity_json"]["attempts_used"] == 4
        assert owner["continuity_json"]["failures_used"] == 2
        accepted_oid = repaired_args["input_json"]["candidate_commit_oid"]
        assert git(root, "rev-parse", DESTINATION) == accepted_oid
        after_success = (root / ".git/logs" / DESTINATION).read_bytes()
        assert len(after_success.splitlines()) == len(before_success.splitlines()) + 1
        assert after_success.splitlines()[-1].split()[:2] == [
            base_oid.encode(),
            accepted_oid.encode(),
        ]
        calls_before_replay = _rows(engine, ActionCall)
        failed_replay = require_ok(await call("action.execute", deepcopy(original_args)))
        success_replay = require_ok(await call("action.execute", deepcopy(repaired_args)))
        assert failed_replay == {**original, "idempotency_replay": True}
        assert success_replay == {**repaired, "idempotency_replay": True}
        assert _rows(engine, ActionCall) == calls_before_replay
        assert (root / ".git/logs" / DESTINATION).read_bytes() == after_success
        _verify_publication_chain(settings, engine, case, [original, routine, repaired])

        # Fixture observation supplies mechanically checked facts; it cannot accept work.
        synthetic_review = dict(
            review_kind="synthetic_test_only",
            independent_review=False,
            observed_candidate_oid=accepted_oid,
            observed_successful_publication_calls=1,
            actual_independent_review="pending",
            main_acceptance="pending",
        )
        chain.update(
            repaired_call_id=repaired["data"]["action_call"]["id"],
            candidate_commit_oid=accepted_oid,
            synthetic_review=synthetic_review,
        )
        await _child(
            call, engine, case, evidence=chain, blocker="Actual independent review pending"
        )
        learning = require_ok(
            await call(
                "learning.create",
                dict(
                    project_id=case.project,
                    statement="Reconcile observed CAS state before new approved intent",
                    domain="engineering",
                    confidence="synthetic_mechanics_only",
                    review_state="proposed",
                    created_by="synthetic:main-controller",
                    tags=["MYB-103", "synthetic-feedback"],
                    applies_to_json=dict(
                        action_ref="engineering.publish-candidate.v1", settled_CAS_conflict=True
                    ),
                    evidence_json=chain,
                    metadata_json=dict(
                        review_kind="synthetic_test_only", actual_independent_review="pending"
                    ),
                    idempotency_key="feedback-proposed-lesson:" + occurrence,
                    run_token=case.args["run_token"],
                    response_mode="raw",
                ),
            )
        )["data"]
        before_promotion = _rows(engine, Learning)
        require_denied(
            await call(
                "learning.update",
                dict(
                    project_id=case.project,
                    learning_id=learning["id"],
                    review_state="accepted",
                    run_token=case.args["run_token"],
                    response_mode="raw",
                ),
            )
        )
        assert _rows(engine, Learning) == before_promotion
        accepted_lessons = require_ok(
            await call(
                "learning.query",
                dict(
                    project_id=case.project,
                    status="active",
                    review_state="accepted",
                    tags=["synthetic-feedback"],
                    fields=["statement", "review_state"],
                    response_mode="raw",
                ),
            )
        )
        assert accepted_lessons["items"] == []
        # A later step is frozen but cannot start while this report/child is unresolved.
        require_denied(
            await call(
                "runPlan.claimStep",
                dict(
                    project_id=case.project,
                    run_plan_id=case.plan,
                    step_id="closeout",
                    claimed_by="synthetic:main-controller",
                    run_token=case.args["run_token"],
                    response_mode="raw",
                ),
            )
        )
        assert _rows(engine, AgentRequest)[0]["status"] == "run-created"
        assert _rows(engine, Learning)[0]["review_state"] == "proposed"
        child = next(r for r in _rows(engine, TrackerTicket) if r["key"] == case.ticket_key)
        assert child["status"] == "in-progress" and child["assignee"] == "synthetic:lead"
        assert child["completion_evidence_json"]["actual_independent_review"] == "pending"
        verify_report(case.command.report_directory, input_path, original_input, sentinel)
        assert (root / "unrelated-sentinel").read_bytes() == b"owned Git evidence unchanged\n"
        public = json.dumps(
            [original, routine, repaired, failed_replay, success_replay, learning, chain]
        )
        for secret in (
            claim_token,
            case.args["run_token"],
            settings.token_path.read_text().strip(),
        ):
            assert secret not in public, "Private authority leaked into feedback evidence"
        audit.update(
            status="mechanics_passed_actual_review_pending",
            canonical_item_id=case.item,
            ticket_key=case.ticket_key,
            request_id=request["id"],
            learning_id=learning["id"],
            occurrence_ref=occurrence,
            original_failure_call_id=original_id,
            ineffective_repair_call_id=routine_id,
            repaired_call_id=repaired["data"]["action_call"]["id"],
            candidate_commit_oid=accepted_oid,
            report_sha256=hashlib.sha256(report).hexdigest(),
            original_failure_and_success_replayed_without_new_calls=True,
            successful_publication_transitions=1,
            fixture_destination_transitions=1,
            request_disposition="unresolved_pending_actual_independent_review",
            learning_review_state="proposed",
            actual_independent_review=False,
            later_admitted_usage="not_executed_requires_real_review_and_Main_closeout",
            autonomous_code_repair=False,
        )
        # Explicit retained active execution custody, never public evidence. Main
        # continues this same disposable occurrence after actual review, within
        # its original claim lease; expiry fails closed without resetting state.
        private = dict(
            schema="stackos.feedback-retained-custody.v1",
            project=case.project,
            item=case.item,
            ticket_key=case.ticket_key,
            ticket_id=case.task,
            plan=case.plan,
            step=case.step,
            run=case.run,
            run_token=case.args["run_token"],
            request_id=request["id"],
            claim_token=claim_token,
            claim_expires_at=claim["claim_expires_at"],
            claimed_by=claim["claimed_by"],
            learning_id=learning["id"],
            occurrence=occurrence,
            chain=chain,
            accepted_oid=accepted_oid,
            report_directory=str(case.command.report_directory),
            env=env,
            data_dir=str(settings.data_dir),
            state_dir=str(settings.state_dir),
            original_owner=owner,
            candidate_sha256=os.environ["STACKOS_FEEDBACK_CANDIDATE_SHA256"],
            source_root=str(case.command.source_root),
            source_manifest=_source_manifest(case.command.source_root),
        )
        custody = work / "continuation-private.json"
        fd = os.open(custody, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(private, stream, sort_keys=True)
            stream.write("\n")
        audit["retained_custody"] = custody.name


def test_loaded_routine_failure_routes_unresolved_child_and_preserves_review_gate(tmp_path):
    loaded.run_loaded_scenario(tmp_path, _scenario)
    work = tmp_path / "owned-loaded-proof"
    private = json.loads((work / "continuation-private.json").read_text())
    template = dict(
        schema="stackos.feedback-actual-review-main.v1",
        kind="actual_independent_review_and_main_decision",
        occurrence=private["occurrence"],
        canonical_item_id=private["item"],
        candidate_sha256=private["candidate_sha256"],
        capture_sha256=hashlib.sha256((work / "receipt.json").read_bytes()).hexdigest(),
        lesson_id=private["learning_id"],
        reviewer=dict(
            actor="REQUIRED_ACTUAL_REVIEWER",
            author_actor="REQUIRED_ACTUAL_AUTHOR",
            report_path="REQUIRED_RAW_COMPLETED_REPORT",
            report_sha256="REQUIRED_SHA256",
            completed=False,
            disposition="pending",
        ),
        main=dict(
            actor="REQUIRED_MAIN_ACTOR",
            disposition="pending",
            decision="REQUIRED_MAIN_ADJUDICATION",
            decision_ref="REQUIRED_MAIN_DECISION_REF",
        ),
        claim_custody=dict(
            source_claimed_by=private["claimed_by"],
            accepted_by="REQUIRED_MAIN_ACTOR",
            request_id=private["request_id"],
            explicit_transfer=False,
            custody_ref="REQUIRED_EXPLICIT_ACTIVE_CLAIM_CUSTODY_REF",
        ),
    )
    (work / "actual-review-input.template.json").write_text(json.dumps(template, indent=2) + "\n")


def _db_state(engine):
    """All table values, compared in memory only; private values are never emitted."""
    with engine.connect() as connection:
        tables = (
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            .scalars()
            .all()
        )
        return {
            name: connection.exec_driver_sql(
                'SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid'
            ).fetchall()
            for name in tables
        }


async def _stakeholder_reads(call, settings, engine, base, env, work, case, operations):
    """Three public operation adapters return the same bounded factual readback."""
    before = _db_state(engine)
    sentinel = work / "stakeholder-sentinel"
    sentinel.write_bytes(b"read-only stakeholder evidence\n")
    evidence_before = sentinel.read_bytes()
    result = {}
    token = settings.token_path.read_text().strip()
    with httpx.Client(
        headers={"Authorization": "Bearer " + token},
        transport=httpx.HTTPTransport(retries=0),
        trust_env=False,
        follow_redirects=False,
    ) as rest:
        for index, (operation, arguments) in enumerate(operations):
            args = dict(project_id=case.project, response_mode="raw", **arguments)
            mcp = require_ok(await call(operation, args))
            response = rest.post(
                base + f"/api/v1/operations/{operation}/call", json={"arguments": args}
            )
            response.raise_for_status()
            request_file = work / f"stakeholder-read-{index}.json"
            request_file.write_text(json.dumps(args))
            cli = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "stackos",
                    "ops",
                    "call",
                    operation,
                    "--input",
                    str(request_file),
                    "--response-mode",
                    "raw",
                ],
                cwd=work,
                env=env,
                capture_output=True,
                check=True,
            )
            assert loaded._without_capture(mcp) == loaded._without_capture(response.json())
            assert loaded._without_capture(mcp) == loaded._without_capture(json.loads(cli.stdout))
            result[operation] = mcp
    assert _db_state(engine) == before
    assert sentinel.read_bytes() == evidence_before
    return result


async def _f8_scenario(settings, engine, base, env, work, audit, daemon_owner):
    async with sdk(settings, base) as call:
        case = loaded._setup_case(
            settings, engine, publication=True, feedback=True, feedback_filter_proof=True
        )
        before = _db_state(engine)
        # Exact granted resource/action boundaries. These requests fail before
        # creating any resource/action/queue row or launching a controlled child.
        resource_denial = await call(
            "resource.upsert",
            dict(
                project_id=case.project,
                plugin_slug="engineering",
                resource_key="ungranted-feedback-record",
                data_json={},
                run_token=case.args["run_token"],
                response_mode="raw",
            ),
        )
        require_denied(resource_denial)
        assert resource_denial.value["message"] == "ToolNotGrantedError"
        unbound = deepcopy(case.args)
        del unbound["run_token"]
        action_denial = await call("action.execute", unbound)
        require_denied(action_denial)
        assert action_denial.value["message"] == "ToolNotGrantedError"
        assert _db_state(engine) == before
        tag = "synthetic-only-filter-mechanics"

        async def create(statement, **extra):
            return require_ok(
                await call(
                    "learning.create",
                    dict(
                        project_id=case.project,
                        statement=statement,
                        domain="engineering",
                        status="active",
                        review_state="accepted",
                        tags=[tag],
                        confidence="synthetic_storage_filter_only",
                        metadata_json=dict(
                            review_kind="synthetic_test_only", independent_review=False
                        ),
                        run_token=case.args["run_token"],
                        response_mode="raw",
                        **extra,
                    ),
                )
            )["data"]

        old = await create("Synthetic superseded rule")
        replacement = await create("Synthetic replacement rule", supersedes_learning_id=old["id"])

        async def eligible():
            return require_ok(
                await call(
                    "learning.query",
                    dict(
                        project_id=case.project,
                        domain="engineering",
                        status="active",
                        review_state="accepted",
                        tags=[tag],
                        fields=["statement", "status", "review_state"],
                        response_mode="raw",
                    ),
                )
            )["items"]

        # A supersedes pointer alone deliberately has no automatic policy effect.
        assert {row["id"] for row in await eligible()} == {old["id"], replacement["id"]}
        require_ok(
            await call(
                "learning.update",
                dict(
                    project_id=case.project,
                    learning_id=old["id"],
                    status="archived",
                    review_state="revoked",
                    evidence_json=dict(synthetic_test_only=True, superseded_by=replacement["id"]),
                    run_token=case.args["run_token"],
                    response_mode="raw",
                ),
            )
        )
        assert [row["id"] for row in await eligible()] == [replacement["id"]]
        views = await _stakeholder_reads(
            call,
            settings,
            engine,
            base,
            env,
            work,
            case,
            [
                ("run.ownerGet", dict(canonical_item_id=case.item)),
                (
                    "learning.query",
                    dict(
                        domain="engineering",
                        status="active",
                        review_state="accepted",
                        tags=[tag],
                        fields=["statement", "status", "review_state"],
                    ),
                ),
            ],
        )
        assert views["run.ownerGet"]["status"]["acceptance_state"] == "not_evaluated"
        assert views["learning.query"]["items"][0]["id"] == replacement["id"]
        require_ok(
            await call(
                "learning.update",
                dict(
                    project_id=case.project,
                    learning_id=replacement["id"],
                    status="archived",
                    review_state="revoked",
                    evidence_json=dict(synthetic_test_only=True, revoked=True),
                    run_token=case.args["run_token"],
                    response_mode="raw",
                ),
            )
        )
        assert await eligible() == []
        assert len(_rows(engine, Learning)) == 2 and _rows(engine, ActionCall) == []
        assert _rows(engine, AgentRequest) == []
        audit.update(
            status="synthetic_f8_mechanics_passed",
            independent_review=False,
            granted_authority_denials_without_mutation=True,
            supersedes_pointer_does_not_retire=True,
            explicit_revocation_filtered=True,
            stakeholder_mcp_rest_cli_same_readback_without_writes=True,
            learning_rows_retained=2,
            actual_acceptance=False,
        )


def test_loaded_feedback_authority_supersession_revocation_and_stakeholder_reads(tmp_path):
    loaded.run_loaded_scenario(tmp_path, _f8_scenario)
