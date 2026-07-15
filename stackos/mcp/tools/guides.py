"""Public StackOS guide retrieval for agent-facing onboarding help."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from stackos import __version__
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.server import ToolRegistry
from stackos.mcp.streaming import ProgressEmitter

GETTING_STARTED_GUIDE_URL = "https://stackos.flowmonkey.io/getting-started"
GETTING_STARTED_MARKDOWN_URL = f"{GETTING_STARTED_GUIDE_URL}.md"
MAX_GUIDE_BYTES = 256_000


class GettingStartedGuideInput(MCPInput):
    """Fetch the canonical public getting-started Markdown document."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {}},
    )

    timeout_s: float = Field(default=10.0, gt=0, le=30)


class GettingStartedGuideOutput(BaseModel):
    """Canonical references plus the current public Markdown when reachable."""

    title: str
    guide_url: str
    markdown_url: str
    source: Literal["stackos-website"]
    status: Literal["fetched", "unavailable"]
    content_available: bool
    content_type: str
    size_bytes: int
    sha256: str | None = None
    content: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _unavailable(message: str) -> GettingStartedGuideOutput:
    return GettingStartedGuideOutput(
        title="StackOS is installed. What happens next?",
        guide_url=GETTING_STARTED_GUIDE_URL,
        markdown_url=GETTING_STARTED_MARKDOWN_URL,
        source="stackos-website",
        status="unavailable",
        content_available=False,
        content_type="text/markdown; charset=utf-8",
        size_bytes=0,
        warnings=[message],
    )


async def _guide_getting_started(
    payload: GettingStartedGuideInput,
    _ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> GettingStartedGuideOutput:
    """Fetch only the fixed public Markdown URL; callers cannot supply arbitrary URLs."""
    try:
        async with httpx.AsyncClient(timeout=payload.timeout_s, follow_redirects=True) as client:
            response = await client.get(
                GETTING_STARTED_MARKDOWN_URL,
                headers={
                    "Accept": "text/markdown, text/plain;q=0.9",
                    "User-Agent": f"StackOS/{__version__} (+{GETTING_STARTED_GUIDE_URL})",
                },
            )
    except httpx.HTTPError:
        return _unavailable(
            "The public Markdown guide could not be reached. "
            "Link the website guide and retry later."
        )

    if response.status_code != 200:
        return _unavailable(
            f"The public Markdown guide returned HTTP {response.status_code}. "
            "Link the website guide and retry later."
        )

    body = response.content
    if not body:
        return _unavailable(
            "The public Markdown guide was empty. Link the website guide and retry later."
        )
    if len(body) > MAX_GUIDE_BYTES:
        return _unavailable(
            "The public Markdown guide exceeded the safe fetch limit. "
            "Link the website guide instead of returning the document."
        )

    try:
        content = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _unavailable(
            "The public guide was not valid UTF-8 Markdown. Link the website guide and retry later."
        )

    stripped = content.lstrip()
    canonical_marker = f"canonicalUrl: {GETTING_STARTED_GUIDE_URL}"
    if stripped.startswith("<") or canonical_marker not in content:
        return _unavailable(
            "The public guide response was not the canonical Markdown document. "
            "Link the website guide and retry later."
        )

    return GettingStartedGuideOutput(
        title="StackOS is installed. What happens next?",
        guide_url=GETTING_STARTED_GUIDE_URL,
        markdown_url=GETTING_STARTED_MARKDOWN_URL,
        source="stackos-website",
        status="fetched",
        content_available=True,
        content_type="text/markdown; charset=utf-8",
        size_bytes=len(body),
        sha256=sha256(body).hexdigest(),
        content=content,
    )


def register(registry: ToolRegistry) -> None:
    """Register public guide retrieval operations."""
    from stackos.operations.adapters.mcp import register_mcp_operation_names

    register_mcp_operation_names(registry, ("guide.gettingStarted",))


__all__ = [
    "GETTING_STARTED_GUIDE_URL",
    "GETTING_STARTED_MARKDOWN_URL",
    "GettingStartedGuideInput",
    "GettingStartedGuideOutput",
    "register",
]
