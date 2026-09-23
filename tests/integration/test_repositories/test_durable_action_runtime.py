"""Generic durable action scheduling and recovery coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from stackos.actions import ActionRepository
from stackos.actions.connectors import (
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
)
from stackos.actions.manifest import find_action_manifest_secret_paths
from stackos.actions.repository.execution import _effective_durable_pacing
from stackos.auth_providers import ResolvedCredential
from stackos.db.connection import make_engine
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    Credential,
    IntegrationCredential,
    Plugin,
    PluginSource,
    ProjectCredential,
    Provider,
    Run,
    RunPlan,
    RunPlanStep,
    RunPlanStepStatus,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.actions.execution import action_call_get
from stackos.operations.actions.lifecycle import (
    action_call_cancel,
    action_call_items,
    action_call_pause,
    action_call_resume,
    action_call_retry,
)
from stackos.operations.actions.schemas import (
    ActionCallControlInput,
    ActionCallGetInput,
    ActionCallItemsInput,
    ActionCallResumeInput,
    ActionCallRetryInput,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository


class _DurablePreparingConnector:
    key = "test.durable-preparing"

    def __init__(self) -> None:
        self.prepare_requests: list[ActionConnectorRequest] = []
        self.execute_calls = 0

    def validate(self, request: ActionConnectorRequest) -> list[object]:
        del request
        return []

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        del request
        return 0

    async def prepare_delivery(self, request: ActionConnectorRequest) -> list[dict[str, object]]:
        self.prepare_requests.append(request)
        return [
            {
                "destination_ref": "telegram-user:300",
                "input_json": {"text": request.input_json["text"]},
            }
        ]

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        del request
        self.execute_calls += 1
        return ActionConnectorResult(output_json={"unexpected": True})


def test_durable_pacing_selects_auth_method_floor_and_caller_can_only_slow() -> None:
    manifest = SimpleNamespace(
        action_ref="communications.telegram.message.broadcast",
        config_json={
            "durable_pacing_json": {
                "account_interval_seconds": 1.0,
                "destination_interval_seconds": 1.428571,
            },
            "durable_pacing_by_auth_method_json": [
                {
                    "auth_method_key": "tdlib-bot-token",
                    "pacing_json": {"account_interval_seconds": 0.047619},
                },
                {
                    "auth_method_key": "tdlib-user-session",
                    "pacing_json": {"account_interval_seconds": 1.0},
                },
            ],
        },
    )
    assert find_action_manifest_secret_paths(manifest.config_json) == []
    assert _effective_durable_pacing(
        manifest=manifest,
        auth_method_key="tdlib-bot-token",
        account_interval_seconds=None,
        destination_interval_seconds=None,
    ) == {
        "account_interval_seconds": 0.047619,
        "destination_interval_seconds": 1.428571,
    }
    assert _effective_durable_pacing(
        manifest=manifest,
        auth_method_key="tdlib-user-session",
        account_interval_seconds=2.0,
        destination_interval_seconds=3.0,
    ) == {
        "account_interval_seconds": 2.0,
        "destination_interval_seconds": 3.0,
    }
    assert (
        _effective_durable_pacing(
            manifest=manifest,
            auth_method_key="another-method",
            account_interval_seconds=None,
            destination_interval_seconds=None,
        )["account_interval_seconds"]
        == 1.0
    )


@pytest.mark.parametrize(
    ("config", "field"),
    [
        (
            {"durable_pacing_json": {"account_interval_seconds": float("inf")}},
            "account_interval_seconds",
        ),
        ({"durable_pacing_json": {"bogus": 1}}, "bogus"),
        ({"durable_pacing_by_auth_method_json": {}}, "durable_pacing_by_auth_method_json"),
        (
            {
                "durable_pacing_by_auth_method_json": [
                    {"auth_method_key": "tdlib-bot-token", "pacing_json": {"bogus": 1}}
                ]
            },
            "bogus",
        ),
        (
            {
                "durable_pacing_by_auth_method_json": [
                    {
                        "auth_method_key": "tdlib-bot-token",
                        "pacing_json": {"account_interval_seconds": float("nan")},
                    }
                ]
            },
            "account_interval_seconds",
        ),
        (
            {
                "durable_pacing_by_auth_method_json": [
                    {"auth_method_key": "", "pacing_json": {"account_interval_seconds": 1}}
                ]
            },
            "auth_method_key",
        ),
        (
            {
                "durable_pacing_by_auth_method_json": [
                    {"auth_method_key": "tdlib-bot-token", "pacing_json": {}},
                    {"auth_method_key": "tdlib-bot-token", "pacing_json": {}},
                ]
            },
            "auth_method_key",
        ),
        (
            {
                "durable_pacing_by_auth_method_json": [
                    {"auth_method_key": "tdlib-bot-token", "pacing_json": {}, "bogus": 1}
                ]
            },
            "bogus",
        ),
    ],
)
def test_durable_pacing_rejects_invalid_manifest_config(config: dict, field: str) -> None:
    manifest = SimpleNamespace(action_ref="test.send", config_json=config)
    with pytest.raises(ValidationError) as exc_info:
        _effective_durable_pacing(
            manifest=manifest,
            auth_method_key="tdlib-user-session",
            account_interval_seconds=None,
            destination_interval_seconds=None,
        )
    assert exc_info.value.data["field"] == field


@pytest.mark.parametrize("value", [float("inf"), float("nan"), 0, -1, True])
def test_durable_pacing_rejects_invalid_caller_request(value: float) -> None:
    manifest = SimpleNamespace(action_ref="test.send", config_json={})
    with pytest.raises(ValidationError) as exc_info:
        _effective_durable_pacing(
            manifest=manifest,
            auth_method_key=None,
            account_interval_seconds=value,
            destination_interval_seconds=None,
        )
    assert exc_info.value.data["field"] == "account_interval_seconds"


def _database(tmp_path: Path) -> tuple[Engine, int, int, str, int]:
    engine = make_engine(tmp_path / "durable-actions.sqlite")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        first_project = (
            ProjectRepository(session)
            .create(
                slug="durable-actions",
                name="Durable Actions",
                domain="durable-actions.example.test",
                locale="en-US",
            )
            .data
        )
        second_project = (
            ProjectRepository(session)
            .create(
                slug="durable-actions-second",
                name="Durable Actions Second",
                domain="durable-actions-second.example.test",
                locale="en-US",
            )
            .data
        )
        assert first_project.id is not None and second_project.id is not None
        credential = Credential(
            credential_ref="cred_durable_telegram",
            provider_key="telegram",
            display_name="Durable Telegram",
            display_name_key="durable telegram",
            auth_type="tdlib",
            auth_method_key="tdlib-user-session",
        )
        session.add(credential)
        session.flush()
        assert credential.id is not None
        integration = IntegrationCredential(
            encrypted_payload=b"durable-action-test",
            nonce=b"0" * 12,
        )
        session.add(integration)
        session.flush()
        assert integration.id is not None
        credential.integration_credential_id = integration.id
        session.add(credential)
        session.add_all(
            [
                ProjectCredential(project_id=first_project.id, credential_id=credential.id),
                ProjectCredential(project_id=second_project.id, credential_id=credential.id),
            ]
        )
        plugin = Plugin(
            slug="durable-test",
            name="Durable Test",
            version="0.1.0",
            source=PluginSource.PROJECT,
            manifest_json={},
        )
        session.add(plugin)
        session.flush()
        assert plugin.id is not None
        action_call = ActionCall(
            project_id=first_project.id,
            action_key="message.send",
            plugin_slug=plugin.slug,
            provider_key="telegram",
            connector_key="telegram.tdlib",
            operation="message.send",
            status=ActionCallStatus.RUNNING,
            credential_id=credential.id,
            credential_ref=credential.credential_ref,
        )
        session.add(action_call)
        session.commit()
        assert action_call.id is not None
        return (
            engine,
            first_project.id,
            second_project.id,
            credential.credential_ref,
            action_call.id,
        )


def _new_action_call(
    session: Session,
    *,
    project_id: int,
    credential_ref: str,
) -> int:
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.id is not None
    call = ActionCall(
        project_id=project_id,
        action_key="message.send",
        plugin_slug="durable-test",
        provider_key="telegram",
        connector_key="telegram.tdlib",
        operation="message.send",
        status=ActionCallStatus.RUNNING,
        credential_id=credential.id,
        credential_ref=credential_ref,
    )
    session.add(call)
    session.commit()
    assert call.id is not None
    return call.id


def test_durable_action_seals_items_leases_before_effect_and_aggregates_receipt(
    tmp_path: Path,
) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:100",
                    "input_json": {"chat_id": 100, "text": "Hello"},
                    "correlation_ref": "campaign:welcome:100",
                }
            ],
            due_at=now,
            idempotency_key="welcome-100",
        )
        assert job.state == "running"
        assert job.item_count == 1
        assert len(job.input_digest) == 64

        polled = asyncio.run(
            action_call_get(
                ActionCallGetInput(project_id=project_id, action_call_id=action_call_id),
                MCPContext(
                    session=session,
                    request_id="test-durable-action-poll",
                    project_id=project_id,
                ),
                None,  # type: ignore[arg-type]
            )
        )
        assert polled.status == ActionCallStatus.RUNNING
        assert polled.poll_operation == "actionCall.get"
        assert polled.progress == {
            "phase": "running",
            "total_count": 1,
            "pending_count": 1,
            "leased_count": 0,
            "completed_count": 0,
            "failed_count": 0,
            "unknown_count": 0,
            "cancelled_count": 0,
            "next_eligible_at": now.isoformat(),
        }

        leased = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=job.id,
            now=now,
            account_interval_seconds=2,
            destination_interval_seconds=2,
        )
        assert len(leased) == 1
        item = leased[0]
        assert item.state == "leased"
        assert item.attempt_ref.startswith("durable-attempt:")
        assert item.correlation_ref == "campaign:welcome:100"
        assert item.input_json == {"chat_id": 100, "text": "Hello"}

        progress = repo.record_durable_action_item_progress(
            project_id=project_id,
            item_id=item.id,
            attempt_ref=item.attempt_ref,
            progress_json={"temporary_message_ids": [-item.id]},
            temporary_message_ref=f"telegram-message:100:{-item.id}",
            provider_sending_id=str(item.id),
            now=now,
        )
        assert progress.attempt_ref == item.attempt_ref

        immediate = repo.admit_delivery(
            project_id=project_id,
            credential_ref=credential_ref,
            destination_ref="telegram-user:100",
            now=now,
            account_interval_seconds=2,
            destination_interval_seconds=2,
        )
        assert immediate.admitted is False
        assert immediate.reason in {"account_busy", "destination_busy", "not_eligible"}

        completed = repo.complete_durable_action_item(
            project_id=project_id,
            item_id=item.id,
            attempt_ref=item.attempt_ref,
            result_json={"message_ref": "telegram-message:100:55"},
            now=now + timedelta(seconds=1),
        )
        assert completed.state == "succeeded"

        status = repo.get_durable_action_job(project_id=project_id, job_id=job.id)
        assert status.state == "completed"
        assert status.completed_count == 1
        assert status.pending_count == 0
        assert repo.durable_action_progress(action_call_id=action_call_id) == {
            "phase": "completed",
            "total_count": 1,
            "pending_count": 0,
            "leased_count": 0,
            "completed_count": 1,
            "failed_count": 0,
            "unknown_count": 0,
            "cancelled_count": 0,
            "next_eligible_at": None,
        }
        action_call = session.get(ActionCall, action_call_id)
        assert action_call is not None
        assert action_call.status == ActionCallStatus.SUCCESS
        assert action_call.response_json == {
            "status": "completed",
            "total_count": 1,
            "completed_count": 1,
            "failed_count": 0,
            "unknown_count": 0,
            "cancelled_count": 0,
        }
    engine.dispose()


def test_action_call_poll_exposes_peer_flood_pause_and_next_action(tmp_path: Path) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.broadcast",
            items=[
                {"destination_ref": "telegram-user:201", "input_json": {"chat_id": 201}},
                {"destination_ref": "telegram-user:202", "input_json": {"chat_id": 202}},
            ],
            due_at=now,
        )
        first = repo.claim_durable_action_items(project_id=project_id, job_id=job.id, now=now)[0]
        repo.fail_durable_action_item(
            project_id=project_id,
            item_id=first.id,
            attempt_ref=first.attempt_ref,
            error="PEER_FLOOD",
            result_json={"provider_error": "PEER_FLOOD", "retry_safe": False},
            pause_remaining_reason="peer_flood",
            now=now,
        )

        polled = asyncio.run(
            action_call_get(
                ActionCallGetInput(project_id=project_id, action_call_id=action_call_id),
                MCPContext(
                    session=session, request_id="test-peer-flood-poll", project_id=project_id
                ),
                None,  # type: ignore[arg-type]
            )
        )
        assert polled.status == ActionCallStatus.RUNNING
        assert polled.progress is not None
        assert polled.progress["phase"] == "paused"
        assert polled.progress["pause_reason"] == "peer_flood"
        assert polled.progress["pause_item_id"] == first.id
        assert polled.progress["next_action"] == (
            "inspect actionCall.items, then actionCall.resume or actionCall.cancel"
        )
    engine.dispose()


def test_durable_action_defers_known_flood_wait_and_holds_inflight_restart_unknown(
    tmp_path: Path,
) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:101",
                    "input_json": {"chat_id": 101, "text": "Later"},
                    "correlation_ref": "campaign:later:101",
                },
                {
                    "destination_ref": "telegram-user:102",
                    "input_json": {"chat_id": 102, "text": "Unknown"},
                    "correlation_ref": "campaign:unknown:102",
                },
            ],
            due_at=now,
        )
        first = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=job.id,
            now=now,
            limit=1,
        )[0]
        deferred = repo.defer_durable_action_item(
            project_id=project_id,
            item_id=first.id,
            attempt_ref=first.attempt_ref,
            retry_at=now + timedelta(seconds=30),
            reason="flood_wait",
            flood_wait_seconds=30,
            now=now,
        )
        assert deferred.state == "deferred"
        assert (
            repo.claim_durable_action_items(
                project_id=project_id,
                job_id=job.id,
                now=now + timedelta(seconds=29),
            )
            == []
        )
        retried = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=job.id,
            now=now + timedelta(seconds=30),
            limit=1,
        )[0]
        assert retried.id == first.id
        assert retried.attempt_ref != first.attempt_ref
        repo.complete_durable_action_item(
            project_id=project_id,
            item_id=retried.id,
            attempt_ref=retried.attempt_ref,
            result_json={"message_ref": "telegram-message:101:55"},
            now=now + timedelta(seconds=30),
        )

        repo.claim_durable_action_items(
            project_id=project_id,
            job_id=job.id,
            now=now + timedelta(seconds=31),
            limit=1,
        )[0]
        quiesce = repo.quiesce_account_delivery(
            credential_ref=credential_ref,
            reason="account-update",
            now=now + timedelta(seconds=31),
        )
        assert quiesce.state == "draining"
        assert quiesce.active_lease_count == 1

        with pytest.raises(ConflictError, match="no remaining receipt-proven-no-effect"):
            repo.cancel_durable_action_job(project_id=project_id, job_id=job.id)
        recovered = repo.reconcile_durable_action_jobs(now=now + timedelta(seconds=32))
        assert recovered.unknown_held_count == 1
        held = repo.get_durable_action_job(project_id=project_id, job_id=job.id)
        assert held.state == "unknown-hold"
        assert held.unknown_count == 1
        assert (
            repo.claim_durable_action_items(
                project_id=project_id,
                job_id=job.id,
                now=now + timedelta(minutes=1),
            )
            == []
        )
        action_call = session.get(ActionCall, action_call_id)
        assert action_call is not None
        assert action_call.status == ActionCallStatus.FAILED
        assert action_call.response_json is not None
        assert action_call.response_json["outcome_unknown"] is True
        assert action_call.response_json["retry_safe"] is False
    engine.dispose()


@pytest.mark.parametrize("late_success", [True, False])
def test_late_receipts_reopen_parent_and_keep_remaining_work_behind_step_gate(
    tmp_path: Path,
    monkeypatch,
    late_success: bool,
) -> None:
    engine, project_id, _, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 23, 15, tzinfo=UTC).replace(tzinfo=None)
    monkeypatch.setattr("stackos.actions.repository.durable.utcnow", lambda: now)
    with Session(engine) as session:
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "late-receipts",
                "title": "Wait for durable receipts",
                "steps": [{"id": "work", "title": "Deliver sealed items"}],
            },
        ).data
        started = plans.start(plan.id, project_id=project_id).data
        plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="work")
        step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).one()
        call = session.get(ActionCall, action_call_id)
        assert call is not None
        call.run_id = started.run_id
        call.run_plan_id = plan.id
        call.run_plan_step_id = step.id
        session.add(call)
        session.commit()
        repo = ActionRepository(session)
        job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="durable-test.message.send",
            items=[
                {
                    "destination_ref": f"telegram-chat:{number}",
                    "input_json": {"text": "Sealed once"},
                    "correlation_ref": f"late-receipt:{number}",
                }
                for number in (1, 2, 3)
            ],
        )
        held = [
            repo.claim_durable_action_items(
                project_id=project_id,
                job_id=job.id,
                now=now + timedelta(seconds=offset),
                overlap_account_leases=True,
            )[0]
            for offset in (0, 2)
        ]
        assert (
            repo.reconcile_durable_action_jobs(now=now + timedelta(seconds=3)).unknown_held_count
            == 2
        )
        for index, item in enumerate(held):
            repo.reconcile_durable_action_item(
                project_id=project_id,
                item_id=item.id,
                attempt_ref=item.attempt_ref,
                success=late_success if index else True,
                result_json={"receipt_ref": f"provider-receipt:{index}", "retry_safe": False},
                now=now + timedelta(seconds=4 + index),
            )
            session.refresh(call)
            if index == 0:
                assert call.status == ActionCallStatus.FAILED
                assert call.response_json["outcome_unknown"] is True
                assert (
                    repo.claim_durable_action_items(
                        project_id=project_id, job_id=job.id, now=now + timedelta(seconds=5)
                    )
                    == []
                )
        assert call.status == ActionCallStatus.RUNNING
        assert call.response_json is None
        assert call.completed_at is None
        for phase in ("pending", "leased"):
            with pytest.raises(ValidationError, match="action calls are still running"):
                plans.record_step(
                    run_plan_id=plan.id,
                    run_id=started.run_id,
                    step_id="work",
                    status=RunPlanStepStatus.SUCCESS,
                )
            if phase == "pending":
                remaining = repo.claim_durable_action_items(
                    project_id=project_id, job_id=job.id, now=now + timedelta(seconds=6)
                )
                assert len(remaining) == 1
                assert remaining[0].id not in {item.id for item in held}
        repo.complete_durable_action_item(
            project_id=project_id,
            item_id=remaining[0].id,
            attempt_ref=remaining[0].attempt_ref,
            result_json={"message_ref": "telegram-message:3:33"},
            now=now + timedelta(seconds=7),
        )
        session.refresh(call)
        assert call.status == (
            ActionCallStatus.SUCCESS if late_success else ActionCallStatus.FAILED
        )
        items = repo.list_durable_action_items(project_id=project_id, job_id=job.id)
        assert [item.attempt_count for item in items] == [1, 1, 1]
        assert [item.result_json["receipt_ref"] for item in items[:2]] == [
            "provider-receipt:0",
            "provider-receipt:1",
        ]
        result = plans.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="work",
            status=RunPlanStepStatus.SUCCESS,
        ).data
        assert result.steps[0].status == RunPlanStepStatus.SUCCESS
    engine.dispose()


def test_durable_action_call_lifecycle_operations_are_scoped_and_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        ActionRepository(session).create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:199",
                    "input_json": {"chat_id": 199, "text": "Cancel me"},
                }
            ],
            due_at=now,
        )
        ctx = MCPContext(
            session=session,
            request_id="test-durable-action-lifecycle",
            project_id=project_id,
        )
        control = ActionCallControlInput(project_id=project_id, action_call_id=action_call_id)
        paused = asyncio.run(action_call_pause(control, ctx, None))  # type: ignore[arg-type]
        assert paused.data.state == "paused"
        monkeypatch.setattr(
            ActionRepository,
            "describe",
            lambda _self, **_kwargs: SimpleNamespace(
                manifest=SimpleNamespace(risk_level="write", config_json={})
            ),
        )
        with pytest.raises(ValidationError, match="confirm_direct"):
            asyncio.run(
                action_call_resume(
                    ActionCallResumeInput(project_id=project_id, action_call_id=action_call_id),
                    ctx,
                    None,  # type: ignore[arg-type]
                )
            )
        resumed = asyncio.run(
            action_call_resume(
                ActionCallResumeInput(
                    project_id=project_id,
                    action_call_id=action_call_id,
                    confirm_direct=True,
                    intent_summary=(
                        "Operator reviewed the paused delivery and approved resuming it."
                    ),
                ),
                ctx,
                None,  # type: ignore[arg-type]
            )
        )
        assert resumed.data.state == "running"
        listed = asyncio.run(
            action_call_items(
                ActionCallItemsInput(project_id=project_id, action_call_id=action_call_id),
                ctx,
                None,  # type: ignore[arg-type]
            )
        )
        assert listed.count == 1
        assert listed.items[0].state == "pending"
        cancelled = asyncio.run(action_call_cancel(control, ctx, None))  # type: ignore[arg-type]
        assert cancelled.data.state == "cancelled"
        with pytest.raises(ConflictError, match="no remaining receipt-proven-no-effect"):
            asyncio.run(action_call_cancel(control, ctx, None))  # type: ignore[arg-type]
        call = session.get(ActionCall, action_call_id)
        assert call is not None
        assert call.status == ActionCallStatus.FAILED
        assert call.response_json is not None
        assert call.response_json["cancelled_count"] == 1
        with pytest.raises(ValidationError, match="confirm_direct"):
            asyncio.run(
                action_call_retry(
                    ActionCallRetryInput(
                        project_id=project_id,
                        action_call_id=action_call_id,
                        item_ids=[listed.items[0].id],
                    ),
                    ctx,
                    None,  # type: ignore[arg-type]
                )
            )
        retried = asyncio.run(
            action_call_retry(
                ActionCallRetryInput(
                    project_id=project_id,
                    action_call_id=action_call_id,
                    item_ids=[listed.items[0].id],
                    confirm_direct=True,
                    intent_summary=(
                        "Operator reviewed the cancelled no-effect item and approved its retry."
                    ),
                ),
                ctx,
                None,  # type: ignore[arg-type]
            )
        )
        assert retried.data.state == "running"
        restarted_items = ActionRepository(session).list_durable_action_items(
            project_id=project_id,
            job_id=retried.data.id,
        )
        assert restarted_items[0].state == "pending"
        assert restarted_items[0].can_retry is False
        reopened = session.get(ActionCall, action_call_id)
        assert reopened is not None
        assert reopened.status == ActionCallStatus.RUNNING
        assert reopened.response_json is None
    engine.dispose()


def test_workflow_resume_requires_the_original_step_and_its_communication_grant(
    tmp_path: Path,
) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        plan = (
            RunPlanRepository(session)
            .create(
                project_id=project_id,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": "durable.resume.run",
                    "title": "Resume durable delivery",
                    "grants": {
                        "mcp_tool_grants": [
                            {
                                "step_id": "send",
                                "tool": "communication.send",
                                "targets": ["campaign"],
                            }
                        ]
                    },
                    "steps": [{"id": "send", "title": "Send campaign"}],
                },
            )
            .data
        )
        started = RunPlanRepository(session).start(plan.id, project_id=project_id).data
        step = (
            RunPlanRepository(session)
            .claim_step(
                run_plan_id=plan.id,
                run_id=started.run_id,
                step_id="send",
                claimed_by="durable-resume-test",
            )
            .data
        )
        call = session.get(ActionCall, action_call_id)
        assert call is not None
        call.run_id = started.run_id
        call.run_plan_id = plan.id
        call.run_plan_step_id = step.id
        call.metadata_json = {
            "operation": "communication.send",
            "target_ref": "communication-target:campaign",
        }
        session.add(call)
        session.commit()
        job = ActionRepository(session).create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-chat:100",
                    "input_json": {"chat_id": 100, "text": "Resume me"},
                }
            ],
            due_at=now,
        )
        ActionRepository(session).pause_durable_action_job(project_id=project_id, job_id=job.id)
        run = session.get(Run, started.run_id)
        assert run is not None
        ctx = MCPContext(
            session=session,
            request_id="test-durable-action-workflow-resume",
            run_token="run-plan-token",
            run=run,
            run_id=run.id,
            project_id=project_id,
        )
        resumed = asyncio.run(
            action_call_resume(
                ActionCallResumeInput(project_id=project_id, action_call_id=action_call_id),
                ctx,
                None,  # type: ignore[arg-type]
            )
        )
        assert resumed.data.state == "running"

        ActionRepository(session).pause_durable_action_job(project_id=project_id, job_id=job.id)
        call.metadata_json = {"operation": "communication.reply", "source_request_id": 7}
        plan_row = session.get(RunPlan, plan.id)
        assert plan_row is not None
        plan_row.grant_snapshot_json = {
            "mcp_tool_grants": [
                {
                    "step_id": "send",
                    "tool": "communication.reply",
                    "sources": ["telegram"],
                }
            ]
        }
        session.add_all([call, plan_row])
        session.commit()
        with pytest.raises(ToolNotGrantedError, match=r"action\.execute grant"):
            asyncio.run(
                action_call_resume(
                    ActionCallResumeInput(project_id=project_id, action_call_id=action_call_id),
                    ctx,
                    None,  # type: ignore[arg-type]
                )
            )
        assert (
            ActionRepository(session)
            .get_durable_action_job(project_id=project_id, job_id=job.id)
            .state
            == "paused"
        )
    engine.dispose()


def test_durable_action_controls_cannot_escape_the_context_project(tmp_path: Path) -> None:
    (
        engine,
        first_project_id,
        second_project_id,
        credential_ref,
        _action_call_id,
    ) = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        assert credential.id is not None
        second_call = ActionCall(
            project_id=second_project_id,
            action_key="message.send",
            plugin_slug="durable-test",
            provider_key="telegram",
            connector_key="telegram.tdlib",
            operation="message.send",
            status=ActionCallStatus.RUNNING,
            credential_id=credential.id,
            credential_ref=credential_ref,
        )
        session.add(second_call)
        session.commit()
        assert second_call.id is not None
        job = ActionRepository(session).create_durable_action_job(
            project_id=second_project_id,
            action_call_id=second_call.id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:299",
                    "input_json": {"chat_id": 299, "text": "Scope test"},
                }
            ],
            due_at=now,
        )
        ctx = MCPContext(
            session=session,
            request_id="test-durable-action-control-scope",
            project_id=first_project_id,
        )
        with pytest.raises(ValidationError, match="workspace project"):
            asyncio.run(
                action_call_pause(
                    ActionCallControlInput(
                        project_id=second_project_id,
                        action_call_id=second_call.id,
                    ),
                    ctx,
                    None,  # type: ignore[arg-type]
                )
            )
        assert (
            ActionRepository(session)
            .get_durable_action_job(project_id=second_project_id, job_id=job.id)
            .state
            == "running"
        )
    engine.dispose()


def test_delivery_admission_is_account_global_across_project_attachments(tmp_path: Path) -> None:
    engine, first_project_id, second_project_id, credential_ref, _action_call_id = _database(
        tmp_path
    )
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        first = repo.admit_delivery(
            project_id=first_project_id,
            credential_ref=credential_ref,
            destination_ref="telegram-user:200",
            now=now,
            account_interval_seconds=10,
            destination_interval_seconds=1,
        )
        assert first.admitted is True
        second = repo.admit_delivery(
            project_id=second_project_id,
            credential_ref=credential_ref,
            destination_ref="telegram-user:201",
            now=now,
            account_interval_seconds=10,
            destination_interval_seconds=1,
        )
        assert second.admitted is False
        assert second.reason in {"account_busy", "not_eligible"}
        assert second.credential_ref == credential_ref
        assert second.destination_ref == "telegram-user:201"
        assert "project" not in second.model_dump(mode="json")
    engine.dispose()


def test_account_delivery_hold_ownership_preserves_an_existing_external_hold(
    tmp_path: Path,
) -> None:
    engine, project_id, _second_project_id, credential_ref, _action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        restricted = repo.quiesce_account_delivery(
            credential_ref=credential_ref,
            reason="telegram-peer-flood:attempt-1",
            now=now,
        )
        assert restricted.acquired is True
        auth_hold = repo.quiesce_account_delivery(
            credential_ref=credential_ref,
            reason="auth-transition:attempt-2",
            now=now + timedelta(seconds=1),
        )
        assert auth_hold.acquired is False
        assert auth_hold.reason == "telegram-peer-flood:attempt-1"
        assert (
            repo.release_account_delivery_quiesce(
                credential_ref=credential_ref,
                expected_reason="auth-transition:attempt-2",
                now=now + timedelta(seconds=2),
            )
            is False
        )
        assert (
            repo.admit_delivery(
                project_id=project_id,
                credential_ref=credential_ref,
                destination_ref="telegram-user:still-held",
                now=now + timedelta(seconds=2),
            ).reason
            == "account_quiesced"
        )
        assert (
            repo.release_account_delivery_quiesce(
                credential_ref=credential_ref,
                expected_reason="telegram-peer-flood:attempt-1",
                now=now + timedelta(seconds=2),
            )
            is True
        )
    engine.dispose()


def test_dispatchable_job_listing_skips_deferred_items_before_the_scan_limit(
    tmp_path: Path,
) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        first = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:deferred",
                    "input_json": {"chat_id": 1, "text": "deferred"},
                }
            ],
            due_at=now,
        )
        deferred_item = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=first.id,
            now=now,
        )[0]
        repo.defer_durable_action_item(
            project_id=project_id,
            item_id=deferred_item.id,
            attempt_ref=deferred_item.attempt_ref,
            retry_at=now + timedelta(hours=1),
            reason="flood_wait",
            flood_wait_seconds=3600,
            now=now,
        )

        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        assert credential.id is not None
        second_call = ActionCall(
            project_id=project_id,
            action_key="message.send",
            plugin_slug="durable-test",
            provider_key="telegram",
            connector_key="telegram.tdlib",
            operation="message.send",
            status=ActionCallStatus.RUNNING,
            credential_id=credential.id,
            credential_ref=credential_ref,
        )
        session.add(second_call)
        session.commit()
        assert second_call.id is not None
        ready = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=second_call.id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:ready",
                    "input_json": {"chat_id": 2, "text": "ready"},
                }
            ],
            due_at=now,
        )

        dispatchable = repo.list_dispatchable_durable_action_jobs(now=now, limit=1)
        assert [job.id for job in dispatchable] == [ready.id]
    engine.dispose()


def test_durable_execute_prepares_and_seals_before_returning_the_action_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, project_id, _second_project_id, credential_ref, _action_call_id = _database(tmp_path)
    connector = _DurablePreparingConnector()
    registry = ActionConnectorRegistry()
    registry.register(connector)
    with Session(engine) as session:
        plugin = Plugin(
            slug="durable-delivery-test",
            name="Durable Delivery Test",
            version="0.1.0",
            source=PluginSource.PROJECT,
            manifest_json={},
        )
        session.add(plugin)
        session.flush()
        assert plugin.id is not None
        provider = Provider(
            plugin_id=plugin.id,
            key="telegram",
            name="Telegram",
            description="Durable delivery fixture provider.",
            auth_type="native",
        )
        session.add(provider)
        session.flush()
        assert provider.id is not None
        action = Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key="message.send",
            name="Send message",
            description="Durable delivery fixture.",
            capability_key=None,
            risk_level="write",
            input_schema_json={
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": connector.key,
                "operation": "message.send",
                "execution_mode": "background",
                "durable_delivery": True,
                "durable_pacing_json": {
                    "account_interval_seconds": 2,
                    "destination_interval_seconds": 1,
                },
                "durable_pacing_by_auth_method_json": [
                    {
                        "auth_method_key": "tdlib-user-session",
                        "pacing_json": {
                            "account_interval_seconds": 6,
                            "destination_interval_seconds": 4,
                        },
                    }
                ],
                "requires_credential": True,
            },
        )
        session.add(action)
        session.commit()
        repo = ActionRepository(session, connectors=registry)

        async def resolve_for_prepare(**_kwargs: object) -> ResolvedCredential:
            credential = session.exec(
                select(Credential).where(Credential.credential_ref == credential_ref)
            ).one()
            integration = session.get(IntegrationCredential, credential.integration_credential_id)
            assert integration is not None
            return ResolvedCredential(
                credential=credential,
                integration=integration,
                secret_payload=b"durable-action-test",
            )

        monkeypatch.setattr(repo, "_resolve_credential", resolve_for_prepare)
        validation = repo.validate(
            project_id=project_id,
            action_ref="durable-delivery-test.message.send",
            input_json={"text": "Prepared once"},
            credential_ref=credential_ref,
            idempotency_key="prepared-once",
        )
        assert validation.valid, [issue.model_dump() for issue in validation.issues]
        due_at = datetime(2026, 9, 23, 15, tzinfo=UTC).replace(tzinfo=None)
        expires_at = due_at + timedelta(hours=1)
        monkeypatch.setattr(
            "stackos.actions.repository.durable.utcnow",
            lambda: due_at - timedelta(minutes=1),
        )
        accepted = asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="durable-delivery-test.message.send",
                input_json={"text": "Prepared once"},
                credential_ref=credential_ref,
                idempotency_key="prepared-once",
                durable_due_at=due_at,
                durable_expires_at=expires_at,
                durable_account_interval_seconds=5,
                durable_destination_interval_seconds=3,
            )
        ).data
        assert accepted.action_call.status == ActionCallStatus.RUNNING
        assert accepted.poll_operation == "actionCall.get"
        assert connector.execute_calls == 0
        assert len(connector.prepare_requests) == 1
        assert connector.prepare_requests[0].action_call_id is None

        replayed = asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="durable-delivery-test.message.send",
                input_json={"text": "Prepared once"},
                credential_ref=credential_ref,
                idempotency_key="prepared-once",
                durable_due_at=due_at,
                durable_expires_at=expires_at,
                durable_account_interval_seconds=5,
                durable_destination_interval_seconds=3,
            )
        ).data
        assert replayed.replayed is True
        assert replayed.action_call.id == accepted.action_call.id
        assert len(connector.prepare_requests) == 1

        job = repo.get_durable_action_job(
            project_id=project_id,
            job_id=accepted.action_call.metadata_json["durable_action_job_id"],
        )
        assert job.state == "scheduled"
        assert job.due_at == due_at
        assert job.expires_at == expires_at
        assert job.pacing_json == {
            "account_interval_seconds": 6.0,
            "destination_interval_seconds": 4.0,
        }
        assert repo.list_durable_action_items(project_id=project_id, job_id=job.id)[
            0
        ].input_json == {"text": "Prepared once"}
        assert repo.list_durable_action_items(project_id=project_id, job_id=job.id)[
            0
        ].correlation_ref.startswith("durable-item:")
        with pytest.raises(ConflictError, match="idempotency key replayed"):
            asyncio.run(
                repo.execute(
                    project_id=project_id,
                    action_ref="durable-delivery-test.message.send",
                    input_json={"text": "Prepared once"},
                    credential_ref=credential_ref,
                    idempotency_key="prepared-once",
                    durable_due_at=due_at,
                    durable_expires_at=expires_at + timedelta(minutes=1),
                    durable_account_interval_seconds=5,
                    durable_destination_interval_seconds=3,
                )
            )
        monkeypatch.setattr(
            "stackos.actions.repository.durable.utcnow",
            lambda: expires_at + timedelta(seconds=1),
        )
        expired_replay = asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="durable-delivery-test.message.send",
                input_json={"text": "Prepared once"},
                credential_ref=credential_ref,
                idempotency_key="prepared-once",
                durable_due_at=due_at,
                durable_expires_at=expires_at,
                durable_account_interval_seconds=5,
                durable_destination_interval_seconds=3,
            )
        ).data
        assert expired_replay.replayed is True
        assert expired_replay.action_call.id == accepted.action_call.id
        prior_call_count = len(session.exec(select(ActionCall)).all())
        with pytest.raises(ValidationError, match="expires_at"):
            asyncio.run(
                repo.execute(
                    project_id=project_id,
                    action_ref="durable-delivery-test.message.send",
                    input_json={"text": "New expired job"},
                    credential_ref=credential_ref,
                    idempotency_key="expired-new",
                    durable_due_at=due_at,
                    durable_expires_at=expires_at,
                    durable_account_interval_seconds=5,
                    durable_destination_interval_seconds=3,
                )
            )
        assert len(session.exec(select(ActionCall)).all()) == prior_call_count
        assert len(connector.prepare_requests) == 1
        monkeypatch.undo()
        monkeypatch.setattr(
            "stackos.actions.repository.durable.utcnow",
            lambda: due_at - timedelta(minutes=1),
        )
        # The fixture's unrelated running ActionCall is reconciled, but the
        # durable parent must remain pending for its item dispatcher.
        assert repo.reconcile_running_calls() == 1
        preserved = session.get(ActionCall, accepted.action_call.id)
        assert preserved is not None
        assert preserved.status == ActionCallStatus.RUNNING
    engine.dispose()


def test_durable_expiry_and_cancel_only_change_receipt_proven_no_effect_items(
    tmp_path: Path,
) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = (datetime.now(UTC) + timedelta(minutes=5)).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        mixed_job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:active",
                    "input_json": {"chat_id": 10, "text": "active"},
                },
                {
                    "destination_ref": "telegram-user:remaining",
                    "input_json": {"chat_id": 11, "text": "remaining"},
                },
            ],
            due_at=now,
        )
        active = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=mixed_job.id,
            now=now,
            limit=1,
        )[0]
        cancelled = repo.cancel_durable_action_job(
            project_id=project_id,
            job_id=mixed_job.id,
            now=now + timedelta(seconds=1),
        )
        assert cancelled.state == "running"
        items = repo.list_durable_action_items(project_id=project_id, job_id=mixed_job.id)
        assert [item.state for item in items] == ["leased", "cancelled"]
        assert items[0].attempt_ref == active.attempt_ref
        assert cancelled.can_cancel is False

        expiry_call_id = _new_action_call(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
        )
        expiry_claim_at = now + timedelta(minutes=2)
        expiry = now + timedelta(minutes=3)
        expired_job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=expiry_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:expiry-active",
                    "input_json": {"chat_id": 12, "text": "active"},
                },
                {
                    "destination_ref": "telegram-user:expiry-pending",
                    "input_json": {"chat_id": 13, "text": "pending"},
                },
            ],
            due_at=now,
            expires_at=expiry,
        )
        leased = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=expired_job.id,
            now=expiry_claim_at,
            limit=1,
        )[0]
        assert repo.expire_durable_action_jobs(now=expiry) == 1
        expired_items = repo.list_durable_action_items(
            project_id=project_id,
            job_id=expired_job.id,
        )
        assert [item.state for item in expired_items] == ["leased", "cancelled"]
        assert expired_items[0].attempt_ref == leased.attempt_ref
        assert (
            repo.claim_durable_action_items(
                project_id=project_id,
                job_id=expired_job.id,
                now=expiry + timedelta(seconds=1),
            )
            == []
        )
        parent = session.get(ActionCall, expiry_call_id)
        assert parent is not None
        assert parent.status == ActionCallStatus.RUNNING
    engine.dispose()


def test_durable_retry_output_fails_closed_for_unknown_or_partial_receipts(
    tmp_path: Path,
) -> None:
    engine, project_id, _second_project_id, credential_ref, action_call_id = _database(tmp_path)
    now = datetime(2026, 9, 22, 15, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        repo = ActionRepository(session)
        job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:retry-safe",
                    "input_json": {"chat_id": 14, "text": "safe"},
                },
                {
                    "destination_ref": "telegram-user:retry-unknown",
                    "input_json": {"chat_id": 15, "text": "unknown"},
                },
            ],
            due_at=now,
        )
        safe = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=job.id,
            now=now,
        )[0]
        failed = repo.fail_durable_action_item(
            project_id=project_id,
            item_id=safe.id,
            attempt_ref=safe.attempt_ref,
            error="pre-effect request rejected",
            result_json={"provider_executed": False, "retry_safe": True},
            now=now,
        )
        assert failed.can_retry is True
        unknown = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=job.id,
            now=now + timedelta(seconds=2),
        )[0]
        repo.hold_durable_action_item_unknown(
            project_id=project_id,
            item_id=unknown.id,
            attempt_ref=unknown.attempt_ref,
            reason="provider delivery result unavailable",
            now=now + timedelta(seconds=2),
        )
        listed = repo.list_durable_action_items(project_id=project_id, job_id=job.id)
        assert listed[0].can_retry is False
        with pytest.raises(ConflictError, match="awaits an unknown receipt"):
            repo.retry_durable_action_items(
                project_id=project_id,
                job_id=job.id,
                item_ids=[safe.id],
                now=now + timedelta(seconds=3),
            )

        partial_call_id = _new_action_call(
            session,
            project_id=project_id,
            credential_ref=credential_ref,
        )
        partial_job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=partial_call_id,
            credential_ref=credential_ref,
            action_ref="communications.telegram.message.send",
            items=[
                {
                    "destination_ref": "telegram-user:album",
                    "input_json": {"chat_id": 16, "text": "album"},
                }
            ],
            due_at=now,
        )
        partial = repo.claim_durable_action_items(
            project_id=project_id,
            job_id=partial_job.id,
            now=now + timedelta(seconds=4),
        )[0]
        partial_failed = repo.fail_durable_action_item(
            project_id=project_id,
            item_id=partial.id,
            attempt_ref=partial.attempt_ref,
            error="album progress was observed",
            result_json={
                "provider_executed": False,
                "retry_safe": True,
                "temporary_message_ref": "telegram-message:16:-1",
            },
            now=now + timedelta(seconds=4),
        )
        assert partial_failed.can_retry is False
        with pytest.raises(ConflictError, match="receipt proving no provider effect"):
            repo.retry_durable_action_items(
                project_id=project_id,
                job_id=partial_job.id,
                item_ids=[partial.id],
                now=now + timedelta(seconds=5),
            )
    engine.dispose()
