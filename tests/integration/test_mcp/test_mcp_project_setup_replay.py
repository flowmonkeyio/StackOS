"""Setup retries preserve owner authority before returning canonical cached data."""

import copy
import json

import pytest
from sqlmodel import Session, select

from stackos.db.models import AgentRequest, IdempotencyKey
from stackos.operations.project_setup_state import SETUP_REPLAY_DIGEST_FIELD
from stackos.repositories.project_setup import ProjectSetupRepository
from tests.integration.test_repositories.test_project_setup import verified_result

from .test_mcp_bridge_agent_path import (
    _initialize,
    _operation_data,
    _scoped_bridge,
    _structured,
    _tool_call,
    _toolbox_call,
)


def _open(mcp_client, folder, *, client_id=None):
    proxy, client = _scoped_bridge(mcp_client, cwd=str(folder))
    if client_id is not None:
        proxy.client_session_id = client_id
    _initialize(proxy, client)
    startup = _operation_data(_structured(_tool_call(proxy, client, "workspace.startSession")))
    return proxy, client, startup["agent_session_id"]


@pytest.fixture
def setup_owner(mcp_client, tmp_path):
    mcp_client.call_tool_structured("workspace.bootstrap", {"cwd": str(tmp_path)})
    proxy, client, session_id = _open(mcp_client, tmp_path)
    args = {
        "session_id": session_id,
        "workflow_keys": ["agency.project-setup"],
        "idempotency_key": "owner-start",
        "requested_scope": "infrastructure_only",
    }
    response = _toolbox_call(proxy, client, "projectSetup.start", args)
    assert not response["result"].get("isError"), response
    return proxy, client, args, _operation_data(_structured(response))


def _report_args(start, opened, *, status="running"):
    return {
        "session_id": start["session_id"],
        "request_id": opened["attempt"]["request_id"],
        "claim_token": opened["claim_token"],
        "expected_revision": 1,
        "idempotency_key": "owner-report",
        "report": {
            "status": status,
            "progress": {"message": "Owner progress", "evidence_ref": "fixture/owner"},
        },
    }


def _state(proxy, client):
    return _operation_data(_structured(_toolbox_call(proxy, client, "projectSetup.get", {})))


def _denied(response, token):
    assert response["result"].get("isError"), response
    assert token not in json.dumps(response)
    assert "inspect_setup" in json.dumps(response)


def test_start_replay_fences_foreign_session_and_preserves_owner_retry(
    setup_owner, mcp_client, mcp_app, tmp_path
):
    proxy, client, args, opened = setup_owner
    other, other_client, other_id = _open(mcp_client, tmp_path, client_id="foreign-start")
    before = _state(proxy, client)
    foreign = _toolbox_call(
        other, other_client, "projectSetup.start", {**args, "session_id": other_id}
    )
    _denied(foreign, opened["claim_token"])
    assert _state(proxy, client) == before
    # Omitted versus explicit defaults and response presentation are equivalent.
    owner = _toolbox_call(
        proxy,
        client,
        "projectSetup.start",
        {**args, "mode": "setup", "entry_kind": "existing_agent", "response_mode": "raw"},
    )
    assert not owner["result"].get("isError"), owner
    assert _structured(owner)["idempotency_replay"] is True
    assert _operation_data(_structured(owner))["claim_token"] == opened["claim_token"]
    assert SETUP_REPLAY_DIGEST_FIELD not in json.dumps(owner)
    with Session(mcp_app.state.engine) as session:
        assert len(session.exec(select(AgentRequest)).all()) == 1
    # A foreign agent can still attach under its own key, without writer authority.
    attached = _toolbox_call(
        other,
        other_client,
        "projectSetup.start",
        {**args, "session_id": other_id, "idempotency_key": "observer-start"},
    )
    data = _operation_data(_structured(attached))
    assert data["attached"] and data["claim_token"] is None
    replay = _toolbox_call(
        other,
        other_client,
        "projectSetup.start",
        {
            **args,
            "session_id": other_id,
            "idempotency_key": "observer-start",
            "response_mode": "raw",
        },
    )
    assert _operation_data(_structured(replay))["claim_token"] is None
    assert _structured(replay)["idempotency_replay"]


@pytest.mark.parametrize(
    "actor", ["foreign_invalid_token", "foreign_copied_token", "owner_invalid_token"]
)
def test_report_replay_rejects_foreign_and_invalid_claims(setup_owner, mcp_client, tmp_path, actor):
    proxy, client, start, opened = setup_owner
    args = _report_args(start, opened)
    good = _toolbox_call(proxy, client, "projectSetup.report", args)
    assert not good["result"].get("isError"), good
    before = _state(proxy, client)
    if actor.startswith("foreign"):
        caller, transport, session_id = _open(mcp_client, tmp_path, client_id="foreign-report")
    else:
        caller, transport, session_id = proxy, client, start["session_id"]
    token = opened["claim_token"] if actor == "foreign_copied_token" else "invalid"
    rejected = _toolbox_call(
        caller,
        transport,
        "projectSetup.report",
        {**args, "session_id": session_id, "claim_token": token},
    )
    _denied(rejected, opened["claim_token"])
    assert _state(proxy, client) == before
    # Rejections retain the original key and canonical raw owner response.
    retry = _toolbox_call(
        proxy,
        client,
        "projectSetup.report",
        {
            **args,
            "response_mode": "raw",
            "report": {**args["report"], "question": None, "workflow_results": []},
        },
    )
    assert not retry["result"].get("isError"), retry
    data = _operation_data(_structured(retry))
    assert data["revision"] == 2 and len(data["reports"]) == 1
    assert _structured(retry)["idempotency_replay"]
    assert SETUP_REPLAY_DIGEST_FIELD not in json.dumps(retry)


def test_changed_report_intent_cannot_reuse_a_successful_key(setup_owner):
    proxy, client, start, opened = setup_owner
    args = _report_args(start, opened)
    assert not _toolbox_call(proxy, client, "projectSetup.report", args)["result"].get("isError")
    before = _state(proxy, client)
    changed = copy.deepcopy(args)
    changed.update(
        expected_revision=2,
        report={
            "status": "cancelled",
            "progress": {"message": "Stop", "evidence_ref": "fixture/stop"},
        },
    )
    _denied(_toolbox_call(proxy, client, "projectSetup.report", changed), opened["claim_token"])
    assert _state(proxy, client) == before
    assert not _toolbox_call(proxy, client, "projectSetup.report", args)["result"].get("isError")


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "update"},
        {"workflow_keys": ["engineering.tracked-delivery"]},
        {"requested_scope": "ready_to_use"},
    ],
)
def test_changed_start_intent_cannot_recover_a_writer_token(setup_owner, change):
    proxy, client, args, opened = setup_owner
    before = _state(proxy, client)
    _denied(
        _toolbox_call(proxy, client, "projectSetup.start", {**args, **change}),
        opened["claim_token"],
    )
    assert _state(proxy, client) == before


@pytest.mark.parametrize("status", ["success", "cancelled"])
def test_terminal_report_retries_are_historical_and_cannot_operate_on_successor(
    setup_owner, mcp_app, status
):
    proxy, client, start, opened = setup_owner
    args = _report_args(start, opened, status=status)
    if status == "success":
        with Session(mcp_app.state.engine) as session:
            proof = verified_result(
                ProjectSetupRepository(session),
                opened["attempt"]["project_id"],
                "agency.project-setup",
            )
        proof.attempt_ref = opened["attempt"]["attempt_ref"]
        args["report"]["workflow_results"] = [
            {
                "workflow_key": "agency.project-setup",
                "expected_revision": 0,
                "result": proof.model_dump(mode="json"),
            }
        ]
    original = _toolbox_call(proxy, client, "projectSetup.report", args)
    assert not original["result"].get("isError"), original
    for successor in (False, True):
        if successor:
            new = _toolbox_call(
                proxy, client, "projectSetup.start", {**start, "idempotency_key": "new-attempt"}
            )
            assert not new["result"].get("isError"), new
            request_id = _operation_data(_structured(new))["attempt"]["request_id"]
            assert request_id != args["request_id"]
        before = _state(proxy, client)
        retry = _toolbox_call(
            proxy, client, "projectSetup.report", {**args, "response_mode": "raw"}
        )
        assert not retry["result"].get("isError"), retry
        data = _operation_data(_structured(retry))
        assert data["status"] == status and data["request_id"] == args["request_id"]
        assert _structured(retry)["idempotency_replay"]
        _denied(
            _toolbox_call(proxy, client, "projectSetup.report", {**args, "claim_token": "invalid"}),
            opened["claim_token"],
        )
        if successor:
            _denied(
                _toolbox_call(
                    proxy, client, "projectSetup.report", {**args, "request_id": request_id}
                ),
                opened["claim_token"],
            )
        # A stale start cannot resurrect its revoked writer token.
        _denied(_toolbox_call(proxy, client, "projectSetup.start", start), opened["claim_token"])
        assert _state(proxy, client) == before


def test_cached_progress_cannot_misrepresent_a_finished_attempt(setup_owner):
    proxy, client, start, opened = setup_owner
    args = _report_args(start, opened)
    assert not _toolbox_call(proxy, client, "projectSetup.report", args)["result"].get("isError")
    terminal = copy.deepcopy(args)
    terminal.update(expected_revision=2, idempotency_key="terminal-report")
    terminal["report"]["status"] = "cancelled"
    assert not _toolbox_call(proxy, client, "projectSetup.report", terminal)["result"].get(
        "isError"
    )
    before = _state(proxy, client)
    _denied(_toolbox_call(proxy, client, "projectSetup.report", args), opened["claim_token"])
    assert _state(proxy, client) == before


def test_legacy_unbound_cache_fails_closed_without_disclosing_token(setup_owner, mcp_app):
    proxy, client, args, opened = setup_owner
    with Session(mcp_app.state.engine) as session:
        row = session.exec(
            select(IdempotencyKey).where(IdempotencyKey.tool_name == "projectSetup.start")
        ).one()
        row.response_json = {
            key: value
            for key, value in row.response_json.items()
            if key != SETUP_REPLAY_DIGEST_FIELD
        }
        session.add(row)
        session.commit()
    before = _state(proxy, client)
    _denied(_toolbox_call(proxy, client, "projectSetup.start", args), opened["claim_token"])
    assert _state(proxy, client) == before
