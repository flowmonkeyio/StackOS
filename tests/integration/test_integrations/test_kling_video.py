"""Kling video wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.kling_video import KlingVideoIntegration
from stackos.mcp.errors import IntegrationDownError

_CREDENTIAL = b'{"access_key":"ak-test","secret_key":"sk-test"}'


def test_generate_video_submits_text_task_polls_and_persists_video(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api-singapore.klingai.com/v1/videos/text2video",
        json={
            "code": 0,
            "request_id": "kling-submit-1",
            "data": {"task_id": "task-123", "task_status": "submitted"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api-singapore.klingai.com/v1/videos/text2video/task-123",
        json={
            "code": 0,
            "request_id": "kling-poll-1",
            "data": {"task_id": "task-123", "task_status": "processing"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api-singapore.klingai.com/v1/videos/text2video/task-123",
        json={
            "code": 0,
            "request_id": "kling-poll-2",
            "data": {
                "task_id": "task-123",
                "task_status": "succeed",
                "task_result": {
                    "videos": [
                        {
                            "id": "video-123",
                            "url": "https://kling.example/video.mp4",
                            "duration": "5",
                        }
                    ]
                },
                "final_unit_deduction": "20",
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kling.example/video.mp4",
        content=b"kling-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = KlingVideoIntegration(
                payload=_CREDENTIAL,
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.generate_video(
                prompt="cinematic food ad",
                duration=5,
                aspect_ratio="1:1",
                poll_interval_seconds=0,
            )

    result = asyncio.run(go())
    submit = httpx_mock.get_requests()[0]
    assert submit.headers["authorization"].startswith("Bearer ")
    body = json.loads(submit.content.decode("utf-8"))
    assert body == {
        "model_name": "kling-v3",
        "prompt": "cinematic food ad",
        "duration": "5",
        "mode": "pro",
        "sound": "off",
        "aspect_ratio": "1:1",
    }
    assert result.data["task_id"] == "task-123"
    item = result.data["data"][0]
    assert item["url"].startswith("/generated-assets/kling/kling-video-")
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == b"kling-video"


def test_generate_video_sends_raw_base64_for_first_and_last_frame(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.png"
    tail = tmp_path / "tail.jpg"
    first.write_bytes(b"first-image")
    tail.write_bytes(b"tail-image")
    httpx_mock.add_response(
        method="POST",
        url="https://api-singapore.klingai.com/v1/videos/image2video",
        json={
            "code": 0,
            "request_id": "kling-submit-2",
            "data": {"task_id": "task-frames", "task_status": "submitted"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api-singapore.klingai.com/v1/videos/image2video/task-frames",
        json={
            "code": 0,
            "request_id": "kling-poll-frames",
            "data": {
                "task_id": "task-frames",
                "task_status": "succeed",
                "task_result": {
                    "videos": [
                        {
                            "id": "video-frames",
                            "url": "https://kling.example/frames.mp4",
                            "duration": "5",
                        }
                    ]
                },
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kling.example/frames.mp4",
        content=b"kling-frames",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = KlingVideoIntegration(
                payload=_CREDENTIAL,
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            await integ.generate_video(
                prompt="interpolate these frames",
                mode="first-last-frame",
                input_image_path=first,
                image_tail_path=tail,
                poll_interval_seconds=0,
            )

    asyncio.run(go())
    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    assert body["image"] == base64.b64encode(b"first-image").decode("ascii")
    assert body["image_tail"] == base64.b64encode(b"tail-image").decode("ascii")
    assert "data:image" not in body["image"]
    assert "aspect_ratio" not in body


def test_generate_video_raises_on_failed_task(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api-singapore.klingai.com/v1/videos/text2video",
        json={
            "code": 0,
            "request_id": "kling-submit-failed",
            "data": {"task_id": "task-failed", "task_status": "submitted"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api-singapore.klingai.com/v1/videos/text2video/task-failed",
        json={
            "code": 0,
            "request_id": "kling-poll-failed",
            "data": {
                "task_id": "task-failed",
                "task_status": "failed",
                "task_status_msg": "content rejected",
            },
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = KlingVideoIntegration(
                payload=_CREDENTIAL,
                project_id=project_id,
                http=client,
            )
            return await integ.generate_video(prompt="bad", poll_interval_seconds=0)

    with pytest.raises(IntegrationDownError, match="failed"):
        asyncio.run(go())
