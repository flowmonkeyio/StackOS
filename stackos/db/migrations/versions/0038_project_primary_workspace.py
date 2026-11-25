"""Store the explicitly selected project folder without guessing from bindings.

Revision ID: 0038_project_primary_workspace
Revises: 0037_schedule_id_nonreuse
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_project_primary_workspace"
down_revision = "0037_schedule_id_nonreuse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"]: column for column in sa.inspect(bind).get_columns("projects")}
    if "primary_workspace_binding_id" in columns:
        # Retrying after a committed DDL write with a lost revision stamp is safe
        # only when the retained reference has the same integrity contract.
        column = columns["primary_workspace_binding_id"]
        # SQLite's own metadata retains inline ON DELETE actions that generic
        # reflection can omit for a column added with REFERENCES.
        foreign_keys = bind.exec_driver_sql("PRAGMA foreign_key_list(projects)").mappings()
        if not (
            isinstance(column["type"], sa.Integer)
            and column["nullable"]
            and any(
                key["from"] == "primary_workspace_binding_id"
                and key["table"] == "workspace_bindings"
                and key["to"] == "id"
                and key["on_delete"] == "SET NULL"
                for key in foreign_keys
            )
        ):
            raise RuntimeError(
                "Existing primary project folder reference has an incompatible schema"
            )
        return
    # SQLite supports a nullable reference in ADD COLUMN. Avoid rebuilding projects
    # and its inbound references; existing projects deliberately remain unselected.
    op.execute(
        "ALTER TABLE projects ADD COLUMN primary_workspace_binding_id INTEGER "
        "REFERENCES workspace_bindings(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.drop_column("projects", "primary_workspace_binding_id")
