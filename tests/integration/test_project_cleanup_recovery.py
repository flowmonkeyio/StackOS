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
from stackos.repositories.projects import ProjectRepository
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
        missing = {key: value for key, value in arguments.items() if key != "confirm_ownership"}
        with pytest.raises(TypeError):
            repo.reconcile_generated_file(**missing)
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
async def test_changed_legacy_file_requires_fresh_content_confirmation_after_restart(
    fixture, monkeypatch
):
    s, settings, target, _ = fixture
    artifact_id, path = legacy_artifact(s, settings, target, monkeypatch)
    first = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert first.status == "incomplete"
    reviewed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text("changed after the ownership decision was prepared")
    with Session(s.get_bind()) as restarted:
        repo = ArtifactRepository(restarted)
        ctx = MCPContext(
            session=restarted,
            request_id="fixture-revised-ownership",
            project_id=target.id,
            extras={"surface": "rest", "trusted_local_admin": True, "auth_scope": "daemon-admin"},
        )
        args = dict(
            project_id=target.id,
            project_slug=target.slug,
            artifact_id=artifact_id,
            expected_uri="/generated-assets/openai-images/legacy.png",
            expected_sha256=reviewed_hash,
            confirm_ownership=True,
            asset_root=settings.generated_assets_dir,
        )
        with bind_context(ctx), pytest.raises(ConflictError, match="content hash"):
            repo.reconcile_generated_file(**args)
        assert restarted.get(Artifact, artifact_id).managed_file_json is None
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
        assert retry.status == "incomplete" and retry.operation_id == first.operation_id
        assert path.read_text() == "changed after the ownership decision was prepared"
        with bind_context(ctx):
            repo.reconcile_generated_file(
                **{**args, "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
    with Session(s.get_bind()) as restarted:
        result = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
        assert result.status == "deleted" and result.operation_id == first.operation_id
    assert not path.exists()


@pytest.mark.asyncio
async def test_reconciliation_cannot_claim_another_projects_partition(fixture):
    s, settings, target, other = fixture
    path = put(
        settings.generated_assets_dir / "action-outputs" / f"project-{other.id}" / "foreign.json"
    )
    uri = "/generated-assets/" + path.relative_to(settings.generated_assets_dir).as_posix()
    artifact = (
        ArtifactRepository(s)
        .create(
            project_id=target.id,
            kind="image",
            uri=uri,
            provenance_json={"source": "openai-images-action"},
        )
        .data
    )
    result = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "incomplete"
    ctx = MCPContext(
        session=s,
        request_id="fixture-foreign-partition",
        project_id=target.id,
        extras={"surface": "rest", "trusted_local_admin": True, "auth_scope": "daemon-admin"},
    )
    with bind_context(ctx), pytest.raises(ValidationError, match="another project root"):
        ArtifactRepository(s).reconcile_generated_file(
            project_id=target.id,
            project_slug=target.slug,
            artifact_id=artifact.id,
            expected_uri=uri,
            expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            confirm_ownership=True,
            asset_root=settings.generated_assets_dir,
        )
    assert path.read_text() == "sentinel"
    assert s.get(Artifact, artifact.id).managed_file_json is None
    assert s.get(Project, other.id).cleanup_json is None


@pytest.mark.asyncio
async def test_reconciled_shared_file_survives_until_last_project_cleanup(fixture, monkeypatch):
    s, settings, target, other = fixture
    artifact_id, path = legacy_artifact(s, settings, target, monkeypatch)
    repo = ArtifactRepository(s)
    survivor = repo.create(
        project_id=other.id, kind="image", uri="/generated-assets/openai-images/legacy.png"
    ).data
    foreign = put(settings.data_dir / "projects" / str(other.id) / "foreign.txt")
    first = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert first.status == "incomplete" and path.exists() and foreign.exists()
    ctx = MCPContext(
        session=s,
        request_id="fixture-shared-ownership",
        project_id=target.id,
        extras={"surface": "rest", "trusted_local_admin": True, "auth_scope": "daemon-admin"},
    )
    args = dict(
        project_id=target.id,
        project_slug=target.slug,
        artifact_id=artifact_id,
        expected_uri="/generated-assets/openai-images/legacy.png",
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        confirm_ownership=True,
        asset_root=settings.generated_assets_dir,
    )
    with bind_context(ctx):
        for wrong in ({"project_id": other.id}, {"artifact_id": survivor.id}):
            with pytest.raises(ConflictError):
                repo.reconcile_generated_file(**{**args, **wrong})
        repo.reconcile_generated_file(**args)
    with Session(s.get_bind()) as restarted:
        result = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
        assert result.status == "deleted" and result.operation_id == first.operation_id
        assert path.exists() and foreign.read_text() == "sentinel"
        assert restarted.get(Artifact, survivor.id).managed_file_json is not None
        assert restarted.get(Artifact, artifact_id) is None
    with Session(s.get_bind()) as restarted:
        result = await ProjectCleanupService(restarted, settings).delete(
            other.id, confirmation=confirmation(other)
        )
        assert result.status == "deleted"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("root_index", range(7))
async def test_shared_leaf_from_each_derived_root_is_retired_by_final_reference(
    fixture, root_index
):
    s, settings, target, other = fixture
    owned_root = ProjectCleanupService(s, settings)._roots(target.id)[root_index]
    path = put(owned_root / "nested" / "shared.bin")
    unrelated = put(settings.data_dir / "unowned" / "must-survive")
    third = (
        ProjectRepository(s)
        .create(slug="disposable-third-reference", name="Third", domain="fixture.test")
        .data
    )
    for project in (other, third):
        ArtifactRepository(s).create(project_id=project.id, kind="file", uri=path.as_uri())
        ArtifactRepository(s).create(project_id=project.id, kind="file", uri=unrelated.as_uri())
    for project in (target, other, third):
        with Session(s.get_bind()) as restarted:
            result = await ProjectCleanupService(restarted, settings).delete(
                project.id, confirmation=confirmation(project)
            )
            assert result.status == "deleted"
        if project.id != third.id:
            assert path.exists()
        assert unrelated.read_text() == "sentinel"
    assert not path.exists() and not owned_root.exists()


@pytest.mark.asyncio
async def test_shared_root_alias_custody_never_follows_its_external_target(fixture, tmp_path):
    s, settings, target, other = fixture
    root = ProjectCleanupService(s, settings)._roots(target.id)[0]
    root.mkdir(parents=True)
    external = put(tmp_path / "repository" / "keep")
    alias = root / "shared-link"
    alias.symlink_to(external)
    ArtifactRepository(s).create(project_id=other.id, kind="file", uri=alias.as_uri())
    for project in (target, other):
        with Session(s.get_bind()) as restarted:
            result = await ProjectCleanupService(restarted, settings).delete(
                project.id, confirmation=confirmation(project)
            )
            assert result.status == "deleted"
        assert external.read_text() == "sentinel"
    assert not alias.is_symlink() and not root.exists()
