"""Secret-safe projection of validation failures for every transport."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def safe_validation_errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep only structural diagnostics; never echo submitted values or context."""

    projected: list[dict[str, Any]] = []
    for error in errors:
        loc = error.get("loc", ())
        projected.append(
            {
                "loc": list(loc) if isinstance(loc, tuple | list) else [str(loc)],
                "type": str(error.get("type") or "validation_error"),
                "msg": str(error.get("msg") or "Invalid value"),
            }
        )
    return projected


__all__ = ["safe_validation_errors"]
