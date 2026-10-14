"""Synthetic REST/operator finance boundaries on accepted domain helpers."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from stackos.db.models import AgentRequest, IdempotencyKey, RunPlan, RunPlanStepStatus
from stackos.plugins.builtin_finance import helpers
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.run_plans import RunPlanRepository
from tests.helpers.finance_workspace import record
from tests.unit.test_finance_management import QUERY, analysis_result, fixture_document

WORKSPACE = "finance:synthetic"


def body(response):
    value = response.json()
    return value.get("data", value)


def call(api, name, arguments, *, ui=True, extra_headers=None):
    headers = dict(extra_headers or {})
    if ui:
        headers["Authorization"] = "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]
    return api.post(
        f"/api/v1/operations/{name}/call", json={"arguments": arguments}, headers=headers
    )


@pytest.fixture
def finance(api, project_id, tmp_path):
    enabled = api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable")
    assert enabled.status_code == 200, enabled.text
    module = helpers()
    path = module.workspace.initialize(tmp_path / "external-finance")
    d = fixture_document()
    d["bookkeeping"].append(
        record(
            "book:cancel",
            "prepared/unposted",
            date="0001-01-01",
            amount={"amount_minor": 123, "currency": "USD"},
            source_refs=["source:bank"],
            category={"state": "unresolved"},
        )
    )
    d["bookkeeping"].append(
        record(
            "book:large",
            "prepared/unposted",
            date="2026-09-03",
            amount={"amount_minor": 9007199254740993, "currency": "JPY"},
            source_refs=["source:bank"],
            category={"state": "unresolved"},
        )
    )
    original = path.parent / "attachments/statement/2026/09/original.html"
    original.parent.mkdir(parents=True)
    data = b"<html><script>synthetic only</script></html>"
    original.write_bytes(data)
    d["attachments"].append(
        record(
            "attachment:synthetic",
            "retained",
            source_ref="source:bank",
            role="original",
            relative_path=original.relative_to(path.parent).as_posix(),
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
            media_type="text/html",
            original_filename="original.html",
        )
    )
    d["sources"][0]["attachment_refs"] = ["attachment:synthetic"]
    d["revision"] = 1
    module.workspace.write_document(path, d, module.workspace.digest(path))
    selected = call(
        api,
        "financeWorkspace.select",
        {
            "project_id": project_id,
            "workspace_ref": WORKSPACE,
            "root": str(path.parent),
            "authority_ref": "operator:synthetic-selection",
            "idempotency_key": "select-root",
        },
    )
    assert selected.status_code == 200, selected.text
    access = selected.json()["data"]
    common = {
        "project_id": project_id,
        "workspace_ref": WORKSPACE,
        "access_generation": access["generation"],
    }
    return {
        "path": path,
        "original": original,
        "access": access,
        "common": common,
        "module": module,
        "project_id": project_id,
    }


def snapshot(finance):
    s = finance["module"].read_snapshot(finance["path"])
    return {"revision": s["revision"], "sha256": s["sha256"]}


def edit(finance, request_id="edit-one"):
    return {
        **finance["common"],
        "request_id": request_id,
        "expected_snapshot": snapshot(finance),
        "action": "correct-bookkeeping",
        "record_ref": "book:expense",
        "fields": {"business_purpose": "Sourced synthetic purpose"},
        "source_refs": ["source:bank"],
        "reason": "Operator verified a supported fact",
    }


def test_console_projection_detail_reports_and_no_read_writes(api, finance):
    before = finance["path"].read_bytes()
    projected = call(api, "finance.query", {**finance["common"], **QUERY, "limit": 1})
    assert projected.status_code == 200, projected.text
    data = projected.json()["data"]
    assert data["page"]["total"] > 1 and len(data["rows"]) == 1
    assert data["snapshot"]["sha256"] == snapshot(finance)["sha256"]
    assert data["coverage"]["state"] != "complete"  # unannotated rows remain gaps
    detail = call(api, "finance.record.get", {**finance["common"], "record_ref": "book:large"})
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["record"]["amount"]["amount_minor"] == "9007199254740993"
    cancelled = call(api, "finance.record.get", {**finance["common"], "record_ref": "book:cancel"})
    assert cancelled.json()["data"]["record"]["date"] == "0001-01-01"
    report = call(
        api, "finance.report.render", {**finance["common"], **QUERY, "limit": 1, "format": "json"}
    )
    assert report.status_code == 200, report.text
    exported = json.loads(base64.b64decode(body(report)["content_base64"]))
    assert len(exported["rows"]) == data["page"]["total"]
    assert projected.headers["cache-control"] == report.headers["cache-control"] == "no-store"
    assert str(finance["path"].parent) not in projected.text
    assert finance["path"].read_bytes() == before


def test_undated_legacy_receipt_keeps_ref_and_supported_rows_without_rewriting_master(api, finance):
    module = finance["module"]
    document = module.workspace.read_document(finance["path"])
    document["receipts"].append(
        record(
            "receipt:undated",
            "prepared",
            source_ref="source:bank",
            attachment_refs=["attachment:synthetic"],
            amount={"amount_minor": 42, "currency": "USD"},
            merchant="Synthetic",
        )
    )
    document["revision"] += 1
    module.workspace.write_document(
        finance["path"], document, module.workspace.digest(finance["path"])
    )
    before = finance["path"].read_bytes()
    source = module.read_snapshot(finance["path"])
    canonical = module.query(source, QUERY)
    assert "receipt:undated" in canonical["undated_or_period_only"]
    response = call(api, "finance.query", {**finance["common"], **QUERY})
    assert response.status_code == 200, response.text
    data = body(response)
    assert data["undated_or_period_only"] == canonical["undated_or_period_only"]
    assert all(isinstance(ref, str) for ref in data["undated_or_period_only"])
    assert data["rows"] == canonical["rows"]
    assert data["review_items"] == canonical["review_items"]
    assert data["snapshot"]["sha256"] == source["sha256"]
    assert finance["path"].read_bytes() == before
    detail = call(api, "finance.record.get", {**finance["common"], "record_ref": "receipt:undated"})
    assert detail.status_code == 200, detail.text
    assert "business_date" not in body(detail)["record"] and "date" not in body(detail)["record"]
    assert finance["path"].read_bytes() == before


def test_preview_cancel_commit_replay_conflict_and_revocation(api, finance, settings):
    intent = edit(finance)
    before = finance["path"].read_bytes()
    preview = call(api, "finance.record.preview", intent)
    assert preview.status_code == 200, preview.text
    assert finance["path"].read_bytes() == before  # cancel is simply no commit
    request = {
        **intent,
        "preview_digest": preview.json()["data"]["preview_digest"],
        "idempotency_key": "external-commit",
    }
    first = call(api, "finance.record.commit", request)
    assert first.status_code == 200, first.text
    second = call(api, "finance.record.commit", request)
    assert second.status_code == 200, second.text
    assert second.json()["data"]["idempotency_replay"] is True
    assert (
        second.json()["data"]["snapshot"]["revision"] == intent["expected_snapshot"]["revision"] + 1
    )
    changed = call(
        api, "finance.record.commit", {**request, "fields": {"business_purpose": "Altered intent"}}
    )
    assert changed.status_code == 409, changed.text
    with Session(api.app.state.engine) as session:
        assert not session.exec(
            select(IdempotencyKey).where(IdempotencyKey.idempotency_key == "external-commit")
        ).all()
    revoked = call(
        api,
        "financeWorkspace.revoke",
        {
            "project_id": finance["project_id"],
            "workspace_ref": WORKSPACE,
            "expected_generation": 1,
            "idempotency_key": "revoke-root",
        },
    )
    assert revoked.status_code == 200, revoked.text
    replay = call(api, "finance.record.commit", request)
    assert replay.status_code == 403, replay.text
    assert finance["module"].read_snapshot(finance["path"])["revision"] == 2


def test_sourced_attestation_creation_and_stale_editor(api, finance):
    prior = snapshot(finance)
    new = {
        **finance["common"],
        "request_id": "attested",
        "expected_snapshot": prior,
        "action": "create-bookkeeping",
        "fields": {
            "date": "2026-09-07",
            "amount": {"minor": "1234", "currency": "KWD"},
            "category": {"state": "unresolved"},
        },
        "source_refs": [],
        "attestation": "Explicit operator statement; original not retained",
        "reason": "Operator creates sourced prepared expense",
    }
    p = call(api, "finance.record.preview", new)
    assert p.status_code == 200, p.text
    saved = call(
        api, "finance.record.commit", {**new, "preview_digest": p.json()["data"]["preview_digest"]}
    )
    assert saved.status_code == 200, saved.text
    old = edit(finance, "stale-editor")
    old["expected_snapshot"] = prior
    stale = call(api, "finance.record.preview", old)
    assert stale.status_code == 409
    d = finance["module"].workspace.read_document(finance["path"])
    assert any(s.get("source_kind") == "operator-attestation" and s["gaps"] for s in d["sources"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_token", "fake-secret-run-token"),
        ("trusted_local_admin", True),
        ("actor", "agent:spoof"),
        ("root", "/private/elsewhere"),
        ("provider_status", "paid"),
    ],
)
def test_no_caller_authority_or_unknown_payload_fields(api, finance, field, value):
    response = call(
        api,
        "finance.query",
        {**finance["common"], **QUERY, field: value},
        extra_headers={"X-StackOS-Client-Surface": "cli", "X-Local-Admin": "true"},
    )
    assert response.status_code == 422, response.text
    if isinstance(value, str):
        assert value not in response.text


@pytest.mark.parametrize("mode", ["compact", "ack"])
def test_private_content_never_compacted_or_acknowledged(api, finance, mode):
    response = call(
        api,
        "finance.evidence.read",
        {
            **finance["common"],
            "attachment_ref": "attachment:synthetic",
            "expected_snapshot": snapshot(finance),
            "response_mode": mode,
        },
    )
    assert response.status_code == 422, response.text


def test_private_original_integrity_disposition_and_unsafe_refs(api, finance, tmp_path):
    args = {
        **finance["common"],
        "attachment_ref": "attachment:synthetic",
        "expected_snapshot": snapshot(finance),
    }
    original = call(api, "finance.evidence.read", args)
    assert original.status_code == 200, original.text
    data = body(original)
    assert data["inline_allowed"] is False and data["disposition"] == "attachment"
    assert base64.b64decode(data["content_base64"]) == finance["original"].read_bytes()
    assert original.headers["cache-control"] == "no-store"
    assert (
        api.get("/generated-assets/attachments/statement/2026/09/original.html").status_code == 404
    )
    escaped = call(api, "finance.evidence.read", {**args, "attachment_ref": "../../sentinel"})
    assert escaped.status_code == 403, escaped.text
    sentinel = tmp_path / "outside-secret"
    sentinel.write_text("private synthetic sentinel")
    finance["original"].unlink()
    finance["original"].symlink_to(sentinel)
    unsafe = call(api, "finance.evidence.read", args)
    assert unsafe.status_code == 200 and body(unsafe)["state"] == "unavailable"
    assert "private synthetic sentinel" not in unsafe.text


def test_current_authority_rechecked_inside_financial_lock(api, finance, monkeypatch):
    import fcntl

    from stackos.operations.finance import FinanceWorkspaceRevokeInput
    from stackos.plugins.builtin_finance import FinanceConsole

    i = edit(finance, "race")
    p = call(api, "finance.record.preview", i)
    before = finance["path"].read_bytes()
    original = FinanceConsole.authorize
    calls = []

    def revoked_under_lock(self, inp, mode="read"):
        calls.append(mode)
        if len(calls) == 2:
            fd = os.open(finance["path"].parent / ".writer.lock", os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            self.revoke(
                FinanceWorkspaceRevokeInput(
                    project_id=finance["project_id"], workspace_ref=WORKSPACE, expected_generation=1
                )
            )
        return original(self, inp, mode)

    monkeypatch.setattr(FinanceConsole, "authorize", revoked_under_lock)
    response = call(
        api, "finance.record.commit", {**i, "preview_digest": p.json()["data"]["preview_digest"]}
    )
    assert response.status_code == 403, response.text
    assert finance["path"].read_bytes() == before


def test_wrong_project_disabled_plugin_stale_generation_and_root_swap(api, finance, tmp_path):
    other = api.post(
        "/api/v1/projects",
        json={
            "slug": "other-finance",
            "name": "Other",
            "domain": "other.example.test",
            "niche": "synthetic",
            "locale": "en-US",
        },
    ).json()["data"]["id"]
    assert api.post(f"/api/v1/projects/{other}/plugins/finance/enable").status_code == 200
    duplicate = call(
        api,
        "financeWorkspace.select",
        {
            "project_id": other,
            "workspace_ref": "finance:other",
            "root": str(finance["path"].parent),
            "authority_ref": "operator:other",
        },
    )
    assert duplicate.status_code == 403
    wrong = call(api, "finance.query", {**finance["common"], **QUERY, "project_id": other})
    assert wrong.status_code == 403
    stale = call(api, "finance.query", {**finance["common"], **QUERY, "access_generation": 99})
    assert stale.status_code == 403
    root = finance["path"].parent
    root.rename(tmp_path / "old-root")
    root.mkdir()
    swapped = call(api, "finance.query", {**finance["common"], **QUERY})
    assert swapped.status_code == 403 and "root_changed" in swapped.text


@pytest.mark.parametrize("bad", ["/", "relative", "../escape"])
def test_selection_requires_deliberate_canonical_root(api, finance, bad):
    response = call(
        api,
        "financeWorkspace.select",
        {
            "project_id": finance["project_id"],
            "workspace_ref": "finance:bad",
            "root": bad,
            "authority_ref": "operator:invalid",
        },
    )
    assert response.status_code == 403, response.text


def test_readiness_no_bootstrap_and_config_private(api, finance, settings, tmp_path):
    root = tmp_path / "unconfigured-master"
    root.mkdir()
    selected = call(
        api,
        "financeWorkspace.select",
        {
            "project_id": finance["project_id"],
            "workspace_ref": "finance:empty",
            "root": str(root),
            "authority_ref": "operator:empty",
        },
    )
    assert selected.status_code == 200
    state = call(
        api,
        "financeWorkspace.status",
        {"project_id": finance["project_id"], "workspace_ref": "finance:empty"},
    )
    assert state.status_code == 200 and state.json()["data"]["readiness"] == "not_configured"
    assert not (root / "finance.json").exists()
    config = settings.data_dir / "plugin-state/finance/access.json"
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.parent.stat().st_mode & 0o777 == 0o700
    assert "book:expense" not in config.read_text() and "100000" not in config.read_text()


@pytest.fixture
def analysis_request(api, finance):
    response = call(
        api,
        "localAgentChat.createMessage",
        {
            "project_id": finance["project_id"],
            "thread_key": "finance-status",
            "message_key": "synthetic-status",
            "sender_ref": "local-user:operator",
            "text": "Inspect the selected snapshot.",
            "metadata_json": {
                "workspace_ref": WORKSPACE,
                "access_generation": finance["common"]["access_generation"],
                "source_snapshot": snapshot(finance),
                "query": QUERY,
                "analysis_kind": "opportunities",
            },
        },
    )
    assert response.status_code == 200, response.text
    return body(response)["agent_request"]["id"]


def analysis_status(api, finance, request_id):
    response = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return body(response)


def prepare_analysis_run(api, finance, request_id):
    with Session(api.app.state.engine) as session:
        prepared = (
            AgentRequestRepository(session)
            .prepare_run_plan(
                project_id=finance["project_id"],
                request_id=request_id,
                claimed_by="host:synthetic",
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": "synthetic-status-run",
                    "title": "Synthetic analysis lifecycle",
                    "steps": [{"id": "analyze", "title": "Analyze"}],
                },
            )
            .data
        )
        return prepared.run_plan.id


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        ("blocked", "run-blocked"),
        ("failed", "failed"),
        ("success", "completed_unpublished"),
        ("aborted", "run-aborted"),
    ],
)
def test_analysis_status_reads_canonical_run_without_mutating_queue(
    api, finance, analysis_request, terminal, expected
):
    plan_id = prepare_analysis_run(api, finance, analysis_request)
    master = finance["path"].read_bytes()
    assert analysis_status(api, finance, analysis_request)["state"] == "run-created"
    with Session(api.app.state.engine) as session:
        plans = RunPlanRepository(session)
        started = plans.start(plan_id, project_id=finance["project_id"]).data
        plans.claim_step(
            run_plan_id=plan_id,
            run_id=started.run_id,
            step_id="analyze",
            claimed_by="host:synthetic",
        )
    assert analysis_status(api, finance, analysis_request)["state"] == "run-started"
    with Session(api.app.state.engine) as session:
        plans = RunPlanRepository(session)
        if terminal == "aborted":
            plans.abort(
                run_plan_id=plan_id, project_id=finance["project_id"], reason="Synthetic abort"
            )
        else:
            plans.record_step(
                run_plan_id=plan_id,
                run_id=started.run_id,
                step_id="analyze",
                status=RunPlanStepStatus(terminal),
                error="PRIVATE_FINANCE_STATUS_MARKER" if terminal != "success" else None,
            )
    final = analysis_status(api, finance, analysis_request)
    assert final["state"] == expected
    assert final["next_action"] == "inspect_agent_request"
    assert "PRIVATE_FINANCE_STATUS_MARKER" not in json.dumps(final)
    assert not final.get("report") and not final.get("result")
    with Session(api.app.state.engine) as session:
        assert session.get(AgentRequest, analysis_request).status == "run-created"
    assert finance["path"].read_bytes() == master


@pytest.mark.parametrize("stale", ["source", "access", "missing-source"])
def test_unresolved_analysis_checks_freshness_before_run_status(
    api, finance, analysis_request, stale
):
    prepare_analysis_run(api, finance, analysis_request)
    if stale == "source":
        intent = edit(finance, request_id="synthetic-after-analysis")
        preview = call(api, "finance.record.preview", intent)
        assert preview.status_code == 200, preview.text
        committed = call(
            api,
            "finance.record.commit",
            {**intent, "preview_digest": body(preview)["preview_digest"]},
        )
        assert committed.status_code == 200, committed.text
    elif stale == "access":
        selection = call(
            api,
            "financeWorkspace.select",
            {
                "project_id": finance["project_id"],
                "workspace_ref": WORKSPACE,
                "root": str(finance["path"].parent),
                "authority_ref": "operator:synthetic-reselection",
                "expected_generation": finance["common"]["access_generation"],
                "idempotency_key": "synthetic-reselect-after-analysis",
            },
        )
        assert selection.status_code == 200, selection.text
        finance["common"]["access_generation"] = body(selection)["generation"]
    else:
        with Session(api.app.state.engine) as session:
            row = session.get(AgentRequest, analysis_request)
            metadata = copy.deepcopy(row.metadata_json)
            metadata["metadata_json"].pop("source_snapshot")
            row.metadata_json = metadata
            session.add(row)
            session.commit()
    status = analysis_status(api, finance, analysis_request)
    assert (
        status["state"]
        == {
            "source": "historical_source",
            "access": "historical_access",
            "missing-source": "source_unavailable",
        }[stale]
    )
    assert status["next_action"] == "request_current_analysis"
    assert not status.get("report")


@pytest.mark.parametrize("link", ["missing", "wrong-project", "absent"])
def test_unresolved_analysis_invalid_link_is_safe(
    api, finance, analysis_request, link, monkeypatch
):
    plan_id = prepare_analysis_run(api, finance, analysis_request)
    if link == "missing":
        original_get = Session.get

        def unavailable_plan(session, model, identity, *args, **kwargs):
            if model is RunPlan and identity == plan_id:
                return None
            return original_get(session, model, identity, *args, **kwargs)

        # Emulate an unavailable linked read without violating stored FK custody.
        monkeypatch.setattr(Session, "get", unavailable_plan)
    with Session(api.app.state.engine) as session:
        if link == "wrong-project":
            from stackos.repositories.projects import ProjectRepository

            other = (
                ProjectRepository(session)
                .create(
                    name="Synthetic other",
                    slug="synthetic-other-status",
                    domain="synthetic-other.test",
                )
                .data
            )
            plan = session.get(RunPlan, plan_id)
            plan.project_id = other.id
            session.add(plan)
        elif link == "absent":
            row = session.get(AgentRequest, analysis_request)
            row.run_plan_id = None
            session.add(row)
        session.commit()
    status = analysis_status(api, finance, analysis_request)
    assert {key: status[key] for key in ("state", "request_id", "next_action")} == {
        "state": "run_unavailable",
        "request_id": analysis_request,
        "next_action": "inspect_agent_request",
    }
    assert not status.get("report") and not status.get("result")


def test_unlinked_analysis_queue_and_expiry_preserved(api, finance, analysis_request):
    assert analysis_status(api, finance, analysis_request)["state"] == "awaiting_agent"
    with Session(api.app.state.engine) as session:
        AgentRequestRepository(session).claim(
            project_id=finance["project_id"],
            request_id=analysis_request,
            claimed_by="host:synthetic",
        )
    assert analysis_status(api, finance, analysis_request)["state"] == "claimed"
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, analysis_request)
        row.claim_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        session.add(row)
        session.commit()
    assert analysis_status(api, finance, analysis_request)["state"] == "claim_expired"


def test_analysis_intake_pending_and_exact_reviewed_result(api, finance):
    query = {**QUERY, "workspace_ref": WORKSPACE, "access_generation": 1}
    source = snapshot(finance)
    request = call(
        api,
        "localAgentChat.createMessage",
        {
            "project_id": finance["project_id"],
            "thread_key": "finance",
            "message_key": "analysis",
            "sender_ref": "local-user:operator",
            "text": "Find supported opportunities.",
            "metadata_json": {
                "workspace_ref": WORKSPACE,
                "access_generation": 1,
                "source_snapshot": source,
                "query": query,
                "analysis_kind": "opportunities",
            },
        },
    )
    assert request.status_code == 200, request.text
    # The existing compact envelope keeps the created request, not a fake running state.
    request_id = request.json()["data"]["agent_request"]["id"]
    pending = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert pending.status_code == 200 and body(pending)["state"] == "awaiting_agent"
    result = analysis_result(finance["module"].read_snapshot(finance["path"]))
    result["request_id"] = str(request_id)
    result["query"] = query
    result["review"]["target_digest_sha256"] = finance["module"].result_digest(result)
    publication = finance["module"].publish_result(
        finance["path"], result, b"Reviewed synthetic report"
    )
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, request_id)
        row.status = "resolved"
        row.metadata_json = {
            **row.metadata_json,
            "external_result_ref": publication["external_result_ref"],
            "external_result_sha256": publication["result_sha256"],
            "source_snapshot": source,
            "state": "published",
        }
        session.add(row)
        session.commit()
    completed = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert completed.status_code == 200, completed.text
    assert body(completed)["state"] == "current"
    assert (
        body(completed)["result"]["report_sha256"]
        == hashlib.sha256(b"Reviewed synthetic report").hexdigest()
    )
    changed = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "start": "2026-09-02",
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert changed.status_code == 422 and "analysis_query_mismatch" in changed.text
    other_result = copy.deepcopy(result)
    other_result["request_id"] = str(request_id + 1)
    other_result["review"]["target_digest_sha256"] = finance["module"].result_digest(other_result)
    other_publication = finance["module"].publish_result(
        finance["path"], other_result, b"Reviewed synthetic report"
    )
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, request_id)
        true_metadata = copy.deepcopy(row.metadata_json)
        row.metadata_json = {
            **true_metadata,
            "external_result_ref": other_publication["external_result_ref"],
            "external_result_sha256": other_publication["result_sha256"],
        }
        session.add(row)
        session.commit()
    wrong_request = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert wrong_request.status_code == 422 and "analysis_request_mismatch" in wrong_request.text
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, request_id)
        row.metadata_json = true_metadata
        session.add(row)
        session.commit()
    # Completion aliases and edited source bindings cannot masquerade as a reviewed result.
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, request_id)
        original_metadata = copy.deepcopy(row.metadata_json)
        row.metadata_json = {
            "metadata_json": original_metadata["metadata_json"],
            "finance_analysis_result": publication,
        }
        session.add(row)
        session.commit()
    bad_alias = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert bad_alias.status_code == 200 and body(bad_alias)["state"] == "result_unavailable"
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, request_id)
        row.metadata_json = {
            **original_metadata,
            "source_snapshot": {"revision": 1, "sha256": "0" * 64},
        }
        session.add(row)
        session.commit()
    wrong_source = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert wrong_source.status_code == 200 and body(wrong_source)["state"] == "result_unavailable"
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, request_id)
        row.metadata_json = original_metadata
        session.add(row)
        session.commit()
    report_path = finance["path"].parent / "exports/analysis" / str(request_id) / "report.md"
    report_path.write_bytes(b"UNREVIEWED_FINANCIAL_MARKER")
    # Simulate an attacker updating the unreviewed publication marker too.
    manifest_path = report_path.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["report.md"] = {
        "bytes": report_path.stat().st_size,
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest))
    tampered = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert tampered.status_code == 422 and "UNREVIEWED_FINANCIAL_MARKER" not in tampered.text
    assert "unreviewed_report" in tampered.text
    report_path.write_bytes(b"Reviewed synthetic report")
    manifest["files"]["report.md"] = {
        "bytes": report_path.stat().st_size,
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest))
    document = finance["module"].workspace.read_document(finance["path"])
    document["revision"] += 1
    finance["module"].workspace.write_document(
        finance["path"], document, finance["module"].workspace.digest(finance["path"])
    )
    stale = call(
        api,
        "finance.report.render",
        {
            **finance["common"],
            **QUERY,
            "report_kind": "analysis",
            "analysis_request_id": request_id,
        },
    )
    assert stale.status_code == 200 and body(stale)["state"] == "stale"


def test_disabled_plugin_and_readonly_mode_deny_mutations(api, finance):
    selected = call(
        api,
        "financeWorkspace.select",
        {
            "project_id": finance["project_id"],
            "workspace_ref": WORKSPACE,
            "root": str(finance["path"].parent),
            "modes": ["read"],
            "expected_generation": 1,
            "authority_ref": "operator:readonly",
        },
    )
    assert selected.status_code == 200
    readonly = {**finance["common"], "access_generation": 2}
    assert call(api, "finance.query", {**readonly, **QUERY}).status_code == 200
    assert call(api, "finance.record.preview", {**edit(finance), **readonly}).status_code == 403
    assert call(api, "finance.report.render", {**readonly, **QUERY}).status_code == 403
    assert (
        api.post(f"/api/v1/projects/{finance['project_id']}/plugins/finance/disable").status_code
        == 200
    )
    assert call(api, "finance.query", {**readonly, **QUERY}).status_code == 403


def test_unselected_and_corrupt_authority_never_disclose_config(api, finance, settings):
    assert (
        call(
            api,
            "finance.query",
            {**finance["common"], **QUERY, "workspace_ref": "finance:unselected"},
        ).status_code
        == 403
    )
    config = settings.data_dir / "plugin-state/finance/access.json"
    config.write_text(
        json.dumps(
            {
                "format": "finance-access-v1",
                "intents": {},
                "entries": [{"canonical_root": "/PRIVATE_CORRUPT_CONFIG"}],
            }
        )
    )
    response = call(api, "finance.query", {**finance["common"], **QUERY})
    assert response.status_code == 422 and "access_config_corrupt" in response.text
    assert "PRIVATE_CORRUPT_CONFIG" not in response.text


@pytest.mark.parametrize("state", ["disposed", "quarantined", "too_large", "missing", "tampered"])
def test_original_unavailability_is_explicit_and_never_returns_private_bytes(api, finance, state):
    document = json.loads(finance["path"].read_text())
    attachment = document["attachments"][-1]
    if state in {"disposed", "quarantined"}:
        attachment["status"] = state
        attachment["status_history"].append(
            {**attachment["status_history"][-1], "from_status": "retained", "to_status": state}
        )
    elif state == "too_large":
        attachment["bytes"] = 21 * 1024 * 1024
    elif state == "missing":
        finance["original"].unlink()
    else:
        finance["original"].write_bytes(b"PRIVATE_TAMPERED_ORIGINAL")
    # Simulate an external custody change without asking the writer to bless it.
    finance["path"].write_text(json.dumps(document))
    response = call(
        api,
        "finance.evidence.read",
        {
            **finance["common"],
            "attachment_ref": "attachment:synthetic",
            "expected_snapshot": snapshot(finance),
        },
    )
    assert "PRIVATE_TAMPERED_ORIGINAL" not in response.text
    if state == "tampered":
        assert response.status_code == 422 and "evidence_integrity" in response.text
    else:
        assert response.status_code == 200, response.text
        assert body(response)["state"] == ("unavailable" if state == "missing" else state)
        assert not body(response).get("content_base64")


def test_unknown_writer_outcome_returns_only_reconciliation_receipt(api, finance, monkeypatch):
    intent = edit(finance, "unknown-write")
    preview = call(api, "finance.record.preview", intent)
    before = finance["path"].read_bytes()

    def unknown(*args, **kwargs):
        raise finance["module"].OutcomeUnknown("unknown-write")

    monkeypatch.setattr(finance["module"], "commit", unknown)
    response = call(
        api,
        "finance.record.commit",
        {
            **intent,
            "preview_digest": body(preview)["preview_digest"],
            "idempotency_key": "unknown-retry",
        },
    )
    assert response.status_code == 409
    assert "outcome_unknown" in response.text and "reconcile_same_request" in response.text
    assert (
        str(finance["path"]) not in response.text
        and "Sourced synthetic purpose" not in response.text
    )
    assert finance["path"].read_bytes() == before


def test_symlink_selection_and_unauthenticated_console_are_denied(api, finance, tmp_path):
    linked = tmp_path / "linked-root"
    linked.symlink_to(finance["path"].parent, target_is_directory=True)
    response = call(
        api,
        "financeWorkspace.select",
        {
            "project_id": finance["project_id"],
            "workspace_ref": "finance:linked",
            "root": str(linked),
            "authority_ref": "operator:linked",
        },
    )
    assert response.status_code == 403
    no_auth = api.post(
        "/api/v1/operations/finance.query/call",
        json={"arguments": {**finance["common"], **QUERY}},
        headers={"Authorization": "Bearer invalid"},
    )
    assert no_auth.status_code == 401


@pytest.mark.parametrize("fault", ["mcp_enabled", "write_policy_on_read", "wrong_path"])
def test_misclassified_registered_policy_never_creates_console_authority(
    api, finance, monkeypatch, fault
):
    from dataclasses import replace

    from stackos.operations.registry import build_operation_registry

    registry = build_operation_registry()
    spec = registry.get("finance.query")
    if fault == "mcp_enabled":
        object.__setattr__(
            spec, "surfaces", replace(spec.surfaces, mcp=replace(spec.surfaces.mcp, enabled=True))
        )
    elif fault == "write_policy_on_read":
        object.__setattr__(spec, "grant_policy", "local-admin-console-write")
    else:
        object.__setattr__(
            spec,
            "surfaces",
            replace(
                spec.surfaces, rest=replace(spec.surfaces.rest, path="/api/v1/operations/fake/call")
            ),
        )
    monkeypatch.setattr("stackos.api.operations.build_operation_registry", lambda: registry)
    assert call(api, "finance.query", {**finance["common"], **QUERY}).status_code == 403


def test_comparison_and_chat_clamping_preserve_typed_safe_refs(api, finance):
    comparison = {"start": "2025-09-01", "end": "2025-09-30", "clamped": True}
    projected = call(api, "finance.query", {**finance["common"], **QUERY, "comparison": comparison})
    assert projected.status_code == 200 and body(projected)["query"]["comparison"] == comparison
    args = {
        "project_id": finance["project_id"],
        "thread_key": "safe-compare",
        "message_key": "compare",
        "sender_ref": "local-user:operator",
        "text": "Compare periods.",
        "metadata_json": {
            "query": {**QUERY, "comparison": comparison},
            "analysis_kind": "spending",
        },
    }
    assert call(api, "localAgentChat.createMessage", args).status_code == 200
    assert (
        call(
            api,
            "localAgentChat.createMessage",
            {**args, "metadata_json": {"analysis_kind": "unknown"}},
        ).status_code
        == 422
    )
    # Browser validation precedes replay; an old successful key cannot bypass actor checks.
    keyed = {**args, "idempotency_key": "safe-chat-key"}
    assert call(api, "localAgentChat.createMessage", keyed).status_code == 200
    assert (
        call(
            api, "localAgentChat.createMessage", {**keyed, "sender_ref": "agent:spoof"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "update",
    [
        {"direction": "outbound"},
        {"sender_ref": "agent:spoof"},
        {"create_request": False},
        {"content_blocks": [{"text": "private financial facts"}]},
        {"metadata_json": {"root": "/secret"}},
        {"metadata_json": {"analysis_kind": []}},
    ],
)
def test_browser_request_is_inbound_human_refs_only(api, finance, update):
    response = call(
        api,
        "localAgentChat.createMessage",
        {
            "project_id": finance["project_id"],
            "thread_key": "finance",
            "message_key": "blocked",
            "sender_ref": "local-user:operator",
            "text": "Review supported records.",
            **update,
        },
        extra_headers={"X-StackOS-Client-Surface": "cli"},
    )
    assert response.status_code in {403, 422}, response.text
    assert "/secret" not in response.text


def test_dispatcher_config_replay_never_bypasses_current_authority(api, finance):
    select_args = {
        "project_id": finance["project_id"],
        "workspace_ref": WORKSPACE,
        "root": str(finance["path"].parent),
        "authority_ref": "operator:synthetic-selection",
        "idempotency_key": "select-root",
    }
    same = call(api, "financeWorkspace.select", select_args)
    assert same.status_code == 200 and same.json()["data"]["generation"] == 1
    changed = call(api, "financeWorkspace.select", {**select_args, "modes": ["read"]})
    assert changed.status_code == 409
    revoked = call(
        api,
        "financeWorkspace.revoke",
        {"project_id": finance["project_id"], "workspace_ref": WORKSPACE, "expected_generation": 1},
    )
    assert revoked.status_code == 200
    old = call(api, "financeWorkspace.select", select_args)
    assert old.status_code == 403
