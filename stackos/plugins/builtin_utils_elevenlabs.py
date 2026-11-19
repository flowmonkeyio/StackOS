"""Project ElevenLabs actions from the pinned native catalog."""

from __future__ import annotations

from typing import Any

from stackos_connectors import get_default_client
from stackos_connectors.contracts import thaw

_ACTIONS = (
    "subscription.get",
    "models.list",
    "voices.list",
    "voices.settings.default.get",
    "voices.settings.get",
    "library.search",
    "library.add",
    "voices.create",
    "dictionaries.create",
    "speech.generate",
    "voices.design",
    "alignment.create",
)


def elevenlabs_action_kwargs() -> list[dict[str, Any]]:
    registry = get_default_client().registry
    actions = []
    for operation in _ACTIONS:
        native = registry.action("elevenlabs", f"elevenlabs.{operation}")
        input_schema = thaw(native.input_schema)
        output_schema = thaw(native.output_schema)
        description = native.description
        artifact_id = {"type": "integer", "minimum": 1, "description": "Project audio artifact ID."}
        if operation == "speech.generate":
            output_schema["properties"]["artifact_id"] = artifact_id
            output_schema["required"].append("artifact_id")
            output_schema["$defs"]["character_alignment"]["additionalProperties"] = False
        elif operation == "voices.design":
            preview = output_schema["properties"]["previews"]["items"]
            preview["properties"]["artifact_id"] = artifact_id
            preview["properties"]["file_index"]["description"] = (
                "Zero-based index in the native connector's saved files for this response."
            )
            preview["oneOf"][0]["required"].append("artifact_id")
            preview["oneOf"][1]["not"] = {
                "anyOf": [{"required": ["file_index"]}, {"required": ["artifact_id"]}]
            }
        elif operation == "alignment.create":
            properties = input_schema["properties"]
            properties.pop("audio_path")
            properties.pop("mime_type")
            properties["audio_artifact_id"] = artifact_id
            input_schema["required"] = ["audio_artifact_id", "text"]
            description = (
                "Align an existing managed project audio artifact to the supplied transcript."
            )
        actions.append(
            {
                "key": native.key,
                "name": native.metadata["name"],
                "description": description,
                "provider": native.connector,
                "risk_level": native.metadata["risk_level"],
                "input_schema": input_schema,
                "output_schema": output_schema,
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
