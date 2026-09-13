"""AIGNC uses the existing Connections, MCP actions, grants and artifact flows."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import wave
from pathlib import Path
from threading import Event
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.config import Settings

from .conftest import MCPClient

_API_KEY = "fixture-aignc-mcp-private-key"
_BASE_URL = "https://cli-api.f2nd.com/v1"
_GROUNDING_REDIRECT_URI = (
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/fixture-source"
)
_OPERATIONS = ("models.list", "chat.complete", "image.generate", "audio.analyze")
_JPEG_BYTES = b"\xff\xd8\xff\xe0fixture-jpeg\xff\xd9"


def _poll_terminal(mcp: MCPClient, project_id: int, accepted: dict[str, Any]) -> dict[str, Any]:
    assert accepted["status"] == "running"
    assert accepted["poll_operation"] == "actionCall.get"
    assert accepted["poll_arguments"] == {"action_call_id": accepted["action_call_id"]}
    assert accepted["next_poll_after_ms"] > 0
    deadline = time.monotonic() + 5
    waiter = Event()
    while True:
        polled = mcp.call_tool_structured(
            accepted["poll_operation"],
            {
                "project_id": project_id,
                **accepted["poll_arguments"],
                "response_mode": "raw",
            },
        )
        if polled["status"] != "running":
            assert polled["poll_operation"] is None
            assert polled["completed_at"] is not None
            return polled
        remaining = deadline - time.monotonic()
        assert remaining > 0, polled
        waiter.wait(min(polled["next_poll_after_ms"] / 1000, remaining))


def _connect(mcp: MCPClient, project_id: int) -> str:
    response = mcp.test_client.post(
        "/api/v1/auth/accounts/aignc",
        json={
            "auth_method_key": "api_key",
            "display_name": "AIGNC fixture",
            "attach_project_id": project_id,
            "fields": {"api_key": _API_KEY},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    connections = mcp.call_tool_structured(
        "connection.list",
        {"project_id": project_id, "provider_key": "aignc", "response_mode": "raw"},
    )
    assert _API_KEY not in json.dumps(connections)
    return connections["accounts"][0]["credential_ref"]


def _claim(
    mcp: MCPClient, project_id: int, operation: str, *, grant_operation: str | None = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    action_ref = f"utils.aignc.{operation}"
    grants = [
        {
            "step_id": "execute",
            "tool": "action.execute",
            "action_refs": [f"utils.aignc.{grant_operation or operation}"],
        }
    ]
    if operation == "audio.analyze":
        grants.append({"step_id": "execute", "tool": "artifact.create", "plugin_slug": "utils"})
    created = mcp.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "run_plan_json": {
                "schema_version": "stackos.run-plan.v1",
                "key": f"aignc.{operation}.proof",
                "title": f"AIGNC {operation} proof",
                "grants": {"mcp_tool_grants": grants},
                "steps": [
                    {
                        "id": "execute",
                        "title": "Execute the requested action",
                        "action_refs": [action_ref],
                    }
                ],
            },
        },
    )["data"]
    started = mcp.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["id"]},
    )["data"]
    claimed = mcp.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": created["id"], "step_id": "execute", "run_token": started["run_token"]},
    )["data"]
    return created, started, claimed


def _recording() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(8000)
        recording.writeframes(b"\x01\x02" * 80)
    return output.getvalue()


def _stage_audio(mcp: MCPClient, settings: Settings, project_id: int, run_token: str) -> int:
    # Host file staging and artifact.create already own this path; no new uploader.
    recording = _recording()
    path = settings.generated_assets_dir / "audio-inputs" / "recording.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(recording)
    artifact = mcp.call_tool_structured(
        "artifact.create",
        {
            "project_id": project_id,
            "plugin_slug": "utils",
            "kind": "audio",
            "uri": "/generated-assets/audio-inputs/recording.wav",
            "mime_type": "audio/wav",
            "size_bytes": len(recording),
            "run_token": run_token,
            "response_mode": "raw",
        },
    )
    return artifact["data"]["id"]


def _completion(operation: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": "Explicit result."}
    if operation == "image.generate":
        # Verified supplier response: the JPEG is separate from nullable text.
        message.update(
            content=None,
            reasoning_content=None,
            tool_calls=None,
            images=[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + base64.b64encode(_JPEG_BYTES).decode("ascii")
                    },
                    "index": 0,
                }
            ],
        )
    return {
        "id": "completion-fixture",
        "model": "gemini-3.1-flash-image" if operation == "image.generate" else "gemini-3.8-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": message,
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 3,
            "total_tokens": 18,
            "google_searches": 1,
        },
        "cost": {"total_cost": 123.45, "currency": "USD"},
        "grounding_metadata": {
            "webSearchQueries": ["official source"],
            "groundingChunks": [
                None,
                {"web": {"uri": _GROUNDING_REDIRECT_URI, "title": "Grounded source"}},
                {"web": {"uri": "https://example.com/source", "title": "Source"}},
            ],
            "groundingSupports": [
                {
                    "segment": {"text": "Explicit result.", "startIndex": 0, "endIndex": 16},
                    "groundingChunkIndices": [1, 2],
                }
            ],
        },
    }


def _audit(
    mcp: MCPClient, project_id: int, operation: str, status: str = "success"
) -> dict[str, Any]:
    response = mcp.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"plugin_slug": "utils", "action_key": f"aignc.{operation}", "status": status},
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return response.json()


@pytest.mark.parametrize("execution", ("direct", "granted"))
def test_aignc_delayed_generation_returns_before_provider_and_replays_one_call(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    execution: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _connect(mcp_client, project_id)
    provider_started = Event()
    release_provider = Event()

    async def delayed_response(request: httpx.Request) -> httpx.Response:
        provider_started.set()
        assert await asyncio.to_thread(release_provider.wait, 5), (
            "fixture provider was not released"
        )
        return httpx.Response(200, json=_completion("chat.complete"), request=request)

    httpx_mock.add_callback(delayed_response, method="POST", url=f"{_BASE_URL}/chat/completions")
    arguments = {
        "project_id": project_id,
        "action_ref": "utils.aignc.chat.complete",
        "credential_ref": credential_ref,
        "confirm_direct": True,
        "intent_summary": "Generate the explicit delayed fixture result.",
        "idempotency_key": "aignc-delayed-one",
        "input_json": {
            "model": "gemini-3.8-flash",
            "messages": [{"role": "user", "content": "Hello"}],
            "output_limit": 100,
        },
    }
    tool = "action.run"
    created = started = None
    if execution == "granted":
        created, started, _ = _claim(mcp_client, project_id, "chat.complete")
        arguments.pop("confirm_direct")
        arguments.pop("intent_summary")
        arguments["run_token"] = started["run_token"]
        tool = "action.execute"
    try:
        accepted = mcp_client.call_tool_structured(tool, arguments)["data"]
        assert accepted.get("status") == "running", accepted
        assert provider_started.wait(2)
        running = mcp_client.call_tool_structured(
            "actionCall.get",
            {
                "project_id": project_id,
                **accepted["poll_arguments"],
                "response_mode": "raw",
            },
        )
        assert running["status"] == "running"
        assert running["completed_at"] is None
        replay = mcp_client.call_tool_structured(tool, arguments)
        assert replay["data"]["status"] == "running"
        assert replay["data"]["action_call_id"] == accepted["action_call_id"]
        assert len(httpx_mock.get_requests()) == 1
        other_project = mcp_client.call_tool_structured(
            "project.create",
            {
                "slug": "other-poll-project",
                "name": "Other Poll Project",
                "domain": "other.example",
                "locale": "en-US",
            },
        )["data"]["id"]
        denied = mcp_client.call_tool_error(
            "actionCall.get",
            {
                "project_id": other_project,
                **accepted["poll_arguments"],
            },
        )
        assert denied["message"] == "NotFoundError"
        if execution == "granted":
            assert created is not None and started is not None
            blocked = mcp_client.call_tool_error(
                "runPlan.recordStep",
                {
                    "project_id": project_id,
                    "run_plan_id": created["id"],
                    "step_id": "execute",
                    "status": "success",
                    "run_token": started["run_token"],
                },
            )
            assert blocked["message"] == "ValidationError", blocked
            assert blocked["data"]["action_call_ids"] == [accepted["action_call_id"]]
            assert "actionCall.get" in json.dumps(blocked["data"])
    finally:
        release_provider.set()
    terminal = _poll_terminal(mcp_client, project_id, accepted)
    assert terminal["status"] == "success"
    replay = mcp_client.call_tool_structured(tool, {**arguments, "response_mode": "raw"})
    assert replay["data"]["action_call"]["id"] == accepted["action_call_id"]
    # Submission replay may retain its original accepted receipt; polling is
    # the canonical terminal authority and must keep the original result.
    replay_terminal = _poll_terminal(mcp_client, project_id, accepted)
    assert replay_terminal["output_json"] == terminal["output_json"]
    assert len(httpx_mock.get_requests()) == 1
    audit = _audit(mcp_client, project_id, "chat.complete")
    assert audit["total_estimate"] == 1
    assert audit["items"][0]["id"] == accepted["action_call_id"]
    assert _API_KEY not in json.dumps(
        {
            "accepted": accepted,
            "running": running,
            "terminal": terminal,
            "replay": replay,
            "audit": audit,
        }
    )
    if execution == "granted":
        assert created is not None and started is not None
        completed = mcp_client.call_tool_structured(
            "runPlan.recordStep",
            {
                "project_id": project_id,
                "run_plan_id": created["id"],
                "step_id": "execute",
                "status": "success",
                "run_token": started["run_token"],
            },
        )
        assert completed["data"]["status"] == "completed", completed


def test_aignc_discovery_uses_existing_connection_visibility(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    args = {"project_id": project_id, "plugin_slug": "utils", "provider_key": "aignc"}
    hidden = mcp_client.call_tool_structured("action.list", args)
    unavailable = mcp_client.call_tool_structured(
        "action.list",
        {**args, "include_unavailable_integrations": True},
    )
    assert hidden["items"] == []
    assert hidden["hidden_count"] == 4
    assert {item["action_ref"] for item in unavailable["items"]} == {
        f"utils.aignc.{operation}" for operation in _OPERATIONS
    }
    assert all(item["availability_status"] == "missing_credential" for item in unavailable["items"])
    _connect(mcp_client, project_id)
    ready = mcp_client.call_tool_structured("action.list", args)
    assert len(ready["items"]) == 4
    assert all(item["availability_status"] == "ready" for item in ready["items"])
    assert all(item["exposure"]["state"] == "external_connected" for item in ready["items"])
    tools = {tool["name"] for tool in mcp_client.list_tools()}
    assert {"action.run", "action.execute"} <= tools
    assert not any("aignc" in name for name in tools)


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("execution", ("direct", "granted"))
def test_aignc_actions_preserve_shared_output_idempotency_artifact_and_audit_contracts(
    mcp_client: MCPClient,
    seeded_project: dict,
    mcp_settings: Settings,
    httpx_mock: HTTPXMock,
    operation: str,
    execution: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _connect(mcp_client, project_id)
    created = started = claimed = None
    if execution == "granted" or operation == "audio.analyze":
        created, started, claimed = _claim(mcp_client, project_id, operation)
    if operation == "models.list":
        input_json = {}
        httpx_mock.add_response(
            method="GET",
            url=f"{_BASE_URL}/models",
            json={"data": [{"id": "gemini-3.8-flash", "pricing": {"input": "999"}}]},
        )
    else:
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/chat/completions",
            json=_completion(operation),
            headers={
                "cf-aig-log-id": "aignc-request-fixture",
                "X-Model-Pricing": "ignored-pricing",
            },
        )
        if operation == "chat.complete":
            input_json = {
                "model": "gemini-3.8-flash-high",
                "messages": [{"role": "user", "content": "Cite a source."}],
                "output_limit": 100,
                "google_search": True,
            }
        elif operation == "image.generate":
            input_json = {"prompt": "A red apple.", "output_limit": 2048}
        else:
            assert started is not None
            artifact_id = _stage_audio(mcp_client, mcp_settings, project_id, started["run_token"])
            input_json = {
                "audio_artifact_id": artifact_id,
                "model": "gemini-3.8-flash",
                "instruction": "Transcribe the recording.",
                "output_limit": 100,
            }
            if execution == "direct":
                assert created is not None
                mcp_client.call_tool_structured(
                    "runPlan.recordStep",
                    {
                        "run_plan_id": created["id"],
                        "step_id": "execute",
                        "status": "success",
                        "run_token": started["run_token"],
                    },
                )
    args = {
        "project_id": project_id,
        "action_ref": f"utils.aignc.{operation}",
        "credential_ref": credential_ref,
        "input_json": input_json,
        "idempotency_key": f"aignc-{execution}-{operation}-one",
    }
    if execution == "granted":
        assert started is not None
        args["run_token"] = started["run_token"]
    elif operation != "models.list":
        args["confirm_direct"] = True
        args["intent_summary"] = "Execute the operator's explicit fixture request."
    tool = "action.execute" if execution == "granted" else "action.run"
    executed = mcp_client.call_tool_structured(tool, args)
    data = executed["data"]
    assert data["action_ref"] == args["action_ref"]
    terminal = None
    if operation == "models.list":
        assert data.get("status") == "success", executed
        assert data.get("poll_operation") is None
        pointer = data["output"]
        assert pointer["output_mode"] == "file"
    else:
        terminal = _poll_terminal(mcp_client, project_id, data)
        assert terminal["status"] == "success", terminal
        assert terminal["output_json"]["output_mode"] == "file"
        pointer = terminal["output_json"]["file"]
    assert pointer["schema_ref"] == "stackos.action-output.v1"
    assert pointer["schema_operation"] == "schema.get"
    saved = json.loads(Path(pointer["path"]).read_text(encoding="utf-8"))
    output = saved["response"]["output_json"]
    assert saved["action_call"]["id"] == data["action_call_id"]

    # A compact first response must preserve canonical data for a later raw replay.
    replay = mcp_client.call_tool_structured(tool, {**args, "response_mode": "raw"})
    assert replay["idempotency_replay"] is True
    assert replay["data"]["action_call"]["id"] == data["action_call_id"]
    if operation == "models.list":
        replay_output = replay["data"]["output_json"]
        assert replay["data"]["action_call"]["status"] == "success"
    else:
        replay_terminal = _poll_terminal(mcp_client, project_id, data)
        assert replay_terminal == terminal
        replay_output = replay_terminal["output_json"]
    assert replay_output["file"]["path"] == pointer["path"]
    assert len(httpx_mock.get_requests()) == 1
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == f"Bearer {_API_KEY}"
    assert str(request.url).startswith(_BASE_URL)
    audit = _audit(mcp_client, project_id, operation)
    assert audit["total_estimate"] == 1
    row = audit["items"][0]
    assert row["id"] == data["action_call_id"]
    for field, expected in (
        ("run_id", started["run_id"] if execution == "granted" and started else None),
        ("run_plan_id", created["id"] if execution == "granted" and created else None),
        ("run_plan_step_id", claimed["id"] if execution == "granted" and claimed else None),
    ):
        assert row[field] == expected
        if execution == "granted":
            assert saved["run"][field] == expected
    assert row["response_json"]["output_mode"] == "file"
    if operation == "models.list":
        assert output["data"] == [{"id": "gemini-3.8-flash"}]
    else:
        body = json.loads(request.content)
        assert body["max_tokens"] == input_json["output_limit"]
        assert "output_limit" not in body
        assert output["provider_request_id"] == "aignc-request-fixture"
        assert output["usage"] == {
            "prompt_count": 15,
            "completion_count": 3,
            "total_count": 18,
            "google_searches": 1,
        }
        if operation == "chat.complete":
            assert body["tools"] == [{"google_search": {}}]
            assert output["requested_model"] == "gemini-3.8-flash-high"
            assert output["returned_model"] == "gemini-3.8-flash"
            assert output["text"] == "Explicit result."
            expected_grounding = {
                "webSearchQueries": ["official source"],
                "groundingChunks": [
                    {},
                    {"web": {"uri": _GROUNDING_REDIRECT_URI, "title": "Grounded source"}},
                    {"web": {"uri": "https://example.com/source", "title": "Source"}},
                ],
                "groundingSupports": [
                    {
                        "segment": {"text": "Explicit result.", "startIndex": 0, "endIndex": 16},
                        "groundingChunkIndices": [1, 2],
                    }
                ],
            }
            assert output["grounding_metadata"] == expected_grounding
            replay_saved = json.loads(
                Path(replay_output["file"]["path"]).read_text(encoding="utf-8")
            )
            assert (
                replay_saved["response"]["output_json"]["grounding_metadata"] == expected_grounding
            )
            # Source URLs remain provider evidence; reading or replaying never follows redirects.
            assert len(httpx_mock.get_requests()) == 1
        elif operation == "image.generate":
            item = output["data"][0]
            assert isinstance(item["artifact_id"], int)
            assert item["artifact_ref"] == item["url"]
            assert output["artifact_refs"] == [item["url"]]
            image_path = mcp_settings.generated_assets_dir / item["url"].removeprefix(
                "/generated-assets/"
            )
            assert image_path.read_bytes() == _JPEG_BYTES
            artifact = mcp_client.call_tool_structured(
                "artifact.get",
                {
                    "project_id": project_id,
                    "artifact_id": item["artifact_id"],
                    "response_mode": "raw",
                },
            )
            assert artifact["kind"] == "image"
            assert artifact["uri"] == item["url"]
        else:
            content = body["messages"][0]["content"]
            audio = next(part["input_audio"] for part in content if part["type"] == "input_audio")
            assert audio["format"] == "wav"
            assert base64.b64decode(audio["data"]) == _recording()
            assert output["audio_artifact_id"] == input_json["audio_artifact_id"]
            assert output["text"] == "Explicit result."
    rendered = json.dumps(
        {
            "executed": executed,
            "replay": replay,
            "saved": saved,
            "audit": audit,
            "terminal": terminal,
            "created": created,
            "started": started,
            "claimed": claimed,
        }
    )
    for excluded in (
        _API_KEY,
        "total_cost",
        "ignored-pricing",
        '"pricing"',
        base64.b64encode(_recording()).decode("ascii"),
        base64.b64encode(_JPEG_BYTES).decode("ascii"),
    ):
        assert excluded not in rendered


@pytest.mark.parametrize("mismatch", ("action", "project"))
def test_aignc_grants_reject_mismatch_before_provider_dispatch(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    mismatch: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _connect(mcp_client, project_id)
    _, started, _ = _claim(mcp_client, project_id, "models.list")
    if mismatch == "project":
        project_id = mcp_client.call_tool_structured(
            "project.create",
            {
                "slug": "other-aignc-project",
                "name": "Other AIGNC Project",
                "domain": "other.example",
                "locale": "en-US",
            },
        )["data"]["id"]
    error = mcp_client.call_tool_error(
        "action.execute",
        {
            "project_id": project_id,
            "action_ref": "utils.aignc.chat.complete"
            if mismatch == "action"
            else "utils.aignc.models.list",
            "credential_ref": credential_ref,
            "input_json": {
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Hello"}],
                "output_limit": 10,
            }
            if mismatch == "action"
            else {},
            "run_token": started["run_token"],
        },
    )
    assert error["code"] == -32007
    assert error["message"] == "ToolNotGrantedError"
    assert not httpx_mock.get_requests()
    assert _API_KEY not in json.dumps(error)


@pytest.mark.parametrize("failure", ("rate_limit", "read_timeout"))
def test_aignc_background_failure_retains_safe_poll_diagnostics_and_failed_audit(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
    failure: str,
) -> None:
    project_id = seeded_project["data"]["id"]
    credential_ref = _connect(mcp_client, project_id)
    if failure == "rate_limit":
        httpx_mock.add_response(
            method="POST",
            url=f"{_BASE_URL}/chat/completions",
            status_code=429,
            headers={"Retry-After": "3", "cf-aig-log-id": "rate-limited-request"},
            json={"error": {"code": "rate_limit", "message": f"Denied {_API_KEY}"}},
        )
    else:
        httpx_mock.add_exception(
            httpx.ReadTimeout(f"fixture timeout {_API_KEY}"),
            method="POST",
            url=f"{_BASE_URL}/chat/completions",
        )
    accepted = mcp_client.call_tool_structured(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.aignc.chat.complete",
            "credential_ref": credential_ref,
            "confirm_direct": True,
            "intent_summary": "Execute the operator's explicit fixture request.",
            "input_json": {
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Hello"}],
                "output_limit": 10,
            },
        },
    )["data"]
    terminal = _poll_terminal(mcp_client, project_id, accepted)
    assert terminal["status"] == "failed"
    assert terminal["output_json"]["automatic_retry_count"] == 0
    assert terminal["retry_safe"] is False
    if failure == "rate_limit":
        assert terminal["output_json"]["provider_status_code"] == 429
        assert terminal["output_json"]["provider_error"]["code"] == "rate_limit"
    else:
        assert terminal["outcome_unknown"] is True
    audit = _audit(mcp_client, project_id, "chat.complete", "failed")
    assert audit["total_estimate"] == 1
    row = audit["items"][0]
    assert row["id"] == accepted["action_call_id"]
    assert row["response_json"] == terminal["output_json"]
    if failure == "rate_limit":
        assert "rate-limited-request" in json.dumps(row["response_json"])
    assert _API_KEY not in json.dumps({"accepted": accepted, "terminal": terminal, "audit": audit})
    assert len(httpx_mock.get_requests()) == 1


def test_aignc_missing_connection_rejects_before_background_acceptance(
    mcp_client: MCPClient,
    seeded_project: dict,
    httpx_mock: HTTPXMock,
) -> None:
    project_id = seeded_project["data"]["id"]
    error = mcp_client.call_tool_error(
        "action.run",
        {
            "project_id": project_id,
            "action_ref": "utils.aignc.chat.complete",
            "confirm_direct": True,
            "intent_summary": "Generate the requested fixture answer.",
            "input_json": {
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Hello"}],
                "output_limit": 100,
            },
        },
    )
    assert "credential" in json.dumps(error["data"]).lower()
    assert error["message"] == "ValidationError"
    assert not httpx_mock.get_requests()
    for status in ("running", "success", "failed"):
        assert _audit(mcp_client, project_id, "chat.complete", status)["total_estimate"] == 0
