"""Shared scaffolding for integration wrappers.

Every per-vendor wrapper inherits ``BaseIntegration`` and implements its
own ``call`` + ``test_credentials`` methods. The base class handles the
cross-cutting concerns:

- Token-bucket QPS pacing (PLAN.md L1037-L1041, ``_rate_limit.py``).
- Pre-call ``IntegrationBudgetRepository.record_call`` so a breach
  surfaces *before* we hit the vendor (audit M-25).
- httpx retries on 429 / 5xx with exponential backoff (3 retries,
  0.5s → 1s → 2s).
- Persistent failure → ``IntegrationDownError`` (PLAN.md error code
  ``-32010``).
- Per-call ``RunStepCallRepository.record_call`` so the daemon's audit
  trail tracks every vendor hit with cost (cents) and duration.
- Sanitised request/response logging — never log secret tokens.

Wrappers can override ``_estimate_cost_usd`` and ``_extract_actual_cost_usd``
to feed the daemon's cost-of-truth column (``run_steps.cost_cents``); the
base estimates against the budget and reconciles after the call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from stackos.integrations._rate_limit import TokenBucket, get_bucket
from stackos.logging import get_logger
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.projects import IntegrationBudgetRepository
from stackos.repositories.runs import RunStepCallRepository

_log = get_logger(__name__)

# Retry policy per audit B-? — 3 retries, exponential backoff.
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 0.5  # seconds; grows 2x each attempt.

# Truncate response bodies for log + audit storage. Vendor responses can
# be huge (Firecrawl markdown returns 50KB+); we record them but cap the
# stored shape so the DB doesn't balloon.
MAX_LOG_BYTES = 4096

type JsonBody = dict[str, Any] | list[Any]
type FormBody = dict[str, Any]


@dataclass
class IntegrationCallResult:
    """Outcome of a single integration call.

    Public fields exposed to skill code; the wrapper's ``call`` method
    typically returns the inner ``data`` dict directly while threading
    cost + duration into ``run_step_calls``.
    """

    data: Any
    cost_usd: float
    duration_ms: int
    cached: bool = False
    metadata: dict[str, Any] | None = None


class BaseIntegration:
    """Mixin-style base class shared by every integration wrapper."""

    #: Human-readable kind label; matches the
    #: ``integration_credentials.kind`` column.
    kind: str = "unknown"

    #: Default qps if no project budget overrides it.
    default_qps: float = 1.0

    #: Vendor short-id used in cost logs (``run_step_calls.mcp_tool``
    #: column gets ``f"{vendor}.{op}"``).
    vendor: str = "unknown"

    def __init__(
        self,
        *,
        payload: bytes,
        project_id: int,
        http: httpx.AsyncClient,
        budget_repo: IntegrationBudgetRepository | None = None,
        run_step_call_repo: RunStepCallRepository | None = None,
        run_step_id: int | None = None,
        run_id: int | None = None,
        qps_override: float | None = None,
    ) -> None:
        self.payload = payload
        self.project_id = project_id
        self._http = http
        self._budget_repo = budget_repo
        self._run_step_call_repo = run_step_call_repo
        self._run_step_id = run_step_id
        self._run_id = run_id
        self._qps_override = qps_override

    # ------------------------------------------------------------------
    # Hooks for subclasses to override.
    # ------------------------------------------------------------------

    def _estimate_cost_usd(self, op: str, **kwargs: Any) -> float:
        """Return the *expected* cost of a call before issuing it.

        Used for budget pre-emption. Subclasses with per-op pricing
        override; the default is zero (no cost recorded against the
        budget but still counted as a call).
        """
        del op, kwargs
        return 0.0

    def _extract_actual_cost_usd(
        self,
        op: str,
        *,
        request: JsonBody | None,
        response: Any,
        estimated: float,
    ) -> float:
        """Reconcile the actual cost from a successful response.

        Some vendors (DataForSEO) report task cost in the response body;
        most do not. Default returns the estimate verbatim.
        """
        del op, request, response
        return estimated

    def _extract_response_metadata(
        self,
        op: str,
        *,
        request: JsonBody | None,
        response: Any,
        http_response: httpx.Response,
    ) -> dict[str, Any] | None:
        """Return non-secret provider metadata from a successful response."""
        del op, request, response, http_response
        return None

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def _bucket(self) -> TokenBucket:
        qps = self._qps_override if self._qps_override is not None else self.default_qps
        return get_bucket(project_id=self.project_id, kind=self.kind, qps=qps)

    @staticmethod
    def _truncate(blob: Any) -> Any:
        """Cap a request/response payload before persisting it."""
        if isinstance(blob, dict | list):
            text = repr(blob)
            if len(text) <= MAX_LOG_BYTES:
                return blob
            return {"_truncated": True, "preview": text[:MAX_LOG_BYTES]}
        if isinstance(blob, str) and len(blob) > MAX_LOG_BYTES:
            return blob[:MAX_LOG_BYTES] + "…[truncated]"
        return blob

    @classmethod
    def _sanitize_request(cls, request_json: JsonBody | None) -> JsonBody | None:
        """Strip fields that look like secrets before persisting."""
        if not request_json:
            return request_json
        if isinstance(request_json, list):
            return [
                cls._sanitize_request(item) if isinstance(item, dict | list) else item
                for item in request_json
            ]
        sensitive = {"api_key", "apikey", "password", "secret", "client_secret", "authorization"}
        clean: dict[str, Any] = {}
        for key, value in request_json.items():
            if key.lower() in sensitive:
                clean[key] = "[redacted]"
            elif isinstance(value, dict | list):
                clean[key] = cls._sanitize_request(value)
            else:
                clean[key] = value
        return clean

    # ------------------------------------------------------------------
    # Core dispatch.
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        op: str,
        json: JsonBody | None = None,
        data: FormBody | None = None,
        files: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: httpx.BasicAuth | None = None,
        follow_redirects: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
    ) -> httpx.Response:
        """Issue ``method url`` with rate-limit + budget + retry handling.

        On a non-recoverable HTTP error (4xx other than 429) we raise
        immediately. On 429 / 5xx we retry up to ``max_retries`` times
        with exponential backoff; the final failure surfaces as
        ``IntegrationDownError`` so callers can branch on the typed
        error.
        """
        last_status: int | None = None
        for attempt in range(max_retries + 1):
            await self._bucket().acquire(1)
            try:
                response = await self._http.request(
                    method,
                    url,
                    json=json,
                    data=data,
                    files=files,
                    params=params,
                    headers=headers,
                    auth=auth,
                    follow_redirects=follow_redirects,
                )
            except httpx.HTTPError as exc:
                _log.warning(
                    "integration.http_error",
                    integration=self.kind,
                    op=op,
                    project_id=self.project_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt >= max_retries:
                    raise IntegrationDownError(
                        f"{self.vendor}.{op} HTTP error after {max_retries} retries: {exc}",
                        data={
                            "vendor": self.vendor,
                            "op": op,
                            "retry_after": int(backoff_base * (2**attempt)),
                        },
                    ) from exc
                await asyncio.sleep(backoff_base * (2**attempt))
                continue

            last_status = response.status_code
            if response.status_code < 400:
                return response
            if response.status_code == 429:
                # Vendor rate-limited us; honor Retry-After if present.
                retry_after = self._parse_retry_after(response)
                if attempt >= max_retries:
                    raise RateLimitedError(
                        f"{self.vendor}.{op} 429 after {max_retries} retries",
                        data={
                            "vendor": self.vendor,
                            "op": op,
                            "status": response.status_code,
                            "retry_after": retry_after,
                            "provider_error": self._provider_error(response),
                        },
                    )
                wait = retry_after or backoff_base * (2**attempt)
                _log.warning(
                    "integration.rate_limited",
                    integration=self.kind,
                    op=op,
                    project_id=self.project_id,
                    attempt=attempt,
                    retry_after=wait,
                )
                await asyncio.sleep(wait)
                continue
            if response.status_code >= 500:
                if attempt >= max_retries:
                    raise IntegrationDownError(
                        f"{self.vendor}.{op} status {response.status_code} "
                        f"after {max_retries} retries",
                        data={
                            "vendor": self.vendor,
                            "op": op,
                            "status": response.status_code,
                            "provider_error": self._provider_error(response),
                        },
                    )
                _log.warning(
                    "integration.5xx",
                    integration=self.kind,
                    op=op,
                    project_id=self.project_id,
                    attempt=attempt,
                    status=response.status_code,
                )
                await asyncio.sleep(backoff_base * (2**attempt))
                continue
            # 4xx other than 429 — surface immediately.
            raise IntegrationDownError(
                f"{self.vendor}.{op} client error {response.status_code}: {response.text[:300]}",
                data={
                    "vendor": self.vendor,
                    "op": op,
                    "status": response.status_code,
                    "provider_error": self._provider_error(response),
                },
            )

        # Loop completed without return/raise — defensive default.
        # pragma: no cover — loop guarantees one of the above branches fires
        raise IntegrationDownError(
            f"{self.vendor}.{op} retries exhausted",
            data={"vendor": self.vendor, "op": op, "last_status": last_status},
        )

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> float | None:
        """Read a numeric ``Retry-After`` header if present."""
        header = response.headers.get("retry-after")
        if header is None:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> Any:
        try:
            body: Any = response.json()
        except ValueError:
            body = {"message": response.text[:500]}
        return cls._truncate(body)

    async def call(
        self,
        *,
        op: str,
        method: str = "POST",
        url: str,
        json_body: JsonBody | None = None,
        data_body: FormBody | None = None,
        files: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth: httpx.BasicAuth | None = None,
        request_log_body: JsonBody | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> IntegrationCallResult:
        """Issue one vendor call with budget + retry + audit trail.

        Subclasses build ``op`` / ``url`` / ``json_body`` from their public
        method signatures (e.g. ``DataForSeoIntegration.serp(...)`` calls
        ``self.call(op='serp', url='https://...', json_body=...)``).

        Steps:

        1. Budget pre-emption (pre-call): ``IntegrationBudgetRepository
           .record_call`` raises ``BudgetExceededError`` if the
           estimated cost would breach the cap. We catch nothing —
           audit M-25 says the breach must surface to the caller.
        2. HTTP request with retry/backoff via ``_request_with_retry``. Callers
           may set ``max_retries=0`` when a provider mutation has no safe
           idempotency contract; existing integrations retain the shared
           default.
        3. Cost reconciliation: ``_extract_actual_cost_usd`` returns the
           true cost from the response (DataForSEO reports it; others
           use the estimate).
        4. ``RunStepCallRepository.record_call`` if a run-step is bound.
        """
        body_for_cost = json_body if json_body is not None else data_body
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        estimated = self._estimate_cost_usd(op, json=body_for_cost, params=params)

        if self._budget_repo is not None and estimated > 0:
            self._budget_repo.record_call(
                project_id=self.project_id,
                kind=self.kind,
                cost_usd=estimated,
            )

        started = perf_counter()
        request_for_log = request_log_body
        if request_for_log is None:
            request_for_log = body_for_cost
        request_log = self._sanitize_request(request_for_log)
        try:
            response = await self._request_with_retry(
                method,
                url,
                op=op,
                json=json_body,
                data=data_body,
                files=files,
                params=params,
                headers=headers,
                auth=auth,
                max_retries=max_retries,
            )
        except (IntegrationDownError, RateLimitedError) as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            self._record_call(
                op=op,
                request=request_log,
                response={"error": exc.detail, "data": exc.data},
                duration_ms=duration_ms,
                error=str(exc),
                cost_cents=round(estimated * 100),
            )
            raise

        duration_ms = int((perf_counter() - started) * 1000)
        # Many vendors return JSON; some return text/markdown. Fall back
        # to text for non-JSON content.
        try:
            data = response.json()
        except ValueError:
            data = response.text

        actual_cost = self._extract_actual_cost_usd(
            op,
            request=request_for_log,
            response=data,
            estimated=estimated,
        )
        metadata = self._extract_response_metadata(
            op,
            request=request_for_log,
            response=data,
            http_response=response,
        )
        # If the vendor reports a different cost than estimated, reconcile
        # against the budget so the cap stays accurate.
        delta = actual_cost - estimated
        if self._budget_repo is not None and delta > 0:
            self._budget_repo.record_call(
                project_id=self.project_id,
                kind=self.kind,
                cost_usd=delta,
            )

        self._record_call(
            op=op,
            request=request_log,
            response=self._truncate(data),
            duration_ms=duration_ms,
            error=None,
            cost_cents=round(actual_cost * 100),
        )

        return IntegrationCallResult(
            data=data,
            cost_usd=actual_cost,
            duration_ms=duration_ms,
            cached=bool(metadata and metadata.get("cached")),
            metadata=metadata,
        )

    async def test_credentials(self) -> dict[str, Any]:
        """Vendor health probe — subclasses override.

        Default raises ``NotImplementedError`` so the dispatcher in
        ``api/projects.py`` and ``mcp/tools/projects.py`` surfaces a
        consistent typed error if someone registers a wrapper that
        forgets to expose a probe.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement test_credentials")

    def _record_call(
        self,
        *,
        op: str,
        request: Any,
        response: Any,
        duration_ms: int,
        error: str | None,
        cost_cents: int,
    ) -> None:
        """Persist a ``run_step_calls`` row if a step is bound."""
        if self._run_step_call_repo is None or self._run_step_id is None:
            return
        self._run_step_call_repo.record_call(
            run_step_id=self._run_step_id,
            mcp_tool=f"{self.kind}.{op}",
            request_json=request if isinstance(request, dict) else {"value": request},
            response_json=response if isinstance(response, dict) else {"value": response},
            duration_ms=duration_ms,
            error=error,
            cost_cents=cost_cents,
        )


__all__ = [
    "DEFAULT_BACKOFF_BASE",
    "DEFAULT_MAX_RETRIES",
    "MAX_LOG_BYTES",
    "BaseIntegration",
    "FormBody",
    "IntegrationCallResult",
    "JsonBody",
]
