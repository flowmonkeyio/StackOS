"""Canonical adoption and finite native continuation, with deterministic adapters.

These fixtures exercise the real request executor and persisted native envelopes.
Injected native runners are not actual host or installed activation evidence.
"""

# Imported pytest fixtures are intentionally shadowed by fixture arguments.
# ruff: noqa: F811

import asyncio
import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from sqlmodel import select

from stackos import task_delivery
from stackos.db.models import AgentRequest, RunPlan, TrackerTask
from stackos.host_mcp import service
from stackos.host_mcp.adapters import codex
from stackos.host_mcp.project_agents import ProjectAgentService
from stackos.host_mcp.result import AgentRunResult
from stackos.mcp.context import MCPContext
from stackos.operations.project_setup_state import ProjectSetupReportInput, project_setup_report
from stackos.project_setup_execution import RESULT_SCHEMA
from stackos.repositories.base import ConflictError
from stackos.repositories.project_setup import (
    ProjectSetupRepository,
    SetupActivationEvidence,
    SetupReport,
    _digest,
)
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.workspaces import WorkspaceRepository
from tests.integration.test_repositories.test_managed_setup import (
    launch,
    managed,  # noqa: F401
    native_lifetime,  # noqa: F401
)
from tests.integration.test_repositories.test_project_setup import verified_result

KEY = "engineering.tracked-delivery"


@pytest.mark.parametrize("verification", [None, "passed", "failed", "blocked"])
def test_native_setup_schema_file_is_closed_required_and_nullable(
    tmp_path, monkeypatch, verification
):
    """Use the real adapter schema file and result decoder, replacing only execution."""
    from jsonschema import ValidationError as JsonValidationError
    from jsonschema import validate

    report = SetupReport(
        status="partial",
        progress=dict(message="Bounded native observation", evidence_ref="fixture/observation"),
    )
    payload = {"report_json": report.model_dump_json(), "verification_status": verification}
    emitted = []

    def execute(command, **kwargs):
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        emitted.append(schema)
        assert schema == RESULT_SCHEMA
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        validate(payload, schema)
        for invalid in (
            {"report_json": payload["report_json"]},
            {**payload, "verification_status": "unknown"},
            {**payload, "extra": True},
        ):
            with pytest.raises(JsonValidationError):
                validate(invalid, schema)
        Path(command[command.index("-o") + 1]).write_text(json.dumps(payload))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fixture/codex")
    monkeypatch.setattr(codex.subprocess, "run", execute)
    result = service.run_agent("codex", tmp_path, "Setup fixture", "Bounded", RESULT_SCHEMA)
    assert result.ok, result.error
    assert len(emitted) == 1 and result.result == payload
    assert SetupReport.model_validate_json(result.result["report_json"]) == report


def proposal_for(managed, session, *, host="codex"):
    _, project, _, _ = managed
    state = ProjectSetupRepository(session).get(project)
    from stackos.repositories.projects import ProjectRepository

    root = Path(ProjectRepository(session).get(project).project_dir)
    roles = {}
    for role, name in (("delivery_agent", "builder"), ("review_agent", "reviewer")):
        choice = dict(host_key=host, name=name, provenance_ref="fixture/reviewed-adaptation")
        if host == "codex":
            folder = root / ".codex" / "agents"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{name}.toml"
            path.write_text(
                f'name="{name}"\ndescription="Fixture"\ndeveloper_instructions="Check setup"\n'
            )
            ref = dict(host_key=host, native_name=name, source_path=f".codex/agents/{name}.toml")
            source = ProjectAgentService(root).get(ref)
            choice.update(source_ref=ref, expected_digest=source.digest)
        else:
            choice.update(instructions="Check setup", expected_digest=_digest("Check setup"))
        roles[role] = choice
    return dict(
        project_id=project,
        binding_id=state.binding_id,
        project_dir=str(root),
        expected_delivery_digest=_digest(
            TrackerRepository(session).delivery_defaults(project_id=project)
        ),
        roles=roles,
    )


def install_runner(
    managed, monkeypatch, proposal, *, mutate=None, verification="passed", setup_host=None
):
    _execution, project, _, factory = managed
    definition = service._definition_by_key(
        setup_host or proposal["roles"]["delivery_agent"]["host_key"]
    )
    calls = []

    def runner(root, instructions, prompt, schema, *, on_session=None, resume_thread_id=None, **kw):
        context = json.loads(prompt)
        calls.append(context)
        if on_session:
            on_session("bounded-setup-native")
        assert context["requested_scope"] == "ready_to_use"
        if context["predecessor_request_id"]:
            assert resume_thread_id == "bounded-setup-native"
            with factory() as check:
                predecessor = check.get(AgentRequest, context["predecessor_request_id"])
                assert predecessor.claim_token_hash is None
                native = predecessor.metadata_json.get("native_execution")
                if native:
                    assert native["finished_at"]
                else:
                    prior = predecessor.metadata_json["project_setup"]["managed"]
                    assert not prior["callback_started"] or prior["preflight_failed"]
                    assert (
                        predecessor.metadata_json["project_setup"]["managed"]["resume_thread_id"]
                        == resume_thread_id
                    )
                assert not predecessor.metadata_json["project_setup"]["managed"]["slot_reserved"]
        report = dict(
            status="partial",
            progress=dict(message="Bounded setup fixture", evidence_ref="fixture/checked"),
        )
        extra = {"verification_status": None}
        with factory() as check:
            repo = ProjectSetupRepository(check)
            if context["phase"] == "preparation":
                result = verified_result(repo, project)
                result.attempt_ref = context["attempt_ref"]
                report.update(
                    adoption_proposal=proposal,
                    workflow_results=[
                        dict(
                            workflow_key=KEY,
                            expected_revision=0,
                            result=result.model_dump(mode="json"),
                        )
                    ],
                )
            elif context["phase"] == "verification":
                assert context["remaining_workflow_keys"] == [KEY]
                actual = task_delivery.resolve_delivery_settings(
                    check, project_id=project, project_dir=str(root)
                )
                assert context["adoption_readback"] == actual
                extra["verification_status"] = verification
            else:
                assert context["phase"] == "completion"
                result = verified_result(repo, project)
                result.attempt_ref = context["attempt_ref"]
                result.verification.activation_proof = SetupActivationEvidence(
                    method_ref="fixture/method",
                    status="verified",
                    required_claims=["saved_defaults", "native_consumption"],
                    checks=[
                        dict(
                            claims=["saved_defaults", "native_consumption"],
                            origin="executed",
                            basis=repo.activation_basis(
                                project,
                                result.verification,
                                environment_ref="fixture/injected-native",
                            ),
                            reference=context["completed_verification"]["reference"],
                            evidence_ref="fixture/deterministic-native-observation",
                        )
                    ],
                )
                report.update(
                    status="success",
                    workflow_results=[
                        dict(
                            workflow_key=KEY,
                            expected_revision=context["observed_receipt_revisions"][KEY],
                            result=result.model_dump(mode="json"),
                        )
                    ],
                )
        if mutate:
            mutate(context)
        return AgentRunResult(
            0,
            thread_id="bounded-setup-native",
            result={"report_json": json.dumps(report), **extra},
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=runner)
    )
    return calls


@pytest.mark.parametrize("phase", ["preparation", "verification"])
@pytest.mark.parametrize("final_outcome", ["failed", "identical", "conflicting_question"])
def test_native_final_outcome_reconciles_persisted_partial_report(
    managed, session, monkeypatch, phase, final_outcome
):
    _, project, scheduler, factory = managed
    tracker = TrackerRepository(session)
    proposal = proposal_for(managed, session)
    install_runner(managed, monkeypatch, proposal)
    definition = service._definition_by_key("codex")
    opened = WorkspaceRepository(session).start_session(
        runtime="codex", cwd=proposal["project_dir"], client_session_id="partial-native"
    )
    observed = []

    def runner(root, instructions, prompt, schema, **kwargs):
        result = definition.run_agent(root, instructions, prompt, schema, **kwargs)
        context = json.loads(prompt)
        if context["phase"] != phase:
            return result
        partial = SetupReport.model_validate_json(result.result["report_json"])
        if final_outcome == "conflicting_question":
            partial = SetupReport.model_validate(
                {
                    **partial.model_dump(mode="json"),
                    "status": "needs_input",
                    "question": {
                        "question_id": "preference",
                        "revision": 1,
                        "prompt": "Which preference?",
                        "workflow_keys": [KEY],
                        "response_channel": "managed_ui",
                    },
                }
            )
        with factory() as check:
            arguments = dict(
                project_id=project,
                request_id=context["request_id"],
                session_id=opened.data.id,
                client_session_id="partial-native",
                claim_token=context["claim_token"],
                expected_revision=context["expected_revision"],
                report=partial,
            )
            ctx = MCPContext(session=check, request_id="partial-report", extras={"surface": "mcp"})
            before = TrackerRepository(check).delivery_defaults(project_id=project)
            with pytest.raises(ConflictError):
                asyncio.run(
                    project_setup_report(
                        ProjectSetupReportInput(**{**arguments, "claim_token": "foreign"}),
                        ctx,
                        None,
                    )
                )
            assert TrackerRepository(check).delivery_defaults(project_id=project) == before
            envelope = asyncio.run(
                project_setup_report(ProjectSetupReportInput(**arguments), ctx, None)
            )
            assert envelope.project_id == project and envelope.data.status == partial.status
            observed.append(
                (context["request_id"], envelope.data.revision, len(envelope.data.reports))
            )
        if final_outcome != "failed":
            return result
        failed = SetupReport(
            status="failed",
            progress=dict(message="Final native failure", evidence_ref="fixture/final-failure"),
        )
        return AgentRunResult(
            0,
            thread_id="bounded-setup-native",
            result={"report_json": failed.model_dump_json(), "verification_status": None},
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=runner)
    )
    launch(managed, host_key="codex")
    scheduler.run()
    if phase == "verification":
        scheduler.run()
    session.expire_all()
    request_id, reported_revision, reports_count = observed[0]
    stored = session.get(AgentRequest, request_id).metadata_json["project_setup"]
    assert stored["outcomes"][KEY]["status"] == "success"
    if final_outcome == "conflicting_question":
        assert stored["status"] == "failed" and not scheduler.jobs
        assert "question" in stored["managed"]["diagnostic"]
        assert stored["question"]["answer_state"] == "pending"
        assert stored["revision"] == reported_revision
        if phase == "preparation":
            assert tracker.delivery_defaults(project_id=project) == {}
    elif final_outcome == "failed":
        assert stored["status"] == "failed" and not scheduler.jobs
        assert [item["status"] for item in stored["reports"]][-2:] == ["partial", "failed"]
        assert stored["revision"] == reported_revision + 1
        if phase == "preparation":
            assert tracker.delivery_defaults(project_id=project) == {}
    else:
        # Identical native output revalidates existing proof, without re-applying
        # workflow results, duplicating report history or advancing its revision.
        assert len(stored["reports"]) == reports_count
        assert stored["revision"] == reported_revision + 1  # successor linkage only
        assert len(scheduler.jobs) == 1


def test_adopted_delivery_hosts_are_independent_of_setup_host(managed, session, monkeypatch):
    _, project, scheduler, _ = managed
    tracker = TrackerRepository(session)
    proposal = proposal_for(managed, session)
    saved = {
        "main_agent": {"host_key": "claude-code"},
        "agents": {
            role: {"host_key": None, "source_ref": choice["source_ref"]}
            for role, choice in proposal["roles"].items()
        },
    }
    tracker.configure_delivery(project_id=project, delivery=saved)
    resolved = task_delivery.resolve_delivery_settings(
        session, project_id=project, project_dir=proposal["project_dir"]
    )
    assert all(choice["host_key"] == "codex" for choice in resolved["agents"].values())
    proposal = proposal_for(managed, session)
    calls = install_runner(managed, monkeypatch, proposal, setup_host="claude-code")
    launch(managed, host_key="claude-code")
    for _ in range(3):
        scheduler.run()
    session.expire_all()
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success"
    assert tracker.delivery_defaults(project_id=project) == saved
    assert calls[1]["adoption_readback"] == resolved
    assert not session.exec(select(RunPlan)).all() and not session.exec(select(TrackerTask)).all()


@pytest.mark.parametrize("role_host", ["omitted", "null"])
def test_proposal_cannot_replace_saved_inherited_delivery_host(
    managed, session, monkeypatch, role_host
):
    _, project, scheduler, _ = managed
    tracker = TrackerRepository(session)
    saved = {
        "host_key": "claude-code",
        "main_agent": {"host_key": "claude-code"},
        "agents": {
            "delivery_agent": {"name": "builder", "instructions": "Existing delivery"},
            "review_agent": {"name": "reviewer", "instructions": "Existing review"},
        },
    }
    if role_host == "null":
        for choice in saved["agents"].values():
            choice["host_key"] = None
    tracker.configure_delivery(project_id=project, delivery=saved)
    proposal = proposal_for(managed, session, host="gemini")
    before = task_delivery.resolve_delivery_settings(
        session, project_id=project, project_dir=proposal["project_dir"]
    )
    assert {choice["host_key"] for choice in before["agents"].values()} == {"claude-code"}
    calls = install_runner(managed, monkeypatch, proposal, setup_host="claude-code")
    launch(managed, host_key="claude-code")
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    after = tracker.delivery_defaults(project_id=project)
    assert after == saved
    assert (
        task_delivery.resolve_delivery_settings(
            session, project_id=project, project_dir=proposal["project_dir"]
        )
        == before
    )
    assert current.status == "failed" and not scheduler.jobs
    assert len(calls) == 1 and current.managed.phase == "preparation"
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0


@pytest.mark.parametrize("customized", [False, True])
def test_managed_adopts_then_verifies_once_without_workflow_or_task(
    managed, session, monkeypatch, customized
):
    _execution, project, scheduler, _ = managed
    tracker = TrackerRepository(session)
    original = {}
    if customized:
        original = dict(
            host_key="codex",
            main_agent={"host_key": "codex", "model": None},
            delivery_agent=dict(
                name="local-builder", instructions="old", model=None, provider=None, profile=None
            ),
            review_agent=dict(name="local-reviewer", instructions="old", model=None),
        )
        tracker.configure_delivery(project_id=project, delivery=original)
    frozen = tracker.create_task(project_id=project, key="unrelated", title="Frozen").data.task
    row = session.get(TrackerTask, frozen.id)
    row.metadata_json = {"delivery": {"status": "complete", "agents": {"saved": "snapshot"}}}
    session.add(row)
    session.commit()
    frozen_before = deepcopy(row.metadata_json)
    proposal = proposal_for(managed, session)
    calls = install_runner(managed, monkeypatch, proposal)
    first = launch(managed, host_key="codex")
    old_callback, old_options = scheduler.jobs[0]
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "queued" and current.managed.phase == "verification", (
        current.managed.diagnostic
    )
    assert current.request_id != first.attempt.request_id
    assert current.requested_scope == "ready_to_use"
    saved = tracker.delivery_defaults(project_id=project)
    for role, choice in proposal["roles"].items():
        assert saved["agents"][role]["source_ref"] == choice["source_ref"]
        assert "instructions" not in saved["agents"][role]
        for field, value in original.get(role, {}).items():
            if field != "instructions":
                assert saved["agents"][role][field] == value
    if customized:
        assert saved["main_agent"] == original["main_agent"]
    # A duplicate retired callback cannot repeat adoption or consume capacity.
    old_callback(**old_options["kwargs"])
    assert tracker.delivery_defaults(project_id=project) == saved
    assert len(scheduler.jobs) == 1
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.managed.phase == "completion" and current.status == "queued"
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "success"
    assert [call["phase"] for call in calls] == ["preparation", "verification", "completion"]
    assert len({call["claim_token"] for call in calls}) == 3
    assert not scheduler.jobs
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0
    assert not session.exec(select(RunPlan)).all()
    assert len(session.exec(select(TrackerTask)).all()) == 1
    assert session.get(TrackerTask, frozen.id).metadata_json == frozen_before


def test_supported_inline_host_preserves_preferences_without_codex_fallback(
    managed, session, monkeypatch
):
    _, project, scheduler, _ = managed
    tracker = TrackerRepository(session)
    tracker.configure_delivery(
        project_id=project,
        delivery={
            "host_key": "claude-code",
            "main_agent": {"host_key": "claude-code"},
            "delivery_agent": {"name": "local", "model": None, "provider": None},
        },
    )
    proposal = proposal_for(managed, session, host="claude-code")
    calls = install_runner(managed, monkeypatch, proposal)
    launch(managed, host_key="claude-code")
    for _ in range(3):
        assert scheduler.jobs, (
            ProjectSetupRepository(session).get(project).current_attempt.managed.diagnostic
        )
        scheduler.run()
    session.expire_all()
    saved = tracker.delivery_defaults(project_id=project)
    assert saved["agents"]["delivery_agent"] == dict(
        name="local", instructions="Check setup", model=None, provider=None
    )
    assert all("host_key" not in choice for choice in saved["agents"].values())
    assert all("source_ref" not in choice for choice in saved["agents"].values())
    assert all(call["adoption_readback"]["host_key"] == "claude-code" for call in calls[1:])
    assert all(
        choice["host_key"] == "claude-code"
        for choice in calls[1]["adoption_readback"]["agents"].values()
    )
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success"


@pytest.mark.parametrize("change", ["defaults", "source", "cancel"])
def test_changed_or_cancelled_preparation_cannot_adopt(managed, session, monkeypatch, change):
    execution, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)

    def mutate(context):
        if change == "source":
            path = Path(proposal["project_dir"]) / ".codex/agents/builder.toml"
            path.write_text(path.read_text() + "# changed\n")
        elif change == "defaults":
            with factory() as check:
                TrackerRepository(check).configure_delivery(
                    project_id=project, delivery={"host_key": None}
                )
        else:
            execution.cancel_managed(
                project_id=project,
                request_id=context["request_id"],
                session_factory=factory,
                scheduler=scheduler,
            )

    calls = install_runner(managed, monkeypatch, proposal, mutate=mutate)
    launch(managed, host_key="codex")
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project).current_attempt
    assert state.status == ("cancelled" if change == "cancel" else "failed")
    assert state.successor_request_id is None and not scheduler.jobs
    assert "agents" not in TrackerRepository(session).delivery_defaults(project_id=project)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "phase,change",
    [("verification", "defaults"), ("verification", "source"), ("completion", "source")],
)
def test_changed_adopted_basis_recovers_only_after_restoration(
    managed, session, monkeypatch, phase, change
):
    execution, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    calls = install_runner(managed, monkeypatch, proposal)
    launch(managed, host_key="codex")
    scheduler.run()
    if phase == "completion":
        scheduler.run()
    adopted = TrackerRepository(session).delivery_defaults(project_id=project)
    path = Path(proposal["project_dir"]) / ".codex/agents/builder.toml"
    original_source = path.read_text()
    if change == "source":
        path.write_text(original_source + "# changed\n")
    else:
        TrackerRepository(session).configure_delivery(
            project_id=project, delivery={"host_key": None}
        )
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project).current_attempt
    assert state.status == "failed" and "changed" in state.managed.diagnostic
    assert state.managed.preflight_failed and not scheduler.jobs
    assert len(calls) == (1 if phase == "verification" else 2)
    completed_verification = state.managed.verification_request_id
    assert "native_execution" not in session.get(AgentRequest, state.request_id).metadata_json
    if change == "source":
        path.write_text(original_source)
    else:
        TrackerRepository(session).configure_delivery(project_id=project, delivery=adopted)
    resumed = execution.continue_managed(
        project,
        state.request_id,
        expected_revision=state.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    assert resumed.attempt.managed.resume_thread_id == "bounded-setup-native"
    assert resumed.attempt.managed.verification_request_id == completed_verification
    for _ in range(2 if phase == "verification" else 1):
        scheduler.run()
    session.expire_all()
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success"
    assert [call["phase"] for call in calls] == ["preparation", "verification", "completion"]
    assert TrackerRepository(session).delivery_defaults(project_id=project) == adopted
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0


@pytest.mark.parametrize("outcome", [None, "failed", "blocked"])
def test_unverified_bounded_check_stays_partial_and_is_not_repeated_automatically(
    managed, session, monkeypatch, outcome
):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session)
    calls = install_runner(managed, monkeypatch, proposal, verification=outcome)
    launch(managed, host_key="codex")
    scheduler.run()
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project).current_attempt
    assert state.status == "partial" and state.outcomes[KEY].activation_status == "pending"
    assert not scheduler.jobs and len(calls) == 2


def test_proposal_report_does_not_grant_adoption_authority(managed, session):
    execution, project, _, factory = managed
    opened = launch(managed, host_key="codex")
    proposal = proposal_for(managed, session)
    before = TrackerRepository(session).delivery_defaults(project_id=project)
    with factory() as check, pytest.raises(ConflictError):
        execution._adopt_managed(check, project, opened.attempt.request_id, "foreign-claim")
    assert TrackerRepository(session).delivery_defaults(project_id=project) == before
    assert (
        SetupReport.model_validate(
            dict(
                status="partial",
                progress=dict(message="Prepared", evidence_ref="fixture/prepared"),
                adoption_proposal=proposal,
            )
        ).adoption_proposal.project_id
        == project
    )


def test_interrupted_completion_resumes_without_repeating_native_check(
    managed, session, monkeypatch
):
    execution, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    calls = install_runner(managed, monkeypatch, proposal)
    launch(managed, host_key="codex")
    scheduler.run()
    scheduler.run()
    session.expire_all()
    completing = ProjectSetupRepository(session).get(project).current_attempt
    assert completing.managed.phase == "completion"
    verification_request = completing.managed.verification_request_id
    saved = TrackerRepository(session).delivery_defaults(project_id=project)
    execution.cancel_managed(
        project_id=project,
        request_id=completing.request_id,
        session_factory=factory,
        scheduler=scheduler,
    )
    session.expire_all()
    stopped = ProjectSetupRepository(session).get(project).current_attempt
    continued = execution.continue_managed(
        project,
        stopped.request_id,
        expected_revision=stopped.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    assert continued.attempt.managed.verification_request_id == verification_request
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "success", current.managed.diagnostic
    assert [call["phase"] for call in calls] == ["preparation", "verification", "completion"]
    assert TrackerRepository(session).delivery_defaults(project_id=project) == saved
    assert (
        session.get(AgentRequest, verification_request).metadata_json["project_setup"]["managed"][
            "cancel_requested"
        ]
        is False
    )


@pytest.mark.parametrize("verification_status", [None, "failed", "blocked"])
def test_managed_reuses_current_evidence_without_adoption_or_bounded_recheck(
    managed, session, monkeypatch, verification_status
):
    from tests.integration.test_repositories.test_project_setup_activation import activation_result

    _, project, scheduler, factory = managed
    result, evidence = activation_result((project, None, ProjectSetupRepository(session)), session)
    definition = service._definition_by_key("codex")
    calls = []

    def runner(root, instructions, prompt, schema, *, on_session, **kw):
        context = json.loads(prompt)
        calls.append(context)
        on_session("reuse-current-native")
        result.attempt_ref = context["attempt_ref"]
        report = SetupReport(
            status="success",
            progress=dict(message="Reused current proof", evidence_ref="fixture/reuse"),
            workflow_results=[dict(workflow_key=KEY, expected_revision=0, result=result)],
        )
        native_result = {
            "report_json": report.model_dump_json(),
            "verification_status": verification_status,
        }
        return AgentRunResult(0, thread_id="reuse-current-native", result=native_result)

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=runner)
    )
    launch(managed, host_key="codex")
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project).current_attempt
    assert state.status == ("failed" if verification_status else "success")
    assert state.adoption_proposal is None
    assert len(calls) == 1 and not scheduler.jobs
    with factory() as check:
        assert check.get(AgentRequest, evidence.id).metadata_json == evidence.metadata_json
    assert not session.exec(select(RunPlan)).all()
    assert not session.exec(select(TrackerTask)).all()


def test_adoption_and_receipt_roll_back_together(managed, session, monkeypatch):
    execution, project, scheduler, _ = managed
    proposal = proposal_for(managed, session)
    install_runner(managed, monkeypatch, proposal)
    original_save = execution._save

    def save(check, row, attempt):
        if attempt.managed.phase == "verification":
            raise RuntimeError("fixture receipt persistence failure")
        return original_save(check, row, attempt)

    monkeypatch.setattr(execution, "_save", save)
    launch(managed, host_key="codex")
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project).current_attempt
    assert state.status == "failed" and "persistence failure" in state.managed.diagnostic
    assert TrackerRepository(session).delivery_defaults(project_id=project) == {}
    assert not scheduler.jobs
