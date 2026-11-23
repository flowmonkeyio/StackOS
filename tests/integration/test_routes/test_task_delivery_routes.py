"""Exercise delivery through real daemon grants, adapters and memory scheduler."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED
from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos import task_delivery
from stackos.context import ContextRepository
from stackos.host_mcp import service
from stackos.host_mcp.adapters import claude_code
from stackos.host_mcp.result import AgentRunResult
from tests.helpers.native_task_routes import native_route_defaults  # noqa: F401
from tests.integration.test_mcp.conftest import MCPClient
from tests.integration.test_repositories.test_task_delivery import (
    commit,
    generic_result,
    git,
    native_source_fixtures,  # noqa: F401 -- applies real native admission fixtures to route scenarios
    stopped_review_fixture,
)


@pytest.mark.parametrize("transport", ["rest", "mcp", "cli"])
def test_review_replacement_registered_surfaces_and_authority(
    api, project_id, tmp_path, settings, monkeypatch, transport
):
    from copy import deepcopy
    from io import BytesIO
    from urllib.error import HTTPError
    from urllib.parse import urlsplit

    from typer.testing import CliRunner

    from stackos.cli import api_client, app
    from stackos.repositories.tracker import TrackerRepository

    tmp_path = tmp_path / "candidate"
    tmp_path.mkdir()
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Delivery Test")
    git(tmp_path, "config", "user.email", "delivery@example.test")
    commit(tmp_path, "baseline")
    with Session(api.app.state.engine) as session:
        arguments, request, before, calls = stopped_review_fixture(
            session, project_id, tmp_path, monkeypatch
        )
    endpoint = "/api/v1/operations/tracker.startDelivery/call"
    payload = {
        "project_id": project_id,
        "task_key": "replace",
        "project_dir": str(tmp_path),
        "replace_review": request,
    }
    saved_scheduler = api.app.state.scheduler
    finished = Event()
    saved_scheduler.add_listener(lambda _event: finished.set(), EVENT_JOB_EXECUTED)
    try:
        # Browser-console and unauthenticated callers gain no recovery authority.
        token = api.get("/api/v1/auth/ui-token").json()["token"]
        denied = api.post(
            endpoint, json={"arguments": payload}, headers={"Authorization": f"Bearer {token}"}
        )
        assert denied.status_code == 403, denied.text
        unauthenticated = api.post(
            endpoint, json={"arguments": payload}, headers={"Authorization": "Bearer invalid"}
        )
        assert unauthenticated.status_code == 401, unauthenticated.text
        foreign = api.post(
            "/api/v1/projects",
            json={"slug": "foreign-recovery", "name": "Foreign", "domain": "test"},
        )
        assert foreign.status_code == 201, foreign.text
        foreign_request = api.post(
            endpoint, json={"arguments": {**payload, "project_id": foreign.json()["data"]["id"]}}
        )
        assert foreign_request.status_code == 404, foreign_request.text
        with Session(api.app.state.engine) as session:
            assert (
                TrackerRepository(session)
                .brief(project_id=project_id, ticket_key="work")
                .ticket.completion_evidence_json
                == before
            )
        stale = deepcopy(payload)
        stale["replace_review"]["expected_attempt"] += 1
        rejected = api.post(endpoint, json={"arguments": stale})
        assert rejected.status_code == 422, rejected.text
        assert "stale_target" in rejected.text
        assert not arguments["scheduler"].jobs

        def launch(_host, _directory, _instructions, prompt, _schema, **kwargs):
            packet = json.loads(prompt)
            calls.append((packet, kwargs))
            assert packet["phase"] == "review"
            return AgentRunResult(
                0,
                thread_id="new-review",
                result=generic_result(
                    outputs={"commit_sha": request["expected_candidate_commit_sha"]}
                ),
            )

        monkeypatch.setattr(task_delivery, "run_agent", launch)

        if transport == "rest":
            response = api.post(
                endpoint, json={"arguments": {**payload, "response_mode": "compact"}}
            )
            assert response.status_code == 200, response.text
            result = response.json()
        elif transport == "mcp":
            client = MCPClient(api, api.headers["Authorization"].removeprefix("Bearer "))
            result = client.call_tool_structured(
                "tracker.startDelivery", {**payload, "response_mode": "compact"}
            )
        else:

            def isolated_urlopen(request, **kwargs):
                assert request.get_header("X-stackos-client-surface") == "cli"
                response = api.request(
                    request.method,
                    urlsplit(request.full_url).path,
                    json=json.loads(request.data),
                    headers=dict(request.header_items()),
                )
                if response.is_error:
                    raise HTTPError(
                        request.full_url,
                        response.status_code,
                        "rejected",
                        {},
                        BytesIO(response.content),
                    )
                return BytesIO(response.content)

            monkeypatch.setattr(api_client, "get_settings", lambda: settings)
            monkeypatch.setattr("urllib.request.urlopen", isolated_urlopen)
            cli = CliRunner().invoke(
                app,
                [
                    "ops",
                    "call",
                    "tracker.startDelivery",
                    "--project",
                    str(project_id),
                    "--input",
                    "-",
                    "--response-mode",
                    "compact",
                ],
                input=json.dumps(payload),
            )
            assert cli.exit_code == 0, cli.output
            result = json.loads(cli.output)
        refs = result.get("data", result.get("refs", result))
        assert (
            refs["review_replacement"]["candidate_commit_sha"]
            == request["expected_candidate_commit_sha"]
        )
        raw = api.post(endpoint, json={"arguments": {**payload, "response_mode": "raw"}})
        assert raw.status_code == 200, raw.text
        assert raw.json()["data"]["review_replacement"] == refs["review_replacement"]
        assert finished.wait(5), "Registered replacement job did not finish"
        inspected = api.post(
            "/api/v1/operations/tracker.get/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "task_key": "replace",
                    "response_mode": "raw",
                }
            },
        )
        assert inspected.status_code == 200, inspected.text
        automatic = inspected.json()["tickets"][0]["completion_evidence_json"]["automatic_delivery"]
        replacement = automatic["review_replacements"][0]
        archived = next(
            item
            for item in automatic["attempts"]
            if item["attempt"] == replacement["target"]["expected_attempt"]
        )
        assert archived["review"] == before["automatic_delivery"]["review"]
        assert archived["candidate"] == before["automatic_delivery"]["candidate"]
        assert "review" not in replacement
        assert "candidate" not in replacement
        assert automatic["review"]["thread_id"] == "new-review"
        assert automatic["repair_count"] == 3
        assert len(calls) == 3
    finally:
        assert api.app.state.scheduler is saved_scheduler


@pytest.mark.parametrize("response_mode", ["compact", "ack"])
def test_project_defaults_setup_rejects_lossy_responses(api, project_id, response_mode):
    arguments = {"project_id": project_id, "delivery": {"host_key": "codex"}}
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    saved = api.post(endpoint, json={"arguments": arguments})
    assert saved.status_code == 200, saved.text
    rejected = api.post(
        endpoint,
        json={"arguments": {**arguments, "delivery": {}, "response_mode": response_mode}},
    )
    assert rejected.status_code == 422, rejected.text
    snapshot = api.post(
        "/api/v1/operations/tracker.get/call",
        json={"arguments": {"project_id": project_id, "response_mode": "raw"}},
    )
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["tracker"] == saved.json()["data"]


@pytest.mark.parametrize("argument_form", ["legacy", "map"])
@pytest.mark.parametrize("choice", ["inherit", "set", "clear"])
def test_registered_saved_reasoning_reaches_distinct_native_role_commands(
    api, project_id, tmp_path, monkeypatch, argument_form, choice
):
    from tests.integration.test_routes.test_project_agent_operations import bind

    bind(api, project_id, tmp_path)

    def operation(name, **arguments):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"project_id": project_id, **arguments}},
        )
        assert response.status_code == 200, response.text
        return response.json().get("data", response.json())

    saved = {
        "host_key": "claude-code",
        "agents": {
            role: {
                "name": name,
                "instructions": name,
                "model": "claude-sonnet-5-5",
                "model_reasoning_effort": effort,
            }
            for role, name, effort in (
                ("delivery_agent", "writer", "medium"),
                ("review_agent", "reviewer", "low"),
            )
        },
    }
    saved["agents"]["review_agent"]["execution_requirements"] = {"filesystem_write": True}
    # Exercise the exact typed console writer before the actual admission/runner.
    state = operation("tracker.configureDelivery", delivery={"host_key": "claude-code"})
    for role, settings in saved["agents"].items():
        state = operation(
            "tracker.configureDelivery",
            project_dir=str(tmp_path),
            expected_revision=state["rev"],
            role_patch={"role": role, "settings": settings},
        )
    operation("tracker.createTask", key="reasoning", title="Reasoning")
    operation("tracker.createTicket", task_key="reasoning", key="work", title="Work")
    git(tmp_path, "init")
    commit(tmp_path, "Baseline")
    finished = Event()
    api.app.state.scheduler.add_listener(
        lambda event: finished.set() if event.job_id.startswith("task-delivery:") else None,
        EVENT_JOB_EXECUTED,
    )
    commands = []

    def execute(command, directory, prompt, schema, host, parse, env, /, **options):
        phase = json.loads(prompt)["phase"]
        commands.append((phase, command))
        assert directory == tmp_path and options["process_control"] is not None
        assert env["STACKOS_WORKSPACE_ROOT"] == str(directory.resolve())
        return AgentRunResult(0, thread_id=f"native-{phase}", result=generic_result())

    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/fixture/claude")
    monkeypatch.setattr(claude_code, "subprocess_env_for_cli", lambda _: {})
    monkeypatch.setattr(claude_code, "execute_json_agent", execute)
    override = (
        {}
        if choice == "inherit"
        else {"model_reasoning_effort": "high" if choice == "set" else None}
    )
    arguments = (
        {
            "delivery_agent": override,
            "review_agent": {"execution_requirements": {"filesystem_write": False}},
        }
        if argument_form == "legacy"
        else {
            "agents": {
                "delivery_agent": override,
                "review_agent": {"execution_requirements": {"filesystem_write": False}},
            }
        }
    )
    operation("tracker.startDelivery", task_key="reasoning", project_dir=str(tmp_path), **arguments)
    assert finished.wait(5), "Disposable native delivery did not retire"
    state = operation("tracker.get", task_key="reasoning", response_mode="raw")
    assert state["tasks"][0]["metadata_json"]["delivery"]["status"] == "complete", state
    assert state["tracker"]["config_json"]["delivery"] == saved
    frozen = state["tasks"][0]["metadata_json"]["delivery"]
    assert frozen["agents"]["review_agent"]["execution_requirements"] == {"filesystem_write": False}
    expected = {"inherit": "medium", "set": "high", "clear": None}[choice]
    assert [phase for phase, _ in commands] == ["delivery", "review"]
    sessions = state["tickets"][0]["completion_evidence_json"]["automatic_delivery"]["sessions"]
    assert sessions["review"]["agent_snapshot"]["execution_requirements"] == {
        "filesystem_write": False
    }
    for (phase, command), effort in zip(commands, (expected, "low"), strict=True):
        assert command[command.index("--model") + 1] == "claude-sonnet-5-5"
        assert sessions[phase].get("model_reasoning_effort") == effort
        if effort is None:
            assert "--effort" not in command
        else:
            assert command[command.index("--effort") + 1] == effort


def test_console_role_patch_preserves_defaults_and_rejects_stale_foreign_writes(
    api, project_id, tmp_path
):
    from tests.integration.test_routes.test_project_agent_operations import bind

    scope = bind(api, project_id, tmp_path / "roles")
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    saved = {
        "host_key": "claude-code",
        "agents": {
            "delivery_agent": {
                "name": "writer",
                "instructions": "Build",
                "model": "sonnet",
                "provider": None,
            },
            "review_agent": {"name": "reviewer", "instructions": "Review"},
            "other_role": {"name": "other", "instructions": "Keep"},
        },
        "main_agent": {"host_key": "codex", "model": "gpt-6.1-sol"},
    }
    original = api.post(endpoint, json={"arguments": {"project_id": project_id, "delivery": saved}})
    assert original.status_code == 200, original.text
    arguments = {
        "project_id": project_id,
        "project_dir": scope["project_dir"],
        "expected_revision": original.json()["data"]["rev"],
        "role_patch": {
            "role": "delivery_agent",
            "settings": {"model": None},
        },
    }
    token = api.get("/api/v1/auth/ui-token").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    preview = api.post(
        endpoint, json={"arguments": {**arguments, "preview": True}}, headers=headers
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["role_settings"]["effective"]["name"] == "writer"
    response = api.post(endpoint, json={"arguments": arguments}, headers=headers)
    assert response.status_code == 200, response.text
    readback = response.json()["data"]
    expected = json.loads(json.dumps(saved))
    expected["agents"]["delivery_agent"].pop("model")
    assert readback["config_json"]["delivery"] == expected
    stale = api.post(endpoint, json={"arguments": arguments}, headers=headers)
    assert stale.status_code == 409, stale.text
    foreign = api.post(
        "/api/v1/projects", json={"slug": "foreign-roles", "name": "Foreign", "domain": "test"}
    ).json()["data"]["id"]
    denied = api.post(
        endpoint,
        json={"arguments": {**arguments, "project_id": foreign, "expected_revision": 0}},
        headers=headers,
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["data"]["code"] == "missing_scope"
    current = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "response_mode": "raw",
                "task_index_only": True,
                "include_graph": False,
            }
        },
    ).json()
    assert current["tracker"]["config_json"] == readback["config_json"]
    assert current["tasks"] == []  # Saving never dispatches work.


def test_console_main_role_patch_uses_main_preferences_resolver(api, project_id, tmp_path):
    from stackos.project_setup_execution import resolve_preferences
    from tests.integration.test_routes.test_project_agent_operations import bind

    scope = bind(api, project_id, tmp_path / "main-role")
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    before = api.post(
        endpoint,
        json={
            "arguments": {
                "project_id": project_id,
                "delivery": {"host_key": "claude-code", "main_agent": {"provider": None}},
            }
        },
    ).json()["data"]
    response = api.post(
        endpoint,
        json={
            "arguments": {
                "project_id": project_id,
                "project_dir": scope["project_dir"],
                "expected_revision": before["rev"],
                "role_patch": {
                    "role": "main_agent",
                    "settings": {
                        "host_key": "codex",
                        "model": "gpt-6.1-sol",
                        "model_reasoning_effort": "high",
                    },
                },
            }
        },
    )
    assert response.status_code == 200, response.text
    choices = response.json()["data"]["config_json"]["delivery"]["main_agent"]
    assert choices["provider"] is None
    effective = {key: value for key, value in choices.items() if value is not None}
    with Session(api.app.state.engine) as session:
        assert resolve_preferences(session, project_id) == effective
    assert response.json()["data"]["role_settings"]["effective"] == effective


@pytest.mark.parametrize("concurrent_create", [False, True])
def test_console_first_role_save_guards_tracker_creation(
    api, project_id, tmp_path, monkeypatch, concurrent_create
):
    from stackos import project_setup_execution
    from stackos.repositories.tracker import TrackerRepository
    from tests.integration.test_routes.test_project_agent_operations import bind

    scope = bind(api, project_id, tmp_path / "first-role-race")
    original = project_setup_execution.resolve_preferences
    concurrent = {"host_key": "claude-code"}

    def competing_save(*args, **kwargs):
        result = original(*args, **kwargs)
        if concurrent_create:
            with Session(api.app.state.engine) as other:
                TrackerRepository(other).configure_delivery(
                    project_id=project_id, delivery=concurrent
                )
        return result

    monkeypatch.setattr(project_setup_execution, "resolve_preferences", competing_save)
    response = api.post(
        "/api/v1/operations/tracker.configureDelivery/call",
        json={
            "arguments": {
                "project_id": project_id,
                "project_dir": scope["project_dir"],
                "expected_revision": 0,
                "role_patch": {"role": "main_agent", "settings": {"host_key": "codex"}},
            }
        },
    )
    assert response.status_code == (409 if concurrent_create else 200), response.text
    with Session(api.app.state.engine) as session:
        assert TrackerRepository(session).delivery_defaults(project_id=project_id) == (
            concurrent if concurrent_create else {"main_agent": {"host_key": "codex"}}
        )


def test_console_role_patch_reports_source_precedence_over_saved_fallback(
    api, project_id, tmp_path
):
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "source-precedence")
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {
                "name": "builder",
                "description": "Fixture",
                "developer_instructions": "Build fixture",
                "model": "gpt-6.1-sol",
                "model_reasoning_effort": "high",
            },
        },
    )
    assert created.status_code == 200, created.text
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    saved = api.post(endpoint, json={"arguments": {"project_id": project_id, "delivery": {}}})
    response = api.post(
        endpoint,
        json={
            "arguments": {
                "project_id": project_id,
                "project_dir": scope["project_dir"],
                "expected_revision": saved.json()["data"]["rev"],
                "role_patch": {
                    "role": "delivery_agent",
                    "settings": {
                        "source_ref": created.json()["data"]["source_ref"],
                        "model": "gpt-6-astra",
                        "model_reasoning_effort": "low",
                    },
                },
            }
        },
    )
    assert response.status_code == 200, response.text
    readback = response.json()["data"]
    assert readback["config_json"]["delivery"]["agents"]["delivery_agent"]["model"] == "gpt-6-astra"
    effective = readback["role_settings"]["effective"]
    assert effective["model"] == "gpt-6.1-sol"
    assert effective["native_settings"]["model_reasoning_effort"] == "high"
    assert effective["setting_sources"] == {"model": "source", "model_reasoning_effort": "source"}


@pytest.mark.parametrize("cleared", [False, True])
def test_source_adoption_preview_and_write_use_registered_local_setup(
    api, project_id, tmp_path, cleared
):
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "adoption")
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {
                "name": "builder",
                "description": "Fixture",
                "developer_instructions": "Native source",
                "model_reasoning_effort": "high",
            },
        },
    )
    assert created.status_code == 200, created.text
    source = created.json()["data"]
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    original = {
        "delivery_agent": None
        if cleared
        else {
            "name": "builder",
            "instructions": "Legacy copy",
            "provider": None,
        },
        "review_agent": None,
    }
    saved = api.post(endpoint, json={"arguments": {"project_id": project_id, "delivery": original}})
    assert saved.status_code == 200, saved.text
    arguments = {
        "project_id": project_id,
        "project_dir": scope["project_dir"],
        "adopt_sources": {
            "delivery_agent": {
                "source_ref": source["source_ref"],
                "expected_digest": source["digest"],
            },
        },
    }
    preview = api.post(endpoint, json={"arguments": {**arguments, "preview": True}})
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["adoption"]["differences"]["delivery_agent"]["instructions"] == {
        "saved": None if cleared else "Legacy copy",
        "source": "Native source",
    }

    def defaults():
        response = api.post(
            "/api/v1/operations/tracker.get/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "response_mode": "raw",
                }
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["tracker"]

    assert defaults() == saved.json()["data"]
    adopted = api.post(endpoint, json={"arguments": arguments})
    assert adopted.status_code == 200, adopted.text
    readback = defaults()
    assert readback["config_json"]["delivery"] == adopted.json()["data"]["config_json"]["delivery"]
    selected = readback["config_json"]["delivery"]["agents"]["delivery_agent"]
    if cleared:
        assert "provider" not in selected
    else:
        assert selected["provider"] is None
    assert "instructions" not in selected
    assert selected["source_ref"] == source["source_ref"]
    assert readback["config_json"]["delivery"]["review_agent"] is None


@pytest.mark.parametrize(
    "variant", ["delivery", "main_agent", "source_preview", "source_write", "role_patch"]
)
def test_configure_delivery_denies_actual_cli_transport(
    api, project_id, tmp_path, settings, monkeypatch, variant
):
    from io import BytesIO
    from urllib.error import HTTPError
    from urllib.parse import urlsplit

    from typer.testing import CliRunner

    from stackos.cli import api_client, app
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "cli-adoption")
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {
                "name": "builder",
                "description": "Fixture",
                "developer_instructions": "Native source",
            },
        },
    )
    assert created.status_code == 200, created.text
    source = created.json()["data"]
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    original = {"host_key": "codex"}
    saved = api.post(endpoint, json={"arguments": {"project_id": project_id, "delivery": original}})
    assert saved.status_code == 200, saved.text
    if variant == "delivery":
        payload = {"delivery": {"host_key": "claude-code"}}
    elif variant == "main_agent":
        payload = {"main_agent": {"host_key": "codex"}}
    elif variant == "role_patch":
        payload = {
            "project_dir": scope["project_dir"],
            "expected_revision": saved.json()["data"]["rev"],
            "role_patch": {"role": "main_agent", "settings": {"model": "gpt-6.1-sol"}},
        }
    else:
        payload = {
            "project_dir": scope["project_dir"],
            "adopt_sources": {
                "delivery_agent": {
                    "source_ref": source["source_ref"],
                    "expected_digest": source["digest"],
                }
            },
            "preview": variant == "source_preview",
        }
    responses = []

    def isolated_urlopen(request, **kwargs):
        # Keep the real CLI client, token handling, header construction and error
        # parser. Substitute only HTTP transport with the isolated authenticated app.
        assert request.get_header("X-stackos-client-surface") == "cli"
        response = api.request(
            request.method,
            urlsplit(request.full_url).path,
            json=json.loads(request.data),
            headers=dict(request.header_items()),
        )
        responses.append(response)
        if response.is_error:
            raise HTTPError(
                request.full_url, response.status_code, "denied", {}, BytesIO(response.content)
            )
        return BytesIO(response.content)

    monkeypatch.setattr(api_client, "get_settings", lambda: settings)
    monkeypatch.setattr("urllib.request.urlopen", isolated_urlopen)
    result = CliRunner().invoke(
        app,
        ["ops", "call", "tracker.configureDelivery", "--project", str(project_id), "--input", "-"],
        input=json.dumps(payload),
    )
    assert len(responses) == 1
    assert result.exit_code == 1 and responses[0].status_code == 422, responses[0].text
    assert "local-admin" in responses[0].json()["detail"]
    current = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "response_mode": "raw",
            }
        },
    )
    assert current.status_code == 200, current.text
    assert current.json()["tracker"] == saved.json()["data"]


@pytest.mark.parametrize("argument_form", ["legacy", "map"])
@pytest.mark.parametrize("selected_role", ["delivery_agent", "review_agent"])
@pytest.mark.parametrize("layout", ["legacy", "mapped", "partial"])
@pytest.mark.parametrize("unrelated_missing", [False, True])
def test_registered_partial_handoff_resolves_only_required_roles(
    api, project_id, tmp_path, monkeypatch, argument_form, selected_role, layout, unrelated_missing
):
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "admission")
    native_name = "builder" if selected_role == "delivery_agent" else "reviewer"
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {
                "name": native_name,
                "description": "Fixture",
                "developer_instructions": "Selected source",
            },
        },
    )
    assert created.status_code == 200, created.text
    ref = created.json()["data"]["source_ref"]
    other_role = "review_agent" if selected_role == "delivery_agent" else "delivery_agent"
    other = {"name": "saved-other", "host_key": "claude-code", "instructions": "Saved role"}
    fallback = {"model": "gpt-6-astra", "model_reasoning_effort": "high", "provider": None}
    if layout == "mapped":
        saved = {"agents": {selected_role: fallback, other_role: other}}
    else:
        saved = {selected_role: fallback, other_role: other}
        if layout == "partial":
            saved["agents"] = {selected_role: {"model_reasoning_effort": "medium"}}
    if unrelated_missing:
        saved.setdefault("agents", {})["unrelated"] = {
            "source_ref": {
                "host_key": "codex",
                "native_name": "missing",
                "source_path": ".codex/agents/missing.toml",
            }
        }

    def operation(name, **arguments):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    **arguments,
                }
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        return payload.get("data", payload)

    operation("tracker.configureDelivery", delivery=saved)
    operation("tracker.createTask", key="partial", title="Partial")
    operation("tracker.createTicket", task_key="partial", key="work", title="Work")
    finished = Event()
    api.app.state.scheduler.add_listener(
        lambda event: finished.set() if event.job_id.startswith("task-delivery:") else None,
        EVENT_JOB_EXECUTED,
    )
    monkeypatch.setattr(
        task_delivery,
        "run_agent",
        lambda *a, **k: AgentRunResult(
            0,
            thread_id="fixture-delivery",
            result=generic_result("blocked"),
        ),
    )
    choice = {"source_ref": ref}
    selection = (
        {selected_role: choice}
        if argument_form == "legacy"
        else {"agents": {selected_role: choice}}
    )
    admitted = operation(
        "tracker.startDelivery", task_key="partial", project_dir=scope["project_dir"], **selection
    )
    assert admitted["status"] == "queued"
    assert finished.wait(5), "Disposable delivery did not retire"
    snapshot = operation("tracker.get", task_key="partial", response_mode="raw")
    agents = snapshot["tasks"][0]["metadata_json"]["delivery"]["agents"]
    assert set(agents) == {"delivery_agent", "review_agent"}
    assert agents[other_role] == other
    assert agents[selected_role]["source_ref"] == ref
    assert agents[selected_role]["model"] == "gpt-6-astra"
    assert agents[selected_role]["native_settings"] == {
        "model_reasoning_effort": "medium" if layout == "partial" else "high",
    }
    assert agents[selected_role]["setting_sources"] == {
        "model": "project",
        "model_reasoning_effort": "project",
    }
    assert snapshot["tracker"]["config_json"]["delivery"] == saved


@pytest.mark.parametrize("problem", ["missing_delivery", "missing_review", "same_source"])
def test_registered_handoff_keeps_selected_source_and_independence_failures(
    api, project_id, tmp_path, monkeypatch, problem
):
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "required")
    refs = {}
    for role in ("delivery", "review"):
        response = call(
            api,
            "create",
            {
                **scope,
                "fields": {
                    "name": role,
                    "description": "Fixture",
                    "developer_instructions": role,
                },
            },
        )
        assert response.status_code == 200, response.text
        refs[role] = response.json()["data"]["source_ref"]
    if problem == "same_source":
        refs["review"] = refs["delivery"]
    else:
        (
            Path(scope["project_dir"]) / refs[problem.removeprefix("missing_")]["source_path"]
        ).unlink()
    for name, arguments in [
        ("tracker.createTask", {"key": "required", "title": "Required"}),
        ("tracker.createTicket", {"task_key": "required", "key": "work", "title": "Work"}),
    ]:
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    **arguments,
                }
            },
        )
        assert response.status_code == 200, response.text
    calls = []
    monkeypatch.setattr(task_delivery, "run_agent", lambda *a, **k: calls.append(a))
    response = api.post(
        "/api/v1/operations/tracker.startDelivery/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "required",
                "project_dir": scope["project_dir"],
                "agents": {f"{role}_agent": {"source_ref": ref} for role, ref in refs.items()},
            }
        },
    )
    assert response.status_code == 422, response.text
    assert (
        "independent" in response.text
        if problem == "same_source"
        else "unavailable" in response.text
    )
    snapshot = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "required",
                "response_mode": "raw",
            }
        },
    ).json()
    assert not calls
    assert "delivery" not in (snapshot["tasks"][0]["metadata_json"] or {})


@pytest.mark.parametrize("workflow", [False, True])
@pytest.mark.parametrize("defaults", [False, True])
def test_real_operation_returns_while_scheduler_delivers(
    api: TestClient,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workflow: bool,
    defaults: bool,
) -> None:
    tmp_path = tmp_path / "project"
    tmp_path.mkdir()
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Delivery Test")
    git(tmp_path, "config", "user.email", "delivery@example.test")
    commit(tmp_path, "baseline")

    def operation(name, **arguments):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"project_id": project_id, **arguments}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return body.get("data", body)

    scope = {}
    task_key = "automatic"
    if workflow:
        plan = operation(
            "runPlan.create",
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "automatic-workflow",
                "title": "Automatic workflow",
                "steps": [{"id": "deliver", "title": "Deliver"}],
            },
            response_mode="raw",
        )
        scope = {"run_plan_id": plan["id"], "step_id": "deliver"}
        started = operation("runPlan.start", run_plan_id=plan["id"], response_mode="raw")
        operation("runPlan.claimStep", **scope, run_token=started["run_token"])
        task_key = f"workflow-{plan['id']}"
    else:
        operation("tracker.createTask", key=task_key, title="Automatic")
    for key, dependencies in (("first", []), ("second", ["first"])):
        operation(
            "tracker.createTicket",
            task_key=task_key,
            key=key,
            title=key,
            dependency_keys=dependencies,
            **scope,
        )

    entered, release, finished = Event(), Event(), Event()
    calls = []

    def run_agent(
        _host,
        _directory,
        _instructions,
        prompt,
        _schema,
        *,
        sandbox,
        on_session=None,
        process_control=None,
        native_settings=None,
    ):
        assert process_control is not None, "Managed delivery must retain invocation control"
        packet = json.loads(prompt)
        delivery = packet["phase"] == "delivery"
        calls.append(
            (json.loads(prompt)["brief"]["ticket"]["key"], "delivery" if delivery else "review")
        )
        entered.set()
        assert release.wait(5), "Test did not release the background agent"
        result = generic_result(
            outputs={}
            if packet["workflow_step"]
            else {
                "commit_sha": (
                    commit(tmp_path, calls[-1][0])
                    if delivery
                    else git(tmp_path, "rev-parse", "HEAD")
                )
            }
        )
        return AgentRunResult(0, thread_id=f"{calls[-1][0]}-{delivery}", result=result)

    definition = service._definition_by_key("codex")
    monkeypatch.setattr(
        service,
        "_definition_by_key",
        lambda host: replace(
            definition, run_agent=lambda *args, **kwargs: run_agent(host, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(task_delivery, "run_agent", service.run_agent)
    api.app.state.scheduler.add_listener(
        lambda event: finished.set() if event.job_id.startswith("task-delivery:") else None,
        EVENT_JOB_EXECUTED,
    )
    arguments = {
        "task_key": task_key,
        **scope,
        "project_dir": str(tmp_path),
        "delivery_agent": {"name": "builder", "instructions": "Deliver the project ticket."},
        "review_agent": {"name": "reviewer", "instructions": "Review the project ticket."},
    }
    if defaults:
        enabled = api.post(
            "/api/v1/operations/tracker.configureDelivery/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "delivery": {
                        "delivery_agent": arguments.pop("delivery_agent"),
                        "review_agent": arguments.pop("review_agent"),
                    },
                }
            },
        )
        assert enabled.status_code == 200, enabled.text
        configured = enabled.json()["data"]
        readback = operation("tracker.get", task_key=task_key, response_mode="raw")
        assert readback["tracker"]["config_json"] == configured["config_json"]
        assert readback["tracker"]["rev"] == configured["rev"]
    try:
        receipt = operation("tracker.startDelivery", **arguments)
        assert receipt["status"] == "queued"
        assert entered.wait(5) and not finished.is_set()
        active = operation("tracker.get", task_key=task_key, response_mode="raw")
        assert active["tasks"][0]["active_execution"]["agent"] == "builder"
        first = next(ticket for ticket in active["tickets"] if ticket["key"] == "first")
        assert first["active_execution"] == active["tasks"][0]["active_execution"]
        # This is an ordinary system-authorized MCP caller, not the test grant sentinel.
        mcp = MCPClient(api, api.headers["Authorization"].removeprefix("Bearer "))
        assert "tracker.startDelivery" in {tool["name"] for tool in mcp.list_tools()}
        denied = mcp.call_tool_error(
            "tracker.configureDelivery", {"project_id": project_id, "delivery": {}}
        )
        assert denied["code"] == -32601
        duplicate = mcp.call_tool_structured(
            "tracker.startDelivery", {"project_id": project_id, **arguments}
        )
        assert duplicate["data"]["job_ref"] == receipt["job_ref"]
    finally:
        release.set()
    assert finished.wait(5), "Scheduled delivery did not finish"
    snapshot = operation("tracker.get", task_key=task_key, response_mode="raw")
    assert snapshot["tasks"][0]["active_execution"] is None
    assert all(ticket["active_execution"] is None for ticket in snapshot["tickets"])
    with Session(api.app.state.engine) as reader:
        events = (
            ContextRepository(reader)
            .tracker_status_timeline(project_id=project_id, task_key=task_key, limit=50)
            .items
        )
    lifetime = [event for event in events if event.event_type == "tracker.execution.changed"]
    assert [event.metadata_json["active"] for event in lifetime] == [True, False] * (
        5 if workflow else 4
    )
    assert snapshot["tasks"][0]["status"] == "complete"
    assert snapshot["tasks"][0]["metadata_json"]["delivery"]["status"] == "complete"
    reviewer = snapshot["tasks"][0]["metadata_json"]["delivery"]["agents"]["review_agent"]
    assert {key: reviewer[key] for key in ("name", "instructions", "host_key")} == {
        "name": "reviewer",
        "instructions": "Review the project ticket.",
        "host_key": "codex",
    }
    assert reviewer["source_ref"]["native_name"] == "reviewer"
    assert len(reviewer["source_digest"]) == 64 and reviewer["native_settings"] == {}
    assert [
        ticket["status"] for ticket in snapshot["tickets"] if ticket["key"] in {"first", "second"}
    ] == ["complete", "complete"]
    if workflow:
        mirror = next(
            ticket for ticket in snapshot["tickets"] if ticket["parent_ticket_key"] is None
        )
        assert mirror["status"] == "complete"
    assert calls == [
        ("first", "delivery"),
        ("first", "review"),
        ("second", "delivery"),
        ("second", "review"),
    ] + ([(f"workflow-{plan['id']}-deliver", "delivery")] if workflow else [])
