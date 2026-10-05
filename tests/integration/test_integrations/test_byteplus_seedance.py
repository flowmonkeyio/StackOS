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
from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.shared.byteplus.ark import BytePlusArkIntegration


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
                http=client,
                output_dir=tmp_path / "byteplus-ark",
            )
            return await integ.generate_seedance_video(
                prompt="video prompt",
                ratio="16:9",
                duration=5,
                generate_audio=True,
                watermark=False,
                poll_interval_seconds=0,
                model="dreamina-seedance-2-0-260128",
                mode="text-to-video",
                region="ap-southeast-1",
                resolution="720p",
                poll_timeout_seconds=1800.0,
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
    assert item["path"].startswith(str(tmp_path / "byteplus-ark/byteplus-seedance-video-"))
    assert item["task_id"] == "cgt-123"
    assert "https://ark-output.example/video.mp4" not in rendered
    assert (Path(item["path"])).read_bytes() == (b"seedance-video")


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
                http=client,
                output_dir=tmp_path / "byteplus-ark",
            )
            await integ.generate_seedance_video(
                prompt="use the two frames",
                mode="first-last-frame",
                input_image_paths=[first, last],
                poll_interval_seconds=0,
                model="dreamina-seedance-2-0-260128",
                region="ap-southeast-1",
                resolution="720p",
                ratio="16:9",
                duration=5,
                poll_timeout_seconds=1800.0,
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
                http=client,
            )
            return await integ.generate_seedance_video(
                prompt="bad",
                poll_interval_seconds=0,
                model="dreamina-seedance-2-0-260128",
                mode="text-to-video",
                region="ap-southeast-1",
                resolution="720p",
                ratio="16:9",
                duration=5,
                poll_timeout_seconds=1800.0,
            )

    with pytest.raises(IntegrationDownError, match="ended with status failed"):
        asyncio.run(go())


def test_seedance_rate_limit_does_not_resubmit_mutation(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    submit_url = "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"
    httpx_mock.add_response(
        method="POST",
        url=submit_url,
        status_code=429,
        headers={"retry-after": "0"},
        json={"error": {"message": "rate limited"}},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                http=client,
            )
            return await integ.generate_seedance_video(
                prompt="retry",
                poll_interval_seconds=0,
                model="dreamina-seedance-2-0-260128",
                mode="text-to-video",
                region="ap-southeast-1",
                resolution="720p",
                ratio="16:9",
                duration=5,
                poll_timeout_seconds=1800.0,
            )

    with pytest.raises(RateLimitedError, match="429") as caught:
        asyncio.run(go())
    assert caught.value.data["status"] == 429
    assert len(httpx_mock.get_requests()) == 1


def test_host_default_choices_and_generated_asset_projection(host_media_projection):
    from stackos.actions.byteplus_seedance import BytePlusSeedanceVideoActionConnector

    result, native_data = asyncio.run(
        host_media_projection(
            BytePlusSeedanceVideoActionConnector(),
            provider="byteplus-seedance",
            operation="video.generate",
            data={"prompt": "fixture"},
            output_subdir="byteplus-ark",
        )
    )
    assert native_data["model"] == "dreamina-seedance-2-0-260128"
    assert native_data["mode"] == "text-to-video"
    assert native_data["region"] == "ap-southeast-1"
    assert native_data["resolution"] == "720p"
    assert native_data["ratio"] == "16:9"
    assert native_data["duration"] == 5
    assert native_data["poll_interval_seconds"] == 10.0
    assert native_data["poll_timeout_seconds"] == 1800.0
    assert result.metadata_json["vendor"] == "byteplus-ark"
    assert result.metadata_json["model_family"] == "seedance"
