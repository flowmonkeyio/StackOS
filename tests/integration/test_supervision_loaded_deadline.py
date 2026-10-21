"""Explicit Darwin-only loaded frozen/deadline and exact owned-daemon loss proof.

Preparation only until Main freezes/reviews this source and capture. This file
adds no production command. Host sleep/reboot/escape containment are excluded.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress

import httpx
import pytest
from sqlmodel import Session, select

from stackos import controlled_process_clock as clock
from stackos import controlled_process_host as host
from stackos import controlled_process_identity as identity
from stackos.db.models import ActionCall, Run, WorkItemOwnership
from tests.support.loaded_deadline_fixture import prepare_copy, setup_case
from tests.support.supervision_loaded import run_scenario

pytestmark = pytest.mark.skipif(
    os.environ.get("STACKOS_RUN_LOADED_SUPERVISION_PROOF") != "1",
    reason="Explicit owned-daemon/native supervision proof requires opt-in",
)


async def call(client, base, name, args, *, allow_error=False):
    # Exactly one application request. No bridge, autostart, redirect or retry.
    result = await client.post(base + f"/api/v1/operations/{name}/call", json={"arguments": args})
    if not allow_error:
        result.raise_for_status()
    return result


def original_rows(engine, case):
    with Session(engine) as session:
        calls = session.exec(select(ActionCall).where(ActionCall.project_id == case.project)).all()
        attempts = session.exec(select(Run).where(Run.parent_run_id == case.run)).all()
        owner = session.exec(
            select(WorkItemOwnership).where(
                WorkItemOwnership.project_id == case.project,
                WorkItemOwnership.canonical_item_id == case.item,
            )
        ).one()
        assert len(calls) == 1 and len(attempts) == 1
        return (
            calls[0].model_dump(mode="json"),
            attempts[0].model_dump(mode="json"),
            owner.model_dump(mode="json"),
        )


def verify_deadline(case, output, rows, report):
    """Same invariant verifier for admitted and one-guard-weakened runtime."""
    call_row, attempt, owner = rows
    assert call_row["id"] == output["data"]["action_call"]["id"]
    assert report["manifest_digest"] == case.approved_runtime_digest
    assert report["released"] is True and report["cleanup_known"] is True
    assert report["cleanup_completed_continuous_ns"] < case.policy["cleanup_deadline_continuous_ns"]
    cleanup = [event for event in report["events"] if event["event"] == "cleanup"]
    assert len(cleanup) == 1 and all(cleanup[0]["reaped"].values())
    facts = attempt["metadata_json"]["native_terminal_facts"]
    assert facts["revision"] == 1 and len(facts["entries"]) == 1
    original_policy = attempt["metadata_json"]["native_recovery_private"]["policy"]
    assert original_policy["deadline_continuous_ns"] == case.policy["deadline_continuous_ns"]
    assert (
        original_policy["cleanup_deadline_continuous_ns"]
        == case.policy["cleanup_deadline_continuous_ns"]
    )
    assert owner["continuity_json"]["native_quiescence_certificate"]["attempt_snapshots"]
    assert output["data"]["output_json"]["work_accepted"] is False
    # Natural finite completion would create the unchanged exact report bytes.
    # If independent deadline is absent, the same verifier rejects that outcome.
    assert not (case.command.report_directory / "report.json").exists(), (
        "I4 deadline exceeded: frozen body completed"
    )
    signals = [event for event in report["events"] if event["event"] == "signal"]
    assert signals and all(event["known"] for event in signals)


async def exercise(
    settings,
    engine,
    base,
    env,
    work,
    audit,
    owner,
    *,
    monkeypatch,
    weakened=False,
    lose_daemon=False,
):
    copied, binding = prepare_copy(work, weaken_deadline=weakened)
    # Reuse the one lifecycle owner; this explicit restart selects the isolated
    # fixture before any command/approval exists. Never restart on transport error.
    owner.stop()
    env["PYTHONPATH"] = str(copied)
    owner.start()
    case = setup_case(settings, engine, copied, monkeypatch)
    audit.update(
        fixture=binding, policy=case.policy, approved_runtime_digest=case.approved_runtime_digest
    )
    token = settings.token_path.read_text().strip()
    client = httpx.AsyncClient(
        headers={"Authorization": "Bearer " + token},
        transport=httpx.AsyncHTTPTransport(retries=0),
        trust_env=False,
        follow_redirects=False,
        timeout=15,
    )
    monitor = None
    request = None
    try:
        async with client:
            request = asyncio.create_task(call(client, base, "action.execute", case.args))
            while not case.marker.exists():
                assert not request.done(), "Loaded action ended before frozen body was observed"
                assert clock._sample_clock().continuous_ns < case.policy["deadline_continuous_ns"]
                await asyncio.sleep(0.01)
            assert case.marker.read_text() == "frozen-body-started\n"
            _, attempt, _ = original_rows(engine, case)
            report_path = (
                settings.state_dir
                / "controlled-process"
                / f"{case.project}-{attempt['id']}"
                / "watchdog-observation.json"
            )
            if lose_daemon:
                registered = attempt["metadata_json"]["native_control"]["registration"][
                    "identities"
                ]["watchdog"]
                monitor = host._ExactExitWatch(
                    identity._ProcessIdentity(**registered), work / "held-watchdog.json"
                )
                assert monitor.registered
                owner.lose_owned_daemon()  # Exact retained child and explicit expected exit.
                with suppress(httpx.TransportError):
                    await request
                while not monitor.exited:
                    assert (
                        clock._sample_clock().continuous_ns
                        < case.policy["cleanup_deadline_continuous_ns"]
                    )
                    await asyncio.to_thread(monitor.observe, 0.02)
                report = json.loads(report_path.read_bytes())
                assert report["cleanup_known"] is True and report["certified"] is False
                assert all(
                    event["reaped"].values()
                    for event in report["events"]
                    if event["event"] == "cleanup"
                )
                owner.start()  # Explicit selected recovery observation, not a retry/fallback.
                before = original_rows(engine, case)
                assert before[0]["status"] == "failed"
                assert before[0]["error"] == "daemon-restart-orphan"
                assert before[0]["response_json"] == {
                    "outcome_unknown": True,
                    "retry_safe": False,
                }
                observed = (
                    await call(
                        client,
                        base,
                        "run.ownerGet",
                        dict(
                            project_id=case.project,
                            canonical_item_id=case.item,
                            response_mode="raw",
                        ),
                    )
                ).json()
                assert observed["ownership"]["canonical_item_id"] == case.item
                assert observed["status"]["acceptance_state"] == "not_evaluated"
                reply = await call(client, base, "action.execute", case.args, allow_error=True)
                assert reply.status_code == 409
                held = reply.json()
                audit["authenticated_original_hold"] = held
                assert held["code"] == -32008
                assert held["detail"] == "controlled_history_unknown"
                assert held["data"]["action_call_id"] == before[0]["id"]
                assert original_rows(engine, case) == before
                assert before[0]["id"] and before[0]["status"] != "success"
                audit.update(
                    status="passed",
                    exact_watchdog_note_exit=True,
                    daemon_loss=True,
                    original_intent_held=True,
                    forward_retry=False,
                    certificate_claim=False,
                    original_action_call_id=before[0]["id"],
                    authenticated_original_hold=held,
                    remaining_gap=(
                        "Loaded final audit lost with daemon; public original remains held. "
                        "Existing native controller-loss terminal recovery supplies private "
                        "exit-witness component proof, not a loaded certificate claim."
                    ),
                )
                return
            beats = []
            while not request.done():
                reply = await call(
                    client,
                    base,
                    "run.heartbeat",
                    dict(
                        project_id=case.project,
                        run_id=case.run,
                        run_token=case.args["run_token"],
                        response_mode="raw",
                    ),
                )
                beats.append(reply.json())
                assert (
                    clock._sample_clock().continuous_ns
                    < case.policy["cleanup_deadline_continuous_ns"]
                )
                await asyncio.sleep(0.1)
            result = (await request).json()
            assert len(beats) >= 2, "Healthy controller heartbeat was not independently observed"
            assert len({beat["data"]["heartbeat_at"] for beat in beats}) >= 2
            rows = original_rows(engine, case)
            report = json.loads(report_path.read_bytes())
            if weakened:
                with pytest.raises(AssertionError, match="I4 deadline exceeded"):
                    verify_deadline(case, result, rows, report)
                audit["counterfactual_detected"] = "independent-watchdog-deadline"
            else:
                verify_deadline(case, result, rows, report)
            original = original_rows(engine, case)
            replay = await call(client, base, "action.execute", case.args)
            replay_data = replay.json()["data"]
            assert replay_data["output_json"] == result["data"]["output_json"]
            assert replay_data["action_call"]["id"] == result["data"]["action_call"]["id"]
            assert original_rows(engine, case) == original
            public_owner = (
                await call(
                    client,
                    base,
                    "run.ownerGet",
                    dict(project_id=case.project, canonical_item_id=case.item, response_mode="raw"),
                )
            ).json()
            assert public_owner["status"]["acceptance_state"] == "not_evaluated"
            assert (
                public_owner["ownership"]["quiescence_revision"]
                == public_owner["ownership"]["revision"]
            )
            assert public_owner["status"]["attempt"]["id"] == rows[1]["id"]
            assert original_rows(engine, case) == original
            audit.update(
                status="passed",
                heartbeat_count=len(beats),
                original_terminal_replayed=True,
                deadline_renewed=False,
                work_accepted=False,
                weakened=weakened,
            )
    finally:
        if request is not None and not request.done():
            # Preserve the original awaited operation; do not issue another action.
            with suppress(httpx.TransportError):
                await request
        if monitor is not None:
            await asyncio.to_thread(monitor.finalize)


@pytest.mark.parametrize("weakened", [False, True])
def test_loaded_frozen_report_deadline_is_independent_of_healthy_controller(
    tmp_path, monkeypatch, weakened
):
    async def scenario(*args):
        await exercise(*args, monkeypatch=monkeypatch, weakened=weakened)

    run_scenario(tmp_path, monkeypatch, scenario)


def test_loaded_owned_daemon_loss_retains_original_unknown_without_retry(tmp_path, monkeypatch):
    async def scenario(*args):
        await exercise(*args, monkeypatch=monkeypatch, lose_daemon=True)

    run_scenario(tmp_path, monkeypatch, scenario)
