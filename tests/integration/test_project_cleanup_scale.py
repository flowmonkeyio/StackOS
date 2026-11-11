"""Cleanup reference lookups scale with local ownership, not other projects' volume."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import select

from stackos.db.models import Artifact, TrackerTask
from stackos.project_cleanup import ProjectCleanupService, _remove_owned
from tests.integration.test_project_cleanup import _seed_graph, confirmation, put
from tests.integration.test_project_cleanup import fixture as fixture


def test_protection_lookup_does_not_scan_unrelated_references(tmp_path, monkeypatch):
    root = tmp_path / "data"
    owned = root / "projects" / "1"
    for i in range(200):
        put(owned / f"owned-{i}")
    retained = put(owned / "shared" / "nested" / "keep")
    protected = {root / "projects" / "2" / f"unrelated-{i}" for i in range(2_000)}
    protected.add(owned / "shared")
    original = Path.is_relative_to
    comparisons = 0

    def count(path, other):
        nonlocal comparisons
        comparisons += 1
        return original(path, other)

    monkeypatch.setattr(Path, "is_relative_to", count)
    _remove_owned(owned, protected, root)
    assert retained.read_text() == "sentinel"
    assert list(owned.iterdir()) == [owned / "shared"]
    # The old leaf x all-references scan performs over 400,000 comparisons.
    assert comparisons < 1_000


def test_shared_directory_handoff_does_not_scan_unrelated_artifacts(fixture, monkeypatch):
    s, settings, target, other = fixture
    service = ProjectCleanupService(s, settings)
    owned = service._roots(target.id)[0]
    for i in range(200):
        put(owned / f"shared-{i}")
    shared = Artifact(project_id=other.id, kind="directory", uri=str(owned))
    s.add(shared)
    s.add_all(
        Artifact(
            project_id=other.id,
            kind="unrelated",
            uri=str(settings.data_dir / "projects" / str(other.id) / f"unrelated-{i}"),
        )
        for i in range(1_000)
    )
    s.commit()
    original = Path.is_relative_to
    comparisons = 0

    def count(path, other):
        nonlocal comparisons
        comparisons += 1
        return original(path, other)

    monkeypatch.setattr(Path, "is_relative_to", count)
    service._handoff_shared_files(target.id)
    assert len(shared.managed_file_json["files"]) == 200
    assert all(file["owner_project_id"] == target.id for file in shared.managed_file_json["files"])
    # References are indexed once; each leaf visits only its own ancestors.
    assert comparisons < 8_000


@pytest.mark.asyncio
async def test_null_delivery_metadata_does_not_break_cleanup(fixture):
    s, settings, target, _ = fixture
    _seed_graph(s, target.id)
    task = s.exec(select(TrackerTask).where(TrackerTask.project_id == target.id)).first()
    task.metadata_json = {"delivery": None}
    s.add(task)
    s.commit()
    result = await ProjectCleanupService(s, settings).delete(
        target.id, confirmation=confirmation(target)
    )
    assert result.status == "deleted"
