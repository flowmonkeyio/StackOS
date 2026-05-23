"""OpenAI Images connector for generic StackOS actions."""

from __future__ import annotations

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.config import Settings
from stackos.integrations.openai_images import OpenAIImagesIntegration
from stackos.repositories.base import ValidationError


class OpenAIImagesActionConnector:
    """Decision-free adapter from ``utils.image.generate`` to the vendor wrapper."""

    key = "openai-images"
    _SUPPORTED_MODELS = OpenAIImagesIntegration._GPT_IMAGE_MODELS
    _SUPPORTED_SIZES = frozenset({"auto", "1024x1024", "1536x1024", "1024x1536"})
    _SUPPORTED_QUALITIES = frozenset({"auto", "low", "medium", "high"})
    _SUPPORTED_FORMATS = frozenset({"webp", "png", "jpeg"})

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
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
        model = payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL)
        if not isinstance(model, str) or model not in self._SUPPORTED_MODELS:
            issues.append(
                ActionValidationIssue(
                    path="$.model",
                    message="model must be one of the supported GPT Image model profiles",
                    code="enum_mismatch",
                )
            )
        n = payload.get("n", 1)
        if not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 10:
            issues.append(
                ActionValidationIssue(
                    path="$.n",
                    message="n must be an integer between 1 and 10",
                    code="range",
                )
            )
        size = payload.get("size", "1536x1024")
        if not isinstance(size, str) or size not in self._SUPPORTED_SIZES:
            issues.append(
                ActionValidationIssue(
                    path="$.size",
                    message="size must be one of the supported GPT Image size profiles",
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

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        payload = request.input_json
        model = str(payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL))
        size = str(payload.get("size", "1536x1024"))
        quality = str(payload.get("quality", "medium"))
        raw_n = payload.get("n", 1)
        n = raw_n if isinstance(raw_n, int) and not isinstance(raw_n, bool) else 1
        estimated = OpenAIImagesIntegration.estimate_image_cost_usd(
            model=model,
            size=size,
            quality=quality,
            n=n,
        )
        return max(0, round(estimated * 100))

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("openai-images action requires a resolved credential")
        payload = request.input_json
        asset_dir = request.asset_dir or Settings().generated_assets_dir
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = OpenAIImagesIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                asset_dir=asset_dir,
            )
            result = await client.generate(
                prompt=str(payload["prompt"]),
                size=str(payload.get("size", "1536x1024")),
                quality=str(payload.get("quality", "medium")),
                n=int(payload.get("n", 1)),
                model=str(payload.get("model", OpenAIImagesIntegration.DEFAULT_MODEL)),
                output_format=str(payload.get("output_format", "webp")),
            )
        return ActionConnectorResult(
            output_json=result.data if isinstance(result.data, dict) else {"data": result.data},
            metadata_json={"vendor": "openai-images"},
            cost_cents=max(0, round(result.cost_usd * 100)),
        )


__all__ = ["OpenAIImagesActionConnector"]
