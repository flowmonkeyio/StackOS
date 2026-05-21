"""Per audit P-G4: the wheel must bundle skills and plugins.

A pipx-mode install has no checked-out repo on disk, so
`content-stack install` resolves assets via `importlib.resources` from
``content_stack/_assets/skills/`` and ``content_stack/_assets/plugins/``.
We verify the wheel produced by `python -m build` contains those paths
with the same skill counts as the source repo.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel into a tmp dist dir; return the .whl path."""
    repo_root = Path(__file__).resolve().parents[3]
    dist_dir = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"`python -m build` failed: {result.stderr}\n{result.stdout}")
    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    yield wheels[0]
    shutil.rmtree(str(dist_dir), ignore_errors=True)


def _wheel_names(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as z:
        return z.namelist()


def test_wheel_includes_assets_skills(built_wheel: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    expected = sum(1 for _ in (repo_root / "skills").rglob("SKILL.md"))
    names = _wheel_names(built_wheel)
    bundled = [
        n
        for n in names
        if n.startswith("content_stack/_assets/skills/") and n.endswith("/SKILL.md")
    ]
    assert len(bundled) == expected, (
        f"wheel has {len(bundled)} SKILL.md files; source has {expected}"
    )


def test_wheel_includes_content_stack_plugin(built_wheel: Path) -> None:
    names = _wheel_names(built_wheel)

    assert "content_stack/_assets/plugins/content-stack/.codex-plugin/plugin.json" in names
    assert "content_stack/_assets/plugins/content-stack/.claude-plugin/plugin.json" in names
    assert "content_stack/_assets/plugins/content-stack/.mcp.json" in names


def test_wheel_assets_path_namespace_is_under_content_stack(built_wheel: Path) -> None:
    """Assets are namespaced under the package so `importlib.resources` resolves them."""
    names = _wheel_names(built_wheel)
    # All `_assets/...` entries live inside `content_stack/`.
    stray = [n for n in names if "_assets/" in n and not n.startswith("content_stack/_assets/")]
    assert stray == [], f"stray _assets entries outside content_stack/: {stray}"


def test_wheel_no_duplicate_entries(built_wheel: Path) -> None:
    """Hatchling warns about dupes via `force-include`; the wheel must be clean."""
    names = _wheel_names(built_wheel)
    assert len(names) == len(set(names)), "wheel contains duplicate zip entries"
