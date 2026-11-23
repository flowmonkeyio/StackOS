"""Schedules select a main host, independently of explicit task-delivery roles."""

import hashlib
import json
import subprocess

import pytest

from stackos.db.models import TaskTracker
from stackos.host_mcp.adapters import codex
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ScheduledJobRepository
from stackos.repositories.tracker import TrackerRepository
from tests.helpers.native_task_routes import controlled_profile_host as controlled_profile_host
from tests.integration.test_repositories.test_schedule_main_thread import bind_folder
from tests.integration.test_repositories.test_schedule_main_thread import (
    native_ready as native_ready,
)


@pytest.mark.parametrize(
    "explicit,default,expected",
    [
        ("claude-code", "codex", "claude-code"),
        (None, "claude-code", "claude-code"),
        (None, None, "codex"),
    ],
)
def test_main_host_precedence_ignores_role_instructions_and_models(
    session, project_id, tmp_path, native_ready, explicit, default, expected
):
    bind_folder(session, project_id, tmp_path)
    session.add(
        TaskTracker(
            project_id=project_id,
            key="default",
            name="Work",
            config_json={
                "delivery": {
                    "host_key": default,
                    "agents": {
                        "delivery_agent": {
                            "instructions": "Never use this specialist",
                            "model": "role-pin",
                        },
                        "review_agent": {"source_ref": {"missing": "invalid source"}},
                    },
                }
            },
        )
    )
    session.commit()
    config = dict(
        kind="main",
        name="Main",
        goal="Benign goal",
        cron_expr="0 9 * * *",
        timezone="UTC",
        enabled=True,
        execution_json={"host_key": explicit},
    )
    settings = ScheduledJobRepository(session).validate_configuration(project_id, config)
    assert settings == {"project_dir": str(tmp_path), "host_key": expected}


@pytest.mark.parametrize("explicit", [None, "codex", "claude-code"])
@pytest.mark.parametrize("saved_host", [None, "codex", "grok", "hermes"])
def test_schedule_uses_main_preferences_with_authoritative_host_constraint(
    session, project_id, tmp_path, native_ready, controlled_profile_host, explicit, saved_host
):
    bind_folder(session, project_id, tmp_path)
    saved = {
        "provider": "configured",
        **(
            {"model": "custom-model", "profile": "main-profile"}
            if saved_host in {controlled_profile_host, "hermes"}
            else {"model": "gpt-6.1-sol", "model_reasoning_effort": "high"}
        ),
        **({"host_key": saved_host} if saved_host else {}),
    }
    repo = TrackerRepository(session)
    repo.configure_delivery(
        project_id=project_id, delivery={"host_key": "codex", "main_agent": saved}
    )
    config = dict(
        kind="main",
        name="Main",
        goal="Benign goal",
        cron_expr="0 9 * * *",
        timezone="UTC",
        enabled=True,
        execution_json={"host_key": explicit},
    )
    if saved_host == "hermes" and explicit is None:
        with pytest.raises(ValidationError, match="Hermes managed execution is unavailable"):
            ScheduledJobRepository(session).validate_configuration(project_id, config)
        assert repo.delivery_defaults(project_id=project_id)["main_agent"] == saved
        return
    settings = ScheduledJobRepository(session).validate_configuration(project_id, config)
    expected = {"host_key": explicit or saved_host or "codex", "project_dir": str(tmp_path)}
    if explicit is None or explicit == (saved_host or "codex"):
        expected.update(saved)
    assert settings == expected
    assert repo.delivery_defaults(project_id=project_id)["main_agent"] == saved


def test_schedule_host_constraint_preserves_validated_task_mcp_route(
    session, project_id, tmp_path, native_ready, monkeypatch, controlled_profile_host
):
    bind_folder(session, project_id, tmp_path)
    saved = {
        "host_key": controlled_profile_host,
        "model": "custom-model",
        "provider": "configured",
        "profile": "main-profile",
    }
    TrackerRepository(session).configure_main_agent(
        project_id=project_id,
        preferences=saved,
    )
    fragment = tmp_path / "route.toml"
    fragment.write_text(f"[mcp_servers.stackos]\ncwd = {json.dumps(str(tmp_path))}\n")
    reference = {"path": str(fragment), "sha256": hashlib.sha256(fragment.read_bytes()).hexdigest()}
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fixture/codex")
    monkeypatch.setattr(
        codex,
        "_run_codex",
        lambda *a, **kw: subprocess.CompletedProcess(
            [],
            0,
            json.dumps([{"name": "stackos", "transport": {"type": "stdio", "command": "stackos"}}]),
            "",
        ),
    )
    config = dict(
        kind="main",
        name="Main",
        goal="Benign goal",
        cron_expr="0 9 * * *",
        timezone="UTC",
        enabled=True,
        execution_json={"host_key": "codex", "mcp_config_ref": reference},
    )
    repo = ScheduledJobRepository(session)
    assert repo.validate_configuration(project_id, config) == {
        "host_key": "codex",
        "project_dir": str(tmp_path),
        "mcp_config_ref": reference,
    }
    config["execution_json"]["host_key"] = "claude-code"
    with pytest.raises(ValidationError, match="Task MCP configuration is unsupported"):
        repo.validate_configuration(project_id, config)
    assert (
        TrackerRepository(session).delivery_defaults(project_id=project_id)["main_agent"] == saved
    )


@pytest.mark.parametrize(
    "obsolete", ["agents", "delivery_agent", "review_agent", "step_agent_assignments"]
)
def test_replaced_execution_fields_reject_including_disabled_drafts(session, project_id, obsolete):
    with pytest.raises(ValidationError):
        ScheduledJobRepository(session).set(
            project_id=project_id,
            kind="old",
            cron_expr="0 9 * * *",
            execution_json={obsolete: {}},
        )
