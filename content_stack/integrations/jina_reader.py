"""Jina Reader integration wrapper (PLAN.md L1054).

Authentication: optional Bearer token; Jina exposes a free tier so the
``api_key`` is best-effort. If the credential payload is empty or the
``api_key`` field is missing/empty we issue calls without auth — Jina's
public limits apply.

Operation:

- ``read(url)`` — fetch ``https://r.jina.ai/<url>`` which returns
  Markdown for the target page.
"""

from __future__ import annotations

from typing import Any

from content_stack.integrations._base import BaseIntegration, IntegrationCallResult


class JinaReaderIntegration(BaseIntegration):
    """Wrapper for ``https://r.jina.ai/``."""

    kind = "jina"
    vendor = "jina"
    default_qps = 5.0

    # Official Reader ref: https://jina.ai/en-US/reader/. Auth is optional;
    # a bearer key raises quota while unauthenticated calls use public limits.
    BASE_URL = "https://r.jina.ai"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Empty payload allowed (free tier).
        self._api_key = self.payload.decode("utf-8").strip() if self.payload else ""

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def read(self, *, url: str) -> IntegrationCallResult:
        """Fetch ``r.jina.ai/<url>`` and return Markdown.

        The Jina Reader endpoint takes the target URL as the path
        suffix; the response is text/markdown. We let the base class
        truncate the response for the audit trail.
        """
        target = f"{self.BASE_URL}/{url}"
        return await self.call(
            op="read",
            method="GET",
            url=target,
            headers=self._headers(),
        )

    async def test_credentials(self) -> dict[str, Any]:
        """Health probe via a known good URL."""
        result = await self.read(url="https://example.com")
        return {"ok": True, "vendor": "jina", "bytes": len(str(result.data))}


__all__ = ["JinaReaderIntegration"]
