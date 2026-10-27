"""No-model cost contract controls; no database or process boundary is used."""

import asyncio
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from stackos.action_output_contract import ACTION_OUTPUT_SCHEMA
from stackos.actions.codex_worker import CodexWorkerConnector
from stackos.actions.manifest import ExecutableActionManifest
from stackos.actions.repository.audit import ActionAuditMixin, _cost_metadata, _public_cost_cents
from stackos.actions.repository.execution import (
    ActionExecutionMixin,
    _action_output_envelope,
    _require_unknown_cost_approval,
)
from stackos.actions.repository.validation import RuntimeActionContext
from stackos.db.models import ActionCall, ActionCallStatus, ApprovalRequestStatus
from stackos.operations.communication_delivery.schemas import CommunicationSendOut
from stackos.operations.responses import _compact_action_execution
from stackos.repositories.base import ConflictError


class Session:
    def __init__(self, *, contract=True, marker="admitted", approved=True, step=3):
        self.plan = SimpleNamespace(
            grant_snapshot_json={
                "action_contracts": [
                    {"action": "engineering.codex", "approval_ref": "review:sealed"}
                ]
            }
            if contract
            else {}
        )
        self.approval = SimpleNamespace(
            status=ApprovalRequestStatus.APPROVED if approved else ApprovalRequestStatus.PENDING,
            run_plan_step_id=step,
            decision_json={"unknown_cost": marker},
        )

    def get(self, *_args, **_kwargs):
        return self.plan

    def exec(self, _statement):
        return SimpleNamespace(first=lambda: self.approval)


def admit(session, profile="review:sealed"):
    _require_unknown_cost_approval(
        session,
        project_id=1,
        run_plan_id=2,
        run_plan_step_id=3,
        action_ref="engineering.codex",
        profile_ref=profile,
    )


@pytest.mark.parametrize("change", ["contract", "profile", "marker", "status", "step", "missing"])
def test_unknown_requires_exact_explicit_scoped_approval(change):
    session = Session(
        contract=change != "contract",
        marker=None if change == "marker" else "admitted",
        approved=change != "status",
        step=4 if change == "step" else 3,
    )
    if change == "missing":
        session.approval = None
    with pytest.raises(ConflictError):
        admit(session, "review:other" if change == "profile" else "review:sealed")


def test_unknown_marker_is_checked_again_when_current_decision_changes():
    session = Session()
    admit(session)
    session.approval.decision_json = {}
    with pytest.raises(ConflictError):
        admit(session)


def test_priced_budget_denies_before_profile_or_reservation():
    repo = ActionExecutionMixin()
    with pytest.raises(ConflictError, match="priced budget"):
        repo._require_unknown_cost_admission(
            manifest=SimpleNamespace(enforce_budget=True, budget_kind="money"),
            connector=SimpleNamespace(key="codex-worker"),
            request=object(),
            run_plan_id=2,
            run_plan_step_id=3,
        )


@pytest.mark.parametrize("amount", [0, 17, None])
def test_writer_owns_posture_and_public_projection(amount):
    metadata = _cost_metadata({"execution_cost": {"state": "unknown"}}, amount)
    row = ActionCall(
        id=1,
        project_id=1,
        action_key="send",
        plugin_slug="example",
        connector_key="codex-worker" if amount is None else "example",
        operation="send",
        status=ActionCallStatus.SUCCESS,
        metadata_json=metadata,
        cost_cents=amount if amount is not None else 0,
    )
    assert _public_cost_cents(row) == amount
    audit = ActionAuditMixin()._call_audit_out(row)
    assert audit.model_dump(mode="json")["cost_cents"] == amount
    compact = _compact_action_execution({"cost_cents": amount, "action_call": audit.model_dump()})
    assert "cost_cents" in compact and compact["cost_cents"] == amount


@pytest.mark.parametrize("amount", [None, 0, 17])
def test_file_output_envelope_matches_registered_cost_schema(amount):
    manifest = ExecutableActionManifest(
        action_id=1,
        action_key="inspect",
        action_ref="engineering.inspect",
        plugin_slug="engineering",
        name="Inspect",
        risk_level="read",
        connector_key="codex-worker" if amount is None else "example",
        operation="inspect",
    )
    row = ActionCall(
        id=1,
        project_id=1,
        plugin_slug=manifest.plugin_slug,
        action_key=manifest.action_key,
        connector_key=manifest.connector_key,
        operation=manifest.operation,
        status=ActionCallStatus.SUCCESS,
        cost_cents=amount if amount is not None else 0,
        metadata_json=_cost_metadata({}, amount),
    )
    envelope = _action_output_envelope(
        project_id=1,
        manifest=manifest,
        input_json={},
        provider_context_json=None,
        credential_ref=None,
        runtime_context=RuntimeActionContext(credential_ref=None, provider_context_json={}),
        row=row,
        output_json={"result": "inspected"},
    )
    assert envelope["response"]["cost_cents"] == amount
    Draft202012Validator(ACTION_OUTPUT_SCHEMA).validate(envelope)


@pytest.mark.parametrize("amount", [None, 0, 17])
def test_communication_response_preserves_forwarded_action_cost(amount):
    response = CommunicationSendOut(
        status="sent",
        action_ref="example.send",
        provider_key="example",
        cost_cents=amount,
    )
    public = response.model_dump(mode="json")
    assert public["cost_cents"] == amount
    Draft202012Validator(CommunicationSendOut.model_json_schema()).validate(public)


@pytest.mark.parametrize("amount", [0, 17])
def test_legacy_non_codex_caller_marker_preserves_known_row_audit_and_replay(amount):
    row = ActionCall(
        id=1,
        project_id=1,
        action_key="send",
        plugin_slug="example",
        connector_key="example",
        operation="send",
        status=ActionCallStatus.SUCCESS,
        request_json={},
        metadata_json={"execution_cost": {"state": "unknown"}},
        cost_cents=amount,
    )
    assert _public_cost_cents(row) == amount
    repo = ActionAuditMixin()
    repo._s = SimpleNamespace(exec=lambda _query: SimpleNamespace(first=lambda: row))
    assert repo._call_audit_out(row).cost_cents == amount
    assert repo._call_out(row).cost_cents == amount
    replay = repo._idempotency_replay(
        project_id=1,
        manifest=SimpleNamespace(plugin_slug="example", action_key="send"),
        idempotency_key="same",
        request_json={},
        provider_context_json=None,
        credential_ref=None,
        dry_run=False,
    )
    assert replay.cost_cents == amount and replay.action_call.cost_cents == amount


@pytest.mark.parametrize("dry_run", [False, True])
def test_unknown_estimate_from_other_connector_fails_closed(dry_run):
    repo = ActionExecutionMixin()
    with pytest.raises(ConflictError, match="unsupported for this connector"):
        repo._require_unknown_cost_admission(
            manifest=SimpleNamespace(enforce_budget=False),
            connector=SimpleNamespace(key="other"),
            request=object(),
            run_plan_id=2,
            run_plan_step_id=3,
            dry_run=dry_run,
        )


def test_codex_unknown_dry_run_needs_no_spend_admission_or_reservation():
    # No Session, manifest, request or profile is available to accidentally use.
    ActionExecutionMixin()._require_unknown_cost_admission(
        manifest=object(),
        connector=SimpleNamespace(key="codex-worker"),
        request=object(),
        run_plan_id=None,
        run_plan_step_id=None,
        dry_run=True,
    )


def test_execute_unknown_non_codex_dry_run_denies_before_audit_writer(monkeypatch):
    """Pure caller-path control with catalog/setup doubles and a writer tripwire."""
    from stackos.actions.repository import execution

    manifest = ExecutableActionManifest(
        action_id=1,
        action_key="preview",
        action_ref="example.preview",
        plugin_slug="example",
        name="Preview",
        risk_level="read",
        connector_key="other",
        operation="preview",
    )
    connector = SimpleNamespace(key="other", estimate_cost_cents=lambda _request: None)
    repo = ActionExecutionMixin()
    repo._s = object()
    repo._connectors = SimpleNamespace(list_keys=lambda: ["other"], get=lambda _key: connector)
    repo._require_project = lambda _project: None
    repo._normalize_payload_and_ref = lambda *_args, **_kwargs: ({}, None)
    repo._manifest_with_provider_config = lambda **_kwargs: (manifest, {})
    repo._normalize_provider_context = lambda _context: {}
    repo._resolve_runtime_context = lambda **_kwargs: RuntimeActionContext(
        credential_ref=None,
        provider_context_json={},
    )
    repo._plugin_disabled_for_project = lambda **_kwargs: False
    repo.validate = lambda **_kwargs: SimpleNamespace(issues=[])
    repo._check_run_scope = lambda **_kwargs: None
    repo._connector_request = lambda **_kwargs: object()
    writes = []

    def forbidden_writer(**_kwargs):
        writes.append("unexpected audit write")
        raise AssertionError("unsupported unknown estimate reached audit writer")

    repo._record_call = forbidden_writer
    monkeypatch.setattr(
        execution,
        "build_action_availability",
        lambda *_args, **_kwargs: SimpleNamespace(status="ready", executable=True, reasons=[]),
    )
    monkeypatch.setattr(execution, "build_action_exposure", lambda *_args, **_kwargs: object())
    with pytest.raises(ConflictError, match="unsupported for this connector"):
        asyncio.run(repo.execute(project_id=1, action_ref=manifest.action_ref, dry_run=True))
    assert writes == []


def test_codex_estimate_is_unknown_without_profile_or_native_work():
    assert CodexWorkerConnector(object()).estimate_cost_cents(object()) is None
