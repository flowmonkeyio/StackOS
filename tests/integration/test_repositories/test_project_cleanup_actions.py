"""Upgrade cleanup through real action receipts in disposable databases only."""

import base64
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import event, text
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.migrate import alembic_config
from stackos.db.models import ActionCall, Artifact, Project
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.projects import IntegrationBudgetRepository, ProjectRepository
from stackos.repositories.resources import ArtifactRepository
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot
from tests.integration.account_test_support import seed_test_account
from tests.integration.test_project_cleanup import confirmation, put
from tests.integration.test_repositories.test_actions import _provider_credential_ref


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "tamper"), [("inline", False), ("always_file", False), ("always_file", True)]
)
@pytest.mark.parametrize("upgrade", [False, True])
async def test_existing_producer_files_recover_from_action_audit_after_upgrade_and_restart(
    cleanup_db, httpx_mock, monkeypatch, mode, tamper, upgrade
):
    session, settings, target = cleanup_db
    project_id = target.id
    seed_test_account(
        session, project_id=project_id, provider_key="xai-imagine", secret_payload=b"fixture-key"
    )
    IntegrationBudgetRepository(session).set(
        project_id=project_id, kind="xai-imagine", monthly_budget_usd=10
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.x.ai/v1/images/generations",
        json={"data": [{"b64_json": base64.b64encode(b"legacy-media").decode()}]},
    )

    def old_create(repo, *, asset_root, path, **fields):
        return repo.create(**fields)

    with monkeypatch.context() as legacy:
        legacy.setattr(ArtifactRepository, "create_generated_file", old_create)
        action = (
            await ActionRepository(session, asset_dir=settings.generated_assets_dir).execute(
                project_id=project_id,
                action_ref="utils.xai.image.generate",
                input_json={"prompt": "fixture", "aspect_ratio": "1:1", "resolution": "1k"},
                credential_ref=_provider_credential_ref(session, project_id, "xai-imagine"),
                output_policy_json={"mode": mode},
            )
        ).data
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

    if tamper:
        receipt = Path(action.output_json["file"]["path"])
        original = receipt.read_bytes()
        receipt.write_bytes(original + b" ")
        rejected = await ProjectCleanupService(session, settings).delete(
            project_id, confirmation=confirmation(target)
        )
        assert rejected.status == "incomplete"
        assert rejected.ownership_required_artifact_ids == [artifact.id]
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
