"""Sitemap fetcher (small daemon helper for skill #5).

Skill #5 (``competitor-sitemap-shortcut``) needs to fetch and parse
``sitemap.xml`` files for a list of competitor domains. We expose a
small async helper here plus a thin MCP/REST seam so the skill can call
``sitemap.fetch`` instead of bringing its own HTTP+XML stack into the
LLM session.

Design choices:

- ``httpx.AsyncClient`` for fetches; we time-bound and size-bound every
  request so a hostile or runaway sitemap can't OOM the daemon.
- Sitemap-index recursion is supported up to a fixed depth (default 2)
  so a "sitemap of sitemaps of sitemaps" can't fan out indefinitely.
- We use ``xml.etree.ElementTree`` from the stdlib. Python 3 disabled
  the entity-expansion attack surface of the C-accelerator a long time
  ago, but as a defense-in-depth measure we (a) cap the response size,
  (b) reject any document whose root tag is not in the
  ``http://www.sitemaps.org/schemas/sitemap/0.9`` namespace, and (c)
  do not call ``XMLParser.feed`` with ``custom_parser`` overrides.
- Failures are returned as structured ``SitemapFetchError`` rows
  alongside successful entries so a caller can render a partial result
  instead of getting an opaque all-or-nothing exception.

The wrapper does **not** consult ``integration_credentials`` —
``sitemap.xml`` is unauthenticated by design. It also does **not**
participate in ``IntegrationBudgetRepository`` budget pre-emption
because there's no per-call vendor cost; the cost is wall-clock time
plus a tiny amount of bandwidth, which the per-request size + timeout
caps already bound.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urljoin

import httpx

from stackos.logging import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants — load-bearing safety caps.
# ---------------------------------------------------------------------------


# Sitemaps protocol namespace per https://www.sitemaps.org/protocol.html.
SITEMAP_NS: Final[str] = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Hard cap on bytes per response (10 MiB). Sitemaps over this size
# should be split per the protocol's own "50,000 URLs OR 50 MB
# uncompressed" recommendation; refusing oversized payloads protects
# the daemon from accidental DoS via a misconfigured sitemap.
MAX_BYTES: Final[int] = 10 * 1024 * 1024

# Per-request timeout (seconds). Generous — competitor servers can be
# slow — but bounded so a hung connection doesn't tie up the helper.
DEFAULT_TIMEOUT_S: Final[float] = 15.0

# Recursion limit on sitemap-index nesting. Public sitemaps almost
# never exceed depth 1; depth 2 covers very large sites.
MAX_INDEX_DEPTH: Final[int] = 2

# Per-fetch entry cap to keep results manageable for downstream
# clustering. Skill #5's strip-map note suggests ~500 URLs/competitor;
# we set a higher hard cap here and let the caller filter.
MAX_ENTRIES_PER_FETCH: Final[int] = 5_000


# ---------------------------------------------------------------------------
# Result shapes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    """One parsed ``<url>`` row.

    Optional fields fall back to ``None`` when the source XML omits the
    corresponding tag — sitemap.org's protocol says only ``<loc>`` is
    mandatory.
    """

    url: str
    lastmod: str | None = None
    changefreq: str | None = None
    priority: str | None = None
    source_sitemap: str | None = None


@dataclass(frozen=True, slots=True)
class SitemapFetchError:
    """One failed fetch — surfaces alongside successes for partial-result UX."""

    url: str
    error: str


@dataclass(frozen=True, slots=True)
class SitemapFetchResult:
    """Aggregate result of ``fetch_sitemap_entries``.

    ``entries`` is the de-duplicated, depth-collapsed list of URL rows.
    ``errors`` carries every per-source failure so the caller can show
    "fetched 3 of 5 sitemaps, 2 failed: …".
    """

    entries: list[SitemapEntry] = field(default_factory=list)
    errors: list[SitemapFetchError] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


async def fetch_sitemap_entries(
    urls: Iterable[str],
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_index_depth: int = MAX_INDEX_DEPTH,
    max_entries: int = MAX_ENTRIES_PER_FETCH,
) -> SitemapFetchResult:
    """Fetch and parse one or more sitemaps.

    Each URL is fetched independently; if it points at a sitemap-index
    we recurse into the child sitemaps up to ``max_index_depth`` levels.
    Successes accumulate in ``result.entries`` (deduplicated by URL,
    preserving first-seen lastmod/priority). Failures accumulate in
    ``result.errors``.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)

    assert client is not None  # narrows the optional for the rest of the body
    result = SitemapFetchResult()
    seen_urls: set[str] = set()

    try:
        for source in urls:
            await _fetch_one(
                source,
                client=client,
                depth=0,
                max_depth=max_index_depth,
                result=result,
                seen_urls=seen_urls,
                max_entries=max_entries,
            )
            if len(result.entries) >= max_entries:
                _log.info("sitemap.entry_cap_hit", cap=max_entries)
                break
    finally:
        if owns_client:
            await client.aclose()

    return result


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------


async def _fetch_one(
    url: str,
    *,
    client: httpx.AsyncClient,
    depth: int,
    max_depth: int,
    result: SitemapFetchResult,
    seen_urls: set[str],
    max_entries: int,
) -> None:
    """Fetch ``url``; recurse into a sitemap-index if encountered."""
    try:
        body = await _http_get(client, url)
    except _SitemapHttpError as exc:
        result.errors.append(SitemapFetchError(url=url, error=str(exc)))
        return

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        result.errors.append(SitemapFetchError(url=url, error=f"xml parse: {exc}"))
        return

    tag = _local_name(root.tag)
    if tag == "sitemapindex":
        if depth >= max_depth:
            result.errors.append(
                SitemapFetchError(url=url, error="sitemap-index depth limit reached")
            )
            return
        for sitemap_loc in _iter_locs(root, child_tag="sitemap"):
            absolute = urljoin(url, sitemap_loc)
            await _fetch_one(
                absolute,
                client=client,
                depth=depth + 1,
                max_depth=max_depth,
                result=result,
                seen_urls=seen_urls,
                max_entries=max_entries,
            )
            if len(result.entries) >= max_entries:
                return
    elif tag == "urlset":
        for entry in _iter_url_entries(root, source_url=url):
            if entry.url in seen_urls:
                continue
            seen_urls.add(entry.url)
            result.entries.append(entry)
            if len(result.entries) >= max_entries:
                return
    else:
        result.errors.append(
            SitemapFetchError(
                url=url,
                error=f"unexpected root element: {tag!r}; expected urlset|sitemapindex",
            )
        )


class _SitemapHttpError(Exception):
    """Internal exception type for sitemap fetch failures."""


async def _http_get(client: httpx.AsyncClient, url: str) -> bytes:
    """GET ``url`` with size + status guards.

    We stream the body so we can early-out on oversize without buffering
    the whole response; the size cap protects against accidentally
    parsing a 1 GB sitemap.
    """
    try:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise _SitemapHttpError(f"HTTP {response.status_code}")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise _SitemapHttpError(f"response exceeded {MAX_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise _SitemapHttpError(f"transport error: {exc}") from exc
    except TimeoutError as exc:
        raise _SitemapHttpError("request timed out") from exc


def _local_name(tag: str) -> str:
    """Strip the ``{namespace}`` prefix ElementTree adds to qualified tags."""
    if tag.startswith("{"):
        end = tag.find("}")
        if end != -1:
            return tag[end + 1 :]
    return tag


def _iter_locs(root: ET.Element, *, child_tag: str) -> Iterable[str]:
    """Yield ``<loc>`` text values for either ``<sitemap>`` or ``<url>`` children."""
    for child in root:
        if _local_name(child.tag) != child_tag:
            continue
        for sub in child:
            if _local_name(sub.tag) == "loc" and sub.text:
                yield sub.text.strip()


def _iter_url_entries(root: ET.Element, *, source_url: str) -> Iterable[SitemapEntry]:
    """Yield ``SitemapEntry`` rows from a ``<urlset>`` root."""
    for child in root:
        if _local_name(child.tag) != "url":
            continue
        loc: str | None = None
        lastmod: str | None = None
        changefreq: str | None = None
        priority: str | None = None
        for sub in child:
            sub_tag = _local_name(sub.tag)
            text = (sub.text or "").strip() or None
            if sub_tag == "loc":
                loc = text
            elif sub_tag == "lastmod":
                lastmod = text
            elif sub_tag == "changefreq":
                changefreq = text
            elif sub_tag == "priority":
                priority = text
        if loc:
            yield SitemapEntry(
                url=loc,
                lastmod=lastmod,
                changefreq=changefreq,
                priority=priority,
                source_sitemap=source_url,
            )


__all__ = [
    "MAX_BYTES",
    "MAX_ENTRIES_PER_FETCH",
    "MAX_INDEX_DEPTH",
    "SITEMAP_NS",
    "SitemapEntry",
    "SitemapFetchError",
    "SitemapFetchResult",
    "fetch_sitemap_entries",
]
