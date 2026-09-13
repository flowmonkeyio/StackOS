"""AIGNC supplied-contract wire, normalization, and recovery fixtures."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.aignc import AigncIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError, ValidationError

API = "https://cli-api.f2nd.com/v1"
KEY = "fixture-aignc-credential"
MESSAGES = [{"role": "user", "content": "Capital of France?"}]
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00fixture-jpeg\xff\xd9"
JPEG_BASE64 = base64.b64encode(JPEG_BYTES).decode()
JPEG_DATA_URI = f"data:image/jpeg;base64,{JPEG_BASE64}"


def completion(content: Any = "Paris.") -> dict[str, Any]:
    return {
        "id": "response-123",
        "model": "gemini-3.8-flash",
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        "usage": {
            "prompt_tokens": 15,
            "completion_tokens": 3,
            "total_tokens": 18,
            "google_searches": 0,
            "completion_tokens_details": {"reasoning_tokens": 2},
            "prompt_tokens_details": {"cached_tokens": 4},
            "unexpected_blob": "ignored",
        },
        "cost": {"total_cost": 123},
        "arbitrary": "must not escape",
    }


def image_completion(encoded: str, *, inline: bool) -> dict[str, Any]:
    raw = completion(None if inline else encoded)
    raw["model"] = "gemini-3.1-flash-image"
    if inline:
        raw["choices"][0]["message"].update(
            {
                "reasoning_content": None,
                "tool_calls": None,
                "images": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        "index": 0,
                    }
                ],
            }
        )
    return raw


def run_call(
    project_id: int,
    method: str = "chat_complete",
    *,
    kwargs: dict[str, Any] | None = None,
    **options: Any,
) -> Any:
    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integration = AigncIntegration(
                payload=KEY.encode(), project_id=project_id, http=client, **options
            )
            arguments = (
                kwargs
                if kwargs is not None
                else {
                    "model": "gemini-3.8-flash",
                    "messages": MESSAGES,
                    "max_tokens": 100,
                }
            )
            return await getattr(integration, method)(**arguments)

    return asyncio.run(go())


def test_chat_wire_grounding_usage_and_audit_exclude_prices(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    raw = completion()
    raw["grounding_metadata"] = {
        "webSearchQueries": ["France capital"],
        "groundingChunks": [{"web": {"uri": "https://example.org/paris", "title": "Paris"}}],
        "cost": "discard",
        "extra_base64": "discard",
    }
    audit = Mock()
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/chat/completions",
        json=raw,
        headers={"cf-aig-log-id": "log-123", "X-Model-Pricing": "ignore"},
    )
    result = run_call(
        project_id,
        kwargs={
            "model": "gemini-3.8-flash-high",
            "messages": MESSAGES,
            "max_tokens": 100,
            "google_search": True,
        },
        run_step_call_repo=audit,
        run_step_id=1,
    )
    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "model": "gemini-3.8-flash-high",
        "messages": MESSAGES,
        "max_tokens": 100,
        "stream": False,
        "tools": [{"google_search": {}}],
    }
    assert result.data["requested_model"] == "gemini-3.8-flash-high"
    assert result.data["returned_model"] == "gemini-3.8-flash"
    assert result.data["text"] == "Paris."
    assert result.data["provider_request_id"] == "log-123"
    assert result.data["usage"] == {
        "prompt_count": 15,
        "completion_count": 3,
        "total_count": 18,
        "reasoning_count": 2,
        "cached_count": 4,
        "google_searches": 0,
    }
    assert result.data["grounding_metadata"]["webSearchQueries"] == ["France capital"]
    public = (
        json.dumps(result.data) + repr(result.metadata) + repr(audit.record_call.call_args_list)
    )
    for forbidden in ("total_cost", "X-Model-Pricing", "unexpected_blob", "arbitrary", KEY):
        assert forbidden not in public
    assert result.cost_usd == 0
    assert audit.record_call.call_count == 1


def test_grounding_supports_preserve_original_source_indices_and_redirect(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    redirect_uri = (
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
        "AUZIYQfixtureEncodedSourceLocator_1234567890"
    )
    raw = completion("Paris is the capital of France.")
    raw["grounding_metadata"] = {
        "webSearchQueries": ["France capital"],
        "groundingChunks": [
            None,
            {"web": {"uri": redirect_uri, "title": "France"}},
            {"unknown": "omit"},
        ],
        "groundingSupports": [
            {
                "segment": {
                    "startIndex": 0,
                    "endIndex": 30,
                    "text": "Paris is the capital of France.",
                    "extra": "omit",
                },
                "groundingChunkIndices": [1],
                "cost": "omit",
            }
        ],
    }
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    result = run_call(
        project_id,
        kwargs={
            "model": "gemini-3.8-flash",
            "messages": MESSAGES,
            "max_tokens": 100,
            "google_search": True,
        },
    )
    grounding = result.data["grounding_metadata"]
    assert grounding["groundingChunks"] == [
        {},
        {"web": {"uri": redirect_uri, "title": "France"}},
        {},
    ]
    assert grounding["groundingSupports"] == [
        {
            "segment": {"startIndex": 0, "endIndex": 30, "text": "Paris is the capital of France."},
            "groundingChunkIndices": [1],
        }
    ]
    assert result.data["usage"]["google_searches"] == 0
    assert len(httpx_mock.get_requests()) == 1


def test_grounding_supports_sanitize_malformed_spans_and_indices(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    span = {"startIndex": 0, "endIndex": 6, "text": "Paris."}
    raw = completion()
    raw["grounding_metadata"] = {
        "groundingChunks": [{"web": {"uri": "https://example.org", "title": "France"}}],
        "groundingSupports": [
            None,
            {"segment": {**span, "startIndex": True}, "groundingChunkIndices": [0]},
            {"segment": {**span, "endIndex": -1}, "groundingChunkIndices": [0]},
            {"segment": {**span, "startIndex": 7}, "groundingChunkIndices": [0]},
            {"segment": {**span, "text": {"bad": "type"}}, "groundingChunkIndices": [0]},
            {"segment": span, "groundingChunkIndices": "0"},
            {"segment": span, "groundingChunkIndices": [True, -1, 1, "0"]},
            {"segment": span, "groundingChunkIndices": [0, True, -1, 1, "0"]},
        ],
    }
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    result = run_call(project_id)
    assert result.data["grounding_metadata"]["groundingSupports"] == [
        {"segment": {"endIndex": 6, "text": "Paris."}, "groundingChunkIndices": [0]},
        {"segment": {"startIndex": 0, "text": "Paris."}, "groundingChunkIndices": [0]},
        {"segment": {"text": "Paris."}, "groundingChunkIndices": [0]},
        {"segment": {"startIndex": 0, "endIndex": 6}, "groundingChunkIndices": [0]},
        {"segment": span, "groundingChunkIndices": [0]},
    ]


def test_grounding_supports_preserve_missing_fields_without_invented_offsets(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    supports = [{"segment": {"endIndex": 6, "text": "Paris."}, "groundingChunkIndices": [5]}]
    raw = completion()
    raw["grounding_metadata"] = {"groundingSupports": supports}
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    result = run_call(project_id)
    assert result.data["grounding_metadata"] == {"groundingSupports": supports}


def test_models_discovery_and_probe_do_not_generate(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/models",
        json={
            "data": [
                {"id": "gemini-3.8-flash", "pricing": {"input": 4}},
                {"id": "new-provider-model", "object": "model"},
            ],
            "cost": 3,
        },
    )
    result = run_call(project_id, "models", kwargs={})
    assert result.data["data"] == [{"id": "gemini-3.8-flash"}, {"id": "new-provider-model"}]
    assert "cost" not in result.data
    httpx_mock.add_response(method="GET", url=f"{API}/models", json={"data": []})
    probe = run_call(project_id, "test_credentials", kwargs={})
    assert probe["ok"] is True
    assert probe["generation_verified"] is False
    assert all(request.method == "GET" for request in httpx_mock.get_requests())


def test_audio_encoded_only_on_wire(httpx_mock: HTTPXMock, project_id: int, tmp_path: Path) -> None:
    raw_audio = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"audio-data" * 4
    path = tmp_path / "recording.wav"
    path.write_bytes(raw_audio)
    audit = Mock()
    httpx_mock.add_response(
        method="POST", url=f"{API}/chat/completions", json=completion("Speaker 1")
    )
    result = run_call(
        project_id,
        "analyze_audio",
        kwargs={
            "model": "gemini-3.7-flash",
            "instruction": "Transcribe.",
            "audio_path": path,
            "format": "wav",
            "max_tokens": 500,
        },
        run_step_call_repo=audit,
        run_step_id=1,
    )
    body = json.loads(httpx_mock.get_requests()[0].content)
    audio = body["messages"][0]["content"][1]["input_audio"]
    assert audio == {"data": base64.b64encode(raw_audio).decode(), "format": "wav"}
    assert result.data["text"] == "Speaker 1"
    assert audio["data"] not in repr(audit.record_call.call_args_list)
    assert "input_audio" not in repr(audit.record_call.call_args_list)


@pytest.mark.parametrize("inline", [False, True], ids=["content-base64", "inline-image-url"])
def test_image_persisted_without_base64_audit(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    inline: bool,
) -> None:
    audit = Mock()
    raw = image_completion(JPEG_BASE64, inline=inline)
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    result = run_call(
        project_id,
        "generate_image",
        kwargs={"prompt": "An apple"},
        asset_dir=tmp_path,
        run_step_call_repo=audit,
        run_step_id=1,
    )
    item = result.data["data"][0]
    assert item["source_model"] == "gemini-3.1-flash-image"
    assert item["file_format"] == "jpg"
    assert (tmp_path / item["url"].removeprefix("/generated-assets/")).read_bytes() == JPEG_BYTES
    safe = json.dumps(result.data) + repr(result.metadata) + repr(audit.record_call.call_args_list)
    assert JPEG_BASE64 not in safe
    assert "data:image/" not in safe
    assert "cost" not in json.dumps(result.data)
    assert "text" not in result.data
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("inline", [False, True], ids=["content-base64", "inline-image-url"])
@pytest.mark.parametrize(
    "raw",
    ["", "not-base64!", JPEG_BASE64 + "\n", base64.b64encode(b"not a JPEG").decode()],
    ids=["empty", "invalid-base64", "base64-whitespace", "not-jpeg"],
)
def test_malformed_images_fail_without_payload_leak(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    raw: str,
    inline: bool,
) -> None:
    audit = Mock()
    httpx_mock.add_response(
        method="POST", url=f"{API}/chat/completions", json=image_completion(raw, inline=inline)
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_call(
            project_id,
            "generate_image",
            kwargs={"prompt": "An apple"},
            asset_dir=tmp_path,
            run_step_call_repo=audit,
            run_step_id=1,
        )
    safe = str(caught.value) + repr(caught.value.data) + repr(audit.record_call.call_args_list)
    if raw:
        assert raw not in safe
    assert caught.value.data["outcome_unknown"] is True
    assert list(tmp_path.iterdir()) == []


def test_inline_image_allows_empty_text_content(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    raw = image_completion(JPEG_BASE64, inline=True)
    raw["choices"][0]["message"]["content"] = ""
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    result = run_call(
        project_id, "generate_image", kwargs={"prompt": "An apple"}, asset_dir=tmp_path
    )
    item = result.data["data"][0]
    assert (tmp_path / item["url"].removeprefix("/generated-assets/")).read_bytes() == JPEG_BYTES
    assert "text" not in result.data


@pytest.mark.parametrize(
    "images",
    [
        None,
        {},
        [],
        [None],
        [{"type": "image_url", "image_url": {"url": JPEG_DATA_URI}}] * 2,
        [{"image_url": {"url": JPEG_DATA_URI}}],
        [{"type": "text", "image_url": {"url": JPEG_DATA_URI}}],
        [{"type": "image_url", "image_url": JPEG_DATA_URI}],
        [{"type": "image_url", "image_url": {"url": None}}],
        [{"type": "image_url", "image_url": {"url": "https://example.org/private.jpg"}}],
        [{"type": "image_url", "image_url": {"url": "file:///private/image.jpg"}}],
        [{"type": "image_url", "image_url": {"url": JPEG_DATA_URI.replace("jpeg", "png")}}],
        [{"type": "image_url", "image_url": {"url": JPEG_BASE64}}],
    ],
    ids=[
        "null-images",
        "object-images",
        "empty-images",
        "null-entry",
        "multiple-images",
        "missing-type",
        "wrong-type",
        "string-image-url",
        "null-url",
        "remote-url",
        "file-url",
        "wrong-media-type",
        "bare-url-base64",
    ],
)
def test_unsupported_image_entries_are_not_fetched_or_persisted(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    images: Any,
) -> None:
    raw = image_completion(JPEG_BASE64, inline=True)
    raw["choices"][0]["message"]["images"] = images
    audit = Mock()
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    with pytest.raises(IntegrationDownError) as caught:
        run_call(
            project_id,
            "generate_image",
            kwargs={"prompt": "An apple"},
            asset_dir=tmp_path,
            run_step_call_repo=audit,
            run_step_id=1,
        )
    safe = str(caught.value) + repr(caught.value.data) + repr(audit.record_call.call_args_list)
    assert JPEG_BASE64 not in safe
    assert "private.jpg" not in safe
    assert list(tmp_path.iterdir()) == []
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("content", [JPEG_BASE64, "An image follows", {"text": "An image"}])
def test_image_content_and_images_are_not_silently_selected(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    content: Any,
) -> None:
    raw = image_completion(JPEG_BASE64, inline=True)
    raw["choices"][0]["message"]["content"] = content
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    with pytest.raises(IntegrationDownError, match="exactly one image"):
        run_call(project_id, "generate_image", kwargs={"prompt": "An apple"}, asset_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("method", ["chat_complete", "analyze_audio"])
@pytest.mark.parametrize("content", [None, "An image follows"])
def test_image_messages_are_rejected_for_text_actions(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    method: str,
    content: Any,
) -> None:
    raw = image_completion(JPEG_BASE64, inline=True)
    raw["choices"][0]["message"]["content"] = content
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    kwargs = None
    if method == "analyze_audio":
        audio_path = tmp_path / "fixture.wav"
        audio_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfixture-audio")
        kwargs = {
            "model": "gemini-3.8-flash",
            "instruction": "Transcribe this fixture.",
            "audio_path": audio_path,
            "format": "wav",
            "max_tokens": 100,
        }
    asset_dir = tmp_path / "generated"
    with pytest.raises(IntegrationDownError, match="unsupported completion"):
        run_call(project_id, method, kwargs=kwargs, asset_dir=asset_dir)
    assert not asset_dir.exists()
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "status,error_type",
    [(401, IntegrationDownError), (429, RateLimitedError), (503, IntegrationDownError)],
)
def test_provider_failure_is_safe_and_never_replayed(
    httpx_mock: HTTPXMock,
    project_id: int,
    status: int,
    error_type: type[Exception],
) -> None:
    audit = Mock()
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/chat/completions",
        status_code=status,
        headers={"cf-aig-log-id": "failure-123", "retry-after": "7", "X-Model-Pricing": "ignore"},
        json={
            "error": {
                "message": f"rejected {KEY}",
                "code": "provider_rejected",
                "cost": "ignore",
                "base64": "ignore",
            },
            "cost": "ignore",
        },
    )
    with pytest.raises(error_type) as caught:
        run_call(project_id, run_step_call_repo=audit, run_step_id=1)
    exc = caught.value
    assert exc.data["status"] == status
    assert exc.data["automatic_retry_count"] == 0
    assert exc.data["outcome_unknown"] is (status >= 500)
    assert exc.data["provider_error"]["code"] == "provider_rejected"
    assert exc.data["provider_error"]["provider_request_id"] == "failure-123"
    safe = str(exc) + repr(exc.data) + repr(audit.record_call.call_args_list)
    assert KEY not in safe
    assert "base64" not in safe
    assert "X-Model-Pricing" not in safe
    assert len(httpx_mock.get_requests()) == 1


def test_timeout_reports_unknown_without_replay(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_exception(
        httpx.ReadTimeout("response lost"), method="POST", url=f"{API}/chat/completions"
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_call(project_id)
    assert caught.value.data["outcome_unknown"] is True
    assert caught.value.data["automatic_retry_count"] == 0
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        {"choices": []},
        completion({"blob": "bad"}),
        {"error": {"message": "provider refused", "code": "refused"}},
    ],
)
def test_malformed_or_error_success_is_failure(
    httpx_mock: HTTPXMock, project_id: int, raw: Any
) -> None:
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    with pytest.raises(IntegrationDownError):
        run_call(project_id)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"model": "unknown", "messages": MESSAGES, "max_tokens": 100},
        {
            "model": "claude-opus-4-6-thinking",
            "messages": MESSAGES,
            "max_tokens": 100,
            "google_search": True,
        },
        {"model": "gemini-3.1-flash-image", "messages": MESSAGES, "max_tokens": 100},
        {
            "model": "gemini-3.8-flash",
            "messages": [{"role": "tool", "content": "x"}],
            "max_tokens": 100,
        },
        {"model": "gemini-3.8-flash", "messages": MESSAGES, "max_tokens": True},
    ],
)
def test_invalid_chat_fails_before_http(project_id: int, arguments: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        run_call(project_id, kwargs=arguments)


@pytest.mark.parametrize("format", ["wav", "mp3", "aac", "flac", "ogg"])
def test_audio_magic_mismatch_fails_before_http(
    project_id: int, tmp_path: Path, format: str
) -> None:
    path = tmp_path / f"recording.{format}"
    path.write_bytes(b"not audio")
    with pytest.raises(ValidationError):
        run_call(
            project_id,
            "analyze_audio",
            kwargs={
                "model": "gemini-3.8-flash",
                "instruction": "Transcribe",
                "audio_path": path,
                "format": format,
                "max_tokens": 100,
            },
        )


def test_audio_bound_checked_before_read(project_id: int, tmp_path: Path) -> None:
    path = tmp_path / "recording.wav"
    with path.open("wb") as stream:
        stream.truncate(20 * 1024 * 1024 + 1)
    with pytest.raises(ValidationError, match="20 MiB"):
        run_call(
            project_id,
            "analyze_audio",
            kwargs={
                "model": "gemini-3.8-flash",
                "instruction": "Transcribe",
                "audio_path": path,
                "format": "wav",
                "max_tokens": 100,
            },
        )


def test_audio_echo_in_provider_error_does_not_escape(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    raw = b"fLaC" + b"audio data" * 500
    encoded = base64.b64encode(raw).decode()
    path = tmp_path / "audio.flac"
    path.write_bytes(raw)
    audit = Mock()
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/chat/completions",
        status_code=400,
        json={"error": {"message": f"Cannot parse {encoded}", "code": "invalid_audio"}},
    )
    with pytest.raises(IntegrationDownError) as caught:
        run_call(
            project_id,
            "analyze_audio",
            kwargs={
                "model": "gemini-3.8-flash",
                "instruction": "Transcribe",
                "audio_path": path,
                "format": "flac",
                "max_tokens": 100,
            },
            run_step_call_repo=audit,
            run_step_id=1,
        )
    safe = str(caught.value) + repr(caught.value.data) + repr(audit.record_call.call_args_list)
    assert encoded[:100] not in safe
    assert caught.value.data["provider_error"]["code"] == "invalid_audio"


@pytest.mark.parametrize("inline", [False, True], ids=["content-base64", "inline-image-url"])
def test_large_image_never_becomes_a_raw_audit_preview(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    inline: bool,
) -> None:
    encoded = base64.b64encode(b"\xff\xd8\xff" + b"image" * 2000 + b"\xff\xd9").decode()
    audit = Mock()
    httpx_mock.add_response(
        method="POST", url=f"{API}/chat/completions", json=image_completion(encoded, inline=inline)
    )
    result = run_call(
        project_id,
        "generate_image",
        kwargs={"prompt": "An apple"},
        asset_dir=tmp_path,
        run_step_call_repo=audit,
        run_step_id=1,
    )
    safe = json.dumps(result.data) + repr(audit.record_call.call_args_list)
    assert encoded[:100] not in safe
    assert "preview" not in safe


@pytest.mark.parametrize("inline", [False, True], ids=["content-base64", "inline-image-url"])
def test_image_limit_rejects_before_decode(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline: bool,
) -> None:
    monkeypatch.setattr("stackos.integrations.aignc.MAX_IMAGE_BYTES", 8)
    encoded = base64.b64encode(b"\xff\xd8\xff" + b"large-image" + b"\xff\xd9").decode()
    decode = Mock(side_effect=AssertionError("Oversized image must not be decoded"))
    monkeypatch.setattr("stackos.integrations.aignc.base64.b64decode", decode)
    httpx_mock.add_response(
        method="POST", url=f"{API}/chat/completions", json=image_completion(encoded, inline=inline)
    )
    with pytest.raises(IntegrationDownError, match="20 MiB"):
        run_call(project_id, "generate_image", kwargs={"prompt": "An apple"}, asset_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
    decode.assert_not_called()


@pytest.mark.parametrize("inline", [False, True], ids=["content-base64", "inline-image-url"])
def test_decoded_image_limit_rejects_same_base64_size_bucket(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inline: bool,
) -> None:
    monkeypatch.setattr("stackos.integrations.aignc.MAX_IMAGE_BYTES", 5)
    encoded = base64.b64encode(b"\xff\xd8\xffx\xff\xd9").decode()
    httpx_mock.add_response(
        method="POST", url=f"{API}/chat/completions", json=image_completion(encoded, inline=inline)
    )
    with pytest.raises(IntegrationDownError, match="invalid JPEG"):
        run_call(project_id, "generate_image", kwargs={"prompt": "An apple"}, asset_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_unconfigured_asset_storage_fails_before_generation(project_id: int) -> None:
    with pytest.raises(ValidationError, match="generated-assets"):
        run_call(project_id, "generate_image", kwargs={"prompt": "An apple"})


@pytest.mark.parametrize("raw", [{}, {"data": "bad"}, {"data": [{"id": 5}]}])
def test_malformed_models_are_not_a_successful_probe(
    httpx_mock: HTTPXMock,
    project_id: int,
    raw: Any,
) -> None:
    httpx_mock.add_response(method="GET", url=f"{API}/models", json=raw)
    with pytest.raises(IntegrationDownError):
        run_call(project_id, "test_credentials", kwargs={})


def test_provider_tool_call_is_not_executed(httpx_mock: HTTPXMock, project_id: int) -> None:
    raw = completion()
    raw["choices"][0]["message"]["tool_calls"] = [{"id": "tool-1", "function": {"name": "execute"}}]
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=raw)
    with pytest.raises(IntegrationDownError, match="unsupported completion"):
        run_call(project_id)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "format,raw",
    [
        ("mp3", b"ID3" + b"audio"),
        ("aac", b"\xff\xf1" + b"audio"),
        ("flac", b"fLaC" + b"audio"),
        ("ogg", b"OggS" + b"audio"),
    ],
)
def test_other_documented_audio_formats_are_encoded(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    format: str,
    raw: bytes,
) -> None:
    path = tmp_path / f"audio.{format}"
    path.write_bytes(raw)
    httpx_mock.add_response(method="POST", url=f"{API}/chat/completions", json=completion())
    run_call(
        project_id,
        "analyze_audio",
        kwargs={
            "model": "gemini-3.8-flash",
            "instruction": "Transcribe",
            "audio_path": path,
            "format": format,
            "max_tokens": 100,
        },
    )
    body = json.loads(httpx_mock.get_requests()[0].content)
    audio = body["messages"][0]["content"][1]["input_audio"]
    assert audio == {"data": base64.b64encode(raw).decode(), "format": format}
