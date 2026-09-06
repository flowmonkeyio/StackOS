"""Curated Stripe invoice and payment-lifecycle action connector.

This connector is deliberately a provider-transport adapter.  It creates or
retrieves Stripe objects and exposes sanitized, account-bound references; it
does not decide whether an invoice is correct, collectible, taxable, approved,
or posted to a ledger.

Official references:
* https://docs.stripe.com/api/customers
* https://docs.stripe.com/api/invoices
* https://docs.stripe.com/api/invoiceitems
* https://docs.stripe.com/api/invoice-payment
* https://docs.stripe.com/api/payment_intents
* https://docs.stripe.com/api/payment-record
* https://docs.stripe.com/api/charges
* https://docs.stripe.com/api/balance_transactions
* https://docs.stripe.com/api/refunds
* https://docs.stripe.com/api/balance
* https://docs.stripe.com/api/idempotent_requests
* https://docs.stripe.com/api/pagination
* https://docs.stripe.com/error-low-level
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.vendor_utils import issue, unknown_operation
from stackos.artifacts import redact_secrets
from stackos.integrations.stripe import STRIPE_API_VERSION, StripeIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from stackos.secret_refs import SECRET_REF_SENTINEL

STRIPE_OPERATION = "rest.v1"
STRIPE_DEFAULT_LIMIT = 25
STRIPE_MAX_LIMIT = 100


@dataclass(frozen=True)
class StripeActionSpec:
    method: str
    path: str
    object_type: str | None = None
    list_item_type: str | None = None

    @property
    def write(self) -> bool:
        return self.method == "POST"


STRIPE_ACTION_SPECS: dict[str, StripeActionSpec] = {
    "stripe.customers.create": StripeActionSpec("POST", "/customers", "stripe.customer"),
    "stripe.customers.retrieve": StripeActionSpec(
        "GET", "/customers/{customer_ref}", "stripe.customer"
    ),
    "stripe.customers.list": StripeActionSpec(
        "GET", "/customers", list_item_type="stripe.customer"
    ),
    "stripe.invoices.create": StripeActionSpec("POST", "/invoices", "stripe.invoice"),
    "stripe.invoice-items.create": StripeActionSpec("POST", "/invoiceitems", "stripe.invoice-item"),
    "stripe.invoice-items.list": StripeActionSpec(
        "GET", "/invoiceitems", list_item_type="stripe.invoice-item"
    ),
    "stripe.invoices.finalize": StripeActionSpec(
        "POST", "/invoices/{invoice_ref}/finalize", "stripe.invoice"
    ),
    "stripe.invoices.send": StripeActionSpec(
        "POST", "/invoices/{invoice_ref}/send", "stripe.invoice"
    ),
    "stripe.invoices.mark-paid-out-of-band": StripeActionSpec(
        "POST", "/invoices/{invoice_ref}/pay", "stripe.invoice"
    ),
    "stripe.invoices.attach-payment": StripeActionSpec(
        "POST", "/invoices/{invoice_ref}/attach_payment", "stripe.invoice"
    ),
    "stripe.invoices.retrieve": StripeActionSpec(
        "GET", "/invoices/{invoice_ref}", "stripe.invoice"
    ),
    "stripe.invoices.list": StripeActionSpec("GET", "/invoices", list_item_type="stripe.invoice"),
    "stripe.invoice-payments.list": StripeActionSpec(
        "GET",
        "/invoice_payments",
        list_item_type="stripe.invoice-payment",
    ),
    "stripe.payment-intents.retrieve": StripeActionSpec(
        "GET", "/payment_intents/{payment_intent_ref}", "stripe.payment-intent"
    ),
    "stripe.payment-records.report": StripeActionSpec(
        "POST", "/payment_records/report_payment", "stripe.payment-record"
    ),
    "stripe.payment-records.retrieve": StripeActionSpec(
        "GET", "/payment_records/{payment_record_ref}", "stripe.payment-record"
    ),
    "stripe.payment-records.list": StripeActionSpec(
        "GET", "/payment_records", list_item_type="stripe.payment-record"
    ),
    "stripe.charges.retrieve": StripeActionSpec("GET", "/charges/{charge_ref}", "stripe.charge"),
    "stripe.charges.list": StripeActionSpec("GET", "/charges", list_item_type="stripe.charge"),
    "stripe.disputes.list": StripeActionSpec("GET", "/disputes", list_item_type="stripe.dispute"),
    "stripe.disputes.retrieve": StripeActionSpec(
        "GET", "/disputes/{dispute_ref}", "stripe.dispute"
    ),
    "stripe.balance-transactions.retrieve": StripeActionSpec(
        "GET", "/balance_transactions/{balance_transaction_ref}", "stripe.balance-transaction"
    ),
    "stripe.balance-transactions.list": StripeActionSpec(
        "GET",
        "/balance_transactions",
        list_item_type="stripe.balance-transaction",
    ),
    "stripe.refunds.retrieve": StripeActionSpec("GET", "/refunds/{refund_ref}", "stripe.refund"),
    "stripe.refunds.list": StripeActionSpec("GET", "/refunds", list_item_type="stripe.refund"),
    "stripe.balance.retrieve": StripeActionSpec("GET", "/balance"),
}


class StripeActionConnector:
    """Decision-free connector for the reviewed Stripe finance action set."""

    key = "stripe"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != STRIPE_OPERATION:
            return unknown_operation(request)
        spec = STRIPE_ACTION_SPECS.get(request.action_key)
        if spec is None:
            return [
                issue(
                    "$.action_key",
                    f"unsupported Stripe action {request.action_key!r}",
                    "enum_mismatch",
                )
            ]
        issues: list[ActionValidationIssue] = []
        config = request.config_json.get("stripe")
        if not isinstance(config, Mapping):
            issues.append(issue("$.config.stripe", "Stripe action config is required", "required"))
        else:
            expected = {"method": spec.method, "path": spec.path}
            for key, value in expected.items():
                if config.get(key) != value:
                    issues.append(
                        issue(
                            f"$.config.stripe.{key}",
                            "Stripe action config must match the fixed connector contract",
                            "contract_mismatch",
                        )
                    )
            if config.get("api_version") != STRIPE_API_VERSION:
                issues.append(
                    issue(
                        "$.config.stripe.api_version",
                        "Stripe API version must match the pinned connector contract",
                        "contract_mismatch",
                    )
                )
        if spec.write:
            idempotency_key = request.idempotency_key
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                issues.append(
                    issue(
                        "$.idempotency_key",
                        "Stripe POST actions require a deterministic idempotency key",
                        "required",
                    )
                )
            elif len(idempotency_key) > 255:
                issues.append(
                    issue(
                        "$.idempotency_key",
                        "Stripe idempotency key must be at most 255 characters",
                        "max_length",
                    )
                )
        self._validate_safe_refs(request, issues)
        self._validate_sensitive_text_refs(request, issues)
        self._validate_semantics(request, issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        # Stripe API calls are not a meaningful proxy for processing fees or
        # invoice economics, so StackOS must not claim a monetary estimate.
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != STRIPE_OPERATION:
            raise ValidationError(f"unsupported Stripe operation {request.operation!r}")
        spec = STRIPE_ACTION_SPECS.get(request.action_key)
        if spec is None:
            raise ValidationError(f"unsupported Stripe action {request.action_key!r}")
        if request.credential is None:
            raise ValidationError("Stripe action requires a resolved credential")
        if request.session is None:
            raise ValidationError("Stripe action requires a repository session")

        refs = ProviderObjectReferenceRepository(request.session, project_id=request.project_id)
        path = _path_for(request, spec, refs)
        params = _params_for(request, spec, refs)
        form = _form_for(request, spec, refs)
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                integration = StripeIntegration(
                    payload=request.credential.secret_payload,
                    project_id=request.project_id,
                    http=http,
                    auth_method_key=request.credential.credential.auth_method_key,
                )
                result = await integration.request(
                    method=spec.method,
                    path=path,
                    op=request.action_key,
                    params=params or None,
                    form=form or None,
                    idempotency_key=request.idempotency_key if spec.write else None,
                )
        except (IntegrationDownError, RateLimitedError) as exc:
            raise _connector_error(exc) from exc

        body = result.data
        if not isinstance(body, Mapping):
            raise _malformed_response_error(write=spec.write, action_key=request.action_key)
        try:
            safe = _safe_response(
                body,
                spec=spec,
                refs=refs,
                credential=request.credential.credential,
                correlation_key=request.input_json.get("correlation_key"),
            )
        except ValidationError as exc:
            raise _malformed_response_error(
                write=spec.write, action_key=request.action_key
            ) from exc
        except Exception as exc:
            # HTTP has already completed. Reference persistence/projection failure
            # must not turn an executed POST into an ordinary retryable failure.
            raise _malformed_response_error(
                write=spec.write,
                action_key=request.action_key,
                reason_code="response_normalization_failed",
            ) from exc
        metadata = {"vendor": "stripe", "operation": request.action_key}
        if result.metadata:
            metadata.update(redact_secrets(result.metadata))
        return ActionConnectorResult(
            output_json={"provider": "stripe", "operation": request.action_key, "data": safe},
            metadata_json=metadata,
        )

    @staticmethod
    def _validate_sensitive_text_refs(
        request: ActionConnectorRequest,
        issues: list[ActionValidationIssue],
    ) -> None:
        # Repository validation projects exact $secret_ref markers to this
        # sentinel. Actual execution receives the daemon-materialized values,
        # so enforce the marker only on the pre-dispatch validation request.
        if not request.dry_run:
            return
        fields_by_action = {
            "stripe.customers.create": {"email", "name", "description"},
            "stripe.customers.list": {"email"},
            "stripe.invoices.create": {"description"},
            "stripe.invoice-items.create": {"description"},
            "stripe.payment-records.report": {"payment_reference"},
        }
        for key in fields_by_action.get(request.action_key, set()) & set(request.input_json):
            if request.input_json.get(key) != SECRET_REF_SENTINEL:
                issues.append(
                    issue(
                        f"$.{key}",
                        "sensitive Stripe text must use an exact $secret_ref marker",
                        "payload_secret_ref_required",
                    )
                )

    @staticmethod
    def _validate_safe_refs(
        request: ActionConnectorRequest,
        issues: list[ActionValidationIssue],
    ) -> None:
        ref_keys = {
            "customer_ref",
            "invoice_ref",
            "charge_ref",
            "payment_intent_ref",
            "payment_record_ref",
            "balance_transaction_ref",
            "refund_ref",
            "dispute_ref",
            "page_cursor",
        }
        for key in ref_keys & set(request.input_json):
            value = request.input_json.get(key)
            if not isinstance(value, str) or not value.startswith("provider-object:"):
                issues.append(
                    issue(
                        f"$.{key}",
                        "Stripe selectors must use an account-bound provider-object reference",
                        "safe_ref_required",
                    )
                )

    @staticmethod
    def _validate_semantics(
        request: ActionConnectorRequest,
        issues: list[ActionValidationIssue],
    ) -> None:
        payload = request.input_json
        correlation_key = payload.get("correlation_key")
        if "correlation_key" in payload and (
            not isinstance(correlation_key, str)
            or re.fullmatch(r"[0-9a-f]{32}", correlation_key) is None
        ):
            issues.append(
                issue(
                    "$.correlation_key",
                    "use a random 32-character lowercase hexadecimal key",
                    "format",
                )
            )
        for key in ("created_gte", "created_lte"):
            if key in payload and (
                not isinstance(payload[key], int)
                or isinstance(payload[key], bool)
                or payload[key] < 0
            ):
                issues.append(
                    issue(
                        f"$.{key}", "created bounds must be nonnegative integer timestamps", "range"
                    )
                )
        lower, upper = payload.get("created_gte"), payload.get("created_lte")
        if isinstance(lower, int) and isinstance(upper, int) and lower > upper:
            issues.append(
                issue("$.created_lte", "created_lte must not precede created_gte", "range")
            )
        scope_fields = {
            "stripe.invoice-items.list": ("invoice_ref", "customer_ref"),
            "stripe.disputes.list": ("charge_ref", "payment_intent_ref"),
            "stripe.invoices.attach-payment": ("payment_intent_ref", "payment_record_ref"),
        }.get(request.action_key)
        if scope_fields and sum(key in payload for key in scope_fields) != 1:
            issues.append(
                issue(
                    "$", f"supply exactly one of {scope_fields[0]} or {scope_fields[1]}", "one_of"
                )
            )
        if request.action_key == "stripe.invoices.create":
            currency = payload.get("currency")
            if not isinstance(currency, str) or re.fullmatch(r"[a-z]{3}", currency) is None:
                issues.append(
                    issue("$.currency", "currency must be a lowercase ISO currency code", "format")
                )
            if payload.get("collection_method") != "send_invoice":
                issues.append(
                    issue(
                        "$.collection_method",
                        "Stripe finance invoices must use collection_method=send_invoice",
                        "enum_mismatch",
                    )
                )
            days = payload.get("days_until_due")
            if not isinstance(days, int) or isinstance(days, bool) or days < 1:
                issues.append(
                    issue(
                        "$.days_until_due",
                        "days_until_due must be a positive integer",
                        "range",
                    )
                )
        if request.action_key == "stripe.invoice-items.create":
            amount = payload.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                issues.append(issue("$.amount", "amount must be a positive integer", "range"))
            description = payload.get("description")
            if not isinstance(description, str) or not description.strip():
                issues.append(
                    issue(
                        "$.description",
                        "invoice item description is required for a reviewable line",
                        "required",
                    )
                )
        if request.action_key == "stripe.invoices.mark-paid-out-of-band":
            for key in ("charge", "forgive", "mandate", "payment_method", "source"):
                if key in payload:
                    issues.append(
                        issue(
                            f"$.{key}",
                            "mark-paid-out-of-band does not accept Stripe collection or "
                            "forgiveness controls",
                            "forbidden",
                        )
                    )
        if request.action_key == "stripe.payment-records.report":
            amount = payload.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                issues.append(issue("$.amount", "amount must be a positive integer", "range"))
            currency = payload.get("currency")
            if not isinstance(currency, str) or re.fullmatch(r"[a-z]{3}", currency) is None:
                issues.append(
                    issue("$.currency", "currency must be a lowercase ISO currency code", "format")
                )
            timestamps: dict[str, int] = {}
            for key in ("initiated_at", "guaranteed_at"):
                timestamp = payload.get(key)
                if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
                    issues.append(
                        issue(
                            f"$.{key}",
                            f"{key} must be a nonnegative integer timestamp",
                            "range",
                        )
                    )
                else:
                    timestamps[key] = timestamp
            if (
                "initiated_at" in timestamps
                and "guaranteed_at" in timestamps
                and timestamps["initiated_at"] > timestamps["guaranteed_at"]
            ):
                issues.append(
                    issue(
                        "$.guaranteed_at",
                        "guaranteed_at must not precede initiated_at",
                        "range",
                    )
                )
            payment_reference = payload.get("payment_reference")
            if not request.dry_run and (
                not isinstance(payment_reference, str)
                or not payment_reference.strip()
                or payment_reference == SECRET_REF_SENTINEL
            ):
                issues.append(
                    issue(
                        "$.payment_reference",
                        "payment_reference is required after payload-secret resolution",
                        "required",
                    )
                )
        if request.action_key.endswith(".list"):
            limit = payload.get("limit", STRIPE_DEFAULT_LIMIT)
            if (
                not isinstance(limit, int)
                or isinstance(limit, bool)
                or not 1 <= limit <= STRIPE_MAX_LIMIT
            ):
                issues.append(
                    issue(
                        "$.limit",
                        f"limit must be an integer from 1 through {STRIPE_MAX_LIMIT}",
                        "range",
                    )
                )


def _path_for(
    request: ActionConnectorRequest,
    spec: StripeActionSpec,
    refs: ProviderObjectReferenceRepository,
) -> str:
    path = spec.path
    replacement_types = {
        "{customer_ref}": ("customer_ref", "stripe.customer"),
        "{invoice_ref}": ("invoice_ref", "stripe.invoice"),
        "{charge_ref}": ("charge_ref", "stripe.charge"),
        "{payment_intent_ref}": ("payment_intent_ref", "stripe.payment-intent"),
        "{payment_record_ref}": ("payment_record_ref", "stripe.payment-record"),
        "{balance_transaction_ref}": ("balance_transaction_ref", "stripe.balance-transaction"),
        "{refund_ref}": ("refund_ref", "stripe.refund"),
        "{dispute_ref}": ("dispute_ref", "stripe.dispute"),
    }
    for token, (key, object_type) in replacement_types.items():
        if token not in path:
            continue
        value = _resolve_ref(request, refs, key, object_type)
        path = path.replace(token, value)
    return path


def _params_for(
    request: ActionConnectorRequest,
    spec: StripeActionSpec,
    refs: ProviderObjectReferenceRepository,
) -> dict[str, Any]:
    if spec.write:
        return {}
    payload = request.input_json
    params: dict[str, Any] = {}
    if request.action_key == "stripe.invoices.retrieve":
        # Invoice.customer_email freezes at finalization. Expand the current
        # customer too so an agent can detect a changed primary email.
        # https://docs.stripe.com/api/invoices/object#invoice_object-customer_email
        params["expand[]"] = "customer"
    if request.action_key == "stripe.balance-transactions.retrieve":
        params["expand[]"] = "source"
    elif request.action_key == "stripe.balance-transactions.list":
        params["expand[]"] = "data.source"
    if spec.list_item_type:
        params["limit"] = payload.get("limit", STRIPE_DEFAULT_LIMIT)
        if "page_cursor" in payload:
            params["starting_after"] = _resolve_ref(
                request, refs, "page_cursor", spec.list_item_type
            )
        if request.action_key == "stripe.invoices.list" and "status" in payload:
            params["status"] = payload["status"]
        if request.action_key == "stripe.invoices.list":
            if "customer_ref" in payload:
                params["customer"] = _resolve_ref(request, refs, "customer_ref", "stripe.customer")
            for bound in ("gte", "lte"):
                if f"created_{bound}" in payload:
                    params[f"created[{bound}]"] = payload[f"created_{bound}"]
        if request.action_key == "stripe.invoice-items.list":
            for field, kind in (("invoice", "stripe.invoice"), ("customer", "stripe.customer")):
                if f"{field}_ref" in payload:
                    params[field] = _resolve_ref(request, refs, f"{field}_ref", kind)
        if request.action_key == "stripe.disputes.list":
            for field, kind in (
                ("charge", "stripe.charge"),
                ("payment_intent", "stripe.payment-intent"),
            ):
                if f"{field}_ref" in payload:
                    params[field] = _resolve_ref(request, refs, f"{field}_ref", kind)
        if request.action_key == "stripe.customers.list":
            params["email"] = payload["email"]
        if request.action_key == "stripe.invoice-payments.list":
            params["invoice"] = _resolve_ref(request, refs, "invoice_ref", "stripe.invoice")
        if request.action_key == "stripe.charges.list" and "customer_ref" in payload:
            params["customer"] = _resolve_ref(request, refs, "customer_ref", "stripe.customer")
        if request.action_key == "stripe.charges.list" and "payment_intent_ref" in payload:
            params["payment_intent"] = _resolve_ref(
                request,
                refs,
                "payment_intent_ref",
                "stripe.payment-intent",
            )
        if request.action_key == "stripe.refunds.list" and "charge_ref" in payload:
            params["charge"] = _resolve_ref(request, refs, "charge_ref", "stripe.charge")
    return params


def _form_for(
    request: ActionConnectorRequest,
    spec: StripeActionSpec,
    refs: ProviderObjectReferenceRepository,
) -> dict[str, Any]:
    if not spec.write:
        return {}
    payload = request.input_json
    if request.action_key == "stripe.customers.create":
        return _copy_fields(payload, "email", "name", "description")
    if request.action_key == "stripe.invoices.create":
        return {
            "customer": _resolve_ref(request, refs, "customer_ref", "stripe.customer"),
            "collection_method": "send_invoice",
            "currency": payload["currency"],
            "days_until_due": payload["days_until_due"],
            # The initial action always produces a draft; a workflow decides
            # whether/when the separate finalize and send actions are allowed.
            "auto_advance": "false",
            **(
                {"metadata[stackos_correlation]": payload["correlation_key"]}
                if "correlation_key" in payload
                else {}
            ),
            **_copy_fields(payload, "description"),
        }
    if request.action_key == "stripe.invoices.finalize":
        # https://docs.stripe.com/api/invoices/finalize: retain explicit-only
        # advancement even when a draft was changed outside this connector.
        return {"auto_advance": "false"}
    if request.action_key == "stripe.invoices.mark-paid-out-of-band":
        # https://docs.stripe.com/api/invoices/pay#pay_invoice-paid_out_of_band
        # Never forward charge, source, mandate, or forgiveness controls. The
        # workflow owns the verified full-settlement decision.
        return {"paid_out_of_band": "true"}
    if request.action_key == "stripe.invoices.attach-payment":
        if "payment_intent_ref" in payload:
            return {
                "payment_intent": _resolve_ref(
                    request, refs, "payment_intent_ref", "stripe.payment-intent"
                )
            }
        return {
            "payment_record": _resolve_ref(
                request, refs, "payment_record_ref", "stripe.payment-record"
            )
        }
    if request.action_key == "stripe.payment-records.report":
        return {
            "amount_requested[currency]": payload["currency"],
            "amount_requested[value]": payload["amount"],
            "initiated_at": payload["initiated_at"],
            "outcome": "guaranteed",
            "guaranteed[guaranteed_at]": payload["guaranteed_at"],
            "payment_method_details[type]": "custom",
            "payment_method_details[custom][display_name]": "Bank transfer",
            "processor_details[type]": "custom",
            "processor_details[custom][payment_reference]": payload["payment_reference"],
            "customer_details[customer]": _resolve_ref(
                request, refs, "customer_ref", "stripe.customer"
            ),
        }
    if request.action_key == "stripe.invoice-items.create":
        form = {
            "customer": _resolve_ref(request, refs, "customer_ref", "stripe.customer"),
            "invoice": _resolve_ref(request, refs, "invoice_ref", "stripe.invoice"),
            "amount": payload["amount"],
            "currency": payload["currency"],
            "description": payload["description"],
        }
        return form
    return {}


def _copy_fields(payload: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key in keys if (value := payload.get(key)) is not None}


def _resolve_ref(
    request: ActionConnectorRequest,
    refs: ProviderObjectReferenceRepository,
    key: str,
    object_type: str,
) -> str:
    assert request.credential is not None
    value = request.input_json.get(key)
    if not isinstance(value, str):
        raise ValidationError(f"{key} is required")
    return refs.resolve(
        credential=request.credential.credential,
        safe_ref=value,
        expected_object_type=object_type,
    ).provider_object_id


def _safe_response(
    body: Mapping[str, Any],
    *,
    spec: StripeActionSpec,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
    correlation_key: str | None = None,
) -> dict[str, Any]:
    if spec.list_item_type is not None:
        if body.get("object") != "list":
            raise ValidationError(
                "Stripe list response does not have the list object discriminator"
            )
        items = body.get("data")
        if not isinstance(items, list):
            raise ValidationError("Stripe list response is missing its data array")
        normalized = [
            _safe_object(
                item,
                spec.list_item_type,
                refs=refs,
                credential=credential,
                correlation_key=correlation_key,
            )
            for item in items
            if isinstance(item, Mapping)
        ]
        if len(normalized) != len(items):
            raise ValidationError("Stripe list response contains an invalid item")
        has_more = body.get("has_more")
        if not isinstance(has_more, bool):
            raise ValidationError("Stripe list response has an invalid has_more value")
        result: dict[str, Any] = {
            "items": normalized,
            "has_more": has_more,
        }
        if result["has_more"] and items:
            result["next_page_cursor"] = _object_ref_for_type(normalized[-1], spec.list_item_type)
        elif result["has_more"]:
            raise ValidationError("Stripe paginated response cannot have has_more without an item")
        return result
    if spec.object_type is not None:
        return _safe_object(
            body,
            spec.object_type,
            refs=refs,
            credential=credential,
            correlation_key=correlation_key,
        )
    # Balance has no provider id to turn into an opaque reference. Preserve only
    # the monetary availability buckets needed by cash-flow work.
    if body.get("object") == "balance":
        available = _safe_balance_amounts(body.get("available"), field="available")
        pending = _safe_balance_amounts(body.get("pending"), field="pending")
        livemode = body.get("livemode")
        if not isinstance(livemode, bool):
            raise ValidationError("Stripe balance response has an invalid livemode value")
        return {
            "available": available,
            "pending": pending,
            "livemode": livemode,
        }
    raise ValidationError("Stripe response did not match the reviewed action output")


def _malformed_response_error(
    *, write: bool, action_key: str | None = None, reason_code: str = "malformed_response"
) -> ActionConnectorError:
    """Make an unusable post-success response explicit about side-effect risk."""

    provider_error: dict[str, Any] = {
        "reason_code": reason_code,
        "outcome_unknown": write,
        "retry_safe": False,
    }
    if write and action_key == "stripe.payment-records.report":
        provider_error["recovery"] = (
            "Stripe returned a malformed response after report_payment. Inspect the existing "
            "action audit and response files first. Recover a known payment_record_ref through "
            "finance.stripe.payment-records.retrieve, then compare the exact externally retained "
            "payment_reference_sha256 and account/customer/money evidence. Independently retrieve "
            "and persist the verified ref before attachment. PaymentRecord listing is temporarily "
            "unavailable in StackOS; do not retry listing, change credentials, or guess a URL. "
            "A missing ref or uncertain evidence requires owner/provider recovery; never create "
            "a replacement report. Optional replay uses only the exact same Idempotency-Key "
            "and parameters "
            "within a verified safe window shorter than Stripe's 24-hour retention window."
        )
    elif write:
        provider_error["recovery"] = (
            "Stripe returned a malformed response after a POST. Reconcile with an authoritative "
            "retrieve/list action; do not issue a new mutation. If a retry is necessary, replay "
            "only the exact same Idempotency-Key while it remains inside Stripe's 24-hour "
            "retention window."
        )
    output_json = {
        "status": "failed",
        "outcome_unknown": write,
        "retry_safe": False,
        "provider_error": provider_error,
    }
    message = (
        "Stripe POST response could not be reconciled"
        if write
        else "Stripe returned an invalid response"
    )
    return ActionConnectorError(
        message,
        provider_error=provider_error,
        output_json=output_json,
    )


def _safe_object(
    value: Mapping[str, Any],
    object_type: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
    correlation_key: str | None = None,
) -> dict[str, Any]:
    identifier = value.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ValidationError("Stripe object response is missing id")
    expected_discriminator = {
        "stripe.customer": "customer",
        "stripe.invoice": "invoice",
        "stripe.invoice-item": "invoiceitem",
        "stripe.invoice-payment": "invoice_payment",
        "stripe.payment-intent": "payment_intent",
        "stripe.payment-record": "payment_record",
        "stripe.charge": "charge",
        "stripe.balance-transaction": "balance_transaction",
        "stripe.refund": "refund",
        "stripe.dispute": "dispute",
    }.get(object_type)
    if expected_discriminator is None or value.get("object") != expected_discriminator:
        raise ValidationError("Stripe response object does not match the reviewed action type")
    safe_ref = refs.upsert(
        credential=credential,
        object_type=object_type,
        provider_object_id=identifier,
        display_name="",
    )
    if object_type == "stripe.customer":
        return _safe_customer(value, safe_ref)
    if object_type == "stripe.invoice":
        return _safe_invoice(
            value, safe_ref, refs=refs, credential=credential, correlation_key=correlation_key
        )
    if object_type == "stripe.invoice-item":
        return _safe_invoice_item(value, safe_ref, refs=refs, credential=credential)
    if object_type == "stripe.invoice-payment":
        return _safe_invoice_payment(value, safe_ref, refs=refs, credential=credential)
    if object_type == "stripe.payment-intent":
        return _safe_payment_intent(value, safe_ref, refs=refs, credential=credential)
    if object_type == "stripe.payment-record":
        return _safe_payment_record(value, safe_ref, refs=refs, credential=credential)
    if object_type == "stripe.charge":
        return _safe_charge(value, safe_ref, refs=refs, credential=credential)
    if object_type == "stripe.balance-transaction":
        return _safe_balance_transaction(value, safe_ref, refs=refs, credential=credential)
    if object_type == "stripe.refund":
        return _safe_refund(value, safe_ref, refs=refs, credential=credential)
    if object_type == "stripe.dispute":
        return _safe_dispute(value, safe_ref, refs=refs, credential=credential)
    raise ValidationError(f"unsupported Stripe output object type {object_type!r}")


def _safe_customer(value: Mapping[str, Any], safe_ref: str) -> dict[str, Any]:
    result: dict[str, Any] = {"customer_ref": safe_ref}
    if "deleted" in value:
        if value["deleted"] is not True:
            raise ValidationError("Stripe deleted customer must have deleted=true")
        result["deleted"] = True
    else:
        result["created"] = _safe_nonnegative_integer(
            value.get("created"), field="customer.created"
        )
        result["livemode"] = _safe_boolean(value.get("livemode"), field="customer.livemode")
    result["email_sha256"] = (
        None if value.get("deleted") is True else _email_sha256(value.get("email"))
    )
    return result


def _email_sha256(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValidationError("Stripe customer email must be text or null")
    # Preserve exact provider bytes: no lowercase/trim/email canonicalization.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_invoice(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
    correlation_key: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"invoice_ref": safe_ref}
    for key in (
        "amount_due",
        "amount_paid",
        "amount_remaining",
        "total",
        "subtotal",
    ):
        result[key] = _safe_integer(value.get(key), field=f"invoice.{key}")
    result["amount_overpaid"] = _safe_nonnegative_integer(
        value.get("amount_overpaid"), field="invoice.amount_overpaid"
    )
    if "amount_paid_off_stripe" in value:
        result["amount_paid_off_stripe"] = _safe_nonnegative_integer(
            value["amount_paid_off_stripe"], field="invoice.amount_paid_off_stripe"
        )
    result["currency"] = _safe_currency(value.get("currency"), field="invoice.currency")
    if "status" in value:
        result["status"] = (
            None
            if value["status"] is None
            else _safe_enum(
                value["status"],
                {"draft", "open", "paid", "uncollectible", "void"},
                field="invoice.status",
            )
        )
    result["collection_method"] = _safe_enum(
        value.get("collection_method"),
        {"send_invoice", "charge_automatically"},
        field="invoice.collection_method",
    )
    result["auto_advance"] = _safe_boolean(value.get("auto_advance"), field="invoice.auto_advance")
    if "due_date" in value:
        due_date = value["due_date"]
        result["due_date"] = (
            None
            if due_date is None
            else _safe_nonnegative_integer(due_date, field="invoice.due_date")
        )
    result["created"] = _safe_nonnegative_integer(value.get("created"), field="invoice.created")
    result["livemode"] = _safe_boolean(value.get("livemode"), field="invoice.livemode")
    result["invoice_customer_email_sha256"] = _email_sha256(value.get("customer_email"))
    result["current_customer_email_sha256"] = None
    # Additional billing To/CC settings are not exposed by Stripe's API. These
    # observations must never be presented as complete recipient enumeration.
    result["recipient_scope"] = "primary-email-fields-only"
    if correlation_key is not None:
        metadata = value.get("metadata")
        result["correlation_matches"] = (
            isinstance(metadata, Mapping) and metadata.get("stackos_correlation") == correlation_key
        )
    customer = value.get("customer")
    if isinstance(customer, Mapping):
        projected_customer = _safe_object(
            customer, "stripe.customer", refs=refs, credential=credential
        )
        result["customer_ref"] = projected_customer["customer_ref"]
        result["current_customer_email_sha256"] = projected_customer["email_sha256"]
    else:
        if not isinstance(customer, str) or not customer:
            raise ValidationError("Stripe invoice customer must identify a customer")
        _add_nested_ref(
            result, value, "customer", "customer_ref", "stripe.customer", refs, credential
        )
    return result


def _safe_invoice_item(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"invoice_item_ref": safe_ref}
    result["amount"] = _safe_integer(value.get("amount"), field="invoice_item.amount")
    result["currency"] = _safe_currency(value.get("currency"), field="invoice_item.currency")
    result["date"] = _safe_nonnegative_integer(value.get("date"), field="invoice_item.date")
    for key in ("proration", "livemode"):
        result[key] = _safe_boolean(value.get(key), field=f"invoice_item.{key}")
    description = value.get("description")
    if description is not None and not isinstance(description, str):
        raise ValidationError("Stripe invoice item description must be text or null")
    result["description_sha256"] = (
        hashlib.sha256(description.encode("utf-8")).hexdigest()
        if isinstance(description, str)
        else None
    )
    if value.get("customer") is None:
        raise ValidationError("Stripe invoice item must identify its customer")
    _add_nested_ref(result, value, "customer", "customer_ref", "stripe.customer", refs, credential)
    _add_nested_ref(result, value, "invoice", "invoice_ref", "stripe.invoice", refs, credential)
    return result


def _safe_invoice_payment(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "invoice_payment_ref": safe_ref,
        "amount_requested": _safe_integer(
            value.get("amount_requested"), field="invoice_payment.amount_requested"
        ),
        "created": _safe_nonnegative_integer(value.get("created"), field="invoice_payment.created"),
        "currency": _safe_currency(value.get("currency"), field="invoice_payment.currency"),
        "is_default": _safe_boolean(value.get("is_default"), field="invoice_payment.is_default"),
        "livemode": _safe_boolean(value.get("livemode"), field="invoice_payment.livemode"),
    }
    if "amount_paid" in value:
        amount_paid = value["amount_paid"]
        result["amount_paid"] = (
            None
            if amount_paid is None
            else _safe_integer(amount_paid, field="invoice_payment.amount_paid")
        )
    status = _safe_nonempty_text(value.get("status"), field="invoice_payment.status")
    if status not in {"open", "paid", "canceled"}:
        raise ValidationError("Stripe invoice_payment status is unsupported")
    result["status"] = status
    status_transitions = value.get("status_transitions")
    if not isinstance(status_transitions, Mapping):
        raise ValidationError("Stripe invoice_payment status_transitions must be an object")
    result["status_transitions"] = {
        key: (
            None
            if status_transitions[key] is None
            else _safe_nonnegative_integer(
                status_transitions[key], field=f"invoice_payment.status_transitions.{key}"
            )
        )
        for key in ("canceled_at", "paid_at")
        if key in status_transitions
    }

    invoice = value.get("invoice")
    if not isinstance(invoice, str) or not invoice:
        raise ValidationError("Stripe invoice_payment invoice must be a provider object id")
    result["invoice_ref"] = refs.upsert(
        credential=credential,
        object_type="stripe.invoice",
        provider_object_id=invoice,
        display_name="",
    )
    payment = value.get("payment")
    if not isinstance(payment, Mapping):
        raise ValidationError("Stripe invoice_payment payment must be an object")
    payment_type = _safe_nonempty_text(payment.get("type"), field="invoice_payment.payment.type")
    payment_ref_key, payment_object_type = {
        "charge": ("charge_ref", "stripe.charge"),
        "payment_intent": ("payment_intent_ref", "stripe.payment-intent"),
        "payment_record": ("payment_record_ref", "stripe.payment-record"),
    }.get(payment_type, (None, None))
    if payment_ref_key is None or payment_object_type is None:
        raise ValidationError("Stripe invoice_payment payment type is unsupported")
    result["payment_type"] = payment_type
    if payment_type not in payment:
        result["payment_ref_state"] = "missing"
    else:
        _add_optional_provider_ref(
            result,
            provider_id=payment[payment_type],
            output_key=payment_ref_key,
            object_type=payment_object_type,
            refs=refs,
            credential=credential,
            field=f"invoice_payment.payment.{payment_type}",
        )
        result["payment_ref_state"] = "null" if payment[payment_type] is None else "available"
    return result


def _safe_payment_intent(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    """Project only settlement evidence needed by finance workflows.

    Payment method details, metadata, shipping data, receipt email, and the
    client secret are deliberately outside this provider action contract.
    """

    status = _safe_enum(
        value.get("status"),
        {
            "canceled",
            "processing",
            "requires_action",
            "requires_capture",
            "requires_confirmation",
            "requires_payment_method",
            "succeeded",
        },
        field="payment_intent.status",
    )
    created = _safe_nonnegative_integer(value.get("created"), field="payment_intent.created")
    livemode = _safe_boolean(value.get("livemode"), field="payment_intent.livemode")
    result: dict[str, Any] = {
        "payment_intent_ref": safe_ref,
        "status": status,
        "created": created,
        "livemode": livemode,
    }
    for key in ("amount", "amount_received"):
        if key in value:
            result[key] = _safe_nonnegative_integer(value.get(key), field=f"payment_intent.{key}")
    if "currency" in value:
        result["currency"] = _safe_currency(value.get("currency"), field="payment_intent.currency")
    if "customer" in value:
        _add_optional_nested_ref(
            result,
            value,
            "customer",
            "customer_ref",
            "stripe.customer",
            refs,
            credential,
        )
    if "latest_charge" in value:
        _add_optional_nested_ref(
            result,
            value,
            "latest_charge",
            "latest_charge_ref",
            "stripe.charge",
            refs,
            credential,
        )
    return result


def _safe_payment_record(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    """Project immutable Payment Record evidence without bank-reference PII."""

    result: dict[str, Any] = {"payment_record_ref": safe_ref}
    for key in (
        "amount",
        "amount_authorized",
        "amount_canceled",
        "amount_failed",
        "amount_guaranteed",
        "amount_refunded",
        "amount_requested",
    ):
        result[key] = _safe_payment_record_amount(value.get(key), field=f"payment_record.{key}")
    result["created"] = _safe_nonnegative_integer(
        value.get("created"), field="payment_record.created"
    )
    result["reported_by"] = _safe_enum(
        value.get("reported_by"), {"self", "stripe"}, field="payment_record.reported_by"
    )
    result["livemode"] = _safe_boolean(value.get("livemode"), field="payment_record.livemode")

    customer_details = value.get("customer_details")
    if customer_details is not None and not isinstance(customer_details, Mapping):
        raise ValidationError("Stripe payment_record customer_details must be an object or null")
    customer = customer_details.get("customer") if isinstance(customer_details, Mapping) else None
    # Unlike the expandable Customer links on invoices/charges, this embedded
    # PaymentRecord field is explicitly a nullable string in the pinned API.
    if customer is not None and (not isinstance(customer, str) or not customer):
        raise ValidationError(
            "Stripe payment_record customer_details.customer must be an id or null"
        )
    _add_optional_provider_ref(
        result,
        provider_id=customer,
        output_key="customer_ref",
        object_type="stripe.customer",
        refs=refs,
        credential=credential,
        field="payment_record.customer_details.customer",
    )

    processor_details = value.get("processor_details")
    if not isinstance(processor_details, Mapping):
        raise ValidationError("Stripe payment_record processor_details must be an object")
    result["processor_type"] = _safe_enum(
        processor_details.get("type"), {"custom"}, field="payment_record.processor_details.type"
    )
    custom = processor_details.get("custom")
    if custom is not None and not isinstance(custom, Mapping):
        raise ValidationError(
            "Stripe payment_record processor_details.custom must be an object or null"
        )
    payment_reference = custom.get("payment_reference") if isinstance(custom, Mapping) else None
    if payment_reference is not None and not isinstance(payment_reference, str):
        raise ValidationError(
            "Stripe payment_record processor_details.custom.payment_reference must be text or null"
        )
    result["payment_reference_sha256"] = (
        hashlib.sha256(payment_reference.encode("utf-8")).hexdigest()
        if isinstance(payment_reference, str)
        else None
    )
    return result


def _safe_nonnegative_integer(value: Any, *, field: str) -> int:
    integer = _safe_integer(value, field=field)
    if integer < 0:
        raise ValidationError(f"Stripe {field} must be a nonnegative integer")
    return integer


def _safe_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"Stripe {field} must be an integer")
    return value


def _safe_currency(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z]{3}", value) is None:
        raise ValidationError(f"Stripe {field} must be a lowercase ISO currency code")
    return value


def _safe_boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"Stripe {field} must be a boolean")
    return value


def _safe_nonempty_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Stripe {field} must be nonempty text")
    return value


def _safe_enum(value: Any, choices: set[str], *, field: str) -> str:
    text = _safe_nonempty_text(value, field=field)
    if text not in choices:
        raise ValidationError(f"Stripe {field} is unsupported")
    return text


def _safe_payment_record_amount(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"Stripe {field} must be a currency amount object")
    return {
        "currency": _safe_currency(value.get("currency"), field=f"{field}.currency"),
        "value": _safe_nonnegative_integer(value.get("value"), field=f"{field}.value"),
    }


def _safe_charge(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"charge_ref": safe_ref}
    for key in ("amount", "amount_captured", "amount_refunded"):
        result[key] = _safe_integer(value.get(key), field=f"charge.{key}")
    result["currency"] = _safe_currency(value.get("currency"), field="charge.currency")
    for key in ("paid", "refunded", "livemode"):
        result[key] = _safe_boolean(value.get(key), field=f"charge.{key}")
    result["status"] = _safe_enum(
        value.get("status"), {"failed", "pending", "succeeded"}, field="charge.status"
    )
    result["created"] = _safe_nonnegative_integer(value.get("created"), field="charge.created")
    _add_nested_ref(
        result,
        value,
        "balance_transaction",
        "balance_transaction_ref",
        "stripe.balance-transaction",
        refs,
        credential,
    )
    _add_nested_ref(
        result,
        value,
        "payment_intent",
        "payment_intent_ref",
        "stripe.payment-intent",
        refs,
        credential,
    )
    return result


def _safe_balance_transaction(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"balance_transaction_ref": safe_ref}
    for key in ("amount", "fee", "net"):
        result[key] = _safe_integer(value.get(key), field=f"balance_transaction.{key}")
    result["currency"] = _safe_currency(value.get("currency"), field="balance_transaction.currency")
    result["reporting_category"] = _safe_nonempty_text(
        value.get("reporting_category"), field="balance_transaction.reporting_category"
    )
    result["type"] = _safe_enum(
        value.get("type"),
        {
            "adjustment",
            "advance",
            "advance_funding",
            "anticipation_repayment",
            "application_fee",
            "application_fee_refund",
            "charge",
            "climate_order_purchase",
            "climate_order_refund",
            "connect_collection_transfer",
            "contribution",
            "fee_credit_funding",
            "inbound_transfer",
            "inbound_transfer_reversal",
            "issuing_authorization_hold",
            "issuing_authorization_release",
            "issuing_dispute",
            "issuing_transaction",
            "obligation_outbound",
            "obligation_reversal_inbound",
            "payment",
            "payment_failure_refund",
            "payment_network_reserve_hold",
            "payment_network_reserve_release",
            "payment_refund",
            "payment_reversal",
            "payment_unreconciled",
            "payout",
            "payout_cancel",
            "payout_failure",
            "payout_minimum_balance_hold",
            "payout_minimum_balance_release",
            "refund",
            "refund_failure",
            "reserve_hold",
            "reserve_release",
            "reserve_transaction",
            "reserved_funds",
            "stripe_balance_payment_debit",
            "stripe_balance_payment_debit_reversal",
            "stripe_fee",
            "stripe_fx_fee",
            "tax_fee",
            "tax_fund",
            "topup",
            "topup_reversal",
            "transfer",
            "transfer_cancel",
            "transfer_failure",
            "transfer_refund",
        },
        field="balance_transaction.type",
    )
    result["status"] = _safe_enum(
        value.get("status"), {"available", "pending"}, field="balance_transaction.status"
    )
    result["balance_type"] = _safe_enum(
        value.get("balance_type"),
        {"payments", "issuing", "refund_and_dispute_prefunding", "risk_reserved"},
        field="balance_transaction.balance_type",
    )
    for key in ("available_on", "created"):
        result[key] = _safe_nonnegative_integer(value.get(key), field=f"balance_transaction.{key}")
    source = value.get("source")
    if "source" not in value:
        result["source_state"] = "missing"
    elif source is None:
        result["source_state"] = "null"
    elif isinstance(source, str) and source:
        # Never infer an object kind from an id prefix or accounting type.
        result["source_state"] = "unexpanded"
    elif isinstance(source, Mapping):
        source_type = source.get("object")
        if source_type in {"charge", "refund", "dispute"}:
            projected = _safe_object(
                source, f"stripe.{source_type}", refs=refs, credential=credential
            )
            result["source_state"] = "available"
            result["source_type"] = source_type
            result["source_ref"] = projected[f"{source_type}_ref"]
        elif (
            isinstance(source_type, str)
            and source_type
            and isinstance(source.get("id"), str)
            and source["id"]
        ):
            result["source_state"] = "unsupported"
        else:
            raise ValidationError("Stripe balance transaction source is malformed")
    else:
        raise ValidationError("Stripe balance transaction source must be a provider object or null")
    return result


def _safe_refund(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"refund_ref": safe_ref}
    result["amount"] = _safe_integer(value.get("amount"), field="refund.amount")
    result["currency"] = _safe_currency(value.get("currency"), field="refund.currency")
    for key in ("status", "reason"):
        if key in value:
            choices = (
                {"pending", "requires_action", "succeeded", "failed", "canceled"}
                if key == "status"
                else {
                    "duplicate",
                    "expired_uncaptured_charge",
                    "fraudulent",
                    "requested_by_customer",
                }
            )
            result[key] = (
                None
                if value[key] is None
                else _safe_enum(value[key], choices, field=f"refund.{key}")
            )
    result["created"] = _safe_nonnegative_integer(value.get("created"), field="refund.created")
    _add_nested_ref(
        result,
        value,
        "failure_balance_transaction",
        "failure_balance_transaction_ref",
        "stripe.balance-transaction",
        refs,
        credential,
    )
    _add_nested_ref(result, value, "charge", "charge_ref", "stripe.charge", refs, credential)
    _add_nested_ref(
        result,
        value,
        "balance_transaction",
        "balance_transaction_ref",
        "stripe.balance-transaction",
        refs,
        credential,
    )
    _add_nested_ref(
        result,
        value,
        "payment_intent",
        "payment_intent_ref",
        "stripe.payment-intent",
        refs,
        credential,
    )
    return result


def _safe_dispute(
    value: Mapping[str, Any],
    safe_ref: str,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {"dispute_ref": safe_ref}
    result["amount"] = _safe_integer(value.get("amount"), field="dispute.amount")
    result["currency"] = _safe_currency(value.get("currency"), field="dispute.currency")
    result["status"] = _safe_enum(
        value.get("status"),
        {
            "lost",
            "needs_response",
            "prevented",
            "under_review",
            "warning_closed",
            "warning_needs_response",
            "warning_under_review",
            "won",
        },
        field="dispute.status",
    )
    result["reason"] = _safe_nonempty_text(value.get("reason"), field="dispute.reason")
    result["created"] = _safe_nonnegative_integer(value.get("created"), field="dispute.created")
    result["livemode"] = _safe_boolean(value.get("livemode"), field="dispute.livemode")
    if value.get("charge") is None:
        raise ValidationError("Stripe dispute must identify its charge")
    transactions = value.get("balance_transactions")
    if not isinstance(transactions, list) or any(
        not isinstance(item, Mapping) for item in transactions
    ):
        raise ValidationError("Stripe dispute balance_transactions must be an array of objects")
    result["balance_transaction_refs"] = [
        _safe_object(item, "stripe.balance-transaction", refs=refs, credential=credential)[
            "balance_transaction_ref"
        ]
        for item in transactions
    ]
    _add_nested_ref(result, value, "charge", "charge_ref", "stripe.charge", refs, credential)
    _add_nested_ref(
        result,
        value,
        "payment_intent",
        "payment_intent_ref",
        "stripe.payment-intent",
        refs,
        credential,
    )
    return result


def _add_nested_ref(
    result: dict[str, Any],
    value: Mapping[str, Any],
    source_key: str,
    output_key: str,
    object_type: str,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> None:
    if source_key in value:
        _add_optional_nested_ref(
            result, value, source_key, output_key, object_type, refs, credential
        )


def _add_optional_nested_ref(
    result: dict[str, Any],
    value: Mapping[str, Any],
    source_key: str,
    output_key: str,
    object_type: str,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
) -> None:
    _add_optional_provider_ref(
        result,
        provider_id=value.get(source_key),
        output_key=output_key,
        object_type=object_type,
        refs=refs,
        credential=credential,
        field=f"{source_key}",
    )


def _add_optional_provider_ref(
    result: dict[str, Any],
    *,
    provider_id: Any,
    output_key: str,
    object_type: str,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
    field: str,
) -> None:
    if provider_id is None:
        result[output_key] = None
        return
    if isinstance(provider_id, Mapping):
        projected = _safe_object(provider_id, object_type, refs=refs, credential=credential)
        result[output_key] = _object_ref_for_type(projected, object_type)
        return
    if not isinstance(provider_id, str) or not provider_id:
        raise ValidationError(f"Stripe {field} must be a provider object id or null")
    result[output_key] = refs.upsert(
        credential=credential,
        object_type=object_type,
        provider_object_id=provider_id,
        display_name="",
    )


def _safe_balance_amounts(value: Any, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValidationError(f"Stripe balance response {field} must be an array")
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValidationError(f"Stripe balance response {field} contains an invalid amount")
        amount = item.get("amount")
        currency = item.get("currency")
        entry: dict[str, Any] = {
            "amount": _safe_integer(amount, field=f"balance.{field}.amount"),
            "currency": _safe_currency(currency, field=f"balance.{field}.currency"),
        }
        if "source_types" in item:
            source_types = item["source_types"]
            if not isinstance(source_types, Mapping):
                raise ValidationError(
                    f"Stripe balance response {field} source_types must be an object"
                )
            normalized_source_types: dict[str, int] = {}
            for source_type, source_amount in source_types.items():
                if not isinstance(source_type, str) or not source_type:
                    raise ValidationError(
                        f"Stripe balance response {field} source_types contains an invalid key"
                    )
                normalized_source_types[source_type] = _safe_integer(
                    source_amount, field=f"balance.{field}.source_types.{source_type}"
                )
            entry["source_types"] = normalized_source_types
        normalized.append(entry)
    return normalized


def _object_ref_for_type(value: Mapping[str, Any], object_type: str) -> str:
    ref_key = {
        "stripe.customer": "customer_ref",
        "stripe.invoice": "invoice_ref",
        "stripe.invoice-item": "invoice_item_ref",
        "stripe.invoice-payment": "invoice_payment_ref",
        "stripe.payment-intent": "payment_intent_ref",
        "stripe.payment-record": "payment_record_ref",
        "stripe.charge": "charge_ref",
        "stripe.balance-transaction": "balance_transaction_ref",
        "stripe.refund": "refund_ref",
        "stripe.dispute": "dispute_ref",
    }.get(object_type)
    ref = value.get(ref_key) if ref_key is not None else None
    if not isinstance(ref, str) or not ref.startswith("provider-object:"):
        raise ValidationError("Stripe paginated response has no safe final object reference")
    return ref


def _connector_error(exc: IntegrationDownError | RateLimitedError) -> ActionConnectorError:
    data = exc.data if isinstance(exc.data, Mapping) else {}
    status = data.get("status")
    provider_status_code = status if isinstance(status, int) else None
    provider_error = data.get("provider_error")
    safe_error = dict(provider_error) if isinstance(provider_error, Mapping) else {}
    safe_error.setdefault(
        "reason_code",
        "rate_limited" if isinstance(exc, RateLimitedError) else "provider_failure",
    )
    safe_error["outcome_unknown"] = bool(data.get("outcome_unknown"))
    safe_error["retry_safe"] = bool(data.get("retry_safe"))
    if isinstance(data.get("retry_after"), int | float):
        safe_error["retry_after"] = data["retry_after"]
    if isinstance(data.get("recovery"), str):
        safe_error["recovery"] = data["recovery"][:500]
    output_json = {
        "status": "failed",
        "outcome_unknown": bool(data.get("outcome_unknown")),
        "retry_safe": bool(data.get("retry_safe")),
    }
    if provider_status_code is not None:
        output_json["provider_status_code"] = provider_status_code
    if safe_error:
        output_json["provider_error"] = redact_secrets(safe_error)
    return ActionConnectorError(
        "Stripe action failed",
        provider_status_code=provider_status_code,
        provider_error=redact_secrets(safe_error),
        output_json=output_json,
    )


__all__ = [
    "STRIPE_ACTION_SPECS",
    "STRIPE_DEFAULT_LIMIT",
    "STRIPE_MAX_LIMIT",
    "STRIPE_OPERATION",
    "StripeActionConnector",
]
