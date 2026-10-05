"""BytePlus ModelArk integration wrapper tests."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.byteplus.ark import BytePlusArkIntegration

from stackos.actions.byteplus_seedream import _host_media_cost, _MediaPricing


def _png_bytes(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def _webp_bytes(width: int, height: int) -> bytes:
    payload = (
        b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    )
    return b"RIFF" + (22).to_bytes(4, "little") + b"WEBPVP8X" + (10).to_bytes(4, "little") + payload


PNG_BYTES = _png_bytes(32, 32)
WEBP_BYTES = _webp_bytes(32, 32)


def test_generate_image_uses_modelark_json_and_persists_urls(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    provider_url = "https://ark-output.byteplus.test/generated-1.jpeg?token=secret"
    provider_url_2 = "https://ark-output.byteplus.test/generated-2.jpeg?token=secret"
    httpx_mock.add_response(
        method="POST",
        url="https://ark.eu-west.bytepluses.com/api/v3/images/generations",
        json={
            "model": "seedream-5-0-lite-260128",
            "created": 1780000000,
            "data": [
                {"url": provider_url, "size": "2048x2048"},
                {"url": provider_url_2, "size": "2048x2048"},
            ],
            "usage": {"generated_images": 2, "output_tokens": 32768, "total_tokens": 32768},
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url,
        content=b"byteplus-image-one",
        headers={"content-type": "image/jpeg"},
    )
    httpx_mock.add_response(
        method="GET",
        url=provider_url_2,
        content=b"byteplus-image-two",
        headers={"content-type": "image/jpeg"},
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                http=client,
                output_dir=tmp_path / "byteplus-ark",
            )
            return await integ.generate_image(
                prompt="Editorial product poster",
                region="eu-west-1",
                size="2K",
                output_format="png",
                watermark=False,
                model="seedream-5-0-lite-260128",
                sequential_image_generation="disabled",
            )

    result = asyncio.run(go())
    post_request = httpx_mock.get_requests()[0]
    body = json.loads(post_request.content.decode("utf-8"))
    item = result.data["data"][0]
    item_2 = result.data["data"][1]
    rendered = json.dumps(result.data)

    assert post_request.headers["Authorization"] == "Bearer ark-key"
    assert body == {
        "model": "seedream-5-0-lite-260128",
        "prompt": "Editorial product poster",
        "size": "2K",
        "response_format": "url",
        "sequential_image_generation": "disabled",
        "watermark": False,
        "output_format": "png",
    }
    assert item["path"].startswith(str(tmp_path / "byteplus-ark/byteplus-ark-"))
    assert item["file_format"] == "jpg"
    assert item["provider_url_persisted"] is True
    assert item_2["path"].startswith(str(tmp_path / "byteplus-ark/byteplus-ark-"))
    assert provider_url not in rendered
    assert provider_url_2 not in rendered
    assert (Path(item["path"])).read_bytes() == (b"byteplus-image-one")
    assert (Path(item_2["path"])).read_bytes() == (b"byteplus-image-two")
    assert (
        _host_media_cost(
            "image.generate",
            {
                "prompt": "Editorial product poster",
                "region": "eu-west-1",
                "size": "2K",
                "output_format": "png",
                "watermark": False,
                "model": "seedream-5-0-lite-260128",
                "sequential_image_generation": "disabled",
            },
            result.data,
        )
        == 0.07
    )


def test_edit_image_uploads_generated_asset_data_url_and_persists_base64(
    httpx_mock: HTTPXMock,
    project_id: int,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(PNG_BYTES)
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        json={
            "model": "seedream-4-0-250828",
            "data": [
                {
                    "b64_json": base64.b64encode(b"byteplus-b64-image").decode("ascii"),
                    "size": "2048x2048",
                }
            ],
            "usage": {"generated_images": 1},
        },
    )

    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                http=client,
                output_dir=tmp_path / "byteplus-ark",
            )
            return await integ.edit_image(
                prompt="Keep the object, change the backdrop",
                input_image_paths=[source],
                model="seedream-4-0-250828",
                size="2048x2048",
                region="ap-southeast-1",
                sequential_image_generation="disabled",
            )

    result = asyncio.run(go())
    post_request = httpx_mock.get_requests()[0]
    body = json.loads(post_request.content.decode("utf-8"))
    item = result.data["data"][0]
    rendered = json.dumps(result.data)

    assert body["image"].startswith("data:image/png;base64,")
    assert body["response_format"] == "url"
    assert item["path"].startswith(str(tmp_path / "byteplus-ark/byteplus-ark-"))
    assert item["provider_b64_persisted"] is True
    assert "b64_json" not in rendered
    assert (Path(item["path"])).read_bytes() == (b"byteplus-b64-image")
    assert (
        _host_media_cost(
            "image.edit",
            {
                "prompt": "Keep the object, change the backdrop",
                "input_image_paths": [source],
                "model": "seedream-4-0-250828",
                "size": "2048x2048",
                "region": "ap-southeast-1",
                "sequential_image_generation": "disabled",
            },
            result.data,
        )
        == 0.03
    )


def test_generated_media_requires_asset_dir_to_prevent_temporary_url_leak(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    provider_url = "https://ark-output.byteplus.test/generated.jpeg?token=secret"
    httpx_mock.add_response(
        method="POST",
        url="https://ark.ap-southeast.bytepluses.com/api/v3/images/generations",
        json={"data": [{"url": provider_url}], "usage": {"generated_images": 1}},
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                http=client,
            )
            await integ.generate_image(
                prompt="poster",
                model="seedream-5-0-lite-260128",
                size="2K",
                region="ap-southeast-1",
                sequential_image_generation="disabled",
            )

    with pytest.raises(IntegrationDownError) as exc:
        asyncio.run(go())

    assert "require output_dir" in exc.value.detail
    assert len(httpx_mock.get_requests()) == 1


def test_image_preflight_rejects_invalid_inputs(tmp_path: Path) -> None:
    unsupported = tmp_path / "source.gif"
    unsupported.write_bytes(b"GIF89a")
    fake_webp = tmp_path / "fake.webp"
    fake_webp.write_bytes(b"not-webp")
    too_small = tmp_path / "tiny.png"
    too_small.write_bytes(_png_bytes(10, 32))
    valid_wide = tmp_path / "wide.png"
    valid_wide.write_bytes(_png_bytes(8000, 500))
    too_many_pixels = tmp_path / "huge-pixels.png"
    too_many_pixels.write_bytes(_png_bytes(6001, 6001))
    too_large = tmp_path / "large.png"
    too_large.write_bytes(b"x" * 30_000_001)

    with pytest.raises(IntegrationDownError) as format_exc:
        BytePlusArkIntegration.ensure_image_preflight([unsupported])
    with pytest.raises(IntegrationDownError) as signature_exc:
        BytePlusArkIntegration.ensure_image_preflight([fake_webp])
    with pytest.raises(IntegrationDownError) as dimensions_exc:
        BytePlusArkIntegration.ensure_image_preflight([too_small])
    with pytest.raises(IntegrationDownError) as pixels_exc:
        BytePlusArkIntegration.ensure_image_preflight([too_many_pixels])
    with pytest.raises(IntegrationDownError) as size_exc:
        BytePlusArkIntegration.ensure_image_preflight([too_large])

    assert BytePlusArkIntegration.ensure_image_preflight([valid_wide])[0][1] == "image/png"
    assert "JPEG, PNG, or WEBP" in format_exc.value.detail
    assert "valid JPEG, PNG, or WEBP bytes" in signature_exc.value.detail
    assert "at least 15 px per side" in dimensions_exc.value.detail
    assert "at most 36M total pixels" in pixels_exc.value.detail
    assert "at most 30 MB" in size_exc.value.detail


def test_size_validation_matches_documented_limits() -> None:
    assert BytePlusArkIntegration.validate_size("2K") is True
    assert BytePlusArkIntegration.validate_size("4096x4096") is True
    assert BytePlusArkIntegration.validate_size("1500x1500") is False
    assert BytePlusArkIntegration.validate_size("1K", model="seedream-4-0-250828") is True
    assert BytePlusArkIntegration.validate_size("1500x1500", model="seedream-4-0-250828") is True
    assert BytePlusArkIntegration.validate_size("3K", model="seedream-4-5-251128") is False
    assert BytePlusArkIntegration.validate_size("30000x10") is False
    assert _MediaPricing.estimate_image_cost_usd(generated_images=0) == 0.0


def test_test_credentials_is_explicitly_non_billable_format_only(project_id: int) -> None:
    async def go() -> Any:
        async with httpx.AsyncClient() as client:
            integ = BytePlusArkIntegration(
                payload=b"ark-key",
                http=client,
            )
            return await integ.test_credentials()

    result = asyncio.run(go())
    assert result["ok"] is True
    assert result["status"] == "format-only"
    assert result["probe_mode"] == "non_billable_format_only"


def test_host_default_choices_and_generated_asset_projection(host_media_projection):
    from stackos.actions.byteplus_seedream import BytePlusSeedreamImageActionConnector

    result, native_data = asyncio.run(
        host_media_projection(
            BytePlusSeedreamImageActionConnector(),
            provider="byteplus-seedream",
            operation="image.generate",
            data={"prompt": "fixture"},
            output_subdir="byteplus-ark",
        )
    )
    assert native_data["model"] == "seedream-5-0-lite-260128"
    assert native_data["size"] == "2K"
    assert native_data["region"] == "ap-southeast-1"
    assert native_data["sequential_image_generation"] == "disabled"
    assert result.metadata_json["vendor"] == "byteplus-ark"
