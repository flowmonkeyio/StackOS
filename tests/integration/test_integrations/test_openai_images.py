"""OpenAI Images wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock
from stackos_connectors.connectors.openai_images.integration import OpenAIImagesIntegration
from stackos_connectors.errors import IntegrationDownError

from stackos.actions.openai_images import _host_media_cost


def test_generate_uses_gpt_image_defaults(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/generations",
        json={"data": [{"url": "https://example.com/image.png"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(payload=b"sk-openai", http=client)
            return await integ.generate(
                prompt="image prompt",
                n=1,
                size="1536x1024",
                quality="medium",
                model="gpt-image-2",
                output_format="webp",
            )

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
    assert (
        _host_media_cost(
            "image.generate",
            {
                "prompt": "image prompt",
                "n": 1,
                "size": "1536x1024",
                "quality": "medium",
                "model": "gpt-image-2",
                "output_format": "webp",
            },
            result.data,
        )
        == 0.041
    )


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
                http=client,
                output_dir=tmp_path / "openai-images",
            )
            return await integ.generate(
                prompt="image prompt",
                n=1,
                size="1536x1024",
                quality="medium",
                model="gpt-image-2",
                output_format="webp",
            )

    result = asyncio.run(go())
    item = result.data["data"][0]
    assert "b64_json" not in item
    assert item["path"].startswith(str(tmp_path / "openai-images/openai-"))
    path = Path(item["path"])
    assert path.read_bytes() == image_bytes


def test_generate_rejects_prompt_over_openai_limit(project_id: int) -> None:
    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(payload=b"sk-openai", http=client)
            return await integ.generate(
                prompt="x" * 32_001,
                size="1536x1024",
                quality="medium",
                n=1,
                model="gpt-image-2",
                output_format="webp",
            )

    with pytest.raises(IntegrationDownError, match="at most 32000 characters"):
        asyncio.run(go())


def test_edit_uploads_input_images_as_multipart_files(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    source = tmp_path / "product.png"
    source.write_bytes(b"fake-png-bytes")
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/edits",
        json={"data": [{"url": "https://example.com/edited.png"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(payload=b"sk-openai", http=client)
            return await integ.edit(
                prompt="place the product on a marble table",
                input_image_paths=[source],
                input_fidelity="high",
                size="auto",
                quality="medium",
                n=1,
                model="gpt-image-2",
                output_format="webp",
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == "Bearer sk-openai"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    content = request.content
    assert b'name="prompt"' in content
    assert b"place the product on a marble table" in content
    assert b'name="n"' in content
    assert b"gpt-image-2" in content
    assert b'name="size"' in content
    assert b"auto" in content
    assert b'name="quality"' in content
    assert b"medium" in content
    assert b'name="output_format"' in content
    assert b"webp" in content
    assert b'name="image"; filename="product.png"' in content
    assert b"Content-Type: image/png" in content
    assert b"fake-png-bytes" in content
    assert b"data:image/png;base64" not in content
    assert result.data["data"][0]["url"].endswith("edited.png")


@pytest.mark.parametrize("model", ["gpt-image-1.5", "gpt-image-1-mini"])
def test_edit_forwards_input_fidelity_for_supported_models(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
    model: str,
) -> None:
    source = tmp_path / "product.webp"
    source.write_bytes(b"fake-webp-bytes")
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/edits",
        json={"data": [{"url": "https://example.com/edited.webp"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(payload=b"sk-openai", http=client)
            return await integ.edit(
                prompt="same product, beach scene",
                input_image_paths=[source],
                model=model,
                input_fidelity="high",
                size="auto",
                quality="medium",
                n=1,
                output_format="webp",
            )

    asyncio.run(go())
    content = httpx_mock.get_requests()[0].content
    assert model.encode("utf-8") in content
    assert b'name="input_fidelity"' in content
    assert b"high" in content


def test_edit_persists_gpt_image_base64(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    source = tmp_path / "product.jpg"
    source.write_bytes(b"fake-jpg-bytes")
    edited_bytes = b"fake-edited-bytes"
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/edits",
        json={"data": [{"b64_json": base64.b64encode(edited_bytes).decode("ascii")}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(
                payload=b"sk-openai",
                http=client,
                output_dir=tmp_path / "openai-images",
            )
            return await integ.edit(
                prompt="same product, studio softbox",
                input_image_paths=[source],
                size="auto",
                quality="medium",
                n=1,
                model="gpt-image-2",
                output_format="webp",
            )

    result = asyncio.run(go())
    item = result.data["data"][0]
    assert "b64_json" not in item
    assert item["path"].startswith(str(tmp_path / "openai-images/openai-"))
    path = Path(item["path"])
    assert path.read_bytes() == edited_bytes


def test_edit_rejects_input_image_over_openai_file_limit(
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(OpenAIImagesIntegration, "MAX_EDIT_INPUT_IMAGE_BYTES", 8)
    source = tmp_path / "oversized.png"
    source.write_bytes(b"too-large")

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(payload=b"sk-openai", http=client)
            return await integ.edit(
                prompt="edit prompt",
                input_image_paths=[source],
                size="auto",
                quality="medium",
                n=1,
                model="gpt-image-2",
                output_format="webp",
            )

    with pytest.raises(IntegrationDownError, match="at most 50 MB"):
        asyncio.run(go())


def test_generate_rejects_invalid_base64_provider_output(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.openai.com/v1/images/generations",
        json={"data": [{"b64_json": "not valid base64"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(
                payload=b"sk-openai",
                http=client,
                output_dir=tmp_path / "openai-images",
            )
            return await integ.generate(
                prompt="image prompt",
                size="1536x1024",
                quality="medium",
                n=1,
                model="gpt-image-2",
                output_format="webp",
            )

    with pytest.raises(IntegrationDownError, match="invalid base64 image data"):
        asyncio.run(go())


def test_test_credentials_lists_models(httpx_mock: HTTPXMock, project_id: int) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://api.openai.com/v1/models",
        json={"data": [{"id": "gpt-4"}, {"id": "dall-e-3"}]},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = OpenAIImagesIntegration(payload=b"sk-openai", http=client)
            return await integ.test_credentials()

    out = asyncio.run(go())
    assert out["ok"] is True
    assert out["models_count"] == 2


def test_host_default_choices_and_generated_asset_projection(host_media_projection):
    from stackos.actions.openai_images import OpenAIImagesActionConnector

    result, native_data = asyncio.run(
        host_media_projection(
            OpenAIImagesActionConnector(),
            provider="openai-images",
            operation="image.generate",
            data={"prompt": "fixture"},
            output_subdir="openai-images",
        )
    )
    assert native_data["size"] == "1536x1024"
    assert native_data["quality"] == "medium"
    assert native_data["n"] == 1
    assert native_data["model"] == "gpt-image-2"
    assert native_data["output_format"] == "webp"
    assert result.metadata_json["vendor"] == "openai-images"
