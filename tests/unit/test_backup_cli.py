"""Local backup CLI regression and recovery proof, using synthetic records."""

import json
import sqlite3

from typer.testing import CliRunner

from stackos import backups
from stackos.cli import app
from stackos.config import Settings


def test_backup_without_recipient_rejects_before_writing_plaintext(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path / "state"))
    settings = Settings()
    settings.ensure_dirs()
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("CREATE TABLE probe (value TEXT)")
        conn.execute("INSERT INTO probe VALUES ('synthetic finance')")
    settings.seed_path.write_bytes(b"s" * 32)
    settings.token_path.write_text("synthetic-token")
    output = tmp_path / "backup.stackos-backup"
    result = CliRunner().invoke(app, ["backup", "--output", str(output)])
    assert result.exit_code != 0
    assert not output.exists()


def test_local_cli_configuration_and_finance_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path / "state"))
    settings = Settings()
    settings.ensure_dirs()
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("CREATE TABLE probe (value TEXT)")
        conn.execute("INSERT INTO probe VALUES ('synthetic record')")
    settings.seed_path.write_bytes(b"s" * 32)
    settings.token_path.write_text("synthetic-token")
    finance = tmp_path / "finance"
    finance.mkdir()
    (finance / "finance.json").write_bytes(b'{"gross":"99.99"}')
    (finance / "original.pdf").write_bytes(b"synthetic original invoice")
    (finance / ".writer.lock").touch()
    private, public = tmp_path / "keys/recovery.pem", tmp_path / "keys/recipient.pem"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "backups",
            "keygen",
            "--private-key-file",
            str(private),
            "--public-key-file",
            str(public),
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        [
            "backups",
            "configure",
            "--public-key-file",
            str(public),
            "--target-id",
            "finance",
            "--target-path",
            str(finance),
            "--target-label",
            "Business records",
            "--target-lock",
            str(finance / ".writer.lock"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert backups.load_policy(settings).enabled is False
    for flag in ("--enabled", "--disabled"):
        result = runner.invoke(app, ["backups", "configure", flag, "--keep-last", "2"])
        assert result.exit_code == 0, result.output
        current = backups.load_policy(settings)
        assert current.enabled is (flag == "--enabled")
        assert [target.id for target in current.targets] == ["finance"]
        assert current.recipient_public_key
    output = tmp_path / "archives/manual.sobak"
    result = runner.invoke(app, ["backup", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert b"synthetic-token" not in output.read_bytes()
    assert b"synthetic original invoice" not in output.read_bytes()
    restored = tmp_path / "recovery"
    result = runner.invoke(
        app,
        [
            "restore",
            str(output),
            "--private-key-file",
            str(private),
            "--output-dir",
            str(restored),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (restored / "external/finance/finance.json").read_bytes() == (
        finance / "finance.json"
    ).read_bytes()
    assert (restored / "external/finance/original.pdf").read_bytes() == (
        finance / "original.pdf"
    ).read_bytes()
    assert (restored / "state/seed.bin").read_bytes() == b"s" * 32
    public_output = settings.generated_assets_dir / "unsafe-recovery"
    rejected = runner.invoke(
        app,
        [
            "restore",
            str(output),
            "--private-key-file",
            str(private),
            "--output-dir",
            str(public_output),
        ],
    )
    assert rejected.exit_code != 0
    assert "unsafe_destination" in rejected.output
    assert not public_output.exists()
    wrong_private, wrong_public = (
        tmp_path / "wrong-keys/recovery.pem",
        tmp_path / "wrong-keys/public.pem",
    )
    backups.generate_recovery_key(settings, wrong_private, wrong_public)
    wrong_output = tmp_path / "wrong-recovery"
    result = runner.invoke(
        app,
        [
            "restore",
            str(output),
            "--private-key-file",
            str(wrong_private),
            "--output-dir",
            str(wrong_output),
        ],
    )
    assert result.exit_code != 0
    assert not wrong_output.exists()
    result = runner.invoke(app, ["backup", "--output", str(output)])
    assert result.exit_code != 0
    result = runner.invoke(app, ["backups", "status"])
    assert result.exit_code == 0
    assert "PRIVATE KEY" not in result.output
    assert runner.invoke(app, ["backups", "configure", "--enabled"]).exit_code == 0
    moved = finance.with_name("moved-finance")
    finance.rename(moved)
    finance.symlink_to(moved, target_is_directory=True)
    disabled = runner.invoke(app, ["backups", "configure", "--disabled"])
    assert disabled.exit_code == 0, disabled.output
    assert backups.load_policy(settings).enabled is False


def test_cli_target_registration_upserts_without_replacing_other_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path / "state"))
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    runner = CliRunner()
    for name, path in (("first", first), ("second", second), ("first", first)):
        result = runner.invoke(
            app,
            [
                "backups",
                "configure",
                "--target-id",
                name,
                "--target-path",
                str(path),
                "--target-label",
                f"{name} records",
            ],
        )
        assert result.exit_code == 0, result.output
    assert {item.id for item in backups.load_policy(Settings()).targets} == {"first", "second"}


def test_guarded_local_cli_setup_exports_and_inert_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path / "state"))
    settings = Settings()
    settings.ensure_dirs()
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('synthetic local setup')")
    settings.seed_path.write_bytes(b"s" * 32)
    settings.token_path.write_text("synthetic-token")
    runner = CliRunner()
    private, public = tmp_path / "portable/key.pem", tmp_path / "native/public.pem"
    cache, destination = tmp_path / "native/key.enc", tmp_path / "archives"

    def invoke(*args):
        result = runner.invoke(app, [str(arg) for arg in args])
        assert result.exit_code == 0, result.output
        assert "PRIVATE KEY" not in result.output
        assert "synthetic-token" not in result.output
        return json.loads(result.output)

    invoke(
        "backups",
        "configure",
        "--interval-hours",
        8,
        "--keep-last",
        19,
        "--destination",
        destination,
    )
    before = (settings.state_dir / "backup-policy.json").read_bytes()
    result = invoke(
        "backups",
        "custody",
        "--first-time",
        "--protected-path",
        cache.parent,
        "--destination",
        destination,
    )
    assert result["reserved"] is False
    assert not (settings.state_dir / "backup-key-exclusions.json").exists()
    result = invoke(
        "backups",
        "keygen",
        "--first-time",
        "--private-key-file",
        private,
        "--public-key-file",
        public,
        "--protected-path",
        cache.parent,
        "--destination",
        destination,
    )
    recipient = result["recipient_fingerprint"]
    # The native owner writes OS-encrypted bytes only after this durable reservation.
    cache.write_bytes(b"synthetic protected copy")
    assert (settings.state_dir / "backup-policy.json").read_bytes() == before
    result = invoke(
        "backups",
        "initialize",
        "--public-key-file",
        public,
        "--private-key-file",
        private,
        "--protected-key-file",
        cache,
        "--destination",
        destination,
    )
    assert result["configured"] and result["enabled"]
    assert result["interval_hours"] == 8 and result["keep_last"] == 19
    policy_bytes = (settings.state_dir / "backup-policy.json").read_bytes()
    rejected = runner.invoke(
        app,
        [
            "backups",
            "initialize",
            "--public-key-file",
            str(public),
            "--private-key-file",
            str(private),
            "--protected-key-file",
            str(cache),
        ],
    )
    assert rejected.exit_code == 1 and "already_configured" in rejected.output
    assert (settings.state_dir / "backup-policy.json").read_bytes() == policy_bytes
    invoke("backup")
    exported = tmp_path / "off-device/archive.sbak"
    invoke("backups", "export", "--output", exported)
    recovery_copy = tmp_path / "separate-key/key.pem"
    invoke(
        "backups",
        "export-key",
        "--private-key-file",
        private,
        "--output",
        recovery_copy,
        "--expected-recipient",
        recipient,
    )
    assert recovery_copy.read_bytes() == private.read_bytes()
    invoke(
        "restore",
        exported,
        "--private-key-file",
        recovery_copy,
        "--output-dir",
        tmp_path / "restored",
    )
    assert (tmp_path / "restored/state/seed.bin").read_bytes() == b"s" * 32
    assert (settings.state_dir / "backup-policy.json").read_bytes() == policy_bytes
    assert not (tmp_path / "restored/state/backup-policy.json").exists()


def test_private_export_cli_never_outputs_key_bytes_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path / "state"))
    private, public = tmp_path / "keys/private.pem", tmp_path / "keys/public.pem"
    backups.generate_recovery_key(Settings(), private, public)
    backups.update_policy(Settings(), {"recipient_public_key": public.read_text()})
    private.write_text("synthetic-private-data-that-must-not-leak")
    result = CliRunner().invoke(
        app,
        [
            "backups",
            "export-key",
            "--private-key-file",
            str(private),
            "--output",
            str(tmp_path / "copy.pem"),
        ],
    )
    assert result.exit_code == 1
    assert "invalid_private_key" in result.output
    assert "synthetic-private-data" not in result.output
    assert not (tmp_path / "copy.pem").exists()
