"""The catalog is installation-wide; fixtures never touch the application DB."""

import pytest
from sqlmodel import Session

from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.patterns import PatternRepository


def payload(key="synthetic-pattern", **changes):
    return (
        dict(
            key=key,
            title="Synthetic recommendation",
            summary="When this example applies",
            body="Use [Example](https://example.test/docs).\n\n```text\nexample run\n```\n",
            tags=["free topic", "unregistered-tech"],
        )
        | changes
    )


def test_pattern_crud_retry_revision_and_missing(session: Session):
    repo = PatternRepository(session)
    assert repo.list().items == []
    first = repo.upsert(**payload())
    assert first.source == "custom" and first.source_label == "Custom"
    assert first.revision == 1 and first.etag == "1"
    assert repo.upsert(**payload()) == first
    assert repo.get(key=first.key) == first
    with pytest.raises(ConflictError) as absent:
        repo.upsert(**payload(body="changed"))
    assert absent.value.data["current_etag"] == "1"
    changed = repo.upsert(**payload(body="changed"), expected_etag=first.etag)
    assert changed.id == first.id and changed.revision == 2
    with pytest.raises(ConflictError):
        repo.upsert(**payload(body="stale"), expected_etag=first.etag)
    with pytest.raises(ConflictError):
        repo.delete(first.id, expected_etag=first.etag)
    assert repo.get(pattern_id=first.id).body == "changed"
    repo.delete(first.id, expected_etag=changed.etag)
    with pytest.raises(NotFoundError):
        repo.get(pattern_id=first.id)
    with pytest.raises(NotFoundError):
        repo.delete(first.id, expected_etag=changed.etag)
    replacement = repo.upsert(**payload())
    assert replacement.id > first.id


def test_pattern_combined_filters_bounded_summary_and_stable_pages(session: Session):
    repo = PatternRepository(session)
    a = repo.upsert(**payload("a", body="needle", tags=["alpha"]))
    b = repo.upsert(**payload("b", body="needle", tags=["beta"]))
    repo.upsert(**payload("c", body="different", tags=["alpha"]))
    repo.upsert(**payload("d", body="needle", tags=["excluded"]))
    query = dict(query="needle", tags=["alpha", "beta"], source="custom", limit=1)
    page = repo.list(**query)
    assert [r.id for r in page.items] == [a.id]
    assert "body" not in page.items[0].model_dump()
    assert page.total_estimate == 2 and page.catalog_total == 4
    assert page.next_cursor is not None
    rest = repo.list(**query, cursor=page.next_cursor)
    assert [r.id for r in rest.items] == [b.id] and rest.next_cursor is None
    assert repo.list(query="unregistered-tech").items == []
    assert repo.list(query="different").items[0].key == "c"
    assert repo.list(query="recommendation").total_estimate == 4
    assert repo.list(query="applies").total_estimate == 4
    assert repo.list(query="alpha").total_estimate == 2
    for source in ("stackos", "customized"):
        assert repo.list(source=source).items == []
    with pytest.raises(ValidationError):
        repo.list(limit=101)


def test_pattern_structure_only_preserves_markdown_and_freeform_tags(session: Session):
    repo = PatternRepository(session)
    row = repo.upsert(**payload(tags=[" arbitrary ", "arbitrary", "StackOS", "日本語"]))
    assert row.tags == ["StackOS", "arbitrary", "日本語"]
    assert row.body == payload()["body"]
    # Semantic authoring advice is deliberately not a core validation rule.
    assert repo.upsert(**payload("plain", body="one sentence")).body == "one sentence"
    for values in ({"key": " "}, {"title": " "}, {"body": " "}, {"tags": [""]}):
        with pytest.raises(ValidationError):
            repo.upsert(**(payload("bad") | values))


@pytest.mark.parametrize("action", ["save", "delete"])
def test_pattern_intervening_write_is_not_overwritten(tmp_path, monkeypatch, action):
    from sqlmodel import SQLModel

    from stackos.db.connection import make_engine

    engine = make_engine(tmp_path / "race.db")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as s1, Session(engine) as s2:
            first, other = PatternRepository(s1), PatternRepository(s2)
            initial = first.upsert(**payload())
            original_get = first.get
            paused = False

            def read_then_intervene(**kwargs):
                nonlocal paused
                before = original_get(**kwargs)
                if not paused:
                    paused = True
                    other.upsert(**payload(body="intervening edit"), expected_etag=initial.etag)
                return before

            monkeypatch.setattr(first, "get", read_then_intervene)
            with pytest.raises(ConflictError) as conflict:
                if action == "save":
                    first.upsert(**payload(body="stale edit"), expected_etag=initial.etag)
                else:
                    first.delete(initial.id, expected_etag=initial.etag)
            assert conflict.value.data["current_etag"] == "2"
            assert other.get(pattern_id=initial.id).body == "intervening edit"
    finally:
        engine.dispose()
