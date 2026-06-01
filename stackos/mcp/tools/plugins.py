"""StackOS plugin/catalog MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.plugins import (
    CapabilityOut,
    CatalogOut,
    PluginOut,
    PluginRepository,
    ProjectPluginOut,
    ProviderOut,
)


class PluginListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None


class PluginDescribeInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"plugin_slug": "seo"}})

    plugin_slug: str
    project_id: int | None = None


class PluginEnableInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "plugin_slug": "seo"}},
    )

    project_id: int
    plugin_slug: str
    config_json: dict[str, Any] | None = None


class PluginDisableInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "plugin_slug": "seo"}},
    )

    project_id: int
    plugin_slug: str


class CatalogListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int | None = None


class CatalogDescribeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"plugin_slug": "seo", "project_id": 1}},
    )

    plugin_slug: str
    project_id: int | None = None


class CapabilityListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"plugin_slug": "seo", "project_id": 1}},
    )

    plugin_slug: str | None = None
    project_id: int | None = None


class CapabilityDescribeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"key": "seo-content", "plugin_slug": "seo"}},
    )

    key: str
    plugin_slug: str | None = None


class ProviderListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"plugin_slug": "utils", "project_id": 1}},
    )

    plugin_slug: str | None = None
    project_id: int | None = None


class ProviderDescribeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"key": "openai-images", "plugin_slug": "utils"}},
    )

    key: str
    plugin_slug: str | None = None


async def _plugin_list(
    inp: PluginListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> list[PluginOut]:
    return PluginRepository(ctx.session).list_plugins(project_id=inp.project_id)


async def _plugin_enable(
    inp: PluginEnableInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ProjectPluginOut]:
    env = PluginRepository(ctx.session).enable(
        project_id=inp.project_id,
        plugin_slug=inp.plugin_slug,
        config_json=inp.config_json,
    )
    return WriteEnvelope[ProjectPluginOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _plugin_disable(
    inp: PluginDisableInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ProjectPluginOut]:
    env = PluginRepository(ctx.session).disable(
        project_id=inp.project_id,
        plugin_slug=inp.plugin_slug,
    )
    return WriteEnvelope[ProjectPluginOut](
        data=env.data,
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def _catalog_list(
    inp: CatalogListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CatalogOut:
    return PluginRepository(ctx.session).catalog(project_id=inp.project_id)


async def _catalog_describe(
    inp: CatalogDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CatalogOut:
    return PluginRepository(ctx.session).catalog(
        plugin_slug=inp.plugin_slug,
        project_id=inp.project_id,
    )


async def _capability_list(
    inp: CapabilityListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> list[CapabilityOut]:
    return PluginRepository(ctx.session).list_capabilities(
        plugin_slug=inp.plugin_slug,
        project_id=inp.project_id,
    )


async def _capability_describe(
    inp: CapabilityDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CapabilityOut:
    return PluginRepository(ctx.session).get_capability(
        key=inp.key,
        plugin_slug=inp.plugin_slug,
    )


async def _provider_list(
    inp: ProviderListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> list[ProviderOut]:
    return PluginRepository(ctx.session).list_providers(
        plugin_slug=inp.plugin_slug,
        project_id=inp.project_id,
    )


async def _provider_describe(
    inp: ProviderDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ProviderOut:
    return PluginRepository(ctx.session).get_provider(key=inp.key, plugin_slug=inp.plugin_slug)


def register(registry: ToolRegistry) -> None:
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(
        registry,
        (
            "plugin.list",
            "plugin.enable",
            "plugin.disable",
            "catalog.list",
            "catalog.describe",
            "integration.list",
            "capability.list",
            "capability.describe",
            "provider.list",
            "provider.describe",
        ),
    )


__all__ = ["register"]
