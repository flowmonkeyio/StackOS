"""Trusted update fixtures use only disposable catalog databases."""

import json

import pytest
from sqlmodel import Session, select

from stackos.db.model_patterns import Pattern, PatternDeletion
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.patterns import PatternRepository


def entry(key="shipped", **changes):
    return (
        dict(
            bundled_id=key,
            key=key,
            title=f"Synthetic {key}",
            summary="Applicability",
            tags=["freeform"],
            body="[Reference](https://example.test)\n\nUse `example`.\n",
        )
        | changes
    )


def bundle(*entries, version="v1"):
    return json.dumps(dict(version=version, entries=list(entries)))


def stored(session, key):
    session.expire_all()
    return session.exec(select(Pattern).where(Pattern.key == key)).one().model_dump()


def test_pattern_bundle_updates_preserve_custom_content_and_baselines(session):
    repo = PatternRepository(session)
    repo.refresh_bundle(bundle(entry(), entry("edited"), entry("removed")))
    user = repo.upsert(key="user", title="User", summary="", body="User body", tags=[])
    edited = repo.get(key="edited")
    repo.upsert(
        **edited.model_dump(include={"key", "title", "summary", "body", "tags"})
        | {
            "body": edited.body + " ",
            "expected_etag": edited.etag,
        }
    )
    before = stored(session, "edited")
    removed = stored(session, "removed")
    old = repo.get(key="shipped")
    v2 = bundle(entry(body="Updated"), entry("edited", body="Replace"), entry("new"), version="v2")
    result = repo.refresh_bundle(v2)
    assert result["updated"] == 1 and result["inserted"] == 1
    assert stored(session, "edited") == before
    assert stored(session, "removed") == removed
    assert repo.get(key="user") == user
    assert repo.get(key="shipped").body == "Updated"
    assert stored(session, "shipped")["bundled_version"] == "v2"
    assert repo.get(key="edited").source_label == "StackOS · Customized"
    assert repo.get(key="shipped").source_label == "StackOS"
    assert repo.get(key="user").source_label == "Custom"
    current = stored(session, "shipped")
    repo.refresh_bundle(v2)
    assert stored(session, "shipped") == current
    with pytest.raises(ConflictError):
        repo.upsert(
            key=old.key,
            title=old.title,
            summary=old.summary,
            tags=old.tags,
            body="stale",
            expected_etag=old.etag,
        )
    assert repo.list(query="Use", tags=["freeform"], source="customized").items[0].key == "edited"
    assert repo.list(source="stackos").total_estimate == 4
    assert [p.key for p in repo.list(source="custom").items] == ["user"]
    assert "body" not in repo.list(source="stackos").items[0].model_dump()


def test_pattern_bundle_deletion_collision_and_untrusted_fields(session):
    repo = PatternRepository(session)
    custom = repo.upsert(key="collision", title="Custom", summary="", body="Keep", tags=[])
    title = repo.upsert(key="other", title="Synthetic title", summary="", body="Keep", tags=[])
    result = repo.refresh_bundle(bundle(entry("collision"), entry("title"), entry()))
    assert result["collisions"] == 2
    assert repo.get(key="collision") == custom and repo.get(key="other") == title
    shipped = repo.get(key="shipped")
    repo.delete(shipped.id, expected_etag=shipped.etag)
    assert session.exec(select(PatternDeletion)).one().model_dump() == {"bundled_id": "shipped"}
    repo.refresh_bundle(
        bundle(entry(body="v2")),
    )
    assert repo.list().catalog_total == 2
    for field in ("bundled_id", "bundled_version", "bundled_base_hash", "content_hash", "source"):
        with pytest.raises(ValidationError):
            repo.upsert(
                key="untrusted", title="X", summary="", body="X", tags=[], **{field: "fake"}
            )


@pytest.mark.parametrize(
    "bad",
    ["{", '{"version":"v1","entries":[{}]}', bundle(entry(), entry()), bundle(entry(body=" "))],
)
def test_pattern_malformed_bundle_is_all_or_nothing(session, bad):
    repo = PatternRepository(session)
    repo.refresh_bundle(bundle(entry()))
    before = stored(session, "shipped")
    with pytest.raises(ValidationError):
        repo.refresh_bundle(bad)
    assert stored(session, "shipped") == before


def test_pattern_bundle_controlled_intervening_edit(tmp_path, monkeypatch):
    from sqlmodel import SQLModel

    from stackos.db.connection import make_engine

    engine = make_engine(tmp_path / "race.db")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as first, Session(engine) as other:
            repo, writer = PatternRepository(first), PatternRepository(other)
            repo.refresh_bundle(bundle(entry()))
            initial = writer.get(key="shipped")
            original = repo._refresh_entry

            def intervene(item, version, result):
                writer.upsert(
                    key=initial.key,
                    title=initial.title,
                    summary=initial.summary,
                    tags=initial.tags,
                    body="Committed edit",
                    expected_etag=initial.etag,
                )
                return original(item, version, result)

            monkeypatch.setattr(repo, "_refresh_entry", intervene)
            repo.refresh_bundle(bundle(entry(body="Package replacement"), version="v2"))
            assert writer.get(key="shipped").body == "Committed edit"
            assert stored(other, "shipped")["bundled_version"] == "v1"
    finally:
        engine.dispose()


def test_pattern_bundle_compare_and_write_loses_to_committed_edit(tmp_path, monkeypatch):
    from sqlalchemy.sql.dml import Update
    from sqlmodel import SQLModel

    from stackos.db.connection import make_engine

    engine = make_engine(tmp_path / "compare-race.db")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as first, Session(engine) as other:
            repo, writer = PatternRepository(first), PatternRepository(other)
            repo.refresh_bundle(bundle(entry()))
            initial = writer.get(key="shipped")
            execute = first.execute
            before = None

            def intervene(statement, *args, **kwargs):
                nonlocal before
                if isinstance(statement, Update) and before is None:
                    writer.upsert(
                        key=initial.key,
                        title=initial.title,
                        summary=initial.summary,
                        tags=initial.tags,
                        body="Committed after compare",
                        expected_etag=initial.etag,
                    )
                    before = stored(other, "shipped")
                return execute(statement, *args, **kwargs)

            monkeypatch.setattr(first, "execute", intervene)
            result = repo.refresh_bundle(bundle(entry(body="Package replacement"), version="v2"))
            assert result["concurrent"] == 1 and result["updated"] == 0
            assert stored(other, "shipped") == before
            assert before["bundled_version"] == "v1"
    finally:
        engine.dispose()


def test_pattern_bundle_migration_preserves_existing_rows_and_nonreuse(tmp_path):
    import sqlite3

    from alembic import command

    from stackos.config import Settings
    from stackos.db.connection import make_engine
    from stackos.db.migrate import alembic_config, upgrade_to_head

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    settings.ensure_dirs()
    command.upgrade(alembic_config(settings), "0044_patterns")
    with sqlite3.connect(settings.db_path) as db:
        values = (
            "existing",
            "Keep",
            "",
            "Exact body \n",
            "[]",
            1,
            "2026-11-12 00:00:00",
            "2026-11-12 00:00:00",
        )
        db.execute(
            "INSERT INTO patterns "
            "(key,title,summary,body,tags_json,revision,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            values,
        )
        db.execute(
            "INSERT INTO patterns "
            "(id,key,title,summary,body,tags_json,revision,created_at,updated_at) "
            "VALUES (90,'deleted','Deleted','','X','[]',1,?,?)",
            values[-2:],
        )
        db.execute("DELETE FROM patterns WHERE id=90")
        before = db.execute("SELECT * FROM patterns").fetchall()[0]
    upgrade_to_head(settings)
    with sqlite3.connect(settings.db_path) as db:
        after = db.execute("SELECT * FROM patterns").fetchall()[0]
        assert after[: len(before)] == before and after[len(before) :] == (None, None, None, None)
    engine = make_engine(settings.db_path)
    try:
        with Session(engine) as session:
            repo = PatternRepository(session)
            assert repo.get(key="existing").source == "custom"
            created = repo.upsert(key="new-user", title="X", summary="", body="X", tags=[])
            assert created.id > 90
            repo.refresh_bundle(bundle(entry()))
    finally:
        engine.dispose()


def test_pattern_startup_refresh_once_and_never_during_reads(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import stackos.server as server
    from stackos.config import Settings

    asset = tmp_path / "trusted-fixture.json"
    asset.write_text(bundle(entry()))
    monkeypatch.setattr(server, "bundled_patterns_source", lambda: asset)
    refresh = PatternRepository.refresh_bundle
    calls = []

    def observed(self, raw):
        calls.append(raw)
        return refresh(self, raw)

    monkeypatch.setattr(PatternRepository, "refresh_bundle", observed)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    for count in (1, 2):
        with TestClient(server.create_app(settings), base_url="http://127.0.0.1:5199") as api:
            assert len(calls) == count
            headers = {"Authorization": f"Bearer {settings.token_path.read_text().strip()}"}
            for verb, arguments in (("list", {}), ("get", {"key": "shipped"})):
                response = api.post(
                    f"/api/v1/operations/pattern.{verb}/call",
                    json={"arguments": arguments},
                    headers=headers,
                )
                assert response.status_code == 200
            assert len(calls) == count


def test_pattern_bundle_intervening_insert_and_delete_cannot_resurrect(tmp_path, monkeypatch):
    from sqlalchemy.sql.dml import Insert
    from sqlmodel import SQLModel

    from stackos.db.connection import make_engine

    engine = make_engine(tmp_path / "delete-race.db")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as first, Session(engine) as other:
            repo, writer = PatternRepository(first), PatternRepository(other)
            execute = first.execute
            paused = False

            def intervene(statement, *args, **kwargs):
                nonlocal paused
                if isinstance(statement, Insert) and not paused:
                    paused = True
                    writer.refresh_bundle(bundle(entry()))
                    current = writer.get(key="shipped")
                    writer.delete(current.id, expected_etag=current.etag)
                return execute(statement, *args, **kwargs)

            monkeypatch.setattr(first, "execute", intervene)
            repo.refresh_bundle(bundle(entry()))
            assert writer.list().catalog_total == 0
            assert other.exec(select(PatternDeletion)).one().bundled_id == "shipped"
    finally:
        engine.dispose()
