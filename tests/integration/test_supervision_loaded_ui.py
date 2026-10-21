"""Prepared loaded MCP/REST/CLI/rendered-UI agreement and preservation proof.

Lives outside the pinned source until Main incorporates it after the active
capture. Collection/execution requires Main's reviewed fresh UI build/capture.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from stackos.db.models import (
    ActionCall,
    AgentRequest,
    Artifact,
    IdempotencyKey,
    ProjectEvent,
    ResourceRecord,
    Run,
    RunPlan,
    RunPlanStep,
    TrackerTask,
    WorkItemOwnership,
)
from tests.integration import test_controlled_command_loaded as loaded
from tests.support.supervision_loaded import (
    occurrence_snapshot,
    ownership_case,
    require_ok,
    sdk,
    verify_occurrence,
)

NODE = "/usr/local/bin/node"
MODULES = str(Path(__file__).resolve().parents[2] / "ui/node_modules")
_OWNED_UI_RUNNERS: set[subprocess.Popen] = set()
pytestmark = pytest.mark.skipif(
    os.environ.get("STACKOS_RUN_LOADED_SUPERVISION_PROOF") != "1",
    reason="Explicit owned-daemon/native supervision proof requires opt-in",
)


def _built_ui_binding():
    bundle = Path(loaded.__file__).resolve().parents[2] / "stackos/ui_dist"
    assert (bundle / "index.html").is_file(), "Main must build the reviewed offline UI first"
    scripts = list((bundle / "assets").glob("*.js"))
    assert scripts and any(b"Owner status:" in path.read_bytes() for path in scripts), (
        "Stale served UI bundle lacks Owner status; Main must build it before proof"
    )
    return {
        str(path.relative_to(bundle)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(bundle.rglob("*"))
        if path.is_file()
    }


def _snapshot(engine):
    """Independent full rows: reads must not rewrite evidence, owners or lifecycle."""
    with Session(engine) as session:
        return {
            model.__name__: [
                row.model_dump(mode="json")
                for row in session.exec(select(model).order_by(model.id)).all()
            ]
            for model in (
                ActionCall,
                AgentRequest,
                Artifact,
                IdempotencyKey,
                ProjectEvent,
                ResourceRecord,
                Run,
                RunPlan,
                RunPlanStep,
                TrackerTask,
                WorkItemOwnership,
            )
        }


def _run_browser(helper: Path, request: Path, work: Path):
    private = work / "node-private"
    private.mkdir(mode=0o700)
    for name in ("home", "tmp", "config", "cache"):
        (private / name).mkdir(mode=0o700)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(private / "home"),
        "TMPDIR": str(private / "tmp"),
        "XDG_CONFIG_HOME": str(private / "config"),
        "XDG_CACHE_HOME": str(private / "cache"),
        "LC_ALL": "C",
        "TZ": "UTC",
        "STACKOS_UI_NODE_MODULES": MODULES,
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
    }
    with (work / "node.stdout").open("xb") as stdout, (work / "node.stderr").open("xb") as stderr:
        child = subprocess.Popen(
            [NODE, str(helper), str(request)],
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        _OWNED_UI_RUNNERS.add(child)
        primary = None
        try:
            code = child.wait()
        except BaseException as error:
            primary = error
        finally:
            # Never drop the original runner handle or send a replacement signal.
            # Its finally owns Chrome closure/reaping. Do not relaunch on failure.
            while child.returncode is None:
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    continue
                except BaseException as error:
                    primary = primary or error
                    try:
                        time.sleep(0.05)
                    except BaseException as interruption:
                        primary = primary or interruption
            if child.returncode is not None:
                _OWNED_UI_RUNNERS.discard(child)
        if primary is not None:
            raise primary
    assert code == 0, "Prepared UI helper failed; inspect its private raw receipt/logs"
    return dict(
        returncode=child.returncode,
        reaped=child not in _OWNED_UI_RUNNERS,
        argv=[NODE, str(helper), str(request)],
        environment=env,
    )


def test_loaded_owner_status_agrees_mcp_rest_cli_and_visible_ui(tmp_path):
    helper = Path(__file__).with_name("loaded_owner_ui.cjs")
    assert helper.is_file(), "Main must install the reviewed two-file helper together"
    bundle_binding = _built_ui_binding()

    async def scenario(settings, engine, base, env, work, audit, _owner):
        case = ownership_case(engine)
        sentinel = work / "preserved-sentinel"
        sentinel.write_bytes(b"preserved synthetic evidence\n")
        evidence = work / "retained-evidence.json"
        evidence.write_bytes(b'{"schema_version":"synthetic.preserved.v1","state":"unchanged"}\n')
        evidence_sha256 = hashlib.sha256(evidence.read_bytes()).hexdigest()
        args = dict(project_id=case.project, canonical_item_id=case.item, response_mode="raw")
        async with sdk(settings, base) as call:
            acquired = require_ok(await call("run.ownerAcquire", case.args))["data"]["ownership"]
            trusted = occurrence_snapshot(engine, case)
            verify_occurrence(trusted, case, sentinel)
            before = _snapshot(engine)
            title = next(row["title"] for row in before["TrackerTask"] if row["id"] == case.task)
            mcp = require_ok(await call("run.ownerGet", args))
            assert mcp["ownership"]["revision"] == acquired["revision"]
            assert mcp["status"]["acceptance_state"] == "not_evaluated"
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + settings.token_path.read_text().strip()},
            transport=httpx.AsyncHTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
        ) as rest:
            response = await rest.post(
                base + "/api/v1/operations/run.ownerGet/call", json={"arguments": args}
            )
            response.raise_for_status()
            rest_value = response.json()
        owner_read = work / "owner-read.json"
        owner_read.write_text(json.dumps(args) + "\n")
        cli_argv = [
            sys.executable,
            "-B",
            "-m",
            "stackos",
            "ops",
            "call",
            "run.ownerGet",
            "--input",
            str(owner_read),
            "--response-mode",
            "raw",
        ]
        # Canonical CLI reads its token from this exact private state directory.
        # No token appears in argv, logs, browser input, localStorage or receipts.
        cli = await asyncio.to_thread(
            subprocess.run, cli_argv, cwd=work, env=env, capture_output=True, check=True
        )
        (work / "cli.stdout").write_bytes(cli.stdout)
        (work / "cli.stderr").write_bytes(cli.stderr)
        cli_value = json.loads(cli.stdout)
        expected = loaded._without_capture(mcp)
        assert expected == loaded._without_capture(rest_value)
        assert expected == loaded._without_capture(cli_value)
        assert _snapshot(engine) == before
        browser_work = work / "browser-proof"
        browser_work.mkdir(mode=0o700)
        for name in ("home", "tmp", "config", "cache"):
            (browser_work / name).mkdir(mode=0o700)
        browser_input = browser_work / "input.json"
        browser_input.write_text(
            json.dumps(
                dict(
                    base=base,
                    project=case.project,
                    item=case.item,
                    title=title,
                    expected_inspection=mcp,
                    output=str(browser_work),
                    home=str(browser_work / "home"),
                    tmp=str(browser_work / "tmp"),
                    config=str(browser_work / "config"),
                    cache=str(browser_work / "cache"),
                )
            )
            + "\n"
        )
        runner = await asyncio.to_thread(_run_browser, helper, browser_input, browser_work)
        browser_receipt = json.loads((browser_work / "browser-receipt.json").read_bytes())
        assert browser_receipt["status"] == "passed" and browser_receipt["browser_reaped"] is True
        assert browser_receipt["browser_launches"] == 1
        assert browser_receipt["external_requests_blocked"] == []
        assert browser_receipt["owner_get_requests"]
        assert all(
            request == dict(project_id=case.project, canonical_item_id=case.item)
            for request in browser_receipt["owner_get_requests"]
        )
        assert loaded._without_capture(browser_receipt["inspection"]) == expected
        assert _snapshot(engine) == before
        verify_occurrence(occurrence_snapshot(engine, case), case, sentinel)
        assert hashlib.sha256(evidence.read_bytes()).hexdigest() == evidence_sha256
        assert _built_ui_binding() == bundle_binding
        public_evidence = [
            path.read_bytes()
            for path in (
                browser_work / "rendered-text.txt",
                browser_work / "browser-receipt.json",
                browser_work / "node.stdout",
                browser_work / "node.stderr",
                work / "cli.stdout",
                work / "cli.stderr",
                browser_input,
            )
        ]
        private_values = (settings.token_path.read_bytes().strip(), case.args["run_token"].encode())
        if any(
            private and private in public
            for private in private_values
            for public in public_evidence
        ):
            raise AssertionError("Private fixture authentication escaped public evidence")
        audit.update(
            status="passed",
            scenario="T7 loaded same-revision visible UI",
            project_id=case.project,
            canonical_item_id=case.item,
            owner_revision=acquired["revision"],
            owner_status=mcp["status"],
            mcp_rest_cli_ui_same_revision=True,
            browser_runner=runner,
            browser_receipt=str(browser_work / "browser-receipt.json"),
            screenshot=str(browser_work / "owner-status.png"),
            evidence_sha256=evidence_sha256,
            sentinel_preserved=True,
            semantic_rows_unchanged=True,
            auth_token_exposed=False,
            served_ui_binding=bundle_binding,
        )

    # Exactly the existing owned-daemon lifecycle; no new server launcher.
    loaded.run_loaded_scenario(tmp_path, scenario)
