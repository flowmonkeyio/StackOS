"""Ideogram image generation integration wrapper.

Official docs:

- https://developer.ideogram.ai/api-reference/api-reference/generate-v4
- https://developer.ideogram.ai/api-reference/api-reference/remix-v4
- https://ideogram.ai/api-pricing/

Ideogram v4 returns temporary image URLs. The wrapper downloads each URL
immediately, persists image bytes, and returns generated-assets URLs instead
of signed provider URLs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations._media import download_generated_media, write_generated_media
from stackos.mcp.errors import IntegrationDownError


class IdeogramImagesIntegration(BaseIntegration):
    """Wrapper for Ideogram first-party image endpoints."""

    kind = "ideogram"
    vendor = "ideogram"
    default_qps = 1.0

    BASE_URL = "https://api.ideogram.ai/v1"
    MODEL = "ideogram-v4"
    INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset({"jpg", "jpeg", "png", "webp"})
    MAX_INPUT_IMAGE_BYTES = 10_000_000
    RENDERING_SPEEDS: ClassVar[frozenset[str]] = frozenset({"TURBO", "DEFAULT", "QUALITY"})
    RESOLUTIONS: ClassVar[tuple[str, ...]] = (
        "2048x2048",
        "1440x2880",
        "2880x1440",
        "1664x2496",
        "2496x1664",
        "1792x2240",
        "2240x1792",
        "1440x2560",
        "2560x1440",
        "1600x2560",
        "2560x1600",
        "1728x2304",
        "2304x1728",
        "1296x3168",
        "3168x1296",
        "1152x2944",
        "2944x1152",
        "1248x3328",
        "3328x1248",
        "1280x3072",
        "3072x1280",
        "1024x3072",
        "3072x1024",
    )
    _COSTS_USD: ClassVar[dict[str, float]] = {
        "TURBO": 0.03,
        "DEFAULT": 0.06,
        "QUALITY": 0.10,
    }

    def __init__(
        self,
        *,
        payload: bytes,
        project_id: int,
        http: httpx.AsyncClient,
        asset_dir: Path | None = None,
        asset_url_prefix: str = "/generated-assets",
        **kwargs: Any,
    ) -> None:
        super().__init__(payload=payload, project_id=project_id, http=http, **kwargs)
        self._asset_dir = asset_dir
        self._asset_url_prefix = asset_url_prefix.rstrip("/")
        self._pending_cost_context: dict[str, Any] | None = None

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Api-Key": self.payload.decode("utf-8"),
            "Accept": "application/json",
        }

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        del op
        body = kwargs.get("json")
        if not isinstance(body, dict):
            body = getattr(self, "_pending_cost_context", None)
        if not isinstance(body, dict):
            return 0.0
        return self.estimate_cost_usd(rendering_speed=str(body.get("rendering_speed") or "DEFAULT"))

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

    def _record_call(
        self,
        *,
        op: str,
        request: Any,
        response: Any,
        duration_ms: int,
        error: str | None,
        cost_cents: int,
    ) -> None:
        super()._record_call(
            op=op,
            request=request,
            response=_sanitize_response_urls(response),
            duration_ms=duration_ms,
            error=error,
            cost_cents=cost_cents,
        )

    async def generate_image(
        self,
        *,
        text_prompt: str,
        resolution: str | None = None,
        rendering_speed: str = "DEFAULT",
        enable_copyright_detection: bool | None = None,
    ) -> IntegrationCallResult:
        data_body = _compact_form(
            {
                "text_prompt": text_prompt,
                "resolution": resolution,
                "rendering_speed": rendering_speed,
                "enable_copyright_detection": enable_copyright_detection,
            }
        )
        self._pending_cost_context = data_body
        result = await self.call(
            op="image.generate",
            method="POST",
            url=f"{self.BASE_URL}/ideogram-v4/generate",
            files=_multipart_fields(data_body),
            headers=self._auth_headers(),
            request_log_body=data_body,
        )
        data = await self._persist_url_response(result.data)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    async def remix_image(
        self,
        *,
        text_prompt: str,
        image_path: Path,
        image_weight: int | None = None,
        resolution: str | None = None,
        rendering_speed: str = "DEFAULT",
        enable_copyright_detection: bool | None = None,
    ) -> IntegrationCallResult:
        raw, mime_type = self.ensure_image_preflight(image_path)
        data_body = _compact_form(
            {
                "text_prompt": text_prompt,
                "image_weight": image_weight,
                "resolution": resolution,
                "rendering_speed": rendering_speed,
                "enable_copyright_detection": enable_copyright_detection,
            }
        )
        result = await self.call(
            op="image.remix",
            method="POST",
            url=f"{self.BASE_URL}/ideogram-v4/remix",
            data_body=data_body,
            files={"image": (image_path.name, raw, mime_type)},
            headers=self._auth_headers(),
            request_log_body={
                **data_body,
                "image_name": image_path.name,
                "image_bytes": len(raw),
            },
        )
        data = await self._persist_url_response(result.data)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    @classmethod
    def ensure_image_preflight(cls, path: Path) -> tuple[bytes, str]:
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in cls.INPUT_IMAGE_FORMATS:
            raise IntegrationDownError(
                "Ideogram remix image inputs must be JPEG, PNG, or WEBP",
                data={"vendor": cls.vendor, "file": path.name},
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise IntegrationDownError(
                "Ideogram remix input image could not be read",
                data={"vendor": cls.vendor, "file": path.name},
            ) from exc
        if len(raw) > cls.MAX_INPUT_IMAGE_BYTES:
            raise IntegrationDownError(
                "Ideogram remix image inputs must be at most 10 MB",
                data={
                    "vendor": cls.vendor,
                    "file": path.name,
                    "bytes": len(raw),
                    "max_bytes": cls.MAX_INPUT_IMAGE_BYTES,
                },
            )
        if not _matches_image_signature(raw, suffix):
            raise IntegrationDownError(
                "Ideogram remix image inputs must be valid JPEG, PNG, or WEBP bytes",
                data={"vendor": cls.vendor, "file": path.name},
            )
        return raw, _mime_type_for_suffix(suffix)

    async def _persist_url_response(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        items = data.get("data")
        if not isinstance(items, list):
            return data
        if self._asset_dir is None and _contains_provider_url(items):
            raise IntegrationDownError(
                "Ideogram generated image URLs require generated-assets persistence",
                data={"vendor": self.vendor},
            )
        persisted: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                persisted.append(item)
                continue
            provider_url = item.get("url")
            if not isinstance(provider_url, str) or not provider_url:
                persisted.append(item)
                continue
            persisted.append(await self._persist_provider_url(item, provider_url))
        clean = {key: value for key, value in data.items() if key != "data"}
        clean["data"] = persisted
        return clean

    async def _persist_provider_url(
        self,
        item: dict[str, Any],
        provider_url: str,
    ) -> dict[str, Any]:
        assert self._asset_dir is not None
        raw, ext = await download_generated_media(
            self,
            provider_url,
            fallback_ext="png",
            empty_message="Ideogram returned an empty image download",
        )
        file_info = write_generated_media(
            raw,
            asset_dir=self._asset_dir,
            asset_url_prefix=self._asset_url_prefix,
            subdir="ideogram",
            prefix="ideogram",
            ext=ext,
        )
        clean = {key: value for key, value in item.items() if key != "url"}
        clean.update(file_info)
        clean["source_model"] = self.MODEL
        clean["provider_url_persisted"] = True
        return clean

    async def test_credentials(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vendor": self.vendor,
            "status": "format-only",
            "summary": (
                "Ideogram does not document a free credential probe; StackOS "
                "verified credential storage format without making a billable "
                "image request."
            ),
            "probe_mode": "non_billable_format_only",
            "next_action": "Run an Ideogram image action to verify the live API key.",
        }


def _compact_form(values: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


def _multipart_fields(values: dict[str, str]) -> dict[str, tuple[None, str]]:
    return {key: (None, value) for key, value in values.items()}


def _mime_type_for_suffix(suffix: str) -> str:
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "webp":
        return "image/webp"
    return "image/png"


def _matches_image_signature(raw: bytes, suffix: str) -> bool:
    if suffix in {"jpg", "jpeg"}:
        return raw.startswith(b"\xff\xd8\xff")
    if suffix == "png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == "webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def _contains_provider_url(items: list[Any]) -> bool:
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"]:
            return True
    return False


def _sanitize_response_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[downloaded-to-generated-assets]"
            if key == "url"
            else _sanitize_response_urls(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_response_urls(item) for item in value]
    return value


__all__ = ["IdeogramImagesIntegration"]
