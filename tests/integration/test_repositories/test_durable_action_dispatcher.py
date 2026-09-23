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
    Credential,
    IntegrationCredential,
    Plugin,
    PluginSource,
    ProjectCredential,
    Provider,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ArtifactRepository


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
    clock = iter([scan_at, scan_at, transition_at])

    def clock_now(value: datetime | None) -> datetime:
        return value if value is not None else next(clock)

    monkeypatch.setattr(durable_dispatcher_module, "_as_naive_utc", clock_now)
    assert asyncio.run(_dispatcher(engine, _DispatchConnector(mode="flood")).run_once()) == 1

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
