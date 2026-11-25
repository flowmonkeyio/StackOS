"""The statistics owner's disposable index of canonical receipt inputs.

No prompts, results, configuration or synthetic timestamps are retained here.
Normalization and metric meaning remain in agent_stats; the index only makes
scope/date selection happen before evidence decoding. Native receipts stay raw.
"""

import json
import sqlite3
from functools import lru_cache
from typing import Any

from sqlalchemy import delete, inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection
from sqlmodel import col

from stackos.db.models import TrackerReceiptProjection, TrackerTicket
from stackos.host_mcp.result import normalize_model_observation

_FIELDS = ("input", "output", "total", "cache_read", "cache_write", "reasoning")
_IDENTITY = ("agent", "host_key", "model", "started_at", "invocation_id", "ticket_key", "thread_id")


def _pick(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value[key] for key in keys if key in value}


def _usage(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = _pick(value, ("unit", "status", "reason", "measured"))
    if isinstance(result.get("measured"), dict):
        result["measured"] = _pick(result["measured"], _FIELDS)
    return result


def receipt_input(receipt: dict[str, Any]) -> dict[str, Any]:
    """Keep exactly the inputs used by usage, history, classification and duration."""
    result = _pick(
        receipt,
        (
            "agent",
            "selected_role",
            "validated_outcome",
            "cancelled",
            "upgrade_interruption",
            "launch",
            "execution",
            "finished_at",
            "exit_code",
            "thread_id",
            "result",
            "usage",
            "model_observation",
        ),
    )
    if isinstance(result.get("launch"), dict):
        result["launch"] = _pick(result["launch"], _IDENTITY)
    if result.get("model_observation") is not None:
        result["model_observation"] = (
            normalize_model_observation(
                result["model_observation"],
                session_id=result.get("thread_id"),
                host_key=(result.get("launch") or {}).get("host_key"),
            )
            or {}
        )  # Retain invalid evidence as unknown, never selection fallback.
    if "result" in result:
        result["result"] = {} if isinstance(result["result"], dict) else None
    if "usage" in result:
        result["usage"] = _usage(result["usage"])
    execution = result.get("execution")
    if isinstance(execution, dict):
        result["execution"] = _pick(execution, _IDENTITY)
        if execution.get("model_observation") is not None:
            result["execution"]["model_observation"] = (
                normalize_model_observation(
                    execution["model_observation"],
                    session_id=execution.get("thread_id"),
                    host_key=execution.get("host_key"),
                )
                or {}
            )
        live = execution.get("live_usage")
        if isinstance(live, dict):
            result["execution"]["live_usage"] = _pick(live, ("measured_at", "usage"))
            if "usage" in live:
                result["execution"]["live_usage"]["usage"] = _usage(live["usage"])
            points = live.get("samples")
            if isinstance(points, list):
                result["execution"]["live_usage"]["samples"] = [
                    _pick(point, (*_FIELDS, "at", "source"))
                    for point in points[-120:]
                    if isinstance(point, dict)
                ]
        if execution and not result["execution"]:
            result["execution"] = {"_present": True}
    return result


def _projection_changes(
    ticket_id: int,
    evidence: Any,
    previous_evidence: Any,
    old: dict[tuple, Any],
) -> tuple[list[dict[str, Any]], list[tuple]]:
    """One change calculator shared by ORM/migration and the narrow SQLite repair writer."""
    from stackos.repositories.tracker.agent_stats import (
        _eligible,
        _instant,
        _receipt_items,
        classification_known,
    )

    changes = []
    automatic = evidence.get("automatic_delivery") if isinstance(evidence, dict) else None
    automatic = automatic if isinstance(automatic, dict) else {}
    usage = _receipt_items(evidence)
    timing = _receipt_items(evidence, require_launch=False)
    prior_usage = _receipt_items(previous_evidence)
    prior_timing = _receipt_items(previous_evidence, require_launch=False)
    inputs = [
        (position, identity, receipt, "usage", timing[identity] is receipt)
        for position, (identity, receipt) in enumerate(usage.items())
    ] + [
        (position, identity, receipt, "timing", True)
        for position, (identity, receipt) in enumerate(timing.items())
        if usage.get(identity) is not receipt
    ]
    for position, (attempt, phase, observation), receipt, inventory, for_timing in inputs:
        key = (attempt or 0, phase, inventory, observation)
        current = not observation and receipt is automatic.get(phase)
        previous = old.pop(key, None)
        prior_receipt = (prior_usage if inventory == "usage" else prior_timing).get(
            (attempt, phase, observation)
        )
        finish = _instant(receipt.get("finished_at"))
        finish = finish.replace(tzinfo=None) if finish else None
        closed = any(key in receipt for key in ("finished_at", "exit_code", "result"))
        review_outcome = (
            receipt.get("validated_outcome")
            if receipt.get("selected_role") == "review_agent" and _eligible(receipt)
            else None
        )
        if review_outcome not in ("success", "repair", "blocked", "failed"):
            review_outcome = None
        role = receipt.get("selected_role")
        if (
            _eligible(receipt)
            and not classification_known(receipt)
            and (not isinstance(role, str) or not role or role == "review_agent")
        ):
            review_outcome = "unclassified"
        same_bounds = previous is not None and all(
            previous.get(key) == value
            for key, value in (
                ("finished_at", finish),
                ("closed", closed),
                ("review_outcome", review_outcome),
            )
        )
        if (
            previous is not None
            and previous["current"] == current
            and previous["position"] == position
            and previous["timing"] == for_timing
            and prior_receipt == receipt
            and same_bounds
        ):
            continue
        text = json.dumps(receipt_input(receipt), sort_keys=True, separators=(",", ":"))
        if (
            previous is not None
            and previous["receipt_json"] == text
            and previous["current"] == current
            and previous["position"] == position
            and previous["timing"] == for_timing
            and same_bounds
        ):
            continue
        launch = receipt.get("launch")
        at = _instant(launch.get("started_at")) if isinstance(launch, dict) else None
        values = dict(
            ticket_id=ticket_id,
            attempt=key[0],
            phase=phase,
            inventory=inventory,
            observation_key=observation,
            timing=for_timing,
            current=current,
            launch_at=at.replace(tzinfo=None) if at else None,
            finished_at=finish,
            closed=closed,
            review_outcome=review_outcome,
            receipt_json=text,
            position=position,
        )
        changes.append(values)
    return changes, list(old)


def sync_receipt_projection(
    connection: Connection,
    ticket_id: int,
    evidence: Any,
    previous_evidence: Any = None,
) -> None:
    """Upsert only changed contributions; removal and rollback share the source transaction."""
    table = TrackerReceiptProjection.__table__  # type: ignore[attr-defined]
    old = {
        (row["attempt"], row["phase"], row["inventory"], row["observation_key"]): row
        for row in connection.execute(
            select(table).where(table.c.ticket_id == ticket_id)
        ).mappings()
    }
    changes, removed = _projection_changes(ticket_id, evidence, previous_evidence, old)
    for values in changes:
        statement = insert(table).values(**values)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    table.c.ticket_id,
                    table.c.attempt,
                    table.c.phase,
                    table.c.inventory,
                    table.c.observation_key,
                ],
                set_=values,
            )
        )
    for attempt, phase, inventory, observation in removed:
        connection.execute(
            delete(table).where(
                table.c.ticket_id == ticket_id,
                table.c.attempt == attempt,
                table.c.phase == phase,
                table.c.inventory == inventory,
                table.c.observation_key == observation,
            )
        )


def initialize_receipt_projection(connection: Connection) -> None:
    """One migration/recovery pass; normal statistics reads never backfill."""
    table = TrackerReceiptProjection.__table__  # type: ignore[attr-defined]
    table.create(connection, checkfirst=True)
    # Also serves explicit lost-stamp recovery of an older derived index.
    columns = {column["name"] for column in inspect(connection).get_columns(table.name)}
    if "observation_key" not in columns:
        # The old key cannot represent two invocations in one attempt/phase.
        # Rebuild this disposable index only; canonical evidence is untouched.
        table.drop(connection)
        table.create(connection)
        columns = {column["name"] for column in inspect(connection).get_columns(table.name)}
    for name, ddl in (
        ("finished_at", "DATETIME"),
        ("closed", "BOOLEAN NOT NULL DEFAULT 0"),
        ("review_outcome", "VARCHAR"),
    ):
        if name not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE tracker_receipt_projections ADD COLUMN {name} {ddl}"
            )
    for index in table.indexes:
        index.create(connection, checkfirst=True)
    for ticket_id, evidence in connection.execute(
        select(col(TrackerTicket.id), col(TrackerTicket.completion_evidence_json))
    ):
        sync_receipt_projection(connection, ticket_id, evidence)


def sync_sqlite_receipt_projection(
    connection: sqlite3.Connection,
    ticket_id: int,
    evidence: Any,
    previous_evidence: Any,
) -> None:
    """Same transaction and calculator for the explicit legacy usage-repair script.

    Pre-index databases remain supported by that script; the migration backfills
    them before any new statistics read. This helper neither commits nor migrates.
    """
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracker_receipt_projections'"
    ).fetchone():
        return
    cursor = connection.execute(
        "SELECT * FROM tracker_receipt_projections WHERE ticket_id=?", (ticket_id,)
    )
    columns = [column[0] for column in cursor.description]
    old = {}
    for record in cursor:
        row = dict(zip(columns, record, strict=True))
        old[row["attempt"], row["phase"], row["inventory"], row.get("observation_key", "")] = row
    changes, removed = _projection_changes(ticket_id, evidence, previous_evidence, old)
    for values in changes:
        if "observation_key" not in columns and values["observation_key"]:
            raise ValueError("Interrupted receipt projection requires its schema migration")
        for name in ("launch_at", "finished_at"):
            if values[name] is not None:
                values[name] = values[name].isoformat(" ", timespec="microseconds")
        # The maintenance script also supports a database awaiting the new migration.
        values = {name: value for name, value in values.items() if name in columns}
        names = list(values)
        # All identifiers come from the canonical calculator, never operator input.
        conflict_columns = "ticket_id,attempt,phase,inventory" + (
            ",observation_key" if "observation_key" in columns else ""
        )
        connection.execute(
            f"INSERT INTO tracker_receipt_projections ({','.join(names)}) "
            f"VALUES ({','.join('?' for _ in names)}) "
            f"ON CONFLICT({conflict_columns}) DO UPDATE SET "
            + ",".join(f"{name}=excluded.{name}" for name in names),
            tuple(values[name] for name in names),
        )
    for attempt, phase, inventory, observation in removed:
        connection.execute(
            "DELETE FROM tracker_receipt_projections "
            "WHERE ticket_id=? AND attempt=? AND phase=? AND inventory=?"
            + (" AND observation_key=?" if "observation_key" in columns else ""),
            (ticket_id, attempt, phase, inventory)
            + ((observation,) if "observation_key" in columns else ()),
        )


@lru_cache(maxsize=16384)
def decode_receipt(source_revision: str) -> dict[str, Any]:
    """Bounded pure reuse keyed by the complete projected source, never live counters."""
    return json.loads(source_revision)
