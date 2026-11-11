"""Public deletion proof: real cleanup on disposable app DB/data/state only."""

import hashlib
import json

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

import stackos.cli.operation_commands as operation_cli
from stackos.cli import app as cli_app
from stackos.db.models import Artifact, IdempotencyKey, Project, Run, RunKind, RunStatus
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.resources import ArtifactRepository
from stackos.repositories.runs import RunRepository


def target(api, slug="disposable-delete"):
    response = api.post(
        "/api/v1/projects", json={"slug": slug, "name": "Disposable", "domain": "test"}
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def arguments(project, **extra):
    return {
        "project_id": project["id"],
        "confirmation": {
            "permanent": True,
            "project_id": project["id"],
            "project_slug": project["slug"],
        },
        **extra,
    }


def call(api, name, args, **kwargs):
    return api.post(f"/api/v1/operations/{name}/call", json={"arguments": args}, **kwargs)


@pytest.mark.parametrize("mode", ["compact", "raw"])
@pytest.mark.parametrize("console", [False, True])
def test_confirmed_delete_real_cleanup_repeat_missing_no_payload_recreation(
    api, settings, mode, console
):
    project = target(api)
    other = target(api, "disposable-survivor")
    root = settings.data_dir / "projects" / str(project["id"])
    root.mkdir(parents=True)
    (root / "owned.txt").write_text("fixture-owned")
    keep = settings.data_dir / "projects" / str(other["id"]) / "keep.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("protected")
    headers = {}
    if console:
        headers = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}
    args = arguments(project, scope="catalog", response_mode=mode, idempotency_key="same-delete")
    deleted = call(api, "project.delete", args, headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["status"] == "deleted"
    assert not root.exists() and keep.read_text() == "protected"
    # Same-key retry observes absence rather than recreating/replaying project history.
    repeated = call(api, "project.delete", args, headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"]["status"] == "missing"
    with Session(api.app.state.engine) as session:
        assert session.get(Project, project["id"]) is None
        assert (
            session.exec(
                select(IdempotencyKey).where(IdempotencyKey.project_id == project["id"])
            ).all()
            == []
        )
    assert not root.exists()
    replacement = target(api)
    assert replacement["id"] > project["id"]
    assert call(api, "project.delete", args, headers=headers).json()["data"]["status"] == "missing"
    assert api.get(f"/api/v1/projects/{replacement['id']}").json() == replacement


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"hard": True},
        {"confirmation": {}},
        {"confirmation": {"permanent": False}},
        {"confirmation": {"permanent": True, "project_id": 999, "project_slug": "wrong"}},
        {"confirmation": {"permanent": True, "project_id": True, "project_slug": "wrong"}},
    ],
)
def test_old_or_wrong_confirmation_rejects_before_mutation(api, settings, bad):
    project = target(api)
    args = {"project_id": project["id"], **bad}
    response = call(api, "project.delete", args)
    assert response.status_code == 422, response.text
    assert api.get(f"/api/v1/projects/{project['id']}").json() == project
    assert not (settings.data_dir / "projects" / str(project["id"])).exists()


def test_stale_slug_console_scope_ack_and_removed_route_are_non_destructive(api):
    project = target(api)
    stale = arguments(project)
    stale["confirmation"]["project_slug"] = "stale"
    assert call(api, "project.delete", stale).status_code == 422
    for permanent in (False, 1, "true", None):
        invalid = arguments(project)
        invalid["confirmation"]["permanent"] = permanent
        assert call(api, "project.delete", invalid).status_code == 422
    assert call(api, "project.delete", arguments(project, response_mode="ack")).status_code == 422
    console = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}
    assert call(api, "project.delete", arguments(project), headers=console).status_code == 403
    for suffix in ("", "?hard=true", "?hard=false"):
        assert api.delete(f"/api/v1/projects/{project['id']}{suffix}").status_code == 405
    assert api.get(f"/api/v1/projects/{project['id']}").json() == project


def test_cli_uses_real_cleanup_operation(api, settings, monkeypatch):
    project = target(api)

    def request(method, path, *, body=None, **kwargs):
        response = api.request(method, path, json=body)
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", request)
    result = CliRunner().invoke(
        cli_app,
        ["ops", "call", "project.delete", "--input", "-"],
        input=json.dumps(arguments(project)),
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["status"] == "deleted"
    assert api.get(f"/api/v1/projects/{project['id']}").status_code == 404


@pytest.mark.parametrize("mode", ["compact", "raw"])
def test_blocked_and_partial_failure_preserve_repair_context_without_replaying(
    api, settings, monkeypatch, mode
):
    project = target(api)
    with Session(api.app.state.engine) as session:
        run = RunRepository(session).start(project_id=project["id"], kind=RunKind.MAINTENANCE).data
    args = arguments(project, response_mode=mode, idempotency_key="retry-state")
    blocked = call(api, "project.delete", args)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["data"]["status"] == "blocked"
    assert "running_work" in blocked.json()["data"]["blockers"]
    with Session(api.app.state.engine) as session:
        row = session.get(Run, run.id)
        assert row.status == RunStatus.RUNNING  # Delete never aborts active work.
        row.status = RunStatus.SUCCESS
        session.add(row)
        session.commit()
    root = settings.data_dir / "projects" / str(project["id"])
    root.mkdir(parents=True)
    leaf = root / "fixture.txt"
    leaf.write_text("disposable")
    with monkeypatch.context() as failure:
        # Fail after removing one fixture leaf; retain the real admission/purge flow.
        def fail(service, project_id):
            leaf.unlink()
            raise OSError("secret fixture path must never escape")

        failure.setattr(ProjectCleanupService, "_clean_files", fail)
        incomplete = call(api, "project.delete", args)
    assert incomplete.status_code == 200, incomplete.text
    data = incomplete.json()["data"]
    assert data["status"] == "incomplete" and data["retryable"] is True
    assert data["error_code"] == "OSError" and data["operation_id"]
    assert "secret fixture" not in incomplete.text and not leaf.exists()
    assert (
        api.get(f"/api/v1/projects/{project['id']}").json()["cleanup_json"]["status"]
        == "incomplete"
    )
    retry = call(api, "project.delete", args)
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["status"] == "deleted"
    assert retry.json()["data"]["operation_id"] == data["operation_id"]


def test_precleanup_internal_exception_is_sanitized(api, monkeypatch):
    project = target(api)

    async def fail(*args, **kwargs):
        raise OSError("sensitive internal path")

    monkeypatch.setattr(ProjectCleanupService, "delete", fail)
    response = call(api, "project.delete", arguments(project))
    assert response.status_code == 500
    assert response.json()["data"]["error_code"] == "OSError"
    assert "sensitive internal path" not in response.text
    assert api.get(f"/api/v1/projects/{project['id']}").json() == project


def test_delete_with_purged_run_context_does_not_fail_after_success(api):
    project = target(api)
    with Session(api.app.state.engine) as session:
        run = (
            RunRepository(session)
            .start(
                project_id=project["id"],
                kind=RunKind.MAINTENANCE,
                client_session_id="disposable-delete-context",
                metadata_json={"skill_name": "__system__"},
            )
            .data
        )
        row = session.get(Run, run.id)
        row.status = RunStatus.SUCCESS
        session.add(row)
        session.commit()
    args = arguments(
        project, run_token="disposable-delete-context", idempotency_key="purged-context"
    )
    result = call(api, "project.delete", args)
    assert result.status_code == 200, result.text
    assert result.json()["data"]["status"] == "deleted"
    assert result.json()["run_id"] is None
    with Session(api.app.state.engine) as session:
        assert session.get(Run, run.id) is None
        assert session.get(Project, project["id"]) is None
        assert session.exec(select(IdempotencyKey)).all() == []


def test_legacy_recovery_exposed_only_to_exact_admin_then_retry(api, settings):
    project = target(api)
    file = settings.generated_assets_dir / "legacy" / "fixture.txt"
    file.parent.mkdir(parents=True)
    file.write_text("owned legacy fixture")
    uri = "/generated-assets/legacy/fixture.txt"
    with Session(api.app.state.engine) as session:
        artifact = (
            ArtifactRepository(session)
            .create(
                project_id=project["id"],
                kind="file",
                uri=uri,
                provenance_json={"source": "openai-images-action"},
            )
            .data
        )
    args = arguments(project, response_mode="compact", idempotency_key="retry-delete")
    first = call(api, "project.delete", args)
    assert first.status_code == 200, first.text
    incomplete = first.json()["data"]
    assert incomplete["status"] == "incomplete" and incomplete["retryable"] is True
    assert incomplete["error_code"] == "ProjectFileOwnershipRequired"
    assert incomplete["ownership_required_artifact_ids"] == [artifact.id]
    recovery = {
        "project_id": project["id"],
        "project_slug": project["slug"],
        "artifact_id": artifact.id,
        "expected_uri": uri,
        "expected_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        "confirm_ownership": True,
    }
    missing_confirmation = {
        key: value for key, value in recovery.items() if key != "confirm_ownership"
    }
    assert call(api, "project.reconcileFileOwnership", missing_confirmation).status_code == 422
    assert (
        call(
            api,
            "project.reconcileFileOwnership",
            {
                **recovery,
                "asset_root": str(settings.generated_assets_dir),
            },
        ).status_code
        == 422
    )
    console = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}
    assert call(api, "project.reconcileFileOwnership", recovery, headers=console).status_code == 403
    for patch in (
        {"confirm_ownership": False},
        {"project_slug": "wrong"},
        {"expected_sha256": "0" * 64},
    ):
        rejected = call(api, "project.reconcileFileOwnership", {**recovery, **patch})
        assert rejected.status_code in {409, 422}, rejected.text
        assert file.exists()
    # A content change after review requires a fresh exact hash, even on a new request/session.
    file.write_text("new fixture contents")
    assert call(api, "project.reconcileFileOwnership", recovery).status_code == 409
    recovery["expected_sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    restored = call(api, "project.reconcileFileOwnership", recovery)
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["reconciled"] is True
    with Session(api.app.state.engine) as session:
        assert (
            session.get(Artifact, artifact.id).managed_file_json["evidence"]["basis"]
            == "local-admin-reconciliation"
        )
    retry = call(api, "project.delete", args)
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["status"] == "deleted"
    assert retry.json()["data"]["operation_id"] == incomplete["operation_id"]
    assert not file.exists()
