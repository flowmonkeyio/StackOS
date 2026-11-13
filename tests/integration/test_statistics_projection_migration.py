"""A real pre-index database upgrades without changing canonical receipts."""

from alembic import command
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
from tests.integration.test_repositories.test_agent_statistics import _repaired


def test_existing_receipts_backfill_atomically(settings):
    config = alembic_config(settings)
    command.upgrade(config, "0043_automations_homes_merge")
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
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
