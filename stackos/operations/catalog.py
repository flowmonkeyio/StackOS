"""Plugin catalog, provider, and enum operation contracts."""

from __future__ import annotations

from stackos.api.meta import EnumLookupResponse
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.meta import MetaEnumsInput, _meta_enums
from stackos.mcp.tools.plugins import (
    CapabilityDescribeInput,
    CapabilityListInput,
    CatalogDescribeInput,
    CatalogListInput,
    PluginDisableInput,
    PluginEnableInput,
    PluginListInput,
    ProviderDescribeInput,
    ProviderListInput,
    _capability_describe,
    _capability_list,
    _catalog_describe,
    _catalog_list,
    _plugin_disable,
    _plugin_enable,
    _plugin_list,
    _provider_describe,
    _provider_list,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample
from stackos.repositories.plugins import (
    CapabilityOut,
    CatalogOut,
    PluginOut,
    ProjectPluginOut,
    ProviderOut,
)


def operation_specs():
    return [
        operation_spec(
            name="plugin.list",
            summary="List installed StackOS plugins and optional project enablement.",
            input_model=PluginListInput,
            output_model=list[PluginOut],
            handler=_plugin_list,
            purpose="Use this to discover available plugin domains without loading manifests.",
            examples=(OperationExample(title="List plugins", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="plugin.enable",
            summary="Enable one installed plugin for a project.",
            input_model=PluginEnableInput,
            output_model=WriteEnvelope[ProjectPluginOut],
            handler=_plugin_enable,
            purpose="Use this only for explicit local-admin project setup.",
            prerequisites=("Requires operator/admin authority and an installed plugin slug.",),
            examples=(
                OperationExample(
                    title="Enable SEO plugin",
                    arguments={"project_id": 1, "plugin_slug": "seo"},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-plugin-write",
        ),
        operation_spec(
            name="plugin.disable",
            summary="Disable one project plugin.",
            input_model=PluginDisableInput,
            output_model=WriteEnvelope[ProjectPluginOut],
            handler=_plugin_disable,
            purpose="Use this only for explicit local-admin project setup cleanup.",
            prerequisites=("Requires operator/admin authority.",),
            examples=(
                OperationExample(
                    title="Disable plugin",
                    arguments={"project_id": 1, "plugin_slug": "seo"},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-plugin-write",
        ),
        operation_spec(
            name="catalog.list",
            summary="List the installed StackOS plugin catalog.",
            input_model=CatalogListInput,
            output_model=CatalogOut,
            handler=_catalog_list,
            purpose="Use this for compact discovery of plugin resources, actions, and templates.",
            examples=(OperationExample(title="Read catalog", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="catalog.describe",
            summary="Describe one plugin catalog contribution.",
            input_model=CatalogDescribeInput,
            output_model=CatalogOut,
            handler=_catalog_describe,
            purpose="Use this when an agent needs one plugin's actions/resources/templates.",
            examples=(
                OperationExample(
                    title="Describe plugin catalog",
                    arguments={"plugin_slug": "engineering", "project_id": 1},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="capability.list",
            summary="List StackOS capabilities.",
            input_model=CapabilityListInput,
            output_model=list[CapabilityOut],
            handler=_capability_list,
            purpose="Use this to discover capability groups across plugins/providers.",
            examples=(OperationExample(title="List capabilities", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="capability.describe",
            summary="Describe one StackOS capability.",
            input_model=CapabilityDescribeInput,
            output_model=CapabilityOut,
            handler=_capability_describe,
            purpose="Use this when a workflow names a capability key.",
            examples=(
                OperationExample(
                    title="Describe capability",
                    arguments={"key": "seo-content", "plugin_slug": "seo"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="provider.list",
            summary="List StackOS providers.",
            input_model=ProviderListInput,
            output_model=list[ProviderOut],
            handler=_provider_list,
            purpose="Use this to discover provider keys before auth/status checks.",
            examples=(OperationExample(title="List providers", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="provider.describe",
            summary="Describe one StackOS provider.",
            input_model=ProviderDescribeInput,
            output_model=ProviderOut,
            handler=_provider_describe,
            purpose="Use this when an agent needs provider auth/capability metadata.",
            examples=(
                OperationExample(
                    title="Describe provider",
                    arguments={"key": "slack-bot", "plugin_slug": "communications"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="meta.enums",
            summary="Return enum values and legal state transitions.",
            input_model=MetaEnumsInput,
            output_model=EnumLookupResponse,
            handler=_meta_enums,
            purpose="Use this when an agent needs allowed status values or transitions.",
            examples=(OperationExample(title="Read enums", arguments={}),),
            mutating=False,
            grant_policy="direct-read",
        ),
    ]


__all__ = ["operation_specs"]
