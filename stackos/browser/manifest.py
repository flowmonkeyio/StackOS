"""Manifest-driven browser API policy for StackOS.

StackOS mirrors the Playwright-style page API through generic
``browser.page.call`` and ``browser.context.call`` operations. This manifest
documents named convenience methods for discovery and drift tests, but it is not
a restrictive allowlist: unknown public methods are still forwarded through the
raw args/kwargs path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

BrowserExposureState = Literal["supported"]
BrowserSideEffect = Literal["read", "navigation", "input", "download", "dangerous"]


@dataclass(frozen=True)
class BrowserMethodSpec:
    """One mirrored browser method contract."""

    method: str
    exposure: BrowserExposureState
    side_effect: BrowserSideEffect
    required_args: tuple[str, ...] = ()
    optional_args: tuple[str, ...] = ()
    allowed_arg_names: tuple[str, ...] = ()
    audit_fields: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def mutating(self) -> bool:
        return self.side_effect != "read"

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "exposure": self.exposure,
            "side_effect": self.side_effect,
            "mutating": self.mutating,
            "required_args": list(self.required_args),
            "optional_args": list(self.optional_args),
            "allowed_arg_names": list(self.allowed_arg_names),
            "audit_fields": list(self.audit_fields),
            "reason": self.reason,
        }


def _supported(
    method: str,
    side_effect: BrowserSideEffect,
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    audit_fields: tuple[str, ...] = (),
) -> BrowserMethodSpec:
    return BrowserMethodSpec(
        method=method,
        exposure="supported",
        side_effect=side_effect,
        required_args=required,
        optional_args=optional,
        allowed_arg_names=required + optional,
        audit_fields=audit_fields,
    )


_METHODS: tuple[BrowserMethodSpec, ...] = (
    _supported(
        "goto",
        "navigation",
        required=("url",),
        optional=("wait_until", "timeout_ms", "referer"),
        audit_fields=("url", "wait_until"),
    ),
    _supported(
        "click",
        "input",
        required=("selector",),
        optional=("button", "click_count", "timeout_ms"),
        audit_fields=("selector", "button"),
    ),
    _supported(
        "fill",
        "input",
        required=("selector", "value"),
        optional=("timeout_ms",),
        audit_fields=("selector", "value_length", "value_sha256"),
    ),
    _supported(
        "type",
        "input",
        required=("selector", "text"),
        optional=("delay_ms", "timeout_ms"),
        audit_fields=("selector", "text_length", "text_sha256"),
    ),
    _supported(
        "press",
        "input",
        required=("selector", "key"),
        optional=("timeout_ms",),
        audit_fields=("selector", "key"),
    ),
    _supported(
        "select_option",
        "input",
        required=("selector", "values"),
        optional=("timeout_ms",),
        audit_fields=("selector", "value_count"),
    ),
    _supported(
        "wait_for_selector",
        "read",
        required=("selector",),
        optional=("state", "timeout_ms"),
        audit_fields=("selector", "state"),
    ),
    _supported(
        "wait_for_load_state",
        "read",
        optional=("state", "timeout_ms"),
        audit_fields=("state",),
    ),
    _supported(
        "wait_for_timeout",
        "read",
        required=("timeout_ms",),
        audit_fields=("timeout_ms",),
    ),
    _supported("title", "read"),
    _supported("url", "read"),
    _supported(
        "text_content",
        "read",
        required=("selector",),
        optional=("timeout_ms",),
        audit_fields=("selector",),
    ),
    _supported(
        "inner_text",
        "read",
        required=("selector",),
        optional=("timeout_ms",),
        audit_fields=("selector",),
    ),
    _supported(
        "locator_count",
        "read",
        required=("selector",),
        audit_fields=("selector",),
    ),
    _supported(
        "evaluate",
        "dangerous",
        required=("script",),
        optional=("arg",),
        audit_fields=("script_length", "script_sha256", "arg_summary"),
    ),
    _supported(
        "add_init_script",
        "dangerous",
        required=("script",),
        optional=("path",),
        audit_fields=("script_length", "script_sha256", "path"),
    ),
)

_BY_METHOD = {item.method: item for item in _METHODS}


def browser_method_manifest() -> list[dict[str, Any]]:
    """Return the public browser method policy manifest."""
    return [item.to_dict() for item in _METHODS]


def get_method_spec(method: str) -> BrowserMethodSpec | None:
    """Return one method spec by canonical method name."""
    return _BY_METHOD.get(method)


def supported_method_names() -> list[str]:
    """Return supported mirrored page-call names."""
    return sorted(item.method for item in _METHODS if item.exposure == "supported")


__all__ = [
    "BrowserMethodSpec",
    "browser_method_manifest",
    "get_method_spec",
    "supported_method_names",
]
