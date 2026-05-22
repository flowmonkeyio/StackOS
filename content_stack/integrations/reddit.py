"""Reddit integration wrapper (PLAN.md L1052).

Authentication: OAuth2 *application-only* grant — we use the
``client_credentials`` flow which gives us a 1-hour bearer token tied
to the app rather than a user. The credential payload is JSON
``{client_id, client_secret, user_agent}`` so we can store both halves
under one row.

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
import time
from typing import Any

import httpx

from content_stack.integrations._base import BaseIntegration, IntegrationCallResult
from content_stack.mcp.errors import IntegrationDownError


class RedditIntegration(BaseIntegration):
    """Wrapper for ``https://oauth.reddit.com``."""

    kind = "reddit"
    vendor = "reddit"
    default_qps = 1.0

    AUTH_BASE = "https://www.reddit.com/api/v1/access_token"
    API_BASE = "https://oauth.reddit.com"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        try:
            payload = json.loads(self.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IntegrationDownError(
                "Reddit credential payload is not valid JSON; expected "
                "{client_id, client_secret, user_agent}",
                data={"vendor": "reddit"},
            ) from exc
        self._client_id = str(payload.get("client_id", ""))
        self._client_secret = str(payload.get("client_secret", ""))
        self._user_agent = str(
            payload.get("user_agent", "content-stack/0.1 (https://github.com/...)")
        )
        if not self._client_id or not self._client_secret:
            raise IntegrationDownError(
                "Reddit credential missing client_id / client_secret",
                data={"vendor": "reddit"},
            )
        self._token: str | None = None
        self._token_expiry: float = 0.0  # monotonic-style epoch seconds

    async def _ensure_token(self) -> str:
        """Lazy-acquire (or refresh) the application-only bearer token."""
        if self._token is not None and time.time() < self._token_expiry - 60:
            return self._token
        # Official ref: https://github.com/reddit-archive/reddit/wiki/oauth2
        # Application-only OAuth uses client_credentials with HTTP Basic auth.
        # Reddit returns 401 on expired token, so we refresh proactively.
        resp = await self._http.post(
            self.AUTH_BASE,
            data={"grant_type": "client_credentials"},
            auth=httpx.BasicAuth(self._client_id, self._client_secret),
            headers={"User-Agent": self._user_agent},
        )
        if resp.status_code != 200:
            raise IntegrationDownError(
                f"Reddit token grant failed ({resp.status_code}): {resp.text[:200]}",
                data={"vendor": "reddit", "status": resp.status_code},
            )
        body = resp.json()
        self._token = str(body["access_token"])
        self._token_expiry = time.time() + int(body.get("expires_in", 3600))
        return self._token

    async def _api_headers(self) -> dict[str, str]:
        token = await self._ensure_token()
        return {
            "Authorization": f"Bearer {token}",
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
            headers=await self._api_headers(),
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
            headers=await self._api_headers(),
        )

    # ------------------------------------------------------------------
    # Health check.
    # ------------------------------------------------------------------

    async def test_credentials(self) -> dict[str, Any]:
        """Cheap auth probe — fetch the ``/api/v1/me`` endpoint.

        For application-only tokens ``/me`` returns 403 but we can use
        ``/api/v1/me/prefs`` which returns a 401 if the token is bad
        and a 403 if the scope is missing; either is a recoverable
        signal. Simpler: make a public listing call which forces the
        token grant.
        """
        # Force the token grant; the very fact it succeeds is the probe.
        await self._ensure_token()
        return {"ok": True, "vendor": "reddit", "token_expiry": self._token_expiry}


__all__ = ["RedditIntegration"]
