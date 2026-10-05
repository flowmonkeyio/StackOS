"""Host Trackbooth execution-context selection and discovery limits."""

from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest


def _elapsed_ms(start: float) -> int:
    return max(0, int((perf_counter() - start) * 1000))


def _effective_acting_as_account(request: ActionConnectorRequest) -> str | None:
    return _optional_clean_str(request.provider_context_json.get("acting_as_account"))


def _optional_clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _limit(payload: Mapping[str, Any]) -> int:
    raw = payload.get("limit")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return min(raw, 100)
    return 25
