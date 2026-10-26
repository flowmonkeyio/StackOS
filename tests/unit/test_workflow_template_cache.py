"""Workflow parsing reuse must preserve source freshness and project isolation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from sqlmodel import Session, SQLModel

from stackos.db.connection import make_memory_engine
from stackos.db.models import Project
from stackos.workflows import template_loader
from stackos.workflows.template_loader import WorkflowTemplateLoader

TEMPLATE = """\
schema_version: stackos.workflow-template.v1
key: sample.review
name: First
version: 0.1.0
steps:
  - id: review
    title: Review
    instructions: [Read the evidence.]
outputs:
  - key: summary
    type: object
"""


@pytest.fixture
def loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    template_loader._cached_workflow_template_yaml.cache_clear()
    monkeypatch.setattr(template_loader, "_clone_plugins_root", lambda: tmp_path / "plugins")
    monkeypatch.setattr(template_loader, "_bundled_plugins_root", lambda: None)
    engine = make_memory_engine()
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield WorkflowTemplateLoader(session, sync_plugins=False)
    finally:
        engine.dispose()
        template_loader._cached_workflow_template_yaml.cache_clear()


@pytest.fixture
def parsed_sources(monkeypatch: pytest.MonkeyPatch):
    parsed: list[str] = []
    parse = template_loader.parse_workflow_template_yaml

    def observe(text: str):
        parsed.append(text)
        return parse(text)

    monkeypatch.setattr(template_loader, "parse_workflow_template_yaml", observe)
    return parsed


def _write(path: Path, text: str = TEMPLATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("source", ["plugin", "bundled", "repo"])
def test_repeated_source_parsing_is_reused_without_sharing_mutable_results(
    loader, parsed_sources, tmp_path, monkeypatch, source
):
    if source == "repo":
        path = tmp_path / ".stackos/workflows/review.yaml"
    else:
        root = tmp_path / source
        path = root / "sample/workflows/review.yaml"
        if source == "bundled":
            monkeypatch.setattr(template_loader, "_bundled_plugins_root", lambda: root)
        else:
            monkeypatch.setattr(template_loader, "_clone_plugins_root", lambda: root)
    _write(path)

    first = loader.describe_template(key="sample.review", repo_root=str(tmp_path))
    first.spec.steps[0].instructions.append("Caller mutation")
    first.summary.name = "Caller summary"
    second = loader.describe_template(key="sample.review", repo_root=str(tmp_path))

    assert parsed_sources == [TEMPLATE]
    assert second.spec.steps[0].instructions == ["Read the evidence."]
    assert second.summary.name == "First"
    assert second.summary.source == ("repo" if source == "repo" else "plugin")


def test_same_size_and_timestamp_source_edit_is_visible(loader, parsed_sources, tmp_path):
    path = _write(tmp_path / "plugins/sample/workflows/review.yaml")
    loader.describe_template(key="sample.review")
    before = path.stat()
    changed = TEMPLATE.replace("name: First", "name: Other")
    path.write_text(changed, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    result = loader.describe_template(key="sample.review")

    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert result.spec.name == "Other"
    assert parsed_sources == [TEMPLATE, changed]


def test_invalid_changed_source_fails_and_repaired_source_loads(loader, parsed_sources, tmp_path):
    path = _write(tmp_path / "plugins/sample/workflows/review.yaml")
    loader.describe_template(key="sample.review")
    path.write_text("key: [", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        loader.describe_template(key="sample.review")

    _write(path)
    assert loader.describe_template(key="sample.review").spec.name == "First"
    assert parsed_sources == [TEMPLATE, "key: ["]


def test_cached_plugin_does_not_shadow_new_or_changed_repo_override(loader, tmp_path):
    _write(tmp_path / "plugins/sample/workflows/review.yaml")
    assert loader.describe_template(key="sample.review").summary.source == "plugin"
    repo_path = _write(
        tmp_path / ".stackos/workflows/review.yaml", TEMPLATE.replace("First", "Repo one")
    )
    first = loader.describe_template(key="sample.review", repo_root=str(tmp_path))
    assert first.summary.source == "repo"
    assert first.spec.name == "Repo one"

    _write(repo_path, TEMPLATE.replace("First", "Repo two"))
    assert (
        loader.describe_template(key="sample.review", repo_root=str(tmp_path)).spec.name
        == "Repo two"
    )
    repo_path.unlink()
    assert (
        loader.describe_template(key="sample.review", repo_root=str(tmp_path)).summary.source
        == "plugin"
    )


def test_point_lookup_preserves_shadowed_sources_and_list_result_isolation(loader, tmp_path):
    _write(tmp_path / "plugins/sample/workflows/review.yaml")
    _write(tmp_path / ".stackos/workflows/review.yaml", TEMPLATE.replace("First", "Repo"))
    _write(
        tmp_path / "plugins/sample/workflows/other.yaml",
        TEMPLATE.replace("sample.review", "sample.other"),
    )
    listed = loader.list_templates(repo_root=str(tmp_path), include_shadowed=True)
    review_sources = [item for item in listed.templates if item.key == "sample.review"]
    assert {(item.source, item.shadowed_by) for item in review_sources} == {
        ("repo", None),
        ("plugin", "repo"),
    }
    for summary in review_sources:
        described = loader.describe_template(
            key="sample.review", source=summary.source, repo_root=str(tmp_path)
        )
        assert described.summary == summary
        described.spec.steps[0].instructions.append("Caller mutation")
        summary.name = "Caller summary"

    assert loader.describe_template(key="sample.review").summary.shadowed_by is None
    assert loader.describe_template(key="sample.other").summary.name == "First"
    assert loader.describe_template(key="sample.review").spec.steps[0].instructions == [
        "Read the evidence."
    ]
    assert {
        (item.key, item.name) for item in loader.list_templates(repo_root=str(tmp_path)).templates
    } == {("sample.review", "Repo"), ("sample.other", "First")}


def test_point_lookup_still_rejects_an_invalid_unrelated_source(loader, tmp_path):
    _write(tmp_path / "plugins/sample/workflows/review.yaml")
    _write(tmp_path / "plugins/sample/workflows/other.yaml", "key: [")

    with pytest.raises(yaml.YAMLError):
        loader.describe_template(key="sample.review")


def test_cached_source_keeps_project_extensions_independent_and_current(
    loader, parsed_sources, tmp_path
):
    _write(tmp_path / "plugins/sample/workflows/review.yaml")
    projects = [
        Project(
            slug=f"project-{number}",
            name=f"Project {number}",
            domain="example.test",
            locale="en",
        )
        for number in (1, 2)
    ]
    loader._s.add_all(projects)
    loader._s.commit()
    first_id, second_id = [project.id for project in projects]
    loader.upsert_extension(
        project_id=first_id,
        workflow_key="sample.review",
        template_overrides_json={"description": "First project"},
        step_overrides_json={"review": {"extra_instructions": ["Project instruction"]}},
    )
    first = loader.describe_template(project_id=first_id, key="sample.review")
    second = loader.describe_template(project_id=second_id, key="sample.review")
    assert first.spec.description == "First project"
    assert first.project_extension.step_overrides_json == {
        "review": {"extra_instructions": ["Project instruction"]}
    }
    assert second.spec.description == ""
    assert second.spec.steps[0].instructions == ["Read the evidence."]
    assert second.project_extension is None

    loader.upsert_extension(
        project_id=first_id,
        workflow_key="sample.review",
        template_overrides_json={"description": "Updated project"},
    )
    assert (
        loader.describe_template(project_id=first_id, key="sample.review").spec.description
        == "Updated project"
    )
    assert loader.describe_template(key="sample.review").spec.description == ""
    assert parsed_sources == [TEMPLATE]
