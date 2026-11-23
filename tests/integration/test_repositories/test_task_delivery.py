"""The fixed delivery loop uses real tracker state and a controlled harness."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from threading import Barrier, Event, RLock
from typing import Any

import pytest
from pydantic import ValidationError as SchemaValidationError
from sqlmodel import Session, select

from stackos.context import ContextRepository
from stackos.db.connection import make_engine
from stackos.db.models import RunPlanStepStatus, TrackerRevision
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
from tests.helpers.native_task_routes import (
    controlled_profile_host as controlled_profile_host,
)
from tests.helpers.native_task_routes import write_native_route
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


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
                assert ".codex" not in packet["assignment"]
                assert "commit" not in packet["assignment"]
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
@pytest.mark.parametrize("candidate_result", ["observe", "source", "omitted", "stale", "dirty"])
def test_mirror_candidate_evidence_is_independent_of_selected_role(
    session, project_id, tmp_path, monkeypatch, role, candidate_result
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
        nonlocal head
        packet = json.loads(args[3])
        calls.append(packet)
        assert "Do not edit source" not in packet["assignment"]
        if candidate_result in {"source", "dirty"}:
            head = commit(tmp_path, "assigned-source-change")
        elif candidate_result == "stale":
            commit(tmp_path, "new-current-head")
        if candidate_result == "dirty":
            (tmp_path / "unfinished").write_text("unfinished work")
        return AgentRunResult(
            0,
            thread_id="mirror-observation",
            result=generic_result(
                outputs={} if candidate_result == "omitted" else {"commit_sha": head}
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
        step_id="observe",
    )
    scheduler.run()
    session.expire_all()
    assert len(calls) == 1
    assert calls[0]["phase"] == role.removesuffix("_agent")
    final = plans.get(plan.id)
    if candidate_result in {"omitted", "stale", "dirty"}:
        snapshot = TrackerRepository(session).get(
            project_id=project_id, task_key=f"workflow-{plan.id}"
        )
        assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
        assert final.steps[0].status in {"failed", "blocked"}
        assert final.steps[0].error
        return
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
@pytest.mark.parametrize("phase", ["delivery", "review"])
@pytest.mark.parametrize("resumed", [False, True])
def test_workflow_prompt_projects_history_by_assignment_scope(integration, phase, resumed):
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
        "phase": phase,
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

    # Full retained telemetry is UI evidence, never added to any launch path.
    points = [
        {"at": f"MEASUREMENT-{i}", "input": i, "output": i, "total": i * 2} for i in range(120)
    ]
    for item in [
        current,
        prerequisite,
        external,
        older,
        child,
        bound,
        unrelated,
        value["brief"]["task"],
    ]:
        item["last_execution"] = {"agent": "LAST-IDENTITY"}
        item["agent_stats"] = {"samples": points, "last_execution": {"agent": "LAST-IDENTITY"}}
    for role in ("delivery_result", "review_result"):
        value[role]["execution"] = {"live_usage": {"samples": points}}
        value[role]["last_execution"] = {"agent": "LAST-IDENTITY"}
    if resumed:
        # Native session selectors remain, independently of telemetry size.
        value["delivery_result"]["launch"] = {"resume_thread_id": "EXACT-SESSION"}
        packet = task_delivery._work_prompt_copy(
            {
                **original,
                "delivery_result": {
                    **original["delivery_result"],
                    "launch": {"resume_thread_id": "EXACT-SESSION"},
                },
            }
        )
    assert task_delivery._work_prompt_copy(value) == packet


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
    from hashlib import sha256

    from stackos import task_delivery

    # The complete source is the expectation, independently of prompt projection.
    # Its size and Unicode make accidental summary/truncation or byte changes visible.
    source_body = "\n\n".join(
        f"## Finding {index}: source coverage\n"
        "Observed: the reader needs the original evidence, its scope and uncertainty. "
        "Keep the complete explanation and design rationale, including naïve joins, "
        "dates, excluded populations and the next validation. A summary alone does "
        "not preserve this substantive report."
        for index in range(48)
    )
    source_hash = sha256(source_body.encode("utf-8")).hexdigest()
    domain = {
        "report": {
            "body": source_body,
            "body_sha256": source_hash,
            "followups": [{"text": "Domain next action", "evidence_refs": ["source:48"]}],
        }
    }
    summary = 'Required report.followups must retain {"followups": []} in its schema.'
    expected = {
        "report": {
            "required": True,
            "type": "object",
            "schema_json": {
                "type": "object",
                "required": ["body", "body_sha256", "followups"],
                "properties": {
                    "body": {"type": "string"},
                    "body_sha256": {"type": "string"},
                    "followups": {"type": "array", "items": {"type": "object"}},
                },
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
    # The mirror brief is navigation; workflow output recovery uses the step owner.
    assert recovered.ticket.run_plan_id == plan.id
    assert "result_json" not in recovered.ticket.model_dump()
    full_step = plans.get_step(plan.id, "collect", project_id=project_id, full_context=True)
    assert full_step.result_json == domain
    for result in (
        json.loads(receipt["outputs"]),
        json.loads(
            recovered.ticket.completion_evidence_json["automatic_delivery"]["delivery"]["result"][
                "outputs"
            ]
        ),
        full_step.result_json,
        plans.get_step(plan.id, "report", project_id=project_id, full_context=True).result_json,
    ):
        assert result["report"]["body"] == source_body
        assert sha256(result["report"]["body"].encode("utf-8")).hexdigest() == source_hash
        assert result["report"]["followups"] == domain["report"]["followups"]
    with pytest.raises(NotFoundError):
        plans.get_step(plan.id, "missing-step", project_id=project_id)


@pytest.mark.parametrize("case", ["blocker", "non-pending", "containment"])
def test_unfinished_nonready_work_stops_without_launch(
    session, project_id, tmp_path, monkeypatch, case
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    # Readiness can change after an authorized Todo admission, before its callback.
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"blocker_reason": "Missing approved input"}
        if case == "blocker"
        else {"status": "in-progress"},
    )
    if case == "containment":
        repo.create_ticket(
            project_id=project_id, task_key="deliver", key="group", title="Group", kind="group"
        )
        repo.update_ticket(
            project_id=project_id, ticket_key="first", patch_json={"parent_ticket_key": "group"}
        )
    monkeypatch.setattr(
        task_delivery, "run_agent", lambda *a, **kw: pytest.fail("Blocked work launched")
    )
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key="deliver")
    assert repo.brief(project_id=project_id, ticket_key="first").ticket.status == (
        "not-started" if case == "blocker" else "in-progress"
    )
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    context = snapshot.tasks[0].delivery.stop_context
    assert context.code == "no_ready_work"
    assert context.facts == (
        ["explicit_blocker", "dependency_wait"]
        if case == "blocker"
        else ["dependency_wait", "non_pending_work"]
        + (["containment_wait"] if case == "containment" else [])
    )
    assert context.next_action == "reconcile_prerequisites"
    light = repo.get(
        project_id=project_id, task_key="deliver", projection="ui", include_graph=False
    )
    assert light.tasks[0].delivery == snapshot.tasks[0].delivery
    activity = repo.task_activity(project_id=project_id, task_key="deliver")
    assert any(item.details.get("stop_context") == context.model_dump() for item in activity.items)


def test_unknown_no_ready_exception_stays_unknown(session, project_id, tmp_path, monkeypatch):
    from stackos import task_delivery

    prepared_task(session, project_id)

    def unknown(*args, **kwargs):
        raise RuntimeError("Unrecognized failure")

    monkeypatch.setattr(task_delivery, "_select_work", unknown)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    full = repo.get(project_id=project_id, task_key="deliver").tasks[0]
    assert full.delivery.stop_context.code == "unknown"
    assert full.delivery.stop_context.phase is None
    assert full.delivery.stop_context.evidence_refs == []
    light = repo.get(project_id=project_id, task_key="deliver", projection="ui").tasks[0]
    assert full.delivery == light.delivery


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
    if disposition in {"invalid-output", "invalid-json", "native"}:
        assert snapshot.tasks[0].delivery.stop_context.code == (
            "native_failure" if disposition == "native" else "output_validation"
        )
    final = plans.get(plan.id)
    assert final.steps[0].status in {"blocked", "failed"}
    assert final.steps[1].status != "success"
    assert "repair_count" not in snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    receipt = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]["review"]
    assert receipt["selected_role"] == "review_agent"
    assert receipt["validated_outcome"] == (
        disposition if disposition in {"repair", "blocked", "failed"} else None
    )


@pytest.mark.parametrize("phase", ["delivery", "review"])
@pytest.mark.parametrize("denial", ["raw_secret", "other_validation"])
def test_secret_record_validation_preserves_only_safe_bounded_context(
    session, project_id, tmp_path, monkeypatch, caplog, phase, denial
):
    from stackos import task_delivery
    from stackos.db.models import ProjectEvent, TrackerTicket

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "key": "secret-denial",
            "title": "Secret denial",
            "steps": [
                {
                    "id": "observe",
                    "title": "Observe",
                    "metadata_json": {"agent_assignment": f"{phase}_agent"},
                }
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="observe")
    key_sentinel, value_sentinel = "KEY_SENTINEL_7f9d", "VALUE_SENTINEL_1c2e"
    outputs = {
        "result_json": {
            "api_key": value_sentinel,
            key_sentinel: {"password": value_sentinel},
            key_sentinel * 1000: {"password": value_sentinel},
            "nested": [{"token": value_sentinel} for _ in range(20)],
        }
    }
    if denial == "other_validation":
        outputs = {}
        original_record = RunPlanRepository.record_step

        def reject_other_validation(owner, **kwargs):
            if kwargs.get("result_json") is not None:
                raise ValidationError(
                    "Safe generic validation failure",
                    data={"rule": "raw_secret", "paths": [key_sentinel], "raw": value_sentinel},
                )
            return original_record(owner, **kwargs)

        monkeypatch.setattr(RunPlanRepository, "record_step", reject_other_validation)
    calls = []

    def launch(*args, **kwargs):
        calls.append(json.loads(args[3])["phase"])
        return AgentRunResult(
            0, thread_id="secret-denial-session", result=generic_result(outputs=outputs)
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    # Inspect every receipt write, including evidence saved before record_step.
    writes = []
    original_update = TrackerRepository.update_ticket

    def capture_write(repo, **kwargs):
        writes.append(deepcopy(kwargs["patch_json"]))
        return original_update(repo, **kwargs)

    monkeypatch.setattr(TrackerRepository, "update_ticket", capture_write)
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
    repo = TrackerRepository(session)
    snapshot = repo.get(project_id=project_id, task_key=f"workflow-{plan.id}")
    ticket = repo.brief(project_id=project_id, ticket_key=f"workflow-{plan.id}-observe").ticket
    receipt = ticket.completion_evidence_json["automatic_delivery"][phase]
    if denial == "raw_secret":
        context = receipt["validation_context"]
        assert context["rule"] == "raw_secret"
        assert context["operation"] == "runPlan.recordStep"
        assert context["phase"] == phase
        assert "$.result_json.api_key" in context["paths"]
        assert len(context["paths"]) <= 8
        assert len(json.dumps(context)) <= 2300
        assert "must not contain secrets" in receipt["validation_error"]
    else:
        # Even spoofed path/rule fields on another error are never forwarded.
        assert "validation_context" not in receipt
    assert calls == [phase]
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert plans.get_step(plan.id, "observe").status == RunPlanStepStatus.BLOCKED
    assert plans.get_step(plan.id, "observe").result_json is None
    assert receipt["validated_outcome"] is None
    assert receipt["thread_id"] == "secret-denial-session"
    public = json.dumps(
        {
            "writes": writes,
            "snapshot": snapshot.model_dump(mode="json"),
            "brief": ticket.model_dump(mode="json"),
            "stored": [row.model_dump(mode="json") for row in session.exec(select(TrackerTicket))],
            "events": [row.model_dump(mode="json") for row in session.exec(select(ProjectEvent))],
            "logs": caplog.text,
        }
    )
    assert key_sentinel not in public
    assert value_sentinel not in public


@pytest.mark.parametrize("tree_kind", ["non-git", "unborn", "committed"])
@pytest.mark.parametrize("phase", ["delivery", "review"])
def test_report_correction_resumes_once_without_replaying_accepted_work(
    session, project_id, tmp_path, monkeypatch, phase, tree_kind
):
    from stackos import task_delivery
    from stackos.repositories.tracker.agent_stats import invocation_receipts, usage_refresh
    from stackos.repositories.tracker.schema import TrackerMetricDemand

    if tree_kind != "committed":
        shutil.rmtree(tmp_path / ".git")
        if tree_kind == "unborn":
            git(tmp_path, "init")
    candidate = (
        {"commit_sha": commit(tmp_path, "accepted-source")} if tree_kind == "committed" else {}
    )
    before = (tmp_path / "baseline").read_bytes()
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "key": "report-correction",
            "title": "Report correction",
            "steps": [
                {"id": "accepted", "title": "Accepted"},
                {
                    "id": "report",
                    "title": "Report",
                    "depends_on": ["accepted"],
                    "metadata_json": {"agent_assignment": f"{phase}_agent"},
                },
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="accepted")
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="accepted",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"proof_ref": "accepted-proof"},
    )
    predecessor = plans.get_step(plan.id, "accepted").model_dump(mode="json")
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="report")
    task_key = f"workflow-{plan.id}"
    ticket_key = f"{task_key}-report"
    # Exact safe prose retained by the accepted diagnostics predecessor.
    prose = (
        "Local disconnect removes local custody/dispatch. Explicit CFO revoke terminates future "
        "key admissions; rotate any disclosed key. Native0.2.8 intentionally lacks CFO provider "
        "support."
    )
    original = {**candidate, "recovery_plan": {"credentials": prose}}
    corrected = {**candidate, "recovery_plan": {"access_recovery": prose}}
    calls, accepted, writes, native_calls = [], [], [], []
    original_record = RunPlanRepository.record_step
    original_write = TrackerRepository.update_ticket

    def record(owner, **kwargs):
        result = original_record(owner, **kwargs)
        if kwargs["status"] == RunPlanStepStatus.SUCCESS:
            accepted.append(kwargs["step_id"])
        return result

    def capture_write(owner, **kwargs):
        writes.append(deepcopy(kwargs["patch_json"]))
        return original_write(owner, **kwargs)

    def launch(*args, **kwargs):
        from stackos.host_mcp.service import ActiveInvocation

        packet = json.loads(args[3])
        calls.append(packet)
        native = ActiveInvocation(
            invocation_id=f"report-call-{len(calls)}",
            scope=kwargs["execution_scope"],
            host_key=args[0],
            model=None,
            started_at=datetime.now(UTC).isoformat(),
            thread_id="retained-report",
        )
        kwargs["on_execution_change"](native, True)
        kwargs["on_session_attached"](native)
        native_calls.append(native)
        assert packet["phase"] == phase
        if len(calls) == 1:
            assert kwargs.get("resume_thread_id") is None
        else:
            assert len(calls) == 2
            assert kwargs["resume_thread_id"] == "retained-report"
            assert kwargs["previous_usage"]["measured"]["total"] == 110
            assert "report-only" in packet["assignment"]
            assert "No tools" in packet["assignment"]
            with Session(session.get_bind()) as check:
                current = (
                    TrackerRepository(check)
                    .brief(project_id=project_id, ticket_key=ticket_key)
                    .ticket.completion_evidence_json["automatic_delivery"]
                )
                correction = current["reporting_corrections"][-1]
                assert correction["allowance_used"] is True
                assert correction["original_receipt"].get("result") is None
                assert correction["original_receipt"]["result_omitted"] == "raw_secret"
                assert current["attempt"] == 1
            assert plans.get_step(plan.id, "report").status == RunPlanStepStatus.RUNNING
            assert plans.get_step(plan.id, "report").result_json is None
            # Late original completion/cancellation cannot overwrite the new
            # invocation's receipt or fence, even with identical phase/attempt.
            original_call = native_calls[0]
            original_call.cancelled.set()
            kwargs["on_execution_change"](original_call, False)
            task_delivery._record_execution_transition(
                lambda: Session(session.get_bind()), original_call, False
            )
            with Session(session.get_bind()) as check:
                receipt = (
                    TrackerRepository(check)
                    .brief(project_id=project_id, ticket_key=ticket_key)
                    .ticket.completion_evidence_json["automatic_delivery"][phase]
                )
                assert receipt["execution"]["invocation_id"] == native.invocation_id
        native.finished = True
        native.finished_at = datetime.now(UTC).isoformat()
        kwargs["on_execution_change"](native, False)
        return AgentRunResult(
            0,
            thread_id="retained-report",
            usage=usage_snapshot(
                "codex.turn",
                "session",
                100 if len(calls) == 1 else 120,
                10 if len(calls) == 1 else 20,
            ),
            result=generic_result(
                outputs=original if len(calls) == 1 else corrected,
                followups=[{"text": "Unrelated observation"}],
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    monkeypatch.setattr(RunPlanRepository, "record_step", record)
    monkeypatch.setattr(TrackerRepository, "update_ticket", capture_write)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=plan.id,
        step_id="report",
    )
    scheduler.run()
    session.expire_all()
    assert len(calls) == 2, (
        TrackerRepository(session)
        .get(project_id=project_id, task_key=task_key)
        .tasks[0]
        .metadata_json["delivery"]
        .get("reason")
    )
    assert accepted == ["report"]
    assert plans.get_step(plan.id, "report").result_json == corrected
    assert plans.get_step(plan.id, "accepted").model_dump(mode="json") == predecessor
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key=task_key)
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "complete"
    assert len(snapshot.tasks[0].context_json["followups"]) == 1
    evidence = next(t for t in snapshot.tickets if t.key == ticket_key).completion_evidence_json[
        "automatic_delivery"
    ]
    assert "repair_count" not in evidence
    assert evidence["reporting_corrections"][-1]["status"] == "accepted"
    assert evidence["sessions"][phase]["thread_id"] == "retained-report"
    assert (tmp_path / "baseline").read_bytes() == before
    if tree_kind != "committed":
        assert evidence["candidate"] == {}
        assert "commit_sha" not in plans.get_step(plan.id, "report").result_json
        assert task_delivery._baseline(str(tmp_path)) == {}
    receipts = invocation_receipts({"automatic_delivery": evidence})
    assert [r["execution"]["invocation_id"] for r in receipts] == [
        "report-call-1",
        "report-call-2",
    ]
    assert [r["usage"]["measured"]["total"] for r in receipts] == [110, 30]
    # Known copies in reporting history, archive and upgrade observations still
    # describe these same two calls. Exercise actual projection writes/readers.
    copied = deepcopy(evidence)
    copied["attempts"].append({"attempt": 1, phase: deepcopy(evidence[phase])})
    copied["reporting_corrections"].append(deepcopy(copied["reporting_corrections"][0]))
    copied["upgrade_interruptions"] = [
        {"attempt": 1, "phase": phase, "receipt": deepcopy(receipts[0])},
    ]
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id,
        ticket_key=ticket_key,
        patch_json={"completion_evidence_json": {"automatic_delivery": copied}},
    )
    assert len(invocation_receipts({"automatic_delivery": copied})) == 2
    full = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
    assert full.agent_stats.launch_attempts == 2
    assert full.agent_stats.reported_usage.total == 140
    assert full.agent_stats.coverage.usage_measured_attempts == 2
    assert full.work_duration.recorded_intervals == 2
    assert full.work_duration.coverage == "complete"
    expected_seconds = sum(
        (
            datetime.fromisoformat(r["finished_at"])
            - datetime.fromisoformat(r["launch"]["started_at"])
        ).total_seconds()
        for r in receipts
    )
    assert full.work_duration.total_seconds == pytest.approx(expected_seconds)
    demand = TrackerMetricDemand(task_keys=[task_key], ticket_keys=[ticket_key])
    ui = repo.get(
        project_id=project_id,
        task_key=task_key,
        projection="ui",
        metric_demand=demand,
    )
    projected = next(t for t in ui.tickets if t.key == ticket_key)
    refreshed = usage_refresh(session, project_id, None, metric_demand=demand)
    refreshed_ticket = next(t for t in refreshed["tickets"] if t["key"] == ticket_key)
    for field in ("agent_stats", "work_duration"):
        assert getattr(projected, field) == getattr(full, field)
        assert refreshed_ticket[field] == full.model_dump(mode="json")[field]
    # Rejected raw dictionary keys/bodies never enter any receipt write.
    assert '"credentials"' not in json.dumps(writes)
    # A duplicate scheduler delivery after acceptance cannot replay the native work.
    task_delivery.run_delivery(
        session_factory=lambda: Session(session.get_bind()),
        project_id=project_id,
        task_key=task_key,
        handoff_id=snapshot.tasks[0].metadata_json["delivery"]["handoff_id"],
    )
    assert len(calls) == 2 and accepted == ["report"]


def test_report_correction_preparation_failure_preserves_unused_allowance(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "key": "report-preparation",
            "title": "Report preparation",
            "steps": [{"id": "report", "title": "Report"}],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="report")
    task_key, ticket_key = f"workflow-{plan.id}", f"workflow-{plan.id}-report"
    calls = []
    original = task_delivery._reporting_basis

    def unavailable(*args, **kwargs):
        raise ValidationError("Fixture comparison preparation unavailable")

    def launch(*args, **kwargs):
        calls.append(json.loads(args[3]))
        assert len(calls) <= 3
        assert kwargs.get("resume_thread_id") == ("retained-report" if len(calls) > 1 else None)
        key = "access_recovery" if len(calls) == 3 else "credentials"
        return AgentRunResult(
            0,
            thread_id="retained-report",
            result=generic_result(outputs={key: "Use the existing account owner."}),
        )

    monkeypatch.setattr(task_delivery, "_reporting_basis", unavailable)
    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=plan.id,
        step_id="report",
    )
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    stopped = repo.get(project_id=project_id, task_key=task_key).tasks[0].metadata_json["delivery"]
    evidence = repo.brief(
        project_id=project_id, ticket_key=ticket_key
    ).ticket.completion_evidence_json["automatic_delivery"]
    assert stopped["status"] == "stopped"
    assert "Fixture comparison preparation unavailable" in stopped["reason"]
    assert len(calls) == 1
    assert not evidence.get("reporting_corrections")
    assert plans.get_step(plan.id, "report").result_json in (None, {})

    # Only explicit fixture recovery retries the report after preparation is repaired.
    # The failed preparation did not invoke or spend the single label correction.
    monkeypatch.setattr(task_delivery, "_reporting_basis", original)
    plans.reopen(
        run_plan_id=plan.id, project_id=project_id, step_id="report", reason="Fixture repair"
    )
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="report")
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=plan.id,
        step_id="report",
    )
    scheduler.run()
    session.expire_all()
    assert len(calls) == 3
    assert plans.get_step(plan.id, "report").status == RunPlanStepStatus.SUCCESS
    assert (
        repo.get(project_id=project_id, task_key=task_key)
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "complete"
    )
    retained = repo.brief(
        project_id=project_id, ticket_key=ticket_key
    ).ticket.completion_evidence_json["automatic_delivery"]
    assert len(retained["reporting_corrections"]) == 1
    assert retained["reporting_corrections"][0]["status"] == "accepted"
    assert retained["sessions"] == evidence["sessions"]


@pytest.mark.parametrize(
    "failure,phase,tree_kind",
    [
        (failure, "delivery", "committed")
        for failure in (
            "value",
            "late-value",
            "incomplete",
            "unknown",
            "malformed",
            "trailing",
            "duplicate-key",
            "nonfinite",
            "outer",
            "native",
            "missing-session",
            "candidate",
            "blocked",
            "missing-proof",
            "pending-action",
            "second-key",
            "changed-value",
            "changed-summary",
            "changed-proof",
            "changed-candidate",
            "changed-work",
            "changed-state",
            "changed-session",
            "changed-saved-session",
            "changed-saved-candidate",
            "second-native",
            "second-malformed",
            "second-outer",
            "second-blocked",
            "second-interrupted",
        )
    ]
    + [
        (failure, "review", "committed")
        for failure in (
            "second-key",
            "second-native",
            "second-interrupted",
        )
    ]
    + [
        ("changed-state", "delivery", "non-git"),
        ("changed-saved-session", "delivery", "non-git"),
        ("second-interrupted", "delivery", "non-git"),
        ("changed-value", "review", "unborn"),
        ("second-key", "review", "unborn"),
        ("became-committed", "delivery", "unborn"),
    ],
)
def test_report_correction_fails_closed_and_retains_consumed_allowance(
    session, project_id, tmp_path, monkeypatch, failure, phase, tree_kind
):
    from stackos import task_delivery
    from stackos.repositories.run_plans import RunPlanSecretValidationError
    from stackos.repositories.tracker.agent_stats import invocation_receipts

    if tree_kind != "committed":
        shutil.rmtree(tmp_path / ".git")
        if tree_kind == "unborn":
            git(tmp_path, "init")
            git(tmp_path, "config", "user.name", "Delivery Test")
            git(tmp_path, "config", "user.email", "delivery@example.test")
    candidate = {"commit_sha": commit(tmp_path, "source")} if tree_kind == "committed" else {}
    plans = RunPlanRepository(session)
    step = {
        "id": "report",
        "title": "Report",
        "metadata_json": {"agent_assignment": f"{phase}_agent"},
    }
    if failure == "missing-proof":
        step["expected_outputs"] = {"proof": {"type": "object", "required": True}}
    plan = plans.create(
        project_id=project_id,
        run_plan_json={"key": "closed-report", "title": "Closed report", "steps": [step]},
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="report")
    task_key, ticket_key = f"workflow-{plan.id}", f"workflow-{plan.id}-report"
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id,
        ticket_key=ticket_key,
        patch_json={"completion_evidence_json": {"automatic_delivery": {"repair_count": 3}}},
    )
    original = {
        **candidate,
        "credentials": "Use the existing account owner.",
        "proof_ref": "retained-proof",
    }
    corrected = {
        **candidate,
        "access_recovery": original["credentials"],
        "proof_ref": "retained-proof",
    }
    if failure == "value":
        original["credentials"] = "Bearer synthetic-private-material"
    if failure == "late-value":
        original.update({f"password_{i}": "plain" for i in range(8)})
        original["last"] = [{"body": "Bearer synthetic-private-material"}]
    calls = []
    if failure in {"incomplete", "unknown"}:
        original_record = RunPlanRepository.record_step

        def reject(owner, **kwargs):
            if kwargs.get("result_json"):
                scan = task_delivery.find_run_plan_secret_findings(kwargs["result_json"])
                data = {"rule": "raw_secret", "paths": scan.paths}
                if failure == "incomplete":
                    data.update(scan.model_dump(), classification_complete=False)
                raise RunPlanSecretValidationError("Safe secret rejection", data=data)
            return original_record(owner, **kwargs)

        monkeypatch.setattr(RunPlanRepository, "record_step", reject)
    if failure == "pending-action":
        from stackos.db.models import ActionCall, ActionCallStatus

        session.add(
            ActionCall(
                project_id=project_id,
                run_id=started.run_id,
                run_plan_id=plan.id,
                run_plan_step_id=plans.get_step(plan.id, "report").id,
                action_key="fixture.perform",
                plugin_slug="fixture",
                operation="fixture.perform",
                status=ActionCallStatus.RUNNING,
            )
        )
        session.commit()

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        calls.append(packet)
        correcting = len(calls) == 2
        assert len(calls) <= 2
        outputs = deepcopy(corrected if correcting else original)
        thread = "retained-report"
        status, code = "success", 0
        if correcting:
            assert kwargs["resume_thread_id"] == thread
            if failure == "second-interrupted":
                raise RuntimeError("Fixture interruption after allowance commit")
            if failure == "second-key":
                outputs = original
            elif failure == "changed-value":
                outputs["access_recovery"] = "Different facts"
            elif failure == "changed-proof":
                outputs["proof_ref"] = "different-proof"
            elif failure == "changed-candidate":
                outputs["commit_sha"] = "a" * 40
            elif failure == "changed-work":
                (tmp_path / "unexpected-work").write_text("changed")
            elif failure == "became-committed":
                commit(tmp_path, "unexpected-source")
            elif failure == "changed-state":
                with Session(session.get_bind()) as other:
                    TrackerRepository(other).update_ticket(
                        project_id=project_id,
                        ticket_key=ticket_key,
                        patch_json={"context_json": {"new_authority": "changed"}},
                    )
            elif failure == "changed-session":
                thread = "foreign-session"
            elif failure in {"changed-saved-session", "changed-saved-candidate"}:
                with Session(session.get_bind()) as other:
                    owner = TrackerRepository(other)
                    completion = deepcopy(
                        owner.brief(
                            project_id=project_id, ticket_key=ticket_key
                        ).ticket.completion_evidence_json
                    )
                    automatic = completion["automatic_delivery"]
                    if failure == "changed-saved-session":
                        automatic["sessions"][phase]["thread_id"] = "foreign-session"
                    else:
                        automatic["candidate"]["commit_sha"] = "a" * 40
                    owner.update_ticket(
                        project_id=project_id,
                        ticket_key=ticket_key,
                        patch_json={"completion_evidence_json": completion},
                    )
            elif failure == "second-native":
                code = 1
            elif failure == "second-blocked":
                status = "blocked"
        elif failure == "candidate":
            outputs["commit_sha"] = "a" * 40
        elif failure == "native":
            code = 1
        elif failure == "missing-session":
            thread = None
        elif failure == "blocked":
            status = "blocked"
        value = generic_result(status=status, outputs=outputs)
        if failure == "changed-summary" and correcting:
            value["summary"] = "Different claims"
        if failure in ({"second-malformed"} if correcting else {"malformed", "trailing"}):
            value["outputs"] = '{"credentials":"plain"}' + (
                " trailing" if failure == "trailing" else "{"
            )
        if failure == "duplicate-key" and not correcting:
            value["outputs"] = '{"credentials":"plain","credentials":"other"}'
        if failure == "nonfinite" and not correcting:
            value["outputs"] = '{"credentials":NaN}'
        if failure == ("second-outer" if correcting else "outer"):
            value["unexpected"] = True
        return AgentRunResult(
            code,
            thread_id=thread,
            result=value,
            usage=usage_snapshot(
                "codex.turn", "session", 120 if correcting else 100, 20 if correcting else 10
            ),
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
        step_id="report",
    )
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key=task_key)
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert plans.get_step(plan.id, "report").result_json in (None, {})
    assert plans.get_step(plan.id, "report").status in {
        RunPlanStepStatus.BLOCKED,
        RunPlanStepStatus.FAILED,
    }
    evidence = repo.brief(
        project_id=project_id, ticket_key=ticket_key
    ).ticket.completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 3
    correction_expected = (
        failure.startswith("second-")
        or failure.startswith("changed-")
        or failure == "became-committed"
    )
    assert len(calls) == (2 if correction_expected else 1)
    if correction_expected:
        correction = evidence["reporting_corrections"][-1]
        assert correction["allowance_used"] is True
        assert correction["status"] == (
            "consumed" if failure == "second-interrupted" else "rejected"
        )
        assert correction["original_receipt"].get("result") is None
        receipts = invocation_receipts({"automatic_delivery": evidence})
        assert len(receipts) == 2  # The corrected copy cannot count twice, even without IDs.
        assert receipts[0]["usage"]["measured"]["total"] == 110
        # Explicit rehandoff/restarted controller keeps the allowance and stops
        # before any broad native assignment or a third reporting invocation.
        plans.reopen(
            run_plan_id=plan.id,
            project_id=project_id,
            step_id="report",
            reason="Explicit fixture recovery of consumed reporting allowance",
        )
        plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="report")
        submit(
            session,
            project_id,
            tmp_path,
            scheduler,
            task_key=task_key,
            run_plan_id=plan.id,
            step_id="report",
        )
        scheduler.run()
        session.expire_all()
        retained = repo.brief(
            project_id=project_id, ticket_key=ticket_key
        ).ticket.completion_evidence_json["automatic_delivery"]
        assert len(calls) == 2
        assert retained["repair_count"] == 3
        assert retained["reporting_corrections"] == evidence["reporting_corrections"]
        assert retained["sessions"] == evidence["sessions"]
        assert invocation_receipts({"automatic_delivery": retained}) == receipts


@pytest.mark.parametrize("transition", ["reopened", "cancelled", "native_error"])
@pytest.mark.parametrize("secret_output", [True, False])
def test_unvalidated_result_receipt_privacy_preserves_lifetime_evidence(
    session, project_id, tmp_path, monkeypatch, caplog, transition, secret_output
):
    from stackos import task_delivery
    from stackos.db.models import ProjectEvent, TrackerTicket
    from stackos.repositories.base import ConflictError

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "key": "result-receipt-lifetime",
            "title": "Receipt lifetime",
            "steps": [{"id": "observe", "title": "Observe"}],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="observe")
    repo = TrackerRepository(session)
    ticket_key = f"workflow-{plan.id}-observe"
    repo.update_ticket(
        project_id=project_id,
        ticket_key=ticket_key,
        patch_json={"completion_evidence_json": {"prior": {"note": "Retained history"}}},
    )
    key_sentinel, value_sentinel = "PRIVATE_KEY_CONTENT_7a56", "PRIVATE_VALUE_5d42"
    outputs = (
        {key_sentinel: {"api_key": value_sentinel}}
        if secret_output
        else {"note": "After separate authorization: verify the approved environment."}
    )
    final = generic_result(outputs=outputs)
    scheduler = QueuedScheduler()
    calls, writes = [], []
    original_update = TrackerRepository.update_ticket

    def capture_write(owner, **kwargs):
        writes.append(deepcopy(kwargs["patch_json"]))
        return original_update(owner, **kwargs)

    def launch(*args, **kwargs):
        calls.append(json.loads(args[3])["phase"])
        assert calls == ["delivery"]
        with Session(session.get_bind()) as active:
            if transition == "reopened":
                RunPlanRepository(active).reopen(
                    run_plan_id=plan.id,
                    project_id=project_id,
                    step_id="observe",
                    reason="Disposable fixture recovery",
                )
            elif transition == "cancelled":
                with pytest.raises(ConflictError, match="could not be confirmed"):
                    task_delivery.abort_run_plan(
                        active,
                        run_plan_id=plan.id,
                        project_id=project_id,
                        reason="Disposable fixture cancellation",
                        scheduler=scheduler,
                    )
        return AgentRunResult(
            1 if transition == "native_error" else 0,
            thread_id="lifetime-session",
            result=final,
            error="Synthetic native failure" if transition == "native_error" else None,
        )

    monkeypatch.setattr(TrackerRepository, "update_ticket", capture_write)
    monkeypatch.setattr(task_delivery, "run_agent", launch)
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
    ticket = repo.brief(project_id=project_id, ticket_key=ticket_key).ticket
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    receipt = evidence["delivery"]
    assert ticket.completion_evidence_json["prior"] == {"note": "Retained history"}
    assert (
        receipt["thread_id"] == evidence["sessions"]["delivery"]["thread_id"] == "lifetime-session"
    )
    assert receipt["validated_outcome"] is None
    assert evidence.get("repair_count", 0) == 0 and evidence["attempts"] == []
    assert calls == ["delivery"]
    step = plans.get_step(plan.id, "observe")
    assert step.result_json == (
        {
            "summary": "Run plan aborted while this step was running.",
            "reason": "Disposable fixture cancellation",
        }
        if transition == "cancelled"
        else {}
        if transition == "native_error"
        else None
    )
    assert (
        step.status
        == {
            "reopened": RunPlanStepStatus.PENDING,
            "cancelled": RunPlanStepStatus.SKIPPED,
            "native_error": RunPlanStepStatus.FAILED,
        }[transition]
    )
    if transition == "reopened":
        assert receipt["superseded"] is True
    elif transition == "cancelled":
        assert receipt["cancelled"] is True
    else:
        assert receipt["exit_code"] == 1 and receipt["error"] == "Synthetic native failure"
    if secret_output:
        assert receipt.get("result") is None
    else:
        assert receipt["result"] == final
    surfaces = json.dumps(
        {
            "writes": writes,
            "brief": ticket.model_dump(mode="json"),
            "list": repo.get(project_id=project_id, task_key=f"workflow-{plan.id}").model_dump(
                mode="json"
            ),
            "stored": [row.model_dump(mode="json") for row in session.exec(select(TrackerTicket))],
            "events": [row.model_dump(mode="json") for row in session.exec(select(ProjectEvent))],
            "logs": caplog.text,
        }
    )
    assert key_sentinel not in surfaces and value_sentinel not in surfaces


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
    # The completed fixture's queued callback must retire its reservation
    # before an explicit replacement can adopt current settings.
    scheduler.run()
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
    assert len(scheduler.jobs) == 1


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


@pytest.mark.parametrize("bound_only_join", [False, True])
def test_taskwide_nested_fork_join_and_frozen_mirrors(
    session, project_id, tmp_path, monkeypatch, bound_only_join
):
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
            parent_ticket_key=None if key == "join" and bound_only_join else parent,
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
            accepted = {item["key"]: item for item in packet["accepted_receipts"]}
            if key in {root, f"workflow-{plan.id}-inspect", f"workflow-{plan.id}-publish-local"}:
                assert {"left", "right", "join"} <= accepted.keys()
                join = accepted["join"]
                assert (
                    join["completion_evidence_json"]["automatic_delivery"]["review"][
                        "validated_outcome"
                    ]
                    == "success"
                )
                assert join["evidence_ref"] == {
                    "tool_name": "tracker.brief",
                    "arguments": {
                        "project_id": project_id,
                        "ticket_key": "join",
                        "response_mode": "raw",
                    },
                }
            if key == f"workflow-{plan.id}-side":
                assert "join" not in accepted  # This completed branch is outside side's scope.
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


@pytest.fixture(autouse=True)
def native_source_fixtures(monkeypatch, tmp_path):
    """Existing lifecycle scenarios now admit real disposable native definitions.

    Remove only these owned fixture files after admission. Launch must consume the
    frozen snapshot, so source bytes cannot accidentally satisfy a later read.
    """
    from stackos import task_delivery
    from stackos.host_mcp import bridge, service
    from stackos.repositories.workspaces import WorkspaceRepository

    original = task_delivery.start_delivery
    file_lock = RLock()
    users = {}

    def admit(**kwargs):
        session, project_id = kwargs["session"], kwargs["project_id"]
        root = Path(kwargs["project_dir"]).resolve()
        defaults = TrackerRepository(session).delivery_defaults(project_id=project_id)
        owned = []
        for role in ("delivery_agent", "review_agent"):
            selected = {
                **task_delivery._delivery_role_defaults(defaults, role),
                **(kwargs.get("agents") or {}).get(role, {}),
                **(kwargs.get(role) or {}),
            }
            selected_host = selected.get(
                "host_key", kwargs.get("host_key") or defaults.get("host_key") or "codex"
            )
            if selected_host in {"claude-code", "antigravity", "grok"}:
                prior_metadata = (
                    TrackerRepository(session)
                    .get(project_id=project_id, task_key=kwargs["task_key"])
                    .tasks[0]
                    .metadata_json
                    or {}
                )
                prior_route = prior_metadata.get("delivery", {}).get("mcp_config_ref")
                if not kwargs.get("mcp_config_ref") and not prior_route:
                    # Lifecycle scenarios need real native configuration too. Keep
                    # it in their owned temporary home, never the operator's home.
                    home = tmp_path.parent / f"{tmp_path.name}-native-home"
                    monkeypatch.setattr(service, "default_home", lambda home=home: home)
                    monkeypatch.setattr(bridge, "default_home", lambda home=home: home)
                    monkeypatch.delenv("GROK_HOME", raising=False)
                    monkeypatch.delenv("HERMES_HOME", raising=False)
                    write_native_route(home, selected_host)
                    bindings = WorkspaceRepository(session).list_bindings()
                    if not any(
                        b.last_known_root and Path(b.last_known_root).resolve() == root
                        for b in bindings
                    ):
                        WorkspaceRepository(session).connect(
                            project_id=project_id,
                            last_known_root=str(root),
                            repo_fingerprint=f"fixture:{root}",
                        )
                continue
            if selected.get(
                "host_key", kwargs.get("host_key") or defaults.get("host_key") or "codex"
            ) != "codex" or selected.get("source_ref"):
                continue
            if not selected.get("name") or not selected.get("instructions"):
                continue
            folder = root / ".codex" / "agents"
            if not root.is_dir():
                continue
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{role}.toml"
            content = (
                f'name={json.dumps(selected["name"])}\ndescription="Fixture"\n'
                f"developer_instructions={json.dumps(selected['instructions'])}\n"
            )
            with file_lock:
                if path in users:
                    assert path.read_text() == content
                else:
                    assert not path.exists()
                    path.write_text(content)
                users[path] = users.get(path, 0) + 1
                owned.append(path)
                WorkspaceRepository(session).connect(
                    project_id=project_id,
                    last_known_root=str(root),
                    repo_fingerprint=f"fixture:{root}",
                )
            selected.pop("instructions")
            selected["source_ref"] = {
                "host_key": "codex",
                "native_name": selected["name"],
                "source_path": str(path.relative_to(root)),
            }
            kwargs[role] = selected
        try:
            return original(**kwargs)
        finally:
            for path in owned:
                with file_lock:
                    users[path] -= 1
                    if not users[path]:
                        path.unlink()
                        del users[path]

    monkeypatch.setattr(task_delivery, "start_delivery", admit)


class QueuedScheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, dict[str, Any]]] = []
        self._executors = {}
        self._executors_lock = RLock()

    def add_executor(self, executor, *, alias):
        assert alias not in self._executors
        self._executors[alias] = executor

    def add_job(self, function, **options) -> None:
        assert options["jobstore"] == "memory"
        assert options["executor"] == f"task-delivery:{options['kwargs']['project_id']}"
        assert options["executor"] in self._executors
        self.jobs.append((function, options))

    def run(self) -> None:
        function, options = self.jobs.pop(0)
        function(**options["kwargs"])

    def remove_job(self, job_id, *, jobstore):
        from apscheduler.jobstores.base import JobLookupError

        assert jobstore == "memory"
        for index, (_, options) in enumerate(self.jobs):
            if options["id"] == job_id:
                self.jobs.pop(index)
                return
        raise JobLookupError(job_id)


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


def use_codex_native(monkeypatch, native):
    """Keep the real host/controller boundary; replace only Codex's native edge."""
    from dataclasses import replace

    from stackos import task_delivery
    from stackos.host_mcp import service

    lookup = service._definition_by_key
    definition = lookup("codex")
    assert definition is not None
    monkeypatch.setattr(
        service,
        "_definition_by_key",
        lambda key: replace(definition, run_agent=native) if key == "codex" else lookup(key),
    )
    monkeypatch.setattr(task_delivery, "run_agent", service.run_agent)


@pytest.mark.parametrize("interrupted_phase", ["delivery", "review"])
@pytest.mark.parametrize("host_key", ["codex", "claude-code", "grok"])
def test_account_switch_resumes_same_phase_twice_without_spending_repair_budget(
    native_task_session, project_id, tmp_path, monkeypatch, interrupted_phase, host_key
):
    from types import SimpleNamespace

    from stackos.host_mcp import service
    from stackos.host_mcp.accounts import DesiredAccount
    from stackos.repositories.tracker.agent_stats import _receipt_items

    session = native_task_session
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Account switch")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"completion_evidence_json": {"automatic_delivery": {"repair_count": 2}}},
    )
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler, host_key=host_key)
    selected = DesiredAccount(account_ref="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", revision=1)
    account_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    account_a = selected.account_ref
    ready = [Event(), Event()]
    calls, processes, retained = [], [], []
    phase = None

    def select(_host, ref, revision):
        nonlocal selected
        assert processes[-1].poll() is not None
        selected = DesiredAccount(account_ref=ref, revision=revision + 1)
        return selected

    accounts = SimpleNamespace(
        resolve=lambda _host: SimpleNamespace(desired=selected),
        inventory=lambda: SimpleNamespace(
            hosts={
                host_key: SimpleNamespace(
                    accounts=[
                        SimpleNamespace(account_ref=account_a),
                        SimpleNamespace(account_ref=account_b),
                    ]
                )
            }
        ),
        select=select,
    )

    def native(_dir, _instructions, prompt, _schema, *, on_session, process_control, **kwargs):
        nonlocal phase
        if prompt != "proceed":
            phase = json.loads(prompt)["phase"]
        thread = f"account-{phase}"
        on_session(thread)
        calls.append((phase, prompt, kwargs.get("resume_thread_id"), selected.account_ref))
        targeted = [call for call in calls if call[0] == interrupted_phase]
        if phase == interrupted_phase and len(targeted) <= 2:
            process = process_control.spawn(
                [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"],
                stdout=subprocess.PIPE,
                text=True,
            )
            processes.append(process)
            assert process.stdout.readline().strip() == "ready"
            process.stdout.close()
            ready[len(targeted) - 1].set()
            process.wait(timeout=10)
            return AgentRunResult(
                process.returncode,
                thread_id=thread,
                usage=usage_snapshot(
                    f"{host_key}.turn", "session", 100 * len(targeted), 20 * len(targeted)
                ),
            )
        return AgentRunResult(
            0,
            thread_id=thread,
            result=generic_result(),
            usage=usage_snapshot(f"{host_key}.turn", "session", 300, 60)
            if phase == interrupted_phase
            else None,
        )

    from dataclasses import replace

    from stackos import task_delivery

    lookup = service._definition_by_key
    definition = lookup(host_key)
    monkeypatch.setattr(
        service,
        "_definition_by_key",
        lambda key: (
            replace(definition, run_agent=native, native_models=None)
            if key == host_key
            else lookup(key)
        ),
    )
    monkeypatch.setattr(task_delivery, "run_agent", service.run_agent)
    try:
        with ThreadPoolExecutor() as pool:
            work = pool.submit(scheduler.run)
            for index, target in enumerate((account_b, account_a)):
                assert ready[index].wait(5)
                with Session(session.get_bind()) as reader:
                    evidence = (
                        TrackerRepository(reader)
                        .brief(project_id=project_id, ticket_key="first")
                        .ticket.completion_evidence_json["automatic_delivery"]
                    )
                    retained.append(deepcopy(evidence["sessions"][interrupted_phase]))
                service.select_account(accounts, host_key, target, index + 1)
            work.result(timeout=5)
        session.expire_all()
        ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert ticket.status == "complete", ticket.outcome
        assert evidence["attempt"] == 1 and evidence["repair_count"] == 2
        assert retained[0] == retained[1] == evidence["sessions"][interrupted_phase]
        targeted = [call for call in calls if call[0] == interrupted_phase]
        assert [call[1] for call in targeted[1:]] == ["proceed", "proceed"]
        assert [call[2] for call in targeted] == [
            None,
            f"account-{interrupted_phase}",
            f"account-{interrupted_phase}",
        ]
        assert [call[3] for call in targeted] == [account_a, account_b, account_a]
        receipts = [
            receipt
            for (_, phase, _), receipt in _receipt_items(ticket.completion_evidence_json).items()
            if phase == interrupted_phase
        ]
        assert len(receipts) == 3
        assert len({receipt["execution"]["invocation_id"] for receipt in receipts}) == 3
        assert sum(receipt["usage"]["measured"]["total"] for receipt in receipts) == 360
        assert not service._active_invocations
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        service.shutdown_usage()


def stopped_review_fixture(
    session,
    project_id,
    root,
    monkeypatch,
    defaults=None,
    repair_count=3,
    workflow=False,
    review_status="blocked",
    delivery_role="delivery_agent",
):
    """Real admission/runner, disposable Git/SQLite, controlled native edge only."""
    from hashlib import sha256

    from stackos import task_delivery

    repo = TrackerRepository(session)
    scope, child = {}, {}
    task_key = "replace"
    if workflow:
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "replacement-fixture",
                "title": "Disposable replacement fixture",
                "steps": [{"id": "verify", "title": "Verify"}],
            },
        ).data
        started = plans.start(plan.id, project_id=project_id)
        step = plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="verify").data
        task_key = f"workflow-{plan.id}"
        scope = {"run_plan_id": plan.id, "step_id": "verify"}
        child = {
            "run_plan_id": plan.id,
            "run_plan_step_id": step.id,
            "parent_ticket_key": f"{task_key}-verify",
            "dependency_keys": [f"{task_key}-verify"],
        }
    else:
        repo.create_task(project_id=project_id, key=task_key, title="Replace reviewer")
    if delivery_role != "delivery_agent":
        scope["agent_assignments"] = {"work": delivery_role}
        defaults = {
            "host_key": "claude-code",
            "agents": {
                delivery_role: {"name": "writer", "instructions": "Implement"},
                "review_agent": {"name": "reviewer", "instructions": "Independently verify"},
            },
        }
    repo.create_ticket(project_id=project_id, task_key=task_key, key="work", title="Work", **child)
    repo.configure_delivery(
        project_id=project_id,
        delivery=defaults
        or {
            "host_key": "claude-code",
            "delivery_agent": {"name": "writer", "instructions": "Implement"},
            "review_agent": {"name": "reviewer", "instructions": "Independently verify"},
        },
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="work",
        patch_json={
            "completion_evidence_json": {"automatic_delivery": {"repair_count": repair_count}}
        },
    )
    calls = []

    def launch(_host, directory, _instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        calls.append((packet, kwargs))
        if packet["phase"] == "delivery":
            sha = commit(Path(directory), "implementation")
            return AgentRunResult(
                0, thread_id="writer-thread", result=generic_result(outputs={"commit_sha": sha})
            )
        if review_status == "native-error":
            return AgentRunResult(1, thread_id="retired-reviewer", error="Original reviewer failed")
        return AgentRunResult(
            0,
            thread_id="retired-reviewer",
            result=generic_result(
                "blocked",
                {"commit_sha": packet["candidate"]["commit_sha"]},
                "Required tool unavailable",
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    arguments = dict(
        session=session,
        session_factory=lambda: Session(session.get_bind()),
        scheduler=scheduler,
        project_id=project_id,
        task_key=task_key,
        project_dir=str(root),
        **scope,
    )
    task_delivery.start_delivery(**arguments)
    scheduler.run()
    before = deepcopy(
        repo.brief(project_id=project_id, ticket_key="work").ticket.completion_evidence_json
    )
    automatic = before["automatic_delivery"]
    reviewer = task_delivery.resolve_delivery_settings(
        session, project_id=project_id, project_dir=str(root), role_keys={"review_agent"}
    )["agents"]["review_agent"]
    request = {
        "ticket_key": "work",
        "expected_attempt": automatic["attempt"],
        "expected_review_thread_id": "retired-reviewer",
        "expected_candidate_commit_sha": automatic["candidate"]["commit_sha"],
        "expected_reviewer_snapshot_digest": sha256(
            json.dumps(reviewer, sort_keys=True).encode()
        ).hexdigest(),
        "reason": "Operator authorized fresh independent review after capability diagnosis",
    }
    return arguments, request, before, calls


def test_review_replacement_replay_after_workflow_closeout(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    arguments, request, _before, calls = stopped_review_fixture(
        session, project_id, tmp_path, monkeypatch, workflow=True
    )

    def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        calls.append((packet, kwargs))
        if packet["workflow_step"]:
            return AgentRunResult(0, thread_id="workflow-verifier", result=generic_result())
        assert packet["phase"] == "review"
        return AgentRunResult(
            0,
            thread_id="replacement-reviewer",
            result=generic_result(outputs={"commit_sha": request["expected_candidate_commit_sha"]}),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    receipt = task_delivery.start_delivery(**arguments, replace_review=request)
    arguments["scheduler"].run()
    session.expire_all()
    plan = RunPlanRepository(session).get(arguments["run_plan_id"], project_id=project_id)
    assert plan.status == "completed"
    replay = task_delivery.start_delivery(**arguments, replace_review=request)
    assert replay["review_replacement"] == receipt["review_replacement"]
    assert not arguments["scheduler"].jobs
    assert [packet["phase"] for packet, _ in calls] == ["delivery", "review", "review", "delivery"]


@pytest.mark.parametrize(
    "replacement_status,delivery_role",
    [
        ("success", "delivery_agent"),
        ("blocked", "delivery_agent"),
        ("repair", "delivery_agent"),
        ("success", "builder_role"),
    ],
)
def test_review_replacement_runs_only_review_and_preserves_history(
    session, project_id, tmp_path, monkeypatch, replacement_status, delivery_role
):
    from hashlib import sha256

    from stackos import task_delivery

    arguments, request, before, calls = stopped_review_fixture(
        session, project_id, tmp_path, monkeypatch, delivery_role=delivery_role
    )
    original = before["automatic_delivery"]
    repo = TrackerRepository(session)
    defaults = repo.delivery_defaults(project_id=project_id)
    defaults.get("agents", defaults)["review_agent"]["instructions"] = (
        "Current approved inline reviewer instructions"
    )
    repo.configure_delivery(project_id=project_id, delivery=defaults)
    resolved = task_delivery.resolve_delivery_settings(
        session, project_id=project_id, project_dir=str(tmp_path), role_keys={"review_agent"}
    )["agents"]["review_agent"]
    request["expected_reviewer_snapshot_digest"] = sha256(
        json.dumps(resolved, sort_keys=True).encode()
    ).hexdigest()

    def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        calls.append((packet, kwargs))
        assert packet["phase"] == "review"
        assert "Current approved inline reviewer instructions" in _instructions
        assert "resume_thread_id" not in kwargs
        assert (
            packet["delivery_result"]["result"]["outputs"]
            == original["delivery"]["result"]["outputs"]
        )
        assert packet["candidate"] == original["candidate"]
        assert packet["occurrence"]["repair_count"] == 3
        return AgentRunResult(
            0,
            thread_id="replacement-reviewer",
            result=generic_result(
                replacement_status, {"commit_sha": request["expected_candidate_commit_sha"]}
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    # Use the registered operation handler, including typed request validation.
    inp = TrackerStartDeliveryInput.model_validate(
        {key: arguments[key] for key in ("project_id", "task_key", "project_dir")}
        | {"replace_review": request}
    )
    ctx = MCPContext(
        request_id="replace",
        session=session,
        extras={key: arguments[key] for key in ("scheduler", "session_factory")},
    )
    first = asyncio.run(tracker_start_delivery(inp, ctx, None)).data
    replay = asyncio.run(tracker_start_delivery(inp, ctx, None)).data
    assert first["review_replacement"] == replay["review_replacement"]
    assert len(arguments["scheduler"].jobs) == 1
    arguments["scheduler"].run()
    session.expire_all()
    repo = TrackerRepository(session)
    ticket = repo.brief(project_id=project_id, ticket_key="work").ticket
    current = ticket.completion_evidence_json["automatic_delivery"]
    assert [packet["phase"] for packet, _ in calls] == ["delivery", "review", "review"], (
        repo.get(project_id=project_id, task_key="replace").tasks[0].metadata_json
    )
    assert current["delivery"] == original["delivery"]
    assert current["candidate"] == original["candidate"]
    assert current["repair_count"] == 3
    assert current["sessions"]["delivery"] == original["sessions"]["delivery"]
    assert current["sessions"].get("review", {}).get("thread_id") == "replacement-reviewer", (
        ticket.outcome
    )
    retired = current["review_replacements"][0]
    archived = next(
        attempt
        for attempt in current["attempts"]
        if attempt["attempt"] == retired["target"]["expected_attempt"]
    )
    assert archived["review"] == original["review"]
    assert archived["candidate"] == original["candidate"]
    assert "review" not in retired and "candidate" not in retired
    assert retired["session"] == original["sessions"]["review"]
    assert current["attempts"][-1]["review"] == original["review"]
    assert current["review"]["validated_outcome"] == replacement_status
    assert ticket.status == (
        "complete"
        if replacement_status == "success"
        else "in-progress"
        if replacement_status == "blocked"
        else "failed"
    )
    state = repo.get(project_id=project_id, task_key="replace").tasks[0].metadata_json["delivery"]
    assert state["status"] == ("complete" if replacement_status == "success" else "stopped")
    assert state["agents"]["review_agent"] == original["sessions"]["review"]["agent_snapshot"]
    from stackos.repositories.tracker.agent_stats import _receipt_items

    assert len(_receipt_items(ticket.completion_evidence_json)) == 3
    asyncio.run(tracker_start_delivery(inp, ctx, None))
    assert not arguments["scheduler"].jobs


@pytest.mark.parametrize(
    "thread,valid",
    [("replacement-error", False), ("retired-reviewer", False), ("retired-reviewer", True)],
)
def test_review_replacement_native_failure_stops_without_fallback(
    session, project_id, tmp_path, monkeypatch, thread, valid
):
    from stackos import task_delivery

    arguments, request, before, calls = stopped_review_fixture(
        session, project_id, tmp_path, monkeypatch, review_status="native-error"
    )
    repo = TrackerRepository(session)
    old_handoff = (
        repo.get(project_id=project_id, task_key="replace")
        .tasks[0]
        .metadata_json["delivery"]["handoff_id"]
    )

    def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
        calls.append((json.loads(prompt), kwargs))
        if valid:
            return AgentRunResult(
                0,
                thread_id=thread,
                result=generic_result(
                    outputs={"commit_sha": request["expected_candidate_commit_sha"]}
                ),
            )
        return AgentRunResult(1, thread_id=thread, error="Native replacement failed")

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    task_delivery.start_delivery(**arguments, replace_review=request)
    # A retired scheduler/terminal continuation cannot enter the replacement handoff.
    task_delivery.run_delivery(
        session_factory=arguments["session_factory"],
        project_id=project_id,
        task_key="replace",
        handoff_id=old_handoff,
    )
    assert len(calls) == 2
    arguments["scheduler"].run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="work").ticket
    automatic = ticket.completion_evidence_json["automatic_delivery"]
    assert len(calls) == 3
    assert automatic["delivery"] == before["automatic_delivery"]["delivery"]
    assert automatic["repair_count"] == 3
    assert automatic["review"]["validated_outcome"] is None
    if valid:
        assert "fresh native session" in automatic["review"]["validation_error"]
    else:
        assert automatic["review"]["error"] == "Native replacement failed"
    assert (
        repo.get(project_id=project_id, task_key="replace")
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "stopped"
    )
    assert not arguments["scheduler"].jobs
    if thread == "retired-reviewer":
        assert "review" not in automatic["sessions"]


def test_review_replacement_real_rejection_uses_retained_repair_allowance(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    arguments, request, before, calls = stopped_review_fixture(
        session, project_id, tmp_path, monkeypatch, repair_count=2
    )
    review_calls = 0

    def launch(_host, directory, _instructions, prompt, _schema, **kwargs):
        nonlocal review_calls
        packet = json.loads(prompt)
        calls.append((packet, kwargs))
        if packet["phase"] == "delivery":
            assert kwargs["resume_thread_id"] == "writer-thread"
            assert packet["review_result"]["result"]["status"] == "repair"
            assert packet["occurrence"]["repair_count"] == 3
            sha = commit(Path(directory), "scoped-repair")
            return AgentRunResult(
                0, thread_id="writer-thread", result=generic_result(outputs={"commit_sha": sha})
            )
        review_calls += 1
        assert kwargs.get("resume_thread_id") == (None if review_calls == 1 else "fresh-review")
        return AgentRunResult(
            0,
            thread_id="fresh-review",
            result=generic_result(
                "repair" if review_calls == 1 else "success",
                {"commit_sha": packet["candidate"]["commit_sha"]},
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    task_delivery.start_delivery(**arguments, replace_review=request)
    arguments["scheduler"].run()
    session.expire_all()
    ticket = TrackerRepository(session).brief(project_id=project_id, ticket_key="work").ticket
    current = ticket.completion_evidence_json["automatic_delivery"]
    assert ticket.status == "complete", ticket.outcome
    assert [packet["phase"] for packet, _ in calls] == [
        "delivery",
        "review",
        "review",
        "delivery",
        "review",
    ]
    assert current["repair_count"] == 3
    retired = current["review_replacements"][0]
    archived = next(
        attempt
        for attempt in current["attempts"]
        if attempt["attempt"] == retired["target"]["expected_attempt"]
    )
    assert archived["review"] == before["automatic_delivery"]["review"]
    assert archived["candidate"] == before["automatic_delivery"]["candidate"]
    assert current["candidate"]["commit_sha"] != request["expected_candidate_commit_sha"]


@pytest.mark.parametrize(
    "fault",
    [
        "attempt",
        "thread",
        "snapshot",
        "candidate",
        "dirty",
        "tracked_source",
        "head",
        "live",
        "capacity",
        "registry",
        "accepted",
        "ordinary_rejection",
        "missing_session",
        "missing_snapshot",
        "not_delivered",
        "prerequisite",
        "foreign_ticket",
        "different_request",
    ],
)
def test_review_replacement_rejects_without_mutation(
    session, project_id, tmp_path, monkeypatch, fault
):
    from stackos import task_delivery
    from stackos.host_mcp import service

    arguments, request, before, calls = stopped_review_fixture(
        session, project_id, tmp_path, monkeypatch
    )
    repo = TrackerRepository(session)
    evidence = before["automatic_delivery"]
    if fault in {"attempt", "thread", "snapshot", "candidate"}:
        field, value = {
            "attempt": ("expected_attempt", 99),
            "thread": ("expected_review_thread_id", "stale"),
            "snapshot": ("expected_reviewer_snapshot_digest", "0" * 64),
            "candidate": ("expected_candidate_commit_sha", "0" * 40),
        }[fault]
        request[field] = value
    elif fault in {"dirty", "tracked_source", "head"}:
        if fault == "tracked_source":
            folder = tmp_path / ".codex" / "agents"
            folder.mkdir(parents=True)
            (folder / "reviewer.toml").write_text("changed tracked execution source")
            git(tmp_path, "add", ".codex")
        elif fault == "head":
            commit(tmp_path, "different-head")
        else:
            (tmp_path / "dirty").write_text("dirty")
    elif fault in {"live", "capacity"}:
        task_delivery._update_delivery(
            repo,
            project_id,
            "replace",
            **({"status": "running"} if fault == "live" else {"slot_reserved": True}),
        )
    elif fault == "registry":
        monkeypatch.setattr(
            service,
            "invocation_timings",
            lambda: [{"project_id": project_id, "task_key": "replace", "finished_at": "retiring"}],
        )
    elif fault == "different_request":
        task_delivery.start_delivery(**arguments, replace_review=request)
        request["reason"] = "Different request for an already retired review"
    else:
        if fault in {"accepted", "ordinary_rejection"}:
            evidence["review"]["validated_outcome"] = "success" if fault == "accepted" else "repair"
        elif fault == "missing_session":
            evidence["sessions"].pop("review")
        elif fault == "missing_snapshot":
            evidence["sessions"]["review"].pop("agent_snapshot")
        elif fault == "not_delivered":
            evidence["delivery"]["validated_outcome"] = "blocked"
        elif fault == "prerequisite":
            repo.create_ticket(
                project_id=project_id, task_key="replace", key="prerequisite", title="Pending"
            )
            repo.update_ticket(
                project_id=project_id,
                ticket_key="work",
                patch_json={"add_dependency_keys": ["prerequisite"]},
            )
        elif fault == "foreign_ticket":
            repo.create_task(project_id=project_id, key="other-task", title="Other")
            repo.create_ticket(
                project_id=project_id, task_key="other-task", key="other-work", title="Other"
            )
            request["ticket_key"] = "other-work"
        repo.update_ticket(
            project_id=project_id,
            ticket_key="work",
            patch_json={"completion_evidence_json": before},
        )
    snapshot = repo.get(project_id=project_id, task_key="replace").model_dump(mode="json")
    jobs = len(arguments["scheduler"].jobs)
    with pytest.raises(ValidationError):
        task_delivery.start_delivery(**arguments, replace_review=request)
    session.expire_all()
    assert repo.get(project_id=project_id, task_key="replace").model_dump(mode="json") == snapshot
    assert len(arguments["scheduler"].jobs) == jobs
    assert len(calls) == 2


def test_review_replacement_does_not_implicitly_apply_to_rehandoff(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    arguments, _request, _before, calls = stopped_review_fixture(
        session, project_id, tmp_path, monkeypatch
    )
    # Ordinary explicit requeue still uses its original delivery/reviewer sessions.
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id,
        ticket_key="work",
        patch_json={"status": "not-started", "blocker_reason": None},
    )
    task_delivery.start_delivery(**arguments)
    arguments["scheduler"].run()
    assert calls[2][0]["phase"] == "delivery"
    assert calls[2][1]["resume_thread_id"] == "writer-thread"
    current = repo.brief(project_id=project_id, ticket_key="work").ticket.completion_evidence_json[
        "automatic_delivery"
    ]
    assert "review_replacements" not in current


def test_review_replacement_fences_retired_session_callbacks_and_preserves_metrics(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery
    from stackos.host_mcp import service
    from stackos.repositories.tracker.agent_stats import _receipt_items

    arguments, request, before, _calls = stopped_review_fixture(
        session, project_id, tmp_path, monkeypatch
    )
    task_delivery.start_delivery(**arguments, replace_review=request)
    repo = TrackerRepository(session)
    current = deepcopy(
        repo.brief(project_id=project_id, ticket_key="work").ticket.completion_evidence_json
    )
    old = before["automatic_delivery"]["sessions"]["review"]
    call = service.ActiveInvocation(
        invocation_id="retired-invocation",
        host_key=old["host_key"],
        model=None,
        started_at="2026-11-15T00:00:00+00:00",
        thread_id=old["thread_id"],
        scope=service.AgentExecutionScope(
            project_id=project_id,
            task_key="replace",
            ticket_key="work",
            phase="review",
            attempt=request["expected_attempt"],
            agent=old["agent"],
        ),
    )
    task_delivery._record_execution_transition(arguments["session_factory"], call, False)
    with pytest.raises(ValidationError, match="ownership"):
        task_delivery._record_native_session(
            arguments["session_factory"],
            call,
            handoff_id="retired-handoff",
            evidence=before["automatic_delivery"],
            identity={
                key: value
                for key, value in old.items()
                if key not in {"thread_id", "agent_snapshot"}
            },
            session_snapshot={"agent_snapshot": old["agent_snapshot"]},
        )
    session.expire_all()
    after = repo.brief(project_id=project_id, ticket_key="work").ticket.completion_evidence_json
    assert after == current
    receipts = _receipt_items(after)
    assert len(receipts) == 2
    assert sum(phase == "delivery" for _, phase, _ in receipts) == 1


def test_announced_session_is_durable_before_exit_and_controller_loss_resumes_it(
    session, project_id, tmp_path, monkeypatch
):
    import os

    from stackos import task_delivery
    from stackos.host_mcp import service
    from stackos.host_mcp.progress import run_codex_process

    class ControllerLost(BaseException):
        pass

    engine = make_engine(tmp_path.with_suffix(".db"))
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    with Session(engine) as session:
        prepared_task(session, project_id)
        repo = TrackerRepository(session)
        accepted = {"accepted": {"commit": "unchanged", "review": "independent"}}
        announced = Event()
        release = tmp_path.with_suffix(".exit")
        calls = []

        def native(_dir, _instructions, prompt, _schema, *, on_session, **kwargs):
            packet = json.loads(prompt)
            phase = packet["phase"]
            calls.append((phase, kwargs.get("resume_thread_id")))
            if len(calls) == 1:

                def attached(identity):
                    on_session(identity)
                    announced.set()

                process = run_codex_process(
                    [
                        sys.executable,
                        "-c",
                        "import json,pathlib,sys,time; "
                        "print(json.dumps({'type':'thread.started','thread_id':'second-delivery'}),"
                        "flush=True); "
                        "gate=pathlib.Path(sys.argv[1]); "
                        "exec('while not gate.exists(): time.sleep(.01)')",
                        str(release),
                    ],
                    input="",
                    cwd=tmp_path,
                    env=dict(os.environ),
                    on_session=attached,
                    process_control=kwargs["process_control"],
                )
                assert process.returncode == 0
                raise ControllerLost()
            on_session(f"second-{phase}")
            return AgentRunResult(0, thread_id=f"second-{phase}", result=generic_result())

        scheduler = QueuedScheduler()
        cursor = ContextRepository(session).latest_tracker_status_event_id(project_id=project_id)
        submit(session, project_id, tmp_path, scheduler)
        use_codex_native(monkeypatch, native)
        repo.update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"status": "complete", "completion_evidence_json": accepted},
        )
        repo.update_ticket(
            project_id=project_id,
            ticket_key="second",
            patch_json={"completion_evidence_json": {"automatic_delivery": {"repair_count": 2}}},
        )
        try:
            with ThreadPoolExecutor(1) as executor:
                work = executor.submit(scheduler.run)
                try:
                    assert announced.wait(5)
                    with Session(engine) as reader:
                        evidence = (
                            TrackerRepository(reader)
                            .brief(project_id=project_id, ticket_key="second")
                            .ticket.completion_evidence_json["automatic_delivery"]
                        )
                    retained = evidence["sessions"]["delivery"]
                    assert retained["thread_id"] == "second-delivery"
                    assert retained["agent_snapshot"]["name"] == "project-builder"
                    assert (
                        retained["source_digest"] == evidence["delivery"]["launch"]["source_digest"]
                    )
                    assert evidence["repair_count"] == 2
                    assert "exit_code" not in evidence["delivery"]
                    assert not work.done()
                finally:
                    release.touch()
                with pytest.raises(ControllerLost):
                    work.result(timeout=5)
            # Disposable fixture: explicit operator recovery/rehandoff through
            # the existing owner, without manufacturing a native result.
            task_delivery._update_delivery(
                repo, project_id, "deliver", status="stopped", reason="Controller lost"
            )
            repo.update_ticket(
                project_id=project_id,
                ticket_key="second",
                patch_json={"status": "not-started"},
            )
            submit(session, project_id, tmp_path, scheduler)
            scheduler.run()
            session.expire_all()
            first = repo.brief(project_id=project_id, ticket_key="first").ticket
            second = repo.brief(project_id=project_id, ticket_key="second").ticket
            final = second.completion_evidence_json["automatic_delivery"]
            assert first.completion_evidence_json == accepted and first.status == "complete"
            assert second.status == "complete", (
                repo.get(project_id=project_id, task_key="deliver")
                .tasks[0]
                .metadata_json["delivery"]
                .get("reason"),
                calls,
                second.outcome,
            )
            assert calls == [("delivery", None), ("delivery", "second-delivery"), ("review", None)]
            assert final["sessions"]["delivery"] == retained
            assert final["repair_count"] == 2 and final["attempt"] == 2
            assert final["attempts"][0]["attempt"] == 1
            assert "exit_code" not in final["attempts"][0]["delivery"]
            events = (
                ContextRepository(session)
                .tracker_status_timeline(
                    project_id=project_id, task_key="deliver", after_id=cursor, limit=50
                )
                .items
            )
            assert [
                event.metadata_json["active"]
                for event in events
                if event.event_type == "tracker.execution.changed"
            ] == [True, False] * 3
            assert not service._active_invocations and not service.provisional_usage(project_id)
        finally:
            service.shutdown_usage()
    engine.dispose()


@pytest.mark.parametrize("mismatched_phase", ["delivery", "review"])
def test_terminal_session_mismatch_preserves_announced_identity_for_recovery(
    session, project_id, tmp_path, monkeypatch, mismatched_phase
):
    from stackos.host_mcp import service

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    repo = TrackerRepository(session)
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"completion_evidence_json": {"automatic_delivery": {"repair_count": 2}}},
    )
    calls, announced_sessions = [], {}
    rejected = False

    def native(_dir, _instructions, prompt, _schema, *, on_session, **kwargs):
        nonlocal rejected
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        announced = f"{key}-{phase}-announced"
        previous = (key, phase) in announced_sessions
        assert kwargs.get("resume_thread_id") == (announced if previous else None)
        calls.append((key, phase, kwargs.get("resume_thread_id")))
        on_session(announced)
        with Session(session.get_bind()) as reader:
            early = (
                TrackerRepository(reader)
                .brief(project_id=project_id, ticket_key=key)
                .ticket.completion_evidence_json["automatic_delivery"]["sessions"][phase]
            )
        assert early["thread_id"] == announced
        if previous:
            assert early == announced_sessions[key, phase]
        announced_sessions[key, phase] = early
        mismatch = key == "first" and phase == mismatched_phase and not rejected
        rejected = rejected or mismatch
        return AgentRunResult(
            0, thread_id="foreign-terminal-id" if mismatch else announced, result=generic_result()
        )

    use_codex_native(monkeypatch, native)
    try:
        scheduler.run()
        session.expire_all()
        failed = repo.brief(project_id=project_id, ticket_key="first").ticket
        evidence = failed.completion_evidence_json["automatic_delivery"]
        retained = announced_sessions["first", mismatched_phase]
        assert failed.status == "failed"
        assert evidence[mismatched_phase]["error"] == "Native session identity mismatch"
        assert evidence[mismatched_phase]["thread_id"] == "foreign-terminal-id"
        assert evidence["sessions"][mismatched_phase] == retained
        assert evidence["repair_count"] == 2
        assert (
            repo.get(project_id=project_id, task_key="deliver")
            .tasks[0]
            .metadata_json["delivery"]["status"]
            == "stopped"
        )
        for status in ("in-progress", "not-started"):
            repo.update_ticket(
                project_id=project_id, ticket_key="first", patch_json={"status": status}
            )
        submit(session, project_id, tmp_path, scheduler)
        scheduler.run()
        session.expire_all()
        recovered = repo.brief(project_id=project_id, ticket_key="first").ticket
        final = recovered.completion_evidence_json["automatic_delivery"]
        assert recovered.status == "complete"
        assert final["sessions"][mismatched_phase] == retained
        assert ("first", mismatched_phase, retained["thread_id"]) in calls
        assert final["attempt"] == 2 and final["repair_count"] == 2
        assert final["attempts"][0][mismatched_phase]["thread_id"] == "foreign-terminal-id"
        assert final["attempts"][0][mismatched_phase]["error"] == "Native session identity mismatch"
    finally:
        service.shutdown_usage()


def test_competing_session_rejection_survives_terminal_handling_and_requeue(
    session, project_id, tmp_path, monkeypatch
):
    from datetime import UTC, datetime

    from stackos.host_mcp import service

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    owner = service.ActiveInvocation(
        invocation_id="fixture-other-owner",
        scope=service.AgentExecutionScope(project_id, "unrelated", "other", "other-worker"),
        host_key="codex",
        model=None,
        started_at=datetime.now(UTC).isoformat(),
        session_id="already-owned",
        thread_id="already-owned",
    )
    service._active_invocations[owner.invocation_id] = owner
    calls = []

    def native(_dir, _instructions, _prompt, _schema, *, on_session, **kwargs):
        calls.append(kwargs.get("resume_thread_id"))
        on_session("already-owned")
        with Session(session.get_bind()) as reader:
            early = (
                TrackerRepository(reader)
                .brief(project_id=project_id, ticket_key="first")
                .ticket.completion_evidence_json["automatic_delivery"]
            )
        assert "delivery" not in early["sessions"]
        return AgentRunResult(0, thread_id="already-owned", result=generic_result("blocked"))

    use_codex_native(monkeypatch, native)
    try:
        scheduler.run()
        session.expire_all()
        repo = TrackerRepository(session)
        failed = repo.brief(project_id=project_id, ticket_key="first").ticket
        evidence = failed.completion_evidence_json["automatic_delivery"]
        assert failed.status == "failed"
        assert "delivery" not in evidence["sessions"]
        assert evidence["delivery"]["error"] == "Native session ownership conflict"
        assert evidence["delivery"]["validated_outcome"] is None
        assert service._active_invocations[owner.invocation_id] is owner
        assert owner.thread_id == "already-owned" and not owner.finished
        # An unowned ID cannot authorize exact-session recovery, even after the
        # other owner retires. The existing missing-session stop still applies.
        service._unregister(owner)
        for status in ("in-progress", "not-started"):
            repo.update_ticket(
                project_id=project_id, ticket_key="first", patch_json={"status": status}
            )
        submit(session, project_id, tmp_path, scheduler)
        scheduler.run()
        session.expire_all()
        stopped = repo.brief(project_id=project_id, ticket_key="first").ticket
        assert "missing native session ID" in stopped.outcome
        assert "delivery" not in stopped.completion_evidence_json["automatic_delivery"]["sessions"]
        assert calls == [None]
    finally:
        service._unregister(owner)
        service.shutdown_usage()


def test_task_mcp_route_frozen_through_rehandoff_and_role_resume(
    session, project_id, tmp_path, monkeypatch
):
    import hashlib

    from stackos import task_delivery
    from stackos.host_mcp.adapters import codex

    prepared_task(session, project_id)
    fragment = tmp_path.parent / f"{tmp_path.name}-route.toml"
    fragment.write_text('[mcp_servers.serena]\nenv = {SERENA_PROJECT_NAME = "private-route-value"}')
    ref = {"path": str(fragment), "sha256": hashlib.sha256(fragment.read_bytes()).hexdigest()}
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/native/codex")
    monkeypatch.setattr(
        codex,
        "_run_codex",
        lambda *a, **kw: subprocess.CompletedProcess(
            [],
            0,
            json.dumps([{"name": "serena", "transport": {"type": "stdio", "command": "python3"}}]),
            "",
        ),
    )
    calls = []

    def launch(host, directory, instructions, prompt, schema, **kwargs):
        assert kwargs["mcp_config_ref"] == ref
        assert "private-route-value" not in prompt
        assert "private-route-value" not in instructions
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, phase, kwargs.get("resume_thread_id")))
        return AgentRunResult(
            0,
            thread_id=f"{key}-{phase}",
            result=generic_result(status="blocked" if len(calls) == 1 else "success"),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler, mcp_config_ref=ref)
    scheduler.run()
    session.expire_all()
    for status in ("in-progress", "not-started"):
        TrackerRepository(session).update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"status": status, "blocker_reason": None},
        )
    # Omission keeps the route and resumes the exact delivery session.
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["status"] == "complete", state
    assert calls[:3] == [
        ("first", "delivery", None),
        ("first", "delivery", "first-delivery"),
        ("first", "review", None),
    ]
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["mcp_config_ref"] == ref
    assert "private-route-value" not in json.dumps(state)
    # Requeue an open stopped task to check identity before any scheduler mutation.
    task_delivery._update_delivery(
        TrackerRepository(session), project_id, "deliver", status="stopped"
    )
    changed = {**ref, "sha256": "0" * 64}
    with pytest.raises(ValidationError, match=r"retain.*MCP"):
        submit(session, project_id, tmp_path, scheduler, mcp_config_ref=changed)


def test_task_mcp_route_rejects_unsupported_role_before_queue(session, project_id, tmp_path):
    import hashlib

    prepared_task(session, project_id)
    fragment = tmp_path.parent / f"{tmp_path.name}-route.toml"
    fragment.write_text("[mcp_servers.serena]\nargs = []")
    ref = {"path": str(fragment), "sha256": hashlib.sha256(fragment.read_bytes()).hexdigest()}
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match=r"unsupported|does not support"):
        submit(
            session,
            project_id,
            tmp_path,
            scheduler,
            mcp_config_ref=ref,
            review_agent={"name": "review", "instructions": "review", "host_key": "claude-desktop"},
        )
    assert scheduler.jobs == []


def test_claude_task_mcp_route_frozen_through_registered_rehandoff(
    session, project_id, tmp_path, monkeypatch
):
    import hashlib

    from stackos import task_delivery
    from stackos.host_mcp import bridge, service
    from stackos.repositories.workspaces import WorkspaceRepository

    prepared_task(session, project_id)
    home = tmp_path.parent / f"{tmp_path.name}-native-home"
    home.mkdir()
    monkeypatch.setattr(service, "default_home", lambda: home)
    monkeypatch.setattr(bridge, "default_home", lambda: home)
    command = bridge.resolve_bridge_command(runtime="claude-code", workspace_root=tmp_path)
    config = home / ".claude.json"
    original = json.dumps(
        {
            "mcpServers": {
                "stackos": {
                    "command": command[0],
                    "args": command[1:],
                    "env": {"ROUTE_SENTINEL": "private-route"},
                }
            }
        }
    )
    config.write_text(original)
    WorkspaceRepository(session).connect(
        project_id=project_id, last_known_root=str(tmp_path), repo_fingerprint=f"route:{tmp_path}"
    )
    fragment = home / "route.toml"
    fragment.write_text(f'[mcp_servers.stackos]\ncwd="{tmp_path}"\n')
    ref = {"path": str(fragment), "sha256": hashlib.sha256(fragment.read_bytes()).hexdigest()}
    calls = []
    identities = []

    def launch(host, directory, instructions, prompt, schema, **kwargs):
        packet = json.loads(prompt)
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        assert host == "claude-code" and kwargs["mcp_config_ref"] == ref
        assert instructions.split("\n\n", 1)[0] in {"Build the ticket.", "Review the ticket."}
        assert "private-route" not in prompt
        identities.append(kwargs["mcp_route_identity"])
        calls.append((key, phase, kwargs.get("resume_thread_id")))
        return AgentRunResult(
            0,
            thread_id=f"{key}-{phase}",
            result=generic_result(status="blocked" if len(calls) == 1 else "success"),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler, host_key="claude-code", mcp_config_ref=ref)
    scheduler.run()
    assert calls, (
        TrackerRepository(session).brief(project_id=project_id, ticket_key="first").ticket.outcome
    )
    for status in ("in-progress", "not-started"):
        TrackerRepository(session).update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"status": status, "blocker_reason": None},
        )
    changed = json.loads(original)
    changed["mcpServers"]["stackos"]["env"]["ROUTE_SENTINEL"] = "changed"
    config.write_text(json.dumps(changed))
    with pytest.raises(ValidationError, match="retain its frozen native MCP routes"):
        submit(session, project_id, tmp_path, scheduler, host_key="claude-code")
    assert not scheduler.jobs
    config.write_text(original)
    submit(session, project_id, tmp_path, scheduler, host_key="claude-code")
    scheduler.run()
    assert calls[:3] == [
        ("first", "delivery", None),
        ("first", "delivery", "first-delivery"),
        ("first", "review", None),
    ]
    assert all(identity == identities[0] for identity in identities)
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["status"] == "complete"
    assert state["mcp_route_identities"]["delivery_agent"] == identities[0]
    assert "private-route" not in json.dumps(state)


def test_native_task_mcp_route_rejects_wrong_project_without_admission(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery
    from stackos.host_mcp import bridge, service
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.workspaces import WorkspaceRepository

    prepared_task(session, project_id)
    foreign = (
        ProjectRepository(session)
        .create(slug="foreign-route", name="Foreign", domain="example.test")
        .data.id
    )
    WorkspaceRepository(session).connect(
        project_id=foreign, last_known_root=str(tmp_path), repo_fingerprint=f"foreign:{tmp_path}"
    )
    home = tmp_path.parent / f"{tmp_path.name}-native-home"
    home.mkdir()
    monkeypatch.setattr(service, "default_home", lambda: home)
    monkeypatch.setattr(bridge, "default_home", lambda: home)
    command = bridge.resolve_bridge_command(runtime="claude-code", workspace_root=tmp_path)
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"stackos": {"command": command[0], "args": command[1:]}}})
    )
    before = TrackerRepository(session).get(project_id=project_id, task_key="deliver").model_dump()
    with pytest.raises(ValidationError, match="workspace project binding"):
        task_delivery.resolve_delivery_settings(
            session,
            project_id=project_id,
            project_dir=str(tmp_path),
            host_key="claude-code",
            delivery_agent={"name": "build", "instructions": "Build"},
            review_agent={"name": "review", "instructions": "Review"},
        )
    assert (
        TrackerRepository(session).get(project_id=project_id, task_key="deliver").model_dump()
        == before
    )


def test_native_task_without_route_ref_still_requires_stackos(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery
    from stackos.host_mcp import bridge, service

    monkeypatch.setattr(service, "default_home", lambda: tmp_path)
    monkeypatch.setattr(bridge, "default_home", lambda: tmp_path)
    with pytest.raises(ValidationError, match="existing safe native stdio servers"):
        task_delivery.resolve_delivery_settings(
            session,
            project_id=project_id,
            project_dir=str(tmp_path),
            host_key="claude-code",
            delivery_agent={"name": "build", "instructions": "Build"},
            review_agent={"name": "review", "instructions": "Review"},
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


@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("different_project", [False, True])
def test_active_delivery_rejects_same_directory(
    session, project_id, tmp_path, alias, different_project
):
    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    if different_project:
        from stackos.repositories.projects import ProjectRepository

        project_id = (
            ProjectRepository(session)
            .create(slug="second", name="Second", domain="example.test", locale="en-US")
            .data.id
        )
        TrackerRepository(session).create_task(
            project_id=project_id, key="unrelated", title="Other"
        )
        TrackerRepository(session).create_ticket(
            project_id=project_id, task_key="unrelated", key="other", title="Other"
        )
    directory = tmp_path
    if alias:
        directory = tmp_path.parent / f"{tmp_path.name}-alias"
        directory.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValidationError, match=r"working directory.*active task"):
        submit(
            session,
            project_id,
            directory,
            scheduler,
            task_key="unrelated",
            **({"host_key": "claude-code"} if different_project else {}),
        )
    assert len(scheduler.jobs) == 1
    assert "delivery" not in (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="unrelated")
        .tasks[0]
        .metadata_json
        or {}
    )


@pytest.mark.parametrize("status", ["queued", "stopped"])
def test_delivery_directory_is_retained_on_rehandoff(session, project_id, tmp_path, status):
    from stackos import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    first = submit(session, project_id, tmp_path, scheduler)
    task_delivery._update_delivery(TrackerRepository(session), project_id, "deliver", status=status)
    other = tmp_path / "other-directory"
    other.mkdir()
    with pytest.raises(ValidationError, match=r"retain.*working directory"):
        submit(session, project_id, other, scheduler)
    state = (
        TrackerRepository(session)
        .get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]
    )
    assert state["project_dir"] == str(tmp_path.resolve())
    assert state["job_ref"] == first["job_ref"]
    assert len(scheduler.jobs) == 1


@pytest.fixture
def native_task_session(session, project_id, tmp_path_factory):
    """The real stdout callback commits on a separate thread/connection."""
    engine = make_engine(tmp_path_factory.mktemp("native-task-state") / "state.sqlite")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    try:
        with Session(engine) as reader:
            yield reader
    finally:
        engine.dispose()


@pytest.fixture
def delivery_engine(session, project_id, tmp_path):
    """Independent connections are required for actual concurrent task writers."""
    engine = make_engine(tmp_path / "fixture-state" / "parallel.sqlite")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("same_task", [False, True])
def test_concurrent_handoffs_admit_one_directory_writer(
    delivery_engine, project_id, tmp_path, same_task
):
    with Session(delivery_engine) as session:
        prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    ready = Barrier(2)

    def handoff(key):
        with Session(delivery_engine) as session:
            ready.wait(timeout=10)
            try:
                return submit(session, project_id, tmp_path, scheduler, task_key=key)
            except ValidationError as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(handoff, ["deliver", "deliver" if same_task else "unrelated"]))
    assert len(scheduler.jobs) == 1
    if same_task:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(result, ValidationError) for result in results) == 1


@pytest.mark.parametrize("entity", ["ticket", "task"])
def test_parallel_mutations_allocate_distinct_tracker_revisions(
    delivery_engine, project_id, entity
):
    with Session(delivery_engine) as session:
        prepared_task(session, project_id)
        initial = TrackerRepository(session).ensure_tracker(project_id=project_id).rev
    ready = Barrier(2)
    keys = ["first", "other"] if entity == "ticket" else ["deliver", "unrelated"]

    def mutate(key):
        with Session(delivery_engine) as session:
            repo = TrackerRepository(session)
            # Both writers must hold the same old ORM revision before either
            # mutation. A once-through task overlap alone cannot force this race.
            tracker = repo.ensure_tracker(project_id=project_id)
            assert tracker.rev == initial
            ready.wait(timeout=10)
            if entity == "ticket":
                repo.update_ticket(
                    project_id=project_id, ticket_key=key, patch_json={"title": f"Updated {key}"}
                )
            else:
                repo.update_task(
                    project_id=project_id, task_key=key, patch_json={"title": f"Updated {key}"}
                )

    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(mutate, keys))
    with Session(delivery_engine) as session:
        repo = TrackerRepository(session)
        assert repo.ensure_tracker(project_id=project_id).rev == initial + 2
        revisions = session.exec(
            select(TrackerRevision)
            .where(TrackerRevision.project_id == project_id, TrackerRevision.rev > initial)
            .order_by(TrackerRevision.rev)
        ).all()
        assert [revision.rev for revision in revisions] == [initial + 1, initial + 2]
        assert {revision.entity_key for revision in revisions} == set(keys)
        assert all(revision.entity_kind == entity for revision in revisions)
        snapshot = repo.get(project_id=project_id)
        rows = snapshot.tickets if entity == "ticket" else snapshot.tasks
        assert {row.key: row.title for row in rows if row.key in keys} == {
            key: f"Updated {key}" for key in keys
        }


def test_revision_allocation_rolls_back_with_ticket_change(session, project_id):
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    initial = repo.ensure_tracker(project_id=project_id).rev
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"title": "Uncommitted"}, commit=False
    )
    assert repo.ensure_tracker(project_id=project_id).rev == initial + 1
    session.rollback()
    assert repo.ensure_tracker(project_id=project_id).rev == initial
    assert repo.brief(project_id=project_id, ticket_key="first").ticket.title == "First"
    assert (
        session.exec(
            select(TrackerRevision).where(
                TrackerRevision.project_id == project_id, TrackerRevision.rev > initial
            )
        ).all()
        == []
    )
    result = repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"title": "Committed"}
    )
    assert result.data.rev == initial + 1
    assert result.data.ticket.title == "Committed"


def test_scheduling_failure_releases_only_its_directory(session, project_id, tmp_path):
    from apscheduler.jobstores.base import JobLookupError

    prepared_task(session, project_id)

    class UnavailableScheduler(QueuedScheduler):
        def add_job(self, *args, **kwargs):
            raise RuntimeError("Fixture scheduling failure")

        def remove_job(self, job_id, **kwargs):
            raise JobLookupError(job_id)

    with pytest.raises(RuntimeError, match="Fixture scheduling failure"):
        submit(session, project_id, tmp_path, UnavailableScheduler())
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler, task_key="unrelated")
    with pytest.raises(ValidationError, match=r"working directory.*active task"):
        submit(session, project_id, tmp_path, scheduler)
    assert len(scheduler.jobs) == 1
    repo = TrackerRepository(session)
    assert (
        repo.get(project_id=project_id, task_key="deliver")
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "stopped"
    )
    assert (
        repo.get(project_id=project_id, task_key="unrelated")
        .tasks[0]
        .metadata_json["delivery"]["status"]
        == "queued"
    )


@pytest.mark.parametrize("git_worktrees", [False, True])
def test_parallel_task_jobs_stop_and_resume_independently(
    delivery_engine, project_id, tmp_path, monkeypatch, git_worktrees
):
    from stackos import task_delivery

    if not git_worktrees:
        # The autouse fixture initializes tmp_path as a repository. Merely using
        # child directories would still make them Git working directories.
        if (tmp_path / ".git").exists():
            shutil.rmtree(tmp_path / ".git")
        assert not any((parent / ".git").exists() for parent in (tmp_path, *tmp_path.parents))

        def forbidden_git(*args, **kwargs):
            pytest.fail("Non-Git task execution must not invoke Git")

        monkeypatch.setattr(task_delivery, "_git", forbidden_git)
        assert task_delivery._baseline(str(tmp_path)) == {}
    paths = {key: tmp_path / key for key in ("deliver", "unrelated")}
    for key, directory in paths.items():
        if git_worktrees:
            git(tmp_path, "worktree", "add", "-b", key, str(directory))
        else:
            directory.mkdir()
    if git_worktrees:
        assert git(paths["deliver"], "rev-parse", "--git-common-dir") == git(
            paths["unrelated"], "rev-parse", "--git-common-dir"
        )
    with Session(delivery_engine) as session:
        prepared_task(session, project_id)
        scheduler = QueuedScheduler()
        receipts = {
            key: submit(session, project_id, path, scheduler, task_key=key)
            for key, path in paths.items()
        }
    assert receipts["deliver"]["job_ref"] != receipts["unrelated"]["job_ref"]
    entered = {key: Event() for key in paths}
    finish_b = Event()
    calls = {key: [] for key in paths}

    def host_call(*args, **kwargs):
        packet = json.loads(args[3])
        scope = kwargs["execution_scope"]
        key = scope.task_key
        phase = packet["phase"]
        ticket = packet["brief"]["ticket"]["key"]
        assert scope.project_id == project_id and scope.ticket_key == ticket
        assert Path(args[1]) == paths[key].resolve()
        identity = f"{key}-{ticket}-{phase}"
        resumed = bool(calls[key]) and (ticket, phase) == ("first", "delivery")
        assert kwargs.get("resume_thread_id") == (identity if resumed else None)
        calls[key].append((ticket, phase))
        if len(calls[key]) == 1:
            entered[key].set()
            assert entered["unrelated" if key == "deliver" else "deliver"].wait(10)
            if key == "deliver":
                return scenario_result(0, thread_id=identity, result=generic_result("blocked"))
            assert finish_b.wait(10)
        outputs = {"commit_sha": git(paths[key], "rev-parse", "HEAD")} if git_worktrees else {}
        return scenario_result(0, thread_id=identity, result=generic_result(outputs=outputs))

    monkeypatch.setattr(task_delivery, "run_agent", host_call)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(fn, **options["kwargs"]) for fn, options in scheduler.jobs]
        try:
            futures[0].result(timeout=15)
            assert entered["deliver"].is_set() and entered["unrelated"].is_set()
            assert not futures[1].done()
            with Session(delivery_engine) as session:
                repo = TrackerRepository(session)
                assert (
                    repo.get(project_id=project_id, task_key="deliver")
                    .tasks[0]
                    .metadata_json["delivery"]["status"]
                    == "stopped"
                )
                assert (
                    repo.get(project_id=project_id, task_key="unrelated")
                    .tasks[0]
                    .metadata_json["delivery"]["status"]
                    == "running"
                )
                repo.create_task(project_id=project_id, key="contender", title="Contender")
                repo.create_ticket(
                    project_id=project_id,
                    task_key="contender",
                    key="contender-ticket",
                    title="Contender",
                )
                with pytest.raises(ValidationError, match=r"working directory.*active task"):
                    submit(
                        session,
                        project_id,
                        paths["unrelated"],
                        QueuedScheduler(),
                        task_key="contender",
                    )
                # A's stopped reservation is available while B still runs.
                repo.create_task(project_id=project_id, key="probe", title="Probe")
                repo.create_ticket(
                    project_id=project_id, task_key="probe", key="probe-ticket", title="Probe"
                )
                probe = QueuedScheduler()
                submit(session, project_id, paths["deliver"], probe, task_key="probe")
                assert len(probe.jobs) == 1
                task_delivery._update_delivery(repo, project_id, "probe", status="stopped")
        finally:
            finish_b.set()
        futures[1].result(timeout=15)
    with Session(delivery_engine) as session:
        repo = TrackerRepository(session)
        original = repo.brief(
            project_id=project_id, ticket_key="first"
        ).ticket.completion_evidence_json["automatic_delivery"]
        for status in ("in-progress", "not-started"):
            repo.update_ticket(
                project_id=project_id,
                ticket_key="first",
                patch_json={"status": status, "blocker_reason": None},
            )
        resumed = QueuedScheduler()
        receipt = submit(session, project_id, paths["deliver"], resumed)
        # A new handoff gets its own job while retaining native sessions below.
        assert receipt["job_ref"] != receipts["deliver"]["job_ref"]
        resumed.run()
        session.expire_all()
        for key in paths:
            task = repo.get(project_id=project_id, task_key=key).tasks[0]
            assert task.metadata_json["delivery"]["status"] == "complete"
            assert task.metadata_json["delivery"]["project_dir"] == str(paths[key].resolve())
        final = repo.brief(
            project_id=project_id, ticket_key="first"
        ).ticket.completion_evidence_json["automatic_delivery"]
        assert final["sessions"]["delivery"] == original["sessions"]["delivery"]
        assert final["attempts"][0]["delivery"] == original["delivery"]
        assert final["repair_count"] == original["repair_count"] == 0
        # Completed tasks also release their directory reservation.
        repo.create_task(project_id=project_id, key="next", title="Next")
        repo.create_ticket(project_id=project_id, task_key="next", key="next-ticket", title="Next")
        submit(session, project_id, paths["deliver"], QueuedScheduler(), task_key="next")
    assert calls["deliver"] == [
        ("first", "delivery"),
        ("first", "delivery"),
        ("first", "review"),
        ("second", "delivery"),
        ("second", "review"),
    ]
    assert calls["unrelated"] == [("other", "delivery"), ("other", "review")]


@pytest.mark.parametrize("failure", ["native", "native-candidate", "candidate", "blocked"])
@pytest.mark.parametrize(
    "requeue",
    [
        "same",
        "tracked-change",
        "untracked-change",
        "index-change",
        "session-change",
        "ticket-change",
        "base-change",
        "head-change",
        "mode-change",
        "path-change",
        "source-change",
        "empty-receipt",
        "cleaned-work",
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
    sensitive_names = ["credentials.ts", "token.ts", "secret=assigned.ts"]

    def run_agent(*args, **kwargs):
        packet = json.loads(args[3])
        phase = packet["phase"]
        calls.append((phase, kwargs.get("resume_thread_id")))
        if len(calls) == 1:
            (tmp_path / "baseline").write_text("staged edit")
            git(tmp_path, "add", "baseline")
            (tmp_path / "baseline").write_text("unstaged edit")
            (tmp_path / "new-file").write_text("new work")
            for name in sensitive_names:
                (tmp_path / name).write_text("synthetic private source")
            if failure in {"native", "native-candidate"}:
                return scenario_result(
                    1,
                    thread_id="first-delivery",
                    error="Interrupted",
                    result=generic_result(outputs={"commit_sha": base})
                    if failure == "native-candidate"
                    else None,
                )
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
                # Recovery ownership alone does not declare a source candidate.
                return scenario_result(0, thread_id="first-delivery", result=generic_result())
            git(tmp_path, "add", "baseline", "new-file", *sensitive_names)
            git(tmp_path, "commit", "-m", "Finish original work")
        return scenario_result(
            0,
            thread_id=f"first-{phase}",
            result=generic_result(
                outputs={}
                if failure == "native" and requeue == "dirty-success"
                else {"commit_sha": git(tmp_path, "rev-parse", "HEAD")}
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", run_agent)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert ticket.status == ("in-progress" if failure == "blocked" else "failed")
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    receipt = evidence["owned_work"]
    assert receipt["base_commit_sha"] == base
    assert receipt["session"] == evidence["sessions"]["delivery"]
    assert receipt["source"] == "delivery_return"
    assert len(receipt["snapshot"]["paths"]) == 5
    assert "unstaged edit" not in json.dumps(receipt)
    assert "synthetic private source" not in json.dumps(receipt)
    if requeue == "tracked-change":
        (tmp_path / "baseline").write_text("someone else's edit")
    elif requeue == "untracked-change":
        (tmp_path / "other-file").write_text("someone else's work")
    elif requeue == "index-change":
        git(tmp_path, "add", "baseline")  # Same final bytes, different staged content.
    elif requeue == "head-change":
        git(tmp_path, "commit", "--allow-empty", "-m", "Unowned HEAD change")
    elif requeue == "mode-change":
        (tmp_path / "credentials.ts").chmod(0o755)
    elif requeue == "path-change":
        (tmp_path / "credentials.ts").rename(tmp_path / "credentials-renamed.ts")
    elif requeue == "cleaned-work":
        git(tmp_path, "reset", "--hard", base)
        (tmp_path / "new-file").unlink()
        for name in sensitive_names:
            (tmp_path / name).unlink()
    elif requeue == "empty-receipt":
        evidence["owned_work"] = {}
        repo.update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"completion_evidence_json": {"automatic_delivery": evidence}},
        )
    elif requeue in {"session-change", "ticket-change", "base-change", "source-change"}:
        if requeue == "session-change":
            evidence["sessions"]["delivery"]["thread_id"] = "different-session"
        elif requeue == "ticket-change":
            evidence["owned_work"]["ticket_key"] = "other-ticket"
        elif requeue == "source-change":
            evidence["owned_work"]["session"]["source_digest"] = "0" * 64
        else:
            evidence["candidate"]["base_commit_sha"] = "0" * 40
        repo.update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"completion_evidence_json": {"automatic_delivery": evidence}},
        )
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"status": "not-started", "blocker_reason": None},
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    final = ticket.completion_evidence_json["automatic_delivery"]
    assert final["attempts"][0]["owned_work"] == ({} if requeue == "empty-receipt" else receipt)
    assert final["attempts"][0]["candidate"]["base_commit_sha"] == (
        "0" * 40 if requeue == "base-change" else base
    )
    if requeue == "same" or (failure == "native" and requeue == "dirty-success"):
        assert calls == [("delivery", None), ("delivery", "first-delivery"), ("review", None)]
        assert ticket.status == "complete"
        assert final["candidate"]["base_commit_sha"] == base
        if requeue == "same":
            assert not git(tmp_path, "status", "--porcelain")
        else:
            assert task_delivery._snapshot_integrity(
                task_delivery._worktree_snapshot(str(tmp_path))
            ) == task_delivery._snapshot_integrity(receipt["snapshot"])
        assert "owned_work" not in final
    else:
        assert ticket.status == "failed"
        assert len(calls) == (2 if requeue == "dirty-success" else 1)
        assert ("real commit_sha" if requeue == "dirty-success" else "dirty") in ticket.outcome


@pytest.mark.parametrize("outcome", ["success", "blocked", "native-error", "candidate"])
def test_fresh_dirty_noncoding_delivery_preserves_unowned_work(
    session, project_id, tmp_path, monkeypatch, outcome
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "baseline").write_text("pre-existing staged edit")
    git(tmp_path, "add", "baseline")
    (tmp_path / "baseline").write_text("pre-existing unstaged edit")
    (tmp_path / "unrelated").write_text("preserve this")
    before = task_delivery._worktree_snapshot(str(tmp_path))
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        calls.append((packet["brief"]["ticket"]["key"], packet["phase"]))
        assert packet["candidate"] == {"base_commit_sha": base}
        if outcome == "native-error":
            return AgentRunResult(1, thread_id=str(calls[-1]), error="Interrupted")
        return AgentRunResult(
            0,
            thread_id=str(calls[-1]),
            result=generic_result(
                "blocked" if outcome == "blocked" else "success",
                {"commit_sha": base} if outcome == "candidate" else {},
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == (
        ["complete", "complete"]
        if outcome == "success"
        else ["in-progress" if outcome == "blocked" else "failed", "not-started"]
    )
    assert calls == (
        [(key, phase) for key in ("first", "second") for phase in ("delivery", "review")]
        if outcome == "success"
        else [("first", "delivery")]
    )
    for ticket in snapshot.tickets:
        evidence = (ticket.completion_evidence_json or {}).get("automatic_delivery", {})
        assert "owned_work" not in evidence
    if outcome == "candidate":
        assert "dirty" in snapshot.tickets[0].outcome
    assert task_delivery._worktree_snapshot(str(tmp_path)) == before
    assert (tmp_path / "unrelated").read_text() == "preserve this"


def test_noncoding_outputs_in_git_need_no_candidate_commit(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    base = git(tmp_path, "rev-parse", "HEAD")
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, phase))
        report = tmp_path / f"{key}-report"
        if phase == "delivery":
            report.write_text("Observed business facts")
        assert report.read_text() == "Observed business facts"
        return AgentRunResult(0, thread_id=f"{key}-{phase}", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    assert len(calls) == 4
    assert git(tmp_path, "rev-parse", "HEAD") == base
    assert all(
        "owned_work" not in ticket.completion_evidence_json["automatic_delivery"]
        for ticket in snapshot.tickets
    )


@pytest.mark.parametrize("interruption", ["native", "blocked"])
@pytest.mark.parametrize("review_repair", [False, True])
def test_interrupted_noncoding_report_resumes_without_candidate_contract(
    session, project_id, tmp_path, monkeypatch, interruption, review_repair
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    base = git(tmp_path, "rev-parse", "HEAD")
    report = tmp_path / "business-report.txt"
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        previous = [call for call in calls if call[:2] == (key, phase)]
        assert kwargs.get("resume_thread_id") == (f"{key}-{phase}" if previous else None)
        assert "commit_sha" not in packet["candidate"]
        calls.append((key, phase, kwargs.get("resume_thread_id")))
        if len(calls) == 1:
            report.write_text("Observed business facts\n")
            if interruption == "native":
                return AgentRunResult(1, thread_id="first-delivery", error="Interrupted")
            status = "blocked"
        else:
            assert report.read_text() == "Observed business facts\n"
            status = (
                "repair"
                if key == "first" and phase == "review" and review_repair and not previous
                else "success"
            )
        return AgentRunResult(
            0,
            thread_id=f"{key}-{phase}",
            result=generic_result(status, {"report_path": str(report)}),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    failed = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert failed.status == ("in-progress" if interruption == "blocked" else "failed")
    receipt = failed.completion_evidence_json["automatic_delivery"]["owned_work"]
    assert receipt["snapshot"]["paths"] == ["business-report.txt"]
    for status in ("in-progress", "not-started"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"status": status, "blocker_reason": None},
        )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
    assert calls[:2] == [("first", "delivery", None), ("first", "delivery", "first-delivery")]
    assert len(calls) == (7 if review_repair else 5)
    final = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]
    assert final["attempts"][0]["owned_work"] == receipt
    assert final["repair_count"] == int(review_repair)
    assert "owned_work" not in final
    assert final["review"]["validated_outcome"] == "success"
    assert "commit_sha" not in final["candidate"]
    assert git(tmp_path, "rev-parse", "HEAD") == base
    assert task_delivery._worktree_snapshot(str(tmp_path)) == receipt["snapshot"]


@pytest.mark.parametrize("outcome", ["success", "candidate", "native-error"])
def test_unborn_dirty_git_allows_noncoding_work_without_adopting_files(
    session, project_id, tmp_path, monkeypatch, outcome
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    directory = tmp_path / "unborn"
    directory.mkdir()
    git(directory, "init")
    (directory / "notes").write_text("pre-existing staged notes")
    git(directory, "add", "notes")
    (directory / "notes").write_text("pre-existing unstaged notes")
    (directory / "untracked").write_text("pre-existing untracked notes")

    def contents():
        return {
            "status": git(directory, "status", "--porcelain=v2", "--branch"),
            "staged": git(directory, "diff", "--cached", "--binary"),
            "unstaged": git(directory, "diff", "--binary"),
            "files": {name: (directory / name).read_bytes() for name in ("notes", "untracked")},
        }

    before = contents()
    assert "# branch.oid (initial)" in before["status"]
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        assert packet["candidate"] == {}
        calls.append((packet["brief"]["ticket"]["key"], packet["phase"]))
        if outcome == "native-error":
            return AgentRunResult(1, thread_id=str(calls[-1]), error="Interrupted")
        return AgentRunResult(
            0,
            thread_id=str(calls[-1]),
            result=generic_result(
                outputs={"commit_sha": "0" * 40} if outcome == "candidate" else {}
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, directory, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    assert [ticket.status for ticket in snapshot.tickets] == (
        ["complete", "complete"] if outcome == "success" else ["failed", "not-started"]
    )
    assert calls == (
        [(key, phase) for key in ("first", "second") for phase in ("delivery", "review")]
        if outcome == "success"
        else [("first", "delivery")]
    )
    for ticket in snapshot.tickets:
        evidence = (ticket.completion_evidence_json or {}).get("automatic_delivery", {})
        assert "owned_work" not in evidence
    if outcome == "candidate":
        receipt = snapshot.tickets[0].completion_evidence_json["automatic_delivery"]["delivery"]
        assert receipt["validation_error"].startswith("Git candidate check failed:")
        assert receipt["validated_outcome"] is None
    assert contents() == before  # Includes Git's still-unborn status and the exact index diff.


def test_optional_git_baseline_does_not_hide_invalid_repository_errors(tmp_path):
    from stackos import task_delivery

    (tmp_path / ".git" / "HEAD").write_text("invalid HEAD\n")
    with pytest.raises(ValidationError, match="Git candidate check failed"):
        task_delivery._baseline(str(tmp_path))


@pytest.mark.parametrize("candidate_supplied", [False, True])
def test_feedback_cannot_drop_an_explicit_candidate_contract(
    session, project_id, tmp_path, monkeypatch, candidate_supplied
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        key, phase = packet["brief"]["ticket"]["key"], packet["phase"]
        calls.append((key, phase))
        outputs = {}
        if candidate_supplied and len(calls) == 1:
            outputs["commit_sha"] = commit(tmp_path, "source-candidate")
        elif candidate_supplied and len(calls) == 2:
            outputs["commit_sha"] = packet["candidate"]["commit_sha"]
        status = "repair" if len(calls) == 2 else "success"
        return AgentRunResult(0, thread_id=f"{key}-{phase}", result=generic_result(status, outputs))

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    snapshot = TrackerRepository(session).get(project_id=project_id, task_key="deliver")
    if candidate_supplied:
        assert [ticket.status for ticket in snapshot.tickets] == ["failed", "not-started"]
        assert len(calls) == 3
        assert "real commit_sha" in snapshot.tickets[0].outcome
    else:
        assert [ticket.status for ticket in snapshot.tickets] == ["complete", "complete"]
        assert len(calls) == 6


def test_owned_work_survives_tracker_history_and_fresh_process(
    native_task_session, project_id, tmp_path
):
    from stackos import task_delivery

    session = native_task_session
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    base = git(tmp_path, "rev-parse", "HEAD")
    identity = {"agent": "builder", "host_key": "codex", "thread_id": "original-session"}
    evidence = {
        "candidate": {"base_commit_sha": base},
        "sessions": {"delivery": identity},
        "delivery": {"result": generic_result("blocked")},
        "repair_count": 2,
    }
    for name in ["credentials.ts", "token.ts", "secret.ts", "api_key=value.ts", "api_key=other.ts"]:
        (tmp_path / name).write_text("synthetic private source")
    (tmp_path / "baseline").write_text("staged private source")
    git(tmp_path, "add", "baseline")
    (tmp_path / "baseline").write_text("unstaged private source")
    evidence["owned_work"] = task_delivery._owned_work_receipt(
        str(tmp_path), "first", evidence, baseline_clean=True
    )
    task_delivery._save_evidence(repo, project_id, "first", evidence)
    session.commit()
    session.expire_all()
    stored = repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json[
        "automatic_delivery"
    ]
    task_delivery._archive_attempt(stored)
    task_delivery._save_evidence(repo, project_id, "first", stored)
    session.commit()
    session.expire_all()
    # A fresh interpreter/connection consumes actual redacted DB rows and revisions.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json, sys
from pathlib import Path
from sqlmodel import Session, select
from stackos.db.connection import make_engine
from stackos.db.models import TrackerRevision
from stackos.repositories.tracker import TrackerRepository
from stackos.operations.tracker import write_handlers
from stackos.task_delivery import _launch_baseline
engine = make_engine(Path(sys.argv[1]))
with Session(engine) as session:
    project_id = int(sys.argv[2])
    evidence = TrackerRepository(session).brief(
        project_id=project_id, ticket_key="first"
    ).ticket.completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 2
    assert evidence["attempts"][0]["owned_work"] == evidence["owned_work"]
    revisions = session.exec(select(TrackerRevision).where(
        TrackerRevision.project_id == project_id, TrackerRevision.entity_key == "first"
    )).all()
    for revision in revisions[-2:]:
        assert revision.after_json["completion_evidence_json"]["automatic_delivery"][
            "owned_work"
        ] == evidence["owned_work"]
    assert "private source" not in json.dumps([r.after_json for r in revisions])
    print(json.dumps(_launch_baseline(
        sys.argv[3], "first", evidence, "delivery", evidence["sessions"]["delivery"]
    )))
engine.dispose()
""",
            str(session.get_bind().url.database),
            str(project_id),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"base_commit_sha": base}


@pytest.mark.parametrize(
    "damage",
    [
        "none",
        "labels",
        "digest",
        "unknown",
        "redacted",
        "malformed",
        "null",
        "missing-base",
        "unknown-receipt",
        "malformed-source",
        "unreviewed-adoption",
        "malformed-time",
    ],
)
def test_versioned_owned_work_validates_integrity_without_trusting_labels(tmp_path, damage):
    from stackos import task_delivery

    identity = {"agent": "builder", "host_key": "codex", "thread_id": "original-session"}
    evidence = {
        "candidate": {"base_commit_sha": git(tmp_path, "rev-parse", "HEAD")},
        "sessions": {"delivery": identity},
    }
    (tmp_path / "credentials.ts").write_text("private source")
    receipt = task_delivery._owned_work_receipt(
        str(tmp_path), "first", evidence, baseline_clean=True
    )
    assert receipt["snapshot"]["version"] == 1
    evidence["owned_work"] = receipt
    if damage == "labels":
        receipt["snapshot"]["paths"] = ["arbitrary diagnostic label"]
    elif damage == "digest":
        receipt["snapshot"]["untracked"][0]["sha256"] = "0" * 64
    elif damage == "unknown":
        receipt["snapshot"]["version"] = 999
    elif damage == "redacted":
        receipt["snapshot"]["untracked"][0]["sha256"] = "[redacted]"
    elif damage == "malformed":
        receipt["snapshot"]["untracked"] = {"credentials.ts": {}}
    elif damage == "null":
        evidence["owned_work"] = None
    elif damage == "missing-base":
        receipt["base_commit_sha"] = None
        evidence["candidate"]["base_commit_sha"] = None
    elif damage == "unknown-receipt":
        receipt["version"] = 999
    elif damage == "malformed-source":
        receipt["source"] = []
    elif damage == "unreviewed-adoption":
        receipt["source"] = "operator_recovery"
        receipt["operator_recovery"] = {}
    elif damage == "malformed-time":
        receipt["captured_at"] = None
    if damage in {"none", "labels"}:
        assert (
            task_delivery._launch_baseline(str(tmp_path), "first", evidence, "delivery", identity)
            == evidence["candidate"]
        )
    else:
        with pytest.raises(ValidationError, match="dirty"):
            task_delivery._launch_baseline(str(tmp_path), "first", evidence, "delivery", identity)


@pytest.mark.parametrize("damage", ["none", "redacted", "paths", "mode", "unknown"])
def test_legacy_owned_work_normalizes_only_intact_persisted_receipts(
    session, project_id, tmp_path, damage
):
    from hashlib import sha256

    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    identity = {"agent": "builder", "host_key": "codex", "thread_id": "original-session"}
    evidence = {
        "candidate": {"base_commit_sha": git(tmp_path, "rev-parse", "HEAD")},
        "sessions": {"delivery": identity},
        "repair_count": 3,
        "attempts": [{"attempt": 1, "review": {"validated_outcome": "repair"}}],
    }
    name = "credentials.ts" if damage == "redacted" else "new-file"
    path = tmp_path / name
    path.write_text("unfinished original source")
    receipt = task_delivery._owned_work_receipt(
        str(tmp_path), "first", evidence, baseline_clean=True
    )
    snapshot = receipt["snapshot"]
    # Historical schema, independent of the new writer's untracked representation.
    receipt["snapshot"] = {
        key: snapshot[key] for key in ("head_commit_sha", "staged", "unstaged", "paths")
    }
    receipt["snapshot"]["untracked"] = {
        name: {"sha256": sha256(path.read_bytes()).hexdigest(), "mode": path.lstat().st_mode}
    }
    if damage == "paths":
        receipt["snapshot"]["paths"] = ["[redacted]"]
    elif damage == "mode":
        receipt["snapshot"]["untracked"][name]["mode"] = True
    elif damage == "unknown":
        receipt["snapshot"]["version"] = 999
    evidence["owned_work"] = receipt
    task_delivery._save_evidence(repo, project_id, "first", evidence)
    session.commit()
    session.expire_all()
    stored = repo.brief(project_id=project_id, ticket_key="first").ticket.completion_evidence_json[
        "automatic_delivery"
    ]
    before = deepcopy(stored)
    if damage == "none":
        assert (
            task_delivery._launch_baseline(str(tmp_path), "first", stored, "delivery", identity)
            == evidence["candidate"]
        )
    else:
        with pytest.raises(ValidationError, match="dirty"):
            task_delivery._launch_baseline(str(tmp_path), "first", stored, "delivery", identity)
        stored["owned_work"] = task_delivery._owned_work_receipt(
            str(tmp_path),
            "first",
            stored,
            operator_recovery={"actor": "operator", "reason": "Reviewed exact source and session"},
        )
        task_delivery._save_evidence(repo, project_id, "first", stored)
        session.commit()
        session.expire_all()
        stored = repo.brief(
            project_id=project_id, ticket_key="first"
        ).ticket.completion_evidence_json["automatic_delivery"]
        assert (
            task_delivery._launch_baseline(str(tmp_path), "first", stored, "delivery", identity)
            == evidence["candidate"]
        )
    assert stored["repair_count"] == before["repair_count"]
    assert stored["attempts"] == before["attempts"]
    if damage == "none":
        assert stored == before  # Normalization is read-only; legacy history stays intact.
    else:
        revisions = session.exec(
            select(TrackerRevision).where(TrackerRevision.entity_key == "first")
        ).all()
        assert any(
            r.after_json["completion_evidence_json"] == {"automatic_delivery": before}
            for r in revisions
        )


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
    evidence = {
        "candidate": {"base_commit_sha": original_base, "commit_sha": new_head},
        "sessions": {"delivery": identity},
    }
    (tmp_path / "baseline").write_text("original unfinished work")
    with pytest.raises(ValidationError, match="dirty"):
        task_delivery._launch_baseline(str(tmp_path), "first", evidence, "delivery", identity)
    for review in [{}, {"actor": "operator", "reason": ""}]:
        with pytest.raises(ValidationError, match="actor and reason"):
            task_delivery._owned_work_receipt(
                str(tmp_path), "first", evidence, operator_recovery=review
            )
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


@pytest.mark.parametrize("host", ["claude-code", "grok"])
def test_messages_native_identity_resume_usage_and_rebuilt_statistics(
    native_task_session, project_id, tmp_path, tmp_path_factory, monkeypatch, host
):
    """Real Messages process -> durable role session -> resume -> canonical reader."""
    from sqlalchemy import delete

    from stackos import task_delivery
    from stackos.db.models import TrackerReceiptProjection, TrackerTicket
    from stackos.host_mcp import service
    from stackos.host_mcp.adapters import claude_code, grok
    from stackos.repositories.tracker.receipt_projection import sync_receipt_projection

    session = native_task_session
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Messages")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"completion_evidence_json": {"automatic_delivery": {"repair_count": 2}}},
    )
    scratch = tmp_path_factory.mktemp("messages-native")
    gate, calls_path = scratch / "release", scratch / "calls.json"
    executable = scratch / "native"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json,sys,time\nfrom pathlib import Path\n"
        "if '--version' in sys.argv: print('2.1.330'); sys.exit(0)\n"
        "text = (sys.argv[sys.argv.index('--single')+1] "
        "if '--single' in sys.argv else sys.stdin.read())\n"
        "packet = json.JSONDecoder().raw_decode(text)[0]\nphase = packet['phase']\n"
        f"calls_path = Path({str(calls_path)!r})\n"
        "calls = json.loads(calls_path.read_text()) if calls_path.exists() else []\n"
        "resumed = sys.argv[sys.argv.index('--resume')+1] if '--resume' in sys.argv else None\n"
        "calls.append([phase,resumed]); calls_path.write_text(json.dumps(calls))\n"
        "sid = 'messages-' + phase\n"
        "print(json.dumps({'type':'system','subtype':'init','session_id':sid,"
        "'model':'native-observed'}),flush=True)\n"
        "if len(calls)==1:\n"
        f" gate=Path({str(gate)!r})\n"
        " while not gate.exists(): time.sleep(.01)\n sys.exit(3)\n"
        "count = 150 if len(calls)==3 else 100\n"
        "outcome={'status':'blocked' if len(calls)==2 else 'success','summary':'Checked',"
        "'outputs':'{}','followups':[]}\n"
        "print(json.dumps({'type':'result','subtype':'success','session_id':sid,"
        "'stop_reason':'end_turn','is_error':False,'structured_output':outcome,"
        "'result':json.dumps(outcome),'usage':{'input_tokens':count,"
        "'cache_read_input_tokens':0,'cache_creation_input_tokens':0,'output_tokens':0},"
        "'modelUsage':{'native-observed':{'inputTokens':count,'cacheReadInputTokens':0,"
        "'cacheCreationInputTokens':0,'outputTokens':0}}}),flush=True)\n"
    )
    executable.chmod(0o700)
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: str(executable))
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _: str(executable))
    monkeypatch.setattr(
        service,
        "execution_catalog",
        lambda definition, **_: {**service.asdict(definition.policy.agent_execution)},
    )
    monkeypatch.setattr(task_delivery, "run_agent", service.run_agent)
    announced = Event()
    attach = service._attach_session

    def attached(*args, **kwargs):
        attach(*args, **kwargs)
        announced.set()

    monkeypatch.setattr(service, "_attach_session", attached)
    scheduler = QueuedScheduler()

    def handoff():
        submit(
            session,
            project_id,
            tmp_path,
            scheduler,
            host_key=host,
            delivery_agent={"name": "builder", "host_key": host, "instructions": "Build"},
            review_agent={"name": "reviewer", "host_key": host, "instructions": "Review"},
        )

    handoff()
    with ThreadPoolExecutor(1) as pool:
        work = pool.submit(scheduler.run)
        try:
            assert announced.wait(10)
            with Session(session.get_bind()) as reader:
                early = (
                    TrackerRepository(reader)
                    .brief(project_id=project_id, ticket_key="first")
                    .ticket.completion_evidence_json["automatic_delivery"]
                )
            retained = deepcopy(early["sessions"]["delivery"])
            assert retained["thread_id"] == "messages-delivery"
            assert early["repair_count"] == 2 and "exit_code" not in early["delivery"]
            assert not work.done()
        finally:
            gate.touch()
        work.result(timeout=10)
    # Explicit fixture-only recovery; production task lifecycle is untouched.
    for _ in range(2):
        repo.update_ticket(
            project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
        )
        repo.update_ticket(
            project_id=project_id,
            ticket_key="first",
            patch_json={"status": "not-started", "blocker_reason": None},
        )
        handoff()
        scheduler.run()
        session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    assert ticket.status == "complete", ticket.outcome
    assert evidence["repair_count"] == 2
    assert evidence["sessions"]["delivery"] == retained
    assert evidence["sessions"]["review"]["thread_id"] == "messages-review"
    assert json.loads(calls_path.read_text()) == [
        ["delivery", None],
        ["delivery", "messages-delivery"],
        ["delivery", "messages-delivery"],
        ["review", None],
    ]
    assert evidence["delivery"]["model_observation"]["model"] == "native-observed"
    assert evidence["delivery"]["usage"]["measured"]["total"] == (
        50 if host == "claude-code" else 150
    )
    expected = 150 if host == "claude-code" else 350

    def check_statistics():
        rows = repo.agent_stats(project_id=project_id, group_by="model").rows
        row = next(row for row in rows if row.model == "native-observed")
        assert row.model_basis == "observed"
        assert row.reported_usage.total == expected

    check_statistics()
    session.execute(delete(TrackerReceiptProjection))
    for recorded in session.exec(select(TrackerTicket)).all():
        sync_receipt_projection(
            session.connection(), recorded.id, recorded.completion_evidence_json
        )
    session.commit()
    check_statistics()


def test_observed_model_native_to_live_final_resume_and_rebuilt_statistics(
    session, project_id, tmp_path, tmp_path_factory, monkeypatch
):
    """Real adapter/service/controller/receipts; only the native process is substituted."""
    import sqlite3
    from datetime import UTC, datetime

    from sqlalchemy import delete

    from stackos.db.models import TrackerReceiptProjection, TrackerTicket
    from stackos.host_mcp import progress, service
    from stackos.host_mcp.adapters import codex
    from stackos.repositories.tracker.receipt_projection import sync_receipt_projection

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    repo.create_task(project_id=project_id, key="historical", title="Historical")
    historical = {}
    for key, model, count in (("unknown", None, 11), ("known", "known-selection", 7)):
        repo.create_ticket(project_id=project_id, task_key="historical", key=key, title=key)
        old = {
            "launch": {"agent": "legacy", "host_key": "codex", "model": model},
            "usage": {
                "unit": "tokens",
                "status": "measured",
                "measured": {"input": count, "output": 0, "total": count},
            },
        }
        data = {"automatic_delivery": {"attempt": 1, "delivery": old}}
        repo.update_ticket(
            project_id=project_id, ticket_key=key, patch_json={"completion_evidence_json": data}
        )
        historical[key] = deepcopy(data)

    home = tmp_path_factory.mktemp("native-model-home")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")
    ids = {
        "delivery": "01a1b94c-eb2a-7280-b3c5-d55cd290801b",
        "review": "01a1b94c-eb2a-7280-b3c5-d55cd290801c",
    }
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT)")
        for sid in ids.values():
            path = home / f"{sid}.jsonl"
            path.write_text(
                json.dumps({"type": "session_meta", "payload": {"id": sid, "cwd": str(tmp_path)}})
                + "\n"
            )
            db.execute("INSERT INTO threads VALUES (?, ?)", (sid, str(path)))
    calls = []

    def stream(command, *, input, on_session, **kwargs):
        packet = json.loads(input)
        phase = packet["phase"]
        resumed = "resume" in command
        sid = ids[phase]
        model = "observed-new" if resumed else "observed-native"
        count = 150 if resumed else 100
        on_session(sid)
        at = datetime.now(UTC).isoformat()
        with (home / f"{sid}.jsonl").open("a") as out:
            for event in (
                {
                    "type": "turn_context",
                    "payload": {"cwd": str(tmp_path), "model": model, "turn_id": "redacted-turn"},
                },
                {
                    "type": "event_msg",
                    "timestamp": at,
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": count,
                                "output_tokens": 0,
                                "total_tokens": count,
                            }
                        },
                    },
                },
            ):
                out.write(json.dumps(event) + "\n")
        service._sample_invocations()
        # The real live statistics owner must use the observed inherited model
        # before the native result or final receipt exists.
        for stats in (
            repo.agent_stats(project_id=project_id, group_by="model"),
            repo.agent_stats_all(group_by="model"),
        ):
            row = next(row for row in stats.rows if row.model == model)
            assert row.model_basis == "observed"
            assert row.reported_usage.total == (
                50 if resumed else 200 if phase == "review" else 100
            )
        calls.append((phase, resumed))
        value = generic_result("blocked" if len(calls) == 1 else "success")
        Path(command[command.index("-o") + 1]).write_text(json.dumps(value))
        return subprocess.CompletedProcess(
            command,
            0,
            "\n".join(
                map(
                    json.dumps,
                    (
                        {"type": "thread.started", "thread_id": sid},
                        {
                            "type": "turn.completed",
                            "usage": {"input_tokens": count, "output_tokens": 0},
                        },
                    ),
                )
            ),
            "",
        )

    monkeypatch.setattr(progress, "run_codex_process", stream)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    first = repo.brief(project_id=project_id, ticket_key="first").ticket
    initial = deepcopy(first.completion_evidence_json["automatic_delivery"]["delivery"])
    assert "model_observation" in initial, json.dumps(initial)
    assert initial["model_observation"]["model"] == "observed-native"
    assert initial["execution"]["model_observation"] == initial["model_observation"]
    assert initial["launch"].get("model") is None
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"status": "not-started", "blocker_reason": None},
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    first = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert first.status == "complete", (calls, first.blocker_reason, first.completion_evidence_json)
    assert calls == [("delivery", False), ("delivery", True), ("review", False)]
    automatic = first.completion_evidence_json["automatic_delivery"]
    assert automatic["attempts"][0]["delivery"] == initial
    assert automatic["delivery"]["model_observation"]["model"] == "observed-new"
    assert automatic["delivery"]["usage"]["measured"]["total"] == 50
    assert first.agent_stats.reported_usage.total == 250
    assert automatic["review"]["model_observation"]["model"] == "observed-native"

    def check_groups():
        for group_by in ("model", "agent_model"):
            for stats in (
                repo.agent_stats(project_id=project_id, group_by=group_by),
                repo.agent_stats_all(group_by=group_by),
            ):
                assert stats.totals.reported_usage.total == 268
                assert stats.totals.launch_attempts == 5
                amounts = {}
                for row in stats.rows:
                    amounts[row.model] = amounts.get(row.model, 0) + row.reported_usage.total
                assert amounts == {
                    None: 11,
                    "known-selection": 7,
                    "observed-native": 200,
                    "observed-new": 50,
                }
                assert all(
                    row.model_basis
                    == (
                        "observed"
                        if row.model and row.model.startswith("observed")
                        else "selected"
                        if row.model
                        else None
                    )
                    for row in stats.rows
                )

    check_groups()
    # Rebuild only this disposable fixture index from canonical receipts.
    session.execute(delete(TrackerReceiptProjection))
    for ticket in session.exec(select(TrackerTicket)).all():
        sync_receipt_projection(session.connection(), ticket.id, ticket.completion_evidence_json)
    session.commit()
    check_groups()
    for key, old in historical.items():
        assert (
            repo.brief(project_id=project_id, ticket_key=key).ticket.completion_evidence_json == old
        )


def test_role_host_model_provider_reach_launch_and_receipt(
    session: Session,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    controlled_profile_host: str,
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
        assert "named disposable, isolated test fixtures" in packet["lifecycle"]
        assert "no control over the current task, production state" in packet["lifecycle"]
        assert callable(selection.pop("on_execution_change"))
        assert callable(selection.pop("on_session_attached"))
        assert callable(selection.pop("on_process_started"))
        assert selection.pop("launch_allowed")()
        route = selection.pop("mcp_route_identity")
        assert route["host_key"] == host and route["project_dir"] == str(tmp_path)
        assert len(route["route_digest"]) == 64
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
            "host_key": controlled_profile_host,
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
            (
                controlled_profile_host,
                {"model": "provider/model", "provider": "openrouter", "profile": "work"},
            ),
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
    assert evidence["delivery"]["launch"]["host_key"] == controlled_profile_host
    assert evidence["delivery"]["launch"]["model"] == "provider/model"
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
            "host_key": "antigravity",
            "model": "gemini-3.8-flash-low",
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
        review_agent={"model": "gemini-3.8-flash-high"},
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
        "model": "gemini-3.8-flash-high",
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
        assert callable(selection.pop("on_session_attached"))
        assert callable(selection.pop("on_process_started"))
        assert selection.pop("launch_allowed")()
        route = selection.pop("mcp_route_identity")
        assert (
            route
            == state["delivery"]["mcp_route_identities"][
                "delivery_agent" if host == "claude-code" else "review_agent"
            ]
        )
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
                "antigravity",
                f"Saved review instructions\n\n{task_delivery.LAUNCH_AUTHORITY_BOUNDARY}",
                "normal",
                {"model": "gemini-3.8-flash-high"},
            ),
        ]
        * 2
    )
    assert not (tmp_path / ".codex").exists()


@pytest.mark.parametrize(
    "defaults,override,reason",
    [
        ({}, None, "adoption_required"),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build"}},
            None,
            "independent review_agent binding",
        ),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build", "model": "unknown"}},
            None,
            "native model catalog",
        ),
        (
            {"delivery_agent": {"name": "builder", "instructions": "Build"}},
            {"instructions": None},
            "adoption_required",
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


@pytest.mark.parametrize("role", ["delivery_agent", "review_agent"])
def test_hermes_managed_handoff_rejects_without_rewriting_settings(
    session, project_id, tmp_path, role
):
    prepared_task(session, project_id)
    repo = TrackerRepository(session)
    before = deepcopy(repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json)
    scheduler = QueuedScheduler()
    with pytest.raises(ValidationError, match="gateway"):
        submit(
            session,
            project_id,
            tmp_path,
            scheduler,
            **{
                role: {
                    "host_key": "hermes",
                    "name": "retained",
                    "instructions": "Role",
                    "provider": "openrouter",
                    "model": "saved/model",
                    "profile": "saved-profile",
                }
            },
        )
    assert not scheduler.jobs
    assert repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json == before


@pytest.mark.parametrize("host", ["antigravity"])
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
            assert "Do not modify the reviewed outputs" in packet["assignment"]
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
            state["stop_context"]["code"]
            == {
                "native": "native_failure",
                "mutation": "candidate_validation",
                "reject": "output_validation",
            }[failure]
        )
        assert state["stop_context"]["phase"] == "review"
        assert state["stop_context"]["evidence_refs"] == [
            f"tracker-ticket:{project_id}:{first.id}:attempt:1:review"
        ]
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
    results = {}

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
                accepted = packet["accepted_receipts"][0]
                assert accepted["key"] == "first"
                # Summary-only readers retain the decision facts, with detail at its owner.
                assert accepted["outcome"] == results[("first", "review")]["summary"]
                assert (
                    accepted["completion_evidence_json"]["automatic_delivery"]["candidate"][
                        "commit_sha"
                    ]
                    == json.loads(results[("first", "review")]["outputs"])["commit_sha"]
                )
                ref = accepted["evidence_ref"]
                assert ref["tool_name"] == "tracker.brief"
                recovered = repo.brief(
                    project_id=ref["arguments"]["project_id"],
                    ticket_key=ref["arguments"]["ticket_key"],
                ).ticket
                assert recovered.project_id == project_id
                assert (
                    recovered.completion_evidence_json["automatic_delivery"]["review"]["result"]
                    == results[("first", "review")]
                )
                assert "DETAILED_PROOF" not in json.dumps(accepted)
                with pytest.raises(NotFoundError):
                    repo.brief(project_id=project_id, ticket_key="missing-prerequisite")
        calls.append((ticket_key, instructions, phase))
        sha = (
            commit(tmp_path, ticket_key)
            if phase == "delivery"
            else git(tmp_path, "rev-parse", "HEAD")
        )
        if phase == "review":
            assert packet["candidate"]["commit_sha"] == sha
            assert (
                "Keep commands, results and scoped repairs in summary" not in packet["assignment"]
            )
            assert "verdict, candidate" in packet["assignment"]
            assert "once in outputs or referenced existing evidence" in packet["assignment"]
            assert packet["delivery_result"]["result"] == {
                key: value
                for key, value in results[(ticket_key, "delivery")].items()
                if key != "followups"
            }
        assert "summary-only prerequisite readers" in packet["result_contract"]
        assert schema == task_delivery.RESULT_SCHEMA
        result = generic_result(
            summary=(
                f"{'Verified' if phase == 'review' else 'Implemented'} {ticket_key} "
                f"at {sha}; dependency flow checks passed; source-only, no activation proof."
            ),
            outputs={
                "commit_sha": sha,
                "checks": [
                    {
                        "command": "DETAILED_PROOF",
                        "exit_status": 0,
                        "observed": "Dependency flow passed",
                    }
                ],
                "limitations": ["Installed activation unexecuted and outside this fixture"],
            },
        )
        results[(ticket_key, phase)] = deepcopy(result)
        return AgentRunResult(0, thread_id=f"thread-{len(calls)}", result=result)

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


@pytest.mark.parametrize("explicit", [False, True])
def test_secret_named_ticket_assignment_survives_admission_reload_and_launch(
    session, project_id, tmp_path, monkeypatch, explicit
):
    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    key = "ci-811-metadata-secret"
    repo.create_ticket(project_id=project_id, task_key="deliver", key=key, title="Metadata")
    scheduler = QueuedScheduler()
    calls = []

    def launch(host, directory, instructions, prompt, schema, **kwargs):
        packet = json.loads(prompt)
        assert packet["brief"]["ticket"]["key"] == key
        phase = packet["phase"]
        calls.append(phase)
        assert instructions.startswith(
            "Build the ticket." if phase == "delivery" else "Review the ticket."
        )
        sha = (
            commit(tmp_path, "metadata-fix")
            if phase == "delivery"
            else git(tmp_path, "rev-parse", "HEAD")
        )
        return scenario_result(
            0, thread_id=phase, result=generic_result(outputs={"commit_sha": sha})
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        **({"agent_assignments": {key: "delivery_agent"}} if explicit else {}),
    )
    # A new session and the actual runner must consume the persisted map, not
    # start_delivery's pre-redaction return or an ORM identity-map copy.
    with Session(session.get_bind()) as reloaded:
        state = (
            TrackerRepository(reloaded)
            .get(project_id=project_id, task_key="deliver")
            .tasks[0]
            .metadata_json["delivery"]
        )
        assert state["agent_assignments"][key] == "delivery_agent"
        assert state["agents"][state["agent_assignments"][key]]["name"] == "project-builder"
    scheduler.run()
    assert calls == ["delivery", "review"]
    session.expire_all()
    state = repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json["delivery"]
    assert state["status"] == "complete"
    assert state["agent_assignments"][key] == "delivery_agent"


def test_task_assignment_preservation_does_not_exempt_secret_metadata(session, project_id):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    key = "ci-811-metadata-secret"
    repo.create_ticket(project_id=project_id, task_key="deliver", key=key, title="Metadata")
    repo.create_task(project_id=project_id, key="foreign", title="Foreign")
    repo.create_ticket(
        project_id=project_id, task_key="foreign", key="foreign-secret", title="Foreign"
    )
    metadata = {
        "password": "synthetic-material",
        "delivery": {
            "agents": {"delivery_agent": {"name": "builder", "instructions": "Build."}},
            "agent_assignments": {
                key: "delivery_agent",
                "unknown-secret": "delivery_agent",
                "foreign-secret": "delivery_agent",
                "password": "synthetic-material",
            },
            "Authorization": "synthetic-material",
            "nested": {key: "synthetic-material"},
        },
    }
    repo.update_task(
        project_id=project_id, task_key="deliver", patch_json={"metadata_json": metadata}
    )
    session.expire_all()
    stored = repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json
    assert stored["delivery"]["agent_assignments"][key] == "delivery_agent"
    assert stored["delivery"]["agent_assignments"]["unknown-secret"] == "[redacted]"
    assert stored["delivery"]["agent_assignments"]["foreign-secret"] == "[redacted]"
    assert "synthetic-material" not in json.dumps(stored)
    for bad in ["Bearer synthetic-material", {"password": "synthetic-material"}, "unknown-role"]:
        metadata["delivery"]["agent_assignments"][key] = bad
        repo.update_task(
            project_id=project_id, task_key="deliver", patch_json={"metadata_json": metadata}
        )
        session.expire_all()
        stored = repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json
        assert stored["delivery"]["agent_assignments"][key] == "[redacted]"
        assert "synthetic-material" not in json.dumps(stored)
    assert (
        repo.brief(project_id=project_id, ticket_key="foreign-secret").ticket.status
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
    assert [ticket.status for ticket in snapshot.tickets] == [
        "in-progress" if failure == "blocked" else "failed",
        "not-started",
    ]
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
        "not-descendant",
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
            elif failure == "not-descendant":
                git(tmp_path, "checkout", "--orphan", "unrelated-history")
                git(tmp_path, "commit", "-m", "Unrelated root")
                result["commit_sha"] = git(tmp_path, "rev-parse", "HEAD")
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


def disposition_workflow(session, project_id):
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "explicit-child-scope",
            "title": "Explicit child scope",
            "steps": [
                {"id": "verify", "title": "Verify current requirements"},
                {"id": "review", "title": "Review", "depends_on": ["verify"]},
            ],
        },
    ).data
    started = plans.start(plan.id, project_id=project_id)
    task_key = f"workflow-{plan.id}"
    parent = f"{task_key}-verify"
    repo = TrackerRepository(session)
    for key in ("required", "optional"):
        repo.create_ticket(
            project_id=project_id,
            task_key=task_key,
            key=key,
            title=key,
            parent_ticket_key=parent,
            dependency_keys=[parent],
            run_plan_id=plan.id,
            run_plan_step_id=plans.get_step(plan.id, "verify").id,
        )
    scope = {
        "disposition": "excluded",
        "parent_ticket_key": parent,
        "reason": "Operator withdrew optional scope from this parent",
        "source_ref": "operator-decision:scope-2",
    }
    state = {"run_plan_id": plan.id, "controller_run_id": started.run_id}
    return repo, plans, task_key, parent, scope, state


@pytest.mark.parametrize("status", ["skipped", "aborted", "deferred"])
def test_explicit_child_disposition_settles_only_containment(session, project_id, status):
    from stackos import task_delivery

    repo, plans, task_key, parent, scope, state = disposition_workflow(session, project_id)
    # The selector activates the actual parent before its first child can run.
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        True,
    )
    assert "required" in {
        t.key for t in repo.delivery_ready(project_id=project_id, task_key=task_key)
    }
    for value in ("in-progress", "complete"):
        repo.update_ticket(
            project_id=project_id, ticket_key="required", patch_json={"status": value}
        )
    repo.update_ticket(project_id=project_id, ticket_key="optional", patch_json={"status": status})
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        False,
    )
    if status == "deferred":
        scope.update(resume_owner_ref="team:main", resume_condition="Operator admits next scope")
    patch = {"metadata_json": {"unrelated": 1, "delivery_scope": scope}}
    before = repo.brief(project_id=project_id, ticket_key="optional").ticket
    repo.update_ticket(project_id=project_id, ticket_key="optional", patch_json=patch, dry_run=True)
    assert repo.brief(project_id=project_id, ticket_key="optional").ticket.metadata_json is None
    repo.update_ticket(project_id=project_id, ticket_key="optional", patch_json=patch)
    selected, step, changed = task_delivery._select_work(repo, plans, project_id, task_key, state)
    assert (selected.key, step.step_id, changed) == (parent, "verify", False)
    optional = repo.brief(project_id=project_id, ticket_key="optional").ticket
    assert optional.status == status
    assert optional.parent_ticket_id == before.parent_ticket_id
    assert optional.metadata_json == patch["metadata_json"]
    # A real blocks edge is never satisfied by the disposition.
    reviewer = f"{task_key}-review"
    repo.update_ticket(
        project_id=project_id, ticket_key=reviewer, patch_json={"add_dependency_keys": ["optional"]}
    )
    assert "optional" in repo.brief(project_id=project_id, ticket_key=reviewer).ticket.blocked_by
    repo.update_ticket(
        project_id=project_id,
        ticket_key=reviewer,
        patch_json={"remove_dependency_keys": ["optional"]},
    )
    assert (
        "optional" not in repo.brief(project_id=project_id, ticket_key=reviewer).ticket.blocked_by
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="optional", patch_json={"metadata_json": {"unrelated": 1}}
    )
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        False,
    )
    revisions = session.exec(
        select(TrackerRevision).where(TrackerRevision.entity_key == "optional")
    ).all()
    assert any(
        ((r.before_json or {}).get("metadata_json") or {}).get("delivery_scope") == scope
        for r in revisions
    )


@pytest.mark.parametrize(
    "defect",
    [
        "reason",
        "source_ref",
        "wrong-parent",
        "self-parent",
        "foreign-task",
        "shape",
        "resume-owner",
        "resume-condition",
        "active",
        "failed",
    ],
)
def test_child_disposition_rejects_invalid_marker_without_writes(session, project_id, defect):
    repo, _plans, _task_key, parent, scope, _state = disposition_workflow(session, project_id)
    status = "deferred" if defect.startswith("resume-") else "skipped"
    if defect in {"reason", "source_ref"}:
        scope[defect] = " "
    elif defect == "wrong-parent":
        scope["parent_ticket_key"] = "required"
    elif defect == "self-parent":
        parent = "optional"
        scope["parent_ticket_key"] = parent
    elif defect == "foreign-task":
        repo.create_task(project_id=project_id, key="foreign", title="Foreign")
        repo.create_ticket(
            project_id=project_id, task_key="foreign", key="foreign-parent", title="Foreign"
        )
        parent = "foreign-parent"
        scope["parent_ticket_key"] = parent
    elif defect == "shape":
        scope = []
    elif defect.startswith("resume-"):
        scope.update(resume_owner_ref="team:main", resume_condition="Later admission")
        scope.pop("resume_owner_ref" if defect == "resume-owner" else "resume_condition")
    elif defect in {"active", "failed"}:
        status = "in-progress" if defect == "active" else "failed"
    patch = {"status": status, "metadata_json": {"delivery_scope": scope}}
    if defect in {"foreign-task", "self-parent"}:
        patch["parent_ticket_key"] = parent
    before = repo.brief(project_id=project_id, ticket_key="optional").ticket.model_dump()
    for dry_run in (True, False):
        with pytest.raises(ValidationError, match="delivery_scope"):
            repo.update_ticket(
                project_id=project_id, ticket_key="optional", patch_json=patch, dry_run=dry_run
            )
        session.commit()  # Invalid mutations cannot hide behind a caller rollback.
        assert (
            repo.brief(project_id=project_id, ticket_key="optional").ticket.model_dump() == before
        )


@pytest.mark.parametrize("change", ["reactivate", "reparent"])
def test_child_disposition_clears_on_reactivation_or_reparent(session, project_id, change):
    repo, _plans, _task_key, _parent, scope, _state = disposition_workflow(session, project_id)
    repo.update_ticket(
        project_id=project_id,
        ticket_key="optional",
        patch_json={"status": "skipped", "metadata_json": {"keep": 1, "delivery_scope": scope}},
    )
    patch = (
        {"status": "not-started"} if change == "reactivate" else {"parent_ticket_key": "required"}
    )
    repo.update_ticket(project_id=project_id, ticket_key="optional", patch_json=patch)
    optional = repo.brief(project_id=project_id, ticket_key="optional").ticket
    assert optional.metadata_json == {"keep": 1}
    revisions = session.exec(
        select(TrackerRevision).where(TrackerRevision.entity_key == "optional")
    ).all()
    assert any(
        ((r.before_json or {}).get("metadata_json") or {}).get("delivery_scope") == scope
        for r in revisions
    )


def test_excluded_parent_does_not_hide_nested_required_work_or_complete_group(session, project_id):
    from stackos import task_delivery

    repo, plans, task_key, _parent, scope, state = disposition_workflow(session, project_id)
    task_delivery._select_work(repo, plans, project_id, task_key, state)
    repo.update_ticket(project_id=project_id, ticket_key="required", patch_json={"kind": "group"})
    repo.create_ticket(
        project_id=project_id,
        task_key=task_key,
        key="nested",
        title="Nested",
        parent_ticket_key="required",
        status="failed",
        run_plan_id=state["run_plan_id"],
        run_plan_step_id=plans.get_step(state["run_plan_id"], "verify").id,
    )
    for key in ("required", "optional"):
        repo.update_ticket(
            project_id=project_id,
            ticket_key=key,
            patch_json={"status": "skipped", "metadata_json": {"delivery_scope": scope}},
        )
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        False,
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="required", patch_json={"status": "not-started"}
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="nested", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="nested",
        patch_json={
            "status": "skipped",
            "metadata_json": {"delivery_scope": {**scope, "parent_ticket_key": "required"}},
        },
    )
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        True,
    )
    assert repo.brief(project_id=project_id, ticket_key="required").ticket.status == "in-progress"
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        False,
    )


@pytest.mark.parametrize("excluded_status", ["skipped", "aborted", "deferred"])
def test_mixed_group_settles_required_work_without_completing_excluded_child(
    session, project_id, excluded_status
):
    from stackos import task_delivery

    repo, plans, task_key, parent, scope, state = disposition_workflow(session, project_id)
    task_delivery._select_work(repo, plans, project_id, task_key, state)
    repo.update_ticket(project_id=project_id, ticket_key="required", patch_json={"kind": "group"})
    repo.create_ticket(
        project_id=project_id,
        task_key=task_key,
        key="nested-required",
        title="Required work in group",
        parent_ticket_key="required",
        dependency_keys=["required"],
        run_plan_id=state["run_plan_id"],
        run_plan_step_id=plans.get_step(state["run_plan_id"], "verify").id,
    )
    repo.update_ticket(
        project_id=project_id, ticket_key="optional", patch_json={"parent_ticket_key": "required"}
    )
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        True,
    )
    for status in ("in-progress", "complete"):
        repo.update_ticket(
            project_id=project_id, ticket_key="nested-required", patch_json={"status": status}
        )
    scope.update(
        parent_ticket_key="required",
        resume_owner_ref="team:main",
        resume_condition="Later scope admission",
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="optional",
        patch_json={"status": excluded_status, "metadata_json": {"delivery_scope": scope}},
    )
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        True,
    )
    assert repo.brief(project_id=project_id, ticket_key="required").ticket.status == "complete"
    assert repo.brief(project_id=project_id, ticket_key="optional").ticket.status == excluded_status
    chosen, step, changed = task_delivery._select_work(repo, plans, project_id, task_key, state)
    assert (chosen.key, step.step_id, changed) == (parent, "verify", False)
    # Group settlement does not waive a real downstream success dependency.
    reviewer = f"{task_key}-review"
    repo.update_ticket(
        project_id=project_id,
        ticket_key=reviewer,
        patch_json={"add_dependency_keys": ["required", "optional"]},
    )
    assert (
        "required" not in repo.brief(project_id=project_id, ticket_key=reviewer).ticket.blocked_by
    )
    assert "optional" in repo.brief(project_id=project_id, ticket_key=reviewer).ticket.blocked_by
    # Clearing a nested decision still reinstates the obligation at the verifier.
    repo.update_ticket(
        project_id=project_id, ticket_key="optional", patch_json={"metadata_json": None}
    )
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        False,
    )


@pytest.mark.parametrize("bound", [True, False])
def test_recovery_clears_reactivated_exclusion_without_losing_decision_history(
    session, project_id, bound
):
    from stackos import task_delivery
    from stackos.db.models import TrackerTicket

    repo, plans, task_key, parent, scope, state = disposition_workflow(session, project_id)
    task_delivery._select_work(repo, plans, project_id, task_key, state)
    for status in ("in-progress", "complete"):
        repo.update_ticket(
            project_id=project_id, ticket_key="required", patch_json={"status": status}
        )
    repo.create_ticket(
        project_id=project_id,
        task_key=task_key,
        key="withdrawn",
        title="Already withdrawn",
        status="aborted",
        parent_ticket_key=parent,
        run_plan_id=state["run_plan_id"],
        run_plan_step_id=plans.get_step(state["run_plan_id"], "verify").id,
        metadata_json={"delivery_scope": scope},
    )
    if not bound:
        # Existing recovery supports legacy run-linked children without a bound step.
        row = session.exec(select(TrackerTicket).where(TrackerTicket.key == "optional")).one()
        row.run_plan_step_id = None
        session.add(row)
        session.commit()
    plans.abort(
        run_plan_id=state["run_plan_id"],
        project_id=project_id,
        reason="daemon-restart-orphan",
        actor="system",
    )
    optional = repo.brief(project_id=project_id, ticket_key="optional").ticket
    assert optional.status == "aborted" and "workflow_auto_abort" in optional.metadata_json
    repo.update_ticket(
        project_id=project_id,
        ticket_key="optional",
        patch_json={
            "metadata_json": {**optional.metadata_json, "keep": 1, "delivery_scope": scope}
        },
    )
    plans.recover(
        run_plan_id=state["run_plan_id"],
        project_id=project_id,
        step_id="verify",
        step_status=RunPlanStepStatus.PENDING,
        reason="tracker graph topology repair",
    )
    optional = repo.brief(project_id=project_id, ticket_key="optional").ticket
    assert optional.status == "not-started"
    assert optional.metadata_json == {"keep": 1}
    withdrawn = repo.brief(project_id=project_id, ticket_key="withdrawn").ticket
    assert withdrawn.status == "aborted" and withdrawn.metadata_json == {"delivery_scope": scope}
    revisions = session.exec(
        select(TrackerRevision).where(TrackerRevision.entity_key == "optional")
    ).all()
    assert any(
        ((r.after_json or {}).get("metadata_json") or {}).get("delivery_scope") == scope
        for r in revisions
    )
    task_delivery._select_work(repo, plans, project_id, task_key, state)
    repo.update_ticket(
        project_id=project_id, ticket_key="optional", patch_json={"status": "skipped"}
    )
    assert task_delivery._select_work(repo, plans, project_id, task_key, state) == (
        None,
        None,
        False,
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="optional",
        patch_json={
            "metadata_json": {
                "keep": 1,
                "delivery_scope": {**scope, "source_ref": "operator-decision:fresh-scope"},
            }
        },
    )
    chosen, step, changed = task_delivery._select_work(repo, plans, project_id, task_key, state)
    assert (chosen.key, step.step_id, changed) == (parent, "verify", False)


@pytest.mark.parametrize("verdict", ["success", "blocked"])
def test_verifier_receives_exclusions_separately_from_accepted_sources(
    session, project_id, tmp_path, monkeypatch, verdict
):
    from stackos import task_delivery

    repo, plans, task_key, parent, scope, state = disposition_workflow(session, project_id)
    plans.claim_step(
        run_plan_id=state["run_plan_id"], run_id=state["controller_run_id"], step_id="verify"
    )
    scope.update(resume_owner_ref="team:main", resume_condition="Later operator admission")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="optional",
        patch_json={"status": "deferred", "metadata_json": {"delivery_scope": scope}},
    )
    reviewer = f"{task_key}-review"
    repo.update_ticket(
        project_id=project_id, ticket_key=reviewer, patch_json={"add_dependency_keys": ["required"]}
    )
    calls = []

    def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        key = packet["brief"]["ticket"]["key"]
        calls.append((key, packet["phase"]))
        if key in {parent, reviewer}:
            excluded = packet["child_dispositions"]
            assert [item["key"] for item in excluded] == ["optional"]
            assert excluded[0]["status"] == "deferred"
            assert excluded[0]["metadata_json"]["delivery_scope"] == scope
            assert excluded[0]["parent_ticket_key"] == parent
            assert excluded[0]["evidence_ref"]["tool_name"] == "tracker.brief"
            assert "optional" not in {r["key"] for r in packet["accepted_receipts"]}
            assert "required" in {r["key"] for r in packet["accepted_receipts"]}
        return AgentRunResult(
            0,
            thread_id=f"{key}-{packet['phase']}",
            result=generic_result(
                verdict if key == parent else "success",
                outputs={"commit_sha": git(tmp_path, "rev-parse", "HEAD")},
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=state["run_plan_id"],
        step_id="verify",
    )
    scheduler.run()
    session.expire_all()
    assert calls[:3] == [("required", "delivery"), ("required", "review"), (parent, "delivery")]
    assert calls[3:] == ([(reviewer, "delivery")] if verdict == "success" else [])
    assert plans.get(state["run_plan_id"]).status == (
        "completed" if verdict == "success" else "started"
    )
    task = repo.get(project_id=project_id, task_key=task_key).tasks[0]
    assert task.metadata_json["delivery"]["status"] == (
        "complete" if verdict == "success" else "stopped"
    )
    assert repo.brief(project_id=project_id, ticket_key="optional").ticket.status == "deferred"


@pytest.mark.parametrize("defect", ["wrong-parent", "reason", "resume", "failed", "pending"])
def test_legacy_invalid_disposition_cannot_release_parent(session, project_id, defect):
    from stackos import task_delivery
    from stackos.db.models import TrackerTicket

    repo, plans, task_key, _parent, scope, state = disposition_workflow(session, project_id)
    task_delivery._select_work(repo, plans, project_id, task_key, state)
    for status in ("in-progress", "complete"):
        repo.update_ticket(
            project_id=project_id, ticket_key="required", patch_json={"status": status}
        )
    row = session.exec(select(TrackerTicket).where(TrackerTicket.key == "optional")).one()
    row.status = {"resume": "deferred", "failed": "failed", "pending": "not-started"}.get(
        defect, "skipped"
    )
    if defect == "wrong-parent":
        scope["parent_ticket_key"] = "required"
    if defect == "reason":
        scope.pop("reason")
    row.metadata_json = {"delivery_scope": scope}
    session.add(row)
    session.commit()
    selected, step, changed = task_delivery._select_work(repo, plans, project_id, task_key, state)
    assert step is None and changed is False
    assert selected is None or selected.key == "optional"  # Pending work remains executable.


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


@pytest.mark.parametrize("phase", ["delivery", "review"])
@pytest.mark.parametrize("late", ["success", "error", "exception"])
def test_abort_fences_late_ordinary_results_and_preserves_completed_evidence(
    session, project_id, tmp_path, monkeypatch, phase, late
):
    from stackos import task_delivery
    from stackos.repositories.base import ConflictError

    plan_id = workflow_task(session, project_id)
    key = f"workflow-{plan_id}"
    repo = TrackerRepository(session)
    bound = repo.workflow_step_ticket_context(
        project_id=project_id, run_plan_id=plan_id, step_id="deliver"
    )
    repo.create_ticket(
        project_id=project_id,
        task_key=key,
        key="prior",
        title="Prior",
        parent_ticket_key=bound["parent_ticket_key"],
        run_plan_id=plan_id,
        run_plan_step_id=bound["run_plan_step_id"],
        status="complete",
        completion_evidence_json={"keep": "accepted"},
    )
    scheduler = QueuedScheduler()
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        calls.append(packet["phase"])
        if packet["phase"] == phase:
            with (
                Session(session.get_bind()) as abort_session,
                pytest.raises(ConflictError, match="could not be confirmed"),
            ):
                task_delivery.abort_run_plan(
                    abort_session,
                    run_plan_id=plan_id,
                    project_id=project_id,
                    reason="operator abort",
                    scheduler=scheduler,
                )
            if late == "exception":
                raise RuntimeError("late parser exception")
            if late == "error":
                return AgentRunResult(1, thread_id="late-session", error="late native error")
        return AgentRunResult(0, thread_id=f"{packet['phase']}-session", result=generic_result())

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=key,
        run_plan_id=plan_id,
        step_id="deliver",
    )
    scheduler.run()
    session.expire_all()
    snapshot = repo.get(project_id=project_id, task_key=key)
    assert snapshot.tasks[0].status == "aborted"
    assert snapshot.tasks[0].metadata_json["delivery"]["reason"] == "operator abort"
    tickets = {ticket.key: ticket for ticket in snapshot.tickets}
    assert tickets["prior"].status == "complete"
    assert tickets["prior"].completion_evidence_json == {"keep": "accepted"}
    assert tickets["selected"].status == "aborted"
    assert tickets["other-step"].status == "aborted"
    evidence = tickets["selected"].completion_evidence_json["automatic_delivery"]
    assert evidence.get("cancellation") or evidence[phase]["cancelled"]
    assert calls == (["delivery"] if phase == "delivery" else ["delivery", "review"])
    assert RunPlanRepository(session).get(plan_id).status == "aborted"
    assert all(step.status == "skipped" for step in RunPlanRepository(session).get(plan_id).steps)


@pytest.mark.parametrize("phase", ["delivery", "review"])
def test_native_cancellation_reaches_receipt_and_statistics_without_invalid_result(
    session, project_id, tmp_path, monkeypatch, phase
):
    """Real adapter/receipt/projection; only native process I/O is controlled."""
    from stackos import task_delivery
    from stackos.host_mcp import progress
    from stackos.host_mcp.adapters import codex

    plan_id = workflow_task(session, project_id)
    task_key = f"workflow-{plan_id}"
    scheduler = QueuedScheduler()
    calls = []
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")

    def stream(command, *, input, on_session, process_control, **kwargs):
        packet = json.loads(input)
        current_phase = packet["phase"]
        calls.append(current_phase)
        on_session(f"native-{current_phase}")
        if current_phase == phase:
            with Session(session.get_bind()) as abort_session:
                task_delivery.abort_run_plan(
                    abort_session,
                    run_plan_id=plan_id,
                    project_id=project_id,
                    reason="isolated fixture operator cancellation",
                    scheduler=scheduler,
                )
            assert process_control.cancelled.is_set()
        else:
            Path(command[command.index("-o") + 1]).write_text(
                json.dumps(
                    generic_result(outputs={"commit_sha": git(tmp_path, "rev-parse", "HEAD")})
                )
            )
        return subprocess.CompletedProcess(
            command,
            -15 if current_phase == phase else 0,
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 5}}
            ),
            "",
        )

    monkeypatch.setattr(progress, "run_codex_process", stream)
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=task_key,
        run_plan_id=plan_id,
        step_id="deliver",
    )
    scheduler.run()
    session.expire_all()
    repo = TrackerRepository(session)
    ticket = repo.brief(project_id=project_id, ticket_key="selected").ticket
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    receipt = evidence[phase]
    assert ticket.status == "aborted"
    assert receipt["cancelled"] is True and receipt["validated_outcome"] is None
    assert receipt["error"] == "Codex invocation cancelled by managed process control."
    assert receipt["exit_code"] == -15 and receipt["thread_id"] == f"native-{phase}"
    assert receipt["result"] is None and receipt["usage"]["measured"]["total"] == 105
    assert evidence["repair_count"] == 0
    stats = repo.agent_stats(project_id=project_id).totals
    # Analytics omit the bounded timeline/last invocation carried by work views.
    assert stats.model_dump(exclude={"samples", "last_execution"}) == ticket.agent_stats.model_dump(
        exclude={"samples", "last_execution"}
    )
    assert stats.launch_attempts == len(calls) == (1 if phase == "delivery" else 2)
    assert stats.reported_usage.total == 105 * len(calls)
    assert stats.review_outcomes == {} and stats.recorded_reviewer_rejects == 0
    assert stats.coverage.classification_unknown_attempts == 1
    assert stats.coverage.classification_known_attempts == (0 if phase == "delivery" else 1)


def test_abort_queued_callback_scope_terminal_and_external_state_only(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery
    from stackos.repositories.base import ConflictError, NotFoundError

    plan_id = workflow_task(session, project_id)
    key = f"workflow-{plan_id}"
    scheduler = QueuedScheduler()
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=key,
        run_plan_id=plan_id,
        step_id="deliver",
    )
    function, options = scheduler.jobs[0]
    with pytest.raises((NotFoundError, ValidationError)):
        task_delivery.abort_run_plan(session, run_plan_id=plan_id, project_id=project_id + 999)
    assert RunPlanRepository(session).get(plan_id).status == "started"
    env, cancellation = task_delivery.abort_run_plan(
        session, run_plan_id=plan_id, project_id=project_id, scheduler=scheduler
    )
    assert env.data.status == "aborted"
    assert cancellation["status"] == "state_only"
    assert not scheduler.jobs
    monkeypatch.setattr(task_delivery, "run_agent", lambda *a, **kw: pytest.fail("late launch"))
    function(**options["kwargs"])
    assert task_delivery.abort_run_plan(session, run_plan_id=plan_id)[1]["status"] == "state_only"
    plans = RunPlanRepository(session)
    plans.reopen(
        run_plan_id=plan_id,
        project_id=project_id,
        step_id="deliver",
        reason="Isolated queued callback reopen without a fresh delivery handoff",
    )
    before_plan = plans.get(plan_id).model_dump(mode="json")
    before_task = TrackerRepository(session).get(project_id=project_id, task_key=key)
    function(**options["kwargs"])
    session.expire_all()
    assert plans.get(plan_id).model_dump(mode="json") == before_plan
    assert TrackerRepository(session).get(project_id=project_id, task_key=key) == before_task
    completed = (
        RunPlanRepository(session)
        .create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "already-done",
                "title": "Done",
                "steps": [{"id": "one", "title": "One"}],
            },
        )
        .data
    )
    started = RunPlanRepository(session).start(completed.id, project_id=project_id)
    RunPlanRepository(session).claim_step(run_plan_id=completed.id, run_id=started.run_id)
    RunPlanRepository(session).record_step(
        run_plan_id=completed.id,
        run_id=started.run_id,
        step_id="one",
        status=RunPlanStepStatus.SUCCESS,
    )
    with pytest.raises(ConflictError, match="terminal"):
        task_delivery.abort_run_plan(session, run_plan_id=completed.id)


def test_dequeued_callback_cannot_use_a_new_delivery_handoff(
    session, project_id, tmp_path, monkeypatch
):
    from stackos import task_delivery

    prepared_task(session, project_id)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    function, old = scheduler.jobs.pop()
    repo = TrackerRepository(session)
    task_delivery._update_delivery(repo, project_id, "deliver", status="stopped")
    with pytest.raises(ValidationError, match="still retiring"):
        submit(session, project_id, tmp_path, scheduler)
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        calls.append(packet["phase"])
        return AgentRunResult(0, thread_id=f"fresh-{len(calls)}", result=generic_result("blocked"))

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    # The dequeued callback must retire before its handoff can be replaced.
    function(**old["kwargs"])
    assert not calls
    submit(session, project_id, tmp_path, scheduler)
    current = repo.get(project_id=project_id, task_key="deliver")
    function(**old["kwargs"])
    session.expire_all()
    assert repo.get(project_id=project_id, task_key="deliver") == current
    assert not calls
    scheduler.run()
    assert calls == ["delivery"]


@pytest.mark.parametrize("registration", ["success", "error-before", "error-after"])
@pytest.mark.parametrize("rehandoff", [False, True])
def test_abort_during_scheduler_registration_preserves_only_current_handoff(
    delivery_engine, project_id, tmp_path, monkeypatch, registration, rehandoff
):
    from threading import Lock

    from apscheduler.executors.pool import ThreadPoolExecutor as APSExecutor
    from apscheduler.jobstores.memory import MemoryJobStore
    from apscheduler.schedulers.background import BackgroundScheduler

    from stackos import task_delivery

    ready, release, lock = Event(), Event(), Lock()
    actual = BackgroundScheduler(
        jobstores={"memory": MemoryJobStore()}, executors={"task-delivery": APSExecutor(1)}
    )
    actual.start(paused=True)
    calls, old_jobs = [], []

    class BarrierScheduler:
        count = 0
        _executors = actual._executors
        _executors_lock = actual._executors_lock
        add_executor = actual.add_executor

        def add_job(self, function, **options):
            with lock:
                self.count += 1
                first = self.count == 1
            if first:
                old_jobs.append((function, options))
                ready.set()
                assert release.wait(10)
                if registration == "error-before":
                    raise RuntimeError("Fixture registration failure")
            job = actual.add_job(function, **options)
            if first and registration == "error-after":
                raise RuntimeError("Fixture registration failure after insert")
            return job

        def remove_job(self, *args, **kwargs):
            return actual.remove_job(*args, **kwargs)

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        calls.append(packet["brief"]["ticket"]["key"])
        return AgentRunResult(0, thread_id="fresh-session", result=generic_result("blocked"))

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = BarrierScheduler()
    try:
        with Session(delivery_engine) as session:
            plans = RunPlanRepository(session)
            plan_id = workflow_task(session, project_id)
            key = f"workflow-{plan_id}"

        def first_handoff():
            with Session(delivery_engine) as own_session:
                return submit(
                    own_session,
                    project_id,
                    tmp_path,
                    scheduler,
                    task_key=key,
                    run_plan_id=plan_id,
                    step_id="deliver",
                )

        with ThreadPoolExecutor() as workers:
            old = workers.submit(first_handoff)
            assert ready.wait(5)
            with Session(delivery_engine) as session:
                _, cancellation = task_delivery.abort_run_plan(
                    session, run_plan_id=plan_id, project_id=project_id, scheduler=scheduler
                )
                assert cancellation["status"] == "state_only"
                plans = RunPlanRepository(session)
                repo = TrackerRepository(session)
                if rehandoff:
                    plans.reopen(
                        run_plan_id=plan_id,
                        project_id=project_id,
                        step_id="deliver",
                        reason="Isolated fresh work after abort during registration",
                    )
                    plans.claim_step(
                        run_plan_id=plan_id,
                        project_id=project_id,
                        run_id=plans.get(plan_id).run_id,
                        step_id="deliver",
                    )
                    repo.create_ticket(
                        project_id=project_id,
                        task_key=key,
                        key="fresh-worker",
                        title="Fresh isolated work",
                        parent_ticket_key=f"{key}-deliver",
                        run_plan_id=plan_id,
                        run_plan_step_id=plans.get_step(plan_id, "deliver").id,
                    )
                    current = submit(
                        session,
                        project_id,
                        tmp_path,
                        scheduler,
                        task_key=key,
                        run_plan_id=plan_id,
                        step_id="deliver",
                    )
                before = repo.get(project_id=project_id, task_key=key)
                before_plan = plans.get(plan_id).model_dump(mode="json")
                release.set()
                if registration == "success":
                    assert old.result(timeout=5)["status"] == "stopped"
                else:
                    with pytest.raises(RuntimeError, match="Fixture registration failure"):
                        old.result(timeout=5)
                session.expire_all()
                assert repo.get(project_id=project_id, task_key=key) == before
                assert plans.get(plan_id).model_dump(mode="json") == before_plan
                function, options = old_jobs[0]
                assert actual.get_job(options["id"], jobstore="memory") is None
                # Even an already-dequeued stale registration must remain inert.
                function(**options["kwargs"])
                session.expire_all()
                assert repo.get(project_id=project_id, task_key=key) == before
                assert not calls
                jobs = actual.get_jobs(jobstore="memory")
                assert len(jobs) == int(rehandoff)
                if rehandoff:
                    assert jobs[0].id == current["job_ref"]
                    jobs[0].func(**jobs[0].kwargs)
                    assert calls == ["fresh-worker"]
    finally:
        release.set()
        actual.shutdown(wait=True)


def test_abort_real_managed_invocation_through_lifecycle_coordinator(
    delivery_engine, project_id, tmp_path, monkeypatch, request
):
    from dataclasses import replace

    from stackos import task_delivery
    from stackos.host_mcp import service

    session = Session(delivery_engine)
    request.addfinalizer(session.close)
    plan_id = workflow_task(session, project_id)
    scheduler = QueuedScheduler()
    ready = Event()
    definition = service._definition_by_key("codex")
    native = []

    def execute(*args, process_control, **kwargs):
        process = process_control.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
        native.append(process)
        ready.set()
        process.wait(timeout=10)
        return AgentRunResult(process.returncode, thread_id="retained-managed-session")

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=execute)
    )
    submit(
        session,
        project_id,
        tmp_path,
        scheduler,
        task_key=f"workflow-{plan_id}",
        run_plan_id=plan_id,
        step_id="deliver",
    )
    with ThreadPoolExecutor() as worker:
        running = worker.submit(scheduler.run)
        assert ready.wait(3), (
            TrackerRepository(session)
            .get(project_id=project_id, task_key=f"workflow-{plan_id}")
            .tasks[0]
            .metadata_json
        )
        assert native[0].poll() is None
        assert service.usage_executions(project_id)
        try:
            env, cancellation = task_delivery.abort_run_plan(
                session, run_plan_id=plan_id, project_id=project_id, scheduler=scheduler
            )
        except Exception as error:
            pytest.fail(str(getattr(error, "data", error)))
        assert env.data.status == "aborted"
        assert cancellation == {
            "status": "managed_stopped",
            "invocation_count": 1,
            "stopped_count": 1,
            "prevented_count": 0,
            "external_sessions_terminated": False,
        }
        running.result(timeout=3)
    session.expire_all()
    ticket = TrackerRepository(session).brief(project_id=project_id, ticket_key="selected").ticket
    assert ticket.status == "aborted" and native[0].poll() is not None
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    assert evidence["delivery"]["thread_id"] == "retained-managed-session"
    assert evidence["delivery"]["cancelled"]
    assert evidence["delivery"]["usage"]["status"] != "measured"
    assert not service.usage_executions(project_id)


@pytest.mark.parametrize("late", ["success", "error", "exception"])
@pytest.mark.parametrize("phase", ["delivery", "review"])
@pytest.mark.parametrize("retired", [False, True])
@pytest.mark.parametrize("rehandoff", [False, True])
def test_abort_stays_cancelled_after_reopen_and_native_retirement(
    delivery_engine, project_id, tmp_path, monkeypatch, late, phase, retired, rehandoff
):
    from dataclasses import replace

    from stackos import task_delivery
    from stackos.host_mcp import service
    from stackos.repositories.base import ConflictError

    ready, validating, release = Event(), Event(), Event()
    definition = service._definition_by_key("codex")
    original_validate = task_delivery.validate_agent_result
    calls, native, launch_guards, controls = [], [], [], []

    def launch(*args, **kwargs):
        launch_guards.append(kwargs["launch_allowed"])
        return service.run_agent(*args, **kwargs)

    def execute(*args, process_control, **kwargs):
        packet = json.loads(args[2])
        calls.append(packet["phase"])
        if packet["phase"] == phase:
            process = process_control.spawn(
                [
                    sys.executable,
                    "-c",
                    "import time; print('ready', flush=True); "
                    + ("pass" if retired else "time.sleep(30)"),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            native.append(process)
            controls.append(process_control)
            assert process.stdout.readline().strip() == "ready"
            process.stdout.close()
            ready.set()
            process.wait(timeout=10)
            if late == "error":
                return AgentRunResult(-15, thread_id=f"retained-{phase}", error="late native error")
        return AgentRunResult(0, thread_id=f"retained-{packet['phase']}", result=generic_result())

    def validate(result, *args, **kwargs):
        if result.thread_id == f"retained-{phase}":
            validating.set()
            assert release.wait(10)
            if late == "exception":
                raise RuntimeError("late validation exception after reopen")
        return original_validate(result, *args, **kwargs)

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=execute)
    )
    monkeypatch.setattr(task_delivery, "validate_agent_result", validate)
    monkeypatch.setattr(task_delivery, "run_agent", launch)
    try:
        with Session(delivery_engine) as session:
            plans = RunPlanRepository(session)
            plan_id = workflow_task(session, project_id)
            task_key = f"workflow-{plan_id}"
            scheduler = QueuedScheduler()
            submit(
                session,
                project_id,
                tmp_path,
                scheduler,
                task_key=task_key,
                run_plan_id=plan_id,
                step_id="deliver",
            )
            with ThreadPoolExecutor() as workers:
                running = workers.submit(scheduler.run)
                assert ready.wait(5)
                try:
                    if retired:
                        assert validating.wait(3)
                        assert native[0].poll() == 0
                        assert not service.usage_executions(project_id)
                    try:
                        _, cancellation = task_delivery.abort_run_plan(
                            session, run_plan_id=plan_id, project_id=project_id, scheduler=scheduler
                        )
                    except Exception as error:
                        if not retired or not isinstance(error, ConflictError):
                            raise AssertionError(
                                {
                                    "abort": getattr(error, "data", str(error)),
                                    "stop_errors": [control.stop_error for control in controls],
                                }
                            ) from error
                        assert error.data["status"] == "aborted"
                        cancellation = error.data["cancellation"]
                    assert cancellation["status"] == (
                        "stop_unconfirmed" if retired else "managed_stopped"
                    )
                    assert cancellation["stopped_count"] == (0 if retired else 1)
                    assert native[0].poll() == (0 if retired else -15)
                    assert not service.usage_executions(project_id)
                    assert validating.wait(3)
                    plans.reopen(
                        run_plan_id=plan_id,
                        project_id=project_id,
                        step_id="deliver",
                        reason="Explicit isolated fixture reopen after abort",
                    )
                    if rehandoff:
                        plans.claim_step(
                            run_plan_id=plan_id,
                            project_id=project_id,
                            run_id=plans.get(plan_id).run_id,
                            step_id="deliver",
                        )
                        if retired:
                            # No registered control could confirm the stop. A
                            # replacement must wait for this callback's release.
                            with pytest.raises(ValidationError, match="still retiring"):
                                submit(
                                    session,
                                    project_id,
                                    tmp_path,
                                    scheduler,
                                    task_key=task_key,
                                    run_plan_id=plan_id,
                                    step_id="deliver",
                                )
                        else:
                            submit(
                                session,
                                project_id,
                                tmp_path,
                                scheduler,
                                task_key=task_key,
                                run_plan_id=plan_id,
                                step_id="deliver",
                            )
                    assert not launch_guards[-1]()
                    before = TrackerRepository(session).get(
                        project_id=project_id, task_key=task_key
                    )
                    statuses = {ticket.key: ticket.status for ticket in before.tickets}
                    task_state = before.tasks[0].model_dump(
                        exclude={"agent_stats", "active_execution"}
                    )
                    release.set()
                    running.result(timeout=5)
                finally:
                    release.set()
            session.expire_all()
            after = TrackerRepository(session).get(project_id=project_id, task_key=task_key)
            assert calls == (["delivery"] if phase == "delivery" else ["delivery", "review"])
            assert {ticket.key: ticket.status for ticket in after.tickets} == statuses
            after_task = after.tasks[0].model_dump(exclude={"agent_stats", "active_execution"})
            if retired:
                # Only the original reservation's retirement may change task
                # state after an unconfirmed abort; all lifecycle/evidence
                # fields remain fenced against the late native outcome.
                assert task_state["metadata_json"]["delivery"]["slot_reserved"]
                assert after_task["metadata_json"]["delivery"]["slot_reserved"] is False
                assert after_task["updated_at"] >= task_state["updated_at"]
                task_state["metadata_json"]["delivery"]["slot_reserved"] = False
                task_state["metadata_json"]["delivery"]["updated_at"] = after_task["metadata_json"][
                    "delivery"
                ]["updated_at"]
                task_state["updated_at"] = after_task["updated_at"]
            assert after_task == task_state
            assert plans.get(plan_id).status == "started"
            assert plans.get_step(plan_id, "deliver").status == (
                RunPlanStepStatus.RUNNING if rehandoff else RunPlanStepStatus.PENDING
            )
            evidence = next(
                ticket for ticket in after.tickets if ticket.key == "selected"
            ).completion_evidence_json["automatic_delivery"]
            if rehandoff and not retired:
                assert after.tickets == before.tickets
            else:
                assert evidence.get("cancellation") or evidence[phase]["cancelled"]
            assert evidence[phase].get("validated_outcome") is None
            if rehandoff and retired:
                retained_evidence = deepcopy(evidence)
                submit(
                    session,
                    project_id,
                    tmp_path,
                    scheduler,
                    task_key=task_key,
                    run_plan_id=plan_id,
                    step_id="deliver",
                )
                assert (
                    TrackerRepository(session)
                    .brief(project_id=project_id, ticket_key="selected")
                    .ticket.completion_evidence_json["automatic_delivery"]
                    == retained_evidence
                )
    finally:
        release.set()
        for process in native:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        service.shutdown_usage()


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


@pytest.mark.parametrize("decision", ["approve", "blocked", "reject", "failed"])
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
    assert (
        snapshot.tickets[0].status
        == {
            "approve": "complete",
            "blocked": "in-progress",
            "reject": "failed",
            "failed": "failed",
        }[decision]
    )
    assert snapshot.tasks[0].metadata_json["delivery"]["status"] == (
        "complete" if decision == "approve" else "stopped"
    )
    assert len(calls) == {"blocked": 2, "approve": 4, "reject": 8, "failed": 2}[decision]
    if decision != "approve":
        assert snapshot.tickets[1].status == "not-started"
    context = snapshot.tasks[0].context_json
    assert context["operator_inputs"] == ["Keep input"] and context["other"] == "preserved"
    notes = context["followups"]
    assert len(notes) == {"blocked": 2, "approve": 3, "reject": 5, "failed": 2}[decision]
    assert notes[1]["text"] == "SIDE NOTE first 2"
    assert notes[1]["source_ticket_key"] == "first"
    assert notes[1]["source_attempt"] == 1
    assert notes[1]["created_at"]
    if decision == "blocked":
        ticket = snapshot.tickets[0]
        assert ticket.blocker_reason == ticket.outcome == "Scoped repair"
        assert ticket.completed_at is None
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert evidence["repair_count"] == 0
        assert evidence["review"]["result"]["status"] == "blocked"
        assert evidence["review"]["validated_outcome"] == "blocked"
    elif decision == "failed":
        ticket = snapshot.tickets[0]
        assert ticket.blocker_reason is None
        assert ticket.completed_at is not None
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert evidence["repair_count"] == 0
        assert evidence["review"]["validated_outcome"] == "failed"


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
    assert [ticket.status for ticket in stopped.tickets] == ["in-progress", "not-started"]
    assert stopped.tickets[0].blocker_reason == "Required check unavailable"
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
        ("missing-review-id", "blocked", 2),
        ("shared-id", "blocked", 2),
        ("schema", "blocked", 2),
        ("native-final", "blocked", 2),
        ("stale-review", "blocked", 2),
        ("wrong-review", "blocked", 2),
        ("dirty-review", "blocked", 2),
        ("mutating-review", "blocked", 2),
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
    if decision == "blocked" and fault in {
        "stale-review",
        "wrong-review",
        "dirty-review",
        "mutating-review",
    }:
        assert snapshot.tickets[0].blocker_reason is None
        assert snapshot.tickets[0].completed_at is not None
        assert evidence["review"]["validation_error"]
        assert evidence["repair_count"] == 0
        assert evidence["candidate"] == calls[-1]["packet"]["candidate"]
        assert evidence["sessions"]["review"]["thread_id"] == "first-review"
    if fault == "native-final":
        assert evidence["review"]["result"]["status"] == (
            "blocked" if decision == "blocked" else "repair"
        )
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


@pytest.mark.parametrize(
    ("consumed", "fault"),
    [
        (0, None),
        (1, None),
        (3, None),
        (1, "source-descendant"),
        (0, "source-repair"),
        (0, "source-descendant"),
        (3, "rejected-proof"),
    ]
    + [
        (1, fault)
        for fault in (
            "source-repair",
            "legacy",
            "prose",
            "stale-intent",
            "wrong-base",
            "dirty",
            "wrong-head",
            "wrong-review",
            "stale-approval",
            "changed-source",
            "rejected-proof",
            "changed-intent",
        )
    ],
)
def test_explicit_evidence_only_recovery_retains_exact_candidate_and_history(
    session, project_id, tmp_path, monkeypatch, consumed, fault
):
    from copy import deepcopy

    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Proof recovery")
    repo.create_ticket(
        project_id=project_id,
        task_key="deliver",
        key="first",
        title="Source and proof",
        context_json={"keep": "input"},
    )
    recovering = False
    calls = []

    def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
        packet = json.loads(prompt)
        phase = packet["phase"]
        prior = [call for call in calls if call["phase"] == phase]
        assert kwargs.get("resume_thread_id") == (f"first-{phase}" if prior else None)
        calls.append(packet)
        status = "success"
        sha = git(tmp_path, "rev-parse", "HEAD")
        if phase == "delivery":
            if (not recovering and (consumed > 0 or fault is None)) or (
                recovering and fault in {"changed-source", "source-descendant"}
            ):
                sha = commit(tmp_path, f"source-{len(calls)}")
            elif fault == "dirty":
                (tmp_path / "unfinished").write_text("dirty")
            elif fault == "wrong-head":
                sha = git(tmp_path, "rev-parse", "HEAD~1")
            elif fault == "changed-intent":
                with Session(session.get_bind()) as active:
                    TrackerRepository(active).update_ticket(
                        project_id=project_id,
                        ticket_key="first",
                        patch_json={"context_json": {"correction": "superseded"}},
                    )
        elif not recovering and len(prior) < consumed:
            status = "repair"
        elif recovering:
            if fault == "wrong-review":
                sha = git(tmp_path, "rev-parse", "HEAD~1")
            elif fault == "stale-approval":
                status = "blocked"  # Old exact-SHA approval cannot replace current proof.
            elif fault == "rejected-proof":
                status = "repair"
        return AgentRunResult(
            0,
            thread_id=f"first-{phase}",
            result=generic_result(status, {"commit_sha": sha}, "Fresh proof"),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert ticket.status == "complete"
    original = deepcopy(ticket.completion_evidence_json["automatic_delivery"])
    assert original["repair_count"] == consumed
    sha = original["candidate"]["commit_sha"]
    correction = {
        "source_ticket": "integrated-proof",
        "check": "missing browser evidence",
        "work_intent": "source_repair"
        if fault in {"source-repair", "source-descendant"}
        else "evidence_only",
        "candidate_commit_sha": sha,
        "target_attempt": original["attempt"] + 1,
    }
    if fault in {"legacy", "prose"}:
        correction.pop("work_intent")
        correction["required_outcome"] = "evidence_only; no source changes"
    elif fault == "stale-intent":
        correction["target_attempt"] -= 1
    elif fault == "wrong-base":
        commit(tmp_path, "unrelated-new-head")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={
            "status": "in-progress",
            "context_json": {
                **ticket.context_json,
                "correction": correction,
            },
        },
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"status": "not-started"},
    )
    recovering = True
    prior_calls = len(calls)
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == min(3, consumed + int(fault == "rejected-proof"))
    assert evidence["sessions"] == original["sessions"]
    assert evidence["attempts"][: len(original["attempts"])] == original["attempts"]
    assert evidence["attempts"][len(original["attempts"])]["review"] == original["review"]
    if fault != "changed-intent":
        assert ticket.context_json["keep"] == "input"
    if fault == "source-descendant":
        assert ticket.status == "complete"
        descendant = evidence["candidate"]["commit_sha"]
        assert descendant != sha
        assert git(tmp_path, "merge-base", "--is-ancestor", sha, descendant) == ""
        assert evidence["review"]["validated_outcome"] == "success"
        return
    if fault == "rejected-proof" and consumed == 3:
        assert ticket.status == "failed"
        assert "repair limit" in ticket.outcome.lower()
        assert len(calls) == prior_calls + 2
        assert evidence["review"]["validated_outcome"] == "repair"
        return
    if fault is None:
        assert ticket.status == "complete"
        assert len(calls) == prior_calls + 2  # Mandatory independent current review.
        assert git(tmp_path, "rev-parse", "HEAD") == sha
        assert evidence["candidate"] == {"base_commit_sha": sha, "commit_sha": sha}
        for phase in ("delivery", "review"):
            assert evidence[phase]["occurrence"]["correction_intent"] == {
                key: correction[key]
                for key in ("work_intent", "candidate_commit_sha", "target_attempt")
            }
            assert evidence[phase]["validated_outcome"] == "success"
        if consumed == 0:
            # A successful proof-only occurrence must not erase the source-change
            # obligation for a later ambiguous/legacy correction.
            repo.update_ticket(
                project_id=project_id,
                ticket_key="first",
                patch_json={"status": "in-progress", "context_json": {"correction": "repair"}},
            )
            repo.update_ticket(
                project_id=project_id,
                ticket_key="first",
                patch_json={"status": "not-started"},
            )
            submit(session, project_id, tmp_path, scheduler)
            scheduler.run()
            session.expire_all()
            legacy = repo.brief(project_id=project_id, ticket_key="first").ticket
            assert legacy.status == "failed"
            assert (
                legacy.completion_evidence_json["automatic_delivery"]["delivery"][
                    "validation_error"
                ]
                == "Repair requires a new descendant commit"
            )
    else:
        assert ticket.status != "complete"
        assert (
            repo.get(project_id=project_id, task_key="deliver")
            .tasks[0]
            .metadata_json["delivery"]["status"]
            == "stopped"
        )
        if fault == "stale-approval":
            assert ticket.status == "in-progress"
            assert evidence["review"]["validated_outcome"] == "blocked"
        elif fault == "wrong-base":
            assert len(calls) == prior_calls
            assert "delivery" not in evidence and "review" not in evidence
            assert "prior candidate and base" in ticket.outcome
        else:
            receipt = evidence["review" if fault == "wrong-review" else "delivery"]
            assert receipt["validated_outcome"] is None
            assert receipt["validation_error"]


@pytest.mark.parametrize(
    "case",
    [
        "missing-prior",
        "wrong-intent",
        "wrong-base",
        "pending-repair",
        "unapproved",
        "stale-intent",
        "dirty-required",
    ],
)
def test_candidate_correction_admission_before_native_execution(
    session, project_id, tmp_path, monkeypatch, case
):
    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Candidate admission")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    base = git(tmp_path, "rev-parse", "HEAD")
    if case in {"missing-prior", "dirty-required"}:
        (tmp_path / "baseline").write_text("unrelated staged edit")
        git(tmp_path, "add", "baseline")
        (tmp_path / "baseline").write_text("unrelated unstaged edit")
        (tmp_path / "unrelated").write_text("preserve this file")
    recovering = False
    calls = []

    def launch(*args, **kwargs):
        packet = json.loads(args[3])
        phase = packet["phase"]
        calls.append(packet)
        sha = git(tmp_path, "rev-parse", "HEAD")
        if (
            phase == "delivery"
            and case not in {"missing-prior", "dirty-required"}
            and (not recovering or case == "stale-intent")
        ):
            sha = commit(tmp_path, f"source-{len(calls)}")
        return AgentRunResult(
            0,
            thread_id=f"first-{phase}",
            result=generic_result(
                "blocked" if phase == "review" and not recovering else "success",
                {} if case == "missing-prior" else {"commit_sha": sha},
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    original = deepcopy(ticket.completion_evidence_json["automatic_delivery"])
    sha = original["candidate"].get("commit_sha", base)
    if case == "pending-repair":
        # Seed a retired ordinary rejection to prove admission precedes charging
        # its next repair. The candidate and native sessions came from the loop.
        original["review"]["validated_outcome"] = "repair"
        original["review"]["result"]["status"] = "repair"
    correction = {
        "work_intent": "source_repair" if case == "dirty-required" else "evidence_only",
        "candidate_commit_sha": base if case in {"wrong-intent", "pending-repair"} else sha,
        "target_attempt": original["attempt"] + (case != "stale-intent"),
    }
    if case == "wrong-base":
        commit(tmp_path, "new-base")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={
            "status": "in-progress",
            "context_json": {"correction": correction},
            "completion_evidence_json": {"automatic_delivery": original},
        },
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"status": "not-started", "blocker_reason": None},
    )
    before = task_delivery._worktree_snapshot(str(tmp_path))
    prior_calls = len(calls)
    recovering = True
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == original["repair_count"]
    assert evidence["sessions"] == original["sessions"]
    assert evidence["attempts"][-1] == {
        key: original[key]
        for key in ("attempt", "candidate", "delivery", "review", "repair_count")
        if key in original
    }
    if case in {"unapproved", "stale-intent"}:
        assert ticket.status == "complete"
        assert len(calls) == prior_calls + 2  # Current independent review is mandatory.
        assert calls[-2]["occurrence"]["correction_intent"] == correction
        assert evidence["review"]["validated_outcome"] == "success"
        assert (evidence["candidate"]["commit_sha"] == sha) == (case == "unapproved")
    else:
        assert len(calls) == prior_calls
        assert ticket.status == "failed"
        assert (
            repo.get(project_id=project_id, task_key="deliver").tasks[0].delivery.stop_context.code
            == "candidate_validation"
        )
        assert "delivery" not in evidence and "review" not in evidence
        assert (
            "dirty" if case == "dirty-required" else "prior candidate and base"
        ) in ticket.outcome
        assert task_delivery._worktree_snapshot(str(tmp_path)) == before
        assert (
            repo.get(project_id=project_id, task_key="deliver")
            .tasks[0]
            .metadata_json["delivery"]["status"]
            == "stopped"
        )


@pytest.mark.parametrize("dirty", [False, True])
def test_fresh_evidence_only_correction_admission_preserves_files_and_accepted_receipts(
    session, project_id, tmp_path, monkeypatch, dirty
):
    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Admission")
    repo.create_ticket(
        project_id=project_id,
        task_key="deliver",
        key="first",
        title="First",
        context_json={
            "correction": {
                "work_intent": "evidence_only",
                "candidate_commit_sha": git(tmp_path, "rev-parse", "HEAD"),
                "target_attempt": 1,
            }
        },
    )
    repo.create_task(project_id=project_id, key="held", title="Accepted work")
    repo.create_ticket(project_id=project_id, task_key="held", key="accepted", title="Accepted")
    repo.update_ticket(
        project_id=project_id,
        ticket_key="accepted",
        patch_json={"status": "complete", "completion_evidence_json": {"proof": "retained"}},
    )
    accepted = repo.brief(project_id=project_id, ticket_key="accepted").ticket
    if dirty:
        (tmp_path / "baseline").write_text("unrelated staged edit")
        git(tmp_path, "add", "baseline")
        (tmp_path / "unrelated").write_text("retain these bytes")
    before = task_delivery._worktree_snapshot(str(tmp_path))
    calls = []
    monkeypatch.setattr(task_delivery, "run_agent", lambda *a, **kw: calls.append((a, kw)))
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    evidence = ticket.completion_evidence_json["automatic_delivery"]
    assert calls == []
    assert ticket.status == "failed" and "prior candidate and base" in ticket.outcome
    assert evidence["sessions"] == {} and evidence["repair_count"] == 0
    assert "delivery" not in evidence and "review" not in evidence
    assert task_delivery._worktree_snapshot(str(tmp_path)) == before
    retained = repo.brief(project_id=project_id, ticket_key="accepted").ticket
    assert retained.completion_evidence_json == accepted.completion_evidence_json
    assert retained.status == accepted.status == "complete"
    assert retained.updated_at == accepted.updated_at


@pytest.mark.parametrize(
    "path", ["update", "preview", "patch", "list", "create", "create-list", "create-preview"]
)
def test_correction_admission_rejects_untyped_intent_before_mutation(session, project_id, path):
    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="intent", title="Intent")
    repo.create_ticket(
        project_id=project_id,
        task_key="intent",
        key="existing",
        title="Keep",
        context_json={"unrelated": "keep"},
    )
    before = repo.brief(project_id=project_id, ticket_key="existing").ticket
    context = {
        "correction": {
            "work_intent": "evidence_only",
            "candidate_commit_sha": "a" * 40,
            "target_attempt": True,  # Coercion must not select a candidate exemption.
        }
    }
    patch = {"title": "Should not change", "context_json": context}

    def mutate():
        if path in {"update", "preview"}:
            return repo.update_ticket(
                project_id=project_id,
                ticket_key="existing",
                patch_json=patch,
                dry_run=path == "preview",
            )
        if path == "patch":
            return repo.patch(project_id=project_id, patch_json={"tickets": {"existing": patch}})
        if path == "list":
            return repo.update_ticket_list(
                project_id=project_id,
                updates_json=[{"ticket_key": "existing", "patch_json": patch}],
            )
        if path == "create":
            return repo.create_ticket(
                project_id=project_id,
                task_key="intent",
                key="new",
                title="New",
                context_json=context,
            )
        payload = {"task_key": "intent", "tickets": [{"key": "new", "context_json": context}]}
        if path == "create-preview":
            return repo.validate_ticket_list(project_id=project_id, ticket_list_json=payload)
        return repo.create_ticket_list(project_id=project_id, ticket_list_json=payload)

    if path in {"list", "create-list", "create-preview"}:
        result = mutate()
        data = result if path == "create-preview" else result.data
        assert data.valid is False
        assert data.errors
    else:
        with pytest.raises(ValidationError, match="correction"):
            mutate()
    session.expire_all()
    after = repo.brief(project_id=project_id, ticket_key="existing").ticket
    assert after.context_json == before.context_json
    assert after.title == before.title
    assert len(repo.get(project_id=project_id, task_key="intent").tickets) == 1


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


@pytest.mark.parametrize(
    ("blocked_phase", "summary", "blocker"),
    [
        ("delivery", "Required fixture unavailable", "Required fixture unavailable"),
        (
            "review",
            "Required fixture unavailable; api_key=synthetic-value",
            "Required fixture unavailable; api_key=[redacted]",
        ),
        ("delivery", "", "delivery blocked"),
        ("review", " \t ", "review blocked"),
    ],
)
def test_ordinary_blocked_result_requires_explicit_resolution_and_requeue(
    session, project_id, tmp_path, monkeypatch, blocked_phase, summary, blocker
):
    from copy import deepcopy

    from stackos import task_delivery

    repo = TrackerRepository(session)
    repo.create_task(project_id=project_id, key="deliver", title="Deliver")
    repo.create_ticket(project_id=project_id, task_key="deliver", key="first", title="First")
    calls = []

    def launch(*args, **selection):
        phase = json.loads(args[3])["phase"]
        previous = phase in calls
        assert selection.get("resume_thread_id") == (f"first-{phase}" if previous else None)
        calls.append(phase)
        sha = (
            commit(tmp_path, f"candidate-{len(calls)}")
            if phase == "delivery"
            else git(tmp_path, "rev-parse", "HEAD")
        )
        blocked = phase == blocked_phase and not previous
        return scenario_result(
            0,
            thread_id=f"first-{phase}",
            result=generic_result(
                "blocked" if blocked else "success",
                {"commit_sha": sha, "fixture": "required"},
                summary if blocked else "Checked",
                [{"text": "Unrelated followup"}] if blocked else [],
            ),
        )

    monkeypatch.setattr(task_delivery, "run_agent", launch)
    scheduler = QueuedScheduler()
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    ticket = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert ticket.status == "in-progress"
    assert ticket.blocker_reason == ticket.outcome == blocker
    assert ticket.completed_at is None
    state = repo.get(project_id=project_id, task_key="deliver").tasks[0].metadata_json["delivery"]
    assert state["status"] == "stopped" and state["reason"] == blocker
    assert repo.status(project_id=project_id).blocked_ticket_count == 1
    assert [t.key for t in repo.blockers(project_id=project_id).blocked] == ["first"]
    assert repo.delivery_ready(project_id=project_id, task_key="deliver") == []
    evidence = deepcopy(ticket.completion_evidence_json["automatic_delivery"])
    assert evidence[blocked_phase]["validated_outcome"] == "blocked"
    assert json.loads(evidence[blocked_phase]["result"]["outputs"])["fixture"] == "required"
    assert evidence["repair_count"] == 0
    assert evidence["sessions"][blocked_phase]["thread_id"] == f"first-{blocked_phase}"
    assert "synthetic-value" not in json.dumps(evidence)
    index = repo.get(
        project_id=project_id, task_key="deliver", include_graph=False, task_index_only=True
    )
    assert index.tasks[0].ticket_summary.blocked_count == 1
    assert index.tasks[0].ticket_summary.in_progress_count == 1
    assert index.tasks[0].ticket_summary.terminal_count == 0

    # Neither a new handoff nor requeue alone resolves the explicit blocker.
    # Clearing the blocker alone also leaves in-progress work non-dispatchable.
    stopped_calls = list(calls)
    for patch in ({}, {"status": "not-started"}, {"status": "in-progress", "blocker_reason": None}):
        if patch:
            repo.update_ticket(project_id=project_id, ticket_key="first", patch_json=patch)
        submit(session, project_id, tmp_path, scheduler)
        scheduler.run()
        session.expire_all()
        snapshot = repo.get(project_id=project_id, task_key="deliver")
        assert calls == stopped_calls
        assert snapshot.tasks[0].metadata_json["delivery"]["status"] == "stopped"
        assert snapshot.tickets[0].completion_evidence_json["automatic_delivery"] == evidence
        assert len(snapshot.tasks[0].context_json["followups"]) == 1

    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "not-started"}
    )
    submit(session, project_id, tmp_path, scheduler)
    scheduler.run()
    session.expire_all()
    final = repo.brief(project_id=project_id, ticket_key="first").ticket
    assert final.status == "complete"
    assert final.blocker_reason is None
    assert repo.status(project_id=project_id).blocked_ticket_count == 0
    current = final.completion_evidence_json["automatic_delivery"]
    assert current["attempts"] == [
        {
            key: evidence[key]
            for key in ("attempt", "candidate", "delivery", "review", "repair_count")
            if key in evidence
        }
    ]
    assert current["repair_count"] == 0
    assert current["sessions"]["delivery"] == evidence["sessions"]["delivery"]
    if blocked_phase == "review":
        assert current["sessions"]["review"] == evidence["sessions"]["review"]
    assert calls == [*stopped_calls, "delivery", "review"]


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
    ticket = stopped.tickets[0]
    assert ticket.status == ("in-progress" if fault == "blocked" else "failed")
    assert ticket.blocker_reason == ("Repair interrupted" if fault == "blocked" else None)
    assert (ticket.completed_at is None) == (fault == "blocked")
    assert stopped.tasks[0].metadata_json["delivery"]["status"] == "stopped"
    assert calls == ["delivery", "review", "delivery"]
    evidence = stopped.tickets[0].completion_evidence_json["automatic_delivery"]
    assert evidence["repair_count"] == 1
    assert evidence["delivery"]["validated_outcome"] == ("blocked" if fault == "blocked" else None)
    repo.update_ticket(
        project_id=project_id, ticket_key="first", patch_json={"status": "in-progress"}
    )
    repo.update_ticket(
        project_id=project_id,
        ticket_key="first",
        patch_json={"status": "not-started", "blocker_reason": None},
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
        project_id=project_id,
        ticket_key="first",
        patch_json={"status": "not-started", "blocker_reason": None},
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
    + [("codex", "native-success"), ("codex", "native-blocked"), ("codex", "handoff")],
)
def test_native_collector_failure_persists_session_and_available_usage(
    native_task_session, project_id, tmp_path, monkeypatch, host, fault
):
    """Actual native adapter -> normalizer -> tracker receipt on failed output."""
    from stackos import task_delivery
    from stackos.host_mcp import progress
    from stackos.host_mcp.adapters import codex, grok
    from tests.helpers.native_messages import messages_output

    session = native_task_session
    if fault == "handoff":
        transition = task_delivery._record_execution_transition

        def fail_notification(session_factory, call, active, **kwargs):
            if not active:
                raise RuntimeError("optional transition notification failed")
            transition(session_factory, call, active, **kwargs)

        monkeypatch.setattr(task_delivery, "_record_execution_transition", fail_notification)

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
            stdout = messages_output(
                "grok",
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
                },
            )
        return subprocess.CompletedProcess(command, int(native_failure), stdout, "")

    monkeypatch.setattr(subprocess, "run", execute)

    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        if command[0] == "/fake/grok":
            completed = execute(command)
            return real_popen(
                [
                    sys.executable,
                    "-c",
                    "import sys; "
                    f"sys.stdout.write({completed.stdout!r}); sys.exit({completed.returncode})",
                ],
                **kwargs,
            )
        return real_popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)

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
    assert (
        receipt["execution"]["live_usage"]["samples"][-1]["total"]
        == receipt["usage"]["measured"]["total"]
    )
    assert ticket.agent_stats.samples[-1].total == receipt["usage"]["measured"]["total"]
    assert ticket.agent_stats.last_execution.agent == receipt["agent"]
    assert ticket.work_duration.endpoint_at == receipt["finished_at"]
    assert ticket.work_duration.coverage == "complete"
    assert ticket.work_duration.total_seconds >= 0
    assert ticket.active_execution is None
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["read", "decode"])
@pytest.mark.parametrize("final_status", [None, "blocked", "success"])
def test_native_stream_exception_persists_primary_final_without_replay(
    native_task_session, project_id, tmp_path, tmp_path_factory, monkeypatch, fault, final_status
):
    """The real stream decoder and an injected read failure reach the same receipt gate."""
    from stackos.host_mcp import progress
    from stackos.host_mcp.adapters import codex

    session = native_task_session
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
