"""AIGNC follows the shared auth, action, audit and artifact boundaries."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall
from stackos.repositories.base import ConflictError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ArtifactRepository
from tests.integration.account_test_support import seed_test_account

BASE_URL = "https://cli-api.f2nd.com/v1"
CHAT_INPUT = {
    "model": "gemini-3.8-flash-high",
    "messages": [{"role": "user", "content": "Name a source for this answer."}],
    "output_limit": 100,
    "google_search": True,
}


async def _execute_to_terminal(session: Session, asset_dir: Path, **arguments):
    repository = ActionRepository(session, asset_dir=asset_dir)
    accepted = (await repository.execute(**arguments)).data
    assert accepted.action_call.status == "running"
    assert accepted.poll_operation == "actionCall.get"
    assert accepted.poll_arguments == {"action_call_id": accepted.action_call.id}
    workers = [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == f"stackos-action-{accepted.action_call.id}"
    ]
    assert len(workers) == 1
    await asyncio.wait_for(workers[0], timeout=5)
    session.expire_all()
    terminal = repository.get_call(
        project_id=arguments["project_id"],
        action_call_id=accepted.action_call.id,
    )
    assert terminal.status in {"success", "failed"}
    assert terminal.completed_at is not None
    return terminal


@pytest.mark.asyncio
async def test_aignc_background_reservation_rejects_changed_input_and_replays_terminal_result(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential(session, project_id)
    repository = ActionRepository(session, asset_dir=tmp_path)
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    async def delayed_response(request: httpx.Request) -> httpx.Response:
        provider_started.set()
        await asyncio.wait_for(release_provider.wait(), timeout=5)
        return httpx.Response(200, json=_completion(), request=request)

    httpx_mock.add_callback(delayed_response, method="POST", url=f"{BASE_URL}/chat/completions")
    arguments = {
        "project_id": project_id,
        "action_ref": "utils.aignc.chat.complete",
        "input_json": CHAT_INPUT,
        "credential_ref": credential_ref,
        "idempotency_key": "aignc-reservation-one",
    }
    accepted = (await repository.execute(**arguments)).data
    assert accepted.action_call.status == "running"
    worker = next(
        task
        for task in asyncio.all_tasks()
        if task.get_name() == f"stackos-action-{accepted.action_call.id}"
    )
    try:
        await asyncio.wait_for(provider_started.wait(), timeout=2)
        replay = (await repository.execute(**arguments)).data
        assert replay.replayed is True
        assert replay.action_call.id == accepted.action_call.id
        assert replay.action_call.status == "running"
        with pytest.raises(ConflictError, match="different action request"):
            await repository.execute(
                **{
                    **arguments,
                    "input_json": {**CHAT_INPUT, "output_limit": 101},
                }
            )
        assert len(httpx_mock.get_requests()) == 1
    finally:
        release_provider.set()
        await asyncio.wait_for(worker, timeout=5)
    session.expire_all()
    terminal_replay = (await repository.execute(**arguments)).data
    assert terminal_replay.replayed is True
    assert terminal_replay.action_call.id == accepted.action_call.id
    assert terminal_replay.action_call.status == "success"
    assert terminal_replay.output_json["text"] == "The answer."
    assert terminal_replay.poll_operation is None
    assert terminal_replay.poll_arguments is None
    assert terminal_replay.next_poll_after_ms is None
    assert len(httpx_mock.get_requests()) == 1
    assert (
        len(session.exec(select(ActionCall).where(ActionCall.provider_key == "aignc")).all()) == 1
    )


def _credential(session: Session, project_id: int) -> str:
    seed_test_account(
        session,
        project_id=project_id,
        provider_key="aignc",
        secret_payload=b"fixture-aignc-key",
    )
    return (
        AuthRepository(session)
        .status(project_id=project_id, provider_key="aignc")
        .accounts[0]
        .credential_ref
    )


def _completion(content: str = "The answer.") -> dict:
    return {
        "id": "completion-fixture",
        "model": "gemini-3.8-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        "cost": {"total_cost": 999, "currency": "USD"},
        "grounding_metadata": {
            "webSearchQueries": ["official source"],
            "groundingChunks": [{"web": {"uri": "https://example.com/source", "title": "Source"}}],
        },
    }


def _recording() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(8000)
        recording.writeframes(b"\x00\x00" * 80)
    return output.getvalue()


def _audio_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    *,
    uri: str = "/generated-assets/audio-inputs/recording.wav",
    status: str = "draft",
    kind: str = "audio",
    mime_type: str = "audio/wav",
) -> int:
    path = tmp_path / uri.removeprefix("/generated-assets/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_recording())
    return (
        ArtifactRepository(session)
        .create(
            project_id=project_id,
            plugin_slug="utils",
            kind=kind,
            uri=uri,
            status=status,
            mime_type=mime_type,
            size_bytes=path.stat().st_size,
        )
        .data.id
    )


def test_aignc_chat_uses_explicit_grounding_without_budget_or_pricing(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/chat/completions",
        json=_completion(),
        headers={"cf-aig-log-id": "request-fixture", "X-Model-Pricing": "ignored-price"},
    )
    result = asyncio.run(
        _execute_to_terminal(
            session,
            tmp_path,
            project_id=project_id,
            action_ref="utils.aignc.chat.complete",
            input_json=CHAT_INPUT,
            credential_ref=credential_ref,
        )
    )
    assert result.status == "success"
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == "Bearer fixture-aignc-key"
    body = json.loads(request.content)
    assert body["tools"] == [{"google_search": {}}]
    assert body["model"] == CHAT_INPUT["model"]
    assert body["messages"] == CHAT_INPUT["messages"]
    output = result.response_json
    assert output["text"] == "The answer."
    assert output["requested_model"] == "gemini-3.8-flash-high"
    assert output["returned_model"] == "gemini-3.8-flash"
    assert output["provider_request_id"] == "request-fixture"
    assert output["grounding_metadata"]["groundingChunks"][0]["web"]["uri"] == (
        "https://example.com/source"
    )
    assert "cost" not in output
    assert "fixture-aignc-key" not in json.dumps(result.model_dump(mode="json"))
    row = session.exec(select(ActionCall).where(ActionCall.provider_key == "aignc")).one()
    audit = json.dumps(row.response_json)
    assert "ignored-price" not in audit
    assert "total_cost" not in audit
    assert "fixture-aignc-key" not in audit


@pytest.mark.parametrize(
    "patch",
    [
        {"model": "unreviewed-model"},
        {"model": "claude-3-5-sonnet-20241022", "google_search": True},
        {"model": "gemini-3.1-flash-image"},
        {"tools": [{"type": "function", "function": {"name": "run"}}]},
        {"stream": True},
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "report",
                    "schema": {"type": "object", "properties": {"sources": {"type": "array"}}},
                },
            }
        },
        {"base_url": "https://other.example/v1"},
        {"output_limit": 0},
        {"output_limit": 8193},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "tool", "content": "result"}]},
    ],
)
def test_aignc_chat_rejects_unsupported_contract_before_http(
    session: Session,
    project_id: int,
    patch: dict,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential(session, project_id)
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.aignc.chat.complete",
        input_json={**CHAT_INPUT, **patch},
        credential_ref=credential_ref,
    )
    assert not validation.valid
    assert not httpx_mock.get_requests()


def test_aignc_requires_daemon_credential(session: Session, project_id: int) -> None:
    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.aignc.chat.complete",
        input_json=CHAT_INPUT,
    )
    assert not validation.valid
    assert any(
        "credential" in issue.code or "credential" in issue.message for issue in validation.issues
    )


def test_aignc_audio_consumes_existing_managed_artifact_without_a_new_uploader(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential(session, project_id)
    artifact_id = _audio_artifact(
        session,
        project_id,
        tmp_path,
        kind="communication-media",
        uri="/generated-assets/communication-media/telegram/fixture/recording.wav",
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/chat/completions",
        json=_completion("Transcript and summary."),
    )
    result = asyncio.run(
        _execute_to_terminal(
            session,
            tmp_path,
            project_id=project_id,
            action_ref="utils.aignc.audio.analyze",
            input_json={
                "audio_artifact_id": artifact_id,
                "model": "gemini-3.8-flash",
                "instruction": "Transcribe and summarize.",
                "output_limit": 100,
            },
            credential_ref=credential_ref,
        )
    )
    assert result.status == "success"
    request = httpx_mock.get_request()
    assert request is not None
    content = json.loads(request.content)["messages"][0]["content"]
    audio = next(part["input_audio"] for part in content if part["type"] == "input_audio")
    assert audio["format"] == "wav"
    assert base64.b64decode(audio["data"]) == _recording()
    assert result.response_json["text"] == "Transcript and summary."
    assert result.response_json["audio_artifact_id"] == artifact_id
    row = session.exec(select(ActionCall).where(ActionCall.provider_key == "aignc")).one()
    assert audio["data"] not in json.dumps(row.request_json)
    assert audio["data"] not in json.dumps(row.response_json)


@pytest.mark.parametrize("case", ["other-project", "archived", "private", "symlink", "mime"])
def test_aignc_audio_rejects_ineligible_artifacts_before_http(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    case: str,
) -> None:
    credential_ref = _credential(session, project_id)
    owner = project_id
    if case == "other-project":
        owner = (
            ProjectRepository(session)
            .create(
                name="Other",
                slug="aignc-other",
                domain="other.example",
            )
            .data.id
        )
    uri = (
        "/generated-assets/IMAP-Transfers/recording.wav"
        if case == "private"
        else ("/generated-assets/audio-inputs/recording.wav")
    )
    artifact_id = _audio_artifact(
        session,
        owner,
        tmp_path,
        uri=uri,
        status="archived" if case == "archived" else "draft",
        mime_type="image/png" if case == "mime" else "audio/wav",
    )
    if case == "symlink":
        path = tmp_path / uri.removeprefix("/generated-assets/")
        private = tmp_path / "imap-transfers" / "recording.wav"
        private.parent.mkdir()
        private.write_bytes(_recording())
        path.unlink()
        path.symlink_to(private)
    validation = ActionRepository(session, asset_dir=tmp_path).validate(
        project_id=project_id,
        action_ref="utils.aignc.audio.analyze",
        input_json={
            "audio_artifact_id": artifact_id,
            "model": "gemini-3.8-flash",
            "instruction": "Transcribe.",
            "output_limit": 100,
        },
        credential_ref=credential_ref,
    )
    assert not validation.valid
    assert not httpx_mock.get_requests()


def test_aignc_provider_failure_is_structured_in_failed_action_audit(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/chat/completions",
        status_code=429,
        headers={"Retry-After": "3", "cf-aig-log-id": "limited-request"},
        json={"error": {"code": "rate_limit", "message": "Retry later"}},
    )
    result = asyncio.run(
        _execute_to_terminal(
            session,
            tmp_path,
            project_id=project_id,
            action_ref="utils.aignc.chat.complete",
            input_json=CHAT_INPUT,
            credential_ref=credential_ref,
        )
    )
    assert result.status == "failed"
    assert result.response_json["provider_status_code"] == 429
    row = session.exec(select(ActionCall).where(ActionCall.provider_key == "aignc")).one()
    assert row.status == "failed"
    assert "429" in json.dumps(row.response_json)
    assert len(httpx_mock.get_requests()) == 1


def test_aignc_audio_rejects_raw_paths_and_bytes(session: Session, project_id: int) -> None:
    credential_ref = _credential(session, project_id)
    result = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.aignc.audio.analyze",
        input_json={
            "audio_path": "/tmp/recording.wav",
            "data": "raw-audio",
            "model": "gemini-3.8-flash",
            "instruction": "Transcribe.",
            "output_limit": 10,
        },
        credential_ref=credential_ref,
    )
    assert not result.valid
