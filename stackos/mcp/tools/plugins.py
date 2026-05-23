"""StackOS plugin/catalog MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.server import ToolRegistry, ToolSpec
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
    registry.register(
        ToolSpec(
            name="plugin.list",
            description="List installed StackOS plugins; optionally annotate project enablement.",
            input_model=PluginListInput,
            output_model=list[PluginOut],
            handler=_plugin_list,
        )
    )
    registry.register(
        ToolSpec(
            name="plugin.enable",
            description="Local-admin setup operation to enable a plugin for a project.",
            input_model=PluginEnableInput,
            output_model=WriteEnvelope[ProjectPluginOut],
            handler=_plugin_enable,
        )
    )
    registry.register(
        ToolSpec(
            name="plugin.disable",
            description="Local-admin setup operation to disable a plugin for a project.",
            input_model=PluginDisableInput,
            output_model=WriteEnvelope[ProjectPluginOut],
            handler=_plugin_disable,
        )
    )
    registry.register(
        ToolSpec(
            name="catalog.list",
            description="List the installed StackOS plugin catalog.",
            input_model=CatalogListInput,
            output_model=CatalogOut,
            handler=_catalog_list,
        )
    )
    registry.register(
        ToolSpec(
            name="catalog.describe",
            description="Describe one StackOS plugin catalog contribution.",
            input_model=CatalogDescribeInput,
            output_model=CatalogOut,
            handler=_catalog_describe,
        )
    )
    registry.register(
        ToolSpec(
            name="capability.list",
            description="List StackOS capabilities.",
            input_model=CapabilityListInput,
            output_model=list[CapabilityOut],
            handler=_capability_list,
        )
    )
    registry.register(
        ToolSpec(
            name="capability.describe",
            description="Describe one StackOS capability.",
            input_model=CapabilityDescribeInput,
            output_model=CapabilityOut,
            handler=_capability_describe,
        )
    )
    registry.register(
        ToolSpec(
            name="provider.list",
            description="List StackOS providers.",
            input_model=ProviderListInput,
            output_model=list[ProviderOut],
            handler=_provider_list,
        )
    )
    registry.register(
        ToolSpec(
            name="provider.describe",
            description="Describe one StackOS provider.",
            input_model=ProviderDescribeInput,
            output_model=ProviderOut,
            handler=_provider_describe,
        )
    )


__all__ = ["register"]
