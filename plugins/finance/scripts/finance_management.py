#!/usr/bin/env python3
"""Finance-plugin management of an explicitly authorized external v1 master.

No provider execution or inferred financial treatment. Daemon adapters establish
root authority; host callers must have their own filesystem authority. Financial
method metadata lives in existing versioned operating_settings, never a ledger.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jsonschema import FormatChecker
from jsonschema.exceptions import ValidationError as SchemaValidationError

if __package__:
    from . import finance_views as views
    from . import finance_workspace as workspace
    from .finance_records import CurrentRecords
else:
    import finance_views as views
    import finance_workspace as workspace
    from finance_records import CurrentRecords

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
RECORD_SCOPE = "finance-management-record:"
PRECISION_SCOPE = "finance-management-precision:"
POLICY_SCOPE = "finance-management-reporting"
KINDS = {"bookkeeping", "receipts", "billing_versions"}
EFFECTS = {
    "revenue": (1, 0),
    "revenue_refund": (-1, 0),
    "expense": (0, 1),
    "fee": (0, 1),
    "expense_refund": (0, -1),
}


class ManagementError(ValueError):
    def __init__(self, code: str, message: str | None = None, **data: Any):
        self.code, self.data = code, data
        super().__init__(message or code.replace("_", " "))


class OutcomeUnknown(ManagementError):
    def __init__(self, request_id: str):
        self.receipt = {
            "request_id": request_id,
            "outcome": "unknown",
            "next_action": "reconcile_same_request",
        }
        super().__init__("outcome_unknown", **self.receipt)


def _fail(code: str, message: str | None = None) -> None:
    raise ManagementError(code, message)


def _json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _key(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", value):
        _fail("invalid_request_id")
    return value


def _master(path: Path) -> Path:
    path = workspace.checked_path(Path(path))
    if path.name != "finance.json":
        _fail("invalid_master")
    return path


def _schema(value: Any, name: str) -> None:
    schema = workspace.decode_json(workspace.read_regular(SCHEMAS / name))
    if list(workspace.FinanceValidator(schema, format_checker=FormatChecker()).iter_errors(value)):
        _fail("unsupported_metadata" if name == "management-v1.schema.json" else "invalid_result")


def read_snapshot(path: Path) -> dict[str, Any]:
    """Stable validated snapshot; missing originals are inspectable custody gaps.

    Existing writer/read_document retain strict original-custody verification.
    This read creates no files, lock, metadata, or default business policy.
    """
    path = _master(path)
    try:
        raw = workspace.read_regular(path)
        document = workspace.decode_json(raw)
        if document.get("schema_version") != "local-json-v1":
            _fail("unsupported_version")
        workspace.validate(document, schema_path=path.parent / "schemas/finance-v1.schema.json")
        issues = []
        for attachment in document["attachments"]:
            try:
                checked = workspace.verify_originals({"attachments": [attachment]}, path.parent)
                issues.extend(checked["unavailable_attachments"])
            except (ValueError, OSError):
                issues.append({"attachment_ref": attachment["record_id"], "state": "unavailable"})
        if workspace.read_regular(path) != raw:
            _fail("stale_snapshot")
    except ManagementError:
        raise
    except FileNotFoundError:
        _fail("not_configured")
    except PermissionError:
        _fail("unreadable")
    except (ValueError, OSError, SchemaValidationError):
        _fail("corrupt_workspace")
    return {
        "document": document,
        "revision": document["revision"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema_version": document["schema_version"],
        "custody_issues": issues,
        "read_at": _now(),
    }


def _safe_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        k: snapshot[k]
        for k in ("revision", "sha256", "schema_version", "read_at", "custody_issues")
        if k in snapshot
    }


def _matches(snapshot: dict[str, Any], expected: dict[str, Any]) -> None:
    if expected != {"revision": snapshot["revision"], "sha256": snapshot["sha256"]}:
        _fail("stale_snapshot")


def _approved(
    current: CurrentRecords,
    setting: dict[str, Any],
    scopes: tuple[str, ...] = ("finance-management",),
) -> bool:
    if (
        setting["status"] != "confirmed"
        or not setting.get("provenance")
        or current.resolve(setting["record_id"]) != setting["record_id"]
    ):
        return False
    for ref in setting.get("decision_refs", []):
        review = current.records.get(ref, {})
        if current.collections.get(ref) != "reviews" or current.resolve(ref) != ref:
            continue
        if (
            review.get("target_ref") == setting["record_id"]
            and review.get("target_version") == setting.get("version")
            and review.get("target_digest_sha256")
            == workspace.material_record_digest(setting, "operating_settings")
            and review.get("verdict") in {"approved", "adopted"}
            and review.get("authority") in {"operator", "owner"}
            and review.get("actor")
            and set(scopes).issubset(review.get("scope", []))
        ):
            return True
    return False


def _method(
    current: CurrentRecords,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    settings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in current.active("operating_settings"):
        scope = row.get("scope", "")
        if scope == POLICY_SCOPE or scope.startswith((RECORD_SCOPE, PRECISION_SCOPE)):
            settings[scope].append(row)
    return settings, settings.get(POLICY_SCOPE, [])


def _payload(current: CurrentRecords, setting: dict[str, Any]) -> dict[str, Any]:
    payload = workspace.decode_json(setting.get("policy_text", ""))
    _schema(payload, "management-v1.schema.json")
    if not _approved(current, setting):
        _fail("unapproved_metadata")
    return payload


def _precision(
    current: CurrentRecords,
    settings: dict[str, list[dict[str, Any]]],
    record: dict[str, Any],
    reporting: dict[str, Any] | None,
) -> tuple[int | None, str | None]:
    """Resolve independently reviewed display units; never adopt economic treatment."""
    ref = record["record_id"]
    candidates = settings.get(PRECISION_SCOPE + ref, [])
    if not candidates:
        ancestor = ref
        while ancestor in current.previous:
            ancestor = current.previous[ancestor]
            if settings.get(PRECISION_SCOPE + ancestor):
                return None, "stale_precision"
        return (reporting["currency_exponent"] if reporting else None), None
    if len(candidates) != 1:
        return None, "precision_conflict"
    try:
        setting = candidates[0]
        payload = _payload(current, setting)
        if not _approved(current, setting, ("finance-management", "amount-precision")):
            return None, "unapproved_precision"
        if (
            payload.get("kind") != "record-precision"
            or payload["target_ref"] != ref
            or payload["target_digest_sha256"]
            != workspace.material_record_digest(record, current.collections[ref])
            or payload["currency"] != record.get("amount", {}).get("currency")
        ):
            return None, "stale_precision"
        sources = record.get(
            "source_refs", [record["source_ref"]] if "source_ref" in record else []
        )
        for source in [*sources, *payload["source_refs"]]:
            if current.collections.get(source) != "sources":
                return None, "unknown_precision_source"
            if current.resolve(source) != source or current.records[source]["status"] in {
                "superseded",
                "rejected",
                "withdrawn",
            }:
                return None, "stale_precision_source"
        if reporting and reporting["currency_exponent"] != payload["exponent"]:
            return None, "currency_precision_conflict"
        return payload["exponent"], None
    except (ValueError, KeyError, TypeError):
        return None, "unsupported_or_unapproved_precision"


def _display_amount(
    current: CurrentRecords,
    settings: dict[str, list[dict[str, Any]]],
    record: dict[str, Any],
    reporting: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    amount = record.get("amount", record.get("total"))
    if not amount:
        return None, None
    exponent, issue = _precision(current, settings, record, reporting)
    return _money(amount["amount_minor"], amount["currency"], exponent), issue


def normalize_query(filters: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "start",
        "end",
        "comparison",
        "recognition_basis",
        "date_basis",
        "timezone",
        "currency",
        "scope",
        "external_project_ref",
        "kind",
        "account_ref",
        "category_ref",
        "counterparty_ref",
        "needs_review",
        "q",
        "sort",
        "descending",
        "offset",
        "limit",
        "expected_snapshot",
        "workspace_ref",
        "access_generation",
    }
    if not isinstance(filters, dict) or set(filters) - allowed:
        _fail("invalid_query")
    q = {
        "scope": "business",
        "kind": "all",
        "sort": "date",
        "descending": False,
        "offset": 0,
        "limit": 50,
        **copy.deepcopy(filters),
    }
    try:
        start, end = date.fromisoformat(q["start"]), date.fromisoformat(q["end"])
        ZoneInfo(q["timezone"])
        if (
            start > end
            or q["recognition_basis"] not in {"cash", "accrual"}
            or q["date_basis"] not in {"transaction", "posting", "issue", "cash"}
        ):
            raise ValueError
        if q["scope"] not in {"business", "external-project"} or (
            q["scope"] == "external-project" and not q.get("external_project_ref")
        ):
            raise ValueError
        if q["kind"] not in {"all", "expense", "income", "bill", "receipt"} or q["sort"] not in {
            "date",
            "amount",
            "counterparty",
            "kind",
            "review",
        }:
            raise ValueError
        if (
            type(q["offset"]) is not int
            or q["offset"] < 0
            or type(q["limit"]) is not int
            or not 1 <= q["limit"] <= 200
        ):
            raise ValueError
        if not isinstance(q.get("q", ""), str) or len(q.get("q", "")) > 240:
            raise ValueError
        if q.get("currency") and not re.fullmatch(r"[A-Z]{3}", q["currency"]):
            raise ValueError
    except (KeyError, ValueError, TypeError) as error:
        # ZoneInfoNotFoundError is a KeyError. No raw input in diagnostics.
        if isinstance(error, ManagementError):
            raise
        _fail("invalid_query")
    if q.get("comparison"):
        comparison = q["comparison"]
        normalize_query(
            {**q, "start": comparison["start"], "end": comparison["end"], "comparison": None}
        )
    return q


def comparison_window(start: str, end: str, mode: str) -> dict[str, Any]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        _fail("invalid_query")
    if mode == "previous-period":
        return {
            "start": (first - timedelta(days=(last - first).days + 1)).isoformat(),
            "end": (first - timedelta(days=1)).isoformat(),
        }
    if mode == "previous-year":
        clamped = False

        def prior(day):
            nonlocal clamped
            try:
                return day.replace(year=day.year - 1)
            except ValueError:
                clamped = True
                return day.replace(year=day.year - 1, day=28)

        a, b = prior(first), prior(last)
        return {"start": a.isoformat(), "end": b.isoformat(), "clamped": clamped}
    _fail("invalid_comparison")


def _reporting(
    current: CurrentRecords,
    settings: dict[str, list[dict[str, Any]]],
    row: dict[str, Any],
    q: dict[str, Any],
    *,
    check_basis: bool = True,
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = settings.get(RECORD_SCOPE + row["record_id"], [])
    if not candidates:
        # A superseded target's metadata is stale, not silently inherited.
        lineage = current.root(row["record_id"])
        return None, "stale_metadata" if settings.get(
            RECORD_SCOPE + lineage
        ) else "missing_metadata"
    if len(candidates) != 1:
        return None, "metadata_conflict"
    try:
        payload = _payload(current, candidates[0])
        if (
            payload.get("kind") != "record-reporting"
            or payload.get("target_ref") != row["record_id"]
            or payload["target_digest_sha256"]
            != workspace.material_record_digest(row, "bookkeeping")
        ):
            return None, "stale_metadata"
        reporting = payload["reporting"]
        dependencies = [
            (ref, "sources") for ref in [*row["source_refs"], *reporting["source_refs"]]
        ]
        dependencies.extend((ref, "receipts") for ref in row.get("receipt_refs", []))
        for ref, collection in dependencies:
            if current.collections.get(ref) != collection:
                return None, "unknown_source"
            if current.resolve(ref) != ref or current.records[ref]["status"] in {
                "superseded",
                "rejected",
                "withdrawn",
            }:
                return None, "stale_dependency"
            if collection == "receipts":
                source = current.records[ref]["source_ref"]
                if current.resolve(source) != source:
                    return None, "stale_dependency"
        if any(
            current.collections.get(ref) != "sources" or current.resolve(ref) != ref
            for ref in reporting["source_refs"]
        ):
            return None, "unknown_source"
        if row["amount"]["amount_minor"] < 0:
            return None, "negative_magnitude"
        if check_basis and (
            reporting["recognition_basis"] != q["recognition_basis"]
            or reporting["date_basis"] != q["date_basis"]
        ):
            return None, "basis_mismatch"
        if reporting.get("policy_ref"):
            policy = current.records.get(reporting["policy_ref"], {})
            if current.collections.get(
                reporting["policy_ref"]
            ) != "operating_settings" or not _approved(current, policy):
                return None, "policy_conflict"
        if reporting["treatment"] == "unknown":
            return None, "unknown_treatment"
        return reporting, None
    except (ValueError, KeyError, TypeError):
        return None, "unsupported_or_unapproved_metadata"


def _coverage(
    current: CurrentRecords, policies: list[dict[str, Any]], q: dict[str, Any]
) -> dict[str, Any]:
    issues, policy = [], None
    if len(policies) != 1:
        issues.append("missing_or_conflicting_reporting_policy")
    else:
        try:
            policy = _payload(current, policies[0])
            if policy.get("kind") != "reporting-policy" or not _approved(
                current, policies[0], ("finance-management", "coverage", "revenue", "expenses")
            ):
                issues.append("unapproved_coverage")
            if policy.get("coverage_review_ref") not in policies[0].get("decision_refs", []):
                issues.append("unapproved_coverage")
            if any(policy.get(k) != q[k] for k in ("recognition_basis", "date_basis", "timezone")):
                issues.append("policy_conflict")
            if (
                policies[0].get("effective_from", "0001-01-01") > q["start"]
                or policies[0].get("effective_to", "9999-12-31") < q["end"]
            ):
                issues.append("policy_conflict")
            category_policy_ref = policy.get("category_policy_ref")
            if category_policy_ref and (
                current.collections.get(category_policy_ref) != "operating_settings"
                or not _approved(current, current.records[category_policy_ref], ())
            ):
                issues.append("policy_conflict")
            precision_rows = policy.get("currency_precision", [])
            if len({x["currency"] for x in precision_rows}) != len(precision_rows) or any(
                current.collections.get(x["source_ref"]) != "sources" for x in precision_rows
            ):
                issues.append("policy_conflict")
            accounts = policy.get("scope_accounts", [])
            if not accounts:
                issues.append("missing_account_scope")
            mapping = current.account_map()
            for account in accounts:
                identity = mapping.get(account)
                sources = (
                    [
                        x
                        for x in current.active("sources")
                        if mapping.get(x.get("account_ref")) == identity
                    ]
                    if identity
                    else []
                )
                if not identity or not any(
                    x.get("coverage_state") == "complete"
                    and x.get("available_window", {}).get("start", "9999") <= q["start"]
                    and x.get("available_window", {}).get("end", "0000") >= q["end"]
                    and not x.get("gaps")
                    for x in sources
                ):
                    issues.append("incomplete_account_window")
        except (ValueError, KeyError, TypeError):
            issues.append("unsupported_or_unapproved_policy")
    for profile in current.active("business_profiles"):
        basis = profile.get("accounting_basis")
        if basis and (
            not basis.get("source_refs")
            or any(current.collections.get(ref) != "sources" for ref in basis["source_refs"])
            or basis["value"] != q["recognition_basis"]
        ):
            issues.append("policy_conflict")
    return {
        "state": "complete"
        if not issues and q["scope"] == "business"
        else "partial"
        if policy
        else "unavailable",
        "issues": sorted(set(issues)),
        "policy": policy,
    }


def _money(value: int, currency: str, exponent: int | None) -> dict[str, Any]:
    return {"minor": str(value), "currency": currency, "exponent": exponent}


def query(snapshot: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
    """One filtered population supplies rows, totals, groups, trends and reports."""
    q = normalize_query(filters)
    if q.get("expected_snapshot"):
        _matches(snapshot, q["expected_snapshot"])
    d = snapshot["document"]
    current = CurrentRecords(d)
    settings, policies = _method(current)
    coverage = _coverage(current, policies, q)
    # Economic ambiguity is a property of the current master, not the visible
    # subset. Basis stays in the key; presentation dates/accounts/projects never
    # revive one representation of a globally conflicting event/component.
    economic_groups = defaultdict(list)
    for record in current.active("bookkeeping"):
        reporting, _reason = _reporting(current, settings, record, q, check_basis=False)
        if reporting is not None:
            economic_groups[
                (
                    reporting["recognition_basis"],
                    reporting["economic_event_ref"],
                    reporting["component_ref"],
                )
            ].append(record["record_id"])
    conflicts = {ref for group in economic_groups.values() if len(group) > 1 for ref in group}
    rows, excluded, undated, recognized = [], [], [], []
    unavailable = {x["attachment_ref"] for x in snapshot.get("custody_issues", [])}

    def exclusion(ref, reason):
        excluded.append({"record_ref": ref, "reason": reason})

    for collection in sorted(KINDS):
        for record in current.active(collection):
            ref = record["record_id"]
            reporting, reason = (None, None)
            if collection == "bookkeeping":
                reporting, reason = _reporting(current, settings, record, q)
                if reporting is not None and ref in conflicts:
                    reason = "economic_identity_conflict"
            business_date = (
                record.get("date") if collection == "bookkeeping" else record.get("business_date")
            )
            amount = record.get("amount") or (
                record.get("total") if collection == "billing_versions" else None
            )
            treatment = reporting.get("treatment") if reporting else None
            kind = (
                "receipt"
                if collection == "receipts"
                else "bill"
                if collection == "billing_versions"
                else "income"
                if treatment in {"revenue", "revenue_refund"}
                else "expense"
                if treatment in {"expense", "fee", "expense_refund"}
                else "bookkeeping"
            )
            if (
                q["scope"] == "external-project"
                and record.get("external_project_ref") != q["external_project_ref"]
            ):
                if collection == "bookkeeping":
                    exclusion(ref, "unallocated_or_other_project")
                continue
            # Undated bills/receipts remain visible for inspection, in a distinct lane.
            is_undated = not business_date
            if not is_undated and not q["start"] <= business_date <= q["end"]:
                continue
            if q["kind"] != "all" and q["kind"] != kind:
                continue
            if q.get("currency") and (not amount or amount["currency"] != q["currency"]):
                continue
            category = record.get("category", {})
            for key in ("account_ref", "category_ref", "counterparty_ref"):
                value = (
                    category.get(key)
                    if key == "category_ref"
                    else reporting.get(key)
                    if key == "counterparty_ref" and reporting
                    else record.get(key)
                )
                if q.get(key) and value != q[key]:
                    break
            else:
                evidence_refs = list(record.get("attachment_refs", []))
                source_refs = record.get(
                    "source_refs", [record["source_ref"]] if "source_ref" in record else []
                )
                for source_ref in source_refs:
                    evidence_refs.extend(
                        current.records.get(source_ref, {}).get("attachment_refs", [])
                    )
                if unavailable.intersection(evidence_refs):
                    reason = "unavailable_evidence"
                if "policy_conflict" in coverage["issues"] and collection == "bookkeeping":
                    reason = "policy_conflict"
                if is_undated:
                    reason = reason or "undated_or_period_only"
                display_amount, precision_issue = _display_amount(
                    current, settings, record, reporting
                )
                reason = reason or precision_issue
                review = bool(
                    reason
                    or record.get("gaps")
                    or category.get("state") in {"unresolved", "proposed"}
                )
                row = {
                    "record_ref": ref,
                    "collection": collection,
                    "kind": kind,
                    "date": business_date,
                    "amount": display_amount,
                    "status": record["status"],
                    "business_purpose": record.get("business_purpose", ""),
                    "counterparty": reporting.get("counterparty_label", "")
                    if reporting
                    else record.get("merchant", ""),
                    "counterparty_ref": reporting.get("counterparty_ref") if reporting else None,
                    "account_ref": record.get("account_ref"),
                    "category_ref": category.get("category_ref"),
                    "category_state": category.get("state"),
                    "external_project_ref": record.get("external_project_ref"),
                    "source_refs": source_refs,
                    "evidence_refs": sorted(set(evidence_refs)),
                    "needs_review": review,
                    "gaps": [
                        *copy.deepcopy(record.get("gaps", [])),
                        *(
                            [
                                {
                                    "field": "amount.exponent",
                                    "reason": precision_issue,
                                    "source_refs": source_refs,
                                }
                            ]
                            if precision_issue
                            else []
                        ),
                    ],
                    "reporting": reporting,
                    "metric_exclusion": reason,
                }
                if q.get("needs_review") and not review:
                    continue
                identities = [
                    {
                        k: current.records[linked].get(k)
                        for k in ("source_identity", "source_kind", "original_filename")
                    }
                    for linked in [*source_refs, *evidence_refs]
                    if linked in current.records
                ]
                if q.get("q") and q["q"].casefold() not in _json([row, identities]).casefold():
                    continue
                rows.append(row)
                if is_undated:
                    undated.append(ref)
                if collection == "bookkeeping":
                    if reason:
                        exclusion(ref, reason)
                    elif reporting:
                        recognized.append(row)
                continue
            continue
    exponents = defaultdict(set)
    for row in recognized:
        exponents[row["amount"]["currency"]].add(row["amount"]["exponent"])
    for row in recognized:
        if len(exponents[row["amount"]["currency"]]) != 1:
            exclusion(row["record_ref"], "currency_precision_conflict")
            conflicts.add(row["record_ref"])
    policy = coverage.pop("policy")
    precision_by_currency = {
        x["currency"]: x["exponent"] for x in (policy or {}).get("currency_precision", [])
    }
    for row in recognized:
        expected_precision = precision_by_currency.get(row["amount"]["currency"])
        if expected_precision is not None and row["amount"]["exponent"] != expected_precision:
            exclusion(row["record_ref"], "currency_precision_conflict")
            conflicts.add(row["record_ref"])
    recognized = [r for r in recognized if r["record_ref"] not in conflicts]
    exclusion_by_ref = {x["record_ref"]: x["reason"] for x in excluded}
    for row in rows:
        if row["record_ref"] in exclusion_by_ref:
            row["metric_exclusion"] = exclusion_by_ref[row["record_ref"]]
            row["needs_review"] = True
    account_map = current.account_map()
    approved_accounts = set((policy or {}).get("scope_accounts", []))
    unscoped_accounts = any(
        row["collection"] == "bookkeeping"
        and (
            row["account_ref"] not in account_map
            or account_map[row["account_ref"]]["record_id"] not in approved_accounts
        )
        for row in rows
    )
    if excluded or any(r["gaps"] for r in rows) or unscoped_accounts:
        coverage["state"] = "partial" if policy else "unavailable"
    if q["scope"] == "external-project":
        coverage["issues"].append("project_contribution_before_unallocated_costs")
    metrics = []
    currencies = sorted(
        {r["amount"]["currency"] for r in rows if r["amount"]}
        | {
            x["currency"]
            for x in (policy or {}).get("currency_precision", [])
            if not q.get("currency") or x["currency"] == q["currency"]
        }
    )
    for currency in currencies:
        population = [r for r in recognized if r["amount"]["currency"] == currency]
        precision = next(
            (
                x["exponent"]
                for x in (policy or {}).get("currency_precision", [])
                if x["currency"] == currency
            ),
            None,
        )
        exponent = population[0]["amount"]["exponent"] if population else precision

        def metric(axis, population=population, exponent=exponent, currency=currency):
            selected = [
                r for r in population if EFFECTS.get(r["reporting"]["treatment"], (0, 0))[axis]
            ]
            value = sum(
                int(r["amount"]["minor"]) * EFFECTS[r["reporting"]["treatment"]][axis]
                for r in selected
            )
            known = bool(selected) or (coverage["state"] == "complete" and exponent is not None)
            return {
                "amount": _money(value, currency, exponent) if known else None,
                "state": "complete"
                if known and coverage["state"] == "complete"
                else "partial"
                if known
                else "unavailable",
                "included_record_refs": [r["record_ref"] for r in selected],
                "excluded_record_refs": [x["record_ref"] for x in excluded],
                "basis": q["recognition_basis"],
                "date_basis": q["date_basis"],
                "scope": q["scope"],
            }

        revenue, spend = metric(0), metric(1)
        known = revenue["amount"] is not None and spend["amount"] is not None
        profit = {
            **revenue,
            "amount": _money(
                int(revenue["amount"]["minor"]) - int(spend["amount"]["minor"]), currency, exponent
            )
            if known
            else None,
            "state": "complete"
            if known and coverage["state"] == "complete"
            else "partial"
            if known
            else "unavailable",
            "included_record_refs": sorted(
                set(revenue["included_record_refs"] + spend["included_record_refs"])
            ),
        }
        tax = [r for r in population if r["reporting"]["treatment"] == "tax_payment"]
        metrics.append(
            {
                "currency": currency,
                "exponent": exponent,
                "revenue": revenue,
                "spend": spend,
                "profit": profit,
                "tax_payments": {
                    "amount": _money(
                        sum(int(r["amount"]["minor"]) for r in tax), currency, exponent
                    )
                    if tax
                    else None,
                    "included_record_refs": [r["record_ref"] for r in tax],
                },
            }
        )
    breakdowns = {}
    for dimension in ("category", "account", "counterparty", "project"):
        groups = defaultdict(list)
        field = {
            "category": "category_ref",
            "account": "account_ref",
            "counterparty": "counterparty_ref",
            "project": "external_project_ref",
        }[dimension]
        for row in recognized:
            if row["reporting"]["treatment"] in EFFECTS:
                groups[(row.get(field) or "Unknown/unassigned", row["amount"]["currency"])].append(
                    row
                )
        breakdowns[dimension] = [
            {
                "label": label,
                "currency": currency,
                "included_record_refs": [r["record_ref"] for r in group],
                "revenue_minor": str(
                    sum(
                        int(r["amount"]["minor"]) * EFFECTS[r["reporting"]["treatment"]][0]
                        for r in group
                    )
                ),
                "spend_minor": str(
                    sum(
                        int(r["amount"]["minor"]) * EFFECTS[r["reporting"]["treatment"]][1]
                        for r in group
                    )
                ),
            }
            for (label, currency), group in sorted(groups.items())
        ]
    trend = []
    for day in sorted({r["date"] for r in recognized}):
        for currency in currencies:
            group = [
                r
                for r in recognized
                if r["date"] == day
                and r["amount"]["currency"] == currency
                and r["reporting"]["treatment"] in EFFECTS
            ]
            if group:
                trend.append(
                    {
                        "date": day,
                        "currency": currency,
                        "included_record_refs": [r["record_ref"] for r in group],
                        "revenue_minor": str(
                            sum(
                                int(r["amount"]["minor"]) * EFFECTS[r["reporting"]["treatment"]][0]
                                for r in group
                            )
                        ),
                        "spend_minor": str(
                            sum(
                                int(r["amount"]["minor"]) * EFFECTS[r["reporting"]["treatment"]][1]
                                for r in group
                            )
                        ),
                    }
                )
    rows.sort(key=lambda r: r["record_ref"])
    key = q["sort"]
    rows.sort(
        key=lambda r: (
            (r["amount"]["currency"], int(r["amount"]["minor"]))
            if key == "amount" and r["amount"]
            else ("", 0)
            if key == "amount"
            else str(
                r.get({"counterparty": "counterparty", "review": "needs_review"}.get(key, key))
                or ""
            )
        ),
        reverse=q["descending"],
    )
    issues = [
        {
            "record_ref": r["record_ref"],
            "reason": r["metric_exclusion"] or "classification_or_evidence_gap",
            "next_action": "inspect_source_then_correct",
        }
        for r in rows
        if r["needs_review"]
    ]
    issues.extend(
        {
            "record_ref": x["record_id"],
            "reason": x.get("kind", "exception"),
            "affected_refs": x.get("affected_refs", []),
            "next_action": "inspect_source_then_correct",
        }
        for x in current.active("exceptions")
        if x["status"] != "resolved"
    )
    result = {
        "snapshot": _safe_snapshot(snapshot),
        "query": q,
        "rows": rows[q["offset"] : q["offset"] + q["limit"]],
        "page": {
            "total": len(rows),
            "offset": q["offset"],
            "limit": q["limit"],
            "has_more": q["offset"] + q["limit"] < len(rows),
        },
        "metrics_by_currency": metrics,
        "breakdowns": breakdowns,
        "trend": trend,
        "review_items": issues,
        "coverage": coverage,
        "exclusions": excluded,
        "undated_or_period_only": undated,
        "warnings": ["prepared/unposted; supported operating result, not posted books"],
    }
    if q.get("comparison"):
        window = q["comparison"]
        comparison = query(
            snapshot,
            {**q, "start": window["start"], "end": window["end"], "comparison": None},
        )
        result["comparison"] = {
            "query": comparison["query"],
            "metrics_by_currency": comparison["metrics_by_currency"],
            "coverage": comparison["coverage"],
        }
    return result


def record_detail(snapshot: dict[str, Any], ref: str) -> dict[str, Any]:
    current = CurrentRecords(snapshot["document"])
    if ref not in current.records:
        _fail("unknown_record")
    resolved = current.resolve(ref)
    row = current.records[resolved]
    collection = current.collections[resolved]
    sources = row.get("source_refs", [row["source_ref"]] if "source_ref" in row else [])
    attachments = set(row.get("attachment_refs", []))
    for source in sources:
        attachments.update(current.records.get(source, {}).get("attachment_refs", []))
    issues = {x["attachment_ref"]: x["state"] for x in snapshot.get("custody_issues", [])}
    settings, _policies = _method(current)
    reporting, _reporting_issue = (
        _reporting(current, settings, row, {}, check_basis=False)
        if collection == "bookkeeping"
        else (None, None)
    )
    display_amount, _precision_issue = _display_amount(current, settings, row, reporting)
    lineage = current.lineage(ref)
    history = []
    for edge_ref in lineage:
        correction = current.records[edge_ref]
        if current.collections[edge_ref] != "corrections" or correction["status"] != "applied":
            continue
        events = [e for e in correction["status_history"] if e["to_status"] == "applied"]
        event = events[-1] if events else {}
        history.append(
            {
                "correction_ref": edge_ref,
                "status": "applied",
                "superseded_ref": correction["superseded_ref"],
                "replacement_ref": correction["replacement_ref"],
                "reason": correction["reason"],
                "actor": event.get("actor"),
                "at": event.get("at"),
                "decision_ref": correction.get("decision_ref"),
                "evidence_refs": copy.deepcopy(event.get("evidence_refs", [])),
                "source_refs": list(
                    dict.fromkeys(
                        p["ref"]
                        for p in correction["provenance"]
                        if p["kind"] == "correction-evidence"
                        and current.collections.get(p["ref"]) == "sources"
                    )
                ),
            }
        )
    return {
        "requested_ref": ref,
        "current_ref": resolved,
        "collection": collection,
        "record": copy.deepcopy(row),
        "amount": display_amount,
        "original": copy.deepcopy(current.records[current.root(ref)]),
        "lineage": lineage,
        "correction_history": history,
        "source_refs": sources,
        "evidence": [
            {
                "attachment_ref": a,
                "state": issues.get(a, current.records.get(a, {}).get("status", "unavailable")),
                "sha256": current.records.get(a, {}).get("sha256"),
            }
            for a in sorted(attachments)
        ],
        "editable_fields": [
            "date",
            "amount",
            "category",
            "business_purpose",
            "external_project_ref",
            "reporting",
        ]
        if collection == "bookkeeping"
        else ["business_date", "amount", "merchant", "business_purpose"]
        if collection == "receipts"
        else [],
        "provider_fields_editable": False,
    }


def _intent(intent: dict[str, Any]) -> tuple[dict[str, Any], str]:
    value = copy.deepcopy(intent)
    allowed = {
        "workspace_ref",
        "access_generation",
        "request_id",
        "expected_snapshot",
        "action",
        "record_ref",
        "fields",
        "source_refs",
        "reason",
        "attestation",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        _fail("invalid_intent")
    _key(value.get("request_id"))
    if (
        not isinstance(value.get("reason"), str)
        or not value["reason"].strip()
        or len(value["reason"]) > 2000
    ):
        _fail("invalid_reason")
    if not isinstance(value.get("fields"), dict) or not value["fields"]:
        _fail("invalid_fields")
    material = {
        k: v for k, v in value.items() if k not in {"expected_snapshot", "access_generation"}
    }
    return value, _hash(material)


def _new_record(
    ref: str,
    status: str,
    event_actor: str,
    at: str,
    event_reason: str,
    provenance: str,
    **fields: Any,
) -> dict[str, Any]:
    return {
        "record_id": ref,
        "created_at": at,
        "updated_at": at,
        "status": status,
        "status_history": [
            {
                "at": at,
                "actor": event_actor,
                "to_status": status,
                "reason": event_reason,
                "evidence_refs": [],
            }
        ],
        "provenance": [{"kind": "operator-management", "ref": provenance, "observed_at": at}],
        "gaps": [],
        **fields,
    }


def _candidate(
    snapshot: dict[str, Any], intent: dict[str, Any], actor: str, at: str
) -> tuple[dict[str, Any], list[str]]:
    value, digest = _intent(intent)
    _matches(snapshot, value.get("expected_snapshot", {}))
    if actor != "local-user:operator":
        _fail("invalid_operator_actor")
    d = copy.deepcopy(snapshot["document"])
    current = CurrentRecords(d)
    action, fields = value.get("action"), copy.deepcopy(value["fields"])
    collection = "receipts" if action == "correct-receipt-facts" else "bookkeeping"
    if collection == "receipts" and "date" in fields:
        if "business_date" in fields:
            _fail("conflicting_date_fields")
        fields["business_date"] = fields.pop("date")
    allowed = (
        {"business_date", "amount", "merchant", "business_purpose"}
        if collection == "receipts"
        else {
            "date",
            "amount",
            "category",
            "business_purpose",
            "external_project_ref",
            "reporting",
            "account_ref",
        }
    )
    if set(fields) - allowed:
        _fail("unsupported_field")
    if action not in {"create-bookkeeping", "correct-bookkeeping", "correct-receipt-facts"}:
        _fail(
            "unsupported_action",
            "unsupported action; provider/billing changes require existing workflow",
        )
    sources = value.get("source_refs", [])
    if not isinstance(sources, list) or any(
        current.collections.get(x) != "sources" or current.resolve(x) != x for x in sources
    ):
        _fail("unknown_source")
    amount_supplied = "amount" in fields
    exponent = None
    precision_sources = None
    if amount_supplied:
        a = fields["amount"]
        if (
            not isinstance(a, dict)
            or not {"minor", "currency"}.issubset(a)
            or set(a) - {"minor", "currency", "exponent"}
            or not isinstance(a["minor"], str)
            or not re.fullmatch(r"-?(0|[1-9][0-9]*)", a["minor"])
            or not re.fullmatch(r"[A-Z]{3}", a["currency"])
        ):
            _fail("invalid_money")
        if "exponent" in a:
            if type(a["exponent"]) is not int or not 0 <= a["exponent"] <= 6:
                _fail("invalid_money")
            exponent = a["exponent"]
        fields["amount"] = {"amount_minor": int(a["minor"]), "currency": a["currency"]}
    if fields.get("external_project_ref") is not None and (
        not isinstance(fields["external_project_ref"], str)
        or not fields["external_project_ref"].strip()
    ):
        _fail("invalid_project_ref")
    suffix = hashlib.sha256(value["request_id"].encode()).hexdigest()[:32]
    prefix = f"management:{suffix}"
    marker = f"finance-management-intent:{value['request_id']}:{digest}"
    changes = []
    reporting = fields.pop("reporting", None)
    if action == "create-bookkeeping":
        if not {"date", "amount", "category"}.issubset(fields):
            _fail("missing_creation_facts")
        if not sources:
            attestation = value.get("attestation")
            if (
                not isinstance(attestation, str)
                or not attestation.strip()
                or len(attestation) > 2000
            ):
                _fail("source_required")
            source = _new_record(
                prefix + ":source",
                "observed",
                actor,
                at,
                value["reason"],
                marker,
                source_kind="operator-attestation",
                source_identity=prefix + ":attestation",
                attachment_refs=[],
                coverage_state="partial",
                applicability_notes=attestation,
            )
            source["gaps"] = [
                {
                    "field": "original",
                    "reason": "Operator attestation only; retained original absent",
                    "source_refs": [],
                }
            ]
            d["sources"].append(source)
            sources = [source["record_id"]]
            changes.append(source["record_id"])
        row = _new_record(
            prefix + ":record",
            "prepared/unposted",
            actor,
            at,
            value["reason"],
            marker,
            source_refs=sources,
            **fields,
        )
        if value.get("attestation"):
            row["gaps"] = [
                {"field": "original", "reason": "Operator attestation only", "source_refs": sources}
            ]
    else:
        old = value.get("record_ref")
        if current.collections.get(old) != collection or current.resolve(old) != old:
            _fail("stale_target")
        if not sources:
            _fail("source_required")
        if not amount_supplied:
            settings, _policies = _method(current)
            old_reporting, _issue = (
                _reporting(current, settings, current.records[old], {}, check_basis=False)
                if collection == "bookkeeping"
                else (None, None)
            )
            old_exponent, precision_issue = _precision(
                current, settings, current.records[old], old_reporting
            )
            old_settings = settings.get(PRECISION_SCOPE + old, [])
            if old_settings and precision_issue is None and old_exponent is not None:
                exponent = old_exponent
                precision_sources = _payload(current, old_settings[0])["source_refs"]
        row = copy.deepcopy(current.records[old])
        row.update(fields)
        row.update(
            _new_record(prefix + ":record", row["status"], actor, at, value["reason"], marker)
        )
        # Preserve existing gaps; a correction cannot silently repair completeness.
        row["gaps"] = copy.deepcopy(current.records[old]["gaps"])
        row["provenance"] = [*copy.deepcopy(current.records[old]["provenance"]), *row["provenance"]]
        if collection == "bookkeeping":
            row["source_refs"] = list(dict.fromkeys([*row["source_refs"], *sources]))
        correction = _new_record(
            prefix + ":correction",
            "applied",
            actor,
            at,
            value["reason"],
            marker,
            superseded_ref=old,
            replacement_ref=row["record_id"],
            reason=value["reason"],
            decision_ref=prefix + ":decision",
        )
        correction["provenance"].extend(
            {"kind": "correction-evidence", "ref": ref, "observed_at": at} for ref in sources
        )
        correction["status_history"][-1]["evidence_refs"] = list(sources)
        d["corrections"].append(correction)
        changes.append(correction["record_id"])
    row["provenance"].extend(
        {"kind": "correction-evidence", "ref": ref, "observed_at": at} for ref in sources
    )
    row["status_history"][-1]["evidence_refs"] = list(sources)
    d[collection].append(row)
    changes.append(row["record_id"])
    review = _new_record(
        prefix + ":decision",
        "reviewed",
        actor,
        at,
        value["reason"],
        marker,
        target_ref=row["record_id"],
        target_digest_sha256=workspace.material_record_digest(row, collection),
        scope=["finance-management", "local-fact-correction"],
        authority="operator",
        verdict="approved",
        actor=actor,
        decided_at=at,
        reasons=[value["reason"]],
    )
    review["provenance"].extend(
        {"kind": "correction-evidence", "ref": ref, "observed_at": at} for ref in sources
    )
    review["status_history"][-1]["evidence_refs"] = list(sources)
    d["reviews"].append(review)
    changes.append(review["record_id"])
    if exponent is not None:
        payload = {
            "management_schema_version": "finance-management-v1",
            "kind": "record-precision",
            "target_ref": row["record_id"],
            "target_digest_sha256": workspace.material_record_digest(row, collection),
            "currency": row["amount"]["currency"],
            "exponent": exponent,
            "source_refs": precision_sources or sources,
        }
        _schema(payload, "management-v1.schema.json")
        setting = _new_record(
            prefix + ":precision",
            "confirmed",
            actor,
            at,
            value["reason"],
            marker,
            version=1,
            scope=PRECISION_SCOPE + row["record_id"],
            policy_text=_json(payload),
            decision_refs=[prefix + ":precision-review"],
        )
        setting["provenance"].extend(
            {"kind": "correction-evidence", "ref": ref, "observed_at": at}
            for ref in payload["source_refs"]
        )
        setting["status_history"][-1]["evidence_refs"] = list(payload["source_refs"])
        precision_review = _new_record(
            prefix + ":precision-review",
            "reviewed",
            actor,
            at,
            value["reason"],
            marker,
            target_ref=setting["record_id"],
            target_version=1,
            target_digest_sha256=workspace.material_record_digest(setting, "operating_settings"),
            scope=["finance-management", "amount-precision"],
            authority="operator",
            verdict="approved",
            actor=actor,
            decided_at=at,
        )
        precision_review["provenance"].extend(
            {"kind": "correction-evidence", "ref": ref, "observed_at": at}
            for ref in payload["source_refs"]
        )
        precision_review["status_history"][-1]["evidence_refs"] = list(payload["source_refs"])
        d["operating_settings"].append(setting)
        d["reviews"].append(precision_review)
        changes.extend([setting["record_id"], precision_review["record_id"]])
    if reporting is not None:
        payload = {
            "management_schema_version": "finance-management-v1",
            "kind": "record-reporting",
            "target_ref": row["record_id"],
            "target_digest_sha256": workspace.material_record_digest(row, collection),
            "reporting": reporting,
        }
        _schema(payload, "management-v1.schema.json")
        if (
            collection != "bookkeeping"
            or row["amount"]["amount_minor"] < 0
            or any(
                current.collections.get(ref) != "sources" and ref not in sources
                for ref in reporting["source_refs"]
            )
        ):
            _fail("unsupported_reporting")
        setting = _new_record(
            prefix + ":metadata",
            "confirmed",
            actor,
            at,
            value["reason"],
            marker,
            version=1,
            scope=RECORD_SCOPE + row["record_id"],
            policy_text=_json(payload),
            decision_refs=[prefix + ":metadata-review"],
        )
        meta_review = _new_record(
            prefix + ":metadata-review",
            "reviewed",
            actor,
            at,
            value["reason"],
            marker,
            target_ref=setting["record_id"],
            target_version=1,
            target_digest_sha256=workspace.material_record_digest(setting, "operating_settings"),
            scope=["finance-management"],
            authority="operator",
            verdict="approved",
            actor=actor,
            decided_at=at,
        )
        d["operating_settings"].append(setting)
        d["reviews"].append(meta_review)
        changes.extend([setting["record_id"], meta_review["record_id"]])
    d["revision"] += 1
    try:
        workspace.validate(d)
    except (ValueError, TypeError, SchemaValidationError):
        _fail("invalid_candidate")
    return d, changes


def preview(snapshot: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    value, digest = _intent(intent)
    _candidate_document, changed = _candidate(
        snapshot, value, "local-user:operator", "2000-01-01T00:00:00Z"
    )
    # Digest binds request/material and source snapshot, not generated actor/time.
    return {
        "preview_digest": _hash(
            {"intent_digest": digest, "source_snapshot": value["expected_snapshot"]}
        ),
        "source_snapshot": _safe_snapshot(snapshot),
        "changed_refs": changed,
        "diff": {
            "record_ref": value.get("record_ref"),
            "fields": value["fields"],
            "action": value["action"],
        },
        "state": "valid",
        "financial_decision_adopted": False,
    }


def _replay(document: dict[str, Any], request_id: str, digest: str) -> list[str] | None:
    prefix = f"finance-management-intent:{request_id}:"
    found, refs = set(), []
    for rows in document.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            markers = [
                p["ref"]
                for p in row.get("provenance", [])
                if p.get("kind") == "operator-management" and p.get("ref", "").startswith(prefix)
            ]
            if markers:
                # Corrective descendants retain original provenance; receipt refs
                # are deterministic original request records, not later descendants.
                found.update(x[len(prefix) :] for x in markers)
                suffix = hashlib.sha256(request_id.encode()).hexdigest()[:32]
                if row["record_id"].startswith(f"management:{suffix}:"):
                    refs.append(row["record_id"])
    if found and found != {digest}:
        _fail("intent_conflict")
    return sorted(set(refs)) if found else None


def commit(
    path: Path,
    intent: dict[str, Any],
    preview_digest: str,
    actor: str = "local-user:operator",
    *,
    authorize: Callable[[], None] | None = None,
) -> dict[str, Any]:
    path = _master(path)
    value, digest = _intent(intent)
    if actor != "local-user:operator":
        _fail("invalid_operator_actor")
    try:
        # One lock for replay check and write; never split two cooperating writers.
        with workspace.exclusive_lock(path.parent):
            if authorize is not None:
                authorize()
            before = workspace.read_document(path)
            replay = _replay(before, value["request_id"], digest)
            if replay:
                return {
                    "request_id": value["request_id"],
                    "changed_refs": replay,
                    "snapshot": _safe_snapshot(read_snapshot(path)),
                    "reread_result": "verified",
                    "idempotency_replay": True,
                }
            s = read_snapshot(path)
            _matches(s, value.get("expected_snapshot", {}))
            expected = _hash(
                {"intent_digest": digest, "source_snapshot": value["expected_snapshot"]}
            )
            if preview_digest != expected:
                _fail("stale_preview")
            candidate, changed = _candidate(s, value, actor, _now())
            session = workspace.WriteSession(path, s["sha256"], s["revision"])
            if authorize is not None:
                authorize()
            try:
                proof = session.commit(candidate)
            except (ValueError, OSError, RuntimeError):
                try:
                    unchanged = workspace.digest(path) == s["sha256"]
                except (ValueError, OSError):
                    unchanged = False
                if unchanged:
                    _fail("commit_failed", "commit failed before verified replacement")
                raise OutcomeUnknown(value["request_id"]) from None
            finally:
                session._active = False
            return {
                "request_id": value["request_id"],
                "changed_refs": changed,
                "snapshot": _safe_snapshot(read_snapshot(path)),
                "reread_result": proof["reread_result"],
                "idempotency_replay": False,
            }
    except ManagementError:
        raise
    except RuntimeError:
        _fail("writer_busy")
    except (ValueError, OSError, SchemaValidationError):
        _fail("invalid_workspace")


def render_report(
    projection: dict[str, Any], format: str = "json", *, snapshot: dict[str, Any] | None = None
) -> bytes:
    if projection["page"]["total"] != len(projection["rows"]):
        if snapshot is None:
            _fail("report_population_incomplete")
        _matches(snapshot, {k: projection["snapshot"][k] for k in ("revision", "sha256")})
        rows = []
        for offset in range(0, projection["page"]["total"], 200):
            page = query(snapshot, {**projection["query"], "offset": offset, "limit": 200})
            rows.extend(page["rows"])
        projection = {
            **projection,
            "rows": rows,
            "page": {"total": len(rows), "offset": 0, "limit": len(rows), "has_more": False},
        }
    if format == "json":
        return (_json(projection) + "\n").encode()
    if format == "csv":
        columns = (
            "record_ref",
            "kind",
            "date",
            "currency",
            "minor",
            "exponent",
            "business_purpose",
            "metric_exclusion",
            "status",
            "period_start",
            "period_end",
            "recognition_basis",
            "date_basis",
            "timezone",
            "coverage_state",
            "coverage_gaps",
            "exclusions",
            "metrics_by_currency",
        )
        metadata = {
            "period_start": projection["query"]["start"],
            "period_end": projection["query"]["end"],
            "recognition_basis": projection["query"]["recognition_basis"],
            "date_basis": projection["query"]["date_basis"],
            "timezone": projection["query"]["timezone"],
            "coverage_state": projection["coverage"]["state"],
            "coverage_gaps": _json(projection["coverage"]["issues"]),
            "exclusions": _json(projection["exclusions"]),
            "metrics_by_currency": _json(projection["metrics_by_currency"]),
        }
        rows = [
            {key: {**r, **(r["amount"] or {}), **metadata}.get(key) for key in columns}
            for r in projection["rows"]
        ]
        return views._csv(
            rows, columns, projection["snapshot"]["revision"], projection["snapshot"]["sha256"]
        )
    if format == "markdown":

        def cell(value):
            if value is None or value == "":
                return "Unavailable"
            if isinstance(value, list):
                return "; ".join(cell(item) for item in value) or "None"
            if isinstance(value, dict):
                return "; ".join(f"{cell(k)}: {cell(v)}" for k, v in value.items()) or "None"
            text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            text = re.sub(r"([\\`*_{}\[\]()#!|])", r"\\\1", text)
            return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")

        def amount(value):
            if not value:
                return "Unavailable"
            minor, exponent = str(value["minor"]), value.get("exponent")
            currency = cell(value["currency"])
            if not isinstance(exponent, int) or not 0 <= exponent <= 6:
                return f"{currency} {cell(minor)} minor units (precision unavailable)"
            sign = "-" if minor.startswith("-") else ""
            digits = minor.removeprefix("-").zfill(exponent + 1)
            decimal = f"{digits[:-exponent]}.{digits[-exponent:]}" if exponent else digits
            return f"{currency} {sign}{decimal}"

        lines = [
            "# Finance period report",
            "",
            "Prepared/unposted; supported operating result, not posted books. "
            "Derived report; corrections belong in the authoritative source.",
            "",
        ]

        def table(headers, rows):
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            lines.extend("| " + " | ".join(row) + " |" for row in rows)
            lines.append("")

        def context(value):
            q, coverage = value["query"], value["coverage"]
            lines.extend(
                [
                    f"Period: {cell(q['start'])} through {cell(q['end'])}",
                    "",
                    f"Scope: {cell(q.get('scope', 'business'))}"
                    f" · Project: {cell(q.get('external_project_ref'))}",
                    "",
                    f"Recognition basis: {cell(q['recognition_basis'])} "
                    f"· Date basis: {cell(q['date_basis'])} · Timezone: {cell(q['timezone'])}",
                    "",
                    f"Coverage: {cell(coverage['state'])}",
                    "",
                    f"Coverage issues: {cell(coverage.get('issues', []))}",
                    "",
                ]
            )

        def totals(value):
            rows = []
            for currency in value["metrics_by_currency"]:
                for key, label in [
                    ("spend", "Spend"),
                    ("revenue", "Revenue"),
                    ("profit", "Operating profit"),
                    ("tax_payments", "Tax payments"),
                ]:
                    metric = currency[key]
                    state = metric.get("state")
                    displayed = amount(metric.get("amount"))
                    if state:
                        displayed += f" ({cell(state)})"
                    elif key == "tax_payments":
                        displayed += (
                            " (recorded separately)" if metric.get("amount") else " (unavailable)"
                        )
                    rows.append(
                        [
                            cell(currency["currency"]),
                            label,
                            displayed,
                            cell(metric.get("included_record_refs", [])),
                            cell(metric.get("excluded_record_refs", [])),
                        ]
                    )
            if rows:
                table(
                    [
                        "Currency",
                        "Metric",
                        "Amount / state",
                        "Included current refs",
                        "Excluded refs",
                    ],
                    rows,
                )
            else:
                lines.extend(["Supported totals unavailable; no inferred zero activity.", ""])

        context(projection)
        source = projection["snapshot"]
        lines.extend(
            [
                f"Source revision: {cell(source['revision'])} · SHA-256: {cell(source['sha256'])}",
                "",
                f"Read at: {cell(source.get('read_at'))} "
                f"· Schema: {cell(source.get('schema_version'))}",
                "",
                f"Custody issues: {cell(source.get('custody_issues', []))}",
                "",
                "### Selected filters",
                "",
            ]
        )
        table(
            ["Filter", "Value"],
            [[cell(k), cell(v)] for k, v in projection["query"].items() if k != "comparison"],
        )
        lines.extend(["## Supported totals", ""])
        totals(projection)
        lines.extend(
            [
                "Tax payments are separate from the supported operating profit.",
                "",
                f"## Records · {len(projection['rows'])} records",
                "",
            ]
        )
        table(
            [
                "Current ref",
                "Type / status",
                "Date",
                "Vendor / counterparty",
                "Treatment",
                "Amount",
                "Category / state",
                "Account / project",
                "Purpose",
                "Source / evidence refs",
                "Review / gaps",
            ],
            [
                [
                    cell(r["record_ref"]),
                    cell([r["kind"], r["status"]]),
                    cell(r.get("date")),
                    cell(r.get("counterparty")),
                    cell((r.get("reporting") or {}).get("treatment")),
                    amount(r.get("amount")),
                    cell([r.get("category_ref"), r.get("category_state")]),
                    cell([r.get("account_ref"), r.get("external_project_ref")]),
                    cell(r.get("business_purpose")),
                    cell([*r.get("source_refs", []), *r.get("evidence_refs", [])]),
                    cell(
                        [
                            "Needs review" if r.get("needs_review") else "No recorded review gap",
                            r.get("metric_exclusion"),
                            *r.get("gaps", []),
                        ]
                    ),
                ]
                for r in projection["rows"]
            ],
        )
        lines.extend(["## Review and exclusions", ""])
        table(
            ["Current ref", "Reason", "Affected refs", "Next action"],
            [
                [
                    cell(r.get("record_ref")),
                    cell(r.get("reason")),
                    cell(r.get("affected_refs", [])),
                    cell(r.get("next_action")),
                ]
                for r in projection["review_items"]
            ],
        )
        table(
            ["Excluded ref", "Reason"],
            [[cell(r["record_ref"]), cell(r["reason"])] for r in projection["exclusions"]],
        )
        lines.extend(
            [
                f"Undated or period-only evidence: {cell(projection['undated_or_period_only'])}",
                "",
                f"Warnings: {cell(projection['warnings'])}",
                "",
                "## Breakdowns",
                "",
            ]
        )
        precision = {m["currency"]: m["exponent"] for m in projection["metrics_by_currency"]}

        def group_amount(row, key):
            return amount(
                {
                    "minor": row[key],
                    "currency": row["currency"],
                    "exponent": precision.get(row["currency"]),
                }
            )

        for dimension, groups in projection["breakdowns"].items():
            lines.extend([f"### {cell(dimension.title())}", ""])
            table(
                ["Group", "Revenue", "Spend", "Included current refs"],
                [
                    [
                        cell(r["label"]),
                        group_amount(r, "revenue_minor"),
                        group_amount(r, "spend_minor"),
                        cell(r["included_record_refs"]),
                    ]
                    for r in groups
                ],
            )
        lines.extend(["## Trend", ""])
        table(
            ["Date", "Revenue", "Spend", "Included current refs"],
            [
                [
                    cell(r["date"]),
                    group_amount(r, "revenue_minor"),
                    group_amount(r, "spend_minor"),
                    cell(r["included_record_refs"]),
                ]
                for r in projection["trend"]
            ],
        )
        if projection.get("comparison"):
            lines.extend(["## Comparison", ""])
            context(projection["comparison"])
            totals(projection["comparison"])
            lines.extend([f"Comparison window: {cell(projection['query'].get('comparison'))}", ""])
        return ("\n".join(lines) + "\n").encode()
    _fail("invalid_report_format")


def result_digest(result: dict[str, Any]) -> str:
    return _hash({k: v for k, v in result.items() if k != "review"})


def validate_result(
    result: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_query: dict[str, Any] | None = None,
    allow_stale: bool = False,
) -> None:
    _schema(result, "management-result-v1.schema.json")
    _key(result["request_id"])
    review = result["review"]
    if review["reviewer_ref"] == result["producer_ref"]:
        _fail("review_not_independent", "independent reviewer required")
    if review["target_digest_sha256"] != result_digest(result) or review["verdict"] != "approved":
        _fail("unreviewed_result")
    if not allow_stale:
        _matches(snapshot, result["source_snapshot"])
    normalize_query(result["query"])
    if expected_query is not None and normalize_query(expected_query) != normalize_query(
        result["query"]
    ):
        _fail("result_query_mismatch")
    current = CurrentRecords(snapshot["document"])
    refs = result["evidence_refs"] + [
        ref for opportunity in result["opportunities"] for ref in opportunity["evidence_refs"]
    ]
    if not refs or any(ref not in current.records for ref in refs):
        _fail("unknown_result_evidence")
    if result["state"] == "no-supported-opportunity" and result["opportunities"]:
        _fail("invalid_result_state")
    for opportunity in result["opportunities"]:
        impact = opportunity.get("estimated_impact")
        if impact and not opportunity.get("assumptions"):
            _fail("unsupported_impact")


def publish_result(path: Path, result: dict[str, Any], report_bytes: bytes) -> dict[str, Any]:
    path = _master(path)
    if (
        not isinstance(report_bytes, bytes)
        or not report_bytes
        or len(report_bytes) > 20 * 1024 * 1024
    ):
        _fail("invalid_report_bytes")
    with workspace.exclusive_lock(path.parent):
        s = read_snapshot(path)
        validate_result(result, s)
        if result["report_sha256"] != hashlib.sha256(report_bytes).hexdigest():
            _fail("unreviewed_report")
        directory = workspace.checked_path(
            path.parent / "exports" / "analysis" / _key(result["request_id"]), within=path.parent
        )
        outputs = {"result.json": (_json(result) + "\n").encode(), "report.md": report_bytes}
        manifest = {
            "format": "finance-management-publication-v1",
            "source_snapshot": result["source_snapshot"],
            "request_id": result["request_id"],
            "files": {
                name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                for name, data in outputs.items()
            },
        }
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        marker = directory / "manifest.json"
        if marker.exists():
            existing = workspace.decode_json(workspace.read_regular(marker))
            if existing != manifest:
                _fail("publication_conflict")
            for name, data in outputs.items():
                if workspace.read_regular(directory / name) != data:
                    _fail("publication_conflict")
        else:
            for name, data in outputs.items():
                target = workspace.checked_path(directory / name, within=directory)
                if target.exists() and workspace.read_regular(target) != data:
                    _fail("publication_conflict")
                workspace.atomic_write(target, data)
            _matches(read_snapshot(path), result["source_snapshot"])
            workspace.atomic_write(marker, (_json(manifest) + "\n").encode())
        return {
            "external_result_ref": "finance-analysis:" + result["request_id"],
            "result_sha256": manifest["files"]["result.json"]["sha256"],
            "source_snapshot": result["source_snapshot"],
            "state": "published",
        }


def read_result(
    path: Path,
    external_ref: str,
    *,
    expected_digest: str | None = None,
    expected_query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = _master(path)
    if not isinstance(external_ref, str) or not external_ref.startswith("finance-analysis:"):
        _fail("invalid_result_ref")
    request_id = _key(external_ref.removeprefix("finance-analysis:"))
    directory = workspace.checked_path(
        path.parent / "exports" / "analysis" / request_id, within=path.parent
    )
    try:
        marker = workspace.read_regular(directory / "manifest.json")
        manifest = workspace.decode_json(marker)
        if (
            manifest.get("format") != "finance-management-publication-v1"
            or manifest.get("request_id") != request_id
            or set(manifest.get("files", {})) != {"result.json", "report.md"}
        ):
            _fail("invalid_publication")
        outputs = {}
        for name in ("result.json", "report.md"):
            meta = manifest["files"][name]
            if not isinstance(meta.get("bytes"), int) or not 0 < meta["bytes"] <= 20 * 1024 * 1024:
                _fail("invalid_publication")
            data = workspace.read_regular(
                workspace.checked_path(directory / name, within=directory)
            )
            if len(data) != meta["bytes"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
                _fail("publication_integrity")
            outputs[name] = data
        if expected_digest and expected_digest != manifest["files"]["result.json"]["sha256"]:
            _fail("result_digest_mismatch")
        result = workspace.decode_json(outputs["result.json"])
        s = read_snapshot(path)
        validate_result(result, s, expected_query=expected_query, allow_stale=True)
        if result["report_sha256"] != hashlib.sha256(outputs["report.md"]).hexdigest():
            _fail("unreviewed_report")
        if (
            result["request_id"] != request_id
            or result["source_snapshot"] != manifest["source_snapshot"]
            or workspace.read_regular(directory / "manifest.json") != marker
        ):
            _fail("publication_integrity")
        return {
            "state": "current"
            if result["source_snapshot"] == {"revision": s["revision"], "sha256": s["sha256"]}
            else "stale",
            "result": result,
            "report": outputs["report.md"].decode("utf-8"),
            "result_sha256": manifest["files"]["result.json"]["sha256"],
        }
    except ManagementError:
        raise
    except (ValueError, OSError, KeyError, TypeError):
        _fail("result_unavailable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("query", "detail", "preview", "commit", "result-read", "result-publish")
    )
    parser.add_argument("--finance", type=Path, required=True)
    parser.add_argument(
        "--input", type=Path, required=True, help="Explicit host-owned JSON input file"
    )
    parser.add_argument("--preview-digest")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        value = workspace.decode_json(workspace.read_regular(args.input))
        s = read_snapshot(args.finance)
        if args.command == "query":
            output = query(s, value)
        elif args.command == "detail":
            output = record_detail(s, value["record_ref"])
        elif args.command == "preview":
            output = preview(s, value)
        elif args.command == "commit":
            output = commit(args.finance, value, args.preview_digest)
        elif args.command == "result-read":
            output = read_result(args.finance, value["external_result_ref"])
        else:
            output = publish_result(args.finance, value, workspace.read_regular(args.report))
        print(_json(output))
        return 0
    except ManagementError as error:
        print(_json({"error": error.code, "repair": error.data}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
