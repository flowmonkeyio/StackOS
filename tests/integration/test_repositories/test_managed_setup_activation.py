"""Tracked preparation and real native reviewer receipts preserve setup adoption.

Only the native adapter is synthetic. Source resolution, task scheduling, role
selection, native lifetime, report acceptance and final proof readers are real.
"""

# ruff: noqa: F811

import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlmodel import select

from stackos import task_delivery
from stackos.db.models import AgentRequest, RunPlan, TrackerTask
from stackos.host_mcp.project_agents import ProjectAgentService
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.project_setup import (
    ProjectSetupRepository,
    SetupActivationEvidence,
    _digest,
)
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_managed_setup import (
    install_native,
    launch,
    managed,  # noqa: F401
    native_lifetime,  # noqa: F401
    native_route_defaults,  # noqa: F401
    task_receipts,
)
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_task_delivery import generic_result

KEY = "engineering.tracked-delivery"
MARKER = "NEW-ADOPTED-REVIEWER-MARKER-911"
CLAIMS = ["saved_defaults", "native_consumption", "reviewer_verification"]


def proposal_for(managed, session, *, host="codex"):
    _, project, _, _ = managed
    state = ProjectSetupRepository(session).get(project)
    from stackos.repositories.projects import ProjectRepository

    root = Path(ProjectRepository(session).get(project).project_dir)
    roles = {}
    for role, name in (("delivery_agent", "builder"), ("review_agent", "reviewer")):
        instructions = MARKER if role == "review_agent" else "Check setup as builder"
        choice = dict(host_key=host, name=name, provenance_ref="fixture/reviewed-adaptation")
        if host == "codex":
            path = root / ".codex" / "agents" / f"{name}.toml"
            path.parent.mkdir(parents=True, exist_ok=True)
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
    managed, monkeypatch, proposal=None, *, verification_status="success", checks=None
):
    """Return normal setup outputs through the actual native service lifecycle."""
    _, project, _, factory = managed
    calls = []
    prepared = {}

    def runner(root, instructions, prompt, schema, *, on_session, resume_thread_id=None, **kwargs):
        packet = json.loads(prompt)
        step = packet["workflow_step"]["step_id"] if packet["workflow_step"] else "check"
        thread = resume_thread_id or f"setup-{step}-{packet['phase']}"
        on_session(thread)
        calls.append(
            {
                "packet": packet,
                "instructions": instructions,
                "resume_thread_id": resume_thread_id,
                "native_settings": kwargs.get("native_settings"),
                "thread_id": thread,
            }
        )
        if step == "check":
            role = "review_agent" if packet["phase"] == "review" else "delivery_agent"
            observations = [
                {
                    **{k: request[k] for k in ("claims", "environment_ref", "evidence_ref")},
                    "status": "passed",
                }
                for request in checks
                if request["role"] == role
            ]
            return AgentRunResult(
                0,
                thread_id=thread,
                result=generic_result(
                    outputs={"setup_activation_checks": observations},
                ),
            )
        with factory() as check:
            repo = ProjectSetupRepository(check)
            current = repo.get(project).current_attempt
            if step == "prepare":
                result = verified_result(repo, project)
                result.attempt_ref = current.attempt_ref
                report = dict(
                    status=(
                        "success"
                        if packet["project_setup"]["requested_scope"] == "infrastructure_only"
                        else "partial"
                    ),
                    progress={"message": "Prepared", "evidence_ref": "fixture/prepared"},
                    workflow_results=[
                        {
                            "workflow_key": KEY,
                            "expected_revision": current.observed_receipt_revisions[KEY],
                            "result": result.model_dump(mode="json"),
                        }
                    ],
                )
                if proposal:
                    result.verification.activation_proof = SetupActivationEvidence(
                        method_ref="fixture/selected-method",
                        status="pending",
                        required_claims=CLAIMS,
                        missing_claims=CLAIMS,
                    )
                    report["workflow_results"][0]["result"] = result.model_dump(mode="json")
                    report["adoption_proposal"] = proposal
                    report["workflow_checks"] = {
                        KEY: checks
                        or [
                            {
                                "role": "review_agent",
                                "claims": CLAIMS,
                                "instructions": "Run the bounded fixture check",
                                "environment_ref": "fixture/isolated",
                                "evidence_ref": "fixture/check-output",
                            }
                        ]
                    }
                prepared.update(deepcopy(report))
                outputs = {"setup_report": report}
                disposition = "success"
            else:
                assert step == "activate-verify"
                report = deepcopy(prepared)
                report.update(status="success", adoption_proposal=None, workflow_checks={})
                report["workflow_results"][0]["expected_revision"] = (
                    current.observed_receipt_revisions[KEY]
                )
                if proposal:
                    report["workflow_results"][0]["result"]["verification"]["activation_proof"][
                        "status"
                    ] = "verified"
                    completed = packet["project_setup"]["completed_check_references"]
                    if completed:
                        activation = report["workflow_results"][0]["result"]["verification"][
                            "activation_proof"
                        ]
                        activation["checks"] = [item["check"] for item in completed]
                        activation["missing_claims"] = []
                outputs = {"setup_report": report}
                if proposal:
                    outputs["setup_activation_checks"] = [
                        {
                            "claims": CLAIMS,
                            "status": "passed",
                            "environment_ref": "fixture/isolated",
                            "evidence_ref": "fixture/check-output",
                        }
                    ]
                disposition = (
                    verification_status(calls)
                    if callable(verification_status)
                    else verification_status
                )
        return AgentRunResult(0, thread_id=thread, result=generic_result(disposition, outputs))

    install_native(monkeypatch, runner)
    return calls


@pytest.mark.parametrize("host", ["codex", "claude-code"])
def test_fresh_reviewer_consumes_adopted_source_and_finishes_through_task_proof(
    managed, session, monkeypatch, host
):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session, host=host)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    assert not session.exec(select(TrackerTask)).all()
    calls = install_runner(managed, monkeypatch, proposal)
    opened = launch(managed, host_key="codex")
    scheduler.run()
    task, receipts = task_receipts(session, project, opened.task_key)
    state = ProjectSetupRepository(session).get(project)
    assert state.current_attempt.status == "success", task.metadata_json["delivery"]
    assert [c["packet"]["workflow_step"]["step_id"] for c in calls] == [
        "prepare",
        "activate-verify",
    ]
    final = calls[-1]
    assert MARKER in final["instructions"]
    assert MARKER not in calls[0]["instructions"]
    assert final["resume_thread_id"] is None
    assert final["packet"]["project_setup"]["adoption_readback"] == TrackerRepository(
        session
    ).delivery_defaults(project_id=project)
    assert final["packet"]["project_setup"]["workflow_checks"][KEY][0]["role"] == "review_agent"
    final_evidence = receipts[f"{opened.task_key}-activate-verify"]
    proof_receipts = [final_evidence[p] for p in ("delivery", "review") if p in final_evidence]
    receipt = next(r for r in proof_receipts if r["selected_role"] == "review_agent")
    assert receipt["launch"]["host_key"] == host
    assert receipt["execution"]["invocation_id"] and receipt["finished_at"]
    assert receipt["validated_outcome"] == "success"
    if host == "codex":
        assert (
            receipt["launch"]["source_digest"]
            == proposal["roles"]["review_agent"]["expected_digest"]
        )
    stored = ProjectSetupRepository(session)._row(project, KEY)
    proof = stored.latest_result_json["verification"]["activation_proof"]
    assert proof["status"] == "verified"
    assert len(proof["checks"]) == 1
    reference = proof["checks"][0]["reference"]
    assert reference["kind"] == "task_phase" and reference["task_id"] == task.id
    assert reference["invocation_id"] == receipt["execution"]["invocation_id"]
    assert proof["checks"][0]["execution"]["role"] == "review_agent"
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert len(session.exec(select(RunPlan)).all()) == 1
    assert [t.key for t in session.exec(select(TrackerTask)).all()] == [opened.task_key]
    assert not scheduler.jobs


def test_failed_reviewer_resumes_same_frozen_role_without_repeating_preparation(
    managed, session, monkeypatch
):
    execution, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    calls = install_runner(
        managed,
        monkeypatch,
        proposal,
        verification_status=lambda calls: (
            "failed"
            if sum(c["packet"]["workflow_step"]["step_id"] == "activate-verify" for c in calls) == 1
            else "success"
        ),
    )
    opened = launch(managed, host_key="codex")
    scheduler.run()
    task, first_receipts = task_receipts(session, project, opened.task_key)
    first_prepare = deepcopy(first_receipts[f"{opened.task_key}-prepare"])
    repo = ProjectSetupRepository(session)
    current = repo.get(project).current_attempt
    assert (
        current.status != "success"
        and repo._row(project, KEY).latest_result_json["verification"]["activation_proof"]["status"]
        == "pending"
    )
    assert task.metadata_json["delivery"]["status"] == "stopped"
    execution.continue_managed(
        project_id=project,
        request_id=current.request_id,
        expected_revision=current.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    scheduler.run()
    task, receipts = task_receipts(session, project, opened.task_key)
    assert repo.get(project).current_attempt.status == "success", task.metadata_json["delivery"]
    assert receipts[f"{opened.task_key}-prepare"] == first_prepare
    assert [c["packet"]["workflow_step"]["step_id"] for c in calls] == [
        "prepare",
        "activate-verify",
        "activate-verify",
    ]
    assert calls[-1]["resume_thread_id"] == calls[-2]["thread_id"]
    assert calls[-1]["instructions"] == calls[-2]["instructions"]
    assert MARKER in calls[-1]["instructions"]


def test_incompatible_adopted_reviewer_requirement_rolls_back_before_verifier(
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
    calls = install_runner(managed, monkeypatch, proposal)
    opened = launch(managed, host_key="codex")
    scheduler.run()
    task, _ = task_receipts(session, project, opened.task_key)
    assert TrackerRepository(session).delivery_defaults(project_id=project) == {}
    assert ProjectSetupRepository(session)._row(project, KEY) is None
    assert task.metadata_json["delivery"]["status"] == "stopped"
    assert len(calls) == 1 and not scheduler.jobs


def test_infrastructure_completion_can_start_a_new_update_without_specialist_files(
    managed, session, monkeypatch
):
    execution, project, scheduler, factory = managed
    calls = install_runner(managed, monkeypatch)
    opened = execution.start_managed(
        project_id=project,
        session_factory=factory,
        scheduler=scheduler,
        mode="setup",
        workflow_keys=[KEY],
        preferences={"host_key": "codex"},
        requested_scope="infrastructure_only",
    )
    scheduler.run()
    session.expire_all()
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success"
    assert [c["packet"]["workflow_step"]["step_id"] for c in calls] == ["prepare"]
    _, receipts = task_receipts(session, project, opened.task_key)
    assert set(receipts) == {f"{opened.task_key}-prepare"}
    assert TrackerRepository(session).delivery_defaults(project_id=project) == {}
    updated = execution.start_managed(
        project_id=project,
        session_factory=factory,
        scheduler=scheduler,
        mode="update",
        workflow_keys=[KEY],
        preferences={"host_key": "codex"},
        requested_scope="infrastructure_only",
    )
    assert not updated.attached and updated.run_plan_id != opened.run_plan_id
    assert updated.task_key != opened.task_key and updated.run_id != opened.run_id
    assert len(scheduler.jobs) == 1


def test_setup_host_does_not_replace_saved_delivery_host_or_main_preferences(
    managed, session, monkeypatch
):
    _, project, scheduler, _ = managed
    tracker = TrackerRepository(session)
    proposal = proposal_for(managed, session, host="codex")
    saved = {
        "main_agent": {"host_key": "claude-code", "model": None},
        "agents": {
            role: {
                "name": f"local-{role}",
                "host_key": None,
                "source_ref": choice["source_ref"],
                "model": None,
                "provider": None,
            }
            for role, choice in proposal["roles"].items()
        },
    }
    tracker.configure_delivery(project_id=project, delivery=saved)
    proposal = proposal_for(managed, session)
    calls = install_runner(managed, monkeypatch, proposal)
    opened = launch(managed, host_key="claude-code")
    scheduler.run()
    task, receipts = task_receipts(session, project, opened.task_key)
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success", (
        task.metadata_json["delivery"]
    )
    assert tracker.delivery_defaults(project_id=project) == saved
    assert receipts[f"{opened.task_key}-prepare"]["delivery"]["launch"]["host_key"] == "claude-code"
    final = receipts[f"{opened.task_key}-activate-verify"]
    assert (
        next(
            final[p]
            for p in ("delivery", "review")
            if p in final and final[p]["selected_role"] == "review_agent"
        )["launch"]["host_key"]
        == "codex"
    )
    assert len(calls) == 2


def test_explicit_inspection_requirement_replaces_saved_write_need_before_source_preflight(
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
            "main_agent": {"host_key": "codex"},
            "agents": {
                "review_agent": {
                    "source_ref": reviewer["source_ref"],
                    "execution_requirements": {"filesystem_write": True},
                }
            },
        },
    )
    proposal["expected_delivery_digest"] = _digest(tracker.delivery_defaults(project_id=project))
    path = Path(proposal["project_dir"]) / reviewer["source_ref"]["source_path"]
    path.write_text(path.read_text() + 'sandbox_mode="read-only"\n')
    reviewer["expected_digest"] = (
        ProjectAgentService(Path(proposal["project_dir"])).get(reviewer["source_ref"]).digest
    )
    reviewer["execution_requirements"] = {"filesystem_write": False}
    calls = install_runner(managed, monkeypatch, proposal)
    opened = launch(managed, host_key="codex")
    scheduler.run()
    task, _ = task_receipts(session, project, opened.task_key)
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success", (
        task.metadata_json["delivery"]
    )
    saved = tracker.delivery_defaults(project_id=project)
    assert saved["agents"]["review_agent"]["execution_requirements"] == {"filesystem_write": False}
    assert saved["main_agent"] == {"host_key": "codex"}
    assert len(calls) == 2


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
    proposal = proposal_for(managed, session, host="codex")
    before = task_delivery.resolve_delivery_settings(
        session, project_id=project, project_dir=proposal["project_dir"]
    )
    calls = install_runner(managed, monkeypatch, proposal)
    opened = launch(managed, host_key="claude-code")
    scheduler.run()
    task, _ = task_receipts(session, project, opened.task_key)
    assert tracker.delivery_defaults(project_id=project) == saved
    assert (
        task_delivery.resolve_delivery_settings(
            session, project_id=project, project_dir=proposal["project_dir"]
        )
        == before
    )
    assert ProjectSetupRepository(session)._row(project, KEY) is None
    assert task.metadata_json["delivery"]["status"] == "stopped"
    assert len(calls) == 1 and not scheduler.jobs


def test_multiple_roles_use_normal_child_delivery_review_and_final_main_proof(
    managed, session, monkeypatch
):
    _, project, scheduler, _ = managed
    proposal = proposal_for(managed, session)
    proposal["roles"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    checks = [
        {
            "role": role,
            "claims": claims,
            "instructions": "Run the selected harmless check",
            "environment_ref": "fixture/isolated",
            "evidence_ref": f"fixture/{role}",
        }
        for role, claims in (
            ("delivery_agent", ["saved_defaults", "native_consumption"]),
            ("review_agent", ["reviewer_verification"]),
        )
    ]
    calls = install_runner(managed, monkeypatch, proposal, checks=checks)
    opened = launch(managed, host_key="codex")
    scheduler.run()
    task, receipts = task_receipts(session, project, opened.task_key)
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "success", (
        task.metadata_json["delivery"]
    )
    assert len(calls) == 4
    children = [value for key, value in receipts.items() if "-check-" in key]
    assert len(children) == 1
    child = children[0]
    assert child["delivery"]["selected_role"] == "delivery_agent"
    assert child["review"]["selected_role"] == "review_agent"
    assert child["delivery"]["thread_id"] != child["review"]["thread_id"]
    final = calls[-1]["packet"]
    assert final["workflow_step"]["step_id"] == "activate-verify"
    checks = final["project_setup"]["completed_check_references"]
    assert {item["check"]["reference"]["phase"] for item in checks} == {"delivery", "review"}
    assert {item["check"]["reference"]["invocation_id"] for item in checks} == {
        child[phase]["execution"]["invocation_id"] for phase in ("delivery", "review")
    }
    assert (
        receipts[f"{opened.task_key}-activate-verify"]["delivery"]["selected_role"] == "main_agent"
    )
    stored = ProjectSetupRepository(session)._row(project, KEY)
    assert len(stored.latest_result_json["verification"]["activation_proof"]["checks"]) == 2
    assert len(session.exec(select(TrackerTask)).all()) == 1
