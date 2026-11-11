"""Legacy evidence recovery and custody lifetime in disposable projects only."""

import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from stackos.actions.openai_images import _register_generated_image_artifacts
from stackos.db.models import Artifact, Project
from stackos.mcp.context import MCPContext, bind_context
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.resources import ArtifactRepository
from tests.integration.test_project_cleanup import confirmation, put
from tests.integration.test_project_cleanup import fixture as fixture


def legacy_artifact(s, settings, target, monkeypatch):
    """Execute the real registrar through its pre0040 public insertion contract."""
    path = put(settings.generated_assets_dir / "openai-images" / "legacy.png")
    uri = "/generated-assets/openai-images/legacy.png"
    request = SimpleNamespace(
        session=s,
        asset_dir=settings.generated_assets_dir,
        project_id=target.id,
        operation="image.generate",
        action_ref="fixture",
    )

    def old_create(repo, *, asset_root, path, **fields):
        return repo.create(**fields)

    with monkeypatch.context() as old:
        old.setattr(ArtifactRepository, "create_generated_file", old_create)
        output = _register_generated_image_artifacts(request, {"data": [{"url": uri}]})
    return output["data"][0]["artifact_id"], path


@pytest.mark.asyncio
async def test_unaudited_legacy_reconciliation_requires_exact_admin_decision_then_retries(
    fixture, monkeypatch
):
    s, settings, target, _ = fixture
    artifact_id, path = legacy_artifact(s, settings, target, monkeypatch)
    service = ProjectCleanupService(s, settings)
    first = await service.delete(target.id, confirmation=confirmation(target))
    assert first.status == "incomplete"
    assert first.ownership_required_artifact_ids == [artifact_id]
    assert path.exists() and s.get(Artifact, artifact_id).managed_file_json is None
    arguments = dict(
        project_id=target.id,
        project_slug=target.slug,
        artifact_id=artifact_id,
        expected_uri="/generated-assets/openai-images/legacy.png",
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        confirm_ownership=True,
        asset_root=settings.generated_assets_dir,
    )
    repo = ArtifactRepository(s)
    with pytest.raises(ConflictError):
        repo.reconcile_generated_file(**arguments)
    for context in (
        {"surface": "mcp", "trusted_local_admin": True, "auth_scope": "daemon-admin"},
        {"surface": "rest", "trusted_local_admin": False, "auth_scope": "daemon-admin"},
        {"surface": "rest", "trusted_local_admin": True, "auth_scope": "console"},
    ):
        with (
            bind_context(
                MCPContext(
                    session=s, request_id="fixture-denied", project_id=target.id, extras=context
                )
            ),
            pytest.raises(ConflictError),
        ):
            repo.reconcile_generated_file(**arguments)
    ctx = MCPContext(
        session=s,
        request_id="fixture-reviewed-ownership",
        project_id=target.id,
        extras={"surface": "rest", "trusted_local_admin": True, "auth_scope": "daemon-admin"},
    )
    with bind_context(ctx):
        for patch in (
            {"project_slug": "wrong"},
            {"expected_uri": "wrong"},
            {"confirm_ownership": False},
            {"expected_sha256": "0" * 64},
        ):
            with pytest.raises((ConflictError, ValidationError)):
                repo.reconcile_generated_file(**{**arguments, **patch})
        repo.reconcile_generated_file(**arguments)
    assert (
        s.get(Artifact, artifact_id).managed_file_json["evidence"]["basis"]
        == "local-admin-reconciliation"
    )
    with Session(s.get_bind()) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    assert retry.status == "deleted" and retry.operation_id == first.operation_id
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["owner-first", "reference-first"])
@pytest.mark.parametrize("directory", [False, True])
async def test_shared_custody_survives_both_deletion_orders_and_multiple_files(
    fixture, order, directory
):
    s, settings, target, other = fixture
    paths = [
        put(settings.generated_assets_dir / "openai-images" / name)
        for name in ("one.png", "two.png")
    ]
    repo = ArtifactRepository(s)
    for path in paths:
        repo.create_generated_file(
            project_id=target.id,
            kind="image",
            path=path,
            asset_root=settings.generated_assets_dir,
            uri="/generated-assets/openai-images/" + path.name,
        )
    refs = [
        repo.create(project_id=other.id, kind="image", uri=uri).data.id
        for uri in (
            ["/generated-assets/openai-images"]
            if directory
            else ["/generated-assets/openai-images/" + path.name for path in paths]
        )
    ]
    first, last = (target, other) if order == "owner-first" else (other, target)
    result = await ProjectCleanupService(s, settings).delete(
        first.id, confirmation=confirmation(first)
    )
    assert result.status == "deleted" and all(p.exists() for p in paths)
    assert s.get(Project, first.id) is None
    if order == "owner-first":
        assert all(s.get(Artifact, ref).managed_file_json for ref in refs)
    with Session(s.get_bind()) as restarted:
        result = await ProjectCleanupService(restarted, settings).delete(
            last.id, confirmation=confirmation(last)
        )
        assert result.status == "deleted"
        assert restarted.exec(select(Artifact)).all() == []
        assert restarted.exec(select(Project)).all() == []
    assert not any(p.exists() for p in paths)


@pytest.mark.asyncio
async def test_custody_handoff_rolls_back_with_failed_project_purge(fixture):
    s, settings, target, other = fixture
    path = put(settings.generated_assets_dir / "openai-images" / "shared.png")
    repo = ArtifactRepository(s)
    owner = repo.create_generated_file(
        project_id=target.id,
        kind="image",
        path=path,
        asset_root=settings.generated_assets_dir,
        uri="/generated-assets/openai-images/shared.png",
    ).data
    survivor = repo.create(project_id=other.id, kind="image", uri=owner.uri).data

    def fail_purge(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.startswith("DELETE FROM projects"):
            raise RuntimeError("fixture purge failure after custody handoff")

    engine = s.get_bind()
    event.listen(engine, "before_cursor_execute", fail_purge)
    try:
        result = await ProjectCleanupService(s, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    finally:
        event.remove(engine, "before_cursor_execute", fail_purge)
    assert result.status == "incomplete" and path.exists()
    assert s.get(Artifact, survivor.id).managed_file_json is None
    assert s.get(Artifact, owner.id).managed_file_json is not None
    with Session(engine) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
        assert retry.status == "deleted" and retry.operation_id == result.operation_id
        final = await ProjectCleanupService(restarted, settings).delete(
            other.id, confirmation=confirmation(other)
        )
        assert final.status == "deleted"
    assert not path.exists()
