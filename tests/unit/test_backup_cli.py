"""Local backup CLI regression and recovery proof, using synthetic records."""

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
