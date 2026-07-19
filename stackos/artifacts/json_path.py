"""JSON-path selection for artifact content reads."""

from __future__ import annotations

from typing import Any

from stackos.repositories.base import ValidationError


def select_json_path(value: Any, json_path: str) -> Any:
    if json_path in {"", "$"}:
        return value
    if not json_path.startswith("$."):
        raise ValidationError("json_path must start with '$.'")
    current = value
    for token in json_path[2:].split("."):
        if not token:
            raise ValidationError("json_path contains an empty segment")
        field, indexes = _split_json_path_token(token)
        if field:
            if not isinstance(current, dict) or field not in current:
                raise ValidationError("json_path field was not found", data={"field": field})
            current = current[field]
        for index in indexes:
            if not isinstance(current, list) or index >= len(current):
                raise ValidationError("json_path array index was not found", data={"index": index})
            current = current[index]
    return current


def _split_json_path_token(token: str) -> tuple[str, list[int]]:
    field = token.split("[", 1)[0]
    rest = token[len(field) :]
    indexes: list[int] = []
    while rest:
        if not rest.startswith("[") or "]" not in rest:
            raise ValidationError("json_path supports only simple field and [index] segments")
        raw_index, rest = rest[1:].split("]", 1)
        if not raw_index.isdigit():
            raise ValidationError("json_path array index must be a non-negative integer")
        indexes.append(int(raw_index))
    return field, indexes


__all__ = ["select_json_path"]
