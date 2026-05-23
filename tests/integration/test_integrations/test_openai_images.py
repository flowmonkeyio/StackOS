"""OpenAI Images wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
from pytest_httpx import HTTPXMock

from stackos.integrations.openai_images import OpenAIImagesIntegration


def test_generate_uses_gpt_image_defaults(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/generations",
        json={"data": [{"url": "https://example.com/image.png"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(
                payload=b"sk-openai", project_id=project_id, http=client
            )
            return await integ.generate(prompt="image prompt", n=1)

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == "Bearer sk-openai"
    body = json.loads(request.content.decode("utf-8"))
    assert body == {
        "prompt": "image prompt",
        "n": 1,
        "model": "gpt-image-2",
        "size": "1536x1024",
        "quality": "medium",
        "output_format": "webp",
    }
    assert result.data["data"][0]["url"].endswith("image.png")
    assert result.cost_usd == 0.041


def test_generate_persists_gpt_image_base64(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    image_bytes = b"fake-webp-bytes"
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/generations",
        json={"data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(
                payload=b"sk-openai",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.generate(prompt="image prompt", n=1)

    result = asyncio.run(go())
    item = result.data["data"][0]
    assert "b64_json" not in item
    assert item["url"].startswith("/generated-assets/openai-images/openai-")
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == image_bytes


def test_test_credentials_lists_models(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://api.openai.com/v1/models",
        json={"data": [{"id": "gpt-4"}, {"id": "dall-e-3"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(
                payload=b"sk-openai", project_id=project_id, http=client
            )
            return await integ.test_credentials()

    out = asyncio.run(go())
    assert out["ok"] is True
    assert out["models_count"] == 2
