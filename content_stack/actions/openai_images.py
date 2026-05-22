"""OpenAI Images connector for generic StackOS actions."""

from __future__ import annotations

import httpx

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.config import Settings
from content_stack.integrations.openai_images import OpenAIImagesIntegration
from content_stack.repositories.base import ValidationError


class OpenAIImagesActionConnector:
    """Decision-free adapter from ``utils.image.generate`` to the vendor wrapper."""

    key = "openai-images"

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
        if not isinstance(size, str) or size not in {
            "1024x1024",
            "1536x1024",
            "1024x1536",
            "1792x1024",
            "1024x1792",
        }:
            issues.append(
                ActionValidationIssue(
                    path="$.size",
                    message="size is not supported",
                    code="enum_mismatch",
                )
            )
        quality = payload.get("quality", "medium")
        if not isinstance(quality, str) or quality not in {
            "low",
            "medium",
            "high",
            "standard",
            "hd",
        }:
            issues.append(
                ActionValidationIssue(
                    path="$.quality",
                    message="quality is not supported",
                    code="enum_mismatch",
                )
            )
        output_format = payload.get("output_format", "webp")
        if not isinstance(output_format, str) or output_format not in {"webp", "png", "jpeg"}:
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
        size = str(payload.get("size", "1536x1024"))
        quality = str(payload.get("quality", "medium"))
        raw_n = payload.get("n", 1)
        n = raw_n if isinstance(raw_n, int) and not isinstance(raw_n, bool) else 1
        per_image = OpenAIImagesIntegration._IMAGE_COSTS.get((size, quality), 0.04)
        return max(0, round(per_image * n * 100))

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
                model=str(payload.get("model", "gpt-image-1.5")),
                output_format=str(payload.get("output_format", "webp")),
            )
        return ActionConnectorResult(
            output_json=result.data if isinstance(result.data, dict) else {"data": result.data},
            metadata_json={"vendor": "openai-images"},
            cost_cents=max(0, round(result.cost_usd * 100)),
        )


__all__ = ["OpenAIImagesActionConnector"]
