"""Filesystem evidence for inherited workspace scope."""

from pathlib import Path

import pytest

from stackos.workspace_identity import nested_git_boundary


def test_git_boundary_does_not_read_marker_or_require_git(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    nested = root / "src"
    nested.mkdir(parents=True)
    # A broken marker remains a boundary; do not inspect its target or contents.
    (root / ".git").symlink_to(tmp_path / "unavailable-metadata")
    assert nested_git_boundary(str(nested), str(tmp_path)) == str(root)
    assert nested_git_boundary(str(nested), str(root)) is None
    assert nested_git_boundary(str(root), str(root)) is None


def test_git_boundary_inspection_error_does_not_authorize_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = tmp_path / "repository"
    nested.mkdir()

    def denied(self: Path):
        raise PermissionError("metadata not readable")

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(PermissionError, match="metadata not readable"):
        nested_git_boundary(str(nested), str(tmp_path))
