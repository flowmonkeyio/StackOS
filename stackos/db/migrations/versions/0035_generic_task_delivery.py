"""Move active project delivery defaults to the existing generic tracker.

Revision ID: 0035_generic_task_delivery
Revises: 0034_work_item_ownership
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0035_generic_task_delivery"
down_revision: str | None = "0034_work_item_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preserve_current(legacy: Any, current: Any) -> Any:
    if isinstance(legacy, dict) and isinstance(current, dict):
        return {
            **legacy,
            **{
                key: _preserve_current(legacy[key], value) if key in legacy else value
                for key, value in current.items()
            },
        }
    return current


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT pp.id, pp.project_id, pp.config_json FROM project_plugins pp "
            "JOIN plugins p ON p.id = pp.plugin_id WHERE p.slug = 'engineering'"
        )
    ).all()
    for plugin_id, project_id, raw_config in rows:
        config = json.loads(raw_config) if raw_config else {}
        if "delivery" not in config:
            continue
        legacy = config.pop("delivery")
        tracker = bind.execute(
            sa.text(
                "SELECT id, config_json FROM task_trackers "
                "WHERE project_id = :project_id AND key = 'default'"
            ),
            {"project_id": project_id},
        ).first()
        now = datetime.now(UTC).replace(tzinfo=None)
        if tracker is None:
            tracker_id = bind.execute(
                sa.text(
                    "INSERT INTO task_trackers "
                    "(project_id, key, name, description, rev, created_at, updated_at) "
                    "VALUES (:project_id, 'default', 'Project task tracker', "
                    "'Default project tracker for workflow and direct agent work.', 0, :now, :now) "
                    "RETURNING id"
                ),
                {"project_id": project_id, "now": now},
            ).scalar_one()
            # Match the existing default tracker catalog, without importing mutable runtime code.
            for position, (key, label) in enumerate(
                (
                    ("planning", "Planning"),
                    ("implementation", "Implementation"),
                    ("verification", "Verification"),
                    ("done", "Done"),
                )
            ):
                bind.execute(
                    sa.text(
                        "INSERT INTO task_tracker_lanes "
                        "(tracker_id, key, label, position, created_at, updated_at) "
                        "VALUES (:id, :key, :label, :position, :now, :now)"
                    ),
                    dict(id=tracker_id, key=key, label=label, position=position, now=now),
                )
            for position, (key, label, rank) in enumerate(
                (("p0", "P0", 0), ("p1", "P1", 10), ("p2", "P2", 20), ("p3", "P3", 30))
            ):
                bind.execute(
                    sa.text(
                        "INSERT INTO task_tracker_priorities "
                        "(tracker_id, key, label, rank, position, created_at, updated_at) "
                        "VALUES (:id, :key, :label, :rank, :position, :now, :now)"
                    ),
                    dict(
                        id=tracker_id, key=key, label=label, rank=rank, position=position, now=now
                    ),
                )
            current = {}
        else:
            tracker_id, raw_current = tracker
            current = json.loads(raw_current) if raw_current else {}
        current["delivery"] = (
            _preserve_current(legacy, current["delivery"]) if "delivery" in current else legacy
        )
        bind.execute(
            sa.text("UPDATE task_trackers SET config_json = :config WHERE id = :id"),
            {"id": tracker_id, "config": json.dumps(current)},
        )
        bind.execute(
            sa.text("UPDATE project_plugins SET config_json = :config WHERE id = :id"),
            {"id": plugin_id, "config": json.dumps(config)},
        )


def downgrade() -> None:
    # Keep generic settings under their current owner; a downgrade must not restore dual defaults.
    pass
