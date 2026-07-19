"""Pure media inspection helpers for the BytePlus ModelArk wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_OUTPUT_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _generated_image_count(response: dict[str, Any]) -> int | None:
    usage = response.get("usage")
    if isinstance(usage, dict):
        raw_generated = usage.get("generated_images")
        if isinstance(raw_generated, int) and not isinstance(raw_generated, bool):
            return max(0, raw_generated)
        if isinstance(raw_generated, str):
            try:
                return max(0, int(raw_generated))
            except ValueError:
                pass
    items = response.get("data")
    if isinstance(items, list):
        count = sum(
            1
            for item in items
            if isinstance(item, dict)
            and (isinstance(item.get("url"), str) or isinstance(item.get("b64_json"), str))
        )
        if count > 0:
            return count
    return None


def _mime_type_for_suffix(suffix: str) -> str:
    if suffix in {"jpg", "jpeg"}:
        return "image/jpeg"
    if suffix == "webp":
        return "image/webp"
    return "image/png"


def _mime_type_for_raw(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _matches_image_signature(raw: bytes, suffix: str) -> bool:
    if suffix in {"jpg", "jpeg"}:
        return raw.startswith(b"\xff\xd8\xff")
    if suffix == "png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == "webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def _image_dimensions(raw: bytes, suffix: str) -> tuple[int, int] | None:
    if suffix == "png":
        return _png_dimensions(raw)
    if suffix in {"jpg", "jpeg"}:
        return _jpeg_dimensions(raw)
    if suffix == "webp":
        return _webp_dimensions(raw)
    return None


def _png_dimensions(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 24 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if raw[12:16] != b"IHDR":
        return None
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def _jpeg_dimensions(raw: bytes) -> tuple[int, int] | None:
    if not raw.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof_markers = {
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
    while offset + 3 < len(raw):
        if raw[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        if offset >= len(raw):
            return None
        marker = raw[offset]
        offset += 1
        if marker in {0x01, 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9}:
            continue
        if offset + 2 > len(raw):
            return None
        segment_length = int.from_bytes(raw[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(raw):
            return None
        if marker in sof_markers:
            if segment_length < 7:
                return None
            height = int.from_bytes(raw[offset + 3 : offset + 5], "big")
            width = int.from_bytes(raw[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _webp_dimensions(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 20 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return None
    offset = 12
    while offset + 8 <= len(raw):
        chunk_type = raw[offset : offset + 4]
        chunk_size = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        data_offset = offset + 8
        data_end = data_offset + chunk_size
        if data_end > len(raw):
            return None
        data = raw[data_offset:data_end]
        if chunk_type == b"VP8X" and len(data) >= 10:
            width = 1 + int.from_bytes(data[4:7], "little")
            height = 1 + int.from_bytes(data[7:10], "little")
            return width, height
        if chunk_type == b"VP8L" and len(data) >= 5 and data[0] == 0x2F:
            width = 1 + (((data[2] & 0x3F) << 8) | data[1])
            height = 1 + (((data[4] & 0x0F) << 10) | (data[3] << 2) | ((data[2] & 0xC0) >> 6))
            return width, height
        if chunk_type == b"VP8 " and len(data) >= 10 and data[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(data[6:8], "little") & 0x3FFF
            height = int.from_bytes(data[8:10], "little") & 0x3FFF
            return width, height
        offset = data_end + (chunk_size % 2)
    return None


def _file_format(mime_type: str | None, source_url: str) -> str:
    if isinstance(mime_type, str):
        clean_mime = mime_type.split(";", 1)[0].strip().lower()
        if clean_mime in _OUTPUT_EXTENSIONS:
            return _OUTPUT_EXTENSIONS[clean_mime]
    suffix = Path(urlparse(source_url).path).suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg", "png", "webp"}:
        return "jpg" if suffix == "jpeg" else suffix
    return "jpg"


def _contains_media_output(items: list[Any]) -> bool:
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("url"), str) and item["url"]:
            return True
        if isinstance(item.get("b64_json"), str) and item["b64_json"]:
            return True
    return False
