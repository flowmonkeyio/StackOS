"""Upgrade cleanup through real action receipts in disposable databases only."""

import asyncio
import base64
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import event, text
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.actions.connectors import ActionConnectorRegistry
from stackos.actions.telegram import TelegramActionConnector
from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.migrate import alembic_config
from stackos.db.models import ActionCall, ActionCallStatus, Artifact, Project
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.projects import IntegrationBudgetRepository, ProjectRepository
from stackos.repositories.resources import ArtifactRepository
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot
from tests.integration.account_test_support import seed_test_account
from tests.integration.test_project_cleanup import confirmation, put
from tests.integration.test_repositories.test_actions import _provider_credential_ref
from tests.integration.test_repositories.test_hubspot_actions import _hubspot_credential, _safe_ref
from tests.integration.test_repositories.test_telegram_actions import FakeTelegram, _account


@pytest.fixture
def cleanup_db(session, tmp_path):
    settings = Settings(data_dir=tmp_path / "cleanup-data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    with Session(engine) as isolated:
        target = (
            ProjectRepository(isolated)
            .create(slug="disposable-legacy", name="Disposable legacy", domain="fixture.test")
            .data
        )
        yield isolated, settings, target
    engine.dispose()


async def produce_file(session, settings, project_id, httpx_mock, provider, mode):
    """Use real dispatch, producer registration and audit; replace provider transport only."""
    connectors = None
    if provider == "telegram":
        credential = _account(session, project_id)
        files = settings.state_dir / "private-telegram-account-files"
        put(files / "test.txt", "downloaded content")
        connectors = ActionConnectorRegistry()
        connectors.register(TelegramActionConnector(FakeTelegram(files)))
        action_ref = "communications.telegram.file.download"
        payload = {"profile_ref": "ops", "file_ref": f"telegram-file:{credential}:42"}
    elif provider == "hubspot":
        credential = _hubspot_credential(session, project_id=project_id, scopes={"crm.export"})
        job_ref = _safe_ref(
            session,
            credential_ref=credential,
            object_type="bulk-export-job",
            provider_object_id="9004",
            metadata_json={
                "object_type": "contacts",
                "property_refs": [],
                "provider_property_names": [],
                "associated_object_types": [],
                "format": "CSV",
                "export_name": "Fixture",
            },
        )
        url = "https://exports.hubspotusercontent.example/fixture.csv"
        httpx_mock.add_response(
            method="GET",
            url="https://api.hubapi.com/crm/exports/2026-03/export/async/tasks/9004/status",
            json={"status": "COMPLETE", "result": url},
        )
        httpx_mock.add_response(method="GET", url=url, content=b"email\nfixture@example.test\n")
        action_ref = "gtm.hubspot.bulk.exports.result"
        payload = {"job_ref": job_ref, "max_bytes": 10_000_000}
    else:
        seed_test_account(
            session,
            project_id=project_id,
            provider_key="xai-imagine",
            secret_payload=b"fixture-key",
        )
        IntegrationBudgetRepository(session).set(
            project_id=project_id, kind="xai-imagine", monthly_budget_usd=10
        )
        httpx_mock.add_response(
            method="POST",
            url="https://api.x.ai/v1/images/generations",
            json={"data": [{"b64_json": base64.b64encode(b"legacy-media").decode()}]},
        )
        credential = _provider_credential_ref(session, project_id, "xai-imagine")
        action_ref = "utils.xai.image.generate"
        payload = {"prompt": "fixture", "aspect_ratio": "1:1", "resolution": "1k"}
    repository = ActionRepository(
        session, asset_dir=settings.generated_assets_dir, connectors=connectors
    )
    action = (
        await repository.execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json=payload,
            credential_ref=credential,
            output_policy_json={"mode": mode},
        )
    ).data
    if action.action_call.status == ActionCallStatus.RUNNING:
        task = next(
            task
            for task in asyncio.all_tasks()
            if task.get_name() == f"stackos-action-{action.action_call.id}"
        )
        await asyncio.wait_for(task, timeout=5)
        session.expire_all()
        action.action_call = repository.get_call(
            project_id=project_id, action_call_id=action.action_call.id
        )
        action.output_json = session.get(ActionCall, action.action_call.id).response_json
    assert action.action_call.status == ActionCallStatus.SUCCESS
    return action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "tamper"),
    [("inline", False), ("always_file", False), ("always_file", True), ("inline", "uri")],
)
@pytest.mark.parametrize("upgrade", [False, True])
@pytest.mark.parametrize("provider", ["media", "telegram", "hubspot"])
async def test_existing_producer_files_recover_from_action_audit_after_upgrade_and_restart(
    cleanup_db, httpx_mock, monkeypatch, mode, tamper, upgrade, provider
):
    session, settings, target = cleanup_db
    project_id = target.id

    def old_create(repo, *, asset_root, path, **fields):
        return repo.create(**fields)

    with monkeypatch.context() as legacy:
        legacy.setattr(ArtifactRepository, "create_generated_file", old_create)
        action = await produce_file(session, settings, project_id, httpx_mock, provider, mode)
    artifact = session.exec(select(Artifact).where(Artifact.project_id == project_id)).one()
    assert artifact.managed_file_json is None
    path = settings.generated_assets_dir / artifact.uri.removeprefix("/generated-assets/")
    engine = session.get_bind()
    if upgrade:
        # Reproduce the populated pre0040 schema, then execute the real upgrade.
        session.rollback()
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE artifacts DROP COLUMN managed_file_json"))
        cfg = alembic_config(settings)
        command.stamp(cfg, "0039_project_cleanup")
        command.upgrade(cfg, "head")
        session.expire_all()
        assert session.get(Artifact, artifact.id).managed_file_json is None

    if tamper == "uri":
        original_uri = artifact.uri
        sentinel = put(settings.generated_assets_dir / "xai-imagine" / "unrelated.png")
        artifact.uri = "/generated-assets/xai-imagine/unrelated.png"
        session.add(artifact)
        session.commit()
        rejected = await ProjectCleanupService(session, settings).delete(
            project_id, confirmation=confirmation(target)
        )
        assert rejected.status == "incomplete"
        assert artifact.managed_file_json is None and path.exists() and sentinel.exists()
        artifact.uri = original_uri
        session.add(artifact)
        session.commit()
    elif tamper:
        receipt = Path(action.output_json["file"]["path"])
        original = receipt.read_bytes()
        receipt.write_bytes(original + b" ")

        def stop_before_derived_root_cleanup(_project_id):
            # Telegram's derived project directory has independent custody. Observe
            # failed receipt recovery before that generic directory cleanup runs.
            assert artifact.managed_file_json is None
            raise OSError("fixture stop before file cleanup")

        with monkeypatch.context() as stopped:
            stopped.setattr(
                ProjectCleanupService,
                "_clean_files",
                lambda self, pid: stop_before_derived_root_cleanup(pid),
            )
            rejected = await ProjectCleanupService(session, settings).delete(
                project_id, confirmation=confirmation(target)
            )
        assert rejected.status == "incomplete"
        assert artifact.managed_file_json is None and path.exists()
        receipt.write_bytes(original)

    def fail_before_files(_project_id):
        raise OSError("fixture crash after durable evidence recovery")

    service = ProjectCleanupService(session, settings)
    monkeypatch.setattr(service, "_clean_files", fail_before_files)
    first = await service.delete(project_id, confirmation=confirmation(target))
    assert first.status == "incomplete" and path.exists()
    if tamper:
        assert first.operation_id == rejected.operation_id
    assert session.get(Artifact, artifact.id).managed_file_json["evidence"] == {
        "basis": "action-audit",
        "action_call_id": action.action_call.id,
    }
    with Session(engine) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            project_id, confirmation=confirmation(target)
        )
        assert retry.status == "deleted" and retry.operation_id == first.operation_id
        assert restarted.get(Project, project_id) is None
        assert (
            restarted.exec(select(ActionCall).where(ActionCall.project_id == project_id)).all()
            == []
        )
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["media", "telegram", "hubspot"])
@pytest.mark.parametrize(
    "invalid",
    [
        "failed",
        "dry",
        "foreign-project",
        "artifact-id",
        "created-after",
        "unfinished",
        "unknown-operation",
        "unknown-producer",
        "symlink",
        "hardlink",
    ],
)
async def test_actual_legacy_producer_rejects_untrusted_custody(
    cleanup_db, httpx_mock, monkeypatch, provider, invalid
):
    session, settings, target = cleanup_db
    with monkeypatch.context() as legacy:
        legacy.setattr(
            ArtifactRepository,
            "create_generated_file",
            lambda repo, *, asset_root, path, **fields: repo.create(**fields),
        )
        action = await produce_file(session, settings, target.id, httpx_mock, provider, "inline")
    artifact = session.exec(select(Artifact).where(Artifact.project_id == target.id)).one()
    path = settings.generated_assets_dir / artifact.uri.removeprefix("/generated-assets/")
    call = session.get(ActionCall, action.action_call.id)
    other = (
        ProjectRepository(session)
        .create(slug="foreign-owner", name="Foreign owner", domain="fixture.test")
        .data
    )
    sentinel = put(settings.state_dir / "repository" / "keep.txt")
    if invalid == "failed":
        call.status = ActionCallStatus.FAILED
    elif invalid == "dry":
        call.dry_run = True
    elif invalid == "foreign-project":
        call.project_id = other.id
    elif invalid == "artifact-id":
        foreign = (
            ArtifactRepository(session)
            .create(project_id=other.id, kind="file", uri=artifact.uri)
            .data
        )
        output = json.loads(json.dumps(call.response_json))
        item = output["data"][0] if provider == "media" else output
        item["artifact_id"] = foreign.id
        call.response_json = output
    elif invalid == "created-after":
        artifact.created_at = call.completed_at + timedelta(seconds=1)
        session.add(artifact)
    elif invalid == "unfinished":
        call.completed_at = None
    elif invalid == "unknown-operation":
        call.operation = "unrecognized.fixture"
    elif invalid == "unknown-producer":
        call.connector_key = "unrecognized.fixture"
    elif invalid == "symlink":
        path.unlink()
        path.symlink_to(sentinel)
    elif invalid == "hardlink":
        path.unlink()
        path.hardlink_to(sentinel)
    session.add(call)
    session.commit()

    def stop_before_files(self, project_id):
        assert session.get(Artifact, artifact.id).managed_file_json is None
        raise OSError("fixture observes rejected custody before derived root removal")

    monkeypatch.setattr(ProjectCleanupService, "_clean_files", stop_before_files)
    result = await ProjectCleanupService(session, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "incomplete" and result.retryable
    assert session.get(Artifact, artifact.id).managed_file_json is None
    assert path.exists() and sentinel.read_text() == "sentinel"
    assert session.get(Project, other.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("project", "project_id", 999999),
        ("action_call", "id", 999999),
        ("action", "connector_key", "unknown"),
        ("action", "operation", "unknown"),
        ("action", "plugin_slug", "unknown"),
        ("action", "action_key", "unknown"),
        ("action_call", "status", "failed"),
        ("response", "dry_run", True),
    ],
)
async def test_file_receipt_identity_must_match_even_with_valid_digest(
    cleanup_db, httpx_mock, monkeypatch, section, field, value
):
    session, settings, target = cleanup_db
    with monkeypatch.context() as legacy:
        legacy.setattr(
            ArtifactRepository,
            "create_generated_file",
            lambda repo, *, asset_root, path, **fields: repo.create(**fields),
        )
        action = await produce_file(
            session, settings, target.id, httpx_mock, "media", "always_file"
        )
    artifact = session.exec(select(Artifact).where(Artifact.project_id == target.id)).one()
    call = session.get(ActionCall, action.action_call.id)
    receipt = Path(action.output_json["file"]["path"])
    original = receipt.read_bytes()
    envelope = json.loads(original)
    envelope[section][field] = value
    changed = json.dumps(envelope).encode()
    receipt.write_bytes(changed)
    response = json.loads(json.dumps(call.response_json))
    response["file"].update(bytes=len(changed), sha256=hashlib.sha256(changed).hexdigest())
    call.response_json = response
    session.add(call)
    session.commit()
    result = await ProjectCleanupService(session, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "incomplete" and result.ownership_required_artifact_ids == [artifact.id]
    assert artifact.managed_file_json is None and receipt.exists()
    receipt.write_bytes(original)
    call.response_json = action.output_json
    session.add(call)
    session.commit()
    with Session(session.get_bind()) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    assert retry.status == "deleted" and retry.operation_id == result.operation_id


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["media", "telegram", "hubspot"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("owner_first", [False, True])
@pytest.mark.parametrize("directory", [False, True])
async def test_actual_producer_shared_file_survives_partial_cleanup_and_final_reference(
    cleanup_db, httpx_mock, monkeypatch, provider, legacy, owner_first, directory
):
    session, settings, target = cleanup_db
    with monkeypatch.context() as old:
        if legacy:
            old.setattr(
                ArtifactRepository,
                "create_generated_file",
                lambda repo, *, asset_root, path, **fields: repo.create(**fields),
            )
        await produce_file(session, settings, target.id, httpx_mock, provider, "always_file")
    artifact = session.exec(select(Artifact).where(Artifact.project_id == target.id)).one()
    path = settings.generated_assets_dir / artifact.uri.removeprefix("/generated-assets/")
    other = (
        ProjectRepository(session)
        .create(slug="shared-survivor", name="Shared survivor", domain="fixture.test")
        .data
    )
    ArtifactRepository(session).create(
        project_id=other.id,
        kind="file",
        uri=artifact.uri.rsplit("/", 1)[0] if directory else artifact.uri,
    )
    foreign = put(settings.generated_assets_dir / "action-outputs" / "project-999999" / "keep")
    repository_file = put(settings.state_dir / "repository" / "keep")
    # Fail after real filesystem work, while retaining source rows and recovered custody.
    first, last = (target, other) if owner_first else (other, target)
    service = ProjectCleanupService(session, settings)
    clean = service._clean_files

    def partial(project_id):
        clean(project_id)
        raise OSError("fixture crash after actual partial filesystem cleanup")

    monkeypatch.setattr(service, "_clean_files", partial)
    failed = await service.delete(first.id, confirmation=confirmation(first))
    assert failed.status == "incomplete" and path.exists()
    assert session.get(Artifact, artifact.id) is not None
    with Session(session.get_bind()) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            first.id, confirmation=confirmation(first)
        )
        assert retry.status == "deleted" and retry.operation_id == failed.operation_id
        assert path.exists()
        final = await ProjectCleanupService(restarted, settings).delete(
            last.id, confirmation=confirmation(last)
        )
        assert final.status == "deleted" and not path.exists()
    assert foreign.read_text() == repository_file.read_text() == "sentinel"
    if provider == "telegram":
        assert (settings.state_dir / "private-telegram-account-files" / "test.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("owner_first", [False, True])
@pytest.mark.parametrize("purge_failure", [False, True])
async def test_real_action_response_shared_custody_reaches_final_reference(
    cleanup_db, httpx_mock, directory, owner_first, purge_failure
):
    session, settings, target = cleanup_db
    other = (
        ProjectRepository(session)
        .create(
            slug="disposable-response-survivor", name="Disposable survivor", domain="fixture.test"
        )
        .data
    )
    seed_test_account(
        session, project_id=target.id, provider_key="xai-imagine", secret_payload=b"fixture-key"
    )
    IntegrationBudgetRepository(session).set(
        project_id=target.id, kind="xai-imagine", monthly_budget_usd=10
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/images/generations",
        json={"data": [{"b64_json": base64.b64encode(b"fixture-media").decode()}]},
    )
    action = (
        await ActionRepository(session, asset_dir=settings.generated_assets_dir).execute(
            project_id=target.id,
            action_ref="utils.xai.image.generate",
            input_json={"prompt": "fixture", "aspect_ratio": "1:1", "resolution": "1k"},
            credential_ref=_provider_credential_ref(session, target.id, "xai-imagine"),
            output_policy_json={"mode": "always_file"},
        )
    ).data
    response = Path(action.output_json["file"]["path"])
    owned_root = settings.generated_assets_dir / "action-outputs" / f"project-{target.id}"
    assert response.is_relative_to(owned_root)
    ref_path = owned_root if directory else response
    survivor = (
        ArtifactRepository(session)
        .create(
            project_id=other.id,
            kind="response",
            uri="/generated-assets/"
            + ref_path.relative_to(settings.generated_assets_dir).as_posix(),
        )
        .data
    )
    foreign = put(settings.generated_assets_dir / "action-outputs" / "project-99999" / "keep")
    first, last = (target, other) if owner_first else (other, target)
    engine = session.get_bind()

    def fail_purge(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.startswith("DELETE FROM projects"):
            raise RuntimeError("fixture failure after shared-root handoff")

    if purge_failure:
        event.listen(engine, "before_cursor_execute", fail_purge)
        try:
            failed = await ProjectCleanupService(session, settings).delete(
                first.id, confirmation=confirmation(first)
            )
        finally:
            event.remove(engine, "before_cursor_execute", fail_purge)
        assert failed.status == "incomplete" and response.exists()
        assert session.get(Artifact, survivor.id).managed_file_json is None
        assert session.get(ActionCall, action.action_call.id) is not None
    with Session(engine) as restarted:
        result = await ProjectCleanupService(restarted, settings).delete(
            first.id, confirmation=confirmation(first)
        )
        assert result.status == "deleted" and response.exists()
        if purge_failure:
            assert result.operation_id == failed.operation_id
        if owner_first:
            assert restarted.get(ActionCall, action.action_call.id) is None
            assert restarted.get(Artifact, survivor.id).managed_file_json is not None
    with Session(engine) as restarted:
        result = await ProjectCleanupService(restarted, settings).delete(
            last.id, confirmation=confirmation(last)
        )
        assert result.status == "deleted"
        assert restarted.exec(select(Project)).all() == []
        assert restarted.exec(select(Artifact)).all() == []
        assert restarted.exec(select(ActionCall)).all() == []
    assert not response.exists() and not owned_root.exists()
    assert foreign.read_text() == "sentinel"
