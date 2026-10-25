"""Generic durable action dispatcher integration coverage."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

import stackos.actions.durable_dispatcher as durable_dispatcher_module
from stackos.actions import (
    ActionConnectorError,
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionRepository,
    ActionValidationIssue,
)
from stackos.actions.durable_dispatcher import DurableActionDispatcher
from stackos.auth_providers import AuthRepository, ResolvedCredential
from stackos.db.connection import make_engine
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    ActionDeliveryAdmission,
    Credential,
    DurableActionAttempt,
    DurableActionItem,
    DurableActionJob,
    IntegrationCredential,
    Plugin,
    PluginSource,
    ProjectCredential,
    Provider,
    Run,
    RunKind,
    RunPlan,
    RunPlanStep,
    RunPlanStepStatus,
    RunStatus,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ArtifactRepository
from stackos.repositories.run_plans import RunPlanRepository


class _DispatchConnector:
    key = "test.durable-dispatch"

    def __init__(self, *, mode: str = "success") -> None:
        self.mode = mode
        self.requests: list[ActionConnectorRequest] = []
        self.release: asyncio.Event | None = None

    def validate(self, _request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        return []

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        self.requests.append(request)
        if self.mode == "blocked":
            assert self.release is not None
            await self.release.wait()
        if self.mode == "flood":
            raise ActionConnectorError(
                "flood wait",
                output_json={
                    "retry_after_seconds": 30,
                    "provider_error": "FLOOD_WAIT",
                    "provider_executed": False,
                    "retry_safe": True,
                    "retry_scope": "account",
                },
            )
        if self.mode == "slowmode":
            raise ActionConnectorError(
                "slowmode wait",
                output_json={
                    "retry_after_seconds": 30,
                    "provider_error": "SLOWMODE_WAIT_30",
                    "provider_executed": False,
                    "retry_safe": True,
                    "retry_scope": "destination",
                },
            )
        if self.mode in {"flood_final", "slowmode_final"}:
            account_wait = self.mode == "flood_final"
            raise ActionConnectorError(
                "final send failed with provider wait",
                output_json={
                    "retry_after_seconds": 30,
                    "provider_error": "FLOOD_WAIT_30" if account_wait else "SLOWMODE_WAIT_30",
                    "provider_executed": True,
                    "retry_safe": False,
                    "retry_scope": "account" if account_wait else "destination",
                },
            )
        if self.mode == "restricted":
            raise ActionConnectorError(
                "account restricted",
                output_json={"account_restricted": True, "provider_error": "PEER_FLOOD"},
            )
        if self.mode == "unknown":
            assert request.progress_callback is not None
            request.progress_callback({"phase": "provider_accepted", "temporary_ids": [-1]})
            raise RuntimeError("native wait lost")
        if self.mode == "unknown_result":
            raise ActionConnectorError(
                "receipt uncertain",
                output_json={
                    "outcome_unknown": True,
                    "retry_safe": False,
                    "provider_error": "TDLIB_RECEIPT_TIMEOUT",
                },
            )
        return ActionConnectorResult(output_json={"message_ref": "provider-message:1"})


def _seed_database(tmp_path: Path) -> tuple[Engine, int, str, int]:
    engine = make_engine(tmp_path / "durable-dispatcher.sqlite")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        project = (
            ProjectRepository(session)
            .create(
                slug="durable-dispatcher",
                name="Durable Dispatcher",
                domain="durable-dispatcher.example.test",
                locale="en-US",
            )
            .data
        )
        assert project.id is not None
        plugin = Plugin(
            slug="durable-dispatcher-test",
            name="Durable Dispatcher Test",
            version="0.1.0",
            source=PluginSource.PROJECT,
            manifest_json={},
        )
        session.add(plugin)
        session.flush()
        assert plugin.id is not None
        provider = Provider(
            plugin_id=plugin.id,
            key="test-provider",
            name="Test provider",
            description="",
            auth_type="native",
        )
        session.add(provider)
        session.flush()
        action = Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key="message.send",
            name="Send",
            description="",
            capability_key=None,
            risk_level="write",
            input_schema_json={"type": "object", "additionalProperties": True},
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": _DispatchConnector.key,
                "operation": "message.send",
                "execution_mode": "background",
                "durable_delivery": True,
                "requires_credential": True,
            },
        )
        session.add(action)
        integration = IntegrationCredential(encrypted_payload=b"test", nonce=b"0" * 12)
        session.add(integration)
        session.flush()
        credential = Credential(
            integration_credential_id=integration.id,
            credential_ref="cred_durable_dispatch",
            provider_key="test-provider",
            display_name="Durable dispatcher",
            display_name_key="durable dispatcher",
            auth_type="native",
            auth_method_key="native",
            status="connected",
        )
        session.add(credential)
        session.flush()
        assert credential.id is not None
        session.add(ProjectCredential(project_id=project.id, credential_id=credential.id))
        call = ActionCall(
            project_id=project.id,
            action_id=action.id,
            action_key="message.send",
            plugin_slug=plugin.slug,
            provider_key=provider.key,
            connector_key=_DispatchConnector.key,
            operation="message.send",
            status=ActionCallStatus.RUNNING,
            credential_id=credential.id,
            credential_ref=credential.credential_ref,
        )
        session.add(call)
        session.commit()
        assert call.id is not None
        return engine, project.id, credential.credential_ref, call.id


@pytest.fixture
def fake_credential_resolution(monkeypatch: pytest.MonkeyPatch):
    async def resolve(self: AuthRepository, **kwargs: object) -> ResolvedCredential:
        credential_ref = str(kwargs["credential_ref"])
        credential = self._s.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        integration = self._s.get(IntegrationCredential, credential.integration_credential_id)
        assert integration is not None
        return ResolvedCredential(
            credential=credential,
            integration=integration,
            secret_payload=b"test",
        )

    monkeypatch.setattr(AuthRepository, "resolve_for_execution", resolve)


def _job(
    engine: Engine,
    project_id: int,
    credential_ref: str,
    action_call_id: int,
    now: datetime,
    *,
    destinations: tuple[str, ...] = ("destination:1",),
    expires_at: datetime | None = None,
    pacing_json: dict[str, float] | None = None,
    item_input: dict[str, object] | None = None,
    pacing_units_input_field: str | None = None,
    destination_interval_multipliers: list[dict[str, object]] | None = None,
) -> int:
    with Session(engine) as session:
        job = ActionRepository(session).create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="durable-dispatcher-test.message.send",
            items=[
                {
                    "destination_ref": destination,
                    "input_json": dict(item_input or {"body": "hello"}),
                }
                for destination in destinations
            ],
            due_at=now,
            expires_at=expires_at,
            pacing_json=pacing_json,
            pacing_units_input_field=pacing_units_input_field,
            destination_interval_multipliers=destination_interval_multipliers,
        )
        return job.id


def _dispatcher(
    engine: Engine,
    connector: _DispatchConnector,
    *,
    asset_dir: Path | None = None,
    max_inflight: int = 64,
) -> DurableActionDispatcher:
    registry = ActionConnectorRegistry()
    registry.register(connector)
    return DurableActionDispatcher(
        lambda: Session(engine),
        registry,
        asset_dir=asset_dir,
        poll_interval_seconds=0.01,
        max_inflight=max_inflight,
    )


def _workflow_scope(engine: Engine, project_id: int, action_call_id: int) -> tuple[int, int, int]:
    with Session(engine) as session:
        repo = RunPlanRepository(session)
        plan = repo.create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "durable-lifecycle",
                "title": "Deliver queued work",
                "steps": [{"id": "send", "title": "Send"}],
            },
        ).data
        started = repo.start(plan.id, project_id=project_id).data
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="send")
        step = session.exec(select(RunPlanStep).where(RunPlanStep.run_plan_id == plan.id)).one()
        call = session.get(ActionCall, action_call_id)
        assert call is not None and started.run_id is not None and step.id is not None
        call.run_id, call.run_plan_id, call.run_plan_step_id = started.run_id, plan.id, step.id
        session.add(call)
        session.commit()
        return started.run_id, plan.id, step.id


def _stop_workflow(engine: Engine, project_id: int, plan_id: int, *, abort: bool = False) -> None:
    with Session(engine) as session:
        repo = RunPlanRepository(session)
        if abort:
            repo.abort(run_plan_id=plan_id, project_id=project_id, reason="Operator stopped work")
        else:
            plan = session.get(RunPlan, plan_id)
            assert plan is not None
            repo.record_step(
                run_plan_id=plan_id,
                run_id=plan.run_id,
                step_id="send",
                status=RunPlanStepStatus.BLOCKED,
                error="Operator paused work",
            )


@pytest.mark.parametrize("abort", [False, True], ids=["blocked", "aborted"])
def test_workflow_claim_reads_current_controller_before_any_lease(
    tmp_path: Path, abort: bool
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    run_id, plan_id, step_id = _workflow_scope(engine, project_id, call_id)
    now = datetime(2026, 9, 22, 18)
    job_id = _job(engine, project_id, credential_ref, call_id, now)
    with Session(engine) as session:
        # Keep stale identity-map objects alive; another session changes the controller.
        before = [
            session.get(Run, run_id),
            session.get(RunPlan, plan_id),
            session.get(RunPlanStep, step_id),
        ]
        _stop_workflow(engine, project_id, plan_id, abort=abort)
        repo = ActionRepository(session)
        assert repo.claim_durable_action_items(project_id=project_id, job_id=job_id, now=now) == []
        job = repo.get_durable_action_job(project_id=project_id, job_id=job_id)
        assert job.state == "paused" and job.pause_reason
        assert (
            repo.list_durable_action_items(project_id=project_id, job_id=job_id)[0].attempt_count
            == 0
        )
        assert session.exec(select(DurableActionAttempt)).all() == []
        assert session.exec(select(ActionDeliveryAdmission)).all() == []
        assert all(before)
    engine.dispose()


@pytest.mark.parametrize(
    "broken",
    [
        "run-ref",
        "plan-ref",
        "step-ref",
        "workflow-refs",
        "run-project",
        "plan-project",
        "plan-run",
        "step-plan",
        "run-status",
        "run-kind",
    ],
)
def test_workflow_claim_rejects_incomplete_or_inconsistent_scope(
    tmp_path: Path, broken: str
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    run_id, plan_id, step_id = _workflow_scope(engine, project_id, call_id)
    now = datetime(2026, 9, 22, 18)
    job_id = _job(engine, project_id, credential_ref, call_id, now)
    with Session(engine) as session:
        job = session.get(DurableActionJob, job_id)
        run = session.get(Run, run_id)
        plan = session.get(RunPlan, plan_id)
        step = session.get(RunPlanStep, step_id)
        assert job is not None and run is not None and plan is not None and step is not None
        if broken == "run-ref":
            job.run_id = None
        elif broken == "plan-ref":
            job.run_plan_id = None
        elif broken == "step-ref":
            job.run_plan_step_id = None
        elif broken == "workflow-refs":
            job.run_plan_id = job.run_plan_step_id = None
        elif broken in {"run-project", "plan-project"}:
            other = (
                ProjectRepository(session)
                .create(slug="other", name="Other", domain="other.example.test", locale="en-US")
                .data
            )
            if broken == "run-project":
                run.project_id = other.id
            else:
                plan.project_id = other.id
        elif broken == "plan-run":
            plan.run_id = None
        elif broken == "step-plan":
            other_plan = (
                RunPlanRepository(session)
                .create(
                    project_id=project_id,
                    run_plan_json={
                        "schema_version": "stackos.run-plan.v1",
                        "key": "other",
                        "title": "Other",
                        "steps": [{"id": "other", "title": "Other"}],
                    },
                )
                .data
            )
            step.run_plan_id = other_plan.id
        elif broken == "run-status":
            run.status = RunStatus.FAILED
        else:
            run.kind = RunKind.ACTION
        session.add_all([job, run, plan, step])
        session.commit()
        repo = ActionRepository(session)
        assert repo.claim_durable_action_items(project_id=project_id, job_id=job_id, now=now) == []
        assert repo.get_durable_action_job(project_id=project_id, job_id=job_id).pause_reason
        assert session.exec(select(DurableActionAttempt)).all() == []
        assert session.exec(select(ActionDeliveryAdmission)).all() == []
    engine.dispose()


@pytest.mark.parametrize("abort", [False, True], ids=["blocked", "aborted"])
def test_workflow_rechecked_after_awaited_credentials_before_provider(
    tmp_path: Path, fake_credential_resolution: None, monkeypatch: pytest.MonkeyPatch, abort: bool
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    run_id, plan_id, step_id = _workflow_scope(engine, project_id, call_id)
    now = datetime(2026, 9, 22, 18)
    job_id = _job(engine, project_id, credential_ref, call_id, now, destinations=("one", "two"))
    connector = _DispatchConnector()
    resolve = ActionRepository._resolve_credential

    async def scenario() -> None:
        waiting, release = asyncio.Event(), asyncio.Event()

        async def delayed(repo: ActionRepository, **kwargs):
            stale = [
                repo._s.get(Run, run_id),
                repo._s.get(RunPlan, plan_id),
                repo._s.get(RunPlanStep, step_id),
            ]
            credential = await resolve(repo, **kwargs)
            waiting.set()
            await release.wait()
            assert all(stale)
            return credential

        monkeypatch.setattr(ActionRepository, "_resolve_credential", delayed)
        dispatcher = _dispatcher(engine, connector)
        task = asyncio.create_task(dispatcher.run_once(now=now))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        _stop_workflow(engine, project_id, plan_id, abort=abort)
        release.set()
        assert await asyncio.wait_for(task, timeout=5) == 1
        assert connector.requests == []
        assert await dispatcher.run_once(now=now + timedelta(minutes=1)) == 0

    asyncio.run(scenario())
    with Session(engine) as session:
        repo = ActionRepository(session)
        job = repo.get_durable_action_job(project_id=project_id, job_id=job_id)
        assert job.state == "paused" and job.pause_reason
        failed, pending = repo.list_durable_action_items(project_id=project_id, job_id=job_id)
        assert failed.state == "failed" and failed.can_retry
        assert failed.result_json == {"provider_executed": False, "retry_safe": True}
        assert failed.lease_ref is None
        assert pending.state == "pending" and pending.attempt_count == 0
        attempt = session.exec(select(DurableActionAttempt)).one()
        assert attempt.state == "failed" and attempt.receipt_json == failed.result_json
    engine.dispose()


@pytest.mark.parametrize("workflow", [False, True], ids=["direct", "workflow"])
@pytest.mark.parametrize("control", ["pause", "cancel", "expiry", "lease-missing", "lease-expired"])
def test_job_control_during_preparation_refuses_connector_entry(
    tmp_path: Path,
    fake_credential_resolution: None,
    monkeypatch: pytest.MonkeyPatch,
    workflow: bool,
    control: str,
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    if workflow:
        _workflow_scope(engine, project_id, call_id)
    now = datetime(2099, 9, 22, 18)
    deadline = now + timedelta(seconds=30)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        call_id,
        now,
        destinations=("one", "two"),
        expires_at=deadline if control == "expiry" else None,
    )
    connector = _DispatchConnector()
    resolve = ActionRepository._resolve_credential
    current = now
    monkeypatch.setattr(durable_dispatcher_module, "_as_naive_utc", lambda value: value or current)

    async def scenario() -> None:
        nonlocal current
        waiting, release = asyncio.Event(), asyncio.Event()

        async def delayed(repo: ActionRepository, **kwargs):
            cached = repo._s.get(DurableActionJob, job_id)
            credential = await resolve(repo, **kwargs)
            waiting.set()
            await release.wait()
            assert cached is not None
            return credential

        monkeypatch.setattr(ActionRepository, "_resolve_credential", delayed)
        dispatcher = _dispatcher(engine, connector)
        task = asyncio.create_task(dispatcher.run_once())
        await asyncio.wait_for(waiting.wait(), timeout=5)
        replacement_admissions = None
        if control == "expiry":
            current = deadline  # No expiry sweep: entry must evaluate the fresh clock.
        elif control in {"lease-missing", "lease-expired"}:
            with Session(engine) as session:
                leased = session.exec(
                    select(DurableActionItem).where(DurableActionItem.ordinal == 0)
                ).one()
                assert leased.lease_expires_at == now + timedelta(seconds=90)
                if control == "lease-missing":
                    leased.lease_expires_at = None
                    session.add(leased)
                    session.commit()
                else:
                    current = leased.lease_expires_at  # Equality is already expired.
                    replacement = ActionRepository(session).admit_delivery(
                        project_id=project_id,
                        credential_ref=credential_ref,
                        destination_ref=leased.destination_ref,
                        now=current,
                        lease_seconds=90,
                    )
                    assert replacement.admitted and replacement.lease_ref != leased.lease_ref
                    replacement_admissions = [
                        row.model_dump() for row in session.exec(select(ActionDeliveryAdmission))
                    ]
        else:
            with Session(engine) as session:
                repo = ActionRepository(session)
                operation = getattr(repo, f"{control}_durable_action_job")
                controlled = operation(project_id=project_id, job_id=job_id, now=current)
                if control == "cancel":
                    assert controlled.state == "running"
                    cancelled_job = session.get(DurableActionJob, job_id)
                    assert cancelled_job is not None and cancelled_job.cancelled_at == current
        release.set()
        assert await asyncio.wait_for(task, timeout=5) == 1
        assert connector.requests == []
        with Session(engine) as session:
            repo = ActionRepository(session)
            refused = repo.list_durable_action_items(project_id=project_id, job_id=job_id)[0]
            assert refused.state == "failed"
            assert refused.result_json == {"provider_executed": False, "retry_safe": True}
            assert refused.lease_ref is None
            attempt = session.exec(select(DurableActionAttempt)).one()
            assert attempt.state == "failed" and attempt.receipt_json == refused.result_json
            if replacement_admissions is not None:
                assert [
                    row.model_dump() for row in session.exec(select(ActionDeliveryAdmission))
                ] == replacement_admissions
        assert await dispatcher.run_once() == 0
        assert connector.requests == []

    asyncio.run(scenario())
    engine.dispose()


@pytest.mark.parametrize("workflow", [False, True], ids=["direct", "workflow"])
@pytest.mark.parametrize("transition", ["complete", "unknown", "supersede"])
def test_attempt_lost_during_preparation_preserves_authoritative_receipt(
    tmp_path: Path,
    fake_credential_resolution: None,
    monkeypatch: pytest.MonkeyPatch,
    workflow: bool,
    transition: str,
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    if workflow:
        _workflow_scope(engine, project_id, call_id)
    now = datetime(2026, 9, 22, 18)
    job_id = _job(engine, project_id, credential_ref, call_id, now)
    connector = _DispatchConnector()
    resolve = ActionRepository._resolve_credential

    async def scenario() -> None:
        waiting, release = asyncio.Event(), asyncio.Event()

        async def delayed(repo: ActionRepository, **kwargs):
            # Retain stale job/item/attempt objects across the other session's settlement.
            cached = [
                repo._s.get(DurableActionJob, job_id),
                repo._s.exec(select(DurableActionItem)).one(),
                repo._s.exec(select(DurableActionAttempt)).one(),
            ]
            credential = await resolve(repo, **kwargs)
            waiting.set()
            await release.wait()
            assert all(cached)
            return credential

        monkeypatch.setattr(ActionRepository, "_resolve_credential", delayed)
        task = asyncio.create_task(_dispatcher(engine, connector).run_once(now=now))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        with Session(engine) as session:
            repo = ActionRepository(session)
            item = repo.list_durable_action_items(project_id=project_id, job_id=job_id)[0]
            assert item.attempt_ref is not None
            args = dict(
                project_id=project_id, item_id=item.id, attempt_ref=item.attempt_ref, now=now
            )
            if transition == "complete":
                repo.complete_durable_action_item(**args, result_json={"receipt": "authoritative"})
            elif transition == "unknown":
                repo.hold_durable_action_item_unknown(**args, reason="authoritative uncertainty")
            else:
                repo.fail_durable_action_item(
                    **args,
                    error="no effect",
                    result_json={"provider_executed": False, "retry_safe": True},
                )
                later = now + timedelta(seconds=2)
                repo.retry_durable_action_items(
                    project_id=project_id, job_id=job_id, item_ids=[item.id], now=later
                )
                replacement = repo.claim_durable_action_items(
                    project_id=project_id, job_id=job_id, now=later
                )[0]
                assert replacement.attempt_ref != item.attempt_ref
                assert replacement.state == "leased"
            expected_items = [row.model_dump() for row in session.exec(select(DurableActionItem))]
            expected_attempts = [
                row.model_dump() for row in session.exec(select(DurableActionAttempt))
            ]
        release.set()
        assert await asyncio.wait_for(task, timeout=5) == 1
        assert connector.requests == []
        with Session(engine) as session:
            assert [
                row.model_dump() for row in session.exec(select(DurableActionItem))
            ] == expected_items
            assert [
                row.model_dump() for row in session.exec(select(DurableActionAttempt))
            ] == expected_attempts

    asyncio.run(scenario())
    engine.dispose()


def test_workflow_explicit_recovery_rechecks_same_binding(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    run_id, plan_id, step_id = _workflow_scope(engine, project_id, call_id)
    now = datetime(2026, 9, 22, 18)
    job_id = _job(engine, project_id, credential_ref, call_id, now)
    connector = _DispatchConnector()
    dispatcher = _dispatcher(engine, connector)
    for _ in range(2):
        _stop_workflow(engine, project_id, plan_id)
        assert asyncio.run(dispatcher.run_once(now=now)) == 0
        with Session(engine) as session:
            RunPlanRepository(session).claim_step(
                run_plan_id=plan_id, run_id=run_id, step_id="send"
            )
            repo = ActionRepository(session)
            repo.resume_durable_action_job(project_id=project_id, job_id=job_id, now=now)
            job = session.get(DurableActionJob, job_id)
            assert job is not None
            assert (job.run_id, job.run_plan_id, job.run_plan_step_id) == (run_id, plan_id, step_id)
    assert asyncio.run(dispatcher.run_once(now=now)) == 1
    assert len(connector.requests) == 1
    engine.dispose()


@pytest.mark.parametrize("direct_run", [False, True])
def test_workflow_denial_does_not_prevent_unrelated_direct_dispatch(
    tmp_path: Path, fake_credential_resolution: None, direct_run: bool
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    _, plan_id, _ = _workflow_scope(engine, project_id, call_id)
    now = datetime(2026, 9, 22, 18)
    _job(engine, project_id, credential_ref, call_id, now)
    with Session(engine) as session:
        call = ActionCall(
            project_id=project_id,
            action_key="message.send",
            plugin_slug="durable-dispatcher-test",
            operation="message.send",
            status=ActionCallStatus.RUNNING,
        )
        if direct_run:
            run = Run(project_id=project_id, kind=RunKind.ACTION, status=RunStatus.SUCCESS)
            session.add(run)
            session.flush()
            call.run_id = run.id
        session.add(call)
        session.commit()
        assert call.id is not None
        direct_id = call.id
    _job(engine, project_id, credential_ref, direct_id, now)
    _stop_workflow(engine, project_id, plan_id)
    connector = _DispatchConnector()
    assert asyncio.run(_dispatcher(engine, connector).run_once(now=now)) == 1
    assert [request.action_call_id for request in connector.requests] == [direct_id]
    engine.dispose()


@pytest.mark.parametrize(
    ("workflow", "control"),
    [
        (True, "workflow"),
        (False, "pause"),
        (True, "pause"),
        (False, "cancel"),
        (True, "cancel"),
        (False, "lease-expiry"),
        (True, "lease-expiry"),
    ],
)
@pytest.mark.parametrize("outcome", ["success", "flood_final", "unknown", "cancelled"])
def test_stop_after_provider_entry_preserves_receipt(
    tmp_path: Path,
    fake_credential_resolution: None,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    workflow: bool,
    control: str,
) -> None:
    engine, project_id, credential_ref, call_id = _seed_database(tmp_path)
    if workflow:
        _, plan_id, _ = _workflow_scope(engine, project_id, call_id)
    now = datetime(2026, 9, 22, 18)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        call_id,
        now,
        destinations=("one",) if control == "lease-expiry" else ("one", "two"),
    )
    current = now
    monkeypatch.setattr(durable_dispatcher_module, "_as_naive_utc", lambda value: value or current)

    async def scenario() -> None:
        nonlocal current
        entered, release = asyncio.Event(), asyncio.Event()

        class EnteredConnector(_DispatchConnector):
            async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
                entered.set()
                await release.wait()
                return await super().execute(request)

        connector = EnteredConnector(mode=outcome)
        dispatcher = _dispatcher(engine, connector)
        task = asyncio.create_task(dispatcher.run_once())
        await asyncio.wait_for(entered.wait(), timeout=5)
        if control == "workflow":
            _stop_workflow(engine, project_id, plan_id)
        elif control == "lease-expiry":
            current = now + timedelta(seconds=91)
            with Session(engine) as session:
                leased = session.exec(select(DurableActionItem)).one()
                assert leased.lease_expires_at is not None and leased.lease_expires_at < current
        else:
            with Session(engine) as session:
                operation = getattr(ActionRepository(session), f"{control}_durable_action_job")
                operation(project_id=project_id, job_id=job_id, now=now)
        if outcome == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            assert await asyncio.wait_for(task, timeout=5) == 1
        assert await dispatcher.run_once(now=now + timedelta(minutes=1)) == 0

    asyncio.run(scenario())
    with Session(engine) as session:
        item = ActionRepository(session).list_durable_action_items(
            project_id=project_id, job_id=job_id
        )[0]
        expected_state = {"success": "succeeded", "flood_final": "failed"}.get(
            outcome, "unknown-hold"
        )
        assert item.state == expected_state
        assert not item.can_retry
        if outcome == "success":
            assert item.result_json == {"message_ref": "provider-message:1"}
        elif outcome == "flood_final":
            assert item.result_json is not None
            assert item.result_json["provider_executed"] is True
            assert item.result_json["retry_safe"] is False
        else:
            assert item.result_json is not None and item.result_json["outcome_unknown"] is True
    engine.dispose()


def test_dispatcher_persists_progress_and_completes_parent_action_call(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    _job(engine, project_id, credential_ref, action_call_id, now)
    connector = _DispatchConnector()

    assert asyncio.run(_dispatcher(engine, connector).run_once(now=now)) == 1
    assert len(connector.requests) == 1
    request = connector.requests[0]
    assert request.action_call_id == action_call_id
    assert request.attempt_ref is not None
    assert request.correlation_ref is not None
    assert request.delivery_item_id is not None

    with Session(engine) as session:
        call = session.get(ActionCall, action_call_id)
        assert call is not None
        assert call.status == ActionCallStatus.SUCCESS
        assert call.response_json is not None
        assert call.response_json["completed_count"] == 1
    engine.dispose()


def test_dispatcher_rejects_changed_sealed_media_before_connector_effect(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    assets = tmp_path / "generated-assets"
    source = assets / "telegram" / "media.jpg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"sealed media")
    with Session(engine) as session:
        artifact = (
            ArtifactRepository(session)
            .create(
                project_id=project_id,
                plugin_slug="communications",
                kind="file",
                uri="/generated-assets/telegram/media.jpg",
                status="approved",
            )
            .data
        )
        job = ActionRepository(session, asset_dir=assets).create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=credential_ref,
            action_ref="durable-dispatcher-test.message.send",
            items=[
                {
                    "destination_ref": "destination:media",
                    "input_json": {"content": {"file": {"artifact_ref": artifact.uri}}},
                }
            ],
            due_at=now,
        )
    source.write_bytes(b"different bytes at the sealed URI")
    connector = _DispatchConnector()
    assert asyncio.run(_dispatcher(engine, connector, asset_dir=assets).run_once(now=now)) == 1
    assert connector.requests == []
    with Session(engine) as session:
        repo = ActionRepository(session, asset_dir=assets)
        item = repo.list_durable_action_items(project_id=project_id, job_id=job.id)[0]
        assert item.state == "failed"
        assert item.error == "durable delivery artifact bytes changed after the job was sealed"
    engine.dispose()


def test_dispatcher_defers_typed_flood_wait_and_holds_ambiguous_after_acceptance(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(engine, project_id, credential_ref, action_call_id, now)
    flood = _DispatchConnector(mode="flood")
    dispatcher = _dispatcher(engine, flood)

    assert asyncio.run(dispatcher.run_once(now=now)) == 1
    with Session(engine) as session:
        status = ActionRepository(session).get_durable_action_job(
            project_id=project_id, job_id=job_id
        )
        assert status.state == "scheduled"
        assert status.pending_count == 1
        deferred = ActionRepository(session).list_durable_action_items(
            project_id=project_id, job_id=job_id
        )[0]
        assert deferred.result_json is not None
        assert deferred.result_json["provider_error"] == "FLOOD_WAIT"
        assert deferred.result_json["retry_after_seconds"] == 30
    assert asyncio.run(dispatcher.run_once(now=now + timedelta(seconds=29))) == 0

    unknown = _DispatchConnector(mode="unknown")
    assert asyncio.run(_dispatcher(engine, unknown).run_once(now=now + timedelta(seconds=30))) == 1
    with Session(engine) as session:
        repo = ActionRepository(session)
        status = repo.get_durable_action_job(project_id=project_id, job_id=job_id)
        assert status.state == "unknown-hold"
        held = repo.list_durable_action_items(project_id=project_id, job_id=job_id)[0]
        assert held.attempt_ref is not None
        assert held.progress_json == {"phase": "provider_accepted", "temporary_ids": [-1]}
        assert repo.list_unknown_held_durable_action_items(credential_ref=credential_ref) == [held]
        receipt_progress = repo.record_durable_action_item_progress(
            project_id=project_id,
            item_id=held.id,
            attempt_ref=held.attempt_ref,
            progress_json={"provider_receipts": {"-1": {"message_id": 1001}}},
            now=now + timedelta(seconds=30),
        )
        assert receipt_progress.state == "unknown-hold"
        assert receipt_progress.progress_json == {
            "phase": "provider_accepted",
            "temporary_ids": [-1],
            "provider_receipts": {"-1": {"message_id": 1001}},
        }
        call = session.get(ActionCall, action_call_id)
        assert call is not None
        assert call.response_json is not None
        assert call.response_json["outcome_unknown"] is True
        reconciled = repo.reconcile_durable_action_item(
            project_id=project_id,
            item_id=held.id,
            attempt_ref=held.attempt_ref,
            result_json={"message_ref": "provider-message:late"},
            success=True,
            now=now + timedelta(seconds=31),
        )
        assert reconciled.state == "succeeded"
        session.refresh(call)
        assert call.status == ActionCallStatus.SUCCESS
        assert call.response_json is not None
        assert call.response_json["completed_count"] == 1
    engine.dispose()


def test_dispatcher_bases_flood_deferral_on_the_provider_transition_time(
    tmp_path: Path,
    fake_credential_resolution: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    due = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(engine, project_id, credential_ref, action_call_id, due)
    scan_at = due + timedelta(seconds=1)
    transition_at = due + timedelta(seconds=45)
    current = scan_at

    def clock_now(value: datetime | None) -> datetime:
        return value if value is not None else current

    class TimedFloodConnector(_DispatchConnector):
        async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
            nonlocal current
            current = transition_at
            return await super().execute(request)

    monkeypatch.setattr(durable_dispatcher_module, "_as_naive_utc", clock_now)
    assert asyncio.run(_dispatcher(engine, TimedFloodConnector(mode="flood")).run_once()) == 1

    with Session(engine) as session:
        repo = ActionRepository(session)
        job = repo.get_durable_action_job(project_id=project_id, job_id=job_id)
        item = repo.list_durable_action_items(project_id=project_id, job_id=job.id)[0]
        assert item.state == "deferred"
        assert item.next_eligible_at == transition_at + timedelta(seconds=30)
    engine.dispose()


@pytest.mark.parametrize(("mode", "second_attempts"), [("flood", 0), ("slowmode", 1)])
def test_provider_wait_scope_controls_other_destinations(
    tmp_path: Path,
    fake_credential_resolution: None,
    mode: str,
    second_attempts: int,
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        action_call_id,
        now,
        destinations=("destination:1", "destination:2"),
    )
    connector = _DispatchConnector(mode=mode)
    dispatcher = _dispatcher(engine, connector)
    assert asyncio.run(dispatcher.run_once(now=now)) == 1
    assert asyncio.run(dispatcher.run_once(now=now + timedelta(seconds=2))) == second_attempts
    with Session(engine) as session:
        items = ActionRepository(session).list_durable_action_items(
            project_id=project_id, job_id=job_id
        )
        assert items[0].state == "deferred"
        assert items[0].result_json is not None
        assert items[0].result_json["retry_scope"] == (
            "account" if mode == "flood" else "destination"
        )
        assert items[1].attempt_count == second_attempts
    engine.dispose()


@pytest.mark.parametrize(("mode", "second_attempts"), [("flood_final", 0), ("slowmode_final", 1)])
def test_terminal_provider_wait_keeps_item_failed_and_cools_admission_scope(
    tmp_path: Path,
    fake_credential_resolution: None,
    mode: str,
    second_attempts: int,
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        action_call_id,
        now,
        destinations=("destination:1", "destination:2"),
    )
    connector = _DispatchConnector(mode=mode)
    dispatcher = _dispatcher(engine, connector)
    assert asyncio.run(dispatcher.run_once(now=now)) == 1
    assert asyncio.run(dispatcher.run_once(now=now + timedelta(seconds=2))) == second_attempts
    with Session(engine) as session:
        items = ActionRepository(session).list_durable_action_items(
            project_id=project_id, job_id=job_id
        )
        assert items[0].state == "failed"
        assert items[0].attempt_count == 1
        assert items[0].result_json is not None
        assert items[0].result_json["retry_safe"] is False
        assert items[1].attempt_count == second_attempts
    engine.dispose()


def test_unknown_hold_exposes_provider_diagnostics_without_retry(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(engine, project_id, credential_ref, action_call_id, now)
    assert (
        asyncio.run(
            _dispatcher(engine, _DispatchConnector(mode="unknown_result")).run_once(now=now)
        )
        == 1
    )
    with Session(engine) as session:
        item = ActionRepository(session).list_durable_action_items(
            project_id=project_id, job_id=job_id
        )[0]
        assert item.state == "unknown-hold"
        assert item.can_retry is False
        assert item.result_json is not None
        assert item.result_json["provider_error"] == "TDLIB_RECEIPT_TIMEOUT"
        assert item.result_json["outcome_unknown"] is True
    engine.dispose()


def test_dispatcher_reports_restriction_without_quiescing_other_project_jobs(
    tmp_path: Path,
    fake_credential_resolution: None,
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    first_job_id = _job(engine, project_id, credential_ref, action_call_id, now)
    with Session(engine) as session:
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == credential_ref)
        ).one()
        assert credential.id is not None
        project = (
            ProjectRepository(session)
            .create(
                slug="durable-dispatcher-attached",
                name="Attached durable dispatcher",
                domain="durable-dispatcher-attached.example.test",
                locale="en-US",
            )
            .data
        )
        assert project.id is not None
        session.add(ProjectCredential(project_id=project.id, credential_id=credential.id))
        action = session.exec(select(Action)).one()
        second_call = ActionCall(
            project_id=project.id,
            action_id=action.id,
            action_key=action.key,
            plugin_slug="durable-dispatcher-test",
            provider_key="test-provider",
            connector_key=_DispatchConnector.key,
            operation="message.send",
            status=ActionCallStatus.RUNNING,
            credential_id=credential.id,
            credential_ref=credential_ref,
        )
        session.add(second_call)
        session.commit()
        assert second_call.id is not None
        second_job = ActionRepository(session).create_durable_action_job(
            project_id=project.id,
            action_call_id=second_call.id,
            credential_ref=credential_ref,
            action_ref="durable-dispatcher-test.message.send",
            items=[{"destination_ref": "destination:2", "input_json": {"body": "later"}}],
            due_at=now,
        )

    connector = _DispatchConnector(mode="restricted")
    assert asyncio.run(_dispatcher(engine, connector).run_once(now=now)) == 1
    assert len(connector.requests) == 1
    with Session(engine) as session:
        repo = ActionRepository(session)
        first_status = repo.get_durable_action_job(project_id=project_id, job_id=first_job_id)
        assert first_status.state == "failed"
        waiting = repo.get_durable_action_job(project_id=project.id, job_id=second_job.id)
        assert waiting.pending_count == 1
        assert repo.admit_delivery(
            project_id=project.id,
            credential_ref=credential_ref,
            destination_ref="destination:2",
            now=now + timedelta(seconds=2),
        ).admitted
    engine.dispose()


def test_dispatcher_overlaps_receipts_at_persisted_account_submission_interval(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        action_call_id,
        now,
        destinations=("destination:1", "destination:2"),
        pacing_json={"account_interval_seconds": 0.05, "destination_interval_seconds": 1.0},
    )
    connector = _DispatchConnector(mode="blocked")

    async def exercise() -> None:
        connector.release = asyncio.Event()
        dispatcher = _dispatcher(engine, connector)
        assert await dispatcher.run_once(now=now, wait_for_receipts=False) == 1
        await asyncio.sleep(0)
        assert len(connector.requests) == 1
        assert (
            await dispatcher.run_once(now=now + timedelta(seconds=0.049), wait_for_receipts=False)
            == 0
        )
        assert (
            await dispatcher.run_once(now=now + timedelta(seconds=0.05), wait_for_receipts=False)
            == 1
        )
        await asyncio.sleep(0)
        assert len(connector.requests) == 2
        connector.release.set()
        assert await dispatcher.run_once(now=now + timedelta(seconds=1)) == 0

    asyncio.run(exercise())
    with Session(engine) as session:
        status = ActionRepository(session).get_durable_action_job(
            project_id=project_id, job_id=job_id
        )
        assert status.state == "completed"
        assert status.completed_count == 2
    engine.dispose()


@pytest.mark.parametrize(
    ("field", "units"),
    [("contents", 2), ("contents", 10), ("message_ids", 1), ("message_ids", 100)],
)
def test_multi_message_item_reserves_account_and_destination_units(
    tmp_path: Path,
    fake_credential_resolution: None,
    field: str,
    units: int,
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        action_call_id,
        now,
        destinations=("destination:1", "destination:2"),
        pacing_json={"account_interval_seconds": 0.05, "destination_interval_seconds": 0.1},
        item_input={field: list(range(units))},
        pacing_units_input_field=field,
    )
    connector = _DispatchConnector()
    dispatcher = _dispatcher(engine, connector)
    assert asyncio.run(dispatcher.run_once(now=now)) == 1
    with Session(engine) as session:
        job = ActionRepository(session).get_durable_action_job(project_id=project_id, job_id=job_id)
        assert job.pacing_units_input_field == field
        repo = ActionRepository(session)
        before_destination = repo.admit_delivery(
            project_id=project_id,
            credential_ref=credential_ref,
            destination_ref="destination:1",
            now=now + timedelta(seconds=0.1 * units - 0.001),
            account_interval_seconds=0.05,
            destination_interval_seconds=0.1,
        )
        assert not before_destination.admitted
    assert asyncio.run(dispatcher.run_once(now=now + timedelta(seconds=0.05 * units - 0.001))) == 0
    assert asyncio.run(dispatcher.run_once(now=now + timedelta(seconds=0.05 * units))) == 1
    assert len(connector.requests) == 2
    engine.dispose()


@pytest.mark.parametrize(
    ("field", "item_input"),
    [
        ("contents", {"contents": []}),
        ("message_ids", {"message_ids": "1,2"}),
        ("nested.contents", {"contents": [1, 2]}),
    ],
)
def test_multi_message_pacing_rejects_unsealed_or_invalid_units(
    tmp_path: Path,
    field: str,
    item_input: dict[str, object],
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValidationError, match="durable pacing units"):
        _job(
            engine,
            project_id,
            credential_ref,
            action_call_id,
            now,
            item_input=item_input,
            pacing_units_input_field=field,
        )
    engine.dispose()


@pytest.mark.parametrize(
    ("destination_ref", "expected_seconds"),
    [("telegram-chat:100", 1.43), ("telegram-chat:-100", 4.29)],
)
def test_destination_prefix_pacing_is_shared_across_jobs(
    tmp_path: Path,
    fake_credential_resolution: None,
    destination_ref: str,
    expected_seconds: float,
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    pacing = {"account_interval_seconds": 0.05, "destination_interval_seconds": 1.43}
    multipliers: list[dict[str, object]] = [
        {"destination_ref_prefix": "telegram-chat:-", "multiplier": 3.0}
    ]
    _job(
        engine,
        project_id,
        credential_ref,
        action_call_id,
        now,
        destinations=(destination_ref,),
        pacing_json=pacing,
        destination_interval_multipliers=multipliers,
    )
    with Session(engine) as session:
        first = session.get(ActionCall, action_call_id)
        assert first is not None
        second = ActionCall(
            project_id=project_id,
            action_id=first.action_id,
            action_key=first.action_key,
            plugin_slug=first.plugin_slug,
            provider_key=first.provider_key,
            connector_key=first.connector_key,
            operation=first.operation,
            status=ActionCallStatus.RUNNING,
            credential_id=first.credential_id,
            credential_ref=credential_ref,
        )
        session.add(second)
        session.commit()
        assert second.id is not None
        second_call_id = second.id
    second_job_id = _job(
        engine,
        project_id,
        credential_ref,
        second_call_id,
        now,
        destinations=(destination_ref,),
        pacing_json=pacing,
        destination_interval_multipliers=multipliers,
    )
    connector = _DispatchConnector()
    dispatcher = _dispatcher(engine, connector)
    assert asyncio.run(dispatcher.run_once(now=now)) == 1
    assert (
        asyncio.run(dispatcher.run_once(now=now + timedelta(seconds=expected_seconds - 0.001))) == 0
    )
    with Session(engine) as session:
        pending = ActionRepository(session).list_durable_action_items(
            project_id=project_id, job_id=second_job_id
        )[0]
        assert pending.next_eligible_at == now + timedelta(seconds=expected_seconds)
    assert asyncio.run(dispatcher.run_once(now=now + timedelta(seconds=expected_seconds))) == 1
    with Session(engine) as session:
        second_job = ActionRepository(session).get_durable_action_job(
            project_id=project_id, job_id=second_job_id
        )
        assert second_job.state == "completed"
        assert second_job.destination_interval_multipliers == multipliers
    engine.dispose()


def test_peer_flood_pauses_only_affected_job_with_agent_visible_reason(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        action_call_id,
        now,
        destinations=("destination:1", "destination:2"),
    )
    connector = _DispatchConnector(mode="restricted")
    dispatcher = _dispatcher(engine, connector)
    assert asyncio.run(dispatcher.run_once(now=now)) == 1

    with Session(engine) as session:
        repo = ActionRepository(session)
        status = repo.get_durable_action_job(project_id=project_id, job_id=job_id)
        assert status.state == "paused"
        assert status.pause_reason == "provider_account_restricted"
        assert status.pause_item_id is not None
        items = repo.list_durable_action_items(project_id=project_id, job_id=job_id)
        assert items[0].state == "failed"
        assert items[0].result_json is not None
        assert items[0].result_json["provider_error"] == "PEER_FLOOD"
        assert items[1].state == "pending"
        progress = repo.durable_action_progress(action_call_id=action_call_id)
        assert progress is not None
        assert progress["phase"] == "paused"
        assert progress["pause_reason"] == "provider_account_restricted"
        assert progress["pause_item_id"] == items[0].id
        assert repo.admit_delivery(
            project_id=project_id,
            credential_ref=credential_ref,
            destination_ref="destination:other-job",
            now=now + timedelta(seconds=2),
        ).admitted
        repo.resume_durable_action_job(
            project_id=project_id, job_id=job_id, now=now + timedelta(seconds=3)
        )
        resumed = repo.get_durable_action_job(project_id=project_id, job_id=job_id)
        assert resumed.state == "running"
        assert resumed.pause_reason is None
    engine.dispose()


def test_dispatcher_bounds_inflight_and_holds_interrupted_receipts_unknown(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(
        engine,
        project_id,
        credential_ref,
        action_call_id,
        now,
        destinations=("destination:1", "destination:2", "destination:3"),
        pacing_json={"account_interval_seconds": 0.05, "destination_interval_seconds": 1.0},
    )
    connector = _DispatchConnector(mode="blocked")

    async def exercise() -> None:
        connector.release = asyncio.Event()
        dispatcher = _dispatcher(engine, connector, max_inflight=2)
        assert await dispatcher.run_once(now=now, wait_for_receipts=False) == 1
        assert (
            await dispatcher.run_once(now=now + timedelta(seconds=0.05), wait_for_receipts=False)
            == 1
        )
        await asyncio.sleep(0)
        assert len(connector.requests) == 2
        assert (
            await dispatcher.run_once(now=now + timedelta(seconds=0.1), wait_for_receipts=False)
            == 0
        )
        await dispatcher.stop()

    asyncio.run(exercise())
    with Session(engine) as session:
        repo = ActionRepository(session)
        status = repo.get_durable_action_job(project_id=project_id, job_id=job_id)
        assert status.state == "unknown-hold"
        assert status.unknown_count == 2
        assert status.pending_count == 1
        items = repo.list_durable_action_items(project_id=project_id, job_id=job_id)
        assert [item.state for item in items] == ["unknown-hold", "unknown-hold", "pending"]
    engine.dispose()


def test_stopping_before_claimed_task_starts_reconciles_its_lease(
    tmp_path: Path, fake_credential_resolution: None
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(engine, project_id, credential_ref, action_call_id, now)
    connector = _DispatchConnector()

    async def exercise() -> None:
        dispatcher = _dispatcher(engine, connector)
        assert await dispatcher.run_once(now=now, wait_for_receipts=False) == 1
        await dispatcher.stop()

    asyncio.run(exercise())
    assert connector.requests == []
    with Session(engine) as session:
        status = ActionRepository(session).get_durable_action_job(
            project_id=project_id, job_id=job_id
        )
        assert status.state == "unknown-hold"
        assert status.unknown_count == 1
    engine.dispose()


def test_dispatcher_loop_failure_reconciles_active_lease_without_restart(
    tmp_path: Path,
    fake_credential_resolution: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, project_id, credential_ref, action_call_id = _seed_database(tmp_path)
    now = datetime(2026, 9, 22, 18, tzinfo=UTC).replace(tzinfo=None)
    job_id = _job(engine, project_id, credential_ref, action_call_id, now)
    with Session(engine) as session:
        leased = ActionRepository(session).claim_durable_action_items(
            project_id=project_id,
            job_id=job_id,
            now=now,
        )
        assert len(leased) == 1

    dispatcher = _dispatcher(engine, _DispatchConnector())

    async def fail_after_claim(**_kwargs: object) -> int:
        dispatcher._stopped.set()
        raise RuntimeError("loop failure")

    monkeypatch.setattr(dispatcher, "run_once", fail_after_claim)
    asyncio.run(dispatcher._run())
    with Session(engine) as session:
        status = ActionRepository(session).get_durable_action_job(
            project_id=project_id, job_id=job_id
        )
        assert status.state == "unknown-hold"
        assert status.unknown_count == 1
    engine.dispose()
