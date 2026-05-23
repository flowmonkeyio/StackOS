"""``sitemap.*`` — daemon helper for URL discovery.

Agents can use ``sitemap.fetch`` to inspect one or more sitemap URLs and then
decide which plugin resource records, learnings, or external actions should be
created. The tool is read-shaped: it never writes project state and carries no
workflow decisions.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from stackos.integrations.sitemap import (
    DEFAULT_TIMEOUT_S,
    MAX_ENTRIES_PER_FETCH,
    MAX_INDEX_DEPTH,
    SitemapEntry,
    SitemapFetchError,
    fetch_sitemap_entries,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.server import ToolRegistry, ToolSpec
from stackos.mcp.streaming import ProgressEmitter

# ---------------------------------------------------------------------------
# Inputs / outputs.
# ---------------------------------------------------------------------------


class SitemapFetchInput(MCPInput):
    """Pass one or more sitemap URLs; receive parsed entries."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "urls": [
                    "https://example.com/sitemap.xml",
                    "https://other-competitor.com/sitemap.xml",
                ],
                "max_entries": 500,
            }
        },
    )

    urls: list[str] = Field(min_length=1, max_length=20)
    timeout_s: float = Field(default=DEFAULT_TIMEOUT_S, gt=0, le=60)
    max_index_depth: int = Field(default=MAX_INDEX_DEPTH, ge=0, le=4)
    max_entries: int = Field(default=MAX_ENTRIES_PER_FETCH, ge=1, le=20_000)


class SitemapEntryOut(BaseModel):
    """Wire-shape for one sitemap entry."""

    url: str
    lastmod: str | None = None
    changefreq: str | None = None
    priority: str | None = None
    source_sitemap: str | None = None


class SitemapErrorOut(BaseModel):
    """Wire-shape for a per-URL fetch failure."""

    url: str
    error: str


class SitemapFetchOutput(BaseModel):
    """Aggregate read result: entries plus per-source errors."""

    entries: list[SitemapEntryOut]
    errors: list[SitemapErrorOut]


# ---------------------------------------------------------------------------
# Conversion helpers.
# ---------------------------------------------------------------------------


def _entry_to_wire(entry: SitemapEntry) -> SitemapEntryOut:
    return SitemapEntryOut(
        url=entry.url,
        lastmod=entry.lastmod,
        changefreq=entry.changefreq,
        priority=entry.priority,
        source_sitemap=entry.source_sitemap,
    )


def _error_to_wire(err: SitemapFetchError) -> SitemapErrorOut:
    return SitemapErrorOut(url=err.url, error=err.error)


# ---------------------------------------------------------------------------
# Handler.
# ---------------------------------------------------------------------------


async def _sitemap_fetch(
    payload: SitemapFetchInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> SitemapFetchOutput:
    """Fetch every input URL, recurse into sitemap-indexes, return entries."""
    async with httpx.AsyncClient(
        timeout=payload.timeout_s,
        follow_redirects=True,
    ) as client:
        result = await fetch_sitemap_entries(
            payload.urls,
            client=client,
            timeout_s=payload.timeout_s,
            max_index_depth=payload.max_index_depth,
            max_entries=payload.max_entries,
        )
    return SitemapFetchOutput(
        entries=[_entry_to_wire(e) for e in result.entries],
        errors=[_error_to_wire(err) for err in result.errors],
    )


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------


def register(registry: ToolRegistry) -> None:
    """Register every ``sitemap.*`` tool."""
    registry.register(
        ToolSpec(
            name="sitemap.fetch",
            description="Fetch + parse sitemap URLs (with sitemap-index recursion).",
            input_model=SitemapFetchInput,
            output_model=SitemapFetchOutput,
            handler=_sitemap_fetch,
        )
    )


__all__ = ["register"]
