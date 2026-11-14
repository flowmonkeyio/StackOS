"""Setup reads use actual bound bridge scope, never caller-supplied portfolio authority."""

import json

import pytest
from sqlmodel import Session, select

from stackos.db.models import RunPlan, TrackerTask
from stackos.host_mcp import service
from stackos.repositories.project_setup import ProjectSetupRepository
from stackos.workflows.authoring_guide import PROJECT_SETUP_NOTICES
from tests.integration.test_repositories.test_project_setup import verified_result

from .test_mcp_bridge_agent_path import (
    _create_project,
    _initialize,
    _is_bridge_scope_error,
    _operation_data,
    _scoped_bridge,
    _structured,
    _tool_call,
    _toolbox_call,
)


def test_setup_read_injected_project_and_foreign_rejection(mcp_client, tmp_path, monkeypatch):
    other = _create_project(mcp_client, "other-setup")
    own = mcp_client.call_tool_structured(
        "workspace.bootstrap", {"cwd": str(tmp_path), "project_name": "Setup bound"}
    )["project_id"]
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
    _initialize(proxy, client)
    _tool_call(proxy, client, "workspace.startSession", {"auto_bootstrap": False})
    result = _toolbox_call(proxy, client, "projectSetup.get", {})
    assert not result["result"].get("isError"), result
    data = _operation_data(_structured(result))
    assert data["project_id"] == own
    assert data["eligible"] is True
    assert data["status"] == "setup_needed"
    assert data["notice"] == PROJECT_SETUP_NOTICES["setup_needed"]
    probes = []
    monkeypatch.setattr(service, "inspect_host", lambda host: probes.append(host))
    preview = _toolbox_call(proxy, client, "projectSetup.get", {"resolve_launch_preferences": True})
    assert preview["result"].get("isError"), preview
    assert "local-console authority" in str(preview), preview
    assert probes == []
    rejected = _toolbox_call(proxy, client, "projectSetup.get", {"project_id": other})
    assert _is_bridge_scope_error(rejected), rejected
    denied = _toolbox_call(proxy, client, "projectSetup.setReminders", {"dismiss_setup": True})
    assert denied["result"].get("isError"), denied
    unchanged = mcp_client.call_tool_structured(
        "projectSetup.get", {"project_id": own, "response_mode": "raw"}
    )
    assert unchanged["setup_reminder_dismissed_at"] is None
    foreign = mcp_client.call_tool_structured(
        "projectSetup.get", {"project_id": other, "response_mode": "raw"}
    )
    assert foreign["setup_reminder_dismissed_at"] is None
    assert probes == []


def test_existing_session_setup_owner_report_and_startup_projection(mcp_client, tmp_path):
    foreign_project = _create_project(mcp_client, "foreign-setup-report")
    mcp_client.call_tool_structured("workspace.bootstrap", {"cwd": str(tmp_path)})
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
    _initialize(proxy, client)
    startup = _operation_data(_structured(_tool_call(proxy, client, "workspace.startSession")))
    session_id = startup["agent_session_id"]
    summary = startup["project_setup"]
    assert summary["eligible"] and summary["current_attempt"] is None
    assert summary["guidance_ref"]["tool_name"] == "workflowTemplate.authoringGuide"
    assert summary["notice"] == PROJECT_SETUP_NOTICES["setup_needed"]
    assert "available_sources" not in json.dumps(summary)
    assert startup["setup_state"]["project_scoped_tools_usable"]
    args = {"session_id": session_id, "workflow_keys": ["agency.project-setup"]}
    opened_call = _toolbox_call(
        proxy, client, "projectSetup.start", {**args, "idempotency_key": "start-once"}
    )
    assert not opened_call["result"].get("isError"), opened_call
    assert _structured(opened_call)["operation"] == "projectSetup.start"
    opened = _operation_data(_structured(opened_call))
    assert opened["claim_token"] and not opened["attached"]
    assert opened["attempt"]["requested_scope"] == "ready_to_use"
    replayed = _operation_data(
        _structured(
            _toolbox_call(
                proxy,
                client,
                "projectSetup.start",
                {**args, "idempotency_key": "start-once", "response_mode": "raw"},
            )
        )
    )
    assert replayed["claim_token"] == opened["claim_token"]
    attached = _operation_data(
        _structured(_toolbox_call(proxy, client, "projectSetup.start", args))
    )
    assert attached["attached"] and attached["claim_token"] is None
    assert attached["attempt"]["request_id"] == opened["attempt"]["request_id"]
    report_args = {
        "session_id": session_id,
        "request_id": opened["attempt"]["request_id"],
        "claim_token": opened["claim_token"],
        "expected_revision": 1,
        "report": {
            "status": "needs_input",
            "progress": {"message": "Asked here", "evidence_ref": "fixture/asked"},
            "question": {
                "question_id": "goal",
                "revision": 1,
                "prompt": "What is the project goal?",
                "workflow_keys": ["agency.project-setup"],
            },
        },
    }
    foreign = _toolbox_call(
        proxy, client, "projectSetup.report", {**report_args, "project_id": foreign_project}
    )
    assert _is_bridge_scope_error(foreign), foreign
    denied = _toolbox_call(
        proxy, client, "projectSetup.report", {**report_args, "claim_token": "foreign"}
    )
    assert denied["result"].get("isError"), denied
    other_proxy, other_client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
    other_proxy.client_session_id = "foreign-setup-session"
    _initialize(other_proxy, other_client)
    _tool_call(other_proxy, other_client, "workspace.startSession")
    stolen = _toolbox_call(other_proxy, other_client, "projectSetup.report", report_args)
    assert stolen["result"].get("isError"), stolen
    spoofed = _toolbox_call(
        other_proxy,
        other_client,
        "projectSetup.report",
        {**report_args, "client_session_id": proxy.client_session_id},
    )
    assert _is_bridge_scope_error(spoofed), spoofed
    before = _operation_data(_structured(_toolbox_call(proxy, client, "projectSetup.get", {})))
    assert before["current_attempt"]["revision"] == 1
    reported = _toolbox_call(proxy, client, "projectSetup.report", report_args)
    assert not reported["result"].get("isError"), reported
    assert _structured(reported)["operation"] == "projectSetup.report"
    result = _operation_data(_structured(reported))
    assert result["revision"] == 2 and result["question"]["response_channel"] == "existing_agent"
    assert "reports" not in result
    stale = _toolbox_call(proxy, client, "projectSetup.report", report_args)
    assert stale["result"].get("isError"), stale
    assert "revision" in json.dumps(stale)
    later = _operation_data(_structured(_tool_call(proxy, client, "workspace.startSession")))
    assert later["project_setup"]["current_attempt"]["revision"] == 2
    assert "claim_token" not in json.dumps(later)
    assert "conversation_url" not in json.dumps(later)


def test_ready_scope_compact_coverage_and_activation_repair_are_truthful(
    mcp_client, mcp_app, tmp_path
):
    mcp_client.call_tool_structured("workspace.bootstrap", {"cwd": str(tmp_path)})
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
    _initialize(proxy, client)
    startup = _operation_data(_structured(_tool_call(proxy, client, "workspace.startSession")))
    opened = _operation_data(
        _structured(
            _toolbox_call(
                proxy,
                client,
                "projectSetup.start",
                {
                    "session_id": startup["agent_session_id"],
                    "workflow_keys": ["agency.project-setup"],
                },
            )
        )
    )
    with Session(mcp_app.state.engine) as session:
        result = verified_result(
            ProjectSetupRepository(session), opened["attempt"]["project_id"], "agency.project-setup"
        )
    result.attempt_ref = opened["attempt"]["attempt_ref"]
    args = {
        "session_id": startup["agent_session_id"],
        "request_id": opened["attempt"]["request_id"],
        "claim_token": opened["claim_token"],
        "expected_revision": 1,
        "report": {
            "status": "running",
            "progress": {"message": "Prepared", "evidence_ref": "fixture/prepared"},
            "workflow_results": [
                {
                    "workflow_key": "agency.project-setup",
                    "expected_revision": 0,
                    "result": result.model_dump(mode="json"),
                }
            ],
        },
    }
    progress = _toolbox_call(proxy, client, "projectSetup.report", args)
    assert not progress["result"].get("isError"), progress
    body = _operation_data(_structured(progress))
    assert body["requested_scope"] == "ready_to_use"
    assert body["outcomes"]["agency.project-setup"]["activation_status"] == "pending"
    state = _operation_data(_structured(_toolbox_call(proxy, client, "projectSetup.get", {})))
    workflow = state["workflows"][0]
    assert workflow["activation_status"] == "pending"
    assert workflow["missing_activation_claims"] == ["saved_defaults", "native_consumption"]
    denied = _toolbox_call(
        proxy,
        client,
        "projectSetup.report",
        {
            **args,
            "expected_revision": 2,
            "report": {
                "status": "success",
                "progress": {"message": "Done", "evidence_ref": "fixture/end"},
            },
        },
    )
    assert denied["result"].get("isError"), denied
    repair = json.dumps(denied)
    assert "complete_activation" in repair and "missing_claims" in repair
    assert opened["claim_token"] not in repair
    unchanged = _operation_data(_structured(_toolbox_call(proxy, client, "projectSetup.get", {})))
    assert unchanged == state
    # A real report call must also reject failed logical proof after process exit 0.
    from tests.integration.test_repositories.test_project_setup_activation import activation_result

    with Session(mcp_app.state.engine) as session:
        repo = ProjectSetupRepository(session)
        checked, native = activation_result(
            (opened["attempt"]["project_id"], startup["agent_session_id"], repo),
            session,
            workflow_key="agency.project-setup",
        )
        native.metadata_json = {
            "native_execution": {
                **native.metadata_json["native_execution"],
                "result": {"status": "failed", "summary": "Check failed", "run_plan_id": None},
            }
        }
        session.add(native)
        session.commit()
        from stackos.repositories.project_setup import _digest

        checked.verification.activation_proof.checks[0].reference.outcome_digest = _digest(
            native.metadata_json["native_execution"]["result"]
        )
    checked.attempt_ref = opened["attempt"]["attempt_ref"]
    rejected = _toolbox_call(
        proxy,
        client,
        "projectSetup.report",
        {
            **args,
            "expected_revision": 2,
            "report": {
                "status": "success",
                "progress": {"message": "Checked", "evidence_ref": "fixture/check"},
                "workflow_results": [
                    {
                        "workflow_key": "agency.project-setup",
                        "expected_revision": 1,
                        "result": checked.model_dump(mode="json"),
                    }
                ],
            },
        },
    )
    assert rejected["result"].get("isError"), rejected
    assert "revalidate_activation" in json.dumps(rejected)
    assert opened["claim_token"] not in json.dumps(rejected)
    assert (
        _operation_data(_structured(_toolbox_call(proxy, client, "projectSetup.get", {}))) == state
    )


@pytest.mark.parametrize("selected", [False, True])
def test_global_startup_keeps_explicit_selection_and_directoryless_status(mcp_client, selected):
    project = _create_project(mcp_client, "directoryless-setup")
    proxy, client = _scoped_bridge(mcp_client, cwd="/")
    _initialize(proxy, client)
    result = _tool_call(
        proxy,
        client,
        "workspace.startSession",
        {
            "global_session": True,
            **({"project_id": project} if selected else {}),
        },
    )
    assert not result["result"].get("isError"), result
    data = _operation_data(_structured(result))
    assert data["setup_state"]["workspace_bound"] is False
    if selected:
        assert data["project_setup"]["eligible"] is False
        assert data["project_setup"]["status"] == "ineligible"
        assert data["project_setup"]["current_attempt"] is None
    else:
        assert data["project_setup"] is None


@pytest.mark.parametrize("compact_sources", [False, True])
def test_checked_workflow_results_through_mcp_keep_success_and_no_business_execution(
    mcp_client, mcp_app, tmp_path, compact_sources
):
    mcp_client.call_tool_structured("workspace.bootstrap", {"cwd": str(tmp_path)})
    proxy, client = _scoped_bridge(mcp_client, cwd=str(tmp_path))
    _initialize(proxy, client)
    startup = _operation_data(_structured(_tool_call(proxy, client, "workspace.startSession")))
    opened = _operation_data(
        _structured(
            _toolbox_call(
                proxy,
                client,
                "projectSetup.start",
                {
                    "session_id": startup["agent_session_id"],
                    "workflow_keys": ["engineering.tracked-delivery", "agency.project-setup"],
                },
            )
        )
    )
    with Session(mcp_app.state.engine) as session:
        counts = [len(session.exec(select(model)).all()) for model in (RunPlan, TrackerTask)]
        proof = verified_result(ProjectSetupRepository(session), startup["project_id"])
    proof.attempt_ref = opened["attempt"]["attempt_ref"]
    args = {
        "session_id": startup["agent_session_id"],
        "request_id": opened["attempt"]["request_id"],
        "expected_revision": 1,
        "claim_token": opened["claim_token"],
        "report": {
            "status": "partial",
            "progress": {"message": "One workflow checked", "evidence_ref": "fixture/checked"},
            "workflow_results": [
                {
                    "workflow_key": "engineering.tracked-delivery",
                    "expected_revision": 0,
                    "result": proof.model_dump(mode="json"),
                },
                {
                    "workflow_key": "agency.project-setup",
                    "expected_revision": 0,
                    "result": {
                        "attempt_ref": proof.attempt_ref,
                        "result_ref": "fixture/second",
                        "status": "failed",
                        "changes": ["partial local edit"],
                    },
                },
            ],
        },
    }
    if compact_sources:
        for source in args["report"]["workflow_results"][0]["result"]["verification"]["sources"]:
            source.pop("content")
        denied = _toolbox_call(
            proxy, client, "projectSetup.report", {**args, "claim_token": "foreign"}
        )
        assert denied["result"].get("isError"), denied
        state = _operation_data(_structured(_toolbox_call(proxy, client, "projectSetup.get", {})))
        assert state["current_attempt"]["revision"] == 1
        assert state["configured_count"] == 0
    reported = _toolbox_call(proxy, client, "projectSetup.report", args)
    assert not reported["result"].get("isError"), reported
    result = _operation_data(_structured(reported))
    assert result["outcomes"]["engineering.tracked-delivery"]["status"] == "success"
    assert result["outcomes"]["agency.project-setup"]["status"] == "failed"
    state = _operation_data(
        _structured(_toolbox_call(proxy, client, "projectSetup.get", {"response_mode": "raw"}))
    )
    assert state["configured_count"] == 1
    success = next(
        w for w in state["workflows"] if w["workflow_key"] == "engineering.tracked-delivery"
    )
    assert success["successful"]["verification"]["readiness"]["structurally_ready"]
    assert success["successful"]["verification"]["readiness"]["execution_ready"] is False
    assert success["successful"]["verification"]["readiness"]["deferred_inputs"] == ["goal"]
    assert success["successful"]["verification"]["sources"] == [
        source.model_dump(mode="json") for source in proof.verification.sources
    ]
    with Session(mcp_app.state.engine) as session:
        assert [
            len(session.exec(select(model)).all()) for model in (RunPlan, TrackerTask)
        ] == counts
    forbidden = mcp_client.test_client.post(
        "/api/v1/operations/projectSetup.report/call",
        json={**args, "project_id": startup["project_id"]},
        headers={"Authorization": f"Bearer {mcp_client.auth_token}"},
    )
    assert forbidden.status_code == 404
