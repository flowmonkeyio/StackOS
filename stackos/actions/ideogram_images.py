"""Ideogram image action connector."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import httpx
from stackos_connectors.connectors.ideogram.integration import IdeogramImagesIntegration
from stackos_connectors.errors import IntegrationDownError

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.media_artifacts import execute_media_native, media_projection_failure
from stackos.config import Settings
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository


class IdeogramImagesActionConnector:
    """Decision-free adapter from utils Ideogram image actions to the wrapper."""

    key = "ideogram"
    _RESOLUTIONS = frozenset(IdeogramImagesIntegration.RESOLUTIONS)
    _RENDERING_SPEEDS = IdeogramImagesIntegration.RENDERING_SPEEDS

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues = self._validate_common(payload)
        match request.operation:
            case "image.generate":
                issues.extend(self._validate_generate_prompt(payload))
            case "image.remix":
                issues.extend(self._validate_remix(request))
            case _:
                issues.append(
                    ActionValidationIssue(
                        path="$.operation",
                        message=f"unsupported Ideogram operation {request.operation!r}",
                        code="unknown_operation",
                    )
                )
        return issues

    def _validate_common(self, payload: dict[str, Any]) -> list[ActionValidationIssue]:
        issues: list[ActionValidationIssue] = []
        resolution = payload.get("resolution")
        if resolution is not None and (
            not isinstance(resolution, str) or resolution not in self._RESOLUTIONS
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.resolution",
                    message="resolution must be a documented Ideogram 4.0 2K resolution",
                    code="enum_mismatch",
                )
            )
        rendering_speed = payload.get("rendering_speed", "DEFAULT")
        if not isinstance(rendering_speed, str) or rendering_speed not in self._RENDERING_SPEEDS:
            issues.append(
                ActionValidationIssue(
                    path="$.rendering_speed",
                    message="rendering_speed must be TURBO, DEFAULT, or QUALITY",
                    code="enum_mismatch",
                )
            )
        enable_copyright_detection = payload.get("enable_copyright_detection")
        if enable_copyright_detection is not None and not isinstance(
            enable_copyright_detection, bool
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.enable_copyright_detection",
                    message="enable_copyright_detection must be a boolean when provided",
                    code="type_mismatch",
                )
            )
        if "json_prompt" in payload:
            issues.append(
                ActionValidationIssue(
                    path="$.json_prompt",
                    message="json_prompt is not exposed until its nested contract is modeled",
                    code="unsupported",
                )
            )
        return issues

    def _validate_generate_prompt(
        self,
        payload: dict[str, Any],
    ) -> list[ActionValidationIssue]:
        text_prompt = payload.get("text_prompt")
        if isinstance(text_prompt, str) and text_prompt.strip():
            return []
        return [
            ActionValidationIssue(
                path="$.text_prompt",
                message="text_prompt is required",
                code="required",
            )
        ]

    def _validate_remix(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues = self._validate_generate_prompt(payload)
        image_ref = payload.get("input_image_ref")
        if not isinstance(image_ref, str):
            issues.append(
                ActionValidationIssue(
                    path="$.input_image_ref",
                    message="input_image_ref is required for Ideogram remix",
                    code="required",
                )
            )
        else:
            asset_dir = request.asset_dir or Settings().generated_assets_dir
            try:
                IdeogramImagesIntegration.ensure_image_preflight(
                    _artifact_path(asset_dir, image_ref)
                )
            except (IntegrationDownError, ValidationError) as exc:
                issues.append(
                    ActionValidationIssue(
                        path="$.input_image_ref",
                        message=getattr(exc, "detail", str(exc)),
                        code="invalid_image_ref",
                    )
                )
        image_weight = payload.get("image_weight")
        if image_weight is not None and (
            not isinstance(image_weight, int)
            or isinstance(image_weight, bool)
            or image_weight < 1
            or image_weight > 100
        ):
            issues.append(
                ActionValidationIssue(
                    path="$.image_weight",
                    message="image_weight must be an integer from 1 to 100",
                    code="range",
                )
            )
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        payload = request.input_json
        rendering_speed = str(payload.get("rendering_speed") or "DEFAULT")
        estimated = _MediaPricing.estimate_cost_usd(rendering_speed=rendering_speed)
        if estimated <= 0:
            return 0
        return max(1, round(estimated * 100))

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("Ideogram action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        rendering_speed = str(payload.get("rendering_speed") or "DEFAULT")
        async with httpx.AsyncClient(timeout=180.0) as http:
            if request.operation == "image.remix":
                result = await execute_media_native(
                    request,
                    {
                        "text_prompt": str(payload["text_prompt"]),
                        "image_path": _artifact_path(asset_dir, str(payload["input_image_ref"])),
                        "image_weight": int(payload["image_weight"])
                        if isinstance(payload.get("image_weight"), int)
                        and (not isinstance(payload.get("image_weight"), bool))
                        else None,
                        "resolution": str(payload["resolution"])
                        if isinstance(payload.get("resolution"), str)
                        else None,
                        "rendering_speed": rendering_speed,
                        "enable_copyright_detection": payload["enable_copyright_detection"]
                        if isinstance(payload.get("enable_copyright_detection"), bool)
                        else None,
                    },
                    http=http,
                    connector=self.key,
                    output_subdir="ideogram",
                    qps=1.0,
                    pricing=_host_media_cost,
                )
            else:
                result = await execute_media_native(
                    request,
                    {
                        "text_prompt": str(payload["text_prompt"]),
                        "resolution": str(payload["resolution"])
                        if isinstance(payload.get("resolution"), str)
                        else None,
                        "rendering_speed": rendering_speed,
                        "enable_copyright_detection": payload["enable_copyright_detection"]
                        if isinstance(payload.get("enable_copyright_detection"), bool)
                        else None,
                    },
                    http=http,
                    connector=self.key,
                    output_subdir="ideogram",
                    qps=1.0,
                    pricing=_host_media_cost,
                )
        try:
            output_json = result.data if isinstance(result.data, dict) else {"data": result.data}
            output_json = _register_generated_image_artifacts(request, output_json)
            return ActionConnectorResult(
                output_json=output_json,
                metadata_json={"vendor": "ideogram"},
                cost_cents=_cost_usd_to_cents(result.cost_usd),
            )
        except Exception as exc:
            raise media_projection_failure(exc, result) from None


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
        raise ValidationError("input_image_ref must stay inside generated assets")
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
        file_format = str(item.get("file_format") or path.suffix.removeprefix(".") or "png")
        artifact = repository.create(
            project_id=request.project_id,
            plugin_slug="utils",
            kind="image",
            uri=uri,
            name=path.name,
            mime_type=_mime_type(file_format),
            size_bytes=path.stat().st_size,
            metadata_json={
                "provider_key": "ideogram",
                "operation": request.operation,
                "model": item.get("source_model"),
                "file_format": file_format,
                "resolution": item.get("resolution"),
                "is_image_safe": item.get("is_image_safe"),
                "seed": item.get("seed"),
            },
            provenance_json={
                "source": "ideogram-action",
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
    if file_format in {"jpg", "jpeg"}:
        return "image/jpeg"
    if file_format == "webp":
        return "image/webp"
    return "image/png"


def _cost_usd_to_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, round(cost_usd * 100))


__all__ = ["IdeogramImagesActionConnector"]


class _MediaPricing:
    _COSTS_USD: ClassVar[dict[str, float]] = {
        "TURBO": 0.03,
        "DEFAULT": 0.06,
        "QUALITY": 0.10,
    }

    @classmethod
    def estimate_cost_usd(cls, *, rendering_speed: str = "DEFAULT") -> float:
        return cls._COSTS_USD.get(rendering_speed, cls._COSTS_USD["DEFAULT"])

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
        items = response.get("data")
        if not isinstance(items, list) or not items:
            return estimated
        rendering_speed = "DEFAULT"
        if isinstance(request, dict):
            rendering_speed = str(request.get("rendering_speed") or "DEFAULT")
        return self.estimate_cost_usd(rendering_speed=rendering_speed) * len(items)


def _host_media_cost(operation: str, data: dict[str, Any], output: dict[str, Any]) -> float:
    estimated = _MediaPricing.estimate_cost_usd(rendering_speed=data["rendering_speed"])
    return _MediaPricing()._extract_actual_cost_usd(
        operation, request=data, response=output, estimated=estimated
    )
