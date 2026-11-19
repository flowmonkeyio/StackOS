"""Project the data-only ElevenLabs actions from the pinned native catalog."""

from __future__ import annotations

from typing import Any

from stackos_connectors import get_default_client
from stackos_connectors.contracts import thaw

_DATA_ACTIONS = (
    "subscription.get",
    "models.list",
    "voices.list",
    "voices.settings.default.get",
    "voices.settings.get",
    "library.search",
    "library.add",
    "voices.create",
    "dictionaries.create",
)


def elevenlabs_action_kwargs() -> list[dict[str, Any]]:
    registry = get_default_client().registry
    actions = []
    for operation in _DATA_ACTIONS:
        native = registry.action("elevenlabs", f"elevenlabs.{operation}")
        actions.append(
            {
                "key": native.key,
                "name": native.metadata["name"],
                "description": native.description,
                "provider": native.connector,
                "risk_level": native.metadata["risk_level"],
                "input_schema": thaw(native.input_schema),
                "output_schema": thaw(native.output_schema),
                "config": {
                    "schema_version": "stackos.action.v1",
                    "connector": native.connector,
                    "operation": native.operation,
                    "requires_credential": True,
                    "enforce_budget": False,
                    "agent_guidance": native.guidance,
                    "docs": ["docs/integration-contracts/elevenlabs.md"],
                },
            }
        )
    return actions
