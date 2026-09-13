"""AIGNC generation waits are explicit and never become provider payload fields."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest
from pytest_httpx import HTTPXMock

from stackos.actions.aignc import AigncActionConnector
from stackos.actions.connectors import ActionConnectorRequest
from stackos.auth_providers import ResolvedCredential
from stackos.repositories.base import ValidationError

API = "https://cli-api.f2nd.com/v1"
INPUTS = {
    "chat.complete": {
        "model": "gemini-3.8-flash",
        "messages": [{"role": "user", "content": "Research this question."}],
        "output_limit": 4096,
        "google_search": True,
    },
    "image.generate": {"prompt": "An apple"},
    "audio.analyze": {
        "audio_artifact_id": 1,
        "model": "gemini-3.8-flash",
        "instruction": "Transcribe this recording.",
        "output_limit": 1000,
    },
}


def _request(
    operation: str, payload: dict, tmp_path: Path, progress: list
) -> ActionConnectorRequest:
    credential = Mock(spec=ResolvedCredential)
    credential.secret_payload = b"fixture-aignc-key"
    return ActionConnectorRequest(
        project_id=1,
        plugin_slug="utils",
        action_key=f"aignc.{operation}",
        action_ref=f"utils.aignc.{operation}",
        provider_key="aignc",
        operation=operation,
        input_json=payload,
        config_json={},
        credential=credential,
        asset_dir=tmp_path,
        progress_callback=progress.append,
    )


def _audio_path(tmp_path: Path) -> Path:
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(8000)
        recording.writeframes(b"\x00\x00" * 80)
    path = tmp_path / "synthetic.wav"
    path.write_bytes(output.getvalue())
    return path


@pytest.mark.parametrize("operation", INPUTS)
@pytest.mark.parametrize("read_timeout", [None, 60, 1800])
def test_generation_httpx_timeouts_and_phase_progress(
    operation: str,
    read_timeout: int | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
) -> None:
    payload = dict(INPUTS[operation])
    if read_timeout is not None:
        payload["read_timeout_seconds"] = read_timeout
    monkeypatch.setattr(
        "stackos.actions.aignc._audio_artifact", lambda request: (_audio_path(tmp_path), "wav")
    )
    monkeypatch.setattr(
        "stackos.actions.aignc.register_generated_media_artifacts",
        lambda request, output, **kwargs: output,
    )
    content = (
        base64.b64encode(b"\xff\xd8\xff\xe0fixture\xff\xd9").decode()
        if operation == "image.generate"
        else "A researched answer."
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/chat/completions",
        json={
            "model": "gemini-3.1-flash-image"
            if operation == "image.generate"
            else payload["model"],
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        },
    )
    progress: list = []
    asyncio.run(AigncActionConnector().execute(_request(operation, payload, tmp_path, progress)))
    sent = httpx_mock.get_requests()
    assert len(sent) == 1
    assert sent[0].extensions["timeout"] == {
        "connect": 15,
        "read": 600 if read_timeout is None else read_timeout,
        "write": 180,
        "pool": 15,
    }
    body = json.loads(sent[0].content)
    assert "read_timeout_seconds" not in body
    assert body["stream"] is False
    assert progress == [
        {"phase": "requesting", "operation": operation},
        *(
            [{"phase": "persisting", "operation": operation}]
            if operation == "image.generate"
            else []
        ),
    ]


@pytest.mark.parametrize("operation", INPUTS)
@pytest.mark.parametrize("value", [True, False, None, "600", 59, 1801, 60.5])
def test_invalid_generation_timeout_rejects_before_provider_call(
    operation: str,
    value: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
) -> None:
    monkeypatch.setattr(
        "stackos.actions.aignc._audio_artifact", lambda request: (_audio_path(tmp_path), "wav")
    )
    progress: list = []
    request = _request(
        operation, {**INPUTS[operation], "read_timeout_seconds": value}, tmp_path, progress
    )
    connector = AigncActionConnector()
    issues = connector.validate(request)
    assert any(
        issue.path == "$.read_timeout_seconds" and issue.code == "out_of_range" for issue in issues
    )
    with pytest.raises(ValidationError, match=r"60.*1800"):
        asyncio.run(connector.execute(request))
    assert httpx_mock.get_requests() == []
    assert progress == []


def test_models_list_retains_existing_timeout_without_generation_progress(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        method="GET", url=f"{API}/models", json={"data": [{"id": "gemini-3.8-flash"}]}
    )
    progress: list = []
    asyncio.run(AigncActionConnector().execute(_request("models.list", {}, tmp_path, progress)))
    request = httpx_mock.get_request()
    assert request is not None
    assert request.extensions["timeout"] == {"connect": 180, "read": 180, "write": 180, "pool": 180}
    assert progress == []
