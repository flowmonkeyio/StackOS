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
    assert first.revision == 1
    assert repo.upsert(**payload()) == first
    assert repo.get(key=first.key) == first
    with pytest.raises(ConflictError) as absent:
        repo.upsert(**payload(body="changed"))
    assert absent.value.data["current_etag"] == first.etag
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
            assert conflict.value.data["current_etag"] == other.get(pattern_id=initial.id).etag
            assert other.get(pattern_id=initial.id).body == "intervening edit"
    finally:
        engine.dispose()


@pytest.mark.parametrize("already_stamped", [False, True])
def test_pattern_interrupted_table_creation_recovers_key_and_identity(
    tmp_path, monkeypatch, already_stamped
):
    """Retry real SQLite DDL, an old bad stamp, restart and lost-stamp recovery."""
    import importlib
    import sqlite3

    from alembic import command
    from alembic.script import ScriptDirectory
    from fastapi.testclient import TestClient

    from stackos.config import Settings
    from stackos.db.migrate import alembic_config, current_alembic_version, upgrade_to_head
    from stackos.server import create_app

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    config = alembic_config(settings)
    command.upgrade(config, "0043_automations_homes_merge")
    migration = importlib.import_module("stackos.db.migrations.versions.0044_patterns")
    create_table = migration.op.create_table

    def interrupt_after_real_table(name, *args, **kwargs):
        result = create_table(name, *args, **kwargs)
        if name == "patterns":
            raise RuntimeError("fixture interruption after real patterns CREATE TABLE")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(migration.op, "create_table", interrupt_after_real_table)
        with pytest.raises(RuntimeError, match="fixture interruption"):
            upgrade_to_head(settings)
    assert current_alembic_version(settings) == "0043_automations_homes_merge"
    with sqlite3.connect(settings.db_path) as db:
        assert db.execute("PRAGMA index_list(patterns)").fetchall() == []
        # Valid retained data and a deleted high ID must survive every recovery.
        for identity, key in ((1, "retained"), (90, "deleted-high-id")):
            db.execute(
                "INSERT INTO patterns VALUES (?,?,'Retained','','Exact bytes\n','[\"freeform\"]',"
                "3,'2026-11-12','2026-11-12')",
                (identity, key),
            )
        db.execute("DELETE FROM patterns WHERE id=90")
        original = db.execute("SELECT * FROM patterns WHERE id=1").fetchone()
        assert db.execute("SELECT seq FROM sqlite_sequence WHERE name='patterns'").fetchone() == (
            90,
        )

    if already_stamped:
        # Reproduce the durable state left by the former 0044 retry: it marked
        # 0044 complete without its index, then ran the real 0045 migration.
        command.stamp(config, "0044_patterns")
        command.upgrade(config, "0045_pattern_bundles")
        assert current_alembic_version(settings) == "0045_pattern_bundles"

    expected_head = ScriptDirectory.from_config(config).get_current_head()
    saved = None
    for recovery in ("startup", "repeat-startup", "lost-stamp"):
        if recovery == "lost-stamp":
            with sqlite3.connect(settings.db_path) as db:
                db.execute("DROP TABLE alembic_version")
        with TestClient(
            create_app(settings),
            base_url="http://127.0.0.1:5199",
            raise_server_exceptions=False,
        ) as api:
            assert current_alembic_version(settings) == expected_head
            headers = {"Authorization": f"Bearer {settings.token_path.read_text().strip()}"}
            arguments = payload("after-recovery")
            for _retry in range(2):
                response = api.post(
                    "/api/v1/operations/pattern.upsert/call",
                    json={"arguments": arguments},
                    headers=headers,
                )
                assert response.status_code == 200, response.text
                current = response.json()["data"]
                assert current["id"] > 90
                if saved is None:
                    saved = current
                assert current == saved
            with sqlite3.connect(settings.db_path) as db:
                assert (
                    db.execute(
                        "SELECT id,key,title,summary,body,tags_json,revision,created_at,updated_at "
                        "FROM patterns WHERE id=1"
                    ).fetchone()
                    == original
                )
                assert db.execute(
                    "SELECT COUNT(*) FROM patterns WHERE key='after-recovery'"
                ).fetchone() == (1,)
                assert (
                    db.execute("SELECT seq FROM sqlite_sequence WHERE name='patterns'").fetchone()[
                        0
                    ]
                    >= 90
                )
                key_indexes = [
                    row
                    for row in db.execute("PRAGMA index_list(patterns)")
                    if row[1] == "ix_patterns_key"
                ]
                assert len(key_indexes) == 1 and key_indexes[0][2] == 1
