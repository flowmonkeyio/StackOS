"""Portable selectors retain the type and missing-value admission boundaries."""

from sqlalchemy import JSON, Column, MetaData, Table, create_engine, select
from sqlalchemy.dialects import postgresql

from stackos.db.json_expressions import json_kind, json_value


def test_json_selectors_preserve_types_paths_and_cached_statement_identity():
    table = Table("proof", MetaData(), Column("body", JSON))
    engine = create_engine("sqlite://")
    table.metadata.create_all(engine)
    body = table.c.body
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            {"body": {"a": 0, "b": False, "empty": None, "nested": [{"id": "0"}]}},
        )
        assert connection.execute(
            select(json_kind(body, "a"), json_value(body, "a").as_json())
        ).one() == ("integer", 0)
        # Identically shaped statements must not reuse a different fixed path.
        assert connection.execute(
            select(json_kind(body, "b"), json_value(body, "b").as_json())
        ).one() == ("false", False)
        assert connection.execute(
            select(json_kind(body, "empty"), json_kind(body, "missing"))
        ).one() == ("null", None)
        assert connection.execute(
            select(
                json_kind(body, "nested", 0, "id"),
                json_value(body, "nested", 0, "id").as_json(),
            )
        ).one() == ("text", "0")
    engine.dispose()


def test_postgres_nested_selectors_use_native_json_path_operators():
    table = Table("proof", MetaData(), Column("body", JSON))
    expression = select(
        json_value(table.c.body, "a", "b").as_string(),
        json_kind(table.c.body, "a", 0),
    )
    sql = str(
        expression.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "#>> '{a, b}'" in sql
    assert "json_typeof(proof.body #> '{a, 0}')" in sql
    assert "json_extract" not in sql and "json_type(" not in sql
