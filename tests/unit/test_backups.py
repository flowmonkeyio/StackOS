"""Producer-only safety proof with disposable synthetic state and external finance files."""

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import struct
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from stackos.backups import (
    BackupError,
    BackupPolicy,
    BackupTarget,
    backup_status,
    create_backup,
    envelope,
    generate_recovery_key,
    limits,
    load_policy,
    recovery,
    restore_backup,
    run_due_backup,
    save_policy,
    service,
    update_policy,
)
from stackos.backups.filesystem import backup_lock, inside, overlap
from stackos.config import Settings

SENTINEL = b"synthetic-provider-secret-no-real-account"


@pytest.fixture
def workspace(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.data_dir.mkdir()
    settings.state_dir.mkdir()
    settings.seed_path.write_bytes(os.urandom(32))
    settings.token_path.write_bytes(b"synthetic-daemon-token")
    nonce = os.urandom(12)
    database = sqlite3.connect(settings.db_path)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE accounts (secret BLOB NOT NULL)")
    database.execute(
        "INSERT INTO accounts VALUES (?)",
        (nonce + AESGCM(settings.seed_path.read_bytes()).encrypt(nonce, SENTINEL, None),),
    )
    database.commit()
    finance = tmp_path / "finance"
    (finance / "originals").mkdir(parents=True)
    (finance / "reviews").mkdir()
    (finance / "exports").mkdir()
    (finance / "finance.json").write_text('{"revision":7,"invoices":[{"gross":25.75}]}')
    (finance / "schema.json").write_text('{"version":1}')
    (finance / "originals" / "invoice.pdf").write_bytes(b"%PDF-1.4 synthetic original invoice")
    (finance / "reviews" / "review.json").write_text('{"status":"prepared"}')
    (finance / ".writer.lock").touch()
    private = tmp_path / "recovery-keys" / "private.pem"
    public = tmp_path / "recovery-keys" / "public.pem"
    generate_recovery_key(settings, private, public)
    target = BackupTarget(
        id="finance", label="Business finance", path=finance, lock_file=finance / ".writer.lock"
    )
    policy = save_policy(
        settings, BackupPolicy(recipient_public_key=public.read_text(), targets=[target])
    )
    yield settings, finance, private, public, policy, database
    database.close()


def _assert_clean(settings):
    assert not list(settings.state_dir.glob(".stackos-backup-*"))
    assert not list((settings.state_dir / "backups").glob(".backup-encrypted-*"))


def _compare_restored(workspace, archive, output):
    settings, finance, private, _, _, _ = workspace
    result = restore_backup(settings, Path(archive), private, output)
    assert result["verified"] and result["adoption_required"]
    assert result["target_ids"] == ["finance"]
    for source in finance.rglob("*"):
        if source.is_file() and source.name != ".writer.lock":
            assert (
                output / "external/finance" / source.relative_to(finance)
            ).read_bytes() == source.read_bytes()
    assert (output / "external/finance/exports").is_dir()
    assert not (output / "external/finance/.writer.lock").exists()
    assert (output / "state/seed.bin").read_bytes() == settings.seed_path.read_bytes()
    assert (output / "state/auth.token").read_bytes() == settings.token_path.read_bytes()
    assert (output / "state/backup-policy.reference.json").is_file()
    assert not (output / "state/backup-policy.json").exists()
    recovered_settings = Settings(data_dir=output / "data", state_dir=output / "state")
    assert not load_policy(recovered_settings).enabled
    with sqlite3.connect(output / "data/stackos.db") as database:
        encrypted = database.execute("SELECT secret FROM accounts").fetchone()[0]
    assert (
        AESGCM((output / "state/seed.bin").read_bytes()).decrypt(
            encrypted[:12], encrypted[12:], None
        )
        == SENTINEL
    )
    for path in output.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    return result


def test_manual_and_automatic_roundtrip_are_complete_private_and_wal_consistent(
    workspace, tmp_path
):
    settings, finance, private, _, policy, database = workspace
    original_hash = hashlib.sha256((finance / "finance.json").read_bytes()).hexdigest()
    # Keep the live WAL connection open: the snapshot must include this committed row.
    database.execute("INSERT INTO accounts VALUES (?)", (b"wal-row",))
    database.commit()
    manual = create_backup(settings)
    archive = Path(manual["path"])
    raw = archive.read_bytes()
    assert raw.startswith(envelope.MAGIC)
    assert not zipfile.is_zipfile(archive)
    for forbidden in (
        SENTINEL,
        settings.seed_path.read_bytes(),
        settings.token_path.read_bytes(),
        b"finance.json",
        str(finance).encode(),
        private.read_bytes(),
    ):
        assert forbidden not in raw
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(archive.parent.stat().st_mode) == 0o700
    _compare_restored(workspace, archive, tmp_path / "manual-restored")
    with sqlite3.connect(tmp_path / "manual-restored/data/stackos.db") as database_copy:
        assert database_copy.execute("SELECT count(*) FROM accounts").fetchone() == (2,)
    save_policy(settings, policy.model_copy(update={"enabled": True}))
    auto = run_due_backup(settings, now=datetime(2030, 1, 1, tzinfo=UTC))
    assert auto["status"] == "success"
    _compare_restored(workspace, auto["path"], tmp_path / "auto-restored")
    assert original_hash == hashlib.sha256((finance / "finance.json").read_bytes()).hexdigest()
    _assert_clean(settings)


def test_defaults_opt_in_reload_due_interval_disable_and_status(workspace):
    settings, _, private, _, _, _ = workspace
    assert not load_policy(settings).enabled
    assert run_due_backup(settings) == {"status": "skipped", "reason": "disabled"}
    update_policy(settings, {"enabled": True, "interval_hours": 2})
    now = datetime(2030, 1, 1, tzinfo=UTC)
    first = run_due_backup(settings, now=now)
    assert first["status"] == "success"
    same_settings = Settings(data_dir=settings.data_dir, state_dir=settings.state_dir)
    assert load_policy(same_settings).enabled
    assert run_due_backup(same_settings, now=now + timedelta(hours=1))["reason"] == "not_due"
    update_policy(settings, {"enabled": False})
    assert run_due_backup(same_settings, now=now + timedelta(days=2))["reason"] == "disabled"
    status = backup_status(settings)
    assert status["last_success"]["path"] == first["path"]
    encoded = json.dumps(status)
    assert str(private) not in encoded and private.read_text() not in encoded
    assert set(status) == {
        "configured",
        "ready",
        "enabled",
        "interval_hours",
        "keep_last",
        "destination",
        "targets",
        "project_ids",
        "recipient_fingerprint",
        "last_attempt",
        "last_success",
        "last_error",
    }


def test_fresh_policy_defaults_off_without_creating_archive(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    assert load_policy(settings) == BackupPolicy()
    assert run_due_backup(settings)["reason"] == "disabled"
    assert not (settings.state_dir / "backups").exists()


def test_no_key_never_writes_plain_archive(workspace, tmp_path):
    settings = workspace[0]
    save_policy(settings, BackupPolicy())
    with pytest.raises(BackupError, match="public key"):
        create_backup(settings, output=tmp_path / "no-key.zip")
    assert not (tmp_path / "no-key.zip").exists()
    with pytest.raises(BackupError):
        update_policy(settings, {"enabled": True})
    _assert_clean(settings)


def test_failure_backoff_and_last_good_success_preserved(workspace):
    settings, finance, _, _, _, _ = workspace
    update_policy(settings, {"enabled": True, "interval_hours": 1})
    now = datetime(2030, 1, 1, tzinfo=UTC)
    good = run_due_backup(settings, now=now)
    shutil.rmtree(finance)
    failure = run_due_backup(settings, now=now + timedelta(hours=2))
    assert failure["status"] == "failed"
    assert failure["error"]["code"] == "source_unavailable"
    assert (
        run_due_backup(settings, now=now + timedelta(hours=2, minutes=30))["reason"]
        == "retry_backoff"
    )
    status = backup_status(settings)
    assert status["last_success"]["path"] == good["path"]
    assert Path(good["path"]).is_file()
    assert not status["ready"]
    # Repair control must remain usable even with an unavailable enrolled folder.
    update_policy(settings, {"enabled": False})
    assert not load_policy(settings).enabled


def test_global_lock_serializes_manual_auto_and_atomic_policy_updates(workspace):
    settings = workspace[0]
    update_policy(settings, {"enabled": True})
    with backup_lock(settings.state_dir):
        assert run_due_backup(settings)["reason"] == "busy"
        with pytest.raises(BackupError) as error:
            create_backup(settings)
        assert error.value.code == "backup_busy"
        with pytest.raises(BackupError):
            update_policy(settings, {"keep_last": 5})
    update_policy(settings, {"keep_last": 5})
    update_policy(settings, {"interval_hours": 3})
    assert load_policy(settings).keep_last == 5
    assert load_policy(settings).interval_hours == 3
    second = workspace[1].parent / "another"
    second.mkdir()
    update_policy(
        settings, {}, upsert_target=BackupTarget(id="second", label="Second", path=second)
    )
    assert {item.id for item in load_policy(settings).targets} == {"finance", "second"}


@pytest.mark.parametrize("missing", ["folder", "lock"])
def test_every_registered_root_and_lock_are_mandatory(workspace, missing):
    settings, finance, _, _, _, _ = workspace
    if missing == "folder":
        shutil.rmtree(finance)
    else:
        (finance / ".writer.lock").unlink()
    with pytest.raises(BackupError):
        create_backup(settings)
    assert not list((settings.state_dir / "backups").glob("*.sbak"))
    _assert_clean(settings)


def test_active_finance_writer_fails_without_reclaiming_lock(workspace):
    settings, finance, _, _, _, _ = workspace
    path = finance / ".writer.lock"
    inode = path.stat().st_ino
    with path.open("r+b") as writer:
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BackupError) as error:
            create_backup(settings)
        assert error.value.code == "source_busy"
    assert path.stat().st_ino == inode
    _assert_clean(settings)


@pytest.mark.parametrize("kind", ["symlink", "fifo", "root_symlink", "lock_symlink"])
def test_unsafe_source_files_fail_closed(workspace, tmp_path, kind):
    settings, finance, _, _, _, _ = workspace
    if kind == "symlink":
        (finance / "linked").symlink_to(settings.token_path)
    elif kind == "fifo":
        os.mkfifo(finance / "pipe")
    elif kind == "root_symlink":
        finance.rename(tmp_path / "real-finance")
        finance.symlink_to(tmp_path / "real-finance", target_is_directory=True)
    else:
        (finance / ".writer.lock").unlink()
        (finance / ".writer.lock").symlink_to(settings.token_path)
    with pytest.raises(BackupError):
        create_backup(settings)
    _assert_clean(settings)


def test_changing_source_is_detected_by_final_inventory(workspace, monkeypatch):
    settings, finance, _, _, _, _ = workspace
    original = service._copy_file

    def changing_copy(fd, destination, expected, recipient):
        original(fd, destination, expected, recipient)
        if destination.name == "finance.json":
            (finance / "added-after-copy.json").write_text("{}")

    monkeypatch.setattr(service, "_copy_file", changing_copy)
    with pytest.raises(BackupError) as error:
        create_backup(settings)
    assert error.value.code == "source_changed"
    _assert_clean(settings)


@pytest.mark.parametrize("during_snapshot", [False, True])
def test_seed_rotation_marker_and_changed_seed_fenced(workspace, monkeypatch, during_snapshot):
    settings = workspace[0]
    marker = settings.seed_path.with_suffix(".bin.new")
    if not during_snapshot:
        marker.write_bytes(os.urandom(32))
    else:
        original = service._snapshot_database

        def rotate(source, destination):
            original(source, destination)
            settings.seed_path.write_bytes(os.urandom(32))

        monkeypatch.setattr(service, "_snapshot_database", rotate)
    with pytest.raises(BackupError) as error:
        create_backup(settings)
    assert error.value.code == "seed_rotation"
    _assert_clean(settings)


def test_optional_old_seed_is_preserved(workspace, tmp_path):
    settings = workspace[0]
    old = settings.seed_path.with_suffix(".bin.bak")
    old.write_bytes(os.urandom(32))
    result = create_backup(settings)
    _compare_restored(workspace, result["path"], tmp_path / "old-seed-restored")
    assert (tmp_path / "old-seed-restored/state/seed.bin.bak").read_bytes() == old.read_bytes()


def test_later_enrollment_and_destination_cannot_capture_generated_key(workspace):
    settings, _, private, _, _, _ = workspace
    with pytest.raises(BackupError) as error:
        update_policy(
            settings, {}, upsert_target=BackupTarget(id="keys", label="Keys", path=private.parent)
        )
    assert error.value.code == "recovery_key_capture"
    with pytest.raises(BackupError):
        update_policy(settings, {"destination": str(private.parent)})
    with pytest.raises(BackupError):
        generate_recovery_key(
            settings, workspace[1] / "new-key.pem", workspace[1] / "new-public.pem"
        )
    assert load_policy(settings).targets[0].id == "finance"


@pytest.mark.parametrize("padding", [0, 1024 * 1024 + 100])
def test_copied_or_embedded_matching_private_key_is_rejected(workspace, padding):
    settings, finance, private, _, _, _ = workspace
    (finance / "copied-private.pem").write_bytes(b" " * padding + private.read_bytes())
    with pytest.raises(BackupError) as error:
        create_backup(settings)
    assert error.value.code == "recovery_key_capture"
    assert str(private) not in str(error.value)
    _assert_clean(settings)


def test_public_mount_manual_and_scheduled_destinations_are_rejected(workspace):
    settings = workspace[0]
    settings.generated_assets_dir.mkdir()
    with pytest.raises(BackupError):
        update_policy(settings, {"destination": str(settings.generated_assets_dir / "backups")})
    with pytest.raises(BackupError):
        create_backup(settings, output=settings.generated_assets_dir / "private.sbak")
    assert not (settings.generated_assets_dir / "private.sbak").exists()


def test_overlap_duplicate_target_and_source_output_rejected(workspace):
    settings, finance, _, _, policy, _ = workspace
    with pytest.raises(BackupError):
        create_backup(settings, extra_targets=[policy.targets[0]])
    with pytest.raises(BackupError):
        update_policy(
            settings,
            {},
            upsert_target=BackupTarget(id="nested", label="Nested", path=finance / "originals"),
        )
    with pytest.raises(BackupError):
        create_backup(settings, output=finance / "recursive.sbak")
    with pytest.raises(BackupError):
        update_policy(settings, {"destination": str(finance / "backups")})


def test_existing_output_is_never_overwritten_and_staging_cleaned(workspace, tmp_path):
    settings = workspace[0]
    output = tmp_path / "existing.sbak"
    output.write_bytes(b"existing")
    with pytest.raises(BackupError) as error:
        create_backup(settings, output=output)
    assert error.value.code == "output_exists"
    assert output.read_bytes() == b"existing"
    _assert_clean(settings)


@pytest.mark.parametrize("failure", ["encrypt", "publish", "fsync"])
def test_failed_publication_retains_previous_success_and_removes_partial(
    workspace, tmp_path, monkeypatch, failure
):
    settings = workspace[0]
    good = create_backup(settings)
    output = tmp_path / "manual-output" / "failed.sbak"

    def fail(*_args, **_kwargs):
        raise OSError("synthetic disk failure")

    if failure == "encrypt":
        monkeypatch.setattr(service, "encrypt_payload", fail)
    elif failure == "publish":
        monkeypatch.setattr(service.os, "link", fail)
    else:
        original = service.fsync_directory

        def failing_final_fsync(path):
            if path == output.parent and output.exists():
                fail()
            original(path)

        monkeypatch.setattr(service, "fsync_directory", failing_final_fsync)
    with pytest.raises(BackupError):
        create_backup(settings, output=output)
    assert not output.exists()
    assert Path(good["path"]).exists()
    assert backup_status(settings)["last_success"] == good
    assert not list(output.parent.glob(".backup-encrypted-*"))
    _assert_clean(settings)


def test_retention_only_own_successful_scheduled_archives_after_success(workspace):
    settings, finance, _, _, _, _ = workspace
    manual = create_backup(settings)
    destination = Path(manual["path"]).parent
    foreign = destination / "stackos-auto-foreign.sbak"
    foreign.write_bytes(b"foreign file must stay")
    update_policy(settings, {"enabled": True, "interval_hours": 1, "keep_last": 2})
    now = datetime(2030, 1, 1, tzinfo=UTC)
    first = run_due_backup(settings, now=now)
    second = run_due_backup(settings, now=now + timedelta(hours=1))
    third = run_due_backup(settings, now=now + timedelta(hours=2))
    assert not Path(first["path"]).exists()
    assert Path(second["path"]).is_file() and Path(third["path"]).is_file()
    assert foreign.read_bytes() == b"foreign file must stay"
    assert Path(manual["path"]).is_file()
    (finance / ".writer.lock").unlink()
    assert run_due_backup(settings, now=now + timedelta(hours=3))["status"] == "failed"
    assert Path(second["path"]).is_file() and Path(third["path"]).is_file()


@pytest.mark.parametrize(
    "damage", ["wrong_key", "header", "ciphertext", "tag", "truncated", "unknown"]
)
def test_authentication_rejection_happens_before_zip_use_or_output(
    workspace, tmp_path, monkeypatch, damage
):
    settings, _, private, _, _, _ = workspace
    result = create_backup(settings)
    original = Path(result["path"])
    damaged = tmp_path / "damaged.sbak"
    content = bytearray(original.read_bytes())
    header_length = struct.unpack(">I", content[len(envelope.MAGIC) : len(envelope.MAGIC) + 4])[0]
    if damage == "wrong_key":
        private = tmp_path / "wrong.pem"
        private.write_bytes(
            X25519PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    elif damage == "header":
        position = content.index(b"recipient_fingerprint") + len(b'recipient_fingerprint":"')
        content[position] = ord("a") if content[position] != ord("a") else ord("b")
    elif damage == "ciphertext":
        content[len(envelope.MAGIC) + 4 + header_length + 3] ^= 1
    elif damage == "tag":
        content[-1] ^= 1
    elif damage == "truncated":
        del content[-20:]
    else:
        content[: len(envelope.MAGIC)] = b"OTHERFMT!"
    damaged.write_bytes(content)
    touched_zip = False

    def forbidden_zip(*_args, **_kwargs):
        nonlocal touched_zip
        touched_zip = True
        raise AssertionError("ZIP must not be opened before authentication")

    monkeypatch.setattr(recovery.zipfile, "ZipFile", forbidden_zip)
    with pytest.raises(BackupError):
        restore_backup(workspace[0], damaged, private, tmp_path / "unsafe-restored")
    assert not touched_zip
    assert not (tmp_path / "unsafe-restored").exists()
    assert not list(tmp_path.glob(".stackos-recovery-*"))


def _mutated_authenticated_archive(workspace, tmp_path, mutate, *, compression=zipfile.ZIP_STORED):
    settings, _, private, public, _, _ = workspace
    good = create_backup(settings)
    key = serialization.load_pem_private_key(private.read_bytes(), password=None)
    plaintext = tmp_path / "source.zip"
    envelope.decrypt_payload(Path(good["path"]), plaintext, key)
    with zipfile.ZipFile(plaintext) as source:
        members = [(info, source.read(info)) for info in source.infolist()]
    members = mutate(members)
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as target:
        for info, content in members:
            info.compress_type = compression
            target.writestr(info, content)
    encrypted = tmp_path / "malicious.sbak"
    with encrypted.open("wb") as target:
        envelope.encrypt_payload(
            malicious, target, serialization.load_pem_public_key(public.read_bytes())
        )
    return encrypted


@pytest.mark.parametrize(
    "kind",
    [
        "traversal",
        "absolute",
        "duplicate",
        "symlink",
        "fifo",
        "missing_member",
        "bad_hash",
        "bad_size",
        "bad_db",
        "undeclared_target",
    ],
)
def test_authenticated_malicious_zip_rejected_without_partial_output(workspace, tmp_path, kind):
    def mutate(members):
        if kind in {"traversal", "absolute", "undeclared_target"}:
            name = {
                "traversal": "external/finance/../../escape",
                "absolute": "/escape",
                "undeclared_target": "external/unregistered/evil",
            }[kind]
            members.append((zipfile.ZipInfo(name), b"bad"))
        elif kind == "duplicate":
            members.append(members[0])
        elif kind in {"symlink", "fifo"}:
            info = zipfile.ZipInfo("external/finance/unsafe")
            info.external_attr = (
                (stat.S_IFLNK if kind == "symlink" else stat.S_IFIFO) | 0o600
            ) << 16
            members.append((info, b"bad"))
        elif kind == "missing_member":
            members = [
                (info, content) for info, content in members if info.filename != "state/auth.token"
            ]
        else:
            adjusted = []
            for info, content in members:
                if info.filename == "data/stackos.db" and kind == "bad_db":
                    content = b"invalid sqlite"
                if info.filename == "manifest.json":
                    manifest = json.loads(content)
                    entry = next(
                        item for item in manifest["included"] if item["path"] == "data/stackos.db"
                    )
                    if kind == "bad_hash":
                        entry["sha256"] = "0" * 64
                    elif kind == "bad_size":
                        entry["bytes"] += 1
                    else:
                        entry.update(
                            {
                                "bytes": len(b"invalid sqlite"),
                                "sha256": hashlib.sha256(b"invalid sqlite").hexdigest(),
                            }
                        )
                    content = json.dumps(manifest).encode()
                adjusted.append((info, content))
            members = adjusted
        return members

    encrypted = _mutated_authenticated_archive(workspace, tmp_path, mutate)
    with pytest.raises(BackupError):
        restore_backup(workspace[0], encrypted, workspace[2], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
    assert not (tmp_path / "escape").exists()
    assert not list(tmp_path.glob(".stackos-recovery-*"))


def test_restore_refuses_existing_live_or_empty_directory(workspace, tmp_path):
    result = create_backup(workspace[0])
    empty = tmp_path / "existing-empty"
    empty.mkdir()
    for directory in (workspace[0].state_dir, empty):
        with pytest.raises(BackupError) as error:
            restore_backup(workspace[0], Path(result["path"]), workspace[2], directory)
        assert error.value.code == "output_exists"
    assert workspace[0].seed_path.read_bytes()


def test_manual_output_cannot_share_recorded_recovery_key_folder(workspace):
    settings, _, private, _, _, _ = workspace
    with pytest.raises(BackupError) as error:
        create_backup(settings, output=private.parent / "unsafe.sbak")
    assert error.value.code == "recovery_key_capture"
    assert not (private.parent / "unsafe.sbak").exists()


def test_exclusive_publish_race_preserves_other_writers_output(workspace, tmp_path, monkeypatch):
    settings = workspace[0]
    output = tmp_path / "manual-output" / "race.sbak"
    original = service.os.link

    def racing_link(source, destination, **kwargs):
        output.write_bytes(b"other writer output")
        return original(source, destination, **kwargs)

    monkeypatch.setattr(service.os, "link", racing_link)
    with pytest.raises(BackupError):
        create_backup(settings, output=output)
    assert output.read_bytes() == b"other writer output"
    assert not list(output.parent.glob(".backup-encrypted-*"))
    _assert_clean(settings)


def test_metadata_commit_failure_preserves_previous_success(workspace, tmp_path, monkeypatch):
    settings = workspace[0]
    good = create_backup(settings)
    original = service.atomic_json
    fail_once = True

    def fail_success_record(path, value):
        nonlocal fail_once
        if (
            path.name == service.OUTCOME_FILE
            and value.get("last_success", {}).get("path") != good["path"]
            and fail_once
        ):
            fail_once = False
            raise OSError("synthetic metadata commit failure")
        return original(path, value)

    monkeypatch.setattr(service, "atomic_json", fail_success_record)
    output = tmp_path / "manual-output" / "failed-commit.sbak"
    with pytest.raises(BackupError):
        create_backup(settings, output=output)
    assert not output.exists()
    assert backup_status(settings)["last_success"] == good
    assert Path(good["path"]).exists()


@pytest.mark.parametrize("bound", ["count", "size", "ratio", "manifest"])
def test_authenticated_zip_resource_limits_are_enforced(workspace, tmp_path, monkeypatch, bound):
    if bound == "ratio":
        (workspace[1] / "large-compressible").write_bytes(b"0" * (2 * 1024 * 1024))
        path = _mutated_authenticated_archive(
            workspace, tmp_path, lambda members: members, compression=zipfile.ZIP_DEFLATED
        )
    else:
        path = Path(create_backup(workspace[0])["path"])
    if bound == "count":
        monkeypatch.setattr(limits, "MAX_MEMBERS", 1)
    elif bound == "size":
        monkeypatch.setattr(limits, "MAX_EXPANDED_BYTES", 1)
    elif bound == "manifest":
        monkeypatch.setattr(limits, "MAX_MANIFEST_BYTES", 1)
    with pytest.raises(BackupError):
        restore_backup(workspace[0], path, workspace[2], tmp_path / "bounded-restore")
    assert not (tmp_path / "bounded-restore").exists()


def test_changed_archive_is_rejected_even_after_cipher_authentication(
    workspace, tmp_path, monkeypatch
):
    result = create_backup(workspace[0])
    archive = Path(result["path"])
    original = envelope._read_header

    def changing_header(stream):
        value = original(stream)
        # Change identity/mtime without affecting the authenticated bytes being read.
        os.utime(archive, ns=(archive.stat().st_atime_ns, archive.stat().st_mtime_ns + 1_000_000))
        return value

    monkeypatch.setattr(envelope, "_read_header", changing_header)
    with pytest.raises(BackupError):
        restore_backup(workspace[0], archive, workspace[2], tmp_path / "changed-restore")
    assert not (tmp_path / "changed-restore").exists()


def test_unsafe_metadata_and_target_parent_symlink_rejected(workspace, tmp_path):
    settings, finance, _, _, _, _ = workspace
    alias = tmp_path / "alias"
    alias.symlink_to(finance, target_is_directory=True)
    with pytest.raises(BackupError):
        update_policy(
            settings,
            {},
            upsert_target=BackupTarget(id="alias", label="Alias", path=alias / "originals"),
        )
    policy_path = settings.state_dir / service.POLICY_FILE
    policy_path.unlink()
    policy_path.symlink_to(settings.token_path)
    with pytest.raises(BackupError):
        load_policy(settings)


def test_successful_backup_of_repetitive_data_is_restorable(workspace, tmp_path):
    (workspace[1] / "large-repetitive.csv").write_bytes(b"0" * (2 * 1024 * 1024))
    result = create_backup(workspace[0])
    _compare_restored(workspace, result["path"], tmp_path / "repetitive-restored")


@pytest.mark.parametrize("chunk_boundary", [False, True])
def test_matching_private_key_after_nonmatching_key_is_denied(workspace, chunk_boundary):
    settings, finance, private, _, _, _ = workspace
    other = X25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    # Place the matching PEM begin marker over a source-read boundary.
    padding = b" " * (service.CHUNK - len(other) - 10) if chunk_boundary else b""
    (finance / "private-key-bundle.pem").write_bytes(other + padding + private.read_bytes())
    with pytest.raises(BackupError) as error:
        create_backup(settings)
    assert error.value.code == "recovery_key_capture"
    _assert_clean(settings)


def test_creator_counts_directory_and_manifest_members_against_recovery_limit(
    workspace, monkeypatch
):
    settings, finance, _, _, _, _ = workspace
    monkeypatch.setattr(limits, "MAX_MEMBERS", 20)
    for number in range(12):
        (finance / f"extra-{number}.txt").write_text("synthetic")
    with pytest.raises(BackupError) as error:
        create_backup(settings)
    assert error.value.code == "payload_too_large"
    _assert_clean(settings)


@pytest.mark.parametrize("block_kind", ["oversized_whitespace", "incomplete", "nested"])
def test_private_key_scan_cannot_hide_block_with_unbounded_or_invalid_prefix(workspace, block_kind):
    settings, finance, private, _, _, _ = workspace
    content = private.read_bytes()
    if block_kind == "oversized_whitespace":
        content = content.replace(
            b"-----BEGIN PRIVATE KEY-----\n",
            b"-----BEGIN PRIVATE KEY-----\n" + b" " * (2 * service.CHUNK),
        )
    elif block_kind == "incomplete":
        content = content.split(b"-----END PRIVATE KEY-----")[0]
    else:
        content = b"-----BEGIN PRIVATE KEY-----\ninvalid outer\n" + content
    (finance / "unsafe-private-block.pem").write_bytes(content)
    with pytest.raises(BackupError) as error:
        create_backup(settings)
    assert error.value.code == "recovery_key_capture"
    _assert_clean(settings)


def test_shared_member_limit_boundary_produces_restorable_archive(workspace, tmp_path, monkeypatch):
    created = create_backup(workspace[0])
    key = serialization.load_pem_private_key(workspace[2].read_bytes(), password=None)
    plaintext = tmp_path / "counted.zip"
    envelope.decrypt_payload(Path(created["path"]), plaintext, key)
    with zipfile.ZipFile(plaintext) as archive:
        count = len(archive.infolist())
    monkeypatch.setattr(limits, "MAX_MEMBERS", count)
    at_limit = create_backup(workspace[0])
    _compare_restored(workspace, at_limit["path"], tmp_path / "member-limit-restored")
    monkeypatch.setattr(limits, "MAX_MEMBERS", count - 1)
    with pytest.raises(BackupError) as error:
        create_backup(workspace[0])
    assert error.value.code == "payload_too_large"


@pytest.mark.parametrize("bound", ["expanded", "manifest", "encrypted_payload"])
def test_producer_enforces_shared_recovery_byte_limits(workspace, monkeypatch, bound):
    name = {
        "expanded": "MAX_EXPANDED_BYTES",
        "manifest": "MAX_MANIFEST_BYTES",
        "encrypted_payload": "MAX_ARCHIVE_BYTES",
    }[bound]
    monkeypatch.setattr(limits, name, 1)
    with pytest.raises(BackupError) as error:
        create_backup(workspace[0])
    assert error.value.code == "payload_too_large"
    _assert_clean(workspace[0])


@pytest.mark.parametrize("location", ["public", "data", "state"])
def test_recovery_rejects_protected_output_before_plaintext_staging(
    workspace, monkeypatch, location
):
    settings, _, private, *_ = workspace
    archive = Path(create_backup(settings)["path"])
    root = {
        "public": settings.generated_assets_dir,
        "data": settings.data_dir,
        "state": settings.state_dir,
    }[location]
    output = root / "fresh-recovery"

    def forbidden_staging(*args, **kwargs):
        pytest.fail("Protected recovery output must be rejected before plaintext staging")

    monkeypatch.setattr(recovery.tempfile, "TemporaryDirectory", forbidden_staging)
    with pytest.raises(BackupError) as caught:
        restore_backup(settings, archive, private, output)
    assert caught.value.code == "unsafe_destination"
    assert not output.exists()


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_unsupported_member_names_preserve_last_scheduled_recovery(workspace, tmp_path, kind):
    settings, finance, private, *_ = workspace
    update_policy(settings, {"enabled": True, "interval_hours": 1, "keep_last": 1})
    now = datetime.now(UTC)
    first = run_due_backup(settings, now=now)
    unsupported = finance / "report\\2026.csv"
    if kind == "file":
        unsupported.write_text("synthetic")
    else:
        unsupported.mkdir()
    second = run_due_backup(settings, now=now + timedelta(hours=2))
    assert second["status"] == "failed"
    assert second["error"]["code"] == "unsafe_file"
    assert Path(first["path"]).exists()
    assert backup_status(settings)["last_success"]["path"] == first["path"]
    assert list(Path(first["path"]).parent.glob("*.sbak")) == [Path(first["path"])]
    assert restore_backup(settings, Path(first["path"]), private, tmp_path / "last-good")[
        "verified"
    ]
    _assert_clean(settings)


@pytest.mark.parametrize("kind", ["source", "destination", "lock"])
def test_pure_disable_persists_despite_unsafe_coverage(workspace, kind):
    settings, finance, _, _, policy, _ = workspace
    destination = policy.destination
    destination.mkdir(parents=True, exist_ok=True)
    update_policy(settings, {"enabled": True})
    path = {"source": finance, "destination": destination, "lock": finance / ".writer.lock"}[kind]
    moved = path.with_name(path.name + "-moved")
    path.rename(moved)
    path.symlink_to(moved, target_is_directory=kind != "lock")
    with pytest.raises(BackupError) as caught:
        update_policy(settings, {"enabled": False, "keep_last": 2})
    assert caught.value.code == "unsafe_path"
    assert update_policy(settings, {"enabled": False}).enabled is False
    assert load_policy(settings).enabled is False
    assert backup_status(settings)["enabled"] is False
    assert run_due_backup(settings) == {"status": "skipped", "reason": "disabled"}
    with pytest.raises(BackupError):
        update_policy(settings, {"enabled": True})
    path.unlink()
    moved.rename(path)
    assert load_policy(settings).enabled is False
    assert run_due_backup(settings) == {"status": "skipped", "reason": "disabled"}


@pytest.mark.parametrize("operation", ["restore", "invalid_restore", "backup", "keygen"])
def test_existing_output_parents_preserve_permissions(workspace, tmp_path, operation):
    settings, _, private, *_ = workspace
    parent = tmp_path / "shared-parent"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    sibling = parent / "unrelated.txt"
    sibling.write_text("unrelated")
    if operation in {"restore", "invalid_restore"}:
        archive = Path(create_backup(settings)["path"])
        if operation == "invalid_restore":
            archive = tmp_path / "invalid.sbak"
            archive.write_bytes(b"synthetic invalid archive")
            with pytest.raises(BackupError):
                restore_backup(settings, archive, private, parent / "recovered")
        else:
            restore_backup(settings, archive, private, parent / "recovered")
    elif operation == "backup":
        create_backup(settings, output=parent / "manual.sbak")
    else:
        generate_recovery_key(settings, parent / "private.pem", parent / "public.pem")
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert sibling.read_text() == "unrelated"


def test_new_output_ancestors_are_private(workspace, tmp_path):
    parent = tmp_path / "new-ancestor" / "new-parent"
    create_backup(workspace[0], output=parent / "manual.sbak")
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((parent / "manual.sbak").stat().st_mode) == 0o600


@pytest.mark.parametrize("operation", ["destination", "backup", "keygen", "restore"])
def test_case_aliases_cannot_bypass_backup_path_boundaries(workspace, tmp_path, operation):
    settings, _, private, *_ = workspace
    settings.generated_assets_dir.mkdir(exist_ok=True)
    alias = settings.data_dir.with_name("DATA") / "GENERATED-ASSETS"
    if alias.exists():
        assert os.path.samefile(alias, settings.generated_assets_dir)
    with pytest.raises(BackupError) as caught:
        if operation == "destination":
            update_policy(settings, {"destination": str(alias / "backups")})
        elif operation == "backup":
            create_backup(settings, output=alias / "manual.sbak")
        elif operation == "keygen":
            generate_recovery_key(settings, alias / "private.pem", tmp_path / "other-public.pem")
        else:
            archive = Path(create_backup(settings)["path"])
            restore_backup(settings, archive, private, alias / "recovered")
    expected = "recovery_key_capture" if operation == "keygen" else "unsafe_destination"
    assert caught.value.code == expected
    assert not (alias / "private.pem").exists()
    assert not (alias / "recovered").exists()


@pytest.mark.parametrize(
    ("root_name", "alias_name"),
    [
        ("caf\u00e9", "cafe\u0301"),
        ("cafe\u0301", "caf\u00e9"),
        ("CAF\u00c9", "cafe\u0301"),
        ("\u1e0a\u0323", "d\u0323\u0307"),
    ],
)
def test_backup_containment_compares_canonical_unicode_components(root_name, alias_name):
    root = Path("/synthetic") / root_name
    alias = Path("/synthetic") / alias_name
    assert inside(alias, root)
    assert inside(alias / "fresh-output", root)
    assert overlap(root, alias / "fresh-output")
    assert overlap(alias / "fresh-output", root)
    assert not inside(alias.with_name(alias_name + "-sibling"), root)
    assert not inside(Path("/synthetic/cafe"), root)


@pytest.mark.parametrize("operation", ["destination", "keygen", "restore"])
def test_unicode_filesystem_aliases_cannot_enter_protected_roots(
    workspace, tmp_path, monkeypatch, operation
):
    original_settings, _, private, *_ = workspace
    archive = Path(create_backup(original_settings)["path"])
    settings = original_settings.model_copy(update={"data_dir": tmp_path / "caf\u00e9"})
    settings.generated_assets_dir.mkdir(parents=True)
    alias = tmp_path / "cafe\u0301" / "generated-assets"
    if not alias.exists() or not os.path.samefile(alias, settings.generated_assets_dir):
        pytest.skip("The test filesystem distinguishes canonical Unicode spellings")

    def forbidden_staging(*args, **kwargs):
        pytest.fail("Protected recovery output must be rejected before plaintext staging")

    monkeypatch.setattr(recovery.tempfile, "TemporaryDirectory", forbidden_staging)
    with pytest.raises(BackupError) as caught:
        if operation == "destination":
            update_policy(settings, {"destination": str(alias / "backups")})
        elif operation == "keygen":
            generate_recovery_key(settings, alias / "private.pem", tmp_path / "public.pem")
        else:
            restore_backup(settings, archive, private, alias / "recovered")
    expected = "recovery_key_capture" if operation == "keygen" else "unsafe_destination"
    assert caught.value.code == expected
    assert list(settings.generated_assets_dir.iterdir()) == []


def test_maximum_policy_history_stays_readable_and_prunes_verified_archives(workspace, tmp_path):
    from stackos.backups.filesystem import MAX_JSON, atomic_json, read_json

    settings, _, private, *_ = workspace
    targets = []
    for index in range(100):
        folder = tmp_path / f"source-{index}"
        folder.mkdir()
        targets.append(BackupTarget(id=f"{index:03d}" + "x" * 61, label="Synthetic", path=folder))
    update_policy(
        settings,
        {
            "enabled": True,
            "interval_hours": 1,
            "keep_last": 365,
            "targets": [target.model_dump(mode="json") for target in targets],
        },
    )
    now = datetime.now(UTC)
    first = run_due_backup(settings, now=now)
    archive = Path(first["path"])
    history = [dict(first)]
    for index in range(364):
        path = archive.parent / f"stackos-auto-history-{index:03d}.sbak"
        os.link(archive, path)
        history.append({**first, "path": str(path)})
    # Recreate the previous full-result format at the maximum accepted policy.
    outcome_path = settings.state_dir / service.OUTCOME_FILE
    outcome = read_json(outcome_path, {})
    outcome["scheduled_archives"] = history
    atomic_json(outcome_path, outcome)
    assert outcome_path.stat().st_size > MAX_JSON
    assert backup_status(settings)["last_success"]["target_ids"] == [
        target.id for target in targets
    ]
    second = run_due_backup(settings, now=now + timedelta(hours=2))
    assert second["status"] == "success"
    assert not archive.exists()
    assert len(list(archive.parent.glob("*.sbak"))) == 365
    assert outcome_path.stat().st_size < MAX_JSON
    compact = read_json(outcome_path, {})["scheduled_archives"]
    assert len(compact) == 365
    assert all("target_ids" not in entry for entry in compact)
    third = run_due_backup(settings, now=now + timedelta(hours=4))
    assert third["status"] == "success"
    assert len(list(archive.parent.glob("*.sbak"))) == 365
    assert restore_backup(settings, Path(third["path"]), private, tmp_path / "maximum-restored")[
        "verified"
    ]
    assert backup_status(settings)["last_success"]["path"] == third["path"]


def test_history_is_bounded_when_old_destinations_cannot_be_pruned(workspace, tmp_path):
    from stackos.backups.filesystem import atomic_json, read_json

    settings = workspace[0]
    update_policy(settings, {"enabled": True, "interval_hours": 1, "keep_last": 365})
    now = datetime.now(UTC)
    first = run_due_backup(settings, now=now)
    original = Path(first["path"])
    outcome_path = settings.state_dir / service.OUTCOME_FILE
    outcome = read_json(outcome_path, {})
    outcome["scheduled_archives"] = [
        {**first, "path": str(original.parent / f"stackos-auto-{index}.sbak")}
        for index in range(400)
    ]
    atomic_json(outcome_path, outcome)
    update_policy(settings, {"destination": str(tmp_path / "new-destination")})
    result = run_due_backup(settings, now=now + timedelta(hours=2))
    assert result["status"] == "success"
    assert original.exists()
    assert len(read_json(outcome_path, {})["scheduled_archives"]) <= 366
    assert backup_status(settings)["last_success"]["path"] == result["path"]
