"""System helper operation contracts."""

from __future__ import annotations

from asyncio import gather, to_thread
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from stackos.artifacts import redact_secrets
from stackos.host_mcp import service as host_mcp_service
from stackos.host_mcp.accounts import DesiredAccount
from stackos.host_mcp.result import (
    AgentSandbox,
    HostMcpConnectionState,
    HostMcpSetupPolicy,
    HostMcpStatus,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.streaming import ProgressEmitter
from stackos.mcp.tools.sitemap import SitemapFetchInput, SitemapFetchOutput, _sitemap_fetch
from stackos.operations import host_accounts
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)


class HostMcpStatusInput(MCPInput):
    """Inspect the daemon's local hosts, with no caller-controlled filesystem target."""


class HostMcpAgentExecutionOut(BaseModel):
    """Safe selection facts from the shared host catalog, without auth commands."""

    model_config = ConfigDict(extra="forbid")

    sandboxes: list[AgentSandbox]
    review_sandbox: AgentSandbox | None
    models: list[str]
    recommended_model: str | None = None
    reasoning_efforts: dict[str, list[str]] = Field(
        description="Verified native reasoning choices by explicit model; "
        "absent models use native defaults."
    )
    custom_models: bool
    provider_selection: bool
    profile_selection: bool
    mcp_routing: Literal["unsupported", "per-call", "workspace", "profile"] = "unsupported"
    requires_provider_model: bool = Field(
        description="Requires explicit provider and model; otherwise native defaults are allowed."
    )
    notes: str
    sources: list[str]
    checked_on: str
    runnable: Literal["available", "unavailable", "unknown", "unsupported"]
    cli_version: str | None
    catalog_source: Literal["packaged", "native"]
    probe_status: Literal["not_checked", "observed", "failed", "stale"]
    runtime_checked_at: str | None
    credential_status: Literal["not_checked"]
    model_access: Literal["not_checked", "observed"]


class HostMcpStatusItemOut(BaseModel):
    """Safe status and selection facts; commands and config details stay local."""

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
    agent_execution: HostMcpAgentExecutionOut


class HostMcpStatusOut(BaseModel):
    ok: bool
    items: list[HostMcpStatusItemOut]


class SubscriptionMeter(BaseModel):
    scope: str
    remaining_percent: float = Field(ge=0, le=100)
    resets_at: AwareDatetime | None


class HostSubscription(BaseModel):
    plan: str | None
    meters: list[SubscriptionMeter]


class HostSubscriptionsOut(BaseModel):
    checked_at: AwareDatetime
    readings: dict[str, HostSubscription | None]
    selections: dict[str, DesiredAccount]


async def host_subscriptions(
    inp: HostMcpStatusInput, ctx: MCPContext, _emit: ProgressEmitter
) -> HostSubscriptionsOut:
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("trusted_local_admin") is not True
        or inp.run_token is not None
    ):
        raise ToolNotGrantedError("Subscription readings require a local operator session.")

    def read(reader: Callable[[], dict | None]) -> HostSubscription | None:
        try:
            result = reader()
            return HostSubscription.model_validate(result) if result else None
        except Exception:
            # A native reader can fail independently. Never return raw tool diagnostics.
            return None

    readers = host_mcp_service.subscription_readers()
    accounts = host_mcp_service.host_accounts(ctx.extras["settings"].data_dir)

    async def read_host(
        key: str, reader: Callable[[], dict | None]
    ) -> tuple[HostSubscription | None, DesiredAccount | None]:
        try:
            before = accounts.resolve(key).desired
            reading = await to_thread(read, reader)
        except Exception:
            # An unusable selected profile invalidates only this host. Do not
            # read native defaults or invent an identity when resolution fails.
            return None, None
        return reading, before

    results = await gather(*(read_host(key, reader) for key, reader in readers.items()))
    readings: dict[str, HostSubscription | None] = {}
    selections: dict[str, DesiredAccount] = {}
    # A finished host can change while a sibling is still pending. Revalidate
    # every selection after all readers finish, isolating resolution failures.
    for key, (reading, before) in zip(readers, results, strict=True):
        readings[key] = None
        try:
            selected = accounts.resolve(key).desired
        except Exception:
            continue
        selections[key] = selected
        readings[key] = reading if before == selected else None

    return HostSubscriptionsOut(
        checked_at=datetime.now(UTC),
        readings=readings,
        selections=selections,
    )


async def host_mcp_status(
    _inp: HostMcpStatusInput, _ctx: MCPContext, _emit: ProgressEmitter
) -> HostMcpStatusOut:
    settings = _ctx.extras.get("settings") if _ctx is not None else None
    aggregate = await to_thread(
        host_mcp_service.inspect_all, **({"data_dir": settings.data_dir} if settings else {})
    )
    items = []
    for result in aggregate.results:
        data = result.to_info()
        public = redact_secrets(
            {
                key: data[key]
                for key in HostMcpStatusItemOut.model_fields
                if key != "agent_execution"
            }
        )
        # These allowlisted field names are public facts (including the closed
        # credential_status enum); redact their values without masking safe keys.
        public["agent_execution"] = {
            key: redact_secrets(result.agent_execution[key])
            for key in HostMcpAgentExecutionOut.model_fields
        }
        items.append(HostMcpStatusItemOut.model_validate(public))
    return HostMcpStatusOut(ok=aggregate.ok, items=items)


def operation_specs():
    return [
        *host_accounts.operation_specs(),
        OperationSpec(
            name="hostMcp.subscriptions",
            summary="Read subscription plans and remaining quotas from signed-in native tools.",
            input_model=HostMcpStatusInput,
            output_model=HostSubscriptionsOut,
            handler=host_subscriptions,
            mutating=False,
            grant_policy="local-admin-console-read",
            category="system",
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                cli=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/hostMcp.subscriptions/call",
                ),
            ),
            purpose="Display native plan and quota readings independently of MCP registration.",
            returns=(
                "Each unavailable or superseded host reading is null. selections contains "
                "only successfully resolved current account identities and generations; "
                "an unresolved selected profile has no selection entry or native fallback.",
            ),
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("raw",),
                raw_only_reason="Readings contain only plan, quota windows, and timestamps.",
            ),
            examples=(OperationExample(title="Refresh Home subscription readings", arguments={}),),
        ),
        operation_spec(
            name="hostMcp.status",
            summary="Read local AI-tool connection status and safe native execution choices.",
            input_model=HostMcpStatusInput,
            output_model=HostMcpStatusOut,
            handler=host_mcp_status,
            purpose=(
                "Let the local console inspect supported host connections through the shared "
                "host lifecycle service and its canonical agent capability catalog. "
                "Bounded version and supported native model-list reads report observed runtime "
                "facts separately from MCP. This does not repair hosts, clear restart markers, "
                "restart apps, start model turns or expose registration/auth configuration."
            ),
            returns=(
                "agent_execution contains declared model, per-model reasoning_efforts, "
                "custom-model, provider/profile and "
                "sandbox choices, with catalog notes and provenance. Empty sandboxes mean "
                "headless execution is unsupported. requires_provider_model controls whether "
                "native defaults are allowed; provider_selection also permits provider model IDs.",
                "runnable and cli_version reflect bounded CLI probes. catalog_source and "
                "model_access distinguish packaged suggestions from observed native choices; "
                "neither guarantees a model invocation or another account's entitlement.",
                "mcp_routing declares per-call, workspace, profile or unsupported native routing; "
                "it does not prove a connected project or successful native tool consumption.",
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
