"""xAI Imagine image and video integration wrapper.

Official docs:

- https://docs.x.ai/developers/model-capabilities/images/generation
- https://docs.x.ai/developers/model-capabilities/images/editing
- https://docs.x.ai/developers/model-capabilities/images/multi-image-editing
- https://docs.x.ai/developers/model-capabilities/video/generation
- https://docs.x.ai/developers/model-capabilities/video/image-to-video
- https://docs.x.ai/developers/model-capabilities/video/reference-to-video
- https://docs.x.ai/developers/models
- https://docs.x.ai/developers/pricing
- https://docs.x.ai/developers/cost-tracking

The wrapper persists generated media immediately because xAI-hosted media URLs
are temporary. Agents only receive generated-assets URLs/artifact refs.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations._media import download_generated_media, sanitize_media_audit_payload
from stackos.mcp.errors import IntegrationDownError


class XAIImagineIntegration(BaseIntegration):
    """Wrapper for xAI Imagine image and video endpoints."""

    kind = "xai-imagine"
    vendor = "xai-imagine"
    default_qps = 3.0

    BASE_URL = "https://api.x.ai/v1"
    IMAGE_MODEL = "grok-imagine-image-quality"
    VIDEO_MODEL = "grok-imagine-video"
    IMAGE_INPUT_MAX_BYTES = 20 * 1024 * 1024

    IMAGE_ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset(
        {
            "1:1",
            "16:9",
            "9:16",
            "4:3",
            "3:4",
            "3:2",
            "2:3",
            "2:1",
            "1:2",
            "19.5:9",
            "9:19.5",
            "20:9",
            "9:20",
            "auto",
        }
    )
    IMAGE_RESOLUTIONS: ClassVar[frozenset[str]] = frozenset({"1k", "2k"})
    VIDEO_ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset(
        {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
    )
    VIDEO_RESOLUTIONS: ClassVar[frozenset[str]] = frozenset({"480p", "720p"})
    VIDEO_MODELS: ClassVar[frozenset[str]] = frozenset({VIDEO_MODEL})
    TERMINAL_VIDEO_STATUSES: ClassVar[frozenset[str]] = frozenset({"done", "failed", "expired"})
    # Official pricing refs:
    # https://docs.x.ai/developers/pricing
    #
    # Values are pre-call budget guardrails. Provider invoices remain the
    # billing source of truth.
    _IMAGE_INPUT_COST_PER_IMAGE_USD = 0.01
    _IMAGE_OUTPUT_COSTS_USD: ClassVar[dict[str, float]] = {"1k": 0.05, "2k": 0.07}
    _VIDEO_INPUT_IMAGE_COSTS_USD: ClassVar[dict[str, float]] = {
        VIDEO_MODEL: 0.002,
    }
    _VIDEO_OUTPUT_COSTS_PER_SECOND_USD: ClassVar[dict[str, dict[str, float]]] = {
        VIDEO_MODEL: {
            "480p": 0.05,
            "720p": 0.07,
        },
    }
    _USD_TICKS_PER_DOLLAR = 10_000_000_000

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
        }

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

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        body = kwargs.get("json")
        if not isinstance(body, dict):
            return 0.0
        if op in {"image.generate", "image.edit"}:
            n = body.get("n", 1)
            input_images = 0
            if isinstance(body.get("image"), dict):
                input_images = 1
            elif isinstance(body.get("images"), list):
                input_images = len(body["images"])
            return self.estimate_image_cost_usd(
                n=n if isinstance(n, int) else 1,
                resolution=str(body.get("resolution", "1k")),
                input_images=input_images,
            )
        if op == "video.generate":
            duration = body.get("duration", 5)
            input_images = 0
            if isinstance(body.get("image"), dict):
                input_images = 1
            elif isinstance(body.get("reference_images"), list):
                input_images = len(body["reference_images"])
            return self.estimate_video_cost_usd(
                seconds=duration if isinstance(duration, int) else 5,
                resolution=str(body.get("resolution", "480p")),
                input_images=input_images,
                model=str(body.get("model", self.VIDEO_MODEL)),
            )
        return 0.0

    @classmethod
    def estimate_image_cost_usd(
        cls,
        *,
        n: int = 1,
        resolution: str = "1k",
        input_images: int = 0,
    ) -> float:
        output_cost = cls._IMAGE_OUTPUT_COSTS_USD.get(resolution, cls._IMAGE_OUTPUT_COSTS_USD["1k"])
        return max(1, n) * output_cost + max(0, input_images) * cls._IMAGE_INPUT_COST_PER_IMAGE_USD

    @classmethod
    def estimate_video_cost_usd(
        cls,
        *,
        seconds: int = 5,
        resolution: str = "480p",
        input_images: int = 0,
        model: str = VIDEO_MODEL,
    ) -> float:
        output_costs = cls._VIDEO_OUTPUT_COSTS_PER_SECOND_USD.get(
            model,
            cls._VIDEO_OUTPUT_COSTS_PER_SECOND_USD[cls.VIDEO_MODEL],
        )
        output_cost = output_costs.get(resolution, output_costs["480p"])
        input_cost = cls._VIDEO_INPUT_IMAGE_COSTS_USD.get(
            model,
            cls._VIDEO_INPUT_IMAGE_COSTS_USD[cls.VIDEO_MODEL],
        )
        return max(1, seconds) * output_cost + max(0, input_images) * input_cost

    def _extract_actual_cost_usd(
        self,
        op: str,
        *,
        request: Any,
        response: Any,
        estimated: float,
    ) -> float:
        del request
        if op == "video.poll":
            return estimated
        actual = self._cost_from_usage_usd(response)
        return estimated if actual is None else actual

    @classmethod
    def _cost_from_usage_usd(cls, response: Any) -> float | None:
        if not isinstance(response, dict):
            return None
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return None
        raw_ticks = usage.get("cost_in_usd_ticks")
        if not isinstance(raw_ticks, int | float | str) or isinstance(raw_ticks, bool):
            return None
        try:
            ticks = int(raw_ticks)
        except (TypeError, ValueError):
            return None
        if ticks < 0:
            return None
        return ticks / cls._USD_TICKS_PER_DOLLAR

    def _reconcile_extra_budget(self, *, actual_cost_usd: float, estimated_cost_usd: float) -> None:
        delta = actual_cost_usd - estimated_cost_usd
        if self._budget_repo is not None and delta > 0:
            self._budget_repo.record_call(
                project_id=self.project_id,
                kind=self.kind,
                cost_usd=delta,
            )

    def _actual_or_estimated_cost_usd(self, *, response: Any, estimated: float) -> float:
        actual = self._cost_from_usage_usd(response)
        if actual is None:
            return estimated
        self._reconcile_extra_budget(actual_cost_usd=actual, estimated_cost_usd=estimated)
        return actual

    async def generate_image(
        self,
        *,
        prompt: str,
        aspect_ratio: str = "auto",
        resolution: str = "1k",
        n: int = 1,
        model: str = IMAGE_MODEL,
    ) -> IntegrationCallResult:
        body = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "n": n,
            "response_format": "b64_json",
        }
        result = await self.call(
            op="image.generate",
            method="POST",
            url=f"{self.BASE_URL}/images/generations",
            json_body=body,
            headers=self._auth_headers(),
        )
        data = await self._persist_image_response(result.data, model=model)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    async def edit_image(
        self,
        *,
        prompt: str,
        input_image_paths: list[Path],
        aspect_ratio: str | None = None,
        resolution: str = "1k",
        model: str = IMAGE_MODEL,
    ) -> IntegrationCallResult:
        if not input_image_paths:
            raise IntegrationDownError(
                "xAI image edit requires at least one input image",
                data={"vendor": self.vendor},
            )
        images = [self._image_payload(path) for path in input_image_paths]
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "resolution": resolution,
        }
        if aspect_ratio is not None and len(images) > 1:
            body["aspect_ratio"] = aspect_ratio
        if len(images) == 1:
            body["image"] = images[0]
        else:
            body["images"] = images
        result = await self.call(
            op="image.edit",
            method="POST",
            url=f"{self.BASE_URL}/images/edits",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "model": model,
                "prompt": prompt,
                "resolution": resolution,
                **({"aspect_ratio": aspect_ratio} if "aspect_ratio" in body else {}),
                "input_image_count": len(images),
                "input_image_names": [path.name for path in input_image_paths],
            },
        )
        data = await self._persist_image_response(result.data, model=model)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    async def generate_video(
        self,
        *,
        prompt: str,
        duration: int = 5,
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        model: str = VIDEO_MODEL,
        image_path: Path | None = None,
        reference_image_paths: list[Path] | None = None,
        poll_interval_seconds: float = 5.0,
        poll_timeout_seconds: float = 900.0,
    ) -> IntegrationCallResult:
        if image_path is not None and reference_image_paths:
            raise IntegrationDownError(
                "xAI video generation accepts image-to-video or reference-to-video, not both",
                data={"vendor": self.vendor, "model": model},
            )
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if image_path is not None:
            body["image"] = self._image_payload(image_path)
        if reference_image_paths:
            body["reference_images"] = [self._image_payload(path) for path in reference_image_paths]
        submitted = await self.call(
            op="video.generate",
            method="POST",
            url=f"{self.BASE_URL}/videos/generations",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "model": model,
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "input_image_count": 1 if image_path is not None else 0,
                "reference_image_count": len(reference_image_paths or []),
                **({"input_image_name": image_path.name} if image_path is not None else {}),
                **(
                    {"reference_image_names": [path.name for path in reference_image_paths]}
                    if reference_image_paths
                    else {}
                ),
            },
        )
        if not isinstance(submitted.data, dict) or not isinstance(
            submitted.data.get("request_id"), str
        ):
            raise IntegrationDownError(
                "xAI video generation did not return a request_id",
                data={"vendor": self.vendor, "model": model},
            )
        request_id = submitted.data["request_id"]
        deadline = monotonic() + poll_timeout_seconds
        poll_result: IntegrationCallResult | None = None
        while monotonic() <= deadline:
            poll_result = await self.call(
                op="video.poll",
                method="GET",
                url=f"{self.BASE_URL}/videos/{request_id}",
                headers={"Authorization": self._auth_headers()["Authorization"]},
                request_log_body={"request_id": request_id},
            )
            if not isinstance(poll_result.data, dict):
                raise IntegrationDownError(
                    "xAI video poll returned a non-JSON response",
                    data={"vendor": self.vendor, "request_id": request_id},
                )
            status = str(poll_result.data.get("status") or "")
            if status in self.TERMINAL_VIDEO_STATUSES:
                break
            await asyncio.sleep(poll_interval_seconds)
        else:
            raise IntegrationDownError(
                "xAI video generation timed out",
                data={"vendor": self.vendor, "request_id": request_id},
            )
        assert poll_result is not None
        data = poll_result.data
        status = str(data.get("status") or "")
        if status != "done":
            raise IntegrationDownError(
                f"xAI video generation ended with status {status or 'unknown'}",
                data={
                    "vendor": self.vendor,
                    "request_id": request_id,
                    "status": status,
                    "error": data.get("error"),
                },
            )
        persisted = await self._persist_video_response(data, request_id=request_id, model=model)
        cost_usd = self._actual_or_estimated_cost_usd(
            response=data,
            estimated=submitted.cost_usd,
        )
        return IntegrationCallResult(
            data=persisted,
            cost_usd=cost_usd,
            duration_ms=submitted.duration_ms + poll_result.duration_ms,
        )

    async def _persist_image_response(self, data: Any, *, model: str) -> Any:
        if self._asset_dir is None or not isinstance(data, dict):
            return data
        items = data.get("data")
        if not isinstance(items, list):
            return data
        out = dict(data)
        persisted: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                persisted.append(item)
                continue
            raw: bytes | None = None
            ext = "jpg"
            if isinstance(item.get("b64_json"), str):
                try:
                    raw = base64.b64decode(item["b64_json"], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise IntegrationDownError(
                        "xAI Imagine returned invalid base64 image data",
                        data={"vendor": self.vendor, "model": model},
                    ) from exc
            elif isinstance(item.get("url"), str):
                raw, ext = await self._download_media(str(item["url"]), fallback_ext="jpg")
            if raw is None:
                persisted.append(item)
                continue
            clean = {k: v for k, v in item.items() if k not in {"b64_json"}}
            clean.update(self._write_media(raw, subdir="xai-imagine", prefix="xai-image", ext=ext))
            clean["source_model"] = str(item.get("model") or model)
            persisted.append(clean)
        out["data"] = persisted
        return out

    async def _persist_video_response(
        self,
        data: dict[str, Any],
        *,
        request_id: str,
        model: str,
    ) -> dict[str, Any]:
        video = data.get("video")
        if not isinstance(video, dict) or not isinstance(video.get("url"), str):
            raise IntegrationDownError(
                "xAI video generation completed without a video URL",
                data={"vendor": self.vendor, "request_id": request_id},
            )
        raw, ext = await self._download_media(str(video["url"]), fallback_ext="mp4")
        item = {
            **{k: v for k, v in video.items() if k != "url"},
            **self._write_media(raw, subdir="xai-imagine", prefix="xai-video", ext=ext),
            "source_model": str(data.get("model") or model),
            "request_id": request_id,
        }
        out = {
            "request_id": request_id,
            "status": "done",
            "model": str(data.get("model") or model),
            "data": [item],
        }
        if isinstance(data.get("usage"), dict):
            out["usage"] = data["usage"]
        return out

    async def _download_media(self, url: str, *, fallback_ext: str) -> tuple[bytes, str]:
        return await download_generated_media(
            self,
            url,
            fallback_ext=fallback_ext,
            empty_message="xAI Imagine returned an empty media download",
        )

    def _write_media(self, raw: bytes, *, subdir: str, prefix: str, ext: str) -> dict[str, str]:
        if self._asset_dir is None:
            return {}
        digest = hashlib.sha256(raw).hexdigest()
        filename = f"{prefix}-{digest[:32]}.{ext}"
        target_dir = self._asset_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        if not path.exists():
            path.write_bytes(raw)
        return {
            "url": f"{self._asset_url_prefix}/{subdir}/{filename}",
            "file_format": ext,
        }

    def _image_payload(self, path: Path) -> dict[str, str]:
        suffix = path.suffix.lower().lstrip(".")
        mime = _image_mime_type(suffix)
        if mime is None:
            raise IntegrationDownError(
                "xAI Imagine input images must be PNG or JPEG",
                data={"vendor": self.vendor, "file": path.name},
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise IntegrationDownError(
                "xAI Imagine input image could not be read",
                data={"vendor": self.vendor, "file": path.name},
            ) from exc
        if len(raw) > self.IMAGE_INPUT_MAX_BYTES:
            raise IntegrationDownError(
                "xAI Imagine input images must be at most 20 MiB",
                data={
                    "vendor": self.vendor,
                    "file": path.name,
                    "bytes": len(raw),
                    "max_bytes": self.IMAGE_INPUT_MAX_BYTES,
                },
            )
        encoded = base64.b64encode(raw).decode("ascii")
        return {"url": f"data:{mime};base64,{encoded}"}

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.call(
            op="test",
            method="GET",
            url=f"{self.BASE_URL}/models",
            headers={"Authorization": self._auth_headers()["Authorization"]},
        )
        models_count = len(result.data.get("data", [])) if isinstance(result.data, dict) else 0
        return {"ok": True, "vendor": self.vendor, "models_count": models_count}


def _image_mime_type(suffix: str) -> str | None:
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "png":
        return "image/png"
    return None


__all__ = ["XAIImagineIntegration"]
