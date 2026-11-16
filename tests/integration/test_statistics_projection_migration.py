"""A real pre-index database upgrades without changing canonical receipts."""

import json

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import insert, select
from sqlmodel import Session

from stackos.db.connection import make_engine
from stackos.db.migrate import alembic_config, upgrade_to_head
from stackos.db.models import (
    TrackerItemStatus,
    TrackerReceiptProjection,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketKind,
)
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from tests.helpers.agent_statistics import seed_statistics_workload
from tests.integration.test_repositories.test_agent_statistics import _receipt, _repaired, _store
from tests.integration.test_schema import _historical_project_fixture_session


def test_existing_receipts_backfill_atomically(settings):
    config = alembic_config(settings)
    command.upgrade(config, "0043_automations_homes_merge")
    engine = make_engine(settings.db_path)
    with _historical_project_fixture_session(engine) as session:
        pid = (
            ProjectRepository(session)
            .create(slug="old-statistics", name="Old statistics", domain="test.invalid")
            .data.id
        )
        tracker = TrackerRepository(session).ensure_tracker(project_id=pid)
        task = TrackerTask(
            tracker_id=tracker.id,
            project_id=pid,
            key="old",
            title="Old",
            status=TrackerItemStatus.COMPLETE,
            source_kind=TrackerSourceKind.MANUAL,
        )
        session.add(task)
        session.flush()
        evidence = {"automatic_delivery": _repaired(), "retained_raw": "untouched"}
        # Core insert represents the old on-disk schema before the new ORM hook exists.
        session.execute(
            insert(TrackerTicket).values(
                tracker_id=tracker.id,
                project_id=pid,
                task_id=task.id,
                key="old",
                title="Old",
                goal="",
                status=TrackerItemStatus.COMPLETE,
                kind=TrackerTicketKind.TICKET,
                source_kind=TrackerSourceKind.MANUAL,
                completion_evidence_json=evidence,
                definition_of_done_json=[],
                constraints_json=[],
                expected_changes_json=[],
                allowed_paths_json=[],
            )
        )
        session.commit()
    command.upgrade(config, "head")
    with Session(engine) as session:
        assert (
            session.execute(select(TrackerTicket.completion_evidence_json)).scalar_one() == evidence
        )
        result = TrackerRepository(session).agent_stats(project_id=pid)
        assert result.totals.launch_attempts == 4
        assert result.totals.reported_usage.total == 100
        assert result.totals.recorded_reviewer_rejects == 1
        assert result.delivery_trends.totals.first_review_acceptance.coverage.unavailable == 1
    engine.dispose()


@pytest.mark.parametrize(
    "prior_head", ["base", "0046_receipt_trend_bounds", "0047_patterns_setup_merge"]
)
def test_product_merge_rebuilds_cancelled_inputs_without_rewriting_sources(settings, prior_head):
    config = alembic_config(settings)
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0052_upgrade_receipts"]
    assert script.get_revision("0048_product_receipts_merge").down_revision == (
        "0046_receipt_trend_bounds",
        "0047_patterns_setup_merge",
    )
    command.upgrade(config, prior_head)
    engine = make_engine(settings.db_path)
    if prior_head != "base":
        with _historical_project_fixture_session(engine) as session:
            pid = (
                ProjectRepository(session)
                .create(slug="retained-cancellation", name="Retained", domain="test.invalid")
                .data.id
            )
            receipt = _receipt("review_agent", None, 17)
            receipt["cancelled"] = True
            _store(session, pid, "retained", {"attempt": 1, "review": receipt})
            evidence = session.execute(select(TrackerTicket.completion_evidence_json)).scalar_one()
            # Recreate the former metrics-head projection: canonical cancellation
            # existed, but its copied input and denormalized classification did not.
            row = session.execute(select(TrackerReceiptProjection)).scalar_one()
            old_input = json.loads(row.receipt_json)
            old_input.pop("cancelled")
            row.receipt_json = json.dumps(old_input)
            row.review_outcome = None
            session.add(row)
            session.commit()
    upgrade_to_head(settings)
    upgrade_to_head(settings)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").all() == [
            ("0052_upgrade_receipts",)
        ]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    if prior_head != "base":
        with Session(engine) as session:
            assert (
                session.execute(select(TrackerTicket.completion_evidence_json)).scalar_one()
                == evidence
            )
            row = session.execute(select(TrackerReceiptProjection)).scalar_one()
            assert json.loads(row.receipt_json)["cancelled"] is True
            assert row.review_outcome == "unclassified"
            totals = TrackerRepository(session).agent_stats(project_id=pid).totals
            assert totals.launch_attempts == 1
            assert totals.reported_usage.total == 17
            assert totals.review_outcomes == {}
            assert totals.coverage.classification_unknown_attempts == 1
    engine.dispose()


def test_finish_bounds_upgrade_downgrade_and_recovery_preserve_receipts(settings):
    config = alembic_config(settings)
    command.upgrade(config, "head")
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        pid = (
            ProjectRepository(session)
            .create(slug="trend-migration", name="Trend migration", domain="test.invalid")
            .data.id
        )
        ids = seed_statistics_workload(session, pid, tasks=1, receipts=3)
        before = session.get(TrackerTicket, ids[0]).completion_evidence_json
    # This is also a real old-index fixture: the preceding revision has none
    # of the new columns, so upgrade cannot rely on create_all or read-time repair.
    command.downgrade(config, "0045_tracker_stream_cursor")
    command.upgrade(config, "head")
    with Session(engine) as session:
        assert session.get(TrackerTicket, ids[0]).completion_evidence_json == before
        rows = session.execute(select(TrackerReceiptProjection)).scalars().all()
        assert len(rows) == 3
        assert all(row.closed and row.finished_at is not None for row in rows)
        assert (
            TrackerRepository(session)
            .agent_stats(project_id=pid)
            .delivery_trends.totals.work_time.total_seconds
            == 30
        )
    command.downgrade(config, "0045_tracker_stream_cursor")
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")
    assert upgrade_to_head(settings).stamped_existing_schema
    with Session(engine) as session:
        assert session.get(TrackerTicket, ids[0]).completion_evidence_json == before
        assert all(
            row.closed for row in session.execute(select(TrackerReceiptProjection)).scalars().all()
        )
    engine.dispose()


def test_lost_schema_stamp_rebuilds_inputs_before_marking_head(settings):
    config = alembic_config(settings)
    command.upgrade(config, "head")
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        pid = (
            ProjectRepository(session)
            .create(slug="unstamped-statistics", name="Unstamped", domain="test.invalid")
            .data.id
        )
        seed_statistics_workload(session, pid, tasks=1, receipts=3)
    with engine.begin() as connection:
        # Existing pre-index create_all databases can reach the explicit stamp
        # recovery path. Its source rows must survive and the new index must exist.
        connection.exec_driver_sql("DROP TABLE tracker_receipt_projections")
        connection.exec_driver_sql("DROP TABLE alembic_version")
    assert upgrade_to_head(settings).stamped_existing_schema
    with Session(engine) as session:
        assert (
            TrackerRepository(session).agent_stats(project_id=pid).totals.reported_usage.total == 33
        )
    assert not upgrade_to_head(settings).stamped_existing_schema
    engine.dispose()


def test_upgrade_interruption_index_rebuild_preserves_canonical_evidence(settings):
    from tests.integration.test_repositories.test_work_duration import receipt

    config = alembic_config(settings)
    command.upgrade(config, "head")
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        pid = (
            ProjectRepository(session)
            .create(slug="upgrade-history", name="Upgrade history", domain="test.invalid")
            .data.id
        )
        _store(session, pid, "upgraded", {"attempt": 1, "delivery": receipt(20, 30)})
        before = session.execute(select(TrackerTicket)).scalar_one()
        tid = before.id
        evidence = before.completion_evidence_json
    # Recreate the real preceding PK, which cannot hold two observations for
    # the same attempt/phase. Insert raw evidence without a new-writer ORM hook.
    command.downgrade(config, "0051_upgrade_postflight")
    interrupted = receipt(0, 10)
    interrupted.update(
        execution={"invocation_id": "retired-upgrade-call"}, upgrade_interruption="fixture-upgrade"
    )
    evidence["automatic_delivery"]["upgrade_interruptions"] = [
        {"attempt": 1, "phase": "delivery", "receipt": interrupted}
    ]
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE tracker_tickets SET completion_evidence_json=? WHERE id=?",
            (json.dumps(evidence), tid),
        )
    command.upgrade(config, "head")
    command.stamp(config, "0051_upgrade_postflight")
    command.upgrade(config, "head")
    with Session(engine) as session:
        saved = session.get(TrackerTicket, tid)
        assert saved.completion_evidence_json == evidence
        assert saved.status == before.status and saved.updated_at == before.updated_at
        rows = session.execute(select(TrackerReceiptProjection)).scalars().all()
        assert len(rows) == 2 and len({row.observation_key for row in rows}) == 2
        brief = TrackerRepository(session).brief(project_id=pid, ticket_key="upgraded")
        assert brief.ticket.work_duration.recorded_intervals == 2
        assert brief.ticket.work_duration.total_seconds == 20
    with pytest.raises(RuntimeError, match="Retained invocation observations"):
        command.downgrade(config, "0051_upgrade_postflight")
    engine.dispose()
