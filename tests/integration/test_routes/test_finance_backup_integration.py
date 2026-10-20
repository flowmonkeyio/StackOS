"""Finance and backup administration retain independent local console authority."""

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos.backups import (
    BackupPolicy,
    create_backup,
    generate_recovery_key,
    restore_backup,
    save_policy,
)
from stackos.config import Settings
from stackos.plugins.builtin_finance import helpers
from stackos.repositories.projects import ProjectRepository
from stackos.server import create_app
from tests.helpers.finance_workspace import record
from tests.unit.test_finance_management import QUERY, fixture_document


def test_browser_can_inspect_finance_and_configure_backup_independently(api, project_id, settings):
    enabled = api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable")
    assert enabled.status_code == 200, enabled.text
    ui = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}

    def call(name, arguments):
        return api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"response_mode": "raw", **arguments}},
            headers=ui,
        )

    selected = call(
        "finance.status",
        {"project_id": project_id},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["data"]["access"][0]["generation"] == 1
    master = settings.data_dir / "projects" / str(project_id) / "finance/finance.json"
    original = master.read_bytes()
    assert selected.headers["Cache-Control"] == "no-store"
    assert selected.headers["X-Content-Type-Options"] == "nosniff"

    before = call("backup.status", {})
    assert before.status_code == 200, before.text
    assert before.json()["targets"] == []
    assert before.json()["project_ids"] == [project_id]
    configured = call("backup.configure", {"enabled": False, "interval_hours": 24})
    assert configured.status_code == 200, configured.text
    assert configured.json()["data"]["interval_hours"] == 24
    assert configured.json()["data"]["targets"] == []

    finance = call(
        "finance.status",
        {"project_id": project_id},
    )
    assert finance.status_code == 200, finance.text
    assert finance.json()["data"]["readiness"] == "ready"
    assert finance.json()["data"]["access"][0]["generation"] == 1
    assert finance.headers["Cache-Control"] == "no-store"
    assert finance.headers["X-Content-Type-Options"] == "nosniff"
    assert master.read_bytes() == original
    assert call("backup.status", {}).json()["targets"] == []

    unrelated = call(
        "resource.upsert",
        {"project_id": project_id, "resource_type": "note", "key": "blocked"},
    )
    assert unrelated.status_code == 403, unrelated.text


def test_two_projects_finance_roundtrip_and_restored_incarnation_are_isolated(
    api, project_id, settings, tmp_path
):
    second = api.post(
        "/api/v1/projects",
        json={"name": "Finance Two", "slug": "finance-two", "domain": "finance-two.test"},
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["data"]["id"]
    project_ids = (project_id, second_id)
    module = helpers()
    originals = {}
    access = {}

    def call(client, name, arguments):
        bootstrap = client.get("/api/v1/auth/ui-token")
        assert bootstrap.status_code == 200, bootstrap.text
        token = bootstrap.json()["token"]
        return client.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"response_mode": "raw", **arguments}},
            headers={"Authorization": "Bearer " + token},
        )

    for pid in project_ids:
        assert api.post(f"/api/v1/projects/{pid}/plugins/finance/enable").status_code == 200
        initialized = call(api, "finance.status", {"project_id": pid})
        assert initialized.status_code == 200, initialized.text
        access[pid] = initialized.json()["data"]["access"][0]
        master = settings.data_dir / "projects" / str(pid) / "finance/finance.json"
        document = fixture_document()
        document["bookkeeping"].append(
            record(
                f"book:owned:{pid}",
                "prepared/unposted",
                date="2026-09-04",
                amount={"amount_minor": pid * 2345, "currency": "USD"},
                source_refs=["source:bank"],
                category={"state": "unresolved"},
            )
        )
        for kind in ("invoice", "receipt"):
            relative = f"attachments/2026/09/project-{pid}-{kind}.pdf"
            payload = f"%PDF-1.4 synthetic {kind} owned by project {pid}".encode()
            path = master.parent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            ref = f"attachment:{kind}:{pid}"
            document["attachments"].append(
                record(
                    ref,
                    "retained",
                    source_ref="source:bank",
                    role="original",
                    relative_path=relative,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    bytes=len(payload),
                    media_type="application/pdf",
                    original_filename=f"Project {pid} {kind}.pdf",
                )
            )
            document["sources"][0].setdefault("attachment_refs", []).append(ref)
        document["revision"] = 1
        module.workspace.write_document(master, document, module.workspace.digest(master))
        originals[pid] = {
            file.relative_to(master.parent).as_posix(): file.read_bytes()
            for file in master.parent.rglob("*")
            if file.is_file() and file.name != ".writer.lock"
        }
        common = {
            "project_id": pid,
            "workspace_ref": access[pid]["workspace_ref"],
            "access_generation": 1,
        }
        projected = call(api, "finance.query", {**common, **QUERY})
        assert projected.status_code == 200, projected.text
        rows = projected.json()["rows"]
        assert f"book:owned:{pid}" in str(rows)
        assert f"book:owned:{second_id if pid == project_id else project_id}" not in str(rows)

    # Neither guessing another source identity nor crossing it with one's project works.
    denied = call(
        api,
        "finance.query",
        {
            "project_id": project_id,
            "workspace_ref": access[second_id]["workspace_ref"],
            "access_generation": 1,
            **QUERY,
        },
    )
    assert denied.status_code == 403
    private, public = tmp_path / "keys/private.pem", tmp_path / "keys/public.pem"
    generate_recovery_key(settings, private, public)
    save_policy(settings, BackupPolicy(recipient_public_key=public.read_text()))
    archive = Path(create_backup(settings)["path"])
    recovered = tmp_path / "recovered-finance"
    outcome = restore_backup(settings, archive, private, recovered)
    assert outcome["project_ids"] == sorted(project_ids)
    assert outcome["target_ids"] == []
    for pid in project_ids:
        directory = recovered / "data/projects" / str(pid) / "finance"
        assert {
            file.relative_to(directory).as_posix(): file.read_bytes()
            for file in directory.rglob("*")
            if file.is_file() and file.name != ".writer.lock"
        } == originals[pid]
    recovered_settings = Settings(
        _env_file=None,
        data_dir=recovered / "data",
        state_dir=recovered / "state",
    )
    with TestClient(create_app(recovered_settings), base_url="http://127.0.0.1:5180") as client:
        for pid in project_ids:
            ready = call(client, "finance.status", {"project_id": pid})
            assert ready.status_code == 200, ready.text
            entry = ready.json()["data"]["access"][0]
            assert entry["project_id"] == pid and entry["generation"] == 2
            stale = call(
                client,
                "finance.query",
                {
                    "project_id": pid,
                    "workspace_ref": entry["workspace_ref"],
                    "access_generation": 1,
                    **QUERY,
                },
            )
            assert stale.status_code == 403
            current = call(
                client,
                "finance.query",
                {
                    "project_id": pid,
                    "workspace_ref": entry["workspace_ref"],
                    "access_generation": entry["generation"],
                    **QUERY,
                },
            )
            assert current.status_code == 200, current.text
            assert f"book:owned:{pid}" in str(current.json()["rows"])
            directory = recovered / "data/projects" / str(pid) / "finance"
            assert {
                file.relative_to(directory).as_posix(): file.read_bytes()
                for file in directory.rglob("*")
                if file.is_file() and file.name != ".writer.lock"
            } == originals[pid]


@pytest.mark.parametrize("retained", ["deleted", "plugin-disabled"])
def test_retained_finance_can_be_backed_up_again_after_inert_restore(
    api, project_id, settings, tmp_path, retained
):
    def status(client):
        token = client.get("/api/v1/auth/ui-token").json()["token"]
        return client.post(
            "/api/v1/operations/finance.status/call",
            json={"arguments": {"project_id": project_id}},
            headers={"Authorization": "Bearer " + token},
        )

    assert api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable").status_code == 200
    assert status(api).status_code == 200
    root = settings.data_dir / "projects" / str(project_id) / "finance"
    master = root / "finance.json"
    document = fixture_document()
    document["revision"] = 1
    helpers().workspace.write_document(master, document, helpers().workspace.digest(master))
    original = master.read_bytes()
    if retained == "deleted":
        with Session(api.app.state.engine) as session:
            ProjectRepository(session).delete(project_id, hard=True)
        expected_denial = 404
    else:
        assert api.post(f"/api/v1/projects/{project_id}/plugins/finance/disable").status_code == 200
        expected_denial = 403

    private, public = tmp_path / "keys/private.pem", tmp_path / "keys/public.pem"
    generate_recovery_key(settings, private, public)
    save_policy(settings, BackupPolicy(recipient_public_key=public.read_text()))
    first = Path(create_backup(settings)["path"])
    output = tmp_path / "retained-recovery"
    assert restore_backup(settings, first, private, output)["project_ids"] == [project_id]
    adopted = Settings(_env_file=None, data_dir=output / "data", state_dir=output / "state")
    with TestClient(create_app(adopted), base_url="http://127.0.0.1:5180") as client:
        assert status(client).status_code == expected_denial
        save_policy(adopted, BackupPolicy(recipient_public_key=public.read_text()))
        second = Path(create_backup(adopted)["path"])
    second_output = tmp_path / "retained-second-recovery"
    result = restore_backup(adopted, second, private, second_output)
    assert result["project_ids"] == [project_id]
    assert (
        second_output / "data/projects" / str(project_id) / "finance/finance.json"
    ).read_bytes() == original
