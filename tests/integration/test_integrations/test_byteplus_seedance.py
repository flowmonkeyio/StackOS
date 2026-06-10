"""BytePlus Seedance video wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.byteplus_ark import BytePlusArkIntegration
from stackos.mcp.errors import IntegrationDownError


def test_seedance_text_to_video_task_polls_and_persists_output(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks",
        json={"id": "cgt-123"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks/cgt-123",
        json={"id": "cgt-123", "status": "running"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks/cgt-123",
        json={
            "id": "cgt-123",
            "model": "dreamina-seedance-2-0-260128",
            "status": "succeeded",
            "content": {"video_url": "https://ark-output.example/video.mp4?sig=secret"},
            "usage": {"completion_tokens": 108900, "total_tokens": 108900},
            "resolution": "720p",
            "ratio": "16:9",
            "duration": 5,
            "framespersecond": 24,
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://ark-output.example/video.mp4?sig=secret",
        content=b"seedance-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.generate_seedance_video(
                prompt="video prompt",
                ratio="16:9",
                duration=5,
                generate_audio=True,
                watermark=False,
                poll_interval_seconds=0,
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer ark-key"
    body = json.loads(request.content.decode("utf-8"))
    assert body == {
        "model": "dreamina-seedance-2-0-260128",
        "content": [{"type": "text", "text": "video prompt"}],
        "resolution": "720p",
        "ratio": "16:9",
        "duration": 5,
        "generate_audio": True,
        "watermark": False,
    }
    item = result.data["data"][0]
    rendered = json.dumps(result.data)
    assert item["url"].startswith("/generated-assets/byteplus-ark/byteplus-seedance-video-")
    assert item["task_id"] == "cgt-123"
    assert "https://ark-output.example/video.mp4" not in rendered
    assert (tmp_path / item["url"].removeprefix("/generated-assets/")).read_bytes() == (
        b"seedance-video"
    )


def test_seedance_first_last_frame_sends_base64_image_roles(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.png"
    last = tmp_path / "last.webp"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks",
        json={"id": "cgt-frames"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks/cgt-frames",
        json={
            "id": "cgt-frames",
            "status": "succeeded",
            "content": {"video_url": "https://ark-output.example/frames.mp4"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://ark-output.example/frames.mp4",
        content=b"frames-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            await integ.generate_seedance_video(
                prompt="use the two frames",
                mode="first-last-frame",
                input_image_paths=[first, last],
                poll_interval_seconds=0,
            )

    asyncio.run(go())
    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    assert body["content"][0] == {"type": "text", "text": "use the two frames"}
    assert body["content"][1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(b'first').decode('ascii')}"},
        "role": "first_frame",
    }
    assert body["content"][2] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/webp;base64,{base64.b64encode(b'last').decode('ascii')}"},
        "role": "last_frame",
    }


def test_seedance_raises_on_failed_status(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks",
        json={"id": "cgt-failed"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks/cgt-failed",
        json={"id": "cgt-failed", "status": "failed", "error": {"code": "InvalidParameter"}},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                project_id=project_id,
                http=client,
            )
            return await integ.generate_seedance_video(
                prompt="bad",
                poll_interval_seconds=0,
            )

    with pytest.raises(IntegrationDownError, match="ended with status failed"):
        asyncio.run(go())
