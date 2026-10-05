"""OpenAI Images connector for generic StackOS actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import httpx
from stackos_connectors.connectors.openai_images.integration import OpenAIImagesIntegration

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.media_artifacts import execute_media_native, media_projection_failure
from stackos.config import Settings
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository


class OpenAIImagesActionConnector:
    """Decision-free adapter from utils image actions to the vendor wrapper."""

    key = "openai-images"
    _SUPPORTED_MODELS = OpenAIImagesIntegration._GPT_IMAGE_MODELS
    _FIDELITY_MODELS = OpenAIImagesIntegration._INPUT_FIDELITY_MODELS
    _MAX_INPUT_IMAGES = OpenAIImagesIntegration.MAX_EDIT_INPUT_IMAGES
    _SUPPORTED_SIZES = frozenset({"auto", "1024x1024", "1536x1024", "1024x1536"})
    _SUPPORTED_QUALITIES = frozenset({"auto", "low", "medium", "high"})
    _SUPPORTED_FORMATS = frozenset({"webp", "png", "jpeg"})
    _SUPPORTED_FIDELITY = frozenset({"high", "low"})
    _PROMPT_MAX_CHARS = OpenAIImagesIntegration.MAX_PROMPT_CHARS

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        issues = self._validate_common(request)
        if request.operation == "image.edit":
            issues.extend(self._validate_edit(request))
        return issues

    def _validate_common(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        payload = request.input_json
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            issues.append(
                ActionValidationIssue(
                    path="$.prompt",
                    message="prompt is required",
                    code="required",
                )
            )
        elif len(prompt) > self._PROMPT_MAX_CHARS:
            issues.append(
                ActionValidationIssue(
                    path="$.prompt",
                    message=f"prompt must be at most {self._PROMPT_MAX_CHARS} characters",
                    code="range",
                )
            )
        model = payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL)
        if not isinstance(model, str) or model not in self._SUPPORTED_MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.model",
                    message="model must be one of the supported GPT Image model profiles",
                    code="enum_mismatch",
                )
            )
            model = OpenAIImagesIntegration.DEFAULT_MODEL
        n = payload.get("n", 1)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 10:
            issues.append(
                ActionValidationIssue(
                    path="$.n",
                    message="n must be an integer between 1 and 10",
                    code="range",
                )
            )
        size = payload.get("size", self._default_size(request.operation))
        if not isinstance(size, str) or not self._size_supported(model=str(model), size=size):
            issues.append(
                ActionValidationIssue(
                    path="$.size",
                    message=(
                        "size must be a supported StackOS GPT Image size profile: "
                        "auto, 1024x1024, 1536x1024, or 1024x1536"
                    ),
                    code="enum_mismatch",
                )
            )
        quality = payload.get("quality", "medium")
        if not isinstance(quality, str) or quality not in self._SUPPORTED_QUALITIES:
            issues.append(
                ActionValidationIssue(
                    path="$.quality",
                    message="quality must be one of the supported GPT Image quality profiles",
                    code="enum_mismatch",
                )
            )
        output_format = payload.get("output_format", "webp")
        if not isinstance(output_format, str) or output_format not in self._SUPPORTED_FORMATS:
            issues.append(
                ActionValidationIssue(
                    path="$.output_format",
                    message="output_format is not supported",
                    code="enum_mismatch",
                )
            )
        return issues

    def _validate_edit(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        payload = request.input_json
        refs = payload.get("input_image_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(ref, str) and ref.strip() for ref in refs)
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message="input_image_refs must be a non-empty list of artifact refs",
                    code="required",
                )
            )
        elif len(refs) > self._MAX_INPUT_IMAGES:
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message=f"input_image_refs accepts at most {self._MAX_INPUT_IMAGES} images",
                    code="range",
                )
            )
        fidelity = payload.get("input_fidelity")
        model = payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL)
        if fidelity is not None:
            if not isinstance(fidelity, str) or fidelity not in self._SUPPORTED_FIDELITY:
                issues.append(
                    ActionValidationIssue(
                        path="$.input_fidelity",
                        message="input_fidelity must be 'high' or 'low'",
                        code="enum_mismatch",
                    )
                )
            elif model not in self._FIDELITY_MODELS:
                issues.append(
                    ActionValidationIssue(
                        path="$.input_fidelity",
                        message=(
                            "input_fidelity is configurable for gpt-image-1.5, "
                            "gpt-image-1, and gpt-image-1-mini; gpt-image-2 always "
                            "uses high input fidelity"
                        ),
                        code="model_mismatch",
                    )
                )
        return issues

    @classmethod
    def _default_size(cls, operation: str) -> str:
        return "auto" if operation == "image.edit" else "1536x1024"

    @classmethod
    def _size_supported(cls, *, model: str, size: str) -> bool:
        del model
        return size in cls._SUPPORTED_SIZES

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        payload = request.input_json
        model = str(payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL))
        size = str(payload.get("size", self._default_size(request.operation)))
        quality = str(payload.get("quality", "medium"))
        raw_n = payload.get("n", 1)
        n = raw_n if isinstance(raw_n, int) and not isinstance(raw_n, bool) else 1
        estimated = _MediaPricing.estimate_image_cost_usd(
            model=model,
            size=size,
            quality=quality,
            n=n,
        )
        if estimated <= 0:
            return 0
        return max(1, round(estimated * 100))

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("openai-images action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        async with httpx.AsyncClient(timeout=120.0) as http:
            match request.operation:
                case "image.edit":
                    fidelity = payload.get("input_fidelity")
                    result = await execute_media_native(
                        request,
                        {
                            "prompt": str(payload["prompt"]),
                            "input_image_paths": [
                                _artifact_path(asset_dir, str(ref))
                                for ref in payload["input_image_refs"]
                            ],
                            "size": str(payload.get("size", "auto")),
                            "quality": str(payload.get("quality", "medium")),
                            "n": int(payload.get("n", 1)),
                            "model": str(
                                payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL)
                            ),
                            "output_format": str(payload.get("output_format", "webp")),
                            "input_fidelity": str(fidelity) if isinstance(fidelity, str) else None,
                        },
                        http=http,
                        connector=self.key,
                        output_subdir="openai-images",
                        qps=10.0,
                        pricing=_host_media_cost,
                    )
                case _:
                    result = await execute_media_native(
                        request,
                        {
                            "prompt": str(payload["prompt"]),
                            "size": str(payload.get("size", "1536x1024")),
                            "quality": str(payload.get("quality", "medium")),
                            "n": int(payload.get("n", 1)),
                            "model": str(
                                payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL)
                            ),
                            "output_format": str(payload.get("output_format", "webp")),
                        },
                        http=http,
                        connector=self.key,
                        output_subdir="openai-images",
                        qps=10.0,
                        pricing=_host_media_cost,
                    )
        try:
            output_json = result.data if isinstance(result.data, dict) else {"data": result.data}
            output_json = _register_generated_image_artifacts(request, output_json)
            return ActionConnectorResult(
                output_json=output_json,
                metadata_json={"vendor": "openai-images"},
                cost_cents=_cost_usd_to_cents(result.cost_usd),
            )
        except Exception as exc:
            raise media_projection_failure(exc, result) from None


def _artifact_path(asset_dir: Path, artifact_ref: str) -> Path:
    """Resolve a generated-assets artifact ref to a local file path."""
    if artifact_ref.startswith("/generated-assets/"):
        relative = artifact_ref.removeprefix("/generated-assets/")
    elif artifact_ref.startswith("generated-assets/"):
        relative = artifact_ref.removeprefix("generated-assets/")
    else:
        relative = artifact_ref.lstrip("/")
    base = asset_dir.resolve()
    candidate = (base / relative).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValidationError("input_image_refs must stay inside generated assets")
    if not candidate.is_file():
        raise ValidationError(f"input image ref {artifact_ref!r} does not point to a file")
    return candidate


def _register_generated_image_artifacts(
    request: ActionConnectorRequest,
    output_json: dict[str, Any],
) -> dict[str, Any]:
    """Register persisted generated image files as generic StackOS artifacts."""
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
        file_format = str(item.get("file_format") or path.suffix.removeprefix(".") or "webp")
        artifact = repository.create(
            project_id=request.project_id,
            plugin_slug="utils",
            kind="image",
            uri=uri,
            name=path.name,
            mime_type=_image_mime_type(file_format),
            size_bytes=path.stat().st_size,
            metadata_json={
                "provider_key": "openai-images",
                "operation": request.operation,
                "model": item.get("source_model"),
                "file_format": file_format,
            },
            provenance_json={
                "source": "openai-images-action",
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


def _image_mime_type(file_format: str) -> str:
    if file_format == "png":
        return "image/png"
    if file_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    return "image/webp"


def _cost_usd_to_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, round(cost_usd * 100))


__all__ = ["OpenAIImagesActionConnector"]


class _MediaPricing:
    _GPT_IMAGE_MODELS = OpenAIImagesIntegration._GPT_IMAGE_MODELS
    _IMAGE_COSTS: ClassVar[dict[tuple[str, str, str], float]] = {
        ("gpt-image-2", "1024x1024", "low"): 0.006,
        ("gpt-image-2", "1024x1024", "medium"): 0.053,
        ("gpt-image-2", "1024x1024", "high"): 0.211,
        ("gpt-image-2", "1536x1024", "low"): 0.005,
        ("gpt-image-2", "1536x1024", "medium"): 0.041,
        ("gpt-image-2", "1536x1024", "high"): 0.165,
        ("gpt-image-2", "1024x1536", "low"): 0.005,
        ("gpt-image-2", "1024x1536", "medium"): 0.041,
        ("gpt-image-2", "1024x1536", "high"): 0.165,
        ("gpt-image-1.5", "1024x1024", "low"): 0.009,
        ("gpt-image-1.5", "1024x1024", "medium"): 0.034,
        ("gpt-image-1.5", "1024x1024", "high"): 0.133,
        ("gpt-image-1.5", "1536x1024", "low"): 0.013,
        ("gpt-image-1.5", "1536x1024", "medium"): 0.050,
        ("gpt-image-1.5", "1536x1024", "high"): 0.200,
        ("gpt-image-1.5", "1024x1536", "low"): 0.013,
        ("gpt-image-1.5", "1024x1536", "medium"): 0.050,
        ("gpt-image-1.5", "1024x1536", "high"): 0.200,
        ("gpt-image-1", "1024x1024", "low"): 0.011,
        ("gpt-image-1", "1024x1024", "medium"): 0.042,
        ("gpt-image-1", "1024x1024", "high"): 0.167,
        ("gpt-image-1", "1536x1024", "low"): 0.016,
        ("gpt-image-1", "1536x1024", "medium"): 0.063,
        ("gpt-image-1", "1536x1024", "high"): 0.250,
        ("gpt-image-1", "1024x1536", "low"): 0.016,
        ("gpt-image-1", "1024x1536", "medium"): 0.063,
        ("gpt-image-1", "1024x1536", "high"): 0.250,
        ("gpt-image-1-mini", "1024x1024", "low"): 0.005,
        ("gpt-image-1-mini", "1024x1024", "medium"): 0.011,
        ("gpt-image-1-mini", "1024x1024", "high"): 0.036,
        ("gpt-image-1-mini", "1536x1024", "low"): 0.006,
        ("gpt-image-1-mini", "1536x1024", "medium"): 0.015,
        ("gpt-image-1-mini", "1536x1024", "high"): 0.052,
        ("gpt-image-1-mini", "1024x1536", "low"): 0.006,
        ("gpt-image-1-mini", "1024x1536", "medium"): 0.015,
        ("gpt-image-1-mini", "1024x1536", "high"): 0.052,
    }

    @classmethod
    def estimate_image_cost_usd(cls, *, model: str, size: str, quality: str, n: int = 1) -> float:
        """Estimate output-image cost for the current StackOS-supported sizes."""
        if model not in cls._GPT_IMAGE_MODELS:
            return 0.04 * n
        normalized_size = size if size != "auto" else "1024x1024"
        normalized_quality = quality if quality != "auto" else "high"
        per_image = cls._IMAGE_COSTS.get((model, normalized_size, normalized_quality))
        if per_image is None:
            model_costs = [
                cost
                for (cost_model, _size, cost_quality), cost in cls._IMAGE_COSTS.items()
                if cost_model == model and cost_quality == normalized_quality
            ]
            per_image = max(model_costs) if model_costs else 0.04
        return per_image * n


def _host_media_cost(operation: str, data: dict[str, Any], output: dict[str, Any]) -> float:
    estimated = _MediaPricing.estimate_image_cost_usd(
        model=data["model"], size=data["size"], quality=data["quality"], n=data["n"]
    )
    return estimated
