"""Host Trackbooth blocked-endpoint annotations and bounded discovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from stackos_connectors.connectors.trackbooth.assets import (
    TrackboothAssets as NativeTrackboothAssets,
)
from stackos_connectors.connectors.trackbooth.assets import _detail_from_endpoint as native_detail

from stackos.actions.trackbooth_contract import BLOCKED_OPERATION_IDS as _BLOCKED_OPERATION_IDS
from stackos.actions.trackbooth_contract import JsonObject
from stackos.actions.trackbooth_transport import _limit, _optional_clean_str


class TrackboothAssets(NativeTrackboothAssets):
    """Add host endpoint restrictions to provider schema facts."""

    def summary(self, endpoint):
        return {**super().summary(endpoint), "execution_blocked": self.is_blocked(endpoint)}

    def is_blocked(self, endpoint):
        return _is_blocked_endpoint(endpoint)

    def filter_catalog(
        self,
        items: Sequence[Mapping[str, Any]],
        payload: Mapping[str, Any],
    ) -> list[JsonObject]:
        """Project live catalog rows into bounded summaries for discovery."""
        query = _optional_clean_str(payload.get("query"))
        category = _optional_clean_str(payload.get("category"))
        method = _optional_clean_str(payload.get("method"))
        path = _optional_clean_str(payload.get("path"))
        operation_id = _optional_clean_str(payload.get("operation_id"))
        tags = [str(tag).lower() for tag in payload.get("tags", []) if isinstance(tag, str)]
        limit = _limit(payload)
        summaries: list[JsonObject] = []
        for item in items:
            summary = self.summary(item)
            if operation_id and operation_id.lower() not in str(summary["operation_id"]).lower():
                continue
            if method and str(summary.get("method") or "").upper() != method.upper():
                continue
            if category and category.lower() not in str(summary.get("category") or "").lower():
                continue
            if path and path.lower() not in str(summary.get("path") or "").lower():
                continue
            item_tags = [str(tag).lower() for tag in summary.get("tags") or []]
            if tags and not all(tag in item_tags for tag in tags):
                continue
            if query:
                haystack = " ".join(
                    str(value or "")
                    for value in (
                        summary.get("operation_id"),
                        summary.get("title"),
                        summary.get("subtitle"),
                        summary.get("description"),
                        summary.get("category"),
                        summary.get("method"),
                        summary.get("path"),
                        " ".join(item_tags),
                    )
                ).lower()
                if query.lower() not in haystack:
                    continue
            summaries.append(summary)
            if len(summaries) >= limit:
                break
        return summaries


def _is_blocked_endpoint(endpoint: Mapping[str, Any] | str) -> bool:
    if isinstance(endpoint, str):
        operation_id = endpoint
        path = ""
    else:
        operation_id = str(endpoint.get("operation_id") or "")
        path = str(endpoint.get("path") or "")
    return operation_id in _BLOCKED_OPERATION_IDS or "/api-key" in path


def _detail_from_endpoint(endpoint, *, openapi_schemas=None):
    return {
        **native_detail(endpoint, openapi_schemas=openapi_schemas),
        "execution_blocked": _is_blocked_endpoint(endpoint),
    }
