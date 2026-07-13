"""StackOS plugin manifest schema and built-in manifests."""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from stackos.provider_setup import find_provider_setup_secret_paths

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[-.][a-z0-9_]+)*$")
_DEFAULT_PLUGIN_DISPLAY_ORDER = {
    "engineering": 10,
    "communications": 20,
    "gtm": 30,
    "media-buying": 40,
    "publishing": 50,
    "seo": 60,
    "core": 900,
    "utils": 910,
}


def _validate_key(value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must be lowercase snake/kebab/dotted identifier")
    return value


class CapabilityManifest(BaseModel):
    """Capability metadata contributed by a plugin."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    kind: str = Field(default="domain", max_length=80)
    config: dict[str, Any] | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class AuthFieldManifest(BaseModel):
    """One field in a provider-owned credential setup contract."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=200)
    type: str = Field(default="text", max_length=40)
    secret: bool = False
    required: bool = False
    placeholder: str | None = None
    description: str | None = None
    options: list[dict[str, str]] | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class AuthMethodManifest(BaseModel):
    """Credential setup method contributed by a provider."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=200)
    auth_type: str = Field(default="none", max_length=80)
    description: str = ""
    interactive: bool = False
    payload_format: str = Field(default="json", max_length=40)
    payload_field: str | None = Field(default=None, max_length=160)
    fields: list[AuthFieldManifest] = Field(default_factory=list)
    config: dict[str, Any] | None = None

    @field_validator("key", "payload_field")
    @classmethod
    def _keys(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_key(value)


class ProviderManifest(BaseModel):
    """Provider metadata contributed by a plugin."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    auth_type: str = Field(default="none", max_length=80)
    auth_methods: list[AuthMethodManifest] = Field(default_factory=list)
    config: dict[str, Any] | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)

    @field_validator("config")
    @classmethod
    def _config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        secret_paths = find_provider_setup_secret_paths(value.get("setup"), path="$.setup")
        if secret_paths:
            raise ValueError(
                "provider config.setup contains unsupported or secret-like fields: "
                + ", ".join(secret_paths[:8])
            )
        return value


class ActionManifest(BaseModel):
    """Action catalog metadata.

    D02 stores static action schemas only. Execution and credential resolution
    are later deliverables and must be grant-gated.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    provider: str | None = Field(default=None, max_length=160)
    capability: str | None = Field(default=None, max_length=160)
    risk_level: str = Field(default="read", max_length=40)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] | None = None

    @field_validator("key", "provider", "capability")
    @classmethod
    def _keys(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_key(value)


class ResourceManifest(BaseModel):
    """Resource schema contributed by a plugin."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    schema_data: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("schema", "schema_data"),
        serialization_alias="schema",
    )
    ui_schema: dict[str, Any] | None = None
    config: dict[str, Any] | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class PluginManifest(BaseModel):
    """Top-level StackOS plugin manifest."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="0.1.0", max_length=40)
    description: str = ""
    display_order: int = Field(default=500, ge=0, le=10_000)
    source: str = Field(default="builtin", max_length=40)
    capabilities: list[CapabilityManifest] = Field(default_factory=list)
    providers: list[ProviderManifest] = Field(default_factory=list)
    actions: list[ActionManifest] = Field(default_factory=list)
    resources: list[ResourceManifest] = Field(default_factory=list)
    ui: dict[str, Any] | None = None
    config: dict[str, Any] | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        return _validate_key(value)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _plugin_manifest_paths() -> list[Path]:
    root = _repo_root() / "plugins"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*/plugin.yaml") if path.is_file())


def _bundled_plugin_manifest_nodes() -> list[Traversable]:
    root = resources.files("stackos").joinpath("_assets").joinpath("plugins")
    if not root.is_dir():
        return []
    return sorted(
        [
            plugin_dir.joinpath("plugin.yaml")
            for plugin_dir in root.iterdir()
            if plugin_dir.is_dir() and plugin_dir.joinpath("plugin.yaml").is_file()
        ],
        key=lambda node: str(node),
    )


def load_plugin_manifest_file(path: Path) -> PluginManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"plugin manifest must be a YAML object: {path}")
    return PluginManifest.model_validate(raw)


def _load_plugin_manifest_node(node: Traversable) -> PluginManifest:
    raw = yaml.safe_load(node.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"plugin manifest must be a YAML object: {node}")
    return PluginManifest.model_validate(raw)


def load_plugin_manifest_files() -> tuple[PluginManifest, ...]:
    bundled = [_load_plugin_manifest_node(node) for node in _bundled_plugin_manifest_nodes()]
    clone = [load_plugin_manifest_file(path) for path in _plugin_manifest_paths()]
    manifests = {manifest.slug: manifest for manifest in bundled}
    manifests.update({manifest.slug: manifest for manifest in clone})
    return tuple(
        sorted(
            manifests.values(),
            key=lambda manifest: plugin_sort_key(
                manifest.slug,
                manifest.model_dump(mode="json", by_alias=True),
            ),
        )
    )


def plugin_display_order(
    slug: str | None,
    manifest_json: dict[str, Any] | None = None,
) -> int:
    if manifest_json is not None:
        raw = manifest_json.get("display_order")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.isdecimal():
            return int(raw)
    if slug is not None and slug in _DEFAULT_PLUGIN_DISPLAY_ORDER:
        return _DEFAULT_PLUGIN_DISPLAY_ORDER[slug]
    return 500


def plugin_sort_key(
    slug: str | None,
    manifest_json: dict[str, Any] | None = None,
) -> tuple[int, str]:
    return (plugin_display_order(slug, manifest_json), slug or "")


_OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}
_TEXT_RECORD_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}
_ARTIFACT_RESOURCE_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "uri": {"type": "string"},
        "kind": {"type": "string"},
        "metadata": {"type": "object", "additionalProperties": True},
    },
    "required": ["uri"],
}
_IMAGE_ACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "data": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "url": {"type": "string"},
                    "artifact_ref": {"type": "string"},
                    "artifact_id": {"type": "integer"},
                    "file_format": {"type": "string", "enum": ["webp", "png", "jpg", "jpeg"]},
                    "source_model": {"type": "string"},
                },
            },
        },
        "artifact_refs": {"type": "array", "items": {"type": "string"}},
        "usage": {"type": "object", "additionalProperties": True},
    },
}
_VIDEO_ACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "request_id": {"type": "string"},
        "status": {"type": "string"},
        "data": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "url": {"type": "string"},
                    "artifact_ref": {"type": "string"},
                    "artifact_id": {"type": "integer"},
                    "file_format": {"type": "string", "enum": ["mp4"]},
                    "source_model": {"type": "string"},
                    "request_id": {"type": "string"},
                },
            },
        },
        "artifact_refs": {"type": "array", "items": {"type": "string"}},
        "usage": {"type": "object", "additionalProperties": True},
    },
}
_WEB_SCRAPE_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url"],
    "properties": {
        "url": {"type": "string"},
        "formats": {"type": "array", "items": {"type": "string"}},
        "only_main_content": {"type": "boolean"},
    },
}
_WEB_CRAWL_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url"],
    "properties": {
        "url": {"type": "string"},
        "max_depth": {"type": "integer"},
        "limit": {"type": "integer"},
    },
}
_WEB_MAP_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url"],
    "properties": {
        "url": {"type": "string"},
        "search": {"type": "string"},
    },
}
_WEB_EXTRACT_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url"],
    "properties": {
        "url": {"type": "string"},
        "schema": {"type": "object", "additionalProperties": True},
        "prompt": {"type": "string"},
    },
}
_WEB_READ_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["url"],
    "properties": {"url": {"type": "string"}},
}
_SITEMAP_FETCH_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["urls"],
    "properties": {
        "urls": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 20,
        },
        "timeout_s": {"type": "number", "minimum": 0.1, "maximum": 60},
        "max_index_depth": {"type": "integer", "minimum": 0, "maximum": 4},
        "max_entries": {"type": "integer", "minimum": 1, "maximum": 20000},
    },
}
_REDDIT_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subreddit", "query"],
    "properties": {
        "subreddit": {"type": "string"},
        "query": {"type": "string"},
        "sort": {"type": "string"},
        "limit": {"type": "integer"},
    },
}
_REDDIT_TOP_QUESTIONS_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subreddit"],
    "properties": {
        "subreddit": {"type": "string"},
        "time_filter": {"type": "string"},
        "limit": {"type": "integer"},
    },
}
_MOCK_SCENARIOS = [
    "success",
    "partial_success",
    "provider_error",
    "invalid_credentials",
    "rate_limit",
    "timeout",
]
_MOCK_ECHO_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["message"],
    "properties": {
        "message": {"type": "string"},
        "scenario": {
            "type": "string",
            "enum": _MOCK_SCENARIOS,
        },
        "echo": {"type": "object", "additionalProperties": True},
        "cost_cents": {"type": "integer"},
    },
}

_CODE_PLUGIN_MANIFESTS: tuple[PluginManifest, ...] = (
    PluginManifest(
        slug="core",
        name="StackOS Core",
        description="Domain-neutral project, workflow, run, context, auth, and catalog primitives.",
        display_order=900,
        capabilities=[
            CapabilityManifest(
                key="plugin-catalog",
                name="Plugin Catalog",
                description="List plugins, capabilities, providers, and action schemas.",
                kind="core",
            ),
            CapabilityManifest(
                key="workflow-runtime",
                name="Workflow Runtime",
                description="Reusable workflow templates, run plans, runs, gates, and audit.",
                kind="core",
            ),
            CapabilityManifest(
                key="project-data",
                name="Project Data",
                description=(
                    "Bounded project context, learnings, experiments, decisions, and artifacts."
                ),
                kind="core",
            ),
        ],
        providers=[
            ProviderManifest(
                key="local-daemon",
                name="Local StackOS Daemon",
                description="Local storage, validation, grant enforcement, and audit provider.",
                auth_type="local",
                auth_methods=[
                    AuthMethodManifest(
                        key="local",
                        label="Local daemon",
                        auth_type="local",
                        payload_format="none",
                    )
                ],
                config={
                    "setup": {
                        "credential_label": "Local daemon state",
                        "setup_note": (
                            "No external registration is required. Use the local StackOS "
                            "daemon/UI for setup, connections, and project state."
                        ),
                        "fallback_url": "http://127.0.0.1:5180/",
                        "fallback_reason": (
                            "Local StackOS provider; no vendor registration page exists."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {"fallback_url": "verified"},
                    }
                },
            )
        ],
        actions=[
            ActionManifest(
                key="catalog.describe",
                name="Describe Catalog",
                description="Describe installed plugin catalog metadata.",
                provider="local-daemon",
                capability="plugin-catalog",
                risk_level="read",
                input_schema=_OBJECT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
            )
        ],
        resources=[
            ResourceManifest(
                key="project-note",
                name="Project Note",
                description="Domain-neutral project record for agent-visible context.",
                schema_data=_TEXT_RECORD_SCHEMA,
            ),
            ResourceManifest(
                key="learning",
                name="Learning",
                description="Durable observation or lesson derived from prior work.",
                schema_data=_TEXT_RECORD_SCHEMA,
            ),
            ResourceManifest(
                key="experiment",
                name="Experiment",
                description="Experiment definition or result summary owned by the project.",
                schema_data=_OBJECT_SCHEMA,
            ),
        ],
    ),
    PluginManifest(
        slug="utils",
        name="Utilities",
        description="Domain-neutral utility providers and actions reusable by any plugin.",
        display_order=910,
        capabilities=[
            CapabilityManifest(
                key="image-generation",
                name="Image Generation",
                description="Generate image artifacts through configured providers.",
                kind="utility",
            ),
            CapabilityManifest(
                key="video-generation",
                name="Video Generation",
                description="Generate short video artifacts through configured providers.",
                kind="utility",
            ),
            CapabilityManifest(
                key="web-retrieval",
                name="Web Retrieval",
                description="Read, scrape, and normalize external web content.",
                kind="utility",
            ),
            CapabilityManifest(
                key="community-research",
                name="Community Research",
                description="Retrieve community discussions and question signals.",
                kind="utility",
            ),
            CapabilityManifest(
                key="model-access",
                name="Model Access",
                description="Store and validate domain-neutral model gateway credentials.",
                kind="utility",
            ),
            CapabilityManifest(
                key="integration-testing",
                name="Integration Testing",
                description="Exercise StackOS auth, grants, connector, and audit flow locally.",
                kind="utility",
            ),
        ],
        providers=[
            ProviderManifest(
                key="openai-images",
                name="OpenAI Images",
                description="Image generation provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="API Key",
                                type="secret",
                                secret=True,
                                required=True,
                                placeholder="sk-...",
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the OpenAI API key from the OpenAI Platform. StackOS "
                        "resolves it inside the daemon for image generation and editing."
                    ),
                    "setup": {
                        "credential_label": "OpenAI API key",
                        "setup_note": (
                            "Create an OpenAI Platform account, add billing if needed, "
                            "create an API key, and store only that key in StackOS."
                        ),
                        "homepage_url": "https://openai.com/api/",
                        "signup_url": "https://platform.openai.com/signup",
                        "console_url": "https://platform.openai.com/",
                        "api_key_url": "https://platform.openai.com/api-keys",
                        "billing_url": (
                            "https://platform.openai.com/settings/organization/billing/overview"
                        ),
                        "docs_url": (
                            "https://developers.openai.com/api/docs/guides/image-generation"
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "verified",
                            "api_key_url": "verified",
                            "billing_url": "verified",
                            "docs_url": "verified",
                        },
                    },
                },
            ),
            ProviderManifest(
                key="xai-imagine",
                name="xAI Imagine",
                description="Grok Imagine image and video generation provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="xAI API Key",
                                type="secret",
                                secret=True,
                                required=True,
                                placeholder="xai-...",
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the xAI API key from console.x.ai. StackOS resolves it "
                        "inside the daemon for Grok Imagine image and video actions."
                    ),
                    "setup": {
                        "credential_label": "xAI API key",
                        "setup_note": (
                            "Create or sign in to xAI Console, add billing/credits if "
                            "needed, create an API key, and store it in StackOS."
                        ),
                        "homepage_url": "https://x.ai/",
                        "signup_url": "https://console.x.ai/",
                        "console_url": "https://console.x.ai/",
                        "api_key_url": "https://console.x.ai/",
                        "billing_url": "https://console.x.ai/",
                        "docs_url": "https://docs.x.ai/overview",
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "verified",
                            "api_key_url": "directional",
                            "billing_url": "directional",
                            "docs_url": "verified",
                        },
                    },
                    "docs": [
                        "https://docs.x.ai/developers/model-capabilities/images/generation",
                        "https://docs.x.ai/developers/model-capabilities/video/generation",
                        "https://docs.x.ai/developers/models",
                        "https://docs.x.ai/developers/pricing",
                    ],
                },
            ),
            ProviderManifest(
                key="reve",
                name="Reve",
                description="Reve image create, edit, and remix provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="Reve API Key",
                                type="secret",
                                secret=True,
                                required=True,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the Reve API key from api.reve.com. Reve does not "
                        "document a free credential probe, so StackOS auth.test "
                        "records credential storage format without making a billable "
                        "image request."
                    ),
                    "setup": {
                        "credential_label": "Reve API key",
                        "setup_note": (
                            "Use the official Reve app/API console to register and find "
                            "API credentials; exact API-key and billing pages are not "
                            "publicly verified."
                        ),
                        "homepage_url": "https://app.reve.com/",
                        "signup_url": "https://app.reve.com/",
                        "console_url": "https://app.reve.com/",
                        "api_key_url": "https://app.reve.com/",
                        "billing_url": "https://app.reve.com/",
                        "docs_url": "https://api.reve.com/console/docs",
                        "fallback_url": "https://app.reve.com/",
                        "fallback_reason": (
                            "Exact Reve credential and billing routes are app-gated; "
                            "the official app is the closest public setup entry."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "directional",
                            "signup_url": "directional",
                            "console_url": "directional",
                            "api_key_url": "directional",
                            "billing_url": "directional",
                            "docs_url": "directional",
                            "fallback_url": "directional",
                        },
                    },
                    "docs": [
                        "https://api.reve.com/console/docs",
                        "https://api.reve.com/console/docs/create",
                        "https://api.reve.com/console/docs/edit",
                        "https://api.reve.com/console/docs/remix",
                        "https://api.reve.com/console/pricing",
                    ],
                },
            ),
            ProviderManifest(
                key="google-gemini-image",
                name="Google Gemini Image",
                description="Gemini Nano Banana image generation and editing provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="Gemini API Key",
                                type="secret",
                                secret=True,
                                required=True,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the Gemini Developer API key from Google AI Studio. "
                        "StackOS resolves it inside the daemon for Gemini image "
                        "generation and editing actions."
                    ),
                    "setup": {
                        "credential_label": "Gemini API key",
                        "setup_note": (
                            "Create or sign in to Google AI Studio, create a Gemini API "
                            "key, attach billing in Google Cloud if required, and store "
                            "the key in StackOS."
                        ),
                        "homepage_url": "https://ai.google.dev/",
                        "signup_url": "https://aistudio.google.com/",
                        "console_url": "https://aistudio.google.com/",
                        "api_key_url": "https://aistudio.google.com/app/apikey",
                        "billing_url": "https://console.cloud.google.com/billing",
                        "docs_url": "https://ai.google.dev/gemini-api/docs/image-generation",
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "verified",
                            "api_key_url": "verified",
                            "billing_url": "verified",
                            "docs_url": "verified",
                        },
                    },
                    "docs": [
                        "https://ai.google.dev/gemini-api/docs/image-generation",
                        "https://ai.google.dev/gemini-api/docs/image-understanding",
                        "https://ai.google.dev/api/generate-content",
                        "https://ai.google.dev/gemini-api/docs/pricing",
                    ],
                },
            ),
            ProviderManifest(
                key="google-veo",
                name="Google Veo",
                description="Google Gemini API Veo video generation provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="Gemini API Key",
                                type="secret",
                                secret=True,
                                required=True,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the Gemini Developer API key from Google AI Studio. "
                        "StackOS resolves it inside the daemon for Veo video actions."
                    ),
                    "setup": {
                        "credential_label": "Gemini API key",
                        "setup_note": (
                            "Create or sign in to Google AI Studio, create a Gemini API "
                            "key, attach billing in Google Cloud if required, and store "
                            "the key in StackOS."
                        ),
                        "homepage_url": "https://ai.google.dev/",
                        "signup_url": "https://aistudio.google.com/",
                        "console_url": "https://aistudio.google.com/",
                        "api_key_url": "https://aistudio.google.com/app/apikey",
                        "billing_url": "https://console.cloud.google.com/billing",
                        "docs_url": "https://ai.google.dev/gemini-api/docs/video-generation",
                        "fallback_url": "https://ai.google.dev/gemini-api/docs/video",
                        "fallback_reason": (
                            "Google may route Veo docs under video or video-generation "
                            "paths; both are official Gemini API documentation targets."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "verified",
                            "api_key_url": "verified",
                            "billing_url": "verified",
                            "docs_url": "directional",
                            "fallback_url": "verified",
                        },
                    },
                    "docs": [
                        "https://ai.google.dev/gemini-api/docs/video",
                        "https://ai.google.dev/gemini-api/docs/pricing",
                        "https://github.com/googleapis/python-genai",
                    ],
                },
            ),
            ProviderManifest(
                key="ideogram",
                name="Ideogram",
                description="Ideogram 4.0 image generation and remix provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="Ideogram API Key",
                                type="secret",
                                secret=True,
                                required=True,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the Ideogram API key from the Ideogram API dashboard. "
                        "StackOS resolves it inside the daemon for Ideogram image "
                        "generation and remix actions."
                    ),
                    "setup": {
                        "credential_label": "Ideogram API key",
                        "setup_note": (
                            "Open the Ideogram API dashboard, create or copy an API key, "
                            "confirm plan/credits, and store the key in StackOS."
                        ),
                        "homepage_url": "https://ideogram.ai/",
                        "signup_url": "https://ideogram.ai/api",
                        "console_url": "https://ideogram.ai/api",
                        "api_key_url": "https://ideogram.ai/api",
                        "billing_url": "https://ideogram.ai/pricing",
                        "docs_url": "https://developer.ideogram.ai/",
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "verified",
                            "api_key_url": "verified",
                            "billing_url": "verified",
                            "docs_url": "verified",
                        },
                    },
                    "docs": [
                        "https://developer.ideogram.ai/api-reference/api-reference/generate-v4",
                        "https://developer.ideogram.ai/api-reference/api-reference/remix-v4",
                        "https://ideogram.ai/api-pricing/",
                    ],
                },
            ),
            ProviderManifest(
                key="byteplus-ark",
                name="BytePlus ModelArk",
                description="BytePlus ModelArk media generation provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="BytePlus ModelArk API Key",
                                type="secret",
                                secret=True,
                                required=True,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the BytePlus ModelArk API key. StackOS resolves it "
                        "inside the daemon for Seedream image actions and Seedance "
                        "video generation actions."
                    ),
                    "setup": {
                        "credential_label": "BytePlus ModelArk API key",
                        "setup_note": (
                            "Register/sign in to BytePlus, enable ModelArk, create an "
                            "IAM/API key, confirm billing, and store the key in StackOS."
                        ),
                        "homepage_url": "https://www.byteplus.com/en/product/modelark",
                        "signup_url": "https://console.byteplus.com/",
                        "console_url": "https://console.byteplus.com/",
                        "api_key_url": "https://console.byteplus.com/iam/keymanage/",
                        "billing_url": "https://console.byteplus.com/finance/",
                        "docs_url": "https://docs.byteplus.com/en/docs/ModelArk/",
                        "fallback_url": "https://docs.byteplus.com/en/docs/ModelArk/",
                        "fallback_reason": (
                            "Some ModelArk setup routes are account-gated; official "
                            "console and ModelArk docs are the nearest setup targets."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "directional",
                            "console_url": "directional",
                            "api_key_url": "directional",
                            "billing_url": "directional",
                            "docs_url": "verified",
                            "fallback_url": "verified",
                        },
                    },
                    "docs": [
                        "https://docs.byteplus.com/en/docs/ModelArk/1541523",
                        "https://docs.byteplus.com/en/docs/ModelArk/1330310",
                        "https://docs.byteplus.com/en/docs/ModelArk/1544106",
                        "https://docs.byteplus.com/en/docs/ModelArk/1520757",
                        "https://docs.byteplus.com/en/docs/ModelArk/1521309",
                    ],
                },
            ),
            ProviderManifest(
                key="alibaba-wan",
                name="Alibaba Wan",
                description="Alibaba Model Studio Wan async video generation provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="Model Studio API Key",
                                type="secret",
                                secret=True,
                                required=True,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the Alibaba Model Studio/DashScope API key. StackOS "
                        "resolves it inside the daemon for Wan video actions."
                    ),
                    "setup": {
                        "credential_label": "Alibaba Model Studio/DashScope API key",
                        "setup_note": (
                            "Register for Alibaba Cloud, enable Model Studio/DashScope, "
                            "create API credentials, confirm billing, and store the key "
                            "in StackOS."
                        ),
                        "homepage_url": ("https://www.alibabacloud.com/help/en/model-studio/"),
                        "signup_url": "https://account.alibabacloud.com/register/",
                        "console_url": "https://bailian.console.alibabacloud.com/",
                        "api_key_url": "https://bailian.console.alibabacloud.com/",
                        "billing_url": "https://billing-cost.console.aliyun.com/",
                        "docs_url": ("https://www.alibabacloud.com/help/en/model-studio/"),
                        "fallback_url": ("https://www.alibabacloud.com/help/en/model-studio/"),
                        "fallback_reason": (
                            "Exact credential screens are account/region gated; the "
                            "official Model Studio console and docs are the nearest setup path."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "directional",
                            "api_key_url": "directional",
                            "billing_url": "directional",
                            "docs_url": "verified",
                            "fallback_url": "verified",
                        },
                    },
                    "docs": [
                        "https://www.alibabacloud.com/help/en/model-studio/video-generate-edit-model/",
                        "https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference/",
                        "https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference",
                        "https://github.com/dashscope/dashscope-sdk-python",
                    ],
                },
            ),
            ProviderManifest(
                key="kling",
                name="Kling AI",
                description="Kling AI text-to-video and image-to-video provider.",
                auth_type="jwt-access-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="access_key_secret",
                        label="Access key and secret key",
                        auth_type="jwt-access-key",
                        payload_format="json",
                        fields=[
                            AuthFieldManifest(
                                key="access_key",
                                label="Access Key",
                                type="secret",
                                secret=True,
                                required=True,
                            ),
                            AuthFieldManifest(
                                key="secret_key",
                                label="Secret Key",
                                type="secret",
                                secret=True,
                                required=True,
                            ),
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the Kling AccessKey and SecretKey as a daemon-held JSON "
                        "credential. StackOS signs short-lived JWTs inside the daemon."
                    ),
                    "setup": {
                        "credential_label": "Kling AccessKey and SecretKey",
                        "setup_note": (
                            "Use the official Kling AI/API console and documentation to "
                            "obtain access keys, then store both AccessKey and SecretKey "
                            "in StackOS."
                        ),
                        "homepage_url": "https://klingai.com/",
                        "signup_url": "https://app.klingai.com/",
                        "console_url": "https://app.klingai.com/",
                        "api_key_url": "https://kling.ai/document-api",
                        "docs_url": "https://kling.ai/document-api",
                        "fallback_url": "https://kling.ai/document-api",
                        "fallback_reason": (
                            "Exact billing/key pages are not publicly verified; official "
                            "Kling API docs and app are the closest setup targets."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "directional",
                            "console_url": "directional",
                            "api_key_url": "directional",
                            "docs_url": "verified",
                            "fallback_url": "verified",
                        },
                    },
                    "docs": [
                        "https://kling.ai/document-api/apiReference%2FcommonInfo",
                        "https://kling.ai/document-api/apiReference%2Fmodel%2FtextToVideo",
                        "https://kling.ai/document-api/apiReference%2Fmodel%2FimageToVideo",
                    ],
                },
            ),
            ProviderManifest(
                key="video-generation",
                name="Video Generation",
                description=(
                    "Provider-neutral video generation connection; the concrete vendor "
                    "backend is selected per deployment."
                ),
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="API Key",
                                type="secret",
                                secret=True,
                                required=True,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Credential storage and grants are wired; the vendor backend is "
                        "not selected yet. The OpenAI Sora Videos API was deprecated for "
                        "removal on 2026-09-24, so the first backend will be chosen among "
                        "actively supported video APIs before video.generate is enabled."
                    ),
                    "setup": {
                        "credential_label": "Concrete video provider credential",
                        "setup_note": (
                            "This neutral provider is a placeholder. Choose the concrete "
                            "video backend first, then use that provider's registration, "
                            "API-key, billing, and docs URLs."
                        ),
                        "fallback_url": "http://127.0.0.1:5180/",
                        "fallback_reason": (
                            "No vendor registration page exists for the neutral placeholder."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {"fallback_url": "verified"},
                    },
                },
            ),
            ProviderManifest(
                key="openrouter",
                name="OpenRouter",
                description=(
                    "Unified model API provider connection for future workflow-owned model actions."
                ),
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="OpenRouter API Key",
                                type="secret",
                                secret=True,
                                required=True,
                                placeholder="sk-or-v1-...",
                            ),
                            AuthFieldManifest(
                                key="http_referer",
                                label="HTTP Referer",
                                type="text",
                                secret=False,
                                required=False,
                            ),
                            AuthFieldManifest(
                                key="app_title",
                                label="Application Title",
                                type="text",
                                secret=False,
                                required=False,
                            ),
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "OpenRouter is currently stored and auth-tested as a connection "
                        "only. StackOS does not expose generic text-generation actions "
                        "until a workflow-owned policy, grant, and audit contract exists."
                    ),
                    "setup": {
                        "credential_label": "OpenRouter API key",
                        "setup_note": (
                            "Sign in to OpenRouter, create an API key, add credits if "
                            "needed, and store the key in StackOS."
                        ),
                        "homepage_url": "https://openrouter.ai/",
                        "signup_url": "https://openrouter.ai/sign-in",
                        "console_url": "https://openrouter.ai/settings/keys",
                        "api_key_url": "https://openrouter.ai/settings/keys",
                        "billing_url": "https://openrouter.ai/settings/credits",
                        "docs_url": "https://openrouter.ai/docs",
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "verified",
                            "api_key_url": "verified",
                            "billing_url": "verified",
                            "docs_url": "verified",
                        },
                    },
                    "docs": [
                        "https://openrouter.ai/docs/api/reference/authentication",
                        "https://openrouter.ai/docs/api/api-reference/models/get-models",
                    ],
                },
            ),
            ProviderManifest(
                key="firecrawl",
                name="Firecrawl",
                description="Web crawling and scraping provider.",
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="API Key",
                                type="secret",
                                secret=True,
                                required=True,
                                placeholder="fc-...",
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Store the Firecrawl API key from the Firecrawl app. StackOS "
                        "resolves it inside the daemon for scrape, crawl, and map actions."
                    ),
                    "setup": {
                        "credential_label": "Firecrawl API key",
                        "setup_note": (
                            "Sign in to Firecrawl, create/copy an API key, confirm plan "
                            "limits, and store the key in StackOS."
                        ),
                        "homepage_url": "https://www.firecrawl.dev/",
                        "signup_url": "https://app.firecrawl.dev/",
                        "console_url": "https://app.firecrawl.dev/",
                        "api_key_url": "https://app.firecrawl.dev/",
                        "billing_url": "https://www.firecrawl.dev/pricing",
                        "docs_url": "https://docs.firecrawl.dev/",
                        "fallback_url": "https://docs.firecrawl.dev/",
                        "fallback_reason": (
                            "Exact key screen can be app-gated; official app and docs "
                            "are the nearest setup targets."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "directional",
                            "console_url": "directional",
                            "api_key_url": "directional",
                            "billing_url": "verified",
                            "docs_url": "verified",
                            "fallback_url": "verified",
                        },
                    },
                },
            ),
            ProviderManifest(
                key="jina",
                name="Jina Reader",
                description=(
                    "Readable web document extraction provider; API key is optional "
                    "for higher quota."
                ),
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="API Key",
                                type="secret",
                                secret=True,
                                required=False,
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Jina Reader can run without a key; add a daemon-held API key "
                        "only when higher quota is needed."
                    ),
                    "setup": {
                        "credential_label": "Optional Jina API key",
                        "setup_note": (
                            "Jina Reader can run without a key. For higher quota, use "
                            "Jina's API dashboard and store the API key in StackOS."
                        ),
                        "homepage_url": "https://jina.ai/",
                        "signup_url": "https://jina.ai/",
                        "console_url": "https://jina.ai/api-dashboard/",
                        "api_key_url": "https://jina.ai/api-dashboard/",
                        "billing_url": "https://jina.ai/pricing",
                        "docs_url": "https://docs.jina.ai/",
                        "fallback_url": "https://jina.ai/api-dashboard/",
                        "fallback_reason": (
                            "Exact dashboard routing can vary; use the official Jina API dashboard."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "directional",
                            "console_url": "directional",
                            "api_key_url": "directional",
                            "billing_url": "verified",
                            "docs_url": "verified",
                            "fallback_url": "directional",
                        },
                    },
                },
            ),
            ProviderManifest(
                key="reddit",
                name="Reddit",
                description="Reddit research provider using OAuth client credentials.",
                auth_type="oauth-client-credentials",
                auth_methods=[
                    AuthMethodManifest(
                        key="client_credentials",
                        label="Client credentials",
                        auth_type="oauth-client-credentials",
                        payload_format="json",
                        fields=[
                            AuthFieldManifest(
                                key="client_id",
                                label="Client ID",
                                type="secret",
                                secret=True,
                                required=True,
                            ),
                            AuthFieldManifest(
                                key="client_secret",
                                label="Client Secret",
                                type="secret",
                                secret=True,
                                required=True,
                            ),
                            AuthFieldManifest(
                                key="user_agent",
                                label="User Agent",
                                type="secret",
                                secret=True,
                                required=True,
                            ),
                        ],
                    )
                ],
                config={
                    "credential_payload": {
                        "format": "json",
                        "required_keys": ["client_id", "client_secret", "user_agent"],
                        "secret_keys": ["client_secret"],
                    },
                    "setup_note": (
                        "Store the OAuth app JSON in the encrypted payload; do not "
                        "persist access tokens in agent-visible state."
                    ),
                    "setup": {
                        "credential_label": "Reddit app client id, client secret, and user agent",
                        "setup_note": (
                            "Create a Reddit app under developer preferences, then store "
                            "client id, client secret, and user agent in StackOS."
                        ),
                        "homepage_url": "https://www.reddit.com/",
                        "signup_url": "https://www.reddit.com/register/",
                        "console_url": "https://www.reddit.com/prefs/apps",
                        "api_key_url": "https://www.reddit.com/prefs/apps",
                        "docs_url": "https://www.reddit.com/dev/api/",
                        "verified_at": "2026-06-11",
                        "url_confidence": {
                            "homepage_url": "verified",
                            "signup_url": "verified",
                            "console_url": "verified",
                            "api_key_url": "verified",
                            "docs_url": "verified",
                        },
                    },
                },
            ),
            ProviderManifest(
                key="mock-provider",
                name="Mock Provider",
                description=(
                    "Local fake provider for end-to-end StackOS action execution tests "
                    "without external API accounts."
                ),
                auth_type="api-key",
                auth_methods=[
                    AuthMethodManifest(
                        key="api_key",
                        label="API key",
                        auth_type="api-key",
                        payload_format="raw",
                        payload_field="api_key",
                        fields=[
                            AuthFieldManifest(
                                key="api_key",
                                label="Fake API Key",
                                type="secret",
                                secret=True,
                                required=True,
                                placeholder="mock_...",
                            )
                        ],
                    )
                ],
                config={
                    "setup_note": (
                        "Use any non-empty fake key. This provider never calls a vendor "
                        "network, but it still goes through credential storage, run-plan "
                        "grants, action execution, redaction, and audit."
                    ),
                    "setup": {
                        "credential_label": "Fake API key",
                        "setup_note": (
                            "No vendor registration exists. Use any non-empty fake key "
                            "when testing StackOS credential and action execution flow."
                        ),
                        "fallback_url": "http://127.0.0.1:5180/",
                        "fallback_reason": (
                            "Local test provider; no vendor registration page exists."
                        ),
                        "verified_at": "2026-06-11",
                        "url_confidence": {"fallback_url": "verified"},
                    },
                },
            ),
        ],
        actions=[
            ActionManifest(
                key="image.generate",
                name="Generate Image",
                description=(
                    "Generate and persist image artifacts through an explicit GPT Image "
                    "model profile."
                ),
                provider="openai-images",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 32000},
                        "size": {
                            "type": "string",
                            "description": (
                                "StackOS-supported size profile: auto, 1024x1024, "
                                "1536x1024, or 1024x1536."
                            ),
                        },
                        "quality": {
                            "type": "string",
                            "enum": ["auto", "low", "medium", "high"],
                        },
                        "n": {"type": "integer"},
                        "model": {
                            "type": "string",
                            "enum": [
                                "gpt-image-2",
                                "gpt-image-1.5",
                                "gpt-image-1",
                                "gpt-image-1-mini",
                            ],
                        },
                        "output_format": {"type": "string", "enum": ["webp", "png", "jpeg"]},
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "openai-images",
                    "operation": "image.generate",
                    "requires_credential": True,
                    "budget_kind": "openai-images",
                    "enforce_budget": True,
                    "default_model": "gpt-image-2",
                    "model_profiles": {
                        "gpt-image-2": {
                            "qualities": ["auto", "low", "medium", "high"],
                            "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                            "output_formats": ["webp", "png", "jpeg"],
                            "transparent_background": False,
                            "docs": [
                                "https://developers.openai.com/api/docs/guides/image-generation",
                                "https://developers.openai.com/api/reference/resources/images",
                            ],
                        },
                        "gpt-image-1.5": {
                            "qualities": ["auto", "low", "medium", "high"],
                            "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                            "output_formats": ["webp", "png", "jpeg"],
                            "docs": [
                                "https://developers.openai.com/api/docs/guides/image-generation",
                                "https://developers.openai.com/api/reference/resources/images",
                            ],
                        },
                        "gpt-image-1": {
                            "qualities": ["auto", "low", "medium", "high"],
                            "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                            "output_formats": ["webp", "png", "jpeg"],
                            "docs": [
                                "https://developers.openai.com/api/docs/guides/image-generation",
                                "https://developers.openai.com/api/reference/resources/images",
                            ],
                        },
                        "gpt-image-1-mini": {
                            "qualities": ["auto", "low", "medium", "high"],
                            "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                            "output_formats": ["webp", "png", "jpeg"],
                            "docs": [
                                "https://developers.openai.com/api/docs/guides/image-generation",
                                "https://developers.openai.com/api/reference/resources/images",
                            ],
                        },
                    },
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text"],
                            "output": ["image"],
                        },
                        "modes": ["text-to-image"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/images/generations",
                            "persistence": (
                                "OpenAI base64 image outputs are written to generated "
                                "assets and registered as generic image artifacts; action "
                                "responses return artifact URLs and artifact ids when "
                                "execution has a repository session."
                            ),
                        },
                        "limits": {
                            "prompt_max_chars": 32000,
                            "max_outputs_per_request": 10,
                        },
                        "models": {
                            "gpt-image-2": {
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                                "max_outputs_per_request": 10,
                                "transparent_background": False,
                            },
                            "gpt-image-1.5": {
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                            },
                            "gpt-image-1": {
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                            },
                            "gpt-image-1-mini": {
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                            },
                        },
                        "safety": {
                            "moderation": "OpenAI image generation moderation applies.",
                            "transparent_background": "Not supported for gpt-image-2.",
                            "watermark": "No StackOS-exposed watermark toggle.",
                        },
                        "unsupported_provider_features": [
                            "background parameter",
                            "gpt-image-2 custom WxH sizes beyond StackOS presets",
                            "moderation parameter",
                            "output_compression parameter",
                            "streaming partial images",
                            "Responses API conversational image generation",
                        ],
                        "doc_notes": [
                            (
                                "OpenAI's image guide and gpt-image-2 model page document "
                                "gpt-image-2 Image API support; some API-reference enum "
                                "snippets can lag that model listing."
                            )
                        ],
                        "docs": [
                            "https://developers.openai.com/api/docs/guides/image-generation",
                            "https://developers.openai.com/api/docs/models/gpt-image-2",
                            "https://developers.openai.com/api/reference/resources/images/methods/generate",
                        ],
                    },
                },
            ),
            ActionManifest(
                key="image.edit",
                name="Edit Image With References",
                description=(
                    "Compose or restyle images from input reference images (for example "
                    "a product photo) while keeping the referenced subject faithful."
                ),
                provider="openai-images",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt", "input_image_refs"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 32000},
                        "input_image_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 16,
                            "description": (
                                "Generated-assets artifact refs (png, jpg, webp, max 50 MB "
                                "each) used as input reference images; the first image "
                                "anchors edits."
                            ),
                        },
                        "size": {
                            "type": "string",
                            "description": (
                                "StackOS-supported size profile: auto, 1024x1024, "
                                "1536x1024, or 1024x1536."
                            ),
                        },
                        "quality": {
                            "type": "string",
                            "enum": ["auto", "low", "medium", "high"],
                        },
                        "n": {"type": "integer"},
                        "model": {
                            "type": "string",
                            "enum": [
                                "gpt-image-2",
                                "gpt-image-1.5",
                                "gpt-image-1",
                                "gpt-image-1-mini",
                            ],
                        },
                        "output_format": {"type": "string", "enum": ["webp", "png", "jpeg"]},
                        "input_fidelity": {
                            "type": "string",
                            "enum": ["high", "low"],
                            "description": (
                                "Input image fidelity control for gpt-image-1.5, "
                                "gpt-image-1, and gpt-image-1-mini; gpt-image-2 always "
                                "uses high fidelity."
                            ),
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "openai-images",
                    "operation": "image.edit",
                    "requires_credential": True,
                    "budget_kind": "openai-images",
                    "enforce_budget": True,
                    "default_model": "gpt-image-2",
                    "agent_guidance": (
                        "Use image.edit instead of image.generate when output must stay "
                        "faithful to an existing product, logo, or label. Restate the "
                        "preserve list in the prompt on every iteration."
                    ),
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text", "image"],
                            "output": ["image"],
                        },
                        "modes": ["image-to-image", "reference-image-compose"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/images/edits",
                            "persistence": (
                                "OpenAI base64 image outputs are written to generated "
                                "assets and registered as generic image artifacts; action "
                                "responses return artifact URLs and artifact ids when "
                                "execution has a repository session."
                            ),
                        },
                        "limits": {
                            "prompt_max_chars": 32000,
                            "max_input_image_bytes": 52428800,
                            "max_outputs_per_request": 10,
                        },
                        "models": {
                            "gpt-image-2": {
                                "max_input_images": 16,
                                "max_input_image_bytes": 52428800,
                                "input_image_formats": ["png", "jpg", "jpeg", "webp"],
                                "input_fidelity": "always-high; do not send parameter",
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                            },
                            "gpt-image-1.5": {
                                "max_input_images": 16,
                                "max_input_image_bytes": 52428800,
                                "input_image_formats": ["png", "jpg", "jpeg", "webp"],
                                "input_fidelity": ["low", "high"],
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                            },
                            "gpt-image-1": {
                                "max_input_images": 16,
                                "max_input_image_bytes": 52428800,
                                "input_image_formats": ["png", "jpg", "jpeg", "webp"],
                                "input_fidelity": ["low", "high"],
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                            },
                            "gpt-image-1-mini": {
                                "max_input_images": 16,
                                "max_input_image_bytes": 52428800,
                                "input_image_formats": ["png", "jpg", "jpeg", "webp"],
                                "input_fidelity": ["low", "high"],
                                "sizes": ["auto", "1024x1024", "1536x1024", "1024x1536"],
                                "qualities": ["auto", "low", "medium", "high"],
                                "output_formats": ["webp", "png", "jpeg"],
                            },
                        },
                        "safety": {
                            "moderation": "OpenAI image generation moderation applies.",
                            "transparent_background": "Not supported for gpt-image-2.",
                            "watermark": "No StackOS-exposed watermark toggle.",
                        },
                        "unsupported_provider_features": [
                            "background parameter",
                            "explicit mask uploads",
                            "gpt-image-2 custom WxH sizes beyond StackOS presets",
                            "JSON image_url or file_id references",
                            "moderation parameter",
                            "output_compression parameter",
                            "Responses API multi-turn edits",
                            "streaming partial images",
                        ],
                        "doc_notes": [
                            (
                                "OpenAI's image guide and gpt-image-2 model page document "
                                "gpt-image-2 Image API support; some API-reference enum "
                                "snippets can lag that model listing."
                            )
                        ],
                        "docs": [
                            "https://developers.openai.com/api/docs/guides/image-generation",
                            "https://developers.openai.com/api/docs/models/gpt-image-2",
                            "https://developers.openai.com/api/reference/resources/images/methods/edit",
                        ],
                    },
                    "docs": [
                        "https://developers.openai.com/api/docs/guides/image-generation",
                        "https://developers.openai.com/api/reference/resources/images",
                    ],
                },
            ),
            ActionManifest(
                key="xai.image.generate",
                name="Generate Grok Image",
                description="Generate and persist images through xAI Grok Imagine.",
                provider="xai-imagine",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 2560},
                        "aspect_ratio": {
                            "type": "string",
                            "enum": [
                                "1:1",
                                "16:9",
                                "9:16",
                                "4:3",
                                "3:4",
                                "3:2",
                                "2:3",
                                "2:1",
                                "1:2",
                                "19.5:9",
                                "9:19.5",
                                "20:9",
                                "9:20",
                                "auto",
                            ],
                        },
                        "resolution": {"type": "string", "enum": ["1k", "2k"]},
                        "n": {"type": "integer", "minimum": 1, "maximum": 10},
                        "model": {
                            "type": "string",
                            "enum": ["grok-imagine-image-quality"],
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "xai-imagine",
                    "operation": "image.generate",
                    "requires_credential": True,
                    "budget_kind": "xai-imagine",
                    "enforce_budget": True,
                    "default_model": "grok-imagine-image-quality",
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text"],
                            "output": ["image"],
                        },
                        "modes": ["text-to-image"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/images/generations",
                            "persistence": (
                                "xAI image outputs are requested as base64 where supported, "
                                "written to generated assets, and registered as generic "
                                "image artifacts during repository-backed execution."
                            ),
                        },
                        "limits": {
                            "max_outputs_per_request": 10,
                            "resolutions": ["1k", "2k"],
                            "aspect_ratios": [
                                "1:1",
                                "16:9",
                                "9:16",
                                "4:3",
                                "3:4",
                                "3:2",
                                "2:3",
                                "2:1",
                                "1:2",
                                "19.5:9",
                                "9:19.5",
                                "20:9",
                                "9:20",
                                "auto",
                            ],
                        },
                        "models": {
                            "grok-imagine-image-quality": {
                                "status": "StackOS v1 quality image model",
                                "resolutions": ["1k", "2k"],
                            }
                        },
                        "safety": {
                            "moderation": (
                                "Provider moderation result may be returned on image responses."
                            ),
                            "watermark": "No official image watermark toggle verified in xAI docs.",
                        },
                        "unsupported_provider_features": [
                            "cheaper grok-imagine-image model",
                            "legacy grok-imagine-image-pro model",
                            "mask/inpainting controls",
                            "transparent background controls",
                        ],
                        "docs": [
                            "https://docs.x.ai/developers/model-capabilities/images/generation",
                            "https://docs.x.ai/developers/models",
                            "https://docs.x.ai/developers/pricing",
                        ],
                    },
                    "docs": [
                        "https://docs.x.ai/developers/model-capabilities/images/generation",
                        "https://docs.x.ai/developers/models",
                        "https://docs.x.ai/developers/pricing",
                    ],
                },
            ),
            ActionManifest(
                key="xai.image.edit",
                name="Edit Grok Image",
                description="Edit or compose up to three images through xAI Grok Imagine.",
                provider="xai-imagine",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt", "input_image_refs"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 2560},
                        "input_image_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 3,
                            "description": (
                                "Generated-assets PNG/JPEG refs sent as xAI JSON data URIs."
                            ),
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": [
                                "1:1",
                                "16:9",
                                "9:16",
                                "4:3",
                                "3:4",
                                "3:2",
                                "2:3",
                                "2:1",
                                "1:2",
                                "19.5:9",
                                "9:19.5",
                                "20:9",
                                "9:20",
                                "auto",
                            ],
                        },
                        "resolution": {"type": "string", "enum": ["1k", "2k"]},
                        "model": {
                            "type": "string",
                            "enum": ["grok-imagine-image-quality"],
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "xai-imagine",
                    "operation": "image.edit",
                    "requires_credential": True,
                    "budget_kind": "xai-imagine",
                    "enforce_budget": True,
                    "default_model": "grok-imagine-image-quality",
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text", "image"],
                            "output": ["image"],
                        },
                        "modes": ["image-to-image", "multi-image-compose", "style-transfer"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/images/edits",
                            "persistence": (
                                "xAI-hosted temporary output URLs are downloaded and "
                                "persisted into generated assets; action responses return "
                                "local URLs and artifact ids when repository-backed."
                            ),
                        },
                        "limits": {
                            "max_input_images": 3,
                            "max_input_image_bytes": 20971520,
                            "input_image_formats": ["png", "jpg", "jpeg"],
                            "aspect_ratio": (
                                "Only accepted for multi-image edits; single-image edits keep "
                                "the input image ratio."
                            ),
                            "resolutions": ["1k", "2k"],
                        },
                        "models": {
                            "grok-imagine-image-quality": {
                                "status": "StackOS v1 quality image model",
                                "max_input_images": 3,
                            }
                        },
                        "safety": {
                            "moderation": (
                                "Provider moderation result may be returned on image responses."
                            ),
                            "watermark": "No official image watermark toggle verified in xAI docs.",
                        },
                        "unsupported_provider_features": [
                            "cheaper grok-imagine-image model",
                            "OpenAI SDK multipart images.edit shape",
                            "mask/inpainting controls",
                            "transparent background controls",
                            "video input",
                        ],
                        "docs": [
                            "https://docs.x.ai/developers/model-capabilities/images/editing",
                            "https://docs.x.ai/developers/model-capabilities/images/multi-image-editing",
                            "https://docs.x.ai/developers/models",
                            "https://docs.x.ai/developers/pricing",
                        ],
                    },
                    "docs": [
                        "https://docs.x.ai/developers/model-capabilities/images/editing",
                        "https://docs.x.ai/developers/model-capabilities/images/multi-image-editing",
                        "https://docs.x.ai/developers/models",
                        "https://docs.x.ai/developers/pricing",
                    ],
                },
            ),
            ActionManifest(
                key="xai.video.generate",
                name="Generate Grok Video",
                description=(
                    "Generate and persist Grok Imagine videos from text, one first-frame "
                    "image, or up to seven reference images."
                ),
                provider="xai-imagine",
                capability="video-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 2560},
                        "mode": {
                            "type": "string",
                            "enum": ["text-to-video", "image-to-video", "reference-to-video"],
                        },
                        "duration": {"type": "integer", "minimum": 1, "maximum": 15},
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
                        },
                        "resolution": {"type": "string", "enum": ["480p", "720p"]},
                        "model": {
                            "type": "string",
                            "enum": ["grok-imagine-video"],
                        },
                        "input_image_ref": {
                            "type": "string",
                            "description": "Generated-assets PNG/JPEG ref for image-to-video.",
                        },
                        "reference_image_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 7,
                            "description": (
                                "Generated-assets PNG/JPEG refs for reference-to-video; "
                                "cannot be combined with image-to-video."
                            ),
                        },
                        "poll_interval_seconds": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 30,
                        },
                        "poll_timeout_seconds": {
                            "type": "number",
                            "minimum": 60,
                            "maximum": 1800,
                        },
                    },
                },
                output_schema=_VIDEO_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "xai-imagine",
                    "operation": "video.generate",
                    "requires_credential": True,
                    "budget_kind": "xai-imagine",
                    "enforce_budget": True,
                    "default_model": "grok-imagine-video",
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text", "image"],
                            "output": ["video"],
                        },
                        "modes": ["text-to-video", "image-to-video", "reference-to-video"],
                        "execution": {
                            "mode": "async",
                            "provider_endpoint": "/v1/videos/generations",
                            "poll_endpoint": "/v1/videos/{request_id}",
                            "persistence": (
                                "xAI temporary video URLs are downloaded immediately into "
                                "generated assets and registered as generic video artifacts."
                            ),
                        },
                        "limits": {
                            "duration_seconds": [1, 15],
                            "reference_to_video_duration_max_seconds": 10,
                            "max_reference_images": 7,
                            "input_image_formats": ["png", "jpg", "jpeg"],
                            "resolutions": ["480p", "720p"],
                            "aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"],
                        },
                        "models": {
                            "grok-imagine-video": {
                                "status": "StackOS v1 stable video model",
                                "resolutions": ["480p", "720p"],
                            },
                        },
                        "safety": {
                            "status_values": ["pending", "done", "expired", "failed"],
                            "watermark": "No official video watermark toggle verified in xAI docs.",
                            "url_retention": (
                                "Official docs say URLs are temporary; exact duration is "
                                "not specified."
                            ),
                        },
                        "unsupported_provider_features": [
                            "grok-imagine-video-1.5-preview image-to-video-only preview model",
                            "video editing endpoint",
                            "video extension endpoint",
                            "custom fps",
                            "custom audio controls",
                            "exact provider URL-expiry duration",
                        ],
                        "docs": [
                            "https://docs.x.ai/developers/model-capabilities/video/generation",
                            "https://docs.x.ai/developers/model-capabilities/video/image-to-video",
                            "https://docs.x.ai/developers/model-capabilities/video/reference-to-video",
                            "https://docs.x.ai/developers/models",
                            "https://docs.x.ai/developers/pricing",
                        ],
                    },
                    "docs": [
                        "https://docs.x.ai/developers/model-capabilities/video/generation",
                        "https://docs.x.ai/developers/model-capabilities/video/image-to-video",
                        "https://docs.x.ai/developers/model-capabilities/video/reference-to-video",
                        "https://docs.x.ai/developers/models",
                        "https://docs.x.ai/developers/pricing",
                    ],
                },
            ),
            ActionManifest(
                key="google.video.generate",
                name="Generate Google Veo Video",
                description=(
                    "Generate and persist Veo videos through the Gemini API from "
                    "text, an input image, or first and last frame images."
                ),
                provider="google-veo",
                capability="video-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1},
                        "model": {
                            "type": "string",
                            "enum": [
                                "veo-3.1-generate-preview",
                                "veo-3.1-fast-generate-preview",
                                "veo-3.1-lite-generate-preview",
                            ],
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["text-to-video", "image-to-video", "first-last-frame"],
                        },
                        "duration_seconds": {"type": "integer", "enum": [4, 6, 8]},
                        "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16"]},
                        "resolution": {"type": "string", "enum": ["720p", "1080p", "4k"]},
                        "input_image_ref": {
                            "type": "string",
                            "description": (
                                "Generated-assets PNG/JPEG ref for image-to-video or the "
                                "first frame in first-last-frame mode."
                            ),
                        },
                        "last_frame_ref": {
                            "type": "string",
                            "description": (
                                "Generated-assets PNG/JPEG ref for first-last-frame mode."
                            ),
                        },
                        "enhance_prompt": {"type": "boolean"},
                        "person_generation": {
                            "type": "string",
                            "enum": ["allow_all", "allow_adult"],
                        },
                        "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                        "poll_interval_seconds": {"type": "number", "minimum": 1, "maximum": 60},
                        "poll_timeout_seconds": {"type": "number", "minimum": 60, "maximum": 3600},
                    },
                },
                output_schema=_VIDEO_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "google-veo",
                    "operation": "video.generate",
                    "requires_credential": True,
                    "budget_kind": "google-veo",
                    "enforce_budget": True,
                    "default_model": "veo-3.1-generate-preview",
                    "capability_metadata": {
                        "modalities": {"input": ["text", "image"], "output": ["video"]},
                        "modes": ["text-to-video", "image-to-video", "first-last-frame"],
                        "execution": {
                            "mode": "async",
                            "provider_endpoint": ("/v1beta/models/{model}:predictLongRunning"),
                            "poll_endpoint": "/v1beta/operations/{operation}",
                            "persistence": (
                                "Veo-generated media URIs are downloaded immediately into "
                                "generated assets and registered as generic video artifacts."
                            ),
                        },
                        "limits": {
                            "duration_seconds": [4, 6, 8],
                            "aspect_ratios": ["16:9", "9:16"],
                            "resolutions": ["720p", "1080p", "4k"],
                            "resolutions_by_model": {
                                "veo-3.1-generate-preview": ["720p", "1080p", "4k"],
                                "veo-3.1-fast-generate-preview": ["720p", "1080p", "4k"],
                                "veo-3.1-lite-generate-preview": ["720p", "1080p"],
                            },
                            "duration_rules": [
                                "1080p and 4k require duration_seconds 8",
                                "first-last-frame requires duration_seconds 8",
                            ],
                            "person_generation": {
                                "text-to-video": ["allow_all"],
                                "image-to-video": ["allow_adult"],
                                "first-last-frame": ["allow_adult"],
                            },
                            "input_image_formats": ["png", "jpg", "jpeg"],
                            "max_input_image_bytes": 20000000,
                        },
                        "models": {
                            "veo-3.1-generate-preview": {
                                "status": "default",
                                "modes": ["text-to-video", "image-to-video", "first-last-frame"],
                            },
                            "veo-3.1-fast-generate-preview": {"status": "supported"},
                            "veo-3.1-lite-generate-preview": {
                                "status": "supported",
                                "resolutions": ["720p", "1080p"],
                            },
                        },
                        "safety": {
                            "watermark": "Generated videos include Google's SynthID watermark.",
                            "person_generation": {
                                "text-to-video": ["allow_all"],
                                "image-to-video": ["allow_adult"],
                                "first-last-frame": ["allow_adult"],
                            },
                            "retention": "Official docs describe temporary generated video URIs.",
                        },
                        "unsupported_provider_features": [
                            "reference-to-video",
                            "video extension",
                            "video-to-video editing",
                            "older Veo 3 and Veo 2 model ids",
                            "camera controls",
                            "audio generation controls",
                        ],
                        "docs": [
                            "https://ai.google.dev/gemini-api/docs/video",
                            "https://ai.google.dev/gemini-api/docs/pricing",
                            "https://github.com/googleapis/python-genai",
                        ],
                    },
                    "docs": [
                        "https://ai.google.dev/gemini-api/docs/video",
                        "https://ai.google.dev/gemini-api/docs/pricing",
                    ],
                },
            ),
            ActionManifest(
                key="alibaba.video.generate",
                name="Generate Alibaba Wan Video",
                description=(
                    "Generate and persist Wan videos through Alibaba Model Studio from "
                    "text, provider-fetchable images, or provider-fetchable video clips."
                ),
                provider="alibaba-wan",
                capability="video-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1},
                        "mode": {
                            "type": "string",
                            "enum": [
                                "text-to-video",
                                "image-to-video",
                                "first-last-frame",
                                "video-continuation",
                            ],
                        },
                        "region": {
                            "type": "string",
                            "enum": ["singapore", "beijing"],
                        },
                        "resolution": {"type": "string", "enum": ["720P", "1080P"]},
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                        },
                        "duration": {"type": "integer", "minimum": 2, "maximum": 15},
                        "prompt_extend": {"type": "boolean"},
                        "watermark": {"type": "boolean"},
                        "negative_prompt": {"type": "string"},
                        "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                        "first_frame_url": {
                            "type": "string",
                            "description": (
                                "Provider-fetchable http(s) image URL for image modes."
                            ),
                        },
                        "last_frame_url": {
                            "type": "string",
                            "description": (
                                "Provider-fetchable http(s) image URL for first-last-frame."
                            ),
                        },
                        "first_clip_url": {
                            "type": "string",
                            "description": (
                                "Provider-fetchable http(s) video URL for video-continuation."
                            ),
                        },
                        "audio_url": {"type": "string"},
                        "poll_interval_seconds": {"type": "number", "minimum": 1, "maximum": 60},
                        "poll_timeout_seconds": {"type": "number", "minimum": 60, "maximum": 3600},
                    },
                },
                output_schema=_VIDEO_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "alibaba-wan",
                    "operation": "video.generate",
                    "requires_credential": True,
                    "budget_kind": "alibaba-wan",
                    "enforce_budget": True,
                    "default_models": {
                        "text-to-video": "wan2.6-t2v",
                        "image-to-video": "wan2.7-i2v",
                    },
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text", "image-url", "video-url", "audio-url"],
                            "output": ["video"],
                        },
                        "modes": [
                            "text-to-video",
                            "image-to-video",
                            "first-last-frame",
                            "video-continuation",
                        ],
                        "execution": {
                            "mode": "async",
                            "provider_endpoint": (
                                "/api/v1/services/aigc/video-generation/video-synthesis"
                            ),
                            "poll_endpoint": "/api/v1/tasks/{task_id}",
                            "persistence": (
                                "Wan task output video_url is downloaded immediately into "
                                "generated assets and registered as a generic video artifact."
                            ),
                        },
                        "limits": {
                            "duration_seconds": [2, 15],
                            "resolutions": ["720P", "1080P"],
                            "text_to_video_aspect_ratios": [
                                "16:9",
                                "9:16",
                                "1:1",
                                "4:3",
                                "3:4",
                            ],
                            "media_inputs": "http(s) URLs only in StackOS v1 connector",
                        },
                        "models": {
                            "wan2.6-t2v": {
                                "modes": ["text-to-video"],
                                "status": "official T2V API reference contract",
                            },
                            "wan2.7-i2v": {
                                "modes": [
                                    "image-to-video",
                                    "first-last-frame",
                                    "video-continuation",
                                ]
                            },
                        },
                        "safety": {
                            "url_retention": "Official docs describe 24-hour temporary URLs.",
                            "prompt_extend_default": True,
                        },
                        "unsupported_provider_features": [
                            "local generated-assets image upload",
                            "model override in public action schema",
                            (
                                "wan2.7-t2v until Alibaba publishes a matching "
                                "executable T2V API contract"
                            ),
                            "wan2.7-r2v reference-to-video",
                            "wan2.7-videoedit video editing",
                            "advanced motion controls",
                        ],
                        "docs": [
                            "https://www.alibabacloud.com/help/en/model-studio/video-generate-edit-model/",
                            "https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference/",
                            "https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference",
                            "https://github.com/dashscope/dashscope-sdk-python",
                        ],
                    },
                    "docs": [
                        "https://www.alibabacloud.com/help/en/model-studio/video-generate-edit-model/",
                        "https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference/",
                        "https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference",
                    ],
                },
            ),
            ActionManifest(
                key="byteplus.video.generate",
                name="Generate BytePlus Seedance Video",
                description=(
                    "Generate and persist Seedance videos through BytePlus ModelArk "
                    "from text, generated-assets images, or reference media URLs."
                ),
                provider="byteplus-ark",
                capability="video-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "prompt": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "enum": [
                                "text-to-video",
                                "image-to-video",
                                "first-last-frame",
                                "reference-to-video",
                            ],
                        },
                        "model": {
                            "type": "string",
                            "enum": [
                                "dreamina-seedance-2-0-260128",
                                "dreamina-seedance-2-0-fast-260128",
                                "seedance-1-5-pro-251215",
                                "seedance-1-0-pro-250528",
                                "seedance-1-0-pro-fast-251015",
                            ],
                        },
                        "region": {"type": "string", "enum": ["ap-southeast-1"]},
                        "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"]},
                        "ratio": {
                            "type": "string",
                            "enum": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"],
                        },
                        "duration": {
                            "type": "integer",
                            "description": (
                                "-1 for model default on 2.0/1.5 only; "
                                "model-specific ranges are 2.0: 4-15, "
                                "1.5 Pro: 4-12, 1.0: 2-12 seconds."
                            ),
                        },
                        "input_image_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Generated-assets image refs. Use one for image-to-video "
                                "or two for first-last-frame."
                            ),
                        },
                        "reference_video_urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 3,
                        },
                        "reference_audio_urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 3,
                        },
                        "generate_audio": {"type": "boolean"},
                        "watermark": {"type": "boolean"},
                        "return_last_frame": {"type": "boolean"},
                        "seed": {"type": "integer", "minimum": -1, "maximum": 4294967295},
                        "priority": {"type": "integer", "minimum": 0, "maximum": 9},
                        "poll_interval_seconds": {"type": "number", "minimum": 1, "maximum": 60},
                        "poll_timeout_seconds": {"type": "number", "minimum": 60, "maximum": 3600},
                    },
                },
                output_schema=_VIDEO_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "byteplus-seedance",
                    "operation": "video.generate",
                    "requires_credential": True,
                    "budget_kind": "byteplus-ark",
                    "enforce_budget": True,
                    "default_model": "dreamina-seedance-2-0-260128",
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text", "image", "video-url", "audio-url"],
                            "output": ["video"],
                        },
                        "modes": [
                            "text-to-video",
                            "image-to-video",
                            "first-last-frame",
                            "reference-to-video",
                        ],
                        "execution": {
                            "mode": "async",
                            "provider_endpoint": "/api/v3/contents/generations/tasks",
                            "poll_endpoint": "/api/v3/contents/generations/tasks/{task_id}",
                            "persistence": (
                                "Seedance output video_url is downloaded immediately into "
                                "generated assets and registered as a generic video artifact."
                            ),
                        },
                        "limits": {
                            "duration_seconds": {
                                "dreamina-seedance-2-0-260128": [-1, 4, 15],
                                "dreamina-seedance-2-0-fast-260128": [-1, 4, 15],
                                "seedance-1-5-pro-251215": [-1, 4, 12],
                                "seedance-1-0-pro-250528": [2, 12],
                                "seedance-1-0-pro-fast-251015": [2, 12],
                            },
                            "resolutions": ["480p", "720p", "1080p"],
                            "ratios": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"],
                            "max_reference_images": 9,
                            "max_reference_videos": 3,
                            "max_reference_audio": 3,
                            "input_image_formats": ["jpg", "jpeg", "png", "webp", "bmp"],
                            "priority": [0, 9],
                            "seed": [-1, 4294967295],
                            "regions": ["ap-southeast-1"],
                            "reference_to_video_models": [
                                "dreamina-seedance-2-0-260128",
                                "dreamina-seedance-2-0-fast-260128",
                            ],
                            "generate_audio_models": [
                                "dreamina-seedance-2-0-260128",
                                "dreamina-seedance-2-0-fast-260128",
                                "seedance-1-5-pro-251215",
                            ],
                            "priority_models": [
                                "dreamina-seedance-2-0-260128",
                                "dreamina-seedance-2-0-fast-260128",
                            ],
                        },
                        "models": {
                            "dreamina-seedance-2-0-260128": {
                                "status": "default",
                                "modes": [
                                    "text-to-video",
                                    "image-to-video",
                                    "first-last-frame",
                                    "reference-to-video",
                                ],
                            },
                            "dreamina-seedance-2-0-fast-260128": {
                                "modes": [
                                    "text-to-video",
                                    "image-to-video",
                                    "first-last-frame",
                                    "reference-to-video",
                                ],
                                "unsupported_resolution": "1080p",
                            },
                            "seedance-1-5-pro-251215": {
                                "status": "supported",
                                "modes": ["text-to-video", "image-to-video", "first-last-frame"],
                            },
                            "seedance-1-0-pro-250528": {
                                "status": "supported",
                                "modes": ["text-to-video", "image-to-video", "first-last-frame"],
                                "unsupported_features": ["generate_audio", "priority"],
                            },
                            "seedance-1-0-pro-fast-251015": {
                                "status": "supported",
                                "modes": ["text-to-video", "image-to-video"],
                                "unsupported_features": [
                                    "first-last-frame",
                                    "generate_audio",
                                    "priority",
                                ],
                            },
                        },
                        "safety": {
                            "url_retention": "Official docs state video URLs expire in 24h.",
                            "status_values": [
                                "queued",
                                "running",
                                "cancelled",
                                "succeeded",
                                "failed",
                                "expired",
                            ],
                        },
                        "unsupported_provider_features": [
                            "draft-task input",
                            "provider asset-id inputs",
                            "eu-west-1 Seedance video region until official docs publish it",
                            "callback URL registration",
                        ],
                        "docs": [
                            "https://docs.byteplus.com/en/docs/ModelArk/1520757",
                            "https://docs.byteplus.com/en/docs/ModelArk/1521309",
                        ],
                    },
                    "docs": [
                        "https://docs.byteplus.com/en/docs/ModelArk/1520757",
                        "https://docs.byteplus.com/en/docs/ModelArk/1521309",
                    ],
                },
            ),
            ActionManifest(
                key="kling.video.generate",
                name="Generate Kling Video",
                description=(
                    "Generate and persist Kling videos from text, one input image, "
                    "or first and last frame images."
                ),
                provider="kling",
                capability="video-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 2500},
                        "negative_prompt": {"type": "string", "maxLength": 2500},
                        "mode": {
                            "type": "string",
                            "enum": ["text-to-video", "image-to-video", "first-last-frame"],
                        },
                        "model_name": {
                            "type": "string",
                            "enum": ["kling-v3"],
                        },
                        "quality_mode": {"type": "string", "enum": ["std", "pro", "4k"]},
                        "duration": {"type": "integer", "minimum": 3, "maximum": 15},
                        "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
                        "sound": {"type": "string", "enum": ["on", "off"]},
                        "cfg_scale": {"type": "number", "minimum": 0, "maximum": 1},
                        "input_image_ref": {
                            "type": "string",
                            "description": (
                                "Generated-assets PNG/JPEG ref for image-to-video or "
                                "the first frame in first-last-frame mode."
                            ),
                        },
                        "image_tail_ref": {
                            "type": "string",
                            "description": ("Generated-assets PNG/JPEG ref for the last frame."),
                        },
                        "watermark_enabled": {"type": "boolean"},
                        "external_task_id": {"type": "string"},
                        "poll_interval_seconds": {"type": "number", "minimum": 1, "maximum": 60},
                        "poll_timeout_seconds": {"type": "number", "minimum": 60, "maximum": 3600},
                    },
                },
                output_schema=_VIDEO_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "kling-video",
                    "operation": "video.generate",
                    "requires_credential": True,
                    "budget_kind": "kling",
                    "enforce_budget": True,
                    "default_model": "kling-v3",
                    "capability_metadata": {
                        "modalities": {"input": ["text", "image"], "output": ["video"]},
                        "modes": ["text-to-video", "image-to-video", "first-last-frame"],
                        "execution": {
                            "mode": "async",
                            "provider_endpoint": "/v1/videos/text2video or /v1/videos/image2video",
                            "poll_endpoint": "/v1/videos/{endpoint}/{task_id}",
                            "persistence": (
                                "Kling-generated media URLs are downloaded immediately into "
                                "generated assets and registered as generic video artifacts."
                            ),
                        },
                        "limits": {
                            "duration_seconds": [3, 15],
                            "quality_modes": ["std", "pro", "4k"],
                            "text_aspect_ratios": ["16:9", "9:16", "1:1"],
                            "input_image_formats": ["jpg", "jpeg", "png"],
                            "max_input_image_bytes": 10485760,
                            "local_image_encoding": "raw base64 without data URI prefix",
                        },
                        "models": {
                            "kling-v3": {"status": "StackOS v1 default executable model"},
                        },
                        "safety": {
                            "status_values": ["submitted", "processing", "succeed", "failed"],
                            "retention": "Generated videos are cleared after 30 days.",
                        },
                        "unsupported_provider_features": [
                            "multi-shot storyboard mode",
                            "reference-to-video endpoint",
                            "kling-v3-omni model until Omni-specific schemas are added",
                            "kling-video-o1 model until O1-specific schemas are added",
                            "older Kling v1/v2 model ids with narrower duration/quality maps",
                            "camera_control",
                            "motion brush",
                            "multi-elements-to-video",
                            "video extension",
                            "callback_url until StackOS owns a configured ingress route",
                            "lip sync",
                            "avatar",
                            "voice_list and element_list controls",
                        ],
                        "docs": [
                            "https://kling.ai/document-api/apiReference%2FcommonInfo",
                            "https://kling.ai/document-api/apiReference%2Fmodel%2FtextToVideo",
                            "https://kling.ai/document-api/apiReference%2Fmodel%2FimageToVideo",
                        ],
                    },
                    "docs": [
                        "https://kling.ai/document-api/apiReference%2FcommonInfo",
                        "https://kling.ai/document-api/apiReference%2Fmodel%2FtextToVideo",
                        "https://kling.ai/document-api/apiReference%2Fmodel%2FimageToVideo",
                    ],
                },
            ),
            ActionManifest(
                key="reve.image.generate",
                name="Generate Reve Image",
                description="Create and persist a Reve image from a text prompt.",
                provider="reve",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 2560},
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "1:1", "auto"],
                        },
                        "version": {
                            "type": "string",
                            "enum": ["latest", "reve-create@20250915"],
                        },
                        "test_time_scaling": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 15,
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "reve",
                    "operation": "image.create",
                    "requires_credential": True,
                    "budget_kind": "reve",
                    "enforce_budget": True,
                    "default_version": "latest",
                    "capability_metadata": {
                        "modalities": {"input": ["text"], "output": ["image"]},
                        "modes": ["text-to-image"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/image/create",
                            "response_format": "application/json base64 PNG",
                            "persistence": (
                                "Reve JSON image output is decoded, written to generated "
                                "assets, and registered as a generic image artifact during "
                                "repository-backed execution."
                            ),
                        },
                        "limits": {
                            "prompt_max_chars": 2560,
                            "aspect_ratios": [
                                "16:9",
                                "9:16",
                                "3:2",
                                "2:3",
                                "4:3",
                                "3:4",
                                "1:1",
                                "auto",
                            ],
                            "test_time_scaling": [1, 15],
                        },
                        "models": {
                            "latest": {"status": "Reve default create version"},
                            "reve-create@20250915": {"status": "Pinned create version"},
                        },
                        "pricing": {
                            "base_credits": 18,
                            "credit_pack": "$10 = 7,500 credits",
                            "test_time_scaling": "multiplies base credit cost",
                        },
                        "safety": {
                            "content_violation": (
                                "Responses include content_violation and may return an "
                                "empty image when content policy rejects output."
                            )
                        },
                        "unsupported_provider_features": [
                            "postprocessing upscale/remove_background/fit_image/effect",
                            "binary image Accept response modes",
                            "create fast is listed as coming soon",
                        ],
                        "docs": [
                            "https://api.reve.com/console/docs",
                            "https://api.reve.com/console/docs/create",
                            "https://api.reve.com/console/pricing",
                        ],
                    },
                    "docs": [
                        "https://api.reve.com/console/docs/create",
                        "https://api.reve.com/console/pricing",
                    ],
                },
            ),
            ActionManifest(
                key="reve.image.edit",
                name="Edit Reve Image",
                description="Edit one generated-assets image through Reve.",
                provider="reve",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["edit_instruction", "input_image_ref"],
                    "properties": {
                        "edit_instruction": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 2560,
                        },
                        "input_image_ref": {
                            "type": "string",
                            "description": "Generated-assets image ref sent as base64 JSON.",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "1:1", "auto"],
                        },
                        "version": {
                            "type": "string",
                            "enum": [
                                "latest",
                                "latest-fast",
                                "reve-edit@20250915",
                                "reve-edit-fast@20251030",
                            ],
                        },
                        "test_time_scaling": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 15,
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "reve",
                    "operation": "image.edit",
                    "requires_credential": True,
                    "budget_kind": "reve",
                    "enforce_budget": True,
                    "default_version": "latest",
                    "capability_metadata": {
                        "modalities": {"input": ["text", "image"], "output": ["image"]},
                        "modes": ["image-to-image", "instructional-edit"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/image/edit",
                            "response_format": "application/json base64 PNG",
                            "persistence": (
                                "Reve JSON image output is decoded and persisted before "
                                "returning artifact refs to agents."
                            ),
                        },
                        "limits": {
                            "prompt_max_chars": 2560,
                            "max_input_images": 1,
                            "max_input_image_bytes": 10485760,
                            "input_image_formats": ["webp", "jpeg", "png", "gif", "tiff"],
                            "input_image_bytes_source": "StackOS preflight safety cap",
                            "input_image_formats_source": "StackOS preflight-supported formats",
                            "aspect_ratios": [
                                "16:9",
                                "9:16",
                                "3:2",
                                "2:3",
                                "4:3",
                                "3:4",
                                "1:1",
                                "auto",
                            ],
                            "test_time_scaling": [1, 15],
                        },
                        "models": {
                            "latest": {"status": "Reve default edit version"},
                            "latest-fast": {"status": "Fast edit alias"},
                            "reve-edit@20250915": {"status": "Pinned edit version"},
                            "reve-edit-fast@20251030": {"status": "Pinned fast edit version"},
                        },
                        "pricing": {
                            "base_credits": {"standard": 30, "fast": 5},
                            "credit_pack": "$10 = 7,500 credits",
                            "test_time_scaling": "multiplies base credit cost",
                        },
                        "safety": {
                            "content_violation": "Responses include content_violation metadata."
                        },
                        "unsupported_provider_features": [
                            "postprocessing upscale/remove_background/fit_image/effect",
                            "binary image Accept response modes",
                            "mask/inpainting controls",
                            "multiple edit input images",
                        ],
                        "docs": [
                            "https://api.reve.com/console/docs",
                            "https://api.reve.com/console/docs/edit",
                            "https://api.reve.com/console/pricing",
                        ],
                    },
                    "docs": [
                        "https://api.reve.com/console/docs/edit",
                        "https://api.reve.com/console/pricing",
                    ],
                },
            ),
            ActionManifest(
                key="reve.image.remix",
                name="Remix Reve Image",
                description="Create a Reve image from a prompt and one to six reference images.",
                provider="reve",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt", "input_image_refs"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 2560},
                        "input_image_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 6,
                            "description": "Generated-assets refs sent as base64 JSON.",
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": ["16:9", "9:16", "3:2", "2:3", "4:3", "3:4", "1:1", "auto"],
                        },
                        "version": {
                            "type": "string",
                            "enum": [
                                "latest",
                                "latest-fast",
                                "reve-remix@20250915",
                                "reve-remix-fast@20251030",
                            ],
                        },
                        "test_time_scaling": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 15,
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "reve",
                    "operation": "image.remix",
                    "requires_credential": True,
                    "budget_kind": "reve",
                    "enforce_budget": True,
                    "default_version": "latest",
                    "capability_metadata": {
                        "modalities": {"input": ["text", "image"], "output": ["image"]},
                        "modes": ["reference-to-image", "multi-image-remix"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/image/remix",
                            "response_format": "application/json base64 PNG",
                            "persistence": (
                                "Reve JSON image output is decoded and persisted before "
                                "returning artifact refs to agents."
                            ),
                        },
                        "limits": {
                            "prompt_max_chars": 2560,
                            "max_input_images": 6,
                            "max_input_image_bytes": 10485760,
                            "max_total_input_pixels": 32000000,
                            "input_image_formats": ["webp", "jpeg", "png", "gif", "tiff"],
                            "input_image_bytes_source": "StackOS preflight safety cap",
                            "input_image_formats_source": "StackOS preflight-supported formats",
                            "max_total_input_pixels_source": "Reve remix documentation",
                            "aspect_ratios": [
                                "16:9",
                                "9:16",
                                "3:2",
                                "2:3",
                                "4:3",
                                "3:4",
                                "1:1",
                                "auto",
                            ],
                            "test_time_scaling": [1, 15],
                        },
                        "models": {
                            "latest": {"status": "Reve default remix version"},
                            "latest-fast": {"status": "Fast remix alias"},
                            "reve-remix@20250915": {"status": "Pinned remix version"},
                            "reve-remix-fast@20251030": {"status": "Pinned fast remix version"},
                        },
                        "pricing": {
                            "base_credits": {"standard": 30, "fast": 5},
                            "credit_pack": "$10 = 7,500 credits",
                            "test_time_scaling": "multiplies base credit cost",
                        },
                        "safety": {
                            "content_violation": "Responses include content_violation metadata."
                        },
                        "unsupported_provider_features": [
                            "postprocessing upscale/remove_background/fit_image/effect",
                            "binary image Accept response modes",
                            "more than six reference images",
                        ],
                        "docs": [
                            "https://api.reve.com/console/docs",
                            "https://api.reve.com/console/docs/remix",
                            "https://api.reve.com/console/pricing",
                        ],
                    },
                    "docs": [
                        "https://api.reve.com/console/docs/remix",
                        "https://api.reve.com/console/pricing",
                    ],
                },
            ),
            ActionManifest(
                key="google.image.generate",
                name="Generate Google Gemini Image",
                description=(
                    "Generate and persist image artifacts through Google's Gemini Nano "
                    "Banana image models."
                ),
                provider="google-gemini-image",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1},
                        "model": {
                            "type": "string",
                            "enum": [
                                "gemini-3.1-flash-image",
                                "gemini-3-pro-image",
                                "gemini-2.5-flash-image",
                            ],
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": [
                                "1:1",
                                "1:4",
                                "1:8",
                                "2:3",
                                "3:2",
                                "3:4",
                                "4:1",
                                "4:3",
                                "4:5",
                                "5:4",
                                "8:1",
                                "9:16",
                                "16:9",
                                "21:9",
                            ],
                        },
                        "image_size": {
                            "type": "string",
                            "enum": ["512", "1K", "2K", "4K"],
                            "description": (
                                "Gemini 3 image size. 512 is valid only for "
                                "gemini-3.1-flash-image; image_size is not valid "
                                "for gemini-2.5-flash-image."
                            ),
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "google-gemini-image",
                    "operation": "image.generate",
                    "requires_credential": True,
                    "budget_kind": "google-gemini-image",
                    "enforce_budget": True,
                    "default_model": "gemini-3.1-flash-image",
                    "capability_metadata": {
                        "modalities": {"input": ["text"], "output": ["image"]},
                        "modes": ["text-to-image"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/models/{model}:generateContent",
                            "persistence": (
                                "Gemini inline base64 image parts are written to generated "
                                "assets and registered as generic image artifacts."
                            ),
                        },
                        "limits": {
                            "inline_request_max_bytes": 20_000_000,
                            "default_aspect_ratio": "1:1",
                        },
                        "models": {
                            "gemini-3.1-flash-image": {
                                "label": "Nano Banana 2",
                                "aspect_ratios": [
                                    "1:1",
                                    "1:4",
                                    "1:8",
                                    "2:3",
                                    "3:2",
                                    "3:4",
                                    "4:1",
                                    "4:3",
                                    "4:5",
                                    "5:4",
                                    "8:1",
                                    "9:16",
                                    "16:9",
                                    "21:9",
                                ],
                                "image_sizes": ["512", "1K", "2K", "4K"],
                                "pricing_usd_per_output": {
                                    "512": 0.045,
                                    "1K": 0.067,
                                    "2K": 0.101,
                                    "4K": 0.151,
                                },
                            },
                            "gemini-3-pro-image": {
                                "label": "Nano Banana Pro",
                                "aspect_ratios": [
                                    "1:1",
                                    "2:3",
                                    "3:2",
                                    "3:4",
                                    "4:3",
                                    "4:5",
                                    "5:4",
                                    "9:16",
                                    "16:9",
                                    "21:9",
                                ],
                                "image_sizes": ["1K", "2K", "4K"],
                                "pricing_usd_per_output": {
                                    "1K": 0.134,
                                    "2K": 0.134,
                                    "4K": 0.24,
                                },
                            },
                            "gemini-2.5-flash-image": {
                                "label": "Nano Banana",
                                "aspect_ratios": [
                                    "1:1",
                                    "2:3",
                                    "3:2",
                                    "3:4",
                                    "4:3",
                                    "4:5",
                                    "5:4",
                                    "9:16",
                                    "16:9",
                                    "21:9",
                                ],
                                "image_sizes": ["fixed 1024-class output"],
                                "pricing_usd_per_output": 0.039,
                            },
                        },
                        "pricing": {
                            "pre_call_estimate": (
                                "Output image price only, plus Gemini 3 Pro Image "
                                "documented input-image equivalent where applicable. "
                                "Text/image input token charges are provider-invoiced "
                                "and not pre-estimated by StackOS."
                            ),
                            "docs": ["https://ai.google.dev/gemini-api/docs/pricing"],
                        },
                        "safety": {
                            "watermark": "Generated images include Google's SynthID watermark.",
                            "policy": "Google Gemini API image generation policies apply.",
                        },
                        "unsupported_provider_features": [
                            "conversational multi-turn chat state",
                            "Google Search grounding tools",
                            "video input for image generation",
                            "Files API image input",
                            "output compression controls",
                            "output MIME type controls",
                            "person_generation control",
                        ],
                        "docs": [
                            "https://ai.google.dev/gemini-api/docs/image-generation",
                            "https://ai.google.dev/api/generate-content",
                            "https://ai.google.dev/gemini-api/docs/pricing",
                        ],
                    },
                },
            ),
            ActionManifest(
                key="google.image.edit",
                name="Edit Google Gemini Image",
                description=(
                    "Generate an image from text plus one or more generated-assets "
                    "reference images through Gemini Nano Banana image models."
                ),
                provider="google-gemini-image",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt", "input_image_refs"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1},
                        "input_image_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 14,
                            "description": (
                                "Generated-assets refs for JPEG, PNG, or WEBP images. "
                                "Inline request payload must remain under 20 MB."
                            ),
                        },
                        "model": {
                            "type": "string",
                            "enum": [
                                "gemini-3.1-flash-image",
                                "gemini-3-pro-image",
                                "gemini-2.5-flash-image",
                            ],
                        },
                        "aspect_ratio": {
                            "type": "string",
                            "enum": [
                                "1:1",
                                "1:4",
                                "1:8",
                                "2:3",
                                "3:2",
                                "3:4",
                                "4:1",
                                "4:3",
                                "4:5",
                                "5:4",
                                "8:1",
                                "9:16",
                                "16:9",
                                "21:9",
                            ],
                        },
                        "image_size": {
                            "type": "string",
                            "enum": ["512", "1K", "2K", "4K"],
                            "description": (
                                "Gemini 3 image size. 512 is valid only for "
                                "gemini-3.1-flash-image; image_size is not valid "
                                "for gemini-2.5-flash-image."
                            ),
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "google-gemini-image",
                    "operation": "image.edit",
                    "requires_credential": True,
                    "budget_kind": "google-gemini-image",
                    "enforce_budget": True,
                    "default_model": "gemini-3.1-flash-image",
                    "agent_guidance": (
                        "Use this action when the output must preserve or combine "
                        "existing visual references. Keep references as generated-assets "
                        "refs; StackOS sends them inline and does not expose secrets."
                    ),
                    "capability_metadata": {
                        "modalities": {"input": ["text", "image"], "output": ["image"]},
                        "modes": [
                            "image-to-image",
                            "multi-image-reference",
                            "style-transfer",
                            "object-preserving-edit",
                            "character-consistency",
                        ],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/models/{model}:generateContent",
                            "persistence": (
                                "Gemini inline base64 image parts are written to generated "
                                "assets and registered as generic image artifacts."
                            ),
                        },
                        "limits": {
                            "inline_request_max_bytes": 20_000_000,
                            "input_image_formats": ["jpg", "jpeg", "png", "webp"],
                            "max_input_images_by_model": {
                                "gemini-3.1-flash-image": 14,
                                "gemini-3-pro-image": 14,
                                "gemini-2.5-flash-image": 3,
                            },
                            "default_output_shape": (
                                "Provider defaults to matching the input image size or "
                                "generating 1:1 when no input size applies."
                            ),
                        },
                        "models": {
                            "gemini-3.1-flash-image": {
                                "label": "Nano Banana 2",
                                "max_input_images": 14,
                                "reference_guidance": (
                                    "Up to 10 object references plus up to 4 character "
                                    "references in one workflow."
                                ),
                                "aspect_ratios": [
                                    "1:1",
                                    "1:4",
                                    "1:8",
                                    "2:3",
                                    "3:2",
                                    "3:4",
                                    "4:1",
                                    "4:3",
                                    "4:5",
                                    "5:4",
                                    "8:1",
                                    "9:16",
                                    "16:9",
                                    "21:9",
                                ],
                                "image_sizes": ["512", "1K", "2K", "4K"],
                                "pricing_usd_per_output": {
                                    "512": 0.045,
                                    "1K": 0.067,
                                    "2K": 0.101,
                                    "4K": 0.151,
                                },
                            },
                            "gemini-3-pro-image": {
                                "label": "Nano Banana Pro",
                                "max_input_images": 14,
                                "reference_guidance": (
                                    "Up to 5 high-fidelity character references and up to "
                                    "14 images total."
                                ),
                                "aspect_ratios": [
                                    "1:1",
                                    "2:3",
                                    "3:2",
                                    "3:4",
                                    "4:3",
                                    "4:5",
                                    "5:4",
                                    "9:16",
                                    "16:9",
                                    "21:9",
                                ],
                                "image_sizes": ["1K", "2K", "4K"],
                                "pricing_usd_per_input_image": 0.0011,
                                "pricing_usd_per_output": {
                                    "1K": 0.134,
                                    "2K": 0.134,
                                    "4K": 0.24,
                                },
                            },
                            "gemini-2.5-flash-image": {
                                "label": "Nano Banana",
                                "max_input_images": 3,
                                "aspect_ratios": [
                                    "1:1",
                                    "2:3",
                                    "3:2",
                                    "3:4",
                                    "4:3",
                                    "4:5",
                                    "5:4",
                                    "9:16",
                                    "16:9",
                                    "21:9",
                                ],
                                "image_sizes": ["fixed 1024-class output"],
                                "pricing_usd_per_output": 0.039,
                            },
                        },
                        "pricing": {
                            "pre_call_estimate": (
                                "Output image price only, plus Gemini 3 Pro Image "
                                "documented input-image equivalent where applicable. "
                                "Text/image input token charges are provider-invoiced "
                                "and not pre-estimated by StackOS."
                            ),
                            "docs": ["https://ai.google.dev/gemini-api/docs/pricing"],
                        },
                        "safety": {
                            "watermark": "Generated images include Google's SynthID watermark.",
                            "policy": "Google Gemini API image generation policies apply.",
                        },
                        "unsupported_provider_features": [
                            "conversational multi-turn chat state",
                            "Google Search grounding tools",
                            "video input for image generation",
                            "Files API image input",
                            "output compression controls",
                            "output MIME type controls",
                            "person_generation control",
                        ],
                        "docs": [
                            "https://ai.google.dev/gemini-api/docs/image-generation",
                            "https://ai.google.dev/gemini-api/docs/image-understanding",
                            "https://ai.google.dev/api/generate-content",
                            "https://ai.google.dev/gemini-api/docs/pricing",
                        ],
                    },
                },
            ),
            ActionManifest(
                key="ideogram.image.generate",
                name="Generate Ideogram Image",
                description=("Generate and persist image artifacts through Ideogram 4.0."),
                provider="ideogram",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text_prompt"],
                    "properties": {
                        "text_prompt": {"type": "string", "minLength": 1},
                        "resolution": {
                            "type": "string",
                            "enum": [
                                "2048x2048",
                                "1440x2880",
                                "2880x1440",
                                "1664x2496",
                                "2496x1664",
                                "1792x2240",
                                "2240x1792",
                                "1440x2560",
                                "2560x1440",
                                "1600x2560",
                                "2560x1600",
                                "1728x2304",
                                "2304x1728",
                                "1296x3168",
                                "3168x1296",
                                "1152x2944",
                                "2944x1152",
                                "1248x3328",
                                "3328x1248",
                                "1280x3072",
                                "3072x1280",
                                "1024x3072",
                                "3072x1024",
                            ],
                        },
                        "rendering_speed": {
                            "type": "string",
                            "enum": ["TURBO", "DEFAULT", "QUALITY"],
                            "description": (
                                "Ideogram 4.0 speed tier. FLASH is documented as "
                                "coming soon and currently returns 400, so it is not "
                                "exposed."
                            ),
                        },
                        "enable_copyright_detection": {"type": "boolean"},
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "ideogram",
                    "operation": "image.generate",
                    "requires_credential": True,
                    "budget_kind": "ideogram",
                    "enforce_budget": True,
                    "default_model": "ideogram-v4",
                    "capability_metadata": {
                        "modalities": {"input": ["text"], "output": ["image"]},
                        "modes": ["text-to-image"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/ideogram-v4/generate",
                            "persistence": (
                                "Ideogram temporary image URLs are downloaded "
                                "immediately, written to generated assets, and "
                                "registered as generic image artifacts."
                            ),
                        },
                        "limits": {
                            "temporary_provider_urls": "download-and-strip",
                            "resolution_count": 23,
                        },
                        "models": {
                            "ideogram-v4": {
                                "label": "Ideogram 4.0",
                                "resolutions": [
                                    "2048x2048",
                                    "1440x2880",
                                    "2880x1440",
                                    "1664x2496",
                                    "2496x1664",
                                    "1792x2240",
                                    "2240x1792",
                                    "1440x2560",
                                    "2560x1440",
                                    "1600x2560",
                                    "2560x1600",
                                    "1728x2304",
                                    "2304x1728",
                                    "1296x3168",
                                    "3168x1296",
                                    "1152x2944",
                                    "2944x1152",
                                    "1248x3328",
                                    "3328x1248",
                                    "1280x3072",
                                    "3072x1280",
                                    "1024x3072",
                                    "3072x1024",
                                ],
                                "rendering_speeds": ["TURBO", "DEFAULT", "QUALITY"],
                                "pricing_usd_per_output": {
                                    "TURBO": 0.03,
                                    "DEFAULT": 0.06,
                                    "QUALITY": 0.10,
                                },
                            }
                        },
                        "unsupported_provider_features": [
                            "rendering_speed FLASH",
                            "structured json_prompt",
                            "magic-prompt helper endpoint",
                            "describe endpoint",
                            "legacy edit",
                            "Ideogram 3.0 inpaint/reframe/replace-background",
                            "remove background",
                            "upscale",
                        ],
                        "docs": [
                            "https://developer.ideogram.ai/api-reference/api-reference/generate-v4",
                            "https://ideogram.ai/api-pricing/",
                        ],
                    },
                },
            ),
            ActionManifest(
                key="ideogram.image.remix",
                name="Remix Ideogram Image",
                description=(
                    "Generate an Ideogram 4.0 image from a prompt and one "
                    "generated-assets reference image."
                ),
                provider="ideogram",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text_prompt", "input_image_ref"],
                    "properties": {
                        "text_prompt": {"type": "string", "minLength": 1},
                        "input_image_ref": {
                            "type": "string",
                            "description": (
                                "Generated-assets ref for a JPEG, PNG, or WEBP image at most 10 MB."
                            ),
                        },
                        "image_weight": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                        },
                        "resolution": {
                            "type": "string",
                            "enum": [
                                "2048x2048",
                                "1440x2880",
                                "2880x1440",
                                "1664x2496",
                                "2496x1664",
                                "1792x2240",
                                "2240x1792",
                                "1440x2560",
                                "2560x1440",
                                "1600x2560",
                                "2560x1600",
                                "1728x2304",
                                "2304x1728",
                                "1296x3168",
                                "3168x1296",
                                "1152x2944",
                                "2944x1152",
                                "1248x3328",
                                "3328x1248",
                                "1280x3072",
                                "3072x1280",
                                "1024x3072",
                                "3072x1024",
                            ],
                        },
                        "rendering_speed": {
                            "type": "string",
                            "enum": ["TURBO", "DEFAULT", "QUALITY"],
                            "description": (
                                "Ideogram 4.0 speed tier. FLASH is documented as "
                                "coming soon and currently returns 400, so it is not "
                                "exposed."
                            ),
                        },
                        "enable_copyright_detection": {"type": "boolean"},
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "ideogram",
                    "operation": "image.remix",
                    "requires_credential": True,
                    "budget_kind": "ideogram",
                    "enforce_budget": True,
                    "default_model": "ideogram-v4",
                    "agent_guidance": (
                        "Use this action when an Ideogram 4.0 output should follow "
                        "one existing generated-assets image. Keep the provider's "
                        "temporary URL out of prompts and logs."
                    ),
                    "capability_metadata": {
                        "modalities": {"input": ["text", "image"], "output": ["image"]},
                        "modes": ["image-remix", "image-to-image"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/v1/ideogram-v4/remix",
                            "persistence": (
                                "Ideogram temporary image URLs are downloaded "
                                "immediately, written to generated assets, and "
                                "registered as generic image artifacts."
                            ),
                        },
                        "limits": {
                            "max_input_images": 1,
                            "max_input_image_bytes": 10_000_000,
                            "input_image_formats": ["jpg", "jpeg", "png", "webp"],
                            "image_weight": {"minimum": 1, "maximum": 100},
                            "temporary_provider_urls": "download-and-strip",
                        },
                        "models": {
                            "ideogram-v4": {
                                "label": "Ideogram 4.0",
                                "resolutions": [
                                    "2048x2048",
                                    "1440x2880",
                                    "2880x1440",
                                    "1664x2496",
                                    "2496x1664",
                                    "1792x2240",
                                    "2240x1792",
                                    "1440x2560",
                                    "2560x1440",
                                    "1600x2560",
                                    "2560x1600",
                                    "1728x2304",
                                    "2304x1728",
                                    "1296x3168",
                                    "3168x1296",
                                    "1152x2944",
                                    "2944x1152",
                                    "1248x3328",
                                    "3328x1248",
                                    "1280x3072",
                                    "3072x1280",
                                    "1024x3072",
                                    "3072x1024",
                                ],
                                "rendering_speeds": ["TURBO", "DEFAULT", "QUALITY"],
                                "pricing_usd_per_output": {
                                    "TURBO": 0.03,
                                    "DEFAULT": 0.06,
                                    "QUALITY": 0.10,
                                },
                            }
                        },
                        "unsupported_provider_features": [
                            "rendering_speed FLASH",
                            "structured json_prompt",
                            "magic-prompt helper endpoint",
                            "describe endpoint",
                            "legacy edit",
                            "Ideogram 3.0 inpaint/reframe/replace-background",
                            "remove background",
                            "upscale",
                        ],
                        "docs": [
                            "https://developer.ideogram.ai/api-reference/api-reference/remix-v4",
                            "https://ideogram.ai/api-pricing/",
                        ],
                    },
                },
            ),
            ActionManifest(
                key="byteplus.image.generate",
                name="Generate BytePlus Seedream Image",
                description="Generate and persist image artifacts through BytePlus Seedream.",
                provider="byteplus-ark",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1},
                        "model": {
                            "type": "string",
                            "enum": [
                                "seedream-5-0-lite-260128",
                                "seedream-4-5-251128",
                                "seedream-4-0-250828",
                            ],
                        },
                        "size": {
                            "type": "string",
                            "description": (
                                "Model-supported shortcut or custom WxH satisfying "
                                "BytePlus Seedream pixel/aspect limits."
                            ),
                        },
                        "region": {
                            "type": "string",
                            "enum": ["ap-southeast-1", "eu-west-1"],
                        },
                        "sequential_image_generation": {
                            "type": "string",
                            "enum": ["disabled", "auto"],
                        },
                        "max_images": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 15,
                        },
                        "watermark": {"type": "boolean"},
                        "output_format": {
                            "type": "string",
                            "enum": ["jpeg", "png"],
                            "description": ("Only supported by seedream-5-0-lite-260128."),
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "byteplus-seedream",
                    "operation": "image.generate",
                    "requires_credential": True,
                    "budget_kind": "byteplus-ark",
                    "enforce_budget": True,
                    "default_model": "seedream-5-0-lite-260128",
                    "agent_guidance": (
                        "Use this action for BytePlus Seedream text-to-image. "
                        "Generated provider URLs expire after 24 hours and are "
                        "persisted immediately."
                    ),
                    "capability_metadata": {
                        "modalities": {"input": ["text"], "output": ["image"]},
                        "modes": ["text-to-image", "optional-batch-generation"],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/api/v3/images/generations",
                            "base_urls": {
                                "ap-southeast-1": (
                                    "https://ark.ap-southeast.bytepluses.com/api/v3"
                                ),
                                "eu-west-1": "https://ark.eu-west.bytepluses.com/api/v3",
                            },
                            "persistence": (
                                "BytePlus temporary image URLs are downloaded "
                                "immediately, written to generated assets, and "
                                "registered as generic image artifacts."
                            ),
                        },
                        "limits": {
                            "size_keywords_by_model": {
                                "seedream-5-0-lite-260128": ["2K", "3K", "4K"],
                                "seedream-4-5-251128": ["2K", "4K"],
                                "seedream-4-0-250828": ["1K", "2K", "4K"],
                            },
                            "custom_size_min_pixels_by_model": {
                                "seedream-5-0-lite-260128": 3_686_400,
                                "seedream-4-5-251128": 3_686_400,
                                "seedream-4-0-250828": 921_600,
                            },
                            "custom_size_max_pixels": 16_777_216,
                            "custom_size_aspect_ratio": {
                                "minimum": 0.0625,
                                "maximum": 16,
                            },
                            "sequential_image_generation": ["disabled", "auto"],
                            "max_generated_plus_reference_images": 15,
                            "temporary_provider_urls": "download-and-strip",
                        },
                        "models": {
                            "seedream-5-0-lite-260128": {
                                "label": "Seedream 5.0 Lite",
                                "regions": ["ap-southeast-1", "eu-west-1"],
                                "pricing_usd_per_successful_output": 0.035,
                                "output_formats": ["jpeg", "png"],
                            },
                            "seedream-4-5-251128": {
                                "label": "Seedream 4.5",
                                "regions": ["ap-southeast-1"],
                                "pricing_usd_per_successful_output": 0.04,
                            },
                            "seedream-4-0-250828": {
                                "label": "Seedream 4.0",
                                "regions": ["ap-southeast-1"],
                                "pricing_usd_per_successful_output": 0.03,
                            },
                        },
                        "unsupported_provider_features": [
                            "streaming output",
                            "non-lite seedream-5-0-260128 until pricing is modeled",
                            "seededit-3-0-i2i specialized controls",
                            "prompt optimization controls",
                            "input image formats beyond JPEG/PNG/WEBP",
                            "external image URLs as inputs",
                        ],
                        "docs": [
                            "https://docs.byteplus.com/en/docs/ModelArk/1541523",
                            "https://docs.byteplus.com/en/docs/ModelArk/1330310",
                            "https://docs.byteplus.com/en/docs/ModelArk/1544106",
                        ],
                    },
                },
            ),
            ActionManifest(
                key="byteplus.image.edit",
                name="Edit BytePlus Seedream Image",
                description=(
                    "Generate a BytePlus Seedream image from a prompt and one or more "
                    "generated-assets reference images."
                ),
                provider="byteplus-ark",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt", "input_image_refs"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1},
                        "input_image_refs": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 14,
                            "items": {"type": "string"},
                        },
                        "model": {
                            "type": "string",
                            "enum": [
                                "seedream-5-0-lite-260128",
                                "seedream-4-5-251128",
                                "seedream-4-0-250828",
                            ],
                        },
                        "size": {
                            "type": "string",
                            "description": (
                                "Model-supported shortcut or custom WxH satisfying "
                                "BytePlus Seedream pixel/aspect limits."
                            ),
                        },
                        "region": {
                            "type": "string",
                            "enum": ["ap-southeast-1", "eu-west-1"],
                        },
                        "sequential_image_generation": {
                            "type": "string",
                            "enum": ["disabled", "auto"],
                        },
                        "max_images": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 15,
                        },
                        "watermark": {"type": "boolean"},
                        "output_format": {
                            "type": "string",
                            "enum": ["jpeg", "png"],
                            "description": ("Only supported by seedream-5-0-lite-260128."),
                        },
                    },
                },
                output_schema=_IMAGE_ACTION_OUTPUT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "byteplus-seedream",
                    "operation": "image.edit",
                    "requires_credential": True,
                    "budget_kind": "byteplus-ark",
                    "enforce_budget": True,
                    "default_model": "seedream-5-0-lite-260128",
                    "agent_guidance": (
                        "Use this action for Seedream image-to-image or "
                        "multi-reference generation from generated-assets refs."
                    ),
                    "capability_metadata": {
                        "modalities": {"input": ["text", "image"], "output": ["image"]},
                        "modes": [
                            "image-to-image",
                            "multi-reference-image-generation",
                            "optional-batch-generation",
                        ],
                        "execution": {
                            "mode": "sync",
                            "provider_endpoint": "/api/v3/images/generations",
                            "persistence": (
                                "BytePlus temporary image URLs are downloaded "
                                "immediately, written to generated assets, and "
                                "registered as generic image artifacts."
                            ),
                        },
                        "limits": {
                            "max_input_images": 14,
                            "max_input_image_bytes": 30_000_000,
                            "input_image_formats": ["jpg", "jpeg", "png", "webp"],
                            "provider_supported_extra_input_formats_deferred": [
                                "bmp",
                                "tiff",
                                "gif",
                                "heic",
                                "heif",
                            ],
                            "reference_plus_output_images_max": 15,
                            "min_input_image_side_px": 15,
                            "max_input_image_pixels": 36_000_000,
                            "input_image_aspect_ratio": {
                                "minimum": 0.0625,
                                "maximum": 16,
                            },
                            "size_keywords_by_model": {
                                "seedream-5-0-lite-260128": ["2K", "3K", "4K"],
                                "seedream-4-5-251128": ["2K", "4K"],
                                "seedream-4-0-250828": ["1K", "2K", "4K"],
                            },
                            "custom_size_min_pixels_by_model": {
                                "seedream-5-0-lite-260128": 3_686_400,
                                "seedream-4-5-251128": 3_686_400,
                                "seedream-4-0-250828": 921_600,
                            },
                            "custom_size_max_pixels": 16_777_216,
                            "custom_size_aspect_ratio": {
                                "minimum": 0.0625,
                                "maximum": 16,
                            },
                            "temporary_provider_urls": "download-and-strip",
                        },
                        "models": {
                            "seedream-5-0-lite-260128": {
                                "label": "Seedream 5.0 Lite",
                                "regions": ["ap-southeast-1", "eu-west-1"],
                                "pricing_usd_per_successful_output": 0.035,
                                "output_formats": ["jpeg", "png"],
                            },
                            "seedream-4-5-251128": {
                                "label": "Seedream 4.5",
                                "regions": ["ap-southeast-1"],
                                "pricing_usd_per_successful_output": 0.04,
                            },
                            "seedream-4-0-250828": {
                                "label": "Seedream 4.0",
                                "regions": ["ap-southeast-1"],
                                "pricing_usd_per_successful_output": 0.03,
                            },
                        },
                        "unsupported_provider_features": [
                            "streaming output",
                            "non-lite seedream-5-0-260128 until pricing is modeled",
                            "seededit-3-0-i2i specialized controls",
                            "prompt optimization controls",
                            "input image formats beyond JPEG/PNG/WEBP",
                            "external image URLs as inputs",
                        ],
                        "docs": [
                            "https://docs.byteplus.com/en/docs/ModelArk/1541523",
                            "https://docs.byteplus.com/en/docs/ModelArk/1330310",
                            "https://docs.byteplus.com/en/docs/ModelArk/1544106",
                        ],
                    },
                },
            ),
            ActionManifest(
                key="video.generate",
                name="Generate Video",
                description=(
                    "Deferred provider-neutral video generation contract; execution is "
                    "enabled once a supported vendor backend and connector are selected."
                ),
                provider="video-generation",
                capability="video-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "Single-shot video prompt: prose scene first, then "
                                "labeled cinematography, action, and audio cues."
                            ),
                        },
                        "model": {"type": "string"},
                        "size": {
                            "type": "string",
                            "description": "Target resolution as WxH, e.g. 720x1280 or 1920x1080.",
                        },
                        "seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 60,
                            "description": (
                                "Requested clip duration; vendors clamp to supported steps."
                            ),
                        },
                        "input_reference_ref": {
                            "type": "string",
                            "description": (
                                "Optional generated-assets artifact ref used as the "
                                "first-frame anchor, for example a product photo."
                            ),
                        },
                    },
                },
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "operation": "video.generate",
                    "execution_mode": "deferred-video-backend-selection",
                    "deferred_reason": (
                        "No video vendor backend is wired yet. The OpenAI Sora Videos "
                        "API is deprecated for removal on 2026-09-24, so enable this "
                        "action only after an actively supported backend connector "
                        "lands; the credential, budget, and grant path is already "
                        "prepared under the video-generation provider."
                    ),
                    "budget_kind": "video-generation",
                    "agent_guidance": (
                        "Treat video.generate as plannable but not executable. When a "
                        "run plan needs video and this action is still deferred, surface "
                        "the gap at readiness time and let the operator choose an "
                        "image-only downgrade or a stop."
                    ),
                    "capability_metadata": {
                        "modalities": {
                            "input": ["text", "image"],
                            "output": ["video"],
                        },
                        "modes": ["text-to-video", "image-to-video"],
                        "execution": {
                            "mode": "deferred",
                            "reason": "provider backend not selected",
                            "required_pattern": "async submit/poll/download/persist",
                        },
                        "models": {},
                        "safety": {
                            "watermark": "provider-specific; unresolved until backend selection",
                        },
                        "docs": ["docs/integration-contracts/media-generation.md"],
                    },
                },
            ),
            ActionManifest(
                key="web.scrape",
                name="Scrape Web Page",
                description="Fetch and normalize a web page.",
                provider="firecrawl",
                capability="web-retrieval",
                risk_level="read",
                input_schema=_WEB_SCRAPE_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "firecrawl",
                    "operation": "scrape",
                    "requires_credential": True,
                    "budget_kind": "firecrawl",
                    "enforce_budget": True,
                },
            ),
            ActionManifest(
                key="web.crawl",
                name="Crawl Website",
                description="Start a bounded Firecrawl crawl for an agent-selected site.",
                provider="firecrawl",
                capability="web-retrieval",
                risk_level="cost",
                input_schema=_WEB_CRAWL_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "firecrawl",
                    "operation": "crawl",
                    "requires_credential": True,
                    "budget_kind": "firecrawl",
                    "enforce_budget": True,
                },
            ),
            ActionManifest(
                key="web.map",
                name="Map Website URLs",
                description="Discover URLs from an agent-selected site.",
                provider="firecrawl",
                capability="web-retrieval",
                risk_level="cost",
                input_schema=_WEB_MAP_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "firecrawl",
                    "operation": "map",
                    "requires_credential": True,
                    "budget_kind": "firecrawl",
                    "enforce_budget": True,
                },
            ),
            ActionManifest(
                key="web.extract",
                name="Extract Web Data",
                description=(
                    "Deferred Firecrawl structured extraction contract; execution needs "
                    "async status polling before this action can be enabled."
                ),
                provider="firecrawl",
                capability="web-retrieval",
                risk_level="cost",
                input_schema=_WEB_EXTRACT_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "operation": "extract",
                    "execution_mode": "deferred-firecrawl-async-extract",
                    "deferred_reason": (
                        "Firecrawl extract submits an async job; enable this action only "
                        "after StackOS has an explicit status-poll action and run-plan "
                        "artifact contract."
                    ),
                    "docs": ["https://docs.firecrawl.dev/api-reference/endpoint/extract-post"],
                },
            ),
            ActionManifest(
                key="web.read",
                name="Read Web Page",
                description="Fetch a readable Markdown view of a URL through Jina Reader.",
                provider="jina",
                capability="web-retrieval",
                risk_level="read",
                input_schema=_WEB_READ_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "jina",
                    "operation": "read",
                    "requires_credential": False,
                    "allows_credential": True,
                    "budget_kind": "jina",
                    "enforce_budget": False,
                },
            ),
            ActionManifest(
                key="sitemap.fetch",
                name="Fetch Sitemap",
                description="Fetch and parse public sitemap URLs with sitemap-index recursion.",
                provider=None,
                capability="web-retrieval",
                risk_level="read",
                input_schema=_SITEMAP_FETCH_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "sitemap",
                    "operation": "fetch",
                    "requires_credential": False,
                    "allows_credential": False,
                },
            ),
            ActionManifest(
                key="reddit.search-subreddit",
                name="Search Subreddit",
                description="Search posts in a configured subreddit.",
                provider="reddit",
                capability="community-research",
                risk_level="read",
                input_schema=_REDDIT_SEARCH_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "reddit",
                    "operation": "search_subreddit",
                    "requires_credential": True,
                },
            ),
            ActionManifest(
                key="reddit.top-questions",
                name="Top Reddit Posts",
                description=(
                    "Fetch raw top Reddit posts from a subreddit; the executing agent "
                    "filters question-shaped titles when needed."
                ),
                provider="reddit",
                capability="community-research",
                risk_level="read",
                input_schema=_REDDIT_TOP_QUESTIONS_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "reddit",
                    "operation": "top_questions",
                    "requires_credential": True,
                },
            ),
            ActionManifest(
                key="mock.echo",
                name="Mock Provider Echo",
                description=(
                    "Local executable action for validating StackOS auth, grants, "
                    "redaction, audit, and failure handling without provider accounts."
                ),
                provider="mock-provider",
                capability="integration-testing",
                risk_level="read",
                input_schema=_MOCK_ECHO_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "mock-provider",
                    "operation": "echo",
                    "requires_credential": True,
                    "enforce_budget": False,
                    "scenarios": _MOCK_SCENARIOS,
                    "docs": ["docs/integration-testing.md"],
                },
            ),
        ],
        resources=[
            ResourceManifest(
                key="generated-image",
                name="Generated Image",
                description="Generated image artifact metadata reusable by any workflow.",
                schema_data=_ARTIFACT_RESOURCE_SCHEMA,
            ),
            ResourceManifest(
                key="generated-video",
                name="Generated Video",
                description="Generated video artifact metadata reusable by any workflow.",
                schema_data=_ARTIFACT_RESOURCE_SCHEMA,
            ),
            ResourceManifest(
                key="web-document",
                name="Web Document",
                description="Retrieved or normalized web document metadata.",
                schema_data=_OBJECT_SCHEMA,
            ),
        ],
    ),
)


def _combined_builtin_plugin_manifests() -> tuple[PluginManifest, ...]:
    manifests = {manifest.slug: manifest for manifest in _CODE_PLUGIN_MANIFESTS}
    manifests.update({manifest.slug: manifest for manifest in load_plugin_manifest_files()})
    return tuple(
        sorted(
            manifests.values(),
            key=lambda manifest: plugin_sort_key(
                manifest.slug,
                manifest.model_dump(mode="json", by_alias=True),
            ),
        )
    )


@dataclass(frozen=True)
class BuiltinPluginManifestSnapshot:
    """One internally consistent generation of built-in plugin manifests."""

    generation: str
    manifests: tuple[PluginManifest, ...]


_BUILTIN_PLUGIN_MANIFEST_SNAPSHOT: BuiltinPluginManifestSnapshot | None = None
_BUILTIN_PLUGIN_MANIFEST_LOCK = threading.Lock()


def _clone_plugin_manifest_generation() -> str:
    """Fingerprint editable clone manifests without reparsing them on every catalog read."""
    digest = hashlib.sha256()
    for path in _plugin_manifest_paths():
        stat = path.stat()
        for value in (path.resolve(), stat.st_mtime_ns, stat.st_size):
            digest.update(str(value).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def get_builtin_plugin_manifest_snapshot() -> BuiltinPluginManifestSnapshot:
    """Return current manifests, reloading editable sources when their generation changes.

    Code-defined and wheel-bundled manifests change with a process/package restart. Clone
    ``plugins/*/plugin.yaml`` files are the supported live-edit surface and override bundled
    manifests, so their file generation is the cache invalidation boundary.
    """
    global _BUILTIN_PLUGIN_MANIFEST_SNAPSHOT

    generation = _clone_plugin_manifest_generation()
    cached = _BUILTIN_PLUGIN_MANIFEST_SNAPSHOT
    if cached is not None and cached.generation == generation:
        return cached
    with _BUILTIN_PLUGIN_MANIFEST_LOCK:
        cached = _BUILTIN_PLUGIN_MANIFEST_SNAPSHOT
        if cached is not None and cached.generation == generation:
            return cached
        snapshot = BuiltinPluginManifestSnapshot(
            generation=generation,
            manifests=_combined_builtin_plugin_manifests(),
        )
        _BUILTIN_PLUGIN_MANIFEST_SNAPSHOT = snapshot
        return snapshot


BUILTIN_PLUGIN_MANIFESTS: tuple[PluginManifest, ...] = (
    get_builtin_plugin_manifest_snapshot().manifests
)

__all__ = [
    "BUILTIN_PLUGIN_MANIFESTS",
    "ActionManifest",
    "BuiltinPluginManifestSnapshot",
    "CapabilityManifest",
    "PluginManifest",
    "ProviderManifest",
    "ResourceManifest",
    "get_builtin_plugin_manifest_snapshot",
    "load_plugin_manifest_file",
    "load_plugin_manifest_files",
    "plugin_sort_key",
]
