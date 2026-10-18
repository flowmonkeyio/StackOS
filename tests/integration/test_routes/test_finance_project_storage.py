"""Synthetic managed Finance initialization, custody and migration contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos.config import Settings
from stackos.plugins import builtin_finance
from stackos.plugins.builtin_finance import FinanceConsole, helpers
from stackos.repositories.base import RepositoryError
from tests.helpers.finance_workspace import record
from tests.unit.test_finance_management import QUERY, fixture_document


def call(api, name, arguments):
    ui = api.get("/api/v1/auth/ui-token").json()["token"]
    return api.post(
        f"/api/v1/operations/{name}/call",
        json={"arguments": arguments},
        headers={"Authorization": f"Bearer {ui}"},
    )


def ready(api, project_id):
    assert api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable").status_code == 200
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 200, response.text
    return response.json()["data"]["access"][0]


def common(project_id, access):
    return {
        "project_id": project_id,
        "workspace_ref": access["workspace_ref"],
        "access_generation": access["generation"],
    }


def _original(root, state="retained"):
    original = root / "attachments/2026/10/synthetic-retained.txt"
    original.parent.mkdir(parents=True, exist_ok=True)
    payload = b"synthetic original evidence"
    original.write_bytes(payload)
    document = fixture_document()
    document["revision"] = 1
    document["attachments"].append(
        record(
            "attachment:repair-proof",
            "retained" if state == "quarantine-ref" else state,
            source_ref="source:bank",
            role="original",
            relative_path=original.relative_to(root).as_posix(),
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            media_type="text/plain",
            original_filename="synthetic-retained.txt",
        )
    )
    if state == "quarantine-ref":
        document["attachments"][-1].pop("relative_path")
        document["attachments"][-1]["quarantine_ref"] = "quarantine:synthetic"
    document["sources"][0]["attachment_refs"] = ["attachment:repair-proof"]
    master = root / "finance.json"
    helpers().workspace.write_document(master, document, helpers().workspace.digest(master))
    return original


@pytest.fixture
def legacy(api, project_id, settings, tmp_path):
    assert api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable").status_code == 200
    root = tmp_path / "legacy-finance"
    master = helpers().workspace.initialize(root)
    (root / "attachments").mkdir()
    (root / "attachments/original.txt").write_bytes(b"synthetic retained original")
    (root / "reports").mkdir()
    (root / "reports/retained.md").write_text("Synthetic historical report")
    info = root.stat()
    entry = {
        "access_id": "finance-access:legacy",
        "project_id": project_id,
        "workspace_ref": "finance:legacy",
        "backend_key": "local-json",
        "canonical_root": str(root),
        "root_device": info.st_dev,
        "root_inode": info.st_ino,
        "modes": ["read", "correct", "export"],
        "generation": 7,
        "selected_at": datetime.now(UTC).isoformat(),
    }
    config = settings.data_dir / "plugin-state/finance/access.json"
    config.parent.mkdir(parents=True)
    document = {"format": "finance-access-v1", "entries": [entry], "intents": {}}
    config.write_text(json.dumps(document))
    return {"root": root, "master": master, "config": config, "doc": document}


def test_managed_store_initializes_once_without_selection(api, project_id, settings):
    access = ready(api, project_id)
    assert access["workspace_ref"] == f"finance:project:{project_id}"
    assert access["generation"] == 1
    root = settings.data_dir / "projects" / str(project_id) / "finance"
    assert root.is_dir() and root.stat().st_mode & 0o777 == 0o700
    before = (root / "finance.json").read_bytes()
    second = call(api, "finance.status", {"project_id": project_id})
    assert second.json()["data"]["access"][0]["generation"] == 1
    queried = call(api, "finance.query", {**common(project_id, access), **QUERY})
    assert queried.status_code == 200, queried.text
    assert queried.json()["data"]["rows"] == []
    assert (root / "finance.json").read_bytes() == before
    assert (root.parent / ".writer.lock").is_file()
    assert (root / ".writer.lock").is_file()


def test_reads_and_denied_status_do_not_create_a_store(api, project_id, settings):
    api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable")
    arguments = {
        "project_id": project_id,
        "workspace_ref": f"finance:project:{project_id}",
        "access_generation": 1,
        **QUERY,
    }
    assert call(api, "finance.query", arguments).status_code == 403
    assert not (settings.data_dir / "projects").exists()
    assert call(api, "finance.status", {"project_id": project_id + 999}).status_code == 404
    api.post(f"/api/v1/projects/{project_id}/plugins/finance/disable")
    assert call(api, "finance.status", {"project_id": project_id}).status_code == 403
    assert not (settings.data_dir / "projects").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("root", "/private/synthetic"),
        ("workspace_ref", "finance:other"),
        ("modes", ["read"]),
        ("access_generation", 1),
    ],
)
def test_status_accepts_project_only(api, project_id, settings, field, value):
    api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable")
    assert call(api, "finance.status", {"project_id": project_id, field: value}).status_code == 422
    assert not (settings.data_dir / "projects").exists()


def test_project_scope_cannot_select_another_store(api, project_id, settings):
    one = ready(api, project_id)
    other = api.post(
        "/api/v1/projects", json={"slug": "second", "name": "Second", "domain": "second.example"}
    ).json()["data"]["id"]
    two = ready(api, other)
    for operation, extra in [
        ("finance.query", QUERY),
        ("finance.record.get", {"record_ref": "book:foreign"}),
        (
            "finance.evidence.read",
            {
                "attachment_ref": "attachment:foreign",
                "expected_snapshot": {"revision": 0, "sha256": "0" * 64},
            },
        ),
        ("finance.report.render", QUERY),
    ]:
        response = call(api, operation, {**common(other, one), **extra})
        assert response.status_code == 403, response.text
    assert one["workspace_ref"] != two["workspace_ref"]
    assert (settings.data_dir / "projects" / str(project_id) / "finance").stat().st_ino != (
        settings.data_dir / "projects" / str(other) / "finance"
    ).stat().st_ino


def test_legacy_intent_history_without_an_entry_cannot_bootstrap_empty(
    api, project_id, settings, legacy
):
    doc = legacy["doc"]
    doc["entries"] = []
    doc["intents"] = {f"{project_id}:prior-selection": {"digest": "0" * 64, "generation": 7}}
    legacy["config"].write_text(json.dumps(doc))
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 422 and "legacy_history_requires_recovery" in response.text
    assert not (settings.data_dir / "projects" / str(project_id) / "finance").exists()


def test_fresh_initialization_rechecks_history_under_configuration_lock(
    api, project_id, settings, monkeypatch
):
    api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable")
    original = FinanceConsole._legacy
    calls = []

    def changed(self):
        doc = original(self)
        calls.append(True)
        if len(calls) == 2:
            doc["intents"][f"{project_id}:prior-selection"] = {"digest": "0" * 64, "generation": 1}
        return doc

    monkeypatch.setattr(FinanceConsole, "_legacy", changed)
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 409, response.text
    assert not (settings.data_dir / "projects" / str(project_id) / "finance").exists()


def test_unique_legacy_store_is_copied_without_record_or_original_changes(
    api, project_id, settings, legacy
):
    before = {
        p.relative_to(legacy["root"]): p.read_bytes()
        for p in legacy["root"].rglob("*")
        if p.is_file() and p.name != ".writer.lock"
    }
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 200, response.text
    access = response.json()["data"]["access"][0]
    assert access["generation"] == 8
    target = settings.data_dir / "projects" / str(project_id) / "finance"
    for relative, content in before.items():
        assert (target / relative).read_bytes() == content
        assert (legacy["root"] / relative).read_bytes() == content
    assert json.loads(legacy["config"].read_text())["entries"][0]["retired_to_managed_project"]
    assert (
        call(api, "finance.status", {"project_id": project_id}).json()["data"]["access"][0][
            "generation"
        ]
        == 8
    )


@pytest.mark.parametrize(
    "case",
    [
        "revoked",
        "missing",
        "multiple",
        "shared",
        "overlapping",
        "hardlink",
        "symlink",
        "old_project",
        "corrupt",
    ],
)
def test_unresolved_legacy_history_never_becomes_an_empty_store(
    api, project_id, settings, legacy, case
):
    doc = legacy["doc"]
    entry = doc["entries"][0]
    if case == "revoked":
        entry["revoked_at"] = datetime.now(UTC).isoformat()
    elif case == "missing":
        entry["canonical_root"] = str(legacy["root"].with_name("unavailable"))
    elif case in {"multiple", "shared", "overlapping"}:
        other = copy.deepcopy(entry)
        other["access_id"] = "finance-access:other"
        other["workspace_ref"] = "finance:other"
        if case != "multiple":
            other["project_id"] = project_id + 999
            other["revoked_at"] = datetime.now(UTC).isoformat()
        if case == "overlapping":
            other["canonical_root"] = str(legacy["root"] / "attachments")
            other["root_inode"] = (legacy["root"] / "attachments").stat().st_ino
        doc["entries"].append(other)
    elif case == "hardlink":
        (legacy["root"] / "aliased.txt").hardlink_to(legacy["root"] / "attachments/original.txt")
    elif case == "symlink":
        (legacy["root"] / "aliased.txt").symlink_to(legacy["root"] / "attachments/original.txt")
    elif case == "old_project":
        entry["selected_at"] = "2000-01-01T00:00:00+00:00"
    elif case == "corrupt":
        legacy["master"].write_text("invalid synthetic master")
    legacy["config"].write_text(json.dumps(doc))
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code in {403, 422}, response.text
    assert not (settings.data_dir / "projects" / str(project_id) / "finance").exists()
    assert legacy["master"].exists()


def test_missing_and_unrecognized_managed_data_are_never_reinitialized(api, project_id, settings):
    ready(api, project_id)
    parent = settings.data_dir / "projects" / str(project_id)
    shutil.rmtree(parent / "finance")
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 422 and "storage_missing" in response.text
    assert not (parent / "finance").exists()
    (parent / "finance-state.json").unlink()
    (parent / "finance").mkdir()
    marker = parent / "finance/preserved.txt"
    marker.write_text("synthetic unrecognized store")
    assert call(api, "finance.status", {"project_id": project_id}).status_code == 422
    assert marker.read_text() == "synthetic unrecognized store"


def test_restored_store_rebinds_once_and_recreates_locks(api, project_id, settings, tmp_path):
    access = ready(api, project_id)
    recovered = Settings(
        data_dir=tmp_path / "recovered/data", state_dir=tmp_path / "recovered/state"
    )
    shutil.copytree(
        settings.data_dir / "projects",
        recovered.data_dir / "projects",
        ignore=shutil.ignore_patterns(".writer.lock"),
    )
    with Session(api.app.state.engine) as session:
        console = FinanceConsole(session, recovered)
        with pytest.raises(RepositoryError):
            console.authorize(SimpleNamespace(**common(project_id, access)))
        result = console.status(SimpleNamespace(project_id=project_id))
        current = result["access"][0]
        assert current["generation"] == access["generation"] + 1
        assert (
            console.status(SimpleNamespace(project_id=project_id))["access"][0]["generation"]
            == current["generation"]
        )
        with pytest.raises(RepositoryError):
            console.authorize(SimpleNamespace(**common(project_id, access)))
        console.authorize(SimpleNamespace(**common(project_id, current)))
    parent = recovered.data_dir / "projects" / str(project_id)
    assert (parent / ".writer.lock").is_file() and (parent / "finance/.writer.lock").is_file()


def test_publication_cannot_replace_even_an_empty_destination(
    api, project_id, settings, legacy, monkeypatch
):
    original = builtin_finance._publish_directory

    def collide(stage, target):
        target.mkdir()
        original(stage, target)

    monkeypatch.setattr(builtin_finance, "_publish_directory", collide)
    assert call(api, "finance.status", {"project_id": project_id}).status_code == 422
    root = settings.data_dir / "projects" / str(project_id) / "finance"
    assert list(root.iterdir()) == []
    assert legacy["master"].is_file()


def test_interrupted_adoption_reconciles_without_copying_a_second_master(
    api, project_id, settings, legacy, monkeypatch
):
    original = builtin_finance._publish_directory

    def interrupted(stage, target):
        original(stage, target)
        raise OSError("synthetic interruption after publication")

    monkeypatch.setattr(builtin_finance, "_publish_directory", interrupted)
    assert call(api, "finance.status", {"project_id": project_id}).status_code == 422
    root = settings.data_dir / "projects" / str(project_id) / "finance"
    before = (root / "finance.json").read_bytes()
    monkeypatch.setattr(builtin_finance, "_publish_directory", original)
    resumed = call(api, "finance.status", {"project_id": project_id})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["data"]["access"][0]["generation"] == 8
    assert (root / "finance.json").read_bytes() == before


@pytest.mark.parametrize("change", ["mapping", "tree"])
def test_legacy_source_and_full_mapping_stay_pinned_until_publication(
    api, project_id, settings, legacy, monkeypatch, change
):
    original = FinanceConsole._tree

    def changing(root, destination=None):
        result = original(root, destination)
        if root == legacy["root"] and destination is not None:
            if change == "mapping":
                document = json.loads(legacy["config"].read_text())
                document["entries"][0]["generation"] += 1
                legacy["config"].write_text(json.dumps(document))
            else:
                (root / "attachments/original.txt").write_bytes(b"synthetic changed source")
        return result

    monkeypatch.setattr(FinanceConsole, "_tree", staticmethod(changing))
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 409, response.text
    assert not (settings.data_dir / "projects" / str(project_id) / "finance").exists()
    assert legacy["master"].is_file()


def test_changed_legacy_source_after_interruption_requires_reconciliation(
    api, project_id, settings, legacy, monkeypatch
):
    original = builtin_finance._publish_directory

    def interrupted(stage, target):
        raise OSError("synthetic interruption before publication")

    monkeypatch.setattr(builtin_finance, "_publish_directory", interrupted)
    assert call(api, "finance.status", {"project_id": project_id}).status_code == 422
    (legacy["root"] / "reports/retained.md").write_text("Synthetic subsequent report")
    monkeypatch.setattr(builtin_finance, "_publish_directory", original)
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 409, response.text
    assert not (settings.data_dir / "projects" / str(project_id) / "finance").exists()


@pytest.mark.parametrize("lock_name", [".writer.lock", "finance/.writer.lock"])
def test_status_respects_parent_and_finance_writers(api, project_id, settings, lock_name):
    import fcntl
    import os

    ready(api, project_id)
    parent = settings.data_dir / "projects" / str(project_id)
    fd = os.open(parent / lock_name, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        response = call(api, "finance.status", {"project_id": project_id})
        assert response.status_code == 409 and "writer_busy" in response.text
    finally:
        os.close(fd)
    assert (
        call(api, "finance.status", {"project_id": project_id}).json()["data"]["access"][0][
            "generation"
        ]
        == 1
    )


def test_project_rename_and_deletion_cannot_reassign_retained_data(api, project_id, settings):
    from stackos.repositories.projects import ProjectRepository

    ready(api, project_id)
    parent = settings.data_dir / "projects" / str(project_id)
    before = (parent / "finance/finance.json").read_bytes()
    with Session(api.app.state.engine) as session:
        repo = ProjectRepository(session)
        repo.update(project_id, name="Renamed synthetic project")
    assert (
        call(api, "finance.status", {"project_id": project_id}).json()["data"]["access"][0][
            "generation"
        ]
        == 1
    )
    with Session(api.app.state.engine) as session:
        repo = ProjectRepository(session)
        repo.delete(project_id, hard=True)
        replacement = repo.create(
            slug="betsage", name="Replacement", domain="replacement.example"
        ).data.id
    assert replacement > project_id
    assert call(api, "finance.status", {"project_id": project_id}).status_code == 404
    ready(api, replacement)
    assert (parent / "finance/finance.json").read_bytes() == before
    assert (settings.data_dir / "projects" / str(replacement) / "finance").stat().st_ino != (
        parent / "finance"
    ).stat().st_ino


@pytest.mark.parametrize("boundary", ["journal", "before-rename", "after-rename", "ready-state"])
def test_publication_marker_is_durable_until_completed_reconciliation(
    api, project_id, settings, legacy, monkeypatch, boundary
):
    parent = settings.data_dir / "projects" / str(project_id)
    marker = parent / ".publication-pending"
    publish = builtin_finance._publish_directory
    save = FinanceConsole._save
    clear = FinanceConsole._clear_publication

    def stopped_publish(stage, target):
        assert marker.read_bytes() == b"pending\n"
        if boundary == "after-rename":
            publish(stage, target)
        raise OSError("synthetic publication interruption")

    def stopped_save(path, value):
        if path.name == "finance-state.json" and value["phase"] == "publishing":
            assert marker.read_bytes() == b"pending\n"
            raise OSError("synthetic journal interruption")
        save(path, value)

    def stopped_clear(self, directory, state):
        assert state["phase"] == "ready" and marker.exists()
        assert json.loads(legacy["config"].read_text())["entries"][0]["revoked_at"]
        raise OSError("synthetic interruption after ready state")

    if boundary == "journal":
        monkeypatch.setattr(FinanceConsole, "_save", staticmethod(stopped_save))
    elif boundary == "ready-state":
        monkeypatch.setattr(FinanceConsole, "_clear_publication", stopped_clear)
    else:
        monkeypatch.setattr(builtin_finance, "_publish_directory", stopped_publish)
    assert call(api, "finance.status", {"project_id": project_id}).status_code == 422
    assert marker.read_bytes() == b"pending\n"
    monkeypatch.setattr(FinanceConsole, "_save", staticmethod(save))
    monkeypatch.setattr(FinanceConsole, "_clear_publication", clear)
    monkeypatch.setattr(builtin_finance, "_publish_directory", publish)
    resumed = call(api, "finance.status", {"project_id": project_id})
    assert resumed.status_code == 200, resumed.text
    assert not marker.exists()
    assert json.loads((parent / "finance-state.json").read_text())["phase"] == "ready"


@pytest.mark.parametrize("state", ["missing", "disposed", "quarantined", "quarantine-ref"])
def test_legacy_adoption_preserves_document_availability_gaps(
    api, project_id, settings, legacy, state
):
    original = _original(legacy["root"], "retained" if state == "missing" else state)
    if state == "missing":
        original.unlink()
    before = legacy["master"].read_bytes()
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 200, response.text
    access = response.json()["data"]["access"][0]
    assert access["snapshot"]["custody_issues"][0]["state"] == (
        {"missing": "unavailable", "quarantine-ref": "retained"}.get(state, state)
    )
    root = settings.data_dir / "projects" / str(project_id) / "finance"
    assert (root / "finance.json").read_bytes() == before == legacy["master"].read_bytes()


@pytest.mark.parametrize("state", ["missing", "disposed", "quarantined", "quarantine-ref"])
def test_restore_preserves_document_gaps_without_weakening_writer_custody(
    api, project_id, settings, tmp_path, state
):
    ready(api, project_id)
    root = settings.data_dir / "projects" / str(project_id) / "finance"
    original = _original(root, "retained" if state == "missing" else state)
    if state == "missing":
        original.unlink()
    recovered = Settings(
        data_dir=tmp_path / "recovered/data", state_dir=tmp_path / "recovered/state"
    )
    shutil.copytree(
        settings.data_dir / "projects",
        recovered.data_dir / "projects",
        ignore=shutil.ignore_patterns(".writer.lock"),
    )
    with Session(api.app.state.engine) as session:
        console = FinanceConsole(session, recovered)
        result = console.status(SimpleNamespace(project_id=project_id))
        assert result["readiness"] == "ready"
        assert result["access"][0]["snapshot"]["custody_issues"][0]["state"] == (
            {"missing": "unavailable", "quarantine-ref": "retained"}.get(state, state)
        )
    if state == "missing":
        with pytest.raises(FileNotFoundError):
            helpers().workspace.read_document(root / "finance.json")


@pytest.mark.parametrize("fault", ["hash", "size", "symlink", "hardlink", "fifo"])
def test_restore_rejects_actual_original_integrity_failures(
    api, project_id, settings, tmp_path, fault
):
    ready(api, project_id)
    root = settings.data_dir / "projects" / str(project_id) / "finance"
    _original(root)
    recovered = Settings(
        data_dir=tmp_path / "recovered/data", state_dir=tmp_path / "recovered/state"
    )
    shutil.copytree(
        settings.data_dir / "projects",
        recovered.data_dir / "projects",
        ignore=shutil.ignore_patterns(".writer.lock"),
    )
    target = (
        recovered.data_dir
        / "projects"
        / str(project_id)
        / "finance/attachments/2026/10/synthetic-retained.txt"
    )
    if fault == "hash":
        target.write_bytes(b"X" * target.stat().st_size)
    elif fault == "size":
        target.write_bytes(b"synthetic truncated")
    else:
        target.unlink()
        source = root / "attachments/2026/10/synthetic-retained.txt"
        if fault == "symlink":
            target.symlink_to(source)
        elif fault == "fifo":
            import os

            os.mkfifo(target)
        else:
            target.hardlink_to(source)
    with (
        Session(api.app.state.engine) as session,
        pytest.raises(RepositoryError, match="Finance operation requires repair") as error,
    ):
        FinanceConsole(session, recovered).status(SimpleNamespace(project_id=project_id))
    assert error.value.data["finance_code"] == "storage_custody_requires_repair"


@pytest.mark.parametrize("other_project", [True, False])
def test_physical_owner_binding_blocks_foreign_project_and_incarnation_copies(
    api, project_id, settings, tmp_path, other_project, monkeypatch
):
    access = ready(api, project_id)
    parent = settings.data_dir / "projects" / str(project_id)
    root = parent / "finance"
    preserved = tmp_path / "preserved-original-store"
    root.rename(preserved)
    if other_project:
        other = api.post(
            "/api/v1/projects",
            json={"slug": "foreign", "name": "Foreign", "domain": "foreign.example"},
        ).json()["data"]["id"]
        ready(api, other)
        shutil.copytree(settings.data_dir / "projects" / str(other) / "finance", root)
    else:
        shutil.copytree(preserved, root)
        marker = root / ".finance-owner.json"
        owner = json.loads(marker.read_text())
        owner["access_id"] = "finance-access:foreign-incarnation"
        marker.write_text(json.dumps(owner))
    before = (parent / "finance-state.json").read_bytes()

    def forbidden_snapshot(*args, **kwargs):
        pytest.fail("Foreign physical store must be denied before reading financial records")

    monkeypatch.setattr(helpers(), "read_snapshot", forbidden_snapshot)
    response = call(api, "finance.status", {"project_id": project_id})
    assert response.status_code == 422 and "storage_owner_mismatch" in response.text
    query = call(api, "finance.query", {**common(project_id, access), **QUERY})
    assert query.status_code == 403 and "root_changed" in query.text
    assert (parent / "finance-state.json").read_bytes() == before
    assert (preserved / ".finance-owner.json").is_file()


@pytest.mark.parametrize("changed_identity", ["project_id", "access_id"])
def test_ordinary_read_rejects_owner_replacement_without_root_swap(
    api, project_id, settings, changed_identity, monkeypatch
):
    access = ready(api, project_id)
    parent = settings.data_dir / "projects" / str(project_id)
    root = parent / "finance"
    original_root = root.stat()
    before = (parent / "finance-state.json").read_bytes()
    marker = root / ".finance-owner.json"
    owner = json.loads(marker.read_text())
    owner[changed_identity] = (
        project_id + 1 if changed_identity == "project_id" else "finance-access:foreign-incarnation"
    )
    marker.write_text(json.dumps(owner))

    def forbidden_snapshot(*args, **kwargs):
        pytest.fail("Mismatched physical owner must be denied before reading financial records")

    monkeypatch.setattr(helpers(), "read_snapshot", forbidden_snapshot)
    query = call(api, "finance.query", {**common(project_id, access), **QUERY})
    status = call(api, "finance.status", {"project_id": project_id})

    assert query.status_code == 422 and "storage_owner_mismatch" in query.text
    assert status.status_code == 422 and "storage_owner_mismatch" in status.text
    assert (root.stat().st_dev, root.stat().st_ino) == (
        original_root.st_dev,
        original_root.st_ino,
    )
    assert (parent / "finance-state.json").read_bytes() == before


@pytest.mark.parametrize(
    "relative",
    ["finance-state.json", "finance/.finance-owner.json", "finance/schemas/finance-v1.schema.json"],
)
def test_special_metadata_and_schema_fail_without_blocking(
    api, project_id, settings, tmp_path, relative
):
    import os
    import signal

    ready(api, project_id)
    recovered = Settings(
        data_dir=tmp_path / "recovered/data", state_dir=tmp_path / "recovered/state"
    )
    shutil.copytree(
        settings.data_dir / "projects",
        recovered.data_dir / "projects",
        ignore=shutil.ignore_patterns(".writer.lock"),
    )
    target = recovered.data_dir / "projects" / str(project_id) / relative
    target.unlink()
    os.mkfifo(target)

    def blocked(*_args):
        pytest.fail("Finance status blocked while opening a special file")

    previous = signal.signal(signal.SIGALRM, blocked)
    signal.alarm(2)
    try:
        with Session(api.app.state.engine) as session, pytest.raises(RepositoryError):
            FinanceConsole(session, recovered).status(SimpleNamespace(project_id=project_id))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_metadata_descriptor_read_cannot_block_on_regular_to_fifo_swap(tmp_path, monkeypatch):
    import os
    import signal

    path = tmp_path / "synthetic-metadata.json"
    path.write_text('{"format":"synthetic"}')
    original = builtin_finance.read_regular

    def swapped(target):
        target.unlink()
        os.mkfifo(target)
        return original(target)

    monkeypatch.setattr(builtin_finance, "read_regular", swapped)

    def blocked(*_args):
        pytest.fail("Metadata descriptor read blocked after a special-file swap")

    previous = signal.signal(signal.SIGALRM, blocked)
    signal.alarm(2)
    try:
        with pytest.raises(RepositoryError) as error:
            FinanceConsole._read_json(path)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert error.value.data["finance_code"] == "unsafe_storage_metadata"


@pytest.mark.parametrize("fault", ["hardlink", "oversize"])
def test_metadata_is_bounded_regular_and_unaliased(tmp_path, fault):
    from stackos.backups.filesystem import MAX_JSON

    path = tmp_path / "synthetic-metadata.json"
    if fault == "hardlink":
        original = tmp_path / "source.json"
        original.write_text('{"format":"synthetic"}')
        path.hardlink_to(original)
    else:
        path.write_bytes(b"x" * (MAX_JSON + 1))
    with pytest.raises(RepositoryError) as error:
        FinanceConsole._read_json(path)
    assert error.value.data["finance_code"] == "unsafe_storage_metadata"
