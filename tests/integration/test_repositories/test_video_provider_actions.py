"""Executable video provider action tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.projects import (
    IntegrationBudgetRepository,
    IntegrationCredentialRepository,
)


def _credential_ref(
    session: Session,
    project_id: int,
    provider_key: str,
    payload: bytes,
) -> str:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind=provider_key,
        secret_payload=payload,
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id,
        kind=provider_key,
        monthly_budget_usd=10.0,
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key=provider_key)
    return status.connections[0].credential_ref


def test_google_veo_video_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential_ref(session, project_id, "google-veo", b"gemini-key")
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "veo-3.1-generate-preview:predictLongRunning"
        ),
        json={"name": "operations/action-veo"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://generativelanguage.googleapis.com/v1beta/operations/action-veo",
        json={
            "name": "operations/action-veo",
            "done": True,
            "response": {
                "generateVideoResponse": {
                    "generatedSamples": [
                        {"video": {"uri": "https://google.example/action-veo.mp4"}}
                    ]
                }
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://google.example/action-veo.mp4",
        content=b"veo-action",
        headers={"content-type": "video/mp4"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.google.video.generate",
            input_json={
                "prompt": "cinematic",
                "duration_seconds": 6,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            credential_ref=credential_ref,
        )
    ).data

    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert item["url"].startswith("/generated-assets/google-veo/google-veo-video-")
    assert item["artifact_ref"] == item["url"]
    assert isinstance(item["artifact_id"], int)
    assert "https://google.example/action-veo.mp4" not in rendered
    assert "gemini-key" not in rendered
    assert (tmp_path / item["url"].removeprefix("/generated-assets/")).read_bytes() == b"veo-action"


def test_google_veo_video_action_rejects_undocumented_parameter_combinations(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id, "google-veo", b"gemini-key")
    repo = ActionRepository(session)

    validation = repo.validate(
        project_id=project_id,
        action_ref="utils.google.video.generate",
        input_json={
            "prompt": "cinematic",
            "duration_seconds": 5,
            "resolution": "720p",
        },
        credential_ref=credential_ref,
    )
    assert validation.valid is False
    assert any(issue.path == "$.duration_seconds" for issue in validation.issues)

    high_res = repo.validate(
        project_id=project_id,
        action_ref="utils.google.video.generate",
        input_json={
            "prompt": "cinematic",
            "duration_seconds": 6,
            "resolution": "1080p",
        },
        credential_ref=credential_ref,
    )
    assert high_res.valid is False
    assert any(
        issue.path == "$.duration_seconds" and issue.code == "model_mismatch"
        for issue in high_res.issues
    )

    person = repo.validate(
        project_id=project_id,
        action_ref="utils.google.video.generate",
        input_json={
            "prompt": "cinematic",
            "mode": "text-to-video",
            "person_generation": "allow_adult",
        },
        credential_ref=credential_ref,
    )
    assert person.valid is False
    assert any(
        issue.path == "$.person_generation" and issue.code == "mode_mismatch"
        for issue in person.issues
    )


def test_alibaba_wan_video_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential_ref(session, project_id, "alibaba-wan", b"dashscope-key")
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/"
            "video-generation/video-synthesis"
        ),
        json={"request_id": "ali-submit", "output": {"task_id": "ali-task"}},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://dashscope-intl.aliyuncs.com/api/v1/tasks/ali-task",
        json={
            "request_id": "ali-poll",
            "output": {
                "task_id": "ali-task",
                "task_status": "SUCCEEDED",
                "video_url": "https://alibaba.example/video.mp4",
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://alibaba.example/video.mp4",
        content=b"ali-video",
        headers={"content-type": "video/mp4"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.alibaba.video.generate",
            input_json={
                "prompt": "cinematic",
                "duration": 5,
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            credential_ref=credential_ref,
        )
    ).data

    post_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert post_body["model"] == "wan2.7-t2v"
    assert post_body["parameters"]["size"] == "1280*720"
    assert item["url"].startswith("/generated-assets/alibaba-wan/alibaba-wan-video-")
    assert item["artifact_ref"] == item["url"]
    assert "https://alibaba.example/video.mp4" not in rendered
    assert "dashscope-key" not in rendered
    assert (tmp_path / item["url"].removeprefix("/generated-assets/")).read_bytes() == b"ali-video"


def test_alibaba_wan_video_action_rejects_missing_provider_fetchable_urls(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id, "alibaba-wan", b"dashscope-key")

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.alibaba.video.generate",
        input_json={
            "prompt": "animate product",
            "mode": "image-to-video",
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.first_frame_url" for issue in validation.issues)


def test_byteplus_seedance_video_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential_ref(session, project_id, "byteplus-ark", b"byteplus-key")
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks",
        json={"id": "seedance-task"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks/seedance-task",
        json={
            "id": "seedance-task",
            "status": "succeeded",
            "model": "dreamina-seedance-2-0-260128",
            "content": {"video_url": "https://byteplus.example/video.mp4"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://byteplus.example/video.mp4",
        content=b"seedance-video",
        headers={"content-type": "video/mp4"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.byteplus.video.generate",
            input_json={
                "prompt": "cinematic",
                "mode": "text-to-video",
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            credential_ref=credential_ref,
        )
    ).data

    post_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert post_body["content"] == [{"type": "text", "text": "cinematic"}]
    assert item["url"].startswith("/generated-assets/byteplus-ark/byteplus-seedance-video-")
    assert item["artifact_ref"] == item["url"]
    assert "https://byteplus.example/video.mp4" not in rendered
    assert "byteplus-key" not in rendered
    assert (
        tmp_path / item["url"].removeprefix("/generated-assets/")
    ).read_bytes() == b"seedance-video"


def test_byteplus_seedance_video_action_rejects_schema_and_model_duration_mismatches(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id, "byteplus-ark", b"byteplus-key")
    repo = ActionRepository(session)

    priority = repo.validate(
        project_id=project_id,
        action_ref="utils.byteplus.video.generate",
        input_json={
            "prompt": "seedance",
            "priority": "high",
        },
        credential_ref=credential_ref,
    )
    assert priority.valid is False
    assert any(issue.path == "$.priority" for issue in priority.issues)

    duration = repo.validate(
        project_id=project_id,
        action_ref="utils.byteplus.video.generate",
        input_json={
            "prompt": "seedance",
            "model": "seedance-1-5-pro-251215",
            "duration": 15,
        },
        credential_ref=credential_ref,
    )
    assert duration.valid is False
    assert any(issue.path == "$.duration" and issue.code == "range" for issue in duration.issues)


def test_kling_video_action_executes_and_registers_artifact(
    session: Session,
    project_id: int,
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential_ref(
        session,
        project_id,
        "kling",
        b'{"access_key":"ak-test","secret_key":"sk-test"}',
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api-singapore.klingai.com/v1/videos/text2video",
        json={
            "code": 0,
            "request_id": "kling-submit",
            "data": {"task_id": "kling-task", "task_status": "submitted"},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api-singapore.klingai.com/v1/videos/text2video/kling-task",
        json={
            "code": 0,
            "request_id": "kling-poll",
            "data": {
                "task_id": "kling-task",
                "task_status": "succeed",
                "task_result": {
                    "videos": [
                        {
                            "id": "kling-video",
                            "url": "https://kling.example/video.mp4",
                            "duration": "5",
                        }
                    ]
                },
            },
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://kling.example/video.mp4",
        content=b"kling-action",
        headers={"content-type": "video/mp4"},
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="utils.kling.video.generate",
            input_json={
                "prompt": "cinematic",
                "poll_interval_seconds": 1,
                "poll_timeout_seconds": 60,
            },
            credential_ref=credential_ref,
        )
    ).data

    post_body = json.loads(httpx_mock.get_requests()[0].content.decode("utf-8"))
    item = result.output_json["data"][0]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert post_body["model_name"] == "kling-v3"
    assert post_body["mode"] == "pro"
    assert item["url"].startswith("/generated-assets/kling/kling-video-")
    assert item["artifact_ref"] == item["url"]
    assert "https://kling.example/video.mp4" not in rendered
    assert "sk-test" not in rendered
    assert (
        tmp_path / item["url"].removeprefix("/generated-assets/")
    ).read_bytes() == b"kling-action"


def test_kling_video_action_rejects_older_model_ids(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(
        session,
        project_id,
        "kling",
        b'{"access_key":"ak-test","secret_key":"sk-test"}',
    )

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref="utils.kling.video.generate",
        input_json={
            "prompt": "kling",
            "model_name": "kling-v2-6",
        },
        credential_ref=credential_ref,
    )

    assert validation.valid is False
    assert any(issue.path == "$.model_name" for issue in validation.issues)
