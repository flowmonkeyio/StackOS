"""Managed project data is mandatory backup coverage, with isolated recovery paths."""

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from stackos.backups import (
    BackupError,
    BackupPolicy,
    backup_status,
    create_backup,
    generate_recovery_key,
    recovery,
    restore_backup,
    save_policy,
    service,
)
from stackos.backups.format import LEGACY_MANIFEST_SCHEMA, REQUIRED_NAMES
from stackos.backups.models import parse_public_key
from stackos.config import Settings


@pytest.fixture
def project_files(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.data_dir.mkdir()
    settings.state_dir.mkdir()
    settings.seed_path.write_bytes(os.urandom(32))
    settings.token_path.write_text("synthetic-project-backup-token")
    with sqlite3.connect(settings.db_path) as db:
        db.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)")
        db.executemany("INSERT INTO projects VALUES (?, ?)", [(1, "One"), (2, "Two")])
    for project_id in (1, 2):
        parent = settings.data_dir / "projects" / str(project_id)
        finance = parent / "finance"
        (finance / "originals").mkdir(parents=True)
        (parent / ".writer.lock").touch()
        (finance / ".writer.lock").touch()
        (parent / "finance-state.json").write_text(json.dumps({"project_id": project_id}))
        (finance / "finance.json").write_text(json.dumps({"synthetic_project": project_id}))
        (finance / "originals" / "invoice.pdf").write_bytes(
            f"synthetic-invoice-{project_id}".encode()
        )
        (finance / "originals" / "receipt.pdf").write_bytes(
            f"synthetic-receipt-{project_id}".encode()
        )
    private, public = tmp_path / "keys/private.pem", tmp_path / "keys/public.pem"
    generate_recovery_key(settings, private, public)
    save_policy(settings, BackupPolicy(recipient_public_key=public.read_text()))
    return settings, private


def test_project_files_and_originals_roundtrip_without_enrollment(project_files, tmp_path):
    settings, private = project_files
    for lock in (settings.data_dir / "projects").rglob(".writer.lock"):
        lock.write_bytes(b"old coordination state is not recovered")
    status = backup_status(settings)
    assert status["targets"] == []
    assert status["project_ids"] == [1, 2]
    assert not status["enabled"]
    created = create_backup(settings)
    archive = Path(created["path"])
    assert b"synthetic-invoice" not in archive.read_bytes()
    output = tmp_path / "restored"
    result = restore_backup(settings, archive, private, output)
    assert result["verified"] and result["adoption_required"]
    assert result["encryption"]["format"] == created["encryption"]["format"]
    assert result["manifest_schema"] == "stackos.local-backup.v3"
    assert result["project_ids"] == [1, 2]
    assert not (output / "state/backup-policy.json").exists()
    for project_id in (1, 2):
        expected = settings.data_dir / "projects" / str(project_id)
        restored = output / "data/projects" / str(project_id)
        for source in expected.rglob("*"):
            if source.is_file() and source.name != ".writer.lock":
                assert (restored / source.relative_to(expected)).read_bytes() == source.read_bytes()
        assert {
            lock.relative_to(restored).as_posix() for lock in restored.rglob(".writer.lock")
        } == {
            ".writer.lock",
            "finance/.writer.lock",
        }
        for lock in restored.rglob(".writer.lock"):
            info = lock.lstat()
            assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            assert info.st_mode & 0o777 == 0o600
            assert lock.read_bytes() == b""
    assert (output / "data/projects/1/finance/originals/invoice.pdf").read_bytes() != (
        output / "data/projects/2/finance/originals/invoice.pdf"
    ).read_bytes()


@pytest.mark.parametrize("lock", ["1/.writer.lock", "1/finance/.writer.lock"])
def test_busy_project_or_finance_writer_fails_archive(project_files, lock):
    settings, _ = project_files
    fd = os.open(settings.data_dir / "projects" / lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BackupError, match="active writer"):
            create_backup(settings)
        assert not list((settings.state_dir / "backups").glob("*.sbak"))
    finally:
        os.close(fd)


@pytest.mark.parametrize("lock", ["1/.writer.lock", "1/finance/.writer.lock"])
def test_missing_lock_never_silently_omits_project(project_files, lock):
    settings, _ = project_files
    (settings.data_dir / "projects" / lock).unlink()
    assert not backup_status(settings)["ready"]
    assert backup_status(settings)["project_ids"] is None
    with pytest.raises(BackupError, match="coordination lock is missing"):
        create_backup(settings)


@pytest.mark.parametrize(
    "component", ["data", "data/projects", "data/projects/1", "data/projects/1/finance"]
)
def test_recovered_project_coordination_rejects_path_aliases(tmp_path, component):
    output = tmp_path / "recovered"
    (output / "data/projects/1/finance").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = output / component
    shutil.rmtree(target)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, BackupError)):
        recovery._initialize_project_locks(output, [1])
    assert not (outside / ".writer.lock").exists()


@pytest.mark.parametrize("existing", ["regular", "symlink", "fifo"])
def test_recovery_requires_fresh_coordination_locks(tmp_path, existing):
    output = tmp_path / "recovered"
    container = output / "data/projects/1/plugin-data"
    container.mkdir(parents=True)
    lock = container / ".writer.lock"
    outside = tmp_path / "outside-file"
    outside.write_bytes(b"preserve outside bytes")
    if existing == "regular":
        lock.write_bytes(b"untrusted old coordination")
    elif existing == "symlink":
        lock.symlink_to(outside)
    else:
        os.mkfifo(lock)
    with pytest.raises((OSError, BackupError)):
        recovery._initialize_project_locks(output, [1])
    assert outside.read_bytes() == b"preserve outside bytes"


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_project_aliases_are_rejected(project_files, kind):
    settings, _ = project_files
    source = settings.data_dir / "projects/1/finance/finance.json"
    alias = settings.data_dir / "projects/2/finance/alias.json"
    if kind == "symlink":
        alias.symlink_to(source)
    else:
        os.link(source, alias)
    with pytest.raises(BackupError):
        create_backup(settings)


def test_deleted_project_files_stay_covered(project_files, tmp_path):
    settings, private = project_files
    with sqlite3.connect(settings.db_path) as db:
        db.execute("DELETE FROM projects WHERE id = 2")
    archive = Path(create_backup(settings)["path"])
    output = tmp_path / "deleted-project-recovery"
    result = restore_backup(settings, archive, private, output)
    assert result["project_ids"] == [1, 2]
    assert (output / "data/projects/2/finance/finance.json").is_file()
    with sqlite3.connect(output / "data/stackos.db") as db:
        assert db.execute("SELECT id FROM projects").fetchall() == [(1,)]


@pytest.mark.parametrize("published", [False, True])
def test_interrupted_project_publication_cannot_produce_incomplete_archive(
    project_files, published
):
    settings, _ = project_files
    parent = settings.data_dir / "projects/1"
    if not published:
        import shutil

        shutil.rmtree(parent / "finance")
    (parent / ".publication-pending").write_text("synthetic pending publication")
    status = backup_status(settings)
    assert not status["ready"] and status["project_ids"] is None
    with pytest.raises(BackupError, match="incomplete publication"):
        create_backup(settings)
    assert not list((settings.state_dir / "backups").glob("*.sbak"))


def test_project_created_during_backup_fails_instead_of_being_omitted(project_files, monkeypatch):
    settings, _ = project_files
    original = service._snapshot_database

    def new_project(source, destination):
        original(source, destination)
        new = settings.data_dir / "projects/3"
        new.mkdir()
        (new / ".writer.lock").touch()
        (new / "record.json").write_text("synthetic-new-project")

    monkeypatch.setattr(service, "_snapshot_database", new_project)
    with pytest.raises(BackupError, match="changed during backup"):
        create_backup(settings)


def test_v2_archive_manifest_remains_readable_but_cannot_claim_managed_projects():
    files = {name: b"synthetic" for name in REQUIRED_NAMES}
    manifest = {
        "schema": LEGACY_MANIFEST_SCHEMA,
        "targets": [],
        "included": [
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ],
    }
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(payload) as archive:
        assert recovery._inspect(archive)[0]["schema"] == LEGACY_MANIFEST_SCHEMA
    extra = "data/projects/1/finance/finance.json"
    files[extra] = b"{}"
    manifest["included"].append(
        {"path": extra, "bytes": 2, "sha256": hashlib.sha256(b"{}").hexdigest()}
    )
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("data/projects/1/", b"")
        for name, data in files.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(payload) as archive, pytest.raises(BackupError, match="manifest"):
        recovery._inspect(archive)


def test_real_v2_encrypted_archive_restores_without_inventing_project_files(
    project_files, tmp_path
):
    settings, private = project_files
    files = {
        "data/stackos.db": settings.db_path.read_bytes(),
        "state/seed.bin": settings.seed_path.read_bytes(),
        "state/auth.token": settings.token_path.read_bytes(),
        "state/backup-policy.reference.json": b"{}",
    }
    manifest = {
        "schema": LEGACY_MANIFEST_SCHEMA,
        "created_at": "2026-10-01T00:00:00+00:00",
        "targets": [],
        "included": [
            {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in files.items()
        ],
    }
    payload = tmp_path / "legacy-payload.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    encrypted = tmp_path / "legacy.sbak"
    key = service.load_policy(settings).recipient_public_key
    assert key is not None
    with encrypted.open("wb") as output:
        created_encryption = service.encrypt_payload(payload, output, parse_public_key(key))
    restored = tmp_path / "legacy-restored"
    result = restore_backup(settings, encrypted, private, restored)
    assert result["verified"]
    assert result["encryption"] == created_encryption
    assert result["manifest_schema"] == LEGACY_MANIFEST_SCHEMA
    assert result["project_ids"] == []
    assert not (restored / "data/projects").exists()
    assert (restored / "data/stackos.db").read_bytes() == settings.db_path.read_bytes()
