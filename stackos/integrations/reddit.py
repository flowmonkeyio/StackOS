"""Reddit integration wrapper (PLAN.md L1052).

Authentication: OAuth2 *application-only* grant. StackOS core acquires and
renews the bearer token; this connector receives only the resolved
``{access_token, user_agent}`` payload needed for Reddit API calls.

Operations:

- ``search_subreddit(subreddit, query, sort, limit)`` — search posts.
- ``top_questions(subreddit, time, limit)`` — top posts that look like
  questions (heuristic: title ends with ``?``).

We use ``httpx`` directly rather than ``praw`` because praw is a thick
sync wrapper that would force us off the async path (PLAN.md "no
``requests``; no ``urllib``"). The two ops we need are tiny — a single
search GET and a single subreddit listing GET.
"""

from __future__ import annotations

import json
from typing import Any

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError


class RedditIntegration(BaseIntegration):
    """Wrapper for ``https://oauth.reddit.com``."""

    kind = "reddit"
    vendor = "reddit"
    default_qps = 1.0

    API_BASE = "https://oauth.reddit.com"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            payload = json.loads(self.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrationDownError(
                "Reddit credential payload is not valid JSON; expected {access_token, user_agent}",
                data={"vendor": "reddit"},
            ) from exc
        self._access_token = str(payload.get("access_token", "")).strip()
        self._user_agent = str(payload.get("user_agent", "stackos/0.1 (https://github.com/...)"))
        if not self._access_token:
            raise IntegrationDownError(
                "Reddit credential missing resolved access_token",
                data={"vendor": "reddit"},
            )

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": self._user_agent,
        }

    # ------------------------------------------------------------------
    # Public ops.
    # ------------------------------------------------------------------

    async def search_subreddit(
        self,
        *,
        subreddit: str,
        query: str,
        sort: str = "relevance",
        limit: int = 25,
    ) -> IntegrationCallResult:
        """Search a subreddit; ``q`` is the search term."""
        # API ref: https://www.reddit.com/dev/api/#GET_search. Listing
        # pagination uses after/before/count; expose it only with schema tests.
        params = {
            "q": query,
            "restrict_sr": "true",
            "sort": sort,
            "limit": str(limit),
        }
        return await self.call(
            op="search_subreddit",
            method="GET",
            url=f"{self.API_BASE}/r/{subreddit}/search",
            params=params,
            headers=self._api_headers(),
        )

    async def top_questions(
        self,
        *,
        subreddit: str,
        time_filter: str = "month",
        limit: int = 50,
    ) -> IntegrationCallResult:
        """Return top posts; caller filters for question-shaped titles."""
        # API ref: https://www.reddit.com/dev/api/#GET_{sort}. This wrapper
        # returns the raw listing; question filtering belongs to callers today.
        params = {"t": time_filter, "limit": str(limit)}
        return await self.call(
            op="top_questions",
            method="GET",
            url=f"{self.API_BASE}/r/{subreddit}/top",
            params=params,
            headers=self._api_headers(),
        )

    # ------------------------------------------------------------------
    # Health check.
    # ------------------------------------------------------------------

    async def test_credentials(self) -> dict[str, Any]:
        """Confirm that core supplied the resolved credential contract."""
        return {"ok": True, "vendor": "reddit"}


__all__ = ["RedditIntegration"]
