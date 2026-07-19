"""Characterization tests for the artifact JSON-path selector."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from stackos.repositories.base import ValidationError


def _selector():
    from stackos.artifacts.json_path import select_json_path

    return select_json_path


def test_select_json_path_preserves_root_nested_and_index_behavior() -> None:
    select_json_path = _selector()
    payload = {
        "data": {
            "name": "report",
            "items": [{"id": "one"}, {"id": "two"}],
            "matrix": [["zero", "one"]],
        }
    }
    root_items = [{"id": "root"}]

    assert select_json_path(payload, "") is payload
    assert select_json_path(payload, "$") is payload
    assert select_json_path(payload, "$.data.name") == "report"
    assert select_json_path(payload, "$.data.items[1]") == {"id": "two"}
    assert select_json_path(payload, "$.data.matrix[0][1]") == "one"
    assert select_json_path(root_items, "$.[0]") is root_items[0]


@pytest.mark.parametrize(
    ("value", "json_path", "detail", "data"),
    [
        ({}, "data", "json_path must start with '$.'", {}),
        ({}, "$.", "json_path contains an empty segment", {}),
        ({}, "$.missing", "json_path field was not found", {"field": "missing"}),
        (
            {"items": []},
            "$.items[1]",
            "json_path array index was not found",
            {"index": 1},
        ),
        (
            {"items": ["value"]},
            "$.items[0]tail",
            "json_path supports only simple field and [index] segments",
            {},
        ),
        (
            {"items": ["value"]},
            "$.items[-1]",
            "json_path array index must be a non-negative integer",
            {},
        ),
    ],
)
def test_select_json_path_preserves_validation_packets(
    value: Any,
    json_path: str,
    detail: str,
    data: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _selector()(value, json_path)

    assert exc_info.value.detail == detail
    assert exc_info.value.data == data


def test_consumers_import_the_same_selector_without_local_parser_copies() -> None:
    repository_root = Path(__file__).parents[2]
    consumer_paths = (
        repository_root / "stackos/operations/execution_contexts.py",
        repository_root / "stackos/mcp/tools/artifacts.py",
    )

    for path in consumer_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        direct_imports = {
            (alias.name, alias.asname)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "stackos.artifacts.json_path"
            for alias in node.names
        }
        local_functions = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }

        assert ("select_json_path", None) in direct_imports
        assert "_select_json_path" not in local_functions
        assert "_split_json_path_token" not in local_functions


def test_artifact_json_path_module_exports_only_the_selector() -> None:
    from stackos.artifacts import json_path

    assert json_path.__all__ == ["select_json_path"]
