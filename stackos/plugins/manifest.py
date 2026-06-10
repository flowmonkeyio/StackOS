"""StackOS plugin manifest schema and built-in manifests."""

from __future__ import annotations

import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

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
                    )
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
                        "model": {"type": "string", "enum": ["grok-imagine-video"]},
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
                            }
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
                            "grok-imagine-video-1.5-preview image-to-video preview model",
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


BUILTIN_PLUGIN_MANIFESTS: tuple[PluginManifest, ...] = _combined_builtin_plugin_manifests()

__all__ = [
    "BUILTIN_PLUGIN_MANIFESTS",
    "ActionManifest",
    "CapabilityManifest",
    "PluginManifest",
    "ProviderManifest",
    "ResourceManifest",
    "load_plugin_manifest_file",
    "load_plugin_manifest_files",
    "plugin_sort_key",
]
