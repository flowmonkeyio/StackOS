"""Pure helpers for opaque, write-only action payload secret references."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

SECRET_REF_KEY = "$secret_ref"
SECRET_REF_SENTINEL = "__stackos_payload_secret__"

_SECRET_REF_RE = re.compile(r"^secret_[A-Za-z0-9_-]{16,}$")


@dataclass(frozen=True)
class SecretRefOccurrence:
    """One exact secret-reference marker and its action-input path."""

    path: str
    secret_ref: str


def is_opaque_secret_ref(value: Any) -> bool:
    """Return whether ``value`` has the public opaque-reference grammar."""
    return isinstance(value, str) and _SECRET_REF_RE.fullmatch(value) is not None


def is_secret_ref_marker(value: Any) -> bool:
    """Return whether ``value`` is exactly ``{"$secret_ref": "secret_..."}``."""
    return (
        isinstance(value, Mapping)
        and len(value) == 1
        and SECRET_REF_KEY in value
        and is_opaque_secret_ref(value[SECRET_REF_KEY])
    )


def find_secret_ref_occurrences(value: Any, *, path: str = "$") -> list[SecretRefOccurrence]:
    """Collect exact markers without resolving or otherwise exposing their values."""
    if is_secret_ref_marker(value):
        return [SecretRefOccurrence(path=path, secret_ref=value[SECRET_REF_KEY])]
    occurrences: list[SecretRefOccurrence] = []
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            occurrences.extend(find_secret_ref_occurrences(raw_value, path=f"{path}.{raw_key}"))
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            occurrences.extend(find_secret_ref_occurrences(item, path=f"{path}[{index}]"))
    return occurrences


def project_secret_refs(value: Any) -> Any:
    """Return a deep copy with exact markers projected to a safe string sentinel."""
    if is_secret_ref_marker(value):
        return SECRET_REF_SENTINEL
    if isinstance(value, Mapping):
        return {key: project_secret_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [project_secret_refs(item) for item in value]
    if isinstance(value, tuple):
        return tuple(project_secret_refs(item) for item in value)
    return value


def materialize_secret_refs(
    value: Any,
    *,
    resolve: Callable[[str], str],
) -> tuple[Any, tuple[str, ...]]:
    """Resolve exact markers into a fresh deep copy immediately before dispatch."""
    cache: dict[str, str] = {}

    def materialize(item: Any) -> Any:
        if is_secret_ref_marker(item):
            secret_ref = item[SECRET_REF_KEY]
            if secret_ref not in cache:
                cache[secret_ref] = resolve(secret_ref)
            return cache[secret_ref]
        if isinstance(item, Mapping):
            return {key: materialize(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [materialize(nested) for nested in item]
        if isinstance(item, tuple):
            return tuple(materialize(nested) for nested in item)
        return item

    materialized = materialize(value)
    sensitive_values = tuple(sorted(set(cache.values()), key=len, reverse=True))
    return materialized, sensitive_values


def redact_secret_values(value: Any, sensitive_values: tuple[str, ...]) -> Any:
    """Redact exact daemon-resolved values from controlled JSON and text outputs."""
    values = tuple(item for item in sensitive_values if item)
    if not values:
        return value

    def redact_text(text: str) -> str:
        redacted = text
        for secret in values:
            redacted = redacted.replace(secret, "[redacted]")
        return redacted

    if isinstance(value, Mapping):
        return {
            redact_text(str(key)): redact_secret_values(item, values) for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_values(item, values) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secret_values(item, values) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


__all__ = [
    "SECRET_REF_KEY",
    "SECRET_REF_SENTINEL",
    "SecretRefOccurrence",
    "find_secret_ref_occurrences",
    "is_opaque_secret_ref",
    "is_secret_ref_marker",
    "materialize_secret_refs",
    "project_secret_refs",
    "redact_secret_values",
]
