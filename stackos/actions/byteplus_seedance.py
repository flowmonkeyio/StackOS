"""BytePlus Seedance video action connector."""

from __future__ import annotations

from pathlib import Path
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
from stackos.integrations.byteplus_ark import BytePlusArkIntegration
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ValidationError


class BytePlusSeedanceVideoActionConnector:
    """Decision-free adapter from utils BytePlus Seedance actions to ModelArk."""

    key = "byteplus-seedance"
    _MODES = frozenset(
        {"text-to-video", "image-to-video", "first-last-frame", "reference-to-video"}
    )

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation != "video.generate":
            return [
                ActionValidationIssue(
                    path="$.operation",
                    message=f"unsupported BytePlus Seedance operation {request.operation!r}",
                    code="unknown_operation",
                )
            ]
        prompt = payload.get("prompt")
        mode = payload.get("mode", "text-to-video")
        if not isinstance(mode, str) or mode not in self._MODES:
            issues.append(
                ActionValidationIssue(
                    path="$.mode",
                    message=(
                        "mode must be text-to-video, image-to-video, first-last-frame, "
                        "or reference-to-video"
                    ),
                    code="enum_mismatch",
                )
            )
            mode = "text-to-video"
        if mode == "text-to-video" and (not isinstance(prompt, str) or not prompt.strip()):
            issues.append(
                ActionValidationIssue(
                    path="$.prompt",
                    message="prompt is required",
                    code="required",
                )
            )
        elif prompt is not None and not isinstance(prompt, str):
            issues.append(
                ActionValidationIssue(
                    path="$.prompt",
                    message="prompt must be a string when provided",
                    code="type_mismatch",
                )
            )
        model = payload.get("model", BytePlusArkIntegration.DEFAULT_SEEDANCE_MODEL)
        if not isinstance(model, str) or model not in BytePlusArkIntegration.SEEDANCE_MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.model",
                    message="model must be a supported BytePlus Seedance video model",
                    code="enum_mismatch",
                )
            )
        region = payload.get("region", BytePlusArkIntegration.DEFAULT_REGION)
        if (
            not isinstance(region, str)
            or region not in BytePlusArkIntegration.SEEDANCE_REGION_BASE_URLS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.region",
                    message="region must be ap-southeast-1",
                    code="enum_mismatch",
                )
            )
        resolution = payload.get("resolution", "720p")
        if (
            not isinstance(resolution, str)
            or resolution not in BytePlusArkIntegration.SEEDANCE_RESOLUTIONS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message="resolution must be 480p, 720p, or 1080p",
                    code="enum_mismatch",
                )
            )
        elif model == "dreamina-seedance-2-0-fast-260128" and resolution == "1080p":
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message="Seedance 2.0 Fast does not support 1080p",
                    code="model_mismatch",
                )
            )
        if isinstance(model, str):
            issues.extend(self._validate_model_feature_support(payload, model=model, mode=mode))
        ratio = payload.get("ratio", "16:9")
        if not isinstance(ratio, str) or ratio not in BytePlusArkIntegration.SEEDANCE_RATIOS:
            issues.append(
                ActionValidationIssue(
                    path="$.ratio",
                    message="ratio must be 16:9, 4:3, 1:1, 3:4, 9:16, 21:9, or adaptive",
                    code="enum_mismatch",
                )
            )
        duration = payload.get("duration", 5)
        duration_range = (
            BytePlusArkIntegration.SEEDANCE_DURATION_RANGES.get(model)
            if isinstance(model, str)
            else None
        )
        min_duration, max_duration = duration_range or (4, 15)
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or (
                duration == -1
                and model not in BytePlusArkIntegration.SEEDANCE_DEFAULT_DURATION_MODELS
            )
            or (duration != -1 and (duration < min_duration or duration > max_duration))
        ):
            duration_message = (
                f"duration must be -1 or an integer between {min_duration} "
                f"and {max_duration} seconds for {model}"
                if model in BytePlusArkIntegration.SEEDANCE_DEFAULT_DURATION_MODELS
                else (
                    f"duration must be an integer between {min_duration} "
                    f"and {max_duration} seconds for {model}"
                )
            )
            issues.append(
                ActionValidationIssue(
                    path="$.duration",
                    message=duration_message,
                    code="range",
                )
            )
        issues.extend(self._validate_media_refs(request, mode=mode))
        issues.extend(self._validate_flags(payload))
        issues.extend(self._validate_poll_controls(payload))
        return issues

    def _validate_model_feature_support(
        self,
        payload: dict[str, Any],
        *,
        model: str,
        mode: str,
    ) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        if mode == "reference-to-video" and model not in BytePlusArkIntegration.SEEDANCE_2_MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.mode",
                    message="reference-to-video requires a Seedance 2.0 model",
                    code="model_mismatch",
                )
            )
        if (
            mode == "first-last-frame"
            and model in BytePlusArkIntegration.SEEDANCE_FIRST_LAST_UNSUPPORTED_MODELS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.mode",
                    message="Seedance 1.0 Pro Fast does not support first-last-frame mode",
                    code="model_mismatch",
                )
            )
        if (
            payload.get("generate_audio") is not None
            and model not in BytePlusArkIntegration.SEEDANCE_AUDIO_MODELS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.generate_audio",
                    message="generate_audio requires a Seedance 2.0 or 1.5 model",
                    code="model_mismatch",
                )
            )
        if (
            payload.get("priority") is not None
            and model not in BytePlusArkIntegration.SEEDANCE_2_MODELS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.priority",
                    message="priority requires a Seedance 2.0 model",
                    code="model_mismatch",
                )
            )
        return issues

    def _validate_media_refs(
        self,
        request: ActionConnectorRequest,
        *,
        mode: str,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        refs = payload.get("input_image_refs")
        video_urls = payload.get("reference_video_urls")
        audio_urls = payload.get("reference_audio_urls")
        issues: list[ActionValidationIssue] = []
        expected_counts = {"image-to-video": 1, "first-last-frame": 2}
        if mode in expected_counts:
            if not isinstance(refs, list) or len(refs) != expected_counts[mode]:
                issues.append(
                    ActionValidationIssue(
                        path="$.input_image_refs",
                        message=f"{mode} requires exactly {expected_counts[mode]} input image refs",
                        code="required",
                    )
                )
        elif mode == "reference-to-video":
            image_count = len(refs) if isinstance(refs, list) else 0
            video_count = len(video_urls) if isinstance(video_urls, list) else 0
            audio_count = len(audio_urls) if isinstance(audio_urls, list) else 0
            if image_count + video_count == 0:
                issues.append(
                    ActionValidationIssue(
                        path="$.input_image_refs",
                        message="reference-to-video requires at least one image ref or video URL",
                        code="required",
                    )
                )
            if image_count > 9:
                issues.append(
                    ActionValidationIssue(
                        path="$.input_image_refs",
                        message="reference-to-video accepts at most 9 images",
                        code="range",
                    )
                )
            if video_count > 3:
                issues.append(
                    ActionValidationIssue(
                        path="$.reference_video_urls",
                        message="reference-to-video accepts at most 3 video URLs",
                        code="range",
                    )
                )
            if audio_count > 3:
                issues.append(
                    ActionValidationIssue(
                        path="$.reference_audio_urls",
                        message="reference-to-video accepts at most 3 audio URLs",
                        code="range",
                    )
                )
        elif refs is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message="input_image_refs is only valid for image/reference modes",
                    code="mode_mismatch",
                )
            )
        if isinstance(refs, list):
            asset_dir = request.asset_dir or Settings().generated_assets_dir
            try:
                paths = [
                    artifact_path(asset_dir, str(ref), label="input_image_refs") for ref in refs
                ]
                for path in paths:
                    BytePlusArkIntegration.ensure_seedance_image_preflight(path)
            except (IntegrationDownError, ValidationError) as exc:
                issues.append(
                    ActionValidationIssue(
                        path="$.input_image_refs",
                        message=getattr(exc, "detail", str(exc)),
                        code="invalid_image_ref",
                    )
                )
        for key in ("reference_video_urls", "reference_audio_urls"):
            value = payload.get(key)
            if value is None:
                continue
            if not isinstance(value, list) or not all(_is_http_url(item) for item in value):
                issues.append(
                    ActionValidationIssue(
                        path=f"$.{key}",
                        message=f"{key} must be a list of http(s) URLs",
                        code="url",
                    )
                )
        return issues

    @staticmethod
    def _validate_flags(payload: dict[str, Any]) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        for key in ("generate_audio", "watermark", "return_last_frame"):
            if payload.get(key) is not None and not isinstance(payload[key], bool):
                issues.append(
                    ActionValidationIssue(
                        path=f"$.{key}",
                        message=f"{key} must be a boolean",
                        code="type_mismatch",
                    )
                )
        priority = payload.get("priority")
        if priority is not None and (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or priority < 0
            or priority > 9
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.priority",
                    message="priority must be an integer from 0 to 9",
                    code="range",
                )
            )
        seed = payload.get("seed")
        if seed is not None and (
            not isinstance(seed, int) or isinstance(seed, bool) or seed < -1 or seed > 4_294_967_295
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.seed",
                    message="seed must be an integer from -1 to 4294967295",
                    code="range",
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
            raise ValidationError("byteplus-seedance action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        refs = payload.get("input_image_refs")
        image_paths: list[Path] = (
            [artifact_path(asset_dir, str(ref), label="input_image_refs") for ref in refs]
            if isinstance(refs, list)
            else []
        )
        async with httpx.AsyncClient(timeout=180.0) as http:
            client = BytePlusArkIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                asset_dir=asset_dir,
            )
            result = await client.generate_seedance_video(
                prompt=str(payload["prompt"]) if isinstance(payload.get("prompt"), str) else None,
                model=str(payload.get("model", BytePlusArkIntegration.DEFAULT_SEEDANCE_MODEL)),
                mode=str(payload.get("mode", "text-to-video")),
                region=str(payload.get("region", BytePlusArkIntegration.DEFAULT_REGION)),
                resolution=str(payload.get("resolution", "720p")),
                ratio=str(payload.get("ratio", "16:9")),
                duration=int(payload.get("duration", 5)),
                input_image_paths=image_paths,
                reference_video_urls=_url_list(payload.get("reference_video_urls")),
                reference_audio_urls=_url_list(payload.get("reference_audio_urls")),
                generate_audio=(
                    payload["generate_audio"]
                    if isinstance(payload.get("generate_audio"), bool)
                    else None
                ),
                watermark=(
                    payload["watermark"] if isinstance(payload.get("watermark"), bool) else None
                ),
                seed=(
                    int(payload["seed"])
                    if isinstance(payload.get("seed"), int)
                    and not isinstance(payload.get("seed"), bool)
                    else None
                ),
                return_last_frame=(
                    payload["return_last_frame"]
                    if isinstance(payload.get("return_last_frame"), bool)
                    else None
                ),
                safety_identifier=(
                    str(payload["safety_identifier"])
                    if isinstance(payload.get("safety_identifier"), str)
                    else None
                ),
                priority=(
                    int(payload["priority"])
                    if isinstance(payload.get("priority"), int)
                    and not isinstance(payload.get("priority"), bool)
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
            provider_key="byteplus-ark",
            source="byteplus-seedance-action",
            metadata_builder=lambda item: {
                "task_id": item.get("task_id"),
                "mode": item.get("mode"),
            },
        )
        return ActionConnectorResult(
            output_json=output_json,
            metadata_json={"vendor": "byteplus-ark", "model_family": "seedance"},
            cost_cents=cost_usd_to_cents(result.cost_usd),
        )


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _url_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _is_http_url(item)]


__all__ = ["BytePlusSeedanceVideoActionConnector"]
