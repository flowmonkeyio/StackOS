"""BytePlus ModelArk media integration wrapper.

Official docs:

- https://docs.byteplus.com/en/docs/ModelArk/1541523
- https://docs.byteplus.com/en/docs/ModelArk/1330310
- https://docs.byteplus.com/en/docs/ModelArk/1544106

Seedream image generation returns either temporary image URLs or base64 data.
StackOS requests URL responses, downloads them immediately, and returns
generated-assets refs instead of provider URLs.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations._media import (
    data_url_payload,
    download_generated_media,
    sanitize_media_audit_payload,
    write_generated_media,
)
from stackos.mcp.errors import IntegrationDownError


class BytePlusArkIntegration(BaseIntegration):
    """Wrapper for BytePlus ModelArk media endpoints."""

    kind = "byteplus-ark"
    vendor = "byteplus-ark"
    default_qps = 1.0

    DEFAULT_REGION = "ap-southeast-1"
    REGION_BASE_URLS: ClassVar[dict[str, str]] = {
        "ap-southeast-1": "https://ark.ap-southeast.bytepluses.com/api/v3",
        "eu-west-1": "https://ark.eu-west.bytepluses.com/api/v3",
    }
    SEEDANCE_REGION_BASE_URLS: ClassVar[dict[str, str]] = {
        "ap-southeast-1": "https://ark.ap-southeast.bytepluses.com/api/v3",
    }
    DEFAULT_SEEDREAM_MODEL = "seedream-5-0-lite-260128"
    DEFAULT_SEEDANCE_MODEL = "dreamina-seedance-2-0-260128"
    SEEDREAM_MODELS: ClassVar[frozenset[str]] = frozenset(
        {
            "seedream-5-0-lite-260128",
            "seedream-4-5-251128",
            "seedream-4-0-250828",
        }
    )
    SEEDANCE_MODELS: ClassVar[frozenset[str]] = frozenset(
        {
            "dreamina-seedance-2-0-260128",
            "dreamina-seedance-2-0-fast-260128",
            "seedance-1-5-pro-251215",
            "seedance-1-0-pro-250528",
            "seedance-1-0-pro-fast-251015",
        }
    )
    SEEDANCE_2_MODELS: ClassVar[frozenset[str]] = frozenset(
        {"dreamina-seedance-2-0-260128", "dreamina-seedance-2-0-fast-260128"}
    )
    SEEDANCE_AUDIO_MODELS: ClassVar[frozenset[str]] = frozenset(
        {
            "dreamina-seedance-2-0-260128",
            "dreamina-seedance-2-0-fast-260128",
            "seedance-1-5-pro-251215",
        }
    )
    SEEDANCE_DEFAULT_DURATION_MODELS: ClassVar[frozenset[str]] = frozenset(
        {
            "dreamina-seedance-2-0-260128",
            "dreamina-seedance-2-0-fast-260128",
            "seedance-1-5-pro-251215",
        }
    )
    SEEDANCE_FIRST_LAST_UNSUPPORTED_MODELS: ClassVar[frozenset[str]] = frozenset(
        {"seedance-1-0-pro-fast-251015"}
    )
    SEEDANCE_DURATION_RANGES: ClassVar[dict[str, tuple[int, int]]] = {
        "dreamina-seedance-2-0-260128": (4, 15),
        "dreamina-seedance-2-0-fast-260128": (4, 15),
        "seedance-1-5-pro-251215": (4, 12),
        "seedance-1-0-pro-250528": (2, 12),
        "seedance-1-0-pro-fast-251015": (2, 12),
    }
    EU_WEST_MODELS: ClassVar[frozenset[str]] = frozenset({"seedream-5-0-lite-260128"})
    SIZE_KEYWORDS_BY_MODEL: ClassVar[dict[str, frozenset[str]]] = {
        "seedream-5-0-lite-260128": frozenset({"2K", "3K", "4K"}),
        "seedream-4-5-251128": frozenset({"2K", "4K"}),
        "seedream-4-0-250828": frozenset({"1K", "2K", "4K"}),
    }
    SEQUENTIAL_IMAGE_GENERATION_VALUES: ClassVar[frozenset[str]] = frozenset({"disabled", "auto"})
    OUTPUT_FORMATS: ClassVar[frozenset[str]] = frozenset({"jpeg", "png"})
    INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset({"jpg", "jpeg", "png", "webp"})
    SEEDANCE_INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset(
        {"jpg", "jpeg", "png", "webp", "bmp"}
    )
    SEEDANCE_RATIOS: ClassVar[frozenset[str]] = frozenset(
        {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"}
    )
    SEEDANCE_RESOLUTIONS: ClassVar[frozenset[str]] = frozenset({"480p", "720p", "1080p"})
    SEEDANCE_TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"succeeded", "failed", "cancelled", "expired"}
    )
    MAX_INPUT_IMAGES = 14
    MAX_INPUT_IMAGE_BYTES = 30_000_000
    CUSTOM_SIZE_MIN_PIXELS_BY_MODEL: ClassVar[dict[str, int]] = {
        "seedream-5-0-lite-260128": 2560 * 1440,
        "seedream-4-5-251128": 2560 * 1440,
        "seedream-4-0-250828": 1280 * 720,
    }
    CUSTOM_SIZE_MAX_PIXELS = 4096 * 4096
    CUSTOM_SIZE_MIN_RATIO = 1 / 16
    CUSTOM_SIZE_MAX_RATIO = 16
    MIN_INPUT_IMAGE_SIDE = 15
    MAX_INPUT_IMAGE_PIXELS = 6000 * 6000

    _OUTPUT_EXTENSIONS: ClassVar[dict[str, str]] = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }
    _COSTS_USD: ClassVar[dict[str, float]] = {
        "seedream-5-0-lite-260128": 0.035,
        "seedream-4-5-251128": 0.04,
        "seedream-4-0-250828": 0.03,
    }

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
            "Authorization": f"Bearer {self.payload.decode('utf-8')}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        body = kwargs.get("json")
        if not isinstance(body, dict):
            return 0.0
        if op.startswith("video."):
            return 0.0
        return self.estimate_image_cost_usd(model=str(body.get("model") or ""))

    @classmethod
    def estimate_image_cost_usd(
        cls,
        *,
        model: str = DEFAULT_SEEDREAM_MODEL,
        generated_images: int = 1,
    ) -> float:
        price = cls._COSTS_USD.get(model, cls._COSTS_USD[cls.DEFAULT_SEEDREAM_MODEL])
        return max(0, generated_images) * price

    def _extract_actual_cost_usd(
        self,
        op: str,
        *,
        request: Any,
        response: Any,
        estimated: float,
    ) -> float:
        del op
        if not isinstance(response, dict):
            return estimated
        model = (
            str(request.get("model") or self.DEFAULT_SEEDREAM_MODEL)
            if isinstance(request, dict)
            else self.DEFAULT_SEEDREAM_MODEL
        )
        generated = _generated_image_count(response)
        if generated is None:
            return estimated
        return self.estimate_image_cost_usd(model=model, generated_images=generated)

    def _record_call(
        self,
        *,
        op: str,
        request: Any,
        response: Any,
        duration_ms: int,
        error: str | None,
        cost_cents: int,
    ) -> None:
        super()._record_call(
            op=op,
            request=request,
            response=sanitize_media_audit_payload(response),
            duration_ms=duration_ms,
            error=error,
            cost_cents=cost_cents,
        )

    async def generate_image(
        self,
        *,
        prompt: str,
        model: str = DEFAULT_SEEDREAM_MODEL,
        size: str = "2K",
        region: str = DEFAULT_REGION,
        sequential_image_generation: str = "disabled",
        max_images: int | None = None,
        watermark: bool | None = None,
        output_format: str | None = None,
    ) -> IntegrationCallResult:
        return await self._generate_images(
            prompt=prompt,
            input_image_paths=[],
            model=model,
            size=size,
            region=region,
            sequential_image_generation=sequential_image_generation,
            max_images=max_images,
            watermark=watermark,
            output_format=output_format,
        )

    async def edit_image(
        self,
        *,
        prompt: str,
        input_image_paths: list[Path],
        model: str = DEFAULT_SEEDREAM_MODEL,
        size: str = "2K",
        region: str = DEFAULT_REGION,
        sequential_image_generation: str = "disabled",
        max_images: int | None = None,
        watermark: bool | None = None,
        output_format: str | None = None,
    ) -> IntegrationCallResult:
        if not input_image_paths:
            raise IntegrationDownError(
                "BytePlus Seedream image edit requires at least one input image",
                data={"vendor": self.vendor},
            )
        return await self._generate_images(
            prompt=prompt,
            input_image_paths=input_image_paths,
            model=model,
            size=size,
            region=region,
            sequential_image_generation=sequential_image_generation,
            max_images=max_images,
            watermark=watermark,
            output_format=output_format,
        )

    async def _generate_images(
        self,
        *,
        prompt: str,
        input_image_paths: list[Path],
        model: str,
        size: str,
        region: str,
        sequential_image_generation: str,
        max_images: int | None,
        watermark: bool | None,
        output_format: str | None,
    ) -> IntegrationCallResult:
        images = self._image_payloads(input_image_paths)
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": "url",
            "sequential_image_generation": sequential_image_generation,
        }
        if images:
            body["image"] = images[0] if len(images) == 1 else images
        if sequential_image_generation == "auto" and max_images is not None:
            body["sequential_image_generation_options"] = {"max_images": max_images}
        if watermark is not None:
            body["watermark"] = watermark
        if model == self.DEFAULT_SEEDREAM_MODEL and output_format is not None:
            body["output_format"] = output_format

        result = await self.call(
            op="image.edit" if input_image_paths else "image.generate",
            method="POST",
            url=f"{self.base_url_for_region(region)}/images/generations",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "model": model,
                "prompt": prompt,
                "size": size,
                "region": region,
                "input_image_count": len(input_image_paths),
                "input_image_names": [path.name for path in input_image_paths],
                "response_format": "url",
                "sequential_image_generation": sequential_image_generation,
                **(
                    {"sequential_image_generation_options": {"max_images": max_images}}
                    if sequential_image_generation == "auto" and max_images is not None
                    else {}
                ),
                **({"watermark": watermark} if watermark is not None else {}),
                **({"output_format": output_format} if output_format is not None else {}),
            },
        )
        data = await self._persist_response_images(result.data)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    async def generate_seedance_video(
        self,
        *,
        prompt: str | None,
        model: str = DEFAULT_SEEDANCE_MODEL,
        mode: str = "text-to-video",
        region: str = DEFAULT_REGION,
        resolution: str = "720p",
        ratio: str = "16:9",
        duration: int = 5,
        input_image_paths: list[Path] | None = None,
        reference_video_urls: list[str] | None = None,
        reference_audio_urls: list[str] | None = None,
        generate_audio: bool | None = None,
        watermark: bool | None = None,
        seed: int | None = None,
        return_last_frame: bool | None = None,
        safety_identifier: str | None = None,
        priority: int | None = None,
        poll_interval_seconds: float = 10.0,
        poll_timeout_seconds: float = 1800.0,
    ) -> IntegrationCallResult:
        self._ensure_seedance_video_contract(
            model=model,
            mode=mode,
            region=region,
            duration=duration,
            generate_audio=generate_audio,
            priority=priority,
        )
        content = self._seedance_content(
            prompt=prompt,
            mode=mode,
            image_paths=input_image_paths or [],
            reference_video_urls=reference_video_urls or [],
            reference_audio_urls=reference_audio_urls or [],
        )
        body: dict[str, Any] = {
            "model": model,
            "content": content,
            "resolution": resolution,
            "ratio": ratio,
            "duration": duration,
        }
        if generate_audio is not None:
            body["generate_audio"] = generate_audio
        if watermark is not None:
            body["watermark"] = watermark
        if seed is not None:
            body["seed"] = seed
        if return_last_frame is not None:
            body["return_last_frame"] = return_last_frame
        if safety_identifier is not None:
            body["safety_identifier"] = safety_identifier
        if priority is not None:
            body["priority"] = priority
        submitted = await self.call(
            op="video.generate",
            method="POST",
            url=f"{self.seedance_base_url_for_region(region)}/contents/generations/tasks",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "model": model,
                "mode": mode,
                "prompt": prompt,
                "region": region,
                "resolution": resolution,
                "ratio": ratio,
                "duration": duration,
                "input_image_count": len(input_image_paths or []),
                "reference_video_count": len(reference_video_urls or []),
                "reference_audio_count": len(reference_audio_urls or []),
                "generate_audio": generate_audio,
                "watermark": watermark,
                "seed": seed,
                "return_last_frame": return_last_frame,
                "safety_identifier": safety_identifier,
                "priority": priority,
            },
        )
        task_id = self._seedance_task_id(submitted.data)
        poll_result = await self._poll_seedance_task(
            task_id=task_id,
            region=region,
            poll_interval_seconds=poll_interval_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
        )
        persisted = await self._persist_seedance_video_response(
            poll_result.data,
            task_id=task_id,
            mode=mode,
        )
        return IntegrationCallResult(
            data=persisted,
            cost_usd=submitted.cost_usd + poll_result.cost_usd,
            duration_ms=submitted.duration_ms + poll_result.duration_ms,
        )

    def _ensure_seedance_video_contract(
        self,
        *,
        model: str,
        mode: str,
        region: str,
        duration: int,
        generate_audio: bool | None,
        priority: int | None,
    ) -> None:
        if region not in self.SEEDANCE_REGION_BASE_URLS:
            raise IntegrationDownError(
                "BytePlus Seedance video region is not supported",
                data={"vendor": self.vendor, "region": region},
            )
        if mode == "reference-to-video" and model not in self.SEEDANCE_2_MODELS:
            raise IntegrationDownError(
                "BytePlus Seedance reference-to-video requires a Seedance 2.0 model",
                data={"vendor": self.vendor, "model": model, "mode": mode},
            )
        if mode == "first-last-frame" and model in self.SEEDANCE_FIRST_LAST_UNSUPPORTED_MODELS:
            raise IntegrationDownError(
                "BytePlus Seedance 1.0 Pro Fast does not support first-last-frame mode",
                data={"vendor": self.vendor, "model": model, "mode": mode},
            )
        if duration == -1 and model not in self.SEEDANCE_DEFAULT_DURATION_MODELS:
            raise IntegrationDownError(
                "BytePlus Seedance default duration is not supported for this model",
                data={"vendor": self.vendor, "model": model},
            )
        if generate_audio is not None and model not in self.SEEDANCE_AUDIO_MODELS:
            raise IntegrationDownError(
                "BytePlus Seedance generate_audio is not supported for this model",
                data={"vendor": self.vendor, "model": model},
            )
        if priority is not None and model not in self.SEEDANCE_2_MODELS:
            raise IntegrationDownError(
                "BytePlus Seedance priority is only supported for Seedance 2.0 models",
                data={"vendor": self.vendor, "model": model},
            )

    def _seedance_content(
        self,
        *,
        prompt: str | None,
        mode: str,
        image_paths: list[Path],
        reference_video_urls: list[str],
        reference_audio_urls: list[str],
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        if mode == "text-to-video":
            return content
        if mode == "image-to-video":
            if len(image_paths) != 1:
                raise IntegrationDownError(
                    "BytePlus Seedance image-to-video requires exactly one first-frame image",
                    data={"vendor": self.vendor, "count": len(image_paths)},
                )
            content.append(self._seedance_image_content(image_paths[0], role="first_frame"))
        elif mode == "first-last-frame":
            if len(image_paths) != 2:
                raise IntegrationDownError(
                    "BytePlus Seedance first-last-frame mode requires two images",
                    data={"vendor": self.vendor, "count": len(image_paths)},
                )
            content.append(self._seedance_image_content(image_paths[0], role="first_frame"))
            content.append(self._seedance_image_content(image_paths[1], role="last_frame"))
        elif mode == "reference-to-video":
            for path in image_paths:
                content.append(self._seedance_image_content(path, role="reference_image"))
            for url in reference_video_urls:
                content.append(
                    {"type": "video_url", "video_url": {"url": url}, "role": "reference_video"}
                )
            for url in reference_audio_urls:
                content.append(
                    {"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"}
                )
            if len(content) <= (1 if prompt else 0):
                raise IntegrationDownError(
                    "BytePlus Seedance reference-to-video requires at least one media reference",
                    data={"vendor": self.vendor},
                )
        return content

    def _seedance_image_content(self, path: Path, *, role: str) -> dict[str, Any]:
        self.ensure_seedance_image_preflight(path)
        data_url, _, _ = data_url_payload(
            path,
            allowed_suffixes=self.SEEDANCE_INPUT_IMAGE_FORMATS,
            max_bytes=self.MAX_INPUT_IMAGE_BYTES,
            vendor=self.vendor,
        )
        return {"type": "image_url", "image_url": {"url": data_url}, "role": role}

    @classmethod
    def ensure_seedance_image_preflight(cls, path: Path) -> None:
        data_url_payload(
            path,
            allowed_suffixes=cls.SEEDANCE_INPUT_IMAGE_FORMATS,
            max_bytes=cls.MAX_INPUT_IMAGE_BYTES,
            vendor=cls.vendor,
        )

    async def _poll_seedance_task(
        self,
        *,
        task_id: str,
        region: str,
        poll_interval_seconds: float,
        poll_timeout_seconds: float,
    ) -> IntegrationCallResult:
        deadline = monotonic() + poll_timeout_seconds
        poll_result: IntegrationCallResult | None = None
        while monotonic() <= deadline:
            poll_result = await self.call(
                op="video.poll",
                method="GET",
                url=(
                    f"{self.seedance_base_url_for_region(region)}"
                    f"/contents/generations/tasks/{task_id}"
                ),
                headers=self._auth_headers(),
                request_log_body={"task_id": task_id, "region": region},
            )
            if not isinstance(poll_result.data, dict):
                raise IntegrationDownError(
                    "BytePlus Seedance poll returned a non-JSON response",
                    data={"vendor": self.vendor, "task_id": task_id},
                )
            status = str(poll_result.data.get("status") or "")
            if status in self.SEEDANCE_TERMINAL_STATUSES:
                break
            await asyncio.sleep(poll_interval_seconds)
        else:
            raise IntegrationDownError(
                "BytePlus Seedance video generation timed out",
                data={"vendor": self.vendor, "task_id": task_id},
            )
        assert poll_result is not None
        status = str(poll_result.data.get("status") or "")
        if status != "succeeded":
            raise IntegrationDownError(
                f"BytePlus Seedance video generation ended with status {status or 'unknown'}",
                data={
                    "vendor": self.vendor,
                    "task_id": task_id,
                    "status": status,
                    "error": poll_result.data.get("error"),
                },
            )
        return poll_result

    async def _persist_seedance_video_response(
        self,
        data: dict[str, Any],
        *,
        task_id: str,
        mode: str,
    ) -> dict[str, Any]:
        content = data.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("video_url"), str):
            raise IntegrationDownError(
                "BytePlus Seedance task completed without a video_url",
                data={"vendor": self.vendor, "task_id": task_id},
            )
        raw, ext = await download_generated_media(
            self,
            str(content["video_url"]),
            fallback_ext="mp4",
            empty_message="BytePlus Seedance returned an empty video download",
        )
        assert self._asset_dir is not None
        item = {
            **{key: value for key, value in content.items() if key != "video_url"},
            **write_generated_media(
                raw,
                asset_dir=self._asset_dir,
                asset_url_prefix=self._asset_url_prefix,
                subdir="byteplus-ark",
                prefix="byteplus-seedance-video",
                ext=ext,
            ),
            "source_model": str(data.get("model") or self.DEFAULT_SEEDANCE_MODEL),
            "task_id": task_id,
            "mode": mode,
        }
        out: dict[str, Any] = {
            "task_id": task_id,
            "status": "succeeded",
            "model": str(data.get("model") or self.DEFAULT_SEEDANCE_MODEL),
            "data": [item],
        }
        if isinstance(data.get("usage"), dict):
            out["usage"] = data["usage"]
        for key in (
            "seed",
            "resolution",
            "ratio",
            "duration",
            "frames",
            "framespersecond",
            "generate_audio",
            "service_tier",
            "execution_expires_after",
            "priority",
        ):
            if key in data:
                out[key] = data[key]
        return out

    @staticmethod
    def _seedance_task_id(data: Any) -> str:
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            return data["id"]
        raise IntegrationDownError(
            "BytePlus Seedance generation did not return a task id",
            data={"vendor": BytePlusArkIntegration.vendor},
        )

    @classmethod
    def base_url_for_region(cls, region: str) -> str:
        return cls.REGION_BASE_URLS.get(region, cls.REGION_BASE_URLS[cls.DEFAULT_REGION])

    @classmethod
    def seedance_base_url_for_region(cls, region: str) -> str:
        return cls.SEEDANCE_REGION_BASE_URLS.get(
            region,
            cls.SEEDANCE_REGION_BASE_URLS[cls.DEFAULT_REGION],
        )

    def _image_payloads(self, paths: list[Path]) -> list[str]:
        if not paths:
            return []
        return [
            f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"
            for raw, mime_type in self.ensure_image_preflight(paths)
        ]

    @classmethod
    def ensure_image_preflight(cls, paths: list[Path]) -> list[tuple[bytes, str]]:
        if len(paths) > cls.MAX_INPUT_IMAGES:
            raise IntegrationDownError(
                f"BytePlus Seedream accepts at most {cls.MAX_INPUT_IMAGES} reference images",
                data={"vendor": cls.vendor, "count": len(paths)},
            )
        images: list[tuple[bytes, str]] = []
        for path in paths:
            suffix = path.suffix.lower().lstrip(".")
            if suffix not in cls.INPUT_IMAGE_FORMATS:
                raise IntegrationDownError(
                    "BytePlus Seedream input images must be JPEG, PNG, or WEBP",
                    data={"vendor": cls.vendor, "file": path.name},
                )
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise IntegrationDownError(
                    "BytePlus Seedream input image could not be read",
                    data={"vendor": cls.vendor, "file": path.name},
                ) from exc
            if len(raw) > cls.MAX_INPUT_IMAGE_BYTES:
                raise IntegrationDownError(
                    "BytePlus Seedream input images must be at most 30 MB each",
                    data={
                        "vendor": cls.vendor,
                        "file": path.name,
                        "bytes": len(raw),
                        "max_bytes": cls.MAX_INPUT_IMAGE_BYTES,
                    },
                )
            if not _matches_image_signature(raw, suffix):
                raise IntegrationDownError(
                    "BytePlus Seedream input images must be valid JPEG, PNG, or WEBP bytes",
                    data={"vendor": cls.vendor, "file": path.name},
                )
            dimensions = _image_dimensions(raw, suffix)
            if dimensions is None:
                raise IntegrationDownError(
                    "BytePlus Seedream input images must include readable dimensions",
                    data={"vendor": cls.vendor, "file": path.name},
                )
            width, height = dimensions
            pixels = width * height
            ratio = width / height
            if (
                width < cls.MIN_INPUT_IMAGE_SIDE
                or height < cls.MIN_INPUT_IMAGE_SIDE
                or pixels > cls.MAX_INPUT_IMAGE_PIXELS
                or ratio < cls.CUSTOM_SIZE_MIN_RATIO
                or ratio > cls.CUSTOM_SIZE_MAX_RATIO
            ):
                raise IntegrationDownError(
                    (
                        "BytePlus Seedream input images must be at least 15 px per side, "
                        "at most 36M total pixels, and within 1:16-16:1 aspect ratio"
                    ),
                    data={
                        "vendor": cls.vendor,
                        "file": path.name,
                        "width": width,
                        "height": height,
                    },
                )
            images.append((raw, _mime_type_for_suffix(suffix)))
        return images

    @classmethod
    def size_keywords_for_model(cls, model: str) -> frozenset[str]:
        return cls.SIZE_KEYWORDS_BY_MODEL.get(
            model,
            cls.SIZE_KEYWORDS_BY_MODEL[cls.DEFAULT_SEEDREAM_MODEL],
        )

    @classmethod
    def validate_size(cls, size: str, *, model: str = DEFAULT_SEEDREAM_MODEL) -> bool:
        if size in cls.size_keywords_for_model(model):
            return True
        match = re.fullmatch(r"([1-9]\d{1,4})x([1-9]\d{1,4})", size)
        if match is None:
            return False
        width = int(match.group(1))
        height = int(match.group(2))
        pixels = width * height
        ratio = width / height
        min_pixels = cls.CUSTOM_SIZE_MIN_PIXELS_BY_MODEL.get(
            model,
            cls.CUSTOM_SIZE_MIN_PIXELS_BY_MODEL[cls.DEFAULT_SEEDREAM_MODEL],
        )
        return (
            min_pixels <= pixels <= cls.CUSTOM_SIZE_MAX_PIXELS
            and cls.CUSTOM_SIZE_MIN_RATIO <= ratio <= cls.CUSTOM_SIZE_MAX_RATIO
        )

    async def _persist_response_images(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        items = data.get("data")
        if not isinstance(items, list):
            return data
        if self._asset_dir is None and _contains_media_output(items):
            raise IntegrationDownError(
                "BytePlus Seedream image outputs require generated-assets persistence",
                data={"vendor": self.vendor},
            )
        persisted: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                persisted.append(item)
                continue
            if isinstance(item.get("error"), dict):
                persisted.append(item)
                continue
            provider_url = item.get("url")
            if isinstance(provider_url, str) and provider_url:
                persisted.append(await self._persist_provider_url(item, provider_url))
                continue
            raw_b64 = item.get("b64_json")
            if isinstance(raw_b64, str) and raw_b64:
                persisted.append(self._persist_b64_item(item, raw_b64))
                continue
            persisted.append(item)
        clean = {key: value for key, value in data.items() if key != "data"}
        clean["data"] = persisted
        return clean

    async def _persist_provider_url(
        self,
        item: dict[str, Any],
        provider_url: str,
    ) -> dict[str, Any]:
        assert self._asset_dir is not None
        raw, ext = await download_generated_media(
            self,
            provider_url,
            fallback_ext="jpg",
            empty_message="BytePlus Seedream returned an empty image download",
        )
        file_info = write_generated_media(
            raw,
            asset_dir=self._asset_dir,
            asset_url_prefix=self._asset_url_prefix,
            subdir="byteplus-ark",
            prefix="byteplus-ark",
            ext=ext,
        )
        clean = {key: value for key, value in item.items() if key != "url"}
        clean.update(file_info)
        clean["provider_url_persisted"] = True
        return clean

    def _persist_b64_item(self, item: dict[str, Any], raw_b64: str) -> dict[str, Any]:
        try:
            raw = base64.b64decode(raw_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise IntegrationDownError(
                "BytePlus Seedream returned invalid base64 image data",
                data={"vendor": self.vendor},
            ) from exc
        file_info = self._write_image(raw, mime_type=_mime_type_for_raw(raw), source_url="")
        clean = {key: value for key, value in item.items() if key != "b64_json"}
        clean.update(file_info)
        clean["provider_b64_persisted"] = True
        return clean

    def _write_image(
        self,
        raw: bytes,
        *,
        mime_type: str | None,
        source_url: str,
    ) -> dict[str, str]:
        digest = hashlib.sha256(raw).hexdigest()
        file_format = _file_format(mime_type, source_url)
        filename = f"byteplus-ark-{digest[:32]}.{file_format}"
        assert self._asset_dir is not None
        target_dir = self._asset_dir / "byteplus-ark"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        if not path.exists():
            path.write_bytes(raw)
        return {
            "url": f"{self._asset_url_prefix}/byteplus-ark/{filename}",
            "file_format": file_format,
        }

    async def test_credentials(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vendor": self.vendor,
            "status": "format-only",
            "summary": (
                "BytePlus ModelArk does not document a free media credential probe; "
                "StackOS verified credential storage format without making a billable "
                "image request."
            ),
            "probe_mode": "non_billable_format_only",
            "next_action": "Run a BytePlus Seedream image action to verify the live API key.",
        }


def _generated_image_count(response: dict[str, Any]) -> int | None:
    usage = response.get("usage")
    if isinstance(usage, dict):
        raw_generated = usage.get("generated_images")
        if isinstance(raw_generated, int) and not isinstance(raw_generated, bool):
            return max(0, raw_generated)
        if isinstance(raw_generated, str):
            try:
                return max(0, int(raw_generated))
            except ValueError:
                pass
    items = response.get("data")
    if isinstance(items, list):
        count = sum(
            1
            for item in items
            if isinstance(item, dict)
            and (isinstance(item.get("url"), str) or isinstance(item.get("b64_json"), str))
        )
        if count > 0:
            return count
    return None


def _mime_type_for_suffix(suffix: str) -> str:
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "webp":
        return "image/webp"
    return "image/png"


def _mime_type_for_raw(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _matches_image_signature(raw: bytes, suffix: str) -> bool:
    if suffix in {"jpg", "jpeg"}:
        return raw.startswith(b"\xff\xd8\xff")
    if suffix == "png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == "webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def _image_dimensions(raw: bytes, suffix: str) -> tuple[int, int] | None:
    if suffix == "png":
        return _png_dimensions(raw)
    if suffix in {"jpg", "jpeg"}:
        return _jpeg_dimensions(raw)
    if suffix == "webp":
        return _webp_dimensions(raw)
    return None


def _png_dimensions(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 24 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if raw[12:16] != b"IHDR":
        return None
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def _jpeg_dimensions(raw: bytes) -> tuple[int, int] | None:
    if not raw.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset + 3 < len(raw):
        if raw[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        if offset >= len(raw):
            return None
        marker = raw[offset]
        offset += 1
        if marker in {0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9}:
            continue
        if offset + 2 > len(raw):
            return None
        segment_length = int.from_bytes(raw[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(raw):
            return None
        if marker in sof_markers:
            if segment_length < 7:
                return None
            height = int.from_bytes(raw[offset + 3 : offset + 5], "big")
            width = int.from_bytes(raw[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _webp_dimensions(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 20 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return None
    offset = 12
    while offset + 8 <= len(raw):
        chunk_type = raw[offset : offset + 4]
        chunk_size = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        data_offset = offset + 8
        data_end = data_offset + chunk_size
        if data_end > len(raw):
            return None
        data = raw[data_offset:data_end]
        if chunk_type == b"VP8X" and len(data) >= 10:
            width = 1 + int.from_bytes(data[4:7], "little")
            height = 1 + int.from_bytes(data[7:10], "little")
            return width, height
        if chunk_type == b"VP8L" and len(data) >= 5 and data[0] == 0x2F:
            width = 1 + (((data[2] & 0x3F) << 8) | data[1])
            height = 1 + (((data[4] & 0x0F) << 10) | (data[3] << 2) | ((data[2] & 0xC0) >> 6))
            return width, height
        if chunk_type == b"VP8 " and len(data) >= 10 and data[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[6:8], "little") & 0x3FFF
            height = int.from_bytes(data[8:10], "little") & 0x3FFF
            return width, height
        offset = data_end + (chunk_size % 2)
    return None


def _file_format(mime_type: str | None, source_url: str) -> str:
    if isinstance(mime_type, str):
        clean_mime = mime_type.split(";", 1)[0].strip().lower()
        if clean_mime in BytePlusArkIntegration._OUTPUT_EXTENSIONS:
            return BytePlusArkIntegration._OUTPUT_EXTENSIONS[clean_mime]
    suffix = Path(urlparse(source_url).path).suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg", "png", "webp"}:
        return "jpg" if suffix == "jpeg" else suffix
    return "jpg"


def _contains_media_output(items: list[Any]) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("url"), str) and item["url"]:
            return True
        if isinstance(item.get("b64_json"), str) and item["b64_json"]:
            return True
    return False


__all__ = ["BytePlusArkIntegration"]
