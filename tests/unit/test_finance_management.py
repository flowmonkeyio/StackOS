"""Synthetic finance management proofs; no real business files or providers."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json

import pytest

from tests.helpers.finance_workspace import money, record


def management():
    return importlib.import_module("plugins.finance.scripts.finance_management")


def test_missing_revenue_is_unknown_not_zero(tmp_path):
    module = management()
    path = module.workspace.initialize(tmp_path / "finance")
    snapshot = module.read_snapshot(path)
    projected = module.query(
        snapshot,
        {
            "start": "2026-09-01",
            "end": "2026-09-30",
            "recognition_basis": "cash",
            "date_basis": "transaction",
            "timezone": "UTC",
        },
    )
    assert projected["metrics_by_currency"] == []
    assert projected["coverage"]["state"] == "unavailable"


QUERY = {
    "start": "2026-09-01",
    "end": "2026-09-30",
    "recognition_basis": "cash",
    "date_basis": "transaction",
    "timezone": "UTC",
}


def fixture_document():
    from plugins.finance.scripts import finance_workspace as ws

    d = ws.new_document()
    d["sources"].append(
        record(
            "source:bank",
            "verified",
            source_kind="bank-export",
            source_identity="synthetic:bank:september",
            account_ref="bank:one",
            attachment_refs=[],
            coverage_state="complete",
            required_window={"start": QUERY["start"], "end": QUERY["end"]},
            available_window={"start": QUERY["start"], "end": QUERY["end"]},
        )
    )
    d["operating_settings"].append(
        record(
            "account:one",
            "confirmed",
            version=1,
            scope="account",
            decision_refs=["fixture:decision"],
            account_identity={"display_name": "Synthetic account", "aliases": ["bank:one"]},
        )
    )
    for ref, amount, treatment in (
        ("revenue", 100000, "revenue"),
        ("expense", 10000, "expense"),
        ("fee", 1000, "fee"),
        ("refund", 5000, "revenue_refund"),
        ("transfer", 40000, "transfer"),
        ("reserve", 7000, "reserve"),
        ("owner", 3000, "owner_movement"),
        ("tax", 2000, "tax_payment"),
    ):
        b = record(
            f"book:{ref}",
            "prepared/unposted",
            date="2026-09-03",
            amount=money(amount),
            source_refs=["source:bank"],
            account_ref="bank:one",
            category={"state": "unresolved"},
        )
        d["bookkeeping"].append(b)
        metadata(d, b, treatment)
    setting(
        d,
        "policy:reporting",
        "finance-management-reporting",
        {
            "management_schema_version": "finance-management-v1",
            "kind": "reporting-policy",
            "recognition_basis": "cash",
            "date_basis": "transaction",
            "timezone": "UTC",
            "currency_precision": [{"currency": "USD", "exponent": 2, "source_ref": "source:bank"}],
            "scope_accounts": ["account:one"],
            "coverage_review_ref": "review:policy:reporting",
        },
        scopes=["finance-management", "coverage", "revenue", "expenses"],
    )
    return d


def setting(document, ref, scope, payload, *, scopes=None):
    from plugins.finance.scripts import finance_workspace as ws

    review_id = f"review:{ref}"
    s = record(
        ref,
        "confirmed",
        version=1,
        scope=scope,
        policy_text=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        decision_refs=[review_id],
    )
    document["operating_settings"].append(s)
    document["reviews"].append(
        record(
            review_id,
            "reviewed",
            target_ref=ref,
            target_version=1,
            target_digest_sha256=ws.material_record_digest(s, "operating_settings"),
            scope=scopes or ["finance-management"],
            authority="operator",
            verdict="approved",
            actor="local-user:operator",
            decided_at="2026-09-06T12:00:00Z",
        )
    )
    return s


def metadata(document, target, treatment="expense", **extra):
    from plugins.finance.scripts import finance_workspace as ws

    r = {
        "economic_event_ref": f"event:{target['record_id']}",
        "component_ref": f"component:{target['record_id']}",
        "treatment": treatment,
        "recognition_basis": "cash",
        "date_basis": "transaction",
        "currency_exponent": 2,
        "source_refs": ["source:bank"],
        **extra,
    }
    return setting(
        document,
        f"metadata:{target['record_id']}",
        f"finance-management-record:{target['record_id']}",
        {
            "management_schema_version": "finance-management-v1",
            "kind": "record-reporting",
            "target_ref": target["record_id"],
            "target_digest_sha256": ws.material_record_digest(target, "bookkeeping"),
            "reporting": r,
        },
    )


def snapshot(document):
    return {
        "document": document,
        "revision": document["revision"],
        "sha256": hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
        "schema_version": "local-json-v1",
        "custody_issues": [],
        "read_at": "2026-09-06T12:00:00Z",
    }


def test_golden_metrics_and_report_trace_do_not_count_other_representations():
    module = management()
    d = fixture_document()
    p = module.query(snapshot(d), QUERY)
    m = p["metrics_by_currency"][0]
    assert m["revenue"]["amount"]["minor"] == "95000"
    assert m["spend"]["amount"]["minor"] == "11000"
    assert m["profit"]["amount"]["minor"] == "84000"
    assert m["profit"]["state"] == "complete"
    assert m["tax_payments"]["amount"]["minor"] == "2000"
    assert set(m["profit"]["included_record_refs"]) == {
        "book:revenue",
        "book:expense",
        "book:fee",
        "book:refund",
    }
    exported = json.loads(module.render_report(p, "json"))
    assert exported["metrics_by_currency"] == p["metrics_by_currency"]
    assert exported["snapshot"] == p["snapshot"]
    assert p["breakdowns"]["category"][0]["label"] == "Unknown/unassigned"


@pytest.mark.parametrize("category_state", ["proposed", "unresolved", "applied", "reviewed"])
@pytest.mark.parametrize("category_ref", [None, "category:selected"])
def test_category_breakdown_uses_supported_state_and_preserves_proposals(
    category_state, category_ref
):
    module = management()
    d = fixture_document()
    baseline = module.query(snapshot(d), QUERY)
    expense = next(x for x in d["bookkeeping"] if x["record_id"] == "book:expense")
    category = {"state": category_state}
    if category_ref is not None:
        category["category_ref"] = category_ref
    expense["category"] = category
    # Keep the approved economic treatment bound to this exact schema-valid row.
    s = next(x for x in d["operating_settings"] if x["record_id"] == "metadata:book:expense")
    payload = json.loads(s["policy_text"])
    payload["target_digest_sha256"] = module.workspace.material_record_digest(
        expense, "bookkeeping"
    )
    s["policy_text"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    review = next(x for x in d["reviews"] if x["record_id"] == "review:metadata:book:expense")
    review["target_digest_sha256"] = module.workspace.material_record_digest(
        s, "operating_settings"
    )
    module.workspace.validate(d)

    source = snapshot(d)
    p = module.query(source, QUERY)
    assert p["metrics_by_currency"] == baseline["metrics_by_currency"]
    assert p["trend"] == baseline["trend"]
    row = next(x for x in p["rows"] if x["record_ref"] == "book:expense")
    assert row["category_ref"] == category_ref
    assert row["category_state"] == category_state
    assert row["needs_review"] == (category_state in {"proposed", "unresolved"})
    assert module.record_detail(source, "book:expense")["record"]["category"] == category

    groups = p["breakdowns"]["category"]
    expense_group = next(x for x in groups if "book:expense" in x["included_record_refs"])
    expected_label = (
        category_ref
        if category_ref is not None and category_state in {"applied", "reviewed"}
        else "Unknown/unassigned"
    )
    assert expense_group["label"] == expected_label
    metrics = p["metrics_by_currency"][0]
    assert (
        str(sum(int(x["revenue_minor"]) for x in groups)) == metrics["revenue"]["amount"]["minor"]
    )
    assert str(sum(int(x["spend_minor"]) for x in groups)) == metrics["spend"]["amount"]["minor"]
    assert json.loads(module.render_report(p, "json"))["breakdowns"] == p["breakdowns"]

    if category_ref is not None:
        filtered = module.query(source, {**QUERY, "category_ref": category_ref})
        assert filtered["breakdowns"]["category"] == [
            {
                "label": expected_label,
                "currency": "USD",
                "included_record_refs": ["book:expense"],
                "revenue_minor": "0",
                "spend_minor": "10000",
            }
        ]


@pytest.mark.parametrize(
    "case",
    [
        "proposed",
        "unreviewed",
        "stale",
        "wrong-scope",
        "unsupported",
        "conflict",
        "negative",
        "unknown-source",
        "policy-conflict",
    ],
)
def test_unapproved_or_ambiguous_metadata_excludes_only_affected_rows(case):
    module = management()
    d = fixture_document()
    s = next(x for x in d["operating_settings"] if x["record_id"] == "metadata:book:expense")
    review = next(x for x in d["reviews"] if x["record_id"] == "review:metadata:book:expense")
    if case == "proposed":
        s["status"] = "proposed"
    elif case == "unreviewed":
        review["verdict"] = "held"
    elif case == "wrong-scope":
        review["scope"] = ["unrelated"]
    elif case == "conflict":
        d["operating_settings"].append({**copy.deepcopy(s), "record_id": "metadata:conflict"})
    elif case == "negative":
        next(x for x in d["bookkeeping"] if x["record_id"] == "book:expense")["amount"][
            "amount_minor"
        ] = -10000
    else:
        payload = json.loads(s["policy_text"])
        if case == "stale":
            payload["target_digest_sha256"] = "0" * 64
        elif case == "unsupported":
            payload["management_schema_version"] = "unsupported"
        elif case == "unknown-source":
            payload["reporting"]["source_refs"] = ["missing"]
        elif case == "policy-conflict":
            payload["reporting"]["policy_ref"] = "unapproved:policy"
        s["policy_text"] = json.dumps(payload)
    p = module.query(snapshot(d), QUERY)
    assert "book:expense" not in p["metrics_by_currency"][0]["spend"]["included_record_refs"]
    assert any(x["record_ref"] == "book:expense" for x in p["exclusions"])
    assert p["metrics_by_currency"][0]["profit"]["state"] != "complete"


def test_duplicate_components_are_excluded_but_equal_genuine_events_count():
    module = management()
    d = fixture_document()
    expense = next(x for x in d["bookkeeping"] if x["record_id"] == "book:expense")
    other = {**copy.deepcopy(expense), "record_id": "book:other"}
    d["bookkeeping"].append(other)
    metadata(
        d, other, economic_event_ref="event:book:expense", component_ref="component:book:expense"
    )
    p = module.query(snapshot(d), QUERY)
    assert p["metrics_by_currency"][0]["spend"]["amount"]["minor"] == "1000"
    assert {
        x["record_ref"] for x in p["exclusions"] if x["reason"] == "economic_identity_conflict"
    } == {"book:expense", "book:other"}
    d = fixture_document()
    d["bookkeeping"].append(other)
    metadata(d, other)
    assert (
        module.query(snapshot(d), QUERY)["metrics_by_currency"][0]["spend"]["amount"]["minor"]
        == "21000"
    )


def test_currency_precision_large_integer_filter_and_comparison():
    module = management()
    d = fixture_document()
    for i, (currency, value, exponent) in enumerate(
        (("USD", 9007199254740993, 2), ("JPY", 105, 0), ("KWD", 1234, 3))
    ):
        b = record(
            f"book:currency{i}",
            "prepared/unposted",
            date="2026-09-30",
            amount=money(value, currency),
            source_refs=["source:bank"],
            category={"state": "unresolved"},
            external_project_ref="project:A",
        )
        d["bookkeeping"].append(b)
        metadata(d, b, currency_exponent=exponent)
    p = module.query(
        snapshot(d),
        {**QUERY, "scope": "external-project", "external_project_ref": "project:A", "limit": 1},
    )
    assert p["page"]["total"] == 3 and len(p["rows"]) == 1
    assert {x["currency"] for x in p["metrics_by_currency"]} == {"USD", "JPY", "KWD"}
    assert (
        next(x for x in p["metrics_by_currency"] if x["currency"] == "USD")["spend"]["amount"][
            "minor"
        ]
        == "9007199254740993"
    )
    assert module.comparison_window("2026-09-01", "2026-09-30", "previous-period") == {
        "start": "2026-08-02",
        "end": "2026-08-31",
    }
    assert module.comparison_window("2024-02-29", "2024-02-29", "previous-year")["clamped"] is True
    assert module.query(snapshot(d), {**QUERY, "q": "not-present"})["page"]["total"] == 0


@pytest.mark.parametrize(
    "window_kind", ["hint-true", "hint-false", "generated-true", "generated-false"]
)
def test_comparison_hint_preserves_window_filters_population_and_metadata(window_kind):
    module = management()
    if window_kind == "generated-true":
        current_start, current_end = "2024-02-29", "2024-02-29"
        window = module.comparison_window(current_start, current_end, "previous-year")
        assert window["clamped"] is True
    elif window_kind == "generated-false":
        current_start, current_end = QUERY["start"], QUERY["end"]
        window = module.comparison_window(current_start, current_end, "previous-year")
        assert window["clamped"] is False
    else:
        current_start, current_end = QUERY["start"], QUERY["end"]
        window = {
            **module.comparison_window(current_start, current_end, "previous-period"),
            "clamped": window_kind == "hint-true",
        }
    document = fixture_document()
    for number, (amount, project, account) in enumerate(
        [
            (1000, "project:A", "bank:one"),
            (2000, "project:A", "bank:one"),
            (9000, "project:B", "bank:one"),
            (8000, "project:A", "bank:other"),
        ]
    ):
        row = record(
            f"book:comparison-{number}",
            "prepared/unposted",
            date=window["start"],
            amount=money(amount),
            source_refs=["source:bank"],
            account_ref=account,
            category={"state": "unresolved"},
            external_project_ref=project,
        )
        document["bookkeeping"].append(row)
        metadata(document, row)
    source = snapshot(document)
    filters = {
        **QUERY,
        "start": current_start,
        "end": current_end,
        "scope": "external-project",
        "external_project_ref": "project:A",
        "account_ref": "bank:one",
        "kind": "expense",
        "currency": "USD",
        "limit": 1,
        "comparison": window,
    }
    before = copy.deepcopy(filters)
    dates = {"start": window["start"], "end": window["end"]}
    plain = module.query(source, {**filters, **dates, "comparison": None})
    assert plain["page"]["total"] == 2 and len(plain["rows"]) == 1
    assert plain["metrics_by_currency"][0]["spend"]["amount"]["minor"] == "3000"
    without_hint = module.query(source, {**filters, "comparison": dates})
    actual = module.query(source, filters)
    assert actual["comparison"] == without_hint["comparison"]
    assert actual["comparison"]["metrics_by_currency"] == plain["metrics_by_currency"]
    assert actual["comparison"]["coverage"] == plain["coverage"]
    assert actual["comparison"]["query"] == plain["query"]
    assert actual["query"]["comparison"] == window
    assert filters == before
    assert source["sha256"] == snapshot(document)["sha256"]


def test_expense_only_missing_coverage_and_basis_conflict():
    module = management()
    d = fixture_document()
    d["bookkeeping"] = [
        x for x in d["bookkeeping"] if x["record_id"] not in {"book:revenue", "book:refund"}
    ]
    d["operating_settings"] = [
        x
        for x in d["operating_settings"]
        if not x["scope"].startswith("finance-management-record:book:re")
    ]
    d["reviews"] = [
        x
        for x in d["reviews"]
        if x["target_ref"] in {s["record_id"] for s in d["operating_settings"]}
    ]
    d["sources"][0]["coverage_state"] = "partial"
    p = module.query(snapshot(d), QUERY)
    assert p["metrics_by_currency"][0]["revenue"]["amount"] is None
    assert p["metrics_by_currency"][0]["profit"]["amount"] is None
    before = copy.deepcopy(p)
    report = module.render_report(p, "markdown").decode()
    assert "| Revenue | Unavailable (unavailable) |" in report
    assert "| Operating profit | Unavailable (unavailable) |" in report
    assert report.count("| Revenue contribution | Spend contribution |") == 5
    assert "Breakdowns and trends show spend and revenue contributions" in report
    assert "from selected supported records." in report
    assert "A zero revenue contribution means no selected supported income contribution" in report
    assert "for expense-only records without income coverage" in report
    assert "total revenue and operating profit remain unavailable" in report
    assert all(
        group["revenue_minor"] == "0" for groups in p["breakdowns"].values() for group in groups
    )
    assert all(row["revenue_minor"] == "0" for row in p["trend"])
    assert p == before
    assert json.loads(module.render_report(p, "json")) == before
    d["business_profiles"].append(
        record(
            "business",
            version=1,
            business_ref="business:one",
            accounting_basis={"value": "accrual", "source_refs": ["source:bank"]},
        )
    )
    assert any(
        x["reason"] == "policy_conflict" for x in module.query(snapshot(d), QUERY)["exclusions"]
    )


def save_fixture(tmp_path):
    module = management()
    path = module.workspace.initialize(tmp_path / "finance")
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = fixture_document()
        d["revision"] = 1
        session.commit(d)
    return module, path


def intent(s, **overrides):
    return {
        "request_id": "synthetic-request-1",
        "expected_snapshot": {"revision": s["revision"], "sha256": s["sha256"]},
        "action": "correct-bookkeeping",
        "record_ref": "book:expense",
        "fields": {"date": "0001-01-01"},
        "source_refs": ["source:bank"],
        "reason": "Explicit synthetic correction",
        **overrides,
    }


@pytest.mark.parametrize(
    "currency,minor,exponent,display",
    [
        ("JPY", "123", 0, "JPY 123"),
        ("USD", "9007199254740993", 2, "USD 90071992547409.93"),
        ("KWD", "1234", 3, "KWD 1.234"),
    ],
)
def test_precision_unclassified_create_roundtrip(tmp_path, currency, minor, exponent, display):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    i = intent(
        s,
        action="create-bookkeeping",
        record_ref=None,
        fields={
            "date": "2026-09-04",
            "amount": {"minor": minor, "currency": currency, "exponent": exponent},
            "category": {"state": "unresolved"},
        },
    )
    before = path.read_bytes()
    preview = module.preview(s, i)
    assert path.read_bytes() == before
    receipt = module.commit(path, i, preview["preview_digest"])
    new = next(r for r in receipt["changed_refs"] if r.endswith(":record"))
    fresh = module.read_snapshot(path)
    detail = module.record_detail(fresh, new)
    assert detail["amount"] == {"minor": minor, "currency": currency, "exponent": exponent}
    assert detail["record"]["amount"] == {"amount_minor": int(minor), "currency": currency}
    p = module.query(fresh, QUERY)
    row = next(r for r in p["rows"] if r["record_ref"] == new)
    assert row["amount"] == detail["amount"]
    assert row["reporting"] is None and row["metric_exclusion"] == "missing_metadata"
    assert all(
        new not in m[axis]["included_record_refs"]
        for m in p["metrics_by_currency"]
        for axis in ("spend", "revenue", "profit")
    )
    assert display in module.render_report(p, "markdown").decode()
    assert (
        next(
            r for r in json.loads(module.render_report(p, "json"))["rows"] if r["record_ref"] == new
        )["amount"]["exponent"]
        == exponent
    )
    import csv
    import io

    exported = list(csv.DictReader(io.StringIO(module.render_report(p, "csv").decode())))
    assert next(r for r in exported if r["record_ref"] == new)["exponent"] == str(exponent)
    assert module.commit(path, i, preview["preview_digest"])["idempotency_replay"] is True
    with pytest.raises(module.ManagementError, match="intent conflict"):
        module.commit(
            path,
            {
                **i,
                "fields": {
                    **i["fields"],
                    "amount": {**i["fields"]["amount"], "exponent": (exponent + 1) % 7},
                },
            },
            preview["preview_digest"],
        )


def test_correction_history_reads_full_native_chain_for_current_and_prior(tmp_path):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    a = intent(
        s, request_id="history-a", fields={"date": "2026-09-02"}, reason="Alpha source correction"
    )
    ar = module.commit(path, a, module.preview(s, a)["preview_digest"])
    anew = next(r for r in ar["changed_refs"] if r.endswith(":record"))
    s = module.read_snapshot(path)
    b = intent(
        s,
        request_id="history-b",
        record_ref=anew,
        fields={"business_purpose": "Supported synthetic purpose"},
        reason="Beta purpose correction",
    )
    br = module.commit(path, b, module.preview(s, b)["preview_digest"])
    bnew = next(r for r in br["changed_refs"] if r.endswith(":record"))
    d = module.read_snapshot(path)["document"]
    # Document/lexical/time order deliberately differ from the canonical chain.
    d["corrections"].reverse()
    by_reason = {r["reason"]: r for r in d["corrections"]}
    by_reason["Alpha source correction"]["status_history"][-1].update(
        actor="fixture:alpha", at="2026-10-02T00:00:00Z"
    )
    by_reason["Beta purpose correction"]["status_history"][-1].update(
        actor="fixture:beta", at="2026-10-01T00:00:00Z"
    )
    before = path.read_bytes()
    for ref in ["book:expense", anew, bnew]:
        detail = module.record_detail(snapshot(d), ref)
        history = detail["correction_history"]
        assert [r["reason"] for r in history] == [
            "Alpha source correction",
            "Beta purpose correction",
        ]
        assert [r["actor"] for r in history] == ["fixture:alpha", "fixture:beta"]
        assert [r["at"] for r in history] == ["2026-10-02T00:00:00Z", "2026-10-01T00:00:00Z"]
        assert all(
            r["source_refs"] == ["source:bank"] and r["evidence_refs"] == ["source:bank"]
            for r in history
        )
        assert [r["correction_ref"] for r in history] == detail["lineage"]
        assert detail["original"]["date"] == "2026-09-03" and detail["current_ref"] == bnew
    assert path.read_bytes() == before


def precision_metadata(document, target, exponent=2, **overrides):
    module = management()
    return setting(
        document,
        "precision:" + target["record_id"],
        "finance-management-precision:" + target["record_id"],
        {
            "management_schema_version": "finance-management-v1",
            "kind": "record-precision",
            "target_ref": target["record_id"],
            "target_digest_sha256": module.workspace.material_record_digest(target, "bookkeeping"),
            "currency": target["amount"]["currency"],
            "exponent": exponent,
            "source_refs": ["source:bank"],
            **overrides,
        },
        scopes=["finance-management", "amount-precision"],
    )


@pytest.mark.parametrize(
    "case",
    [
        "proposed",
        "no-review",
        "wrong-scope",
        "stale-review",
        "stale-target",
        "wrong-currency",
        "unknown-source",
        "stale-source",
        "conflicting",
        "reporting-conflict",
    ],
)
def test_precision_invalid_material_never_uses_reporting_fallback(case):
    module = management()
    d = fixture_document()
    target = next(r for r in d["bookkeeping"] if r["record_id"] == "book:expense")
    payload_overrides = (
        {"currency": "JPY"}
        if case == "wrong-currency"
        else {"source_refs": ["source:missing"]}
        if case == "unknown-source"
        else {}
    )
    precision = precision_metadata(
        d, target, 3 if case == "reporting-conflict" else 2, **payload_overrides
    )
    review = next(r for r in d["reviews"] if r["target_ref"] == precision["record_id"])
    if case == "proposed":
        precision["status"] = "proposed"
    if case == "no-review":
        precision["decision_refs"] = []
    if case == "wrong-scope":
        review["scope"] = ["finance-management"]
    if case == "stale-review":
        review["target_digest_sha256"] = "f" * 64
    if case == "stale-target":
        target["date"] = "2026-09-04"
    if case == "stale-source":
        source = copy.deepcopy(d["sources"][0])
        source.update(record_id="source:replacement", supersedes_ref="source:bank")
        d["sources"].append(source)
    if case == "conflicting":
        other = copy.deepcopy(precision)
        other["record_id"] = "precision:conflict"
        d["operating_settings"].append(other)
    s = snapshot(d)
    row = next(r for r in module.query(s, QUERY)["rows"] if r["record_ref"] == target["record_id"])
    assert row["amount"]["exponent"] is None
    assert any(g["field"] == "amount.exponent" for g in row["gaps"])
    assert module.record_detail(s, target["record_id"])["amount"]["exponent"] is None
    assert (
        "book:expense"
        not in module.query(s, QUERY)["metrics_by_currency"][0]["spend"]["included_record_refs"]
    )


@pytest.mark.parametrize("exponent", [True, "2", -1, 7, None])
def test_precision_input_is_strict_and_bounded(tmp_path, exponent):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    before = path.read_bytes()
    with pytest.raises(module.ManagementError, match="invalid money"):
        module.preview(
            s,
            intent(
                s, fields={"amount": {"minor": "2000", "currency": "USD", "exponent": exponent}}
            ),
        )
    assert path.read_bytes() == before


def test_precision_legacy_optional_and_nonamount_rebind_only(tmp_path):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    create = intent(
        s,
        action="create-bookkeeping",
        record_ref=None,
        fields={
            "date": "2026-09-04",
            "amount": {"minor": "2000", "currency": "USD", "exponent": 2},
            "category": {"state": "unresolved"},
        },
    )
    cr = module.commit(path, create, module.preview(s, create)["preview_digest"])
    current = next(r for r in cr["changed_refs"] if r.endswith(":record"))
    s = module.read_snapshot(path)
    edit = intent(
        s, request_id="precision-nonamount", record_ref=current, fields={"date": "2026-09-05"}
    )
    er = module.commit(path, edit, module.preview(s, edit)["preview_digest"])
    current = next(r for r in er["changed_refs"] if r.endswith(":record"))
    assert module.record_detail(module.read_snapshot(path), current)["amount"]["exponent"] == 2
    s = module.read_snapshot(path)
    edit = intent(
        s,
        request_id="precision-legacy-money",
        record_ref=current,
        fields={"amount": {"minor": "3000", "currency": "USD"}},
    )
    er = module.commit(path, edit, module.preview(s, edit)["preview_digest"])
    current = next(r for r in er["changed_refs"] if r.endswith(":record"))
    s = module.read_snapshot(path)
    assert module.record_detail(s, current)["amount"]["exponent"] is None
    assert not any(
        r["scope"] == "finance-management-precision:" + current
        for r in s["document"]["operating_settings"]
    )
    legacy = intent(
        s,
        request_id="legacy-create",
        action="create-bookkeeping",
        record_ref=None,
        fields={
            "date": "2026-09-06",
            "amount": {"minor": "4000", "currency": "USD"},
            "category": {"state": "unresolved"},
        },
    )
    lr = module.commit(path, legacy, module.preview(s, legacy)["preview_digest"])
    legacy_ref = next(r for r in lr["changed_refs"] if r.endswith(":record"))
    assert (
        module.record_detail(module.read_snapshot(path), legacy_ref)["amount"]["exponent"] is None
    )


def test_precision_nonamount_correction_does_not_rebind_stale_review(tmp_path):
    module, path = save_fixture(tmp_path)
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = copy.deepcopy(session.document)
        target = next(r for r in d["bookkeeping"] if r["record_id"] == "book:expense")
        meta = precision_metadata(d, target)
        next(r for r in d["reviews"] if r["target_ref"] == meta["record_id"])["scope"] = [
            "finance-management"
        ]
        d["revision"] += 1
        session.commit(d)
    s = module.read_snapshot(path)
    i = intent(s, fields={"date": "2026-09-04"})
    result = module.commit(path, i, module.preview(s, i)["preview_digest"])
    new = next(r for r in result["changed_refs"] if r.endswith(":record"))
    assert module.record_detail(module.read_snapshot(path), new)["amount"]["exponent"] is None


def test_precision_receipt_correction_and_attested_create_are_format_only(tmp_path):
    module, path = save_fixture(tmp_path)
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = copy.deepcopy(session.document)
        retained = session.retain_original(
            "attachments/2026/09/units.txt", b"synthetic units source"
        )
        d["attachments"].append(
            record(
                "attachment:units",
                "retained",
                source_ref="source:bank",
                role="original",
                media_type="text/plain",
                original_filename="units.txt",
                **retained,
            )
        )
        d["sources"][0]["attachment_refs"].append("attachment:units")
        d["receipts"].append(
            record(
                "receipt:units",
                "prepared",
                business_date="2026-09-02",
                merchant="Synthetic",
                amount=money(2000),
                source_ref="source:bank",
                attachment_refs=["attachment:units"],
            )
        )
        d["revision"] += 1
        session.commit(d)
    s = module.read_snapshot(path)
    i = intent(
        s,
        action="correct-receipt-facts",
        record_ref="receipt:units",
        fields={"amount": {"minor": "2000", "currency": "USD", "exponent": 2}},
    )
    r = module.commit(path, i, module.preview(s, i)["preview_digest"])
    new = next(x for x in r["changed_refs"] if x.endswith(":record"))
    s = module.read_snapshot(path)
    assert module.record_detail(s, new)["amount"] == {
        "minor": "2000",
        "currency": "USD",
        "exponent": 2,
    }
    row = next(r for r in module.query(s, QUERY)["rows"] if r["record_ref"] == new)
    assert row["reporting"] is None and row["kind"] == "receipt"
    i = intent(
        s,
        request_id="precision-attestation",
        action="create-bookkeeping",
        record_ref=None,
        source_refs=[],
        attestation="Operator source with explicit units",
        fields={
            "date": "2026-09-06",
            "amount": {"minor": "1234", "currency": "KWD", "exponent": 3},
            "category": {"state": "unresolved"},
        },
    )
    r = module.commit(path, i, module.preview(s, i)["preview_digest"])
    new = next(x for x in r["changed_refs"] if x.endswith(":record"))
    s = module.read_snapshot(path)
    assert module.record_detail(s, new)["amount"]["exponent"] == 3
    assert (
        next(r for r in module.query(s, QUERY)["rows"] if r["record_ref"] == new)[
            "metric_exclusion"
        ]
        == "missing_metadata"
    )


def test_correction_history_keeps_exact_events_and_ignores_nonapplied_or_version_refs():
    module = management()
    d = fixture_document()
    old = d["bookkeeping"][1]
    new = copy.deepcopy(old)
    new["record_id"] = "book:next"
    new["supersedes_ref"] = old["record_id"]
    d["bookkeeping"].append(new)
    for ref, state in [
        ("correction:z", "applied"),
        ("correction:a", "applied"),
        ("correction:proposal", "proposed"),
    ]:
        c = record(
            ref,
            state,
            superseded_ref=old["record_id"],
            replacement_ref=new["record_id"],
            reason=ref,
            decision_ref="fixture:decision",
        )
        c["provenance"].append(
            {
                "kind": "correction-evidence",
                "ref": "source:bank",
                "observed_at": "2026-09-06T12:00:00Z",
            }
        )
        if ref == "correction:z":
            c["status_history"] = []
        d["corrections"].append(c)
    detail = module.record_detail(snapshot(d), new["record_id"])
    assert detail["lineage"] == ["correction:z", "correction:a", new["record_id"]]
    history = detail["correction_history"]
    assert [r["correction_ref"] for r in history] == ["correction:z", "correction:a"]
    assert (
        history[0]["actor"] is None
        and history[0]["at"] is None
        and history[0]["evidence_refs"] == []
    )
    assert all(
        r["source_refs"] == ["source:bank"] and r["decision_ref"] == "fixture:decision"
        for r in history
    )
    assert detail["record"] == new and detail["original"] == old


def test_preview_cancel_commit_legacy_immediate_and_idempotent(tmp_path):
    module, path = save_fixture(tmp_path)
    before = path.read_bytes()
    s = module.read_snapshot(path)
    i = intent(s)
    p = module.preview(s, i)
    assert path.read_bytes() == before
    receipt = module.commit(path, i, p["preview_digest"])
    assert receipt["reread_result"] == "verified"
    assert receipt["snapshot"]["revision"] == 2
    current = module.record_detail(module.read_snapshot(path), "book:expense")
    assert current["record"]["date"] == "0001-01-01"
    assert current["current_ref"] != "book:expense"
    assert (
        next(
            x
            for x in module.workspace.read_document(path)["bookkeeping"]
            if x["record_id"] == "book:expense"
        )["date"]
        == "2026-09-03"
    )
    assert module.commit(path, i, p["preview_digest"])["idempotency_replay"] is True
    assert module.workspace.read_document(path)["revision"] == 2
    changed = {**i, "reason": "changed same intent"}
    with pytest.raises(ValueError, match="intent conflict"):
        module.commit(path, changed, p["preview_digest"])
    assert any(
        x["reason"] == "stale_metadata"
        for x in module.query(module.read_snapshot(path), {**QUERY, "start": "0001-01-01"})[
            "exclusions"
        ]
    )


def test_authority_rechecked_inside_writer_lock_before_replay_or_mutation(tmp_path, monkeypatch):
    from contextlib import contextmanager

    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    i = intent(s)
    p = module.preview(s, i)
    before = path.read_bytes()
    original = module.workspace.exclusive_lock
    state = {"locked": False, "revoked": False}

    @contextmanager
    def revoked_while_acquiring(root):
        with original(root):
            state.update(locked=True, revoked=True)
            yield

    def authorize():
        assert state["locked"]
        if state["revoked"]:
            raise module.ManagementError("authority_revoked")

    monkeypatch.setattr(module.workspace, "exclusive_lock", revoked_while_acquiring)
    with pytest.raises(module.ManagementError, match="authority revoked"):
        module.commit(path, i, p["preview_digest"], authorize=authorize)
    assert path.read_bytes() == before


def test_legacy_receipt_date_correction_and_provider_fields_denied(tmp_path):
    module, path = save_fixture(tmp_path)
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = copy.deepcopy(session.document)
        d["receipts"].append(
            record(
                "receipt:legacy",
                "prepared",
                source_ref="source:bank",
                attachment_refs=["attachment:original"],
                amount=money(500),
                business_date="0001-01-01",
            )
        )
        retained = session.retain_original(
            "attachments/2026/09/original.txt", b"synthetic original"
        )
        d["attachments"].append(
            record(
                "attachment:original",
                "retained",
                source_ref="source:bank",
                role="original",
                media_type="text/plain",
                original_filename="original.txt",
                **retained,
            )
        )
        d["sources"][0]["attachment_refs"].append("attachment:original")
        d["revision"] += 1
        session.commit(d)
    s = module.read_snapshot(path)
    original = (path.parent / "attachments/2026/09/original.txt").read_bytes()
    i = intent(
        s,
        action="correct-receipt-facts",
        record_ref="receipt:legacy",
        fields={"business_date": "2026-09-02"},
    )
    module.commit(path, i, module.preview(s, i)["preview_digest"])
    assert (
        module.record_detail(module.read_snapshot(path), "receipt:legacy")["record"][
            "business_date"
        ]
        == "2026-09-02"
    )
    assert (path.parent / "attachments/2026/09/original.txt").read_bytes() == original
    s = module.read_snapshot(path)
    with pytest.raises(ValueError, match="unsupported field"):
        module.preview(s, intent(s, fields={"provider_status": "paid"}))
    assert module.workspace.read_document(path)["schema_version"] == "local-json-v1"


def test_stale_writer_and_unknown_postreplace_retry(tmp_path, monkeypatch):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    i = intent(s)
    p = module.preview(s, i)
    original = module.workspace.atomic_write

    def failed_after_replace(target, data):
        original(target, data)
        raise OSError("synthetic lost readback")

    monkeypatch.setattr(module.workspace, "atomic_write", failed_after_replace)
    with pytest.raises(module.OutcomeUnknown):
        module.commit(path, i, p["preview_digest"])
    monkeypatch.setattr(module.workspace, "atomic_write", original)
    assert module.commit(path, i, p["preview_digest"])["idempotency_replay"]
    assert module.workspace.read_document(path)["revision"] == 2
    with pytest.raises(ValueError, match="stale"):
        module.commit(path, {**i, "request_id": "synthetic-request-2"}, p["preview_digest"])


def test_source_attestation_create_and_csv_formula_safety(tmp_path):
    import csv
    import io

    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    i = intent(
        s,
        action="create-bookkeeping",
        record_ref=None,
        fields={
            "date": "2026-09-02",
            "amount": {"minor": "1234", "currency": "KWD"},
            "category": {"state": "unresolved"},
            "business_purpose": "\n =HYPERLINK()",
        },
        source_refs=[],
        attestation="Operator attests a synthetic expense; no original",
    )
    r = module.commit(path, i, module.preview(s, i)["preview_digest"])
    d = module.workspace.read_document(path)
    assert d["revision"] == 2
    new = next(x for x in d["bookkeeping"] if x["record_id"] in r["changed_refs"])
    assert new["gaps"] and new["source_refs"]
    p = module.query(module.read_snapshot(path), QUERY)
    rows = list(csv.DictReader(io.StringIO(module.render_report(p, "csv").decode())))
    assert any(
        row["business_purpose"].startswith("'")
        for row in rows
        if "HYPERLINK" in row["business_purpose"]
    )


def analysis_result(s, report_bytes=b"Reviewed synthetic report"):
    return {
        "format": "finance-management-result-v1",
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "request_id": "synthetic-analysis-1",
        "source_snapshot": {"revision": s["revision"], "sha256": s["sha256"]},
        "query": QUERY,
        "producer_ref": "agent:producer",
        "run_ref": "run:synthetic",
        "created_at": "2026-09-06T12:00:00Z",
        "state": "no-supported-opportunity",
        "opportunities": [],
        "evidence_refs": ["book:expense"],
        "gaps": ["No supported opportunity in this synthetic scope"],
        "review": {
            "reviewer_ref": "agent:reviewer",
            "run_ref": "run:synthetic-review",
            "verdict": "approved",
            "reviewed_at": "2026-09-06T13:00:00Z",
            "target_digest_sha256": "",
        },
    }


def test_external_analysis_publication_review_independence_and_staleness(tmp_path):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    result = analysis_result(s)
    result["review"]["target_digest_sha256"] = module.result_digest(result)
    before = path.read_bytes()
    receipt = module.publish_result(path, result, b"Reviewed synthetic report")
    assert path.read_bytes() == before
    loaded = module.read_result(
        path,
        receipt["external_result_ref"],
        expected_digest=receipt["result_sha256"],
        expected_query=QUERY,
    )
    assert loaded["state"] == "current" and loaded["result"]["state"] == "no-supported-opportunity"
    bad = copy.deepcopy(result)
    bad["review"]["reviewer_ref"] = bad["producer_ref"]
    with pytest.raises(ValueError, match="independent"):
        module.validate_result(bad, s)
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = copy.deepcopy(session.document)
        d["revision"] += 1
        session.commit(d)
    assert module.read_result(path, receipt["external_result_ref"])["state"] == "stale"
    with pytest.raises(ValueError):
        module.read_result(path, "../outside")


def test_precision_conflict_totals_breakdowns_and_rows_agree():
    module = management()
    d = fixture_document()
    setting_row = next(x for x in d["operating_settings"] if x["record_id"] == "policy:reporting")
    payload = json.loads(setting_row["policy_text"])
    payload["currency_precision"][0]["exponent"] = 3
    setting_row["policy_text"] = json.dumps(payload)
    review = next(x for x in d["reviews"] if x["target_ref"] == setting_row["record_id"])
    review["target_digest_sha256"] = module.workspace.material_record_digest(
        setting_row, "operating_settings"
    )
    p = module.query(snapshot(d), QUERY)
    assert p["metrics_by_currency"][0]["spend"]["amount"] is None
    assert p["breakdowns"]["category"] == []
    assert all(r["metric_exclusion"] == "currency_precision_conflict" for r in p["rows"])


def test_coverage_cannot_certify_an_account_outside_approved_scope():
    module = management()
    d = fixture_document()
    b = d["bookkeeping"][0]
    b["account_ref"] = "unknown-account"
    s = next(
        x
        for x in d["operating_settings"]
        if x["scope"] == "finance-management-record:" + b["record_id"]
    )
    payload = json.loads(s["policy_text"])
    payload["target_digest_sha256"] = module.workspace.material_record_digest(b, "bookkeeping")
    s["policy_text"] = json.dumps(payload)
    next(x for x in d["reviews"] if x["target_ref"] == s["record_id"])["target_digest_sha256"] = (
        module.workspace.material_record_digest(s, "operating_settings")
    )
    assert (
        module.query(snapshot(d), QUERY)["metrics_by_currency"][0]["profit"]["state"] == "partial"
    )


def test_safe_validation_error_for_corrupt_workspace_and_invalid_date(tmp_path):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    with pytest.raises(module.ManagementError) as error:
        module.preview(s, intent(s, fields={"date": "not-a-date"}))
    assert error.value.code == "invalid_candidate"
    d = json.loads(path.read_text())
    d["bookkeeping"][0]["amount"]["currency"] = "bad"
    path.write_text(json.dumps(d))
    with pytest.raises(module.ManagementError) as error:
        module.read_snapshot(path)
    assert error.value.code == "corrupt_workspace"
    assert str(path) not in str(error.value)


def test_paged_export_requires_same_snapshot_and_exports_full_population():
    module = management()
    s = snapshot(fixture_document())
    p = module.query(s, {**QUERY, "limit": 1})
    with pytest.raises(module.ManagementError, match="report population incomplete"):
        module.render_report(p, "csv")
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(module.render_report(p, "csv", snapshot=s).decode())))
    assert len(rows) == p["page"]["total"] == 8
    assert all(
        row["recognition_basis"] == "cash" and row["date_basis"] == "transaction" for row in rows
    )
    assert all(
        row["period_start"] == QUERY["start"] and row["coverage_state"] == "complete"
        for row in rows
    )


def test_markdown_report_is_readable_source_bound_and_full_population():
    module = management()
    s = snapshot(fixture_document())
    p = module.query(s, {**QUERY, "limit": 1})
    with pytest.raises(module.ManagementError, match="report population incomplete"):
        module.render_report(p, "markdown")
    report = module.render_report(p, "markdown", snapshot=s).decode()
    assert "```json" not in report
    assert "## Supported totals" in report
    assert "Operating profit" in report
    assert "USD 840.00" in report
    assert "USD 950.00" in report
    assert "USD 110.00" in report
    assert "USD 20.00" in report
    assert s["sha256"] in report
    assert "2026-09-01 through 2026-09-30" in report
    assert "Recognition basis: cash" in report
    assert "Date basis: transaction" in report
    assert "Timezone: UTC" in report
    assert "Coverage: complete" in report
    assert "8 records" in report
    assert "source:bank" in report
    assert all(row["record_id"] in report for row in s["document"]["bookkeeping"])
    assert "## Breakdowns" in report and "## Trend" in report
    assert "Unknown/unassigned" in report
    assert s["document"] == fixture_document()


def test_markdown_report_exact_precision_unknown_and_escaped_source_text():
    module = management()
    p = module.query(snapshot(fixture_document()), QUERY)
    p["rows"][0]["counterparty"] = "<script>x</script>|[click](https://unsafe)\n# Inject"
    p["rows"][0]["amount"] = {"minor": "9007199254740993", "currency": "USD", "exponent": 2}
    p["rows"][1]["amount"] = {"minor": "-1234", "currency": "KWD", "exponent": 3}
    p["rows"][2]["amount"] = {"minor": "42", "currency": "JPY", "exponent": 0}
    p["rows"][3]["amount"] = {"minor": "42", "currency": "USD", "exponent": None}
    p["metrics_by_currency"][0]["revenue"]["amount"] = None
    p["metrics_by_currency"][0]["revenue"]["state"] = "unavailable"
    report = module.render_report(p, "markdown").decode()
    assert "USD 90071992547409.93" in report
    assert "KWD -1.234" in report and "JPY 42" in report
    assert "42 minor units (precision unavailable)" in report
    assert "Unavailable (unavailable)" in report
    assert "<script>" not in report and "[click](https://unsafe)" not in report
    assert "&lt;script&gt;" in report and r"\|" in report and r"\[click\]" in report
    assert "\n# Inject" not in report


def test_markdown_report_keeps_comparison_review_exclusion_and_undated_states():
    module = management()
    d = fixture_document()
    d["operating_settings"] = []
    p = module.query(
        snapshot(d), {**QUERY, "comparison": {"start": "2026-08-01", "end": "2026-08-31"}}
    )
    report = module.render_report(p, "markdown").decode()
    assert "Coverage: unavailable" in report
    assert r"missing\_or\_conflicting\_reporting\_policy" in report
    assert "## Comparison" in report
    assert "2026-08-01 through 2026-08-31" in report
    assert "## Review and exclusions" in report
    assert r"missing\_metadata" in report and r"inspect\_source\_then\_correct" in report
    assert "Undated or period-only evidence: None" in report
    assert "prepared/unposted" in report


def test_missing_original_is_inspectable_and_master_bytes_unchanged(tmp_path):
    module, path = save_fixture(tmp_path)
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = copy.deepcopy(session.document)
        retained = session.retain_original("attachments/2026/09/missing.txt", b"synthetic original")
        d["attachments"].append(
            record(
                "attachment:missing",
                "retained",
                source_ref="source:bank",
                role="original",
                media_type="text/plain",
                original_filename="missing.txt",
                **retained,
            )
        )
        d["sources"][0]["attachment_refs"].append("attachment:missing")
        d["revision"] += 1
        session.commit(d)
    (path.parent / "attachments/2026/09/missing.txt").unlink()
    before = path.read_bytes()
    s = module.read_snapshot(path)
    assert s["custody_issues"] == [{"attachment_ref": "attachment:missing", "state": "unavailable"}]
    assert module.query(s, QUERY)["metrics_by_currency"][0]["profit"]["amount"] is None
    assert path.read_bytes() == before


def test_publication_interruption_cannot_be_loaded_or_silently_replaced(tmp_path, monkeypatch):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    result = analysis_result(s, b"synthetic report")
    result["review"]["target_digest_sha256"] = module.result_digest(result)
    original = module.workspace.atomic_write

    def fail_marker(target, data):
        if target.name == "manifest.json":
            raise OSError("interrupted publication")
        original(target, data)

    monkeypatch.setattr(module.workspace, "atomic_write", fail_marker)
    with pytest.raises(OSError):
        module.publish_result(path, result, b"synthetic report")
    with pytest.raises(module.ManagementError):
        module.read_result(path, "finance-analysis:synthetic-analysis-1")
    monkeypatch.setattr(module.workspace, "atomic_write", original)
    receipt = module.publish_result(path, result, b"synthetic report")
    with pytest.raises(module.ManagementError, match="unreviewed report"):
        module.publish_result(path, result, b"changed report")
    changed_result = copy.deepcopy(result)
    changed_result["report_sha256"] = hashlib.sha256(b"changed report").hexdigest()
    changed_result["review"]["target_digest_sha256"] = module.result_digest(changed_result)
    with pytest.raises(module.ManagementError, match="publication conflict"):
        module.publish_result(path, changed_result, b"changed report")
    result_path = path.parent / "exports/analysis/synthetic-analysis-1/result.json"
    result_path.write_bytes(b"changed")
    with pytest.raises(module.ManagementError, match="publication integrity"):
        module.read_result(path, receipt["external_result_ref"])


def test_corrected_receipt_invalidates_unchanged_prepared_financial_dependency(tmp_path):
    module, path = save_fixture(tmp_path)
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = copy.deepcopy(session.document)
        original = session.retain_original(
            "attachments/2026/09/invoice-0001.txt", b"synthetic invoice original"
        )
        d["attachments"].append(
            record(
                "attachment:invoice",
                "retained",
                source_ref="source:bank",
                role="original",
                media_type="text/plain",
                original_filename="invoice-0001.txt",
                **original,
            )
        )
        d["sources"][0]["attachment_refs"].append("attachment:invoice")
        receipt = record(
            "receipt:invoice",
            "prepared",
            source_ref="source:bank",
            attachment_refs=["attachment:invoice"],
            amount=money(10000),
            business_date="2026-09-03",
        )
        d["receipts"].append(receipt)
        book = next(x for x in d["bookkeeping"] if x["record_id"] == "book:expense")
        book["receipt_refs"] = [receipt["record_id"]]
        s = next(
            x
            for x in d["operating_settings"]
            if x["scope"] == "finance-management-record:book:expense"
        )
        payload = json.loads(s["policy_text"])
        payload["target_digest_sha256"] = module.workspace.material_record_digest(
            book, "bookkeeping"
        )
        s["policy_text"] = json.dumps(payload)
        next(x for x in d["reviews"] if x["target_ref"] == s["record_id"])[
            "target_digest_sha256"
        ] = module.workspace.material_record_digest(s, "operating_settings")
        d["revision"] += 1
        session.commit(d)
    before = module.read_snapshot(path)
    assert (
        "book:expense"
        in module.query(before, QUERY)["metrics_by_currency"][0]["spend"]["included_record_refs"]
    )
    i = intent(
        before,
        action="correct-receipt-facts",
        record_ref="receipt:invoice",
        fields={"date": "2026-09-04"},
    )
    module.commit(path, i, module.preview(before, i)["preview_digest"])
    after = module.read_snapshot(path)
    assert (
        next(x for x in after["document"]["bookkeeping"] if x["record_id"] == "book:expense")
        == book
    )
    p = module.query(after, QUERY)
    assert "book:expense" not in p["metrics_by_currency"][0]["spend"]["included_record_refs"]
    assert any(
        x == {"record_ref": "book:expense", "reason": "stale_dependency"} for x in p["exclusions"]
    )
    assert module.query(after, {**QUERY, "q": "invoice-0001"})["page"]["total"] >= 1


@pytest.mark.parametrize(
    "filter_kind",
    ["account", "project", "search", "date", "currency", "category", "kind", "review"],
)
def test_global_economic_conflict_cannot_be_restored_by_presentation_filter(filter_kind):
    module = management()
    d = fixture_document()
    expense = next(x for x in d["bookkeeping"] if x["record_id"] == "book:expense")
    expense["external_project_ref"] = "project:A"
    expense["business_purpose"] = "only-first-record"
    expense["category"] = {"state": "unresolved", "category_ref": "category:selected"}
    meta = next(
        x for x in d["operating_settings"] if x["scope"] == "finance-management-record:book:expense"
    )
    payload = json.loads(meta["policy_text"])
    payload["target_digest_sha256"] = module.workspace.material_record_digest(
        expense, "bookkeeping"
    )
    meta["policy_text"] = json.dumps(payload)
    next(x for x in d["reviews"] if x["target_ref"] == meta["record_id"])[
        "target_digest_sha256"
    ] = module.workspace.material_record_digest(meta, "operating_settings")
    duplicate = {
        **copy.deepcopy(expense),
        "record_id": "book:duplicate",
        "account_ref": "bank:other",
        "external_project_ref": "project:B",
        "business_purpose": "other representation",
        "category": {"state": "reviewed", "category_ref": "category:other"},
    }
    if filter_kind == "date":
        duplicate["date"] = "2026-08-31"
    if filter_kind == "currency":
        duplicate["amount"] = money(10000, "JPY")
    d["bookkeeping"].append(duplicate)
    metadata(
        d,
        duplicate,
        economic_event_ref="event:book:expense",
        component_ref="component:book:expense",
        currency_exponent=0 if filter_kind == "currency" else 2,
        treatment="revenue" if filter_kind == "kind" else "expense",
    )
    filters = {
        "account": {"account_ref": "bank:one"},
        "project": {"scope": "external-project", "external_project_ref": "project:A"},
        "search": {"q": "only-first-record"},
        "date": {},
        "currency": {"currency": "USD"},
        "category": {"category_ref": "category:selected"},
        "kind": {"kind": "expense"},
        "review": {"needs_review": True},
    }[filter_kind]
    p = module.query(snapshot(d), {**QUERY, **filters})
    usd = next(x for x in p["metrics_by_currency"] if x["currency"] == "USD")
    assert "book:expense" not in usd["spend"]["included_record_refs"]
    assert usd["spend"]["state"] != "complete"
    assert {"record_ref": "book:expense", "reason": "economic_identity_conflict"} in p["exclusions"]


def test_economic_identity_keeps_cash_and_accrual_recognition_separate():
    module = management()
    d = fixture_document()
    expense = next(x for x in d["bookkeeping"] if x["record_id"] == "book:expense")
    duplicate = {**copy.deepcopy(expense), "record_id": "book:accrual"}
    d["bookkeeping"].append(duplicate)
    metadata(
        d,
        duplicate,
        economic_event_ref="event:book:expense",
        component_ref="component:book:expense",
        recognition_basis="accrual",
    )
    p = module.query(snapshot(d), QUERY)
    assert "book:expense" in p["metrics_by_currency"][0]["spend"]["included_record_refs"]
    assert not any(x["reason"] == "economic_identity_conflict" for x in p["exclusions"])


def test_selected_correction_evidence_retained_in_replacement_correction_and_review(tmp_path):
    module, path = save_fixture(tmp_path)
    with module.workspace.writer(path, module.workspace.digest(path)) as session:
        d = copy.deepcopy(session.document)
        d["sources"].append(
            record(
                "source:new-evidence",
                "verified",
                source_kind="operator-evidence",
                source_identity="synthetic:new-evidence",
                attachment_refs=[],
                coverage_state="partial",
            )
        )
        d["revision"] += 1
        session.commit(d)
    s = module.read_snapshot(path)
    i = intent(
        s, source_refs=["source:new-evidence"], fields={"business_purpose": "Supported correction"}
    )
    receipt = module.commit(path, i, module.preview(s, i)["preview_digest"])
    d = module.workspace.read_document(path)
    new = next(x for x in d["bookkeeping"] if x["record_id"] in receipt["changed_refs"])
    assert set(new["source_refs"]) == {"source:bank", "source:new-evidence"}
    for collection in ("bookkeeping", "corrections", "reviews"):
        relevant = [x for x in d[collection] if x["record_id"] in receipt["changed_refs"]]
        assert all(
            any(p["ref"] == "source:new-evidence" for p in x["provenance"]) for x in relevant
        )
    assert all(
        x["status_history"][-1]["evidence_refs"] == ["source:new-evidence"] for x in relevant
    )


def test_reviewed_result_cannot_publish_or_load_unreviewed_report_text(tmp_path):
    module, path = save_fixture(tmp_path)
    s = module.read_snapshot(path)
    approved = b"Reviewed synthetic report"
    result = analysis_result(s, approved)
    result["review"]["target_digest_sha256"] = module.result_digest(result)
    with pytest.raises(module.ManagementError, match="unreviewed report"):
        module.publish_result(path, result, b"Unreviewed alternative financial claims")
    receipt = module.publish_result(path, result, approved)
    directory = path.parent / "exports/analysis/synthetic-analysis-1"
    altered = b"Unreviewed alternative financial claims"
    (directory / "report.md").write_bytes(altered)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"]["report.md"] = {
        "bytes": len(altered),
        "sha256": hashlib.sha256(altered).hexdigest(),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(module.ManagementError, match="unreviewed report"):
        module.read_result(path, receipt["external_result_ref"])
