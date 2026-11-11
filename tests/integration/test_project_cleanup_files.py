"""Repair regressions through real artifact/producer and cleanup owners.

All files/DBs use the disposable cleanup fixture; no provider calls are made.
"""

import fcntl
import importlib
import json
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError as SchemaValidationError
from sqlmodel import Session

from stackos.actions.media_artifacts import register_generated_media_artifacts
from stackos.db.models import Artifact, Project
from stackos.mcp.tools.artifacts import ArtifactCreateInput
from stackos.plugins.builtin_finance import FinanceConsole
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactOut, ArtifactRepository
from tests.integration.test_project_cleanup import confirmation, put
from tests.integration.test_project_cleanup import fixture as fixture

PRODUCERS = [
    "openai_images",
    "google_gemini_image",
    "ideogram_images",
    "byteplus_seedream",
    "reve_images",
    "xai_imagine",
    "aignc",
    "alibaba_wan",
    "google_veo",
    "byteplus_seedance",
    "kling_video",
]


def register(producer, request, uri):
    output = {"data": [{"url": uri, "file_format": "png"}]}
    if producer in PRODUCERS[:5]:
        return importlib.import_module(
            "stackos.actions." + producer
        )._register_generated_image_artifacts(request, output)
    if producer == "xai_imagine":
        return importlib.import_module(
            "stackos.actions.xai_imagine"
        )._register_generated_media_artifacts(request, output, kind="image")
    return register_generated_media_artifacts(
        request, output, kind="image", provider_key=producer, source=producer + "-action"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("producer", PRODUCERS)
@pytest.mark.parametrize("shared", ["none", "file", "directory"])
async def test_actual_media_registrars_supply_custody_and_preserve_shared_bytes(
    fixture, producer, shared
):
    s, settings, target, other = fixture
    path = put(settings.generated_assets_dir / producer / "owned.png")
    uri = "/generated-assets/" + path.relative_to(settings.generated_assets_dir).as_posix()
    request = SimpleNamespace(
        session=s,
        asset_dir=settings.generated_assets_dir,
        project_id=target.id,
        operation="fixture",
        action_ref="fixture",
    )
    output = register(producer, request, uri)
    artifact_id = output["data"][0]["artifact_id"]
    assert s.get(Artifact, artifact_id).managed_file_json["path"] == producer + "/owned.png"
    repo = ArtifactRepository(s)
    if shared != "none":
        reference = uri if shared == "file" else uri.rsplit("/", 1)[0]
        repo.create(project_id=other.id, kind="image", uri=reference)
    # Editing the public reference or provenance cannot change file ownership.
    foreign = put(settings.generated_assets_dir / "action-outputs" / f"project-{other.id}" / "keep")
    repo.update(
        artifact_id,
        project_id=target.id,
        fields={"uri", "provenance_json"},
        uri="/generated-assets/action-outputs/" + f"project-{other.id}/keep",
        provenance_json={"source": "caller-edited"},
    )
    result = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "deleted"
    assert path.exists() is (shared != "none")
    assert foreign.read_text() == "sentinel"


@pytest.mark.asyncio
@pytest.mark.parametrize("directory", [False, True])
async def test_public_artifact_create_cannot_grant_custody_with_metadata(fixture, directory):
    s, settings, target, other = fixture
    foreign = put(settings.generated_assets_dir / "action-outputs" / f"project-{other.id}" / "keep")
    relative = "action-outputs" if directory else f"action-outputs/project-{other.id}/keep"
    out = (
        ArtifactRepository(s)
        .create(
            project_id=target.id,
            kind="file",
            uri="/generated-assets/" + relative,
            provenance_json={"source": "aignc-action"},
            metadata_json={"managed_file_json": {"path": relative}},
        )
        .data
    )
    assert s.get(Artifact, out.id).managed_file_json is None
    with pytest.raises(SchemaValidationError):
        ArtifactCreateInput.model_validate(
            {
                "project_id": target.id,
                "kind": "file",
                "uri": "/generated-assets/" + relative,
                "managed_file_json": {"path": relative},
            }
        )
    assert "managed_file_json" not in ArtifactOut.model_fields
    result = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "incomplete"  # Preserve ambiguous legacy evidence, never infer custody.
    assert foreign.read_text() == "sentinel"
    assert s.get(Artifact, out.id) is not None


@pytest.mark.parametrize("directory", [False, True])
def test_internal_file_custody_rejects_foreign_roots_and_directories(fixture, directory):
    s, settings, target, other = fixture
    foreign = put(settings.generated_assets_dir / "action-outputs" / f"project-{other.id}" / "keep")
    path = foreign.parent if directory else foreign
    with pytest.raises(ValidationError):
        ArtifactRepository(s).create_generated_file(
            project_id=target.id,
            kind="file",
            path=path,
            asset_root=settings.generated_assets_dir,
            uri="/generated-assets/" + path.relative_to(settings.generated_assets_dir).as_posix(),
        )
    assert foreign.exists()


@pytest.mark.asyncio
async def test_file_replacement_retains_source_and_retry_can_restore_original_custody(fixture):
    s, settings, target, _ = fixture
    path = put(settings.generated_assets_dir / "provider" / "owned.png")
    artifact = (
        ArtifactRepository(s)
        .create_generated_file(
            project_id=target.id,
            kind="image",
            path=path,
            asset_root=settings.generated_assets_dir,
            uri="/generated-assets/provider/owned.png",
        )
        .data
    )
    original = path.with_name("moved.png")
    path.rename(original)
    put(path, "replacement")
    result = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "incomplete" and path.read_text() == "replacement"
    assert s.get(Artifact, artifact.id) is not None
    original.replace(path)
    with Session(s.get_bind()) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    assert retry.status == "deleted" and retry.operation_id == result.operation_id
    assert not path.exists()


@pytest.mark.asyncio
async def test_partial_finance_stage_cleanup_retries_after_lock_was_removed(fixture, monkeypatch):
    s, settings, target, _ = fixture
    console = FinanceConsole(s, settings)
    state = console._new_state(s.get(Project, target.id), 1)
    state["publication"] = {"token": "b" * 32}
    put(settings.data_dir / "projects" / str(target.id) / "finance-state.json", json.dumps(state))
    stage = console.config_dir / "staging" / ("b" * 32)
    put(stage / ".writer.lock")
    remaining = put(stage / "finance.json")
    unlink, listing = os.unlink, os.listdir
    injected = False

    def lock_first(path):
        return sorted(listing(path), key=lambda name: (name != ".writer.lock", name))

    def fail_once(path, *args, **kwargs):
        nonlocal injected
        if path == "finance.json" and not injected:
            injected = True
            raise OSError("disposable interrupted unlink")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patches:
        patches.setattr(os, "listdir", lock_first)
        patches.setattr(os, "unlink", fail_once)
        result = await ProjectCleanupService(s, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    assert result.status == "incomplete" and injected
    assert remaining.exists() and not (stage / ".writer.lock").exists()
    with Session(s.get_bind()) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    assert retry.status == "deleted" and retry.operation_id == result.operation_id
    assert not stage.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", [False, True])
async def test_finance_stage_retry_preserves_busy_or_aliased_lock(fixture, unsafe):
    s, settings, target, _ = fixture
    console = FinanceConsole(s, settings)
    state = console._new_state(s.get(Project, target.id), 1)
    state["publication"] = {"token": "c" * 32}
    put(settings.data_dir / "projects" / str(target.id) / "finance-state.json", json.dumps(state))
    stage = console.config_dir / "staging" / ("c" * 32)
    retained = put(stage / "finance.json")
    lock = stage / ".writer.lock"
    sentinel = put(settings.state_dir / "external-lock")
    if unsafe:
        lock.symlink_to(sentinel)
    else:
        put(lock)
    with sentinel.open("r+") if unsafe else lock.open("r+") as writer:
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = lock.lstat().st_ino
        result = await ProjectCleanupService(s, settings).delete(
            target.id, confirmation=confirmation(target)
        )
        assert result.status == "incomplete"
        assert lock.lstat().st_ino == identity and retained.exists()
        assert sentinel.read_text() == "sentinel"
    if unsafe:
        lock.unlink()  # Explicit fixture repair of the unsafe alias.
    with Session(s.get_bind()) as restarted:
        retry = await ProjectCleanupService(restarted, settings).delete(
            target.id, confirmation=confirmation(target)
        )
    assert retry.status == "deleted" and retry.operation_id == result.operation_id


def test_managed_file_migration_preserves_old_refs_and_existing_custody(tmp_path):
    import sqlite3

    from alembic import command

    from stackos.config import Settings
    from stackos.db.migrate import alembic_config

    settings = Settings(
        data_dir=tmp_path / "migration-data", state_dir=tmp_path / "migration-state"
    )
    settings.ensure_dirs()
    config = alembic_config(settings)
    command.upgrade(config, "0039_project_cleanup")
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute(
            "INSERT INTO artifacts(kind,uri,status,created_at,updated_at) "
            "VALUES ('image','/generated-assets/legacy/file.png','draft','2026-01-01','2026-01-01')"
        )
    command.upgrade(config, "head")
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT uri,managed_file_json FROM artifacts").fetchall() == [
            ("/generated-assets/legacy/file.png", None)
        ]
        connection.execute(
            "UPDATE artifacts SET managed_file_json=?", (json.dumps({"path": "kept"}),)
        )
        connection.execute("UPDATE alembic_version SET version_num='0039_project_cleanup'")
    command.upgrade(config, "head")
    with sqlite3.connect(settings.db_path) as connection:
        assert json.loads(
            connection.execute("SELECT managed_file_json FROM artifacts").fetchone()[0]
        ) == {"path": "kept"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
