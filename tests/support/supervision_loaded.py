"""Loaded scenario adapters; reuse the reviewed daemon owner without another launcher."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlmodel import Session, select

from stackos.db.models import Project, ProjectEvent, Run, RunPlan, TrackerTask, WorkItemOwnership
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration import test_controlled_command_loaded as loaded


def run_scenario(tmp_path, monkeypatch, scenario):
    """Retain the reviewed exact Popen/reap owner and its explicit lifecycle seam."""
    loaded.run_loaded_scenario(tmp_path, scenario)


@asynccontextmanager
async def sdk(settings, base):
    wire, logical = [], []

    async def sent(request):
        if request.method == "POST":
            value = json.loads(request.content)
            if value.get("method") == "tools/call":
                wire.append(value["params"]["name"])

    async with (
        httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + settings.token_path.read_text().strip()},
            transport=httpx2.AsyncHTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [sent]},
        ) as client,
        streamable_http_client(base + "/mcp", http_client=client) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()

        async def call(name, arguments):
            logical.append(name)
            result = await session.call_tool(name, arguments=arguments)
            value = json.loads(result.content[0].text)
            return SimpleNamespace(error=result.is_error, value=value)

        yield call
    assert wire == logical, "An operation was retried or omitted by the loaded transport"


def require_ok(reply):
    assert not reply.error, reply.value
    return reply.value


def require_denied(reply):
    assert reply.error, reply.value


def ownership_case(engine):
    """Use the existing trusted AgentRequest→plan producer and pre-start grants."""
    with Session(engine) as session:
        project = Project(
            slug="loaded-occurrence",
            name="Loaded occurrence",
            domain="example.test",
            locale="en-US",
        )
        session.add(project)
        session.commit()
        requests = AgentRequestRepository(session)
        request = requests.create(
            project_id=project.id,
            request_key="synthetic:loaded-occurrence",
            title="Synthetic request",
            body_preview="Private synthetic preview",
            source_provider="synthetic",
            source_kind="manual-request",
            source_message_ref="synthetic:loaded-message",
        ).data
        prepared = requests.prepare_run_plan(
            project_id=project.id,
            request_id=request.id,
            claimed_by="synthetic:inbox-owner",
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "loaded.occurrence",
                "title": "Loaded occurrence",
                "steps": [{"id": "write", "title": "Write"}],
            },
        ).data
        plan = prepared.run_plan
        TrackerRepository(session).create_task(
            project_id=project.id, key="loaded-occurrence-item", title="Synthetic item"
        )
        task = session.exec(
            select(TrackerTask).where(
                TrackerTask.project_id == project.id, TrackerTask.key == "loaded-occurrence-item"
            )
        ).one()
        step = RunPlanRepository(session).get_step(plan.id, "write")
        task.source_json = {"run_plan_id": plan.id, "run_plan_step_id": step.id}
        task.metadata_json = {
            "ownership_continuity": dict(
                scope_digest=loaded.SCOPE,
                budget_ref="synthetic:budget",
                stop_scope_ref="synthetic:stop",
                history_ref="synthetic:history",
                stopped=False,
                denied=False,
                attempts_used=0,
                failures_used=0,
                attempt_limit=2,
                failure_limit=2,
                effect_identity="effect:synthetic:no-publication",
            )
        }
        item = f"task:{task.id}"
        definition = session.get(RunPlan, plan.id)
        definition.grant_snapshot_json = {
            "mcp_tool_grants": [
                {"step_id": "write", "tool": name, "targets": [item]}
                for name in (
                    "run.ownerAcquire",
                    "run.ownerCheckpoint",
                    "run.ownerStop",
                    "run.ownerConsume",
                )
            ]
        }
        session.add_all([task, definition])
        session.commit()
        plans = RunPlanRepository(session)
        started = plans.start(plan.id, project_id=project.id).data
        plans.claim_step(
            run_plan_id=plan.id, run_id=started.run_id, step_id="write", claimed_by="synthetic:lead"
        )
        return SimpleNamespace(
            project=project.id,
            item=item,
            task=task.id,
            plan=plan.id,
            request=request.id,
            args=dict(
                project_id=project.id,
                canonical_item_id=item,
                owner_ref="synthetic:lead",
                scope_digest=loaded.SCOPE,
                run_token=started.run_token,
                response_mode="raw",
            ),
        )


def occurrence_snapshot(engine, case):
    with Session(engine) as session:
        events = [
            row.model_dump(mode="json")
            for row in session.exec(
                select(ProjectEvent)
                .where(
                    ProjectEvent.project_id == case.project,
                    ProjectEvent.event_type == "WORK_ITEM_OWNER_REQUEST_OCCURRENCE",
                )
                .order_by(ProjectEvent.id)
            ).all()
        ]
        owner = session.exec(
            select(WorkItemOwnership).where(
                WorkItemOwnership.project_id == case.project,
                WorkItemOwnership.canonical_item_id == case.item,
            )
        ).one()
        return dict(
            events=events,
            owner=owner.model_dump(mode="json"),
            runs=[
                row.model_dump(mode="json")
                for row in session.exec(
                    select(Run).where(Run.project_id == case.project).order_by(Run.id)
                ).all()
            ],
        )


def verify_occurrence(snapshot, case, sentinel):
    assert sentinel.read_bytes() == b"preserved synthetic evidence\n"
    assert len(snapshot["events"]) == 1, "trusted occurrence admitted more than once"
    record = snapshot["events"][0]["metadata_json"]["request_occurrence"]
    assert record["source_id"] == case.request and record["canonical_item_id"] == case.item
    assert snapshot["owner"]["continuity_json"]["attempts_used"] == 0
    assert "Private synthetic preview" not in json.dumps(snapshot["events"])


def fence(owner):
    return dict(expected_epoch=owner["epoch"], expected_revision=owner["revision"])
