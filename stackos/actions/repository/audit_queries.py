"""Shared predicates for project action audit inspection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_
from sqlmodel import col

from stackos.db.models import ActionCall, ActionCallStatus
from stackos.repositories.base import ValidationError


def utc_bound(value: datetime | None, label: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{label} must include a timezone offset")
    return value.astimezone(UTC).replace(tzinfo=None)


def audit_predicates(
    *,
    project_id: int | None,
    action_call_id: int | None = None,
    run_id: int | None = None,
    run_plan_id: int | None = None,
    run_plan_step_id: int | None = None,
    plugin_slug: str | None = None,
    action_key: str | None = None,
    provider_key: str | None = None,
    status: ActionCallStatus | None = None,
    dry_run: bool | None = False,
    created_from: datetime | None = None,
    created_before: datetime | None = None,
) -> list[Any]:
    filters: list[Any] = []
    for column, value in (
        (col(ActionCall.project_id), project_id),
        (col(ActionCall.id), action_call_id),
        (col(ActionCall.run_id), run_id),
        (col(ActionCall.run_plan_id), run_plan_id),
        (col(ActionCall.run_plan_step_id), run_plan_step_id),
        (col(ActionCall.plugin_slug), plugin_slug),
        (col(ActionCall.action_key), action_key),
        (col(ActionCall.provider_key), provider_key),
        (col(ActionCall.status), status),
    ):
        if value is not None:
            filters.append(column == value)
    is_dry = or_(
        col(ActionCall.dry_run).is_(True), col(ActionCall.status) == ActionCallStatus.DRY_RUN
    )
    if dry_run is not None:
        filters.append(is_dry if dry_run else ~is_dry)
    lower, upper = (
        utc_bound(created_from, "created_from"),
        utc_bound(created_before, "created_before"),
    )
    if lower is not None and upper is not None and lower > upper:
        raise ValidationError("created_from must not be later than created_before")
    if lower is not None:
        filters.append(col(ActionCall.created_at) >= lower)
    if upper is not None:
        filters.append(col(ActionCall.created_at) < upper)
    return filters
