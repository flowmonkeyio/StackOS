"""Preparation references use existing task receipts, never native history."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult
from stackos.host_mcp.toml_config import read_mcp_config_ref
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    commit,
    generic_result,
    git,
    use_codex_native,
)
from tests.integration.test_repositories.test_task_delivery_sources import source, start


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.fixture
def prepared_context(api, project_id, tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    scheduler = QueuedScheduler()
    monkeypatch.setitem(api.app.state.operation_services, "scheduler", scheduler)
    calls = []
    failure = {"resume": False}
    route_path = tmp_path / "routes.toml"
    route_path.write_text(f"[mcp_servers.stackos]\ncwd = {json.dumps(str(root))}\n")
    route = {"path": str(route_path), "sha256": sha256(route_path.read_bytes()).hexdigest()}
    # Native transport is controlled; retain the actual fragment validator and
    # verify the identity received by the existing native runner below.
    monkeypatch.setattr(
        service, "task_mcp_route", lambda host, directory, ref, **kw: read_mcp_config_ref(ref)[0]
    )
    with Session(api.app.state.engine) as session:
        preparer = source(
            session, project_id, root, "preparer", settings='sandbox_mode="read-only"\n'
        )
        writer = source(
            session, project_id, root, "writer", settings='model_reasoning_effort="high"\n'
        )
        reviewer = source(session, project_id, root, "reviewer")
        git(root, "init")
        git(root, "config", "user.name", "Fixture")
        git(root, "config", "user.email", "fixture@example.test")
        git(root, "add", ".codex")
        candidate = commit(root, "baseline")
        repo = TrackerRepository(session)
        for task in ("prepare", "implement"):
            repo.create_task(project_id=project_id, key=task, title=task)
            repo.create_ticket(project_id=project_id, task_key=task, key=f"{task}-work", title=task)

        def native(directory, instructions, prompt, schema, **kwargs):
            packet = json.loads(prompt)
            calls.append(
                (
                    packet,
                    instructions,
                    deepcopy(
                        {
                            k: v
                            for k, v in kwargs.items()
                            if k not in {"on_session", "process_control"}
                        }
                    ),
                )
            )
            assert Path(directory) == root
            identity = f"{packet['brief']['task']['key']}-{packet['phase']}"
            if kwargs.get("resume_thread_id") and failure["resume"]:
                return AgentRunResult(1, error="fixture exact resume failed")
            kwargs["on_session"](identity)
            if packet["brief"]["task"]["key"] == "implement" and failure.get("fresh"):
                return AgentRunResult(
                    1, thread_id=identity, error="fixture native execution denied"
                )
            return AgentRunResult(
                0, thread_id=identity, result=generic_result(outputs={"commit_sha": candidate})
            )

        use_codex_native(monkeypatch, native)
        start(
            session,
            project_id,
            root,
            scheduler,
            task_key="prepare",
            agents={
                "delivery_agent": {"source_ref": preparer},
                "review_agent": {"source_ref": reviewer},
            },
        )
        scheduler.run()
        session.expire_all()
        parent = repo.brief(project_id=project_id, ticket_key="prepare-work").ticket
        assert parent.status.value == "complete", parent.outcome
        automatic = parent.completion_evidence_json["automatic_delivery"]
        receipt = automatic["delivery"]
        reference = {
            "kind": "task_phase",
            "project_id": project_id,
            "task_id": parent.task_id,
            "ticket_id": parent.id,
            "attempt": automatic["attempt"],
            "phase": "delivery",
            "invocation_id": receipt["execution"]["invocation_id"],
            "thread_id": receipt["thread_id"],
            "outcome_digest": digest(receipt["result"]),
            "snapshot_digest": digest(automatic["sessions"]["delivery"]),
            "preparation_only": True,
        }
    calls.clear()
    return root, scheduler, reference, writer, reviewer, calls, failure, route


def handoff(api, project_id, context, **extra):
    root, _, _, writer, reviewer, _, _, route = context
    return api.post(
        "/api/v1/operations/tracker.startDelivery/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "implement",
                "project_dir": str(root),
                "mcp_config_ref": route,
                "agents": {
                    "delivery_agent": {"source_ref": writer, "model": "gpt-6.1-sol"},
                    "review_agent": {"source_ref": reviewer, "model": "gpt-6-astra"},
                },
                **extra,
            }
        },
    )


@pytest.mark.parametrize("parent", ["absent", "valid", "ineligible"])
@pytest.mark.parametrize("recovery", ["failure", "missing"])
def test_registered_preparation_selects_fresh_and_exact_recovery(
    api, project_id, prepared_context, parent, recovery
):
    _root, scheduler, reference, _, _, calls, failure, route = prepared_context
    response = handoff(
        api,
        project_id,
        prepared_context,
        **({"preparation_parent": reference} if parent != "absent" else {}),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"].get("preparation_parent") == (
        reference if parent != "absent" else None
    )
    if parent == "ineligible":
        with Session(api.app.state.engine) as session:
            repo = TrackerRepository(session)
            ticket = repo.brief(project_id=project_id, ticket_key="prepare-work").ticket
            completion = deepcopy(ticket.completion_evidence_json)
            completion["automatic_delivery"]["delivery"]["superseded"] = True
            repo.update_ticket(
                project_id=project_id,
                ticket_key=ticket.key,
                patch_json={"completion_evidence_json": completion},
            )
    scheduler.run()
    assert len(calls) == 2
    delivery, review = calls
    decision = delivery[0]["occurrence"]["preparation_context"]
    assert decision["mode"] == "fresh"
    assert decision["reason"] == ("unsupported" if parent != "absent" else "absent")
    assert decision.get("reference") == (reference if parent != "absent" else None)
    assert delivery[1].startswith("writer") and delivery[2]["model"] == "gpt-6.1-sol"
    assert delivery[2]["native_settings"] == {"model_reasoning_effort": "high"}
    assert delivery[2]["mcp_config_ref"] == route and review[2]["mcp_config_ref"] == route
    assert "resume_thread_id" not in delivery[2]
    assert review[1].startswith("reviewer") and review[2]["model"] == "gpt-6-astra"
    assert review[0]["occurrence"]["preparation_context"] == {
        "mode": "fresh",
        "reason": "independent_review",
    }
    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        ticket = repo.brief(project_id=project_id, ticket_key="implement-work").ticket
        evidence = ticket.completion_evidence_json["automatic_delivery"]
        assert (
            evidence["sessions"]["delivery"]["thread_id"]
            != evidence["sessions"]["review"]["thread_id"]
        )
        # Reopening this disposable fixture retains its exact native identity.
        for status in ("in-progress", "not-started"):
            repo.update_ticket(
                project_id=project_id, ticket_key=ticket.key, patch_json={"status": status}
            )
        if recovery == "missing":
            evidence["sessions"].pop("delivery")
            repo.update_ticket(
                project_id=project_id,
                ticket_key=ticket.key,
                patch_json={"completion_evidence_json": {"automatic_delivery": evidence}},
            )
    failure["resume"] = True
    response = handoff(api, project_id, prepared_context)
    assert response.status_code == 200, response.text
    scheduler.run()
    assert len(calls) == (3 if recovery == "failure" else 2)
    if recovery == "failure":
        assert calls[-1][2]["resume_thread_id"] == "implement-delivery"
        assert calls[-1][0]["occurrence"]["preparation_context"] == {
            "mode": "resume",
            "reason": "retained_session",
        }
        assert calls[-1][2]["mcp_config_ref"] == route
    with Session(api.app.state.engine) as session:
        task = TrackerRepository(session).get(project_id=project_id, task_key="implement").tasks[0]
        assert task.metadata_json["delivery"]["status"] == "stopped"


@pytest.mark.parametrize(
    "fault",
    [
        "malformed",
        "foreign",
        "foreign_record",
        "outcome",
        "snapshot",
        "identity",
        "review",
        "attestation",
        "numeric_attestation",
    ],
)
def test_registered_preparation_rejects_before_native_access(
    api, project_id, prepared_context, monkeypatch, fault
):
    _, scheduler, reference, _, _, calls, _, _ = prepared_context
    reference = deepcopy(reference)
    if fault == "malformed":
        reference["attempt"] = "1"
    elif fault == "foreign":
        reference["project_id"] += 1
    elif fault == "foreign_record":
        response = api.post(
            "/api/v1/projects",
            json={"slug": "foreign-preparation", "name": "Foreign", "domain": "test"},
        )
        assert response.status_code == 201, response.text
        with Session(api.app.state.engine) as session:
            repo = TrackerRepository(session)
            foreign = response.json()["data"]["id"]
            repo.create_task(project_id=foreign, key="foreign", title="Foreign")
            repo.create_ticket(
                project_id=foreign, task_key="foreign", key="foreign-work", title="Foreign"
            )
            ticket = repo.brief(project_id=foreign, ticket_key="foreign-work").ticket
            reference.update(task_id=ticket.task_id, ticket_id=ticket.id)
    elif fault in {"outcome", "snapshot"}:
        reference[f"{fault}_digest"] = "0" * 64
    elif fault == "identity":
        reference["thread_id"] = "forged"
    elif fault == "review":
        reference["phase"] = "review"
    elif fault == "attestation":
        reference["preparation_only"] = False
    else:
        reference["preparation_only"] = 1
    monkeypatch.setattr(
        service,
        "execution_catalog",
        lambda *a, **k: pytest.fail("native preflight before reference rejection"),
    )
    response = handoff(api, project_id, prepared_context, preparation_parent=reference)
    assert response.status_code == 422, response.text
    assert not calls and not scheduler.jobs
    with Session(api.app.state.engine) as session:
        task = TrackerRepository(session).get(project_id=project_id, task_key="implement").tasks[0]
        assert not (task.metadata_json or {}).get("delivery")


@pytest.mark.parametrize(
    "fault",
    [
        "root",
        "missing_identity",
        "source",
        "host",
        "provider",
        "profile",
        "settings",
        "writable",
        "malformed_record",
    ],
)
def test_preparation_receipt_identity_is_validated_before_native_access(
    api, project_id, prepared_context, monkeypatch, fault
):
    _, scheduler, reference, _, _, calls, _, _ = prepared_context
    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        ticket = repo.brief(project_id=project_id, ticket_key="prepare-work").ticket
        completion = deepcopy(ticket.completion_evidence_json)
        automatic = completion["automatic_delivery"]
        native = automatic["sessions"]["delivery"]
        snapshot = native["agent_snapshot"]
        if fault == "root":
            native["project_dir"] = "/foreign-root"
        elif fault == "missing_identity":
            native.pop("project_dir")
        elif fault == "source":
            native["source_ref"]["source_path"] = "../foreign.toml"
        elif fault == "host":
            snapshot["host_key"] = "grok"
        elif fault in {"provider", "profile"}:
            snapshot[fault] = "foreign"
        elif fault == "settings":
            snapshot["native_settings"] = {"sandbox_mode": "workspace-write"}
        elif fault == "writable":
            automatic["delivery"]["launch"]["sandbox"] = "workspace-write"
        else:
            automatic["delivery"] = []
        # Even a supplied digest matching the tampered native record cannot
        # replace consistency with its actual launch/source/settings receipt.
        reference = {**reference, "snapshot_digest": digest(native)}
        repo.update_ticket(
            project_id=project_id,
            ticket_key=ticket.key,
            patch_json={"completion_evidence_json": completion},
        )
    monkeypatch.setattr(
        service,
        "execution_catalog",
        lambda *a, **k: pytest.fail("invalid parent reached native preflight"),
    )
    response = handoff(api, project_id, prepared_context, preparation_parent=reference)
    assert response.status_code == 422, response.text
    assert not calls and not scheduler.jobs


def test_preparation_fresh_decision_never_retries_a_native_denial(
    api, project_id, prepared_context
):
    _, scheduler, reference, _, _, calls, failure, _ = prepared_context
    failure["fresh"] = True
    response = handoff(api, project_id, prepared_context, preparation_parent=reference)
    assert response.status_code == 200, response.text
    scheduler.run()
    assert len(calls) == 1
    assert "resume_thread_id" not in calls[0][2]
    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        task = repo.get(project_id=project_id, task_key="implement").tasks[0]
        assert task.metadata_json["delivery"]["status"] == "stopped"
        evidence = repo.brief(
            project_id=project_id, ticket_key="implement-work"
        ).ticket.completion_evidence_json["automatic_delivery"]
        assert evidence["sessions"]["delivery"]["thread_id"] == "implement-delivery"
        assert evidence["repair_count"] == 0
