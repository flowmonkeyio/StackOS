"""xAI Imagine wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.xai_imagine import XAIImagineIntegration
from stackos.mcp.errors import IntegrationDownError


def test_generate_image_requests_base64_and_persists_output(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    image_bytes = b"fake-jpg-bytes"
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/images/generations",
        json={
            "data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}],
            "usage": {"cost_in_usd_ticks": 120000000},
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = XAIImagineIntegration(
                payload=b"xai-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.generate_image(
                prompt="image prompt",
                aspect_ratio="16:9",
                resolution="2k",
                n=2,
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == "Bearer xai-key"
    body = json.loads(request.content.decode("utf-8"))
    assert body == {
        "model": "grok-imagine-image-quality",
        "prompt": "image prompt",
        "aspect_ratio": "16:9",
        "resolution": "2k",
        "n": 2,
        "response_format": "b64_json",
    }
    item = result.data["data"][0]
    assert "b64_json" not in item
    assert item["url"].startswith("/generated-assets/xai-imagine/xai-image-")
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == image_bytes
    assert result.cost_usd == 0.012


def test_edit_image_sends_json_data_uri_and_persists_temporary_url(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"fake-png")
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/images/edits",
        json={"data": [{"url": "https://cdn.x.ai/image.jpg"}]},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://cdn.x.ai/image.jpg",
        content=b"edited-jpg",
        headers={"content-type": "image/jpeg"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = XAIImagineIntegration(
                payload=b"xai-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.edit_image(
                prompt="make it cinematic",
                input_image_paths=[source],
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert request.headers["content-type"] == "application/json"
    body = json.loads(request.content.decode("utf-8"))
    assert body["image"]["url"].startswith("data:image/png;base64,")
    assert "aspect_ratio" not in body
    assert body["model"] == "grok-imagine-image-quality"
    assert result.data["data"][0]["file_format"] == "jpg"
    path = tmp_path / result.data["data"][0]["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"edited-jpg"


def test_edit_image_sends_aspect_ratio_for_multi_image_edit(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/images/edits",
        json={"data": [{"b64_json": base64.b64encode(b"edited").decode("ascii")}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = XAIImagineIntegration(
                payload=b"xai-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.edit_image(
                prompt="combine them",
                input_image_paths=[first, second],
                aspect_ratio="1:1",
            )

    asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content.decode("utf-8"))
    assert body["aspect_ratio"] == "1:1"
    assert len(body["images"]) == 2


def test_generate_video_polls_downloads_and_persists_output(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/videos/generations",
        json={"request_id": "req_123"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.x.ai/v1/videos/req_123",
        json={"status": "pending"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.x.ai/v1/videos/req_123",
        json={
            "status": "done",
            "model": "grok-imagine-video",
            "video": {"url": "https://cdn.x.ai/video.mp4", "duration": 5},
            "usage": {"cost_in_usd_ticks": 3300000000},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://cdn.x.ai/video.mp4",
        content=b"fake-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = XAIImagineIntegration(
                payload=b"xai-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.generate_video(
                prompt="video prompt",
                duration=5,
                aspect_ratio="16:9",
                resolution="720p",
                poll_interval_seconds=0,
            )

    result = asyncio.run(go())
    submit = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    assert submit == {
        "model": "grok-imagine-video",
        "prompt": "video prompt",
        "duration": 5,
        "aspect_ratio": "16:9",
        "resolution": "720p",
    }
    assert result.data["request_id"] == "req_123"
    item = result.data["data"][0]
    assert item["url"].startswith("/generated-assets/xai-imagine/xai-video-")
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"fake-video"
    assert result.cost_usd == pytest.approx(0.33)


def test_generate_video_raises_on_failed_status(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/videos/generations",
        json={"request_id": "req_123"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.x.ai/v1/videos/req_123",
        json={"status": "failed", "error": {"code": "invalid_argument"}},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = XAIImagineIntegration(
                payload=b"xai-key",
                project_id=project_id,
                http=client,
            )
            return await integ.generate_video(
                prompt="video prompt",
                poll_interval_seconds=0,
            )

    with pytest.raises(IntegrationDownError, match="ended with status failed"):
        asyncio.run(go())
