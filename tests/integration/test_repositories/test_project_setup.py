"""Receipt truth through real source loaders and disposable project state."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from alembic import command
from pydantic import ValidationError as ModelValidationError
from sqlalchemy import event, text
from sqlmodel import Session, select

from stackos.backups import (
    BackupPolicy,
    create_backup,
    generate_recovery_key,
    restore_backup,
    save_policy,
)
from stackos.config import Settings
from stackos.crypto.seed import ensure_seed_file
from stackos.db.connection import make_engine
from stackos.db.migrate import alembic_config
from stackos.db.models import Project, ProjectSetupReceipt, WorkflowTemplateExtension
from stackos.operations.dispatcher import OperationDispatcher
from stackos.operations.registry import build_operation_registry
from stackos.project_cleanup import ProjectCleanupService
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.project_setup import ProjectSetupRepository, SetupResult, VerifiedSetup
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.workspaces import WorkspaceRepository
from stackos.workflows.authoring_guide import PROJECT_SETUP_NOTICES
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot


@pytest.fixture
def setup_project(session, tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    project = WorkspaceRepository(session).bootstrap(cwd=str(folder)).project_id
    return project, folder, ProjectSetupRepository(session)


def verified_result(
    repo, project, key="engineering.tracked-delivery", *, selected_optional_presets=None
):
    selected = selected_optional_presets or []
    sources = repo.inspect_sources(project, key, selected_optional_presets=selected)
    template = sources[0].content
    mapping = []
    for field, ref_key, kind in (
        ("agent_requirements", "agent_preset_ref", "specialist"),
        ("skill_preset_requirements", "skill_preset_ref", "main_agent"),
    ):
        for req in template.get(field, []):
            if req["requirement"] == "optional" and req[ref_key] not in selected:
                continue
            source = next(s for s in sources if s.kind == kind and s.key == req[ref_key])
            mapping.append(
                {
                    "source_ref": source.ref,
                    "role": req.get("role", req[ref_key]),
                    "decision": "materialized",
                    "target_paths": ["fixture/role"],
                }
            )
    # These are explicit synthetic agent assertions for repository validation,
    # not native consumption or installed activation proof.
    proof = VerifiedSetup(
        sources=sources,
        selected_optional_presets=selected,
        role_mapping=mapping,
        obligations=[
            {
                "source_ref": s.ref,
                "obligation_ref": "fixture/all-applicable-rules",
                "disposition": "retained",
                "target_paths": ["fixture/role"],
            }
            for s in sources
        ],
        local_adaptations=[
            {
                "path": "fixture/role",
                "digest": "a" * 64,
                "provenance": "synthetic adaptation with retained local override",
            }
        ],
        checks={
            k: {"status": "passed", "evidence_ref": "fixture/" + k}
            for k in ("syntax", "registration", "references", "behavior")
        },
        activation="verified_loaded",
        activation_evidence_ref="fixture/loaded",
        readiness={
            "structurally_ready": True,
            "context_status": "deferred",
            "required_providers_ready": False,
            "execution_ready": False,
            "structural_validation_ref": "fixture/structural",
            "strict_validation_ref": "fixture/strict",
            "deferred_inputs": ["goal"],
        },
    )
    return SetupResult(
        attempt_ref="fixture/attempt-1",
        result_ref="fixture/result-1",
        status="success",
        verification=proof,
    )


def test_independent_success_failure_and_unknown_legacy(setup_project, session):
    project, _, repo = setup_project
    first = verified_result(repo, project)
    saved = repo.record_result(
        project, "engineering.tracked-delivery", expected_revision=0, result=first
    )
    assert saved.status == "current"
    detail = repo.get(project, workflow_key="nonexistent.unconfigured")
    assert detail.status == "current" and detail.configured_count == 1
    assert detail.unknown_count == 0 and detail.workflows[0].status == "source_unavailable"
    assert saved.successful["verification"]["readiness"]["execution_ready"] is False
    assert saved.successful["verification"]["readiness"]["deferred_inputs"] == ["goal"]
    repo.record_result(
        project,
        "agency.project-setup",
        expected_revision=0,
        result=SetupResult(
            attempt_ref="fixture/attempt-1",
            result_ref="fixture/failed",
            status="failed",
            changes=["partial file edit"],
        ),
    )
    before = copy.deepcopy(saved.successful)
    failed = repo.record_result(
        project,
        "engineering.tracked-delivery",
        expected_revision=1,
        result=SetupResult(
            attempt_ref="fixture/attempt-2",
            result_ref="fixture/f2",
            status="interrupted",
            changes=["partial edit"],
        ),
    )
    assert failed.successful == before
    assert failed.latest_result["changes"] == ["partial edit"]
    assert failed.revision == 2
    state = repo.get(project)
    assert state.configured_count == 1 and state.unknown_count == 1
    assert state.status == "setup_needed"
    assert (
        session.exec(
            select(WorkflowTemplateExtension).where(WorkflowTemplateExtension.project_id == project)
        ).all()
        == []
    )
    with pytest.raises(ConflictError, match="revision changed"):
        repo.record_result(
            project, "engineering.tracked-delivery", expected_revision=1, result=first
        )
    assert repo.get(project).configured_count == 1
    completed_second = repo.record_result(
        project,
        "agency.project-setup",
        expected_revision=1,
        result=verified_result(repo, project, "agency.project-setup"),
    )
    assert completed_second.status == "current"
    assert repo.get(project).configured_count == 2
    assert repo.get(project).workflows[1].successful == before


def test_source_inspection_is_read_only_and_does_not_release_writer_reservation(
    setup_project, session
):
    project, _, repo = setup_project
    commits = []
    event.listen(session, "after_commit", lambda _: commits.append(True))
    result = verified_result(repo, project)
    repo.get(project, workflow_key="engineering.tracked-delivery")
    assert commits == []
    repo.record_result(project, "engineering.tracked-delivery", expected_revision=0, result=result)
    # Source resolution must not commit an intervening plugin sync; the existing
    # project reservation remains held until this one receipt commit.
    assert commits == [True]


@pytest.mark.parametrize("changed_kind", ["template", "main_agent", "specialist"])
def test_source_bumps_do_not_advance_applied_and_previous_content_survives(
    setup_project, changed_kind
):
    project, root, repo = setup_project
    key = "engineering.tracked-delivery"
    result = verified_result(repo, project)
    saved = repo.record_result(project, key, expected_revision=0, result=result)
    original = copy.deepcopy(saved.successful)
    dismissed = repo.set_reminders(project, dismiss_setup=True, dismiss_updates=True)
    directory = {
        "template": "workflows",
        "main_agent": "skill-presets",
        "specialist": "agent-presets",
    }
    source = root / ".stackos" / directory[changed_kind] / "changed.yaml"
    source.parent.mkdir(parents=True)
    import yaml

    selected = next(s for s in result.verification.sources if s.kind == changed_kind)
    spec = copy.deepcopy(selected.content)
    spec["version"] = "99.0.0"
    source.write_text(yaml.safe_dump(spec))
    state = repo.get(project)
    assert state.update_count == 1
    assert state.workflows[0].successful == original
    assert (
        next(s for s in state.workflows[0].available_sources if s.key == selected.key).version
        == "99.0.0"
    )
    assert state.setup_reminder_dismissed_at == dismissed.setup_reminder_dismissed_at
    assert state.update_reminder_dismissed_at == dismissed.update_reminder_dismissed_at
    assert original["verification"]["sources"][0]["content"]["version"] == "0.8.3"
    with pytest.raises(ConflictError, match="sources changed"):
        repo.record_result(project, key, expected_revision=1, result=result)
    # Source disappearance does not discard the receipt-owned previous base.
    source.unlink()
    assert repo.get(project).workflows[0].successful == original
    assert repo.get(project).update_count == 0


def test_same_version_content_bump_and_existing_override_provenance(setup_project, session):
    from stackos.workflows.template_loader import WorkflowTemplateLoader

    project, _, repo = setup_project
    key = "engineering.tracked-delivery"
    loader = WorkflowTemplateLoader(session)
    loader.upsert_extension(
        project_id=project,
        workflow_key=key,
        input_defaults_json={"goal": "retained operator intent"},
    )
    result = verified_result(repo, project)
    saved = repo.record_result(project, key, expected_revision=0, result=result)
    override = next(s for s in result.verification.sources if s.kind == "override")
    assert override.content["input_defaults_json"] == {"goal": "retained operator intent"}
    loader.upsert_extension(
        project_id=project, workflow_key=key, input_defaults_json={"goal": "new operator intent"}
    )
    current = repo.get(project)
    assert current.update_count == 1
    assert current.workflows[0].successful == saved.successful
    assert (
        current.workflows[0].available_sources[0].version
        == saved.successful["verification"]["sources"][0]["version"]
    )


@pytest.mark.parametrize("broken", ["obligation", "checks", "digest", "coverage"])
def test_material_loss_and_incomplete_proof_cannot_claim_success(setup_project, broken):
    project, _, repo = setup_project
    proof = verified_result(repo, project).verification.model_dump()
    if broken == "obligation":
        proof["obligations"][0].update(disposition="omitted", reason="Fits project better")
    elif broken == "checks":
        proof["checks"]["behavior"]["status"] = "unknown"
    elif broken == "digest":
        proof["sources"][0]["digest"] = "not-actual-content"
    else:
        proof["obligations"] = proof["obligations"][:1]
    with pytest.raises(ModelValidationError):
        VerifiedSetup.model_validate(proof)
    assert repo.get(project).configured_count == 0


def test_startup_notice_is_advisory_and_respects_permanent_dismissal(setup_project):
    project, _, repo = setup_project
    before = repo.startup_summary(project)
    assert before["notice"] == PROJECT_SETUP_NOTICES["setup_needed"]
    assert before["guidance_ref"]["arguments"]["response_mode"] == "raw"
    repo.set_reminders(project, dismiss_setup=True)
    after = repo.startup_summary(project)
    assert after["notice"] is None
    assert after["status"] == "setup_needed"
    assert after["detail_ref"] == before["detail_ref"]


def test_required_role_mapping_and_reload_truth(setup_project):
    project, _, repo = setup_project
    result = verified_result(repo, project)
    result.verification.role_mapping = []
    with pytest.raises(ValidationError, match="source-to-target"):
        repo.record_result(
            project, "engineering.tracked-delivery", expected_revision=0, result=result
        )
    result = verified_result(repo, project)
    result.verification.activation = "reload_required"
    repo.record_result(project, "engineering.tracked-delivery", expected_revision=0, result=result)
    assert repo.get(project).workflows[0].status == "activation_pending"
    assert repo.get(project).status == "setup_needed"


def test_compatible_adaptation_and_justified_recommended_role_decision(setup_project):
    project, _, repo = setup_project
    result = verified_result(repo, project)
    mapping = next(
        item for item in result.verification.role_mapping if item.role == "codebase-explorer"
    )
    mapping.decision = "omitted"
    mapping.target_paths = []
    mapping.reason = (
        "Recommended role omitted for bounded work; main retains the exploration method"
    )
    obligation = next(
        item for item in result.verification.obligations if item.source_ref == mapping.source_ref
    )
    obligation.disposition = "adapted"
    obligation.target_paths = ["fixture/main-orchestrator"]
    obligation.reason = "The applicable method remains in the selected main-agent contract"
    saved = repo.record_result(
        project, "engineering.tracked-delivery", expected_revision=0, result=result
    )
    proof = saved.successful["verification"]
    assert proof["role_mapping"] == result.verification.model_dump(mode="json")["role_mapping"]
    assert proof["obligations"] == result.verification.model_dump(mode="json")["obligations"]


def test_dismissals_survive_recreation_rename_and_missing_folder(setup_project, session):
    project, root, repo = setup_project
    first = repo.set_reminders(project, dismiss_setup=True)
    assert first.setup_reminder_dismissed_at and not first.update_reminder_dismissed_at
    second = repo.set_reminders(project, dismiss_updates=True)
    ProjectRepository(session).update(project, name="Renamed project")
    with Session(session.get_bind()) as reloaded:
        observed = ProjectSetupRepository(reloaded).get(project)
        assert observed.setup_reminder_dismissed_at == second.setup_reminder_dismissed_at
        assert observed.update_reminder_dismissed_at == second.update_reminder_dismissed_at
    assert (
        repo.set_reminders(project).setup_reminder_dismissed_at
        == second.setup_reminder_dismissed_at
    )
    root.rmdir()
    unavailable = repo.get(project)
    assert unavailable.eligible and unavailable.folder_status == "unavailable"
    assert unavailable.status == "folder_unavailable"
    assert unavailable.next_action == "repair_project_folder"
    assert unavailable.setup_reminder_dismissed_at == second.setup_reminder_dismissed_at
    repo.record_result(
        project,
        "engineering.tracked-delivery",
        expected_revision=0,
        result=SetupResult(
            attempt_ref="fixture/missing",
            result_ref="fixture/partial",
            status="interrupted",
            changes=["possible partial edit"],
        ),
    )
    assert repo.get(project).workflows[0].partial_changes
    root.mkdir()
    assert repo.get(project).folder_status == "available"
    directoryless = ProjectRepository(session).create(slug="none", name="None", domain="test").data
    assert repo.get(directoryless.id).eligible is False
    assert repo.get(directoryless.id).status == "ineligible"
    assert repo.get(directoryless.id).update_count == 0


@pytest.mark.asyncio
async def test_real_operation_admin_authority_compact_and_cleanup_fence(setup_project, session):
    project, _, repo = setup_project
    dispatcher = OperationDispatcher(build_operation_registry())
    raw = await dispatcher.dispatch(
        "projectSetup.get",
        {"project_id": project, "response_mode": "raw"},
        session=session,
        surface="mcp",
    )
    assert raw.payload["status"] == "setup_needed"
    with pytest.raises(NotFoundError):
        await dispatcher.dispatch(
            "projectSetup.setReminders",
            {"project_id": project, "dismiss_setup": True},
            session=session,
            surface="mcp",
        )
    assert repo.get(project).setup_reminder_dismissed_at is None
    allowed = await dispatcher.dispatch(
        "projectSetup.setReminders",
        {"project_id": project, "dismiss_setup": True},
        session=session,
        surface="rest",
        trusted_local_admin=True,
        auth_scope="browser",
    )
    assert allowed.payload["data"]["setup_reminder_dismissed_at"]
    repo.record_result(
        project,
        "engineering.tracked-delivery",
        expected_revision=0,
        result=verified_result(repo, project),
    )
    compact = await dispatcher.dispatch(
        "projectSetup.get", {"project_id": project}, session=session, surface="mcp"
    )
    assert compact.payload["data"]["workflows"][0]["receipt_id"]
    assert "content" not in json.dumps(compact.payload)
    assert compact.payload["data"]["detail_ref"]["arguments"]["response_mode"] == "raw"
    row = session.get(Project, project)
    row.cleanup_json = {"status": "pending"}
    session.add(row)
    session.commit()
    with pytest.raises(ConflictError, match="cleanup"):
        repo.set_reminders(project, dismiss_updates=True)


@pytest.mark.asyncio
async def test_upgrade_encrypted_backup_and_canonical_delete_preserve_repo(session, tmp_path):
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    settings.token_path.write_text("synthetic-fixture-token")
    engine = make_engine(settings.db_path)
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    ensure_seed_file(settings.seed_path)
    with Session(engine) as isolated:
        root = tmp_path / "source"
        root.mkdir()
        sentinel = root / "keep"
        sentinel.write_text("repository content")
        project = WorkspaceRepository(isolated).bootstrap(cwd=str(root)).project_id
        repo = ProjectSetupRepository(isolated)
        repo.record_result(
            project,
            "engineering.tracked-delivery",
            expected_revision=0,
            result=verified_result(repo, project),
        )
        repo.set_reminders(project, dismiss_setup=True, dismiss_updates=True)
        before = repo.get(project).model_dump(mode="json")
        private, public = tmp_path / "private.pem", tmp_path / "public.pem"
        generate_recovery_key(settings, private, public)
        save_policy(settings, BackupPolicy(recipient_public_key=public.read_text()))
        backup = create_backup(settings)
        output = tmp_path / "restored"
        restore_backup(settings, Path(backup["path"]), private, output)
        restored_engine = make_engine(output / "data/stackos.db")
        with Session(restored_engine) as restored:
            assert ProjectSetupRepository(restored).get(project).model_dump(mode="json") == before
        restored_engine.dispose()
        target = ProjectRepository(isolated).get(project)
        deleted = await ProjectCleanupService(isolated, settings).delete(
            project,
            confirmation={"permanent": True, "project_id": project, "project_slug": target.slug},
        )
        assert deleted.status == "deleted"
        assert (
            isolated.exec(
                select(ProjectSetupReceipt).where(ProjectSetupReceipt.project_id == project)
            ).all()
            == []
        )
        assert sentinel.read_text() == "repository content"
        assert Path(backup["path"]).is_file()
    engine.dispose()

    # An actual reconciled-baseline upgrade adds nullable state, never success.
    migration_settings = Settings(
        data_dir=tmp_path / "migration", state_dir=tmp_path / "migration-state"
    )
    migration_settings.ensure_dirs()
    command.upgrade(alembic_config(migration_settings), "0043_automations_homes_merge")
    historical = make_engine(migration_settings.db_path)
    with historical.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO projects "
                "(slug,name,domain,locale,is_active,created_at,updated_at,order_index) "
                "VALUES ('legacy','Legacy','test','en-US',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,7)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO workflow_template_extensions "
                "(project_id,workflow_key,enabled,input_defaults_json,created_at,updated_at) "
                "VALUES (1,'engineering.tracked-delivery',1,:value,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"value": '{"goal":"keep existing extension"}'},
        )
        conn.execute(
            text(
                "INSERT INTO task_trackers "
                "(project_id,key,name,description,rev,config_json,created_at,updated_at) "
                "VALUES (1,'default','Work','',0,:value,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"value": '{"delivery":{"host_key":"claude","profile":"retained"}}'},
        )
        retained = {
            table: conn.execute(text(f"SELECT * FROM {table}")).all()
            for table in ("workflow_template_extensions", "task_trackers")
        }
    command.upgrade(alembic_config(migration_settings), "head")
    with Session(historical) as upgraded:
        row = upgraded.exec(select(Project).where(Project.slug == "legacy")).one()
        assert row.name == "Legacy" and row.order_index == 7
        assert row.setup_reminder_dismissed_at is None and row.update_reminder_dismissed_at is None
        assert upgraded.exec(select(ProjectSetupReceipt)).all() == []
        for table, before_rows in retained.items():
            assert upgraded.execute(text(f"SELECT * FROM {table}")).all() == before_rows
        migration_root = tmp_path / "migration-repo"
        migration_root.mkdir()
        binding = (
            WorkspaceRepository(upgraded)
            .connect(
                project_id=row.id,
                repo_fingerprint="path:migration-repo",
                last_known_root=str(migration_root),
            )
            .data
        )
        ProjectRepository(upgraded).update(row.id, primary_workspace_binding_id=binding.id)
        receipts = ProjectSetupRepository(upgraded)
        receipts.record_result(
            row.id,
            "engineering.tracked-delivery",
            expected_revision=0,
            result=verified_result(receipts, row.id),
        )
        receipts.set_reminders(row.id, dismiss_setup=True, dismiss_updates=True)
        applied_before_replay = receipts.get(row.id).model_dump(mode="json")
    with historical.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num='0043_automations_homes_merge'"))
    command.upgrade(alembic_config(migration_settings), "head")
    with Session(historical) as replayed:
        assert (
            ProjectSetupRepository(replayed).get(1).model_dump(mode="json") == applied_before_replay
        )
    historical.dispose()


@pytest.mark.parametrize("directory", ["workflows", "agent-presets", "skill-presets"])
@pytest.mark.parametrize("consumer", ["get", "failed", "unfinished", "reminders"])
def test_malformed_sources_retain_evidence_and_committed_write_responses(
    setup_project, session, directory, consumer
):
    project, root, repo = setup_project
    key = "engineering.tracked-delivery"
    saved = repo.record_result(
        project, key, expected_revision=0, result=verified_result(repo, project)
    )
    previous = copy.deepcopy(saved.successful)
    source = root / ".stackos" / directory / "partial.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("presets: [synthetic-private-source-content\n")
    if consumer in {"failed", "unfinished"}:
        written = repo.record_result(
            project,
            key,
            expected_revision=1,
            result=SetupResult(
                attempt_ref="fixture/partial",
                result_ref="fixture/partial-result",
                status=consumer,
                changes=["partial YAML edit"],
            ),
        )
        assert written.successful == previous
        assert written.latest_status == consumer and written.partial_changes
    elif consumer == "reminders":
        written = repo.set_reminders(project, dismiss_setup=True)
        assert written.setup_reminder_dismissed_at
    observed = repo.get(project)
    workflow = observed.workflows[0]
    assert workflow.status == "source_unavailable"
    assert workflow.successful == previous
    assert observed.configured_count == 1
    assert "projectSetup.get" in workflow.diagnostic
    assert len(workflow.diagnostic) <= 800
    assert "synthetic-private-source-content" not in workflow.diagnostic
    with Session(session.get_bind()) as reloaded:
        persisted = ProjectSetupRepository(reloaded).get(project)
        assert persisted.workflows[0].successful == previous
        assert persisted.workflows[0].revision == (2 if consumer in {"failed", "unfinished"} else 1)
        assert bool(persisted.setup_reminder_dismissed_at) == (consumer == "reminders")
    source.unlink()
    recovered = repo.get(project)
    assert recovered.workflows[0].status == "current"
    assert recovered.workflows[0].diagnostic is None
    assert recovered.workflows[0].successful == previous
    assert bool(recovered.setup_reminder_dismissed_at) == (consumer == "reminders")


@pytest.mark.parametrize("transition", ["required", "removed"])
@pytest.mark.parametrize(
    "field,ref_key",
    [
        ("agent_requirements", "agent_preset_ref"),
        ("skill_preset_requirements", "skill_preset_ref"),
    ],
)
def test_historical_optional_selection_transitions_are_updates_and_new_success_stays_strict(
    setup_project, field, ref_key, transition
):
    project, root, repo = setup_project
    key = "engineering.tracked-delivery"
    spec = copy.deepcopy(repo.inspect_sources(project, key)[0].content)
    declaration = next(item for item in spec[field] if item["requirement"] == "required")
    declaration["requirement"] = "optional"
    selected = declaration[ref_key]
    source = root / ".stackos" / "workflows" / "optional.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(yaml.safe_dump(spec))
    applied = verified_result(repo, project, selected_optional_presets=[selected])
    saved = repo.record_result(project, key, expected_revision=0, result=applied)
    previous = copy.deepcopy(saved.successful)
    assert saved.status == "current"
    if transition == "required":
        declaration["requirement"] = "required"
    else:
        spec[field].remove(declaration)
    spec["version"] = "99.0.0"
    source.write_text(yaml.safe_dump(spec))
    current = repo.get(project)
    assert current.update_count == 1
    assert current.workflows[0].status == "update_available"
    assert current.workflows[0].successful == previous
    assert current.workflows[0].available_sources[0].version == "99.0.0"
    with pytest.raises(ValidationError, match="must be declared"):
        repo.inspect_sources(project, key, selected_optional_presets=[selected])
    with pytest.raises(ValidationError, match="must be declared"):
        repo.record_result(project, key, expected_revision=1, result=applied)
    assert repo.get(project).workflows[0].revision == 1
    refreshed = repo.record_result(
        project, key, expected_revision=1, result=verified_result(repo, project)
    )
    assert refreshed.status == "current" and refreshed.revision == 2
    assert refreshed.successful["verification"]["selected_optional_presets"] == []
    assert repo.get(project).update_count == 0
