"""Server lifecycle over authenticated REST and the finite computer transport.

Only Supabase HTTP and native outcomes are substituted here. Actual native and
local Supabase proof is retained separately by the lifecycle delivery receipt.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlmodel import Session

from stackos.local_runtime.exchange import InvocationExchange, InvocationReport
from stackos.repositories.tracker import TrackerRepository

pytest_plugins = ["tests.integration.test_routes.test_local_runtime_exchange"]


@pytest.mark.parametrize("retired", [False, True])
def test_finished_known_report_waits_for_controller_release(connected, retired):
    from tests.integration.test_routes.test_local_runtime_exchange import offer, terminal

    exchange, connection, invocation, _, _ = offer(connected)
    exchange.poll(connection, None)
    exchange.report(connection, InvocationReport.model_validate(terminal(invocation)))
    invocation.finished = True
    if retired:
        invocation.retired.set()
    observed = exchange.poll(connection, invocation.invocation_id)
    assert observed["state"] == "completed"
    assert observed["needs_attention"] is False
    assert "launch" not in observed
    assert exchange.snapshot(connection)["needs_attention"] is False
    exchange.release(connection, invocation)


def prepare(connected, monkeypatch):
    client, settings, identity = connected
    jobs = []
    monkeypatch.setattr(
        client.app.state.scheduler, "add_job", lambda fn, **kw: jobs.append((fn, kw))
    )
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        repo.create_task(project_id=identity["project_id"], key="connected", title="Connected")
        repo.create_ticket(
            project_id=identity["project_id"], task_key="connected", key="build", title="Build"
        )
    computer = client.headers.pop("Authorization")
    login = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.test", "password": "password123"}
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    args = identity | {
        "task_key": "connected",
        "base_commit_sha": "a" * 40,
        "delivery_agent": {"name": "builder", "instructions": "Build", "model": "gpt-6.1-sol"},
        "review_agent": {"name": "reviewer", "instructions": "Review", "model": "gpt-6.1-sol"},
    }
    return client, settings, identity, computer, args, jobs


def start(client, args):
    return client.post("/api/v1/operations/tracker.startDelivery/call", json={"arguments": args})


def poll(client, identity, computer):
    return client.post(
        "/api/v1/operations/localRuntime.poll/call",
        json={"arguments": identity},
        headers={"Authorization": computer},
    )


def test_disconnected_computer_refuses_handoff(connected, monkeypatch):
    client, _, identity, computer, args, jobs = prepare(connected, monkeypatch)
    response = start(client, args)
    assert response.status_code == 409, response.text
    assert not jobs
    assert poll(client, identity, computer).json()["state"] == "idle"
    assert start(client, args | {"project_dir": "/server/path"}).status_code == 422


def _run_connected_scenario(connected, monkeypatch, mode):
    unknown = mode == "unknown"
    client, _, identity, computer, args, jobs = prepare(connected, monkeypatch)
    assert poll(client, identity, computer).json()["state"] == "idle"
    response = start(client, args)
    assert response.status_code == 200, response.text
    assert len(jobs) == 1
    assert start(client, args).status_code == 200
    assert len(jobs) == 1
    fn, job = jobs[0]
    with ThreadPoolExecutor() as pool:
        future = pool.submit(fn, **job["kwargs"])
        phases = (
            ["delivery"] if unknown else ["delivery", "review"] * (2 if mode == "repair" else 1)
        )
        for index, phase in enumerate(phases):
            deadline = time.monotonic() + 10
            while True:
                received = poll(client, identity, computer).json()
                if received["state"] == "launch":
                    break
                assert not future.done(), future.exception()
                assert time.monotonic() < deadline, received
                time.sleep(0.01)
            assert received["scope"]["phase"] == phase
            if index >= 2:
                assert received["launch"]["resume_thread_id"] == phase + "-session"
                assert received["launch"]["candidate"]["base_commit_sha"] == "b" * 40
            assert "project_dir" not in received["launch"]
            duplicate = poll(client, identity, computer).json()
            assert duplicate["needs_attention"] and "launch" not in duplicate
            outcome = {
                "exit_code": 0,
                "thread_id": ("delivery" if mode == "same-session" else phase) + "-session",
                "result": {
                    "status": "repair" if mode == "repair" and index == 1 else "success",
                    "summary": "Checked",
                    "outputs": json.dumps(
                        {
                            "commit_sha": (
                                "c"
                                if index >= 2 or (mode == "wrong-sha" and phase == "review")
                                else "b"
                            )
                            * 40
                        }
                    ),
                    "followups": [],
                },
            }
            report = {
                "invocation_id": received["invocation_id"],
                "attempt": received["scope"]["attempt"],
                "state": "unknown" if unknown else "completed",
            }
            if not unknown:
                report["outcome"] = outcome
            result = client.post(
                "/api/v1/operations/localRuntime.report/call",
                json={"arguments": identity | {"report": report}},
                headers={"Authorization": computer},
            )
            assert result.status_code == 200, result.text
        future.result(timeout=10)
    with Session(client.app.state.engine) as session:
        snapshot = TrackerRepository(session).get(
            project_id=identity["project_id"], task_key="connected"
        )
        ticket = snapshot.tickets[0]
        assert ticket.status == (
            "failed" if mode in {"unknown", "wrong-sha", "same-session"} else "complete"
        )
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert evidence["repair_count"] == (1 if mode == "repair" else 0)
        assert len(evidence["sessions"]) == (0 if unknown else 1 if mode == "same-session" else 2)
    if unknown:
        assert poll(client, identity, computer).json()["state"] == "unknown"
        assert start(client, args).status_code == 409
        client.app.state.operation_services["local_invocations"] = InvocationExchange()
        assert poll(client, identity, computer).json()["state"] == "unknown"
        assert start(client, args).status_code == 409
    return client, identity, computer, args, jobs


@pytest.mark.parametrize("mode", ["success", "unknown", "repair", "wrong-sha", "same-session"])
def test_server_accepts_review_once_and_unknown_stops_admission(connected, monkeypatch, mode):
    _run_connected_scenario(connected, monkeypatch, mode)


@pytest.mark.parametrize("evidence_only", [False, True])
def test_connected_retry_retains_sessions_without_server_git(connected, monkeypatch, evidence_only):
    git_calls = []

    def unexpected_git(*args):
        git_calls.append(args)
        raise AssertionError("Connected delivery must not inspect the server filesystem")

    monkeypatch.setattr("stackos.task_delivery._git", unexpected_git)
    client, identity, computer, args, jobs = _run_connected_scenario(
        connected, monkeypatch, "wrong-sha"
    )
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        ticket = repo.brief(project_id=identity["project_id"], ticket_key="build").ticket
        original = deepcopy(ticket.completion_evidence_json["automatic_delivery"])
        assert original["candidate"]["commit_sha"] == "b" * 40
        patch = {"status": "in-progress"}
        if evidence_only:
            patch["context_json"] = {
                **(ticket.context_json or {}),
                "correction": {
                    "work_intent": "evidence_only",
                    "candidate_commit_sha": "b" * 40,
                    "target_attempt": original["attempt"] + 1,
                },
            }
        repo.update_ticket(project_id=identity["project_id"], ticket_key="build", patch_json=patch)
        repo.update_ticket(
            project_id=identity["project_id"],
            ticket_key="build",
            patch_json={"status": "not-started"},
        )

    assert poll(client, identity, computer).json()["state"] == "idle"
    response = start(client, args)
    assert response.status_code == 200, response.text
    assert len(jobs) == 2
    fn, job = jobs[-1]
    with ThreadPoolExecutor() as pool:
        future = pool.submit(fn, **job["kwargs"])
        if not evidence_only:
            for phase in ("delivery", "review"):
                deadline = time.monotonic() + 10
                while True:
                    received = poll(client, identity, computer).json()
                    if received["state"] == "launch":
                        break
                    assert not future.done(), future.exception()
                    assert time.monotonic() < deadline, received
                    time.sleep(0.01)
                assert received["scope"]["phase"] == phase
                assert received["scope"]["attempt"] == original["attempt"] + 1
                assert received["launch"]["resume_thread_id"] == phase + "-session"
                assert (
                    received["launch"]["candidate"]["commit_sha"]
                    == ("b" if phase == "delivery" else "c") * 40
                )
                assert "project_dir" not in received["launch"]
                report = client.post(
                    "/api/v1/operations/localRuntime.report/call",
                    json={
                        "arguments": identity
                        | {
                            "report": {
                                "invocation_id": received["invocation_id"],
                                "attempt": received["scope"]["attempt"],
                                "state": "completed",
                                "outcome": {
                                    "exit_code": 0,
                                    "thread_id": phase + "-session",
                                    "result": {
                                        "status": "success",
                                        "summary": "Verified descendant candidate",
                                        "outputs": json.dumps({"commit_sha": "c" * 40}),
                                        "followups": [],
                                    },
                                },
                            }
                        }
                    },
                    headers={"Authorization": computer},
                )
                assert report.status_code == 200, report.text
        future.result(timeout=10)
    assert not git_calls
    assert poll(client, identity, computer).json()["state"] == "idle"
    with Session(client.app.state.engine) as session:
        ticket = (
            TrackerRepository(session)
            .brief(project_id=identity["project_id"], ticket_key="build")
            .ticket
        )
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert evidence["sessions"] == original["sessions"]
        assert evidence["repair_count"] == original["repair_count"] == 0
        assert evidence["attempts"][-1]["delivery"] == original["delivery"]
        assert evidence["attempts"][-1]["review"] == original["review"]
        if evidence_only:
            assert ticket.status == "failed"
            assert (
                "Evidence-only continuation is not supported for connected computers"
                in ticket.outcome
            )
            assert "delivery" not in evidence and "review" not in evidence
            assert evidence["candidate"] == original["candidate"]
        else:
            assert ticket.status == "complete"
            assert evidence["candidate"]["commit_sha"] == "c" * 40
            assert evidence["review"]["validated_outcome"] == "success"


@pytest.mark.parametrize("prior_mode", ["unknown", "success"])
def test_connection_admission_honors_prior_task_outcome_after_transport_restart(
    connected, monkeypatch, prior_mode
):
    client, identity, computer, args, jobs = _run_connected_scenario(
        connected, monkeypatch, prior_mode
    )
    with Session(client.app.state.engine) as session:
        repo = TrackerRepository(session)
        original = repo.get(project_id=identity["project_id"], task_key="connected")
        state = original.tasks[0].metadata_json["delivery"]
        history = original.tickets[0].completion_evidence_json
        assert state["status"] == ("stopped" if prior_mode == "unknown" else "complete")
        assert bool(state.get("uncertain_invocation")) == (prior_mode == "unknown")
        repo.create_task(project_id=identity["project_id"], key="other", title="Other")
        repo.create_ticket(
            project_id=identity["project_id"], task_key="other", key="other-build", title="Other"
        )

    # A fresh transport and authenticated poll cannot erase durable uncertainty.
    client.app.state.operation_services["local_invocations"] = InvocationExchange()
    assert poll(client, identity, computer).json()["state"] == (
        "unknown" if prior_mode == "unknown" else "idle"
    )
    other_args = args | {"task_key": "other"}
    queued_before = len(jobs)
    response = start(client, other_args)
    expected_status = 409 if prior_mode == "unknown" else 200
    assert response.status_code == expected_status, response.text
    assert len(jobs) == queued_before + (prior_mode == "success")

    if prior_mode == "unknown":
        # The fence belongs to this connection, not every computer/project task.
        issued = client.post(
            "/api/v1/operations/localRuntime.authorize/call",
            json={"arguments": {"project_id": identity["project_id"]}},
        )
        assert issued.status_code == 200, issued.text
        other_identity = identity | {"connection_id": issued.json()["connection_id"]}
        other_computer = "Bearer " + issued.json()["credential"]
        assert poll(client, other_identity, other_computer).json()["state"] == "idle"
        response = start(client, other_args | other_identity)
        assert response.status_code == 200, response.text
        assert len(jobs) == queued_before + 1

    with Session(client.app.state.engine) as session:
        original = TrackerRepository(session).get(
            project_id=identity["project_id"], task_key="connected"
        )
        assert original.tasks[0].metadata_json["delivery"] == state
        assert original.tickets[0].completion_evidence_json == history
