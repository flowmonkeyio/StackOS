"""Stable provider-specific communication identifiers."""

from __future__ import annotations

import hashlib


def telegram_callback_button_external_id(
    *,
    profile_key: str,
    message_ref: str,
    callback_data: str,
) -> str:
    digest = hashlib.sha256(f"{message_ref}\0{callback_data}".encode()).hexdigest()[:24]
    return f"telegram-button:{profile_key}:{digest}"


__all__ = ["telegram_callback_button_external_id"]
