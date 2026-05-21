"""StackOS plugin manifest schema and built-in manifests."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:[-.][a-z0-9]+)*$")


def _validate_key(value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must be lowercase kebab/dotted identifier")
    return value


class CapabilityManifest(BaseModel):
    """Capability metadata contributed by a plugin."""

    model_config = ConfigDict(extra="forbid")

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
    schema: dict[str, Any] = Field(default_factory=dict)
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

BUILTIN_PLUGIN_MANIFESTS: tuple[PluginManifest, ...] = (
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
                schema=_TEXT_RECORD_SCHEMA,
            ),
            ResourceManifest(
                key="learning",
                name="Learning",
                description="Durable observation or lesson derived from prior work.",
                schema=_TEXT_RECORD_SCHEMA,
            ),
            ResourceManifest(
                key="experiment",
                name="Experiment",
                description="Experiment definition or result summary owned by the project.",
                schema=_OBJECT_SCHEMA,
            ),
        ],
    ),
    PluginManifest(
        slug="seo",
        name="SEO",
        description="Compatibility plugin facade for current SEO content operations.",
        capabilities=[
            CapabilityManifest(
                key="seo-content",
                name="SEO Content",
                description="Topics, articles, sources, internal links, schema, and publishing.",
            ),
            CapabilityManifest(
                key="search-console",
                name="Search Console",
                description=(
                    "GSC metrics, opportunity discovery, crawl checks, and refresh signals."
                ),
            ),
        ],
        providers=[
            ProviderManifest(
                key="dataforseo",
                name="DataForSEO",
                description="SERP, keyword volume, and competitor data provider.",
                auth_type="api-key",
            ),
            ProviderManifest(
                key="gsc",
                name="Google Search Console",
                description="Search Console OAuth-backed metrics provider.",
                auth_type="oauth",
            ),
            ProviderManifest(
                key="wordpress",
                name="WordPress",
                description="WordPress publishing target.",
                auth_type="api-key",
            ),
            ProviderManifest(
                key="ghost",
                name="Ghost",
                description="Ghost publishing target.",
                auth_type="api-key",
            ),
        ],
        actions=[
            ActionManifest(
                key="topic.bulk-create",
                name="Create Topic Candidates",
                description="Create topic records from agent-selected SEO candidates.",
                capability="seo-content",
                risk_level="write",
                input_schema=_OBJECT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
            ),
            ActionManifest(
                key="publish.record",
                name="Record Publish",
                description="Record an externally completed publish result.",
                capability="seo-content",
                risk_level="write",
                input_schema=_OBJECT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
            ),
        ],
        resources=[
            ResourceManifest(
                key="topic",
                name="Topic",
                description="Compatibility schema for SEO topic candidates.",
                schema=_OBJECT_SCHEMA,
            ),
            ResourceManifest(
                key="article",
                name="Article",
                description="Compatibility schema for SEO article records.",
                schema=_OBJECT_SCHEMA,
            ),
            ResourceManifest(
                key="research-source",
                name="Research Source",
                description="Compatibility schema for article research sources.",
                schema=_OBJECT_SCHEMA,
            ),
            ResourceManifest(
                key="article-asset",
                name="Article Asset",
                description="Compatibility schema for article image/media assets.",
                schema=_ARTIFACT_RESOURCE_SCHEMA,
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
                input_schema=_OBJECT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
            ),
            ActionManifest(
                key="web.scrape",
                name="Scrape Web Page",
                description="Fetch and normalize a web page.",
                provider="firecrawl",
                capability="web-retrieval",
                risk_level="read",
                input_schema=_OBJECT_SCHEMA,
                output_schema=_OBJECT_SCHEMA,
            ),
        ],
        resources=[
            ResourceManifest(
                key="generated-image",
                name="Generated Image",
                description="Generated image artifact metadata reusable by any workflow.",
                schema=_ARTIFACT_RESOURCE_SCHEMA,
            ),
            ResourceManifest(
                key="web-document",
                name="Web Document",
                description="Retrieved or normalized web document metadata.",
                schema=_OBJECT_SCHEMA,
            ),
        ],
    ),
)

__all__ = [
    "BUILTIN_PLUGIN_MANIFESTS",
    "ActionManifest",
    "CapabilityManifest",
    "PluginManifest",
    "ProviderManifest",
    "ResourceManifest",
]
