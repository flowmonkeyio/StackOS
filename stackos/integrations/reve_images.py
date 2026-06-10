"""Reve image generation integration wrapper.

Official docs:

- https://api.reve.com/console/docs
- https://api.reve.com/console/docs/create
- https://api.reve.com/console/docs/edit
- https://api.reve.com/console/docs/remix
- https://api.reve.com/console/pricing

Reve's JSON response returns PNG image data as base64 plus request and credit
metadata. The wrapper persists image bytes immediately and only returns
generated-assets URLs to agents.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Any, ClassVar, Literal

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError


class ReveImagesIntegration(BaseIntegration):
    """Wrapper for Reve first-party image endpoints."""

    kind = "reve"
    vendor = "reve"
    default_qps = 2.0

    BASE_URL = "https://api.reve.com/v1"
    PROMPT_MAX_CHARS = 2560
    MAX_IMAGE_BYTES = 10 * 1024 * 1024
    MAX_TOTAL_IMAGE_PIXELS = 32_000_000
    INPUT_IMAGE_FORMATS: ClassVar[frozenset[str]] = frozenset(
        {"webp", "jpg", "jpeg", "png", "gif", "tif", "tiff"}
    )

    ASPECT_RATIOS: ClassVar[frozenset[str]] = frozenset(
        {"16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "1:1", "auto"}
    )
    CREATE_VERSIONS: ClassVar[frozenset[str]] = frozenset({"latest", "reve-create@20250915"})
    EDIT_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {"latest", "latest-fast", "reve-edit@20250915", "reve-edit-fast@20251030"}
    )
    REMIX_VERSIONS: ClassVar[frozenset[str]] = frozenset(
        {"latest", "latest-fast", "reve-remix@20250915", "reve-remix-fast@20251030"}
    )
    _CREDIT_USD = 10.0 / 7500.0
    _BASE_CREDITS: ClassVar[dict[str, dict[str, int]]] = {
        "image.create": {
            "latest": 18,
            "reve-create@20250915": 18,
        },
        "image.edit": {
            "latest": 30,
            "latest-fast": 5,
            "reve-edit@20250915": 30,
            "reve-edit-fast@20251030": 5,
        },
        "image.remix": {
            "latest": 30,
            "latest-fast": 5,
            "reve-remix@20250915": 30,
            "reve-remix-fast@20251030": 5,
        },
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

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.payload.decode('utf-8')}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        body = kwargs.get("json")
        if not isinstance(body, dict):
            return 0.0
        version = str(body.get("version") or "latest")
        test_time_scaling = body.get("test_time_scaling", 1)
        scaling = (
            test_time_scaling
            if isinstance(test_time_scaling, int) and not isinstance(test_time_scaling, bool)
            else 1
        )
        return self.estimate_cost_usd(op=op, version=version, test_time_scaling=scaling)

    @classmethod
    def estimate_cost_usd(
        cls,
        *,
        op: str,
        version: str = "latest",
        test_time_scaling: int = 1,
    ) -> float:
        credits = cls._BASE_CREDITS.get(op, {}).get(version, 0)
        scaling = min(15, max(1, test_time_scaling))
        return credits * scaling * cls._CREDIT_USD

    def _extract_actual_cost_usd(
        self,
        op: str,
        *,
        request: Any,
        response: Any,
        estimated: float,
    ) -> float:
        del op, request
        if not isinstance(response, dict):
            return estimated
        raw_credits = response.get("credits_used")
        if not isinstance(raw_credits, int | float | str) or isinstance(raw_credits, bool):
            return estimated
        try:
            credits = float(raw_credits)
        except (TypeError, ValueError):
            return estimated
        if credits < 0:
            return estimated
        return credits * self._CREDIT_USD

    async def create_image(
        self,
        *,
        prompt: str,
        aspect_ratio: str = "3:2",
        version: str = "latest",
        test_time_scaling: int = 1,
    ) -> IntegrationCallResult:
        body = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "version": version,
            "test_time_scaling": test_time_scaling,
        }
        result = await self.call(
            op="image.create",
            method="POST",
            url=f"{self.BASE_URL}/image/create",
            json_body=body,
            headers=self._auth_headers(),
        )
        data = self._persist_image_response(result.data, fallback_version=version)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    async def edit_image(
        self,
        *,
        edit_instruction: str,
        reference_image_path: Path,
        aspect_ratio: str | None = None,
        version: str = "latest",
        test_time_scaling: int = 1,
    ) -> IntegrationCallResult:
        reference_image = self._image_b64(reference_image_path)
        body: dict[str, Any] = {
            "edit_instruction": edit_instruction,
            "reference_image": reference_image,
            "version": version,
            "test_time_scaling": test_time_scaling,
        }
        if aspect_ratio is not None:
            body["aspect_ratio"] = aspect_ratio
        result = await self.call(
            op="image.edit",
            method="POST",
            url=f"{self.BASE_URL}/image/edit",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "edit_instruction": edit_instruction,
                "reference_image_name": reference_image_path.name,
                "version": version,
                "test_time_scaling": test_time_scaling,
                **({"aspect_ratio": aspect_ratio} if aspect_ratio is not None else {}),
            },
        )
        data = self._persist_image_response(result.data, fallback_version=version)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    async def remix_image(
        self,
        *,
        prompt: str,
        reference_image_paths: list[Path],
        aspect_ratio: str | None = None,
        version: str = "latest",
        test_time_scaling: int = 1,
    ) -> IntegrationCallResult:
        reference_images = self._images_b64(reference_image_paths)
        body: dict[str, Any] = {
            "prompt": prompt,
            "reference_images": reference_images,
            "version": version,
            "test_time_scaling": test_time_scaling,
        }
        if aspect_ratio is not None:
            body["aspect_ratio"] = aspect_ratio
        result = await self.call(
            op="image.remix",
            method="POST",
            url=f"{self.BASE_URL}/image/remix",
            json_body=body,
            headers=self._auth_headers(),
            request_log_body={
                "prompt": prompt,
                "reference_image_count": len(reference_image_paths),
                "reference_image_names": [path.name for path in reference_image_paths],
                "version": version,
                "test_time_scaling": test_time_scaling,
                **({"aspect_ratio": aspect_ratio} if aspect_ratio is not None else {}),
            },
        )
        data = self._persist_image_response(result.data, fallback_version=version)
        return IntegrationCallResult(
            data=data,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
        )

    def _persist_image_response(self, data: Any, *, fallback_version: str) -> Any:
        if self._asset_dir is None or not isinstance(data, dict):
            return data
        raw_image = data.get("image")
        if not isinstance(raw_image, str) or not raw_image:
            return data
        try:
            raw = base64.b64decode(raw_image, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise IntegrationDownError(
                "Reve returned invalid base64 image data",
                data={"vendor": self.vendor},
            ) from exc
        clean = {key: value for key, value in data.items() if key != "image"}
        item = {
            **self._write_image(raw),
            "source_model": str(data.get("version") or fallback_version),
            "request_id": str(data.get("request_id") or ""),
            "content_violation": bool(data.get("content_violation"))
            if isinstance(data.get("content_violation"), bool)
            else data.get("content_violation"),
            "credits_used": data.get("credits_used"),
            "credits_remaining": data.get("credits_remaining"),
        }
        return {
            **clean,
            "data": [item],
            "usage": {
                "credits_used": data.get("credits_used"),
                "credits_remaining": data.get("credits_remaining"),
            },
        }

    def _write_image(self, raw: bytes) -> dict[str, str]:
        digest = hashlib.sha256(raw).hexdigest()
        filename = f"reve-image-{digest[:32]}.png"
        assert self._asset_dir is not None
        target_dir = self._asset_dir / "reve"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / filename
        if not path.exists():
            path.write_bytes(raw)
        return {
            "url": f"{self._asset_url_prefix}/reve/{filename}",
            "file_format": "png",
        }

    def _images_b64(self, paths: list[Path]) -> list[str]:
        self.ensure_remix_image_preflight(paths)
        return [self._image_b64(path) for path in paths]

    @classmethod
    def ensure_remix_image_preflight(cls, paths: list[Path]) -> None:
        total_pixels = 0
        for path in paths:
            raw = cls.ensure_image_preflight(path)
            pixel_count = cls._image_pixel_count(path, raw)
            total_pixels += pixel_count
            if total_pixels > cls.MAX_TOTAL_IMAGE_PIXELS:
                raise IntegrationDownError(
                    "Reve remix input images must be at most 32 million pixels total",
                    data={
                        "vendor": cls.vendor,
                        "pixels": total_pixels,
                        "max_pixels": cls.MAX_TOTAL_IMAGE_PIXELS,
                    },
                )

    def _image_b64(self, path: Path) -> str:
        return base64.b64encode(self.ensure_image_preflight(path)).decode("ascii")

    @classmethod
    def ensure_image_preflight(cls, path: Path) -> bytes:
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in cls.INPUT_IMAGE_FORMATS:
            raise IntegrationDownError(
                "Reve input images must be WEBP, JPEG, PNG, GIF, or TIFF",
                data={"vendor": cls.vendor, "file": path.name},
            )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise IntegrationDownError(
                "Reve input image could not be read",
                data={"vendor": cls.vendor, "file": path.name},
            ) from exc
        if len(raw) > cls.MAX_IMAGE_BYTES:
            raise IntegrationDownError(
                "Reve input images must be at most 10 MiB each",
                data={
                    "vendor": cls.vendor,
                    "file": path.name,
                    "bytes": len(raw),
                    "max_bytes": cls.MAX_IMAGE_BYTES,
                },
            )
        return raw

    @classmethod
    def _image_pixel_count(cls, path: Path, raw: bytes) -> int:
        try:
            width, height = _image_dimensions(path.suffix.lower().lstrip("."), raw)
        except ValueError as exc:
            raise IntegrationDownError(
                "Reve remix input image dimensions could not be read",
                data={"vendor": cls.vendor, "file": path.name},
            ) from exc
        if width <= 0 or height <= 0:
            raise IntegrationDownError(
                "Reve remix input image dimensions are invalid",
                data={"vendor": cls.vendor, "file": path.name, "width": width, "height": height},
            )
        return width * height

    async def test_credentials(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vendor": self.vendor,
            "status": "format-only",
            "summary": (
                "Reve does not document a free credential probe; StackOS verified "
                "credential storage format without making a billable image request."
            ),
            "probe_mode": "non_billable_format_only",
            "next_action": "Run a Reve image action to verify the live API key.",
        }


__all__ = ["ReveImagesIntegration"]


def _image_dimensions(suffix: str, data: bytes) -> tuple[int, int]:
    match suffix:
        case "png":
            return _png_dimensions(data)
        case "jpg" | "jpeg":
            return _jpeg_dimensions(data)
        case "gif":
            return _gif_dimensions(data)
        case "webp":
            return _webp_dimensions(data)
        case "tif" | "tiff":
            return _tiff_dimensions(data)
        case _:
            raise ValueError("unsupported image format")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        raise ValueError("invalid PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        raise ValueError("invalid JPEG")
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    index = 2
    while index < len(data):
        while index < len(data) and data[index] != 0xFF:
            index += 1
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in start_of_frame:
            if segment_length < 7:
                break
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    raise ValueError("JPEG dimensions not found")


def _gif_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 10 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValueError("invalid GIF")
    return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("invalid WEBP")
    offset = 12
    while offset + 8 <= len(data):
        chunk_type = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        payload = offset + 8
        end = payload + chunk_size
        if end > len(data):
            break
        if chunk_type == b"VP8X" and chunk_size >= 10:
            width = 1 + int.from_bytes(data[payload + 4 : payload + 7], "little")
            height = 1 + int.from_bytes(data[payload + 7 : payload + 10], "little")
            return width, height
        if chunk_type == b"VP8L" and chunk_size >= 5 and data[payload] == 0x2F:
            b0, b1, b2, b3 = data[payload + 1 : payload + 5]
            width = 1 + (((b1 & 0x3F) << 8) | b0)
            height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return width, height
        if (
            chunk_type == b"VP8 "
            and chunk_size >= 10
            and data[payload + 3 : payload + 6] == (b"\x9d\x01\x2a")
        ):
            width = int.from_bytes(data[payload + 6 : payload + 8], "little") & 0x3FFF
            height = int.from_bytes(data[payload + 8 : payload + 10], "little") & 0x3FFF
            return width, height
        offset = end + (chunk_size % 2)
    raise ValueError("WEBP dimensions not found")


def _tiff_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 8:
        raise ValueError("invalid TIFF")
    if data[:2] == b"II":
        byteorder: Literal["little", "big"] = "little"
    elif data[:2] == b"MM":
        byteorder = "big"
    else:
        raise ValueError("invalid TIFF byte order")
    if int.from_bytes(data[2:4], byteorder) != 42:
        raise ValueError("invalid TIFF magic")
    ifd_offset = int.from_bytes(data[4:8], byteorder)
    if ifd_offset + 2 > len(data):
        raise ValueError("invalid TIFF IFD")
    entry_count = int.from_bytes(data[ifd_offset : ifd_offset + 2], byteorder)
    width: int | None = None
    height: int | None = None
    cursor = ifd_offset + 2
    for _ in range(entry_count):
        if cursor + 12 > len(data):
            raise ValueError("truncated TIFF IFD")
        tag = int.from_bytes(data[cursor : cursor + 2], byteorder)
        field_type = int.from_bytes(data[cursor + 2 : cursor + 4], byteorder)
        count = int.from_bytes(data[cursor + 4 : cursor + 8], byteorder)
        value = data[cursor + 8 : cursor + 12]
        number = _tiff_inline_number(byteorder, field_type, count, value)
        if tag == 256:
            width = number
        elif tag == 257:
            height = number
        cursor += 12
    if width is None or height is None:
        raise ValueError("TIFF dimensions not found")
    return width, height


def _tiff_inline_number(
    byteorder: Literal["little", "big"],
    field_type: int,
    count: int,
    value: bytes,
) -> int | None:
    if count < 1:
        return None
    if field_type == 3:
        return int.from_bytes(value[:2], byteorder)
    if field_type == 4:
        return int.from_bytes(value, byteorder)
    return None
