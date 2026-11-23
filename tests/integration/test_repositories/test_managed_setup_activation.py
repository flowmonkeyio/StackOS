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
from tests.helpers.native_task_routes import native_route_defaults  # noqa: F401
from tests.integration.test_repositories.test_managed_setup import (
    launch,
    managed,  # noqa: F401
    native_lifetime,  # noqa: F401
)
from tests.integration.test_repositories.test_project_setup import verified_result

KEY = "engineering.tracked-delivery"


def test_managed_setup_selects_registered_antigravity(managed, session, monkeypatch):
    from stackos.host_mcp.adapters import antigravity_execution as native
    from stackos.host_mcp.result import validate_agent_result

    _execution, _project, scheduler, _factory = managed
    calls = []

    def execute(command, root, stdin, schema, host, decode, env, /, **options):
        assert env["STACKOS_WORKSPACE_ROOT"] == str(root.resolve())
        assert host == "Antigravity" and options["process_control"] is not None
        assert json.loads(command[command.index("--json-schema") + 1]) == RESULT_SCHEMA
        assert "Consume its complete project_setup_method" in command[command.index("--print") + 1]
        payload = {
            "verification_status": None,
            "report_json": SetupReport(
                status="partial",
                progress={"message": "Inspected native setup", "evidence_ref": "fixture/agy"},
                recovery_condition="Select remaining inputs",
            ).model_dump_json(),
        }
        calls.append(str(root))
        envelope = {
            "event": "result",
            "result": {
                "conversation_id": "agy-setup",
                "status": "SUCCESS",
                "structured_output": payload,
            },
        }
        return validate_agent_result(decode(json.dumps(envelope)), schema, host)

    monkeypatch.setattr(native, "resolve_antigravity_bin", lambda: "/fixture/agy")
    monkeypatch.setattr(native, "execute_json_agent", execute)
    opened = launch(managed, host_key="antigravity", model="gemini-3.8-flash-high")
    scheduler.run()
    session.expire_all()
    row = session.get(AgentRequest, opened.attempt.request_id)
    evidence = row.metadata_json["native_execution"]
    assert evidence["snapshot"]["host_key"] == "antigravity"
    assert evidence["execution"]["thread_id"] == "agy-setup"
    assert row.metadata_json["project_setup"]["status"] == "partial" and len(calls) == 1


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
        instructions = f"Check setup as {name}"
        choice = dict(host_key=host, name=name, provenance_ref="fixture/reviewed-adaptation")
        if host == "codex":
            folder = root / ".codex" / "agents"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{name}.toml"
            path.write_text(
                f'name="{name}"\ndescription="Fixture"\ndeveloper_instructions="{instructions}"\n'
            )
            ref = dict(host_key=host, native_name=name, source_path=f".codex/agents/{name}.toml")
            source = ProjectAgentService(root).get(ref)
            choice.update(source_ref=ref, expected_digest=source.digest)
        else:
            choice.update(instructions=instructions, expected_digest=_digest(instructions))
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
    managed,
    monkeypatch,
    proposal,
    *,
    mutate=None,
    verification="passed",
    setup_host=None,
    seed_role=True,
    fresh_role=False,
    role_status="success",
    partial_receipt=False,
):
    _execution, project, _, factory = managed
    definition = service._definition_by_key(
        setup_host or proposal["roles"]["delivery_agent"]["host_key"]
    )
    calls = []
    # Seed completed reviewer evidence before setup. The injected coordinator
    # cannot turn its own generic success into specialist capability proof.
    from tests.integration.test_repositories.test_project_setup_activation import reviewer_check

    with factory() as check:
        repo = ProjectSetupRepository(check)
        selection = proposal["roles"]["review_agent"]
        selected_host = service._definition_by_key(selection["host_key"])
        agent = (
            task_delivery.resolve_delivery_settings(
                check,
                project_id=project,
                project_dir=proposal["project_dir"],
                agents={
                    "review_agent": {
                        key: selection[key]
                        for key in (
                            "name",
                            "host_key",
                            "source_ref",
                            "instructions",
                            "execution_requirements",
                        )
                        if key in selection
                    }
                },
                role_keys={"review_agent"},
            )["agents"]["review_agent"]
            if seed_role and selected_host and selected_host.run_agent
            else None
        )
        existing_role_check = (
            reviewer_check(
                (project, None, repo), check, verified_result(repo, project), agent=agent
            )
            if agent
            else None
        )

    def runner(root, instructions, prompt, schema, *, on_session=None, resume_thread_id=None, **kw):
        context = json.loads(prompt)
        calls.append(context)
        is_role = context["phase"] == "role_verification"
        if on_session:
            on_session("bounded-review-native" if is_role else "bounded-setup-native")
        assert context["requested_scope"] == "ready_to_use"
        if not is_role and context["predecessor_request_id"]:
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
        if is_role:
            assert resume_thread_id == (
                "bounded-review-native"
                if any(call["phase"] == "role_verification" for call in calls[:-1])
                else None
            )
            selected = context["role_check"]
            with factory() as check:
                row = check.get(AgentRequest, context["request_id"])
                frozen = row.metadata_json["native_execution"]["snapshot"]["context"]["role_check"]
            assert instructions == frozen["agent_snapshot"]["instructions"]
            assert kw.get("native_settings") == frozen["agent_snapshot"].get("native_settings")
            request = selected["request"]
            if mutate:
                mutate(context)
            return AgentRunResult(
                0,
                thread_id="bounded-review-native",
                result={
                    "status": role_status(context) if callable(role_status) else role_status,
                    "summary": "Synthetic bounded reviewer observation",
                    "outputs": json.dumps(
                        {
                            "setup_activation_checks": [
                                {
                                    "claims": request["claims"],
                                    "status": "passed",
                                    "environment_ref": request["environment_ref"],
                                    "evidence_ref": request["evidence_ref"],
                                }
                            ]
                        }
                    ),
                    "followups": [],
                },
            )
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
                if fresh_role:
                    if partial_receipt:
                        partial = verified_result(repo, project)
                        partial.attempt_ref = context["attempt_ref"]
                        partial.status = "unfinished"
                        partial.verification = None
                        report["workflow_results"] = [
                            dict(
                                workflow_key=KEY,
                                expected_revision=context["observed_receipt_revisions"][KEY],
                                result=partial.model_dump(mode="json"),
                            )
                        ]
                    report["role_verification_request"] = dict(
                        role="review_agent",
                        claims=["reviewer_verification"],
                        instructions="Run the project-selected harmless scratch/tool/SQLite "
                        "fixture check and record its real command and exit.",
                        environment_ref="fixture/native",
                        evidence_ref="fixture/fresh-reviewer-check",
                    )
                elif existing_role_check and actual["agents"]["review_agent"].get(
                    "execution_requirements", {}
                ).get("filesystem_write"):
                    partial = verified_result(repo, project)
                    partial.attempt_ref = context["attempt_ref"]
                    partial.status = "unfinished"
                    role_check = deepcopy(existing_role_check)
                    role_check["basis"] = repo.activation_basis(
                        project, partial.verification, environment_ref="fixture/native"
                    )
                    partial.verification.activation_proof = SetupActivationEvidence(
                        method_ref="fixture/method",
                        status="pending",
                        required_claims=[
                            "saved_defaults",
                            "native_consumption",
                            "reviewer_verification",
                        ],
                        checks=[role_check],
                    )
                    report["workflow_results"] = [
                        dict(
                            workflow_key=KEY,
                            expected_revision=context["observed_receipt_revisions"][KEY],
                            result=partial.model_dump(mode="json"),
                        )
                    ]
            else:
                assert context["phase"] == "completion"
                result = verified_result(repo, project)
                result.attempt_ref = context["attempt_ref"]
                role_check = deepcopy(existing_role_check)
                if fresh_role:
                    observed = context["completed_role_verifications"][0]
                    role_check = dict(
                        claims=["reviewer_verification"],
                        origin="executed",
                        reference=observed["reference"],
                        execution=observed["execution"],
                        evidence_ref=observed["request"]["evidence_ref"],
                    )
                assert role_check is not None
                role_check["basis"] = repo.activation_basis(
                    project, result.verification, environment_ref="fixture/native"
                )
                result.verification.activation_proof = SetupActivationEvidence(
                    method_ref="fixture/method",
                    status="verified",
                    required_claims=[
                        "saved_defaults",
                        "native_consumption",
                        "reviewer_verification",
                    ],
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
                        ),
                        role_check,
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


def test_managed_setup_saves_required_reviewer_capability_atomically(managed, session, monkeypatch):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(managed, monkeypatch, proposal)
    launch(managed, host_key="codex")
    for _ in range(3):
        scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project)
    assert state.current_attempt.status == "success"
    assert TrackerRepository(session).delivery_defaults(project_id=project)["agents"][
        "review_agent"
    ]["execution_requirements"] == {"filesystem_write": True}
    assert [call["phase"] for call in calls] == ["preparation", "verification", "completion"]
    assert calls[1]["activation_role_bindings"]["review_agent"]["role"] == "review_agent"


def test_managed_incompatible_reviewer_requirement_rolls_back_adoption(
    managed, session, monkeypatch
):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session)
    reviewer = proposal["roles"]["review_agent"]
    path = Path(proposal["project_dir"]) / reviewer["source_ref"]["source_path"]
    path.write_text(path.read_text() + 'sandbox_mode="read-only"\n')
    reviewer["expected_digest"] = (
        ProjectAgentService(Path(proposal["project_dir"])).get(reviewer["source_ref"]).digest
    )
    reviewer["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(managed, monkeypatch, proposal, seed_role=False)
    launch(managed, host_key="codex")
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project).current_attempt
    assert state.status == "failed" and "filesystem_write" in state.managed.diagnostic
    assert TrackerRepository(session).delivery_defaults(project_id=project) == {}
    assert [call["phase"] for call in calls] == ["preparation"]


@pytest.mark.parametrize("host", ["codex", "claude-code"])
@pytest.mark.parametrize("partial_receipt", [False, True])
def test_managed_fresh_reviewer_native_proof_needs_no_preseeded_task(
    managed, session, monkeypatch, host, partial_receipt
):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session, host=host)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        seed_role=False,
        fresh_role=True,
        partial_receipt=partial_receipt,
    )
    native_calls = []
    run_agent = service.run_agent

    def capture(**kwargs):
        if json.loads(kwargs["prompt"])["phase"] == "role_verification":
            native_calls.append(kwargs)
        return run_agent(**kwargs)

    monkeypatch.setattr(service, "run_agent", capture)
    launch(managed, host_key="codex")
    for _ in range(2):
        scheduler.run()
    if partial_receipt:
        session.expire_all()
        receipt = ProjectSetupRepository(session)._row(project, KEY)
        assert receipt.latest_result_json["status"] == "unfinished"
        assert receipt.latest_result_json["verification"] is None
        assert ProjectSetupRepository(session).get(project).current_attempt.status != "success"
    role_callback, role_options = scheduler.jobs[0]
    scheduler.run()
    session.expire_all()
    completing = ProjectSetupRepository(session).get(project).current_attempt
    assert completing.status == "queued", completing.managed.diagnostic
    assert completing.managed.phase == "completion", completing.managed.diagnostic
    role_request_id = calls[-1]["request_id"]
    assert completing.managed.role_verification_request_ids == [role_request_id]
    assert len(scheduler.jobs) == 1
    retired_before_replay = deepcopy(session.get(AgentRequest, role_request_id).metadata_json)
    role_callback(**role_options["kwargs"])
    session.expire_all()
    assert session.get(AgentRequest, role_request_id).metadata_json == retired_before_replay
    assert len(scheduler.jobs) == 1
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project)
    assert state.current_attempt.status == "success", state.current_attempt.managed.diagnostic
    assert [call["phase"] for call in calls] == [
        "preparation",
        "verification",
        "role_verification",
        "completion",
    ]
    assert not session.exec(select(TrackerTask)).all()
    assert not session.exec(select(RunPlan)).all()
    assert state.workflows[0].activation_status == "verified"
    assert calls[-1]["completed_role_verifications"][0]["reference"]["kind"] == "native_request"
    assert calls[-1]["completed_role_verifications"][0]["execution"]["role"] == "review_agent"
    assert len(native_calls) == 1
    call = native_calls[0]
    prompt = json.loads(call["prompt"])
    retired = session.get(AgentRequest, prompt["request_id"])
    snapshot = retired.metadata_json["native_execution"]["snapshot"]
    frozen = snapshot["context"]
    reviewer = frozen["role_check"]["agent_snapshot"]
    delivery = frozen["adoption_readback"]["agents"]["delivery_agent"]
    assert reviewer["instructions"] != delivery["instructions"]
    assert call["instructions"] == snapshot["instructions"] == reviewer["instructions"]
    assert reviewer["instructions"] not in call["prompt"]
    assert delivery["instructions"] not in call["prompt"]
    assert set(prompt) == {
        "project_id",
        "request_id",
        "attempt_ref",
        "requested_scope",
        "phase",
        "project_dir",
        "role_check",
        "assignment",
    }
    assert prompt["role_check"] == {
        key: frozen["role_check"][key] for key in ("workflow_key", "request", "execution")
    }
    assert prompt["assignment"] == frozen["assignment"]
    assert "Do not change" in prompt["assignment"] and "proof is blocked" in prompt["assignment"]
    assert frozen == call["request_execution"].snapshot
    assert frozen["completed_verification"] and frozen["adopted_delivery_digest"]
    assert frozen["adoption_readback"]["agents"]["review_agent"] == reviewer


@pytest.mark.parametrize("thread_projection_gap", [False, True])
def test_failed_native_role_check_explicitly_resumes_same_reviewer_and_coordinator(
    managed, session, monkeypatch, thread_projection_gap
):
    from stackos.project_setup_execution import continue_managed

    _, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    verdict = {"value": "failed"}
    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        seed_role=False,
        fresh_role=True,
        role_status=lambda _: verdict["value"],
    )
    launch(managed, host_key="codex")
    for _ in range(3):
        scheduler.run()
    session.expire_all()
    failed = ProjectSetupRepository(session).get(project).current_attempt
    assert failed.status == "failed" and not scheduler.jobs
    assert failed.thread_id == "bounded-review-native"
    if thread_projection_gap:
        row = session.get(AgentRequest, failed.request_id)
        metadata = deepcopy(row.metadata_json)
        metadata["project_setup"]["thread_id"] = None
        row.metadata_json = metadata
        session.add(row)
        session.commit()
    verdict["value"] = "success"
    continue_managed(
        project,
        failed.request_id,
        expected_revision=failed.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    scheduler.run()
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "success", current.managed.diagnostic
    assert current.thread_id == "bounded-setup-native"
    retired = session.get(AgentRequest, failed.request_id)
    assert retired.metadata_json["native_execution"]["result"]["status"] == "failed"
    assert [call["phase"] for call in calls] == [
        "preparation",
        "verification",
        "role_verification",
        "role_verification",
        "completion",
    ]


@pytest.mark.parametrize(
    "rejection", ["missing-observation", "wrong-environment", "wrong-settings"]
)
@pytest.mark.parametrize("partial_receipt", [False, True])
def test_rejected_native_evidence_recovers_before_completion_promotion(
    managed, session, monkeypatch, rejection, partial_receipt
):
    from stackos.project_setup_execution import continue_managed

    _, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        seed_role=False,
        fresh_role=True,
        partial_receipt=partial_receipt,
    )
    definition = service._definition_by_key("codex")
    reject = {"value": True}

    def runner(*args, **kwargs):
        result = definition.run_agent(*args, **kwargs)
        context = json.loads(args[2])
        if context["phase"] == "role_verification" and reject["value"]:
            if rejection == "missing-observation":
                result.result["outputs"] = "{}"
            elif rejection == "wrong-environment":
                outputs = json.loads(result.result["outputs"])
                outputs["setup_activation_checks"][0]["environment_ref"] = "fixture/foreign-env"
                result.result["outputs"] = json.dumps(outputs)
            else:
                with factory() as check:
                    row = check.get(AgentRequest, context["request_id"])
                    metadata = deepcopy(row.metadata_json)
                    metadata["native_execution"]["snapshot"]["model"] = "foreign-model"
                    row.metadata_json = metadata
                    check.add(row)
                    check.commit()
        return result

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=runner)
    )
    launch(managed, host_key="codex")
    for _ in range(3):
        scheduler.run()
    session.expire_all()
    failed = ProjectSetupRepository(session).get(project).current_attempt
    assert failed.status == "failed" and not scheduler.jobs
    assert failed.managed.phase == "role_verification"
    assert failed.managed.role_check is not None
    assert failed.request_id not in failed.managed.role_verification_request_ids
    assert failed.thread_id == "bounded-review-native"
    frozen = failed.managed.role_check.model_dump(mode="json")
    reject["value"] = False
    continued = continue_managed(
        project,
        failed.request_id,
        expected_revision=failed.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    assert continued.attempt.managed.role_check.model_dump(mode="json") == frozen
    assert continued.attempt.managed.resume_thread_id == failed.thread_id
    scheduler.run()
    session.expire_all()
    retried = ProjectSetupRepository(session).get(project).current_attempt
    assert scheduler.jobs, retried.managed.diagnostic
    assert len(retried.reports) == len(failed.reports) + 1
    assert retried.reports[-1].report_digest == failed.reports[-1].report_digest
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "success", current.managed.diagnostic
    assert current.thread_id == "bounded-setup-native"
    assert failed.request_id not in current.managed.role_verification_request_ids
    assert current.managed.role_verification_request_ids == [continued.attempt.request_id]
    retained = session.get(AgentRequest, failed.request_id)
    assert retained.metadata_json["project_setup"]["status"] == "failed"
    assert retained.metadata_json["native_execution"]["result"]["status"] == "success"
    assert [call["phase"] for call in calls] == [
        "preparation",
        "verification",
        "role_verification",
        "role_verification",
        "completion",
    ]
    assert (
        calls[2]["role_check"]
        == calls[3]["role_check"]
        == {key: value for key, value in frozen.items() if key != "agent_snapshot"}
    )
    assert not session.exec(select(TrackerTask)).all()


@pytest.mark.parametrize("completion_status", ["partial", "success"])
def test_partial_receipt_role_success_still_needs_complete_aggregate_proof(
    managed, session, monkeypatch, completion_status
):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        seed_role=False,
        fresh_role=True,
        partial_receipt=True,
    )
    definition = service._definition_by_key("codex")

    def runner(*args, **kwargs):
        result = definition.run_agent(*args, **kwargs)
        if json.loads(args[2])["phase"] == "completion":
            report = json.loads(result.result["report_json"])
            report["status"] = completion_status
            report["workflow_results"] = []
            result.result["report_json"] = json.dumps(report)
        return result

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=runner)
    )
    launch(managed, host_key="codex")
    for _ in range(4):
        scheduler.run()
    session.expire_all()
    repo = ProjectSetupRepository(session)
    state = repo.get(project)
    current = state.current_attempt
    assert current.status == ("partial" if completion_status == "partial" else "failed")
    assert current.managed.role_verification_request_ids == [calls[2]["request_id"]]
    assert state.workflows[0].activation_status != "verified"
    assert repo._row(project, KEY).latest_result_json["verification"] is None
    assert [call["phase"] for call in calls] == [
        "preparation",
        "verification",
        "role_verification",
        "completion",
    ]
    assert not scheduler.jobs


def test_cancelled_never_started_role_check_recovers_only_its_first_admission(
    managed, session, monkeypatch
):
    from stackos.project_setup_execution import cancel_managed, continue_managed

    _, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(managed, monkeypatch, proposal, seed_role=False, fresh_role=True)
    launch(managed, host_key="codex")
    scheduler.run()
    scheduler.run()
    session.expire_all()
    pending = ProjectSetupRepository(session).get(project).current_attempt
    assert pending.managed.phase == "role_verification" and not pending.managed.callback_started
    stopped = cancel_managed(
        project, pending.request_id, session_factory=factory, scheduler=scheduler
    )
    assert stopped.status == "cancelled" and not scheduler.jobs
    continued = continue_managed(
        project,
        stopped.request_id,
        expected_revision=stopped.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    assert continued.attempt.partial_changes_possible == stopped.partial_changes_possible
    scheduler.run()
    scheduler.run()
    session.expire_all()
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success"
    assert [call["phase"] for call in calls].count("role_verification") == 1


@pytest.mark.parametrize(
    "failure", ["wrong-role", "stale-source", "failed", "cancelled", "missing-session"]
)
def test_fresh_native_role_proof_rejects_invalid_outcomes(managed, session, monkeypatch, failure):
    from stackos.project_setup_execution import cancel_managed

    _, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}

    def mutate(context):
        if failure == "cancelled" and context["phase"] == "role_verification":
            cancel_managed(
                project, context["request_id"], session_factory=factory, scheduler=scheduler
            )

    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        seed_role=False,
        fresh_role=True,
        role_status="failed" if failure == "failed" else "success",
        mutate=mutate,
    )
    launch(managed, host_key="codex")
    for _ in range(3):
        scheduler.run()
    session.expire_all()
    role_attempt = ProjectSetupRepository(session).get(project).current_attempt
    if failure in {"wrong-role", "stale-source", "missing-session"}:
        if failure in {"wrong-role", "missing-session"}:
            row = session.get(AgentRequest, role_attempt.predecessor_request_id)
            metadata = deepcopy(row.metadata_json)
            if failure == "wrong-role":
                metadata["native_execution"]["snapshot"]["context"]["role_check"]["request"][
                    "role"
                ] = "delivery_agent"
            else:
                metadata["native_execution"]["execution"]["thread_id"] = None
            row.metadata_json = metadata
            session.add(row)
            session.commit()
        else:
            path = (
                Path(proposal["project_dir"])
                / proposal["roles"]["review_agent"]["source_ref"]["source_path"]
            )
            path.write_text(path.read_text() + "\n# changed relevant source\n")
        scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project)
    assert state.current_attempt.status == ("cancelled" if failure == "cancelled" else "failed")
    assert state.workflows[0].activation_status != "verified"
    assert not scheduler.jobs
    assert not session.exec(select(TrackerTask)).all()
    assert [call["phase"] for call in calls].count("role_verification") == 1


def test_managed_explicit_inspection_requirement_precedes_source_preflight(
    managed, session, monkeypatch
):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session)
    reviewer = proposal["roles"]["review_agent"]
    tracker = TrackerRepository(session)
    tracker.configure_delivery(
        project_id=project,
        delivery={
            "host_key": "codex",
            "agents": {
                "review_agent": {
                    "source_ref": reviewer["source_ref"],
                    "execution_requirements": {"filesystem_write": True},
                }
            },
            "main_agent": {"host_key": "codex"},
        },
    )
    proposal["expected_delivery_digest"] = _digest(tracker.delivery_defaults(project_id=project))
    path = Path(proposal["project_dir"]) / reviewer["source_ref"]["source_path"]
    path.write_text(path.read_text() + 'sandbox_mode="read-only"\n')
    reviewer["expected_digest"] = (
        ProjectAgentService(Path(proposal["project_dir"])).get(reviewer["source_ref"]).digest
    )
    reviewer["execution_requirements"] = {"filesystem_write": False}
    install_runner(managed, monkeypatch, proposal, seed_role=False)
    launch(managed, host_key="codex")
    scheduler.run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(project).current_attempt
    assert state.managed.phase == "verification", state.managed.diagnostic
    assert tracker.delivery_defaults(project_id=project)["agents"]["review_agent"][
        "execution_requirements"
    ] == {"filesystem_write": False}
    assert tracker.delivery_defaults(project_id=project)["main_agent"] == {"host_key": "codex"}


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


@pytest.mark.parametrize(
    "final_outcome",
    ["shorter", "identical", "failed", "blocked", "nonzero", "changed_defaults", "changed_native"],
)
def test_native_final_confirmation_revalidates_accepted_success(
    managed, session, monkeypatch, final_outcome
):
    _, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    calls = install_runner(managed, monkeypatch, proposal)
    definition = service._definition_by_key("codex")
    opened = WorkspaceRepository(session).start_session(
        runtime="codex", cwd=proposal["project_dir"], client_session_id="completion-native"
    )
    observed = []

    def runner(root, instructions, prompt, schema, **kwargs):
        result = definition.run_agent(root, instructions, prompt, schema, **kwargs)
        context = json.loads(prompt)
        if context["phase"] != "completion":
            return result
        full = SetupReport.model_validate_json(result.result["report_json"])
        full.workflow_keys = [KEY]
        with factory() as check:
            envelope = asyncio.run(
                project_setup_report(
                    ProjectSetupReportInput(
                        project_id=project,
                        request_id=context["request_id"],
                        session_id=opened.data.id,
                        client_session_id="completion-native",
                        claim_token=context["claim_token"],
                        expected_revision=context["expected_revision"],
                        report=full,
                    ),
                    MCPContext(session=check, request_id="full-success", extras={"surface": "mcp"}),
                    None,
                )
            )
            assert envelope.project_id == project and envelope.data.status == "success"
            receipt = ProjectSetupRepository(check)._row(project, KEY)
            observed.append(
                (
                    envelope.data.model_dump(mode="json"),
                    receipt.revision,
                    deepcopy(receipt.successful_json),
                )
            )
            if final_outcome == "changed_defaults":
                tracker = TrackerRepository(check)
                tracker.configure_delivery(
                    project_id=project,
                    delivery={**tracker.delivery_defaults(project_id=project), "model": "changed"},
                )
            elif final_outcome == "changed_native":
                native = check.get(
                    AgentRequest, context["completed_verification"]["reference"]["request_id"]
                )
                metadata = deepcopy(native.metadata_json)
                metadata["native_execution"]["result"]["verification_status"] = "blocked"
                native.metadata_json = metadata
                check.add(native)
                check.commit()
        final = (
            full
            if final_outcome == "identical"
            else SetupReport(
                status="failed" if final_outcome == "failed" else "success",
                workflow_keys=[KEY],
                progress=dict(
                    message="Final confirmation", evidence_ref="fixture/final-confirmation"
                ),
            )
        )
        return AgentRunResult(
            1 if final_outcome == "nonzero" else 0,
            thread_id="bounded-setup-native",
            result={
                "report_json": final.model_dump_json(),
                "verification_status": "blocked" if final_outcome == "blocked" else None,
            },
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=runner)
    )
    launch(managed, host_key="codex")
    for _ in range(3):
        scheduler.run()
    session.expire_all()
    assert [call["phase"] for call in calls] == ["preparation", "verification", "completion"]
    assert not scheduler.jobs
    accepted, receipt_revision, successful = observed[0]
    row = session.get(AgentRequest, accepted["request_id"])
    stored = row.metadata_json["project_setup"]
    receipt = ProjectSetupRepository(session)._row(project, KEY)
    # Final confirmation never writes another workflow receipt or native check.
    assert receipt.revision == receipt_revision and receipt.successful_json == successful
    assert stored["outcomes"] == accepted["outcomes"]
    assert not stored["managed"]["slot_reserved"] and row.claim_token_hash is None
    assert row.metadata_json["native_execution"]["finished_at"]
    assert len(session.exec(select(AgentRequest)).all()) == 3
    if final_outcome in {"shorter", "identical"}:
        assert stored["status"] == "success", stored["managed"]["diagnostic"]
        assert row.status == "resolved"
        assert stored["revision"] == accepted["revision"]
        assert stored["reports"] == accepted["reports"]
        assert stored["progress"] == accepted["progress"]
    else:
        assert stored["status"] == "failed" and row.status == "failed"
        if final_outcome == "changed_defaults":
            assert "activation" in stored["managed"]["diagnostic"]
        elif final_outcome == "changed_native":
            assert "native" in stored["managed"]["diagnostic"]


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
    assert not session.exec(select(RunPlan)).all()
    assert [task.key for task in session.exec(select(TrackerTask)).all()] == ["setup-proof"]


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
    capacity = task_delivery.delivery_capacity(session, scheduler, project)["projects"]
    assert len(capacity) == 1 and capacity[0]["project_id"] == project
    assert capacity[0]["running_count"] == 0


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
    capacity = task_delivery.delivery_capacity(session, scheduler, project)["projects"]
    assert len(capacity) == 1 and capacity[0]["project_id"] == project
    assert capacity[0]["running_count"] == 0
    assert not session.exec(select(RunPlan)).all()
    assert len(session.exec(select(TrackerTask)).all()) == 2  # existing task and reviewer proof
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
        name="local", instructions="Check setup as builder", model=None, provider=None
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
    capacity = task_delivery.delivery_capacity(session, scheduler, project)["projects"]
    assert len(capacity) == 1 and capacity[0]["project_id"] == project
    assert capacity[0]["running_count"] == 0


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


@pytest.mark.parametrize("fresh_role", [False, True])
def test_interrupted_completion_resumes_without_repeating_native_check(
    managed, session, monkeypatch, fresh_role
):
    execution, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        seed_role=not fresh_role,
        fresh_role=fresh_role,
        partial_receipt=fresh_role,
    )
    launch(managed, host_key="codex")
    scheduler.run()
    scheduler.run()
    if fresh_role:
        scheduler.run()
    session.expire_all()
    completing = ProjectSetupRepository(session).get(project).current_attempt
    assert completing.managed.phase == "completion"
    verification_request = completing.managed.verification_request_id
    role_requests = completing.managed.role_verification_request_ids[:]
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
    assert continued.attempt.managed.role_verification_request_ids == role_requests
    assert continued.attempt.managed.resume_thread_id == "bounded-setup-native"
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "success", current.managed.diagnostic
    assert [call["phase"] for call in calls] == [
        "preparation",
        "verification",
        *(["role_verification"] if fresh_role else []),
        "completion",
    ]
    assert current.managed.role_verification_request_ids == role_requests
    assert TrackerRepository(session).delivery_defaults(project_id=project) == saved
    assert (
        session.get(AgentRequest, verification_request).metadata_json["project_setup"]["managed"][
            "cancel_requested"
        ]
        is False
    )


@pytest.mark.parametrize("failure", ["role-live", "role-cancelled", "coordinator-missing"])
def test_completion_recovery_requires_retired_role_and_exact_coordinator(
    managed, session, monkeypatch, failure
):
    execution, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        seed_role=False,
        fresh_role=True,
        partial_receipt=True,
    )
    launch(managed, host_key="codex")
    for _ in range(3):
        scheduler.run()
    session.expire_all()
    completing = ProjectSetupRepository(session).get(project).current_attempt
    execution.cancel_managed(
        project, completing.request_id, session_factory=factory, scheduler=scheduler
    )
    session.expire_all()
    stopped = ProjectSetupRepository(session).get(project).current_attempt
    target = (
        stopped.managed.verification_request_id
        if failure == "coordinator-missing"
        else stopped.managed.role_verification_request_ids[0]
    )
    row = session.get(AgentRequest, target)
    metadata = deepcopy(row.metadata_json)
    native = metadata["native_execution"]
    if failure == "role-live":
        native["finished_at"] = None
    elif failure == "role-cancelled":
        native["cancelled"] = True
    else:
        native["execution"]["thread_id"] = None
    row.metadata_json = metadata
    session.add(row)
    session.commit()
    before = len(session.exec(select(AgentRequest)).all())
    with pytest.raises(ConflictError, match=r"role verification|session identity"):
        execution.continue_managed(
            project,
            stopped.request_id,
            expected_revision=stopped.revision,
            session_factory=factory,
            scheduler=scheduler,
        )
    session.expire_all()
    assert len(session.exec(select(AgentRequest)).all()) == before
    assert ProjectSetupRepository(session).get(project).current_attempt.successor_request_id is None
    assert len(calls) == 3 and not scheduler.jobs


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
    assert [task.key for task in session.exec(select(TrackerTask)).all()] == ["setup-proof"]


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
