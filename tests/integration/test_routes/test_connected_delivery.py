"""Server lifecycle over authenticated REST and the finite computer transport.

Only Supabase HTTP and native outcomes are substituted here. Actual native and
local Supabase proof is retained separately by the lifecycle delivery receipt.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session

from stackos.local_runtime.exchange import InvocationExchange
from stackos.repositories.tracker import TrackerRepository

pytest_plugins = ["tests.integration.test_routes.test_local_runtime_exchange"]


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


@pytest.mark.parametrize("mode", ["success", "unknown", "repair", "wrong-sha", "same-session"])
def test_server_accepts_review_once_and_unknown_stops_admission(connected, monkeypatch, mode):
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
        assert start(client, args).status_code == 409
        client.app.state.operation_services["local_invocations"] = InvocationExchange()
        assert poll(client, identity, computer).json()["state"] == "idle"
        assert start(client, args).status_code == 409
