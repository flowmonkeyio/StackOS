"""Real scoped bridge replacement and console-only recovery of external setup."""

import copy
import json

from sqlmodel import Session

from stackos.auth import derive_ui_token
from stackos.db.models import AgentRequest
from stackos.repositories.project_setup import ProjectSetupRepository
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_task_delivery import QueuedScheduler

from .test_mcp_bridge_agent_path import (
    _initialize,
    _operation_data,
    _scoped_bridge,
    _structured,
    _tool_call,
    _toolbox_call,
)


def test_bridge_replacement_cancel_and_deliberate_new_owner(mcp_client, mcp_app, tmp_path):
    mcp_client.call_tool_structured("workspace.bootstrap", {"cwd": str(tmp_path)})

    def bridge(identity):
        proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
        proxy.client_session_id = identity
        _initialize(proxy, client)
        opened = _operation_data(
            _structured(
                _tool_call(
                    proxy, client, "workspace.startSession", {"thread_id": "same-native-thread"}
                )
            )
        )
        return proxy, client, opened["agent_session_id"]

    a, a_client, a_session = bridge("bridge-A")
    start_args = {
        "session_id": a_session,
        "workflow_keys": ["agency.project-setup"],
        "idempotency_key": "A-start",
        "response_mode": "raw",
    }
    started = _operation_data(
        _structured(_toolbox_call(a, a_client, "projectSetup.start", start_args))
    )
    project = started["attempt"]["project_id"]
    request = started["attempt"]["request_id"]
    with Session(mcp_app.state.engine) as session:
        proof = verified_result(ProjectSetupRepository(session), project, "agency.project-setup")
    proof.attempt_ref = started["attempt"]["attempt_ref"]
    report = {
        "session_id": a_session,
        "request_id": request,
        "claim_token": started["claim_token"],
        "expected_revision": 1,
        "idempotency_key": "A-progress",
        "response_mode": "raw",
        "report": {
            "status": "needs_input",
            "progress": {
                "message": "Partial edits and checked workflow retained",
                "evidence_ref": "fixture/partial-edits",
            },
            "question": {
                "question_id": "q",
                "revision": 1,
                "prompt": "Keep the local choice?",
                "workflow_keys": ["agency.project-setup"],
                "answer_state": "pending",
                "response_channel": "existing_agent",
            },
            "workflow_results": [
                {
                    "workflow_key": "agency.project-setup",
                    "expected_revision": 0,
                    "result": proof.model_dump(mode="json"),
                }
            ],
        },
    }
    progress = _toolbox_call(a, a_client, "projectSetup.report", report)
    assert not progress["result"].get("isError"), progress
    retained = _operation_data(_structured(progress))
    with Session(mcp_app.state.engine) as session:
        receipt_before = (
            ProjectSetupRepository(session)
            ._row(project, "agency.project-setup")
            .model_dump(mode="json")
        )

    b, b_client, b_session = bridge("bridge-B")
    # Replacement has the same project/thread, but cannot use A's old session
    # or a copied token. A's disconnected transport has no new call; separately
    # retain A here to prove the late connected-caller case after cancellation.
    for args in (
        {**report, "expected_revision": 2, "idempotency_key": "B-old-session"},
        {
            **report,
            "session_id": b_session,
            "expected_revision": 2,
            "idempotency_key": "B-copied-token",
        },
    ):
        denied = _toolbox_call(b, b_client, "projectSetup.report", args)
        assert denied["result"].get("isError"), denied
        assert started["claim_token"] not in json.dumps(denied)
    attached = _operation_data(
        _structured(
            _toolbox_call(
                b,
                b_client,
                "projectSetup.start",
                {**start_args, "session_id": b_session, "idempotency_key": "B-observe"},
            )
        )
    )
    assert attached["attached"] and attached["claim_token"] is None
    assert attached["attempt"]["request_id"] == request
    args = {
        "project_id": project,
        "request_id": request,
        "expected_revision": 2,
        "external_work_stopped": True,
        "idempotency_key": "console-cancel",
        "response_mode": "raw",
    }
    mcp_denied = _toolbox_call(b, b_client, "projectSetup.cancel", args)
    assert mcp_denied["result"].get("isError"), mcp_denied
    mcp_app.state.operation_services["scheduler"] = QueuedScheduler()
    ui = {"Authorization": f"Bearer {derive_ui_token(mcp_client.auth_token)}"}
    response = mcp_client.test_client.post(
        "/api/v1/operations/projectSetup.cancel/call", json={"arguments": args}, headers=ui
    )
    assert response.status_code == 200, response.text
    cancelled = response.json()["data"]
    assert cancelled["revision"] == 3 and cancelled["status"] == "cancelled"
    for field in (
        "session_id",
        "runtime",
        "thread_id",
        "workflow_keys",
        "reports",
        "outcomes",
        "answers",
        "question",
        "progress",
    ):
        assert cancelled[field] == retained[field]
    assert cancelled["partial_changes_possible"] is True
    late = copy.deepcopy(report)
    late.update(expected_revision=3, idempotency_key="A-late")
    for operation, arguments in (
        ("projectSetup.report", late),
        ("projectSetup.report", report),
        ("projectSetup.start", start_args),
    ):
        rejected = _toolbox_call(a, a_client, operation, arguments)
        assert rejected["result"].get("isError"), rejected
        assert started["claim_token"] not in json.dumps(rejected)
    new = _toolbox_call(
        b,
        b_client,
        "projectSetup.start",
        {**start_args, "session_id": b_session, "idempotency_key": "B-deliberate-new"},
    )
    assert not new["result"].get("isError"), new
    successor = _operation_data(_structured(new))
    assert not successor["attached"] and successor["claim_token"]
    assert successor["attempt"]["request_id"] != request
    fresh = {
        **late,
        "session_id": b_session,
        "request_id": successor["attempt"]["request_id"],
        "claim_token": successor["claim_token"],
        "expected_revision": 1,
        "idempotency_key": "B-progress",
        "report": {
            "status": "running",
            "progress": {"message": "New owner", "evidence_ref": "fixture/new-owner"},
        },
    }
    result = _toolbox_call(b, b_client, "projectSetup.report", fresh)
    assert not result["result"].get("isError"), result
    with Session(mcp_app.state.engine) as session:
        assert session.get(AgentRequest, request).claim_token_hash is None
        assert (
            ProjectSetupRepository(session)
            ._row(project, "agency.project-setup")
            .model_dump(mode="json")
            == receipt_before
        )
