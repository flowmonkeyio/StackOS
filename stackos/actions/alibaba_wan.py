"""Alibaba Wan video action connector."""

from __future__ import annotations

from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.media_artifacts import cost_usd_to_cents, register_generated_media_artifacts
from stackos.config import Settings
from stackos.integrations.alibaba_wan import AlibabaWanIntegration
from stackos.repositories.base import ValidationError


class AlibabaWanVideoActionConnector:
    """Decision-free adapter from utils Alibaba Wan video actions to Model Studio."""

    key = "alibaba-wan"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation != "video.generate":
            return [
                ActionValidationIssue(
                    path="$.operation",
                    message=f"unsupported Alibaba Wan operation {request.operation!r}",
                    code="unknown_operation",
                )
            ]
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            issues.append(
                ActionValidationIssue(
                    path="$.prompt",
                    message="prompt is required",
                    code="required",
                )
            )
        mode = payload.get("mode", "text-to-video")
        if not isinstance(mode, str) or mode not in AlibabaWanIntegration.MODES:
            issues.append(
                ActionValidationIssue(
                    path="$.mode",
                    message=(
                        "mode must be text-to-video, image-to-video, first-last-frame, "
                        "or video-continuation"
                    ),
                    code="enum_mismatch",
                )
            )
            mode = "text-to-video"
        region = payload.get("region", AlibabaWanIntegration.DEFAULT_REGION)
        if not isinstance(region, str) or region not in AlibabaWanIntegration.REGION_BASE_URLS:
            issues.append(
                ActionValidationIssue(
                    path="$.region",
                    message="region must be singapore or beijing",
                    code="enum_mismatch",
                )
            )
        resolution = payload.get("resolution", "720P")
        if (
            not isinstance(resolution, str)
            or resolution not in AlibabaWanIntegration.RESOLUTION_TIERS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message="resolution must be 720P or 1080P",
                    code="enum_mismatch",
                )
            )
        aspect_ratio = payload.get("aspect_ratio", "16:9")
        if mode == "text-to-video":
            supported = AlibabaWanIntegration.T2V_SIZE_BY_TIER_AND_ASPECT.get(str(resolution), {})
            if not isinstance(aspect_ratio, str) or aspect_ratio not in supported:
                issues.append(
                    ActionValidationIssue(
                        path="$.aspect_ratio",
                        message="aspect_ratio must be 16:9, 9:16, 1:1, 4:3, or 3:4",
                        code="enum_mismatch",
                    )
                )
        duration = payload.get("duration", 5)
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or duration < 2
            or duration > 15
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.duration",
                    message="duration must be an integer between 2 and 15 seconds",
                    code="range",
                )
            )
        for key in ("prompt_extend", "watermark"):
            if payload.get(key) is not None and not isinstance(payload[key], bool):
                issues.append(
                    ActionValidationIssue(
                        path=f"$.{key}",
                        message=f"{key} must be a boolean",
                        code="type_mismatch",
                    )
                )
        seed = payload.get("seed")
        if seed is not None and (
            not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 or seed > 2_147_483_647
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.seed",
                    message="seed must be an integer between 0 and 2147483647",
                    code="range",
                )
            )
        issues.extend(self._validate_urls(payload, mode=mode))
        issues.extend(self._validate_poll_controls(payload))
        return issues

    @staticmethod
    def _validate_urls(payload: dict[str, Any], *, mode: str) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        required_by_mode = {
            "image-to-video": ["first_frame_url"],
            "first-last-frame": ["first_frame_url", "last_frame_url"],
            "video-continuation": ["first_clip_url"],
        }
        for key in required_by_mode.get(mode, []):
            if not _is_http_url(payload.get(key)):
                issues.append(
                    ActionValidationIssue(
                        path=f"$.{key}",
                        message=f"{key} must be a provider-fetchable http(s) URL",
                        code="required",
                    )
                )
        for key in ("first_frame_url", "last_frame_url", "first_clip_url", "audio_url"):
            value = payload.get(key)
            if value is not None and not _is_http_url(value):
                issues.append(
                    ActionValidationIssue(
                        path=f"$.{key}",
                        message=f"{key} must be an http(s) URL",
                        code="url",
                    )
                )
        if mode == "text-to-video":
            for key in ("first_frame_url", "last_frame_url", "first_clip_url"):
                if payload.get(key) is not None:
                    issues.append(
                        ActionValidationIssue(
                            path=f"$.{key}",
                            message=f"{key} is only valid for image/video input modes",
                            code="mode_mismatch",
                        )
                    )
        if mode == "video-continuation" and payload.get("audio_url") is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.audio_url",
                    message="audio_url is not valid for video-continuation",
                    code="mode_mismatch",
                )
            )
        return issues

    @staticmethod
    def _validate_poll_controls(payload: dict[str, Any]) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        poll_interval = payload.get("poll_interval_seconds", 15)
        if (
            not isinstance(poll_interval, int | float)
            or isinstance(poll_interval, bool)
            or poll_interval < 1
            or poll_interval > 60
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.poll_interval_seconds",
                    message="poll_interval_seconds must be between 1 and 60",
                    code="range",
                )
            )
        poll_timeout = payload.get("poll_timeout_seconds", 1800)
        if (
            not isinstance(poll_timeout, int | float)
            or isinstance(poll_timeout, bool)
            or poll_timeout < 60
            or poll_timeout > 3600
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.poll_timeout_seconds",
                    message="poll_timeout_seconds must be between 60 and 3600",
                    code="range",
                )
            )
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        del request
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("alibaba-wan action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        async with httpx.AsyncClient(timeout=180.0) as http:
            client = AlibabaWanIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                asset_dir=asset_dir,
            )
            result = await client.generate_video(
                prompt=str(payload["prompt"]),
                mode=str(payload.get("mode", "text-to-video")),
                region=str(payload.get("region", AlibabaWanIntegration.DEFAULT_REGION)),
                resolution=str(payload.get("resolution", "720P")),
                aspect_ratio=str(payload.get("aspect_ratio", "16:9")),
                duration=int(payload.get("duration", 5)),
                prompt_extend=(
                    payload["prompt_extend"]
                    if isinstance(payload.get("prompt_extend"), bool)
                    else True
                ),
                watermark=(
                    payload["watermark"] if isinstance(payload.get("watermark"), bool) else None
                ),
                negative_prompt=(
                    str(payload["negative_prompt"])
                    if isinstance(payload.get("negative_prompt"), str)
                    else None
                ),
                seed=(
                    int(payload["seed"])
                    if isinstance(payload.get("seed"), int)
                    and not isinstance(payload.get("seed"), bool)
                    else None
                ),
                first_frame_url=_optional_url(payload.get("first_frame_url")),
                last_frame_url=_optional_url(payload.get("last_frame_url")),
                first_clip_url=_optional_url(payload.get("first_clip_url")),
                audio_url=_optional_url(payload.get("audio_url")),
                poll_interval_seconds=float(payload.get("poll_interval_seconds", 15)),
                poll_timeout_seconds=float(payload.get("poll_timeout_seconds", 1800)),
            )
        output_json = result.data if isinstance(result.data, dict) else {"data": result.data}
        output_json = register_generated_media_artifacts(
            request,
            output_json,
            kind="video",
            provider_key="alibaba-wan",
            source="alibaba-wan-action",
            metadata_builder=lambda item: {
                "task_id": item.get("task_id"),
                "mode": item.get("mode"),
            },
        )
        return ActionConnectorResult(
            output_json=output_json,
            metadata_json={"vendor": "alibaba-wan"},
            cost_cents=cost_usd_to_cents(result.cost_usd),
        )


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _optional_url(value: Any) -> str | None:
    return value if _is_http_url(value) else None


__all__ = ["AlibabaWanVideoActionConnector"]
