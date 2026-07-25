"""Generated MCP surface, grant, and audit proofs for Linear actions."""

from __future__ import annotations

import json
from typing import Any

from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.models import Credential, CredentialAccount, CredentialScope
from tests.integration.account_test_support import seed_test_account

from .conftest import MCPClient


def _seed_linear_credential(
    *,
    settings: Settings,
    project_id: int,
    auth_method_key: str = "oauth2_authorization_code",
) -> str:
    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            config_json: dict[str, str] = {"auth_method_key": auth_method_key}
            if auth_method_key == "oauth2_authorization_code":
                config_json["scope_status"] = "known"
            backing = seed_test_account(
                session,
                project_id=project_id,
                provider_key="linear",
                display_name="Linear - Primary",
                secret_payload=(
                    b"linear-personal-key-sentinel"
                    if auth_method_key == "personal_api_key"
                    else json.dumps({"access_token": "linear-mcp-secret"}).encode()
                ),
                config_json=config_json,
            )
            credential = session.exec(
                select(Credential).where(Credential.integration_credential_id == backing.data.id)
            ).one()
            credential.auth_type = "api-key" if auth_method_key == "personal_api_key" else "oauth"
            credential.auth_method_key = auth_method_key
            if auth_method_key == "oauth2_authorization_code":
                credential.config_json = {
                    **(credential.config_json or {}),
                    "scope_status": "known",
                }
            session.add(credential)
            assert credential.id is not None
            session.add(
                CredentialAccount(
                    credential_id=credential.id,
                    provider_account_id="linear-org-mcp",
                    display_name="Linear MCP Workspace",
                )
            )
            if auth_method_key == "oauth2_authorization_code":
                for scope in ("read", "write"):
                    session.add(CredentialScope(credential_id=credential.id, scope=scope))
            session.commit()
            return credential.credential_ref
    finally:
        engine.dispose()


def _linear_plan() -> dict[str, Any]:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "linear.viewer-proof.run",
        "title": "Linear viewer proof",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "read-viewer",
                    "tool": "action.execute",
                    "action_refs": ["linear.viewer.get"],
                }
            ]
        },
        "steps": [
            {
                "id": "read-viewer",
                "title": "Read Linear viewer",
                "action_refs": ["linear.viewer.get"],
            }
        ],
    }


def test_linear_uses_only_generated_generic_action_surfaces(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_linear_credential(
        settings=mcp_settings,
        project_id=project_id,
    )
    tools = {tool["name"] for tool in mcp_client.list_tools()}
    listed = mcp_client.call_tool_structured(
        "action.list",
        {
            "project_id": project_id,
            "plugin_slug": "linear",
            "include_unavailable_integrations": True,
            "response_mode": "raw",
        },
    )
    described = mcp_client.call_tool_structured(
        "action.describe",
        {
            "project_id": project_id,
            "action_ref": "linear.issues.update",
            "response_mode": "raw",
        },
    )
    validation = mcp_client.call_tool_structured(
        "action.validate",
        {
            "project_id": project_id,
            "action_ref": "linear.issues.search",
            "input_json": {"term": "oauth", "first": 25},
            "credential_ref": credential_ref,
            "response_mode": "raw",
        },
    )

    linear_items = [item for item in listed["items"] if item["action_ref"].startswith("linear.")]
    assert len(linear_items) == 34
    assert described["manifest"]["connector_key"] == "linear"
    assert described["manifest"]["required_scopes"] == ["write"]
    assert described["manifest"]["risk_level"] == "write"
    assert validation["valid"] is True
    assert validation["credential_ref"] == credential_ref
    assert "action.list" in tools
    assert "action.describe" in tools
    assert "action.validate" in tools
    assert "action.run" in tools
    assert "action.execute" in tools
    assert not any(name.startswith("linear.") for name in tools)


def test_linear_run_plan_grant_executes_and_records_audit_linkage(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_linear_credential(
        settings=mcp_settings,
        project_id=project_id,
    )
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _linear_plan()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "read-viewer",
            "run_token": started["data"]["run_token"],
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "viewer": {
                    "id": "linear-user-mcp",
                    "name": "MCP Operator",
                    "organization": {
                        "id": "linear-org-mcp",
                        "name": "Linear MCP Workspace",
                    },
                }
            }
        },
        headers={"x-request-id": "linear-mcp-request"},
    )

    executed = mcp_client.call_tool_structured(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "linear.viewer.get",
            "input_json": {},
            "credential_ref": credential_ref,
            "run_token": started["data"]["run_token"],
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )

    data = executed["data"]
    assert data["action_call"]["connector_key"] == "linear"
    assert data["output_json"]["data"]["viewer"]["user_ref"].startswith("provider-object:")
    assert data["metadata_json"]["schema_ref"] == "graphql/viewer/get.graphql"
    assert data["metadata_json"]["schema_operation"] == "viewer"
    assert data["metadata_json"]["request_id"] == "linear-mcp-request"
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": created["data"]["id"],
            "run_plan_step_id": claimed["data"]["id"],
            "plugin_slug": "linear",
            "action_key": "viewer.get",
            "status": "success",
        },
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"][0]
    assert audit["id"] == data["action_call"]["id"]
    assert audit["run_id"] == started["data"]["run_id"]
    assert audit["run_plan_id"] == created["data"]["id"]
    assert audit["run_plan_step_id"] == claimed["data"]["id"]

    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "linear.teams.list",
            "input_json": {},
            "credential_ref": credential_ref,
            "run_token": started["data"]["run_token"],
            "response_mode": "raw",
        },
    )
    assert denied["message"] == "ToolNotGrantedError"
    assert denied["data"]["tool"] == "action.execute"
    assert len(httpx_mock.get_requests()) == 1


def test_linear_personal_api_key_direct_action_uses_raw_transport_and_redacts_audit(
    mcp_client: MCPClient,
    mcp_settings: Settings,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _seed_linear_credential(
        settings=mcp_settings,
        project_id=project_id,
        auth_method_key="personal_api_key",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "viewer": {
                    "id": "linear-personal-user",
                    "name": "Personal Operator",
                    "organization": {
                        "id": "linear-personal-org",
                        "name": "Personal Workspace",
                    },
                }
            }
        },
    )

    out = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "linear.viewer.get",
            "credential_ref": credential_ref,
            "input_json": {},
            "output_policy_json": {"mode": "inline"},
            "response_mode": "raw",
        },
    )

    request = httpx_mock.get_requests()[0]
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        headers=mcp_client._headers(),
    )
    rendered = json.dumps({"out": out, "audit": audit_response.json()})
    assert request.headers["Authorization"] == "linear-personal-key-sentinel"
    assert request.headers["Authorization"] != "Bearer linear-personal-key-sentinel"
    assert out["data"]["action_call"]["connector_key"] == "linear"
    assert audit_response.status_code == 200
    assert "linear-personal-key-sentinel" not in rendered
