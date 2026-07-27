"""Dependency-light Amazon S3 Account configuration contract."""

from __future__ import annotations

import re
from typing import Any

AWS_S3_REGIONS = (
    "af-south-1",
    "ap-east-1",
    "ap-east-2",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-northeast-3",
    "ap-south-1",
    "ap-south-2",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-southeast-3",
    "ap-southeast-4",
    "ap-southeast-5",
    "ap-southeast-6",
    "ap-southeast-7",
    "ca-central-1",
    "ca-west-1",
    "eu-central-1",
    "eu-central-2",
    "eu-north-1",
    "eu-south-1",
    "eu-south-2",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "il-central-1",
    "me-central-1",
    "me-south-1",
    "mx-central-1",
    "sa-east-1",
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "cn-north-1",
    "cn-northwest-1",
    "us-gov-east-1",
    "us-gov-west-1",
    "us-iso-east-1",
    "us-iso-west-1",
    "us-isob-east-1",
    "us-isob-west-1",
    "eu-isoe-west-1",
    "us-isof-east-1",
    "us-isof-south-1",
    "eusc-de-east-1",
)
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def normalize_s3_prefix(value: Any) -> str:
    """Return one canonical relative directory prefix or the bucket root."""

    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Amazon S3 prefix must be a relative path")
    prefix = value.strip()
    if not prefix:
        return ""
    if (
        prefix.startswith("/")
        or _URI_SCHEME_RE.match(prefix)
        or "\\" in prefix
        or "//" in prefix
        or any(ord(char) < 32 or ord(char) == 127 for char in prefix)
    ):
        raise ValueError(
            "Amazon S3 prefix must be a relative path without a URI, leading slash, "
            "backslash, repeated separator, or control character"
        )
    segments = prefix.rstrip("/").split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("Amazon S3 prefix relative path must not contain dot or empty segments")
    normalized = "/".join(segments) + "/"
    if len(normalized.encode("utf-8")) > 1024:
        raise ValueError("Amazon S3 prefix must be at most 1024 UTF-8 bytes")
    return normalized


__all__ = ["AWS_S3_REGIONS", "normalize_s3_prefix"]
