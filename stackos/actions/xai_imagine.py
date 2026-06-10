"""xAI Imagine connector for provider-specific media actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.config import Settings
from stackos.integrations.xai_imagine import XAIImagineIntegration
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository


class XAIImagineActionConnector:
    """Decision-free adapter from utils xAI actions to the vendor wrapper."""

    key = "xai-imagine"
    _IMAGE_MODEL = XAIImagineIntegration.IMAGE_MODEL
    _VIDEO_MODEL = XAIImagineIntegration.VIDEO_MODEL
    _VIDEO_MODELS = XAIImagineIntegration.VIDEO_MODELS
    _IMAGE_ASPECT_RATIOS = XAIImagineIntegration.IMAGE_ASPECT_RATIOS
    _IMAGE_RESOLUTIONS = XAIImagineIntegration.IMAGE_RESOLUTIONS
    _VIDEO_ASPECT_RATIOS = XAIImagineIntegration.VIDEO_ASPECT_RATIOS
    _VIDEO_RESOLUTIONS = XAIImagineIntegration.VIDEO_RESOLUTIONS
    _IMAGE_INPUT_MAX_BYTES = XAIImagineIntegration.IMAGE_INPUT_MAX_BYTES
    _MAX_IMAGE_EDIT_REFS = 3
    _MAX_VIDEO_REFERENCE_REFS = 7

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        issues = self._validate_prompt_and_model(request)
        match request.operation:
            case "image.generate":
                issues.extend(self._validate_image_generate(request))
            case "image.edit":
                issues.extend(self._validate_image_edit(request))
            case "video.generate":
                issues.extend(self._validate_video_generate(request))
            case _:
                issues.append(
                    ActionValidationIssue(
                        path="$.operation",
                        message=f"unsupported xAI Imagine operation {request.operation!r}",
                        code="unknown_operation",
                    )
                )
        return issues

    def _validate_prompt_and_model(
        self,
        request: ActionConnectorRequest,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            issues.append(
                ActionValidationIssue(
                    path="$.prompt", message="prompt is required", code="required"
                )
            )
        model = payload.get("model", self._default_model(request.operation))
        if request.operation == "video.generate":
            valid_model = isinstance(model, str) and model in self._VIDEO_MODELS
        else:
            valid_model = isinstance(model, str) and model == self._default_model(request.operation)
        if not valid_model:
            issues.append(
                ActionValidationIssue(
                    path="$.model",
                    message="model must be a supported Grok Imagine model for this action",
                    code="enum_mismatch",
                )
            )
        return issues

    def _validate_image_generate(
        self,
        request: ActionConnectorRequest,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        issues.extend(self._validate_image_controls(payload))
        n = payload.get("n", 1)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 10:
            issues.append(
                ActionValidationIssue(
                    path="$.n",
                    message="n must be an integer between 1 and 10",
                    code="range",
                )
            )
        return issues

    def _validate_image_edit(
        self,
        request: ActionConnectorRequest,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues = self._validate_image_controls(payload, aspect_optional=True)
        refs = payload.get("input_image_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message="input_image_refs must be a non-empty list of generated asset refs",
                    code="required",
                )
            )
        elif len(refs) > self._MAX_IMAGE_EDIT_REFS:
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message="xAI image edit accepts at most 3 source images",
                    code="range",
                )
            )
        elif len(refs) == 1 and payload.get("aspect_ratio") is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.aspect_ratio",
                    message="aspect_ratio is only supported for multi-image edits",
                    code="mode_mismatch",
                )
            )
        return issues

    def _validate_video_generate(
        self,
        request: ActionConnectorRequest,
    ) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        duration = payload.get("duration", 5)
        mode = payload.get("mode", "text-to-video")
        if mode not in {"text-to-video", "image-to-video", "reference-to-video"}:
            issues.append(
                ActionValidationIssue(
                    path="$.mode",
                    message="mode must be text-to-video, image-to-video, or reference-to-video",
                    code="enum_mismatch",
                )
            )
            mode = "text-to-video"
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or duration < 1
            or duration > 15
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.duration",
                    message="duration must be an integer between 1 and 15 seconds",
                    code="range",
                )
            )
        elif mode == "reference-to-video" and duration > 10:
            issues.append(
                ActionValidationIssue(
                    path="$.duration",
                    message="reference-to-video duration must be at most 10 seconds",
                    code="range",
                )
            )
        aspect_ratio = payload.get("aspect_ratio", "16:9")
        if not isinstance(aspect_ratio, str) or aspect_ratio not in self._VIDEO_ASPECT_RATIOS:
            issues.append(
                ActionValidationIssue(
                    path="$.aspect_ratio",
                    message="aspect_ratio must be a supported xAI video ratio",
                    code="enum_mismatch",
                )
            )
        resolution = payload.get("resolution", "480p")
        if not isinstance(resolution, str) or resolution not in self._VIDEO_RESOLUTIONS:
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message="resolution must be 480p or 720p",
                    code="enum_mismatch",
                )
            )
        poll_interval = payload.get("poll_interval_seconds", 5)
        if (
            not isinstance(poll_interval, int | float)
            or isinstance(poll_interval, bool)
            or poll_interval < 1
            or poll_interval > 30
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.poll_interval_seconds",
                    message="poll_interval_seconds must be between 1 and 30",
                    code="range",
                )
            )
        poll_timeout = payload.get("poll_timeout_seconds", 900)
        if (
            not isinstance(poll_timeout, int | float)
            or isinstance(poll_timeout, bool)
            or poll_timeout < 60
            or poll_timeout > 1800
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.poll_timeout_seconds",
                    message="poll_timeout_seconds must be between 60 and 1800",
                    code="range",
                )
            )
        input_ref = payload.get("input_image_ref")
        reference_refs = payload.get("reference_image_refs")
        if mode == "image-to-video" and not isinstance(input_ref, str):
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_ref",
                    message="input_image_ref is required for image-to-video",
                    code="required",
                )
            )
        if mode != "image-to-video" and input_ref is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_ref",
                    message="input_image_ref is only valid for image-to-video",
                    code="mode_mismatch",
                )
            )
        if mode == "reference-to-video":
            if (
                not isinstance(reference_refs, list)
                or not reference_refs
                or not all(isinstance(ref, str) for ref in reference_refs)
            ):
                issues.append(
                    ActionValidationIssue(
                        path="$.reference_image_refs",
                        message="reference_image_refs is required for reference-to-video",
                        code="required",
                    )
                )
            elif len(reference_refs) > self._MAX_VIDEO_REFERENCE_REFS:
                issues.append(
                    ActionValidationIssue(
                        path="$.reference_image_refs",
                        message="reference-to-video accepts at most 7 reference images",
                        code="range",
                    )
                )
        elif reference_refs is not None:
            issues.append(
                ActionValidationIssue(
                    path="$.reference_image_refs",
                    message="reference_image_refs is only valid for reference-to-video",
                    code="mode_mismatch",
                )
            )
        return issues

    def _validate_image_controls(
        self,
        payload: dict[str, Any],
        *,
        aspect_optional: bool = False,
    ) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        aspect_ratio = payload.get("aspect_ratio")
        if aspect_ratio is None and not aspect_optional:
            aspect_ratio = "auto"
        if aspect_ratio is not None and (
            not isinstance(aspect_ratio, str) or aspect_ratio not in self._IMAGE_ASPECT_RATIOS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.aspect_ratio",
                    message="aspect_ratio must be a supported xAI image ratio",
                    code="enum_mismatch",
                )
            )
        resolution = payload.get("resolution", "1k")
        if not isinstance(resolution, str) or resolution not in self._IMAGE_RESOLUTIONS:
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message="resolution must be 1k or 2k",
                    code="enum_mismatch",
                )
            )
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        payload = request.input_json
        if request.operation == "video.generate":
            raw_duration = payload.get("duration", 5)
            duration = (
                raw_duration
                if isinstance(raw_duration, int) and not isinstance(raw_duration, bool)
                else 5
            )
            input_images = 0
            if isinstance(payload.get("input_image_ref"), str):
                input_images = 1
            elif isinstance(payload.get("reference_image_refs"), list):
                input_images = len(payload["reference_image_refs"])
            return _cost_usd_to_cents(
                XAIImagineIntegration.estimate_video_cost_usd(
                    seconds=duration,
                    resolution=str(payload.get("resolution", "480p")),
                    input_images=input_images,
                    model=str(payload.get("model", self._VIDEO_MODEL)),
                )
            )
        raw_n = payload.get("n", 1)
        n = raw_n if isinstance(raw_n, int) and not isinstance(raw_n, bool) else 1
        input_images = (
            len(payload["input_image_refs"])
            if isinstance(payload.get("input_image_refs"), list)
            else 0
        )
        return _cost_usd_to_cents(
            XAIImagineIntegration.estimate_image_cost_usd(
                n=n,
                resolution=str(payload.get("resolution", "1k")),
                input_images=input_images,
            )
        )

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("xai-imagine action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        async with httpx.AsyncClient(timeout=180.0) as http:
            client = XAIImagineIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                asset_dir=asset_dir,
            )
            match request.operation:
                case "image.edit":
                    result = await client.edit_image(
                        prompt=str(payload["prompt"]),
                        input_image_paths=[
                            _artifact_path(asset_dir, str(ref))
                            for ref in payload["input_image_refs"]
                        ],
                        aspect_ratio=(
                            str(payload["aspect_ratio"])
                            if isinstance(payload.get("aspect_ratio"), str)
                            else None
                        ),
                        resolution=str(payload.get("resolution", "1k")),
                        model=str(payload.get("model", self._IMAGE_MODEL)),
                    )
                    media_kind = "image"
                case "video.generate":
                    result = await client.generate_video(
                        prompt=str(payload["prompt"]),
                        duration=int(payload.get("duration", 5)),
                        aspect_ratio=str(payload.get("aspect_ratio", "16:9")),
                        resolution=str(payload.get("resolution", "480p")),
                        model=str(payload.get("model", self._VIDEO_MODEL)),
                        image_path=(
                            _artifact_path(asset_dir, str(payload["input_image_ref"]))
                            if isinstance(payload.get("input_image_ref"), str)
                            else None
                        ),
                        reference_image_paths=[
                            _artifact_path(asset_dir, str(ref))
                            for ref in payload.get("reference_image_refs", [])
                        ]
                        if isinstance(payload.get("reference_image_refs"), list)
                        else None,
                        poll_interval_seconds=float(payload.get("poll_interval_seconds", 5)),
                        poll_timeout_seconds=float(payload.get("poll_timeout_seconds", 900)),
                    )
                    media_kind = "video"
                case _:
                    result = await client.generate_image(
                        prompt=str(payload["prompt"]),
                        aspect_ratio=str(payload.get("aspect_ratio", "auto")),
                        resolution=str(payload.get("resolution", "1k")),
                        n=int(payload.get("n", 1)),
                        model=str(payload.get("model", self._IMAGE_MODEL)),
                    )
                    media_kind = "image"
        output_json = result.data if isinstance(result.data, dict) else {"data": result.data}
        output_json = _register_generated_media_artifacts(request, output_json, kind=media_kind)
        return ActionConnectorResult(
            output_json=output_json,
            metadata_json={"vendor": "xai-imagine"},
            cost_cents=_cost_usd_to_cents(result.cost_usd),
        )

    @classmethod
    def _default_model(cls, operation: str) -> str:
        return cls._VIDEO_MODEL if operation == "video.generate" else cls._IMAGE_MODEL


def _artifact_path(asset_dir: Path, artifact_ref: str) -> Path:
    if artifact_ref.startswith("/generated-assets/"):
        relative = artifact_ref.removeprefix("/generated-assets/")
    elif artifact_ref.startswith("generated-assets/"):
        relative = artifact_ref.removeprefix("generated-assets/")
    else:
        relative = artifact_ref.lstrip("/")
    base = asset_dir.resolve()
    candidate = (base / relative).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValidationError("media refs must stay inside generated assets")
    if not candidate.is_file():
        raise ValidationError(f"media ref {artifact_ref!r} does not point to a file")
    return candidate


def _register_generated_media_artifacts(
    request: ActionConnectorRequest,
    output_json: dict[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    if request.session is None:
        return output_json
    items = output_json.get("data")
    if not isinstance(items, list):
        return output_json
    asset_dir = (request.asset_dir or Settings().generated_assets_dir).resolve()
    repository = ArtifactRepository(request.session)
    registered_items: list[Any] = []
    artifact_refs: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            registered_items.append(item)
            continue
        uri = item.get("url")
        if not isinstance(uri, str) or not uri.startswith("/generated-assets/"):
            registered_items.append(item)
            continue
        path = _artifact_path(asset_dir, uri)
        file_format = str(item.get("file_format") or path.suffix.removeprefix(".") or "bin")
        artifact = repository.create(
            project_id=request.project_id,
            plugin_slug="utils",
            kind=kind,
            uri=uri,
            name=path.name,
            mime_type=_mime_type(kind=kind, file_format=file_format),
            size_bytes=path.stat().st_size,
            metadata_json={
                "provider_key": "xai-imagine",
                "operation": request.operation,
                "model": item.get("source_model"),
                "request_id": item.get("request_id"),
                "file_format": file_format,
            },
            provenance_json={
                "source": "xai-imagine-action",
                "action_ref": request.action_ref,
            },
        ).data
        clean = dict(item)
        clean["artifact_ref"] = uri
        clean["artifact_id"] = artifact.id
        registered_items.append(clean)
        artifact_refs.append(uri)
    if not artifact_refs:
        return output_json
    out = dict(output_json)
    out["data"] = registered_items
    out["artifact_refs"] = artifact_refs
    return out


def _mime_type(*, kind: str, file_format: str) -> str:
    if kind == "video":
        return "video/mp4"
    if file_format == "png":
        return "image/png"
    if file_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    return "image/webp"


def _cost_usd_to_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, round(cost_usd * 100))


__all__ = ["XAIImagineActionConnector"]
