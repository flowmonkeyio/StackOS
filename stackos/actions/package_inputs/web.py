"""Resolve StackOS web-provider selectors before passing plain provider IDs to connectors."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def prepare_web_input(
    connector: str, data: Mapping[str, Any], credential_config: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Preserve existing GA/GTM aliases and defaults without forwarding their maps."""
    payload = deepcopy(dict(data))
    config = credential_config or {}
    names: tuple[str, ...]
    if connector == "google-analytics":
        names = ("property",)
    elif connector == "google-tag-manager":
        names = ("account", "container", "workspace")
    else:
        return payload
    for name in names:
        old_key = f"{name}_ref"
        if old_key not in payload:
            continue
        value = payload.pop(old_key)
        if isinstance(value, str):
            default = config.get(f"default_{name}_ref")
            mapping = config.get(f"{name}_refs")
            if value in {"default", "main"} and isinstance(default, str) and default:
                value = default
            elif isinstance(mapping, Mapping):
                mapped = mapping.get(value)
                if isinstance(mapped, str) and mapped:
                    value = mapped
        payload[f"{name}_id"] = value
    return payload
