from __future__ import annotations

import json

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from content_stack.db.models import CredentialUsageEvent
from content_stack.repositories.agent_requests import AgentRequestRepository
from content_stack.repositories.run_plans import RunPlanRepository


def _sitemap_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.sitemap-action.run",
        "title": "Sitemap action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "fetch-sitemap",
                    "tool": "action.execute",
                    "action_refs": ["utils.sitemap.fetch"],
                }
            ]
        },
        "steps": [
            {
                "id": "fetch-sitemap",
                "title": "Fetch sitemap",
                "action_refs": ["utils.sitemap.fetch"],
            }
        ],
    }


def _mock_action_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.mock-provider.run",
        "title": "Mock provider action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "execute-mock",
                    "tool": "action.execute",
                    "action_refs": ["utils.mock.echo"],
                }
            ]
        },
        "steps": [
            {
                "id": "execute-mock",
                "title": "Execute mock provider",
                "action_refs": ["utils.mock.echo"],
            }
        ],
    }


def _agent_request_ingest_plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "agent-request-ingest.run",
        "title": "Agent request ingest",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "ingest",
                    "tool": "agentRequest.create",
                }
            ]
        },
        "steps": [{"id": "ingest", "title": "Ingest request"}],
    }


def _store_mock_credential(
    api: TestClient, project_id: int, secret: str = "mock-secret-token"
) -> str:
    credential = api.post(
        f"/api/v1/projects/{project_id}/auth/mock-provider/credentials",
        json={
            "auth_method_key": "api_key",
            "profile_key": "primary",
            "label": "Mock Primary",
            "fields": {"api_key": secret},
        },
    )
    assert credential.status_code == 201, credential.text
    return str(credential.json()["data"]["credential_ref"])


def _store_telegram_credential(api: TestClient, project_id: int) -> str:
    credential = api.post(
        f"/api/v1/projects/{project_id}/auth/telegram-bot/credentials",
        json={
            "auth_method_key": "bot-token",
            "profile_key": "support",
            "label": "Support Bot",
            "fields": {
                "bot_token": "123456:ABC",
                "webhook_secret_token": "telegram-secret",
                "api_base_url": "http://127.0.0.1:8081",
            },
        },
    )
    assert credential.status_code == 201, credential.text
    return str(credential.json()["data"]["credential_ref"])


def _start_mock_run_plan(api: TestClient, project_id: int) -> tuple[int, str, int, int]:
    created = api.post(
        "/api/v1/operations/runPlan.create/call",
        json={"arguments": {"project_id": project_id, "run_plan_json": _mock_action_plan_json()}},
    )
    assert created.status_code == 200, created.text
    run_plan_id = int(created.json()["data"]["id"])

    started = api.post(
        "/api/v1/operations/runPlan.start/call",
        json={"arguments": {"project_id": project_id, "run_plan_id": run_plan_id}},
    )
    assert started.status_code == 200, started.text
    run_token = str(started.json()["data"]["run_token"])
    run_id = int(started.json()["data"]["run_id"])

    claimed = api.post(
        "/api/v1/operations/runPlan.claimStep/call",
        json={
            "arguments": {
                "run_plan_id": run_plan_id,
                "step_id": "execute-mock",
                "run_token": run_token,
            }
        },
    )
    assert claimed.status_code == 200, claimed.text
    step_pk = int(claimed.json()["data"]["id"])
    return run_plan_id, run_token, run_id, step_pk


def test_operation_docs_are_agent_readable(api: TestClient) -> None:
    listed = api.get("/api/v1/operations", params={"surface": "rest"})
    described = api.get("/api/v1/operations/action.execute")
    run_plan = api.get("/api/v1/operations/runPlan.claimStep")

    assert listed.status_code == 200
    assert "action.execute" in {item["name"] for item in listed.json()["items"]}
    assert "runPlan.create" in {item["name"] for item in listed.json()["items"]}
    assert described.status_code == 200
    body = described.json()
    assert body["name"] == "action.execute"
    assert body["surfaces"]["mcp"]["enabled"] is True
    assert body["surfaces"]["rest"]["enabled"] is True
    assert body["surfaces"]["cli"]["enabled"] is True
    assert body["grant_policy"] == "run-plan-step-action-ref"
    assert "project_id" in body["input_schema"]["properties"]
    assert body["examples"][0]["arguments"]["action_ref"] == "utils.sitemap.fetch"
    assert any("credential_ref" in item for item in body["prerequisites"])

    assert run_plan.status_code == 200
    run_plan_body = run_plan.json()
    assert run_plan_body["grant_policy"] == "run-plan-controller"
    assert run_plan_body["surfaces"]["cli"]["command"] == "run-plans claim-step"
    assert any("run_token" in item for item in run_plan_body["prerequisites"])


def test_operation_rest_call_uses_registered_action_handler(api: TestClient) -> None:
    resp = api.post(
        "/api/v1/operations/action.describe/call",
        json={"arguments": {"action_ref": "core.catalog.describe"}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["manifest"]["action_ref"] == "core.catalog.describe"
    assert body["execution_available"] is False


def test_operation_rest_run_plan_lifecycle_uses_registered_handlers(
    api: TestClient,
    project_id: int,
) -> None:
    created = api.post(
        "/api/v1/operations/runPlan.create/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_plan_json": _sitemap_action_plan_json(),
            }
        },
    )
    assert created.status_code == 200
    run_plan_id = created.json()["data"]["id"]

    started = api.post(
        "/api/v1/operations/runPlan.start/call",
        json={"arguments": {"project_id": project_id, "run_plan_id": run_plan_id}},
    )
    assert started.status_code == 200
    run_token = started.json()["data"]["run_token"]
    assert started.json()["data"]["run_id"] > 0

    denied = api.post(
        "/api/v1/operations/runPlan.claimStep/call",
        json={"arguments": {"run_plan_id": run_plan_id, "step_id": "fetch-sitemap"}},
    )
    assert denied.status_code == 403

    claimed = api.post(
        "/api/v1/operations/runPlan.claimStep/call",
        json={
            "arguments": {
                "run_plan_id": run_plan_id,
                "step_id": "fetch-sitemap",
                "run_token": run_token,
            }
        },
    )
    assert claimed.status_code == 200
    assert claimed.json()["data"]["status"] == "running"

    completed = api.post(
        "/api/v1/operations/runPlan.recordStep/call",
        json={
            "arguments": {
                "run_plan_id": run_plan_id,
                "step_id": "fetch-sitemap",
                "status": "success",
                "result_json": {"summary": "done"},
                "run_token": run_token,
            }
        },
    )
    assert completed.status_code == 200
    assert completed.json()["data"]["status"] == "completed"


def test_operation_rest_call_enforces_mcp_grants(api: TestClient, project_id: int) -> None:
    resp = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": project_id,
                "action_ref": "utils.sitemap.fetch",
                "input_json": {"urls": ["https://example.com/sitemap.xml"]},
            }
        },
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == -32007
    assert body["data"]["tool"] == "action.execute"


def test_operation_rest_action_execute_uses_run_plan_boundary(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        repo = RunPlanRepository(session)
        created = repo.create(project_id=project_id, run_plan_json=_sitemap_action_plan_json())
        started = repo.start(created.data.id, project_id=project_id)
        claimed = repo.claim_step(
            run_plan_id=created.data.id,
            run_id=started.run_id,
            step_id="fetch-sitemap",
        )
        run_token = started.data.run_token

    httpx_mock.add_response(
        method="GET",
        url="https://example.com/sitemap.xml",
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://example.com/a</loc></url>"
            "</urlset>"
        ),
    )

    resp = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_token": run_token,
                "action_ref": "utils.sitemap.fetch",
                "input_json": {"urls": ["https://example.com/sitemap.xml"]},
            }
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["action_call"]["run_id"] == started.run_id
    assert body["data"]["action_call"]["run_plan_id"] == created.data.id
    assert body["data"]["action_call"]["run_plan_step_id"] == claimed.data.id
    assert body["data"]["output_json"]["entries"][0]["url"] == "https://example.com/a"

    audit_resp = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started.run_id,
            "run_plan_id": created.data.id,
            "run_plan_step_id": claimed.data.id,
            "status": "success",
        },
    )
    assert audit_resp.status_code == 200
    assert audit_resp.json()["total_estimate"] == 1


def test_operation_rest_mock_provider_vertical_slice(
    api: TestClient,
    project_id: int,
) -> None:
    credential_ref = _store_mock_credential(api, project_id)
    run_plan_id, run_token, run_id, step_id = _start_mock_run_plan(api, project_id)

    executed = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_token": run_token,
                "action_ref": "utils.mock.echo",
                "credential_ref": credential_ref,
                "input_json": {
                    "message": "hello from local fake provider",
                    "echo": {"campaign": "mock-campaign"},
                    "cost_cents": 7,
                },
            }
        },
    )

    assert executed.status_code == 200, executed.text
    body = executed.json()["data"]
    rendered = json.dumps(body)
    assert body["action_call"]["run_id"] == run_id
    assert body["action_call"]["run_plan_id"] == run_plan_id
    assert body["action_call"]["run_plan_step_id"] == step_id
    assert body["action_call"]["provider_key"] == "mock-provider"
    assert body["action_call"]["connector_key"] == "mock-provider"
    assert body["output_json"]["status"] == "success"
    assert body["output_json"]["credential_ref"] == credential_ref
    assert body["output_json"]["leak_check"] == {
        "authorization": "[redacted]",
        "api_key": "[redacted]",
    }
    assert body["metadata_json"]["access_token"] == "[redacted]"
    assert body["cost_cents"] == 7
    assert "mock-secret-token" not in rendered

    audit_resp = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": run_id,
            "run_plan_id": run_plan_id,
            "run_plan_step_id": step_id,
            "status": "success",
        },
    )
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    assert audit["total_estimate"] == 1
    assert "mock-secret-token" not in json.dumps(audit)

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        usage = session.exec(
            select(CredentialUsageEvent).where(
                CredentialUsageEvent.project_id == project_id,
                CredentialUsageEvent.provider_key == "mock-provider",
            )
        ).all()
    operations = {row.operation for row in usage}
    assert {"auth.credential.set", "action.utils.mock.echo"} <= operations
    assert "mock-secret-token" not in json.dumps(
        [row.metadata_json for row in usage],
        default=str,
    )


def test_operation_rest_agent_request_vertical_slice(
    api: TestClient,
    project_id: int,
) -> None:
    direct_create = api.post(
        "/api/v1/operations/agentRequest.create/call",
        json={
            "arguments": {
                "project_id": project_id,
                "request_key": "telegram:update:blocked",
                "title": "Blocked direct create",
            }
        },
    )
    assert direct_create.status_code == 403

    created_plan = api.post(
        "/api/v1/operations/runPlan.create/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_plan_json": _agent_request_ingest_plan_json(),
            }
        },
    )
    assert created_plan.status_code == 200, created_plan.text
    run_plan_id = created_plan.json()["data"]["id"]
    started_plan = api.post(
        "/api/v1/operations/runPlan.start/call",
        json={"arguments": {"project_id": project_id, "run_plan_id": run_plan_id}},
    )
    assert started_plan.status_code == 200, started_plan.text
    run_token = started_plan.json()["data"]["run_token"]
    claim_step = api.post(
        "/api/v1/operations/runPlan.claimStep/call",
        json={
            "arguments": {
                "run_plan_id": run_plan_id,
                "step_id": "ingest",
                "run_token": run_token,
            }
        },
    )
    assert claim_step.status_code == 200, claim_step.text

    created_request = api.post(
        "/api/v1/operations/agentRequest.create/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_token": run_token,
                "request_key": "telegram:update:agent-request-slice",
                "title": "Authorization: Bearer secret",
                "body_preview": "api_key=hidden",
                "source_provider": "telegram-bot",
                "source_kind": "telegram-message",
                "metadata_json": {"access_token": "hidden"},
            }
        },
    )
    assert created_request.status_code == 200, created_request.text
    request = created_request.json()["data"]
    assert request["title"] == "Authorization: Bearer [redacted]"
    assert request["body_preview"] == "api_key=[redacted]"
    assert request["metadata_json"] == {"access_token": "[redacted]"}

    listed = api.post(
        "/api/v1/operations/agentRequest.list/call",
        json={"arguments": {"project_id": project_id, "claimable": True}},
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [request["id"]]

    missing_idempotency = api.post(
        "/api/v1/operations/agentRequest.claim/call",
        json={
            "arguments": {
                "project_id": project_id,
                "request_id": request["id"],
                "claimed_by": "codex",
            }
        },
    )
    assert missing_idempotency.status_code == 422

    claim_args = {
        "project_id": project_id,
        "request_id": request["id"],
        "claimed_by": "codex",
        "idempotency_key": f"claim-agent-request-{request['id']}",
    }
    claimed = api.post(
        "/api/v1/operations/agentRequest.claim/call",
        json={"arguments": claim_args},
    )
    assert claimed.status_code == 200, claimed.text
    claim_token = claimed.json()["data"]["claim_token"]
    assert claim_token

    replayed = api.post(
        "/api/v1/operations/agentRequest.claim/call",
        json={"arguments": claim_args},
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["data"]["claim_token"] == claim_token

    linked = api.post(
        "/api/v1/operations/agentRequest.linkRunPlan/call",
        json={
            "arguments": {
                "project_id": project_id,
                "request_id": request["id"],
                "run_plan_id": run_plan_id,
                "claim_token": claim_token,
            }
        },
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["data"]["run_plan_id"] == run_plan_id

    completed = api.post(
        "/api/v1/operations/agentRequest.complete/call",
        json={
            "arguments": {
                "project_id": project_id,
                "request_id": request["id"],
                "claim_token": claim_token,
                "status": "resolved",
                "metadata_json": {"summary": "done"},
            }
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "resolved"
    assert completed.json()["data"]["metadata_json"]["summary"] == "done"


def test_operation_rest_telegram_bot_profile_setup_to_ingress_slice(
    api: TestClient,
    project_id: int,
) -> None:
    _store_telegram_credential(api, project_id)

    missing_credential = api.post(
        "/api/v1/operations/communicationBotProfile.upsert/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "missing-credential",
                "auth_profile_key": "missing",
                "identity": {
                    "display_name": "Missing Bot",
                    "purpose": "Exercise credential validation.",
                    "voice": "Concise.",
                },
                "access_policy": {
                    "dm_mode": "allowlist",
                    "group_mode": "allowlist",
                    "user_mode": "allowlist",
                    "allowed_chat_refs": ["telegram-chat:999"],
                    "allowed_user_refs": ["telegram-user:555"],
                },
            }
        },
    )
    assert missing_credential.status_code == 422, missing_credential.text

    created = api.post(
        "/api/v1/operations/communicationBotProfile.upsert/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "support-bot",
                "auth_profile_key": "support",
                "bot_username": "support_bot",
                "identity": {
                    "display_name": "Support Bot",
                    "purpose": "Handle support requests from approved Telegram users.",
                    "voice": "Concise and calm.",
                },
                "agent_guidance": {
                    "default_instructions": (
                        "Triage support requests and inspect project context before replying."
                    ),
                    "boundaries": (
                        "Do not change billing, legal, or account state without approval."
                    ),
                },
                "access_policy": {
                    "dm_mode": "allowlist",
                    "group_mode": "allowlist",
                    "user_mode": "allowlist",
                    "allowed_chat_refs": ["telegram-chat:999"],
                    "allowed_user_refs": ["telegram-user:555"],
                },
                "trigger_policy": {
                    "dm_trigger": "always",
                    "group_trigger": "mention_or_command",
                    "commands": [
                        {
                            "command": "/support",
                            "description": "Handle a support request.",
                            "guidance": (
                                "Classify the request, gather relevant context, and return "
                                "the next safe action."
                            ),
                        }
                    ],
                    "mention_patterns": ["@support_bot"],
                    "reply_to_bot_triggers": True,
                },
                "visibility_policy": {"store_non_trigger_messages": True},
                "response_policy": {
                    "reply_in_same_chat": True,
                    "origin_required": True,
                    "reply_to_source_message": True,
                    "same_thread": True,
                },
                "reply_to_message_refs": {"telegram-message:999:88": 88},
                "thread_refs": {"telegram-thread:999:default": 1},
                "direct_messages_topic_refs": {"telegram-dm-topic:999:555": 22},
                "allowed_webhook_hosts": ["127.0.0.1"],
            }
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()["data"]
    assert body["key"] == "support-bot"
    assert body["auth_profile_key"] == "support"
    assert body["bot_username"] == "support_bot"
    assert body["identity"]["display_name"] == "Support Bot"
    assert body["agent_guidance"]["boundaries"].startswith("Do not change")
    assert body["access_policy"]["allowed_user_refs"] == ["telegram-user:555"]
    assert body["reply_to_message_refs"] == {"telegram-message:999:88": 88}
    assert body["thread_refs"] == {"telegram-thread:999:default": 1}
    assert body["direct_messages_topic_refs"] == {"telegram-dm-topic:999:555": 22}
    assert "123456:ABC" not in json.dumps(created.json())

    fetched = api.post(
        "/api/v1/operations/communicationBotProfile.get/call",
        json={"arguments": {"project_id": project_id, "key": "support-bot"}},
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["key"] == "support-bot"
    assert fetched.json()["reply_to_message_refs"] == {"telegram-message:999:88": 88}

    listed = api.post(
        "/api/v1/operations/communicationBotProfile.list/call",
        json={"arguments": {"project_id": project_id}},
    )
    assert listed.status_code == 200, listed.text
    assert [item["key"] for item in listed.json()["items"]] == ["support-bot"]

    original_auth = api.headers.pop("Authorization", None)
    try:
        ingress = api.post(
            f"/api/v1/ingress/telegram/{project_id}/support-bot",
            headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
            json={
                "update_id": 789,
                "message": {
                    "message_id": 88,
                    "date": 1_779_526_000,
                    "from": {"id": 555, "username": "ada"},
                    "chat": {"id": 999, "type": "private", "username": "ada"},
                    "text": "/support check campaign",
                },
            },
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth

    assert ingress.status_code == 202, ingress.text
    assert ingress.json()["agent_request_id"] is not None

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        requests = AgentRequestRepository(session).list(project_id=project_id)
    assert requests.total_estimate == 1
    assert requests.items[0].request_key == "telegram-update:support-bot:789"
    assert requests.items[0].metadata_json["identity"]["display_name"] == "Support Bot"
    assert (
        requests.items[0]
        .metadata_json["agent_guidance"]["default_instructions"]
        .startswith("Triage support")
    )
    assert requests.items[0].metadata_json["matched_command"]["command"] == "/support"


def test_operation_rest_mock_provider_failure_records_redacted_audit(
    api: TestClient,
    project_id: int,
) -> None:
    credential_ref = _store_mock_credential(api, project_id, secret="sk-mock-failure-secret")
    run_plan_id, run_token, run_id, step_id = _start_mock_run_plan(api, project_id)

    failed = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": project_id,
                "run_token": run_token,
                "action_ref": "utils.mock.echo",
                "credential_ref": credential_ref,
                "input_json": {
                    "message": "simulate rate limit",
                    "scenario": "rate_limit",
                },
            }
        },
    )

    assert failed.status_code == 409, failed.text
    rendered_failure = json.dumps(failed.json())
    assert "sk-mock-failure-secret" not in rendered_failure
    assert failed.json()["data"]["connector"] == "mock-provider"

    audit_resp = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": run_id,
            "run_plan_id": run_plan_id,
            "run_plan_step_id": step_id,
            "status": "failed",
        },
    )
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    assert audit["total_estimate"] == 1
    row = audit["items"][0]
    assert row["status"] == "failed"
    assert row["connector_key"] == "mock-provider"
    assert "rate limited" in row["error"]
    assert "sk-mock-failure-secret" not in json.dumps(audit)


def test_operation_rest_run_token_cannot_spoof_project(
    api: TestClient,
    project_id: int,
) -> None:
    other_resp = api.post(
        "/api/v1/projects",
        json={
            "slug": "other-project",
            "name": "Other Project",
            "domain": "other.example",
            "locale": "en-US",
        },
    )
    assert other_resp.status_code == 201
    other_project_id = other_resp.json()["data"]["id"]

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        repo = RunPlanRepository(session)
        created = repo.create(project_id=project_id, run_plan_json=_sitemap_action_plan_json())
        started = repo.start(created.data.id, project_id=project_id)
        repo.claim_step(
            run_plan_id=created.data.id,
            run_id=started.run_id,
            step_id="fetch-sitemap",
        )

    resp = api.post(
        "/api/v1/operations/action.execute/call",
        json={
            "arguments": {
                "project_id": other_project_id,
                "run_token": started.data.run_token,
                "action_ref": "utils.sitemap.fetch",
                "input_json": {"urls": ["https://example.com/sitemap.xml"]},
            }
        },
    )

    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == -32007
    assert body["detail"] == "run_token is not scoped to this project"
