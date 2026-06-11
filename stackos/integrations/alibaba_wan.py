"""Alibaba Model Studio Wan video integration wrapper.

Official docs:

- https://www.alibabacloud.com/help/en/model-studio/video-generate-edit-model/
- https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference/
- https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference
- https://github.com/dashscope/dashscope-sdk-python

Wan video generation is asynchronous. Successful task responses include a
24-hour temporary video URL, which StackOS downloads immediately.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import Any, ClassVar

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations._media import (
    download_generated_media,
    sanitize_media_audit_payload,
    write_generated_media,
)
from stackos.mcp.errors import IntegrationDownError


class AlibabaWanIntegration(BaseIntegration):
    """Wrapper for Alibaba Model Studio Wan async video tasks."""

    kind = "alibaba-wan"
    vendor = "alibaba-wan"
    default_qps = 1.0

    DEFAULT_REGION = "singapore"
    REGION_BASE_URLS: ClassVar[dict[str, str]] = {
        "singapore": "https://dashscope-intl.aliyuncs.com/api/v1",
        "beijing": "https://dashscope.aliyuncs.com/api/v1",
    }
    DEFAULT_T2V_MODEL = "wan2.6-t2v"
    DEFAULT_I2V_MODEL = "wan2.7-i2v"
    T2V_MODELS: ClassVar[frozenset[str]] = frozenset({"wan2.6-t2v"})
    I2V_MODELS: ClassVar[frozenset[str]] = frozenset({"wan2.7-i2v"})
    MODES: ClassVar[frozenset[str]] = frozenset(
        {"text-to-video", "image-to-video", "first-last-frame", "video-continuation"}
    )
    RESOLUTION_TIERS: ClassVar[frozenset[str]] = frozenset({"720P", "1080P"})
    T2V_SIZE_BY_TIER_AND_ASPECT: ClassVar[dict[str, dict[str, str]]] = {
        "720P": {
            "16:9": "1280*720",
            "9:16": "720*1280",
            "1:1": "960*960",
            "4:3": "1088*832",
            "3:4": "832*1088",
        },
        "1080P": {
            "16:9": "1920*1080",
            "9:16": "1080*1920",
            "1:1": "1440*1440",
            "4:3": "1632*1248",
            "3:4": "1248*1632",
        },
    }
    TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}
    )

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
            "X-DashScope-Async": "enable",
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

    async def generate_video(
        self,
        *,
        prompt: str,
        mode: str = "text-to-video",
        region: str = DEFAULT_REGION,
        resolution: str = "720P",
        aspect_ratio: str = "16:9",
        duration: int = 5,
        prompt_extend: bool = True,
        watermark: bool | None = None,
        negative_prompt: str | None = None,
        seed: int | None = None,
        first_frame_url: str | None = None,
        last_frame_url: str | None = None,
        first_clip_url: str | None = None,
        audio_url: str | None = None,
        poll_interval_seconds: float = 15.0,
        poll_timeout_seconds: float = 1800.0,
    ) -> IntegrationCallResult:
        model = self.DEFAULT_T2V_MODEL if mode == "text-to-video" else self.DEFAULT_I2V_MODEL
        body = self._build_request_body(
            prompt=prompt,
            mode=mode,
            model=model,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            prompt_extend=prompt_extend,
            watermark=watermark,
            negative_prompt=negative_prompt,
            seed=seed,
            first_frame_url=first_frame_url,
            last_frame_url=last_frame_url,
            first_clip_url=first_clip_url,
            audio_url=audio_url,
        )
        submitted = await self.call(
            op="video.generate",
            method="POST",
            url=f"{self.base_url_for_region(region)}/services/aigc/video-generation/video-synthesis",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "model": model,
                "mode": mode,
                "prompt": prompt,
                "region": region,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
                "duration": duration,
                "prompt_extend": prompt_extend,
                "watermark": watermark,
                "has_first_frame_url": first_frame_url is not None,
                "has_last_frame_url": last_frame_url is not None,
                "has_first_clip_url": first_clip_url is not None,
                "has_audio_url": audio_url is not None,
            },
        )
        task_id = self._task_id(submitted.data)
        poll_result = await self._poll_task(
            task_id=task_id,
            region=region,
            poll_interval_seconds=poll_interval_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
        )
        persisted = await self._persist_task_response(
            poll_result.data,
            task_id=task_id,
            model=model,
            mode=mode,
        )
        return IntegrationCallResult(
            data=persisted,
            cost_usd=submitted.cost_usd + poll_result.cost_usd,
            duration_ms=submitted.duration_ms + poll_result.duration_ms,
        )

    @classmethod
    def _build_request_body(
        cls,
        *,
        prompt: str,
        mode: str,
        model: str,
        resolution: str,
        aspect_ratio: str,
        duration: int,
        prompt_extend: bool,
        watermark: bool | None,
        negative_prompt: str | None,
        seed: int | None,
        first_frame_url: str | None,
        last_frame_url: str | None,
        first_clip_url: str | None,
        audio_url: str | None,
    ) -> dict[str, Any]:
        input_json: dict[str, Any] = {"prompt": prompt}
        if negative_prompt:
            input_json["negative_prompt"] = negative_prompt
        parameters: dict[str, Any] = {
            "duration": duration,
            "prompt_extend": prompt_extend,
        }
        if watermark is not None:
            parameters["watermark"] = watermark
        if seed is not None:
            parameters["seed"] = seed
        if mode == "text-to-video":
            parameters["size"] = cls.T2V_SIZE_BY_TIER_AND_ASPECT[resolution][aspect_ratio]
            if audio_url:
                input_json["audio_url"] = audio_url
        else:
            media = cls._media_items(
                mode=mode,
                first_frame_url=first_frame_url,
                last_frame_url=last_frame_url,
                first_clip_url=first_clip_url,
                audio_url=audio_url,
            )
            input_json["media"] = media
            parameters["resolution"] = resolution
        return {
            "model": model,
            "input": input_json,
            "parameters": parameters,
        }

    @staticmethod
    def _media_items(
        *,
        mode: str,
        first_frame_url: str | None,
        last_frame_url: str | None,
        first_clip_url: str | None,
        audio_url: str | None,
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        if mode in {"image-to-video", "first-last-frame"}:
            if first_frame_url is None:
                raise IntegrationDownError(
                    "Alibaba Wan image modes require first_frame_url",
                    data={"vendor": AlibabaWanIntegration.vendor, "mode": mode},
                )
            items.append({"type": "first_frame", "url": first_frame_url})
        if mode == "first-last-frame":
            if last_frame_url is None:
                raise IntegrationDownError(
                    "Alibaba Wan first-last-frame mode requires last_frame_url",
                    data={"vendor": AlibabaWanIntegration.vendor},
                )
            items.append({"type": "last_frame", "url": last_frame_url})
        if mode == "video-continuation":
            if first_clip_url is None:
                raise IntegrationDownError(
                    "Alibaba Wan video-continuation mode requires first_clip_url",
                    data={"vendor": AlibabaWanIntegration.vendor},
                )
            if audio_url is not None:
                raise IntegrationDownError(
                    "Alibaba Wan video-continuation mode does not support audio_url",
                    data={"vendor": AlibabaWanIntegration.vendor},
                )
            items.append({"type": "first_clip", "url": first_clip_url})
            if last_frame_url is not None:
                items.append({"type": "last_frame", "url": last_frame_url})
        if audio_url is not None and mode != "video-continuation":
            items.append({"type": "driving_audio", "url": audio_url})
        return items

    async def _poll_task(
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
                url=f"{self.base_url_for_region(region)}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.payload.decode('utf-8')}"},
                request_log_body={"task_id": task_id, "region": region},
            )
            status = self._task_status(poll_result.data)
            if status in self.TERMINAL_STATUSES:
                break
            await asyncio.sleep(poll_interval_seconds)
        else:
            raise IntegrationDownError(
                "Alibaba Wan video generation timed out",
                data={"vendor": self.vendor, "task_id": task_id},
            )
        assert poll_result is not None
        status = self._task_status(poll_result.data)
        if status != "SUCCEEDED":
            output = poll_result.data.get("output") if isinstance(poll_result.data, dict) else None
            raise IntegrationDownError(
                f"Alibaba Wan video generation ended with status {status or 'unknown'}",
                data={"vendor": self.vendor, "task_id": task_id, "output": output},
            )
        return poll_result

    async def _persist_task_response(
        self,
        data: Any,
        *,
        task_id: str,
        model: str,
        mode: str,
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise IntegrationDownError(
                "Alibaba Wan poll returned a non-JSON response",
                data={"vendor": self.vendor, "task_id": task_id},
            )
        output = data.get("output")
        if not isinstance(output, dict) or not isinstance(output.get("video_url"), str):
            raise IntegrationDownError(
                "Alibaba Wan task completed without a video_url",
                data={"vendor": self.vendor, "task_id": task_id},
            )
        raw, ext = await download_generated_media(
            self,
            str(output["video_url"]),
            fallback_ext="mp4",
            empty_message="Alibaba Wan returned an empty video download",
        )
        assert self._asset_dir is not None
        item = {
            **{key: value for key, value in output.items() if key != "video_url"},
            **write_generated_media(
                raw,
                asset_dir=self._asset_dir,
                asset_url_prefix=self._asset_url_prefix,
                subdir="alibaba-wan",
                prefix="alibaba-wan-video",
                ext=ext,
            ),
            "source_model": model,
            "task_id": task_id,
            "mode": mode,
        }
        out: dict[str, Any] = {
            "task_id": task_id,
            "request_id": data.get("request_id"),
            "status": "SUCCEEDED",
            "model": model,
            "data": [item],
        }
        if isinstance(data.get("usage"), dict):
            out["usage"] = data["usage"]
        return out

    @classmethod
    def base_url_for_region(cls, region: str) -> str:
        return cls.REGION_BASE_URLS.get(region, cls.REGION_BASE_URLS[cls.DEFAULT_REGION])

    @staticmethod
    def _task_id(data: Any) -> str:
        output = data.get("output") if isinstance(data, dict) else None
        if isinstance(output, dict) and isinstance(output.get("task_id"), str):
            return output["task_id"]
        raise IntegrationDownError(
            "Alibaba Wan generation did not return a task_id",
            data={"vendor": AlibabaWanIntegration.vendor},
        )

    @staticmethod
    def _task_status(data: Any) -> str:
        output = data.get("output") if isinstance(data, dict) else None
        if isinstance(output, dict):
            raw = output.get("task_status")
            return raw if isinstance(raw, str) else ""
        return ""

    async def test_credentials(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vendor": self.vendor,
            "status": "format-only",
            "summary": (
                "Alibaba Model Studio does not document a free Wan credential probe; "
                "StackOS verified credential storage format without making a billable task."
            ),
            "probe_mode": "non_billable_format_only",
            "next_action": "Run an Alibaba Wan video action to verify live model access.",
        }


__all__ = ["AlibabaWanIntegration"]
