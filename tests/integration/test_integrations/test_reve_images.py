"""Reve image wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.reve_images import ReveImagesIntegration
from stackos.mcp.errors import IntegrationDownError


def _png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def test_create_image_requests_json_and_persists_base64_output(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    image_bytes = b"reve-png"
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/create",
        json={
            "image": base64.b64encode(image_bytes).decode("ascii"),
            "version": "reve-create@20250915",
            "content_violation": False,
            "request_id": "rsid-create",
            "credits_used": 18,
            "credits_remaining": 982,
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = ReveImagesIntegration(
                payload=b"reve-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.create_image(
                prompt="image prompt",
                aspect_ratio="16:9",
                version="reve-create@20250915",
                test_time_scaling=1,
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content.decode("utf-8"))
    item = result.data["data"][0]
    assert request.headers["authorization"] == "Bearer reve-key"
    assert request.headers["accept"] == "application/json"
    assert body == {
        "prompt": "image prompt",
        "aspect_ratio": "16:9",
        "version": "reve-create@20250915",
        "test_time_scaling": 1,
    }
    assert item["url"].startswith("/generated-assets/reve/reve-image-")
    assert item["source_model"] == "reve-create@20250915"
    assert item["request_id"] == "rsid-create"
    assert item["credits_used"] == 18
    assert "image" not in result.data
    path = tmp_path / item["url"].removeprefix("/generated-assets/")
    assert path.read_bytes() == image_bytes
    assert result.cost_usd == 18 * (10 / 7500)


def test_edit_image_sends_reference_base64_and_sanitizes_request_log(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"source-png")
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/edit",
        json={
            "image": base64.b64encode(b"edited-png").decode("ascii"),
            "version": "reve-edit@20250915",
            "content_violation": False,
            "request_id": "rsid-edit",
            "credits_used": 30,
            "credits_remaining": 970,
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = ReveImagesIntegration(
                payload=b"reve-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.edit_image(
                edit_instruction="make it cinematic",
                reference_image_path=source,
                aspect_ratio="auto",
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content.decode("utf-8"))
    assert body["reference_image"] == base64.b64encode(b"source-png").decode("ascii")
    assert body["edit_instruction"] == "make it cinematic"
    assert body["aspect_ratio"] == "auto"
    assert result.data["data"][0]["url"].startswith("/generated-assets/reve/reve-image-")


def test_remix_image_sends_multiple_references_and_uses_fast_credit_cost(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    first_bytes = _png_header(1, 1)
    second_bytes = _png_header(2, 1)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(first_bytes)
    second.write_bytes(second_bytes)
    httpx_mock.add_response(
        method="POST",
        url="https://api.reve.com/v1/image/remix",
        json={
            "image": base64.b64encode(b"remix-png").decode("ascii"),
            "version": "reve-remix-fast@20251030",
            "content_violation": False,
            "request_id": "rsid-remix",
            "credits_used": 5,
            "credits_remaining": 995,
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = ReveImagesIntegration(
                payload=b"reve-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.remix_image(
                prompt="combine <img>0</img> and <img>1</img>",
                reference_image_paths=[first, second],
                version="reve-remix-fast@20251030",
            )

    result = asyncio.run(go())
    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content.decode("utf-8"))
    assert body["reference_images"] == [
        base64.b64encode(first_bytes).decode("ascii"),
        base64.b64encode(second_bytes).decode("ascii"),
    ]
    assert "aspect_ratio" not in body
    assert result.cost_usd == 5 * (10 / 7500)


def test_remix_rejects_over_pixel_reference_set_before_provider_call(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    source = tmp_path / "too-large.png"
    source.write_bytes(_png_header(8000, 4001))

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = ReveImagesIntegration(
                payload=b"reve-key",
                project_id=project_id,
                http=client,
                asset_dir=tmp_path,
            )
            return await integ.remix_image(
                prompt="use the oversized reference",
                reference_image_paths=[source],
            )

    with pytest.raises(IntegrationDownError) as exc_info:
        asyncio.run(go())

    assert "32 million pixels" in exc_info.value.detail
    assert exc_info.value.data["max_pixels"] == 32_000_000
    assert httpx_mock.get_requests() == []


def test_test_credentials_is_explicitly_non_billable_format_only(project_id: int) -> None:
    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = ReveImagesIntegration(payload=b"reve-key", project_id=project_id, http=client)
            return await integ.test_credentials()

    result = asyncio.run(go())
    assert result["ok"] is True
    assert result["status"] == "format-only"
    assert result["probe_mode"] == "non_billable_format_only"
