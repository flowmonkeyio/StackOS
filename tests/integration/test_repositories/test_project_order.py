"""Project display identity and bounded catalog ordering on disposable databases."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlmodel import Session, SQLModel, select

from stackos.db.connection import make_engine
from stackos.db.models import Project
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.projects import ProjectRepository


@pytest.mark.parametrize("name", ["", " \t\n", "x" * 201, None, 3])
def test_names_reject_without_mutation(session, name):
    repo = ProjectRepository(session)
    original = repo.create(slug="original", name="Original", domain="original.test").data
    with pytest.raises(ValidationError):
        repo.update(original.id, name=name)
    assert repo.get(original.id) == original
    with pytest.raises(ValidationError):
        repo.create(slug="invalid", name=name, domain="invalid.test")
    assert repo.list().total_estimate == 1


def test_names_trim_allow_duplicates_and_preserve_identity(session):
    repo = ProjectRepository(session)
    first = repo.create(slug="one", name="  Same  ", domain="one.test").data
    second = repo.create(slug="two", name="Second", domain="two.test").data
    updated = repo.update(second.id, name="\tSame\n").data
    assert first.name == updated.name == "Same"
    assert updated.model_dump(exclude={"name", "updated_at"}) == second.model_dump(
        exclude={"name", "updated_at"}
    )
    exact = "x" * 200
    assert repo.update(first.id, name=" " + exact + " ").data.name == exact
    original = repo.get(first.id)
    assert repo.update(first.id, name=exact).data == original


def _all(repo, **filters):
    result = []
    cursor = None
    while True:
        page = repo.list(limit=17, after_id=cursor, **filters)
        result.extend(item.id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return result


def test_bounded_moves_preserve_unseen_records_and_paginate_ties(session):
    repo = ProjectRepository(session)
    projects = [
        repo.create(slug=f"p-{i}", name=f"Project {i}", domain=f"p-{i}.test").data
        for i in range(75)
    ]
    ids = [p.id for p in projects]
    assert _all(repo) == ids
    baseline = {p.id: p.model_dump(exclude={"order_index"}) for p in projects}
    repo.move(ids[70], anchor_id=ids[2], placement="before")
    expected = ids.copy()
    expected.remove(ids[70])
    expected.insert(2, ids[70])
    assert _all(repo) == expected
    repo.move(ids[0], anchor_id=ids[60], placement="after")
    expected.remove(ids[0])
    expected.insert(expected.index(ids[60]) + 1, ids[0])
    assert _all(repo) == expected
    repo.move(ids[60], placement="top")
    expected.remove(ids[60])
    expected.insert(0, ids[60])
    assert _all(repo) == expected
    for p in projects:
        assert repo.get(p.id).model_dump(exclude={"order_index"}) == baseline[p.id]
    appended = repo.create(slug="new", name="New", domain="new.test").data
    assert _all(repo) == [*expected, appended.id]
    # Imported/legacy equal keys still have deterministic ID ordering and cursors.
    for row in session.exec(select(Project)).all():
        row.order_index = 7
        session.add(row)
    session.commit()
    assert _all(repo) == [*ids, appended.id]


def test_move_validation_noop_and_filtered_cursor(session):
    repo = ProjectRepository(session)
    ids = [repo.create(slug=str(i), name=str(i), domain="test").data.id for i in range(4)]
    baseline = [p.model_dump() for p in repo.list().items]
    driver = session.connection().connection.driver_connection
    initial_changes = driver.total_changes
    for kwargs in (
        {"placement": "top"},
        {"placement": "before", "anchor_id": ids[1]},
        {"placement": "after", "anchor_id": ids[0]},
    ):
        target = ids[1] if kwargs["placement"] == "after" else ids[0]
        repo.move(target, **kwargs)
        assert [p.model_dump() for p in repo.list().items] == baseline
    repo.move(ids[1], anchor_id=ids[1], placement="before")
    assert [p.model_dump() for p in repo.list().items] == baseline
    assert driver.total_changes == initial_changes
    for kwargs in (
        {"placement": "before"},
        {"placement": "top", "anchor_id": ids[1]},
        {"placement": "invalid"},
    ):
        with pytest.raises(ValidationError):
            repo.move(ids[0], **kwargs)
    with pytest.raises(NotFoundError):
        repo.move(ids[0], placement="before", anchor_id=99999)
    repo.update(ids[2], is_active=False)
    with pytest.raises(NotFoundError):
        repo.move(ids[0], placement="before", anchor_id=ids[2])
    with pytest.raises(NotFoundError):
        repo.move(ids[2], placement="top")
    with pytest.raises(ValidationError):
        repo.list(active_only=True, after_id=ids[2])
    assert _all(repo, active_only=True) == [ids[0], ids[1], ids[3]]


def test_order_persists_and_concurrent_creates_append(tmp_path):
    engine = make_engine(tmp_path / "project-order.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        repo = ProjectRepository(session)
        first = repo.create(slug="first", name="First", domain="test").data.id
        second = repo.create(slug="second", name="Second", domain="test").data.id
        repo.move(second, placement="top")

    def create(i):
        with Session(engine) as session:
            return ProjectRepository(session).create(slug=str(i), name=str(i), domain="test").data

    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(create, range(8)))
    engine.dispose()
    engine = make_engine(tmp_path / "project-order.db")
    with Session(engine) as session:
        repo = ProjectRepository(session)
        assert _all(repo)[:2] == [second, first]
        assert len({p.order_index for p in repo.list().items}) == 10
        assert set(_all(repo)[2:]) == {p.id for p in created}
    engine.dispose()
