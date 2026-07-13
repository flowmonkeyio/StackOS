"""Regression tests for run-scoped output ownership checks."""

from __future__ import annotations

import pytest

from stackos.mcp.server import _find_mismatched_project_id as find_mcp_mismatch
from stackos.operations.dispatcher import (
    _find_mismatched_project_id as find_operation_mismatch,
)


@pytest.mark.parametrize("finder", [find_mcp_mismatch, find_operation_mismatch])
def test_scope_output_allows_project_provenance_inside_opaque_json(finder) -> None:
    payload = {
        "project_id": 1,
        "data": {
            "step_id": "inventory",
            "result_json": {
                "source": {"project_id": 9, "kind": "approved-reference"},
            },
            "metadata_json": {"related_project_id": 9},
        },
    }

    assert finder(payload, 1) is None


@pytest.mark.parametrize("finder", [find_mcp_mismatch, find_operation_mismatch])
def test_scope_output_still_rejects_nested_cross_project_objects(finder) -> None:
    payload = {
        "project_id": 1,
        "data": {
            "items": [
                {"id": 42, "project_id": 9, "name": "foreign record"},
            ],
        },
    }

    assert finder(payload, 1) == 9
