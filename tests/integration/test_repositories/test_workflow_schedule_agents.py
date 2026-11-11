"""Schedules select a main host, independently of explicit task-delivery roles."""

import pytest

from stackos.db.models import TaskTracker
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ScheduledJobRepository
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
