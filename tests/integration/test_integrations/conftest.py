"""Shared fixtures for the M4 integration-wrapper tests.

Each test gets:

- A deterministic ``seed.bin`` configured for the AES-GCM round trip.
- A fresh in-memory SQLite DB with the schema applied (so wrappers that
  hit ``IntegrationBudgetRepository`` / ``RunStepCallRepository`` see
  rows they expect).
- Reset token-bucket registry so QPS math is deterministic across tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from stackos.crypto.aes_gcm import configure_seed_path
from stackos.crypto.seed import ensure_seed_file
from stackos.db.connection import make_memory_engine
from stackos.integrations._rate_limit import reset_buckets


@pytest.fixture(scope="session", autouse=True)
def _crypto_seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Configure a per-session seed file; matches the repository conftest."""
    seed_dir = tmp_path_factory.mktemp("integrations-crypto-seed")
    seed_path = seed_dir / "seed.bin"
    ensure_seed_file(seed_path)
    configure_seed_path(seed_path)
    yield seed_path


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets() -> Iterator[None]:
    """Drop every token bucket before each test (state is process-level)."""
    reset_buckets()
    yield
    reset_buckets()


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace ``asyncio.sleep`` inside the integration retry loop with a no-op.

    The base class' exponential backoff is 0.5s → 1s → 2s → 4s; with
    real sleeps a 4-retry test runs ~7.5s which blows the 35s budget
    fast. We're not testing wall-clock semantics — tests assert that
    the loop *runs the right number of HTTP calls* — so the sleeps are
    safe to no-op.
    """
    import asyncio as asyncio_module

    async def _zero_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio_module, "sleep", _zero_sleep)
    yield


@pytest.fixture
def session() -> Iterator[Session]:
    """Yield a fresh SQLModel ``Session`` bound to an in-memory engine."""
    engine = make_memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def project_id(session: Session) -> int:
    """Create a project so dependent rows have a valid FK."""
    from stackos.repositories.projects import ProjectRepository

    repo = ProjectRepository(session)
    env = repo.create(slug="t-int", name="T", domain="x", locale="en")
    pid = env.data.id
    assert pid is not None
    return pid


@pytest.fixture
def host_media_projection(monkeypatch, project_id, tmp_path):
    """Exercise the real host defaults and file projection at the native boundary."""
    import json

    from stackos_connectors import ConnectorClient, ConnectorFile, ConnectorResult
    from stackos_connectors.catalog import load_registry

    from stackos.actions.connectors import ActionConnectorRequest
    from stackos.auth_providers.repository.schema import ResolvedCredential
    from stackos.db.models import Credential, IntegrationCredential

    async def run(connector, *, provider, operation, data, output_subdir, expected_context=None):
        captured = []

        async def execute(_client, key, action_key, native_data, auth, options):
            captured.append((key, action_key, native_data, auth, options))
            if expected_context is not None:
                assert options.provider_context == expected_context
            path = options.output_dir / "fixture.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-image")
            return ConnectorResult(
                output_json={"data": [{"path": str(path), "file_format": "png"}]},
                files=[ConnectorFile(path=str(path), mime_type="image/png")],
            )

        monkeypatch.setattr(ConnectorClient, "execute", execute)
        registry = load_registry(f"connectors/{provider.replace('-', '_')}/catalog.json")
        method = registry.connector_metadata[provider]["auth_methods"][0]
        definition = next(item for item in registry.actions.values() if item.operation == operation)
        credential = ResolvedCredential(
            credential=Credential(provider_key=provider, auth_method_key=method["key"]),
            integration=IntegrationCredential(encrypted_payload=b"unused", nonce=b"0" * 12),
            secret_payload=(
                json.dumps(
                    {key: "synthetic-key" for key in method["fields_schema"]["properties"]}
                ).encode()
                if method.get("payload_format") == "json"
                else b"synthetic-key"
            ),
            config_json={"auth_method_key": method["key"]},
        )
        result = await connector.execute(
            ActionConnectorRequest(
                project_id=project_id,
                plugin_slug="utils",
                action_key=definition.key,
                action_ref=f"utils.{definition.key}",
                provider_key=provider,
                operation=operation,
                input_json=data,
                config_json={},
                credential=credential,
                asset_dir=tmp_path,
            )
        )
        assert len(captured) == 1
        assert result.output_json["data"][0]["url"] == (
            f"/generated-assets/{output_subdir}/fixture.png"
        )
        assert (tmp_path / output_subdir / "fixture.png").read_bytes() == b"synthetic-image"
        assert captured[0][4].output_dir == tmp_path / output_subdir
        return result, captured[0][2]

    return run


@pytest.fixture
def host_audit(session, project_id, monkeypatch, tmp_path):
    """Observe persisted ActionCall rows from the canonical host executor."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from stackos_connectors.catalog import load_registry

    from stackos.actions import ActionRepository
    from stackos.actions.repository.execution import _PreparedActionExecution
    from stackos.actions.repository.validation import RuntimeActionContext
    from stackos.auth_providers.repository.schema import ResolvedCredential
    from stackos.db.models import Credential, IntegrationCredential

    repository = ActionRepository(session, asset_dir=tmp_path)
    record_call = Mock()
    original_record = repository._record_call

    def record(**kwargs):
        row = original_record(**kwargs)
        # Observe database-backed safe columns, not an invented native audit hook.
        record_call(
            request_json=row.request_json,
            response_json=row.response_json,
            metadata_json=row.metadata_json,
            error=row.error,
            status=row.status.value,
            cost_cents=row.cost_cents,
            action_call_id=row.id,
        )
        return row

    monkeypatch.setattr(repository, "_record_call", record)

    async def execute(
        provider,
        action_key,
        data,
        *,
        secret_payload,
        config=None,
        asset_dir=None,
        estimated_cost_cents=0,
        budget_kind=None,
        connector_override=None,
    ):
        registry = load_registry(f"connectors/{provider.replace('-', '_')}/catalog.json")
        method = registry.connector_metadata[provider]["auth_methods"][0]["key"]
        credential = ResolvedCredential(
            credential=Credential(provider_key=provider, auth_method_key=method),
            integration=IntegrationCredential(encrypted_payload=b"unused", nonce=b"0" * 12),
            secret_payload=secret_payload,
            config_json={"auth_method_key": method, **(config or {})},
        )
        monkeypatch.setattr(repository, "_resolve_credential", AsyncMock(return_value=credential))
        repository._asset_dir = asset_dir or tmp_path
        plugin = "utils" if provider == "aignc" else "seo"
        manifest = repository._manifest(
            action_ref=f"{plugin}.{action_key}",
            plugin_slug=None,
            action_key=None,
            project_id=project_id,
        )
        if connector_override is not None:
            from stackos.actions.connectors import ActionConnectorRegistry

            connectors = ActionConnectorRegistry()
            connectors.register(connector_override)
            repository._connectors = connectors
        if budget_kind:
            manifest = manifest.model_copy(
                update={"enforce_budget": True, "budget_kind": budget_kind}
            )
        prepared = _PreparedActionExecution(
            project_id=project_id,
            manifest=manifest,
            payload=data,
            provider_context={},
            provider_context_for_audit=None,
            credential_ref=None,
            runtime_context=RuntimeActionContext(credential_ref=None, provider_context_json={}),
            effective_output_policy={"mode": "inline", "max_inline_bytes": 16000},
            metadata_json=None,
            estimated_cost_cents=estimated_cost_cents,
            run_id=None,
            run_plan_id=None,
            run_plan_step_id=None,
            idempotency_key=None,
        )
        row, _output_json = await repository._execute_prepared(prepared)
        return row

    return SimpleNamespace(execute=execute, record_call=record_call, repository=repository)


@pytest.fixture
def host_probe_preflight(session):
    from stackos.auth_providers import AuthRepository
    from stackos.auth_providers.repository.schema import PermissionVerificationOut

    def check(provider_key, method_key, *, evidence_source, enforcement):
        repository = AuthRepository(session)
        provider = repository._get_provider(provider_key)
        method = repository._get_auth_method(provider, method_key).model_copy(
            update={
                "permission_verification": PermissionVerificationOut(
                    evidence_source=evidence_source,
                    enforcement=enforcement,
                ),
            }
        )
        return repository._account_probe_preflight(provider=provider, method=method)

    return check


@pytest.fixture
def host_account_probe(session, project_id):
    from sqlmodel import select

    from stackos.auth_providers import AuthRepository
    from stackos.db.models import CredentialUsageEvent

    async def probe(provider_key, method_key, fields):
        repository = AuthRepository(session)
        credential = repository.store_credential(
            provider_key=provider_key,
            auth_method_key=method_key,
            display_name="Synthetic probe",
            fields=fields,
            attach_project_id=project_id,
        ).data
        result = (
            await repository.test(
                project_id=project_id,
                credential_ref=credential.credential_ref,
            )
        ).data
        events = session.exec(
            select(CredentialUsageEvent).where(
                CredentialUsageEvent.operation == "account.test",
            )
        ).all()
        assert len(events) == 1
        return result, events[0].metadata_json

    return probe
