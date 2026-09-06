"""Stripe REST transport used by the finance provider actions.

The wrapper intentionally owns only HTTP/auth/retry semantics.  Invoice
authority, collections, bookkeeping, tax, and customer communication policy
belong to finance workflows and their selected external backend.

Official references:
* https://docs.stripe.com/api/authentication
* https://docs.stripe.com/api/idempotent_requests
* https://docs.stripe.com/api/versioning
* https://docs.stripe.com/error-low-level
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import httpx

from stackos.artifacts import redact_secrets
from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

STRIPE_API_BASE_URL = "https://api.stripe.com/v1"
STRIPE_API_VERSION = "2026-08-26.dahlia"
STRIPE_API_KEY_AUTH_METHOD_KEY = "api_key"
_ERROR_TYPES = frozenset({"api_error", "card_error", "idempotency_error", "invalid_request_error"})
_RATE_LIMIT_REASONS = frozenset(
    {
        "global-rate",
        "endpoint-rate",
        "global-concurrency",
        "endpoint-concurrency",
        "resource-specific",
    }
)
_CREDENTIAL_MARKER = re.compile(
    r"(?:(?:[spr]k|rkcs)_(?:test|live|claimable)_|whsec_|bearer)", re.IGNORECASE
)
# Diagnostic disclosure allowlist, not endpoint routing. Object-specific paths
# stay private even when their spelling resembles a valid API collection.
_DIAGNOSTIC_STATIC_PATHS = frozenset(
    {
        "/v1/account",
        "/v1/balance",
        "/v1/balance_transactions",
        "/v1/charges",
        "/v1/customers",
        "/v1/disputes",
        "/v1/invoice_payments",
        "/v1/invoiceitems",
        "/v1/invoices",
        "/v1/payment_records",
        "/v1/payment_records/report_payment",
        "/v1/refunds",
    }
)
_DIAGNOSTIC_API_VERSION_PATTERN = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:\.(?:acacia|basil|clover|dahlia|preview))?"
)

# Provider-owned repair guidance. Never copy arbitrary Stripe error.message:
# sandbox restrictions can include a private claim URL or credential material.
_FAILURE_DIAGNOSTICS: dict[str, tuple[str, str, bool]] = {
    "claimable_key_restricted": (
        "Stripe rejected this operation because the claimable sandbox key has limited permissions.",
        "Have the sandbox owner claim it through Stripe's original setup flow, then save an "
        "appropriately scoped test API key and test this Account again.",
        False,
    ),
    "authentication_failed": (
        "Stripe rejected the API key (HTTP 401).",
        "Check that this Account has a valid API key for the intended Stripe account or sandbox, "
        "replace it if needed, and test again.",
        False,
    ),
    "permission_denied": (
        "Stripe denied this operation (HTTP 403).",
        "Have the Stripe account owner check this key's permissions for the requested operation, "
        "update the Account if needed, and test again.",
        False,
    ),
    "rate_limited": (
        "Stripe rate-limited this request (HTTP 429).",
        "Wait for Stripe's retry delay. Retry reads later; follow the action recovery guidance "
        "before retrying a write.",
        True,
    ),
    "provider_unavailable": (
        "Stripe could not complete this request because of a server error.",
        "Check Stripe's service status and retry reads later. For writes, reconcile using the "
        "action recovery guidance before any retry.",
        True,
    ),
    "network_error": (
        "StackOS could not reach Stripe to complete this credential test.",
        "Check network access to Stripe and retry this read-only credential test.",
        True,
    ),
    "endpoint_not_recognized": (
        "Stripe did not recognize the requested API endpoint (HTTP 404).",
        "Check the requested method, endpoint and pinned API version against Stripe's contract; "
        "use the matching request log with Stripe if the documented route is rejected. "
        "Do not change keys or retry writes to diagnose endpoint availability.",
        False,
    ),
    "idempotency_conflict": (
        "Stripe reported a request conflict; the original operation may still be running.",
        "Preserve the original operation key and parameters. Reconcile its result before any "
        "further write; do not create a replacement operation key.",
        False,
    ),
    "idempotency_mismatch": (
        "Stripe rejected reuse of an operation key with different parameters or endpoint.",
        "Resolve the original operation and compare its immutable request before any further "
        "write. Do not replace the key to bypass this mismatch.",
        False,
    ),
    "provider_failure": (
        "Stripe rejected or could not complete this request.",
        "Review the safe provider error code and matching request_log_url (when present), or "
        "request ID, with the Stripe account owner before trying again. Unreviewed private "
        "message text is withheld and marked message_redacted.",
        False,
    ),
}


def parse_stripe_api_key_payload(payload: bytes) -> str:
    """Read the one daemon-held Stripe API key from its JSON credential payload."""

    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Stripe credential payload must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("Stripe credential payload must be a JSON object")
    api_key = parsed.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValidationError("Stripe credential JSON must contain api_key")
    api_key = api_key.strip()
    # Reject unsafe bearer material before HTTPX/h11 can echo an invalid
    # Authorization header through the shared transport exception logger.
    # Preserve ordinary pasted whitespace trimming without assuming key prefixes.
    if re.fullmatch(r"[\x21-\x7e]+", api_key) is None:
        raise ValidationError("Stripe API key must contain only visible ASCII characters")
    return api_key


def stripe_headers(api_key: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    """Build Stripe's daemon-only bearer authentication and version headers."""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Stripe-Version": STRIPE_API_VERSION,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


class StripeIntegration(BaseIntegration):
    """Thin, safe Stripe REST wrapper for one static API-version contract."""

    kind = "stripe"
    vendor = "stripe"
    # Stripe publishes account-specific rate limits. Keep StackOS below common
    # endpoint defaults until a per-account limit contract is configured.
    default_qps = 25.0

    def __init__(
        self,
        *,
        auth_method_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        probe_method = self.probe_context.auth_method_key if self.probe_context else None
        resolved_method = auth_method_key if auth_method_key is not None else probe_method
        if resolved_method != STRIPE_API_KEY_AUTH_METHOD_KEY:
            raise IntegrationDownError(
                "Stripe credential has an unsupported auth method",
                data={"vendor": "stripe", "reason_code": "unsupported_auth_method"},
            )
        try:
            self._api_key = parse_stripe_api_key_payload(self.payload)
        except ValidationError as exc:
            raise IntegrationDownError(
                "Stripe credential is invalid",
                data={"vendor": "stripe", "reason_code": "invalid_credential"},
            ) from exc

    async def request(
        self,
        *,
        method: str,
        path: str,
        op: str,
        params: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> IntegrationCallResult:
        """Call one reviewed Stripe REST endpoint.

        Stripe's idempotency guarantees are keyed by a caller-provided header
        for POST operations.  StackOS never retries those dispatches itself:
        an ambiguous outcome must be reconciled first (or replayed by Stripe
        with the exact same key) rather than being guessed locally.
        """

        normalized_method = method.upper()
        write = normalized_method == "POST"
        if write and not idempotency_key:
            raise ValidationError("Stripe POST requires an idempotency key")
        if write and len(idempotency_key or "") > 255:
            raise ValidationError("Stripe idempotency key must be at most 255 characters")
        if not path.startswith("/") or "?" in path:
            raise ValidationError("Stripe endpoint path must be a reviewed API-relative path")
        try:
            return await self.call(
                op=op,
                method=normalized_method,
                url=f"{STRIPE_API_BASE_URL}{path}",
                params=params,
                data_body=form,
                headers=stripe_headers(self._api_key, idempotency_key=idempotency_key),
                # Do not duplicate customer PII or financial values into the
                # lower-level integration audit; the canonical action-call
                # audit owns its redacted request envelope.
                request_log_body=_safe_request_log(op=op, form=form, params=params),
                max_retries=0 if write else 3,
            )
        except RateLimitedError as exc:
            data = _safe_exception_data(exc.data)
            data.update(
                {
                    "vendor": "stripe",
                    "op": op,
                    "outcome_unknown": False,
                    "retry_safe": not write,
                }
            )
            if write:
                data["recovery"] = (
                    "Wait for the provider limit, correct the request if needed, then use a fresh "
                    "idempotency key."
                )
            raise RateLimitedError("Stripe rate limit rejected the request", data=data) from exc
        except IntegrationDownError as exc:
            data = _safe_exception_data(exc.data)
            status = data.get("status")
            status_code = status if isinstance(status, int) else None
            provider_error = data.get("provider_error", {})
            idempotency_reason = provider_error.get("reason_code")
            idempotency_conflict = write and idempotency_reason in {
                "idempotency_conflict",
                "idempotency_mismatch",
            }
            # A rejected retry does not establish the original operation's result.
            # https://docs.stripe.com/api/idempotent_requests
            unknown = write and (status_code is None or status_code >= 500 or idempotency_conflict)
            retry_same_key = write and status_code is None
            data.update(
                {
                    "vendor": "stripe",
                    "op": op,
                    "outcome_unknown": unknown,
                    "retry_safe": retry_same_key if unknown else not write,
                }
            )
            if idempotency_reason == "idempotency_mismatch" and write:
                data["recovery"] = (
                    "The original operation may have executed. Reconcile its result and compare "
                    "the original endpoint and immutable parameters with this rejected retry. "
                    "Never use a new key to bypass the mismatch. Resolve the original operation "
                    "before deciding whether a separately authorized new operation is needed."
                )
            elif idempotency_conflict:
                data["recovery"] = (
                    "The original operation may still be running. Wait and reconcile its result. "
                    "Never use a new key. If recovery permits replay, use only the original "
                    "Idempotency-Key and identical parameters within Stripe's 24-hour retention "
                    "window; otherwise hold for resolution."
                )
            elif unknown and status_code is None:
                data["recovery"] = (
                    "The network failed before a response. Reconcile with retrieve/list when a "
                    "target is known; otherwise replay only the exact same Idempotency-Key with "
                    "identical parameters while it remains within Stripe's 24-hour retention "
                    "window. After that, "
                    "reconcile and do not dispatch."
                )
            elif unknown:
                data["recovery"] = (
                    "The Stripe server returned an indeterminate 5xx. Do not issue a new mutation: "
                    "reconcile with an authoritative retrieve/list action. Never use a new key. If "
                    "recovery explicitly requires a retry, use the exact same Idempotency-Key with "
                    "identical parameters only within Stripe's 24-hour retention window; otherwise "
                    "do not dispatch."
                )
            elif write:
                data["recovery"] = (
                    "Correct the rejected request before retrying with a fresh idempotency key."
                )
            raise IntegrationDownError("Stripe API request failed", data=data) from exc

    async def test_credentials(self) -> dict[str, Any]:
        """Probe the harmless account endpoint and return only safe account facts."""

        try:
            result = await self.request(method="GET", path="/account", op="auth.test")
        except (IntegrationDownError, RateLimitedError) as exc:
            safe = _safe_exception_data(exc.data)
            provider_error = safe.get("provider_error", {})
            reason_code = provider_error.get("reason_code")
            if reason_code not in _FAILURE_DIAGNOSTICS:
                reason_code = "network_error" if "status" not in safe else "provider_failure"
            summary, next_action, retryable = _FAILURE_DIAGNOSTICS[reason_code]
            should_retry = provider_error.get("should_retry")
            if isinstance(should_retry, bool):
                retryable = should_retry
                if not should_retry:
                    next_action = (
                        "Stripe explicitly advised against retrying this request. Review the "
                        "provider diagnostic and request log with the account owner before "
                        "another test."
                    )
            failure_metadata: dict[str, Any] = {"stage": "auth.test", "reason_code": reason_code}
            if "status" in safe:
                failure_metadata["provider_status_code"] = safe["status"]
            if "retry_after" in safe:
                failure_metadata["retry_after"] = safe["retry_after"]
            if provider_error:
                failure_metadata["provider_error"] = provider_error
            # This is a GET diagnostic: retryability says whether a transient
            # failure may recover, not whether an action mutation is safe to replay.
            return {
                "ok": False,
                "vendor": "stripe",
                "status": "failed",
                "summary": summary,
                "next_action": next_action,
                "retryable": retryable,
                "metadata": failure_metadata,
            }
        body = result.data
        if (
            not isinstance(body, Mapping)
            or body.get("object") != "account"
            or not isinstance(body.get("id"), str)
        ):
            return {
                "ok": False,
                "vendor": "stripe",
                "status": "invalid_response",
                "summary": "Stripe account probe returned no account id",
            }
        account_id = str(body["id"])
        display_name = (
            body.get("business_profile", {}).get("name")
            if isinstance(body.get("business_profile"), Mapping)
            else None
        )
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = body.get("email") if isinstance(body.get("email"), str) else None
        metadata: dict[str, Any] = {
            "evidence": {
                "account": {
                    "provider_account_id": account_id,
                    "display_name": display_name.strip()[:500]
                    if isinstance(display_name, str)
                    else None,
                    "metadata": {
                        "country": body.get("country")
                        if isinstance(body.get("country"), str)
                        else None,
                    },
                }
            }
        }
        return {
            "ok": True,
            "vendor": "stripe",
            "status": "ok",
            "summary": "Stripe account authenticated",
            "metadata": redact_secrets(metadata),
        }

    def _should_retry_response(self, response: httpx.Response) -> bool:
        # The provider override is authoritative; the shared loop enforces the
        # read retry bound and the zero-automatic-retry boundary for writes.
        # https://docs.stripe.com/error-low-level#the-stripe-should-retry-header
        should_retry = response.headers.get("stripe-should-retry", "").strip().lower()
        if should_retry in {"true", "false"}:
            return should_retry == "true"
        return super()._should_retry_response(response)

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Replace BaseIntegration's raw provider detail with an allowlisted error."""

        try:
            return await super()._request_with_retry(method, url, **kwargs)
        except RateLimitedError as exc:
            raise RateLimitedError(
                "Stripe rate limit rejected the request",
                data=_safe_exception_data(exc.data),
            ) from exc
        except IntegrationDownError as exc:
            raise IntegrationDownError(
                "Stripe API request failed",
                data=_safe_exception_data(exc.data),
            ) from exc

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> dict[str, Any]:
        """Return only Stripe's documented safe error fields, never raw bodies."""

        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        safe_payload = _safe_provider_error(
            payload,
            request_id=response.headers.get("request-id"),
        )
        # Capture the actual HTTP exchange, never version/path claims from the
        # response body or the configured pin. Keep missing headers explicit.
        safe_payload.update(_transport_diagnostics(response))
        error = payload.get("error") if isinstance(payload, Mapping) else None
        message = error.get("message") if isinstance(error, Mapping) else None
        reason_code = "provider_failure"
        if safe_payload.get("type") == "idempotency_error":
            reason_code = "idempotency_mismatch"
        elif response.status_code == 409 or safe_payload.get("code") == "idempotency_key_in_use":
            reason_code = "idempotency_conflict"
        elif response.status_code == 401:
            reason_code = "authentication_failed"
        elif response.status_code == 403:
            reason_code = "permission_denied"
            if isinstance(message, str) and message.strip().startswith(
                "This is a claimable sandbox key with limited permissions."
            ):
                reason_code = "claimable_key_restricted"
        elif (
            response.status_code == 404
            and isinstance(message, str)
            and message.startswith("Unrecognized request URL (")
        ):
            reason_code = "endpoint_not_recognized"
            # The diagnostic meaning is reviewed; its echoed URL is not. Use
            # only the actual static collection route, never query values or
            # an object-specific path (which can contain customer identifiers).
            route = safe_payload["request_path"]
            method = safe_payload["request_method"]
            safe_payload["message"] = (
                f"Unrecognized request URL ({method}: {route})."
                if route and route.count("/") == 2 and method in {"GET", "POST"}
                else "Unrecognized request URL."
            )
            safe_payload["message_redacted"] = safe_payload["message"] != message
        elif response.status_code == 429:
            reason_code = "rate_limited"
        elif response.status_code >= 500:
            reason_code = "provider_unavailable"
        safe_payload["reason_code"] = reason_code
        should_retry = response.headers.get("stripe-should-retry")
        if should_retry is not None:
            normalized_should_retry = should_retry.strip().lower()
            if normalized_should_retry in {"true", "false"}:
                safe_payload["should_retry"] = normalized_should_retry == "true"
        rate_limited_reason = response.headers.get("stripe-rate-limited-reason")
        if rate_limited_reason:
            safe_payload["rate_limited_reason"] = rate_limited_reason
        # Reconstruct guidance from a finite static classification, not a
        # provider-supplied summary or claim link, and validate header fields too.
        return _safe_provider_error(safe_payload)

    def _extract_response_metadata(
        self,
        op: str,
        *,
        request: dict[str, Any] | list[Any] | None,
        response: Any,
        http_response: httpx.Response,
    ) -> dict[str, Any] | None:
        del op, request, response
        # api_version retains its configured-contract meaning for compatibility.
        # Only request_api_version/response_api_version describe this exchange.
        metadata: dict[str, Any] = {
            "api_version": STRIPE_API_VERSION,
            **_transport_diagnostics(http_response),
        }
        request_id = _safe_diagnostic_token(
            http_response.headers.get("request-id"), r"req_[A-Za-z0-9_]+", max_length=200
        )
        if request_id:
            metadata["request_id"] = request_id
        retry_after = _safe_diagnostic_token(
            http_response.headers.get("retry-after"), r"[0-9]+(?:\.[0-9]+)?", max_length=40
        )
        if retry_after:
            metadata["retry_after"] = retry_after
        idempotent_replayed = http_response.headers.get("idempotent-replayed")
        if idempotent_replayed is not None:
            metadata["idempotent_replayed"] = idempotent_replayed.strip().lower() == "true"
        return metadata


def _safe_exception_data(data: Any) -> dict[str, Any]:
    raw = data if isinstance(data, Mapping) else {}
    safe: dict[str, Any] = {}
    status = raw.get("status")
    if isinstance(status, int):
        safe["status"] = status
    retry_after = raw.get("retry_after")
    if isinstance(retry_after, int | float):
        safe["retry_after"] = retry_after
    provider_error = raw.get("provider_error")
    if provider_error is not None:
        safe["provider_error"] = _safe_provider_error(provider_error)
    return safe


def _safe_provider_error(payload: Any, *, request_id: str | None = None) -> dict[str, Any]:
    """Keep reviewed diagnostics and correlated log links, not raw provider data."""

    if request_id is None and isinstance(payload, Mapping):
        candidate_request_id = payload.get("request_id")
        if isinstance(candidate_request_id, str):
            request_id = candidate_request_id
    error = payload.get("error") if isinstance(payload, Mapping) else payload
    if error is None and isinstance(payload, Mapping):
        error = payload
    if not isinstance(error, Mapping):
        error = {}
    safe: dict[str, Any] = {}
    error_type = error.get("type")
    if isinstance(error_type, str) and error_type in _ERROR_TYPES:
        safe["type"] = error_type
    for key in ("code", "decline_code"):
        value = _safe_diagnostic_token(error.get(key), r"[a-z][a-z0-9_]*", max_length=128)
        if value:
            safe[key] = value
    param = _safe_diagnostic_token(
        error.get("param"),
        r"[a-z][a-z0-9_]*(?:(?:\[[A-Za-z0-9_]*\])|(?:\.[A-Za-z0-9_]+))*",
        max_length=200,
    )
    if param:
        safe["param"] = param
    doc_url = _safe_diagnostic_token(
        error.get("doc_url"),
        r"https://(?:docs\.stripe\.com/error-codes|stripe\.com/docs/error-codes)"
        r"(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?",
        max_length=300,
    )
    if doc_url:
        # Keep the known public documentation destination, not a provider-
        # controlled suffix. Query strings, fragments and other origins reject.
        safe["doc_url"] = "https://docs.stripe.com/error-codes"
    safe_request_id = _safe_diagnostic_token(request_id, r"req_[A-Za-z0-9_]+", max_length=200)
    if safe_request_id:
        safe["request_id"] = safe_request_id
        # Stripe documents request_log_url as a Dashboard request-log locator:
        # https://docs.stripe.com/api/errors. Accept only the observed Workbench
        # route, with no credentials, extra parameters, fragments or redirects.
        log_url = _safe_diagnostic_token(
            error.get("request_log_url"),
            r"https://dashboard\.stripe\.com/(?:acct_[A-Za-z0-9]+/)?(?:test/)?"
            r"workbench/logs\?object=req_[A-Za-z0-9_]+",
            max_length=500,
        )
        if log_url and log_url.rsplit("=", 1)[1] == safe_request_id:
            safe["request_log_url"] = log_url
    # Unknown free text may contain a private claim URL, customer data or keys
    # that a generic redactor cannot safely identify. Make withholding visible.
    if isinstance(error.get("message"), str) or error.get("message_redacted") is True:
        safe["message_redacted"] = True
    if isinstance(payload, Mapping):
        safe.update(_safe_transport_fields(payload))
        reason_code = payload.get("reason_code")
        if isinstance(reason_code, str) and reason_code in _FAILURE_DIAGNOSTICS:
            summary, next_action, _ = _FAILURE_DIAGNOSTICS[reason_code]
            safe.update(reason_code=reason_code, summary=summary, next_action=next_action)
            if reason_code == "endpoint_not_recognized":
                message = _safe_diagnostic_token(
                    error.get("message"),
                    r"Unrecognized request URL(?: \((?:GET|POST): /v1/[a-z_]+\))?\.",
                    max_length=150,
                )
                if message:
                    safe["message"] = message
                    safe["message_redacted"] = error.get("message_redacted") is not False
        should_retry = payload.get("should_retry")
        if isinstance(should_retry, bool):
            safe["should_retry"] = should_retry
        rate_limited_reason = payload.get("rate_limited_reason")
        if isinstance(rate_limited_reason, str) and rate_limited_reason in _RATE_LIMIT_REASONS:
            safe["rate_limited_reason"] = rate_limited_reason
    return redact_secrets(safe)


def _transport_diagnostics(response: httpx.Response) -> dict[str, Any]:
    """Project reviewed HTTP facts without exposing IDs, queries or other headers."""

    fields: dict[str, Any] = {
        "request_method": None,
        "request_path": None,
        "request_api_version": None,
        "response_api_version": response.headers.get("stripe-version"),
    }
    try:
        request = response.request
    except RuntimeError:
        # A response without its request cannot establish what was sent.
        pass
    else:
        fields.update(
            request_method=request.method,
            # Inspect the wire spelling; URL.path would decode escaped text.
            request_path=request.url.raw_path.partition(b"?")[0].decode("ascii"),
            request_api_version=request.headers.get("stripe-version"),
        )
    return _safe_transport_fields(fields)


def _safe_transport_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Keep observed facts safe across repeated provider-error normalization."""

    safe: dict[str, Any] = {}
    for key in ("request_api_version", "response_api_version"):
        if key in fields:
            safe[key] = _safe_diagnostic_token(
                fields.get(key), _DIAGNOSTIC_API_VERSION_PATTERN, max_length=32
            )
    if "request_method" in fields:
        method = fields.get("request_method")
        safe["request_method"] = (
            method if isinstance(method, str) and method in {"GET", "POST"} else None
        )
    if "request_path" in fields:
        path = fields.get("request_path")
        safe["request_path"] = (
            path if isinstance(path, str) and path in _DIAGNOSTIC_STATIC_PATHS else None
        )
    return safe


def _safe_diagnostic_token(value: Any, pattern: str, *, max_length: int) -> str | None:
    """Validate an entire structured field; never truncate secrets into valid tokens."""

    if not isinstance(value, str) or not 0 < len(value) <= max_length:
        return None
    if _CREDENTIAL_MARKER.search(value) or re.fullmatch(pattern, value) is None:
        return None
    return value


def _safe_request_log(
    *,
    op: str,
    form: Mapping[str, Any] | None,
    params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Keep endpoint shape, never caller PII, description, or money values."""

    return {
        "operation": op,
        "form_fields": sorted(form) if form else [],
        "query_fields": sorted(params) if params else [],
    }


__all__ = [
    "STRIPE_API_BASE_URL",
    "STRIPE_API_KEY_AUTH_METHOD_KEY",
    "STRIPE_API_VERSION",
    "StripeIntegration",
    "parse_stripe_api_key_payload",
    "stripe_headers",
]
