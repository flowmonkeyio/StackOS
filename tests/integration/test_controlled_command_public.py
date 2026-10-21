"""Real public readback/audit transactions; native success is proved separately."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from itertools import product

import pytest
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.actions import controlled_command as fixed
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
    IdempotencyKey,
    Plugin,
    Run,
    RunPlan,
    WorkItemOwnership,
)
from stackos.mcp.context import bind_context, build_context
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.actions import controlled
from stackos.operations.actions.schemas import ActionExecuteInput
from stackos.repositories.base import ConflictError, RepositoryError
from stackos.repositories.runs import RunRepository
from tests.integration.test_controlled_command_admission import case_factory as case_factory
from tests.integration.test_controlled_process_host import admitted, prepare
from tests.integration.test_controlled_process_terminal import (
    certify_original,
    database_snapshot,
    make_original,
)
from tests.integration.test_controlled_process_terminal import (
    isolated as isolated,
)


def snapshot(case):
    with Session(case.engine) as session:
        return (
            database_snapshot(session),
            tuple(
                row.model_dump(mode="json") for row in session.exec(select(IdempotencyKey)).all()
            ),
        )


def assert_private_absent(value):
    if isinstance(value, dict):
        assert (
            not {
                "controlled_native",
                "approval_decision_digest",
                "continuity_digest",
                "native_quiescence_certificate",
                "native_recovery_private",
                "wrapped_key",
                "storage_context",
            }
            & value.keys()
        )
        for item in value.values():
            assert_private_absent(item)
    elif isinstance(value, list):
        for item in value:
            assert_private_absent(item)


def result_for(case, **changes):
    value = dict(
        schema_version="stackos.controlled-command-result.v2",
        status="held",
        code="controlled_execution_held",
        admission_verified=True,
        command_digest=case.args["input_json"]["command_digest"],
        intent_ref=case.args["input_json"]["intent_ref"],
        host_dispatched=False,
        released=False,
        provider_executed=False,
        outcome_unknown=False,
        cleanup_known=True,
        retry_safe=False,
        work_accepted=False,
    )
    return value | changes


@pytest.fixture
def original_audit(case_factory, tmp_path):
    """Genuine not-spawned terminal producer, never a claimed native success."""
    case = case_factory()
    prepare(case)
    with admitted(case) as admission:
        repo = ActionRepository(case.session)
        manifest = repo.describe(
            project_id=case.project, action_ref="engineering.controlled-proof.v1"
        ).manifest
        row, replay = repo._reserve_background_call(
            project_id=case.project,
            manifest=manifest,
            credential_ref=None,
            run_id=case.run,
            run_plan_id=case.plan,
            run_plan_step_id=case.step,
            idempotency_key=controlled._controlled_intent_key(admission.original),
            request_json=case.args["input_json"],
            provider_context_json=None,
            metadata_json=repo._controlled_metadata(admission.original),
            estimated_cost_cents=0,
            commit_guard=admission.original.revalidate,
        )
        assert not replay
    owner = make_original(case, tmp_path)
    try:
        raw_owner = case.session.get(
            WorkItemOwnership,
            RunRepository(case.session)
            .owner_get(
                project_id=case.project, canonical_item_id=owner.original.binding.canonical_item_id
            )
            .id,
            populate_existing=True,
        )
        attempt = case.session.get(
            Run, owner.original.binding.attempt_run_id, populate_existing=True
        )
        response = result_for(case)
        audit = fixed._ControlledNativeAudit(
            schema_version="stackos.controlled-native-audit.v1",
            action_call_id=row.id,
            binding=asdict(owner.original.binding),
            command_digest=case.args["input_json"]["command_digest"],
            runtime_digest=owner.original.binding.manifest_digest,
            storage_digest=controlled._native_storage(case.settings)[2],
            owner_revision_at_finalize=raw_owner.revision,
            owner_basis_digest=fixed._canonical_digest(
                RunRepository._native_owner_basis(raw_owner, raw_owner.revision)
            ),
            terminal_receipt_digest=owner.authenticated.digest,
            terminal_slot_revision=attempt.metadata_json["native_terminal_slot"]["revision"],
            control_revision=0,
            result_digest=fixed._canonical_digest(response),
        )
        repo._finalize_running_call(
            project_id=case.project,
            action_call_id=row.id,
            credential=None,
            response_json=response,
            metadata_json={
                "controlled_admission": deepcopy(row.metadata_json["controlled_admission"])
                | deepcopy(owner.original.admission),
                "controlled_native": audit.model_dump(),
            },
            status=ActionCallStatus.FAILED,
            cost_cents=0,
            duration_ms=0,
            error="controlled_execution_held",
            commit=True,
        )
        yield case, owner, row
    finally:
        owner.custody.wait_accounted()


def test_recovered_original_unknown_precedes_advanced_owner_fence(original_audit):
    case, owner, row = original_audit
    # Recreate the persisted pre-final-audit boundary observed on daemon loss.
    row.status = ActionCallStatus.RUNNING
    row.response_json = None
    row.metadata_json = {"controlled_admission": row.metadata_json["controlled_admission"]}
    row.error = None
    case.session.add(row)
    case.session.commit()
    current = RunRepository(case.session).owner_get(
        project_id=case.project, canonical_item_id=owner.original.binding.canonical_item_id
    )
    assert current.revision > case.args["input_json"]["expected_revision"]
    assert ActionRepository(case.session).reconcile_running_calls() == 1
    before = snapshot(case)
    with pytest.raises(ConflictError, match=r"^controlled_history_unknown$") as caught:
        asyncio.run(case.dispatch())
    assert caught.value.data == {"action_call_id": row.id}
    assert snapshot(case) == before
    assert case.session.exec(select(ActionCall)).one().response_json == {
        "outcome_unknown": True,
        "retry_safe": False,
    }


@pytest.mark.parametrize(
    "case_id",
    [
        "wrong-action-grant",
        "wrong-argument-grant",
        "wrong-project",
        "wrong-controller",
        "approval-pending",
        "approval-drift",
        "task-stopped",
        "stale-epoch",
        "stale-revision",
        "command-source-drift",
        "runtime-drift",
        "environment-drift",
    ],
)
def test_public_native_forward_denial(case_factory, case_id, monkeypatch):
    case = case_factory()
    prepare(case)
    ActionRepository(case.session).describe(
        project_id=case.project, action_ref="engineering.controlled-proof.v1"
    )
    args = deepcopy(case.args)
    if case_id == "approval-pending":
        # Prepare the legitimate policy first, then install the durable pending
        # test state. This is not a product approved-to-pending transition.
        approval = case.session.exec(
            select(ApprovalRequest).where(ApprovalRequest.run_plan_id == case.plan)
        ).one()
        approval.status = ApprovalRequestStatus.PENDING
        approval.decision_json = None
        approval.decided_by = None
        approval.decided_at = None
        case.session.add(approval)
        case.session.commit()
    elif case_id in {"wrong-action-grant", "wrong-argument-grant"}:
        plan = case.session.get(RunPlan, case.plan)
        value = deepcopy(plan.grant_snapshot_json)
        grant = value["mcp_tool_grants"][0]
        if case_id == "wrong-action-grant":
            grant["action_refs"] = ["engineering.echo"]
        else:
            grant["plugin_slug"] = "foreign"
        plan.grant_snapshot_json = value
        case.session.add(plan)
        case.session.commit()
    elif case_id == "wrong-project":
        args["project_id"] += 1
    elif case_id == "wrong-controller":
        args["run_token"] = "foreign-token"
    elif case_id == "approval-drift":
        case.approval(change=lambda d: d["controlled_command"].update(command_digest="0" * 64))
    elif case_id == "task-stopped":
        case.stop()
    elif case_id in {"stale-epoch", "stale-revision"}:
        args["input_json"][
            "expected_epoch" if case_id == "stale-epoch" else "expected_revision"
        ] += 1
    elif case_id == "command-source-drift":
        (case.source / fixed._CONTROLLED_SOURCE_FILES[0]).write_text("changed source\n")
    elif case_id == "runtime-drift":
        case.approval(change=lambda d: d["controlled_host"].update(runtime_digest="0" * 64))
    elif case_id == "environment-drift":
        case.approval(
            change=lambda d: d["controlled_command"]["command"]["environment"].update(
                LANG="changed"
            )
        )
    before = snapshot(case)
    if case_id == "approval-pending":
        case.session.refresh(approval)
        assert approval.status == ApprovalRequestStatus.PENDING
        assert approval.decision_json is None
        pending_before = approval.model_dump(mode="json")
    with pytest.raises((RepositoryError, ToolNotGrantedError)):
        asyncio.run(case.dispatch(args))
    assert snapshot(case) == before
    if case_id == "approval-pending":
        case.session.refresh(approval)
        assert approval.model_dump(mode="json") == pending_before


@pytest.mark.parametrize(
    "case_id",
    ["valid-direct-request", "registry", "background", "durable", "foreign-context-settings"],
)
def test_public_native_bypass_denied(case_factory, case_id, monkeypatch):
    case = case_factory()
    prepare(case)
    ActionRepository(case.session).describe(
        project_id=case.project, action_ref="engineering.controlled-proof.v1"
    )
    args = deepcopy(case.args)
    if case_id == "foreign-context-settings":
        with bind_context(build_context(args, case.session)) as ctx:
            ctx.extras["settings"] = case.settings.model_copy()
            with pytest.raises(ConflictError, match="controlled_host_unavailable"):
                controlled._issue_native_admission(ActionExecuteInput.model_validate(args), ctx)
        return
    if case_id == "background":
        from stackos.repositories.plugins import PluginRepository

        sync = PluginRepository.sync_builtin_plugins

        def mode(repo):
            value = sync(repo)
            action = repo._s.exec(
                select(Action)
                .join(Plugin, Action.plugin_id == Plugin.id)
                .where(Plugin.slug == "engineering", Action.key == "controlled-proof.v1")
            ).one()
            action.config_json = action.config_json | {"execution_mode": "background"}
            repo._s.add(action)
            repo._s.commit()
            return value

        monkeypatch.setattr(PluginRepository, "sync_builtin_plugins", mode)
    elif case_id == "durable":
        args["account_interval_seconds"] = 1
    elif case_id == "valid-direct-request":
        args.pop("run_token")
        args.update(confirm_direct=True, intent_summary="Synthetic direct boundary denial")
    before = snapshot(case)
    if case_id == "registry":
        from stackos.actions import ActionConnectorRequest
        from stackos.actions.connectors import ActionConnectorError

        with pytest.raises(ActionConnectorError, match="controlled_host_unavailable"):
            asyncio.run(
                case.connectors.get("controlled-command").execute(
                    ActionConnectorRequest(
                        project_id=case.project,
                        action_ref="engineering.controlled-proof.v1",
                        plugin_slug="engineering",
                        action_key="controlled-proof.v1",
                        provider_key=None,
                        operation="controlled-proof.v1",
                        input_json=args["input_json"],
                        config_json={},
                    )
                )
            )
    else:
        with pytest.raises((RepositoryError, ToolNotGrantedError)):
            asyncio.run(
                case.dispatch(
                    args,
                    operation="action.run"
                    if case_id == "valid-direct-request"
                    else "action.execute",
                )
            )
    assert snapshot(case) == before


@pytest.mark.parametrize(
    "case_id",
    [
        "legacy-v1",
        "running-none",
        "orphan-unknown",
        "native-completed-shape",
        "native-held-shape",
        "native-unknown-shape",
        "extra-key",
        "bool-as-int",
    ],
)
def test_public_native_audit_projection(case_factory, case_id):
    case = case_factory()
    asyncio.run(case.dispatch())
    row = case.session.exec(select(ActionCall)).one()
    if case_id == "running-none":
        row.status = ActionCallStatus.RUNNING
        row.response_json = None
    elif case_id == "orphan-unknown":
        row.error = "daemon-restart-orphan"
        row.response_json = {"outcome_unknown": True, "retry_safe": False}
    elif case_id != "legacy-v1":
        output = result_for(case)
        if case_id == "native-completed-shape":
            output.update(
                status="succeeded",
                code="controlled_command_completed",
                host_dispatched=True,
                released=True,
            )
            row.status = ActionCallStatus.SUCCESS
        elif case_id == "native-unknown-shape":
            output.update(
                code="controlled_outcome_unknown",
                host_dispatched=None,
                outcome_unknown=True,
                cleanup_known=False,
            )
        elif case_id == "extra-key":
            output["private"] = "never expose"
        elif case_id == "bool-as-int":
            output["admission_verified"] = 1
        row.response_json = output
    case.session.add(row)
    case.session.commit()
    repo = ActionRepository(case.session)
    if case_id in {"extra-key", "bool-as-int"}:
        with pytest.raises(ConflictError, match="controlled_history_unknown"):
            repo.get_call(project_id=case.project, action_call_id=row.id)
        return
    direct = repo.get_call(project_id=case.project, action_call_id=row.id)
    raw = repo._call_out(row)
    got = asyncio.run(
        case.dispatch(
            {"project_id": case.project, "action_call_id": row.id, "response_mode": "raw"},
            operation="actionCall.get",
        )
    )
    queried = asyncio.run(
        case.dispatch(
            {"project_id": case.project, "response_mode": "raw"}, operation="actionCall.query"
        )
    )
    for value in (direct.model_dump(mode="json"), raw.model_dump(mode="json"), got, queried):
        assert_private_absent(value)
    assert got["status"] == row.status.value


def test_fixed_report_default_off_and_replay_create_no_workspace(case_factory, tmp_path):
    from stackos.config import Settings
    from tests.support.controlled_report import select_report

    settings = Settings(
        _env_file=None, state_dir=tmp_path / "report-state", data_dir=tmp_path / "report-data"
    )
    case = case_factory(settings=settings)
    command = select_report(case)
    assert not settings.state_dir.exists()
    output = asyncio.run(case.dispatch())
    assert output["data"]["output_json"]["code"] == "controlled_host_unavailable"
    assert output["data"]["output_json"]["host_dispatched"] is False
    assert "report" not in output["data"]["output_json"]
    replay = asyncio.run(case.dispatch())
    assert replay["data"]["replayed"] is True
    assert replay["data"]["output_json"] == output["data"]["output_json"]
    assert not settings.state_dir.exists() and not command.report_directory.exists()
    assert_private_absent(output)
    assert_private_absent(replay)


READBACK_CASES = [
    "known-final-readback",
    "inflight",
    "unknown",
    "approval-drift",
    "foreign-owner-progress",
    "foreign-original",
    "missing-cached-original",
]


@pytest.mark.parametrize(
    "route,case_id",
    list(product(["repository", "dispatcher-cache"], READBACK_CASES)),
    ids=[r + "-" + c for r, c in product(["repository", "dispatcher-cache"], READBACK_CASES)],
)
def test_public_native_readback_never_launches(original_audit, route, case_id, monkeypatch):
    case, owner, row = original_audit
    certify_original(owner)
    if case_id == "inflight":
        row.status = ActionCallStatus.RUNNING
        row.response_json = None
    elif case_id == "unknown":
        row.response_json = result_for(
            case, code="controlled_outcome_unknown", outcome_unknown=True, cleanup_known=False
        )
    elif case_id == "approval-drift":
        case.approval(change=lambda d: d.update(review_revision=2))
    elif case_id == "foreign-owner-progress":
        raw = case.session.get(
            WorkItemOwnership,
            RunRepository(case.session)
            .owner_get(
                project_id=case.project, canonical_item_id=owner.original.binding.canonical_item_id
            )
            .id,
        )
        raw.revision += 1
        case.session.add(raw)
    elif case_id == "foreign-original":
        row.run_id += 100
    case.session.add(row)
    case.session.commit()
    cached = {
        "data": {
            "action_call": {"id": row.id + 100 if case_id == "missing-cached-original" else row.id}
        },
        "run_id": case.run,
        "project_id": case.project,
    }
    args = deepcopy(case.args)
    if route == "dispatcher-cache":
        args["idempotency_key"] = row.idempotency_key
        case.session.add(
            IdempotencyKey(
                project_id=case.project,
                tool_name="action.execute",
                idempotency_key=row.idempotency_key,
                run_id=case.run,
                response_json=cached,
            )
        )
        case.session.commit()
    elif case_id == "missing-cached-original":
        cached["data"]["action_call"]["id"] = row.id + 100
        reservation = case.session.exec(
            select(IdempotencyKey).where(IdempotencyKey.tool_name == "action.background")
        ).one()
        reservation.response_json = reservation.response_json | {"action_call_id": row.id + 100}
        case.session.add(reservation)
        case.session.commit()

    def forbidden(*a, **k):
        raise AssertionError("readback reached new-execution issuer")

    monkeypatch.setattr(controlled, "_issue_controlled_authority", forbidden)
    before = snapshot(case)
    if case_id == "known-final-readback":
        result = asyncio.run(case.dispatch(args))
        assert result["data"]["replayed"] is True
        assert result["data"]["action_call"]["id"] == row.id
        assert_private_absent(result)
    else:
        with pytest.raises((RepositoryError, ToolNotGrantedError, ValueError)):
            if route == "repository" and case_id == "missing-cached-original":
                # Actual losing reservation path must reject its contradictory
                # durable link. Do not invent a public cached-id argument.
                repo = ActionRepository(case.session)
                manifest = repo.describe(
                    project_id=case.project, action_ref="engineering.controlled-proof.v1"
                ).manifest
                repo._reserve_background_call(
                    project_id=case.project,
                    manifest=manifest,
                    credential_ref=None,
                    run_id=case.run,
                    run_plan_id=case.plan,
                    run_plan_step_id=case.step,
                    idempotency_key=row.idempotency_key,
                    request_json=row.request_json,
                    provider_context_json=None,
                    metadata_json=row.metadata_json,
                    estimated_cost_cents=0,
                    commit_guard=lambda: pytest.fail("duplicate reached new winner guard"),
                )
            else:
                asyncio.run(case.dispatch(args))
    assert snapshot(case) == before


@pytest.mark.parametrize(
    "case_id", ["outer-private", "foreign-project", "foreign-run", "bool-call-id"]
)
def test_public_cached_raw_envelope_is_canonical(original_audit, case_id):
    case, owner, row = original_audit
    certify_original(owner)
    cached = {
        "data": {"action_call": {"id": row.id}},
        "project_id": case.project,
        "run_id": case.run,
    }
    if case_id == "outer-private":
        cached["native_recovery_private"] = {"never_public": "synthetic"}
    elif case_id == "foreign-project":
        cached["project_id"] += 1
    elif case_id == "foreign-run":
        cached["run_id"] += 1
    else:
        cached["data"]["action_call"]["id"] = True
    case.session.add(
        IdempotencyKey(
            project_id=case.project,
            tool_name="action.execute",
            idempotency_key=row.idempotency_key,
            run_id=case.run,
            response_json=cached,
        )
    )
    case.session.commit()
    args = deepcopy(case.args)
    args.update(idempotency_key=row.idempotency_key, response_mode="raw")
    before = snapshot(case)
    if case_id == "outer-private":
        result = asyncio.run(case.dispatch(args))
        assert set(result) == {"data", "run_id", "project_id", "idempotency_replay"}
        assert result["idempotency_replay"] is True
        assert result["run_id"] == case.run and result["project_id"] == case.project
        assert result["data"]["replayed"] is True
        assert_private_absent(result)
    else:
        with pytest.raises(ConflictError, match="controlled cached"):
            asyncio.run(case.dispatch(args))
    assert snapshot(case) == before


@pytest.mark.parametrize(
    "case_id",
    [
        "ambient-other",
        "ambient-unconfigured",
        "canonical-missing",
        "canonical-mode",
        "seed-drift",
        "state-drift",
    ],
)
def test_public_native_explicit_seed_and_storage(case_factory, case_id, monkeypatch, tmp_path):
    from stackos.config import Settings
    from stackos.crypto import aes_gcm

    case = case_factory()
    prepare(case)
    foreign = Settings(
        _env_file=None, state_dir=tmp_path / "foreign-state", data_dir=tmp_path / "foreign-data"
    )
    foreign.state_dir.mkdir(mode=0o700)
    foreign.seed_path.write_bytes(b"B" * 32)
    foreign.seed_path.chmod(0o600)
    monkeypatch.setattr("stackos.config.get_settings", lambda: foreign)
    monkeypatch.setattr(
        aes_gcm,
        "_default_seed_path",
        None if case_id == "ambient-unconfigured" else foreign.seed_path,
    )
    if case_id == "canonical-missing":
        case.settings.seed_path.unlink()
    if case_id == "canonical-mode":
        case.settings.seed_path.chmod(0o644)
    before = snapshot(case)
    if case_id in {"canonical-missing", "canonical-mode"}:
        with (
            pytest.raises(ConflictError, match="native original storage is unavailable"),
            admitted(case),
        ):
            pytest.fail("invalid explicit seed admitted")
    else:
        with admitted(case) as admission:
            assert admission.settings is case.settings
            assert admission.storage == (str(case.settings.state_dir), str(case.settings.seed_path))
            same = admission.seed == case.settings.seed_path.read_bytes()
            assert same
            assert "seed=" not in repr(admission)
            if case_id == "seed-drift":
                case.settings.seed_path.write_bytes(b"D" * 32)
            if case_id == "state-drift":
                case.settings.state_dir = foreign.state_dir
            if case_id in {"seed-drift", "state-drift"}:
                with pytest.raises(ConflictError, match="native storage configuration changed"):
                    controlled._revalidate_native_context(admission)
            else:
                controlled._revalidate_native_context(admission)
    assert snapshot(case) == before


@pytest.mark.parametrize(
    "case_id",
    [
        "unlinked-terminal",
        "metadata-only-claim",
        "second-session-drift",
        "running-call",
        "audit-after-certificate-drift",
    ],
)
def test_native_effect_readback_requires_original_terminal(original_audit, case_id):
    case, owner, row = original_audit
    if case_id == "audit-after-certificate-drift":
        certify_original(owner)
    binding = owner.original.binding
    if case_id == "second-session-drift":
        with Session(case.engine) as other:
            changed = other.get(ActionCall, row.id)
            changed.status = ActionCallStatus.RUNNING
            other.add(changed)
            other.commit()
    elif case_id == "running-call":
        row.status = ActionCallStatus.RUNNING
        row.response_json = None
        case.session.add(row)
        case.session.commit()
    elif case_id == "metadata-only-claim":
        attempt = case.session.get(Run, binding.attempt_run_id)
        attempt.metadata_json = attempt.metadata_json | {"native_terminal_facts": None}
        case.session.add(attempt)
        case.session.commit()
    elif case_id == "unlinked-terminal":
        value = deepcopy(row.metadata_json)
        value["controlled_native"]["terminal_receipt_digest"] = "0" * 64
        row.metadata_json = value
        case.session.add(row)
        case.session.commit()
    else:
        row.error = "changed after certificate"
        case.session.add(row)
        case.session.commit()
    before = snapshot(case)
    if case_id == "audit-after-certificate-drift":
        with pytest.raises(ConflictError):
            RunRepository(case.session)._assert_native_certificate_current(
                case.session.exec(select(WorkItemOwnership)).one()
            )
    else:
        value = ActionRepository(case.session)._controlled_effect_snapshot(
            project_id=case.project,
            bindings=(binding,),
            effect_identity=owner.custody.admission.continuity["effect_identity"],
            command_manifests=(owner.original.manifest,),
        )
        assert value.status == "unknown"
    assert snapshot(case) == before


@pytest.mark.parametrize(
    "case_id",
    ["two-session-one-winner", "different-payload-same-intent", "lost-ack-canonical-readback"],
)
def test_public_native_intent_cas(case_factory, case_id, monkeypatch):
    # Existing real two-Session winner test owns the transactional race; retain
    # that actual producer rather than emulate a successful host callback.
    from tests.integration.test_controlled_command_admission import (
        test_controlled_concurrent_intent_has_one_audit_winner,
    )

    case = case_factory()
    if case_id == "two-session-one-winner":
        test_controlled_concurrent_intent_has_one_audit_winner(case)
        return
    asyncio.run(case.dispatch())
    before = snapshot(case)
    args = deepcopy(case.args)
    if case_id == "different-payload-same-intent":
        args["input_json"]["scope_digest"] = "0" * 64
    if case_id == "different-payload-same-intent":
        with pytest.raises((RepositoryError, ToolNotGrantedError)):
            asyncio.run(case.dispatch(args))
    else:
        out = asyncio.run(case.dispatch(args))
        assert out["data"]["replayed"] is True
    assert snapshot(case) == before
