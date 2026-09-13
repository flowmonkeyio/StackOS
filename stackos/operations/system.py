"""System helper operation contracts."""

from __future__ import annotations

from asyncio import to_thread

from pydantic import BaseModel, ConfigDict, Field

from stackos.artifacts import redact_secrets
from stackos.host_mcp import service as host_mcp_service
from stackos.host_mcp.result import HostMcpConnectionState, HostMcpSetupPolicy, HostMcpStatus
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.mcp.tools.sitemap import SitemapFetchInput, SitemapFetchOutput, _sitemap_fetch
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationResponsePolicy


class HostMcpStatusInput(MCPInput):
    """Inspect the daemon's local hosts, with no caller-controlled filesystem target."""


class HostMcpStatusItemOut(BaseModel):
    """Normalized status only; registration commands and config details stay local."""

    model_config = ConfigDict(extra="forbid")

    host_key: str
    surface: str
    status: HostMcpStatus
    message: str
    ok: bool
    available: bool
    advisory: bool
    blocking: bool
    needs_restart: bool
    display_name: str
    connection_state: HostMcpConnectionState | None
    status_label: str | None
    selected: bool
    managed: bool
    repairable: bool
    setup_policy: HostMcpSetupPolicy | None
    repair: str | None
    warnings: list[str] = Field(default_factory=list)


class HostMcpStatusOut(BaseModel):
    ok: bool
    items: list[HostMcpStatusItemOut]


async def host_mcp_status(
    _inp: HostMcpStatusInput, _ctx: MCPContext, _emit: ProgressEmitter
) -> HostMcpStatusOut:
    aggregate = await to_thread(host_mcp_service.inspect_all)
    items = []
    for result in aggregate.results:
        data = result.to_info()
        public = {key: data[key] for key in HostMcpStatusItemOut.model_fields}
        items.append(HostMcpStatusItemOut.model_validate(redact_secrets(public)))
    return HostMcpStatusOut(ok=aggregate.ok, items=items)


def operation_specs():
    return [
        operation_spec(
            name="hostMcp.status",
            summary="Read local AI-tool connection status without changing registrations.",
            input_model=HostMcpStatusInput,
            output_model=HostMcpStatusOut,
            handler=host_mcp_status,
            purpose=(
                "Let the local console inspect supported host connections through the shared "
                "host lifecycle service. This does not repair hosts, clear restart markers, "
                "restart apps, or expose registration commands and config contents."
            ),
            mutating=False,
            grant_policy="local-admin-read",
            category="system",
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("raw",),
                raw_only_reason="The bounded host status set is already a safe display projection.",
            ),
            examples=(OperationExample(title="Read local host connections", arguments={}),),
        ),
        operation_spec(
            name="sitemap.fetch",
            summary="Fetch and parse sitemap URLs without writing project state.",
            input_model=SitemapFetchInput,
            output_model=SitemapFetchOutput,
            handler=_sitemap_fetch,
            purpose=(
                "Use this as a low-level read helper when no workflow/action audit is needed. "
                "For normal provider/action flows, prefer action.describe, action.validate, "
                "and action.run/action.execute on utils.sitemap.fetch."
            ),
            when_to_use=(
                "A setup/debugging agent needs a bounded sitemap read and no durable action audit.",
            ),
            prerequisites=(
                "Inputs are public sitemap URLs; the helper never writes project state.",
            ),
            examples=(
                OperationExample(
                    title="Fetch one sitemap",
                    arguments={"urls": ["https://example.com/sitemap.xml"], "max_entries": 100},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
    ]


__all__ = ["operation_specs"]
