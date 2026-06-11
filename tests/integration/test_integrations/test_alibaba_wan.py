"""Alibaba Wan wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.alibaba_wan import AlibabaWanIntegration
from stackos.mcp.errors import IntegrationDownError


def test_text_to_video_submits_dashscope_task_polls_and_persists(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
        json={"output": {"task_id": "task_123", "task_status": "PENDING"}, "request_id": "req_1"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/task_123",
        json={"output": {"task_id": "task_123", "task_status": "RUNNING"}},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/task_123",
        json={
            "request_id": "req_poll",
            "output": {
                "task_id": "task_123",
                "task_status": "SUCCEEDED",
                "video_url": "https://dashscope-result.example/video.mp4?Expires=secret",
                "orig_prompt": "video prompt",
            },
            "usage": {"duration": 5, "video_count": 1, "SR": 720},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-result.example/video.mp4?Expires=secret",
        content=b"wan-video",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AlibabaWanIntegration(
                payload=b"dashscope-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.generate_video(
                prompt="video prompt",
                mode="text-to-video",
                resolution="720P",
                aspect_ratio="9:16",
                duration=5,
                prompt_extend=False,
                poll_interval_seconds=0,
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer dashscope-key"
    assert request.headers["X-DashScope-Async"] == "enable"
    body = json.loads(request.content.decode("utf-8"))
    assert body == {
        "model": "wan2.6-t2v",
        "input": {"prompt": "video prompt"},
        "parameters": {
            "duration": 5,
            "prompt_extend": False,
            "size": "720*1280",
        },
    }
    item = result.data["data"][0]
    assert item["url"].startswith("/generated-assets/alibaba-wan/alibaba-wan-video-")
    assert item["task_id"] == "task_123"
    assert "video_url" not in item
    assert (tmp_path / item["url"].removeprefix("/generated-assets/")).read_bytes() == b"wan-video"


def test_image_to_video_sends_url_media_protocol(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
        json={"output": {"task_id": "task_i2v", "task_status": "PENDING"}},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/task_i2v",
        json={
            "output": {
                "task_id": "task_i2v",
                "task_status": "SUCCEEDED",
                "video_url": "https://dashscope-result.example/i2v.mp4",
            }
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-result.example/i2v.mp4",
        content=b"wan-i2v",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = AlibabaWanIntegration(
                payload=b"dashscope-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            await integ.generate_video(
                prompt="animate frame",
                mode="first-last-frame",
                first_frame_url="https://cdn.example/first.png",
                last_frame_url="https://cdn.example/last.png",
                audio_url="https://cdn.example/audio.mp3",
                resolution="1080P",
                duration=10,
                poll_interval_seconds=0,
            )

    asyncio.run(go())
    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    assert body["model"] == "wan2.7-i2v"
    assert body["input"]["media"] == [
        {"type": "first_frame", "url": "https://cdn.example/first.png"},
        {"type": "last_frame", "url": "https://cdn.example/last.png"},
        {"type": "driving_audio", "url": "https://cdn.example/audio.mp3"},
    ]
    assert body["parameters"]["resolution"] == "1080P"
    assert "size" not in body["parameters"]


def test_video_continuation_sends_first_clip_and_optional_last_frame(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
        json={"output": {"task_id": "task_cont", "task_status": "PENDING"}},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/task_cont",
        json={
            "output": {
                "task_id": "task_cont",
                "task_status": "SUCCEEDED",
                "video_url": "https://dashscope-result.example/cont.mp4",
            }
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-result.example/cont.mp4",
        content=b"wan-continuation",
        headers={"content-type": "video/mp4"},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = AlibabaWanIntegration(
                payload=b"dashscope-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            await integ.generate_video(
                prompt="continue clip",
                mode="video-continuation",
                first_clip_url="https://cdn.example/clip.mp4",
                last_frame_url="https://cdn.example/last.png",
                resolution="720P",
                duration=6,
                poll_interval_seconds=0,
            )

    asyncio.run(go())
    body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    assert body["model"] == "wan2.7-i2v"
    assert body["input"]["media"] == [
        {"type": "first_clip", "url": "https://cdn.example/clip.mp4"},
        {"type": "last_frame", "url": "https://cdn.example/last.png"},
    ]


def test_video_continuation_rejects_audio_url(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = AlibabaWanIntegration(
                payload=b"dashscope-key",
                project_id=project_id,
                http=client,
            )
            await integ.generate_video(
                prompt="continue clip",
                mode="video-continuation",
                first_clip_url="https://cdn.example/clip.mp4",
                audio_url="https://cdn.example/audio.mp3",
                poll_interval_seconds=0,
            )

    with pytest.raises(IntegrationDownError, match="does not support audio_url"):
        asyncio.run(go())
    assert httpx_mock.get_requests() == []


def test_failed_task_status_raises(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
        json={"output": {"task_id": "task_bad", "task_status": "PENDING"}},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/task_bad",
        json={
            "output": {
                "task_id": "task_bad",
                "task_status": "FAILED",
                "code": "InvalidParameter",
            }
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AlibabaWanIntegration(
                payload=b"dashscope-key",
                project_id=project_id,
                http=client,
            )
            return await integ.generate_video(prompt="bad", poll_interval_seconds=0)

    with pytest.raises(IntegrationDownError, match="ended with status FAILED"):
        asyncio.run(go())


def test_text_to_video_raises_on_auth_failure(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
        status_code=401,
        json={"message": "invalid api key"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = AlibabaWanIntegration(
                payload=b"bad-key",
                project_id=project_id,
                http=client,
            )
            return await integ.generate_video(prompt="auth", poll_interval_seconds=0)

    with pytest.raises(IntegrationDownError, match="client error 401"):
        asyncio.run(go())
