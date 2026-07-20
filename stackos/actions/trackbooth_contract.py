"""Shared Trackbooth action connector contracts and constants."""

from __future__ import annotations

import re
from typing import Any

JsonObject = dict[str, Any]

TRACKBOOTH_PLUGIN_SLUG = "trackbooth"
TRACKBOOTH_PROVIDER_KEY = "trackbooth"
READ_METHODS = {"GET"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
RUNTIME_INVENTORY_SOURCE = "trackbooth.catalog.sync"
RUNTIME_ACTION_KEY_PREFIX = "api."
BLOCKED_OPERATION_MESSAGE = "Trackbooth API-key reveal/generate operations are not executable"
BLOCKED_OPERATION_IDS = {
    "AccountApiKeyController.revealApiKey",
    "AccountApiKeyController.generateApiKey",
}
TRACKBOOTH_PROVIDER_CONTEXT_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "acting_as_account": {
            "type": "string",
            "description": (
                "Optional managed Trackbooth account id. StackOS sends it as X-Acting-As-Account."
            ),
        }
    },
}
PATH_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\}")
ACTION_SLUG_RE = re.compile(r"[^a-z0-9_]+")
