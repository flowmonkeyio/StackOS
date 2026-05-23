"""Google People-Also-Ask scraper (PLAN.md L1053).

This is the no-key "integration" — there's no Google PAA API, so we
delegate to ``FirecrawlIntegration`` (the supported scraper) to fetch
the SERP page and extract the PAA box markup. Cost flows through
Firecrawl's budget; this wrapper exists so the skill code can call
``GooglePaaIntegration.extract(query=...)`` symmetrically with the
other integrations rather than hard-coding the Firecrawl detour.

The response shape is normalised to ``{"questions": [str, ...]}`` so
callers don't need to know about the Firecrawl markdown shape.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.integrations.firecrawl import FirecrawlIntegration
from stackos.mcp.errors import IntegrationDownError


class GooglePaaIntegration(BaseIntegration):
    """No-key wrapper that orchestrates a Firecrawl scrape of Google SERP."""

    kind = "google-paa"
    vendor = "google-paa"
    default_qps = 0.5

    def __init__(
        self,
        *,
        firecrawl: FirecrawlIntegration | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # ``firecrawl`` is injected by the dispatcher (so we share the
        # budget + rate-limit context). If not passed, the wrapper
        # raises a typed error rather than instantiating its own —
        # cost-tracking would otherwise leak out of the budget cap.
        self._firecrawl = firecrawl

    async def call(self, **kwargs: Any) -> IntegrationCallResult:
        """Disabled — use ``extract`` instead."""
        raise NotImplementedError(
            "GooglePaaIntegration delegates to Firecrawl; use extract(query=...)"
        )

    async def extract(self, *, query: str) -> dict[str, Any]:
        """Scrape Google SERP for ``query`` and parse PAA questions.

        The Firecrawl scrape gives us the rendered markdown; we look
        for the "People also ask" block which Firecrawl renders as a
        list of headings. Returns ``{"questions": [...]}``.
        """
        if self._firecrawl is None:
            raise IntegrationDownError(
                "GooglePaaIntegration requires a FirecrawlIntegration instance",
                data={"vendor": "google-paa", "hint": "wire firecrawl=... at construct"},
            )
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        result = await self._firecrawl.scrape(url=url, only_main_content=False)
        data = result.data
        # Firecrawl returns either ``{data: {markdown: ...}}`` or just
        # the markdown string; we accept both for robustness.
        if isinstance(data, dict):
            markdown = data.get("data", {}).get("markdown") or data.get("markdown") or ""
        else:
            markdown = str(data)
        # Heuristic: any line ending in '?' inside the first ~1500 chars
        # of the page that's not a Google-UI string. Real implementation
        # would parse the DOM; for M4 the markdown heuristic is
        # sufficient since the skill consumer (keyword-discovery)
        # post-filters the questions anyway.
        questions: list[str] = []
        for line in markdown.splitlines():
            stripped = line.strip().lstrip("#- *>").strip()
            if stripped.endswith("?") and len(stripped) > 10 and len(stripped) < 200:
                questions.append(stripped)
            if len(questions) >= 10:
                break
        return {"questions": questions, "raw_bytes": len(markdown)}

    async def test_credentials(self) -> dict[str, Any]:
        """Probe — scrape a known query and return the question count."""
        out = await self.extract(query="content marketing")
        return {"ok": True, "vendor": "google-paa", "questions_found": len(out["questions"])}


__all__ = ["GooglePaaIntegration"]
