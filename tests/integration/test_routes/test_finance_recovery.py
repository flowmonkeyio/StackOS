"""Synthetic explicit operator recovery; never touches real business files."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos.db.models import WorkspaceBinding
from stackos.mcp.errors import ToolNotGrantedError
from stackos.plugins.builtin_finance import FinanceConsole, helpers
from stackos.plugins.finance_recovery import FinanceRecovery
from stackos.repositories.base import RepositoryError
from stackos.repositories.projects import ProjectRepository
from tests.helpers.finance_workspace import record
from tests.unit.test_finance_management import fixture_document


def fixture_source(api, project_id, tmp_path):
    workspace = tmp_path / "workspace"
    source = workspace / "finance"
    module = helpers()
    master = module.workspace.initialize(source)
    document = fixture_document()
    document["revision"] = 1
    relative = "attachments/2026/10/original.txt"
    payload = b"synthetic original evidence"
    (source / relative).parent.mkdir(parents=True, exist_ok=True)
    (source / relative).write_bytes(payload)
    document["attachments"].append(
        record(
            "attachment:recovery",
            "retained",
            source_ref="source:bank",
            role="original",
            relative_path=relative,
            sha256=hashlib.sha256(payload).hexdigest(),
            bytes=len(payload),
            media_type="text/plain",
            original_filename="original.txt",
        )
    )
    document["sources"][0]["attachment_refs"] = ["attachment:recovery"]
    module.workspace.write_document(master, document, module.workspace.digest(master))
    with Session(api.app.state.engine) as session:
        session.add(
            WorkspaceBinding(
                project_id=project_id,
                repo_fingerprint="recovery-fixture",
                last_known_root=str(workspace),
            )
        )
        session.commit()
    return source


def files(root: Path):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_preview_is_inert_and_apply_preserves_source_and_enablement(
    api, project_id, settings, tmp_path
):
    source = fixture_source(api, project_id, tmp_path)
    before = files(source)
    with Session(api.app.state.engine) as session:
        service = FinanceRecovery(session, settings)
        preview = service.preview(project_id, source)
        assert preview["verified_attachments"] == 1
        assert (
            preview["master_sha256"]
            == hashlib.sha256((source / "finance.json").read_bytes()).hexdigest()
        )
        assert files(source) == before
        assert not (settings.data_dir / "projects" / str(project_id)).exists()
        result = service.apply(project_id, source, preview["confirmation_sha256"])
        assert result["applied"]
        assert result["record_counts"] == preview["record_counts"]
        assert files(source) == before
        assert not service.console.config_path.exists()
        target = Path(result["target_root"])
        assert (target / "finance.json").read_bytes() == (source / "finance.json").read_bytes()
        repeated = service.apply(project_id, source, preview["confirmation_sha256"])
        assert repeated["already_applied_or_resumed"]
        with pytest.raises(ToolNotGrantedError):
            FinanceConsole(session, settings).status(SimpleNamespace(project_id=project_id))
    assert api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable").status_code == 200
    with Session(api.app.state.engine) as session:
        FinanceConsole(session, settings).status(SimpleNamespace(project_id=project_id))
    assert (target / "finance.json").read_bytes() == before["finance.json"][0]


@pytest.mark.parametrize(
    "fault", ["changed", "wrong_token", "target", "symlink", "unbound", "missing", "altered"]
)
def test_recovery_rejects_changed_or_ambiguous_inputs(api, project_id, settings, tmp_path, fault):
    source = fixture_source(api, project_id, tmp_path)
    with Session(api.app.state.engine) as session:
        service = FinanceRecovery(session, settings)
        preview = service.preview(project_id, source)
        token = preview["confirmation_sha256"]
        if fault == "changed":
            (source / "extra.txt").write_text("source changed")
        elif fault == "wrong_token":
            token = "0" * 64
        elif fault == "target":
            Path(preview["target_root"]).mkdir(parents=True)
        elif fault == "symlink":
            link = source.parent / "linked-finance"
            link.symlink_to(source)
            source = link
        elif fault == "unbound":
            binding = session.get(WorkspaceBinding, preview["workspace_binding_id"])
            session.delete(binding)
            session.commit()
        elif fault == "missing":
            (source / "attachments/2026/10/original.txt").unlink()
        elif fault == "altered":
            (source / "attachments/2026/10/original.txt").write_bytes(b"changed original")
        with pytest.raises((RepositoryError, OSError, ValueError)):
            service.apply(project_id, source, token)
        assert not service.console.config_path.exists()


def test_recovery_blocks_custody_gap_even_when_normal_read_tolerates_it(
    api, project_id, settings, tmp_path, monkeypatch
):
    source = fixture_source(api, project_id, tmp_path)
    with Session(api.app.state.engine) as session:
        service = FinanceRecovery(session, settings)
        original = service.console._validated

        def unavailable(root):
            snapshot = original(root)
            snapshot["custody_issues"] = [{"attachment_ref": "missing", "state": "unavailable"}]
            return snapshot

        monkeypatch.setattr(service.console, "_validated", unavailable)
        with pytest.raises(RepositoryError):
            service.preview(project_id, source)
        assert not (settings.data_dir / "projects" / str(project_id)).exists()


@pytest.mark.parametrize("phase", ["before_state", "after_publish"])
def test_interrupted_recovery_is_resumed_without_initializing_or_enablement(
    api, project_id, settings, tmp_path, monkeypatch, phase
):
    source = fixture_source(api, project_id, tmp_path)
    with Session(api.app.state.engine) as session:
        service = FinanceRecovery(session, settings)
        preview = service.preview(project_id, source)
        before = files(source)
        original = service.console._save

        def fail(path, state):
            if phase == "before_state" or state["phase"] == "ready":
                raise OSError("injected interruption")
            return original(path, state)

        monkeypatch.setattr(service.console, "_save", fail)
        with pytest.raises(OSError):
            service.apply(project_id, source, preview["confirmation_sha256"])
        monkeypatch.undo()
        if phase == "before_state":
            with pytest.raises(RepositoryError):
                service.apply(project_id, source, preview["confirmation_sha256"])
            assert not Path(preview["target_root"]).exists()
            from stackos.backups.models import BackupError
            from stackos.backups.service import _project_inventory

            with pytest.raises(BackupError):
                _project_inventory(settings)
            assert (
                api.post(f"/api/v1/projects/{project_id}/plugins/finance/enable").status_code == 200
            )
            with pytest.raises(RepositoryError):
                service.console.status(SimpleNamespace(project_id=project_id))
            assert not Path(preview["target_root"]).exists()
            assert files(source) == before
            return
        result = service.apply(project_id, source, preview["confirmation_sha256"])
        assert result["already_applied_or_resumed"]
        assert files(source) == before
        assert (Path(result["target_root"]) / "finance.json").read_bytes() == before[
            "finance.json"
        ][0]


def test_marker_only_target_is_rejected(api, project_id, settings, tmp_path):
    source = fixture_source(api, project_id, tmp_path)
    with Session(api.app.state.engine) as session:
        service = FinanceRecovery(session, settings)
        preview = service.preview(project_id, source)
        parent = Path(preview["target_root"]).parent
        parent.mkdir(parents=True)
        (parent / ".publication-pending").write_text("pending")
        with pytest.raises(RepositoryError):
            service.preview(project_id, source)
        with pytest.raises(RepositoryError):
            service.apply(project_id, source, preview["confirmation_sha256"])
        assert not Path(preview["target_root"]).exists()


def test_cli_preview_and_offline_apply_gate(api, project_id, settings, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from stackos.cli import finance_recovery_commands as commands
    from stackos.cli.app import app

    source = fixture_source(api, project_id, tmp_path)
    monkeypatch.setattr(commands, "get_settings", lambda: settings)
    args = ["finance-recover", "--project-id", str(project_id), "--source", str(source)]
    runner = CliRunner()
    preview = runner.invoke(app, args)
    assert preview.exit_code == 0, preview.output
    result = json.loads(preview.output)
    monkeypatch.setattr(commands, "_tcp_can_connect", lambda *_: True)
    blocked = runner.invoke(
        app, [*args, "--apply", "--confirmation-sha256", result["confirmation_sha256"]]
    )
    assert blocked.exit_code == 1
    assert "stop StackOS" in blocked.output
    assert not Path(result["target_root"]).exists()


@pytest.mark.parametrize("fault", ["foreign", "multiple", "target_link", "hardlink", "legacy"])
def test_recovery_rejects_custody_ambiguity(api, project_id, settings, tmp_path, fault):
    source = fixture_source(api, project_id, tmp_path)
    with Session(api.app.state.engine) as session:
        service = FinanceRecovery(session, settings)
        preview = service.preview(project_id, source)
        if fault in {"foreign", "multiple"}:
            other_id = project_id
            if fault == "foreign":
                other_id = (
                    ProjectRepository(session)
                    .create(slug="other", name="Other", domain="other.example")
                    .data.id
                )
            session.add(
                WorkspaceBinding(
                    project_id=other_id,
                    repo_fingerprint="other-recovery-binding",
                    last_known_root=str(source.parent),
                )
            )
            session.commit()
        elif fault == "target_link":
            target = Path(preview["target_root"])
            target.parent.mkdir(parents=True)
            target.symlink_to(source)
        elif fault == "hardlink":
            os.link(source / "finance.json", source / "alias.json")
        else:
            service.console.config_dir.mkdir(parents=True)
            service.console.config_path.write_text(
                json.dumps({"format": "finance-access-v1", "entries": [], "intents": {"other": {}}})
            )
        with pytest.raises((RepositoryError, OSError, ValueError)):
            service.apply(project_id, source, preview["confirmation_sha256"])


def test_recovery_retry_rejects_modified_published_store(api, project_id, settings, tmp_path):
    source = fixture_source(api, project_id, tmp_path)
    with Session(api.app.state.engine) as session:
        service = FinanceRecovery(session, settings)
        preview = service.preview(project_id, source)
        result = service.apply(project_id, source, preview["confirmation_sha256"])
        target = Path(result["target_root"])
        (target / "unexpected.txt").write_text("new file")
        with pytest.raises(RepositoryError):
            service.apply(project_id, source, preview["confirmation_sha256"])
        assert (target / "unexpected.txt").read_text() == "new file"


@pytest.mark.parametrize("root_kind", ["ancestor", "nested"])
def test_workspace_uses_nearest_binding_but_rejects_foreign_nested_store(
    api, project_id, settings, tmp_path, root_kind
):
    source = fixture_source(api, project_id, tmp_path)
    with Session(api.app.state.engine) as session:
        other_id = (
            ProjectRepository(session)
            .create(slug="ancestor", name="Ancestor", domain="ancestor.example")
            .data.id
        )
        root = Path("/") if root_kind == "ancestor" else source / "nested-project"
        if root_kind == "nested":
            root.mkdir()
        session.add(
            WorkspaceBinding(
                project_id=other_id,
                repo_fingerprint="overlapping-ancestor",
                last_known_root=str(root),
            )
        )
        session.commit()
        service = FinanceRecovery(session, settings)
        if root_kind == "nested":
            with pytest.raises(RepositoryError):
                service.preview(project_id, source)
        else:
            preview = service.preview(project_id, source)
            assert preview["workspace_root"] == str(source.parent)
            assert preview["project_id"] == project_id
