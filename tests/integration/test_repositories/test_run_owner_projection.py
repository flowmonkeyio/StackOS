"""Request-session ownership snapshot guard invariants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from stackos.db.models import Project
from stackos.operations.runs import OwnerStatusProjectionV1, _begin_owner_status_snapshot
from stackos.repositories.base import ConflictError


def test_status_dto_rejects_extra_fields_and_unbounded_control():
    valid = {
        "state": "not_reserved",
        "freshness": "current_at_snapshot",
        "captured_at": datetime.now(UTC),
        "owner": {"owner_ref": "worker:1", "epoch": 1, "revision": 1},
        "needs": ["missing_reservation"],
        "next_actor": "agent",
        "next_action": "reserve_or_launch",
        "acceptance_state": "not_evaluated",
    }
    assert OwnerStatusProjectionV1.model_validate(valid).state == "not_reserved"
    safe_control = {
        "schema_version": "stackos.native-control.v1",
        "phase": "REGISTERED",
        "control_revision": 1,
        "binding_digest": "a" * 64,
        "manifest_digest": "b" * 64,
    }
    assert (
        OwnerStatusProjectionV1.model_validate(valid | {"control": safe_control}).control
        is not None
    )
    with pytest.raises(ValidationError):
        OwnerStatusProjectionV1.model_validate(valid | {"unreviewed": True})
    with pytest.raises(ValidationError):
        OwnerStatusProjectionV1.model_validate(valid | {"control": {"arbitrary": "value"}})
    with pytest.raises(ValidationError):
        OwnerStatusProjectionV1.model_validate(valid | {"control": safe_control | {"extra": True}})
    with pytest.raises(ValidationError):
        OwnerStatusProjectionV1.model_validate(
            valid | {"owner": {"owner_ref": "worker:1", "epoch": 0, "revision": 1}}
        )
    with pytest.raises(ValidationError):
        OwnerStatusProjectionV1.model_validate(
            valid | {"owner": {"owner_ref": "worker:1", "epoch": True, "revision": 1}}
        )


def test_snapshot_begins_after_sqlalchemy_logical_autobegin_without_dbapi_txn():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.exec(text("select 1"))
        assert session.in_transaction()
        assert not session.connection().connection.driver_connection.in_transaction
        _begin_owner_status_snapshot(session)
        assert session.connection().connection.driver_connection.in_transaction


@pytest.mark.parametrize("collection", ["new", "dirty", "deleted"])
def test_snapshot_rejects_real_pending_session_state_without_takeover(collection: str):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        row = Project(slug="pending", name="Pending", domain="example.test", locale="en")
        if collection == "new":
            session.add(row)
        else:
            session.add(row)
            session.flush()
            if collection == "dirty":
                row.name = "Changed"
            else:
                session.delete(row)
        with pytest.raises(ConflictError, match="clean request session"):
            _begin_owner_status_snapshot(session)
        assert (row in session.new) is (collection == "new")
        assert (row in session.dirty) is (collection == "dirty")
        assert (row in session.deleted) is (collection == "deleted")


def test_snapshot_rejects_real_physical_transaction_without_rollback():
    engine = create_engine("sqlite://")
    with Session(engine) as session:
        connection = session.connection()
        connection.exec_driver_sql("BEGIN")
        driver = connection.connection.driver_connection
        assert driver.in_transaction
        with pytest.raises(ConflictError, match="existing database transaction"):
            _begin_owner_status_snapshot(session)
        assert driver.in_transaction
