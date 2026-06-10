"""Google Veo wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.google_veo import GoogleVeoIntegration
from stackos.mcp.errors import IntegrationDownError


def test_generate_video_uses_long_running_operation_and_persists_video(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "veo-3.1-generate-preview:predictLongRunning"
        ),
        json={"name": "operations/veo-123"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/v1beta/operations/veo-123",
        json={"name": "operations/veo-123", "done": False},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/v1beta/operations/veo-123",
        json={
            "name": "operations/veo-123",
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [
                        {
                            "video": {
                                "uri": "https://generativelanguage.googleapis.com/download/video"
                            }
                        }
                    ]
                }
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/download/video",
        content=b"veo-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = GoogleVeoIntegration(
                payload=b"gemini-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.generate_video(
                prompt="cinematic product video",
                duration_seconds=6,
                aspect_ratio="16:9",
                resolution="720p",
                poll_interval_seconds=0,
            )

    result = asyncio.run(go())
    submit = httpx_mock.get_requests()[0]
    assert submit.headers["x-goog-api-key"] == "gemini-key"
    body = json.loads(submit.content.decode("utf-8"))
    assert body == {
        "instances": [{"prompt": "cinematic product video"}],
        "parameters": {
            "aspectRatio": "16:9",
            "durationSeconds": 6,
            "resolution": "720p",
        },
    }
    download = httpx_mock.get_requests()[-1]
    assert download.headers["x-goog-api-key"] == "gemini-key"
    assert result.data["operation_name"] == "operations/veo-123"
    item = result.data["data"][0]
    assert item["url"].startswith("/generated-assets/google-veo/google-veo-video-")
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"veo-video"


def test_generate_video_rejects_non_google_operation_url(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "veo-3.1-generate-preview:predictLongRunning"
        ),
        json={"name": "https://evil.example.test/operations/veo-123"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = GoogleVeoIntegration(
                payload=b"gemini-key",
                project_id=project_id,
                http=client,
            )
            return await integ.generate_video(prompt="bad operation host")

    with pytest.raises(IntegrationDownError, match="operation URL"):
        asyncio.run(go())
    assert len(httpx_mock.get_requests()) == 1


def test_generate_video_strips_api_key_on_cross_origin_download_redirect(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "veo-3.1-generate-preview:predictLongRunning"
        ),
        json={"name": "operations/veo-redirect"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/v1beta/operations/veo-redirect",
        json={
            "name": "operations/veo-redirect",
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [
                        {
                            "video": {
                                "uri": "https://generativelanguage.googleapis.com/download/redirect"
                            }
                        }
                    ]
                }
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/download/redirect",
        status_code=302,
        headers={"location": "https://media.example.test/video.mp4"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://media.example.test/video.mp4",
        content=b"redirect-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = GoogleVeoIntegration(
                payload=b"gemini-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            await integ.generate_video(prompt="download redirect", poll_interval_seconds=0)

    asyncio.run(go())
    requests = httpx_mock.get_requests()
    assert requests[-2].headers["x-goog-api-key"] == "gemini-key"
    assert "x-goog-api-key" not in requests[-1].headers


def test_generate_video_sends_first_and_last_frame_inline_data(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.png"
    last = tmp_path / "last.jpg"
    first.write_bytes(b"first-png")
    last.write_bytes(b"last-jpg")
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "veo-3.1-generate-preview:predictLongRunning"
        ),
        json={"name": "operations/veo-frames"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/v1beta/operations/veo-frames",
        json={
            "name": "operations/veo-frames",
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [
                        {
                            "video": {
                                "uri": "https://generativelanguage.googleapis.com/download/frames"
                            }
                        }
                    ]
                }
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/download/frames",
        content=b"frame-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = GoogleVeoIntegration(
                payload=b"gemini-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            await integ.generate_video(
                prompt="interpolate frames",
                mode="first-last-frame",
                input_image_path=first,
                last_frame_path=last,
                poll_interval_seconds=0,
            )

    asyncio.run(go())
    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    instance = body["instances"][0]
    assert instance["image"]["inlineData"] == {
        "mimeType": "image/png",
        "data": base64.b64encode(b"first-png").decode("ascii"),
    }
    assert instance["lastFrame"]["inlineData"] == {
        "mimeType": "image/jpeg",
        "data": base64.b64encode(b"last-jpg").decode("ascii"),
    }


def test_generate_video_raises_on_operation_error(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "veo-3.1-generate-preview:predictLongRunning"
        ),
        json={"name": "operations/veo-failed"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/v1beta/operations/veo-failed",
        json={"name": "operations/veo-failed", "done": True, "error": {"code": 400}},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = GoogleVeoIntegration(
                payload=b"gemini-key",
                project_id=project_id,
                http=client,
            )
            return await integ.generate_video(prompt="bad", poll_interval_seconds=0)

    with pytest.raises(IntegrationDownError, match="operation failed"):
        asyncio.run(go())
