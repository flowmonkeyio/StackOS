"""Kling video action connector."""

from __future__ import annotations

from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.media_artifacts import (
    artifact_path,
    cost_usd_to_cents,
    register_generated_media_artifacts,
)
from stackos.config import Settings
from stackos.integrations.kling_video import KlingVideoIntegration
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ValidationError


class KlingVideoActionConnector:
    """Decision-free adapter from utils Kling video actions to Kling AI."""

    key = "kling-video"
    _MODES = frozenset({"text-to-video", "image-to-video", "first-last-frame"})

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation != "video.generate":
            return [
                ActionValidationIssue(
                    path="$.operation",
                    message=f"unsupported Kling operation {request.operation!r}",
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
        elif len(prompt) > 2500:
            issues.append(
                ActionValidationIssue(
                    path="$.prompt",
                    message="prompt must be at most 2500 characters",
                    code="range",
                )
            )
        negative_prompt = payload.get("negative_prompt")
        if negative_prompt is not None and (
            not isinstance(negative_prompt, str) or len(negative_prompt) > 2500
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.negative_prompt",
                    message="negative_prompt must be a string up to 2500 characters",
                    code="range",
                )
            )
        mode = payload.get("mode", "text-to-video")
        if not isinstance(mode, str) or mode not in self._MODES:
            issues.append(
                ActionValidationIssue(
                    path="$.mode",
                    message="mode must be text-to-video, image-to-video, or first-last-frame",
                    code="enum_mismatch",
                )
            )
            mode = "text-to-video"
        model_name = payload.get("model_name", KlingVideoIntegration.DEFAULT_MODEL)
        if not isinstance(model_name, str) or model_name not in KlingVideoIntegration.MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.model_name",
                    message="model_name must be a supported Kling video model",
                    code="enum_mismatch",
                )
            )
        elif mode == "text-to-video" and model_name not in KlingVideoIntegration.TEXT_MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.model_name",
                    message="model_name is not supported for Kling text-to-video",
                    code="mode_mismatch",
                )
            )
        quality_mode = payload.get("quality_mode", "pro")
        if (
            not isinstance(quality_mode, str)
            or quality_mode not in KlingVideoIntegration.QUALITY_MODES
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.quality_mode",
                    message="quality_mode must be std, pro, or 4k",
                    code="enum_mismatch",
                )
            )
        duration = payload.get("duration", 5)
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or duration < 3
            or duration > 15
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.duration",
                    message="duration must be an integer between 3 and 15 seconds",
                    code="range",
                )
            )
        aspect_ratio = payload.get("aspect_ratio", "16:9")
        if mode == "text-to-video":
            if (
                not isinstance(aspect_ratio, str)
                or aspect_ratio not in KlingVideoIntegration.ASPECT_RATIOS
            ):
                issues.append(
                    ActionValidationIssue(
                        path="$.aspect_ratio",
                        message="aspect_ratio must be 16:9, 9:16, or 1:1",
                        code="enum_mismatch",
                    )
                )
        elif "aspect_ratio" in payload:
            issues.append(
                ActionValidationIssue(
                    path="$.aspect_ratio",
                    message="aspect_ratio is only valid for text-to-video",
                    code="mode_mismatch",
                )
            )
        sound = payload.get("sound", "off")
        if not isinstance(sound, str) or sound not in KlingVideoIntegration.SOUND_VALUES:
            issues.append(
                ActionValidationIssue(
                    path="$.sound",
                    message="sound must be on or off",
                    code="enum_mismatch",
                )
            )
        cfg_scale = payload.get("cfg_scale")
        if cfg_scale is not None and (
            not isinstance(cfg_scale, int | float)
            or isinstance(cfg_scale, bool)
            or cfg_scale < 0
            or cfg_scale > 1
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.cfg_scale",
                    message="cfg_scale must be a number between 0 and 1",
                    code="range",
                )
            )
        watermark_enabled = payload.get("watermark_enabled")
        if watermark_enabled is not None and not isinstance(watermark_enabled, bool):
            issues.append(
                ActionValidationIssue(
                    path="$.watermark_enabled",
                    message="watermark_enabled must be a boolean",
                    code="type_mismatch",
                )
            )
        callback_url = payload.get("callback_url")
        if callback_url is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.callback_url",
                    message="callback_url is not supported by the StackOS v1 Kling action",
                    code="unsupported_feature",
                )
            )
        if payload.get("external_task_id") is not None and not isinstance(
            payload["external_task_id"], str
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.external_task_id",
                    message="external_task_id must be a string",
                    code="type_mismatch",
                )
            )
        issues.extend(self._validate_image_refs(request, mode=mode))
        issues.extend(self._validate_poll_controls(payload))
        return issues

    def _validate_image_refs(
        self,
        request: ActionConnectorRequest,
        *,
        mode: str,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        input_ref = payload.get("input_image_ref")
        image_tail_ref = payload.get("image_tail_ref")
        if mode in {"image-to-video", "first-last-frame"} and not isinstance(input_ref, str):
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_ref",
                    message="input_image_ref is required for this Kling mode",
                    code="required",
                )
            )
        if mode == "first-last-frame" and not isinstance(image_tail_ref, str):
            issues.append(
                ActionValidationIssue(
                    path="$.image_tail_ref",
                    message="image_tail_ref is required for first-last-frame mode",
                    code="required",
                )
            )
        if mode == "text-to-video":
            for key in ("input_image_ref", "image_tail_ref"):
                if payload.get(key) is not None:
                    issues.append(
                        ActionValidationIssue(
                            path=f"$.{key}",
                            message=f"{key} is only valid for image modes",
                            code="mode_mismatch",
                        )
                    )
        elif mode != "first-last-frame" and image_tail_ref is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.image_tail_ref",
                    message="image_tail_ref is only valid for first-last-frame mode",
                    code="mode_mismatch",
                )
            )
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        for path_key, ref in (("input_image_ref", input_ref), ("image_tail_ref", image_tail_ref)):
            if not isinstance(ref, str):
                continue
            try:
                candidate = artifact_path(asset_dir, ref, label=path_key)
                suffix = candidate.suffix.lower().lstrip(".")
                if suffix not in KlingVideoIntegration.INPUT_IMAGE_FORMATS:
                    raise ValidationError(f"{path_key} must reference a PNG or JPEG asset")
                if candidate.stat().st_size > KlingVideoIntegration.MAX_INPUT_IMAGE_BYTES:
                    raise ValidationError("Kling input images must be at most 10 MB")
            except (OSError, ValidationError, IntegrationDownError) as exc:
                issues.append(
                    ActionValidationIssue(
                        path=f"$.{path_key}",
                        message=getattr(exc, "detail", str(exc)),
                        code="invalid_image_ref",
                    )
                )
        return issues

    @staticmethod
    def _validate_poll_controls(payload: dict[str, Any]) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        poll_interval = payload.get("poll_interval_seconds", 10)
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
            raise ValidationError("kling action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        mode = str(payload.get("mode") or "text-to-video")
        async with httpx.AsyncClient(timeout=180.0) as http:
            client = KlingVideoIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                asset_dir=asset_dir,
            )
            result = await client.generate_video(
                prompt=str(payload["prompt"]),
                mode=mode,
                model_name=str(payload.get("model_name", KlingVideoIntegration.DEFAULT_MODEL)),
                quality_mode=str(payload.get("quality_mode", "pro")),
                duration=int(payload.get("duration", 5)),
                aspect_ratio=str(payload.get("aspect_ratio", "16:9")),
                sound=str(payload.get("sound", "off")),
                negative_prompt=(
                    str(payload["negative_prompt"])
                    if isinstance(payload.get("negative_prompt"), str)
                    else None
                ),
                cfg_scale=(
                    float(payload["cfg_scale"])
                    if isinstance(payload.get("cfg_scale"), int | float)
                    and not isinstance(payload.get("cfg_scale"), bool)
                    else None
                ),
                input_image_path=(
                    artifact_path(
                        asset_dir,
                        str(payload["input_image_ref"]),
                        label="input_image_ref",
                    )
                    if isinstance(payload.get("input_image_ref"), str)
                    else None
                ),
                image_tail_path=(
                    artifact_path(asset_dir, str(payload["image_tail_ref"]), label="image_tail_ref")
                    if isinstance(payload.get("image_tail_ref"), str)
                    else None
                ),
                watermark_enabled=(
                    payload["watermark_enabled"]
                    if isinstance(payload.get("watermark_enabled"), bool)
                    else None
                ),
                external_task_id=(
                    str(payload["external_task_id"])
                    if isinstance(payload.get("external_task_id"), str)
                    else None
                ),
                poll_interval_seconds=float(payload.get("poll_interval_seconds", 10)),
                poll_timeout_seconds=float(payload.get("poll_timeout_seconds", 1800)),
            )
        output_json = result.data if isinstance(result.data, dict) else {"data": result.data}
        output_json = register_generated_media_artifacts(
            request,
            output_json,
            kind="video",
            provider_key="kling",
            source="kling-video-action",
            metadata_builder=lambda item: {
                "task_id": item.get("task_id"),
                "mode": item.get("mode"),
                "sample_index": item.get("sample_index"),
            },
        )
        return ActionConnectorResult(
            output_json=output_json,
            metadata_json={"vendor": "kling"},
            cost_cents=cost_usd_to_cents(result.cost_usd),
        )


__all__ = ["KlingVideoActionConnector"]
