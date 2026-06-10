"""Google Veo video generation integration wrapper.

Official docs:

- https://ai.google.dev/gemini-api/docs/video
- https://ai.google.dev/gemini-api/docs/pricing
- https://github.com/googleapis/python-genai

Veo uses the Gemini API long-running ``predictLongRunning`` operation. The
wrapper downloads generated video URIs immediately because provider-hosted
media is temporary and should not be returned to agents.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations._media import (
    download_generated_media,
    image_inline_data,
    write_generated_media,
)
from stackos.mcp.errors import IntegrationDownError


class GoogleVeoIntegration(BaseIntegration):
    """Wrapper for Gemini API Veo video generation."""

    kind = "google-veo"
    vendor = "google-veo"
    default_qps = 1.0

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    API_KEY_HOSTS: ClassVar[frozenset[str]] = frozenset({"generativelanguage.googleapis.com"})
    DEFAULT_MODEL = "veo-3.1-generate-preview"
    MODELS: ClassVar[frozenset[str]] = frozenset(
        {
            "veo-3.1-generate-preview",
            "veo-3.1-fast-generate-preview",
            "veo-3.1-lite-generate-preview",
        }
    )
    ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset({"16:9", "9:16"})
    RESOLUTIONS: ClassVar[frozenset[str]] = frozenset({"720p", "1080p", "4k"})
    PERSON_GENERATION_VALUES: ClassVar[frozenset[str]] = frozenset({"allow_all", "allow_adult"})
    INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset({"jpg", "jpeg", "png"})
    MAX_INPUT_IMAGE_BYTES = 20_000_000
    TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset({"done"})

    def __init__(
        self,
        *,
        payload: bytes,
        project_id: int,
        http: httpx.AsyncClient,
        asset_dir: Path | None = None,
        asset_url_prefix: str = "/generated-assets",
        **kwargs: Any,
    ) -> None:
        super().__init__(payload=payload, project_id=project_id, http=http, **kwargs)
        self._asset_dir = asset_dir
        self._asset_url_prefix = asset_url_prefix.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self.payload.decode("utf-8"),
            "Content-Type": "application/json",
        }

    async def generate_video(
        self,
        *,
        prompt: str,
        model: str = DEFAULT_MODEL,
        mode: str = "text-to-video",
        duration_seconds: int | None = None,
        aspect_ratio: str = "16:9",
        resolution: str | None = None,
        input_image_path: Path | None = None,
        last_frame_path: Path | None = None,
        enhance_prompt: bool | None = None,
        person_generation: str | None = None,
        seed: int | None = None,
        poll_interval_seconds: float = 10.0,
        poll_timeout_seconds: float = 1800.0,
    ) -> IntegrationCallResult:
        instance: dict[str, Any] = {"prompt": prompt}
        if mode in {"image-to-video", "first-last-frame"}:
            if input_image_path is None:
                raise IntegrationDownError(
                    "Google Veo image modes require an input image",
                    data={"vendor": self.vendor, "model": model, "mode": mode},
                )
            instance["image"] = image_inline_data(
                input_image_path,
                allowed_suffixes=self.INPUT_IMAGE_FORMATS,
                max_bytes=self.MAX_INPUT_IMAGE_BYTES,
                vendor=self.vendor,
            )
        if mode == "first-last-frame":
            if last_frame_path is None:
                raise IntegrationDownError(
                    "Google Veo first-last-frame mode requires a last frame image",
                    data={"vendor": self.vendor, "model": model},
                )
            instance["lastFrame"] = image_inline_data(
                last_frame_path,
                allowed_suffixes=self.INPUT_IMAGE_FORMATS,
                max_bytes=self.MAX_INPUT_IMAGE_BYTES,
                vendor=self.vendor,
            )
        parameters: dict[str, Any] = {"aspectRatio": aspect_ratio}
        if duration_seconds is not None:
            parameters["durationSeconds"] = duration_seconds
        if resolution is not None:
            parameters["resolution"] = resolution
        if enhance_prompt is not None:
            parameters["enhancePrompt"] = enhance_prompt
        if person_generation is not None:
            parameters["personGeneration"] = person_generation
        if seed is not None:
            parameters["seed"] = seed
        body = {
            "instances": [instance],
            "parameters": parameters,
        }
        submitted = await self.call(
            op="video.generate",
            method="POST",
            url=f"{self.BASE_URL}/models/{model}:predictLongRunning",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "model": model,
                "prompt": prompt,
                "mode": mode,
                "duration_seconds": duration_seconds,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "input_image_name": input_image_path.name if input_image_path else None,
                "last_frame_name": last_frame_path.name if last_frame_path else None,
                "enhance_prompt": enhance_prompt,
                "person_generation": person_generation,
                "seed": seed,
            },
        )
        operation_name = self._operation_name(submitted.data)
        poll_result = await self._poll_operation(
            operation_name=operation_name,
            poll_interval_seconds=poll_interval_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
        )
        persisted = await self._persist_video_response(
            poll_result.data,
            operation_name=operation_name,
            model=model,
        )
        return IntegrationCallResult(
            data=persisted,
            cost_usd=submitted.cost_usd + poll_result.cost_usd,
            duration_ms=submitted.duration_ms + poll_result.duration_ms,
        )

    async def _poll_operation(
        self,
        *,
        operation_name: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> IntegrationCallResult:
        deadline = monotonic() + poll_timeout_seconds
        poll_result: IntegrationCallResult | None = None
        while monotonic() <= deadline:
            operation_url = self._operation_url(operation_name)
            poll_result = await self.call(
                op="video.poll",
                method="GET",
                url=operation_url,
                headers=self._api_key_headers_for_url(operation_url),
                request_log_body={"operation_name": operation_name},
            )
            if not isinstance(poll_result.data, dict):
                raise IntegrationDownError(
                    "Google Veo poll returned a non-JSON response",
                    data={"vendor": self.vendor, "operation_name": operation_name},
                )
            if isinstance(poll_result.data.get("error"), dict):
                raise IntegrationDownError(
                    "Google Veo operation failed",
                    data={
                        "vendor": self.vendor,
                        "operation_name": operation_name,
                        "error": poll_result.data["error"],
                    },
                )
            if poll_result.data.get("done") is True:
                return poll_result
            await asyncio.sleep(poll_interval_seconds)
        raise IntegrationDownError(
            "Google Veo video generation timed out",
            data={"vendor": self.vendor, "operation_name": operation_name},
        )

    async def _persist_video_response(
        self,
        data: dict[str, Any],
        *,
        operation_name: str,
        model: str,
    ) -> dict[str, Any]:
        if self._asset_dir is None:
            return data
        samples = _generated_samples(data)
        if not samples:
            raise IntegrationDownError(
                "Google Veo operation completed without generated videos",
                data={"vendor": self.vendor, "operation_name": operation_name},
            )
        persisted: list[dict[str, Any]] = []
        for index, sample in enumerate(samples):
            video = sample.get("video") if isinstance(sample, dict) else None
            if not isinstance(video, dict) or not isinstance(video.get("uri"), str):
                raise IntegrationDownError(
                    "Google Veo generated sample did not include a video URI",
                    data={"vendor": self.vendor, "operation_name": operation_name, "index": index},
                )
            video_uri = str(video["uri"])
            raw, ext = await download_generated_media(
                self,
                video_uri,
                fallback_ext="mp4",
                headers=self._api_key_headers_for_url(video_uri),
                empty_message="Google Veo returned an empty video download",
            )
            item = {
                **{key: value for key, value in video.items() if key != "uri"},
                **write_generated_media(
                    raw,
                    asset_dir=self._asset_dir,
                    asset_url_prefix=self._asset_url_prefix,
                    subdir="google-veo",
                    prefix="google-veo-video",
                    ext=ext,
                ),
                "source_model": model,
                "operation_name": operation_name,
                "sample_index": index,
            }
            persisted.append(item)
        return {
            "operation_name": operation_name,
            "status": "done",
            "model": model,
            "data": persisted,
        }

    @staticmethod
    def _operation_name(data: Any) -> str:
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            return data["name"]
        raise IntegrationDownError(
            "Google Veo generation did not return an operation name",
            data={"vendor": GoogleVeoIntegration.vendor},
        )

    @classmethod
    def _operation_url(cls, operation_name: str) -> str:
        if operation_name.startswith("http://") or operation_name.startswith("https://"):
            if not cls._is_google_api_url(operation_name):
                raise IntegrationDownError(
                    "Google Veo operation URL was not a Google API URL",
                    data={"vendor": cls.vendor},
                )
            return operation_name
        return f"{cls.BASE_URL}/{operation_name.lstrip('/')}"

    @classmethod
    def _is_google_api_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname in cls.API_KEY_HOSTS

    def _api_key_headers_for_url(self, url: str) -> dict[str, str] | None:
        if self._is_google_api_url(url):
            return {"x-goog-api-key": self.payload.decode("utf-8")}
        return None

    async def test_credentials(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vendor": self.vendor,
            "status": "format-only",
            "summary": (
                "Gemini API does not document a free Veo credential probe; StackOS "
                "verified credential storage format without making a billable video request."
            ),
            "probe_mode": "non_billable_format_only",
            "next_action": "Run a Google Veo video action to verify live model access.",
        }


def _generated_samples(data: dict[str, Any]) -> list[Any]:
    response = data.get("response")
    if not isinstance(response, dict):
        return []
    generate_video_response = response.get("generateVideoResponse")
    if isinstance(generate_video_response, dict):
        samples = generate_video_response.get("generatedSamples")
        if isinstance(samples, list):
            return samples
    generated_videos = response.get("generatedVideos")
    if isinstance(generated_videos, list):
        return generated_videos
    return []


__all__ = ["GoogleVeoIntegration"]
