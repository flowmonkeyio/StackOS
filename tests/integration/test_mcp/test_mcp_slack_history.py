"""Agent-readable Slack history content through direct and granted action paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.auth_providers import AuthRepository
from stackos.repositories.resources import ResourceRepository

from .conftest import MCPClient

_ACTION = "communications.slack-bot.conversation.history"
_TOKEN = "xoxb-1234567890-test-history-token"
_SIGNING_SECRET = "0123456789-signing-secret-sentinel"
_TEXT = "Full operational customer context. " * 40


def _setup(mcp: MCPClient, project_id: int) -> str:
    mcp.call_tool_structured("action.describe", {"action_ref": _ACTION})
    engine = mcp.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential = (
            AuthRepository(session)
            .store_credential(
                provider_key="slack-bot",
                auth_method_key="bot-token",
                display_name="Fixture Slack",
                fields={"bot_token": _TOKEN, "signing_secret": _SIGNING_SECRET},
                attach_project_id=project_id,
            )
            .data.credential_ref
        )
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="communication-profile",
            external_id="communication-profile:history-fixture",
            title="History fixture",
            data_json={
                "key": "history-fixture",
                "enabled": True,
                "provider_facets": {"slack-bot": {"credential_ref": credential}},
                "identity": {},
                "access_policy": {},
                "send_policy": {},
                "trigger_policy": {},
                "context_policy": {},
                "response_policy": {},
            },
            provenance_json={"source": "test"},
        )
        return credential


def _body() -> dict[str, Any]:
    return {
        "ok": True,
        "has_more": True,
        "is_limited": True,
        "response_metadata": {"next_cursor": "safe-cursor"},
        "messages": [
            {
                "ts": "1770000000.000300",
                "text": _TEXT + _SIGNING_SECRET,
                "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": _TEXT + _TOKEN}}],
                "attachments": [
                    {"text": _TEXT, "fields": [{"title": "Context", "value": _SIGNING_SECRET}]}
                ],
                "files": [
                    {
                        "id": "F123",
                        "name": None,
                        "file_access": "check_file_info",
                        "title": _SIGNING_SECRET,
                        "url_private": "https://files.slack.com/private-file",
                    }
                ],
            }
        ],
    }


def _input(include_content: bool | None = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "profile_ref": "communication-profile:history-fixture",
        "channel_ref": "slack-channel:C123",
        "limit": 5,
    }
    if include_content is not None:
        result["include_content"] = include_content
    return result


@pytest.mark.parametrize("include_content", [None, False, True])
def test_slack_history_direct_content_survives_file_and_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    include_content: bool | None,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential = _setup(mcp_client, project_id)
    httpx_mock.add_response(
        method="GET",
        url="https://slack.com/api/conversations.history?channel=C123&limit=5",
        json=_body(),
    )
    result = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": _ACTION,
            "credential_ref": credential,
            "input_json": _input(include_content),
        },
    )["data"]
    assert result["status"] == "success"
    assert result["output"]["output_mode"] == "file"
    saved = json.loads(Path(result["output"]["path"]).read_text())
    data = saved["response"]["output_json"]
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"action_key": "slack-bot.conversation.history"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"][0]
    assert audit["response_json"]["file"]["path"] == result["output"]["path"]
    assert data["is_limited"] is True
    assert data["has_more"] is True
    assert data["next_cursor"] == "safe-cursor"
    message = data["messages"][0]
    assert message["text_preview"] == _TEXT[:500]
    assert message["text_preview_truncated"] is True
    assert message["message_ref"] == "slack-message:C123:1770000000.000300"
    if include_content:
        assert message["text"] == _TEXT + "[redacted]"
        assert message["blocks"][0]["text"]["text"] == _TEXT + "[redacted]"
        assert message["files"] == [
            {
                "file_ref": "slack-file:F123",
                "name": None,
                "file_access": "check_file_info",
                "title": "[redacted]",
            }
        ]
    else:
        for field in ("text", "blocks", "attachments", "files"):
            assert field not in message
    assert data["content_included"] is (include_content is True)
    serialized = json.dumps({"result": result, "saved": saved, "audit": audit})
    for excluded in (_TOKEN, _SIGNING_SECRET, "private-file"):
        assert excluded not in serialized
    assert len(httpx_mock.get_requests()) == 1


def test_slack_history_content_respects_actor_account_and_step_grants(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential = _setup(mcp_client, project_id)
    plan = {
        "schema_version": "stackos.run-plan.v1",
        "key": "slack.history-proof",
        "title": "Slack history proof",
        "grants": {
            "mcp_tool_grants": [
                {"step_id": "history", "tool": "action.execute", "action_refs": [_ACTION]}
            ]
        },
        "steps": [{"id": "history", "title": "Read history", "action_refs": [_ACTION]}],
    }
    plan_id = mcp_client.call_tool_structured(
        "runPlan.create", {"project_id": project_id, "run_plan_json": plan}
    )["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": plan_id}
    )["data"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": plan_id, "step_id": "history", "run_token": started["run_token"]},
    )["data"]
    args = {
        "project_id": project_id,
        "credential_ref": credential,
        "run_token": started["run_token"],
        "response_mode": "raw",
        "output_policy_json": {"mode": "inline"},
    }
    denied = mcp_client.call_tool_error(
        "action.execute",
        {
            **args,
            "action_ref": "communications.slack-bot.conversation.info",
            "input_json": _input(None),
        },
    )
    assert denied["message"] == "ToolNotGrantedError"
    invalid_actor = mcp_client.call_tool_error(
        "action.execute",
        {
            **args,
            "action_ref": _ACTION,
            "input_json": {**_input(), "profile_ref": "communication-profile:missing"},
        },
    )
    assert invalid_actor["message"] == "ConflictError"
    assert "Slack communication profile not found" in json.dumps(invalid_actor)
    engine = mcp_client.test_client.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        other_credential = (
            AuthRepository(session)
            .store_credential(
                provider_key="slack-bot",
                auth_method_key="bot-token",
                display_name="Other fixture account",
                fields={
                    "bot_token": "xoxb-other-fixture-token",
                    "signing_secret": "other-signing-fixture",
                },
                attach_project_id=project_id,
            )
            .data.credential_ref
        )
    wrong_account = mcp_client.call_tool_error(
        "action.execute",
        {
            **args,
            "action_ref": _ACTION,
            "credential_ref": other_credential,
            "input_json": _input(),
        },
    )
    assert wrong_account["message"] == "ConflictError"
    assert "does not match the selected Account" in json.dumps(wrong_account)
    assert httpx_mock.get_requests() == []
    httpx_mock.add_response(
        method="GET",
        url="https://slack.com/api/conversations.history?channel=C123&limit=5",
        json=_body(),
    )
    result = mcp_client.call_tool_structured(
        "action.execute", {**args, "action_ref": _ACTION, "input_json": _input()}
    )["data"]
    assert result["output_json"]["messages"][0]["text"] == _TEXT + "[redacted]"
    assert result["action_call"]["run_plan_step_id"] == claimed["id"]
    audit_response = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"run_plan_id": plan_id, "run_plan_step_id": claimed["id"], "status": "success"},
        headers=mcp_client._headers(),
    )
    assert audit_response.status_code == 200
    audit = audit_response.json()["items"][0]
    assert audit["id"] == result["action_call"]["id"]
    assert audit["response_json"]["messages"][0]["text"] == _TEXT + "[redacted]"
    serialized = json.dumps({"result": result, "audit": audit})
    assert _TOKEN not in serialized
    assert _SIGNING_SECRET not in serialized
    assert len(httpx_mock.get_requests()) == 1
