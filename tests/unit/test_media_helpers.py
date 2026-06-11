"""Shared media helper tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from stackos.integrations._media import download_generated_media, validate_generated_media_url
from stackos.mcp.errors import IntegrationDownError


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example/video.mp4",
        "https://localhost/video.mp4",
        "https://localhost.localdomain/video.mp4",
        "https://127.0.0.1/video.mp4",
        "https://10.0.0.1/video.mp4",
        "https://172.16.0.1/video.mp4",
        "https://192.168.1.10/video.mp4",
        "https://169.254.169.254/latest/meta-data",
        "https://user:pass@media.example/video.mp4",
    ],
)
def test_validate_generated_media_url_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(IntegrationDownError):
        validate_generated_media_url(url, vendor="test-provider")


def test_validate_generated_media_url_allows_absolute_https_public_url() -> None:
    assert (
        validate_generated_media_url(
            "https://media.example/video.mp4",
            vendor="test-provider",
        )
        == "https://media.example/video.mp4"
    )


def test_download_generated_media_rejects_unsafe_redirect_before_following() -> None:
    class RedirectingIntegration:
        vendor = "test-provider"

        def __init__(self) -> None:
            self.urls: list[str] = []

        async def _request_with_retry(
            self,
            method: str,
            url: str,
            **_: object,
        ) -> httpx.Response:
            self.urls.append(url)
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/internal"},
                request=httpx.Request(method, url),
            )

    integration = RedirectingIntegration()

    with pytest.raises(IntegrationDownError, match="absolute HTTPS"):
        asyncio.run(
            download_generated_media(
                integration,
                "https://media.example/video.mp4",
                fallback_ext="mp4",
            )
        )
    assert integration.urls == ["https://media.example/video.mp4"]
