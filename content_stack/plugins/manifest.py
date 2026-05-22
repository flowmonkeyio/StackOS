"""StackOS plugin manifest schema and built-in manifests."""

from __future__ import annotations

import re
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-.][a-z0-9]+)*$")


def _validate_key(value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must be lowercase kebab/dotted identifier")
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


class ProviderManifest(BaseModel):
    """Provider metadata contributed by a plugin."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    auth_type: str = Field(default="none", max_length=80)
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
    root = resources.files("content_stack").joinpath("_assets").joinpath("plugins")
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
    return tuple(sorted(manifests.values(), key=lambda manifest: manifest.slug))


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

_CODE_PLUGIN_MANIFESTS: tuple[PluginManifest, ...] = (
    PluginManifest(
        slug="core",
        name="StackOS Core",
        description="Domain-neutral project, workflow, run, context, auth, and catalog primitives.",
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
        capabilities=[
            CapabilityManifest(
                key="image-generation",
                name="Image Generation",
                description="Generate image artifacts through configured providers.",
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
        ],
        providers=[
            ProviderManifest(
                key="openai-images",
                name="OpenAI Images",
                description="Image generation provider.",
                auth_type="api-key",
            ),
            ProviderManifest(
                key="firecrawl",
                name="Firecrawl",
                description="Web crawling and scraping provider.",
                auth_type="api-key",
            ),
            ProviderManifest(
                key="jina",
                name="Jina Reader",
                description="Readable web document extraction provider.",
                auth_type="api-key",
            ),
            ProviderManifest(
                key="reddit",
                name="Reddit",
                description="Reddit research provider.",
                auth_type="api-key",
            ),
        ],
        actions=[
            ActionManifest(
                key="image.generate",
                name="Generate Image",
                description="Generate and persist image artifacts.",
                provider="openai-images",
                capability="image-generation",
                risk_level="cost",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string"},
                        "size": {
                            "type": "string",
                            "enum": ["1024x1024", "1536x1024", "1024x1536"],
                        },
                        "quality": {
                            "type": "string",
                            "enum": ["low", "medium", "high", "standard", "hd"],
                        },
                        "n": {"type": "integer"},
                        "model": {"type": "string"},
                        "output_format": {"type": "string", "enum": ["webp", "png", "jpeg"]},
                    },
                },
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "openai-images",
                    "operation": "image.generate",
                    "requires_credential": True,
                    "budget_kind": "openai-images",
                    "enforce_budget": True,
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
                description="Run structured extraction on an agent-selected URL.",
                provider="firecrawl",
                capability="web-retrieval",
                risk_level="cost",
                input_schema=_WEB_EXTRACT_INPUT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
                config={
                    "schema_version": "stackos.action.v1",
                    "connector": "firecrawl",
                    "operation": "extract",
                    "requires_credential": True,
                    "budget_kind": "firecrawl",
                    "enforce_budget": True,
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
                name="Top Reddit Questions",
                description="Fetch top question-shaped Reddit posts from a subreddit.",
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
        ],
        resources=[
            ResourceManifest(
                key="generated-image",
                name="Generated Image",
                description="Generated image artifact metadata reusable by any workflow.",
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
    return tuple(sorted(manifests.values(), key=lambda manifest: manifest.slug))


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
]
