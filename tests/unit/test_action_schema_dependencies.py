"""Generic JSON Schema dependency checks use presence, including nested objects."""

from __future__ import annotations

import pytest

from stackos.actions.repository.utils import _schema_issues


@pytest.mark.parametrize("trigger", [[], ["item"], 0, False, None, ""])
@pytest.mark.parametrize("location", ["root", "body", "query", "items"])
def test_dependent_required_uses_presence_at_each_object(trigger, location) -> None:
    schema = {"dependentRequired": {"trigger": ["version"]}}
    value = {"trigger": trigger}
    path = "$"
    if location in {"body", "query"}:
        schema = {"properties": {location: schema}}
        value = {location: value}
        path += f".{location}"
    elif location == "items":
        schema = {"properties": {"rows": {"type": "array", "items": schema}}}
        value = {"rows": [value]}
        path += ".rows[0]"

    issues = _schema_issues(schema, value)

    assert [(issue.path, issue.code) for issue in issues] == [
        (f"{path}.version", "dependent_required")
    ]
    assert "trigger" in issues[0].message


@pytest.mark.parametrize(
    "value",
    [{}, {"version": 0}, {"trigger": [], "version": 0}, {"trigger": False, "version": None}],
)
def test_dependent_required_allows_absent_trigger_and_present_dependency(value) -> None:
    assert _schema_issues({"dependentRequired": {"trigger": ["version"]}}, value) == []


def test_dependent_required_checks_each_missing_dependency() -> None:
    issues = _schema_issues(
        {"dependentRequired": {"trigger": ["first", "second", "present"]}},
        {"trigger": [], "present": False},
    )
    assert [issue.path for issue in issues] == ["$.first", "$.second"]


def test_dependency_presence_does_not_replace_type_or_additional_property_checks() -> None:
    schema = {
        "type": "object",
        "properties": {"trigger": {"type": "array"}, "version": {"type": "integer"}},
        "dependentRequired": {"trigger": ["version"]},
        "additionalProperties": False,
    }
    issues = _schema_issues(schema, {"trigger": [], "version": None, "extra": True})
    assert {(issue.path, issue.code) for issue in issues} == {
        ("$.version", "type_mismatch"),
        ("$.extra", "additional_property"),
    }
