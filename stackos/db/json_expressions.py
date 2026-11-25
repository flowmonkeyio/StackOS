"""Bounded JSON selectors shared by SQLite and Postgres queries."""

from sqlalchemy import JSON, String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.sql.visitors import InternalTraversal


def json_value(column, *path):
    """Keep JSON paths literal so SQLite can use matching expression indexes."""
    if not path:
        return column
    index = path[0] if len(path) == 1 else tuple(path)
    selected = column[index]
    selected.right = selected.right.render_literal_execute()
    return selected


class json_document(FunctionElement):
    """Treat existing serialized JSON text as a document without changing storage."""

    type = JSON()
    inherit_cache = True


@compiles(json_document)
def _sqlite_json_document(element, compiler, **kwargs):
    (column,) = element.clauses
    return compiler.process(column, **kwargs)


@compiles(json_document, "postgresql")
def _postgres_json_document(element, compiler, **kwargs):
    (column,) = element.clauses
    return f"CAST({compiler.process(column, **kwargs)} AS JSON)"


class json_kind(FunctionElement):
    """JSON type names used by retained admission checks, including missing vs null."""

    type = String()
    inherit_cache = True
    _traverse_internals = FunctionElement._traverse_internals.copy()
    _traverse_internals.append(("path", InternalTraversal.dp_plain_obj))

    def __init__(self, column, *path):
        self.path = tuple(path)
        super().__init__(column)


@compiles(json_kind)
def _sqlite_json_kind(element, compiler, **kwargs):
    (column,) = element.clauses
    sql = compiler.process(column, **kwargs)
    if element.path:
        # Paths come only from code-defined keys, never request interpolation.
        import json

        selected = "$" + "".join(
            f"[{key}]" if isinstance(key, int) else "." + json.dumps(key) for key in element.path
        )
        sql += ", " + compiler.render_literal_value(selected, String())
    return f"json_type({sql})"


@compiles(json_kind, "postgresql")
def _postgres_json_kind(element, compiler, **kwargs):
    (column,) = element.clauses
    selected = json_value(column, *element.path)
    sql = compiler.process(selected, **kwargs)
    kind = f"json_typeof({sql})"
    text = f"({sql} #>> '{{}}')"
    return (
        f"(CASE {kind} WHEN 'string' THEN 'text' "
        f"WHEN 'boolean' THEN {text} "
        f"WHEN 'number' THEN CASE WHEN {text} ~ '^-?[0-9]+$' "
        f"THEN 'integer' ELSE 'real' END ELSE {kind} END)"
    )


class json_array_members(FunctionElement):
    """Expand arrays into JSON value/type columns; other shapes have no members."""

    inherit_cache = True


@compiles(json_array_members)
def _sqlite_json_array_members(element, compiler, **kwargs):
    (column,) = element.clauses
    sql = compiler.process(column, **kwargs)
    return f"json_each(CASE WHEN json_type({sql}) = 'array' THEN {sql} ELSE '[]' END)"


@compiles(json_array_members, "postgresql")
def _postgres_json_array_members(element, compiler, **kwargs):
    (column,) = element.clauses
    sql = compiler.process(column, **kwargs)
    return (
        "(SELECT value, json_typeof(value) AS type FROM json_array_elements("
        f"CASE WHEN json_typeof({sql}) = 'array' THEN {sql} ELSE '[]'::json END))"
    )
