"""System helper operation contracts."""

from __future__ import annotations

from stackos.mcp.tools.sitemap import SitemapFetchInput, SitemapFetchOutput, _sitemap_fetch
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample


def operation_specs():
    return [
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
