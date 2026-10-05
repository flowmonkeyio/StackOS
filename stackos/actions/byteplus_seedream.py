"""BytePlus Seedream image action connector."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import httpx
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.byteplus.ark import BytePlusArkIntegration
from stackos_connectors.shared.byteplus.media import _generated_image_count

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.media_artifacts import execute_media_native, media_projection_failure
from stackos.config import Settings
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository


class BytePlusSeedreamImageActionConnector:
    """Decision-free adapter from utils BytePlus Seedream actions to ModelArk."""

    key = "byteplus-seedream"
    _MODELS = BytePlusArkIntegration.SEEDREAM_MODELS
    _REGIONS = frozenset(BytePlusArkIntegration.REGION_BASE_URLS)

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues = self._validate_common(payload)
        match request.operation:
            case "image.generate":
                issues.extend(self._validate_prompt(payload))
            case "image.edit":
                issues.extend(self._validate_prompt(payload))
                issues.extend(self._validate_input_refs(request))
                issues.extend(self._validate_combined_image_count(request))
            case _:
                issues.append(
                    ActionValidationIssue(
                        path="$.operation",
                        message=f"unsupported BytePlus Seedream operation {request.operation!r}",
                        code="unknown_operation",
                    )
                )
        return issues

    def _validate_common(self, payload: dict[str, Any]) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        model = self._model(payload)
        if not isinstance(payload.get("model", model), str) or model not in self._MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.model",
                    message="model must be a supported BytePlus Seedream image model",
                    code="enum_mismatch",
                )
            )
        region = payload.get("region", BytePlusArkIntegration.DEFAULT_REGION)
        if not isinstance(region, str) or region not in self._REGIONS:
            issues.append(
                ActionValidationIssue(
                    path="$.region",
                    message="region must be ap-southeast-1 or eu-west-1",
                    code="enum_mismatch",
                )
            )
        elif region == "eu-west-1" and model not in BytePlusArkIntegration.EU_WEST_MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.region",
                    message="eu-west-1 currently supports only seedream-5-0-lite-260128",
                    code="model_mismatch",
                )
            )
        size = payload.get("size", "2K")
        if not isinstance(size, str) or not BytePlusArkIntegration.validate_size(
            size,
            model=model,
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.size",
                    message=(
                        "size must be a model-supported shortcut or WxH within "
                        "BytePlus Seedream custom size limits"
                    ),
                    code="enum_mismatch",
                )
            )
        sequential = payload.get("sequential_image_generation", "disabled")
        if (
            not isinstance(sequential, str)
            or sequential not in BytePlusArkIntegration.SEQUENTIAL_IMAGE_GENERATION_VALUES
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.sequential_image_generation",
                    message="sequential_image_generation must be disabled or auto",
                    code="enum_mismatch",
                )
            )
        max_images = payload.get("max_images")
        if max_images is not None and (
            not isinstance(max_images, int)
            or isinstance(max_images, bool)
            or max_images < 1
            or max_images > 15
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.max_images",
                    message="max_images must be an integer from 1 to 15",
                    code="range",
                )
            )
        elif max_images is not None and sequential != "auto":
            issues.append(
                ActionValidationIssue(
                    path="$.max_images",
                    message="max_images is only used when sequential_image_generation is auto",
                    code="unsupported",
                )
            )
        watermark = payload.get("watermark")
        if watermark is not None and not isinstance(watermark, bool):
            issues.append(
                ActionValidationIssue(
                    path="$.watermark",
                    message="watermark must be a boolean when provided",
                    code="type_mismatch",
                )
            )
        output_format = payload.get("output_format")
        if output_format is not None:
            if (
                not isinstance(output_format, str)
                or output_format not in BytePlusArkIntegration.OUTPUT_FORMATS
            ):
                issues.append(
                    ActionValidationIssue(
                        path="$.output_format",
                        message="output_format must be jpeg or png",
                        code="enum_mismatch",
                    )
                )
            elif model != BytePlusArkIntegration.DEFAULT_SEEDREAM_MODEL:
                issues.append(
                    ActionValidationIssue(
                        path="$.output_format",
                        message="output_format is only supported for seedream-5-0-lite-260128",
                        code="model_mismatch",
                    )
                )
        return issues

    def _requested_output_count(self, payload: dict[str, Any]) -> int:
        if payload.get("sequential_image_generation", "disabled") != "auto":
            return 1
        max_images = payload.get("max_images")
        if isinstance(max_images, int) and not isinstance(max_images, bool):
            return max(1, min(max_images, 15))
        return 15

    def _validate_prompt(self, payload: dict[str, Any]) -> list[ActionValidationIssue]:
        prompt = payload.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return []
        return [
            ActionValidationIssue(
                path="$.prompt",
                message="prompt is required",
                code="required",
            )
        ]

    def _validate_input_refs(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        refs = request.input_json.get("input_image_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
            return [
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message="input_image_refs must be a non-empty list of generated asset refs",
                    code="required",
                )
            ]
        if len(refs) > BytePlusArkIntegration.MAX_INPUT_IMAGES:
            return [
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message=(
                        f"BytePlus Seedream accepts at most "
                        f"{BytePlusArkIntegration.MAX_INPUT_IMAGES} reference images"
                    ),
                    code="range",
                )
            ]
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        try:
            paths = [_artifact_path(asset_dir, ref) for ref in refs]
            BytePlusArkIntegration.ensure_image_preflight(paths)
        except (IntegrationDownError, ValidationError) as exc:
            return [
                ActionValidationIssue(
                    path="$.input_image_refs",
                    message=getattr(exc, "detail", str(exc)),
                    code="invalid_image_ref",
                )
            ]
        return []

    def _validate_combined_image_count(
        self,
        request: ActionConnectorRequest,
    ) -> list[ActionValidationIssue]:
        refs = request.input_json.get("input_image_refs")
        if not isinstance(refs, list):
            return []
        total_images = len(refs) + self._requested_output_count(request.input_json)
        if total_images <= 15:
            return []
        return [
            ActionValidationIssue(
                path="$.max_images",
                message=(
                    "input_image_refs plus requested generated images must be at most "
                    "15 for BytePlus Seedream sequential generation"
                ),
                code="range",
            )
        ]

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        estimated = _MediaPricing.estimate_image_cost_usd(
            model=self._model(request.input_json),
            generated_images=self._requested_output_count(request.input_json),
        )
        return _cost_usd_to_cents(estimated)

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("BytePlus Seedream action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        model = self._model(payload)
        prompt = str(payload["prompt"])
        size = str(payload.get("size") or "2K")
        region = str(payload.get("region") or BytePlusArkIntegration.DEFAULT_REGION)
        sequential_image_generation = str(payload.get("sequential_image_generation") or "disabled")
        max_images = (
            int(payload["max_images"])
            if isinstance(payload.get("max_images"), int)
            and not isinstance(payload.get("max_images"), bool)
            else None
        )
        watermark = payload["watermark"] if isinstance(payload.get("watermark"), bool) else None
        output_format = (
            str(payload["output_format"]) if isinstance(payload.get("output_format"), str) else None
        )
        async with httpx.AsyncClient(timeout=180.0) as http:
            if request.operation == "image.edit":
                result = await execute_media_native(
                    request,
                    {
                        "prompt": prompt,
                        "model": model,
                        "size": size,
                        "region": region,
                        "sequential_image_generation": sequential_image_generation,
                        "max_images": max_images,
                        "watermark": watermark,
                        "output_format": output_format,
                        "input_image_paths": [
                            _artifact_path(asset_dir, str(ref))
                            for ref in payload["input_image_refs"]
                        ],
                    },
                    http=http,
                    connector=self.key,
                    output_subdir="byteplus-ark",
                    qps=1.0,
                    pricing=_host_media_cost,
                )
            else:
                result = await execute_media_native(
                    request,
                    {
                        "prompt": prompt,
                        "model": model,
                        "size": size,
                        "region": region,
                        "sequential_image_generation": sequential_image_generation,
                        "max_images": max_images,
                        "watermark": watermark,
                        "output_format": output_format,
                    },
                    http=http,
                    connector=self.key,
                    output_subdir="byteplus-ark",
                    qps=1.0,
                    pricing=_host_media_cost,
                )
        try:
            output_json = result.data if isinstance(result.data, dict) else {"data": result.data}
            output_json = _register_generated_image_artifacts(request, output_json)
            return ActionConnectorResult(
                output_json=output_json,
                metadata_json={"vendor": "byteplus-ark"},
                cost_cents=_cost_usd_to_cents(result.cost_usd),
            )
        except Exception as exc:
            raise media_projection_failure(exc, result) from None

    @staticmethod
    def _model(payload: dict[str, Any]) -> str:
        raw = payload.get("model", BytePlusArkIntegration.DEFAULT_SEEDREAM_MODEL)
        return raw if isinstance(raw, str) else BytePlusArkIntegration.DEFAULT_SEEDREAM_MODEL


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
        raise ValidationError("input_image_refs must stay inside generated assets")
    if not candidate.is_file():
        raise ValidationError(f"input image ref {artifact_ref!r} does not point to a file")
    return candidate


def _register_generated_image_artifacts(
    request: ActionConnectorRequest,
    output_json: dict[str, Any],
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
        file_format = str(item.get("file_format") or path.suffix.removeprefix(".") or "jpg")
        artifact = repository.create(
            project_id=request.project_id,
            plugin_slug="utils",
            kind="image",
            uri=uri,
            name=path.name,
            mime_type=_mime_type(file_format),
            size_bytes=path.stat().st_size,
            metadata_json={
                "provider_key": "byteplus-ark",
                "operation": request.operation,
                "model": output_json.get("model"),
                "file_format": file_format,
                "size": item.get("size"),
                "provider_url_persisted": item.get("provider_url_persisted"),
                "provider_b64_persisted": item.get("provider_b64_persisted"),
            },
            provenance_json={
                "source": "byteplus-seedream-action",
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


def _mime_type(file_format: str) -> str:
    if file_format in {"png"}:
        return "image/png"
    if file_format == "webp":
        return "image/webp"
    return "image/jpeg"


def _cost_usd_to_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, round(cost_usd * 100))


__all__ = ["BytePlusSeedreamImageActionConnector"]


class _MediaPricing:
    DEFAULT_SEEDREAM_MODEL = BytePlusArkIntegration.DEFAULT_SEEDREAM_MODEL
    _COSTS_USD: ClassVar[dict[str, float]] = {
        "seedream-5-0-lite-260128": 0.035,
        "seedream-4-5-251128": 0.04,
        "seedream-4-0-250828": 0.03,
    }

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


def _host_media_cost(operation: str, data: dict[str, Any], output: dict[str, Any]) -> float:
    estimated = _MediaPricing.estimate_image_cost_usd(model=data["model"])
    return _MediaPricing()._extract_actual_cost_usd(
        operation, request=data, response=output, estimated=estimated
    )
