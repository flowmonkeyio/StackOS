"""Actual REST/console auth, coverage preservation and private output boundary."""

from pathlib import Path

from stackos import backups
from stackos.auth import derive_ui_token


def call(api, name, arguments=None, headers=None):
    return api.post(
        f"/api/v1/operations/{name}/call",
        json={"arguments": {"response_mode": "raw", **(arguments or {})}},
        headers=headers,
    )


def test_local_console_configuration_preserves_enrolled_targets(api, settings, tmp_path):
    private, public = tmp_path / "keys/recovery.pem", tmp_path / "keys/public.pem"
    backups.generate_recovery_key(settings, private, public)
    finance = tmp_path / "finance"
    finance.mkdir()
    (finance / "finance.json").write_text('{"synthetic":true}')
    (finance / "original.pdf").write_bytes(b"synthetic invoice")
    lock = finance / ".writer.lock"
    lock.touch()
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    response = call(
        api,
        "backup.configure",
        {
            "recipient_public_key": public.read_text(),
            "targets": [
                {
                    "id": "finance",
                    "label": "Business bills",
                    "path": str(finance),
                    "lock_file": str(lock),
                }
            ],
        },
        ui,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["enabled"] is False
    for enabled in (True, False):
        response = call(api, "backup.configure", {"enabled": enabled}, ui)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["targets"][0]["id"] == "finance"
    response = call(api, "backup.create", {}, ui)
    assert response.status_code == 200, response.text
    assert "PRIVATE KEY" not in response.text
    recovered = tmp_path / "recovered"
    backups.restore_backup(settings, Path(response.json()["data"]["path"]), private, recovered)
    assert (recovered / "external/finance/original.pdf").read_bytes() == b"synthetic invoice"
    assert call(api, "backup.status", {}, ui).json()["last_success"]
    assert call(api, "backup.configure", {"enabled": True}, ui).status_code == 200
    moved = finance.with_name("moved-finance")
    finance.rename(moved)
    finance.symlink_to(moved, target_is_directory=True)
    disabled = call(api, "backup.configure", {"enabled": False}, ui)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"]["enabled"] is False
    assert disabled.json()["data"]["ready"] is False
    assert disabled.json()["data"]["targets"][0]["id"] == "finance"
    assert backups.load_policy(settings).enabled is False
    assert backups.run_due_backup(settings) == {"status": "skipped", "reason": "disabled"}


def test_backup_controls_reject_absent_forged_run_and_secret_inputs(api, settings):
    for auth in ("", "Bearer forged"):
        assert call(api, "backup.status", {}, {"Authorization": auth}).status_code == 401
    assert (
        call(api, "backup.configure", {"run_token": "not-a-run", "enabled": True}).status_code
        == 403
    )
    response = call(api, "backup.configure", {"private_key": "synthetic-private-key"})
    assert response.status_code == 422
    assert "synthetic-private-key" not in response.text
    assert call(api, "backup.configure", {"exclusion_paths": []}).status_code == 422
    assert backups.backup_status(settings)["enabled"] is False


def test_public_mount_output_and_private_recovery_key_are_rejected(api, settings, tmp_path):
    private, public = tmp_path / "keys/recovery.pem", tmp_path / "keys/public.pem"
    backups.generate_recovery_key(settings, private, public)
    assert (
        call(api, "backup.configure", {"recipient_public_key": public.read_text()}).status_code
        == 200
    )
    response = call(
        api, "backup.configure", {"destination": str(settings.generated_assets_dir / "backups")}
    )
    assert response.status_code == 422
    response = call(
        api, "backup.create", {"output": str(settings.generated_assets_dir / "leak.sobak")}
    )
    assert response.status_code == 422
    assert not (settings.generated_assets_dir / "leak.sobak").exists()
    response = call(api, "backup.configure", {"recipient_public_key": private.read_text()})
    assert response.status_code == 422
    assert "PRIVATE KEY" not in response.text
    response = call(
        api,
        "backup.configure",
        {"targets": [{"id": "keys", "label": "Bad custody", "path": str(private.parent)}]},
    )
    assert response.status_code == 422
