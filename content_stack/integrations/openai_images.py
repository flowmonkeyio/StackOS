"""OpenAI Images integration wrapper (PLAN.md L1050).

Authentication: Bearer ``Authorization: Bearer <api_key>``. This is a
daemon-side vendor key for image generation only; prose generation uses
the current operator agent's runtime credentials outside content-stack.

Operation:

- ``generate(prompt, size, quality, n)`` — GPT Image generation.

The current OpenAI Image API returns base64 for GPT Image models. This
wrapper persists those bytes into the daemon's generated-assets directory
and returns local artifact URLs.

Cost: the table below is a rough budget guardrail. The operator should
reconcile actual invoices against OpenAI's current pricing page.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Any, ClassVar

from content_stack.integrations._base import BaseIntegration, IntegrationCallResult
from content_stack.mcp.errors import IntegrationDownError


class OpenAIImagesIntegration(BaseIntegration):
    """Wrapper for ``https://api.openai.com/v1/images/generations``."""

    kind = "openai-images"
    vendor = "openai-images"
    default_qps = 10.0

    BASE_URL = "https://api.openai.com/v1"

    # Official refs: https://platform.openai.com/docs/api-reference/images and
    # https://platform.openai.com/docs/models/gpt-image-1.5. Keep this rough
    # guardrail in sync with pricing audits; OpenAI billing remains canonical.
    _IMAGE_COSTS: ClassVar[dict[tuple[str, str], float]] = {
        ("1024x1024", "low"): 0.02,
        ("1024x1024", "medium"): 0.04,
        ("1024x1024", "high"): 0.08,
        ("1536x1024", "low"): 0.04,
        ("1536x1024", "medium"): 0.08,
        ("1536x1024", "high"): 0.16,
        ("1024x1536", "low"): 0.04,
        ("1024x1536", "medium"): 0.08,
        ("1024x1536", "high"): 0.16,
        ("1024x1024", "standard"): 0.04,
        ("1024x1024", "hd"): 0.08,
    }
    _DALL_E_MODELS: ClassVar[frozenset[str]] = frozenset({"dall-e-2", "dall-e-3"})
    _OUTPUT_EXTENSIONS: ClassVar[dict[str, str]] = {
        "jpeg": "jpg",
        "png": "png",
        "webp": "webp",
    }

    def __init__(
        self,
        *,
        asset_dir: Path | None = None,
        asset_url_prefix: str = "/generated-assets",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = self.payload.decode("utf-8")
        self._asset_dir = asset_dir
        self._asset_url_prefix = asset_url_prefix.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        """Estimate cost from ``size`` + ``quality`` keyword args."""
        del op
        body = kwargs.get("json", {}) or {}
        size = body.get("size", "1024x1024")
        quality = body.get("quality", "standard")
        n = int(body.get("n", 1))
        per_image = self._IMAGE_COSTS.get((size, quality), 0.04)
        return per_image * n

    async def generate(
        self,
        *,
        prompt: str,
        size: str = "1536x1024",
        quality: str = "medium",
        n: int = 1,
        model: str = "gpt-image-1.5",
        output_format: str = "webp",
    ) -> IntegrationCallResult:
        """Generate ``n`` images from ``prompt``.

        GPT Image models return base64 image bytes. When ``asset_dir`` is
        configured, the wrapper writes those bytes to disk and returns a
        response with local ``url`` fields and no ``b64_json`` payloads.
        """
        body = {
            "prompt": prompt,
            "n": n,
            "model": model,
        }
        # DALL-E keeps legacy size/quality names while GPT Image models use
        # low/medium/high quality and output_format.
        if model in self._DALL_E_MODELS:
            body["size"] = _dalle_size(size)
            body["quality"] = _dalle_quality(quality)
            body["response_format"] = "url"
        else:
            body["size"] = size
            body["quality"] = quality
            body["output_format"] = output_format
        result = await self.call(
            op="generate",
            method="POST",
            url=f"{self.BASE_URL}/images/generations",
            json_body=body,
            headers=self._auth_headers(),
        )
        return IntegrationCallResult(
            data=self._persist_base64_images(
                result.data,
                output_format=output_format,
                model=model,
            ),
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
            cached=result.cached,
        )

    def _persist_base64_images(
        self,
        data: Any,
        *,
        output_format: str,
        model: str,
    ) -> Any:
        """Replace OpenAI ``b64_json`` entries with daemon-local URLs."""
        if self._asset_dir is None or not isinstance(data, dict):
            return data
        items = data.get("data")
        if not isinstance(items, list):
            return data

        out = dict(data)
        persisted: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                persisted.append(item)
                continue
            b64 = item.get("b64_json")
            if not isinstance(b64, str):
                persisted.append(item)
                continue
            try:
                raw = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IntegrationDownError(
                    "OpenAI Images returned invalid base64 image data",
                    data={"vendor": "openai-images", "model": model},
                ) from exc
            ext = self._OUTPUT_EXTENSIONS.get(output_format, "webp")
            digest = hashlib.sha256(raw).hexdigest()
            filename = f"openai-{digest[:32]}.{ext}"
            subdir = self._asset_dir / "openai-images"
            subdir.mkdir(parents=True, exist_ok=True)
            path = subdir / filename
            if not path.exists():
                path.write_bytes(raw)
            clean = {k: v for k, v in item.items() if k != "b64_json"}
            clean["url"] = f"{self._asset_url_prefix}/openai-images/{filename}"
            clean["file_format"] = ext
            clean["source_model"] = model
            persisted.append(clean)
        out["data"] = persisted
        return out

    async def test_credentials(self) -> dict[str, Any]:
        """Cheap auth probe — list models (free, validates auth)."""
        result = await self.call(
            op="test",
            method="GET",
            url=f"{self.BASE_URL}/models",
            headers=self._auth_headers(),
        )
        models_count = len(result.data.get("data", [])) if isinstance(result.data, dict) else 0
        return {"ok": True, "vendor": "openai-images", "models_count": models_count}


__all__ = ["OpenAIImagesIntegration"]


def _dalle_quality(quality: str) -> str:
    if quality in {"standard", "hd"}:
        return quality
    return "hd" if quality == "high" else "standard"


def _dalle_size(size: str) -> str:
    if size == "1536x1024":
        return "1792x1024"
    if size == "1024x1536":
        return "1024x1792"
    return size
