"""Public fixed-action producer through genuine native roles; no live daemon."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from sqlmodel import Session, select

from stackos import controlled_process_host as host
from stackos.actions import ActionRepository
from stackos.db.models import (
    Action,
    ActionCall,
    ActionCallStatus,
    IdempotencyKey,
    Plugin,
    ProjectEvent,
    Run,
    TrackerTask,
    WorkItemOwnership,
)
from stackos.operations.actions import controlled
from stackos.repositories.base import ConflictError, RepositoryError
from stackos.repositories.runs import RunRepository
from tests.integration.test_controlled_command_admission import case_factory as case_factory
from tests.integration.test_controlled_process_host import prepare
from tests.integration.test_controlled_process_native import (
    lose_grant,
    save,
)
from tests.integration.test_controlled_process_native import (
    native_storage as native_storage,
)


def rows(case):
    with Session(case.engine) as session:
        return tuple(
            (
                model.__name__,
                tuple(
                    row.model_dump(mode="json")
                    for row in session.exec(select(model).order_by(model.id)).all()
                ),
            )
            for model in (
                ActionCall,
                Run,
                WorkItemOwnership,
                ProjectEvent,
                IdempotencyKey,
                TrackerTask,
            )
        )


def private_absent(value):
    if isinstance(value, dict):
        assert (
            not {
                "controlled_native",
                "native_recovery_private",
                "native_quiescence_certificate",
                "wrapped_key",
                "approval_decision_digest",
                "continuity_digest",
                "storage_context",
            }
            & value.keys()
        )
        for item in value.values():
            private_absent(item)
    elif isinstance(value, list):
        for item in value:
            private_absent(item)


@pytest.fixture
def public_case(case_factory, native_storage, monkeypatch, tmp_path):
    # Resolve after native_storage installs its actual synthetic Settings A;
    # a module-imported alias would retain the unrelated default resolver.
    from stackos.config import get_settings

    settings = get_settings()
    state, clock = native_storage
    assert settings.controlled_host_enabled is True
    assert settings.state_dir == state
    assert settings.seed_path == state / "seed.bin"
    case = case_factory(settings=settings)
    prepare(
        case, boot=clock.boot_uuid, deadline=clock.continuous_ns + 5_000_000_000, attempt_limit=1
    )
    action = case.session.exec(
        select(Action)
        .join(Plugin, Action.plugin_id == Plugin.id)
        .where(Plugin.slug == "engineering", Action.key == "controlled-proof.v1")
    ).one()
    config = deepcopy(action.config_json)
    config.pop("controlled_host", None)
    action.config_json = config
    case.session.add(action)
    case.session.commit()
    # Real canonical A must remain selected even if the daemon-global crypto
    # and Settings observations independently point to legitimate owned B.
    from stackos.config import Settings
    from stackos.crypto import aes_gcm

    foreign = Settings(
        _env_file=None, state_dir=tmp_path / "ambient-state", data_dir=tmp_path / "ambient-data"
    )
    foreign.state_dir.mkdir(mode=0o700)
    foreign.seed_path.write_bytes(b"B" * 32)
    foreign.seed_path.chmod(0o600)
    monkeypatch.setattr(aes_gcm, "_default_seed_path", foreign.seed_path)
    monkeypatch.setattr("stackos.config.get_settings", lambda: foreign)
    actual = host._run_native_control
    observed = []

    async def capture(admission, *, custody):
        result = await actual(admission, custody=custody)
        observed.append(result)
        save(tmp_path, "public-native-" + str(len(observed)), result)
        return result

    monkeypatch.setattr(host, "_run_native_control", capture)
    yield case, observed
    # Product owner remains responsible even if the caller's test assertion fails.
    for result in observed:
        result.custody.wait_accounted()
        assert result.custody.accounted
        assert result.custody.process is not None and result.custody.process.returncode is not None
        assert all(fd.state == "closed" for fd in result.custody.owned_fds)
        (tmp_path / "public-final-custody.json").write_text(
            json.dumps(result.custody.snapshot(), sort_keys=True) + "\n"
        )


def assert_accounted(observed):
    assert len(observed) == 1
    result = observed[0]
    assert result.cleanup_known and result.custody.accounted
    assert result.custody.process.returncode == 0
    assert all(fd.state == "closed" for fd in result.custody.owned_fds)
    cleanup = [event for event in result.evidence if event.get("event") == "cleanup"]
    assert cleanup and all(cleanup[-1]["reaped"].values())
    return result


def test_public_native_noop_completes_and_replays_without_second_launch(public_case, monkeypatch):
    case, observed = public_case
    # A genuine transport key covers the actual dispatcher cached route too.
    key = "controlled:" + controlled._canonical_digest(
        dict(
            project_id=case.project,
            controller_run_id=case.run,
            canonical_item_id=case.args["input_json"]["canonical_item_id"],
            epoch=case.args["input_json"]["expected_epoch"],
            intent_ref=case.args["input_json"]["intent_ref"],
            dry_run=False,
        )
    )
    args = deepcopy(case.args)
    args["idempotency_key"] = key
    out = asyncio.run(case.dispatch(args))
    assert out["data"]["output_json"]["status"] == "succeeded"
    assert out["data"]["output_json"]["work_accepted"] is False
    assert "certified" not in out["data"]["output_json"]
    result = assert_accounted(observed)
    assert result.terminal_owner.authenticated.fact.child_returns["worker"] == 0
    raw = case.session.exec(select(WorkItemOwnership)).one()
    assert raw.quiescence_revision == raw.revision
    assert raw.continuity_json["attempts_used"] == 1
    assert (
        raw.continuity_json["native_quiescence_certificate"]["effect_snapshot"]["status"]
        == "known_no_effect"
    )
    before = rows(case)

    def new_execution(*a, **k):
        raise AssertionError("replay reached strict new launch issuer")

    monkeypatch.setattr(controlled, "_issue_controlled_authority", new_execution)
    for request in (case.args, args):
        replay = asyncio.run(case.dispatch(request))
        assert replay["data"]["replayed"] is True
        assert replay["data"]["action_call"]["id"] == out["data"]["action_call"]["id"]
        assert replay["data"]["output_json"] == out["data"]["output_json"]
        private_absent(replay)
        assert rows(case) == before and len(observed) == 1
    private_absent(out)
    # Actual persisted wrapping and recovery select original A, never ambient B.
    from stackos import controlled_process_receipt as receipt
    from stackos.crypto.aes_gcm import decrypt
    from stackos.crypto.seed import load_seed

    original = result.terminal_owner.original
    box = original.wrapped_key
    key = decrypt(
        bytes.fromhex(box["ciphertext"]),
        nonce=bytes.fromhex(box["nonce"]),
        project_id=case.project,
        kind="controlled-host-key:" + receipt._name(original.binding)[:-8],
        seed=load_seed(case.settings.seed_path),
    )
    same_key = key == result.terminal_owner.key
    assert same_key
    case.settings.controlled_host_enabled = False
    before = rows(case)
    recovered = host._recover_native_terminal(
        case.session,
        project_id=case.project,
        canonical_item_id=original.binding.canonical_item_id,
        attempt_run_id=original.binding.attempt_run_id,
        evidence_ref="native:terminal",
        exit_witness=host._OriginalExitWitness(
            result.custody.identity, result.custody.process.returncode, result.custody
        ),
    )
    assert recovered.state == "replayed" and recovered.certificate is not None
    assert rows(case) == before
    disabled_replay = asyncio.run(case.dispatch())
    assert disabled_replay["data"]["replayed"] is True and rows(case) == before
    # A changed explicit seed is a hold even though an ambient B exists. Only
    # this disposable seed is restored; receipt F9 is never rewritten/reset.
    try:
        case.settings.seed_path.write_bytes(b"D" * 32)
        wrong_seed = host._recover_native_terminal(
            case.session,
            project_id=case.project,
            canonical_item_id=original.binding.canonical_item_id,
            attempt_run_id=original.binding.attempt_run_id,
            evidence_ref="native:terminal",
            exit_witness=host._OriginalExitWitness(
                result.custody.identity, result.custody.process.returncode, result.custody
            ),
        )
        assert wrong_seed.state == "held" and wrong_seed.issues == ("terminal_key_unknown",)
        assert rows(case) == before
    finally:
        case.settings.seed_path.write_bytes(result.custody.admission.seed)
    original_state = case.settings.state_dir
    case.settings.state_dir = original_state.parent / "unrelated-original-context"
    held = host._recover_native_terminal(
        case.session,
        project_id=case.project,
        canonical_item_id=original.binding.canonical_item_id,
        attempt_run_id=original.binding.attempt_run_id,
        evidence_ref="native:terminal",
        exit_witness=host._OriginalExitWitness(
            result.custody.identity, result.custody.process.returncode, result.custody
        ),
    )
    assert held.state == "held" and held.issues == ("terminal_key_unknown",)
    assert rows(case) == before and not case.settings.state_dir.exists()
    case.settings.state_dir = original_state


def test_public_native_report_produces_private_candidate_and_replays(
    public_case, tmp_path, monkeypatch
):
    from tests.support.controlled_report import select_report, verify_report

    case, observed = public_case
    command = select_report(case)
    input_path = (
        command.source_root / "plugins/engineering/fixtures/controlled-status-input-v1.json"
    )
    original = input_path.read_bytes()
    sentinel = tmp_path / "report-sentinel"
    sentinel.write_bytes(b"unchanged sentinel\n")
    directory = command.report_directory
    assert not directory.exists()
    key = "controlled:" + controlled._canonical_digest(
        dict(
            project_id=case.project,
            controller_run_id=case.run,
            canonical_item_id=case.args["input_json"]["canonical_item_id"],
            epoch=case.args["input_json"]["expected_epoch"],
            intent_ref=case.args["input_json"]["intent_ref"],
            dry_run=False,
        )
    )
    args = deepcopy(case.args) | {"idempotency_key": key}
    output = asyncio.run(case.dispatch(args))
    result = assert_accounted(observed)
    assert result.terminal_owner.authenticated.fact.child_returns["worker"] == 0
    raw = output["data"]["output_json"]
    assert raw["status"] == "succeeded" and raw["work_accepted"] is False
    assert raw["report"]["accepted"] is False
    assert raw["report"]["candidate_ref"] == "controlled-report:" + directory.name
    assert str(directory) not in json.dumps(output)
    verify_report(directory, input_path, original, sentinel)
    before = rows(case)

    def forbid(*args, **kwargs):
        raise AssertionError("report replay attempted another launch")

    monkeypatch.setattr(host, "_run_native_control", forbid)
    for request in (case.args, args):
        replay = asyncio.run(case.dispatch(request))
        assert replay["data"]["replayed"] is True
        assert replay["data"]["output_json"] == raw
        assert rows(case) == before and len(observed) == 1
        assert str(directory) not in json.dumps(replay)
        private_absent(replay)
        verify_report(directory, input_path, original, sentinel)


def test_public_native_task_stop_before_release_preserves_known_inert_cleanup(
    public_case, monkeypatch
):
    case, observed = public_case
    original = host._Channel.send

    def stop(channel, kind, payload, deadline):
        if kind == "release":
            case.stop()
            raise host._ControlError("native_fixture_stop")
        return original(channel, kind, payload, deadline)

    monkeypatch.setattr(host._Channel, "send", stop)
    try:
        out = asyncio.run(case.dispatch())
    except ConflictError as exc:
        assert str(exc) == "controlled_quiescence_held"
        out = None
    result = assert_accounted(observed)
    assert not result.release_sent and "native_fixture_stop" in result.issues
    assert result.terminal_owner.authenticated.fact.execution_unknown is False
    row = case.session.exec(select(ActionCall)).one()
    assert row.status == ActionCallStatus.FAILED
    output = (
        ActionRepository(case.session)
        .get_call(project_id=case.project, action_call_id=row.id)
        .response_json
    )
    assert (
        output["released"] is False
        and output["work_accepted"] is False
        and output["cleanup_known"] is True
    )
    assert (
        case.session.get(TrackerTask, case.task, populate_existing=True).metadata_json[
            "ownership_continuity"
        ]["stopped"]
        is True
    )
    before = rows(case)
    with pytest.raises(ConflictError):
        asyncio.run(case.dispatch())
    assert rows(case) == before and len(observed) == 1
    if out is not None:
        private_absent(out)


def test_public_native_grant_loss_after_release_keeps_original_safety_custody(
    public_case, monkeypatch
):
    case, observed = public_case
    receive = host._Channel.receive
    lost = []

    def revoke(channel, **kwargs):
        value = receive(channel, **kwargs)
        if value is not None and value[0] == "released" and not lost:
            lose_grant(case)
            lost.append(True)
        return value

    monkeypatch.setattr(host._Channel, "receive", revoke)
    try:
        asyncio.run(case.dispatch())
    except ConflictError as exc:
        assert str(exc) == "controlled_quiescence_held"
    result = assert_accounted(observed)
    assert lost and result.release_sent
    before = rows(case)
    from stackos.mcp.errors import ToolNotGrantedError

    with pytest.raises((ConflictError, ToolNotGrantedError)):
        asyncio.run(case.dispatch())
    assert rows(case) == before and len(observed) == 1


def test_public_native_audit_commit_lost_ack_does_not_repeat_host(public_case, monkeypatch):
    case, observed = public_case
    original = ActionRepository._finalize_running_call
    committed = []

    def lose(repo, **kwargs):
        row = original(repo, **kwargs)
        if row.response_json.get("schema_version") == "stackos.controlled-command-result.v2":
            committed.append(row.id)
            raise RuntimeError("synthetic audit acknowledgment lost")
        return row

    with monkeypatch.context() as patch:
        patch.setattr(ActionRepository, "_finalize_running_call", lose)
        with pytest.raises(RepositoryError, match="operation handler failed") as caught:
            asyncio.run(case.dispatch())
        assert type(caught.value.__cause__) is RuntimeError
        assert str(caught.value.__cause__) == "synthetic audit acknowledgment lost"
    assert_accounted(observed)
    assert len(committed) == 1
    before = rows(case)
    replay = asyncio.run(case.dispatch())
    assert replay["data"]["replayed"] is True
    assert replay["data"]["action_call"]["id"] == committed[0]
    assert rows(case) == before and len(observed) == 1


def test_public_native_audit_persistence_failure_preserves_unknown_original(
    public_case, monkeypatch
):
    case, observed = public_case
    original = ActionRepository._finalize_running_call

    def fail(repo, **kwargs):
        if kwargs["response_json"].get("schema_version") == "stackos.controlled-command-result.v2":
            raise OSError("synthetic audit persistence failure")
        return original(repo, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ActionRepository, "_finalize_running_call", fail)
        with pytest.raises(RepositoryError, match="operation handler failed") as caught:
            asyncio.run(case.dispatch())
        assert type(caught.value.__cause__) is OSError
        assert str(caught.value.__cause__) == "synthetic audit persistence failure"
    assert_accounted(observed)
    row = case.session.exec(select(ActionCall)).one()
    assert row.status == ActionCallStatus.RUNNING and row.response_json is None
    before = rows(case)
    with pytest.raises(ConflictError, match="controlled_call_in_flight"):
        asyncio.run(case.dispatch())
    assert rows(case) == before and len(observed) == 1


def test_public_native_certificate_ack_loss_keeps_audit_immutable(public_case, monkeypatch):
    case, observed = public_case
    original = RunRepository._certify_owner_quiescence
    committed = []

    def lose(repo, **kwargs):
        result = original(repo, **kwargs)
        committed.append(result.revision)
        raise RuntimeError("synthetic certificate acknowledgment lost")

    with monkeypatch.context() as patch:
        patch.setattr(RunRepository, "_certify_owner_quiescence", lose)
        with pytest.raises(ConflictError, match="controlled_quiescence_held"):
            asyncio.run(case.dispatch())
    assert_accounted(observed)
    assert len(committed) == 1
    before = rows(case)
    replay = asyncio.run(case.dispatch())
    assert replay["data"]["replayed"] is True
    raw = case.session.exec(select(WorkItemOwnership)).one()
    assert raw.quiescence_revision == committed[0]
    assert rows(case) == before and len(observed) == 1
    private_absent(replay)


@pytest.mark.parametrize("case_id", ["progressed-original", "contradictory-reservation"])
def test_public_native_reservation_loser_reads_original_after_winner_progress(
    public_case, case_id, monkeypatch
):
    case, observed = public_case
    reserve = ActionRepository._reserve_background_call
    issue = controlled._issue_controlled_authority
    native_issue = controlled._issue_native_admission
    main_thread = threading.get_ident()
    ready = threading.Event()
    finished = threading.Event()
    winner_failed = []

    def delayed_reservation(repo, **kwargs):
        if threading.get_ident() != main_thread:
            # Both real issuers ran before the winner; hold immediately before
            # the actual insert/uniqueness decision, not before entry routing.
            ready.set()
            finished.wait()
            if winner_failed:
                raise ConflictError("fixture winner failed")
        return reserve(repo, **kwargs)

    def strict_issuer(*args, **kwargs):
        if threading.get_ident() != main_thread and finished.is_set():
            raise AssertionError("reservation loser repeated strict forward issuer")
        return issue(*args, **kwargs)

    def native_issuer(*args, **kwargs):
        if threading.get_ident() != main_thread and finished.is_set():
            raise AssertionError("reservation loser minted new native admission")
        return native_issue(*args, **kwargs)

    def loser():
        with Session(case.engine) as session:
            return asyncio.run(case.dispatch(session=session))

    monkeypatch.setattr(ActionRepository, "_reserve_background_call", delayed_reservation)
    monkeypatch.setattr(controlled, "_issue_controlled_authority", strict_issuer)
    monkeypatch.setattr(controlled, "_issue_native_admission", native_issuer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(loser)
        future.add_done_callback(lambda _finished: ready.set())
        try:
            ready.wait()
            if future.done():
                future.result()
                pytest.fail("loser did not reach the actual reservation boundary")
            winner = asyncio.run(case.dispatch())
            result = assert_accounted(observed)
            assert result.release_sent
            owner = case.session.exec(select(WorkItemOwnership)).one()
            assert (
                owner.quiescence_revision == owner.revision
                and owner.continuity_json["attempts_used"] == 1
            )
            if case_id == "contradictory-reservation":
                reservation = case.session.exec(
                    select(IdempotencyKey).where(IdempotencyKey.tool_name == "action.background")
                ).one()
                reservation.response_json = reservation.response_json | {
                    "action_call_id": winner["data"]["action_call"]["id"] + 100
                }
                case.session.add(reservation)
                case.session.commit()
            before = rows(case)
        except BaseException:
            winner_failed.append(True)
            raise
        finally:
            finished.set()
        if case_id == "contradictory-reservation":
            with pytest.raises(
                ConflictError, match="background action reservation is missing its action call"
            ):
                future.result()
        else:
            replay = future.result()
            assert replay["data"]["replayed"] is True
            assert replay["data"]["action_call"]["id"] == winner["data"]["action_call"]["id"]
            assert replay["data"]["output_json"] == winner["data"]["output_json"]
            private_absent(replay)
        assert rows(case) == before and len(observed) == 1
        assert len(case.session.exec(select(ActionCall)).all()) == 1
