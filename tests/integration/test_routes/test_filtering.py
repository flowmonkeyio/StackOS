"""Filter + sort allow-list tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from stackos.api.filtering import parse_filters, parse_sort


def test_parse_filters_drops_reserved_keys() -> None:
    """``limit`` / ``after`` / ``sort`` are silently dropped."""
    out = parse_filters({"limit": "10", "after": "1", "sort": "id"}, allowed={"a", "b"})
    assert out == {}


def test_parse_filters_passes_allowed() -> None:
    """Known filter keys pass through verbatim."""
    out = parse_filters({"a": "1"}, allowed={"a"})
    assert out == {"a": "1"}


def test_parse_filters_rejects_unknown(api: TestClient) -> None:
    """Disallowed key raises 422 via the FastAPI handler."""
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        parse_filters({"unknown": "x"}, allowed={"a"})
    assert exc_info.value.status_code == 422


def test_parse_sort_default() -> None:
    """``None`` resolves to the default."""
    col, desc = parse_sort(None, {"created_at"})
    assert col == "created_at"
    assert desc is True  # default ``-created_at``


def test_parse_sort_descending() -> None:
    """Leading minus = descending."""
    col, desc = parse_sort("-id", {"id"})
    assert col == "id"
    assert desc is True


def test_parse_sort_ascending() -> None:
    """No prefix = ascending."""
    col, desc = parse_sort("id", {"id"})
    assert col == "id"
    assert desc is False


def test_parse_sort_unknown_column() -> None:
    """Unknown column → 422."""
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        parse_sort("unknown", {"id"})
    assert exc_info.value.status_code == 422
