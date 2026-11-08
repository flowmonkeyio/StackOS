"""The fixed delivery loop uses real tracker state and a controlled harness."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as SchemaValidationError
from sqlmodel import Session

from stackos.db.models import RunPlanStepStatus
from stackos.host_mcp.result import AgentRunResult, usage_snapshot
from stackos.mcp.context import MCPContext
from stackos.operations.tracker.schemas import (
    TrackerReopenInput,
    TrackerStartDeliveryInput,
    TrackerUpdateTicketInput,
)
from stackos.operations.tracker.write_handlers import (
    tracker_reopen,
    tracker_start_delivery,
    tracker_update_ticket,
)
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository


def scenario_result(*args, result=None, **kwargs):
    """Express scenario outcomes in the generic native wire format."""
    if result is not None and (
        "decision" in result or "commit_sha" in result or result.get("status") == "complete"
    ):
        value = dict(result)
        disposition = value.pop("decision", value.pop("status", None))
        value["status"] = {"complete": "success", "approve": "success", "reject": "repair"}.get(
            disposition, disposition
        )
        if "commit_sha" in value:
            value["outputs"] = json.dumps({"commit_sha": value.pop("commit_sha")})
        if "decision" not in result:
            value.setdefault("followups", [])
        result = value
    return AgentRunResult(*args, result=result, **kwargs)


def generic_result(status="success", outputs=None, summary="Checked", followups=None):
    return {
        "status": status,
        "summary": summary,
        "outputs": json.dumps(outputs or {}),
        "followups": followups or [],
    }


@pytest.mark.parametrize(
    ("consumed_repair", "observation_result"),
    [
        (False, "repair"),
        (False, "success"),
        (True, "repair"),
        (False, "blocked"),
        (False, "failed"),
        (False, "native-error"),
        (False, "schema-error"),
        (False, "candidate-error"),
        (False, "session-error"),
        (False, "no-reopen"),
        (False, "self-reopen"),
        (False, "stale-correction"),
        (False, "missing-correction-candidate"),
    ],
)
def test_agent_reopen_rescans_dependencies_and_resumes_original_sessions(
    session, project_id, tmp_path, monkeypatch, consumed_repair, observation_result
):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "generic-correction",
            "title": "Generic correction",
            "steps": [
                {"id": "assemble", "title": "Assemble"},
                {
                    "id": "measure",
                    "title": "Measure",
                    "depends_on": ["assemble"],
                    "metadata_json": {"agent_assignment": "review_agent"},
                },
                {"id": "finish", "title": "Finish", "depends_on": ["measure"]},
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="assemble")
    repo = TrackerRepository(session)
    task_key = f"workflow-{plan.id}"
    root = f"{task_key}-assemble"
    measure = f"{task_key}-measure"
    repo.create_ticket(
        project_id=project_id,
        task_key=task_key,
        key="producer",
        title="Producer",
        parent_ticket_key=root,
        dependency_keys=[root],
        run_plan_id=plan.id,
        run_plan_step_id=plans.get_step(plan.id, "assemble").id,
        context_json={"keep": {"input": "original"}},
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key=measure,
        patch_json={"add_dependency_keys": ["producer"]},
    )
    repo.create_task(project_id=project_id, key="held", title="Held")
    repo.create_ticket(project_id=project_id, task_key="held", key="held-child", title="Held")
    held = repo.brief(project_id=project_id, ticket_key="held-child").model_dump(mode="json")
    calls = []
    commits = []
    correction = {"source_ticket": measure, "check": "consumer", "required_outcome": "wired"}
    failed_outputs = {"check": "consumer", "observed": "missing"}

    def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        assert packet["lifecycle"] in _instructions
        assert "Explicitly assigned scoped tracker correction/requeue/reopen" in _instructions
        assert "otherwise return results without lifecycle mutations" in _instructions
        assert "Return results only" not in packet["lifecycle"]
        ticket = packet["brief"]["ticket"]
        key, phase = ticket["key"], packet["phase"]
        prior_calls = [c for c in calls if c == (key, phase)]
        thread = f"{key}-{phase}"
        assert kwargs.get("resume_thread_id") == (thread if prior_calls else None)
        calls.append((key, phase))
        status = "success"
        outputs = {}
        if key == "producer":
            if phase == "delivery":
                assert "a denial stops this task" not in packet["assignment"]
                assert "continue unaffected checks" in packet["assignment"]
                assert "Missing required proof remains blocked" in packet["assignment"]
            correcting = ticket["context_json"].get("correction") is not None
            if correcting:
                assert ticket["context_json"] == {
                    "keep": {"input": "original"},
                    "correction": correction,
                }
                # A previous approval is history, never the current repair request.
                assert packet["review_result"] is None
                assert ticket["completion_evidence_json"]["automatic_delivery"].get(
                    "repair_count", 0
                ) == (3 if consumed_repair else 0)
            if phase == "delivery" and not (
                correcting
                and observation_result in {"stale-correction", "missing-correction-candidate"}
            ):
                commits.append(commit(tmp_path, f"producer-{len(commits)}"))
            elif consumed_repair and (len(prior_calls) < 3 or correcting):
                status = "repair"
            outputs = {"commit_sha": commits[-1]}
            if correcting and observation_result == "missing-correction-candidate":
                outputs = {}
        elif key == measure and not prior_calls:
            with Session(session.get_bind()) as active:
                ctx = MCPContext(
                    session=active, request_id="native-correction", project_id=project_id
                )

                def update(target, patch):
                    return asyncio.run(
                        tracker_update_ticket(
                            TrackerUpdateTicketInput(
                                project_id=project_id, ticket_key=target, patch_json=patch
                            ),
                            ctx,
                            None,
                        )
                    )

                target = (
                    TrackerRepository(active)
                    .brief(project_id=project_id, ticket_key="producer")
                    .ticket
                )
                update(
                    "producer", {"context_json": {**target.context_json, "correction": correction}}
                )
                update(measure, {"context_json": {"failed_outputs": failed_outputs}})
                if observation_result != "self-reopen":
                    update("producer", {"status": "in-progress"})
                    update("producer", {"status": "not-started"})
                if observation_result != "no-reopen":
                    reopened = asyncio.run(
                        tracker_reopen(
                            TrackerReopenInput(
                                project_id=project_id,
                                task_key=task_key,
                                step_id="measure"
                                if observation_result == "self-reopen"
                                else "assemble",
                                reason="Consumer check requires original producer correction",
                            ),
                            ctx,
                            None,
                        )
                    )
                    assert reopened.data.run_id == started.run_id
            status, outputs = "repair", {"commit_sha": commits[-1], **failed_outputs}
            if observation_result in {"success", "blocked", "failed"}:
                status = observation_result
            elif observation_result == "native-error":
                return AgentRunResult(1, thread_id=thread, error="Native access failed")
            elif observation_result == "schema-error":
                return AgentRunResult(0, thread_id=thread, result={"status": "repair"})
            elif observation_result == "candidate-error":
                outputs["commit_sha"] = "0" * 40
            elif observation_result == "session-error":
                thread = None
        return AgentRunResult(
            0, thread_id=thread, result=generic_result(status, outputs, summary="Consumer check")
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=plan.id,
        step_id="assemble",
    )
    scheduler.run()
    session.expire_all()
    final = repo.get(project_id=project_id, task_key=task_key)
    integrity_failure = observation_result not in {"repair", "success"}
    assert final.tasks[0].metadata_json["delivery"]["status"] == (
        "stopped" if consumed_repair or integrity_failure else "complete"
    )
    if observation_result in {"stale-correction", "missing-correction-candidate"}:
        assert calls == [
            ("producer", "delivery"),
            ("producer", "review"),
            (root, "delivery"),
            (measure, "review"),
            ("producer", "delivery"),
        ]
        producer = repo.brief(project_id=project_id, ticket_key="producer").ticket
        assert producer.status == "failed"
        receipt = producer.completion_evidence_json["automatic_delivery"]["delivery"]
        assert receipt["validated_outcome"] is None
        assert receipt["validation_error"] == (
            "Repair requires a new descendant commit"
            if observation_result == "stale-correction"
            else "Agent must return a real commit_sha"
        )
        assert plans.get_step(plan.id, "measure").status == "pending"
        return
    if integrity_failure:
        assert calls == [
            ("producer", "delivery"),
            ("producer", "review"),
            (root, "delivery"),
            (measure, "review"),
        ]
        observation = repo.brief(project_id=project_id, ticket_key=measure).ticket
        receipt = observation.completion_evidence_json["automatic_delivery"]["review"]
        assert receipt["validated_outcome"] in {None, "repair", "blocked", "failed"}
        assert receipt["validated_outcome"] != "success"
        assert plans.get_step(plan.id, "finish").status == "pending"
        if observation_result != "no-reopen":
            assert receipt["superseded"] is True
            assert plans.get_step(plan.id, "measure").status == "pending"
        assert (
            repo.brief(project_id=project_id, ticket_key="held-child").model_dump(mode="json")
            == held
        )
        return
    producer = repo.brief(project_id=project_id, ticket_key="producer").ticket
    evidence = producer.completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == (3 if consumed_repair else 0)
    assert evidence["sessions"]["delivery"]["thread_id"] == "producer-delivery"
    assert evidence["sessions"]["review"]["thread_id"] == "producer-review"
    assert evidence["attempts"][-1]["review"]["validated_outcome"] == "success"
    assert evidence["review"]["validated_outcome"] == ("repair" if consumed_repair else "success")
    assert len(commits) == (5 if consumed_repair else 2)
    assert git(tmp_path, "merge-base", "--is-ancestor", commits[-2], commits[-1]) == ""
    observation = repo.brief(project_id=project_id, ticket_key=measure).ticket
    assert observation.context_json["failed_outputs"] == failed_outputs
    observations = observation.completion_evidence_json["automatic_delivery"]
    old = observations if consumed_repair else observations["attempts"][0]
    assert json.loads(old["review"]["result"]["outputs"]) == {
        "commit_sha": commits[-2],
        **failed_outputs,
    }
    assert old["review"]["superseded"] is True
    assert old["review"]["validated_outcome"] is None
    if not consumed_repair:
        assert all(t.status == "complete" for t in final.tickets)
        assert plans.get(plan.id).status == "completed"
        assert observations["sessions"]["review"]["thread_id"] == f"{measure}-review"
        assert calls[-4:] == [
            ("producer", "review"),
            (root, "delivery"),
            (measure, "review"),
            (f"{task_key}-finish", "delivery"),
        ]
    else:
        assert producer.status == "failed"
        assert plans.get_step(plan.id, "measure").status == "pending"
    assert (
        repo.brief(project_id=project_id, ticket_key="held-child").model_dump(mode="json") == held
    )


@pytest.mark.parametrize("role", ["delivery_agent", "review_agent"])
def test_mirror_candidate_evidence_is_independent_of_selected_role(
    session, project_id, tmp_path, monkeypatch, role
):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "candidate-observation",
            "title": "Observe",
            "steps": [
                {
                    "id": "observe",
                    "title": "Observe",
                    "metadata_json": {"agent_assignment": role},
                    "output_refs": ["commit_sha"],
                    "expected_outputs": {
                        "commit_sha": {
                            "required": True,
                            "type": "string",
                            "schema_json": {"type": "string"},
                        }
                    },
                }
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="observe")
    head = git(tmp_path, "rev-parse", "HEAD")
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        calls.append(packet)
        return AgentRunResult(
            0, thread_id="mirror-observation", result=generic_result(outputs={"commit_sha": head})
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="observe",
    )
    scheduler.run()
    session.expire_all()
    assert len(calls) == 1
    assert calls[0]["phase"] == role.removesuffix("_agent")
    final = plans.get(plan.id)
    assert final.status == "completed"
    assert final.steps[0].result_json == {"commit_sha": head}
    ticket = (
        TrackerRepository(session)
        .brief(project_id=project_id, ticket_key=f"workflow-{plan.id}-observe")
        .ticket
    )
    assert ticket.completion_evidence_json["automatic_delivery"]["candidate"]["commit_sha"] == head
    receipt = ticket.completion_evidence_json["automatic_delivery"][role.removesuffix("_agent")]
    assert receipt["selected_role"] == role
    assert receipt["validated_outcome"] == "success"
    assert git(tmp_path, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("integration", [False, True])
def test_workflow_prompt_projects_history_by_assignment_scope(integration):
    from copy import deepcopy

    from stackos import task_delivery

    def ticket(key, **fields):
        return {
            "id": len(key),
            "project_id": 7,
            "task_id": 9,
            "task_key": "work",
            "key": key,
            "title": key,
            "status": "complete",
            "dependency_keys": [],
            "completion_evidence_json": {
                "automatic_delivery": {
                    "attempt": 4,
                    "repair_count": 3,
                    "candidate": {"commit_sha": "a" * 40},
                    "attempts": [{"delivery": {"result": {"summary": "OLD_HISTORY" * 40}}}],
                    "review": {
                        "validated_outcome": "success",
                        "result": generic_result(summary=f"Accepted {key}"),
                    },
                }
            },
            "agent_stats": {"launch_attempts": 8, "detail": "STATISTICS" * 40},
            "active_execution": {"live_usage": {"samples": [1, 2, 3]}},
            **fields,
        }

    prerequisite = ticket("prerequisite", dependency_keys=["older"])
    external = ticket("external", task_id=10, task_key="another-task")
    older = ticket(
        "older", completion_evidence_json={}, outcome='Domain report contains {"followups": []}'
    )
    child = ticket("child", parent_ticket_key="current")
    bound = ticket("bound", run_plan_step_id=31)
    unrelated = ticket("unrelated", outcome="UNRELATED_HISTORY" * 40)
    current = ticket(
        "current",
        status="in-progress",
        dependency_keys=["prerequisite", "external"],
        definition_of_done_json=["Current acceptance"],
        constraints_json=["Current authority"],
        context_json={"contract_revision": 6, "correction": {"required_outcome": "NEW_FIX"}},
    )
    domain = {"followups": ["Declared domain value"]}
    value = {
        "assignment": "Current assignment",
        "phase": "delivery",
        "lifecycle": "Scoped authority",
        "candidate": {"base_commit_sha": "b" * 40},
        "brief": {
            "ticket": current,
            "task": {
                "project_id": 7,
                "key": "work",
                "context_json": {
                    "operator_amendments": ["Current amendment"],
                    "operator_inputs": domain,
                    "followups": ["DEFERRED_NOTE"],
                },
                "metadata_json": {"delivery": {"instructions": "OLD_SNAPSHOT" * 40}},
                "agent_stats": {"detail": "STATISTICS" * 40},
            },
            "dependencies": [prerequisite, external],
            "dependents": [ticket("later", context_json={"history": "LATER_HISTORY" * 40})],
        },
        "accepted_receipts": [prerequisite, older, child, bound, unrelated],
        "delivery_result": {"result": generic_result(outputs=domain), "usage": {"input": 90}},
        "review_result": {"result": generic_result("repair", {"finding": "NEW_FIX"})},
        "workflow_step": {"id": 31, "expected_outputs_json": domain} if integration else None,
    }
    original = deepcopy(value)
    packet = task_delivery._work_prompt_copy(value)
    serialized = json.dumps(packet)
    assert {r["key"] for r in packet["accepted_receipts"]} == (
        {"prerequisite", "external", "older", "child", "bound"}
        if integration
        else {"prerequisite", "external"}
    )
    for marker in (
        "OLD_HISTORY",
        "UNRELATED_HISTORY",
        "STATISTICS",
        "OLD_SNAPSHOT",
        "LATER_HISTORY",
        "DEFERRED_NOTE",
    ):
        assert marker not in serialized
    assert packet["brief"]["ticket"]["context_json"] == current["context_json"]
    assert packet["brief"]["ticket"]["definition_of_done_json"] == ["Current acceptance"]
    assert packet["brief"]["ticket"]["constraints_json"] == ["Current authority"]
    assert packet["brief"]["task"]["context_json"]["operator_amendments"] == ["Current amendment"]
    assert packet["brief"]["task"]["context_json"]["operator_inputs"] == domain
    assert packet["candidate"] == value["candidate"]
    assert packet["review_result"]["result"] == {
        key: item for key, item in value["review_result"]["result"].items() if key != "followups"
    }
    assert json.loads(packet["delivery_result"]["result"]["outputs"]) == domain
    assert packet["workflow_step"] == value["workflow_step"]
    assert packet["accepted_receipts"][0]["outcome"] == "Accepted prerequisite"
    if integration:
        assert (
            next(item for item in packet["accepted_receipts"] if item["key"] == "older")["outcome"]
            == older["outcome"]
        )
    recovery = packet["accepted_receipts"][0]["evidence_ref"]
    assert recovery == {
        "tool_name": "tracker.brief",
        "arguments": {"project_id": 7, "ticket_key": "prerequisite", "response_mode": "raw"},
    }
    assert packet["brief"]["ticket"]["completion_evidence_json"]["automatic_delivery"] == {
        "attempt": 4,
        "repair_count": 3,
        "candidate": {"commit_sha": "a" * 40},
    }
    assert len(serialized) < len(json.dumps(original))
    assert len(serialized.encode("utf-8")) < len(json.dumps(original).encode("utf-8"))
    assert value == original  # Projection never rewrites canonical receipts or input.


def test_workflow_prompt_compaction_keeps_private_completed_keys_for_reopen(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "private-progress",
            "title": "Private progress",
            "steps": [{"id": "before", "title": "Before"}, {"id": "check", "title": "Check"}],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="before")
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="before",
        status=RunPlanStepStatus.SUCCESS,
    )
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="check")
    repo = TrackerRepository(session)
    task_key = f"workflow-{plan.id}"
    mirror = f"{task_key}-check"
    repo.create_ticket(
        project_id=project_id,
        task_key=task_key,
        key="outside",
        title="Outside",
        run_plan_id=plan.id,
        run_plan_step_id=plans.get_step(plan.id, "before").id,
        parent_ticket_key=f"{task_key}-before",
    )
    historical = {"result": generic_result(summary="OLD_HISTORY")}
    repo.update_ticket(
        project_id=project_id,
        ticket_key="outside",
        patch_json={"status": "in-progress"},
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="outside",
        patch_json={"status": "complete", "completion_evidence_json": {"prior_review": historical}},
    )
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        key = packet["brief"]["ticket"]["key"]
        calls.append((key, packet["phase"]))
        if len(calls) == 1:
            assert packet["accepted_receipts"] == []
            # Full evidence remains available through the advertised canonical owner.
            with Session(session.get_bind()) as active:
                owner = TrackerRepository(active)
                assert owner.brief(
                    project_id=project_id, ticket_key="outside"
                ).ticket.completion_evidence_json == {"prior_review": historical}
                owner.update_ticket(
                    project_id=project_id,
                    ticket_key="outside",
                    patch_json={"status": "in-progress"},
                )
                owner.update_ticket(
                    project_id=project_id,
                    ticket_key="outside",
                    patch_json={"status": "not-started"},
                )
                owner.update_ticket(
                    project_id=project_id,
                    ticket_key=mirror,
                    patch_json={"add_dependency_keys": ["outside"]},
                )
                RunPlanRepository(active).reopen(
                    run_plan_id=plan.id,
                    project_id=project_id,
                    step_id="before",
                    reason="Recheck prerequisite",
                )
            return AgentRunResult(0, thread_id="check-session", result=generic_result("repair"))
        if key == mirror:
            assert kwargs["resume_thread_id"] == "check-session"
            assert {r["key"] for r in packet["accepted_receipts"]} == {"outside"}
        return AgentRunResult(
            0,
            thread_id="check-session" if key == mirror else f"outside-{packet['phase']}",
            result=generic_result(),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=plan.id,
        step_id="check",
    )
    scheduler.run()
    session.expire_all()
    assert calls == [
        (mirror, "delivery"),
        ("outside", "delivery"),
        ("outside", "review"),
        (f"{task_key}-before", "delivery"),
        (mirror, "delivery"),
    ]
    assert plans.get(plan.id).status == "completed"


def test_workflow_prompt_preserves_domain_followups_and_excludes_receipt_notes(
    session, project_id, tmp_path, monkeypatch
):
    from copy import deepcopy

    from stackos import task_delivery

    domain = {"report": {"followups": [{"text": "Domain next action"}]}}
    summary = 'Required report.followups must retain {"followups": []} in its schema.'
    expected = {
        "report": {
            "required": True,
            "type": "object",
            "schema_json": {
                "type": "object",
                "required": ["followups"],
                "properties": {"followups": {"type": "array", "items": {"type": "object"}}},
            },
        }
    }
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "domain-data",
            "title": "Domain data",
            "inputs": {"payload": {"followups": ["Input data"]}},
            "selected_context": {"followups": ["Selected domain context"]},
            "steps": [
                {"id": "collect", "title": "Collect"},
                {
                    "id": "report",
                    "title": "Report",
                    "depends_on": ["collect"],
                    "input_refs": ["payload"],
                    "output_refs": ["report"],
                    "expected_outputs": expected,
                },
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="collect")
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="collect",
        status=RunPlanStepStatus.SUCCESS,
        result_json=domain,
    )
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="report")
    repo = TrackerRepository(session)
    repo.update_task(
        project_id=project_id,
        task_key=f"workflow-{plan.id}",
        patch_json={
            "context_json": {
                "followups": [{"text": "DEFERRED_TASK_NOTE"}],
                "operator_inputs": {"followups": ["Operator domain input"]},
            }
        },
    )
    receipt = generic_result(
        outputs=domain, summary=summary, followups=[{"text": "DEFERRED_RECEIPT_NOTE"}]
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key=f"workflow-{plan.id}-collect",
        patch_json={
            "outcome": summary,
            "completion_evidence_json": {
                "automatic_delivery": {"delivery": {"result": receipt}},
                "domain_evidence": domain,
            },
        },
    )
    frozen = plans.get_step(plan.id, "report", full_context=True).model_dump(mode="json")
    captured = []

    def launch(*args, **kwargs):
        captured.append(json.loads(args[3]))
        return AgentRunResult(0, thread_id="domain-report", result=generic_result(outputs=domain))

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="report",
    )
    scheduler.run()
    session.expire_all()
    assert len(captured) == 1
    packet = captured[0]
    assert packet["workflow_step"] == frozen
    assert packet["brief"]["task"]["context_json"]["operator_inputs"] == {
        "followups": ["Operator domain input"]
    }
    assert "DEFERRED_TASK_NOTE" not in json.dumps(packet)
    assert "DEFERRED_RECEIPT_NOTE" not in json.dumps(packet)
    assert packet["accepted_receipts"][0]["outcome"] == summary
    accepted = packet["accepted_receipts"][0]["completion_evidence_json"]
    assert accepted["automatic_delivery"]["delivery"]["result"] == {"status": "success"}
    ref = packet["accepted_receipts"][0]["evidence_ref"]
    recovered = repo.brief(**{key: ref["arguments"][key] for key in ("project_id", "ticket_key")})
    assert recovered.ticket.completion_evidence_json["domain_evidence"] == domain
    assert (
        recovered.ticket.completion_evidence_json["automatic_delivery"]["delivery"]["result"]
        == receipt
    )
    assert receipt == generic_result(
        outputs=domain, summary=summary, followups=[{"text": "DEFERRED_RECEIPT_NOTE"}]
    )
    assert plans.get(plan.id).status == "completed"
    assert plans.get_step(plan.id, "report").result_json == domain
    assert plans.get_step(plan.id, "report").expected_outputs_json == deepcopy(expected)


def test_unfinished_nonready_work_stops_without_launch(session, project_id, tmp_path, monkeypatch):
    from stackos import task_delivery

    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"blocker_reason": "Missing approved input"},
    )
    monkeypatch.setattr(
        task_delivery, "run_agent", lambda *a, **kw: pytest.fail("Blocked work launched")
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key="deliver")
    assert all(t.status == "not-started" for t in snapshot.tickets)
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"


def test_taskwide_dispatch_cannot_complete_with_pending_actions(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery
    from tests.integration.test_repositories.test_run_plan_pending_actions import (
        _action,
        _running_plan,
    )

    plan = _running_plan(session, project_id)
    action = _action(session, plan)
    calls = []

    def launch(*args, **kwargs):
        calls.append(json.loads(args[3])["workflow_step"]["step_id"])
        return AgentRunResult(0, thread_id="pending-action-session", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="work",
    )
    scheduler.run()
    session.expire_all()
    final = RunPlanRepository(session).get(plan.id)
    assert calls == ["work"]
    assert [s.status for s in final.steps] == ["blocked", "pending"]
    assert action.status == "running"
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key=f"workflow-{plan.id}")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["status"] == "stopped" and "action" in state["reason"]


def test_taskwide_dispatch_preserves_pending_approval_gate(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "gated-task",
            "title": "Gated task",
            "approvals": [{"key": "operator-review", "title": "Operator review"}],
            "steps": [
                {"id": "read-local", "title": "Read"},
                {
                    "id": "change-local",
                    "title": "Change",
                    "depends_on": ["read-local"],
                    "approval_refs": ["operator-review"],
                },
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="read-local")
    calls = []

    def launch(*args, **kwargs):
        calls.append(json.loads(args[3])["workflow_step"]["step_id"])
        return AgentRunResult(0, thread_id="read-session", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="read-local",
    )
    scheduler.run()
    session.expire_all()
    final = plans.get(plan.id)
    assert calls == ["read-local"]
    assert [s.status for s in final.steps] == ["success", "pending"]
    assert final.approval_requests[0].status == "pending"
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key=f"workflow-{plan.id}")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["status"] == "stopped" and "approval" in state["reason"]


@pytest.mark.parametrize(
    "disposition", ["repair", "blocked", "failed", "invalid-json", "invalid-output", "native"]
)
def test_mirror_unsuccessful_or_malformed_result_stops_through_plan_owner(
    session, project_id, tmp_path, monkeypatch, disposition
):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "arbitrary-observation",
            "title": "Observe",
            "steps": [
                {
                    "id": "observe",
                    "title": "Observe",
                    "metadata_json": {"agent_assignment": "review_agent"},
                    "output_refs": ["report"],
                    "expected_outputs": {
                        "report": {
                            "required": True,
                            "type": "object",
                            "schema_json": {
                                "type": "object",
                                "required": ["count"],
                                "properties": {"count": {"type": "integer"}},
                            },
                        }
                    },
                },
                {"id": "wrap-up", "title": "Wrap", "depends_on": ["observe"]},
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="observe")
    calls = []

    def launch(*args, **kwargs):
        calls.append(json.loads(args[3]))
        if disposition == "native":
            return AgentRunResult(1, thread_id="failed-native", error="Native unavailable")
        value = generic_result(
            disposition if disposition in {"repair", "blocked", "failed"} else "success",
            outputs={"report": {"count": "wrong"}},
        )
        if disposition == "invalid-json":
            value["outputs"] = "not JSON"
        return AgentRunResult(0, thread_id="mirror", result=value)

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="observe",
    )
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key=f"workflow-{plan.id}")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert len(calls) == 1
    final = plans.get(plan.id)
    assert final.steps[0].status in {"blocked", "failed"}
    assert final.steps[1].status != "success"
    assert "repair_count" not in snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    receipt = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]["review"]
    assert receipt["selected_role"] == "review_agent"
    assert receipt["validated_outcome"] == (
        disposition if disposition in {"repair", "blocked", "failed"} else None
    )


def test_explicit_rehandoff_refreshes_current_snapshot_preserving_frozen_and_ticket_history(
    session, project_id, tmp_path
):
    from copy import deepcopy

    from stackos import task_delivery

    plan_id = workflow_task(session, project_id)
    plans = RunPlanRepository(session)
    repo = TrackerRepository(session)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
    )
    historical = {
        "sessions": {"delivery": {"thread_id": "accepted-session"}},
        "repair_pass_used": True,
        "attempts": [{"attempt": 1}],
    }
    repo.update_ticket(
        project_id=project_id,
        ticket_key="selected",
        patch_json={
            "status": "complete",
            "completion_evidence_json": {"automatic_delivery": historical},
        },
    )
    task_delivery._update_delivery(repo, project_id, f"workflow-{plan_id}", status="complete")
    frozen = deepcopy(plans.get(plan_id).model_dump(mode="json"))
    repo.create_task(
        project_id=project_id,
        key="historical",
        title="Historical",
        metadata_json={
            "delivery": {"status": "complete", "delivery_agent": {"instructions": "Old"}}
        },
    )
    old = repo.get(project_id=project_id, task_key="historical").tasks[0].model_dump(mode="json")
    repo.configure_delivery(
        project_id=project_id,
        delivery={
            "delivery_agent": {"name": "project-builder", "instructions": "Adopted build"},
            "review_agent": {"name": "project-reviewer", "instructions": "Adopted review"},
        },
    )
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
        delivery_agent=None,
        review_agent=None,
        agent_assignments={f"workflow-{plan_id}-later": "review_agent"},
    )
    current = (
        repo.get(project_id=project_id, task_key=f"workflow-{plan_id}")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert current["delivery_agent"]["instructions"] == "Adopted build"
    assert current["agent_assignments"][f"workflow-{plan_id}-later"] == "review_agent"
    repo.configure_delivery(project_id=project_id, delivery={})
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
        delivery_agent=None,
        review_agent=None,
    )
    assert (
        repo.get(project_id=project_id, task_key=f"workflow-{plan_id}")
        .tasks[0]
        .metadata_json["delivery"]
        == current
    )
    assert (
        repo.brief(project_id=project_id, ticket_key="selected").ticket.completion_evidence_json[
            "automatic_delivery"
        ]
        == historical
    )
    assert plans.get(plan_id).model_dump(mode="json") == frozen
    assert (
        repo.get(project_id=project_id, task_key="historical").tasks[0].model_dump(mode="json")
        == old
    )
    assert len(scheduler.jobs) == 2


def test_unchanged_head_is_valid_observation_evidence(session, project_id, tmp_path, monkeypatch):
    from stackos import task_delivery

    prepared_task(session, project_id)
    head = git(tmp_path, "rev-parse", "HEAD")

    def launch(*args, **kwargs):
        p = json.loads(args[3])
        return AgentRunResult(
            0,
            thread_id=p["brief"]["ticket"]["key"] + p["phase"],
            result=generic_result(outputs={"commit_sha": head}),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    assert (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "complete"
    )
    assert git(tmp_path, "rev-parse", "HEAD") == head


def test_non_git_task_and_unchanged_observation_candidate(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    shutil.rmtree(tmp_path / ".git")
    directory = tmp_path / "local-data"
    directory.mkdir()
    calls = []

    def launch(_host, _directory, _instructions, prompt, schema, **kwargs):
        packet = json.loads(prompt)
        calls.append((packet["brief"]["ticket"]["key"], packet["phase"]))
        return scenario_result(0, thread_id=str(calls[-1]), result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    monkeypatch.setattr(task_delivery, "_git", lambda *a: pytest.fail("Non-Git work invoked Git"))
    scheduler = QueuedScheduler()
    submit(session, project_id, directory, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "complete"
    assert calls == [
        (key, phase) for key in ("first", "second") for phase in ("delivery", "review")
    ]


def test_taskwide_nested_fork_join_and_frozen_mirrors(session, project_id, tmp_path, monkeypatch):
    from stackos import task_delivery

    shutil.rmtree(tmp_path / ".git")
    monkeypatch.setattr(
        task_delivery, "_git", lambda *a: pytest.fail("Non-Git workflow invoked Git")
    )
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "local-data-pipeline",
            "title": "Local data",
            "inputs": {"dataset": "x" * 6000},
            "selected_context": {"source": "y" * 6000},
            "steps": [
                {
                    "id": "gather",
                    "title": "Gather",
                    "instructions": ["Frozen instruction"],
                    "input_refs": ["dataset"],
                },
                {
                    "id": "inspect",
                    "title": "Inspect",
                    "depends_on": ["gather"],
                    "metadata_json": {"agent_assignment": "review_agent"},
                    "output_refs": ["report"],
                    "expected_outputs": {
                        "report": {
                            "required": True,
                            "type": "object",
                            "schema_json": {
                                "type": "object",
                                "required": ["count"],
                                "properties": {"count": {"type": "integer"}},
                            },
                        }
                    },
                },
                {"id": "side", "title": "Independent branch"},
                {
                    "id": "publish-local",
                    "title": "Publish",
                    "depends_on": ["inspect", "side"],
                    "metadata_json": {"agent_assignment": "delivery_agent"},
                },
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="gather")
    repo = TrackerRepository(session)
    root = f"workflow-{plan.id}-gather"
    step_pk = plans.get_step(plan.id, "gather").id
    for key, parent, kind, dependencies in [
        ("bundle", root, "group", [root]),
        ("nested", "bundle", "group", ["bundle"]),
        ("left", "nested", "ticket", ["nested"]),
        ("right", "nested", "ticket", ["nested"]),
        ("join", "bundle", "ticket", ["left", "right"]),
    ]:
        repo.create_ticket(
            project_id=project_id,
            task_key=f"workflow-{plan.id}",
            key=key,
            title=key,
            kind=kind,
            parent_ticket_key=parent,
            dependency_keys=dependencies,
            run_plan_id=plan.id,
            run_plan_step_id=step_pk,
        )
    calls = []

    def launch(_host, _directory, instructions, prompt, schema, **kwargs):
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        calls.append((key, packet["phase"], instructions))
        if packet.get("workflow_step"):
            assert packet["workflow_step"]["input_context_truncated"] is False
            if key == root:
                assert packet["workflow_step"]["input_values_json"]["dataset"] == "x" * 6000
                assert packet["workflow_step"]["step_context_json"]["source"] == "y" * 6000
                assert packet["workflow_step"]["instructions_json"] == ["Frozen instruction"]
                assert {r["key"] for r in packet["accepted_receipts"]} >= {"left", "right", "join"}
            if key == f"workflow-{plan.id}-inspect":
                handoff = packet["workflow_step"]["direct_dependency_handoffs"][0]
                assert handoff["truncated"] is False
                assert handoff["result_json"] == {"evidence": "z" * 6000}
        return scenario_result(
            0,
            thread_id=str(calls[-1]),
            result=generic_result(
                outputs=(
                    {"report": {"count": 2}}
                    if key == f"workflow-{plan.id}-inspect"
                    else {"evidence": "z" * 6000}
                    if key == root
                    else {}
                )
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan.id}",
        run_plan_id=plan.id,
        step_id="gather",
        agent_assignments={f"workflow-{plan.id}-publish-local": "review_agent"},
    )
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key=f"workflow-{plan.id}")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "complete"
    assert all(t.status == "complete" for t in snapshot.tickets)
    assert [c[0] for c in calls] == [
        "left",
        "left",
        "right",
        "right",
        "join",
        "join",
        root,
        f"workflow-{plan.id}-inspect",
        f"workflow-{plan.id}-side",
        f"workflow-{plan.id}-publish-local",
    ]
    assert (
        calls[-3][2]
        == calls[-1][2]
        == f"Review the ticket.\n\n{task_delivery.LAUNCH_AUTHORITY_BOUNDARY}"
    )
    final = plans.get(plan.id)
    assert final.status == "completed"
    assert all(s.claimed_by == task_delivery.ACTOR for s in final.steps[1:])
    assert final.steps[1].result_json == {"report": {"count": 2}}


def git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(directory: Path, name: str) -> str:
    (directory / name).write_text(name)
    git(directory, "add", name)
    git(directory, "commit", "-m", name)
    return git(directory, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def git_project(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Delivery Test")
    git(tmp_path, "config", "user.email", "delivery@example.test")
    commit(tmp_path, "baseline")


class QueuedScheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, dict[str, Any]]] = []

    def add_job(self, function, **options) -> None:
        assert options["jobstore"] == "memory"
        assert options["executor"] == "long"
        self.jobs.append((function, options))

    def run(self) -> None:
        function, options = self.jobs.pop(0)
        function(**options["kwargs"])


def prepared_task(session: Session, project_id: int) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="unrelated", title="Unrelated")
    repo.create_ticket(project_id=project_id, task_key="unrelated", key="other", title="Other")
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.update_task(
        project_id=project_id, task_key="deliver", patch_json={"metadata_json": {"keep": "yes"}}
    )
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    repo.create_ticket(
        project_id=project_id,
        task_key="deliver",
        key="second",
        title="Second",
        dependency_keys=["first"],
    )


def submit(session: Session, project_id: int, tmp_path: Path, scheduler: QueuedScheduler, **scope):
    task_key = scope.pop("task_key", "deliver")
    arguments = TrackerStartDeliveryInput(
        project_id=project_id,
        task_key=task_key,
        project_dir=str(tmp_path),
        host_key=scope.pop("host_key", None),
        delivery_agent=scope.pop(
            "delivery_agent", {"name": "project-builder", "instructions": "Build the ticket."}
        ),
        review_agent=scope.pop(
            "review_agent", {"name": "project-reviewer", "instructions": "Review the ticket."}
        ),
        **scope,
    )
    context = MCPContext(
        session=session,
        request_id="task-delivery-test",
        project_id=project_id,
        extras={"session_factory": lambda: Session(session.get_bind()), "scheduler": scheduler},
    )
    return asyncio.run(tracker_start_delivery(arguments, context, None)).data


@pytest.mark.parametrize("failure", ["native", "candidate", "blocked"])
@pytest.mark.parametrize(
    "requeue",
    [
        "same",
        "tracked-change",
        "untracked-change",
        "index-change",
        "session-change",
        "dirty-success",
    ],
)
def test_dirty_delivery_requeue_requires_unchanged_owned_work_and_saved_session(
    session, project_id, tmp_path, monkeypatch, failure, requeue
):
    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    base = git(tmp_path, "rev-parse", "HEAD")
    calls = []

    def run_agent(*args, **kwargs):
        packet = json.loads(args[3])
        phase = packet["phase"]
        calls.append((phase, kwargs.get("resume_thread_id")))
        if len(calls) == 1:
            (tmp_path / "baseline").write_text("staged edit")
            git(tmp_path, "add", "baseline")
            (tmp_path / "baseline").write_text("unstaged edit")
            (tmp_path / "new-file").write_text("new work")
            if failure == "native":
                return scenario_result(1, thread_id="first-delivery", error="Interrupted")
            return scenario_result(
                0,
                thread_id="first-delivery",
                result=generic_result(
                    "blocked" if failure == "blocked" else "success",
                    {"commit_sha": base},
                    "Commit unfinished",
                ),
            )
        if phase == "delivery":
            assert kwargs["resume_thread_id"] == "first-delivery"
            assert packet["candidate"]["base_commit_sha"] == base
            if requeue == "dirty-success":
                # Omitting a SHA must not bypass the clean candidate gate either.
                return scenario_result(0, thread_id="first-delivery", result=generic_result())
            git(tmp_path, "add", "baseline", "new-file")
            git(tmp_path, "commit", "-m", "Finish original work")
        return scenario_result(
            0,
            thread_id=f"first-{phase}",
            result=generic_result(outputs={"commit_sha": git(tmp_path, "rev-parse", "HEAD")}),
        )

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert ticket.status == "failed"
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    receipt = evidence["owned_work"]
    assert receipt["base_commit_sha"] == base
    assert receipt["session"] == evidence["sessions"]["delivery"]
    assert receipt["source"] == "delivery_return"
    assert receipt["snapshot"]["paths"] == ["baseline", "new-file"]
    assert "unstaged edit" not in json.dumps(receipt)
    if requeue == "tracked-change":
        (tmp_path / "baseline").write_text("someone else's edit")
    elif requeue == "untracked-change":
        (tmp_path / "other-file").write_text("someone else's work")
    elif requeue == "index-change":
        git(tmp_path, "add", "baseline")  # Same final bytes, different staged content.
    elif requeue == "session-change":
        evidence["sessions"]["delivery"]["thread_id"] = "different-session"
        repo.update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"completion_evidence_json": {"automatic_delivery": evidence}},
        )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    final = ticket.completion_evidence_json["automatic_delivery"]
    assert final["attempts"][0]["owned_work"] == receipt
    assert final["attempts"][0]["candidate"]["base_commit_sha"] == base
    if requeue == "same":
        assert calls == [("delivery", None), ("delivery", "first-delivery"), ("review", None)]
        assert ticket.status == "complete"
        assert final["candidate"]["base_commit_sha"] == base
        assert not git(tmp_path, "status", "--porcelain")
        assert "owned_work" not in final
    else:
        assert ticket.status == "failed"
        assert len(calls) == (2 if requeue == "dirty-success" else 1)
        assert "dirty" in ticket.outcome.lower()


def test_fresh_dirty_delivery_never_launches_or_adopts_work(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    (tmp_path / "unrelated").write_text("preserve this")
    monkeypatch.setattr(task_delivery, "run_agent", lambda *args, **kwargs: pytest.fail("launched"))
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = TrackerRepository(session).brief(project_id=project_id, ticket_key="first").ticket
    assert ticket.status == "failed"
    assert "dirty" in ticket.outcome
    assert "owned_work" not in ticket.completion_evidence_json["automatic_delivery"]
    assert (tmp_path / "unrelated").read_text() == "preserve this"


def test_operator_adopts_legacy_dirty_work_without_replacing_original_baseline(tmp_path):
    from stackos import task_delivery

    original_base = git(tmp_path, "rev-parse", "HEAD")
    new_head = commit(tmp_path, "runtime-fix")
    identity = {
        "agent": "builder",
        "host_key": "codex",
        "project_dir": str(tmp_path),
        "thread_id": "original-session",
    }
    evidence = {"candidate": {"base_commit_sha": original_base}, "sessions": {"delivery": identity}}
    (tmp_path / "baseline").write_text("original unfinished work")
    with pytest.raises(ValidationError, match="dirty"):
        task_delivery._launch_baseline(str(tmp_path), "first", evidence, "delivery", identity)
    receipt = task_delivery._owned_work_receipt(
        str(tmp_path),
        "first",
        evidence,
        operator_recovery={
            "actor": "operator",
            "reason": "Reviewed original failed session files; resume authorized",
        },
    )
    evidence["owned_work"] = receipt
    assert receipt["source"] == "operator_recovery"
    assert receipt["snapshot"]["head_commit_sha"] == new_head
    assert task_delivery._launch_baseline(
        str(tmp_path), "first", evidence, "delivery", identity
    ) == {"base_commit_sha": original_base}
    for key, phase, native in [
        ("other-ticket", "delivery", identity),
        ("first", "review", identity),
        ("first", "delivery", {**identity, "thread_id": "other-session"}),
    ]:
        with pytest.raises(ValidationError, match="dirty"):
            task_delivery._launch_baseline(str(tmp_path), key, evidence, phase, native)


def test_role_host_model_provider_reach_launch_and_receipt(
    session: Session, project_id: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackos import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    calls = []

    def run_agent(host, directory, instructions, prompt, schema, *, sandbox, **selection):
        packet = json.loads(prompt)
        delivery = packet["phase"] == "delivery"
        scope = selection.pop("execution_scope")
        assert scope.project_id == project_id
        assert scope.agent == ("builder" if delivery else "reviewer")
        assert instructions == ("Build" if delivery else "Review") + "\n\n" + packet["lifecycle"]
        assert "subject to normal native approval" in packet["lifecycle"]
        assert "within the current project and task" in packet["lifecycle"]
        assert "StackOS retains claim, acceptance, recording and dispatch" in packet["lifecycle"]
        assert callable(selection.pop("on_execution_change"))
        calls.append((host, selection))
        key = packet["brief"]["ticket"]["key"]
        sha = commit(tmp_path, key) if delivery else git(tmp_path, "rev-parse", "HEAD")
        value = {"status": "complete"} if delivery else {"decision": "approve", "followups": []}
        return scenario_result(
            0,
            thread_id=f"{key}-{delivery}",
            result={**value, "summary": "Checked", "commit_sha": sha},
        )

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        delivery_agent={
            "name": "builder",
            "instructions": "Build",
            "host_key": "hermes",
            "model": "provider/model",
            "provider": "openrouter",
            "profile": "work",
        },
        review_agent={
            "name": "reviewer",
            "instructions": "Review",
            "host_key": "claude-code",
            "model": "claude-opus-5-5",
        },
    )
    scheduler.run()
    assert (
        calls
        == [
            ("hermes", {"model": "provider/model", "provider": "openrouter", "profile": "work"}),
            ("claude-code", {"model": "claude-opus-5-5"}),
        ]
        * 2
    )
    session.expire_all()
    evidence = (
        TrackerRepository(session)
        .brief(project_id=project_id, ticket_key="first")
        .ticket.completion_evidence_json["automatic_delivery"]
    )
    assert evidence["delivery"]["launch"]["provider"] == "openrouter"
    assert evidence["delivery"]["launch"]["profile"] == "work"
    assert evidence["review"]["launch"]["host_key"] == "claude-code"
    assert evidence["review"]["launch"]["model"] == "claude-opus-5-5"


def test_unsupported_reviewer_rejects_before_scheduling(session, project_id, tmp_path):
    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="does not support headless"):
        submit(
            session,
            project_id,
            tmp_path,
            scheduler,
            review_agent={"name": "review", "instructions": "Review", "host_key": "claude-desktop"},
        )
    assert not scheduler.jobs
    task = TrackerRepository(session).get(project_id=project_id, task_key="deliver").tasks[0]
    assert task.metadata_json == {"keep": "yes"}


def test_tracker_defaults_preserve_unrelated_configuration_and_are_readable(session, project_id):
    repo = TrackerRepository(session)
    tracker = repo.ensure_tracker(project_id=project_id)
    tracker.config_json = {"unrelated": {"keep": True}}
    session.add(tracker)
    session.commit()
    defaults = {"delivery_agent": {"instructions": "Exact project source"}}
    saved = repo.configure_delivery(project_id=project_id, delivery=defaults)
    assert saved.config_json == {"unrelated": {"keep": True}, "delivery": defaults}
    snapshot = repo.get(project_id=project_id, include_graph=False)
    assert snapshot.tracker.config_json == saved.config_json
    assert snapshot.tracker.rev == saved.rev
    result = repo.delivery_defaults(project_id=project_id)
    result["delivery_agent"]["instructions"] = "Caller mutation"
    assert repo.delivery_defaults(project_id=project_id) == defaults
    cleared = repo.configure_delivery(project_id=project_id, delivery={})
    assert cleared.config_json == {"unrelated": {"keep": True}, "delivery": {}}
    assert cleared.rev == saved.rev + 1


def test_project_defaults_merge_by_role_and_freeze_before_launch(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    defaults = {
        "host_key": "claude-code",
        "delivery_agent": {"name": "builder", "instructions": "Saved build instructions"},
        "review_agent": {
            "name": "reviewer",
            "instructions": "Saved review instructions",
            "host_key": "gemini-cli",
            "model": "gemini-2.5-pro",
        },
    }
    repo.configure_delivery(project_id=project_id, delivery=defaults)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        delivery_agent=None,
        review_agent={"model": "gemini-3.1-pro-preview"},
    )
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json
    )
    assert state["keep"] == "yes"
    assert state["delivery"]["delivery_agent"] == {
        **defaults["delivery_agent"],
        "host_key": "claude-code",
    }
    assert state["delivery"]["review_agent"] == {
        **defaults["review_agent"],
        "model": "gemini-3.1-pro-preview",
    }
    # An edit after submission cannot change the effective task snapshot.
    repo.configure_delivery(
        project_id=project_id,
        delivery={"review_agent": {"instructions": "Changed later"}},
    )
    duplicate = submit(
        session, project_id, tmp_path, scheduler, delivery_agent=None, review_agent=None
    )
    assert duplicate["status"] == "queued" and len(scheduler.jobs) == 1
    calls = []

    def run_agent(host, directory, instructions, prompt, schema, *, sandbox, **selection):
        selection.pop("execution_scope")
        selection.pop("on_execution_change")
        calls.append((host, instructions, sandbox, selection))
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        if packet["phase"] == "delivery":
            value = {"status": "complete", "commit_sha": commit(tmp_path, key)}
        else:
            value = {
                "decision": "approve",
                "followups": [],
                "commit_sha": git(tmp_path, "rev-parse", "HEAD"),
            }
        return scenario_result(
            0, thread_id=f"{key}-{sandbox}", result={**value, "summary": "Checked"}
        )

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    scheduler.run()
    assert (
        calls
        == [
            (
                "claude-code",
                f"Saved build instructions\n\n{task_delivery.LAUNCH_AUTHORITY_BOUNDARY}",
                "workspace-write",
                {},
            ),
            (
                "gemini-cli",
                f"Saved review instructions\n\n{task_delivery.LAUNCH_AUTHORITY_BOUNDARY}",
                "normal",
                {"model": "gemini-3.1-pro-preview"},
            ),
        ]
        * 2
    )
    assert not (tmp_path / ".codex").exists()


@pytest.mark.parametrize(
    "defaults,override,reason",
    [
        ({}, None, "Resolved delivery_agent is invalid"),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build"}},
            None,
            "Resolved review_agent is invalid",
        ),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build", "model": "unknown"}},
            None,
            "native model catalog",
        ),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build"}},
            {"instructions": None},
            "Resolved delivery_agent is invalid",
        ),
    ],
)
def test_incomplete_or_invalid_resolved_defaults_never_schedule(
    session, project_id, tmp_path, defaults, override, reason
):
    prepared_task(session, project_id)
    TrackerRepository(session).configure_delivery(project_id=project_id, delivery=defaults)
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match=reason):
        submit(session, project_id, tmp_path, scheduler, delivery_agent=override, review_agent=None)
    assert not scheduler.jobs
    task = TrackerRepository(session).get(project_id=project_id, task_key="deliver").tasks[0]
    assert task.metadata_json == {"keep": "yes"}


@pytest.mark.parametrize("host", ["gemini-cli", "hermes"])
@pytest.mark.parametrize("failure", [None, "reject", "native", "mutation"])
def test_normal_review_receipts_and_same_task_failure(
    session, project_id, tmp_path, monkeypatch, host, failure
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    calls = []

    def run_agent(selected_host, directory, instructions, prompt, schema, *, sandbox, **selection):
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        calls.append((key, selected_host, sandbox))
        if sandbox == "workspace-write":
            value = {"status": "complete", "commit_sha": commit(tmp_path, key)}
        else:
            assert sandbox == "normal" and selected_host == host
            assert "Do not change files or commits" in packet["assignment"]
            assert schema == task_delivery.RESULT_SCHEMA
            if failure == "native":
                return scenario_result(1, error="Native review access denied")
            value = {
                "decision": "reject" if failure == "reject" else "approve",
                "commit_sha": packet["candidate"]["commit_sha"],
                "followups": [],
            }
            if failure == "mutation":
                (tmp_path / "review-edit").write_text("unauthorized review change")
        return scenario_result(
            0,
            thread_id=None if failure == "reject" and sandbox == "normal" else f"{key}-{sandbox}",
            result={**value, "summary": "Reviewed"},
        )

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    review = {"name": "reviewer", "instructions": "Independent review only", "host_key": host}
    if host == "hermes":
        review.update(provider="openrouter", model="provider/model")
    submit(session, project_id, tmp_path, scheduler, review_agent=review)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    first = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = first.completion_evidence_json["automatic_delivery"]
    assert evidence["review"]["launch"]["sandbox"] == "normal"
    assert evidence["review"]["launch"]["host_key"] == host
    assert first.status.value == ("failed" if failure else "complete")
    state = repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json["delivery"]
    assert state["status"] == ("stopped" if failure else "complete")
    assert len(calls) == (2 if failure else 4)
    if failure:
        assert (
            repo.brief(project_id=project_id, ticket_key="second").ticket.status.value
            == "not-started"
        )
    assert (
        repo.brief(project_id=project_id, ticket_key="other").ticket.status.value == "not-started"
    )


def test_two_tickets_run_in_order_after_submission_returns(
    session: Session, project_id: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackos import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    calls = []

    def run_agent(host, project_dir, instructions, prompt, schema, *, sandbox, **selection):
        packet = json.loads(prompt)
        ticket_key = packet["brief"]["ticket"]["key"]
        phase = "delivery" if packet["phase"] == "delivery" else "review"
        assert "delivery" not in (packet["brief"]["task"]["metadata_json"] or {})
        assert host == "codex" and Path(project_dir) == tmp_path
        with Session(session.get_bind()) as check:
            repo = TrackerRepository(check)
            brief = repo.brief(project_id=project_id, ticket_key=ticket_key)
            assert brief.ticket.status == "in-progress"
            evidence = brief.ticket.completion_evidence_json["automatic_delivery"]
            assert evidence[phase]["launch"]["sandbox"] == sandbox
            if phase == "review":
                assert evidence["candidate"]["commit_sha"] == git(tmp_path, "rev-parse", "HEAD")
            if ticket_key == "second":
                assert (
                    repo.brief(project_id=project_id, ticket_key="first").ticket.status
                    == "complete"
                )
        calls.append((ticket_key, instructions, phase))
        sha = (
            commit(tmp_path, ticket_key)
            if phase == "delivery"
            else git(tmp_path, "rev-parse", "HEAD")
        )
        if phase == "review":
            assert packet["candidate"]["commit_sha"] == sha
        result = (
            {"status": "complete", "summary": "Implemented"}
            if phase == "delivery"
            else {"decision": "approve", "summary": "Verified", "followups": []}
        )
        result["commit_sha"] = sha
        return scenario_result(0, thread_id=f"thread-{len(calls)}", result=result)

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    first = submit(session, project_id, tmp_path, scheduler)
    again = submit(session, project_id, tmp_path, scheduler)
    assert first == again and first["status"] == "queued"
    assert calls == [] and len(scheduler.jobs) == 1
    scheduler.run()
    assert [(key, sandbox) for key, _, sandbox in calls] == [
        ("first", "delivery"),
        ("first", "review"),
        ("second", "delivery"),
        ("second", "review"),
    ]
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert snapshot.tasks[0].status == "complete"
    metadata = snapshot.tasks[0].metadata_json
    assert metadata["keep"] == "yes"
    assert metadata["delivery"]["status"] == "complete"
    assert metadata["delivery"]["delivery_agent"]["name"] == "project-builder"
    for ticket in snapshot.tickets:
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert evidence["review"]["result"]["status"] == "success"
        assert evidence["delivery"]["thread_id"] != evidence["review"]["thread_id"]
        assert (
            evidence["candidate"]["commit_sha"]
            == json.loads(evidence["review"]["result"]["outputs"])["commit_sha"]
        )
        assert evidence["candidate"]["base_commit_sha"] != evidence["candidate"]["commit_sha"]
    assert (
        TrackerRepository(session).brief(project_id=project_id, ticket_key="other").ticket.status
        == "not-started"
    )


@pytest.mark.parametrize("failure", ["delivery", "review", "reject", "blocked"])
def test_failure_stops_before_next_ticket(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from stackos import task_delivery

    prepared_task(session, project_id)
    calls = []

    def run_agent(*args, sandbox, **kwargs):
        packet = json.loads(args[3])
        delivery = packet["phase"] == "delivery"
        calls.append(delivery)
        if failure == "delivery" or (failure == "review" and not delivery):
            return scenario_result(1, error="Harness failed")
        if delivery:
            return scenario_result(
                0,
                thread_id="delivery",
                result={
                    "status": "blocked" if failure == "blocked" else "complete",
                    "summary": "Delivery result",
                    "commit_sha": commit(tmp_path, "delivered"),
                },
            )
        return scenario_result(0, result={"decision": "reject", "summary": "Needs repair"})

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert len(calls) == (1 if failure in {"delivery", "blocked"} else 2)


def test_ready_filters_task_and_pending_before_limit(session: Session, project_id: int) -> None:
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    assert repo.next(project_id=project_id, limit=1).tickets[0].key == "other"
    assert (
        repo.next(project_id=project_id, task_key="deliver", pending_only=True, limit=1)
        .tickets[0]
        .key
        == "first"
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    assert repo.next(project_id=project_id, task_key="deliver", pending_only=True).tickets == []
    assert repo.next(project_id=project_id, task_key="deliver").tickets[0].key == "first"


def test_workflow_task_is_not_a_second_run_plan_controller(
    session: Session, project_id: int, tmp_path: Path
) -> None:
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Workflow", source_kind="workflow")
    with pytest.raises(ValidationError, match=r"run_plan_id.*step_id"):
        submit(session, project_id, tmp_path, QueuedScheduler())


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "stale",
        "unknown",
        "wrong",
        "dirty",
        "review-sha",
        "review-mutation",
        "review-new-sha",
    ],
)
def test_commit_identity_failure_stops_same_task(
    session, project_id, tmp_path, monkeypatch, failure
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    base = git(tmp_path, "rev-parse", "HEAD")
    calls = []

    def run_agent(*args, sandbox, **kwargs):
        packet = json.loads(args[3])
        delivery = packet["phase"] == "delivery"
        calls.append(delivery)
        if delivery:
            sha = commit(tmp_path, "first")
            result = {"status": "complete", "summary": "Delivered", "commit_sha": sha}
            if failure == "missing":
                result.pop("commit_sha")
            elif failure == "stale":
                result["commit_sha"] = base
            elif failure == "unknown":
                result["commit_sha"] = "0" * 40
            elif failure == "wrong":
                commit(tmp_path, "unexpected")
            elif failure == "dirty":
                (tmp_path / "first").write_text("Uncommitted change")
        else:
            sha = git(tmp_path, "rev-parse", "HEAD")
            result = {
                "decision": "approve",
                "summary": "Reviewed",
                "commit_sha": base if failure == "review-sha" else sha,
                "followups": [],
            }
            if failure in {"review-mutation", "review-new-sha"}:
                new_sha = commit(tmp_path, "review-mutation")
                if failure == "review-new-sha":
                    result["commit_sha"] = new_sha
        return scenario_result(0, thread_id=f"thread-{delivery}", result=result)

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    if failure == "missing":
        assert "Invalid codex result" in evidence["delivery"]["error"]
    else:
        assert evidence["delivery"]["result"]["status"] == "success"
    assert len(calls) == (2 if failure.startswith("review-") else 1)


@pytest.mark.parametrize("correction", [False, True])
def test_verification_entry_delivers_and_reviews_children_before_verifier(
    session, project_id, tmp_path, monkeypatch, correction
):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "verification-entry",
            "title": "Delivery without a summary phase",
            "steps": [
                {"id": "review-design", "title": "Readiness"},
                {
                    "id": "verify-delivery",
                    "title": "Verify",
                    "depends_on": ["review-design"],
                },
                {
                    "id": "review-delivery",
                    "title": "Review",
                    "depends_on": ["verify-delivery"],
                    "metadata_json": {"agent_assignment": "review_agent"},
                },
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="review-design")
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="review-design",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"summary": "Ready"},
    )
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="verify-delivery")
    repo = TrackerRepository(session)
    task_key = f"workflow-{plan.id}"
    verifier = f"{task_key}-verify-delivery"
    reviewer = f"{task_key}-review-delivery"
    repo.create_ticket(
        project_id=project_id,
        task_key=task_key,
        key="implementation",
        title="Implement the change",
        parent_ticket_key=verifier,
        dependency_keys=[verifier],
        run_plan_id=plan.id,
        run_plan_step_id=plans.get_step(plan.id, "verify-delivery").id,
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key=reviewer,
        patch_json={"add_dependency_keys": ["implementation"]},
    )
    calls = []
    candidates = []

    def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        resumed = (key, phase) in calls
        thread = f"{key}-{phase}"
        assert kwargs.get("resume_thread_id") == (thread if resumed else None)
        calls.append((key, phase))
        status = "success"
        if key == "implementation":
            assert packet["workflow_step"] is None
            if phase == "delivery":
                if resumed:
                    assert (
                        packet["brief"]["ticket"]["context_json"]["correction"] == "Missed caller"
                    )
                    assert packet["review_result"] is None
                candidates.append(commit(tmp_path, f"implemented-{len(candidates)}"))
            else:
                assert packet["candidate"]["commit_sha"] == candidates[-1]
        elif key == verifier:
            receipt = next(r for r in packet["accepted_receipts"] if r["key"] == "implementation")
            evidence = receipt["completion_evidence_json"]["automatic_delivery"]
            assert evidence["review"]["validated_outcome"] == "success"
            assert evidence["candidate"]["commit_sha"] == candidates[-1]
            if correction and not resumed:
                with Session(session.get_bind()) as active:
                    active_repo = TrackerRepository(active)
                    for patch in [
                        {"context_json": {"correction": "Missed caller"}},
                        {"status": "in-progress"},
                        {"status": "not-started"},
                    ]:
                        active_repo.update_ticket(
                            project_id=project_id, ticket_key="implementation", patch_json=patch
                        )
                    RunPlanRepository(active).reopen(
                        run_plan_id=plan.id,
                        project_id=project_id,
                        step_id="verify-delivery",
                        reason="Correct the accepted child before repeating verification",
                    )
                    # The requeued child is real progress, but activation is pending
                    # until the runner reclaims its own verification entry step.
                    ready = active_repo.delivery_ready(project_id=project_id, task_key=task_key)
                    assert "implementation" not in {ticket.key for ticket in ready}
                status = "repair"
        return AgentRunResult(
            0,
            thread_id=thread,
            result=generic_result(status, outputs={"commit_sha": candidates[-1]}),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=plan.id,
        step_id="verify-delivery",
    )
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key=task_key)
    child_and_verifier = [
        ("implementation", "delivery"),
        ("implementation", "review"),
        (verifier, "delivery"),
    ]
    assert calls == child_and_verifier * (2 if correction else 1) + [(reviewer, "review")]
    assert plans.get(plan.id).status == "completed"
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "complete"
    assert all(ticket.status == "complete" for ticket in snapshot.tickets)


def test_reclaimed_blocked_mirror_is_selected_before_success(session, project_id):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "resume-blocked",
            "title": "Resume blocked",
            "steps": [
                {"id": "inspect", "title": "Inspect"},
                {"id": "finish", "title": "Finish", "depends_on": ["inspect"]},
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="inspect")
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="inspect",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"blocking_issue": "Required fixture unavailable"},
    )
    repo = TrackerRepository(session)
    task_key = f"workflow-{plan.id}"
    state = {"run_plan_id": plan.id, "controller_run_id": started.run_id}

    # A real blocker and the unfinished dependent must both remain excluded.
    assert repo.delivery_ready(project_id=project_id, task_key=task_key) == []
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        False,
    )

    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="inspect")

    # No success has been recorded: selection must use the newly running state.
    assert [
        ticket.key for ticket in repo.delivery_ready(project_id=project_id, task_key=task_key)
    ] == [f"{task_key}-inspect"]
    ticket, step, changed = task_delivery._select_work(repo, plans, project_id, task_key, state)
    assert ticket.key == f"{task_key}-inspect"
    assert step.step_id == "inspect"
    assert step.status == RunPlanStepStatus.RUNNING
    assert step.result_json == {"blocking_issue": "Required fixture unavailable"}
    assert changed is False
    assert plans.get(plan.id).steps[1].status == RunPlanStepStatus.PENDING


def workflow_task(session, project_id, *, running=True):
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "delivery-scope",
            "title": "Delivery scope",
            "steps": [
                {"id": "deliver", "title": "Deliver"},
                {"id": "later", "title": "Later", "depends_on": ["deliver"]},
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    if running:
        plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="deliver")
    repo = TrackerRepository(session)
    for key, step_id, kind in [
        ("other-step", "later", "ticket"),
        ("group", "deliver", "group"),
        ("selected", "deliver", "ticket"),
    ]:
        context = repo.workflow_step_ticket_context(
            project_id=project_id, run_plan_id=plan.id, step_id=step_id
        )
        repo.create_ticket(
            project_id=project_id,
            task_key=context["task_key"],
            key=key,
            title=key,
            kind=kind,
            parent_ticket_key=context["parent_ticket_key"],
            run_plan_id=plan.id,
            run_plan_step_id=context["run_plan_step_id"],
        )
    return plan.id


def test_workflow_advances_beyond_entry_children_through_lifecycle(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    plan_id = workflow_task(session, project_id)
    calls = []

    def run_agent(*args, sandbox, **kwargs):
        packet = json.loads(args[3])
        calls.append(packet["brief"]["ticket"]["key"])
        delivery = packet["phase"] == "delivery"
        sha = git(tmp_path, "rev-parse", "HEAD")
        return scenario_result(
            0,
            thread_id=f"selected-{delivery}",
            result={
                "status" if delivery else "decision": "complete" if delivery else "approve",
                "summary": "Done",
                "commit_sha": sha,
                **({} if delivery else {"followups": []}),
            },
        )

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
    )
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key=f"workflow-{plan_id}")
    assert calls == [
        "selected",
        "selected",
        f"workflow-{plan_id}-deliver",
        "other-step",
        "other-step",
        f"workflow-{plan_id}-later",
    ]
    assert snapshot.tasks[0].status == "complete"
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "complete"
    statuses = {ticket.key: ticket.status for ticket in snapshot.tickets}
    assert all(status == "complete" for status in statuses.values())


@pytest.mark.parametrize("failure", ["pending", "task", "step", "partial"])
def test_workflow_selector_rejected_before_scheduling(session, project_id, tmp_path, failure):
    plan_id = workflow_task(session, project_id, running=failure != "pending")
    scope = {"task_key": f"workflow-{plan_id}", "run_plan_id": plan_id, "step_id": "deliver"}
    if failure == "task":
        prepared_task(session, project_id)
        scope["task_key"] = "deliver"
    elif failure == "step":
        scope["step_id"] = "missing"
    elif failure == "partial":
        scope.pop("step_id")
    scheduler = QueuedScheduler()
    with pytest.raises((ValidationError, NotFoundError, SchemaValidationError)):
        submit(session, project_id, tmp_path, scheduler, **scope)
    assert scheduler.jobs == []


def test_queued_workflow_stops_when_step_is_no_longer_running(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    plan_id = workflow_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
    )
    plans = RunPlanRepository(session)
    plan = plans.get(plan_id)
    plans.record_step(
        run_plan_id=plan_id,
        run_id=plan.run_id,
        step_id="deliver",
        status=RunPlanStepStatus.BLOCKED,
        error="Operator paused delivery",
    )
    monkeypatch.setattr(task_delivery, "run_agent", lambda *a, **kw: pytest.fail("must not launch"))
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key=f"workflow-{plan_id}")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert "No pending ticket" in snapshot.tasks[0].metadata_json["delivery"]["reason"]
    assert (
        next(ticket for ticket in snapshot.tickets if ticket.key == "selected").status
        == "not-started"
    )


def feedback_harness(
    session, project_id, tmp_path, monkeypatch, decisions, *, fault=None, usage_basis="invocation"
):
    """Real commits and durable tracker reads around each controlled native response."""
    from stackos import task_delivery

    calls = []
    base = git(tmp_path, "rev-parse", "HEAD")

    def run_agent(host, directory, instructions, prompt, schema, *, sandbox, **selection):
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        phase = "delivery" if packet["phase"] == "delivery" else "review"
        resume = selection.get("resume_thread_id")
        previous = [call for call in calls if call["key"] == key and call["phase"] == phase]
        assert resume == (f"{key}-{phase}" if previous else None)
        assert sandbox == "workspace-write"  # Both Codex roles execute focused specs.
        assert "SIDE NOTE" not in prompt
        assert packet["brief"]["task"]["context_json"]["operator_inputs"] == ["Keep input"]
        if previous and phase == "delivery":
            with Session(session.get_bind()) as check:
                durable = TrackerRepository(check).brief(project_id=project_id, ticket_key=key)
                evidence = durable.ticket.completion_evidence_json["automatic_delivery"]
                assert 1 <= evidence["repair_count"] <= 3
                assert evidence["attempts"][-1]["review"]["result"]["status"] == "repair"
            assert packet["review_result"]["result"]["summary"] == "Scoped repair"
        calls.append({"key": key, "phase": phase, "resume": resume, "packet": packet})
        identity = f"{key}-{phase}"
        usage = usage_snapshot(
            "fixture",
            usage_basis,
            160 if previous else 100,
            70 if previous else 50,
            cache_read=40 if previous else 20,
            cache_write=10,
            reasoning=5,
        )
        if phase == "delivery":
            sha = commit(tmp_path, f"{key}-{len(calls)}")
            value = {"status": "complete", "summary": "Checked specs", "commit_sha": sha}
            if previous and fault == "stale-repair":
                git(tmp_path, "reset", "--hard", packet["candidate"]["base_commit_sha"])
                value["commit_sha"] = packet["candidate"]["base_commit_sha"]
        else:
            value = {
                "decision": decisions.pop(0),
                "summary": "Scoped repair",
                "commit_sha": packet["candidate"]["commit_sha"],
                "followups": [{"text": f"SIDE NOTE {key} {len(calls)}"}],
            }
            if fault == "stale-review":
                value["commit_sha"] = base
            elif fault == "wrong-review":
                value["commit_sha"] = "0" * 40
            elif fault == "dirty-review":
                (tmp_path / "uncommitted").write_text("reviewer edit")
            elif fault == "mutating-review":
                value["commit_sha"] = commit(tmp_path, "reviewer-commit")
            elif fault == "schema":
                value.pop("followups")
            elif fault in {"native", "native-final"}:
                return scenario_result(
                    1,
                    thread_id=identity,
                    result=value if fault == "native-final" else None,
                    error="Required check unavailable",
                    usage=usage,
                )
        if fault == "missing-id" or (fault == "missing-review-id" and phase == "review"):
            identity = None
        elif resume and (
            (fault == "wrong-id" and phase == "delivery")
            or (fault == "wrong-review-id" and phase == "review")
        ):
            identity = "wrong-session"
        elif fault == "shared-id" and phase == "review":
            identity = f"{key}-delivery"
        return scenario_result(0, thread_id=identity, result=value, usage=usage)

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    return calls


def feedback_task(session, project_id):
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    repo.update_task(
        project_id=project_id,
        task_key="deliver",
        patch_json={
            "context_json": {
                "operator_inputs": ["Keep input"],
                "other": "preserved",
                "followups": [
                    {
                        "text": "SIDE NOTE existing",
                        "source_ticket_key": "older",
                        "source_attempt": 1,
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        },
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={
            "completion_evidence_json": {
                "unrelated": {"keep": True},
                "prior_review": {"deep": {"followups": [{"text": "SIDE NOTE nested"}]}},
            }
        },
    )


@pytest.mark.parametrize("usage_basis", ["invocation", "session"])
def test_feedback_reject_repair_approve_preserves_sessions_attempts_and_notes(
    session, project_id, tmp_path, monkeypatch, usage_basis
):
    feedback_task(session, project_id)
    calls = feedback_harness(
        session,
        project_id,
        tmp_path,
        monkeypatch,
        ["reject", "approve", "approve"],
        usage_basis=usage_basis,
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    assert [(call["key"], call["phase"], call["resume"]) for call in calls] == [
        ("first", "delivery", None),
        ("first", "review", None),
        ("first", "delivery", "first-delivery"),
        ("first", "review", "first-review"),
        ("second", "delivery", None),
        ("second", "review", None),
    ]
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    first, second = snapshot.tickets
    evidence = first.completion_evidence_json["automatic_delivery"]
    assert first.completion_evidence_json["unrelated"] == {"keep": True}
    assert "SIDE NOTE nested" in json.dumps(first.completion_evidence_json["prior_review"])
    prior = evidence["attempts"][0]
    assert prior["review"]["result"]["status"] == "repair"
    assert prior["review"]["selected_role"] == "review_agent"
    assert prior["review"]["validated_outcome"] == "repair"
    for phase, role in (("delivery", "delivery_agent"), ("review", "review_agent")):
        assert prior[phase]["selected_role"] == role
        assert evidence[phase]["selected_role"] == role
        assert evidence[phase]["validated_outcome"] == "success"
        assert prior[phase]["usage"]["snapshot"]["total"] == 150
        assert prior[phase]["usage"]["measured"]["total"] == 150
        assert prior[phase]["usage"]["measured"]["cache_read"] == 20
        assert evidence[phase]["usage"]["snapshot"] == {
            "input": 160,
            "output": 70,
            "total": 230,
            "cache_read": 40,
            "cache_write": 10,
            "reasoning": 5,
        }
        assert evidence[phase]["usage"]["measured"]["total"] == (
            80 if usage_basis == "session" else 230
        )
        assert evidence[phase]["usage"]["measured"]["cache_read"] == (
            20 if usage_basis == "session" else 40
        )
        assert evidence[phase]["usage"]["measured"]["cache_write"] == (
            0 if usage_basis == "session" else 10
        )
        assert evidence["repair_count"] == 1
    assert prior["candidate"]["commit_sha"] != evidence["candidate"]["commit_sha"]
    assert evidence["candidate"]["base_commit_sha"] == prior["candidate"]["commit_sha"]
    assert (
        json.loads(evidence["review"]["result"]["outputs"])["commit_sha"]
        == evidence["candidate"]["commit_sha"]
    )
    assert evidence["sessions"]["delivery"]["thread_id"] == "first-delivery"
    assert evidence["sessions"]["review"]["thread_id"] == "first-review"
    assert (
        second.completion_evidence_json["automatic_delivery"]["sessions"]["review"]["thread_id"]
        == "second-review"
    )
    context = snapshot.tasks[0].context_json
    assert context["operator_inputs"] == ["Keep input"] and context["other"] == "preserved"
    notes = context["followups"]
    assert len(notes) == 4
    assert [(n["source_ticket_key"], n["source_attempt"]) for n in notes[1:]] == [
        ("first", 1),
        ("first", 2),
        ("second", 1),
    ]
    assert all(n["created_at"] for n in notes)
    assert snapshot.tasks[0].metadata_json["keep"] == "yes"
    assert len(snapshot.tickets) == 2 and second.dependency_keys == ["first"]


@pytest.mark.parametrize("decision", ["approve", "blocked", "reject"])
def test_feedback_followup_only_blocked_and_fourth_reject(
    session, project_id, tmp_path, monkeypatch, decision
):
    feedback_task(session, project_id)
    decisions = ["approve", "approve"] if decision == "approve" else [decision] * 4
    calls = feedback_harness(session, project_id, tmp_path, monkeypatch, decisions)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert snapshot.tickets[0].status == ("complete" if decision == "approve" else "failed")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == (
        "complete" if decision == "approve" else "stopped"
    )
    assert len(calls) == {"blocked": 2, "approve": 4, "reject": 8}[decision]
    if decision != "approve":
        assert snapshot.tickets[1].status == "not-started"
    context = snapshot.tasks[0].context_json
    assert context["operator_inputs"] == ["Keep input"] and context["other"] == "preserved"
    notes = context["followups"]
    assert len(notes) == {"blocked": 2, "approve": 3, "reject": 5}[decision]
    assert notes[1]["text"] == "SIDE NOTE first 2"
    assert notes[1]["source_ticket_key"] == "first"
    assert notes[1]["source_attempt"] == 1
    assert notes[1]["created_at"]
    if decision == "blocked":
        evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
        assert evidence["repair_count"] == 0
        assert evidence["review"]["result"]["status"] == "blocked"


@pytest.mark.parametrize("invalid_location", ["result", "followup"])
def test_feedback_schema_error_requeue_excludes_serialized_followups(
    session, project_id, tmp_path, monkeypatch, invalid_location
):
    from stackos import task_delivery

    feedback_task(session, project_id)
    calls = []

    def run_agent(host, directory, instructions, prompt, schema, *, sandbox, **selection):
        packet = json.loads(prompt)
        assert packet["brief"]["ticket"]["key"] == "first"
        assert "SIDE NOTE" not in prompt
        phase = "delivery" if packet["phase"] == "delivery" else "review"
        previous = [call for call in calls if call["phase"] == phase]
        identity = f"first-{phase}"
        assert selection.get("resume_thread_id") == (identity if previous else None)
        calls.append({"phase": phase, "packet": packet})
        if phase == "delivery":
            value = {
                "status": "complete",
                "summary": "Checked specs",
                "commit_sha": commit(tmp_path, f"candidate-{len(calls)}"),
            }
        else:
            value = {
                "decision": "blocked" if previous else "reject",
                "summary": "Required check unavailable" if previous else "Scoped repair",
                "commit_sha": packet["candidate"]["commit_sha"],
                "followups": [] if previous else [{"text": "SIDE NOTE malformed reviewer"}],
            }
            if not previous:
                if invalid_location == "result":
                    value["unexpected"] = True
                else:
                    value["followups"][0]["unexpected"] = True
        return scenario_result(0, thread_id=identity, result=value)

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    stopped = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in stopped.tickets] == ["failed", "not-started"]
    error = stopped.tickets[0].completion_evidence_json["automatic_delivery"]["review"]["error"]
    assert "unexpected" in error and "SIDE NOTE malformed reviewer" in error
    assert stopped.tickets[0].outcome == error
    assert stopped.tasks[0].metadata_json["delivery"]["reason"] == error
    assert len(stopped.tasks[0].context_json["followups"]) == 1
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    assert [call["phase"] for call in calls] == ["delivery", "review", "delivery", "review"]
    session.expire_all()
    stopped = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in stopped.tickets] == ["failed", "not-started"]
    evidence = stopped.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["attempts"][0]["review"]["error"] == error
    assert evidence["repair_count"] == 0
    assert evidence["review"]["result"]["status"] == "blocked"


@pytest.mark.parametrize(
    "fault,decision,expected_calls",
    [
        ("missing-id", "approve", 1),
        ("missing-review-id", "approve", 2),
        ("shared-id", "approve", 2),
        ("wrong-id", "reject", 3),
        ("wrong-review-id", "reject", 4),
        ("stale-repair", "reject", 3),
        ("stale-review", "reject", 2),
        ("wrong-review", "reject", 2),
        ("dirty-review", "reject", 2),
        ("mutating-review", "reject", 2),
        ("schema", "approve", 2),
        ("native", "approve", 2),
        ("native-final", "reject", 2),
    ],
)
def test_feedback_identity_schema_and_check_failures_stop(
    session, project_id, tmp_path, monkeypatch, fault, decision, expected_calls
):
    feedback_task(session, project_id)
    calls = feedback_harness(
        session, project_id, tmp_path, monkeypatch, [decision, "approve"], fault=fault
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert len(calls) == expected_calls
    assert len(snapshot.tasks[0].context_json["followups"]) == (2 if expected_calls >= 3 else 1)
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    phase = calls[-1]["phase"]
    assert evidence[phase]["validated_outcome"] is None
    assert evidence[phase]["usage"]["measured"]["total"] in {150, 230}
    if fault == "native-final":
        assert evidence["review"]["result"]["status"] == "repair"
        assert evidence["review"]["error"] == "Required check unavailable"
        assert evidence["repair_count"] == 0


def test_feedback_requeue_preserves_consumed_repair_budget(
    session, project_id, tmp_path, monkeypatch
):
    feedback_task(session, project_id)
    calls = feedback_harness(session, project_id, tmp_path, monkeypatch, ["reject"] * 4)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    assert len(calls) == 8
    session.expire_all()
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    assert len(calls) == 8  # Requeue cannot obtain an unmetered fifth delivery.
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 3
    assert "repair limit" in snapshot.tickets[0].outcome.lower()
    assert len(evidence["attempts"]) == 4
    assert [a["repair_count"] for a in evidence["attempts"]] == [0, 1, 2, 3]
    assert [a["review"]["result"]["status"] for a in evidence["attempts"]] == ["repair"] * 4
    for attempt in evidence["attempts"]:
        for phase in ("delivery", "review"):
            assert attempt[phase]["selected_role"] == f"{phase}_agent"
            assert attempt[phase]["usage"]["measured"]["total"] in {150, 230}
        assert attempt["review"]["validated_outcome"] == "repair"


def test_three_repairs_reach_approval_in_original_sessions(
    session, project_id, tmp_path, monkeypatch
):
    feedback_task(session, project_id)
    calls = feedback_harness(
        session,
        project_id,
        tmp_path,
        monkeypatch,
        ["reject", "reject", "reject", "approve", "approve"],
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "complete"
    assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    evidence = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    attempts = [*evidence["attempts"], evidence]
    assert [attempt["repair_count"] for attempt in attempts] == [0, 1, 2, 3]
    assert "repair_pass_used" not in evidence
    assert len(calls) == 10
    for previous, repaired in pairwise(attempts):
        old_sha = previous["candidate"]["commit_sha"]
        new_sha = repaired["candidate"]["commit_sha"]
        assert repaired["candidate"]["base_commit_sha"] == old_sha
        assert old_sha != new_sha
        assert git(tmp_path, "merge-base", "--is-ancestor", old_sha, new_sha) == ""
        assert repaired["delivery"]["launch"]["resume_thread_id"] == "first-delivery"
        assert repaired["review"]["launch"]["resume_thread_id"] == "first-review"


@pytest.mark.parametrize("legacy", [True, False, None])
def test_pending_legacy_rejection_requeue_consumes_next_repair(
    session, project_id, tmp_path, monkeypatch, legacy
):
    from stackos import task_delivery

    feedback_task(session, project_id)
    consumed = int(bool(legacy))
    decisions = ["reject"] * (consumed + 1)
    calls = feedback_harness(session, project_id, tmp_path, monkeypatch, decisions)
    scheduler = QueuedScheduler()
    # Recreate a stopped pending rejection under the previous effective allowance.
    monkeypatch.setattr(task_delivery, "MAX_REPAIR_PASSES", consumed)
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    assert evidence["review"]["validated_outcome"] == "repair"
    for attempt in [*evidence["attempts"], evidence]:
        attempt.pop("repair_count")
    if legacy is not None:
        evidence["repair_pass_used"] = legacy
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={
            "status": "in-progress",
            "completion_evidence_json": {
                **ticket.completion_evidence_json,
                "automatic_delivery": evidence,
            },
        },
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    monkeypatch.setattr(task_delivery, "MAX_REPAIR_PASSES", 3)
    decisions.extend(["approve", "approve"])
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    final = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert final.status == "complete"
    automatic = final.completion_evidence_json["automatic_delivery"]
    assert automatic["repair_count"] == consumed + 1
    assert automatic.get("repair_pass_used") is legacy
    assert automatic["delivery"]["launch"]["resume_thread_id"] == "first-delivery"
    assert automatic["review"]["launch"]["resume_thread_id"] == "first-review"
    assert len(calls) == (consumed + 1) * 2 + 4


@pytest.mark.parametrize("fault", ["native", "blocked", "candidate"])
def test_failed_repair_resumes_same_counted_pass(session, project_id, tmp_path, monkeypatch, fault):
    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    calls = []

    def launch(*args, **selection):
        packet = json.loads(args[3])
        phase = packet["phase"]
        previous = [call for call in calls if call == phase]
        assert selection.get("resume_thread_id") == (f"first-{phase}" if previous else None)
        calls.append(phase)
        with Session(session.get_bind()) as check:
            durable = TrackerRepository(check).brief(project_id=project_id, ticket_key="first")
            count = durable.ticket.completion_evidence_json["automatic_delivery"]["repair_count"]
        assert count == (0 if len(calls) <= 2 else 1)
        if len(calls) == 3:
            if fault == "native":
                return scenario_result(1, thread_id="first-delivery", error="Native unavailable")
            return scenario_result(
                0,
                thread_id="first-delivery",
                result=generic_result(
                    "blocked" if fault == "blocked" else "success",
                    {"commit_sha": git(tmp_path, "rev-parse", "HEAD")},
                    "Repair interrupted",
                ),
            )
        sha = (
            commit(tmp_path, f"candidate-{len(calls)}")
            if phase == "delivery"
            else git(tmp_path, "rev-parse", "HEAD")
        )
        status = "repair" if len(calls) == 2 else "success"
        return scenario_result(
            0, thread_id=f"first-{phase}", result=generic_result(status, {"commit_sha": sha})
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    stopped = repo.get(project_id=project_id, task_key="deliver")
    assert stopped.tickets[0].status == "failed"
    assert stopped.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert calls == ["delivery", "review", "delivery"]
    evidence = stopped.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 1
    assert evidence["delivery"]["validated_outcome"] == ("blocked" if fault == "blocked" else None)
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    final = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert final.status == "complete"
    evidence = final.completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 1
    assert [attempt["repair_count"] for attempt in evidence["attempts"]] == [0, 1]
    assert calls == ["delivery", "review", "delivery", "delivery", "review"]


@pytest.mark.parametrize(
    "baseline_kind", ["compatible", "legacy", "operator_reset", "missing", "invocation"]
)
def test_cumulative_receipt_requeue_retains_unknown_and_redaction_boundary(
    session, project_id, tmp_path, monkeypatch, baseline_kind
):
    """Actual tracker writes/readbacks/archive, with a resumed baseline gap."""
    from stackos import task_delivery

    prepared_task(session, project_id)
    calls = []

    def launch(_host, _directory, _instructions, prompt, schema, **kwargs):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, phase))
        resumed = "resume_thread_id" in kwargs
        return AgentRunResult(
            0,
            thread_id=f"{key}-{phase}",
            result=generic_result("blocked" if len(calls) == 2 else "success"),
            usage=usage_snapshot(
                "codex.turn",
                "session",
                36895 if resumed else 18419,
                30 if resumed else 23,
                cache_read=25472 if resumed else 7168,
                cache_write=0,
                reasoning=14,
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    evidence = repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json
    automatic = evidence["automatic_delivery"]
    for phase in ("delivery", "review"):
        assert automatic[phase]["usage"]["measured"] == {
            "input": 18419,
            "output": 23,
            "total": 18442,
            "cache_read": 7168,
            "cache_write": 0,
            "reasoning": 14,
        }
        if baseline_kind in {"legacy", "operator_reset"}:
            for key in ("cache_read", "cache_write", "reasoning"):
                automatic[phase]["usage"]["snapshot"].pop(key)
            if baseline_kind == "operator_reset":
                automatic[phase]["usage"].update(
                    status="unavailable",
                    reason="operator_reset",
                    measured=None,
                )
        elif baseline_kind == "missing":
            automatic[phase].pop("usage")
        elif baseline_kind == "invocation":
            # The former collector mislabeled cumulative snapshots. Even the
            # same scope/session cannot make that receipt a compatible baseline.
            automatic[phase]["usage"]["basis"] = "invocation"
    evidence["credential_token"] = "must-stay-secret"
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"completion_evidence_json": evidence, "status": "in-progress"},
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    evidence = repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json
    assert evidence["credential_token"] == "[redacted]"
    automatic = evidence["automatic_delivery"]
    assert automatic["sessions"]["delivery"]["thread_id"] == "first-delivery"
    assert len(automatic["attempts"]) == 1
    for phase in ("delivery", "review"):
        receipt = automatic[phase]
        assert receipt["validated_outcome"] == "success"
        assert receipt["usage"]["snapshot"]["total"] == 36925
        assert receipt["usage"]["measured"] == (
            {
                "input": 18476,
                "output": 7,
                "total": 18483,
                "cache_read": 18304 if baseline_kind == "compatible" else None,
                "cache_write": 0 if baseline_kind == "compatible" else None,
                "reasoning": 0 if baseline_kind == "compatible" else None,
            }
            if baseline_kind in {"compatible", "legacy", "operator_reset"}
            else None
        )
        if baseline_kind in {"missing", "invocation"}:
            assert receipt["usage"]["reason"] == "missing_baseline"
        old = automatic["attempts"][0][phase]
        assert (
            ("usage" not in old)
            if baseline_kind == "missing"
            else old["usage"]["snapshot"]["total"] == 18442
        )


@pytest.mark.parametrize(
    "host,fault",
    [(host, fault) for host in ("codex", "grok") for fault in ("text", "schema", "native")]
    + [("codex", "native-success"), ("codex", "native-blocked")],
)
def test_native_collector_failure_persists_session_and_available_usage(
    session, project_id, tmp_path, monkeypatch, host, fault
):
    """Actual native adapter -> normalizer -> tracker receipt on failed output."""
    from stackos.host_mcp import progress
    from stackos.host_mcp.adapters import codex, grok

    prepared_task(session, project_id)
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fake/grok")
    real_run = subprocess.run
    calls = []
    native_failure = fault.startswith("native")
    final = (
        generic_result(
            "blocked" if fault == "native-blocked" else "success", summary="Available final"
        )
        if fault in {"native-success", "native-blocked"}
        else None
    )

    def execute(command, **kwargs):
        if command[0] not in {"/fake/codex", "/fake/grok"}:
            return real_run(command, **kwargs)
        calls.append(command)
        text = json.dumps(final) if final else ("bad" if fault == "text" else "{}")
        if host == "codex":
            Path(command[command.index("-o") + 1]).write_text(text)
            events = [
                {"type": "thread.started", "thread_id": "native-session"},
                {"type": "turn.completed", "usage": {"input_tokens": 18419, "output_tokens": 23}},
            ]
            if native_failure:
                events.append({"type": "turn.failed", "error": "denied"})
            stdout = "\n".join(map(json.dumps, events))
        else:
            stdout = json.dumps(
                {
                    "sessionId": "native-session",
                    "stopReason": "end_turn",
                    "text": text,
                    "error": "denied" if native_failure else None,
                    "usage": {
                        "input_tokens": 6820,
                        "cache_read_input_tokens": 8832,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 57,
                        "reasoning_tokens": 52,
                        "total_tokens": 15709,
                    },
                }
            )
        return subprocess.CompletedProcess(command, int(native_failure), stdout, "")

    monkeypatch.setattr(subprocess, "run", execute)

    def stream(command, *, on_session, **kwargs):
        on_session("native-session")
        return execute(command, **kwargs)

    monkeypatch.setattr(progress, "run_codex_process", stream)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler, host_key=host)
    scheduler.run()
    session.expire_all()
    ticket = TrackerRepository(session).brief(project_id=project_id, ticket_key="first").ticket
    assert ticket.status == "failed"
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    receipt = evidence["delivery"]
    assert receipt["thread_id"] == "native-session"
    assert receipt["error"]
    assert receipt["result"] == final
    if native_failure:
        assert "denied" in receipt["error"]
    if fault == "native" and host == "codex":
        assert "Invalid" in receipt["error"]
    assert receipt["validated_outcome"] is None
    assert receipt["usage"]["measured"]["total"] == (18442 if host == "codex" else 15709)
    assert "review" not in evidence
    assert evidence["sessions"]["delivery"]["thread_id"] == "native-session"
    assert evidence["attempts"] == []
    assert ticket.agent_stats.launch_attempts == 1
    assert ticket.agent_stats.reported_usage.total == receipt["usage"]["measured"]["total"]
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["read", "decode"])
@pytest.mark.parametrize("final_status", [None, "blocked", "success"])
def test_native_stream_exception_persists_primary_final_without_replay(
    session, project_id, tmp_path, tmp_path_factory, monkeypatch, fault, final_status
):
    """The real stream decoder and an injected read failure reach the same receipt gate."""
    from stackos.host_mcp import progress
    from stackos.host_mcp.adapters import codex

    prepared_task(session, project_id)
    final = (
        generic_result(final_status, summary="Available structured final")
        if final_status is not None
        else {}
    )
    cli = tmp_path_factory.mktemp("stream-cli") / "codex"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"Path(sys.argv[sys.argv.index('-o') + 1]).write_text({json.dumps(final)!r})\n"
        "print(json.dumps({'type':'thread.started','thread_id':'original-session'}), flush=True)\n"
        "print(json.dumps({'type':'turn.completed','usage':"
        "{'input_tokens':100,'output_tokens':50}}), flush=True)\n"
        "sys.stderr.buffer.write(b'\\xff')\n"
    )
    cli.chmod(0o755)
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: str(cli))
    real_stream = progress.run_codex_process
    calls = []

    def stream(command, *, on_session, **kwargs):
        calls.append(command)
        if fault == "read":
            Path(command[command.index("-o") + 1]).write_text(json.dumps(final))
            on_session("original-session")
            raise OSError("native stdout read failed after final write")
        return real_stream(command, on_session=on_session, **kwargs)

    monkeypatch.setattr(progress, "run_codex_process", stream)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    ticket = snapshot.tickets[0]
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    receipt = evidence["delivery"]
    assert receipt["result"] == (final if final_status is not None else None)
    assert receipt["thread_id"] == "original-session"
    assert ("OSError" if fault == "read" else "UnicodeDecodeError") in receipt["error"]
    assert "Could not launch" not in receipt["error"]
    if final_status is None:
        assert "Invalid Codex result" in receipt["error"]
    assert receipt["validated_outcome"] is None
    assert evidence["sessions"]["delivery"]["thread_id"] == "original-session"
    assert evidence["attempts"] == [] and evidence["repair_count"] == 0
    assert "review" not in evidence
    assert len(calls) == ticket.agent_stats.launch_attempts == 1
    if fault == "decode":
        assert isinstance(receipt["exit_code"], int)
        assert receipt["usage"]["measured"]["total"] == 150
        assert ticket.agent_stats.reported_usage.total == 150
    else:
        assert receipt["exit_code"] is None
        assert receipt["usage"]["measured"] is None
