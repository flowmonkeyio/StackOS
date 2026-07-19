"""Stable provider-specific communication identifiers."""

from __future__ import annotations

import hashlib


def slack_surface_ref(channel_id: str) -> str:
    return f"slack-channel:{channel_id}"


def slack_message_ref(channel_id: str, ts: str) -> str:
    return f"slack-message:{channel_id}:{ts}"


def slack_thread_ref(channel_id: str, ts: str) -> str:
    return f"slack-thread:{channel_id}:{ts}"


def slack_outbound_button_external_id(
    *,
    profile_key: str,
    message_ref: str,
    action_id: str,
    value: str,
    block_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{message_ref}\0{block_id}\0{action_id}\0{value}".encode()
    ).hexdigest()[:24]
    return f"slack-button:{profile_key}:{digest}"


def telegram_callback_button_external_id(
    *,
    profile_key: str,
    message_ref: str,
    callback_data: str,
) -> str:
    digest = hashlib.sha256(f"{message_ref}\0{callback_data}".encode()).hexdigest()[:24]
    return f"telegram-button:{profile_key}:{digest}"


__all__ = [
    "slack_message_ref",
    "slack_outbound_button_external_id",
    "slack_surface_ref",
    "slack_thread_ref",
    "telegram_callback_button_external_id",
]
