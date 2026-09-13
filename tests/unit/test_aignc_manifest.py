"""AIGNC catalog exposes explicit provider operations with bounded capabilities."""

from __future__ import annotations

import json

import jsonschema
import pytest

from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.provider_setup import build_provider_setup


def _catalog():
    utils = next(plugin for plugin in BUILTIN_PLUGIN_MANIFESTS if plugin.slug == "utils")
    provider = next(provider for provider in utils.providers if provider.key == "aignc")
    actions = {action.key: action for action in utils.actions if action.provider == "aignc"}
    return provider, actions


def test_aignc_provider_uses_daemon_api_key_and_honest_setup_metadata() -> None:
    provider, _ = _catalog()
    assert provider.auth_type == "api-key"
    assert len(provider.auth_methods) == 1
    method = provider.auth_methods[0]
    assert (method.key, method.payload_format, method.payload_field) == (
        "api_key",
        "raw",
        "api_key",
    )
    assert [(field.key, field.secret, field.required) for field in method.fields] == [
        ("api_key", True, True)
    ]
    assert provider.config["docs"] == ["docs/integration-contracts/aignc.md"]
    setup = build_provider_setup(
        project_id=1, provider_key=provider.key, provider_config_json=provider.config
    )
    assert setup is not None
    assert setup.fallback_url == "https://cli-api.f2nd.com/"
    assert setup.signup_url is None
    assert setup.credential_url is None
    assert setup.docs_url is None
    fallback = next(url for url in setup.urls if url.key == "fallback_url")
    assert fallback.confidence == "directional"


def test_aignc_exposes_exact_four_actions_without_pricing_or_workflow_policy() -> None:
    provider, actions = _catalog()
    assert set(actions) == {
        "aignc.models.list",
        "aignc.chat.complete",
        "aignc.image.generate",
        "aignc.audio.analyze",
    }
    for key, action in actions.items():
        assert action.config["connector"] == "aignc"
        assert action.config["operation"] == key.removeprefix("aignc.")
        assert action.config["requires_credential"] is True
        assert action.config["enforce_budget"] is False
        assert "budget_kind" not in action.config
        assert "default_model" not in action.config
        assert action.config["docs"] == ["docs/integration-contracts/aignc.md"]
        assert action.input_schema["additionalProperties"] is False
        serialized = json.dumps(action.config).lower()
        assert all(
            term not in serialized for term in ("pricing", "routing_policy", "fallback_model")
        )
    assert actions["aignc.models.list"].risk_level == "read"
    assert all(actions[key].risk_level == "cost" for key in actions if key != "aignc.models.list")
    assert "pricing" not in json.dumps(provider.config).lower()


def test_aignc_text_contract_requires_agent_selected_model_and_explicit_output_cap() -> None:
    _, actions = _catalog()
    schema = actions["aignc.chat.complete"].input_schema
    payload = {
        "model": "gemini-3.8-flash",
        "messages": [{"role": "user", "content": "Hello"}],
        "output_limit": 100,
    }
    jsonschema.validate(payload, schema)
    assert len(schema["properties"]["model"]["enum"]) == 19
    assert "gemini-3.1-flash-image" not in schema["properties"]["model"]["enum"]
    assert schema["properties"]["google_search"]["default"] is False
    for invalid in (
        {key: value for key, value in payload.items() if key != "model"},
        {key: value for key, value in payload.items() if key != "output_limit"},
        {**payload, "model": "new-model-discovered-from-api"},
        {**payload, "output_limit": 8193},
        {**payload, "max_tokens": 100},
        {**payload, "tools": [{"google_search": {}}]},
        {**payload, "messages": [{"role": "tool", "content": "A result"}]},
        {**payload, "messages": [{"role": "user", "content": [{"type": "input_audio"}]}]},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


def test_aignc_media_contract_uses_artifacts_and_only_documented_modes() -> None:
    _, actions = _catalog()
    image = actions["aignc.image.generate"]
    audio = actions["aignc.audio.analyze"]
    jsonschema.validate({"prompt": "An apple"}, image.input_schema)
    assert image.input_schema["properties"]["output_limit"]["default"] == 2048
    assert "size" not in image.input_schema["properties"]
    assert "model" not in image.input_schema["properties"]
    assert set(image.config["capability_metadata"]["models"]) == {"gemini-3.1-flash-image"}
    payload = {
        "audio_artifact_id": 1,
        "model": "gemini-3.8-flash",
        "instruction": "Transcribe this recording",
        "output_limit": 1000,
    }
    jsonschema.validate(payload, audio.input_schema)
    assert audio.input_schema["properties"]["model"]["enum"] == [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
    ]
    for invalid in (
        {**payload, "audio_path": "/tmp/recording.wav"},
        {**payload, "input_audio": {"data": "encoded"}},
        {**payload, "model": "gemini-3.1-pro"},
        {**payload, "audio_artifact_id": 0},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, audio.input_schema)
    for action in (image, audio):
        metadata = action.config["capability_metadata"]
        assert all(
            key in metadata
            for key in (
                "modalities",
                "modes",
                "execution",
                "models",
                "safety",
                "unsupported_provider_features",
                "docs",
            )
        )


def test_aignc_output_schema_exposes_grounding_supports_without_invented_offsets() -> None:
    _, actions = _catalog()
    schema = actions["aignc.chat.complete"].output_schema["properties"]["grounding_metadata"]
    assert set(schema["properties"]) == {"webSearchQueries", "groundingChunks", "groundingSupports"}
    payload = {
        "webSearchQueries": ["official exchange rates"],
        "groundingChunks": [{}, {"web": {"uri": "https://example.com/source", "title": "Source"}}],
        "groundingSupports": [
            {"segment": {"text": "Supported text"}, "groundingChunkIndices": [1]},
            {"segment": {"startIndex": 0, "endIndex": 14}, "groundingChunkIndices": [1]},
        ],
    }
    jsonschema.validate(payload, schema)
    for invalid_indices in ([True], [-1], ["1"]):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    **payload,
                    "groundingSupports": [
                        {
                            "segment": {"text": "Supported text"},
                            "groundingChunkIndices": invalid_indices,
                        }
                    ],
                },
                schema,
            )


@pytest.mark.parametrize(
    ("key", "payload"),
    [
        (
            "aignc.chat.complete",
            {
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": "Research the question."}],
                "output_limit": 4096,
            },
        ),
        ("aignc.image.generate", {"prompt": "An apple"}),
        (
            "aignc.audio.analyze",
            {
                "audio_artifact_id": 1,
                "model": "gemini-3.8-flash",
                "instruction": "Transcribe this recording.",
                "output_limit": 1000,
            },
        ),
    ],
)
def test_aignc_generation_uses_shared_background_execution_and_bounded_read_timeout(
    key: str, payload: dict
) -> None:
    _, actions = _catalog()
    action = actions[key]
    assert action.config["execution_mode"] == "background"
    execution = action.config["capability_metadata"]["execution"]
    assert execution["mode"] == "background"
    assert execution["provider_mode"] == "sync"
    assert execution["poll_operation"] == "actionCall.get"
    assert execution["automatic_retry"] is False
    schema = action.input_schema
    assert schema["properties"]["read_timeout_seconds"]["default"] == 600
    assert "read_timeout_seconds" not in schema["required"]
    jsonschema.validate(payload, schema)
    for value in (60, 600, 1800):
        jsonschema.validate({**payload, "read_timeout_seconds": value}, schema)
    for value in (True, False, None, "600", 59, 1801, 60.5):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**payload, "read_timeout_seconds": value}, schema)
    models = actions["aignc.models.list"]
    assert models.config.get("execution_mode", "inline") == "inline"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"read_timeout_seconds": 600}, models.input_schema)
