"""Google Veo video action connector."""

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
from stackos.integrations.google_veo import GoogleVeoIntegration
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ValidationError


class GoogleVeoVideoActionConnector:
    """Decision-free adapter from utils Google Veo video actions to Gemini."""

    key = "google-veo"
    _MODES = frozenset({"text-to-video", "image-to-video", "first-last-frame"})

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation != "video.generate":
            return [
                ActionValidationIssue(
                    path="$.operation",
                    message=f"unsupported Google Veo operation {request.operation!r}",
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
        model = payload.get("model", GoogleVeoIntegration.DEFAULT_MODEL)
        if not isinstance(model, str) or model not in GoogleVeoIntegration.MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.model",
                    message="model must be a supported Google Veo model",
                    code="enum_mismatch",
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
        duration = payload.get("duration_seconds")
        if duration is not None and (
            not isinstance(duration, int) or isinstance(duration, bool) or duration not in {4, 6, 8}
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.duration_seconds",
                    message="duration_seconds must be 4, 6, or 8",
                    code="range",
                )
            )
        aspect_ratio = payload.get("aspect_ratio", "16:9")
        if (
            not isinstance(aspect_ratio, str)
            or aspect_ratio not in GoogleVeoIntegration.ASPECT_RATIOS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.aspect_ratio",
                    message="aspect_ratio must be 16:9 or 9:16",
                    code="enum_mismatch",
                )
            )
        resolution = payload.get("resolution")
        if resolution is not None and (
            not isinstance(resolution, str) or resolution not in GoogleVeoIntegration.RESOLUTIONS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message="resolution must be 720p, 1080p, or 4k",
                    code="enum_mismatch",
                )
            )
        elif (
            isinstance(model, str)
            and isinstance(resolution, str)
            and resolution
            not in GoogleVeoIntegration.RESOLUTIONS_BY_MODEL.get(
                model,
                GoogleVeoIntegration.RESOLUTIONS,
            )
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message=f"{model} supports resolution 720p or 1080p only",
                    code="model_mismatch",
                )
            )
        elif resolution in {"1080p", "4k"} and duration is not None and duration != 8:
            issues.append(
                ActionValidationIssue(
                    path="$.duration_seconds",
                    message="1080p and 4k Veo output require duration_seconds 8",
                    code="model_mismatch",
                )
            )
        person_generation = payload.get("person_generation")
        if person_generation is not None and (
            not isinstance(person_generation, str)
            or person_generation not in GoogleVeoIntegration.PERSON_GENERATION_VALUES
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.person_generation",
                    message="person_generation must be allow_all or allow_adult",
                    code="enum_mismatch",
                )
            )
        elif mode == "text-to-video" and person_generation not in {None, "allow_all"}:
            issues.append(
                ActionValidationIssue(
                    path="$.person_generation",
                    message="Veo 3.1 text-to-video supports person_generation allow_all only",
                    code="mode_mismatch",
                )
            )
        elif mode in {"image-to-video", "first-last-frame"} and person_generation not in {
            None,
            "allow_adult",
        }:
            issues.append(
                ActionValidationIssue(
                    path="$.person_generation",
                    message=(
                        "Veo 3.1 image-to-video and first-last-frame support "
                        "person_generation allow_adult only"
                    ),
                    code="mode_mismatch",
                )
            )
        if mode == "first-last-frame" and duration is not None and duration != 8:
            issues.append(
                ActionValidationIssue(
                    path="$.duration_seconds",
                    message="Veo first-last-frame generation requires duration_seconds 8",
                    code="mode_mismatch",
                )
            )
        for key in ("enhance_prompt",):
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
        issues.extend(self._validate_poll_controls(payload))
        issues.extend(self._validate_media_refs(request, mode=mode))
        return issues

    def _validate_media_refs(
        self,
        request: ActionConnectorRequest,
        *,
        mode: str,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        input_ref = payload.get("input_image_ref")
        last_ref = payload.get("last_frame_ref")
        if mode in {"image-to-video", "first-last-frame"} and not isinstance(input_ref, str):
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_ref",
                    message="input_image_ref is required for this Google Veo mode",
                    code="required",
                )
            )
        if mode == "first-last-frame" and not isinstance(last_ref, str):
            issues.append(
                ActionValidationIssue(
                    path="$.last_frame_ref",
                    message="last_frame_ref is required for first-last-frame mode",
                    code="required",
                )
            )
        if mode == "text-to-video" and input_ref is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_ref",
                    message="input_image_ref is only valid for image modes",
                    code="mode_mismatch",
                )
            )
        if mode != "first-last-frame" and last_ref is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.last_frame_ref",
                    message="last_frame_ref is only valid for first-last-frame mode",
                    code="mode_mismatch",
                )
            )
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        for path_key, ref in (("input_image_ref", input_ref), ("last_frame_ref", last_ref)):
            if not isinstance(ref, str):
                continue
            try:
                candidate = artifact_path(asset_dir, ref, label=path_key)
                if (
                    candidate.suffix.lower().lstrip(".")
                    not in GoogleVeoIntegration.INPUT_IMAGE_FORMATS
                ):
                    _raise_bad_ref(path_key)
                if candidate.stat().st_size > GoogleVeoIntegration.MAX_INPUT_IMAGE_BYTES:
                    raise ValidationError("Google Veo input images must be at most 20 MB")
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
            raise ValidationError("google-veo action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        mode = str(payload.get("mode") or "text-to-video")
        async with httpx.AsyncClient(timeout=180.0) as http:
            client = GoogleVeoIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                asset_dir=asset_dir,
            )
            result = await client.generate_video(
                prompt=str(payload["prompt"]),
                model=str(payload.get("model", GoogleVeoIntegration.DEFAULT_MODEL)),
                mode=mode,
                duration_seconds=(
                    int(payload["duration_seconds"])
                    if isinstance(payload.get("duration_seconds"), int)
                    and not isinstance(payload.get("duration_seconds"), bool)
                    else None
                ),
                aspect_ratio=str(payload.get("aspect_ratio", "16:9")),
                resolution=(
                    str(payload["resolution"])
                    if isinstance(payload.get("resolution"), str)
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
                last_frame_path=(
                    artifact_path(asset_dir, str(payload["last_frame_ref"]), label="last_frame_ref")
                    if isinstance(payload.get("last_frame_ref"), str)
                    else None
                ),
                enhance_prompt=(
                    payload["enhance_prompt"]
                    if isinstance(payload.get("enhance_prompt"), bool)
                    else None
                ),
                person_generation=(
                    str(payload["person_generation"])
                    if isinstance(payload.get("person_generation"), str)
                    else None
                ),
                seed=(
                    int(payload["seed"])
                    if isinstance(payload.get("seed"), int)
                    and not isinstance(payload.get("seed"), bool)
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
            provider_key="google-veo",
            source="google-veo-action",
            metadata_builder=lambda item: {
                "operation_name": item.get("operation_name"),
                "sample_index": item.get("sample_index"),
            },
        )
        return ActionConnectorResult(
            output_json=output_json,
            metadata_json={"vendor": "google-veo"},
            cost_cents=cost_usd_to_cents(result.cost_usd),
        )


def _raise_bad_ref(path_key: str) -> None:
    raise ValidationError(f"{path_key} must reference a PNG or JPEG generated asset")


__all__ = ["GoogleVeoVideoActionConnector"]
