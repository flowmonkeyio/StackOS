"""Shared constants for communication platform operations."""

from __future__ import annotations

import re
from typing import Any

_PROFILE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_DEFAULT_INGRESS_KEY = "default"
_DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:5180"
_LOCAL_TUNNEL_PROVIDERS: dict[str, dict[str, Any]] = {
    "ngrok": {
        "discovery_url": "http://127.0.0.1:4040/api/endpoints",
        "response_url_fields": ("url", "public_url"),
    },
}
_DEFAULT_DRIVER_CONFIG: dict[str, dict[str, Any]] = {
    "local-tunnel": {"provider": "ngrok"},
    "public-url": {},
}
_CONTEXT_ALLOWED_FIELDS = {
    "message_ref",
    "provider_key",
    "profile_ref",
    "profile_key",
    "direction",
    "surface_ref",
    "channel_ref",
    "thread_ref",
    "content_type",
    "text_preview",
    "attention_status",
    "transport_status",
    "processing_status",
    "from_ref",
    "from_username",
    "sender_ref",
    "recipient_refs",
    "attachments",
    "date",
    "sent_at",
    "received_at",
    "source_agent_request_id",
}
