"""Registered action over file-backed SQLite and exclusively owned real Git."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
import yaml
from jsonschema import Draft202012Validator
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

from stackos.actions import ActionRepository
from stackos.actions.controlled_command import _canonical_digest
from stackos.actions.controlled_publication import (
    _APPLE_TEMP_WARNING,
    DESTINATION,
    GIT,
    PUBLICATION_REF,
    TARGET,
    ZERO,
    _candidate_ref,
    _intent_key,
    _LocalGit,
    _PublicationInput,
)
from stackos.config import Settings
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
    IdempotencyKey,
    Plugin,
    PluginSource,
    Project,
    ProjectEvent,
    Resource,
    ResourceRecord,
    Run,
    TrackerTask,
    WorkItemOwnership,
)
from stackos.mcp.context import bind_context, build_context
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations import build_operation_registry
from stackos.operations.actions.execution import action_execute
from stackos.operations.actions.publication import (
    _issue_publication_authority,
    _resolve_publication_readback,
)
from stackos.operations.actions.schemas import ActionExecuteInput
from stackos.operations.dispatcher import OperationDispatcher
from stackos.repositories.base import ConflictError, RepositoryError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository


def git(root, *args, data=None):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "LC_ALL": "C",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.test",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.test",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    return (
        subprocess.run(
            [str(GIT), *args],
            cwd=root,
            env=env,
            input=data,
            capture_output=True,
            check=True,
        )
        .stdout.strip()
        .decode()
    )


@dataclass
class Case:
    engine: object
    session: Session
    settings: Settings
    project: int
    plan: int
    step: int
    run: int
    task: int
    root: Path
    args: dict

    def dispatch(self, args=None, *, session=None, operation="action.execute", surface="mcp"):
        active = session or self.session
        active.info["operation_services"] = {"settings": self.settings}
        return asyncio.run(
            OperationDispatcher(build_operation_registry()).dispatch(
                operation,
                deepcopy(self.args if args is None else args),
                session=active,
                surface=surface,
                client_surface="cli",
            )
        ).payload

    def approve(self, payload=None):
        payload = payload or self.args["input_json"]
        manifest = (
            ActionRepository(self.session)
            .describe(project_id=self.project, action_ref=PUBLICATION_REF)
            .manifest
        )
        review = self.session.get(ResourceRecord, payload["review_record_id"])
        expected = dict(
            schema_version="stackos.publication-approval.v1",
            action_ref=PUBLICATION_REF,
            project_id=self.project,
            controller_run_id=self.run,
            run_plan_id=self.plan,
            run_plan_step_id=self.step,
            request=payload,
            review=review.data_json["publication"],
            manifest_digest=_canonical_digest(manifest.model_dump(mode="json")),
        )
        approval = self.session.exec(
            select(ApprovalRequest).where(ApprovalRequest.run_plan_id == self.plan)
        ).one()
        # Fixture creates a new approved exact decision, never product approval bypass.
        approval.status = ApprovalRequestStatus.APPROVED
        approval.decided_by = "fixture:authenticated-review-operator"
        approval.decision_json = {"publication": expected}
        self.session.add(approval)
        self.session.commit()

    def snapshot(self):
        self.session.expire_all()
        return {
            model.__name__: [
                r.model_dump(mode="json")
                for r in self.session.exec(select(model).order_by(model.id)).all()
            ]
            for model in (Run, WorkItemOwnership, ActionCall, IdempotencyKey)
        }


@pytest.fixture
def case(tmp_path):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state", data_dir=tmp_path / "data")
    settings.state_dir.mkdir()
    settings.seed_path.write_bytes(b"s" * 32)
    settings.seed_path.chmod(0o600)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fixture.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_idempotency "
                "ON idempotency_keys(project_id, tool_name, idempotency_key)"
            )
        )
    session = Session(engine)
    session.info["operation_services"] = {"settings": settings}
    project = Project(slug="synthetic", name="Synthetic", domain="example.test", locale="en-US")
    plugin = Plugin(slug="engineering", name="Engineering", source=PluginSource.BUILTIN)
    session.add_all([project, plugin])
    session.commit()
    plugin_manifest = yaml.safe_load(
        (Path(__file__).parents[2] / "plugins/engineering/plugin.yaml").read_text()
    )
    declaration = plugin_manifest["actions"][0]
    assert declaration["key"] == "publish-candidate.v1"
    action = Action(
        plugin_id=plugin.id,
        key=declaration["key"],
        name=declaration["name"],
        risk_level="write",
        input_schema_json=declaration["input_schema"],
        config_json=declaration["config"],
    )
    evidence_schema = next(
        value["schema"]
        for value in plugin_manifest["resources"]
        if value["key"] == "engineering-evidence"
    )
    resource = Resource(
        plugin_id=plugin.id,
        key="engineering-evidence",
        name="Evidence",
        schema_data=evidence_schema,
    )
    session.add_all([action, resource])
    session.commit()
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project.id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "fixture.publication",
            "title": "Publish",
            "approvals": [{"key": "review", "title": "Exact authenticated review"}],
            "grants": {
                "mcp_tool_grants": [
                    {
                        "step_id": "publish",
                        "tool": "action.execute",
                        "action_refs": [PUBLICATION_REF],
                    }
                ],
                "action_contracts": [
                    {"key": "publish", "action": PUBLICATION_REF, "approval_ref": "review"}
                ],
            },
            "steps": [{"id": "publish", "title": "Publish", "action_refs": [PUBLICATION_REF]}],
        },
    ).data
    started = plans.start(plan.id, project_id=project.id).data
    step = plans.get_step(plan.id, "publish")
    TrackerRepository(session).create_task(
        project_id=project.id, key="source", title="Synthetic source"
    )
    task = session.exec(select(TrackerTask).where(TrackerTask.key == "source")).one()
    scope = "a" * 64
    task.source_json = {"run_plan_id": plan.id, "run_plan_step_id": step.id}
    task.metadata_json = {
        "ownership_continuity": dict(
            scope_digest=scope,
            budget_ref="fixture:budget",
            stop_scope_ref="fixture:stop",
            history_ref="fixture:history",
            effect_identity="fixture:effects",
            stopped=False,
            denied=False,
            attempts_used=0,
            failures_used=0,
            attempt_limit=5,
            failure_limit=5,
        )
    }
    session.add(task)
    session.commit()
    owner = (
        RunRepository(session)
        .owner_acquire(
            project_id=project.id,
            canonical_item_id=f"task:{task.id}",
            owner_ref="fixture:lead",
            scope_digest=scope,
        )
        .ownership
    )
    plans.claim_step(
        run_plan_id=plan.id, run_id=started.run_id, step_id="publish", claimed_by="fixture:lead"
    )
    root = settings.state_dir / "engineering-publication" / TARGET
    root.mkdir(parents=True)
    git(root, "init", "--quiet")
    (root / ".stackos-owned-synthetic.json").write_text(
        json.dumps(dict(schema_version="stackos.owned-synthetic.v1", project_id=project.id))
    )
    (root / "unrelated-sentinel").write_text("preserve")
    report = b'{"synthetic":true}\n'
    manifest = b'{"schema_version":"synthetic-report.v1"}\n'
    blobs = [git(root, "hash-object", "-w", "--stdin", data=blob) for blob in (manifest, report)]
    tree = git(
        root,
        "mktree",
        data=(
            f"100644 blob {blobs[0]}\tmanifest.json\n100644 blob {blobs[1]}\treport.json\n"
        ).encode(),
    )
    commit = git(root, "commit-tree", tree, data=b"Synthetic fixture\n")
    facts = dict(
        candidate_commit_oid=commit,
        candidate_report_sha256=hashlib.sha256(report).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        canonical_item_id=owner.canonical_item_id,
        scope_digest=scope,
    )
    review = ResourceRecord(
        project_id=project.id,
        resource_id=resource.id,
        data_json=dict(
            schema_version="stackos.engineering-evidence.v2",
            title="Synthetic fixture review",
            evidence_type="manual-review",
            evidence_mode="executed",
            summary="Exact synthetic candidate inspection",
            repository_bound=False,
            status="passed",
            lifecycle_state="current",
            scope=dict(
                covered_paths=["report.json", "manifest.json"],
                flow_refs=["fixture:publication"],
                ticket_keys=["fixture"],
            ),
            execution=dict(
                status="executed",
                command_or_run_ref="fixture:synthetic-review",
                pass_count=1,
                fail_count=0,
                skipped_count=0,
                output_checksum="sha256:" + "1" * 64,
                executed_at="2026-01-01T00:00:00Z",
            ),
            checked_at="2026-01-01T00:00:00Z",
            publication=facts,
        ),
    )
    # Fixture inserts only after validating the complete current plugin schema.
    Draft202012Validator(evidence_schema).validate(review.data_json)
    session.add(review)
    session.commit()
    payload = dict(
        canonical_item_id=owner.canonical_item_id,
        owner_ref=owner.owner_ref,
        expected_epoch=owner.epoch,
        expected_revision=owner.revision,
        scope_digest=scope,
        intent_ref="fixture:publish",
        target_ref=TARGET,
        candidate_commit_oid=commit,
        candidate_report_sha256=facts["candidate_report_sha256"],
        expected_destination_oid=ZERO,
        review_record_id=review.id,
        review_content_digest=_canonical_digest(review.data_json),
    )
    git(
        root,
        "update-ref",
        _candidate_ref(project.id, _PublicationInput.model_validate(payload)),
        commit,
    )
    result = Case(
        engine,
        session,
        settings,
        project.id,
        plan.id,
        step.id,
        started.run_id,
        task.id,
        root,
        dict(
            project_id=project.id,
            run_token=started.run_token,
            action_ref=PUBLICATION_REF,
            input_json=payload,
            response_mode="raw",
        ),
    )
    result.approve()
    yield result
    session.close()
    engine.dispose()


@pytest.mark.parametrize("charged_attempts", [1, 5])
def test_publication_uses_persisted_owner_counter_floor(case, charged_attempts):
    payload = case.args["input_json"]
    owner = RunRepository(case.session).owner_consume(
        project_id=case.project,
        canonical_item_id=payload["canonical_item_id"],
        owner_ref=payload["owner_ref"],
        scope_digest=payload["scope_digest"],
        expected_epoch=payload["expected_epoch"],
        expected_revision=payload["expected_revision"],
        attempts_delta=charged_attempts,
        failures_delta=0,
        evidence_ref="fixture:previous-native-charge",
    )
    assert owner.continuity_json["attempts_used"] == charged_attempts
    task = case.session.get(TrackerTask, case.task, populate_existing=True)
    assert task.metadata_json["ownership_continuity"]["attempts_used"] == 0
    payload["expected_revision"] = owner.revision
    case.approve()
    if charged_attempts == 5:
        before = case.snapshot()
        with pytest.raises(ConflictError, match="exhausted or regressed"):
            case.dispatch()
        assert case.snapshot() == before
        return
    result = case.dispatch()
    assert result["data"]["output_json"]["outcome"] == "published"
    assert git(case.root, "rev-parse", DESTINATION) == payload["candidate_commit_oid"]
    current = RunRepository(case.session).owner_get(
        project_id=case.project, canonical_item_id=payload["canonical_item_id"]
    )
    assert current.continuity_json["attempts_used"] == charged_attempts


def test_registered_real_git_success_original_readback_and_privacy(case, monkeypatch):
    before = case.snapshot()
    promote = _LocalGit.promote
    calls = []

    def observed(git_target, request):
        with Session(case.engine) as other:
            row = other.exec(select(ActionCall)).one()
            assert row.status == ActionCallStatus.RUNNING and row.response_json is None
            assert (
                len(
                    other.exec(
                        select(IdempotencyKey).where(
                            IdempotencyKey.tool_name == "action.background"
                        )
                    ).all()
                )
                == 1
            )
        calls.append(request.intent_ref)
        return promote(git_target, request)

    monkeypatch.setattr(_LocalGit, "promote", observed)
    result = case.dispatch(surface="rest")
    assert result["data"]["output_json"]["outcome"] == "published"
    assert result["data"]["output_json"]["work_accepted"] is False
    assert (
        git(case.root, "rev-parse", DESTINATION) == case.args["input_json"]["candidate_commit_oid"]
    )
    assert (case.root / "unrelated-sentinel").read_text() == "preserve"
    after = case.snapshot()
    assert (
        after["Run"] == before["Run"] and after["WorkItemOwnership"] == before["WorkItemOwnership"]
    )
    replay = case.dispatch()
    assert replay == {**result, "idempotency_replay": True}
    assert case.snapshot() == after and calls == ["fixture:publish"]
    for value in (
        result,
        ActionRepository(case.session)
        .get_call(project_id=case.project, action_call_id=result["data"]["action_call"]["id"])
        .model_dump(mode="json"),
    ):
        serialized = json.dumps(value)
        assert "publication_private" not in serialized and "publication_seal" not in serialized
        assert str(case.root) not in serialized and case.args["run_token"] not in serialized


@pytest.mark.parametrize(
    "change",
    [
        "stale_epoch",
        "stale_revision",
        "scope",
        "stopped",
        "denied",
        "budget",
        "revoked",
        "review",
        "approval",
        "unauthenticated",
        "direct",
        "arbitrary_path",
        "bool",
    ],
)
def test_denied_current_inputs_zero_git(case, change, monkeypatch):
    args = deepcopy(case.args)
    if change.startswith("stale_"):
        args["input_json"]["expected_" + change[6:]] += 1
    elif change == "scope":
        args["input_json"]["scope_digest"] = "b" * 64
    elif change in {"stopped", "denied", "budget"}:
        task = case.session.get(TrackerTask, case.task)
        value = deepcopy(task.metadata_json)
        value["ownership_continuity"]["attempts_used" if change == "budget" else change] = (
            5 if change == "budget" else True
        )
        task.metadata_json = value
        case.session.add(task)
        case.session.commit()
    elif change == "revoked":
        owner = case.session.exec(select(WorkItemOwnership)).one()
        owner.epoch += 1
        owner.owner_ref = "fixture:replacement"
        case.session.add(owner)
        case.session.commit()
    elif change == "review":
        row = case.session.get(ResourceRecord, args["input_json"]["review_record_id"])
        row.data_json = {**row.data_json, "status": "failed"}
        case.session.add(row)
        case.session.commit()
    elif change == "approval":
        row = case.session.exec(select(ApprovalRequest)).one()
        row.status = ApprovalRequestStatus.REJECTED
        case.session.add(row)
        case.session.commit()
    elif change == "unauthenticated":
        args.pop("run_token")
    elif change == "direct":
        args.pop("run_token")
        args["confirm_direct"] = True
        args["intent_summary"] = "Not forward authority"
    elif change == "arbitrary_path":
        args["input_json"]["path"] = "/other/repo"
    elif change == "bool":
        args["input_json"]["expected_epoch"] = True

    def forbidden(*args, **kwargs):
        raise AssertionError("zero Git required")

    monkeypatch.setattr(_LocalGit, "_run", forbidden)
    before = case.snapshot()
    with pytest.raises((RepositoryError, ValueError)):
        case.dispatch(args, operation="action.run" if change == "direct" else "action.execute")
    assert case.snapshot() == before


@pytest.mark.parametrize(
    "fault",
    [
        "symlink",
        "gitfile",
        "symbolic_destination",
        "alternates",
        "config",
        "candidate_drift",
        "checksum",
    ],
)
def test_owned_target_guards_preserve_sentinel(case, fault, monkeypatch):
    request = _PublicationInput.model_validate(case.args["input_json"])
    if fault == "symlink":
        (case.root / "escape").symlink_to(case.root.parent)
    elif fault == "gitfile":
        (case.root / ".git").rename(case.root / "foreign.git")
        (case.root / ".git").write_text("gitdir: foreign.git\n")
    elif fault == "symbolic_destination":
        git(case.root, "symbolic-ref", DESTINATION, "refs/heads/elsewhere")
    elif fault == "alternates":
        (case.root / ".git/objects/info/alternates").write_text("/foreign/objects\n")
    elif fault == "config":
        git(case.root, "config", "remote.foreign.url", "https://example.test/forbidden")
    elif fault == "candidate_drift":
        git(case.root, "update-ref", "-d", _candidate_ref(case.project, request))
    else:
        request = request.model_copy(update={"candidate_report_sha256": "f" * 64})
    with pytest.raises((ConflictError, ValueError)):
        _LocalGit(case.settings.state_dir, case.project).validate(
            request, hashlib.sha256(b'{"schema_version":"synthetic-report.v1"}\n').hexdigest()
        )
    assert (case.root / "unrelated-sentinel").read_text() == "preserve"


def test_two_sessions_same_intent_one_original_one_cas(case, monkeypatch):
    barrier = Barrier(2)
    updates = []
    promote = _LocalGit.promote

    def count(git_target, request):
        updates.append(request.intent_ref)
        return promote(git_target, request)

    monkeypatch.setattr(_LocalGit, "promote", count)

    # Synchronize requests at dispatch entry rather than changing policy internals.
    def run():
        with Session(case.engine) as session:
            barrier.wait(timeout=5)
            try:
                return case.dispatch(session=session)
            except ConflictError as exc:
                return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert (
        sum(
            isinstance(r, dict) and r["data"]["output_json"]["outcome"] == "published"
            for r in results
        )
        >= 1
    )
    case.session.expire_all()
    assert len(case.session.exec(select(ActionCall)).all()) == 1
    assert updates == ["fixture:publish"]
    assert (
        len(
            case.session.exec(
                select(IdempotencyKey).where(IdempotencyKey.tool_name == "action.background")
            ).all()
        )
        == 1
    )


@pytest.mark.parametrize("phase", ["before_reservation", "after_cas", "terminal_commit_lost_ack"])
def test_failure_intervals_original_unknown_or_readback(case, phase, monkeypatch):
    if phase == "before_reservation":

        def fail(session, *_args):
            if any(isinstance(row, ActionCall) for row in session.new):
                raise RuntimeError("fixture reservation failure")

        event.listen(Session, "before_flush", fail)
        try:
            with pytest.raises(RepositoryError):
                case.dispatch()
        finally:
            event.remove(Session, "before_flush", fail)
        assert not case.session.exec(select(ActionCall)).all()
        assert _LocalGit(case.settings.state_dir, case.project).destination() == ZERO
        return
    if phase == "after_cas":
        finalize = ActionRepository._finalize_running_call

        def fail(*args, **kwargs):
            raise RuntimeError("fixture post CAS pre audit")

        monkeypatch.setattr(ActionRepository, "_finalize_running_call", fail)
        with pytest.raises(ConflictError) as error:
            case.dispatch()
        assert error.value.data["outcome_unknown"] is True
        assert (
            git(case.root, "rev-parse", DESTINATION)
            == case.args["input_json"]["candidate_commit_oid"]
        )
        monkeypatch.setattr(ActionRepository, "_finalize_running_call", finalize)
        before = case.snapshot()
        with pytest.raises(ConflictError):
            case.dispatch()
        assert case.snapshot() == before
        assert case.session.exec(select(ActionCall)).one().status == ActionCallStatus.RUNNING
    else:
        out = ActionRepository._publication_out

        def lost(*args, **kwargs):
            raise RuntimeError("fixture lost response after commit")

        monkeypatch.setattr(ActionRepository, "_publication_out", lost)
        with pytest.raises(ConflictError):
            case.dispatch()
        monkeypatch.setattr(ActionRepository, "_publication_out", out)
        before = case.snapshot()
        assert case.dispatch()["data"]["output_json"]["outcome"] == "published"
        assert case.snapshot() == before


def test_zero_launch_effect_set_and_tamper_hold(case):
    repo = RunRepository(case.session)
    owner = case.session.exec(select(WorkItemOwnership)).one()
    runtime = repo._native_runtime_snapshot(
        project_id=case.project, canonical_item_id=owner.canonical_item_id
    )
    initial = repo._build_native_snapshot(owner, runtime=runtime)
    assert not initial.attempts and initial.effect.status == "known_no_effect"
    case.dispatch()
    case.session.expire_all()
    owner = case.session.exec(select(WorkItemOwnership)).one()
    actual = repo._build_native_snapshot(owner, runtime=runtime)
    assert actual.effect.status == "known_settled" and actual.digest != initial.digest
    assert len(actual.effect.rows) == 1
    row = case.session.exec(select(ActionCall)).one()
    row.response_json = {**row.response_json, "observed_destination_oid": ZERO}
    case.session.add(row)
    case.session.commit()
    assert repo._build_native_snapshot(owner, runtime=runtime).effect.status == "unknown"


def second_candidate(case):
    args = deepcopy(case.args)
    request = args["input_json"]
    tree = git(case.root, "rev-parse", request["candidate_commit_oid"] + "^{tree}")
    request["candidate_commit_oid"] = git(
        case.root, "commit-tree", tree, data=b"Different synthetic candidate\n"
    )
    request["intent_ref"] = "fixture:competing-intent"
    old_review = case.session.get(ResourceRecord, request["review_record_id"])
    data = deepcopy(old_review.data_json)
    data["publication"]["candidate_commit_oid"] = request["candidate_commit_oid"]
    review = ResourceRecord(
        project_id=case.project, resource_id=old_review.resource_id, data_json=data
    )
    case.session.add(review)
    case.session.commit()
    request["review_record_id"] = review.id
    request["review_content_digest"] = _canonical_digest(data)
    git(
        case.root,
        "update-ref",
        _candidate_ref(case.project, _PublicationInput.model_validate(request)),
        request["candidate_commit_oid"],
    )
    return args


def test_different_intent_registered_expected_old_conflict(case):
    other = second_candidate(case)
    winner = case.dispatch()
    case.approve(other["input_json"])
    with Session(case.engine) as session:
        loser = case.dispatch(other, session=session)
    assert winner["data"]["output_json"]["outcome"] == "published"
    assert loser["data"]["output_json"]["outcome"] == "conflict"
    assert loser["data"]["output_json"]["outcome_unknown"] is False
    assert (
        git(case.root, "rev-parse", DESTINATION) == case.args["input_json"]["candidate_commit_oid"]
    )
    assert len(case.session.exec(select(ActionCall)).all()) == 2


def test_real_git_two_candidates_compete_expected_old(case):
    other = second_candidate(case)
    requests = [_PublicationInput.model_validate(args["input_json"]) for args in (case.args, other)]
    barrier = Barrier(2)

    def compete(request):
        target = _LocalGit(case.settings.state_dir, case.project)
        target.validate(
            request, hashlib.sha256(b'{"schema_version":"synthetic-report.v1"}\n').hexdigest()
        )
        barrier.wait(timeout=5)
        return target.promote(request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, requests))
    assert sorted(result[0] for result in results) == ["conflict", "published"]
    winner = next(
        request.candidate_commit_oid
        for request, result in zip(requests, results, strict=True)
        if result[0] == "published"
    )
    assert git(case.root, "rev-parse", DESTINATION) == winner


def certify(case, monkeypatch):
    from stackos import controlled_process_clock as clock
    from stackos.controlled_process_host import _verify_native_quiescence

    # Portable clock observation seam only; no native launch/host is involved.
    monkeypatch.setattr(
        clock,
        "_sample_clock",
        lambda: clock._ClockObservation(
            "known",
            "10000000-0000-0000-0000-000000000001",
            100,
            100,
            runtime_abi=clock._SUPPORTED_ABI,
        ),
    )
    repo = RunRepository(case.session)
    owner = case.session.exec(select(WorkItemOwnership)).one()
    snapshot = repo._native_quiescence_snapshot(
        project_id=case.project,
        canonical_item_id=owner.canonical_item_id,
        owner_ref=owner.owner_ref,
        expected_epoch=owner.epoch,
        expected_revision=owner.revision,
    )
    verified = _verify_native_quiescence(case.session, snapshot=snapshot, owners=())
    return repo._certify_owner_quiescence(
        project_id=case.project,
        canonical_item_id=owner.canonical_item_id,
        owner_ref=owner.owner_ref,
        expected_epoch=owner.epoch,
        expected_revision=owner.revision,
        evidence_ref="fixture:portable-effect-certificate",
        verified=verified,
    )


def test_settled_certificate_consumption_and_pending_invalidates_prior(case, monkeypatch):
    initial = certify(case, monkeypatch)
    case.args["input_json"]["expected_revision"] = initial.revision
    case.approve()
    original = ActionRepository._finalize_running_call

    def fail(*args, **kwargs):
        raise RuntimeError("fixture unknown after Git")

    monkeypatch.setattr(ActionRepository, "_finalize_running_call", fail)
    with pytest.raises(ConflictError):
        case.dispatch()
    monkeypatch.setattr(ActionRepository, "_finalize_running_call", original)
    with pytest.raises(ConflictError, match="effects changed"):
        RunRepository(case.session).owner_reassign(
            project_id=case.project,
            canonical_item_id=initial.canonical_item_id,
            owner_ref=initial.owner_ref,
            new_owner_ref="fixture:replacement",
            scope_digest=initial.scope_digest,
            expected_epoch=initial.epoch,
            expected_revision=initial.revision,
        )


def test_published_known_settled_certificate_allows_reassignment(case, monkeypatch):
    case.dispatch()
    certified = certify(case, monkeypatch)
    assert (
        case.session.exec(select(WorkItemOwnership))
        .one()
        .continuity_json["native_quiescence_certificate"]["effect_snapshot"]["status"]
        == "known_settled"
    )
    changed = RunRepository(case.session).owner_reassign(
        project_id=case.project,
        canonical_item_id=certified.canonical_item_id,
        owner_ref=certified.owner_ref,
        new_owner_ref="fixture:replacement",
        scope_digest=certified.scope_digest,
        expected_epoch=certified.epoch,
        expected_revision=certified.revision,
    )
    assert changed.epoch == certified.epoch + 1


def test_stop_and_reassignment_serialize_while_cas_writer_held(case, monkeypatch):
    initial = certify(case, monkeypatch)
    case.args["input_json"]["expected_revision"] = initial.revision
    case.approve()
    promote = _LocalGit.promote
    outcomes = []

    def contender(operation):
        with Session(case.engine) as session:
            session.connection().exec_driver_sql("PRAGMA busy_timeout=0")
            repo = RunRepository(session)
            args = dict(
                project_id=case.project,
                canonical_item_id=initial.canonical_item_id,
                owner_ref=initial.owner_ref,
                expected_epoch=initial.epoch,
                expected_revision=initial.revision,
            )
            with pytest.raises(OperationalError, match="locked"):
                if operation == "stop":
                    repo.owner_stop(**args, reason_ref="fixture:concurrent-stop")
                else:
                    repo.owner_reassign(
                        **args,
                        new_owner_ref="fixture:replacement",
                        scope_digest=initial.scope_digest,
                    )
            outcomes.append(operation)

    def held(target, request):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(contender, ("stop", "reassign")))
        return promote(target, request)

    monkeypatch.setattr(_LocalGit, "promote", held)
    assert case.dispatch()["data"]["output_json"]["outcome"] == "published"
    assert sorted(outcomes) == ["reassign", "stop"]
    stopped = RunRepository(case.session).owner_stop(
        project_id=case.project,
        canonical_item_id=initial.canonical_item_id,
        owner_ref=initial.owner_ref,
        expected_epoch=initial.epoch,
        expected_revision=initial.revision,
        reason_ref="fixture:after-publish",
    )
    assert stopped.continuity_json["stopped"] is True


def test_publication_effect_is_independent_of_native_controller(case):
    from stackos import controlled_process_receipt as receipt

    case.dispatch()
    from stackos.actions.controlled_command import _resolve_controlled_command
    from stackos.controlled_process_host import _native_manifest

    runtime = _native_manifest(_resolve_controlled_command())
    request = case.args["input_json"]
    # A read-only unrelated native binding, no attempt or fake durable job is created.
    binding = receipt._Binding(
        case.project,
        request["canonical_item_id"],
        request["owner_ref"],
        request["expected_epoch"],
        request["expected_revision"],
        request["scope_digest"],
        case.run + 100,
        case.plan + 100,
        case.step + 100,
        999,
        "fixture:other-controller",
        _canonical_digest(runtime),
        "fixture:host",
        "10000000-0000-0000-0000-000000000001",
    )
    actual = ActionRepository(case.session)._controlled_effect_snapshot(
        project_id=case.project,
        bindings=(binding,),
        effect_identity="fixture:effects",
        command_manifests=(runtime,),
        _resolved_runtimes={binding.attempt_run_id: runtime},
        _owner_scope=(request["canonical_item_id"], request["owner_ref"]),
    )
    assert actual.status == "known_settled" and len(actual.rows) == 1


@pytest.mark.parametrize(
    "corruption", ["extra_private", "foreign_call", "foreign_project", "foreign_run"]
)
def test_dispatcher_cache_is_linkage_only_and_rebuilds_public_original(
    case, corruption, monkeypatch
):
    case.args["idempotency_key"] = _intent_key(
        case.project, _PublicationInput.model_validate(case.args["input_json"])
    )
    original = case.dispatch()
    cached = case.session.exec(
        select(IdempotencyKey).where(IdempotencyKey.tool_name == "action.execute")
    ).one()
    value = deepcopy(cached.response_json)
    if corruption == "extra_private":
        value["publication_private"] = "must-not-leak"
        value["data"]["metadata_json"] = {"publication_private": "must-not-leak"}
    elif corruption == "foreign_call":
        value["data"]["action_call"]["id"] += 100
    elif corruption == "foreign_project":
        value["project_id"] += 100
    else:
        value["run_id"] += 100
    cached.response_json = value
    case.session.add(cached)
    case.session.commit()
    before = case.snapshot()

    def forbidden(*args, **kwargs):
        raise AssertionError("cache readback must issue zero Git")

    monkeypatch.setattr(_LocalGit, "_run", forbidden)
    if corruption == "extra_private":
        assert case.dispatch() == {**original, "idempotency_replay": True}
    else:
        with pytest.raises(ConflictError):
            case.dispatch()
    assert case.snapshot() == before


@pytest.mark.parametrize("admission", [None, object()])
def test_repository_denies_missing_or_forged_capability(case, admission, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("direct repository cannot issue Git")

    monkeypatch.setattr(_LocalGit, "_run", forbidden)
    before = case.snapshot()
    with pytest.raises(RepositoryError):
        asyncio.run(
            ActionRepository(case.session).execute(
                project_id=case.project,
                action_ref=PUBLICATION_REF,
                input_json=case.args["input_json"],
                run_id=case.run,
                run_plan_id=case.plan,
                run_plan_step_id=case.step,
                _publication_authority=admission,
            )
        )
    assert case.snapshot() == before


def test_revoke_between_reservation_and_writer_guard_zero_git(case, monkeypatch):
    reserve = ActionRepository._reserve_background_call

    def stopped_after_reserve(repo, **kwargs):
        row, replayed = reserve(repo, **kwargs)
        with Session(case.engine) as other:
            owner = other.exec(select(WorkItemOwnership)).one()
            RunRepository(other).owner_stop(
                project_id=case.project,
                canonical_item_id=owner.canonical_item_id,
                owner_ref=owner.owner_ref,
                expected_epoch=owner.epoch,
                expected_revision=owner.revision,
                reason_ref="fixture:between-reservation-and-cas",
            )
        return row, replayed

    monkeypatch.setattr(ActionRepository, "_reserve_background_call", stopped_after_reserve)

    def forbidden(*args, **kwargs):
        raise AssertionError("revoked publication must issue zero Git")

    monkeypatch.setattr(_LocalGit, "_run", forbidden)
    with pytest.raises(ConflictError) as error:
        case.dispatch()
    assert error.value.data["dispatch_attempted"] is False
    assert case.session.exec(select(ActionCall)).one().status == ActionCallStatus.RUNNING


def test_git_conflict_diagnostic_is_exact_owned_ref(case):
    request = _PublicationInput.model_validate(case.args["input_json"])
    target = _LocalGit(case.settings.state_dir, case.project)
    assert target.promote(request)[0] == "published"
    code, _ = target._run(
        "update-ref",
        "--no-deref",
        DESTINATION,
        request.candidate_commit_oid,
        ZERO,
        allowed=(0, 1, 128),
    )
    assert code != 0
    lines = target._last_error.splitlines()
    assert (
        lines[-1]
        == (
            f"fatal: update_ref failed for ref '{DESTINATION}': "
            f"cannot lock ref '{DESTINATION}': reference already exists"
        ).encode()
    ), repr(target._last_error)
    assert lines[:-1] in ([], [_APPLE_TEMP_WARNING])


def test_final_audit_commit_failure_retains_committed_unknown_intent(case):
    def fail_terminal_commit(session):
        if any(
            isinstance(row, ActionCall) and row.status == ActionCallStatus.SUCCESS
            for row in session.dirty
        ):
            raise RuntimeError("fixture terminal transaction commit failure")

    event.listen(Session, "before_commit", fail_terminal_commit)
    try:
        with pytest.raises(ConflictError):
            case.dispatch()
    finally:
        event.remove(Session, "before_commit", fail_terminal_commit)
    assert (
        git(case.root, "rev-parse", DESTINATION) == case.args["input_json"]["candidate_commit_oid"]
    )
    row = case.session.exec(select(ActionCall)).one()
    assert row.status == ActionCallStatus.RUNNING and row.response_json is None
    before = case.snapshot()
    with pytest.raises(ConflictError):
        case.dispatch()
    assert case.snapshot() == before


@pytest.mark.parametrize("persistent", [False, True])
def test_real_owned_git_child_observation_failure_holds_and_retains_custody(
    case, monkeypatch, persistent
):
    from stackos.actions import controlled_publication as publication

    popen = subprocess.Popen
    owned = []

    class ObservedChild:
        def __init__(self, child):
            self.child = child
            self.failures = 0
            owned.append(self)

        @property
        def returncode(self):
            # A lost observation cannot claim that custody was accounted.
            return None if persistent else self.child.returncode

        def poll(self):
            return self.child.poll()

        def kill(self):
            return self.child.kill()

        def communicate(self, **kwargs):
            self.failures += 1
            if persistent or self.failures == 1:
                raise OSError("fixture owned-child observation lost")
            return self.child.communicate(**kwargs)

    def observed(argv, **kwargs):
        child = popen(argv, **kwargs)
        return ObservedChild(child) if "update-ref" in argv else child

    monkeypatch.setattr(subprocess, "Popen", observed)
    try:
        with pytest.raises(ConflictError):
            case.dispatch()
        assert len(owned) == 1
        assert (owned[0] in publication._OWNED_PUBLICATION_CHILDREN) is persistent
        assert case.session.exec(select(ActionCall)).one().status == ActionCallStatus.RUNNING
        before = case.snapshot()
        with pytest.raises(ConflictError):
            case.dispatch()
        assert case.snapshot() == before and len(owned) == 1
    finally:
        # Fixture recovery reaps only its exact child; product never retries Git.
        for custody in owned:
            custody.child.communicate()
            assert custody.child.returncode is not None
            publication._OWNED_PUBLICATION_CHILDREN.discard(custody)


def _publication_business_entry(case, boundary):
    """Prepare authentication before caller work; generic preflight is outside this promise."""
    inp = ActionExecuteInput.model_validate(case.args)
    ctx = build_context(case.args, case.session)
    repo = ActionRepository(case.session)
    with bind_context(ctx):
        authority = _issue_publication_authority(inp, ctx)

    def invoke():
        with bind_context(ctx):
            if boundary == "repository":
                return asyncio.run(
                    repo.execute(
                        project_id=case.project,
                        action_ref=PUBLICATION_REF,
                        input_json=inp.input_json,
                        run_id=case.run,
                        run_plan_id=case.plan,
                        run_plan_step_id=case.step,
                        idempotency_key=_intent_key(case.project, authority.request),
                        _publication_authority=authority,
                    )
                ).data
            return asyncio.run(action_execute(inp, ctx, ProgressEmitter(None, None))).data

    return ctx, inp, invoke


def _publication_database_snapshot(case):
    with Session(case.engine) as session:
        return {
            model.__name__: [
                row.model_dump(mode="json")
                for row in session.exec(select(model).order_by(model.id)).all()
            ]
            for model in (
                Project,
                TrackerTask,
                ResourceRecord,
                Run,
                WorkItemOwnership,
                ActionCall,
                IdempotencyKey,
                ProjectEvent,
            )
        }


@pytest.mark.parametrize("boundary", ["handler-forward", "handler-original", "repository"])
@pytest.mark.parametrize("pending", ["new", "dirty", "deleted", "physical-transaction"])
def test_publication_business_boundaries_preserve_caller_session(
    case, monkeypatch, boundary, pending
):
    if boundary == "handler-original":
        case.dispatch()
    _ctx, _inp, invoke = _publication_business_entry(case, boundary)
    task = case.session.get(TrackerTask, case.task)
    before = _publication_database_snapshot(case)
    if pending == "new":
        case.session.add(
            ActionCall(
                project_id=case.project,
                plugin_slug="fixture",
                action_key="pending-caller",
                operation="pending-caller",
                status=ActionCallStatus.RUNNING,
            )
        )
    elif pending == "dirty":
        task.title = "Caller unsaved title"
    elif pending == "deleted":
        case.session.delete(task)
    else:
        case.session.connection().exec_driver_sql("BEGIN")
    pending_before = (
        tuple(case.session.new),
        tuple(case.session.dirty),
        tuple(case.session.deleted),
    )
    transaction = case.session.get_transaction()
    driver = case.session.connection().connection.driver_connection
    actual_transaction = driver.in_transaction
    writes = []
    flushes = []

    def sql_written(_connection, _cursor, statement, *_args):
        if statement.lstrip().split(None, 1)[0].upper() in {
            "INSERT",
            "UPDATE",
            "DELETE",
            "REPLACE",
            "CREATE",
            "ALTER",
            "DROP",
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            "SAVEPOINT",
            "RELEASE",
        }:
            writes.append(statement)

    def flushed(*_args):
        flushes.append(True)

    def git_forbidden(*_args, **_kwargs):
        raise AssertionError("publication caller admission must issue zero Git")

    monkeypatch.setattr(_LocalGit, "_run", git_forbidden)
    event.listen(case.engine, "before_cursor_execute", sql_written)
    event.listen(case.session, "before_flush", flushed)
    try:
        with (
            patch.object(case.session, "commit", wraps=case.session.commit) as commit,
            patch.object(case.session, "rollback", wraps=case.session.rollback) as rollback,
        ):
            with pytest.raises(ConflictError, match="caller"):
                invoke()
            commit.assert_not_called()
            rollback.assert_not_called()
        assert not writes and not flushes
        assert (
            tuple(case.session.new),
            tuple(case.session.dirty),
            tuple(case.session.deleted),
        ) == pending_before
        assert case.session.get_transaction() is transaction
        assert driver.in_transaction is actual_transaction
        if pending == "dirty":
            assert task.title == "Caller unsaved title"
        assert _publication_database_snapshot(case) == before
    finally:
        event.remove(case.engine, "before_cursor_execute", sql_written)
        event.remove(case.session, "before_flush", flushed)
        case.session.rollback()  # Only the caller/fixture cleans up after proving preservation.


@pytest.mark.parametrize("boundary", ["handler", "repository"])
def test_publication_authenticated_logical_autobegin_success_and_readback(
    case, monkeypatch, boundary
):
    ctx, inp, invoke = _publication_business_entry(case, boundary)
    transaction = case.session.get_transaction()
    driver = case.session.connection().connection.driver_connection
    assert transaction is not None and driver.in_transaction is False
    with (
        patch.object(case.session, "commit", wraps=case.session.commit) as commit,
        patch.object(case.session, "rollback", wraps=case.session.rollback) as rollback,
    ):
        published = invoke()
        assert published.output_json["outcome"] == "published"
        assert case.session.get_transaction() is transaction and driver.in_transaction is False

        def forbidden(*_args, **_kwargs):
            raise AssertionError("original logical-autobegin readback must issue zero Git")

        monkeypatch.setattr(_LocalGit, "_run", forbidden)
        before = _publication_database_snapshot(case)
        with bind_context(ctx):
            readback = asyncio.run(action_execute(inp, ctx, ProgressEmitter(None, None))).data
            direct = _resolve_publication_readback(inp, ctx)
        assert readback == direct == published
        assert _publication_database_snapshot(case) == before
        assert case.session.get_transaction() is transaction and driver.in_transaction is False
        commit.assert_not_called()
        rollback.assert_not_called()
